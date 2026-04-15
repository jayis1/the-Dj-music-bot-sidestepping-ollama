#!/usr/bin/env python3
import asyncio
import os
import threading
import discord
from discord.ext import commands
import config
import logging
from utils.discord_log_handler import DiscordLogHandler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s:%(levelname)s:%(name)s: %(message)s",
    handlers=[
        logging.FileHandler("bot_activity.log"),
        logging.StreamHandler(),  # Keep StreamHandler for console output
    ],
)

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix=config.COMMAND_PREFIX, intents=intents)

discord_log_handler = None  # Initialize as None, will be set in on_ready


@bot.event
async def on_ready():
    global discord_log_handler
    logging.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    logging.info(f"Intents: {bot.intents}")
    logging.info("------")

    # Clean up any stale voice sessions left over from a previous crash/restart.
    # Without this, Discord rejects new connections with 4006 "Session no longer valid".
    for guild in bot.guilds:
        if guild.voice_client:
            logging.info(f"Cleaning up stale voice connection in {guild.name}")
            await guild.voice_client.disconnect(force=True)

    # Initialize and add DiscordLogHandler after bot is ready
    if config.LOG_CHANNEL_ID and config.LOG_CHANNEL_ID != "YOUR_LOG_CHANNEL_ID":
        discord_log_handler = DiscordLogHandler(bot, config.LOG_CHANNEL_ID)
        logging.getLogger().addHandler(discord_log_handler)  # Add to root logger
        logging.info(
            f"Discord log handler added for channel ID: {config.LOG_CHANNEL_ID}"
        )
    else:
        logging.warning(
            "LOG_CHANNEL_ID is not set in config.py. Discord logging will be disabled."
        )

    # Auto-create the custom Ollama model for the AI Side Host.
    # This bakes the DJ personality into a custom model (e.g. "mbot-sidehost")
    # so the system prompt doesn't need to be sent on every API call.
    if getattr(config, "OLLAMA_DJ_ENABLED", False):
        try:
            from utils.llm_dj import ensure_custom_model

            # Use the bot's Discord display name as the station identity.
            # Falls back to STATION_NAME config if bot user isn't available yet.
            bot_name = bot.user.name if bot.user else None
            await ensure_custom_model(station_name=bot_name)
        except Exception as e:
            logging.debug(f"AI Side Host: Custom model check skipped ({e})")


async def main():
    # The yt_dlp_cache directory is no longer strictly necessary for streaming,
    # but can be kept if yt-dlp still uses it for other metadata caching.
    # For now, we'll keep it as it doesn't harm anything.
    cache_dir = "yt_dlp_cache"
    if not os.path.exists(cache_dir):
        os.makedirs(cache_dir)
        logging.info(f"Created yt-dlp cache directory: {cache_dir}")
    else:
        logging.info(f"yt-dlp cache directory already exists: {cache_dir}")

    # Create sounds directory and default README
    sounds_dir = "sounds"
    if not os.path.exists(sounds_dir):
        os.makedirs(sounds_dir)
        logging.info(f"Created sounds directory: {sounds_dir}")
    from utils.soundboard import create_default_sounds

    create_default_sounds()

    # Create presets directory
    presets_dir = "presets"
    if not os.path.exists(presets_dir):
        os.makedirs(presets_dir)
        logging.info(f"Created presets directory: {presets_dir}")

    async with bot:
        for filename in os.listdir("./cogs"):
            if (
                filename.endswith(".py")
                and filename != "__init__.py"
                and filename != "youtube.py"
                and filename != "logging.py"
            ):
                try:
                    await bot.load_extension(f"cogs.{filename[:-3]}")
                    logging.info(f"Successfully loaded extension: {filename}")
                except Exception as e:
                    logging.error(f"Failed to load extension {filename}: {e}")

        try:
            await bot.start(config.DISCORD_TOKEN)
        except discord.errors.LoginFailure:
            logging.error(
                "Error: Invalid Discord Token. Please check your DISCORD_TOKEN in config.py."
            )
        except Exception as e:
            logging.error(f"Error when starting bot: {e}")

        try:
            await bot.start(config.DISCORD_TOKEN)
        except discord.errors.LoginFailure:
            logging.error(
                "Error: Invalid Discord Token. Please check your DISCORD_TOKEN in config.py."
            )
        except Exception as e:
            logging.error(f"Error when starting bot: {e}")


def run_web_server():
    """Start the Flask web dashboard in a separate thread."""
    try:
        from web.app import app, init_dashboard

        init_dashboard(bot)
        log = logging.getLogger("werkzeug")
        log.setLevel(logging.WARNING)
        logging.info(
            f"Web dashboard starting on http://{config.WEB_HOST}:{config.WEB_PORT}"
        )
        app.run(
            host=config.WEB_HOST, port=config.WEB_PORT, debug=False, use_reloader=False
        )
    except ImportError as e:
        logging.warning(
            f"Flask not installed — web dashboard unavailable. Install with: pip install flask ({e})"
        )
    except Exception as e:
        logging.error(f"Web dashboard failed to start: {e}")


if __name__ == "__main__":
    # Start web dashboard in a background thread
    web_thread = threading.Thread(target=run_web_server, daemon=True)
    web_thread.start()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped.")
