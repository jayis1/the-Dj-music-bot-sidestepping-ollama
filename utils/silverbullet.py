"""
SilverBullet Knowledge Base Client — Write station events to a SilverBullet PKM.

Architecture:
  - All pages live under a configurable prefix (default: "station")
  - Pages use YAML frontmatter for structured, queryable data
  - SilverBullet's #query directive can cross-reference tags like station/incident,
    station/session, station/track, station/stream-health
  - The bot writes pages via SilverBullet's HTTP Space API (PUT /.fs/<path>.md)

Page Hierarchy:
  station/
  ├── Dashboard.md                          ← Auto-generated station overview
  ├── Daily Log/
  │   └── 2026-04-20.md                     ← One page per day, aggregates events
  ├── Incidents/
  │   ├── cookie-auth-block-20260420.md      ← Cookie failures
  │   ├── queue-dry-20260420-1530.md         ← Queue ran dry
  │   ├── obs-crash-20260420.md              ← OBS crash recovery
  │   └── yt-stream-disconnect-20260420.md   ← YouTube stream lost
  ├── Sessions/
  │   └── 2026-04-20-1530.md                ← Stream session (start/end, tracks played)
  ├── Tracks/
  │   └── <slug>.md                         ← Track history entries (queryable)
  ├── Stream Health/
  │   └── 2026-04-20.md                     ← Stream health log for the day
  └── Commercials & Hijacks/
      ├── commercial-20260420-1530.md        ← Commercial break that aired
      └── hijack-20260420-1545.md           ← Station Wars hijack event

Frontmatter Schema per page type:
  Incident:    {type: incident, severity: critical|warning|info, category: ...,
                resolved: bool, timestamp: ISO8601, guild_id: int}
  Session:     {type: session, start: ISO8601, end: ISO8601|null,
                tracks_played: int, guild_id: int}
  Track:       {type: track, title: str, url: str, duration: int,
                played_at: ISO8601, source: autodj|manual|hermes}
  StreamHealth:{type: stream_health, date: YYYY-MM-DD, keyframes_ok: bool,
                bitrate: int, audio_ok: bool}
  Commercial:  {type: commercial, aired_at: ISO8601, voice: str,
                category: str, num_ads: int}
  Hijack:      {type: hijack, aired_at: ISO8601, voice: str,
                station: str, recovery_line: str}
"""

import logging
import re
import time
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

import config

logger = logging.getLogger("silverbullet")

# ── Helpers ────────────────────────────────────────────────────────


def _slug(text: str, max_len: int = 60) -> str:
    """Slugify text for SilverBullet page names (keeps alphanumeric, hyphens, spaces)."""
    text = re.sub(r"[^a-zA-Z0-9\s\-]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def _iso(ts: Optional[float] = None) -> str:
    """ISO8601 timestamp for frontmatter."""
    if ts is None:
        ts = time.time()
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _date_str(ts: Optional[float] = None) -> str:
    """YYYY-MM-DD string for daily pages."""
    if ts is None:
        ts = time.time()
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _time_str(ts: Optional[float] = None) -> str:
    """HH-MM string for sub-daily pages (colons not allowed in SB paths)."""
    if ts is None:
        ts = time.time()
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H%M")


def _frontmatter(data: Dict[str, Any]) -> str:
    """Render a dict as YAML frontmatter block."""
    lines = ["---"]
    for key, value in data.items():
        if isinstance(value, bool):
            lines.append(f"{key}: {'true' if value else 'false'}")
        elif isinstance(value, (int, float)):
            lines.append(f"{key}: {value}")
        elif isinstance(value, str):
            # Quote strings that contain special chars
            if any(
                c in value
                for c in (
                    ":",
                    "#",
                    "{",
                    "}",
                    "[",
                    "]",
                    ",",
                    "&",
                    "*",
                    "?",
                    "|",
                    "-",
                    "<",
                    ">",
                    "=",
                    "!",
                    "%",
                    "@",
                    "`",
                    '"',
                    "'",
                )
            ):
                escaped = value.replace('"', '\\"')
                lines.append(f'{key}: "{escaped}"')
            else:
                lines.append(f"{key}: {value}")
        elif isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                if isinstance(item, str):
                    lines.append(f'  - "{item}"')
                else:
                    lines.append(f"  - {item}")
        elif value is None:
            lines.append(f"{key}: null")
        else:
            lines.append(f"{key}: {json.dumps(value)}")
    lines.append("---")
    return "\n".join(lines)


def _page_path(*parts: str) -> str:
    """Build a SilverBullet page path under the configured prefix."""
    prefix = getattr(config, "SILVERBULLET_PREFIX", "station")
    segments = [prefix] + [p for p in parts if p]
    return "/".join(segments) + ".md"


def _api_url(page_path: str) -> str:
    """Build the SilverBullet HTTP API URL for a page."""
    base = getattr(config, "SILVERBULLET_URL", "").rstrip("/")
    return f"{base}/.fs/{page_path}"


# ── Low-level write ───────────────────────────────────────────────


def write_page(page_path: str, content: str, append: bool = False) -> bool:
    """Write (or append to) a SilverBullet page via the Space API.

    Args:
        page_path: Path relative to the space root, e.g. "station/Daily Log/2026-04-20.md"
        content: Markdown content (with or without frontmatter)
        append: If True, append to existing page content instead of overwriting

    Returns:
        True if write succeeded, False otherwise
    """
    if not getattr(config, "SILVERBULLET_ENABLED", False):
        logger.debug("SilverBullet disabled — skipping write to %s", page_path)
        return False

    base_url = getattr(config, "SILVERBULLET_URL", "")
    if not base_url:
        logger.warning("SILVERBULLET_URL not configured — skipping write")
        return False

    url = _api_url(page_path)

    headers = {"Content-Type": "text/markdown"}
    token = getattr(config, "SILVERBULLET_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    if append:
        # Read existing content first
        try:
            existing = read_page(page_path)
            if existing:
                content = existing + "\n\n" + content
        except Exception:
            pass  # Page doesn't exist yet — just write fresh

    try:
        resp = requests.put(
            url, data=content.encode("utf-8"), headers=headers, timeout=10
        )
        if resp.status_code in (200, 201, 204):
            logger.info("SilverBullet: wrote %s (%d bytes)", page_path, len(content))
            return True
        else:
            logger.error(
                "SilverBullet: write failed for %s — HTTP %d: %s",
                page_path,
                resp.status_code,
                resp.text[:200],
            )
            return False
    except requests.RequestException as e:
        logger.error("SilverBullet: connection error for %s: %s", page_path, e)
        return False


def read_page(page_path: str) -> Optional[str]:
    """Read a SilverBullet page via the Space API.

    Returns:
        Page content as string, or None if page doesn't exist/error
    """
    if not getattr(config, "SILVERBULLET_ENABLED", False):
        return None

    base_url = getattr(config, "SILVERBULLET_URL", "")
    if not base_url:
        return None

    url = _api_url(page_path)

    headers = {}
    token = getattr(config, "SILVERBULLET_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            return resp.text
        elif resp.status_code == 404:
            return None
        else:
            logger.warning(
                "SilverBullet: read failed for %s — HTTP %d",
                page_path,
                resp.status_code,
            )
            return None
    except requests.RequestException as e:
        logger.error("SilverBullet: read error for %s: %s", page_path, e)
        return None


def delete_page(page_path: str) -> bool:
    """Delete a SilverBullet page via the Space API."""
    if not getattr(config, "SILVERBULLET_ENABLED", False):
        return False

    base_url = getattr(config, "SILVERBULLET_URL", "")
    if not base_url:
        return False

    url = _api_url(page_path)

    headers = {}
    token = getattr(config, "SILVERBULLET_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = requests.delete(url, headers=headers, timeout=10)
        return resp.status_code in (200, 204, 404)  # 404 = already gone
    except requests.RequestException:
        return False


# ── High-level documenters ────────────────────────────────────────


def document_incident(
    title: str,
    severity: str = "warning",
    category: str = "general",
    body: str = "",
    guild_id: int = 0,
    resolved: bool = False,
    timestamp: Optional[float] = None,
) -> str:
    """Write an incident page to SilverBullet.

    Args:
        title: Short incident name (e.g. "Cookie Auth Block")
        severity: "critical" | "warning" | "info"
        category: "cookie" | "queue" | "obs" | "youtube" | "tts" | "general"
        body: Markdown body with details
        guild_id: Discord guild ID
        resolved: Whether the incident is already resolved
        timestamp: Unix timestamp (default: now)

    Returns:
        Page path that was written
    """
    ts = timestamp or time.time()
    date = _date_str(ts)
    time_suffix = _time_str(ts)
    slug_title = _slug(title)

    page_path = _page_path("Incidents", f"{slug_title}-{date}-{time_suffix}")

    frontmatter_data = {
        "type": "incident",
        "severity": severity,
        "category": category,
        "resolved": resolved,
        "timestamp": _iso(ts),
        "date": date,
        "guild_id": guild_id,
        "tags": ["station/incident", f"station/incident/{category}"],
    }

    content = _frontmatter(frontmatter_data) + f"\n\n# {title}\n\n"
    if resolved:
        content += "> ✅ **Resolved**\n\n"
    else:
        content += "> ❌ **Unresolved**\n\n"
    content += body + "\n"

    write_page(page_path, content)

    # Also append to the daily log
    _append_daily_log(
        date,
        f"## 🚨 Incident: {title}\n\n"
        f"- **Severity**: {severity}\n"
        f"- **Category**: {category}\n"
        f"- **Resolved**: {'Yes' if resolved else 'No'}\n\n"
        f"{body}\n",
    )

    return page_path


def document_session_start(
    guild_id: int = 0,
    source: str = "",
    autodj_enabled: bool = False,
    timestamp: Optional[float] = None,
) -> str:
    """Document the start of a broadcast session.

    Returns:
        Page path for the session
    """
    ts = timestamp or time.time()
    date = _date_str(ts)
    time_suffix = _time_str(ts)

    page_path = _page_path("Sessions", f"{date}-{time_suffix}")

    frontmatter_data = {
        "type": "session",
        "start": _iso(ts),
        "end": None,
        "tracks_played": 0,
        "guild_id": guild_id,
        "source": source,
        "autodj_enabled": autodj_enabled,
        "tags": ["station/session"],
    }

    content = _frontmatter(frontmatter_data) + f"\n\n# Session {date} {time_suffix}\n\n"
    content += f"- **Started**: {_iso(ts)}\n"
    content += f"- **Guild**: {guild_id}\n"
    content += f"- **Auto-DJ Source**: {source or 'none'}\n"
    content += f"- **Auto-DJ Enabled**: {autodj_enabled}\n\n"
    content += "## Tracks Played\n\n"
    content += "| # | Title | Duration | Source |\n|---|-------|----------|--------|\n"

    write_page(page_path, content)

    _append_daily_log(
        date,
        f"## 📡 Session Started: {date} {time_suffix}\n\n"
        f"- Auto-DJ: {'On' if autodj_enabled else 'Off'}\n"
        f"- Source: {source or 'none'}\n",
    )

    return page_path


def document_session_end(
    session_path: str,
    tracks_played: int = 0,
    timestamp: Optional[float] = None,
) -> bool:
    """Update a session page with end time and stats.

    Args:
        session_path: The page path from document_session_start()
        tracks_played: Total tracks played in this session
        timestamp: End time (default: now)

    Returns:
        True if updated successfully
    """
    ts = timestamp or time.time()
    existing = read_page(session_path)
    if not existing:
        return False

    # Update the end time in frontmatter
    end_iso = _iso(ts)
    updated = existing.replace("end: null", f'end: "{end_iso}"')

    # Update tracks_played count
    import re

    updated = re.sub(r"tracks_played: \d+", f"tracks_played: {tracks_played}", updated)

    # Append end summary
    duration_note = f"\n\n## Session End\n\n- **Ended**: {end_iso}\n- **Tracks Played**: {tracks_played}\n"
    updated += duration_note

    return write_page(session_path, updated)


def document_track(
    title: str,
    url: str = "",
    duration: Optional[int] = None,
    source: str = "manual",
    guild_id: int = 0,
    thumbnail: str = "",
    timestamp: Optional[float] = None,
) -> str:
    """Write a track play entry to SilverBullet.

    Returns:
        Page path
    """
    ts = timestamp or time.time()
    date = _date_str(ts)
    slug_title = _slug(title, max_len=40)
    time_suffix = _time_str(ts)

    page_path = _page_path("Tracks", f"{slug_title}-{date}-{time_suffix}")

    frontmatter_data = {
        "type": "track",
        "title": title,
        "url": url,
        "duration": duration or 0,
        "played_at": _iso(ts),
        "date": date,
        "source": source,
        "guild_id": guild_id,
        "tags": ["station/track"],
    }

    content = _frontmatter(frontmatter_data) + f"\n\n# {title}\n\n"
    if thumbnail:
        content += f"![cover]({thumbnail})\n\n"
    if url:
        content += f"[Listen]({url})\n\n"
    content += f"- **Duration**: {duration or '?'}s\n"
    content += f"- **Source**: {source}\n"

    write_page(page_path, content)

    # Append to daily log as a compact entry
    _append_daily_log(date, f"🎵 **{title}** ({duration or '?'}s) [{source}]\n")

    return page_path


def document_stream_health(
    healthy: bool = True,
    keyframes_ok: bool = True,
    bitrate: int = 0,
    audio_ok: bool = True,
    details: str = "",
    timestamp: Optional[float] = None,
) -> str:
    """Write a stream health snapshot.

    Returns:
        Page path
    """
    ts = timestamp or time.time()
    date = _date_str(ts)

    page_path = _page_path("Stream Health", date)

    # Append to the day's stream health page (don't overwrite)
    health_entry = (
        f"\n### {_iso(ts)}\n\n"
        f"| Metric | Status |\n|--------|--------|\n"
        f"| Overall | {'✅ Good' if healthy else '❌ Bad'} |\n"
        f"| Keyframes | {'✅ OK' if keyframes_ok else '❌ Too far apart'} |\n"
        f"| Bitrate | {bitrate} kbps |\n"
        f"| Audio | {'✅ OK' if audio_ok else '❌ Missing/Corrupt'} |\n\n"
    )
    if details:
        health_entry += f"{details}\n"

    # Check if daily health page exists and has frontmatter already
    existing = read_page(page_path)
    if existing:
        write_page(page_path, health_entry, append=True)
    else:
        frontmatter_data = {
            "type": "stream_health",
            "date": date,
            "tags": ["station/stream_health"],
        }
        content = _frontmatter(frontmatter_data) + f"\n\n# Stream Health — {date}\n\n"
        content += health_entry
        write_page(page_path, content)

    return page_path


def document_commercial(
    category: str = "unknown",
    voice: str = "",
    num_ads: int = 1,
    ad_titles: Optional[List[str]] = None,
    body: str = "",
    timestamp: Optional[float] = None,
) -> str:
    """Write a commercial break entry.

    Returns:
        Page path
    """
    ts = timestamp or time.time()
    date = _date_str(ts)
    time_suffix = _time_str(ts)

    page_path = _page_path("Commercials & Hijacks", f"commercial-{date}-{time_suffix}")

    frontmatter_data = {
        "type": "commercial",
        "aired_at": _iso(ts),
        "date": date,
        "voice": voice,
        "category": category,
        "num_ads": num_ads,
        "tags": ["station/commercial"],
    }

    content = _frontmatter(frontmatter_data) + "\n\n# 📺 Commercial Break\n\n"
    content += f"- **Time**: {_iso(ts)}\n"
    content += f"- **Voice**: {voice}\n"
    content += f"- **Category**: {category}\n"
    content += f"- **Number of Ads**: {num_ads}\n\n"

    if ad_titles:
        content += "## Ads Aired\n\n"
        for i, ad_title in enumerate(ad_titles, 1):
            content += f"{i}. {ad_title}\n"
        content += "\n"

    if body:
        content += body + "\n"

    write_page(page_path, content)

    _append_daily_log(date, f"📺 Commercial break ({num_ads} ads, voice={voice})\n")

    return page_path


def document_hijack(
    station_name: str = "Unknown Kosmos",
    voice: str = "",
    recovery_line: str = "",
    body: str = "",
    timestamp: Optional[float] = None,
) -> str:
    """Write a Station Wars hijack event.

    Returns:
        Page path
    """
    ts = timestamp or time.time()
    date = _date_str(ts)
    time_suffix = _time_str(ts)

    page_path = _page_path("Commercials & Hijacks", f"hijack-{date}-{time_suffix}")

    frontmatter_data = {
        "type": "hijack",
        "aired_at": _iso(ts),
        "date": date,
        "station": station_name,
        "voice": voice,
        "recovery_line": recovery_line,
        "tags": ["station/hijack"],
    }

    content = (
        _frontmatter(frontmatter_data) + f"\n\n# 📡 Station Wars: {station_name}\n\n"
    )
    content += f"- **Time**: {_iso(ts)}\n"
    content += f"- **Invading Station**: {station_name}\n"
    content += f"- **Voice**: {voice}\n\n"

    if body:
        content += f"> *{body}*\n\n"

    if recovery_line:
        content += f'**DJ Recovery**: *"{recovery_line}"*\n'

    write_page(page_path, content)

    _append_daily_log(
        date, f"📡 **STATION WARS**: {station_name} hijacked the frequency!\n"
    )

    return page_path


def update_dashboard(
    station_name: str = "",
    is_streaming: bool = False,
    current_song: str = "",
    autodj_enabled: bool = False,
    autodj_source: str = "",
    queue_length: int = 0,
    cookie_healthy: bool = True,
    yt_stream_active: bool = False,
    guild_id: int = 0,
) -> str:
    """Generate or update the station dashboard page.

    This is the landing page at station/Dashboard — a live operational
    overview with SilverBullet queries that auto-populate.

    Returns:
        Page path
    """
    page_path = _page_path("Dashboard")

    frontmatter_data = {
        "type": "dashboard",
        "station_name": station_name,
        "updated": _iso(),
        "is_streaming": is_streaming,
        "current_song": current_song,
        "tags": ["station"],
    }

    content = _frontmatter(frontmatter_data) + "\n\n"
    content += f"# 🎛️ {station_name} — Station Dashboard\n\n"
    content += f"> Last updated: {_iso()}\n\n"

    # Live status card
    content += "## Status\n\n"
    content += "| Metric | Value |\n|--------|-------|\n"
    content += f"| Stream | {'🟢 LIVE' if yt_stream_active else '🔴 Offline'} |\n"
    content += f"| Now Playing | {current_song or 'Nothing'} |\n"
    content += f"| Auto-DJ | {'✅ On' if autodj_enabled else '❌ Off'} |\n"
    content += f"| Queue Depth | {queue_length} |\n"
    content += f"| Cookies | {'✅ Healthy' if cookie_healthy else '❌ Degraded'} |\n"
    content += f"| Source | {autodj_source or 'none'} |\n\n"

    # SilverBullet queries that auto-populate from frontmatter
    content += "## Today's Activity\n\n"
    content += "```query\n"
    content += 'from p = tags["station/track"]\n'
    content += f'where p.date == "{_date_str()}"\n'
    content += "order by p.played_at desc\n"
    content += "limit 20\n"
    content += "select {|p.played_at|[[${p.name}|p.title]]|p.source|}\n"
    content += "```\n\n"

    content += "## Recent Incidents\n\n"
    content += "```query\n"
    content += 'from p = tags["station/incident"]\n'
    content += "order by p.timestamp desc\n"
    content += "limit 10\n"
    content += "select {|p.timestamp|[[${p.name}|p.title]]|p.severity|p.resolved|}\n"
    content += "```\n\n"

    content += "## Recent Hijacks\n\n"
    content += "```query\n"
    content += 'from p = tags["station/hijack"]\n'
    content += "order by p.aired_at desc\n"
    content += "limit 5\n"
    content += "select {|p.aired_at|p.station|p.voice|}\n"
    content += "```\n\n"

    write_page(page_path, content)
    return page_path


# ── Daily Log ─────────────────────────────────────────────────────


def _append_daily_log(date: str, entry: str) -> bool:
    """Append an entry to the daily log page.

    Creates the page with frontmatter if it doesn't exist.
    """
    page_path = _page_path("Daily Log", date)

    existing = read_page(page_path)
    if existing:
        return write_page(page_path, entry, append=True)
    else:
        frontmatter_data = {
            "type": "daily_log",
            "date": date,
            "tags": ["station/daily_log"],
        }
        content = _frontmatter(frontmatter_data) + f"\n\n# Daily Log — {date}\n\n"
        content += entry + "\n"
        return write_page(page_path, content)


# ── Connection Test ────────────────────────────────────────────────


def test_connection() -> Dict[str, Any]:
    """Test connectivity to the SilverBullet instance.

    Returns:
        {"connected": bool, "url": str, "writable": bool, "error": str|None}
    """
    base_url = getattr(config, "SILVERBULLET_URL", "")
    if not base_url:
        return {
            "connected": False,
            "url": "",
            "writable": False,
            "error": "SILVERBULLET_URL not configured",
        }

    url = f"{base_url}/.ping"

    headers = {}
    token = getattr(config, "SILVERBULLET_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200 and "OK" in resp.text:
            # Try a write test
            test_path = _page_path("test-connection")
            test_content = f"---\ntype: test\nts: {_iso()}\n---\n\nConnection test from {getattr(config, 'STATION_NAME', 'MBot')}.\n"
            write_ok = write_page(test_path, test_content)
            if write_ok:
                # Clean up test page
                delete_page(test_path)
            return {
                "connected": True,
                "url": base_url,
                "writable": write_ok,
                "error": None,
            }
        else:
            return {
                "connected": False,
                "url": base_url,
                "writable": False,
                "error": f"HTTP {resp.status_code}: {resp.text[:100]}",
            }
    except requests.RequestException as e:
        return {
            "connected": False,
            "url": base_url,
            "writable": False,
            "error": str(e),
        }
