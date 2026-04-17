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
        bitrate_audio: int = 128,
        bitrate_video: int = 2500,
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
        """Deprecated hook: The Broadcaster handles PCM streaming directly. Just update the HUD."""
        self.update_hud(title=title)

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
        """Engage the monolithic RTMP connection."""
        await self._kill_process()

        image = self._resolve_image()
        safe_station = self._safe_text(self.station_name, 30)
        W, H = self.WIDTH, self.HEIGHT
        fps = self.fps

        font_bold = self._resolve_font(bold=True)
        font_reg = self._resolve_font(bold=False)
        font_opt_bold = f"fontfile='{font_bold}':" if font_bold else ""
        font_opt_reg = f"fontfile='{font_reg}':" if font_reg else ""

        # Pre-initialize disk text files safely
        for path in [TXT_TITLE, TXT_DJ, TXT_WAITING]:
            if not os.path.exists(path):
                with open(path, "w") as f:
                    f.write("")

        cmd = ["ffmpeg"]

        # Input 0: Physical GUI Image Base / Static Logo
        if image:
            cmd.extend(["-loop", "1", "-framerate", str(fps), "-i", image])
        else:
            cmd.extend(["-f", "lavfi", "-i", f"color=c=0x0f0f23:s={W}x{H}:r={fps}"])

        # Input 1: The Raw PCM UDP Master Audio Socket (driven by PCMBroadcaster)
        cmd.extend([
            "-f", "s16le", 
            "-ar", "48000", 
            "-ac", "2", 
            "-thread_queue_size", "1024",
            "-i", f"udp://127.0.0.1:{self.udp_port}?pkt_size=3840&buffer_size=65536&reuse=1&timeout=15000000"
        ])

        # Input 2: Animated GIF overlay wrapper
        gif_path = self._resolve_gif()
        has_gif = gif_path and os.path.isfile(gif_path)
        if has_gif:
            cmd.extend(["-stream_loop", "-1", "-ignore_loop", "0", "-i", gif_path])

        # Build native video filters
        vf = ""
        if image:
            vf += f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
            vf += f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x0f0f23,"
            vf += f"format=yuva420p[bg];"
        else:
            vf += f"[0:v]format=yuva420p[bg];"

        # Integrate GIF if available
        if has_gif:
            vf += f"[2:v]split=2[gif_big_in][gif_small_in];"
            vf += f"[gif_big_in]scale=120:120:force_original_aspect_ratio=decrease,format=yuva420p[gif_big];"
            vf += f"[gif_small_in]scale=40:40:force_original_aspect_ratio=decrease,format=yuva420p[gif_small];"
            vf += f"[bg][gif_big]overlay=W-w-30:30:format=auto[with_gif_1];"
            vf += f"[with_gif_1][gif_small]overlay=260:145:format=auto[vbase];"
        else:
            vf += f"[bg]copy[vbase];"

        # Apply Dynamic Text reload hooks directly to the pipeline
        filter_chain = "[vbase]"
        
        # 1. Station text (static)
        filter_chain += (
            f"drawtext={font_opt_bold}text='{safe_station}':"
            f"fontcolor=white:fontsize=42:x=450:y=150:shadowcolor=black:shadowx=2:shadowy=2,"
        )
        # 2. Main Title HUD (dynamic txt reload)
        filter_chain += (
            f"drawtext={font_opt_bold}textfile='{TXT_TITLE}':reload=1:"
            f"fontcolor=gold:fontsize=56:x=450:y=210:shadowcolor=black:shadowx=2:shadowy=2,"
        )
        # 3. DJ Hook HUD (dynamic txt reload)
        filter_chain += (
            f"drawtext={font_opt_reg}textfile='{TXT_DJ}':reload=1:"
            f"fontcolor=0x00FFCC:fontsize=36:x=450:y=280:shadowcolor=black:shadowx=1:shadowy=1,"
        )
        # 4. Waiting / Bottom Ticker Strip HUD (dynamic txt reload)
        filter_chain += (
            f"drawbox=y={H}-40:color=black@0.6:width=iw:height=40:t=fill,"
            f"drawtext={font_opt_reg}textfile='{TXT_WAITING}':reload=1:"
            f"fontcolor=white:fontsize=24:x=(w-text_w)/2:y={H}-32"
        )
        filter_chain += "[outv]"

        vf += filter_chain
        
        cmd.extend(["-filter_complex", vf])
        cmd.extend(["-map", "[outv]", "-map", "1:a"]) # Tie video to raw UDP audio stream 

        # ── RTMP encoding settings ──
        cmd.extend([
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", 
            "-b:v", f"{self.bitrate_video}k", "-maxrate", f"{self.bitrate_video}k", 
            "-bufsize", f"{self.bitrate_video*2}k", "-pix_fmt", "yuv420p", "-g", str(fps * 2),
            "-c:a", "aac", "-b:a", f"{self.bitrate_audio}k", "-ar", "48000",
            "-f", "flv", "-flvflags", "no_duration_filesize",
            "-rtmp_live", "live", "-rtmp_buffer", "2000"
        ])

        if "?" in self.rtmp_url:
            base, query = self.rtmp_url.split("?", 1)
            primary_url = f"{base.rstrip('/')}/{self.stream_key}?{query}"
        else:
            primary_url = f"{self.rtmp_url.rstrip('/')}/{self.stream_key}"
        if self.rtmp_url.startswith("rtmps://"):
            cmd.extend(["-tls_verify", "0"])

        cmd.append(primary_url)

        log.info(f"YouTube Live: Executing single unified FFmpeg RTMP wrapper...")
        
        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
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
                        
                    await self._start_master_ffmpeg()
                else:
                    consecutive_failures = 0
        except asyncio.CancelledError:
            pass

    async def _kill_process(self):
        """Native shutdown logic for Master Node."""
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
