"""
Discord Watcher — Loop 5: listen for fan playlist requests in Discord.

Uses a lightweight discord.py bot to watch a specific channel
for YouTube links posted by fans. When a link is detected:
  1. Validate it's a YouTube video or playlist URL
  2. Queue it via the Mission Control API
  3. Alert the station that a fan request came in
"""

import asyncio
import logging
import re
from typing import Optional

import discord

logger = logging.getLogger("shadow.discord_watcher")

# YouTube URL patterns
YOUTUBE_PATTERNS = [
    # Standard watch URLs
    re.compile(
        r"(https?://(?:www\.)?youtube\.com/watch\?[^\s]*v=[\w-]+[^\s]*)", re.IGNORECASE
    ),
    # Short URLs
    re.compile(r"(https?://youtu\.be/[\w-]+[^\s]*)", re.IGNORECASE),
    # Playlist URLs
    re.compile(
        r"(https?://(?:www\.)?youtube\.com/playlist\?list=[\w-]+[^\s]*)", re.IGNORECASE
    ),
    # Music URLs
    re.compile(
        r"(https?://music\.youtube\.com/watch\?[^\s]*v=[\w-]+[^\s]*)", re.IGNORECASE
    ),
    # Shorts
    re.compile(r"(https?://(?:www\.)?youtube\.com/shorts/[\w-]+[^\s]*)", re.IGNORECASE),
    # Live
    re.compile(r"(https?://(?:www\.)?youtube\.com/live/[\w-]+[^\s]*)", re.IGNORECASE),
]


def extract_youtube_urls(text: str) -> list:
    """Extract all YouTube URLs from a text string."""
    urls = []
    for pattern in YOUTUBE_PATTERNS:
        for match in pattern.finditer(text):
            url = match.group(1).strip()
            # Clean trailing punctuation that's not part of URL
            url = url.rstrip(".,;:)>!")
            if url not in urls:
                urls.append(url)
    return urls


class DiscordWatcher:
    """
    Watches a Discord channel for fan-posted YouTube links
    and queues them in the DJ bot.

    Needs a separate Discord bot token with:
      - Message Content Intent (to read message text)
      - Read Messages permission in the target channel
    """

    def __init__(self, config: dict, api_client, alert_system, queue_watchdog=None):
        """
        Args:
            config: Parsed config.yaml with:
                - discord_watcher_token (str)
                - fan_request_channel_id (str/int)
                - guild_id (str/int)
                - fan_request_enabled (bool, default True)
        """
        self.api = api_client
        self.alerts = alert_system
        self.queue_watchdog = queue_watchdog

        self.token = config.get("discord_watcher_token", "")
        self.channel_id = int(config.get("fan_request_channel_id", "0"))
        self.guild_id = str(config.get("guild_id", ""))
        self.enabled = config.get("fan_request_enabled", True)

        self._bot: Optional[discord.Client] = None
        self._running = False
        self._recent_urls: set = set()  # Dedup within session
        self._max_recent = 100

    async def start(self):
        """Start the Discord watcher bot."""
        if not self.enabled:
            logger.info("Discord watcher disabled in config")
            return

        if not self.token:
            logger.warning(
                "No Discord watcher token configured — fan requests disabled"
            )
            return

        if not self.channel_id:
            logger.warning("No fan request channel ID configured")
            return

        self._running = True

        # Set up intents (need message content to read URLs)
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.guild_messages = True

        self._bot = discord.Client(intents=intents)

        @self._bot.event
        async def on_ready():
            logger.info("Discord watcher connected as %s", self._bot.user)
            channel = self._bot.get_channel(self.channel_id)
            if channel:
                logger.info("Watching channel: #%s", channel.name)
            else:
                logger.warning(
                    "Channel ID %s not found or not accessible", self.channel_id
                )

        @self._bot.event
        async def on_message(message: discord.Message):
            await self._handle_message(message)

        # Start the bot (this blocks, so run in background)
        try:
            await self._bot.start(self.token)
        except discord.LoginFailure:
            logger.error("Discord watcher login failed — check token")
            await self.alerts.error(
                "Discord watcher login failed — check token", key="discord_login_fail"
            )
        except Exception as e:
            logger.error("Discord watcher error: %s", e)
            await self.alerts.error(
                f"Discord watcher crashed: {e}", key="discord_crash"
            )

    def stop(self):
        """Stop the Discord watcher bot."""
        self._running = False
        if self._bot and not self._bot.is_closed():
            asyncio.create_task(self._bot.close())
        logger.info("Discord watcher stopped")

    async def _handle_message(self, message: discord.Message):
        """Process a Discord message for YouTube links."""
        # Ignore bot messages (including the DJ bot itself)
        if message.author.bot:
            return

        # Only watch the configured channel
        if message.channel.id != self.channel_id:
            return

        # Extract YouTube URLs
        urls = extract_youtube_urls(message.content)

        if not urls:
            return

        logger.info(
            "Fan request from @%s: %d URL(s) — %s",
            message.author.name,
            len(urls),
            urls[0][:60],
        )

        for url in urls:
            # Dedup: don't queue the same URL twice
            if url in self._recent_urls:
                logger.debug("Skipping duplicate URL: %s", url[:60])
                continue

            self._recent_urls.add(url)
            # Keep the dedup set manageable
            if len(self._recent_urls) > self._max_recent:
                # Remove oldest entries (set is unordered, so just clear half)
                half = len(self._recent_urls) // 2
                self._recent_urls = set(list(self._recent_urls)[half:])

            # Queue via Mission Control API
            try:
                result = await self.api.play(self.guild_id, url)

                if result.get("error"):
                    logger.warning(
                        "Failed to queue fan request %s: %s",
                        url[:50],
                        result.get("error"),
                    )
                    await self.alerts.error(
                        f"Fan request failed: {url[:50]} — {str(result.get('error', ''))[:50]}",
                        key=f"fan_request_fail:{url[:20]}",
                    )
                else:
                    logger.info(
                        "Fan request queued: %s from @%s", url[:60], message.author.name
                    )
                    await self.alerts.fan_request(url, message.author.name)

                    # React to the message to acknowledge
                    try:
                        await message.add_reaction("🎵")
                    except discord.HTTPException:
                        pass  # Bot doesn't have permission to react, fine

            except Exception as e:
                logger.error("Error queuing fan request: %s", e)

    async def scan_channel_history(self, limit: int = 50):
        """
        Scan recent messages in the fan request channel for
        YouTube links that were posted while the watcher was down.

        Call this once at startup to catch up.
        """
        if not self._bot or self._bot.is_closed():
            return

        channel = self._bot.get_channel(self.channel_id)
        if not channel:
            logger.warning(
                "Cannot scan history — channel %d not found", self.channel_id
            )
            return

        logger.info(
            "Scanning last %d messages in #%s for missed fan requests...",
            limit,
            channel.name,
        )

        count = 0
        async for message in channel.history(limit=limit):
            if message.author.bot:
                continue
            urls = extract_youtube_urls(message.content)
            for url in urls:
                if url in self._recent_urls:
                    continue
                # Don't auto-queue old requests, just add to dedup set
                self._recent_urls.add(url)
                count += 1

        logger.info(
            "Scan complete: %d unique YouTube URLs found (added to dedup, not auto-queued)",
            count,
        )
