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
        
        self.update_hud(waiting="Booting Mainframes...")

    @property
    def is_running(self) -> bool:
        return self._running

    def update_hud(self, title="", dj="", waiting=""):
        """Dynamically overwrite FFmpeg GUI layout texts instantly on disk."""
        try:
            with open(TXT_TITLE, "w") as f:
                f.write(self._safe_text(title, 80))
            with open(TXT_DJ, "w") as f:
                f.write(self._safe_text(dj, 80))
            with open(TXT_WAITING, "w") as f:
                f.write(self._safe_text(waiting, 80))
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

    async def start_autonomous(self, playlist_url: str, *args, **kwargs):
        """Start the stream (Called by cogs/music.py). 
        Actual playback logic is now handled exclusively by Voice/Broadcaster queues.
        """
        await self.start()
        self.update_hud(waiting="Autonomous Playback Initiated...")

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

        Sets the RTMP server + stream key in OBS, then calls start_stream().
        OBS handles all video (scenes + browser overlay) and audio capture,
        and is the single streaming point to YouTube Live.

        Returns True if streaming started successfully, False otherwise.
        """
        if not self._obs_bridge:
            return False

        try:
            # Configure OBS stream settings: RTMP server + stream key
            rtmp_endpoint = f"{self.rtmp_url.rstrip('/')}"
            log.info(
                f"YouTube Live/OBS: Configuring stream → {rtmp_endpoint} "
                f"(key: {self.stream_key[:4]}...)"
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

            # Ensure the overlay browser source exists in the current scene.
            # The "📺 Overlay Only" scene already has it, but we make sure
            # OBS can reach the overlay page.
            overlay_url = os.environ.get("OBS_OVERLAY_URL", "http://localhost:8080/overlay")
            log.info(f"YouTube Live/OBS: Overlay URL → {overlay_url}")

            # Start OBS streaming
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

            # Switch to the overlay scene for YouTube Live
            overlay_scene = os.environ.get(
                "OBS_SCENE_OVERLAY", "📺 Overlay Only"
            )
            self._obs_bridge.switch_scene(overlay_scene)
            log.info(f"YouTube Live/OBS: Switched to scene '{overlay_scene}'")

            return True

        except Exception as e:
            log.error(f"YouTube Live/OBS: Exception starting stream: {e}")
            return False

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
            "-i", f"udp://127.0.0.1:{self.udp_port}?pkt_size=3840&buffer_size=65536&reuse=1&timeout=15000000",
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
