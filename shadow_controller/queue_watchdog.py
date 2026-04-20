"""
Queue Watchdog — Loop 2: keep the DJ bot's queue from running dry.

Monitors queue depth and ensures there are always songs to play.
When the queue dips below threshold:
  1. Enable Auto-DJ if not already on
  2. Find and set a playlist as Auto-DJ source
  3. Queue individual songs as a last resort
"""

import asyncio
import logging

logger = logging.getLogger("shadow.queue_watchdog")


class QueueWatchdog:
    """
    Monitors the DJ bot's queue and ensures it never runs dry.

    The station must never go silent — the Queue Watchdog
    is the last line of defense before dead air.
    """

    def __init__(self, config: dict, api_client, alert_system, playlist_finder=None):
        """
        Args:
            config: Parsed config.yaml with:
                - queue_check_interval (seconds, default 60)
                - queue_min_songs (int, default 3)
                - guild_id (str)
        """
        self.api = api_client
        self.alerts = alert_system
        self.playlist_finder = playlist_finder

        self.interval = config.get("queue_check_interval", 60)
        self.min_songs = config.get("queue_min_songs", 3)
        self.guild_id = str(config.get("guild_id", ""))
        self._running = False
        self._last_playlist_url = ""
        self._rotation_index = 0

    async def start(self):
        """Start the queue monitoring loop."""
        self._running = True
        logger.info(
            "Queue watchdog started (interval: %ds, min songs: %d, guild: %s)",
            self.interval,
            self.min_songs,
            self.guild_id,
        )

        while self._running:
            try:
                await self._check_and_refill()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Queue watchdog loop error: %s", e, exc_info=True)

            await asyncio.sleep(self.interval)

    def stop(self):
        """Stop the queue monitoring loop."""
        self._running = False
        logger.info("Queue watchdog stopped")

    async def _check_and_refill(self):
        """Main loop: check queue depth, refill if low."""
        if not self.guild_id:
            logger.warning("No guild_id configured — skipping queue check")
            return

        # Step 1: Get queue status
        queue_state = await self._get_queue_state()
        queue_length = queue_state.get("queue_length", 0)
        playing = queue_state.get("playing", False)
        autodj = queue_state.get("autodj_enabled", False)
        current_title = queue_state.get("current_title", "")

        # Include currently playing song in the count
        effective_count = queue_length + (1 if playing else 0)

        logger.debug(
            "Queue state: %d queued + %s playing = %d effective (autodj=%s)",
            queue_length,
            "1" if playing else "0",
            effective_count,
            autodj,
        )

        if effective_count >= self.min_songs:
            return  # Queue is healthy

        # Step 2: Queue is low — take action
        logger.warning(
            "Queue is LOW: %d songs (minimum: %d) — %s",
            effective_count,
            self.min_songs,
            current_title[:50] if current_title else "nothing playing",
        )
        await self.alerts.queue_low(effective_count)

        # Step 3: Try to refill
        await self._refill_queue(autodj)

    async def _get_queue_state(self) -> dict:
        """
        Get the current queue state.
        Uses Hermes API when available, falls back to dashboard scraping.
        """
        return await self.api.queue_status(self.guild_id)

    async def _refill_queue(self, autodj_enabled: bool):
        """
        Refill the queue using multiple strategies:
          1. Enable Auto-DJ if not enabled
          2. Set a playlist as Auto-DJ source
          3. Play songs from presets
          4. Re-add from recently played history
        """
        # Strategy 1: Enable Auto-DJ
        if not autodj_enabled:
            logger.info("Enabling Auto-DJ...")
            result = await self.api.autodj_toggle(self.guild_id)
            if not result.get("error"):
                await self.alerts.autodj_enabled()
            else:
                logger.warning("Auto-DJ toggle failed: %s", result)

        # Strategy 2: Set a playlist as Auto-DJ source
        if self.playlist_finder:
            try:
                playlist_url = await self.playlist_finder.find_playlist()
                if playlist_url and playlist_url != self._last_playlist_url:
                    logger.info("Setting Auto-DJ source: %s", playlist_url[:80])
                    result = await self.api.autodj_source(self.guild_id, playlist_url)
                    if not result.get("error"):
                        self._last_playlist_url = playlist_url
                        await self.alerts.playlist_queued(
                            playlist_url, source="auto-discovery"
                        )
                    else:
                        logger.warning("Failed to set Auto-DJ source: %s", result)
            except Exception as e:
                logger.error("Playlist finder failed: %s", e)

        # Strategy 3: Load a preset if available
        presets = await self.api.presets_list()
        preset_names = [p.get("name", "") for p in presets if isinstance(presets, list)]
        if preset_names and not self._last_playlist_url:
            # Rotate through presets
            if self._rotation_index < len(preset_names):
                preset_name = preset_names[self._rotation_index]
                logger.info("Loading preset: %s", preset_name)
                result = await self.api.preset_load(self.guild_id, preset_name)
                if not result.get("error"):
                    self._rotation_index += 1
                    if self._rotation_index >= len(preset_names):
                        self._rotation_index = 0
                else:
                    logger.warning("Preset load failed: %s", result)

        # Strategy 4: Replay from history
        history = await self.api.history(self.guild_id)
        if isinstance(history, list) and len(history) >= 2:
            logger.info("Re-adding from recently played history")
            # The API supports /api/<guild_id>/history/replay/<index>
            # Pick a random-ish track from history (not the most recent)
            try:
                index = min(len(history) - 2, 3)  # 4th most recent typically
                await self.api._post(f"/api/{self.guild_id}/history/replay/{index}")
            except Exception as e:
                logger.debug("History replay failed: %s", e)

    async def force_queue_play(self, query: str) -> dict:
        """
        Force-queue a specific song or playlist URL.
        Called by the Discord watcher or playlist finder.
        """
        logger.info("Force-queuing: %s", query[:80])
        result = await self.api.play(self.guild_id, query)
        if result.get("error"):
            await self.alerts.error(f"Failed to queue: {query[:50]}")
        return result
