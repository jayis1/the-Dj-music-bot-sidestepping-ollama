"""
Alert System — Discord webhook + Mission Control log forwarding.

Sends instant alerts via Discord webhook and optionally logs
to the DJ bot's Mission Control activity log.
"""

import asyncio
import logging
from datetime import datetime
from enum import Enum
from typing import Optional

import aiohttp

logger = logging.getLogger("shadow.alerts")


class AlertLevel(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    SUCCESS = "success"


# Emoji prefix per alert level
ALERT_EMOJI = {
    AlertLevel.INFO: "🔵",
    AlertLevel.WARNING: "🟡",
    AlertLevel.ERROR: "🔴",
    AlertLevel.SUCCESS: "🟢",
}

# Discord webhook rate limit: 5 requests per 2 seconds
# We batch rapid-fire alerts to avoid hitting this
_PENDING_ALERTS: list = []
_ALERT_LOCK = asyncio.Lock()


class AlertSystem:
    """
    Sends alerts via Discord webhook and Mission Control API.

    Discord webhooks for instant phone notifications.
    Mission Control log for persistent history.
    """

    def __init__(self, config: dict, api_client=None):
        """
        Args:
            config: Parsed config.yaml with:
                - discord_webhook_url (str)
                - alert_to_mission_control (bool, default True)
                - alert_cooldown_seconds (int, default 30)
            api_client: MissionControlClient instance for log forwarding
        """
        self.webhook_url = config.get("discord_webhook_url", "")
        self.alert_to_mc = config.get("alert_to_mission_control", True)
        self.cooldown = config.get("alert_cooldown_seconds", 30)
        self.api = api_client
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_alert_time: dict = {}  # key -> datetime

    async def start(self):
        """Initialize the HTTP session for webhook calls."""
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10),
        )

    async def close(self):
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    def _should_send(self, key: str) -> bool:
        """Rate-limit check: don't send the same alert type too frequently."""
        now = datetime.utcnow()
        last = self._last_alert_time.get(key)
        if last and (now - last).total_seconds() < self.cooldown:
            return False
        self._last_alert_time[key] = now
        return True

    async def send(
        self,
        message: str,
        level: AlertLevel = AlertLevel.INFO,
        key: Optional[str] = None,
        force: bool = False,
    ):
        """
        Send an alert.

        Args:
            message: Alert text
            level: Severity level
            key: Dedup key (same key won't fire twice within cooldown)
            force: Skip cooldown check
        """
        if not key:
            key = f"{level.value}:{message[:50]}"

        if not force and not self._should_send(key):
            logger.debug("Alert suppressed (cooldown): %s", message)
            return

        emoji = ALERT_EMOJI.get(level, "⚪")
        timestamp = datetime.utcnow().strftime("%H:%M:%S")
        full_message = f"{emoji} **[Shadow]** `{timestamp}` — {message}"

        logger.log(
            logging.INFO
            if level in (AlertLevel.INFO, AlertLevel.SUCCESS)
            else logging.WARNING,
            "Alert [%s]: %s",
            level.value,
            message,
        )

        # Send to Discord webhook
        await self._send_webhook(full_message)

        # Send to Mission Control log
        if self.alert_to_mc and self.api:
            await self._send_mc_log(message, level)

    async def _send_webhook(self, message: str):
        """POST to the Discord webhook URL."""
        if not self.webhook_url or not self._session:
            return

        try:
            payload = {
                "username": "Shadow Controller",
                "avatar_url": "https://upload.wikimedia.org/wikipedia/commons/thumb/2/2a/Intel_logo.svg/1200px-Intel_logo.svg.png",
                "content": message,
            }
            async with self._session.post(
                self.webhook_url,
                json=payload,
            ) as resp:
                if resp.status == 204:
                    logger.debug("Webhook delivered")
                elif resp.status == 429:
                    resp_json = await resp.json()
                    retry_after = resp_json.get("retry_after", 2)
                    logger.warning(
                        "Webhook rate limited, retry after %.1fs", retry_after
                    )
                    await asyncio.sleep(retry_after)
                    # Retry once
                    async with self._session.post(
                        self.webhook_url, json=payload
                    ) as resp2:
                        if resp2.status != 204:
                            logger.error("Webhook retry failed: %d", resp2.status)
                else:
                    logger.error(
                        "Webhook failed: %d %s", resp.status, await resp.text()[:200]
                    )
        except aiohttp.ClientError as e:
            logger.error("Webhook error: %s", e)

    async def _send_mc_log(self, message: str, level: AlertLevel):
        """
        Forward alert to Mission Control as a log entry.
        Uses the /api/logs/recent endpoint — the bot's DiscordLogHandler
        already pushes to the log channel.

        Note: There's no direct /api/logs/inject endpoint, so we just log
        locally. The Discord webhook is the primary alert channel.
        """
        log_level = {
            AlertLevel.INFO: logging.INFO,
            AlertLevel.SUCCESS: logging.INFO,
            AlertLevel.WARNING: logging.WARNING,
            AlertLevel.ERROR: logging.ERROR,
        }.get(level, logging.INFO)

        logger.log(log_level, "[MC-Alert] %s", message)

    # ── Convenience methods ───────────────────────────────────────

    async def info(self, message: str, **kwargs):
        await self.send(message, AlertLevel.INFO, **kwargs)

    async def warning(self, message: str, **kwargs):
        await self.send(message, AlertLevel.WARNING, **kwargs)

    async def error(self, message: str, **kwargs):
        await self.send(message, AlertLevel.ERROR, **kwargs)

    async def success(self, message: str, **kwargs):
        await self.send(message, AlertLevel.SUCCESS, **kwargs)

    async def cookie_expired(self):
        await self.warning(
            "YouTube cookies expired — auto-refreshing...",
            key="cookie_expired",
        )

    async def cookie_refreshed(self):
        await self.success(
            "Cookies refreshed successfully ✅",
            key="cookie_refreshed",
        )

    async def cookie_refresh_failed(self, reason: str = ""):
        await self.error(
            f"Cookie refresh FAILED — auth still blocked{': ' + reason if reason else ''}",
            key="cookie_refresh_failed",
        )

    async def queue_low(self, count: int):
        await self.warning(
            f"Queue running low ({count} songs) — refilling...",
            key="queue_low",
        )

    async def playlist_queued(self, url: str, source: str = "auto"):
        await self.info(
            f"Playlist queued ({source}): {url[:80]}",
            key=f"playlist_queued:{url[:30]}",
        )

    async def fan_request(self, url: str, username: str):
        await self.info(
            f"Fan request queued: {url[:80]} from @{username}",
            key=f"fan_request:{url[:30]}",
        )

    async def stream_down(self):
        await self.error(
            "YouTube Live stream is DOWN — attempting restart",
            key="stream_down",
        )

    async def stream_restarted(self):
        await self.success(
            "YouTube Live stream restarted ✅",
            key="stream_restarted",
        )

    async def obs_disconnected(self):
        await self.error(
            "OBS disconnected — attempting reconnect",
            key="obs_disconnected",
        )

    async def obs_reconnected(self):
        await self.success(
            "OBS reconnected ✅",
            key="obs_reconnected",
        )

    async def autodj_enabled(self, source: str = ""):
        msg = "Auto-DJ enabled"
        if source:
            msg += f" (source: {source[:50]})"
        await self.info(msg, key="autodj_enabled")

    async def hermes_error(self, task: str, error: str):
        await self.error(
            f"Hermes agent error ({task}): {error[:100]}",
            key=f"hermes_error:{task}",
        )
