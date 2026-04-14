import asyncio
import yt_dlp
import discord
import logging

FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

# --- YTDL Options ---
YTDL_FORMAT_OPTIONS = {
    "format": "bestaudio*/best",
    "outtmpl": "%(extractor)s-%(id)s-%(title)s.%(ext)s",
    "restrictfilenames": True,
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "logtostderr": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "cookiefile": "youtube_cookie.txt"
    if __import__("os").path.exists("youtube_cookie.txt")
    else None,
    "http_headers": {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.88 Safari/537.36"
    },
    "extract_flat": "discard_in_playlist",
}

# --- Playlist Options (fast flat extraction — no stream URLs) ---
YTDL_PLAYLIST_FLAT_OPTIONS = {
    "extract_flat": True,  # Only get metadata (title, id), no stream URLs
    "quiet": True,
    "no_warnings": True,
}


class YTDLSource:
    """A fully-resolved audio source with a direct stream URL ready for playback."""

    def __init__(self, data):
        self.data = data
        self.title = data.get("title")
        self.url = data.get("filepath") or data.get("url")
        self.duration = data.get("duration")
        self.thumbnail = data.get("thumbnail")
        self.webpage_url = data.get("webpage_url")

    @classmethod
    async def from_url(cls, url, *, loop=None, ytdl_opts=None):
        """Extract a single video or search result with full stream URL resolution."""
        loop = loop or asyncio.get_event_loop()

        options = ytdl_opts if ytdl_opts is not None else YTDL_FORMAT_OPTIONS.copy()

        data = await loop.run_in_executor(
            None, lambda: yt_dlp.YoutubeDL(options).extract_info(url, download=False)
        )
        logging.info(f"YTDLSource.from_url: Download and extraction complete for {url}")
        logging.info(
            f"YTDLSource.from_url raw yt-dlp data keys: {data.keys() if isinstance(data, dict) else 'N/A'}"
        )
        logging.info(f"YTDLSource.from_url is_playlist: {'entries' in data}")
        if "entries" in data:
            logging.info(
                f"YTDLSource.from_url number of entries: {len(data.get('entries', []))}"
            )

        if "entries" in data:
            return [cls(entry) for entry in data["entries"]]
        else:
            return [cls(data)]

    @classmethod
    async def resolve(cls, url, *, loop=None, ytdl_opts=None):
        """
        Resolve a single video URL to a playable YTDLSource.
        Used by play_next to lazily resolve PlaceholderTrack entries
        right before playback. Always returns a single YTDLSource (not a list).
        Raises Exception if extraction fails.
        """
        loop = loop or asyncio.get_event_loop()
        options = ytdl_opts if ytdl_opts is not None else YTDL_FORMAT_OPTIONS.copy()

        data = await loop.run_in_executor(
            None, lambda: yt_dlp.YoutubeDL(options).extract_info(url, download=False)
        )
        # Handle edge case: yt-dlp might return a playlist dict for a video URL
        if "entries" in data:
            entries = [e for e in data["entries"] if e is not None]
            if entries:
                return cls(entries[0])
        return cls(data)


class PlaceholderTrack:
    """
    A lightweight track produced by fast playlist extraction.
    Contains only metadata (title, video ID) — no stream URL.
    When play_next dequeues this, it calls YTDLSource.resolve()
    to get the real audio URL just before playback.
    """

    def __init__(self, data):
        self.data = data
        self.title = data.get("title") or "Unknown"
        self.url = None  # No stream URL yet — resolved at playback time
        self.duration = data.get("duration")
        self.thumbnail = data.get("thumbnail")
        self.webpage_url = data.get("url") or data.get("webpage_url")

        # Try to build a YouTube URL from the ID if we only have that
        video_id = data.get("id")
        if not self.webpage_url and video_id:
            # heuristic: if extractor_key or ie_key mentions youtube
            extractor = data.get("extractor_key") or data.get("ie_key") or ""
            if "youtube" in extractor.lower() or len(video_id) == 11:
                self.webpage_url = f"https://www.youtube.com/watch?v={video_id}"

    @classmethod
    async def from_playlist_url(cls, url, *, loop=None, playlist_items=None):
        """
        Fast playlist extraction — returns a list of PlaceholderTracks.
        Only metadata is fetched (no stream URLs), so this is near-instant
        even for large playlists.

        Each PlaceholderTrack's webpage_url is set to the video's watch URL,
        which play_next can later resolve to a stream URL via YTDLSource.resolve().
        """
        loop = loop or asyncio.get_event_loop()

        opts = {
            "extract_flat": True,
            "quiet": True,
            "no_warnings": True,
            "extractor_retries": 2,
        }
        if playlist_items:
            opts["playlist_items"] = playlist_items

        data = await loop.run_in_executor(
            None, lambda: yt_dlp.YoutubeDL(opts).extract_info(url, download=False)
        )

        if not data or "entries" not in data:
            # Not a playlist or extraction failed — fall back to full extraction
            logging.warning(
                f"PlaceholderTrack.from_playlist_url: No playlist entries for {url}. "
                "Falling back to full YTDLSource extraction."
            )
            resolved = await YTDLSource.from_url(url, loop=loop)
            return resolved  # Already a list of YTDLSource objects (fully resolved)

        placeholders = []
        for entry in data["entries"]:
            if entry is None:
                continue
            placeholders.append(cls(entry))

        logging.info(
            f"PlaceholderTrack.from_playlist_url: Fast-extracted {len(placeholders)} "
            f"entries from playlist {url}"
        )
        return placeholders
