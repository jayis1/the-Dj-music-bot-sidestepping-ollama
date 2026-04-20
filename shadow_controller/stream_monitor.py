"""
Stream Monitor — Loop 3: watch the YouTube Live stream + OBS health.

Ensures the broadcast stays on the air:
  1. Check YouTube Live stream status via Mission Control API
  2. Check OBS connection + streaming state
  3. Auto-restart stream or reconnect OBS if they die
  4. Monitor the YouTube Live tab in the browser for visual health
"""

import asyncio
import logging

logger = logging.getLogger("shadow.stream_monitor")


class StreamMonitor:
    """
    Monitors the YouTube Live stream and OBS Studio health.

    Dead air on YouTube Live = lost viewers. The stream monitor
    catches problems and auto-recovers before anyone notices.
    """

    def __init__(self, config: dict, api_client, browser_manager, alert_system):
        """
        Args:
            config: Parsed config.yaml with:
                - stream_check_interval (seconds, default 30)
                - stream_should_be_live (bool, default True)
                - guild_id (str)
                - stream_restart_max_attempts (int, default 3)
                - stream_restart_cooldown (seconds, default 120)
        """
        self.api = api_client
        self.browser = browser_manager
        self.alerts = alert_system

        self.interval = config.get("stream_check_interval", 30)
        self.should_be_live = config.get("stream_should_be_live", True)
        self.guild_id = str(config.get("guild_id", ""))
        self.max_restart_attempts = config.get("stream_restart_max_attempts", 3)
        self.restart_cooldown = config.get("stream_restart_cooldown", 120)

        self._running = False
        self._restart_attempts = 0
        self._last_restart_time = 0
        self._obs_disconnect_time = 0

    async def start(self):
        """Start the stream monitoring loop."""
        self._running = True
        logger.info(
            "Stream monitor started (interval: %ds, should_be_live: %s)",
            self.interval,
            self.should_be_live,
        )

        while self._running:
            try:
                await self._check_stream_health()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Stream monitor loop error: %s", e, exc_info=True)

            await asyncio.sleep(self.interval)

    def stop(self):
        """Stop the stream monitoring loop."""
        self._running = False
        logger.info("Stream monitor stopped")

    async def _check_stream_health(self):
        """Check the full stream health chain: Bot → OBS → YouTube Live."""
        if not self.guild_id:
            return

        # ── Check 1: YouTube Live stream status ──────────────────
        stream_status = await self.api.youtube_stream_status(self.guild_id)
        is_active = stream_status.get("active", False)
        is_running = stream_status.get("running", False)
        stream_title = stream_status.get("current_title", "")
        stream_status.get("curated", False)

        logger.debug(
            "Stream: active=%s, running=%s, title=%s",
            is_active,
            is_running,
            stream_title[:40] if stream_title else "none",
        )

        # If stream should be live but isn't, try to restart
        if self.should_be_live and not is_running and is_active:
            await self._attempt_stream_recovery(
                "stream not running but active flag set"
            )
        elif self.should_be_live and not is_active:
            await self._attempt_stream_recovery("stream not active")

        # ── Check 2: OBS health ──────────────────────────────────
        obs_status = await self.api.obs_status()
        obs_connected = obs_status.get("connected", False)
        obs_streaming = obs_status.get("streaming", False)
        obs_recording = obs_status.get("recording", False)

        logger.debug(
            "OBS: connected=%s, streaming=%s, recording=%s",
            obs_connected,
            obs_streaming,
            obs_recording,
        )

        if not obs_connected:
            now = asyncio.get_event_loop().time()
            # Only try to reconnect if we haven't tried recently
            if now - self._obs_disconnect_time > self.restart_cooldown:
                self._obs_disconnect_time = now
                await self._attempt_obs_reconnect()

        # OBS connected but not streaming when it should be
        elif obs_connected and not obs_streaming and self.should_be_live:
            logger.warning("OBS connected but not streaming — starting stream")
            result = await self.api.obs_streaming_start()
            if result.get("error"):
                # Try the full configure_and_start as fallback
                await self.api.obs_streaming_configure_and_start()
                await self.alerts.stream_restarted()
            else:
                await self.alerts.success(
                    "OBS streaming restarted", key="obs_stream_restarted"
                )

        # ── Check 3: Browser tab health ──────────────────────────
        if self.browser.youtube_live_url:
            tab_alive = await self.browser.check_stream_tab_alive()
            if not tab_alive:
                logger.warning("Stream monitoring tab is dead — reopening")
                try:
                    await self.browser.open_stream_tab(self.browser.youtube_live_url)
                except Exception as e:
                    logger.error("Failed to reopen stream tab: %s", e)

    async def _attempt_stream_recovery(self, reason: str):
        """Try to recover a dead YouTube Live stream."""
        now = asyncio.get_event_loop().time()

        # Rate limit restart attempts
        if now - self._last_restart_time < self.restart_cooldown:
            logger.debug(
                "Stream restart on cooldown (%.0fs remaining)",
                self.restart_cooldown - (now - self._last_restart_time),
            )
            return

        if self._restart_attempts >= self.max_restart_attempts:
            await self.alerts.error(
                f"Stream recovery given up after {self.max_restart_attempts} attempts — needs manual fix",
                key="stream_recovery_failed",
            )
            # Reset after a longer cooldown
            await asyncio.sleep(self.restart_cooldown * 3)
            self._restart_attempts = 0
            return

        self._last_restart_time = now
        self._restart_attempts += 1

        logger.warning(
            "Stream recovery attempt %d/%d: %s",
            self._restart_attempts,
            self.max_restart_attempts,
            reason,
        )
        await self.alerts.stream_down()

        # Try 1: OBS streaming start
        result = await self.api.obs_streaming_start()
        if not result.get("error"):
            await asyncio.sleep(5)
            # Verify it's actually running now
            status = await self.api.youtube_stream_status(self.guild_id)
            if status.get("running"):
                self._restart_attempts = 0
                await self.alerts.stream_restarted()
                return

        # Try 2: Full configure and start
        result = await self.api.obs_streaming_configure_and_start()
        if not result.get("error"):
            await asyncio.sleep(5)
            status = await self.api.youtube_stream_status(self.guild_id)
            if status.get("running"):
                self._restart_attempts = 0
                await self.alerts.stream_restarted()
                return

        # Try 3: Toggle the stream via the bot's endpoint
        result = await self.api.youtube_stream_toggle(self.guild_id)
        logger.info("Stream toggle result: %s", result)

        await asyncio.sleep(5)
        status = await self.api.youtube_stream_status(self.guild_id)
        if status.get("running"):
            self._restart_attempts = 0
            await self.alerts.stream_restarted()

    async def _attempt_obs_reconnect(self):
        """Try to reconnect to OBS Studio."""
        logger.warning("OBS disconnected — attempting reconnect")
        await self.alerts.obs_disconnected()

        result = await self.api.obs_reconnect()
        if result.get("error"):
            logger.error("OBS reconnect failed: %s", result.get("error"))
        else:
            await asyncio.sleep(3)
            obs = await self.api.obs_status()
            if obs.get("connected"):
                await self.alerts.obs_reconnected()
            else:
                logger.warning("OBS still not connected after reconnect attempt")

    def set_should_be_live(self, value: bool):
        """Update whether the stream should be live (called by other modules)."""
        old = self.should_be_live
        self.should_be_live = value
        if old != value:
            logger.info("Stream should_be_live changed: %s → %s", old, value)
            self._restart_attempts = 0  # Reset attempts on state change
