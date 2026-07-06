"""Crash notification utilities for the DJ Music Bot.

Sends a Discord webhook message when the bot crashes or OBS dies.
This provides out-of-band alerting that does not depend on the bot's
Discord connection being healthy (webhooks work even if the bot process
is about to exit).

Configuration:
    config.CRASH_NOTIFY_WEBHOOK_URL — Discord webhook URL. If empty,
    crash notifications are silently disabled.
"""

import asyncio
import logging
import sys
import traceback
import traceback as _tb

log = logging.getLogger(__name__)

# Module-level state so the exception handlers can schedule webhook sends
# even when they're invoked from non-async contexts (sys.excepthook).
_bot = None
_webhook_url = ""
_already_notified = False  # Guard against notification storms on cascading crashes


async def send_crash_notification(webhook_url, title, description):
    """Send an embed-style crash notification to a Discord webhook.

    Args:
        webhook_url: The Discord webhook URL to POST to.
        title: Embed title (e.g. "Bot Crash" or "OBS Disconnected").
        description: Embed description — typically the truncated traceback.

    The description is truncated to 1900 characters to stay within Discord's
    4096-char description limit with a comfortable safety margin for the
    surrounding JSON envelope.
    """
    if not webhook_url:
        return

    # Truncate to stay safely within Discord embed limits
    if len(description) > 1900:
        description = description[:1897] + "..."

    payload = {
        "embeds": [
            {
                "title": title[:256],  # Discord embed title max
                "description": description,
                "color": 0xFF0000,  # Red
                "footer": {"text": "Crash Notifier"},
            }
        ]
    }

    try:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.post(
                webhook_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status not in (200, 204):
                    log.warning(
                        f"Crash notifier: webhook returned status {resp.status}"
                    )
    except Exception as e:
        # Never raise from the notifier itself — we don't want to mask
        # the original crash with a notification failure.
        log.debug(f"Crash notifier: failed to send webhook: {e}")


def _schedule_crash_notification(title, description):
    """Best-effort fire-and-forget crash notification.

    Works from both sync and async contexts. If there's a running event loop,
    schedules the coroutine on it. Otherwise creates a new loop just to send
    the notification (used when the loop has already died).
    """
    global _already_notified
    if _already_notified:
        return
    if not _webhook_url:
        return
    _already_notified = True

    try:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                send_crash_notification(_webhook_url, title, description)
            )
        except RuntimeError:
            # No running loop — create one just for the notification.
            asyncio.run(
                send_crash_notification(_webhook_url, title, description)
            )
    except Exception as e:
        log.debug(f"Crash notifier: could not schedule notification: {e}")


def _sys_excepthook(exc_type, exc_value, exc_tb):
    """sys.excepthook replacement — fires on unhandled synchronous exceptions."""
    global _already_notified
    _already_notified = False  # Reset in case multiple distinct crashes happen
    tb_text = "".join(_tb.format_exception(exc_type, exc_value, exc_tb))
    _schedule_crash_notification(
        "🔴 Bot Crash (unhandled exception)",
        f"```\n{tb_text}\n```",
    )
    # Call the original excepthook so the traceback still prints to stderr
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def _async_exception_handler(loop, context):
    """asyncio loop exception handler — fires on unhandled async exceptions."""
    global _already_notified
    _already_notified = False
    exception = context.get("exception")
    message = context.get("message", "Unknown async error")

    if exception is not None:
        tb_text = "".join(
            _tb.format_exception(type(exception), exception, exception.__traceback__)
        )
        desc = f"```\n{tb_text}\n```"
    else:
        desc = f"```\n{message}\n```"

    _schedule_crash_notification("🔴 Bot Crash (async exception)", desc)
    # Still log it via the default handler behavior
    loop.default_exception_handler(context)


def setup_crash_handlers(bot, webhook_url):
    """Register crash handlers on the bot's event loop and global sys.excepthook.

    Call this from bot.py on_ready after the DiscordLogHandler setup.

    Args:
        bot: The discord.ext.commands.Bot instance.
        webhook_url: The Discord webhook URL. If empty, this is a no-op.
    """
    global _bot, _webhook_url
    _bot = bot
    _webhook_url = webhook_url or ""

    if not _webhook_url:
        log.info("Crash notifier: disabled (CRASH_NOTIFY_WEBHOOK_URL not set)")
        return

    # Register the asyncio loop exception handler
    try:
        loop = bot.loop
        loop.set_exception_handler(_async_exception_handler)
        log.info("Crash notifier: async exception handler registered")
    except Exception as e:
        log.warning(f"Crash notifier: could not set async exception handler: {e}")

    # Register the global sys.excepthook for synchronous crashes
    sys.excepthook = _sys_excepthook
    log.info("Crash notifier: sys.excepthook registered")