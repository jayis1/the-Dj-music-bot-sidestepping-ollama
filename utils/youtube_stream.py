"""
utils/youtube_stream.py — YouTube Live streaming for MBot Radio.

Streams the bot's audio output to a YouTube Live event via RTMP.
Requires a YouTube Live stream key (from YouTube Studio → Go Live).

Architecture:
- For each song, a separate FFmpeg process reads the audio URL,
  mixes it with a looping static image, and pushes to YouTube's RTMP ingest.
- When the song ends, the process is killed and a new one starts for the next song.
- Between songs (during DJ intros/TTS/silence), a "waiting" card is shown.
- The DJ's TTS audio is also captured and streamed.

Environment variables:
  YOUTUBE_STREAM_KEY - The stream key from YouTube Studio → Go Live
  YOUTUBE_STREAM_ENABLED - "true" to enable on startup, "false" to disable
  YOUTUBE_STREAM_URL - RTMP URL (default: rtmp://a.rtmp.youtube.com/live2)
  YOUTUBE_STREAM_IMAGE - Path to a static image for the video track (default: assets/stream_card.png)

OBS Overlay:
  The /overlay page is designed as a browser source in OBS. If you use OBS,
  you can skip this FFmpeg approach and just use OBS + Browser Source instead.
  This module is for a headless server where OBS isn't available.
"""

import asyncio
import logging
import os
import time


log = logging.getLogger("youtube-stream")


class YouTubeLiveStreamer:
    """Manages a YouTube Live RTMP stream from the bot's audio.

    Usage:
        streamer = YouTubeLiveStreamer(stream_key="xxxx-xxxx-xxxx")
        await streamer.start()           # Start the stream
        await streamer.play_song(url)    # Stream a song's audio + static image
        await streamer.play_waiting()    # Show "waiting for next track" card
        await streamer.stop()            # Stop streaming
    """

    def __init__(
        self,
        stream_key: str,
        rtmp_url: str = "rtmp://a.rtmp.youtube.com/live2",
        stream_image: str | None = None,
        bitrate_audio: int = 128,
        bitrate_video: int = 1500,
        fps: int = 25,
    ):
        self.stream_key = stream_key
        self.rtmp_url = rtmp_url
        self.stream_image = stream_image
        self.bitrate_audio = bitrate_audio
        self.bitrate_video = bitrate_video
        self.fps = fps

        self._process: asyncio.subprocess.Process | None = None
        self._running = False
        self._current_url: str | None = None
        self._restart_task: asyncio.Task | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def current_url(self) -> str | None:
        return self._current_url

    # ── Lifecycle ──────────────────────────────────────────────────

    async def start(self):
        """Start the YouTube Live stream with a waiting card."""
        if self._running:
            log.warning("YouTube Live: Already streaming")
            return
        self._running = True
        log.info(
            f"YouTube Live: Starting stream → {self.rtmp_url}/...{self.stream_key[-6:]}"
        )
        # Start with a waiting screen between songs
        await self.play_waiting()
        # Start a watcher that auto-reconnects if the FFmpeg process dies
        self._restart_task = asyncio.create_task(self._watchdog())

    async def stop(self):
        """Stop the stream."""
        self._running = False
        if self._restart_task:
            self._restart_task.cancel()
            self._restart_task = None
        await self._kill_process()
        log.info("YouTube Live: Stream stopped")

    async def play_song(self, audio_url: str, title: str = ""):
        """Switch the stream to playing a song's audio with a static image.

        This kills the current FFmpeg process and starts a new one that:
        1. Reads audio from the same URL the Discord bot plays
        2. Generates a video track from a static image (with song title overlay)
        3. Pushes both to YouTube's RTMP ingest

        Args:
            audio_url: The direct audio stream URL (same one FFmpegPCMAudio uses)
            title: The song title (shown on the stream card)
        """
        if not self._running:
            return

        self._current_url = audio_url
        await self._kill_process()
        log.info(f"YouTube Live: Streaming song → {title or audio_url[:80]}")
        await self._start_ffmpeg_song(audio_url, title)

    async def play_waiting(self, message: str = "Waiting for next track..."):
        """Show a "waiting" card with silence between songs.

        This plays a looping static image with no audio.
        """
        if not self._running:
            return

        self._current_url = None
        await self._kill_process()
        log.info("YouTube Live: Showing waiting card")
        await self._start_ffmpeg_waiting(message)

    async def play_tts(self, tts_path: str, text: str = ""):
        """Stream a TTS file (DJ intro, AI side host) to YouTube Live.

        This creates a short FFmpeg process that plays the TTS audio
        with a "DJ Speaking" card.

        Args:
            tts_path: Path to the TTS audio file (WAV or MP3)
            text: The text being spoken (for the card)
        """
        if not self._running or not tts_path or not os.path.isfile(tts_path):
            return

        await self._kill_process()
        log.info(f"YouTube Live: Streaming TTS → {text[:60]}...")
        await self._start_ffmpeg_tts(tts_path, text)

    # ── FFmpeg process management ───────────────────────────────────

    async def _kill_process(self):
        """Kill the current FFmpeg process gracefully."""
        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass
        self._process = None

    async def _start_ffmpeg_song(self, audio_url: str, title: str):
        """Start FFmpeg: audio from URL + static image → RTMP."""
        image = self._resolve_image()
        # Build the drawtext filter for the song title
        # Escape special chars for FFmpeg drawtext
        safe_title = (
            title.replace("'", "\\'").replace(":", "\\:").replace("%", "%%")[:80]
        )
        drawtext = (
            f"drawtext=text='{safe_title}':"
            f"fontcolor=white:fontsize=24:"
            f"x=(w-text_w)/2:y=h*0.7:"
            f"box=1:boxcolor=black@0.6:boxborderw=5"
        )

        cmd = [
            "ffmpeg",
            "-re",  # Real-time pacing
            "-loop",
            "1",  # Loop the image
            "-i",
            image,  # Static image input
            "-i",
            audio_url,  # Audio stream input
            "-c:v",
            "libx264",  # H.264 video codec
            "-preset",
            "veryfast",  # Fast encoding (low latency)
            "-b:v",
            f"{self.bitrate_video}k",
            "-r",
            str(self.fps),
            "-pix_fmt",
            "yuv420p",
            "-vf",
            drawtext,  # Title overlay
            "-c:a",
            "aac",  # AAC audio codec
            "-b:a",
            f"{self.bitrate_audio}k",
            "-ar",
            "44100",
            "-ac",
            "2",  # Stereo
            "-f",
            "flv",  # Flash Video container (RTMP)
            "-flvflags",
            "no_duration_filesize",
            f"{self.rtmp_url}/{self.stream_key}",
        ]
        await self._run_ffmpeg(cmd, "song")

    async def _start_ffmpeg_waiting(self, message: str):
        """Start FFmpeg: looping image + generated silence → RTMP."""
        image = self._resolve_image()
        safe_msg = (
            message.replace("'", "\\'").replace(":", "\\:").replace("%", "%%")[:60]
        )
        drawtext = (
            f"drawtext=text='{safe_msg}':"
            f"fontcolor=white@0.6:fontsize=18:"
            f"x=(w-text_w)/2:y=h*0.7:"
            f"box=1:boxcolor=black@0.4:boxborderw=4"
        )

        cmd = [
            "ffmpeg",
            "-re",
            "-loop",
            "1",
            "-i",
            image,
            "-f",
            "lavfi",  # Generate silent audio
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-b:v",
            f"{self.bitrate_video}k",
            "-r",
            str(self.fps),
            "-pix_fmt",
            "yuv420p",
            "-vf",
            drawtext,
            "-c:a",
            "aac",
            "-b:a",
            f"{self.bitrate_audio}k",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-f",
            "flv",
            "-flvflags",
            "no_duration_filesize",
            "-shortest",
            f"{self.rtmp_url}/{self.stream_key}",
        ]
        await self._run_ffmpeg(cmd, "waiting")

    async def _start_ffmpeg_tts(self, tts_path: str, text: str):
        """Start FFmpeg: TTS audio + static image → RTMP."""
        image = self._resolve_image()
        label = "🎙️ DJ Speaking"
        safe_text = text.replace("'", "\\'").replace(":", "\\:").replace("%", "%%")[:60]
        drawtext = (
            f"drawtext=text='{label}':"
            f"fontcolor=white:fontsize=22:"
            f"x=(w-text_w)/2:y=h*0.65:"
            f"box=1:boxcolor=black@0.6:boxborderw=5,"
            f"drawtext=text='{safe_text}':"
            f"fontcolor=white@0.8:fontsize=14:"
            f"x=(w-text_w)/2:y=h*0.78:"
            f"box=1:boxcolor=black@0.4:boxborderw=3"
        )

        cmd = [
            "ffmpeg",
            "-re",
            "-loop",
            "1",
            "-i",
            image,
            "-i",
            tts_path,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-b:v",
            f"{self.bitrate_video}k",
            "-r",
            str(self.fps),
            "-pix_fmt",
            "yuv420p",
            "-vf",
            drawtext,
            "-c:a",
            "aac",
            "-b:a",
            f"{self.bitrate_audio}k",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-f",
            "flv",
            "-flvflags",
            "no_duration_filesize",
            "-shortest",
            f"{self.rtmp_url}/{self.stream_key}",
        ]
        await self._run_ffmpeg(cmd, "tts")

    async def _run_ffmpeg(self, cmd: list, label: str):
        """Start an FFmpeg subprocess and monitor it."""
        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            log.debug(f"YouTube Live: FFmpeg {label} started (pid={self._process.pid})")
            # Read stderr for errors but don't block
            asyncio.create_task(self._drain_stderr(label))
        except FileNotFoundError:
            log.error(
                "YouTube Live: ffmpeg not found — install with: apt install ffmpeg"
            )
            self._running = False
        except Exception as e:
            log.error(f"YouTube Live: Failed to start FFmpeg {label}: {e}")
            self._running = False

    async def _drain_stderr(self, label: str):
        """Drain FFmpeg stderr to prevent pipe buffer deadlocks."""
        if not self._process or not self._process.stderr:
            return
        try:
            while True:
                line = await self._process.stderr.readline()
                if not line:
                    break
                txt = line.decode("utf-8", errors="replace").strip()
                if "error" in txt.lower() or "warning" in txt.lower():
                    log.debug(f"YouTube Live [{label}]: {txt[:120]}")
        except Exception:
            pass

    async def _watchdog(self):
        """Watchdog that restarts the waiting card if FFmpeg dies unexpectedly."""
        try:
            while self._running:
                await asyncio.sleep(5)
                if (
                    self._process
                    and self._process.returncode is not None
                    and self._running
                ):
                    # FFmpeg process died while we're supposed to be streaming
                    log.warning(
                        f"YouTube Live: FFmpeg exited with code {self._process.returncode}, "
                        "restarting waiting card..."
                    )
                    await self.play_waiting()
        except asyncio.CancelledError:
            pass

    # ── Helpers ─────────────────────────────────────────────────────

    def _resolve_image(self) -> str:
        """Resolve the stream card image path.

        Falls back to a generated black frame if no image is configured.
        """
        if self.stream_image and os.path.isfile(self.stream_image):
            return self.stream_image
        # Check default location
        default = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "assets", "stream_card.png"
        )
        if os.path.isfile(default):
            return default
        # No image found — use a color source as fallback
        # FFmpeg can generate a solid color without an image file
        return os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "assets", "logo.png"
        )
