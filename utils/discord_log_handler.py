import logging
import asyncio
import discord
from collections import deque
import time

# Thread-safe ring buffer for the web dashboard log panel.
# Flask reads from this; DiscordLogHandler writes to it.
# maxlen=200 keeps memory bounded (~50KB max).
log_buffer = deque(maxlen=200)


class DiscordLogHandler(logging.Handler):
    """Sends log records to a Discord channel in batched chunks.

    Thread-safety:
        ``emit()`` is called from arbitrary threads (logging is thread-safe
        but runs in the caller's thread).  We must NOT call
        ``loop.create_task()`` from a non-asyncio thread — that's undefined
        behaviour.  Instead we use ``asyncio.run_coroutine_threadsafe()``
        which is the *correct* way to submit work to an event loop from
        outside it.

        ``self.bot.loop`` was deprecated in discord.py 2.0 and removed in
        recent versions.  We grab the loop via ``asyncio.get_event_loop()``
        with a fallback path for older discord.py that still exposes
        ``bot.loop``.
    """

    def __init__(self, bot_instance, log_channel_id, level=logging.INFO):
        super().__init__(level)
        self.bot = bot_instance
        self.log_channel_id = log_channel_id
        self.flush_interval = 5  # seconds
        # Simple list buffer — we don't need an asyncio.Queue here because
        # scheduling a flush via run_coroutine_threadsafe is the mechanism
        # that drives the coroutine, not a consumer task.
        self.buffer = []
        self._flush_scheduled = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Logging Handler API (called from arbitrary threads)
    # ------------------------------------------------------------------

    def emit(self, record):
        formatted = self.format(record)
        self.buffer.append(formatted)

        # Push to the web dashboard ring buffer (thread-safe deque)
        log_buffer.append(
            {
                "timestamp": time.strftime("%H:%M:%S"),
                "level": record.levelname,
                "message": formatted,
                "created": record.created,
            }
        )

        # Schedule a flush of the buffer to Discord.
        # We use run_coroutine_threadsafe() because this method is called
        # from a non-asyncio thread (the logging thread).  Using
        # loop.create_task() here is NOT thread-safe and will crash on
        # modern discord.py where bot.loop doesn't exist.
        try:
            loop = self._get_event_loop()
            if loop is None or loop.is_closed():
                return
            # Only schedule one flush at a time to avoid piling up coroutines
            if not self._flush_scheduled:
                self._flush_scheduled = True
                asyncio.run_coroutine_threadsafe(self._flush_to_discord(), loop)
        except RuntimeError:
            # Event loop is shutting down — silently skip Discord dispatch
            pass

    # ------------------------------------------------------------------
    # Async flush (runs on the bot's event loop)
    # ------------------------------------------------------------------

    async def _flush_to_discord(self):
        """Send buffered log lines to the configured Discord channel."""
        self._flush_scheduled = False

        await asyncio.sleep(self.flush_interval)

        async with self._lock:
            if not self.buffer:
                return

            messages_to_send = self.buffer[:]
            self.buffer.clear()

            if not self.bot.is_ready():
                # Bot not ready yet — put the messages back and retry later
                self.buffer.extend(messages_to_send)
                return

            channel = self.bot.get_channel(self.log_channel_id)
            if channel:
                try:
                    full_message = "\n".join(messages_to_send)
                    for chunk in [
                        full_message[i : i + 1900]
                        for i in range(0, len(full_message), 1900)
                    ]:
                        await channel.send(f"```\n{chunk}\n```")
                except discord.HTTPException as e:
                    print(f"Failed to send log to Discord (HTTPException): {e}")
                    logging.error(f"Failed to send log to Discord (HTTPException): {e}")
                except Exception as e:
                    print(f"Failed to send log to Discord (General Error): {e}")
                    logging.error(f"Failed to send log to Discord (General Error): {e}")
            else:
                print(f"Discord log channel with ID {self.log_channel_id} not found.")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_event_loop(self):
        """Return the bot's event loop in a way compatible with modern and
        legacy discord.py versions.

        discord.py >= 2.0 deprecated ``bot.loop`` — it may not exist.
        ``asyncio.get_event_loop()`` works on Python 3.10+ when called from
        a thread that has an associated loop, but the bot's loop may be in
        another thread.  We try multiple approaches and fall back gracefully.
        """
        # 1. Try bot.loop first — works on older discord.py (pre-2.0)
        loop = getattr(self.bot, "loop", None)
        if loop is not None and hasattr(loop, "is_closed") and not loop.is_closed():
            return loop

        # 2. Try to get the running event loop (works on Python 3.10+
        #    when called from the thread that owns the loop)
        try:
            loop = asyncio.get_event_loop()
            # On Python 3.10+, get_event_loop() can return an internal
            # _MissingSentinel object when no loop is set. Check for
            # the is_closed attribute to handle this gracefully.
            if loop is not None and hasattr(loop, "is_closed") and not loop.is_closed():
                return loop
        except RuntimeError:
            pass

        # 3. Try asyncio.get_running_loop() — only works if we're
        #    already inside an async context on the same thread
        try:
            loop = asyncio.get_running_loop()
            if not loop.is_closed():
                return loop
        except RuntimeError:
            pass

        # No suitable loop found — can't schedule work
        return None
