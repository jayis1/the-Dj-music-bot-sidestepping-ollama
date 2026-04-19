"""
utils/youtube_stream.py — Master Node RTMP Streamer for MBot Radio.

Runs a single 24/7 permanent FFmpeg Master Engine feeding rtmp:// connections.
Listens to native raw UDP PCM bytes from the PCMBroadcaster subsystem seamlessly,
completely eliminating FFmpeg TCP handshake tearing and latency drops natively.
"""

import asyncio
import logging
import os
import shutil
import time

log = logging.getLogger("youtube-stream")

# ── VA-API Hardware Encoding Support ──────────────────────────────────────
# If AMD_GPU_VAAPI=1 is set in the environment AND /dev/dri/renderD128 exists,
# the streamer will use FFmpeg's h264_vaapi encoder instead of libx264.
# This dramatically reduces CPU usage on AMD GPU systems.
#
# Works on ALL AMD GPUs including integrated/Ryzen APUs (gfx90c etc.)
# where ROCm/LLM inference falls back to CPU — VA-API encoding is separate
# from ROCm and uses the Mesa kernel driver which supports nearly all GPUs.
#
# To enable in Docker: set AMD_GPU_VAAPI=1 in environment and pass
# /dev/dri as a device mapping (see docker-compose.yml --profile amd-gpu).
#
# To check your GPU: vainfo (install: sudo apt install vainfo)
_VAAPI_ENABLED = os.environ.get("AMD_GPU_VAAPI", "").strip().lower() in ("1", "true", "yes")

if _VAAPI_ENABLED:
    _DRI_DEVICE = "/dev/dri/renderD128"
    if not os.path.exists(_DRI_DEVICE):
        log.warning(
            f"YouTube Live: AMD_GPU_VAAPI=1 but {_DRI_DEVICE} not found. "
            "Falling back to software (libx264) encoding. "
            "Make sure /dev/dri is passed to the container."
        )
        _VAAPI_ENABLED = False
    else:
        # Try to detect the GPU architecture for informational logging
        _gfx_info = "unknown"
        try:
            import glob as _glob
            for _path in _glob.glob("/sys/class/drm/card*/device/gpu_id"):
                with open(_path) as _f:
                    _gfx_info = _f.read().strip()
                    break
        except Exception:
            pass
        log.info(
            f"YouTube Live: VA-API hardware encoding ENABLED "
            f"(device: {_DRI_DEVICE}, gpu: {_gfx_info}). "
            f"Using h264_vaapi instead of libx264 — "
            f"dramatically lower CPU usage for streaming."
        )

# Overlays for FFmpeg dynamic HUD reloads
TXT_STATION = "/tmp/radio_station.txt"
TXT_STATE = "/tmp/radio_state.txt"
TXT_TITLE = "/tmp/radio_title.txt"
TXT_DJ = "/tmp/radio_dj.txt"
TXT_WAITING = "/tmp/radio_waiting.txt"

class YouTubeLiveStreamer:
    """Manages the Master YouTube Live RTMP connection via UDP polling.

    When OBS Studio is available (via obs_bridge.py), streaming goes through
    OBS instead of a separate Chromium+FFmpeg pipeline. OBS handles the visual
    layer (scenes with browser overlay source) and audio capture, and is the
    single streaming point to YouTube Live. This eliminates the need for
    Xvfb + Chromium as separate dependencies.

    When OBS is NOT available, falls back to the legacy Chromium+FFmpeg
    x11grab pipeline (requires Xvfb + Chromium installed).
    """

    WIDTH = 1280
    HEIGHT = 720

    # Audio visualizer bar update interval (seconds).
    # 100ms = 10 FPS for the level bar animation — fast enough to look
    # responsive, slow enough to not hammer OBS with WebSocket requests.
    _VISUALIZER_INTERVAL = 0.1

    def __init__(
        self,
        stream_key: str,
        rtmp_url: str = "rtmp://a.rtmp.youtube.com/live2",
        rtmp_backup_url: str = "",
        stream_image: str | None = None,
        stream_gif: str | None = None,
        bitrate_audio: int = 192,
        bitrate_video: int = 3000,
        fps: int = 30,
        station_name: str = "MBot Radio",
        udp_port: int = 12345,
        obs_bridge=None,
    ):
        self.stream_key = stream_key
        self.rtmp_url = rtmp_url
        self.rtmp_backup_url = rtmp_backup_url
        self.stream_image = stream_image
        self.stream_gif = stream_gif
        self.bitrate_audio = bitrate_audio
        self.bitrate_video = bitrate_video
        self.fps = fps
        self.station_name = station_name
        self.udp_port = udp_port
        self._obs_bridge = obs_bridge  # OBSBridge instance (may be None)

        self._process: asyncio.subprocess.Process | None = None
        self._chromium: asyncio.subprocess.Process | None = None
        self._xvfb: asyncio.subprocess.Process | None = None
        self._running = False
        self._using_obs = False  # True when streaming via OBS
        self._watchdog_task: asyncio.Task | None = None
        self._stderr_drain_task: asyncio.Task | None = None
        self._started_at: float = 0
        self._last_error: str = ""
        self._use_vaapi = _VAAPI_ENABLED
        self._stream_starting = False  # Guard against concurrent start attempts
        self._visualizer_task: asyncio.Task | None = None  # Audio level bar polling
        
        self.update_hud(station=self.station_name, waiting="Booting Mainframes...")

        # Warn immediately if stream key is missing — the #1 cause of
        # "preparing stream" / TLS / "Connection reset by peer" errors
        if not self.stream_key:
            log.warning(
                "YouTube Live: ⚠️ No stream key configured! "
                "Set YOUTUBE_STREAM_KEY in .env or Mission Control → Radio → Stream Key. "
                "Without it, OBS cannot connect to YouTube."
            )

    @property
    def is_running(self) -> bool:
        return self._running

    def update_hud(self, station="", state="", title="", dj="", waiting=""):
        """Dynamically overwrite FFmpeg GUI layout texts instantly on disk.

        CRITICAL: Never write an empty string to /tmp/radio_*.txt files!
        OBS's text_ft2_source_v2 (FreeType2) renders empty strings as
        zero-height invisible text. When a field has no content, write
        a single space " " instead so the text source allocates space
        and stays visible for future updates.
        """
        try:
            # Station name: empty = keep whatever's there
            if station:
                with open(TXT_STATION, "w") as f:
                    f.write(self._safe_text(station, 40))
            # State indicator: empty = keep whatever's there
            if state:
                with open(TXT_STATE, "w") as f:
                    f.write(self._safe_text(state, 40))
            # Title: empty = keep whatever's there (don't blank the title)
            if title:
                with open(TXT_TITLE, "w") as f:
                    f.write(self._safe_text(title, 80))
            # DJ: empty string → write space (avoid invisible FreeType2 text)
            dj_text = self._safe_text(dj, 80) if dj else " "
            with open(TXT_DJ, "w") as f:
                f.write(dj_text)
            # Waiting: empty string → write space (avoid invisible FreeType2 text)
            wait_text = self._safe_text(waiting, 80) if waiting else " "
            with open(TXT_WAITING, "w") as f:
                f.write(wait_text)
        except Exception:
            pass

    async def start(self):
        """Invoke the monolithic YouTube RTMP connection.

        When OBS Studio is available (via obs_bridge), streaming goes through
        OBS — the bot configures OBS's RTMP settings and calls start_stream().
        OBS is the single streaming point: it captures the browser overlay
        source (the /overlay page) and streams it to YouTube Live.

        When OBS is NOT available, falls back to the legacy Chromium+FFmpeg
        x11grab pipeline (requires Xvfb + Chromium installed).
        """
        if self._running:
            return

        self._running = True
        self._started_at = time.time()

        # ── Try OBS first ──────────────────────────────────────────
        # If the OBS bridge is connected, use OBS as the streaming point.
        # This eliminates the need for Xvfb + Chromium as separate processes.
        if self._obs_bridge and self._obs_bridge.enabled:
            log.info("YouTube Live: OBS Studio detected — using OBS as streaming backend")
            if await self._start_obs_stream():
                self._using_obs = True
                self._watchdog_task = asyncio.create_task(self._watchdog_obs())
                # Start the audio level visualizer bar polling loop
                self._visualizer_task = asyncio.create_task(self._visualizer_poll_obs())
                return
            else:
                log.warning(
                    "YouTube Live: OBS streaming failed, falling back to Chromium+FFmpeg pipeline"
                )

        # ── Fallback: Chromium + FFmpeg ────────────────────────────
        log.info(f"YouTube Live: Master Engine starting → UDP port {self.udp_port}")
        self._using_obs = False
        await self._start_master_ffmpeg()
        self._watchdog_task = asyncio.create_task(self._watchdog())

    async def stop(self):
        """Teardown the stream completely."""
        self._running = False
        if self._watchdog_task:
            self._watchdog_task.cancel()
            self._watchdog_task = None
        if self._visualizer_task:
            self._visualizer_task.cancel()
            self._visualizer_task = None

        if self._using_obs:
            await self._stop_obs_stream()
        else:
            await self._kill_process()
        log.info("YouTube Live: Master Engine halted.")

    async def play_song(self, audio_url: str, title: str = "", thumbnail: str | None = None):
        """Hook to update HUD text and dynamically download the track thumbnail."""
        self.update_hud(title=title)
        
        if thumbnail:
            async def _download_thumb():
                try:
                    import aiohttp
                    async with aiohttp.ClientSession() as session:
                        async with session.get(thumbnail) as resp:
                            if resp.status == 200:
                                data = await resp.read()
                                temp_path = "/tmp/radio_thumbnail_temp.jpg"
                                with open(temp_path, "wb") as f:
                                    f.write(data)
                                os.rename(temp_path, "/tmp/radio_thumbnail.jpg")
                except Exception as e:
                    log.debug(f"YouTube Live: Failed to fetch thumbnail: {e}")
                    
            asyncio.create_task(_download_thumb())

    async def play_tts(self, tts_path: str, text: str = ""):
        """Deprecated hook: The Broadcaster handles PCM streaming directly. Just update the HUD."""
        self.update_hud(dj=text)

    async def play_sfx(self, sfx_path: str, label: str = ""):
        self.update_hud(dj="SFX: " + label)

    async def play_waiting(self, message: str = ""):
        self.update_hud(waiting=message)

    async def start_curated(self, playlist_url: str, *args, **kwargs):
        """Start the stream in curated (Shadow DJ) mode.

        Called by cogs/music.py when the user starts a curated YouTube Live stream.
        Actual playback logic is handled by Voice/Broadcaster queues.
        """
        await self.start()
        self.update_hud(waiting="Curated Playback — Add songs from Queue Manager")

    # ── FFmpeg Core Engine ──────────────────────────────────────────

    def _safe_text(self, text: str, max_len: int = 60) -> str:
        """Escape text for FFmpeg drawtext filter.

        The drawtext filter treats these characters specially:
        ' : % { } \\  and newline.
        We must escape all of them to avoid breaking the stream overlay.
        """
        # Escape backslash first (so later escapes aren't double-escaped)
        text = text.replace("\\", "\\\\")
        # Escape single quotes (drawtext uses ' for string delimiters)
        text = text.replace("'", "\\'")
        # Escape colons (drawtext uses : for key=value separators)
        text = text.replace(":", "\\:")
        # Escape percent (drawtext uses % for timecode expansion)
        text = text.replace("%", "%%")
        # Escape braces (drawtext uses {} for expression evaluation)
        text = text.replace("{", "\\{")
        text = text.replace("}", "\\}")
        # Remove newlines (they break the filter string entirely)
        text = text.replace("\n", " ").replace("\r", " ")
        # Escape semicolons (drawtext uses ; as command separator)
        text = text.replace(";", "\\;")
        return text[:max_len]

    def _resolve_image(self) -> str | None:
        if self.stream_image and os.path.isfile(self.stream_image):
            return self.stream_image
        assets_logo = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "logo.png")
        if os.path.isfile(assets_logo):
            return assets_logo
        return None

    def _resolve_gif(self) -> str | None:
        if self.stream_gif and os.path.isfile(self.stream_gif):
            return self.stream_gif
        assets_gif = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "giphy.gif")
        if os.path.isfile(assets_gif):
            return assets_gif
        return None

    # ── OBS Streaming Backend ───────────────────────────────────────────

    async def _start_obs_stream(self) -> bool:
        """Configure OBS for streaming and start the stream.

        Sets up everything OBS needs to stream to YouTube Live:
        0. Pre-flight checks: stream key, RTMP reachability
        1. Write service.json to OBS profile dir (OBS reads this on startup)
        2. Push RTMP server + stream key to OBS via WebSocket
        3. Ensure the overlay scene exists
        4. Create overlay sources (native color+text)
        5. Create audio source (UDP PCM from bot's PCMBroadcaster)
        6. Switch to the overlay scene
        7. Start OBS streaming

        Returns True if streaming started successfully, False otherwise.
        """
        if not self._obs_bridge:
            return False

        # Guard against concurrent start attempts (watchdog + initial start)
        if self._stream_starting:
            log.debug("YouTube Live/OBS: Start already in progress, skipping")
            return True  # Assume the in-progress start will succeed
        self._stream_starting = True

        try:
            # ── Step 0: Pre-flight checks ──────────────────────────────
            if not self.stream_key:
                log.error(
                    "YouTube Live/OBS: ❌ No stream key configured! "
                    "Set YOUTUBE_STREAM_KEY in .env or Mission Control → Radio → Stream Key. "
                    "Get your key from YouTube Studio → Go Live → Stream Key."
                )
                return False

            log.info(
                f"YouTube Live/OBS: Pre-flight OK — stream key: ...{self.stream_key[-4:]}, "
                f"RTMP: {self.rtmp_url}"
            )

            # ── Step 1: Write service.json to OBS profile directory ────
            # OBS reads service.json from the active profile directory on startup.
            # If this file is missing, OBS has no idea which streaming service to use
            # and falls back to RTMPS with no server/key — causing TLS errors and
            # "Connection reset by peer". Writing this file ensures OBS has the
            # correct RTMP server + stream key even before the WebSocket API pushes it.
            self._write_obs_service_json()

            # ── Step 2: Configure OBS stream settings via WebSocket API ─
            rtmp_endpoint = f"{self.rtmp_url.rstrip('/')}"
            log.info(
                f"YouTube Live/OBS: Configuring stream → {rtmp_endpoint} "
                f"(key: ...{self.stream_key[-4:]})"
            )
            result = self._obs_bridge.set_stream_settings(
                service="rtmp_custom",
                server=rtmp_endpoint,
                key=self.stream_key,
            )
            if not result.get("connected"):
                log.warning(f"YouTube Live/OBS: Failed to configure stream settings: {result}")
                return False

            log.info("YouTube Live/OBS: Stream settings configured ✅")

            # ── Step 3: Ensure the overlay scene exists ────────────────
            overlay_scene = os.environ.get(
                "OBS_SCENE_OVERLAY", "📺 Overlay Only"
            )
            scene_list_result = self._obs_bridge.get_status()
            existing_scenes = scene_list_result.get("scenes", [])
            if overlay_scene not in existing_scenes:
                log.info(f"YouTube Live/OBS: Creating scene '{overlay_scene}'")
                try:
                    self._obs_bridge.create_scene(overlay_scene)
                except Exception:
                    pass  # Scene might already exist (race), that's OK

            # ── Step 4: Create overlay sources (native color+text) ──────
            # On Debian 12, obs-browser plugin is NOT available, so browser_source
            # fails with error 605. We try native overlay first (color+text sources)
            # which works on all platforms, and fall back to browser source on
            # platforms where obs-browser is installed.
            overlay_scene = os.environ.get(
                "OBS_SCENE_OVERLAY", "📺 Overlay Only"
            )

            # Try native overlay first (works everywhere including Debian 12)
            log.info("YouTube Live/OBS: Creating native overlay (color+text sources)")
            result = self._obs_bridge.create_native_overlay(
                scene_name=overlay_scene,
            )
            if result.get("errors"):
                log.warning(
                    f"YouTube Live/OBS: Native overlay had errors: {result['errors']}. "
                    "Overlay may be incomplete."
                )

            # ── Step 5: Create FFmpeg audio source (UDP PCM from bot) ───
            result = self._obs_bridge.create_audio_source(
                source_name="Bot Audio (UDP)",
                udp_port=12345,
                scene_name=overlay_scene,
            )
            if result.get("error"):
                log.warning(f"YouTube Live/OBS: Audio source issue: {result['error']}")
            else:
                log.info("YouTube Live/OBS: Audio source created ✅")

            # ── Step 6: Mute OBS Desktop Audio ──────────────────────────
            # OBS's "Desktop Audio" captures from PulseAudio's radio_dj_sink
            # at 44.1kHz. This creates a DOUBLE-AUDIO problem (same audio
            # arrives both via PulseAudio AND via the UDP ffmpeg source)
            # and a SAMPLE RATE MISMATCH (44.1kHz Desktop vs 48kHz UDP).
            # The 44.1kHz→48kHz resampling causes the "slowed down" effect.
            # Since the bot audio already arrives via UDP, the PulseAudio
            # Desktop Audio capture is redundant and must be muted.
            try:
                mute_result = self._obs_bridge.set_source_mute("Desktop Audio", muted=True)
                if not mute_result.get("error"):
                    log.info("YouTube Live/OBS: Muted Desktop Audio (using UDP source instead)")
                else:
                    # Might be named differently — try alternative names
                    for alt_name in ["PulseAudio", "Audio Output", "DesktopAudioHandler"]:
                        alt_result = self._obs_bridge.set_source_mute(alt_name, muted=True)
                        if not alt_result.get("error"):
                            log.info(f"YouTube Live/OBS: Muted '{alt_name}' (using UDP source instead)")
                            break
            except Exception as e:
                log.debug(f"YouTube Live/OBS: Could not mute Desktop Audio: {e}")

            # ── Step 7: Switch to the overlay scene BEFORE starting stream
            # This ensures the correct content is there from frame 1
            self._obs_bridge.set_current_scene(overlay_scene)
            log.info(f"YouTube Live/OBS: Switched to scene '{overlay_scene}'")

            # ── Step 7.5: Push encoder settings (keyint_sec, bitrate) ────
            # OBS's Advanced mode often caches stale encoder settings from
            # previous runs. Even though basic.ini has keyint_sec=2, OBS may
            # still use keyint=250 (x264 default = 8.3s @ 30fps → "Poor" on
            # YouTube). Pushing via WebSocket forces the correct values.
            try:
                enc_result = self._obs_bridge.set_encoder_settings(
                    keyint_sec=2, bitrate=3000,
                    preset="veryfast", rate_control="CBR",
                )
                if not enc_result.get("error"):
                    log.info(
                        "YouTube Live/OBS: Encoder settings pushed — "
                        "keyint_sec=2, bitrate=3000, CBR, veryfast"
                    )
                else:
                    log.debug(
                        f"YouTube Live/OBS: Encoder settings push had issues: "
                        f"{enc_result}"
                    )
            except Exception as e:
                log.debug(f"YouTube Live/OBS: Could not push encoder settings: {e}")

            # ── Step 8: Start OBS streaming ─────────────────────────────
            result = self._obs_bridge.start_streaming()
            if not result.get("connected"):
                log.warning(f"YouTube Live/OBS: Failed to start streaming: {result}")
                return False

            if result.get("error"):
                log.warning(f"YouTube Live/OBS: Stream start error: {result['error']}")
                # OBS might already be streaming — check status
                status = self._obs_bridge.get_status()
                if status.get("streaming"):
                    log.info("YouTube Live/OBS: Stream is already active ✅")
                    return True
                return False

            log.info("YouTube Live/OBS: Streaming started ✅")
            return True

        except Exception as e:
            log.error(f"YouTube Live/OBS: Exception starting stream: {e}")
            return False
        finally:
            self._stream_starting = False

    def _write_obs_service_json(self):
        """Write service.json to the active OBS profile directory on disk.

        OBS reads service.json from the active profile directory (e.g.
        ~/.config/obs-studio/basic/profiles/RadioDJ/) to know which
        streaming service to use, the RTMP server URL, and the stream key.

        If this file is missing or contains an empty stream key, OBS:
          - Still tries to connect to YouTube on start_streaming()
          - Falls back to RTMPS (TLS), causing "Error in the pull function"
            and "Connection reset by peer" errors
          - The stream never actually starts

        Writing this file BEFORE calling start_streaming() via the WebSocket
        API ensures OBS has the correct service configuration even if the
        WebSocket push arrives after OBS has already initialized its output
        module.

        This is a defensive measure — the WebSocket set_stream_service_settings()
        should also write this file, but we do it ourselves to be certain.
        """
        import json

        # Find the active OBS profile directory
        # OBS uses --profile "RadioDJ" from start.sh
        profile_name = os.environ.get("OBS_PROFILE_NAME", "RadioDJ")
        profile_dir = os.path.expanduser(
            f"~/.config/obs-studio/basic/profiles/{profile_name}"
        )

        if not os.path.isdir(profile_dir):
            # Try to create the directory if it doesn't exist
            try:
                os.makedirs(profile_dir, exist_ok=True)
            except Exception as e:
                log.debug(f"YouTube Live/OBS: Could not create profile dir {profile_dir}: {e}")
                return

        rtmp_endpoint = f"{self.rtmp_url.rstrip('/')}"

        service_data = {
            "type": "rtmp_custom",
            "settings": {
                "server": rtmp_endpoint,
                "key": self.stream_key,
            },
        }

        service_json_path = os.path.join(profile_dir, "service.json")
        try:
            with open(service_json_path, "w") as f:
                json.dump(service_data, f, indent=4)
            log.info(
                f"YouTube Live/OBS: Wrote service.json → {profile_dir} "
                f"(server: {rtmp_endpoint}, key: ...{self.stream_key[-4:]})"
            )
        except Exception as e:
            log.warning(f"YouTube Live/OBS: Failed to write service.json: {e}")

    async def _stop_obs_stream(self):
        """Stop OBS streaming."""
        if not self._obs_bridge:
            return
        try:
            result = self._obs_bridge.stop_streaming()
            if result.get("connected"):
                log.info("YouTube Live/OBS: Streaming stopped ✅")
            else:
                log.debug(f"YouTube Live/OBS: Stop streaming result: {result}")
        except Exception as e:
            log.error(f"YouTube Live/OBS: Exception stopping stream: {e}")

    async def _watchdog_obs(self):
        """Monitor OBS streaming status. Restart if OBS stops streaming."""
        backoff = 0
        try:
            while self._running:
                await asyncio.sleep(10)
                if not self._obs_bridge:
                    break
                status = self._obs_bridge.get_status()
                if not status.get("connected"):
                    backoff += 1
                    if backoff > 6:
                        log.warning("YouTube Live/OBS: OBS disconnected for 60s, attempting reconnect...")
                        result = await self._start_obs_stream()
                        if result:
                            backoff = 0
                            log.info("YouTube Live/OBS: Reconnected ✅")
                        else:
                            log.error("YouTube Live/OBS: Reconnect failed")
                elif status.get("streaming"):
                    backoff = 0
                else:
                    # Connected but not streaming — OBS may have stopped
                    backoff += 1
                    if backoff > 3:
                        log.warning("YouTube Live/OBS: Stream stopped, restarting...")
                        result = await self._start_obs_stream()
                        if result:
                            backoff = 0
        except asyncio.CancelledError:
            pass

    async def _visualizer_poll_obs(self):
        """Poll the PCMBroadcaster's audio level and update the OBS visualizer bar.

        This coroutine runs at ~10 FPS (every 100ms) while the stream is active.
        It reads the smoothed RMS audio level from the PCMBroadcaster and
        adjusts the width (scaleX) of the "Audio Visualizer" color source
        in OBS via the WebSocket API.

        The visualizer bar provides real-time visual feedback on the stream
        showing when audio is playing — viewers can "see" beats and silence.
        """
        try:
            from utils.broadcaster import get_broadcaster
        except ImportError:
            log.debug("YouTube Live/OBS: PCMBroadcaster not available for visualizer")
            return

        try:
            overlay_scene = os.environ.get("OBS_SCENE_OVERLAY", "📺 Overlay Only")
            while self._running:
                await asyncio.sleep(self._VISUALIZER_INTERVAL)

                if not self._obs_bridge or not self._obs_bridge.enabled:
                    continue

                # Read the audio level from the registered PCMBroadcaster
                level = 0.0
                try:
                    broadcaster = get_broadcaster()
                    if broadcaster:
                        level = broadcaster.get_audio_level()
                except Exception:
                    pass

                # Update the OBS visualizer bar width
                if self._obs_bridge and self._obs_bridge.enabled:
                    try:
                        self._obs_bridge.update_visualizer_bar(
                            level=level, scene_name=overlay_scene
                        )
                    except Exception:
                        pass  # Non-critical — visualizer is cosmetic

        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.debug(f"YouTube Live/OBS: Visualizer polling error: {e}")

    _FONT_BOLD: str | None = None
    _FONT_REGULAR: str | None = None

    @classmethod
    def _resolve_font(cls, bold: bool = False) -> str:
        cache_key = "_FONT_BOLD" if bold else "_FONT_REGULAR"
        cached = getattr(cls, cache_key)
        if cached is not None:
            return cached

        candidates_bold = [
            "/usr/share/fonts/noto/NotoSans-Bold.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ]
        candidates_regular = [
            "/usr/share/fonts/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        candidates = candidates_bold if bold else candidates_regular

        for path in candidates:
            if os.path.isfile(path):
                setattr(cls, cache_key, path)
                return path

        setattr(cls, cache_key, "")
        return ""

    async def _start_master_ffmpeg(self):
        """Construct the FFmpeg process with Xvfb and Chromium headless screen capture!"""
        import shutil
        xvfb_path = shutil.which("Xvfb")
        chromium_path = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
        
        if not xvfb_path or not chromium_path:
            log.error(f"YouTube Live: FATAL - Missing dependencies! Xvfb: {xvfb_path}, Chromium: {chromium_path}")
            log.error(
                "YouTube Live: Cannot start Chromium+FFmpeg pipeline. Either:\n"
                "  1. Install dependencies: sudo apt install xvfb chromium-browser (or chromium)\n"
                "  2. Or set up OBS Studio with obs-websocket for OBS-native streaming (recommended).\n"
                "     OBS handles the overlay + streaming without needing Xvfb/Chromium."
            )
            return

        if not self.stream_key and not self.rtmp_url:
            log.error("YouTube Live: Cannot start Master Engine (No Configs)")
            return

        primary_url = f"{self.rtmp_url.rstrip('/')}/{self.stream_key}"
        
        # Cleanup previously running headless processes upon restarts
        if self._chromium:
            try:
                self._chromium.kill()
            except Exception:
                pass
        if self._xvfb:
            try:
                self._xvfb.kill()
            except Exception:
                pass
        if self._process:
            try:
                self._process.kill()
            except Exception:
                pass
                
        try:
            os.remove("/tmp/.X99-lock")
        except Exception:
            pass
        try:
            os.remove("/tmp/.X11-unix/X99")
        except Exception:
            pass
        
        # Kill only the Xvfb process WE started (by PID), not all Xvfb
        # instances on the system. Using pkill -f would kill unrelated
        # processes which is dangerous in shared environments.
        if self._xvfb is not None and hasattr(self._xvfb, "pid"):
            try:
                import signal as _signal
                os.kill(self._xvfb.pid, _signal.SIGTERM)
                log.info(f"YouTube Live: Sent SIGTERM to stale Xvfb (PID {self._xvfb.pid})")
            except (ProcessLookupError, PermissionError, OSError):
                pass  # Process already gone or not ours
        log.info(f"YouTube Live: Spawning Headless ({xvfb_path}) overlay capture...")
        
        # 1. Spawn Xvfb virtual frame buffer
        try:
            self._xvfb = await asyncio.create_subprocess_exec(
                xvfb_path, ":99", "-screen", "0", f"{self.WIDTH}x{self.HEIGHT}x24",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await asyncio.sleep(1) # Allow X11 daemon to initialize
        except Exception as e:
            log.error(f"YouTube Live: Failed to launch Xvfb: {e}")
            return

        # 2. Spawn headless Chromium to render the beautiful Flask overlay
        env = os.environ.copy()
        env["DISPLAY"] = ":99"
        # If VA-API is enabled, allow Chromium to use GPU rendering
        if self._use_vaapi:
            # Disable --disable-gpu so Chromium can use GPU compositing
            chromium_flags = [
                "--kiosk", "--no-sandbox", "--disable-dev-shm-usage",
                "--hide-scrollbars", "--autoplay-policy=no-user-gesture-required",
                f"--window-size={self.WIDTH},{self.HEIGHT}", "--incognito",
                # Enable GPU compositing for better overlay rendering
                "--enable-gpu", "--enable-unsafe-swiftshader",
            ]
        else:
            chromium_flags = [
                "--kiosk", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
                "--hide-scrollbars", "--autoplay-policy=no-user-gesture-required",
                f"--window-size={self.WIDTH},{self.HEIGHT}", "--incognito",
            ]
        try:
            self._chromium = await asyncio.create_subprocess_exec(
                chromium_path,
                *chromium_flags,
                "http://127.0.0.1:8080/overlay",
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await asyncio.sleep(5) # Allow page to fully render resources
        except Exception as e:
            log.error(f"YouTube Live: Failed to launch Chromium: {e}")

        # 3. Launch FFmpeg x11grab + audio capture
        # Build video encoding command based on hardware acceleration
        if self._use_vaapi:
            # ── AMD GPU VA-API hardware encoding ──────────────────────────
            # Uses h264_vaapi for dramatically lower CPU usage on AMD GPUs.
            # Requires /dev/dri/renderD128 passed through to the container
            # and AMD_GPU_VAAPI=1 in the environment.
            log.info("YouTube Live: Using VA-API (h264_vaapi) hardware encoding")
            vaapi_init = [
                "-vaapi_device", "/dev/dri/renderD128",
            ]
            video_encode = [
                # Upload x11grab frames to VA-API surface
                "-vf", "setpts=PTS-STARTPTS,format=nv12,hwupload",
                "-c:v", "h264_vaapi",
                "-b:v", f"{self.bitrate_video}k",
                "-maxrate", f"{self.bitrate_video}k",
                "-minrate", f"{self.bitrate_video}k",
                "-bufsize", f"{self.bitrate_video * 2}k",
                "-g", str(self.fps * 2),
                "-keyint_min", str(self.fps * 2),
                "-sc_threshold", "0",
                "-r", str(self.fps),
            ]
        else:
            # ── CPU software encoding (libx264) ────────────────────────────
            # Default path — works everywhere, no GPU needed.
            log.info("YouTube Live: Using software encoding (libx264)")
            vaapi_init = []
            video_encode = [
                "-vf", "setpts=PTS-STARTPTS",
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                "-b:v", f"{self.bitrate_video}k", "-maxrate", f"{self.bitrate_video}k",
                "-minrate", f"{self.bitrate_video}k",
                "-bufsize", f"{self.bitrate_video * 2}k", "-pix_fmt", "yuv420p",
                "-g", str(self.fps * 2), "-keyint_min", str(self.fps * 2),
                "-sc_threshold", "0",
                "-nal-hrd", "cbr",
                "-r", str(self.fps),
            ]

        cmd = [
            "ffmpeg",
            *vaapi_init,
            "-thread_queue_size", "4096",
            "-f", "x11grab", "-video_size", f"{self.WIDTH}x{self.HEIGHT}",
            "-framerate", str(self.fps),
            "-i", ":99.0+0,0",
            # Audio source from the PCMBroadcaster master node
            "-thread_queue_size", "4096",
            "-f", "s16le", "-ar", "48000", "-ac", "2",
            "-i", f"udp://127.0.0.1:{self.udp_port}?pkt_size=3840&buffer_size=262144&fifo_size=262144&overrun_nonfatal=1&reuse=1&timeout=15000000",
            # Normalize ALL timestamps perfectly to 0.0s to align audio with screen 
            "-map", "0:v", "-map", "1:a",
            *video_encode,
            # Audio codec (always AAC — no GPU acceleration needed for audio)
            "-af", "asetpts=PTS-STARTPTS",
            "-c:a", "aac", "-b:a", f"{self.bitrate_audio}k", "-ar", "48000",
            "-max_muxing_queue_size", "9999",
            "-f", "flv", "-flvflags", "no_duration_filesize",
            "-rtmp_live", "live", "-rtmp_buffer", "2000"
        ]

        if self.rtmp_url.startswith("rtmps://"):
            cmd.extend(["-tls_verify", "0"])

        cmd.append(primary_url)

        log.info("YouTube Live: Executing Chromium FFmpeg x11grab RTMP wrapper...")
        
        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            # Start a background task to continuously drain stderr.
            # If stderr is set to PIPE but never read, the kernel buffer fills
            # up and FFmpeg blocks on stderr writes, freezing the stream.
            if self._process.stderr:
                self._stderr_drain_task = asyncio.create_task(self._drain_stderr())
        except Exception as e:
            log.error(f"YouTube Live: FFmpeg invoke failed: {e}")

    async def _drain_stderr(self):
        """Continuously read and discard FFmpeg's stderr output.

        This prevents the stderr pipe buffer from filling up and blocking
        FFmpeg's output thread, which would freeze the entire stream.
        Logged at DEBUG level so it's available for diagnostics but
        doesn't spam the main log.
        """
        if not self._process or not self._process.stderr:
            return
        try:
            while self._running:
                line = await self._process.stderr.readline()
                if not line:
                    # EOF — FFmpeg closed stderr (likely exiting)
                    break
                log.debug(f"YouTube Live/FFmpeg: {line.decode(errors='replace').rstrip()}")
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def _watchdog(self):
        """Monitors Master FFmpeg stream survival."""
        consecutive_failures = 0
        try:
            while self._running:
                await asyncio.sleep(8)
                if self._process and self._process.returncode is not None:
                    consecutive_failures += 1
                    err = b""
                    if self._process.stderr:
                        try:
                            err = await self._process.stderr.read()
                        except Exception:
                            pass
                    self._last_error = f"Exited code {self._process.returncode}"
                    log.error(f"YouTube Live: Master FFmpeg crashed: {self._last_error} | {err[-500:]}")
                    
                    if consecutive_failures > 5:
                        backoff = min(30, 5 * (consecutive_failures - 5))
                        log.error(f"Master Stream halted entirely. Rebooting in {backoff}s...")
                        await asyncio.sleep(backoff)
                        
                    await self._kill_process() # Cleanup dead xvfb instances before reviving!
                    await self._start_master_ffmpeg()
                else:
                    consecutive_failures = 0
        except asyncio.CancelledError:
            pass

    async def _kill_process(self):
        """Native shutdown logic for Master Node + Chromium layer."""
        # Cancel the stderr drain task first
        if self._stderr_drain_task and not self._stderr_drain_task.done():
            self._stderr_drain_task.cancel()
            try:
                await self._stderr_drain_task
            except asyncio.CancelledError:
                pass
            self._stderr_drain_task = None

        if self._process and self._process.returncode is None:
            try:
                if self._process.stdin:
                    self._process.stdin.write(b"q")
                    await self._process.stdin.drain()
                    try:
                        await asyncio.wait_for(self._process.wait(), timeout=3)
                    except asyncio.TimeoutError:
                        pass
            except Exception:
                pass

            if self._process and self._process.returncode is None:
                try:
                    self._process.terminate()
                    await asyncio.wait_for(self._process.wait(), timeout=5)
                except Exception:
                    try:
                        self._process.kill()
                    except Exception:
                        pass
        self._process = None
        
        # Aggressively cleanup Xvfb and Chromium headless instances too
        if hasattr(self, '_chromium') and self._chromium and getattr(self._chromium, 'returncode', None) is None:
            try:
                self._chromium.kill()
            except Exception:
                pass
            self._chromium = None
            
        if hasattr(self, '_xvfb') and self._xvfb and getattr(self._xvfb, 'returncode', None) is None:
            try:
                self._xvfb.kill()
            except Exception:
                pass
            self._xvfb = None
