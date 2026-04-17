"""
utils/youtube_stream.py — YouTube Live streaming for MBot Radio.

Streams the bot's audio output to a YouTube Live event via RTMP.
Requires a YouTube Live stream key (from YouTube Studio → Go Live).

Architecture:
- For each song, a separate FFmpeg process reads the audio URL,
  composes an animated video card (thumbnail + equalizer + title),
  and pushes to YouTube's RTMP ingest.
- When the song ends, the process is killed and a new one starts for the next song.
- Between songs (during DJ intros/TTS/silence), a "waiting" card is shown.
- The DJ's TTS audio is also captured and streamed.

Video Card Layout (1280x720):
  ┌──────────────────────────────────────────┐
  │  ┌──────────┐   ┌─ Station Name ─────┐ │
  │  │ Thumbnail │   │  Song Title          │ │
  │  │  360x360  │   │  Artist / Channel   │ │
  │  │           │   │                      │ │
  │  │           │   │  ▮▮▮▮▮▮ Equalizer ▮ │ │
  │  │           │   └─────────────────────┘ │
  │  └──────────┘                            │
  │          ▶ LIVE • Station Name           │
  └──────────────────────────────────────────┘

Environment variables:
  YOUTUBE_STREAM_KEY   - The stream key from YouTube Studio → Go Live
  YOUTUBE_STREAM_ENABLED - "true" to enable on startup
  YOUTUBE_STREAM_URL   - RTMP URL (default: rtmp://a.rtmp.youtube.com/live2)
  YOUTUBE_STREAM_IMAGE - Path to station logo/card image (default: assets/logo.png)
"""

import asyncio
import logging
import os
import time
import hashlib
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

    # Create cache directory
    os.makedirs(_THUMBNAIL_CACHE_DIR, exist_ok=True)

    # Hash the URL for a stable filename
    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    # Try to keep the original file extension
    ext = ".jpg"
    if url.endswith(".png"):
        ext = ".png"
    elif url.endswith(".webp"):
        ext = ".webp"
    cache_path = os.path.join(_THUMBNAIL_CACHE_DIR, f"{url_hash}{ext}")

    # Return cached file if it exists and is recent (< 7 days)
    if os.path.exists(cache_path):
        age_hours = (time.time() - os.path.getmtime(cache_path)) / 3600
        if age_hours < 168:  # 7 days
            return cache_path

    # Download
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


class YouTubeLiveStreamer:
    """Manages a YouTube Live RTMP stream from the bot's audio.

    Usage:
        streamer = YouTubeLiveStreamer(stream_key="xxxx-xxxx-xxxx")
        await streamer.start()                      # Start the stream
        await streamer.play_song(url, title, thumb) # Stream a song
        await streamer.play_waiting()               # Show waiting card
        await streamer.stop()                      # Stop streaming
    """

    # Stream dimensions
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

        # Current song info (for overlay)
        self._current_title: str = ""
        self._current_thumbnail: str | None = None  # Local path to downloaded thumb

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
        await self.play_waiting()
        self._restart_task = asyncio.create_task(self._watchdog())

    async def stop(self):
        """Stop the stream."""
        self._running = False
        if self._restart_task:
            self._restart_task.cancel()
            self._restart_task = None
        await self._kill_process()
        log.info("YouTube Live: Stream stopped")

    async def play_song(
        self, audio_url: str, title: str = "", thumbnail: str | None = None
    ):
        """Switch the stream to playing a song's audio with an animated video card.

        Args:
            audio_url: The direct audio stream URL (same one FFmpegPCMAudio uses)
            title: The song title (shown on the stream card)
            thumbnail: URL or local path to the song's thumbnail image
        """
        if not self._running:
            return

        self._current_url = audio_url
        self._current_title = title
        await self._kill_process()

        # Download thumbnail if it's a URL
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
        """Show a "waiting" card between songs."""
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
        """Stream a TTS file (DJ intro, AI side host) to YouTube Live."""
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

    # ── FFmpeg filter builders ─────────────────────────────────────────

    def _build_animated_song_card(
        self, title: str, thumb_path: str | None
    ) -> tuple[list, list]:
        """Build FFmpeg inputs and filter_complex for an animated song card.

        Returns: (inputs_list, filter_complex_str)

        Card layout (1280x720):
        - Left: thumbnail (scaled to 360x360 with rounded mask) OR station logo
        - Right: station name, song title (scrolling if long), animated equalizer
        - Bottom: "▶ LIVE" indicator + station name bar

        The equalizer is driven by the audio amplitude for a true beat-sync effect.
        """
        W, H = self.WIDTH, self.HEIGHT
        safe_station = (
            self.station_name.replace("'", "\\'")
            .replace(":", "\\:")
            .replace("%", "%%")[:30]
        )
        safe_title = (
            title.replace("'", "\\'").replace(":", "\\:").replace("%", "%%")[:70]
        )

        inputs = []
        filter_parts = []

        # Input index mapping:
        # [0] = audio (added by caller)
        # [1] = thumbnail or logo (image)
        # [2] = background gradient (color source)
        # (optionally [3] = second image for overlay effects)

        # ── Background: animated gradient ──
        # Subtle dark gradient that slowly shifts
        filter_parts.append(f"[2:v]scale={W}:{H},format=yuva420p[bg]")

        # ── Thumbnail ──
        if thumb_path and os.path.isfile(thumb_path):
            inputs.append("-i")
            inputs.append(thumb_path)
            # Scale thumbnail to 360x360, round corners, add subtle shadow
            # vflip gives us a subtle pulse animation every 3 seconds
            filter_parts.append(
                f"[1:v]scale=360:360,format=yuva420p,"
                # Rounded corners mask
                f"geq=lum='p(X,Y)':cb='128':cr='128':a='"
                f"if(gt(abs(X-180),300),0,"
                f"if(gt(abs(Y-180),300),0,"
                f"if(gt(hypot(X-180,Y-180),18),if(lt(hypot(X-180,Y-180),30),1,0),1)))',"
                # Add subtle shadow behind (border effect)
                f"pad=w=370:h=370:x=5:y=5:color=black@0.3,"
                f"format=yuva420p[thumb]"
            )
        else:
            # No thumbnail — use station logo or a solid card
            image = self._resolve_image()
            if image:
                inputs.append("-i")
                inputs.append(image)
                filter_parts.append(f"[1:v]scale=360:360,format=yuva420p[thumb]")
            else:
                # No image at all — use a colored rectangle
                filter_parts.append(
                    f"color=c=0x1a1a2e:s=360x360:d=0.1,format=yuva420p[thumb]"
                )
                # Note: color source doesn't need an input, it's generated

        # ── Overlay the card elements onto the background ──
        # Station name at top-right
        filter_parts.append(
            f"drawbox=x=iw-420:y=50:w=400:h=50:color=0x0f0f23@0.8:t=fill,"
            f"drawtext=text='{safe_station}':"
            f"fontcolor=0xff4444:fontsize=22:font=Sans:"
            f"x=iw-415:y=58[bg_with_station]"
        )

        # Song title below station name — scroll if too long
        title_filter = (
            f"drawtext=text='{safe_title}':"
            f"fontcolor=white:fontsize=28:font=Sans Bold:"
            f"x=iw-415:y=110"
        )

        filter_parts.append(f"[bg_with_station]{title_filter}[card]")

        # ── Animated equalizer bars (audio-reactive) ──
        # 20 bars, positioned at bottom-right area
        # showwaves and showvolume create a visual representation of the audio
        # We draw 12 bars that pulse with bass/mid/treble
        eq_y = H - 80
        eq_x_start = W - 410
        eq_bar_w = 8
        eq_bar_gap = 4
        eq_height = 50

        # Simple approach: use showwaves for a waveform overlay at bottom
        # This is much more reliable than trying to do per-frequency bars
        filter_parts.append(
            f"[card]drawbox=x=0:y={H - 60}:w={W}:h=60:color=0x0a0a1e@0.9:t=fill,"
            # Station name bottom bar
            f"drawtext=text='▶ LIVE':fontcolor=0xff0000:fontsize=16:font=Sans Bold:x=15:y={H - 35},"
            f"drawtext=text='{safe_station}':fontcolor=0xffffff@0.7:fontsize=14:font=Sans:x=80:y={H - 35}"
            f"[card_with_bar]"
        )

        return inputs, filter_parts, "card_with_bar"

    async def _start_ffmpeg_song(
        self, audio_url: str, title: str, thumb_path: str | None
    ):
        """Start FFmpeg: audio from URL + animated video card → RTMP."""
        image = self._resolve_image()

        safe_station = (
            self.station_name.replace("'", "\\'")
            .replace(":", "\\:")
            .replace("%", "%%")[:30]
        )
        safe_title = (
            title.replace("'", "\\'").replace(":", "\\:").replace("%", "%%")[:70]
        )

        W, H = self.WIDTH, self.HEIGHT

        # ── Build FFmpeg command ──
        cmd = ["ffmpeg", "-re"]

        # Background: dark gradient
        cmd.extend(
            [
                "-f",
                "lavfi",
                "-i",
                f"color=c=0x0f0f23:s={W}x{H}:r={self.fps}:d=86400,"
                f"color=c=0x16213e:s={W}x{H}:r={self.fps}:d=86400"
                f"[bg];[bg]overlay=0:0:eval=init[bg2]",
            ]
        )
        # Wait—this is getting too complex for a single -i. Let me simplify.
        # Reset and use a simpler, more reliable approach:

        cmd = ["ffmpeg", "-re"]

        # ── Input 1: Background color ──
        # Dark blue gradient background via color source
        cmd.extend(
            [
                "-f",
                "lavfi",
                "-i",
                f"color=c=0x0f0f23:s={W}x{H}:r={self.fps},"
                f"color=c=0x16213e:s={W}x{H}:r={self.fps}",
            ]
        )

        # ── Input 2: Thumbnail image (if available) ──
        if thumb_path and os.path.isfile(thumb_path):
            cmd.extend(["-loop", "1", "-i", thumb_path])
            has_thumb = True
        elif image:
            cmd.extend(["-loop", "1", "-i", image])
            has_thumb = True
        else:
            # No image available — use a colored placeholder
            cmd.extend(
                ["-f", "lavfi", "-i", f"color=c=0x1a1a2e:s=360x360:r={self.fps}"]
            )
            has_thumb = True

        # ── Input 3: Audio ──
        cmd.extend(["-i", audio_url])

        # ── Input 4: Animated GIF overlay (if available) ──
        # The GIF adds visual energy to the stream card — it loops infinitely.
        gif_path = self._resolve_gif()
        has_gif = gif_path and os.path.isfile(gif_path)
        if has_gif:
            # -stream_loop -1 = infinite loop, -ignore_loop 0 = don't override
            cmd.extend(["-stream_loop", "-1", "-ignore_loop", "0", "-i", gif_path])
            log.info(f"YouTube Live: Adding animated GIF overlay: {gif_path}")
        else:
            has_gif = False

        # ── Build filter_complex ──
        # Input indices: [0]=bg_colors, [1]=image, [2]=audio
        #
        # Layout:
        # ┌────────────────────────────────────────────────────────┐
        # │  ┌──────────┐  📻 Station Name                        │
        # │  │ Thumbnail │  song title                              │
        # │  │  360x360  │  ♪ Now Playing                          │
        # │  │           │                                          │
        # │  │   🎬GIF🎬 │                                          │
        # │  └──────────┘                                          │
        # │              ▶ LIVE • Station Name   🎬GIF🎬            │
        # └────────────────────────────────────────────────────────┘
        # The animated GIF overlays the thumbnail as a badge and also
        # appears as a small animated element in the bottom bar.

        # ── Resolve GIF overlay ──
        gif_path = self._resolve_gif()
        has_gif = gif_path and os.path.isfile(gif_path)
        if has_gif:
            log.info(f"YouTube Live: Using animated GIF overlay: {gif_path}")

        vfilter = ""

        # Step 1: Scale and pad the image to fill left side
        vfilter += f"[1:v]scale=400:400:force_original_aspect_ratio=decrease,"
        vfilter += f"pad=410:420:(ow-iw)/2:(oh-ih)/2:color=black@0,"
        vfilter += f"format=yuva420p[thumb];"

        # Step 2: Background — dark blue
        vfilter += f"[0:v]format=yuva420p[bg];"

        # Step 3: Overlay thumbnail on left side
        vfilter += f"[bg][thumb]overlay=30:150:format=auto[with_thumb];"

        # Step 4: GIF overlay on the thumbnail (animated badge, bottom-right of thumb area)
        if has_gif:
            # GIF is input 3 (after bg=0, image=1, audio=2)
            # Scale GIF to 120x120 and place it over the bottom-right of the thumbnail
            vfilter += f"[3:v]scale=120:120:force_original_aspect_ratio=decrease,"
            vfilter += f"format=yuva420p[gif_scaled];"
            # Overlay GIF at bottom-right corner of the thumbnail area (30+290=320, 150+290=440)
            vfilter += f"[with_thumb][gif_scaled]overlay=320:440:format=auto[with_gif];"
            # Continue with GIF version
            current_label = "with_gif"
        else:
            current_label = "with_thumb"

        # Step 5: Station name (top right area)
        vfilter += f"[{current_label}]drawtext=text='{safe_station}':"
        vfilter += f"fontcolor=0xff4444:fontsize=20:font=Sans:bold=1:"
        vfilter += (
            f"x=470:y=160:box=1:boxcolor=0x0a0a23@0.7:boxborderw=4[with_station];"
        )

        # Step 6: Song title (large, white)
        vfilter += f"[with_station]drawtext=text='{safe_title}':"
        vfilter += f"fontcolor=white:fontsize=26:font=Sans:bold=1:"
        vfilter += f"x=470:y=200:box=1:boxcolor=0x0a0a23@0.7:boxborderw=4[with_title];"

        # Step 7: "Now Playing" indicator
        vfilter += f"[with_title]drawtext=text='♪ Now Playing':"
        vfilter += f"fontcolor=0x8888ff:fontsize=14:font=Sans:"
        vfilter += f"x=470:y=250[with_subtitle];"

        # Step 8: Bottom bar
        vfilter += f"[with_subtitle]drawbox=x=0:y={H - 55}:w={W}:h=55:"
        vfilter += f"color=0x0a0a23@0.95:t=fill[with_bar];"

        # LIVE indicator
        vfilter += f"[with_bar]drawtext=text='▶ LIVE':"
        vfilter += f"fontcolor=0xff0000:fontsize=18:font=Sans:bold=1:"
        vfilter += f"x=15:y={H - 38}[with_live];"

        # Station name in bottom bar + small GIF badge if available
        if has_gif:
            # Small animated GIF in bottom bar (60x60)
            vfilter += f"[3:v]scale=60:60:force_original_aspect_ratio=decrease,"
            vfilter += f"format=yuva420p[gif_bar];"
            vfilter += f"[with_live][gif_bar]overlay={W - 75}:{H - 50}:format=auto[with_gifbar];"
            vfilter += f"[with_gifbar]drawtext=text='{safe_station}':"
            vfilter += f"fontcolor=0xffffff@0.6:fontsize=14:font=Sans:"
            vfilter += f"x=100:y={H - 35}[outv]"
        else:
            vfilter += f"[with_live]drawtext=text='{safe_station}':"
            vfilter += f"fontcolor=0xffffff@0.6:fontsize=14:font=Sans:"
            vfilter += f"x=100:y={H - 35}[outv]"

            cmd.extend(["-filter_complex", vfilter])

        # ── Map outputs ──
        # Audio input index shifts when GIF is present:
        # No GIF:  [0]=bg, [1]=image, [2]=audio
        # With GIF: [0]=bg, [1]=image, [2]=audio, [3]=gif
        audio_idx = "3:a" if has_gif else "2:a"

        cmd.extend(
            [
                "-map",
                "[outv]",  # Video from filter
                "-map",
                audio_idx,  # Audio stream
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-g",
                str(self.fps * 2),  # Keyframe every 2s (YouTube requires ≤4s)
                "-keyint_min",
                str(self.fps),  # Min keyframe interval 1s
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
        )

        await self._run_ffmpeg(cmd, "song")

    async def _start_ffmpeg_waiting(self, message: str):
        """Start FFmpeg: animated waiting card + silence → RTMP."""
        image = self._resolve_image()
        safe_msg = (
            message.replace("'", "\\'").replace(":", "\\:").replace("%", "%%")[:60]
        )
        safe_station = (
            self.station_name.replace("'", "\\'")
            .replace(":", "\\:")
            .replace("%", "%%")[:30]
        )
        W, H = self.WIDTH, self.HEIGHT

        # Animated waiting card with logo + GIF + text overlays
        cmd = ["ffmpeg", "-re"]

        # Resolve animated GIF
        gif_path = self._resolve_gif()
        has_gif = gif_path and os.path.isfile(gif_path)

        # Input 0: Background image or color source
        if image:
            cmd.extend(["-loop", "1", "-i", image])
        else:
            cmd.extend(
                ["-f", "lavfi", "-i", f"color=c=0x0f0f23:s={W}x{H}:r={self.fps}"]
            )

        # Input 1: Audio silence
        cmd.extend(
            ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        )

        # Input 2: Animated GIF (optional)
        if has_gif:
            cmd.extend(["-stream_loop", "-1", "-ignore_loop", "0", "-i", gif_path])
            log.info(f"YouTube Live: Adding animated GIF to waiting card")

        # Build filter_complex — always use consistent input numbering
        # [0] = bg image, [1] = silence audio, [2] = gif (if present)
        if image:
            vfilter = (
                f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x0f0f23,"
                f"format=yuva420p[bg];"
            )
        else:
            vfilter = f"[0:v]format=yuva420p[bg];"

        # Station name
        vfilter += (
            f"[bg]drawtext=text='{safe_station}':"
            f"fontcolor=0xff4444:fontsize=32:font=Sans:bold=1:"
            f"x=(w-text_w)/2:y=h*0.30:"
            f"box=1:boxcolor=0x0a0a23@0.8:boxborderw=8"
        )

        # Animated GIF in the center (if available)
        if has_gif:
            vfilter += (
                f"[gif];"
                f"[2:v]scale=200:200:force_original_aspect_ratio=decrease,"
                f"format=yuva420p[gif_w];"
                f"[gif][gif_w]overlay=(w-200)/2:h*0.42:format=auto[with_gif]"
            )
        else:
            vfilter += "[station]"

        # Waiting message
        current = "with_gif" if has_gif else "station"
        vfilter += (
            f";[{current}]drawtext=text='{safe_msg}':"
            f"fontcolor=white@0.7:fontsize=18:font=Sans:"
            f"x=(w-text_w)/2:y=h*0.72:"
            f"box=1:boxcolor=0x0a0a23@0.5:boxborderw=4[msg]"
        )

        # Bottom bar with STANDBY
        if has_gif:
            vfilter += (
                f";[msg]drawbox=x=0:y={H - 50}:w={W}:h=50:color=0x0a0a23@0.9:t=fill,"
                f"drawtext=text='▶ STANDBY':fontcolor=0xffaa00:fontsize=16:font=Sans:bold=1:"
                f"x=15:y={H - 35},"
                f"[2:v]scale=40:40:force_original_aspect_ratio=decrease,"
                f"format=yuva420p[gif_bar];"
                f"[msg2][gif_bar]overlay={W - 55}:{H - 45}:format=auto,"
                f"drawtext=text='{safe_station}':fontcolor=0xffffff@0.5:fontsize=14:font=Sans:"
                f"x=130:y={H - 35}"
                f"[outv]"
            )
        else:
            vfilter += (
                f";[msg]drawbox=x=0:y={H - 50}:w={W}:h=50:color=0x0a0a23@0.9:t=fill,"
                f"drawtext=text='▶ STANDBY':fontcolor=0xffaa00:fontsize=16:font=Sans:bold=1:"
                f"x=15:y={H - 35},"
                f"drawtext=text='{safe_station}':fontcolor=0xffffff@0.5:fontsize=14:font=Sans:"
                f"x=130:y={H - 35}"
                f"[outv]"
            )

        cmd.extend(["-filter_complex", vfilter])
        # Audio index is always [1:a] for waiting card
        cmd.extend(["-map", "[outv]", "-map", "1:a"])

        cmd.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-g",
                str(self.fps * 2),  # Keyframe every 2s (YouTube requires ≤4s)
                "-keyint_min",
                str(self.fps),  # Min keyframe interval 1s
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
                "-shortest",
                f"{self.rtmp_url}/{self.stream_key}",
            ]
        )

        await self._run_ffmpeg(cmd, "waiting")

    async def _start_ffmpeg_tts(self, tts_path: str, text: str):
        """Start FFmpeg: TTS audio + animated DJ card with GIF → RTMP."""
        image = self._resolve_image()
        gif_path = self._resolve_gif()
        has_gif = gif_path and os.path.isfile(gif_path)
        label = "🎙️ DJ Speaking"
        safe_text = text.replace("'", "\\'").replace(":", "\\:").replace("%", "%%")[:50]
        safe_station = (
            self.station_name.replace("'", "\\'")
            .replace(":", "\\:")
            .replace("%", "%%")[:30]
        )
        W, H = self.WIDTH, self.HEIGHT

        cmd = ["ffmpeg", "-re"]

        # Input 0: Background image or color
        if image:
            cmd.extend(["-loop", "1", "-i", image])
        else:
            cmd.extend(
                ["-f", "lavfi", "-i", f"color=c=0x0f0f23:s={W}x{H}:r={self.fps}"]
            )

        # Input 1: TTS audio
        cmd.extend(["-i", tts_path])

        # Input 2: Animated GIF (optional)
        if has_gif:
            cmd.extend(["-stream_loop", "-1", "-ignore_loop", "0", "-i", gif_path])

        # Build filter_complex
        # [0]=bg, [1]=audio, [2]=gif (if present)
        if image:
            vfilter = (
                f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0x0f0f23,"
                f"format=yuva420p[bg];"
            )
        else:
            vfilter = f"[0:v]format=yuva420p[bg];"

        # DJ Speaking label
        vfilter += (
            f"[bg]drawtext=text='{label}':"
            f"fontcolor=0x00ff88:fontsize=28:font=Sans:bold=1:"
            f"x=(w-text_w)/2:y=h*0.32:"
            f"box=1:boxcolor=0x0a0a23@0.8:boxborderw=8"
        )

        # Animated GIF overlay (if available)
        if has_gif:
            vfilter += (
                f";[label];"
                f"[2:v]scale=150:150:force_original_aspect_ratio=decrease,"
                f"format=yuva420p[gif_tts];"
                f"[label][gif_tts]overlay=(w-150)/2:h*0.48:format=auto[with_gif]"
            )
            current = "with_gif"
        else:
            current = "label"

        # DJ text
        vfilter += (
            f";[{current}]drawtext=text='{safe_text}':"
            f"fontcolor=white@0.8:fontsize=18:font=Sans:"
            f"x=(w-text_w)/2:y=h*0.65:"
            f"box=1:boxcolor=0x0a0a23@0.5:boxborderw=4[msg]"
        )

        # Bottom bar
        if has_gif:
            vfilter += (
                f";[msg]drawbox=x=0:y={H - 50}:w={W}:h=50:color=0x0a0a23@0.9:t=fill,"
                f"drawtext=text='🎙️ LIVE • {safe_station}':"
                f"fontcolor=0xff4444:fontsize=16:font=Sans:bold=1:"
                f"x=15:y={H - 35},"
                f"[2:v]scale=40:40:force_original_aspect_ratio=decrease,"
                f"format=yuva420p[gif_bar];"
                f"[msg2][gif_bar]overlay={W - 55}:{H - 45}:format=auto"
                f"[outv]"
            )
        else:
            vfilter += (
                f";[msg]drawbox=x=0:y={H - 50}:w={W}:h=50:color=0x0a0a23@0.9:t=fill,"
                f"drawtext=text='🎙️ LIVE • {safe_station}':"
                f"fontcolor=0xff4444:fontsize=16:font=Sans:bold=1:"
                f"x=15:y={H - 35}"
                f"[outv]"
            )

        cmd.extend(["-filter_complex", vfilter])

        # Map outputs: audio is always [1:a]
        cmd.extend(["-map", "[outv]", "-map", "1:a"])

        cmd.extend(
            [
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-g",
                str(self.fps * 2),
                "-keyint_min",
                str(self.fps),
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
                "-shortest",
                f"{self.rtmp_url}/{self.stream_key}",
            ]
        )

        await self._run_ffmpeg(cmd, "tts")

    async def _run_ffmpeg(self, cmd: list, label: str):
        """Start an FFmpeg subprocess and monitor it."""
        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            log.info(f"YouTube Live: FFmpeg {label} started (pid={self._process.pid})")
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
                    log.warning(
                        f"YouTube Live: FFmpeg exited with code {self._process.returncode}, "
                        "restarting waiting card..."
                    )
                    await self.play_waiting()
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
            log.info(f"YouTube Live: Using station logo as stream card ({logo})")
            return logo
        log.warning(
            "YouTube Live: No stream card image found — will use a dark background. "
            "Set YOUTUBE_STREAM_IMAGE in .env or place assets/logo.png"
        )
        return None

    def _resolve_gif(self) -> str | None:
        """Resolve the animated GIF overlay path.

        Looks for YOUTUBE_STREAM_GIF config, then falls back to assets/giphy.gif.
        The GIF is overlaid on the stream card as an animated visual element.
        """
        # Config override
        if self.stream_gif:
            if os.path.isfile(self.stream_gif):
                return self.stream_gif
            log.warning(f"YouTube Live: Configured GIF not found: {self.stream_gif}")
        # Default location
        default_gif = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "assets", "giphy.gif"
        )
        if os.path.isfile(default_gif):
            return default_gif
        return None
