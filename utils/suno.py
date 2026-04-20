"""
utils/suno.py — Suno.com URL detection and audio stream resolution.

Supports:
  Single song:  https://suno.com/song/<uuid>
                 https://app.suno.ai/song/<uuid>
  Playlist:     https://suno.com/playlist/<uuid>
                 https://app.suno.ai/playlist/<uuid>

Single songs resolve via OG tag scraping → direct CDN MP3.
Playlists are extracted via two strategies:
  1. Server-rendered JSON (Next.js __NEXT_DATA__ or embedded JSON-LD)
  2. Headless browser (Playwright) — used when the SPA renders client-side only

Each song in a playlist becomes a SunoPlaylistTrack with a deterministic
CDN URL that can be played immediately. Title/thumbnail are resolved
lazily at playback time via get_suno_track().
"""

import json
import logging
import re
from typing import Optional

import aiohttp

# ── URL patterns ───────────────────────────────────────────────────────────

# Single song: suno.com/song/<uuid> or app.suno.ai/song/<uuid>
_SUNO_SONG_RE = re.compile(
    r"https?://(?:app\.suno\.ai|suno\.com)/song/([0-9a-f-]{36})", re.IGNORECASE
)

# Playlist: suno.com/playlist/<uuid> or app.suno.ai/playlist/<uuid>
_SUNO_PLAYLIST_RE = re.compile(
    r"https?://(?:app\.suno\.ai|suno\.com)/playlist/([0-9a-f-]{36})", re.IGNORECASE
)

# Broad match: any suno.com or app.suno.ai URL (used for detection)
_SUNO_ANY_RE = re.compile(
    r"https?://(?:app\.suno\.ai|suno\.com)/(?:song|playlist)/", re.IGNORECASE
)

CDN_BASE = "https://cdn1.suno.ai"

# ── Config defaults ────────────────────────────────────────────────────────
# These can be overridden via config.py
SUNO_MAX_PLAYLIST_SIZE = 50
SUNO_EXTRACTION_TIMEOUT = 30  # seconds
SUNO_PLAYWRIGHT_TIMEOUT = 20  # seconds for headless browser

# ── HTTP headers ───────────────────────────────────────────────────────────
_SUNO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


# ── URL detection ──────────────────────────────────────────────────────────


def is_suno_url(text: str) -> bool:
    """Return True if text looks like any Suno URL (song or playlist)."""
    return bool(_SUNO_ANY_RE.search(text))


def is_suno_song_url(text: str) -> bool:
    """Return True if text is a Suno single-song URL."""
    return bool(_SUNO_SONG_RE.search(text))


def is_suno_playlist_url(text: str) -> bool:
    """Return True if text is a Suno playlist URL."""
    return bool(_SUNO_PLAYLIST_RE.search(text))


def _extract_song_id(url: str) -> str | None:
    """Extract a song UUID from a Suno song URL."""
    m = _SUNO_SONG_RE.search(url)
    return m.group(1) if m else None


def _extract_playlist_id(url: str) -> str | None:
    """Extract a playlist UUID from a Suno playlist URL."""
    m = _SUNO_PLAYLIST_RE.search(url)
    return m.group(1) if m else None


# ── Track classes ──────────────────────────────────────────────────────────


class SunoTrack:
    """A fully-resolved Suno song with title and thumbnail from OG tags.

    Compatible with music.py's queue/playback system.
    Has a deterministic CDN URL: https://cdn1.suno.ai/{song_id}.mp3
    """

    def __init__(
        self, song_id: str, title: str, thumbnail: str | None, webpage_url: str
    ):
        self.song_id = song_id
        self.title = title
        self.url = f"{CDN_BASE}/{song_id}.mp3"
        self.duration = 0  # Unknown — progress bar will show 0:00 / 0:00
        self.thumbnail = thumbnail
        self.webpage_url = webpage_url

    def __repr__(self):
        return f"SunoTrack(id={self.song_id[:8]}…, title={self.title!r})"


class SunoPlaylistTrack:
    """A lightweight track from a Suno playlist, ready for immediate playback.

    Unlike PlaceholderTrack (which needs yt-dlp resolution at playback time),
    SunoPlaylistTrack has a deterministic CDN URL that works right away.
    Title and thumbnail are best-effort from the playlist extraction;
    if not available, a placeholder title is used and they can be enriched
    at playback time by calling get_suno_track().

    This is the key design choice: Suno CDN URLs are deterministic from the
    song ID alone, so we can start playing immediately without any network
    request at enqueue time. The title is just for display.
    """

    def __init__(
        self,
        song_id: str,
        title: str | None = None,
        thumbnail: str | None = None,
        webpage_url: str | None = None,
    ):
        self.song_id = song_id
        self.title = title or f"Suno ({song_id[:8]}…)"
        self.url = f"{CDN_BASE}/{song_id}.mp3"
        self.duration = 0
        self.thumbnail = thumbnail
        self.webpage_url = webpage_url or f"https://suno.com/song/{song_id}"

    async def resolve(self) -> "SunoTrack | None":
        """Enrich this track with full metadata from Suno's OG tags.

        Called at playback time if we want a proper title and thumbnail
        for the now-playing embed. NOT required for audio playback —
        the CDN URL already works.

        Returns a fully-resolved SunoTrack, or None if the page can't be fetched.
        """
        track = await get_suno_track(self.webpage_url)
        if track:
            # Update ourselves with the richer data
            self.title = track.title
            self.thumbnail = track.thumbnail
        return track

    def __repr__(self):
        return f"SunoPlaylistTrack(id={self.song_id[:8]}…, title={self.title!r})"


# ── Single song resolution ────────────────────────────────────────────────


async def get_suno_track(url: str) -> SunoTrack | None:
    """
    Given a Suno song URL, return a SunoTrack ready for playback.
    Returns None if the song ID cannot be extracted or the CDN is unreachable.
    """
    song_id = _extract_song_id(url)
    if not song_id:
        logging.warning(f"get_suno_track: Could not extract song ID from URL: {url}")
        return None

    webpage_url = f"https://suno.com/song/{song_id}"
    cdn_url = f"{CDN_BASE}/{song_id}.mp3"
    title = f"Suno Song ({song_id[:8]}…)"  # fallback title
    thumbnail = None

    try:
        async with aiohttp.ClientSession(headers=_SUNO_HEADERS) as session:
            # Scrape OG tags for title + thumbnail
            async with session.get(
                webpage_url, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    html = await resp.text()
                    title, thumbnail = _scrape_og_tags(html, title)
                else:
                    logging.warning(
                        f"get_suno_track: Page fetch returned HTTP {resp.status} for {webpage_url}"
                    )

            # Quick HEAD check that the CDN MP3 is accessible
            async with session.head(
                cdn_url, timeout=aiohttp.ClientTimeout(total=8)
            ) as cdn_resp:
                if cdn_resp.status not in (200, 206):
                    logging.error(
                        f"get_suno_track: CDN returned HTTP {cdn_resp.status} for {cdn_url}"
                    )
                    return None

    except aiohttp.ClientError as e:
        logging.error(f"get_suno_track: HTTP error resolving {url}: {e}")
        return None
    except Exception as e:
        logging.error(f"get_suno_track: Unexpected error for {url}: {e}")
        return None

    logging.info(f"get_suno_track: Resolved '{title}' → {cdn_url}")
    return SunoTrack(
        song_id=song_id, title=title, thumbnail=thumbnail, webpage_url=webpage_url
    )


# ── Playlist extraction ────────────────────────────────────────────────────


async def get_suno_playlist(
    url: str, max_songs: int = SUNO_MAX_PLAYLIST_SIZE
) -> list[SunoPlaylistTrack]:
    """
    Extract all songs from a Suno playlist URL.

    Strategy:
      1. Strategy 1 (FAST): Fetch the page HTML and try to extract song IDs
         from server-rendered JSON (Next.js __NEXT_DATA__ or embedded JSON-LD).
         Many Suno playlist pages include the playlist data as server-rendered
         JSON in a <script> tag, which we can parse without a browser.

      2. Strategy 2 (RELIABLE): If Strategy 1 yields no results (SPA-only page),
         fall back to Playwright headless browser to render the page and
         extract song IDs from the rendered DOM.

    Returns a list of SunoPlaylistTrack objects, each with a deterministic
    CDN URL ready for immediate playback. Title/thumbnail may be placeholder
    values if not found in the extracted data.

    Max playlist size is capped at max_songs (default: 50) to prevent
    abuse or accidental loading of massive playlists.
    """
    playlist_id = _extract_playlist_id(url)
    if not playlist_id:
        logging.warning(f"get_suno_playlist: Not a Suno playlist URL: {url}")
        return []

    logging.info(f"get_suno_playlist: Extracting playlist {playlist_id} from {url}")

    # Strategy 1: Try server-rendered JSON first (fast, no browser needed)
    tracks = await _extract_playlist_from_html(url, max_songs)

    if tracks:
        logging.info(
            f"get_suno_playlist: Extracted {len(tracks)} songs via HTML scraping"
        )
        return tracks

    # Strategy 2: Fall back to Playwright headless browser
    logging.info("get_suno_playlist: HTML scraping found no songs, trying Playwright")
    tracks = await _extract_playlist_with_playwright(url, max_songs)

    if tracks:
        logging.info(f"get_suno_playlist: Extracted {len(tracks)} songs via Playwright")
        return tracks

    logging.warning(f"get_suno_playlist: No songs found in playlist {playlist_id}")
    return []


async def _extract_playlist_from_html(
    url: str, max_songs: int
) -> list[SunoPlaylistTrack]:
    """Strategy 1: Extract song IDs from server-rendered HTML/JSON.

    Suno's pages sometimes include playlist data as:
    - Next.js __NEXT_DATA__ JSON in a <script id="__NEXT_DATA__"> tag
    - JSON-LD structured data in <script type="application/ld+json">
    - OG tags with the first song's info (we can at least get that one)

    We search for all song UUIDs in the page HTML using the known UUID pattern.
    """
    try:
        async with aiohttp.ClientSession(headers=_SUNO_HEADERS) as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=SUNO_EXTRACTION_TIMEOUT)
            ) as resp:
                if resp.status != 200:
                    logging.warning(
                        f"_extract_playlist_from_html: HTTP {resp.status} for {url}"
                    )
                    return []

                html = await resp.text()
    except Exception as e:
        logging.error(f"_extract_playlist_from_html: Error fetching {url}: {e}")
        return []

    if not html:
        return []

    tracks = []

    # ── Method A: Try __NEXT_DATA__ (Next.js server-rendered JSON) ──
    # Next.js pages embed their data in: <script id="__NEXT_DATA__" type="application/json">
    next_data_match = re.search(
        r'<script\s+id="__NEXT_DATA__"\s+type="application/json"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    if next_data_match:
        try:
            data = json.loads(next_data_match.group(1))
            song_ids = _extract_song_ids_from_json(data)
            for sid in song_ids[:max_songs]:
                tracks.append(
                    SunoPlaylistTrack(
                        song_id=sid,
                        webpage_url=f"https://suno.com/song/{sid}",
                    )
                )
            if tracks:
                logging.info(
                    f"_extract_playlist_from_html: Found {len(tracks)} songs via __NEXT_DATA__"
                )
                return tracks
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logging.debug(
                f"_extract_playlist_from_html: __NEXT_DATA__ parse failed: {e}"
            )

    # ── Method B: Try JSON-LD structured data ──
    # <script type="application/ld+json">...</script>
    json_ld_matches = re.findall(
        r'<script\s+type="application/ld\+json"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    )
    for json_ld_str in json_ld_matches:
        try:
            data = json.loads(json_ld_str)
            song_ids = _extract_song_ids_from_json(data)
            for sid in song_ids[:max_songs]:
                tracks.append(
                    SunoPlaylistTrack(
                        song_id=sid,
                        webpage_url=f"https://suno.com/song/{sid}",
                    )
                )
            if tracks:
                logging.info(
                    f"_extract_playlist_from_html: Found {len(tracks)} songs via JSON-LD"
                )
                return tracks
        except (json.JSONDecodeError, KeyError, TypeError):
            continue

    # ── Method C: Brute-force UUID scan across entire HTML ──
    # The playlist page HTML often contains song URLs even if not in structured data.
    # Find all /song/<uuid> patterns and deduplicate.
    all_ids = re.findall(r"/song/([0-9a-f-]{36})", html)
    seen = set()
    for sid in all_ids:
        if sid not in seen:
            seen.add(sid)
            tracks.append(
                SunoPlaylistTrack(
                    song_id=sid,
                    webpage_url=f"https://suno.com/song/{sid}",
                )
            )
        if len(tracks) >= max_songs:
            break

    if tracks:
        logging.info(
            f"_extract_playlist_from_html: Found {len(tracks)} songs via UUID scan"
        )

    return tracks


def _extract_song_ids_from_json(data, depth: int = 0) -> list[str]:
    """Recursively extract Suno song UUIDs from a JSON structure.

    Searches for any string value matching a Suno song ID pattern.
    Also looks for common key names like 'song_id', 'id', 'clip_id'.
    """
    if depth > 10:  # Prevent infinite recursion on deeply nested structures
        return []

    ids = []

    if isinstance(data, dict):
        # Check specific keys that are likely to contain song IDs
        for key in ("id", "song_id", "clip_id", "audio_url", "url"):
            val = data.get(key)
            if isinstance(val, str) and re.match(r"^[0-9a-f-]{36}$", val):
                ids.append(val)

        # Check for 'songs' or 'clips' arrays
        for key in ("songs", "clips", "items", "entries", "tracks"):
            val = data.get(key)
            if isinstance(val, list):
                for item in val:
                    ids.extend(_extract_song_ids_from_json(item, depth + 1))

        # Recurse into all values
        for val in data.values():
            if isinstance(val, (dict, list)):
                ids.extend(_extract_song_ids_from_json(val, depth + 1))

    elif isinstance(data, list):
        for item in data:
            ids.extend(_extract_song_ids_from_json(item, depth + 1))

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for sid in ids:
        if sid not in seen:
            seen.add(sid)
            unique.append(sid)
    return unique


async def _extract_playlist_with_playwright(
    url: str, max_songs: int
) -> list[SunoPlaylistTrack]:
    """Strategy 2: Use Playwright to render the SPA and extract song IDs.

    This is the fallback for pages that are entirely client-rendered.
    Requires the `playwright` package to be installed.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logging.warning(
            "_extract_playlist_with_playwright: playwright not installed — "
            "cannot extract Suno playlist from SPA-only pages. "
            "Install with: pip install playwright && playwright install chromium"
        )
        return []

    tracks = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            logging.info(f"_extract_playlist_with_playwright: Navigating to {url}")
            await page.goto(
                url, wait_until="networkidle", timeout=SUNO_PLAYWRIGHT_TIMEOUT * 1000
            )

            # Wait for song cards to render
            # Suno renders song links as <a href="/song/<uuid>">
            try:
                await page.wait_for_selector(
                    'a[href*="/song/"]', timeout=SUNO_PLAYWRIGHT_TIMEOUT * 1000
                )
            except Exception:
                logging.warning(
                    "_extract_playlist_with_playwright: No song links found after waiting"
                )
                # Try scrolling to load lazy content
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2000)

            # Extract all song IDs from rendered <a> elements
            song_ids = await page.eval_on_all_selector(
                'a[href*="/song/"]',
                "elements => elements.map(e => e.getAttribute('href'))",
            )

            # Also check for data attributes that might contain clip IDs
            # Some Suno versions use data-clip-id or similar attributes
            clip_ids = await page.evaluate("""
                () => {
                    const ids = [];
                    document.querySelectorAll('[data-clip-id]').forEach(el => {
                        ids.push(el.getAttribute('data-clip-id'));
                    });
                    return ids;
                }
            """)

            seen = set()
            # Process <a href> song links
            for href in song_ids or []:
                if not href:
                    continue
                m = _SUNO_SONG_RE.search(href)
                if m and m.group(1) not in seen:
                    seen.add(m.group(1))
                    tracks.append(
                        SunoPlaylistTrack(
                            song_id=m.group(1),
                            webpage_url=f"https://suno.com/song/{m.group(1)}",
                        )
                    )
                    if len(tracks) >= max_songs:
                        break

            # Process data-clip-id attributes
            for clip_id in clip_ids or []:
                if (
                    clip_id
                    and clip_id not in seen
                    and re.match(r"^[0-9a-f-]{36}$", clip_id)
                ):
                    seen.add(clip_id)
                    tracks.append(
                        SunoPlaylistTrack(
                            song_id=clip_id,
                            webpage_url=f"https://suno.com/song/{clip_id}",
                        )
                    )
                    if len(tracks) >= max_songs:
                        break

            # Scroll through the page to load all lazy-loaded content
            if len(tracks) < max_songs and len(tracks) > 0:
                previous_count = len(tracks)
                for scroll_attempt in range(5):
                    await page.evaluate(
                        "window.scrollTo(0, document.body.scrollHeight)"
                    )
                    await page.wait_for_timeout(1500)

                    # Re-extract song links after scrolling
                    new_hrefs = await page.eval_on_all_selector(
                        'a[href*="/song/"]',
                        "elements => elements.map(e => e.getAttribute('href'))",
                    )
                    for href in new_hrefs or []:
                        if not href:
                            continue
                        m = _SUNO_SONG_RE.search(href)
                        if m and m.group(1) not in seen:
                            seen.add(m.group(1))
                            tracks.append(
                                SunoPlaylistTrack(
                                    song_id=m.group(1),
                                    webpage_url=f"https://suno.com/song/{m.group(1)}",
                                )
                            )
                            if len(tracks) >= max_songs:
                                break

                    if len(tracks) == previous_count:
                        break  # No new songs loaded
                    previous_count = len(tracks)

            await browser.close()

    except Exception as e:
        logging.error(f"_extract_playlist_with_playwright: Error: {e}")
        return []

    return tracks


# ── OG tag scraping helper ─────────────────────────────────────────────────


def _scrape_og_tags(html: str, fallback_title: str) -> tuple[str, str | None]:
    """Extract og:title and og:image from HTML.

    Returns (title, thumbnail_url). Uses fallback_title if og:title not found.
    """
    title = fallback_title
    thumbnail = None

    # og:title
    m = re.search(
        r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
        html,
    )
    if m:
        title = m.group(1).strip()
    else:
        # Also try content before property (some pages swap the order)
        m2 = re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
            html,
        )
        if m2:
            title = m2.group(1).strip()
        else:
            # Fallback: <title> tag
            m3 = re.search(r"<title>([^<]+)</title>", html)
            if m3:
                title = m3.group(1).strip()

    # og:image
    m = re.search(
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
        html,
    )
    if m:
        thumbnail = m.group(1).strip()
    else:
        m2 = re.search(
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
            html,
        )
        if m2:
            thumbnail = m2.group(1).strip()

    return title, thumbnail
