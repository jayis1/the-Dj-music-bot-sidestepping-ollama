"""
utils/youtube_stream.py — YouTube Live streaming for MBot Radio.

Streams music to a YouTube Live event via RTMP, with animated video cards
showing song thumbnails, titles, and station branding.

Two streaming modes:

1. **Mirror mode** (default): Shadows the Discord bot's audio playback.
   The streamer is notified by the music cog whenever a song plays,
   DJ speaks, or playback stops. No Discord voice connection needed for
   the stream itself — the FFmpeg process pulls audio directly from URLs.

2. **Autonomous mode** (24/7): The streamer runs its own playlist scheduler.
   It pulls songs from a YouTube playlist URL, resolves audio URLs via yt-dlp,
   and plays them one after another with auto-advance. When the playlist
   runs out, it can loop or pull fresh entries. DJ TTS intros play between
   songs. No Discord interaction required — perfect for headless servers.

Video Card Layout (1280x720):
  ┌──────────────────────────────────────────────────────┐
  │  ┌──────────┐  📻 Station Name                      │
  │  │ Thumbnail │  Song Title (large)                   │
  │  │  360x360  │  ♪ Now Playing                       │
  │  │           │                                       │
  │  │  🎬GIF🎬  │                                       │
  │  └──────────┘                                       │
  │             ▶ LIVE • Station Name   🎬GIF🎬          │
  └──────────────────────────────────────────────────────┘

Environment variables:
  YOUTUBE_STREAM_KEY     - Stream key from YouTube Studio → Go Live
  YOUTUBE_STREAM_ENABLED - "true" to enable on startup
  YOUTUBE_STREAM_URL     - RTMP URL (default: rtmp://a.rtmp.youtube.com/live2)
  YOUTUBE_STREAM_IMAGE    - Path to station logo/card image (default: assets/logo.png)
  YOUTUBE_STREAM_GIF     - Path to animated GIF overlay (default: assets/giphy.gif)
  YOUTUBE_STREAM_PLAYLIST - Default playlist URL for autonomous mode
"""

import asyncio
import json
import logging
import os
import random
import time
import hashlib
import subprocess
import aiohttp
from pathlib import Path

log = logging.getLogger("youtube-stream")

# ── Thumbnail cache ──────────────────────────────────────────────────────
_THUMBNAIL_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "yt_stream_cache"
)


async def download_thumbnail(url: str) -> str | None:
    """Download a thumbnail image and cache it locally.

    Returns the local file path, or None if download fails.
    Cache is keyed by URL hash so we don't re-download the same image.
    """
    if not url:
        return None

    os.makedirs(_THUMBNAIL_CACHE_DIR, exist_ok=True)

    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    ext = ".jpg"
    if url.endswith(".png"):
        ext = ".png"
    elif url.endswith(".webp"):
        ext = ".webp"
    cache_path = os.path.join(_THUMBNAIL_CACHE_DIR, f"{url_hash}{ext}")

    if os.path.exists(cache_path):
        age_hours = (time.time() - os.path.getmtime(cache_path)) / 3600
        if age_hours < 168:  # 7 days
            return cache_path

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    with open(cache_path, "wb") as f:
                        f.write(await resp.read())
                    log.info(f"YouTube Live: Downloaded thumbnail → {cache_path}")
                    return cache_path
                else:
                    log.warning(
                        f"YouTube Live: Thumbnail download failed ({resp.status})"
                    )
                    return None
    except Exception as e:
        log.warning(f"YouTube Live: Thumbnail download error: {e}")
        return None


# ── Playlist state persistence ────────────────────────────────────────────
_STREAM_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "yt_stream_state.json"
)


def _load_stream_state() -> dict:
    """Load persisted autonomous stream state (playlist position, etc)."""
    try:
        if os.path.isfile(_STREAM_STATE_FILE):
            with open(_STREAM_STATE_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        log.warning(f"YouTube Live: Failed to load stream state: {e}")
    return {}


def _save_stream_state(state: dict):
    """Persist autonomous stream state so it survives restarts."""
    try:
        with open(_STREAM_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log.warning(f"YouTube Live: Failed to save stream state: {e}")


class YouTubeLiveStreamer:
    """Manages a YouTube Live RTMP stream.

    Two modes:
        - Mirror mode: Call play_song()/play_tts()/play_waiting() from
          the music cog. The streamer just mirrors whatever Discord is doing.
        - Autonomous mode: Call start_autonomous() with a playlist URL.
          The streamer resolves and plays songs on its own, 24/7.

    Usage (mirror):
        streamer = YouTubeLiveStreamer(stream_key="xxxx-xxxx-xxxx")
        await streamer.start()
        await streamer.play_song(url, title, thumbnail)
        await streamer.play_waiting()
        await streamer.stop()

    Usage (autonomous / 24/7):
        streamer = YouTubeLiveStreamer(stream_key="xxxx-xxxx-xxxx")
        await streamer.start_autonomous(playlist_url="https://youtube.com/playlist?list=...")
        # ...it runs forever until you call stop()
        await streamer.stop()
    """

    WIDTH = 1280
    HEIGHT = 720

    def __init__(
        self,
        stream_key: str,
        rtmp_url: str = "rtmp://a.rtmp.youtube.com/live2",
        stream_image: str | None = None,
        stream_gif: str | None = None,
        bitrate_audio: int = 128,
        bitrate_video: int = 2500,
        fps: int = 30,
        station_name: str = "MBot Radio",
    ):
        self.stream_key = stream_key
        self.rtmp_url = rtmp_url
        self.stream_image = stream_image
        self.stream_gif = stream_gif
        self.bitrate_audio = bitrate_audio
        self.bitrate_video = bitrate_video
        self.fps = fps
        self.station_name = station_name

        self._process: asyncio.subprocess.Process | None = None
        self._running = False
        self._current_url: str | None = None
        self._restart_task: asyncio.Task | None = None
        self._stderr_lines: list[str] = []  # Recent stderr for debugging

        # Current song info (for overlay / Mission Control)
        self._current_title: str = ""
        self._current_thumbnail: str | None = None

        # ── Autonomous mode state ──────────────────────────────────
        self._autonomous = False
        self._autonomous_task: asyncio.Task | None = None
        self._playlist_url: str = ""
        self._playlist_entries: list[dict] = []  # [{id, title, url, thumbnail}, ...]
        self._playlist_index: int = 0
        self._playlist_loop: bool = True
        self._shuffle: bool = False
        self._song_count: int = 0  # Total songs streamed (for status)
        self._started_at: float = 0  # Timestamp when stream started
        self._last_error: str = ""  # Most recent error (for Mission Control)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def current_url(self) -> str | None:
        return self._current_url

    @property
    def current_title(self) -> str:
        return self._current_title

    @property
    def is_autonomous(self) -> bool:
        return self._autonomous

    @property
    def playlist_url(self) -> str:
        return self._playlist_url

    @property
    def playlist_size(self) -> int:
        return len(self._playlist_entries)

    @property
    def playlist_index(self) -> int:
        return self._playlist_index

    @property
    def song_count(self) -> int:
        return self._song_count

    @property
    def uptime_seconds(self) -> float:
        if not self._started_at:
            return 0
        return time.time() - self._started_at

    @property
    def last_error(self) -> str:
        return self._last_error

    def get_status(self) -> dict:
        """Return a full status dict for Mission Control / API endpoints."""
        return {
            "running": self._running,
            "autonomous": self._autonomous,
            "current_title": self._current_title,
            "current_url": self._current_url,
            "playlist_url": self._playlist_url,
            "playlist_size": len(self._playlist_entries),
            "playlist_index": self._playlist_index,
            "playlist_loop": self._playlist_loop,
            "shuffle": self._shuffle,
            "song_count": self._song_count,
            "uptime_seconds": round(self.uptime_seconds, 1),
            "last_error": self._last_error,
            "ffmpeg_pid": self._process.pid
            if self._process and self._process.returncode is None
            else None,
        }

    # ── Lifecycle ──────────────────────────────────────────────────

    async def start(self):
        """Start the YouTube Live stream with a waiting card (mirror mode)."""
        if self._running:
            log.warning("YouTube Live: Already streaming")
            return
        self._running = True
        self._started_at = time.time()
        log.info(
            f"YouTube Live: Starting stream → {self.rtmp_url}/...{self.stream_key[-6:]}"
        )
        await self.play_waiting()
        self._restart_task = asyncio.create_task(self._watchdog())

    async def start_autonomous(
        self,
        playlist_url: str,
        loop_playlist: bool = True,
        shuffle: bool = False,
    ):
        """Start the stream in autonomous 24/7 mode.

        Resolves a YouTube playlist, then plays songs one after another
        via FFmpeg → RTMP. When the playlist runs out, loops from the
        start (if loop_playlist=True). No Discord connection needed.

        Args:
            playlist_url: YouTube playlist URL or search query
            loop_playlist: Whether to loop the playlist when it ends
            shuffle: Whether to shuffle the playlist order
        """
        if self._running:
            log.warning(
                "YouTube Live: Already streaming — stopping existing stream first"
            )
            await self.stop()

        self._autonomous = True
        self._playlist_url = playlist_url
        self._playlist_loop = loop_playlist
        self._shuffle = shuffle
        self._running = True
        self._started_at = time.time()

        log.info(
            f"YouTube Live: Starting AUTONOMOUS stream → {self.rtmp_url}/...{self.stream_key[-6:]}"
        )
        log.info(
            f"YouTube Live: Playlist: {playlist_url} (loop={loop_playlist}, shuffle={shuffle})"
        )

        # Load persisted state (playlist position from last run)
        state = _load_stream_state()
        saved_index = state.get("playlist_index", 0)
        saved_url = state.get("playlist_url", "")
        if saved_url == playlist_url:
            self._playlist_index = saved_index
            log.info(f"YouTube Live: Resuming from playlist position {saved_index}")

        # Start the autonomous scheduler as a background task
        self._autonomous_task = asyncio.create_task(self._run_autonomous_loop())
        self._restart_task = asyncio.create_task(self._watchdog())

    async def stop(self):
        """Stop the stream (works for both mirror and autonomous mode)."""
        was_autonomous = self._autonomous
        self._running = False
        self._autonomous = False

        if self._autonomous_task:
            self._autonomous_task.cancel()
            self._autonomous_task = None

        if self._restart_task:
            self._restart_task.cancel()
            self._restart_task = None

        await self._kill_process()

        if was_autonomous:
            # Save state so we can resume later
            _save_stream_state(
                {
                    "playlist_url": self._playlist_url,
                    "playlist_index": self._playlist_index,
                    "song_count": self._song_count,
                    "last_running": time.time(),
                }
            )

        log.info("YouTube Live: Stream stopped")

    async def skip_song(self):
        """Skip to the next song in autonomous mode.

        Kills the current FFmpeg process, which triggers the autonomous
        loop to advance to the next track.
        """
        if not self._autonomous:
            return
        log.info("YouTube Live: Skipping to next song (autonomous)")
        await self._kill_process()

    # ── Mirror mode playback ───────────────────────────────────────

    async def play_song(
        self, audio_url: str, title: str = "", thumbnail: str | None = None
    ):
        """Switch the stream to playing a song (mirror mode).

        Args:
            audio_url: Direct audio stream URL
            title: Song title for the video card
            thumbnail: URL or local path to the song's thumbnail
        """
        if not self._running:
            return
        if self._autonomous:
            log.warning("YouTube Live: play_song() called in autonomous mode — ignored")
            return

        self._current_url = audio_url
        self._current_title = title
        await self._kill_process()

        thumb_path = None
        if thumbnail:
            if thumbnail.startswith("http"):
                thumb_path = await download_thumbnail(thumbnail)
            elif os.path.isfile(thumbnail):
                thumb_path = thumbnail

        self._current_thumbnail = thumb_path
        log.info(f"YouTube Live: Streaming song → {title or audio_url[:80]}")
        await self._start_ffmpeg_song(audio_url, title, thumb_path)

    async def play_waiting(self, message: str = ""):
        """Show a "waiting" card between songs (mirror mode)."""
        if not self._running:
            return

        self._current_url = None
        self._current_title = (
            message or f"Waiting for next track... | {self.station_name}"
        )
        self._current_thumbnail = None
        await self._kill_process()
        log.info("YouTube Live: Showing waiting card")
        await self._start_ffmpeg_waiting(self._current_title)

    async def play_tts(self, tts_path: str, text: str = ""):
        """Stream a TTS file to YouTube Live (mirror mode)."""
        if not self._running or not tts_path or not os.path.isfile(tts_path):
            return

        await self._kill_process()
        log.info(f"YouTube Live: Streaming TTS → {text[:60]}...")
        await self._start_ffmpeg_tts(tts_path, text)

    # ── Autonomous mode scheduler ──────────────────────────────────

    async def _run_autonomous_loop(self):
        """Main loop for autonomous 24/7 streaming.

        1. Load the playlist via yt-dlp
        2. Play each song via FFmpeg → RTMP
        3. When the song ends (FFmpeg process exits), advance to next
        4. Loop or stop when playlist runs out
        """
        try:
            # Load the playlist
            await self._load_playlist()
            if not self._playlist_entries:
                self._last_error = "No tracks found in playlist"
                log.error(f"YouTube Live: {self._last_error}")
                await self.play_waiting(
                    "No tracks found in playlist — add a playlist URL"
                )
                return

            # Start with waiting card
            await self.play_waiting(f"{self.station_name} Radio — Starting Soon...")

            while self._running:
                # Advance to next track
                if self._playlist_index >= len(self._playlist_entries):
                    if self._playlist_loop:
                        self._playlist_index = 0
                        if self._shuffle:
                            random.shuffle(self._playlist_entries)
                        log.info("YouTube Live: Playlist looped — starting over")
                        # Reload playlist to pick up additions
                        await self._load_playlist()
                    else:
                        log.info(
                            "YouTube Live: Playlist exhausted — showing waiting card"
                        )
                        await self.play_waiting(
                            f"Playlist ended — {self.station_name} Radio Standby"
                        )
                        # Keep the waiting card running forever
                        return

                # Get the current track
                entry = self._playlist_entries[self._playlist_index]
                title = entry.get("title", "Unknown")
                webpage_url = entry.get("webpage_url", "")
                thumbnail = entry.get("thumbnail", "")

                if not webpage_url:
                    log.warning(f"YouTube Live: Skipping track with no URL: {title}")
                    self._playlist_index += 1
                    continue

                # Resolve the actual audio stream URL via yt-dlp
                log.info(
                    f"YouTube Live: [{self._playlist_index + 1}/{len(self._playlist_entries)}] "
                    f"Resolving: {title}"
                )

                try:
                    audio_url = await self._resolve_audio_url(webpage_url)
                except Exception as e:
                    self._last_error = f"Failed to resolve '{title}': {e}"
                    log.error(f"YouTube Live: {self._last_error}")
                    self._playlist_index += 1
                    # Show a brief waiting card, then try next track
                    await self.play_waiting(
                        f"Skipping: {title[:40]}... — resolving next track"
                    )
                    await asyncio.sleep(3)
                    continue

                if not audio_url:
                    self._last_error = f"No audio URL for: {title}"
                    log.error(f"YouTube Live: {self._last_error}")
                    self._playlist_index += 1
                    continue

                # Download thumbnail
                thumb_path = None
                if thumbnail:
                    if thumbnail.startswith("http"):
                        thumb_path = await download_thumbnail(thumbnail)
                    elif os.path.isfile(thumbnail):
                        thumb_path = thumbnail

                # Play the song
                self._current_title = title
                self._current_url = audio_url
                self._current_thumbnail = thumb_path
                self._song_count += 1

                log.info(
                    f"YouTube Live: ► Playing [{self._playlist_index + 1}/{len(self._playlist_entries)}] "
                    f"#{self._song_count}: {title}"
                )

                await self._start_ffmpeg_song(audio_url, title, thumb_path)

                # Save state every 5 songs
                if self._song_count % 5 == 0:
                    _save_stream_state(
                        {
                            "playlist_url": self._playlist_url,
                            "playlist_index": self._playlist_index + 1,
                            "song_count": self._song_count,
                            "last_running": time.time(),
                        }
                    )

                # Wait for the FFmpeg song process to finish (song ends)
                # _wait_for_process() blocks until FFmpeg exits
                await self._wait_for_process()

                if not self._running:
                    break

                # Song ended — advance
                self._playlist_index += 1

                # Brief waiting card between songs (prevents YouTube "not receiving data" errors)
                await self.play_waiting(f"Up next... | {self.station_name} Radio")
                await asyncio.sleep(2)

        except asyncio.CancelledError:
            log.info("YouTube Live: Autonomous loop cancelled")
        except Exception as e:
            self._last_error = f"Autonomous loop crashed: {e}"
            log.error(f"YouTube Live: {self._last_error}", exc_info=True)

    async def _load_playlist(self):
        """Load playlist entries via yt-dlp (flat extraction — fast)."""
        import yt_dlp
        from cogs.youtube import YTDL_PLAYLIST_FLAT_OPTIONS

        log.info(f"YouTube Live: Loading playlist: {self._playlist_url}")

        opts = YTDL_PLAYLIST_FLAT_OPTIONS.copy()

        try:
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(
                None,
                lambda: yt_dlp.YoutubeDL(opts).extract_info(
                    self._playlist_url, download=False
                ),
            )
        except Exception as e:
            self._last_error = f"Playlist load failed: {e}"
            log.error(f"YouTube Live: Failed to load playlist: {e}")
            return

        if not data:
            log.warning("YouTube Live: Playlist extraction returned nothing")
            return

        entries = []
        if "entries" in data:
            for entry in data["entries"]:
                if entry is None:
                    continue
                # Build a proper watch URL from the video ID
                video_id = entry.get("id") or entry.get("url", "")
                raw_url = entry.get("url") or ""
                if raw_url.startswith("http"):
                    webpage_url = raw_url
                elif video_id and len(video_id) == 11:
                    webpage_url = f"https://www.youtube.com/watch?v={video_id}"
                else:
                    webpage_url = entry.get("webpage_url") or ""

                entries.append(
                    {
                        "id": video_id,
                        "title": entry.get("title", "Unknown"),
                        "webpage_url": webpage_url,
                        "thumbnail": entry.get("thumbnail")
                        or f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
                        "duration": entry.get("duration"),
                    }
                )
        else:
            # Single video, not a playlist
            video_id = data.get("id") or ""
            webpage_url = (
                data.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"
            )
            entries.append(
                {
                    "id": video_id,
                    "title": data.get("title", "Unknown"),
                    "webpage_url": webpage_url,
                    "thumbnail": data.get("thumbnail")
                    or f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
                    "duration": data.get("duration"),
                }
            )

        self._playlist_entries = entries
        if self._shuffle:
            random.shuffle(self._playlist_entries)

        log.info(
            f"YouTube Live: Loaded {len(self._playlist_entries)} tracks from playlist"
        )

    async def _resolve_audio_url(self, webpage_url: str) -> str | None:
        """Resolve a YouTube watch URL to a direct audio stream URL."""
        from cogs.youtube import YTDLSource

        try:
            source = await YTDLSource.resolve(webpage_url)
            return source.url if source else None
        except Exception as e:
            log.error(f"YouTube Live: Failed to resolve {webpage_url}: {e}")
            return None

    async def _wait_for_process(self):
        """Wait for the current FFmpeg process to exit (song ends naturally)."""
        if not self._process:
            return
        try:
            await self._process.wait()
        except asyncio.CancelledError:
            pass

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

    # ── FFmpeg command builders ─────────────────────────────────────
    # Each builder constructs a complete ffmpeg command with proper
    # filter_complex pad labels. Every input stream is used exactly once
    # (or split first if needed by multiple filters).
    #
    # Key rules for valid filter_complex:
    #   1. Every [label] used as input must be produced by a prior filter
    #   2. Every [label] produced must be used as input by a later filter
    #      (except the final output [outv])
    #   3. A single input stream (e.g. [3:v]) can only feed ONE filter
    #      chain. Use split/asplit if you need it in multiple chains.
    #   4. drawtext/drawbox are FILTERS, not pad labels — they chain
    #      onto the previous pad: [pad]drawtext=...[new_pad]

    def _safe_text(self, text: str, max_len: int = 60) -> str:
        """Escape text for FFmpeg drawtext filter."""
        return text.replace("'", "\\'").replace(":", "\\:").replace("%", "%%")[:max_len]

    # ── Font resolution ──────────────────────────────────────────────
    # FFmpeg drawtext doesn't support "bold=1" — that causes "Option not found".
    # "font=Sans" requires --enable-fontconfig at compile time and isn't reliable.
    # Instead, we use fontfile= with absolute paths. For bold text, we use
    # the -Bold variant of the font.

    _FONT_BOLD: str | None = None
    _FONT_REGULAR: str | None = None

    @classmethod
    def _resolve_font(cls, bold: bool = False) -> str:
        """Resolve a font file path for FFmpeg drawtext.

        Tries Noto Sans, then DejaVu Sans, then falls back to a basic X11 font.
        Returns a fontfile= parameter value (absolute path).
        """
        cache_key = "_FONT_BOLD" if bold else "_FONT_REGULAR"
        cached = getattr(cls, cache_key)
        if cached is not None:
            return cached

        candidates_bold = [
            "/usr/share/fonts/noto/NotoSans-Bold.ttf",
            "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
        ]
        candidates_regular = [
            "/usr/share/fonts/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
        ]
        candidates = candidates_bold if bold else candidates_regular

        for path in candidates:
            if os.path.isfile(path):
                setattr(cls, cache_key, path)
                log.debug(f"YouTube Live: Using font: {path}")
                return path

        # Fallback: no font file found — drawtext will use no fontfile
        # (this usually fails, but at least we tried)
        log.warning(
            "YouTube Live: No suitable font found! drawtext may fail. "
            "Install fonts-noto or fonts-dejavu: apt install fonts-dejavu-core"
        )
        setattr(cls, cache_key, "")
        return ""

    async def _start_ffmpeg_song(
        self, audio_url: str, title: str, thumb_path: str | None
    ):
        """Start FFmpeg: audio from URL + animated video card -> RTMP.

        Input layout:
            [0] = background color source (lavfi)
            [1] = thumbnail/logo image (looped still)
            [2] = audio from URL
            [3] = animated GIF (optional, if has_gif)

        Uses fontfile= with absolute paths (not font=Name) for reliability.
        Uses NotoSans-Bold.ttf for bold text, NotoSans-Regular.ttf for normal.
        No 'bold=1' parameter — FFmpeg drawtext does not support it.
        """
        image = self._resolve_image()
        safe_station = self._safe_text(self.station_name, 30)
        safe_title = self._safe_text(title, 70)
        W, H = self.WIDTH, self.HEIGHT
        fps = self.fps

        font_bold = self._resolve_font(bold=True)
        font_reg = self._resolve_font(bold=False)

        cmd = ["ffmpeg", "-re"]

        # Input 0: Background color source
        cmd.extend(["-f", "lavfi", "-i", f"color=c=0x0f0f23:s={W}x{H}:r={fps}"])

        # Input 1: Thumbnail or logo image (looped, with framerate set)
        if thumb_path and os.path.isfile(thumb_path):
            cmd.extend(["-loop", "1", "-framerate", str(fps), "-i", thumb_path])
        elif image:
            cmd.extend(["-loop", "1", "-framerate", str(fps), "-i", image])
        else:
            cmd.extend(["-f", "lavfi", "-i", f"color=c=0x1a1a2e:s=360x360:r={fps}"])

        # Input 2: Audio URL (with user-agent + reconnect for YouTube)
        cmd.extend(
            [
                "-user_agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "-reconnect",
                "1",
                "-reconnect_streamed",
                "1",
                "-reconnect_delay_max",
                "5",
                "-i",
                audio_url,
            ]
        )

        # Input 3: Animated GIF (optional)
        gif_path = self._resolve_gif()
        has_gif = gif_path and os.path.isfile(gif_path)
        if has_gif:
            cmd.extend(["-stream_loop", "-1", "-ignore_loop", "0", "-i", gif_path])

        # ── Build filter_complex ────────────────────────────────────
        vf = ""

        # Scale thumbnail
        vf += f"[1:v]scale=400:400:force_original_aspect_ratio=decrease,"
        vf += f"pad=410:420:(ow-iw)/2:(oh-ih)/2:color=black@0,"
        vf += f"format=yuva420p[thumb];"

        # Background
        vf += f"[0:v]format=yuva420p[bg];"

        # Overlay thumbnail on left
        vf += f"[bg][thumb]overlay=30:150:format=auto[with_thumb];"

        # GIF overlay (use split so one GIF input feeds two filters)
        if has_gif:
            vf += f"[3:v]split=2[gif_big_in][gif_small_in];"
            vf += f"[gif_big_in]scale=120:120:force_original_aspect_ratio=decrease,"
            vf += f"format=yuva420p[gif_big];"
            vf += f"[gif_small_in]scale=60:60:force_original_aspect_ratio=decrease,"
            vf += f"format=yuva420p[gif_small];"
            vf += f"[with_thumb][gif_big]overlay=320:440:format=auto[with_gif];"
            current = "with_gif"
        else:
            current = "with_thumb"

        # Station name (top right, bold font)
        vf += f"[{current}]drawtext=text='{safe_station}':"
        vf += f"fontcolor=0xff4444:fontsize=20"
        if font_bold:
            vf += f":fontfile={font_bold}"
        vf += f":x=470:y=160:box=1:boxcolor=0x0a0a23@0.7:boxborderw=4[with_station];"

        # Song title (bold font)
        vf += f"[with_station]drawtext=text='{safe_title}':"
        vf += f"fontcolor=white:fontsize=26"
        if font_bold:
            vf += f":fontfile={font_bold}"
        vf += f":x=470:y=200:box=1:boxcolor=0x0a0a23@0.7:boxborderw=4[with_title];"

        # "Now Playing" indicator (regular font)
        vf += f"[with_title]drawtext=text='Now Playing':"
        vf += f"fontcolor=0x8888ff:fontsize=14"
        if font_reg:
            vf += f":fontfile={font_reg}"
        vf += f":x=470:y=250[with_sub];"

        # Bottom bar background
        vf += f"[with_sub]drawbox=x=0:y={H - 55}:w={W}:h=55:"
        vf += f"color=0x0a0a23@0.95:t=fill[with_bar];"

        # LIVE indicator (bold font)
        vf += f"[with_bar]drawtext=text='LIVE':"
        vf += f"fontcolor=0xff0000:fontsize=18"
        if font_bold:
            vf += f":fontfile={font_bold}"
        vf += f":x=15:y={H - 38}[with_live];"

        # Station name in bottom bar + optional GIF badge
        if has_gif:
            vf += f"[with_live][gif_small]overlay={W - 75}:{H - 50}:format=auto[with_gifbar];"
            vf += f"[with_gifbar]drawtext=text='{safe_station}':"
            vf += f"fontcolor=0xffffff@0.6:fontsize=14"
            if font_reg:
                vf += f":fontfile={font_reg}"
            vf += f":x=100:y={H - 35}[outv]"
        else:
            vf += f"[with_live]drawtext=text='{safe_station}':"
            vf += f"fontcolor=0xffffff@0.6:fontsize=14"
            if font_reg:
                vf += f":fontfile={font_reg}"
            vf += f":x=100:y={H - 35}[outv]"

        cmd.extend(["-filter_complex", vf])

        # Map outputs — audio index shifts when GIF is present
        audio_idx = "3:a" if has_gif else "2:a"
        cmd.extend(["-map", "[outv]", "-map", audio_idx])

        # Encoding settings
        cmd.extend(self._encoding_args())

        await self._run_ffmpeg(cmd, "song")

    async def _start_ffmpeg_waiting(self, message: str):
        """Start FFmpeg: animated waiting card + silence -> RTMP.

        Input layout:
            [0] = background image (looped) or color source (lavfi)
            [1] = silence audio source (lavfi anullsrc)
            [2] = animated GIF (optional, if has_gif)
        """
        image = self._resolve_image()
        safe_msg = self._safe_text(message, 60)
        safe_station = self._safe_text(self.station_name, 30)
        W, H = self.WIDTH, self.HEIGHT
        fps = self.fps

        font_bold = self._resolve_font(bold=True)
        font_reg = self._resolve_font(bold=False)

        cmd = ["ffmpeg", "-re"]

        # Input 0: Background image or color
        if image:
            cmd.extend(["-loop", "1", "-framerate", str(fps), "-i", image])
        else:
            cmd.extend(["-f", "lavfi", "-i", f"color=c=0x0f0f23:s={W}x{H}:r={fps}"])

        # Input 1: Silence
        cmd.extend(
            ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        )

        # Input 2: Animated GIF (optional)
        gif_path = self._resolve_gif()
        has_gif = gif_path and os.path.isfile(gif_path)
        if has_gif:
            cmd.extend(["-stream_loop", "-1", "-ignore_loop", "0", "-i", gif_path])

        # ── Build filter_complex ────────────────────────────────────
        vf = ""

        # Scale background
        if image:
            vf += f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
            vf += f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x0f0f23,"
            vf += f"format=yuva420p[bg];"
        else:
            vf += f"[0:v]format=yuva420p[bg];"

        # Station name centered (bold)
        vf += f"[bg]drawtext=text='{safe_station}':"
        vf += f"fontcolor=0xff4444:fontsize=32"
        if font_bold:
            vf += f":fontfile={font_bold}"
        vf += f":x=(w-text_w)/2:y=h*0.30:"
        vf += f"box=1:boxcolor=0x0a0a23@0.8:boxborderw=8[with_station];"

        if has_gif:
            # Split GIF into center and bar copies
            vf += f"[2:v]split=2[gif_center_in][gif_bar_in];"
            vf += f"[gif_center_in]scale=200:200:force_original_aspect_ratio=decrease,"
            vf += f"format=yuva420p[gif_center];"
            vf += f"[with_station][gif_center]overlay=(w-200)/2:h*0.42:format=auto[with_gif];"

            # Waiting message (regular)
            vf += f"[with_gif]drawtext=text='{safe_msg}':"
            vf += f"fontcolor=white@0.7:fontsize=18"
            if font_reg:
                vf += f":fontfile={font_reg}"
            vf += f":x=(w-text_w)/2:y=h*0.72:"
            vf += f"box=1:boxcolor=0x0a0a23@0.5:boxborderw=4[msg];"

            # Bottom bar
            vf += f"[msg]drawbox=x=0:y={H - 50}:w={W}:h=50:color=0x0a0a23@0.9:t=fill,"
            vf += f"drawtext=text='STANDBY':fontcolor=0xffaa00:fontsize=16"
            if font_bold:
                vf += f":fontfile={font_bold}"
            vf += f":x=15:y={H - 35}[with_bar];"

            # Small GIF badge in bar
            vf += f"[gif_bar_in]scale=40:40:force_original_aspect_ratio=decrease,"
            vf += f"format=yuva420p[gif_bar];"
            vf += f"[with_bar][gif_bar]overlay={W - 55}:{H - 45}:format=auto[with_bar_gif];"

            # Station name in bar
            vf += f"[with_bar_gif]drawtext=text='{safe_station}':"
            vf += f"fontcolor=0xffffff@0.5:fontsize=14"
            if font_reg:
                vf += f":fontfile={font_reg}"
            vf += f":x=130:y={H - 35}[outv]"
        else:
            # No GIF — simple chain
            vf += f"[with_station]drawtext=text='{safe_msg}':"
            vf += f"fontcolor=white@0.7:fontsize=18"
            if font_reg:
                vf += f":fontfile={font_reg}"
            vf += f":x=(w-text_w)/2:y=h*0.72:"
            vf += f"box=1:boxcolor=0x0a0a23@0.5:boxborderw=4[msg];"

            # Bottom bar
            vf += f"[msg]drawbox=x=0:y={H - 50}:w={W}:h=50:color=0x0a0a23@0.9:t=fill,"
            vf += f"drawtext=text='STANDBY':fontcolor=0xffaa00:fontsize=16"
            if font_bold:
                vf += f":fontfile={font_bold}"
            vf += f":x=15:y={H - 35},"
            vf += f"drawtext=text='{safe_station}':fontcolor=0xffffff@0.5:fontsize=14"
            if font_reg:
                vf += f":fontfile={font_reg}"
            vf += f":x=130:y={H - 35}"
            vf += f"[outv]"

        cmd.extend(["-filter_complex", vf])
        cmd.extend(["-map", "[outv]", "-map", "1:a"])

        cmd.extend(self._encoding_args())
        cmd.append("-shortest")

        await self._run_ffmpeg(cmd, "waiting")

    async def _start_ffmpeg_tts(self, tts_path: str, text: str):
        """Start FFmpeg: TTS audio + animated DJ card -> RTMP.

        Input layout:
            [0] = background image (looped) or color source
            [1] = TTS audio file
            [2] = animated GIF (optional, if has_gif)
        """
        image = self._resolve_image()
        gif_path = self._resolve_gif()
        has_gif = gif_path and os.path.isfile(gif_path)
        label = "DJ Speaking"
        safe_text = self._safe_text(text, 50)
        safe_station = self._safe_text(self.station_name, 30)
        W, H = self.WIDTH, self.HEIGHT
        fps = self.fps

        font_bold = self._resolve_font(bold=True)
        font_reg = self._resolve_font(bold=False)

        cmd = ["ffmpeg", "-re"]

        # Input 0: Background image or color
        if image:
            cmd.extend(["-loop", "1", "-framerate", str(fps), "-i", image])
        else:
            cmd.extend(["-f", "lavfi", "-i", f"color=c=0x0f0f23:s={W}x{H}:r={fps}"])

        # Input 1: TTS audio
        cmd.extend(["-i", tts_path])

        # Input 2: Animated GIF (optional)
        if has_gif:
            cmd.extend(["-stream_loop", "-1", "-ignore_loop", "0", "-i", gif_path])

        # ── Build filter_complex ────────────────────────────────────
        vf = ""

        # Scale background
        if image:
            vf += f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
            vf += f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x0f0f23,"
            vf += f"format=yuva420p[bg];"
        else:
            vf += f"[0:v]format=yuva420p[bg];"

        # "DJ Speaking" label (bold, green)
        vf += f"[bg]drawtext=text='{label}':"
        vf += f"fontcolor=0x00ff88:fontsize=28"
        if font_bold:
            vf += f":fontfile={font_bold}"
        vf += f":x=(w-text_w)/2:y=h*0.32:"
        vf += f"box=1:boxcolor=0x0a0a23@0.8:boxborderw=8[with_label];"

        if has_gif:
            # Split GIF for center and bar
            vf += f"[2:v]split=2[gif_center_in][gif_bar_in];"
            vf += f"[gif_center_in]scale=150:150:force_original_aspect_ratio=decrease,"
            vf += f"format=yuva420p[gif_center];"
            vf += f"[with_label][gif_center]overlay=(w-150)/2:h*0.48:format=auto[with_gif];"

            # DJ text (regular)
            vf += f"[with_gif]drawtext=text='{safe_text}':"
            vf += f"fontcolor=white@0.8:fontsize=18"
            if font_reg:
                vf += f":fontfile={font_reg}"
            vf += f":x=(w-text_w)/2:y=h*0.65:"
            vf += f"box=1:boxcolor=0x0a0a23@0.5:boxborderw=4[msg];"

            # Bottom bar
            vf += f"[msg]drawbox=x=0:y={H - 50}:w={W}:h=50:color=0x0a0a23@0.9:t=fill,"
            vf += f"drawtext=text='LIVE - {safe_station}':"
            vf += f"fontcolor=0xff4444:fontsize=16"
            if font_bold:
                vf += f":fontfile={font_bold}"
            vf += f":x=15:y={H - 35}[with_bar];"

            # Small GIF badge
            vf += f"[gif_bar_in]scale=40:40:force_original_aspect_ratio=decrease,"
            vf += f"format=yuva420p[gif_bar];"
            vf += f"[with_bar][gif_bar]overlay={W - 55}:{H - 45}:format=auto[outv]"
        else:
            # DJ text (regular)
            vf += f"[with_label]drawtext=text='{safe_text}':"
            vf += f"fontcolor=white@0.8:fontsize=18"
            if font_reg:
                vf += f":fontfile={font_reg}"
            vf += f":x=(w-text_w)/2:y=h*0.65:"
            vf += f"box=1:boxcolor=0x0a0a23@0.5:boxborderw=4[msg];"

            # Bottom bar
            vf += f"[msg]drawbox=x=0:y={H - 50}:w={W}:h=50:color=0x0a0a23@0.9:t=fill,"
            vf += f"drawtext=text='LIVE - {safe_station}':"
            vf += f"fontcolor=0xff4444:fontsize=16"
            if font_bold:
                vf += f":fontfile={font_bold}"
            vf += f":x=15:y={H - 35}"
            vf += f"[outv]"

        cmd.extend(["-filter_complex", vf])

        # Map outputs: audio is always [1:a]
        cmd.extend(["-map", "[outv]", "-map", "1:a"])

        cmd.extend(self._encoding_args())
        cmd.append("-shortest")

        await self._run_ffmpeg(cmd, "tts")

    def _encoding_args(self) -> list:
        """Return the common FFmpeg encoding argument list."""
        return [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-g",
            str(self.fps * 2),  # Keyframe every 2s
            "-keyint_min",
            str(self.fps),  # Min keyframe 1s
            "-b:v",
            f"{self.bitrate_video}k",
            "-r",
            str(self.fps),
            "-pix_fmt",
            "yuv420p",
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
            f"{self.rtmp_url}/{self.stream_key}",
        ]

    async def _run_ffmpeg(self, cmd: list, label: str):
        """Start an FFmpeg subprocess and monitor it."""
        try:
            self._stderr_lines = []
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            log.info(
                f"YouTube Live: FFmpeg {label} started "
                f"(pid={self._process.pid}, cmd has {len(cmd)} args)"
            )
            # Log the full command for debugging (very helpful for filter_complex issues)
            log.debug(f"YouTube Live: FFmpeg command:\n{' '.join(cmd)}")
            asyncio.create_task(self._drain_stderr(label))
        except FileNotFoundError:
            self._last_error = "ffmpeg not found — install with: apt install ffmpeg"
            log.error(f"YouTube Live: {self._last_error}")
            self._running = False
        except Exception as e:
            self._last_error = f"FFmpeg start failed: {e}"
            log.error(f"YouTube Live: {self._last_error}")
            self._running = False

    async def _drain_stderr(self, label: str):
        """Drain FFmpeg stderr to prevent pipe buffer deadlocks.

        Logs ALL lines at WARNING level so we see exactly what FFmpeg complains about.
        Previous code only logged lines containing 'error'/'warning', which missed
        the actual "Option not found" lines that FFmpeg emits at normal log level.
        """
        if not self._process or not self._process.stderr:
            return
        try:
            while True:
                line = await self._process.stderr.readline()
                if not line:
                    break
                txt = line.decode("utf-8", errors="replace").strip()
                # Keep last 30 lines for debugging
                self._stderr_lines.append(txt)
                if len(self._stderr_lines) > 30:
                    self._stderr_lines.pop(0)
                # Log everything — filter_complex errors often appear on
                # lines that don't contain "error" (e.g. "Option not found")
                if txt:
                    log.warning(f"YouTube Live [{label}]: {txt[:300]}")
        except Exception:
            pass

    async def _watchdog(self):
        """Watchdog that restarts the stream if FFmpeg dies unexpectedly.

        In autonomous mode, the _run_autonomous_loop handles re-advancement.
        In mirror mode, we restart the waiting card so the stream stays alive.
        """
        try:
            while self._running:
                await asyncio.sleep(10)
                if (
                    self._process
                    and self._process.returncode is not None
                    and self._running
                ):
                    code = self._process.returncode
                    log.warning(f"YouTube Live: FFmpeg exited with code {code}")
                    if code != 0:
                        # Log the last few stderr lines for diagnosis
                        if self._stderr_lines:
                            for line in self._stderr_lines[-5:]:
                                log.warning(f"YouTube Live [stderr]: {line[:200]}")

                    if not self._autonomous:
                        # Mirror mode: restart waiting card
                        log.warning("YouTube Live: Restarting waiting card...")
                        await self.play_waiting()
                    # In autonomous mode, the loop handles advancement
        except asyncio.CancelledError:
            pass

    # ── Helpers ─────────────────────────────────────────────────────

    def _resolve_image(self) -> str | None:
        """Resolve the stream card image path."""
        if self.stream_image and os.path.isfile(self.stream_image):
            return self.stream_image
        default = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "assets", "stream_card.png"
        )
        if os.path.isfile(default):
            return default
        logo = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "assets", "logo.png"
        )
        if os.path.isfile(logo):
            return logo
        log.warning(
            "YouTube Live: No stream card image found — will use a dark background. "
            "Set YOUTUBE_STREAM_IMAGE in .env or place assets/logo.png"
        )
        return None

    def _resolve_gif(self) -> str | None:
        """Resolve the animated GIF overlay path."""
        if self.stream_gif:
            if os.path.isfile(self.stream_gif):
                return self.stream_gif
            log.warning(f"YouTube Live: Configured GIF not found: {self.stream_gif}")
        default_gif = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "assets", "giphy.gif"
        )
        if os.path.isfile(default_gif):
            return default_gif
        return None
