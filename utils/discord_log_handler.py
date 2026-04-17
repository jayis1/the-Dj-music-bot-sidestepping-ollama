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
    def __init__(self, bot_instance, log_channel_id, level=logging.INFO):
        super().__init__(level)
        self.bot = bot_instance
        self.log_channel_id = log_channel_id
        self.queue = asyncio.Queue()
        self.task = None
        self.buffer = []
        self.buffer_lock = asyncio.Lock()
        self.flush_interval = 5  # seconds

    def emit(self, record):
        formatted = self.format(record)
        self.buffer.append(formatted)
        # Push to the web dashboard ring buffer (thread-safe)
        log_buffer.append(
            {
                "timestamp": time.strftime("%H:%M:%S"),
                "level": record.levelname,
                "message": formatted,
                "created": record.created,
            }
        )
        # Guard: during shutdown, bot.loop becomes _MissingSentinel and
        # create_task will crash. Only schedule a flush if the loop is alive.
        try:
            loop = self.bot.loop
            if loop is None or loop.is_closed():
                return
            if self.task is None or self.task.done():
                self.task = loop.create_task(self.flush_buffer())
        except (AttributeError, RuntimeError):
            # Loop is gone or shutting down — silently skip Discord dispatch
            pass

    async def flush_buffer(self):
        await asyncio.sleep(self.flush_interval)
        async with self.buffer_lock:
            if not self.buffer:
                return

            messages_to_send = self.buffer[:]
            self.buffer.clear()

            if not self.bot.is_ready():
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
