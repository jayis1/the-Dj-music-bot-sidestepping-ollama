"""
utils/youtube_stream.py — Master Node RTMP Streamer for MBot Radio.

Runs a single 24/7 permanent FFmpeg Master Engine feeding rtmp:// connections.
Listens to native raw UDP PCM bytes from the PCMBroadcaster subsystem seamlessly,
completely eliminating FFmpeg TCP handshake tearing and latency drops natively.
"""

import asyncio
import logging
import os
import time

log = logging.getLogger("youtube-stream")

# Overlays for FFmpeg dynamic HUD reloads
TXT_TITLE = "/tmp/radio_title.txt"
TXT_DJ = "/tmp/radio_dj.txt"
TXT_WAITING = "/tmp/radio_waiting.txt"

class YouTubeLiveStreamer:
    """Manages the Master YouTube Live RTMP connection via UDP polling."""

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

        self._process: asyncio.subprocess.Process | None = None
        self._running = False
        self._watchdog_task: asyncio.Task | None = None
        self._started_at: float = 0
        self._last_error: str = ""
        
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
        """Invoke the monolithic YouTube RTMP connection."""
        if self._running:
            return
            
        self._running = True
        self._started_at = time.time()
        log.info(f"YouTube Live: Master Engine starting → UDP port {self.udp_port}")
        await self._start_master_ffmpeg()
        self._watchdog_task = asyncio.create_task(self._watchdog())

    async def stop(self):
        """Teardown the stream completely."""
        self._running = False
        if self._watchdog_task:
            self._watchdog_task.cancel()
            self._watchdog_task = None
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
        return text.replace("'", "\\'").replace(":", "\\:").replace("%", "%%")[:max_len]

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
        if not self.stream_key and not self.rtmp_url:
            log.error("YouTube Live: Cannot start Master Engine (No Configs)")
            return

        primary_url = f"{self.rtmp_url.rstrip('/')}/{self.stream_key}"
        
        log.info("YouTube Live: Spawning Headless Xvfb overlay capture...")
        
        # 1. Spawn Xvfb virtual frame buffer
        try:
            self._xvfb = await asyncio.create_subprocess_exec(
                "Xvfb", ":99", "-screen", "0", f"{self.WIDTH}x{self.HEIGHT}x24",
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
        try:
            self._chromium = await asyncio.create_subprocess_exec(
                "chromium", 
                "--kiosk", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
                "--hide-scrollbars", "--autoplay-policy=no-user-gesture-required",
                f"--window-size={self.WIDTH},{self.HEIGHT}", "--incognito",
                "http://127.0.0.1:8080/overlay",
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await asyncio.sleep(5) # Allow page to fully render resources
        except Exception as e:
            log.error(f"YouTube Live: Failed to launch Chromium: {e}")

        # 3. Launch FFmpeg x11grab + audio capture
        cmd = [
            "ffmpeg",
            "-thread_queue_size", "2048",
            "-f", "x11grab", "-video_size", f"{self.WIDTH}x{self.HEIGHT}",
            "-framerate", str(self.fps),
            "-i", ":99.0+0,0",
            # Audio source from the PCMBroadcaster master node
            "-f", "s16le", "-ar", "48000", "-ac", "2", "-thread_queue_size", "1024",
            "-i", f"udp://127.0.0.1:{self.udp_port}?pkt_size=3840&buffer_size=65536&reuse=1&timeout=15000000",
            # Map explicitly
            "-map", "0:v", "-map", "1:a",
            # Streaming Codecs
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-b:v", f"{self.bitrate_video}k", "-maxrate", f"{self.bitrate_video}k", 
            "-bufsize", f"{self.bitrate_video * 2}k", "-pix_fmt", "yuv420p", "-g", str(self.fps * 2),
            "-c:a", "aac", "-b:a", f"{self.bitrate_audio}k", "-ar", "48000",
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
        except Exception as e:
            log.error(f"YouTube Live: FFmpeg invoke failed: {e}")

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
                        except:
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
        if self._process and self._process.returncode is None:
            try:
                if self._process.stdin:
                    self._process.stdin.write(b"q")
                    self._process.stdin.flush()
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
        if hasattr(self, '_chromium') and self._chromium and self._chromium.returncode is None:
            try:
                self._chromium.kill()
            except Exception:
                pass
            self._chromium = None
            
        if hasattr(self, '_xvfb') and self._xvfb and self._xvfb.returncode is None:
            try:
                self._xvfb.kill()
            except Exception:
                pass
            self._xvfb = None
