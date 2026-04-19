#!/usr/bin/env python3
import asyncio
import os
import re
import sys
import threading
import discord
from discord.ext import commands
import config
import logging
from utils.discord_log_handler import DiscordLogHandler

# Version constant (single source of truth — also in config.py)
BOT_VERSION = config.BOT_VERSION

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
    logging.info(f"🏴‍☠️ The Radio Pirate DJ Bot {BOT_VERSION}")
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
    ensure_default_assets()

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
        # Quick test: search for a generic term to verify API connectivity
        _test_url = "ytsearch1:test"
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
    if tts_mode == "kokoro":
        kokoro_url = getattr(config, "KOKORO_TTS_URL", "")
        logging.info(f"  TTS Engine: 🎙️ Kokoro-FastAPI → {kokoro_url}")
    elif tts_mode == "moss":
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
        stream_playlist = getattr(config, "YOUTUBE_STREAM_PLAYLIST", "")
        logging.info(f"  YouTube Live: ✅ Enabled → {stream_url}")
        if stream_playlist:
            logging.info(f"  YouTube Live playlist: 📜 {stream_playlist[:60]}")
        logging.info(
            "  YouTube Live modes: 🪞 Mirror (Discord) · 🎙️ Curated (Shadow DJ)"
        )
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
            if not config.DISCORD_TOKEN or config.DISCORD_TOKEN == "your_discord_bot_token":
                raise discord.errors.LoginFailure("Invalid or default Discord token")
            await bot.start(config.DISCORD_TOKEN)
        except discord.errors.LoginFailure:
            logging.warning("⚠️ Invalid or missing Discord Token.")
            logging.warning("📻 Running in Pure Headless Radio Mode (No Discord connection).")
            # Keep event loop alive so the Web Dashboard and Headless Radio can function
            while True:
                await asyncio.sleep(3600)
        except Exception as e:
            logging.error(f"Error when starting bot: {e}")


def ensure_default_assets():
    import shutil

    for asset_type in ["sounds", "presets"]:
        default_dir = f"default_{asset_type}"
        target_dir = asset_type
        if os.path.isdir(default_dir):
            os.makedirs(target_dir, exist_ok=True)
            for item in os.listdir(default_dir):
                src = os.path.join(default_dir, item)
                dst = os.path.join(target_dir, item)
                if not os.path.exists(dst):
                    try:
                        if os.path.isfile(src):
                            shutil.copy2(src, dst)
                        elif os.path.isdir(src):
                            shutil.copytree(src, dst)
                    except Exception as e:
                        logging.warning(f"Failed to copy default asset {item}: {e}")


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

        # Initialize OBS Bridge if configured
        try:
            from utils.obs_bridge import init_bridge
            init_bridge(
                host=config.OBS_WS_HOST,
                port=config.OBS_WS_PORT,
                password=config.OBS_WS_PASSWORD,
                enabled=config.OBS_WS_ENABLED,
            )
            logging.info("OBS Bridge initialized")

            # Fix OBS's user.ini to ensure it loads "Radio DJ" scene collection.
            # OBS stores the active collection in [Basic] → SceneCollection.
            # If it says "Untitled" (OBS's default), OBS loads a blank scene
            # even when --collection "Radio DJ" is on the command line.
            #
            # CRITICAL: OBS overwrites user.ini on exit with whatever scene
            # collection it had loaded at shutdown. If OBS was running with
            # "Untitled" (the default), it writes SceneCollection=Untitled
            # back to user.ini. This creates a chicken-and-egg problem:
            # our fix gets overwritten every time OBS exits.
            #
            # Strategy: Fix user.ini in BOTH apt and Flatpak dirs + delete
            # Untitled.json so OBS has NOTHING to fall back to. When OBS
            # can't find the Untitled collection JSON, it creates a blank
            # scene — but our ensure_scenes_exist() programmatically creates
            # the correct scene via WebSocket anyway.
            #
            # CRITICAL: We do NOT use configparser here because it lowercases
            # all option names by default (SceneCollection → scenecollection),
            # which OBS doesn't recognize. We do direct string replacement
            # instead, preserving OBS's mixed-case option names.

            # Detect Flatpak OBS to know the Flatpak config dir
            _obs_use_flatpak = getattr(config, "OBS_USE_FLATPAK", "auto").lower()
            if _obs_use_flatpak == "auto":
                import shutil as _shutil
                if _shutil.which("flatpak"):
                    try:
                        _fp_result = __import__("subprocess").run(
                            ["flatpak", "list"], capture_output=True, text=True, timeout=5
                        )
                        if "com.obsproject.Studio" in _fp_result.stdout:
                            _obs_use_flatpak = "true"
                    except Exception:
                        pass
            _flatpak_obs_dir = os.path.expanduser(
                "~/.var/app/com.obsproject.Studio/config/obs-studio"
            ) if _obs_use_flatpak == "true" else None

            def _fix_obs_user_ini(obs_base_dir):
                """Fix or create user.ini for an OBS config directory."""
                user_ini_path = os.path.join(obs_base_dir, "user.ini")
                try:
                    os.makedirs(os.path.dirname(user_ini_path), exist_ok=True)

                    content = ""
                    if os.path.isfile(user_ini_path):
                        with open(user_ini_path, "r") as f:
                            content = f.read()

                    original = content

                    if "[General]" not in content:
                        content = "[General]\n" + content

                    if "[Basic]" not in content:
                        content = content.rstrip("\n") + "\n\n[Basic]\n"

                    # Use string replacement to preserve OBS's mixed-case keys
                    import re

                    if "SceneCollection=" in content:
                        content = re.sub(r'SceneCollection=.*', 'SceneCollection=Radio DJ', content)
                    else:
                        content = content.rstrip("\n") + "\nSceneCollection=Radio DJ\n"

                    if "SceneCollectionFile=" in content:
                        content = re.sub(r'SceneCollectionFile=.*', 'SceneCollectionFile=Radio DJ.json', content)
                    else:
                        content = content.rstrip("\n") + "\nSceneCollectionFile=Radio DJ.json\n"

                    if "Profile=" in content:
                        val = re.search(r'Profile=(.*)', content)
                        if val and val.group(1).strip() in ("", "Untitled", "Unnamed"):
                            content = re.sub(r'Profile=.*', 'Profile=RadioDJ', content)
                    else:
                        content = content.rstrip("\n") + "\nProfile=RadioDJ\n"

                    if "ProfileDir=" in content:
                        val = re.search(r'ProfileDir=(.*)', content)
                        if val and val.group(1).strip() in ("", "Untitled", "Unnamed"):
                            content = re.sub(r'ProfileDir=.*', 'ProfileDir=RadioDJ', content)
                    else:
                        content = content.rstrip("\n") + "\nProfileDir=RadioDJ\n"

                    # Remove ConfigOnNewProfile — causes OBS to create blank
                    # profile every startup, ignoring our --profile flag
                    content = re.sub(r'^ConfigOnNewProfile=.*\n?', '', content, flags=re.MULTILINE)

                    if content != original:
                        with open(user_ini_path, "w") as f:
                            f.write(content)
                        logging.info(f"OBS: Fixed user.ini → {obs_base_dir}")
                except Exception as e:
                    logging.debug(f"OBS: Could not fix user.ini in {obs_base_dir}: {e}")

            # Fix user.ini for apt OBS dir
            _apt_obs_base = os.path.expanduser("~/.config/obs-studio")
            _fix_obs_user_ini(_apt_obs_base)

            # Fix user.ini for Flatpak OBS dir too
            if _flatpak_obs_dir and os.path.isdir(os.path.dirname(_flatpak_obs_dir)):
                _fix_obs_user_ini(_flatpak_obs_dir)

            # Delete any Untitled scene collection files so OBS can't
            # fall back to them. This is the most important part — even
            # if user.ini somehow gets overwritten by OBS, deleting the
            # Untitled.json prevents OBS from loading a blank scene.
            for _obs_base in [_apt_obs_base, _flatpak_obs_dir]:
                if not _obs_base:
                    continue
                _obs_scenes_dir = os.path.join(_obs_base, "basic", "scenes")
                for name in ["Untitled.json", "Untitled.json.bak", "Untitled.json.bak.1"]:
                    untitled_path = os.path.join(_obs_scenes_dir, name)
                    if os.path.exists(untitled_path):
                        try:
                            os.remove(untitled_path)
                            logging.info(f"OBS: Deleted stale scene collection: {name}")
                        except Exception:
                            pass

            # Push stream settings (RTMP server + stream key) to OBS at startup
            # so OBS is ready to stream when the user clicks Start Streaming.
            stream_key = getattr(config, "YOUTUBE_STREAM_KEY", "")
            rtmp_server = getattr(config, "YOUTUBE_STREAM_URL", "rtmp://a.rtmp.youtube.com/live2")

            # Import get_bridge ONCE at this scope so it's available for all
            # the OBS setup below — not just inside the if stream_key block.
            from utils.obs_bridge import get_bridge

            # ── Write OBS config files BEFORE OBS needs them ─────────
            # Detect OBS install method to find correct profile directory.
            # Flatpak OBS: ~/.var/app/com.obsproject.Studio/config/obs-studio/
            # apt OBS:      ~/.config/obs-studio/
            obs_use_flatpak = getattr(config, "OBS_USE_FLATPAK", "auto").lower()
            if obs_use_flatpak == "auto":
                import shutil
                if shutil.which("flatpak"):
                    try:
                        result = __import__("subprocess").run(
                            ["flatpak", "list"], capture_output=True, text=True, timeout=5
                        )
                        if "com.obsproject.Studio" in result.stdout:
                            obs_use_flatpak = "true"
                    except Exception:
                        pass
            if obs_use_flatpak == "true":
                primary_profile_dir = os.path.expanduser(
                    "~/.var/app/com.obsproject.Studio/config/obs-studio/basic/profiles/RadioDJ"
                )
            else:
                primary_profile_dir = os.path.expanduser(
                    "~/.config/obs-studio/basic/profiles/RadioDJ"
                )

            # Always write to BOTH apt and Flatpak dirs — ensures configs
            # are available regardless of which OBS version is running.
            profile_dirs = [
                primary_profile_dir,
                os.path.expanduser("~/.config/obs-studio/basic/profiles/RadioDJ"),
                os.path.expanduser("~/.var/app/com.obsproject.Studio/config/obs-studio/basic/profiles/RadioDJ"),
            ]
            # Deduplicate (primary dir may match one of the two above)
            profile_dirs = list(dict.fromkeys(profile_dirs))

            if stream_key:
                bridge = get_bridge()
                if bridge and bridge.enabled:
                    # Write service.json to ALL OBS profile directories.
                    # OBS reads this file when initializing the output module.
                    # Without it, OBS falls back to RTMPS with no key → TLS errors.
                    import json
                    service_data = {
                        "type": "rtmp_custom",
                        "settings": {
                            "server": rtmp_server,
                            "key": stream_key,
                        },
                    }
                    for p_dir in profile_dirs:
                        try:
                            os.makedirs(p_dir, exist_ok=True)
                            with open(os.path.join(p_dir, "service.json"), "w") as f:
                                json.dump(service_data, f, indent=4)
                            logging.info(
                                f"OBS: Wrote service.json → {p_dir} "
                                f"(server: {rtmp_server}, key: ...{stream_key[-4:]})"
                            )
                        except Exception as e:
                            logging.warning(f"OBS: Failed to write service.json to {p_dir}: {e}")

                    # Write streamEncoder.json to ALL OBS profile directories.
                    # OBS 29 uses per-encoder JSON files that take PRECEDENCE
                    # over basic.ini settings. Without this file, OBS uses
                    # YouTube's recommended settings (bitrate=2500, keyint=250)
                    # even though basic.ini says keyint_sec=2 and Bitrate=3000.
                    # This MUST be written before OBS starts streaming — OBS
                    # reads encoder settings from disk at startup.
                    encoder_data = {
                        "obs_x264": {
                            "rate_control": "CBR",
                            "bitrate": 3000,
                            "buffer_size": 3000,
                            "keyint_sec": 2,
                            "preset": "veryfast",
                            "profile": "high",
                            "tune": "zerolatency",
                            "x264opts": "keyint=60:min-keyint=60:bframes=0",
                        }
                    }
                    for p_dir in profile_dirs:
                        try:
                            os.makedirs(p_dir, exist_ok=True)
                            with open(os.path.join(p_dir, "streamEncoder.json"), "w") as f:
                                json.dump(encoder_data, f, indent=4)
                            logging.info(
                                f"OBS: Wrote streamEncoder.json → {p_dir} "
                                f"(keyint_sec=2, bitrate=3000)"
                            )
                        except Exception as e:
                            logging.warning(f"OBS: Failed to write streamEncoder.json to {p_dir}: {e}")

                    # ALSO push via WebSocket API (OBS applies these immediately)
                    result = bridge.set_stream_settings(
                        service="rtmp_custom",
                        server=rtmp_server,
                        key=stream_key,
                    )
                    if result.get("error") and not result.get("connected"):
                        logging.warning(f"OBS: Failed to push stream settings at startup: {result}")
                    else:
                        logging.info(f"OBS: Stream settings pushed (RTMP: {rtmp_server}, key: ...{stream_key[-4:]})")
            else:
                logging.warning(
                    "OBS: ⚠️ No YOUTUBE_STREAM_KEY configured. "
                    "OBS will not be able to stream to YouTube. "
                    "Set YOUTUBE_STREAM_KEY in .env or Mission Control."
                )

            # Wait for OBS to become ready before sending scene/source commands.
            # OBS (especially Flatpak OBS 32) can take 10-30 seconds to fully
            # initialize after the process starts. Sending WebSocket commands
            # too early causes crashes ("basic_string: construction from null
            # is not valid" — OBS tries to enumerate a scene that doesn't
            # exist yet).
            #
            # Instead of a fixed sleep(1), we poll the WebSocket until OBS
            # actually responds, then wait a few more seconds for OBS's
            # internal scene loading to complete before creating sources.
            bridge = get_bridge()
            if bridge and bridge.enabled:
                obs_ready = bridge.wait_for_obs(timeout=60, poll_interval=2.0)
                if obs_ready:
                    # Give OBS a few extra seconds to finish loading the scene
                    # collection and initializing its internal state. Even after
                    # the WebSocket responds, OBS may still be processing the
                    # scene collection JSON and trying to create sources.
                    import time as _time
                    _time.sleep(3)

                    try:
                        bridge.ensure_scenes_exist()
                        logging.info("OBS: Scene setup complete ✅")
                    except Exception as e:
                        logging.warning(f"OBS: Scene setup failed (will retry on stream start): {e}")
                else:
                    logging.warning(
                        "OBS: Timed out waiting for OBS WebSocket. "
                        "Scene setup will be attempted when streaming starts."
                    )

            # Mute OBS's Desktop Audio (PulseAudio capture).
            # The bot sends audio via UDP (ffmpeg_source "Bot Audio (UDP)")
            # at 48kHz. OBS's Desktop Audio also captures from PulseAudio
            # at 44.1kHz, causing a double-audio + sample-rate mismatch
            # that makes the stream audio sound "slowed down".
            # Muting Desktop Audio ensures only the clean UDP path is used.
            bridge = get_bridge()
            if bridge and bridge.enabled:
                try:
                    result = bridge.set_source_mute("Desktop Audio", muted=True)
                    if not result.get("error"):
                        logging.info("OBS: Muted Desktop Audio (bot audio comes via UDP, not PulseAudio)")
                except Exception:
                    pass

                # Force-push correct audio source settings early.
                # OBS may have loaded stale settings from a previous run
                # (e.g. "ar=48000 ac=2" which is WRONG — causes slow
                # loud audio). This ensures the UDP source has the correct
                # "sample_rate=48000 channels=2" before any audio plays.
                try:
                    bridge.create_audio_source()
                    logging.info("OBS: Audio source settings force-updated (sample_rate=48000 channels=2)")
                except Exception as e:
                    logging.debug(f"OBS: Audio source pre-update failed (will retry on stream start): {e}")
        except Exception as e:
            logging.warning(f"OBS Bridge initialization failed: {e}")

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
