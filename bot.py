#!/usr/bin/env python3
import asyncio
import os
import sys
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

    # ── Startup Config Summary ───────────────────────────────────────────
    # Show all critical config values at startup so you can spot problems
    # (missing API keys, no cookies, wrong URLs) at a glance.
    api_key = getattr(config, "YOUTUBE_API_KEY", "")
    cookie_browser = getattr(config, "YTDDL_COOKIES_FROM_BROWSER", "").strip()
    cookie_file = getattr(config, "YTDDL_COOKIEFILE", "youtube_cookie.txt").strip()
    cookie_file_exists = os.path.exists(cookie_file) if cookie_file else False
    tts_mode = getattr(config, "TTS_MODE", "edge-tts").lower()
    moss_url = getattr(config, "MOSS_TTS_URL", "")
    ollama_enabled = getattr(config, "OLLAMA_DJ_ENABLED", False)
    ollama_host = getattr(config, "OLLAMA_HOST", "")
    stream_enabled = getattr(config, "YOUTUBE_STREAM_ENABLED", False)

    logging.info("─── Startup Config ───────────────────────────────────────")

    # YouTube Data API v3 key (used for search, not for playback)
    if api_key and api_key not in ("your_youtube_api_key", ""):
        logging.info(f"  YouTube Data API key: ✅ Set ({len(api_key)} chars)")
    else:
        logging.warning(
            "  YouTube Data API key: ⚠️ NOT SET — YouTube search (?search, "
            "?play <keywords>) will not work. Set YOUTUBE_API_KEY in .env. "
            "(Direct URL playback still works without it.)"
        )

    # yt-dlp cookie auth (prevents "Sign in to confirm you're not a bot")
    try:
        import yt_dlp

        ytdlp_version = yt_dlp.version.__version__
    except Exception:
        ytdlp_version = "unknown"

    if cookie_browser:
        logging.info(f"  yt-dlp cookies: 🌐 Browser → {cookie_browser}")
    elif cookie_file_exists:
        logging.info(f"  yt-dlp cookies: 📄 File → {cookie_file}")
    else:
        logging.warning(
            f"  yt-dlp cookies: ⚠️ NOT CONFIGURED — {cookie_file} not found and "
            "no browser set. YouTube may block playback. Set YTDDL_COOKIES_FROM_BROWSER "
            "or export cookies from Mission Control → Settings → Cookie Auth."
        )
    logging.info(f"  yt-dlp version: {ytdlp_version}")

    # Check if yt-dlp is very old (YouTube breaks it frequently)
    try:
        import datetime

        version_date = ytdlp_version.replace(".", "-", 2)
        version_dt = datetime.datetime.strptime(version_date, "%Y-%m-%d")
        days_old = (datetime.datetime.now() - version_dt).days
        if days_old > 30:
            logging.error(
                f"  yt-dlp age: ❌ {days_old} DAYS OLD — yt-dlp almost certainly CANNOT "
                "play YouTube right now. YouTube changes their cipher frequently and "
                "you will see 'Requested format is not available' or 'Sign in to confirm' "
                "errors. UPGRADE NOW: pip install -U yt-dlp"
            )
        elif days_old > 14:
            logging.warning(
                f"  yt-dlp age: ⚠️ {days_old} days old — may fail on YouTube. "
                "Upgrade with: pip install -U yt-dlp"
            )
        else:
            logging.info(f"  yt-dlp age: ✅ {days_old} days old")
    except Exception:
        pass

    # ── yt-dlp Cipher Health Check ──────────────────────────────────────
    # Before starting the bot, verify yt-dlp can actually extract YouTube.
    # If the cipher is broken (outdated yt-dlp), auto-upgrade from PyPI.
    # Tries: 1) stable release → 2) nightly/pre-release → 3) git master
    logging.info("  yt-dlp: Testing YouTube extraction...")
    _ytdlp_healthy = False
    try:
        import yt_dlp

        _test_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
            "noplaylist": True,
        }
        # Add cookies if available
        if cookie_browser:
            parts = cookie_browser.split(":", 1)
            _test_opts["cookiesfrombrowser"] = (
                (parts[0].strip().lower(),)
                if len(parts) == 1
                else (parts[0].strip().lower(), parts[1].strip())
            )
        elif cookie_file and cookie_file_exists:
            _test_opts["cookiefile"] = cookie_file
        # Quick test: extract metadata only (flat) from a well-known video
        _test_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        with yt_dlp.YoutubeDL(_test_opts) as _ydl:
            _info = _ydl.extract_info(_test_url, download=False)
        if _info and _info.get("id"):
            _ytdlp_healthy = True
            logging.info("  yt-dlp: ✅ YouTube extraction works")
        else:
            logging.warning("  yt-dlp: ⚠️ Test extraction returned no data")
    except Exception as _e:
        _err = str(_e).lower()
        if (
            "sign in to confirm" in _err
            or "format is not available" in _err
            or "signature" in _err
        ):
            logging.error(f"  yt-dlp: ❌ BROKEN — cannot extract YouTube")
            logging.error(f"  yt-dlp: Error: {_e}")

            import subprocess

            _upgraded = False

            # Stage 1: Try stable PyPI upgrade
            logging.info(
                "  yt-dlp: Auto-upgrade attempt 1/3 — latest stable release..."
            )
            try:
                _r = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "pip",
                        "install",
                        "--upgrade",
                        "--break-system-packages",
                        "--quiet",
                        "yt-dlp",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if _r.returncode == 0:
                    logging.info("  yt-dlp: Stable upgrade installed, reloading...")
                    import importlib

                    importlib.reload(yt_dlp)
                    logging.info(f"  yt-dlp: Now at {yt_dlp.version.__version__}")
                    try:
                        with yt_dlp.YoutubeDL(_test_opts) as _ydl:
                            _t = _ydl.extract_info(_test_url, download=False)
                        if _t and _t.get("id"):
                            _ytdlp_healthy = True
                            _upgraded = True
                            logging.info(
                                "  yt-dlp: ✅ FIXED! YouTube extraction works after stable upgrade"
                            )
                    except Exception:
                        logging.warning("  yt-dlp: Stable upgrade didn't fix it")
                else:
                    logging.warning(
                        f"  yt-dlp: Stable upgrade failed: {_r.stderr[:200]}"
                    )
            except Exception as _ue:
                logging.warning(f"  yt-dlp: Stable upgrade error: {_ue}")

            # Stage 2: Try nightly/pre-release (has latest cipher fixes)
            if not _upgraded:
                logging.info(
                    "  yt-dlp: Auto-upgrade attempt 2/3 — nightly pre-release..."
                )
                try:
                    _r = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "pip",
                            "install",
                            "--upgrade",
                            "--break-system-packages",
                            "--quiet",
                            "--pre",
                            "yt-dlp",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=120,
                    )
                    if _r.returncode == 0:
                        logging.info("  yt-dlp: Nightly installed, reloading...")
                        import importlib

                        importlib.reload(yt_dlp)
                        logging.info(f"  yt-dlp: Now at {yt_dlp.version.__version__}")
                        try:
                            with yt_dlp.YoutubeDL(_test_opts) as _ydl:
                                _t = _ydl.extract_info(_test_url, download=False)
                            if _t and _t.get("id"):
                                _ytdlp_healthy = True
                                _upgraded = True
                                logging.info(
                                    "  yt-dlp: ✅ FIXED! YouTube extraction works after nightly upgrade"
                                )
                        except Exception:
                            logging.warning("  yt-dlp: Nightly upgrade didn't fix it")
                    else:
                        logging.warning(
                            f"  yt-dlp: Nightly upgrade failed: {_r.stderr[:200]}"
                        )
                except Exception as _ue:
                    logging.warning(f"  yt-dlp: Nightly upgrade error: {_ue}")

            # Stage 3: Try directly from git master
            if not _upgraded:
                logging.info(
                    "  yt-dlp: Auto-upgrade attempt 3/3 — git master branch..."
                )
                try:
                    _r = subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "pip",
                            "install",
                            "--upgrade",
                            "--break-system-packages",
                            "--quiet",
                            "yt-dlp @ git+https://github.com/yt-dlp/yt-dlp.git@master",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=180,
                    )
                    if _r.returncode == 0:
                        logging.info("  yt-dlp: Git master installed, reloading...")
                        import importlib

                        importlib.reload(yt_dlp)
                        logging.info(f"  yt-dlp: Now at {yt_dlp.version.__version__}")
                        try:
                            with yt_dlp.YoutubeDL(_test_opts) as _ydl:
                                _t = _ydl.extract_info(_test_url, download=False)
                            if _t and _t.get("id"):
                                _ytdlp_healthy = True
                                _upgraded = True
                                logging.info(
                                    "  yt-dlp: ✅ FIXED! YouTube extraction works after git master upgrade"
                                )
                        except Exception:
                            logging.warning("  yt-dlp: Git master didn't fix it either")
                    else:
                        logging.warning(
                            f"  yt-dlp: Git master upgrade failed: {_r.stderr[:200]}"
                        )
                except Exception as _ue:
                    logging.warning(f"  yt-dlp: Git master upgrade error: {_ue}")

            if not _upgraded:
                logging.error("  yt-dlp: ❌ All 3 auto-upgrade attempts failed.")
                logging.error(
                    "  yt-dlp: YouTube may have just changed their cipher and "
                    "yt-dlp hasn't released a fix yet. Check:"
                )
                logging.error("    https://github.com/yt-dlp/yt-dlp/issues")
        else:
            logging.warning(f"  yt-dlp: ⚠️ Test extraction error: {_e}")

    if not _ytdlp_healthy:
        logging.error("  ──────────────────────────────────────────────────────────")
        logging.error("  ⚠️  yt-dlp CANNOT extract YouTube! The bot will start but")
        logging.error("      ALL YouTube playback will fail until yt-dlp is fixed.")
        logging.error("      Try manually: pip install --pre -U yt-dlp")
        logging.error("  ──────────────────────────────────────────────────────────")

    # TTS engine
    if tts_mode == "moss":
        logging.info(f"  TTS Engine: 🖥️ MOSS-TTS-Nano → {moss_url}")
    elif tts_mode == "vibevoice":
        logging.info(
            f"  TTS Engine: ⚡ VibeVoice → {getattr(config, 'VIBEVOICE_TTS_URL', '')}"
        )
    else:
        logging.info("  TTS Engine: ☁️ Edge TTS (cloud)")
    logging.info(f"  DJ default voice: {getattr(config, 'DJ_VOICE', 'N/A')}")

    # AI Side Host
    if ollama_enabled:
        logging.info(f"  AI Side Host: ✅ Enabled → {ollama_host}")
        logging.info(
            f"  AI model: {getattr(config, 'OLLAMA_CUSTOM_MODEL', 'N/A')} (base: {getattr(config, 'OLLAMA_MODEL', 'N/A')})"
        )
    else:
        logging.info("  AI Side Host: ⚪ Disabled")

    # YouTube Live Streaming
    if stream_enabled:
        stream_url = getattr(config, "YOUTUBE_STREAM_URL", "")
        logging.info(f"  YouTube Live: ✅ Enabled → {stream_url}")
    else:
        logging.info("  YouTube Live: ⚪ Disabled")

    # Web dashboard
    logging.info(f"  Web Dashboard: http://{config.WEB_HOST}:{config.WEB_PORT}")
    if getattr(config, "WEB_PASSWORD", ""):
        logging.info("  Web Dashboard auth: 🔒 Password set")
    else:
        logging.warning(
            "  Web Dashboard auth: ⚠️ No password — dashboard is open to everyone"
        )

    logging.info("─────────────────────────────────────────────────────────")

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
