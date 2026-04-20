"""
Thin HTTP client for the DJ Bot's Mission Control API.

Supports two auth modes:
  1. Hermes Bearer token (preferred) — machine-to-machine, no CSRF needed
  2. Session + CSRF (fallback) — browser-like login, for legacy endpoints

All Hermes endpoints are prefixed with /api/hermes/ and use Bearer auth.
Legacy endpoints use session cookies + CSRF tokens.
"""

import logging
from typing import Optional

import aiohttp

logger = logging.getLogger("shadow.api")


class MissionControlClient:
    """Async client for the DJ Bot Mission Control REST API."""

    def __init__(self, base_url: str, web_password: str = "", hermes_api_key: str = ""):
        """
        Args:
            base_url: e.g. "http://192.168.1.50:8080"
            web_password: Optional Mission Control login password (session auth fallback)
            hermes_api_key: Hermes Agent API key (Bearer token auth, preferred)
        """
        self.base_url = base_url.rstrip("/")
        self.web_password = web_password
        self.hermes_api_key = hermes_api_key
        self._session: Optional[aiohttp.ClientSession] = None
        self._csrf_token: Optional[str] = None
        self._use_hermes = bool(hermes_api_key)  # Prefer Hermes if key is set

    # ── Session lifecycle ──────────────────────────────────────────

    async def start(self):
        """Create the HTTP session and authenticate."""
        headers = {"Accept": "application/json"}
        if self._use_hermes:
            headers["Authorization"] = f"Bearer {self.hermes_api_key}"

        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers=headers,
        )

        if self._use_hermes:
            # Test Hermes auth
            try:
                resp = await self._get("/api/hermes/state")
                if resp.get("error") and "Unauthorized" in str(resp.get("error", "")):
                    logger.error(
                        "Hermes API key rejected! Check HERMES_API_KEY in .env"
                    )
                    self._use_hermes = False
                else:
                    logger.info("Hermes Agent API connected (Bearer auth) ✅")
                    return
            except Exception as e:
                logger.warning(
                    "Hermes auth test failed: %s — falling back to session auth", e
                )
                self._use_hermes = False

        # Fallback: session-based auth
        if self.web_password:
            await self._authenticate()

    async def close(self):
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    # ── Auth ───────────────────────────────────────────────────────

    async def _authenticate(self):
        """Log into Mission Control and store session + CSRF token."""
        login_url = f"{self.base_url}/login"
        async with self._session.post(
            login_url,
            data={"password": self.web_password},
            allow_redirects=False,
        ) as resp:
            if resp.status not in (200, 302):
                logger.warning("Login may have failed (status %d)", resp.status)

        # Fetch any page to extract CSRF token from meta tag
        try:
            async with self._session.get(f"{self.base_url}/") as as_resp:
                text = await as_resp.text()
                import re

                match = re.search(
                    r'<meta\s+name="csrf-token"\s+content="([^"]+)"', text
                )
                if match:
                    self._csrf_token = match.group(1)
                    logger.info("CSRF token acquired")
        except Exception as e:
            logger.warning("Could not fetch CSRF token: %s", e)

    def _headers(self) -> dict:
        """Return headers with CSRF token if available."""
        h = {}
        if self._csrf_token:
            h["X-CSRFToken"] = self._csrf_token
        return h

    # ── Generic request helpers ────────────────────────────────────

    async def _get(self, path: str) -> dict:
        url = f"{self.base_url}{path}"
        try:
            async with self._session.get(url) as resp:
                if resp.content_type == "application/json":
                    return await resp.json()
                return {"status": resp.status, "text": await resp.text()}
        except aiohttp.ClientError as e:
            logger.error("GET %s failed: %s", path, e)
            return {"error": str(e)}

    async def _post(
        self, path: str, json: Optional[dict] = None, data: Optional[dict] = None
    ) -> dict:
        url = f"{self.base_url}{path}"
        try:
            async with self._session.post(
                url, json=json, data=data, headers=self._headers()
            ) as resp:
                if resp.content_type == "application/json":
                    return await resp.json()
                return {"status": resp.status, "text": await resp.text()}
        except aiohttp.ClientError as e:
            logger.error("POST %s failed: %s", path, e)
            return {"error": str(e)}

    # ═══════════════════════════════════════════════════════════════
    # ── HERMES AGENT ENDPOINTS (preferred) ───────────────────────
    # ═══════════════════════════════════════════════════════════════
    # These use /api/hermes/* with Bearer token auth.
    # No CSRF token needed, no session cookies.
    # ═══════════════════════════════════════════════════════════════

    async def hermes_state(self, guild_id: str = "") -> dict:
        """GET /api/hermes/state — full bot state (queue, now playing, cookies, audio)."""
        path = "/api/hermes/state"
        if guild_id:
            path += f"?guild_id={guild_id}"
        return await self._get(path)

    async def hermes_queue(self, guild_id: str = "") -> dict:
        """GET /api/hermes/queue — queue contents."""
        path = "/api/hermes/queue"
        if guild_id:
            path += f"?guild_id={guild_id}"
        return await self._get(path)

    async def hermes_queue_add(
        self, query: str, position: str = "end", guild_id: str = ""
    ) -> dict:
        """POST /api/hermes/queue/add — add song by URL or search query."""
        path = "/api/hermes/queue/add"
        if guild_id:
            path += f"?guild_id={guild_id}"
        return await self._post(path, json={"query": query, "position": position})

    async def hermes_queue_remove(self, position: int, guild_id: str = "") -> dict:
        """POST /api/hermes/queue/remove — remove song by position index."""
        path = "/api/hermes/queue/remove"
        if guild_id:
            path += f"?guild_id={guild_id}"
        return await self._post(path, json={"position": position})

    async def hermes_queue_move(
        self, from_pos: int, to_pos: int, guild_id: str = ""
    ) -> dict:
        """POST /api/hermes/queue/move — reorder queue."""
        path = "/api/hermes/queue/move"
        if guild_id:
            path += f"?guild_id={guild_id}"
        return await self._post(path, json={"from": from_pos, "to": to_pos})

    async def hermes_queue_clear(self, guild_id: str = "") -> dict:
        """POST /api/hermes/queue/clear — clear entire queue."""
        path = "/api/hermes/queue/clear"
        if guild_id:
            path += f"?guild_id={guild_id}"
        return await self._post(path)

    async def hermes_queue_shuffle(self, guild_id: str = "") -> dict:
        """POST /api/hermes/queue/shuffle — randomize queue order."""
        path = "/api/hermes/queue/shuffle"
        if guild_id:
            path += f"?guild_id={guild_id}"
        return await self._post(path)

    async def hermes_skip(self, guild_id: str = "") -> dict:
        """POST /api/hermes/skip — skip current song."""
        path = "/api/hermes/skip"
        if guild_id:
            path += f"?guild_id={guild_id}"
        return await self._post(path)

    async def hermes_cookies_health(self) -> dict:
        """GET /api/hermes/cookies/health — cookie auth health check."""
        return await self._get("/api/hermes/cookies/health")

    async def hermes_cookies_inject(
        self, cookie_text: str, source: str = "shadow-controller"
    ) -> dict:
        """POST /api/hermes/cookies/inject — inject fresh cookies + auto-retry blocked playback."""
        return await self._post(
            "/api/hermes/cookies/inject",
            json={"cookies": cookie_text, "source": source},
        )

    # ═══════════════════════════════════════════════════════════════
    # ── LEGACY ENDPOINTS (session auth fallback) ──────────────────
    # ═══════════════════════════════════════════════════════════════
    # Used when HERMES_API_KEY is not configured.

    # ── Cookie endpoints ──────────────────────────────────────────

    async def cookie_health(self) -> dict:
        """Cookie health check — Hermes endpoint or legacy fallback."""
        if self._use_hermes:
            return await self.hermes_cookies_health()
        return await self._get("/api/ytcookies/health")

    async def cookie_auth_status(self) -> dict:
        """GET /api/ytcookies/auth_status — auth_blocked flag + recent errors."""
        return await self._get("/api/ytcookies/auth_status")

    async def cookie_inject(
        self, cookie_text: str, source: str = "shadow-controller"
    ) -> dict:
        """Inject fresh cookies — Hermes endpoint or legacy fallback."""
        if self._use_hermes:
            return await self.hermes_cookies_inject(cookie_text, source)
        return await self._post(
            "/api/ytcookies/inject",
            json={"cookies": cookie_text, "format": "netscape", "source": source},
        )

    async def cookie_status(self) -> dict:
        """GET /api/ytcookies/status — current cookie auth status overview."""
        return await self._get("/api/ytcookies/status")

    # ── Playback endpoints ─────────────────────────────────────────

    async def play(self, guild_id: str, query: str) -> dict:
        """Queue a song — Hermes endpoint or legacy fallback."""
        if self._use_hermes:
            return await self.hermes_queue_add(query, position="end", guild_id=guild_id)
        return await self._post(f"/api/{guild_id}/play", json={"query": query})

    async def skip(self, guild_id: str) -> dict:
        """Skip current track — Hermes endpoint or legacy fallback."""
        if self._use_hermes:
            return await self.hermes_skip(guild_id=guild_id)
        return await self._post(f"/api/{guild_id}/skip")

    async def stop(self, guild_id: str) -> dict:
        """POST /api/<guild_id>/stop — stop playback, clear queue."""
        return await self._post(f"/api/{guild_id}/stop")

    async def volume(self, guild_id: str, vol: int) -> dict:
        """POST /api/<guild_id>/volume — set volume 0-200."""
        return await self._post(f"/api/{guild_id}/volume", json={"volume": vol})

    # ── Queue endpoints ───────────────────────────────────────────

    async def queue_status(self, guild_id: str = "") -> dict:
        """Get queue status — Hermes endpoint or scrape fallback."""
        if self._use_hermes:
            resp = await self.hermes_queue(guild_id=guild_id)
            # Normalize to the format queue_watchdog expects
            return {
                "queue_length": resp.get("queue_length", 0),
                "playing": resp.get("playing", False) if "playing" in resp else False,
                "current_title": resp.get("current_song", {}).get("title", "")
                if resp.get("current_song")
                else "",
                "autodj_enabled": resp.get("dj_enabled", False),
            }
        return await self.queue_status_scrape(guild_id)

    async def queue_clear(self, guild_id: str) -> dict:
        """Clear entire queue — Hermes endpoint or legacy fallback."""
        if self._use_hermes:
            return await self.hermes_queue_clear(guild_id=guild_id)
        return await self._post(f"/api/{guild_id}/queue/clear")

    async def queue_remove(self, guild_id: str, index: int) -> dict:
        """Remove item by index — Hermes endpoint or legacy fallback."""
        if self._use_hermes:
            return await self.hermes_queue_remove(index, guild_id=guild_id)
        url = f"{self.base_url}/api/{guild_id}/queue/{index}"
        try:
            async with self._session.delete(url, headers=self._headers()) as resp:
                if resp.content_type == "application/json":
                    return await resp.json()
                return {"status": resp.status}
        except aiohttp.ClientError as e:
            logger.error("DELETE /api/%s/queue/%d failed: %s", guild_id, index, e)
            return {"error": str(e)}

    # ── Auto-DJ endpoints ──────────────────────────────────────────

    async def autodj_toggle(self, guild_id: str) -> dict:
        """POST /api/<guild_id>/autodj_toggle — toggle Auto-DJ on/off."""
        return await self._post(f"/api/{guild_id}/autodj_toggle")

    async def autodj_source(self, guild_id: str, source: str) -> dict:
        """POST /api/<guild_id>/autodj_source — set Auto-DJ source (playlist URL or preset)."""
        return await self._post(
            f"/api/{guild_id}/autodj_source", json={"source": source}
        )

    # ── DJ mode endpoints ──────────────────────────────────────────

    async def dj_toggle(self, guild_id: str) -> dict:
        """POST /api/<guild_id>/dj_toggle — toggle DJ mode."""
        return await self._post(f"/api/{guild_id}/dj_toggle")

    async def ai_dj_toggle(self, guild_id: str) -> dict:
        """POST /api/<guild_id>/ai_dj_toggle — toggle AI side host."""
        return await self._post(f"/api/{guild_id}/ai_dj_toggle")

    # ── Stream endpoints ──────────────────────────────────────────

    async def youtube_stream_status(self, guild_id: str) -> dict:
        """GET /api/<guild_id>/youtube_stream/status — stream active/running/title."""
        return await self._get(f"/api/{guild_id}/youtube_stream/status")

    async def youtube_stream_toggle(self, guild_id: str) -> dict:
        """POST /api/<guild_id>/youtube_stream/toggle — start/stop stream."""
        return await self._post(f"/api/{guild_id}/youtube_stream/toggle")

    # ── OBS endpoints ──────────────────────────────────────────────

    async def obs_status(self) -> dict:
        """GET /api/obs/status — OBS connection/streaming/recording status."""
        return await self._get("/api/obs/status")

    async def obs_reconnect(self) -> dict:
        """POST /api/obs/reconnect — force reconnect to OBS."""
        return await self._post("/api/obs/reconnect")

    async def obs_streaming_start(self) -> dict:
        """POST /api/obs/streaming/start — start OBS streaming."""
        return await self._post("/api/obs/streaming/start")

    async def obs_streaming_configure_and_start(self) -> dict:
        """POST /api/obs/streaming/configure_and_start — configure + start in one call."""
        return await self._post("/api/obs/streaming/configure_and_start")

    async def obs_streaming_stop(self) -> dict:
        """POST /api/obs/streaming/stop — stop OBS streaming."""
        return await self._post("/api/obs/streaming/stop")

    # ── History / presets ──────────────────────────────────────────

    async def history(self, guild_id: str) -> dict:
        """GET /api/<guild_id>/history — recently played tracks."""
        return await self._get(f"/api/{guild_id}/history")

    async def presets_list(self) -> dict:
        """GET /api/presets — list saved presets."""
        return await self._get("/api/presets")

    async def preset_load(self, guild_id: str, name: str) -> dict:
        """POST /api/<guild_id>/presets/load — load a preset into queue."""
        return await self._post(f"/api/{guild_id}/presets/load", json={"name": name})

    # ── System endpoints ───────────────────────────────────────────

    async def ollama_status(self) -> dict:
        """GET /api/ollama/status — check Ollama availability."""
        return await self._get("/api/ollama/status")

    async def restart_bot(self) -> dict:
        """POST /api/restart — restart the DJ bot."""
        return await self._post("/api/restart")

    async def shutdown_bot(self) -> dict:
        """POST /api/shutdown — shut down the DJ bot."""
        return await self._post("/api/shutdown")

    # ── Dashboard scrape (fallback for queue depth) ──────────────

    async def dashboard_html(self) -> str:
        """GET / — fetch the full dashboard HTML for parsing queue state."""
        try:
            async with self._session.get(f"{self.base_url}/") as resp:
                return await resp.text()
        except aiohttp.ClientError as e:
            logger.error("Dashboard HTML fetch failed: %s", e)
            return ""

    async def queue_status_scrape(self, guild_id: str) -> dict:
        """
        Scrape queue info from the dashboard page.
        Returns {queue_length, playing, current_title, autodj_enabled}.

        This is a fallback when Hermes API is not available.
        """
        html = await self.dashboard_html()
        if not html:
            return {"error": "could not fetch dashboard"}

        import re

        result = {
            "queue_length": 0,
            "playing": False,
            "current_title": "",
            "autodj_enabled": False,
        }

        queue_items = re.findall(r'data-queue-item|class="queue-item"', html)
        result["queue_length"] = len(queue_items)

        title_match = re.search(r'data-current-title="([^"]*)"', html)
        if not title_match:
            title_match = re.search(r'class="now-playing[^"]*"[^>]*>([^<]+)<', html)
        if title_match:
            result["current_title"] = title_match.group(1)

        if "Now Playing" in html or "data-playing" in html:
            result["playing"] = True

        if "autodj" in html.lower() and "enabled" in html.lower():
            result["autodj_enabled"] = True

        return result
