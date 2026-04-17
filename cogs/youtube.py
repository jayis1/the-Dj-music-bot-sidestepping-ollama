import asyncio
import yt_dlp
import discord
import logging
import os
import config

FFMPEG_OPTIONS = {
    "before_options": '-user_agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.88 Safari/537.36" -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    "options": "-vn",
}

# ── Cookie / Auth helpers ─────────────────────────────────────────────────


def _build_cookie_opts():
    """
    Build cookie-related yt-dlp options from config.
    Priority: cookies_from_browser > cookiefile > none
    Returns a dict of cookie options to merge into yt-dlp opts.
    """
    opts = {}
    browser = config.YTDDL_COOKIES_FROM_BROWSER.strip()
    cookiefile = config.YTDDL_COOKIEFILE.strip()

    if browser:
        # cookies_from_browser can be "browser" or "browser:profile"
        # e.g. "chrome", "firefox", "chrome:Profile 1", "firefox:default"
        parts = browser.split(":", 1)
        browser_name = parts[0].strip().lower()
        profile = parts[1].strip() if len(parts) > 1 else None
        try:
            if profile:
                opts["cookiesfrombrowser"] = (browser_name, profile)
            else:
                opts["cookiesfrombrowser"] = (browser_name,)
            logging.info(
                f"yt-dlp: Using cookies from browser '{browser_name}'"
                + (f" profile '{profile}'" if profile else "")
            )
        except Exception as e:
            logging.error(f"yt-dlp: Failed to set cookies_from_browser: {e}")
            opts.pop("cookiesfrombrowser", None)

    elif cookiefile and os.path.exists(cookiefile):
        opts["cookiefile"] = cookiefile
        logging.info(f"yt-dlp: Using cookie file '{cookiefile}'")
    else:
        if cookiefile:
            logging.warning(
                f"yt-dlp: Cookie file '{cookiefile}' not found. "
                "Running without cookies — YouTube may block requests."
            )

    return opts


# ── Shared cookie options (computed once at import) ────────────────────────
_COOKIE_OPTS = _build_cookie_opts()

# ── Format strings (tried in order on failure) ────────────────────────────
# YouTube sometimes removes certain format IDs from the player response,
# especially when signature solving fails. We try multiple format strings
# from most specific (best quality) to most permissive (anything that works).
FORMAT_FALLBACK_CHAIN = [
    "bestaudio*/best",  # Prefer audio-only, fall back to any best
    "bestaudio/best",  # Audio-only, then any best (no * wildcard)
    "bestaudio[abr>0]/best",  # Audio with explicit bitrate
    "ba/b",  # Short aliases
    "best",  # Any single best format (may include video)
    "worstaudio/worst",  # Desperate: lowest quality that works
]


def _make_base_opts():
    """Base yt-dlp options without format string (shared across all attempts)."""
    return {
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
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.88 Safari/537.36"
        },
        "extractor_args": {
            "youtube": {
                "player_client": ["ios", "tv", "web"]
            }
        },
        "extract_flat": "discard_in_playlist",
    }


# ── YTDL Options ──────────────────────────────────────────────────────────
YTDL_FORMAT_OPTIONS = _make_base_opts()
YTDL_FORMAT_OPTIONS["format"] = FORMAT_FALLBACK_CHAIN[0]
YTDL_FORMAT_OPTIONS.update(_COOKIE_OPTS)


def get_ytdl_format_options():
    """
    Return a fresh copy of YTDL_FORMAT_OPTIONS with current cookie settings.
    Use this when you need to customize options — always returns a copy
    so mutations don't affect the global state.
    """
    return YTDL_FORMAT_OPTIONS.copy()


# ── Playlist Options (fast flat extraction — no stream URLs) ──────────────
# Playlist extraction also needs cookies — YouTube blocks unauthenticated
# metadata requests too when bot detection is active.
YTDL_PLAYLIST_FLAT_OPTIONS = {
    "extract_flat": True,  # Only get metadata (title, id), no stream URLs
    "quiet": True,
    "no_warnings": True,
}
YTDL_PLAYLIST_FLAT_OPTIONS.update(_COOKIE_OPTS)


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

        if ytdl_opts is not None:
            data = await loop.run_in_executor(
                None,
                lambda: yt_dlp.YoutubeDL(ytdl_opts).extract_info(url, download=False),
            )
        else:
            data = await _resolve_with_fallback(url, loop)

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

        if ytdl_opts is not None:
            data = await loop.run_in_executor(
                None,
                lambda: yt_dlp.YoutubeDL(ytdl_opts).extract_info(url, download=False),
            )
        else:
            data = await _resolve_with_fallback(url, loop)

        # Handle edge case: yt-dlp might return a playlist dict for a video URL
        if "entries" in data:
            entries = [e for e in data["entries"] if e is not None]
            if entries:
                return cls(entries[0])
        return cls(data)


async def _resolve_with_fallback(url, loop):
    """
    Try resolving a URL with multiple format strings in sequence.
    If the first attempt fails with "Requested format is not available",
    try the next format string in the fallback chain.

    This is critical because YouTube frequently breaks yt-dlp's signature
    solving, which means only a subset of formats may be available. By
    trying progressively more permissive format strings, we maximize the
    chance of finding a playable stream.
    """
    base_opts = _make_base_opts()
    base_opts.update(_COOKIE_OPTS)

    last_error = None
    for i, fmt in enumerate(FORMAT_FALLBACK_CHAIN):
        opts = base_opts.copy()
        opts["format"] = fmt

        try:
            data = await loop.run_in_executor(
                None,
                lambda o=opts, u=url: yt_dlp.YoutubeDL(o).extract_info(
                    u, download=False
                ),
            )
            if data:
                # Verify we got a usable audio URL (not just metadata/storyboard)
                if isinstance(data, dict):
                    stream_url = data.get("filepath") or data.get("url", "")
                    # If the URL is a webpage URL (not a stream), this format didn't work
                    if stream_url.startswith("https://www.youtube.com/watch"):
                        logging.warning(
                            f"_resolve_with_fallback: format '{fmt}' returned webpage URL, "
                            "not a stream — trying next format"
                        )
                        continue
                if i > 0:
                    logging.info(
                        f"_resolve_with_fallback: Succeeded with format '{fmt}' "
                        f"(attempt {i + 1}/{len(FORMAT_FALLBACK_CHAIN)})"
                    )
                return data
        except Exception as e:
            error_str = str(e)
            last_error = e
            is_format_error = "format is not available" in error_str.lower()
            is_signin_error = "sign in to confirm" in error_str.lower()

            if is_signin_error:
                # Auth error — no format string will help, bail immediately
                logging.error(
                    f"_resolve_with_fallback: YouTube auth error — cookies required. "
                    f"Format '{fmt}' attempt {i + 1}/{len(FORMAT_FALLBACK_CHAIN)}"
                )
                raise

            if is_format_error:
                logging.warning(
                    f"_resolve_with_fallback: format '{fmt}' not available, "
                    f"trying next (attempt {i + 1}/{len(FORMAT_FALLBACK_CHAIN)})"
                )
                continue

            # Other error — don't retry different formats, just raise
            raise

    # All format strings failed
    if last_error:
        logging.error(
            f"_resolve_with_fallback: All {len(FORMAT_FALLBACK_CHAIN)} format "
            f"strings failed for {url}. Last error: {last_error}"
        )
        raise last_error
    else:
        raise Exception(
            f"Could not extract any playable format for {url}. "
            "This may be a yt-dlp version issue — try upgrading: "
            "pip install -U yt-dlp"
        )


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

        # yt-dlp extract_flat=True returns the video ID in the "url" field,
        # NOT a full URL. e.g. data["url"] = "dQw4w9WgXcQ"
        # We must detect this and build a real watch URL from the ID.
        raw_url = data.get("url") or ""
        video_id = data.get("id")
        extractor = (data.get("extractor_key") or data.get("ie_key") or "").lower()

        if raw_url.startswith("http"):
            # Already a full URL — use as-is
            self.webpage_url = raw_url
        elif video_id and ("youtube" in extractor or len(video_id) == 11):
            # Bare YouTube video ID — build a proper watch URL
            self.webpage_url = f"https://www.youtube.com/watch?v={video_id}"
        elif data.get("webpage_url"):
            # Fallback to webpage_url if available
            self.webpage_url = data.get("webpage_url")
        elif raw_url and video_id:
            # Other extractors — try building from the base URL heuristically
            self.webpage_url = f"https://www.youtube.com/watch?v={video_id}"
        else:
            self.webpage_url = None
            logging.warning(
                f"PlaceholderTrack: Cannot determine URL for '{self.title}' "
                f"(id={video_id}, raw_url={raw_url!r})"
            )

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

        # Use the shared playlist flat options (includes cookies)
        opts = YTDL_PLAYLIST_FLAT_OPTIONS.copy()
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
