"""
web/app.py — Mission Control Dashboard for MBot.

A Flask web app that runs alongside the Discord bot, providing:
- Live dashboard with playback controls, queue manager, album art
- DJ line management (add/remove custom lines per category)
- DJ voice picker (dropdown of all edge-tts voices)
- Search-to-queue (paste a URL or search term, bot plays it)
- Interactive volume/speed sliders

The bot instance is passed in at startup so the dashboard can read
and modify bot state directly via the Music cog.
"""

import hashlib
import hmac
import asyncio
import logging
import os
import re
import signal
import sys
import time
import urllib.parse

import config

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    Response,
    session,
    url_for,
)

from utils.custom_lines import (
    LINE_CATEGORIES,
    CATEGORY_LABELS,
    CATEGORY_PLACEHOLDERS,
    add_line,
    load_custom_lines,
    remove_line,
)
from utils.llm_dj import OLLAMA_DJ_AVAILABLE, check_ollama_available

app = Flask(__name__)
app.secret_key = os.environ.get(
    "SECRET_KEY", "mbot-mission-control-secret-key-change-me"
)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB max upload

# ── Reverse Proxy Support ────────────────────────────────────────────
# When the dashboard runs behind a reverse proxy (Nginx Proxy Manager,
# Caddy, Traefik, Cloudflare Tunnel, etc.), enable REVERSE_PROXY in .env
# so Flask correctly handles X-Forwarded-* headers. This fixes:
# - HTTPS redirects (proxy terminates TLS, Flask sees HTTP without this)
# - Real client IPs in logs (otherwise every request appears from 127.0.0.1)
# - Correct URL generation (url_for, redirect) with the external hostname
if getattr(config, "REVERSE_PROXY", False):
    from werkzeug.middleware.proxy_fix import ProxyFix

    proxy_count = getattr(config, "TRUSTED_PROXY_COUNT", 1)
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=proxy_count,  # Trust X-Forwarded-For
        x_proto=proxy_count,  # Trust X-Forwarded-Proto (HTTP → HTTPS)
        x_host=proxy_count,  # Trust X-Forwarded-Host (correct hostname)
        x_prefix=proxy_count,  # Trust X-Forwarded-Prefix (subpath support)
    )
    logging.info(
        f"Dashboard: Reverse proxy support enabled (trusted proxies: {proxy_count})"
    )


# ── Authentication ────────────────────────────────────────────────────


def _password_required():
    """Return True if a password is configured and authentication is needed."""
    return bool(getattr(config, "WEB_PASSWORD", ""))


@app.before_request
def require_login():
    """Redirect unauthenticated users to the login page when a password is set.

    Public endpoints (login page, static files, API endpoints) are always
    accessible so that the login flow and client-side JS calls work.
    """
    if not _password_required():
        return  # No password configured — open access

    # Allow these endpoints without authentication
    allowed_endpoints = {"login", "static"}
    if request.endpoint in allowed_endpoints:
        return

    # API endpoints require session auth
    if session.get("authenticated"):
        return

    # Redirect everything else to login
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    """Login page for Mission Control."""
    if not _password_required():
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        password = request.form.get("password", "")
        if hmac.compare_digest(
            hashlib.sha256(password.encode()).hexdigest(),
            hashlib.sha256(config.WEB_PASSWORD.encode()).hexdigest(),
        ):
            session["authenticated"] = True
            flash("Logged in successfully.", "success")
            return redirect(url_for("dashboard"))
        else:
            flash("Incorrect password. Please try again.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    """Log out and redirect to the login page."""
    session.pop("authenticated", None)
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


# ── Template Filters ──────────────────────────────────────────────────


@app.template_filter("highlight_sound_tags")
def highlight_sound_tags(text):
    """Wrap {sound:name} tags in styled spans so they stand out on the DJ Lines page."""
    return re.sub(
        r"\{sound:([^}]+)\}",
        r'<span class="sound-tag">🔊 \1</span>',
        text,
    )


@app.template_filter("highlight_placeholders")
def highlight_placeholders(text):
    """Wrap all {placeholder} tags in styled spans, with different styles for sound tags."""
    # First highlight sound tags
    text = re.sub(
        r"\{sound:([^}]+)\}",
        r'<span class="sound-tag">🔊 \1</span>',
        text,
    )
    # Then highlight other placeholders like {title}, {prev_title}, etc.
    text = re.sub(
        r"\{(greeting|title|prev_title|next_title)\}",
        r'<span class="placeholder-tag">{\1}</span>',
        text,
    )
    return text


# ── Bot state (set by bot.py at startup) ──────────────────────────
bot = None


@app.context_processor
def inject_bot_name():
    """Make the bot's name and session auth state available in all templates."""
    name = bot.user.name if bot and bot.user else "MBot"
    return {
        "bot_name": name,
        "logged_in": session.get("authenticated", False),
    }


def init_dashboard(discord_bot):
    """Called from bot.py to inject the running bot instance."""
    global bot
    bot = discord_bot


def _get_music_cog():
    """Return the Music cog from the running bot, or None."""
    if bot is None:
        return None
    return bot.get_cog("Music")


def _run_async(coro):
    """Submit an async coroutine to the bot's event loop and wait for it.

    This is the bridge between Flask (sync threads) and discord.py (async).
    Returns the coroutine's result, or None if the loop is unavailable.
    """
    if bot is None or bot.loop is None:
        return None
    future = asyncio.run_coroutine_threadsafe(coro, bot.loop)
    try:
        return future.result(timeout=10)
    except Exception as e:
        logging.error(f"Dashboard: async call failed: {e}")
        return None


def _build_atempo_chain(speed):
    """Build an FFmpeg atempo filter chain for any speed value.

    FFmpeg's atempo filter only supports 0.5-2.0 per instance.
    For speeds outside that range, we chain multiple atempo filters.
    E.g. 0.25x = atempo=0.5,atempo=0.5
    """
    filters = []
    remaining = speed
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5  # each 0.5 halves the speed
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0  # each 2.0 doubles the speed
    filters.append(f"atempo={remaining}")
    return filters


# ── Dashboard ────────────────────────────────────────────────────


@app.route("/")
def dashboard():
    music = _get_music_cog()
    guilds_data = []

    if bot and bot.guilds:
        for guild in bot.guilds:
            guild_id = guild.id
            voice = guild.voice_client
            current = None
            queue_items = []
            queue_size = 0

            if music:
                current = music.current_song.get(guild_id)
                q = music.song_queues.get(guild_id)
                if q:
                    queue_size = q.qsize()
                    # Show only 5 items in the compact queue view
                    try:
                        queue_items = list(q._queue)[:5]
                    except Exception:
                        queue_items = []

            guilds_data.append(
                {
                    "id": guild_id,
                    "name": guild.name,
                    "member_count": guild.member_count,
                    "in_voice": voice is not None,
                    "voice_channel": voice.channel.name if voice else None,
                    "playing": voice.is_playing() if voice else False,
                    "paused": voice.is_paused() if voice else False,
                    "current_song": current.title if current else None,
                    "current_song_url": current.webpage_url if current else None,
                    "current_thumbnail": current.thumbnail if current else None,
                    "current_duration": current.duration if current else None,
                    "current_elapsed": (
                        int(time.time() - music.song_start_time[guild_id])
                        if music
                        and guild_id in music.song_start_time
                        and (voice and (voice.is_playing() or voice.is_paused()))
                        else 0
                    ),
                    "queue_size": queue_size,
                    "queue_duration": sum(
                        getattr(item, "duration", 0) or 0 for item in queue_items
                    ),
                    "queue_items": [
                        {
                            "title": item.title,
                            "url": getattr(item, "webpage_url", None),
                            "thumbnail": getattr(item, "thumbnail", None),
                            "duration": getattr(item, "duration", None),
                        }
                        for item in queue_items
                    ],
                    "dj_enabled": music.dj_enabled.get(guild_id, False)
                    if music
                    else False,
                    "dj_voice": music.dj_voice.get(guild_id, "")
                    or getattr(config, "DJ_VOICE", "af_heart")
                    if music
                    else getattr(config, "DJ_VOICE", "af_heart"),
                    "volume": int(music.current_volume.get(guild_id, 1.0) * 100)
                    if music
                    else 100,
                    "looping": music.looping.get(guild_id, False) if music else False,
                    "speed": music.playback_speed.get(guild_id, 1.0) if music else 1.0,
                    "autodj_enabled": music.autodj_enabled.get(guild_id, False)
                    if music
                    else False,
                    "autodj_source": music.autodj_source.get(guild_id, "")
                    if music
                    else "",
                    "ai_dj_enabled": music.ai_dj_enabled.get(guild_id, False)
                    if music
                    else False,
                    "ai_dj_voice": music.ai_dj_voice.get(guild_id, "")
                    or getattr(config, "OLLAMA_DJ_VOICE", "am_adam")
                    if music
                    else getattr(config, "OLLAMA_DJ_VOICE", "am_adam"),
                    "recently_played": music.recently_played.get(guild_id, [])[:15]
                    if music
                    else [],
                    "listeners": [
                        {
                            "id": m.id,
                            "name": m.display_name,
                            "avatar": m.display_avatar.url if m.avatar else None,
                        }
                        for m in (voice.channel.members if voice else [])
                        if not m.bot
                    ],
                }
            )

    from utils.presets import list_presets as list_presets_fn

    return render_template(
        "dashboard.html",
        guilds=guilds_data,
        presets=list_presets_fn(),
        bot_user=str(bot.user) if bot else "Not connected",
        bot_avatar=bot.user.display_avatar.url if bot and bot.user else None,
        guild_count=len(bot.guilds) if bot else 0,
        auto_refresh=True,
    )


# ── Radio Page ──────────────────────────────────────────────────


@app.route("/radio")
def radio():
    """Radio / Auto-DJ control page with recently played history."""
    music = _get_music_cog()
    guilds_data = []

    if bot and bot.guilds:
        for guild in bot.guilds:
            guild_id = guild.id
            voice = guild.voice_client
            current = None

            if music:
                current = music.current_song.get(guild_id)

            guilds_data.append(
                {
                    "id": guild_id,
                    "name": guild.name,
                    "member_count": guild.member_count,
                    "in_voice": voice is not None,
                    "voice_channel": voice.channel.name if voice else None,
                    "playing": voice.is_playing() if voice else False,
                    "paused": voice.is_paused() if voice else False,
                    "current_song": current.title if current else None,
                    "current_song_url": current.webpage_url if current else None,
                    "current_thumbnail": current.thumbnail if current else None,
                    "current_duration": current.duration if current else None,
                    "current_elapsed": (
                        int(time.time() - music.song_start_time[guild_id])
                        if music
                        and guild_id in music.song_start_time
                        and (voice and (voice.is_playing() or voice.is_paused()))
                        else 0
                    ),
                    "queue_size": music.song_queues.get(
                        guild_id, asyncio.Queue()
                    ).qsize()
                    if music
                    else 0,
                    "dj_enabled": music.dj_enabled.get(guild_id, False)
                    if music
                    else False,
                    "autodj_enabled": music.autodj_enabled.get(guild_id, False)
                    if music
                    else False,
                    "autodj_source": music.autodj_source.get(guild_id, "")
                    if music
                    else "",
                    "ai_dj_enabled": music.ai_dj_enabled.get(guild_id, False)
                    if music
                    else False,
                    "ai_dj_voice": music.ai_dj_voice.get(guild_id, "")
                    or getattr(config, "OLLAMA_DJ_VOICE", "am_adam")
                    if music
                    else getattr(config, "OLLAMA_DJ_VOICE", "am_adam"),
                    "recently_played": music.recently_played.get(guild_id, [])[:30]
                    if music
                    else [],
                    "listeners": [
                        {
                            "id": m.id,
                            "name": m.display_name,
                            "avatar": m.display_avatar.url if m.avatar else None,
                        }
                        for m in (voice.channel.members if voice else [])
                        if not m.bot
                    ],
                }
            )

    return render_template(
        "radio.html",
        guilds=guilds_data,
        bot_user=str(bot.user) if bot else "Not connected",
        guild_count=len(bot.guilds) if bot else 0,
        tts_mode=getattr(config, "TTS_MODE", "moss"),
        config=config,
    )


# ── Queue Manager Page ──────────────────────────────────────────────


@app.route("/queue-manager")
def queue_manager():
    """Queue manager page — add songs/playlists, view and manage the queue."""
    music = _get_music_cog()
    guilds_data = []

    if bot and bot.guilds:
        for guild in bot.guilds:
            guild_id = guild.id
            voice = guild.voice_client
            current = None
            queue_items = []
            queue_size = 0

            if music:
                current = music.current_song.get(guild_id)
                q = music.song_queues.get(guild_id)
                if q:
                    queue_size = q.qsize()
                    try:
                        queue_items = list(q._queue)  # Show all items
                    except Exception:
                        queue_items = []

            guilds_data.append(
                {
                    "id": guild_id,
                    "name": guild.name,
                    "member_count": guild.member_count,
                    "in_voice": voice is not None,
                    "voice_channel": voice.channel.name if voice else None,
                    "playing": voice.is_playing() if voice else False,
                    "paused": voice.is_paused() if voice else False,
                    "current_song": current.title if current else None,
                    "current_song_url": current.webpage_url if current else None,
                    "current_thumbnail": current.thumbnail if current else None,
                    "current_duration": current.duration if current else None,
                    "queue_size": queue_size,
                    "queue_items": [
                        {
                            "title": item.title,
                            "url": getattr(item, "webpage_url", None),
                            "thumbnail": getattr(item, "thumbnail", None),
                            "duration": getattr(item, "duration", None),
                        }
                        for item in queue_items
                    ],
                    "dj_enabled": music.dj_enabled.get(guild_id, False)
                    if music
                    else False,
                    "dj_voice": music.dj_voice.get(guild_id, "")
                    or getattr(config, "DJ_VOICE", "af_heart")
                    if music
                    else getattr(config, "DJ_VOICE", "af_heart"),
                    "volume": int(music.current_volume.get(guild_id, 1.0) * 100)
                    if music
                    else 100,
                    "looping": music.looping.get(guild_id, False) if music else False,
                    "speed": music.playback_speed.get(guild_id, 1.0) if music else 1.0,
                    "autodj_enabled": music.autodj_enabled.get(guild_id, False)
                    if music
                    else False,
                    "autodj_source": music.autodj_source.get(guild_id, "")
                    if music
                    else "",
                    "ai_dj_enabled": music.ai_dj_enabled.get(guild_id, False)
                    if music
                    else False,
                    "ai_dj_voice": music.ai_dj_voice.get(guild_id, "")
                    or getattr(config, "OLLAMA_DJ_VOICE", "am_adam")
                    if music
                    else getattr(config, "OLLAMA_DJ_VOICE", "am_adam"),
                }
            )

    from utils.presets import list_presets as list_presets_fn

    return render_template(
        "queue_manager.html",
        guilds=guilds_data,
        presets=list_presets_fn(),
        bot_user=str(bot.user) if bot else "Not connected",
        bot_avatar=bot.user.display_avatar.url if bot and bot.user else None,
        guild_count=len(bot.guilds) if bot else 0,
    )


# ── API Endpoints (called via JavaScript from dashboard) ─────────


@app.route("/api/<int:guild_id>/skip", methods=["POST"])
def api_skip(guild_id):
    music = _get_music_cog()
    if not music:
        return jsonify({"error": "Music cog not loaded"}), 503
    guild = bot.get_guild(guild_id)
    if not guild or not guild.voice_client or not guild.voice_client.is_playing():
        return jsonify({"error": "Nothing playing"}), 400
    guild.voice_client.stop()
    return jsonify({"ok": True})


@app.route("/api/<int:guild_id>/pause", methods=["POST"])
def api_pause(guild_id):
    music = _get_music_cog()
    if not music:
        return jsonify({"error": "Music cog not loaded"}), 503
    guild = bot.get_guild(guild_id)
    if not guild or not guild.voice_client:
        return jsonify({"error": "Not in voice"}), 400
    if guild.voice_client.is_paused():
        guild.voice_client.resume()
        return jsonify({"ok": True, "state": "playing"})
    elif guild.voice_client.is_playing():
        guild.voice_client.pause()
        return jsonify({"ok": True, "state": "paused"})
    return jsonify({"error": "Nothing playing"}), 400


@app.route("/api/<int:guild_id>/join", methods=["POST"])
def api_join(guild_id):
    """Join the first voice channel that has a human member in it."""
    guild = bot.get_guild(guild_id) if bot else None
    if not guild:
        return jsonify({"error": "Guild not found"}), 404
    if guild.voice_client and guild.voice_client.is_connected():
        return jsonify({"ok": True, "note": "Already in voice"})

    async def _join():
        # Find the first human in a voice channel
        voice_channel = None
        for member in guild.members:
            if not member.bot and member.voice and member.voice.channel:
                voice_channel = member.voice.channel
                break
        if not voice_channel:
            return "No one in a voice channel"
        await voice_channel.connect(self_deaf=True)
        return "Joined " + voice_channel.name

    result = _run_async(_join())
    if result is None:
        return jsonify({"error": "Request timed out"}), 504
    if "no one" in str(result).lower():
        return jsonify({"error": result}), 404
    return jsonify({"ok": True, "result": str(result)})


@app.route("/api/<int:guild_id>/stop", methods=["POST"])
def api_stop(guild_id):
    music = _get_music_cog()
    if not music:
        return jsonify({"error": "Music cog not loaded"}), 503

    async def _stop():
        queue = await music.get_queue(guild_id)
        while not queue.empty():
            await queue.get()
        guild = bot.get_guild(guild_id)
        if guild and guild.voice_client:
            guild.voice_client.stop()

    _run_async(_stop())
    return jsonify({"ok": True})


@app.route("/api/<int:guild_id>/leave", methods=["POST"])
def api_leave(guild_id):
    """Disconnect the bot from the voice channel in a guild."""
    guild = bot.get_guild(guild_id) if bot else None
    if not guild or not guild.voice_client:
        return jsonify({"error": "Not in voice"}), 400

    async def _leave():
        await guild.voice_client.disconnect()

    _run_async(_leave())
    return jsonify({"ok": True})


@app.route("/api/<int:guild_id>/ai_dj_toggle", methods=["POST"])
def api_ai_dj_toggle(guild_id):
    """Toggle the AI side host (studio joker) on or off."""
    music = _get_music_cog()
    if not music:
        return jsonify({"error": "Music cog not loaded"}), 503
    music.ai_dj_enabled[guild_id] = not music.ai_dj_enabled.get(guild_id, False)
    return jsonify({"ok": True, "ai_dj_enabled": music.ai_dj_enabled[guild_id]})


@app.route("/api/<int:guild_id>/ai_dj_voice", methods=["POST"])
def api_ai_dj_voice(guild_id):
    """Set the AI side host's TTS voice."""
    music = _get_music_cog()
    if not music:
        return jsonify({"error": "Music cog not loaded"}), 503
    voice = request.json.get("voice", "")
    if not voice:
        return jsonify({"error": "Voice required"}), 400
    music.ai_dj_voice[guild_id] = voice
    return jsonify({"ok": True, "voice": voice})


@app.route("/api/<int:guild_id>/ai_dj_status")
def api_ai_dj_status(guild_id):
    """Get the AI side host status for a guild."""
    music = _get_music_cog()
    if not music:
        return jsonify({"error": "Music cog not loaded"}), 503
    import config as cfg

    return jsonify(
        {
            "enabled": music.ai_dj_enabled.get(guild_id, False),
            "voice": music.ai_dj_voice.get(guild_id, "")
            or getattr(cfg, "OLLAMA_DJ_VOICE", "en_news_male"),
            "model": getattr(cfg, "OLLAMA_MODEL", "gemma4:latest"),
            "chance": getattr(cfg, "OLLAMA_DJ_CHANCE", 0.25),
            "ollama_available": OLLAMA_DJ_AVAILABLE,
        }
    )


@app.route("/api/<int:guild_id>/volume", methods=["POST"])
def api_volume(guild_id):
    music = _get_music_cog()
    if not music:
        return jsonify({"error": "Music cog not loaded"}), 503
    data = request.json or request.form
    try:
        vol = int(data.get("volume", 100))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid volume"}), 400
    vol = max(0, min(200, vol))
    music.current_volume[guild_id] = vol / 100.0
    # Apply to currently playing source
    guild = bot.get_guild(guild_id) if bot else None
    if guild and guild.voice_client and guild.voice_client.source:
        guild.voice_client.source.volume = vol / 100.0
    return jsonify({"ok": True, "volume": vol})


@app.route("/api/<int:guild_id>/speed", methods=["POST"])
def api_speed(guild_id):
    """Set playback speed. Restarts the current song with the new atempo filter."""
    music = _get_music_cog()
    if not music:
        return jsonify({"error": "Music cog not loaded"}), 503
    data = request.json or request.form
    speed_steps = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
    try:
        speed = float(data.get("speed", 1.0))
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid speed"}), 400
    # Snap to nearest step
    speed = min(speed_steps, key=lambda s: abs(s - speed))

    # Save the speed regardless — this way it applies to the next song even if
    # nothing is playing right now
    music.playback_speed[guild_id] = speed

    guild = bot.get_guild(guild_id) if bot else None
    if not guild or not guild.voice_client or not guild.voice_client.is_playing():
        return jsonify({"ok": True, "speed": speed, "note": "saved for next song"})

    current_song = music.current_song.get(guild_id)
    if not current_song or not current_song.url:
        return jsonify({"ok": True, "speed": speed, "note": "saved for next song"})

    # Restart the song with the new speed via the bot's event loop
    async def _apply_speed():
        try:
            guild.voice_client.stop()
            await asyncio.sleep(0.3)

            from cogs.youtube import FFMPEG_OPTIONS
            import discord

            player_options = FFMPEG_OPTIONS.copy()
            if speed != 1.0:
                atempo_filters = _build_atempo_chain(speed)
                player_options["options"] += f' -filter:a "{"+".join(atempo_filters)}"'

            source = discord.FFmpegPCMAudio(current_song.url, **player_options)
            player = discord.PCMVolumeTransformer(source)
            player.volume = music.current_volume.get(guild_id, 1.0)
            guild.voice_client.play(player)
            music.song_start_time[guild_id] = time.time()
            logging.info(
                f"Speed API: Restarted playback at {speed}x for guild {guild_id}"
            )
            return True
        except Exception as e:
            logging.error(f"Speed API: Failed to restart playback: {e}")
            return False

    result = _run_async(_apply_speed())
    if result:
        return jsonify({"ok": True, "speed": speed})
    return jsonify(
        {"ok": True, "speed": speed, "note": "speed saved, restart may have failed"}
    )


@app.route("/api/<int:guild_id>/dj_toggle", methods=["POST"])
def api_dj_toggle(guild_id):
    music = _get_music_cog()
    if not music:
        return jsonify({"error": "Music cog not loaded"}), 503
    from utils.dj import EDGE_TTS_AVAILABLE

    if not EDGE_TTS_AVAILABLE:
        return jsonify({"error": "edge-tts not installed"}), 400
    music.dj_enabled[guild_id] = not music.dj_enabled.get(guild_id, False)
    return jsonify({"ok": True, "dj_enabled": music.dj_enabled[guild_id]})


@app.route("/api/<int:guild_id>/dj_voice", methods=["POST"])
def api_dj_voice(guild_id):
    music = _get_music_cog()
    if not music:
        return jsonify({"error": "Music cog not loaded"}), 503
    voice = request.json.get("voice", "")
    if not voice:
        return jsonify({"error": "Voice required"}), 400
    music.dj_voice[guild_id] = voice
    return jsonify({"ok": True, "voice": voice})


@app.route("/api/<int:guild_id>/voices")
def api_voices(guild_id):
    """Return available TTS voices, with server-side caching to avoid repeated API calls.

    Supports three TTS engines via config.TTS_MODE:
    - "moss": Returns voices from assets/moss_voices/ directory (cached 30 min)
    - "vibevoice": Queries VibeVoice-Realtime /config endpoint (cached 30 min)
    - "edge-tts": Fetches from Microsoft's TTS API (cached 30 min)
    """
    from utils.dj import list_voices, EDGE_TTS_AVAILABLE, TTS_MODE as CURRENT_TTS_MODE

    lang = request.args.get("lang", "en")

    # ── MOSS TTS mode ────────────────────────────────────────────
    if CURRENT_TTS_MODE == "moss":
        cache_key = "_moss_voice_cache"
        cache_timestamp_key = "_moss_voice_cache_ts"

        if not hasattr(api_voices, cache_key):
            setattr(api_voices, cache_key, None)
            setattr(api_voices, cache_timestamp_key, 0)

        cached_voices = getattr(api_voices, cache_key)
        cache_ts = getattr(api_voices, cache_timestamp_key)
        cache_ttl = 30 * 60  # 30 minutes

        if cached_voices is not None and (time.time() - cache_ts) < cache_ttl:
            return jsonify({"voices": cached_voices, "tts_mode": "moss"})

        raw_voices = _run_async(list_voices(lang))
        if raw_voices is None:
            if cached_voices is not None:
                return jsonify(
                    {"voices": cached_voices, "cached": True, "tts_mode": "moss"}
                )
            return jsonify(
                {
                    "voices": [],
                    "tts_mode": "moss",
                    "error": "Failed to fetch voices from MOSS-TTS-Nano server. "
                    "Make sure the server is running at {url}.".format(
                        url=getattr(config, "MOSS_TTS_URL", "http://localhost:18083")
                    ),
                }
            )

        formatted = [
            {
                "name": v.get("ShortName", v.get("name", "")),
                "is_default": v.get("default", False),
                "gender": v.get("Gender", v.get("gender", "?")),
                "locale": v.get("Locale", v.get("locale", "?")),
                "description": v.get("description", ""),
            }
            for v in raw_voices
        ]

        setattr(api_voices, cache_key, formatted)
        setattr(api_voices, cache_timestamp_key, time.time())

        return jsonify({"voices": formatted, "tts_mode": "moss"})

    # ── VibeVoice TTS mode ──────────────────────────────────────────
    if CURRENT_TTS_MODE == "vibevoice":
        cache_key = "_vv_voice_cache"
        cache_timestamp_key = "_vv_voice_cache_ts"

        if not hasattr(api_voices, cache_key):
            setattr(api_voices, cache_key, None)
            setattr(api_voices, cache_timestamp_key, 0)

        cached_voices = getattr(api_voices, cache_key)
        cache_ts = getattr(api_voices, cache_timestamp_key)
        cache_ttl = 30 * 60  # 30 minutes

        if cached_voices is not None and (time.time() - cache_ts) < cache_ttl:
            return jsonify({"voices": cached_voices, "tts_mode": "vibevoice"})

        raw_voices = _run_async(list_voices(lang))
        if raw_voices is None:
            if cached_voices is not None:
                return jsonify(
                    {"voices": cached_voices, "cached": True, "tts_mode": "vibevoice"}
                )
            return jsonify(
                {
                    "voices": [],
                    "tts_mode": "vibevoice",
                    "error": "Failed to fetch voices from VibeVoice server. "
                    "Make sure VibeVoice-Realtime is running at {url}.".format(
                        url=getattr(
                            config, "VIBEVOICE_TTS_URL", "http://localhost:3000"
                        )
                    ),
                }
            )

        formatted = [
            {
                "name": v.get("ShortName", v.get("name", "")),
                "gender": v.get("Gender", v.get("gender", "?")),
                "locale": v.get("Locale", v.get("locale", "?")),
            }
            for v in raw_voices
        ]

        setattr(api_voices, cache_key, formatted)
        setattr(api_voices, cache_timestamp_key, time.time())

        return jsonify({"voices": formatted, "tts_mode": "vibevoice"})

    # ── Default: edge-tts mode ─────────────────────────────────────
    if not EDGE_TTS_AVAILABLE:
        return jsonify(
            {
                "voices": [],
                "tts_mode": "edge-tts",
                "error": "edge-tts not installed — install with: pip install edge-tts",
            }
        )

    # ── Server-side voice cache ──────────────────────────────────
    # edge_tts.list_voices() makes a live HTTP request to Microsoft's TTS
    # API every call (5-15 seconds). Caching prevents the Radio page
    # from hanging on every load or every time the voice dropdown opens.
    cache_key = f"_voice_cache_{lang}"
    cache_timestamp_key = f"_voice_cache_ts_{lang}"

    if not hasattr(api_voices, cache_key) or not hasattr(
        api_voices, cache_timestamp_key
    ):
        setattr(api_voices, cache_key, None)
        setattr(api_voices, cache_timestamp_key, 0)

    cached_voices = getattr(api_voices, cache_key)
    cache_ts = getattr(api_voices, cache_timestamp_key)
    cache_ttl = 30 * 60  # 30 minutes

    if cached_voices is not None and (time.time() - cache_ts) < cache_ttl:
        return jsonify({"voices": cached_voices, "tts_mode": "edge-tts"})

    # Cache miss — fetch from edge-tts
    raw_voices = _run_async(list_voices(lang))
    if raw_voices is None:
        # The async call timed out or failed — return stale cache if available
        if cached_voices is not None:
            return jsonify(
                {"voices": cached_voices, "cached": True, "tts_mode": "edge-tts"}
            )
        return jsonify(
            {
                "voices": [],
                "tts_mode": "edge-tts",
                "error": "Failed to fetch voices (request timed out). edge-tts may not be installed or the Microsoft TTS API is unreachable.",
            }
        )

    formatted = [
        {
            "name": v["ShortName"],
            "gender": v.get("Gender", "?"),
            "locale": v.get("Locale", "?"),
        }
        for v in raw_voices
    ]

    # Update cache
    setattr(api_voices, cache_key, formatted)
    setattr(api_voices, cache_timestamp_key, time.time())

    return jsonify({"voices": formatted, "tts_mode": "edge-tts"})


@app.route("/api/<int:guild_id>/queue/<int:index>", methods=["DELETE"])
def api_queue_remove(guild_id, index):
    music = _get_music_cog()
    if not music:
        return jsonify({"error": "Music cog not loaded"}), 503

    async def _remove():
        q = await music.get_queue(guild_id)
        if index < 0 or index >= q.qsize():
            return False
        items = []
        while not q.empty():
            items.append(await q.get())
        removed = items.pop(index)
        for item in items:
            await q.put(item)
        return True

    result = _run_async(_remove())
    if result:
        return jsonify({"ok": True})
    return jsonify({"error": "Invalid index"}), 400


@app.route("/api/<int:guild_id>/play", methods=["POST"])
def api_play(guild_id):
    music = _get_music_cog()
    if not music:
        return jsonify({"error": "Music cog not loaded"}), 503
    query = request.json.get("query", "").strip()
    if not query:
        return jsonify({"error": "Query required"}), 400

    async def _play():
        guild = bot.get_guild(guild_id)
        if not guild:
            return "Guild not found"

        # Join a voice channel if not already in one
        # Find the first human in a voice channel
        voice_channel = None
        for member in guild.members:
            if not member.bot and member.voice and member.voice.channel:
                voice_channel = member.voice.channel
                break

        if not voice_channel:
            return "No one in a voice channel"

        if not guild.voice_client:
            await voice_channel.connect(self_deaf=True)
        elif not guild.voice_client.is_connected():
            await guild.voice_client.disconnect(force=True)
            await asyncio.sleep(0.5)
            await voice_channel.connect(self_deaf=True)

        queue = await music.get_queue(guild_id)

        from utils.suno import is_suno_url, get_suno_track
        from cogs.youtube import YTDLSource, PlaceholderTrack

        # Determine URL type and extract
        if is_suno_url(query):
            track = await get_suno_track(query)
            if not track:
                return "Could not resolve Suno URL"
            await queue.put(track)
            count = 1
        elif "playlist" in query or "list=" in query:
            # No playlist_items limit — load the entire playlist
            tracks = await PlaceholderTrack.from_playlist_url(query, loop=bot.loop)
            for t in tracks:
                await queue.put(t)
            count = len(tracks)
        else:
            result = await YTDLSource.from_url(query, loop=bot.loop)
            for r in result:
                await queue.put(r)
            count = len(result)

        # Start playback if nothing is playing
        if not guild.voice_client.is_playing() and not guild.voice_client.is_paused():
            # Build a minimal context object for play_next
            class WebCtx:
                pass

            ctx = WebCtx()
            ctx.guild = guild
            ctx.voice_client = guild.voice_client
            ctx.channel = guild.text_channels[0] if guild.text_channels else None
            ctx.author = guild.me
            # Cancel any inactivity timer
            if guild_id in music.inactivity_timers:
                music.inactivity_timers[guild_id].cancel()
                del music.inactivity_timers[guild_id]
            await music.play_next(ctx)

        return f"Added {count} track{'s' if count != 1 else ''}"

    result = _run_async(_play())
    if result is None:
        return jsonify({"error": "Request timed out"}), 504
    if "not found" in str(result).lower() or "no one" in str(result).lower():
        return jsonify({"error": result}), 404
    return jsonify({"ok": True, "result": str(result)})


# ── DJ Lines ─────────────────────────────────────────────────────


@app.route("/dj-lines")
def dj_lines():
    from utils.soundboard import list_sounds

    custom = load_custom_lines()
    categories = []
    for cat in LINE_CATEGORIES:
        built_in = _get_builtin_lines(cat)
        custom_for_cat = custom.get(cat, [])
        categories.append(
            {
                "key": cat,
                "label": CATEGORY_LABELS.get(cat, cat),
                "placeholders": CATEGORY_PLACEHOLDERS.get(cat, []),
                "builtin": built_in,
                "builtin_count": len(built_in),
                "custom": custom_for_cat,
                "custom_count": len(custom_for_cat),
                "total": len(built_in) + len(custom_for_cat),
            }
        )
    return render_template("dj_lines.html", categories=categories, sounds=list_sounds())


@app.route("/dj-lines/add", methods=["POST"])
def dj_lines_add():
    category = request.form.get("category", "").strip()
    line = request.form.get("line", "").strip()
    if not category or not line:
        flash("Category and line are required.", "error")
        return redirect(url_for("dj_lines"))
    if category not in LINE_CATEGORIES:
        flash(f"Invalid category: {category}", "error")
        return redirect(url_for("dj_lines"))
    success = add_line(category, line)
    if success:
        flash(
            f'Added to {CATEGORY_LABELS.get(category, category)}: "{line}"', "success"
        )
    else:
        flash("Failed to add line.", "error")
    return redirect(url_for("dj_lines"))


@app.route("/dj-lines/remove", methods=["POST"])
def dj_lines_remove():
    category = request.form.get("category", "").strip()
    index = request.form.get("index", "").strip()
    try:
        index = int(index)
    except ValueError:
        flash("Invalid index.", "error")
        return redirect(url_for("dj_lines"))
    success = remove_line(category, index)
    if success:
        flash(
            f"Removed line from {CATEGORY_LABELS.get(category, category)}.", "success"
        )
    else:
        flash("Failed to remove line. Check the index.", "error")
    return redirect(url_for("dj_lines"))


# ── Soundboard Page ─────────────────────────────────────────────────


@app.route("/soundboard")
def soundboard():
    """Soundboard management page."""
    from utils.soundboard import list_sounds

    # Pass guilds that the bot is in (so JS knows which guild to play sounds in)
    guilds_data = []
    if bot and bot.guilds:
        for guild in bot.guilds:
            voice = guild.voice_client
            guilds_data.append(
                {
                    "id": guild.id,
                    "name": guild.name,
                    "in_voice": voice is not None,
                }
            )

    return render_template(
        "soundboard.html",
        sounds=list_sounds(),
        guilds=guilds_data,
    )


@app.route("/api/ollama/status")
def api_ollama_status():
    """Check Ollama server availability and return status info."""
    try:
        from utils.llm_dj import OLLAMA_DJ_AVAILABLE, check_ollama_available
    except ImportError:
        return jsonify(
            {
                "available": False,
                "model": getattr(config, "OLLAMA_MODEL", "gemma4:latest"),
                "models": [],
                "enabled": getattr(config, "OLLAMA_DJ_ENABLED", False),
                "error": "llm_dj module not found",
            }
        )

    # Run the async check in a thread-safe way
    if bot and bot.loop:
        future = asyncio.run_coroutine_threadsafe(check_ollama_available(), bot.loop)
        try:
            result = future.result(timeout=5)
        except Exception as e:
            result = {
                "available": False,
                "model": getattr(config, "OLLAMA_MODEL", "gemma4:latest"),
                "models": [],
                "error": f"Check timed out: {e}",
            }
    else:
        result = {
            "available": False,
            "model": getattr(config, "OLLAMA_MODEL", "gemma4:latest"),
            "models": [],
            "error": "Bot not connected",
        }

    result["enabled"] = getattr(config, "OLLAMA_DJ_ENABLED", False)
    return jsonify(result)


# ── Settings Page ────────────────────────────────────────────────────


@app.route("/settings")
def settings_page():
    """Settings page — restart, shutdown, and system info."""
    bot_user = str(bot.user) if bot and bot.user else "Not connected"
    bot_avatar = bot.user.display_avatar.url if bot and bot.user else None
    guild_count = len(bot.guilds) if bot else 0

    import platform

    mem_mb = 0
    cpu_pct = 0
    try:
        import psutil

        proc = psutil.Process()
        mem_mb = proc.memory_info().rss / (1024 * 1024)
        cpu_pct = proc.cpu_percent(interval=0.1)
    except ImportError:
        pass

    from utils.dj import EDGE_TTS_AVAILABLE

    return render_template(
        "settings.html",
        bot_user=bot_user,
        bot_avatar=bot_avatar,
        guild_count=guild_count,
        python_version=platform.python_version(),
        platform_info=platform.platform(),
        mem_mb=mem_mb,
        cpu_pct=cpu_pct,
        auto_refresh=False,
        tts_mode=getattr(config, "TTS_MODE", "moss"),
        moss_tts_url=getattr(config, "MOSS_TTS_URL", "http://localhost:18083"),
        vibevoice_tts_url=getattr(config, "VIBEVOICE_TTS_URL", "http://localhost:3000"),
        edge_tts_available=EDGE_TTS_AVAILABLE,
        reverse_proxy=getattr(config, "REVERSE_PROXY", False),
        trusted_proxy_count=getattr(config, "TRUSTED_PROXY_COUNT", 1),
        web_host=getattr(config, "WEB_HOST", "0.0.0.0"),
        web_port=getattr(config, "WEB_PORT", 8080),
    )


@app.route("/api/restart", methods=["POST"])
def api_restart():
    """Restart the bot process."""
    logging.info("Dashboard: Restart requested via Settings page")

    def _do_restart():
        import time as _time

        _time.sleep(1)
        os.execv(sys.executable, [sys.executable] + sys.argv)

    import threading

    threading.Thread(target=_do_restart, daemon=True).start()
    return jsonify({"ok": True, "message": "Restarting..."})


@app.route("/api/shutdown", methods=["POST"])
def api_shutdown():
    """Shut down the bot process."""
    logging.info("Dashboard: Shutdown requested via Settings page")

    def _do_shutdown():
        import time as _time

        _time.sleep(1)
        os.kill(os.getpid(), signal.SIGTERM)

    import threading

    threading.Thread(target=_do_shutdown, daemon=True).start()
    return jsonify({"ok": True, "message": "Shutting down..."})


@app.route("/api/reverse-proxy", methods=["POST"])
def api_toggle_reverse_proxy():
    """Toggle REVERSE_PROXY in the .env file.

    Writes the new value to .env and returns the updated state.
    Requires a bot restart for ProxyFix middleware to take effect.
    """
    data = request.get_json(silent=True) or {}
    enabled = data.get("enabled", False)

    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if not os.path.exists(env_path):
        return jsonify({"ok": False, "error": ".env file not found"}), 404

    try:
        with open(env_path, "r") as f:
            lines = f.readlines()

        found = False
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("REVERSE_PROXY="):
                new_lines.append(f"REVERSE_PROXY={'true' if enabled else 'false'}\n")
                found = True
            else:
                new_lines.append(line)

        if not found:
            # Add at the end of the Web Dashboard section
            new_lines.append("\n# Reverse Proxy Support\n")
            new_lines.append(f"REVERSE_PROXY={'true' if enabled else 'false'}\n")

        with open(env_path, "w") as f:
            f.writelines(new_lines)

        # Update config in-memory so the status reflects immediately
        config.REVERSE_PROXY = enabled

        status = "enabled" if enabled else "disabled"
        logging.info(
            f"Dashboard: Reverse proxy support {status} "
            f"(restart required for ProxyFix middleware to apply)"
        )

        return jsonify(
            {
                "ok": True,
                "enabled": enabled,
                "message": f"Reverse proxy support {status}. "
                "Restart the bot for the change to take full effect.",
                "restart_required": True,
            }
        )
    except Exception as e:
        logging.error(f"Dashboard: Failed to toggle reverse proxy: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Soundboard API ─────────────────────────────────────────────────


@app.route("/api/sounds")
def api_sounds():
    """List available soundboard sounds."""
    from utils.soundboard import list_sounds

    sounds = list_sounds()
    return jsonify({"sounds": sounds})


@app.route("/api/sounds/upload", methods=["POST"])
def api_sounds_upload():
    """Upload a sound file to the soundboard."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No filename"}), 400

    # Only allow audio extensions
    allowed_ext = {".mp3", ".wav", ".ogg", ".flac"}
    import os

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_ext:
        return jsonify(
            {
                "error": f"File type '{ext}' not allowed. Use {', '.join(sorted(allowed_ext))}"
            }
        ), 400

    # Sanitize filename
    safe_name = os.path.basename(file.filename)
    # Replace spaces/unsafe chars
    safe_name = "".join(c if c.isalnum() or c in " -_." else "_" for c in safe_name)
    if not safe_name:
        safe_name = f"sound{ext}"

    from utils.soundboard import SOUNDS_DIR

    os.makedirs(SOUNDS_DIR, exist_ok=True)
    filepath = os.path.join(SOUNDS_DIR, safe_name)

    # Don't overwrite existing files — append number if needed
    if os.path.exists(filepath):
        base = os.path.splitext(safe_name)[0]
        n = 1
        while os.path.exists(os.path.join(SOUNDS_DIR, f"{base}_{n}{ext}")):
            n += 1
        safe_name = f"{base}_{n}{ext}"
        filepath = os.path.join(SOUNDS_DIR, safe_name)

    try:
        file.save(filepath)
        logging.info(
            f"Soundboard: Uploaded {safe_name} ({os.path.getsize(filepath)} bytes)"
        )
        name = (
            os.path.splitext(safe_name)[0].replace("_", " ").replace("-", " ").title()
        )
        return jsonify({"ok": True, "id": safe_name, "name": name})
    except Exception as e:
        logging.error(f"Soundboard: Upload failed: {e}")
        return jsonify({"error": "Upload failed"}), 500


@app.route("/api/sounds/delete", methods=["POST"])
def api_sounds_delete():
    """Delete a sound from the soundboard."""
    data = request.json or request.form
    sound_id = data.get("sound", "").strip()
    if not sound_id:
        return jsonify({"error": "Sound ID required"}), 400

    from utils.soundboard import get_sound_path

    path = get_sound_path(sound_id)
    if not path:
        return jsonify({"error": f"Sound '{sound_id}' not found"}), 404

    try:
        os.remove(path)
        logging.info(f"Soundboard: Deleted {sound_id}")
        return jsonify({"ok": True})
    except Exception as e:
        logging.error(f"Soundboard: Delete failed: {e}")
        return jsonify({"error": "Delete failed"}), 500


@app.route("/api/<int:guild_id>/soundboard", methods=["POST"])
def api_soundboard(guild_id):
    """Play a sound effect in a guild's voice channel.

    Sounds are capped at MAX_SOUND_SECONDS (default 8s) to prevent long
    effects from blocking subsequent audio. DJ line sounds can go up to 10s.
    """
    data = request.json or request.form
    sound_id = data.get("sound", "").strip()
    if not sound_id:
        return jsonify({"error": "Sound ID required"}), 400

    from utils.soundboard import get_sound_path

    path = get_sound_path(sound_id)
    if not path:
        return jsonify({"error": f"Sound '{sound_id}' not found"}), 404

    guild = bot.get_guild(guild_id) if bot else None
    if not guild or not guild.voice_client:
        return jsonify({"error": "Bot not in voice"}), 400

    import discord

    async def _play_sound():
        """Play the sound effect on the bot's event loop thread.

        discord.py voice_client.play() is synchronous and must be called
        from the bot's event loop thread to avoid thread-safety issues.
        If something is already playing, stop it first.
        """
        try:
            # Stop any currently playing audio before playing the new sound.
            # Without this, discord.py raises "already playing audio".
            if guild.voice_client.is_playing():
                guild.voice_client.stop()
                await asyncio.sleep(0.15)  # Brief pause for stop to take effect

            source = discord.FFmpegPCMAudio(
                path,
                before_options="-nostdin",
                options=f"-vn -t {getattr(config, 'MAX_SOUND_SECONDS', 8)}",  # Soft cap
            )
            guild.voice_client.play(source)
            return True
        except Exception as e:
            logging.error(f"Soundboard: {e}")
            return False

    result = _run_async(_play_sound())

    if result:
        return jsonify({"ok": True})
    return jsonify({"error": "Failed to play sound"}), 500


# ── Recently Played & Auto-DJ & Listeners ────────────────────────────


@app.route("/api/<int:guild_id>/history")
def api_history(guild_id):
    """Get recently played history."""
    music = _get_music_cog()
    if not music:
        return jsonify({"error": "Music cog not loaded"}), 503
    history = music.recently_played.get(guild_id, [])
    return jsonify({"history": history[:30]})


@app.route("/api/<int:guild_id>/history/replay/<int:index>", methods=["POST"])
def api_history_replay(guild_id, index):
    """Re-add a track from history back to the queue."""
    music = _get_music_cog()
    if not music:
        return jsonify({"error": "Music cog not loaded"}), 503
    history = music.recently_played.get(guild_id, [])
    if index < 0 or index >= len(history):
        return jsonify({"error": "Invalid index"}), 400
    entry = history[index]
    url = entry.get("url")
    if not url:
        return jsonify({"error": "No URL for this track"}), 400

    async def _replay():
        queue = await music.get_queue(guild_id)
        from cogs.youtube import PlaceholderTrack

        pt_data = {
            "id": url.split("v=")[-1].split("&")[0] if "v=" in url else "",
            "title": entry.get("title", "Unknown"),
            "url": url,
            "ie_key": "Youtube",
        }
        await queue.put(PlaceholderTrack(pt_data))

        # Start playback if nothing is playing
        guild = bot.get_guild(guild_id)
        if (
            guild
            and guild.voice_client
            and not guild.voice_client.is_playing()
            and not guild.voice_client.is_paused()
        ):

            class WebCtx:
                pass

            ctx = WebCtx()
            ctx.guild = guild
            ctx.voice_client = guild.voice_client
            ctx.channel = guild.text_channels[0] if guild.text_channels else None
            ctx.author = guild.me
            if guild_id in music.inactivity_timers:
                music.inactivity_timers[guild_id].cancel()
                del music.inactivity_timers[guild_id]
            await music.play_next(ctx)

    _run_async(_replay())
    return jsonify({"ok": True, "title": entry.get("title", "Unknown")})


@app.route("/api/<int:guild_id>/autodj_toggle", methods=["POST"])
def api_autodj_toggle(guild_id):
    """Toggle Auto-DJ mode."""
    music = _get_music_cog()
    if not music:
        return jsonify({"error": "Music cog not loaded"}), 503
    music.autodj_enabled[guild_id] = not music.autodj_enabled.get(guild_id, False)
    return jsonify({"ok": True, "autodj_enabled": music.autodj_enabled[guild_id]})


@app.route("/api/<int:guild_id>/autodj_source", methods=["POST"])
def api_autodj_source(guild_id):
    """Set the Auto-DJ source playlist/preset."""
    music = _get_music_cog()
    if not music:
        return jsonify({"error": "Music cog not loaded"}), 503
    data = request.json or request.form
    source = data.get("source", "").strip()
    music.autodj_source[guild_id] = source
    return jsonify({"ok": True, "source": source})


@app.route("/api/<int:guild_id>/listeners")
def api_listeners(guild_id):
    """Get list of users currently in the bot's voice channel."""
    guild = bot.get_guild(guild_id) if bot else None
    if not guild or not guild.voice_client:
        return jsonify({"listeners": []})
    members = [
        {
            "id": str(m.id),
            "name": m.display_name,
            "avatar": m.display_avatar.url if m.avatar else None,
        }
        for m in guild.voice_client.channel.members
        if not m.bot
    ]
    return jsonify({"listeners": members})


# ── Queue Reorder & Play Next ──────────────────────────────────────


@app.route("/api/<int:guild_id>/queue/clear", methods=["POST"])
def api_queue_clear(guild_id):
    """Clear the entire queue."""
    music = _get_music_cog()
    if not music:
        return jsonify({"error": "Music cog not loaded"}), 503
    queue = asyncio.run_coroutine_threadsafe(
        music.get_queue(guild_id), bot.loop
    ).result(timeout=5)
    size = queue.qsize()
    # Drain the queue
    while not queue.empty():
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            break
    return jsonify({"ok": True, "cleared": size})


@app.route("/api/<int:guild_id>/queue/reorder", methods=["POST"])
def api_queue_reorder(guild_id):
    """Reorder the queue. Expects JSON: {"order": [2, 0, 1, 3, ...]} (old indices)."""
    music = _get_music_cog()
    if not music:
        return jsonify({"error": "Music cog not loaded"}), 503
    data = request.json or {}
    order = data.get("order", [])
    if not order or not isinstance(order, list):
        return jsonify({"error": "order array required"}), 400

    async def _reorder():
        q = await music.get_queue(guild_id)
        size = q.qsize()
        if len(order) != size:
            return False
        # Drain all items
        items = []
        while not q.empty():
            items.append(await q.get())
        # Reorder
        try:
            reordered = [items[i] for i in order]
        except (IndexError, TypeError):
            # Put items back in original order on failure
            for item in items:
                await q.put(item)
            return False
        # Put back in new order
        for item in reordered:
            await q.put(item)
        return True

    result = _run_async(_reorder())
    if result:
        return jsonify({"ok": True})
    return jsonify({"error": "Reorder failed"}), 400


@app.route("/api/<int:guild_id>/queue/play_next/<int:index>", methods=["POST"])
def api_queue_play_next(guild_id, index):
    """Move a queue item to position 0 (next to be played)."""
    music = _get_music_cog()
    if not music:
        return jsonify({"error": "Music cog not loaded"}), 503

    async def _play_next():
        q = await music.get_queue(guild_id)
        size = q.qsize()
        if index < 0 or index >= size:
            return False
        items = []
        while not q.empty():
            items.append(await q.get())
        # Move item at index to position 0
        item = items.pop(index)
        items.insert(0, item)
        for item in items:
            await q.put(item)
        return True

    result = _run_async(_play_next())
    if result:
        return jsonify({"ok": True})
    return jsonify({"error": "Invalid index"}), 400


# ── Lyrics ──────────────────────────────────────────────────────────


@app.route("/api/<int:guild_id>/lyrics")
def api_lyrics(guild_id):
    """Fetch lyrics for the currently playing song."""
    music = _get_music_cog()
    if not music:
        return jsonify({"error": "Music cog not loaded"}), 503
    current = music.current_song.get(guild_id)
    if not current:
        return jsonify({"lyrics": None, "title": None})

    from utils.lyrics import get_lyrics

    lyrics = _run_async(get_lyrics(current.title))
    return jsonify({"lyrics": lyrics, "title": current.title})


# ── Activity Log (SSE) ──────────────────────────────────────────────


@app.route("/api/logs/recent")
def api_logs_recent():
    """Return the last N log entries from the in-memory ring buffer."""
    from utils.discord_log_handler import log_buffer

    count = request.args.get("count", 100, type=int)
    count = min(count, 200)
    entries = list(log_buffer)[-count:]
    return jsonify({"entries": entries, "total": len(log_buffer)})


@app.route("/api/logs/stream")
def api_logs_stream():
    """Server-Sent Events endpoint for real-time log streaming."""
    from utils.discord_log_handler import log_buffer

    def generate():
        import json
        import time as _time

        last_index = len(log_buffer)
        idle_count = 0

        while True:
            current_len = len(log_buffer)
            if current_len > last_index:
                # New entries available — send them
                # Convert deque to list to slice by index
                all_entries = list(log_buffer)
                new_entries = all_entries[last_index:]
                for entry in new_entries:
                    yield f"data: {json.dumps(entry)}\n\n"
                last_index = current_len
                idle_count = 0
            else:
                idle_count += 1

            # Send a heartbeat every ~5 seconds to keep the connection alive
            if idle_count >= 10:
                yield ": heartbeat\n\n"
                idle_count = 0

            _time.sleep(0.5)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── Presets (Save/Load Playlists) ─────────────────────────────────


@app.route("/api/presets")
def api_presets_list():
    """List all saved presets."""
    from utils.presets import list_presets

    return jsonify({"presets": list_presets()})


@app.route("/api/<int:guild_id>/presets/save", methods=["POST"])
def api_presets_save(guild_id):
    """Save the current queue as a preset."""
    music = _get_music_cog()
    if not music:
        return jsonify({"error": "Music cog not loaded"}), 503
    data = request.json or request.form
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Preset name required"}), 400

    async def _save():
        q = music.song_queues.get(guild_id)
        if not q or q.empty():
            return "empty"
        from utils.presets import save_preset, queue_to_tracks

        tracks = queue_to_tracks(q)
        return save_preset(name, tracks)

    result = _run_async(_save())
    if result == "empty":
        return jsonify({"error": "Queue is empty"}), 400
    if result:
        return jsonify({"ok": True})
    return jsonify({"error": "Save failed"}), 500


@app.route("/api/<int:guild_id>/presets/load", methods=["POST"])
def api_presets_load(guild_id):
    """Load a preset into the queue."""
    music = _get_music_cog()
    if not music:
        return jsonify({"error": "Music cog not loaded"}), 503
    data = request.json or request.form
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Preset name required"}), 400

    from utils.presets import load_preset

    tracks = load_preset(name)
    if not tracks:
        return jsonify({"error": f"Preset '{name}' not found"}), 404

    async def _load():
        queue = await music.get_queue(guild_id)
        from cogs.youtube import PlaceholderTrack

        count = 0
        for t in tracks:
            url = t.get("webpage_url") or t.get("url")
            if url:
                # Build a PlaceholderTrack-compatible dict
                entry = {
                    "id": url.split("v=")[-1].split("&")[0] if "v=" in url else "",
                    "title": t.get("title", "Unknown"),
                    "url": url,
                    "ie_key": "Youtube",
                    "duration": t.get("duration"),
                    "thumbnail": t.get("thumbnail"),
                }
                pt = PlaceholderTrack(entry)
                await queue.put(pt)
                count += 1
        return count

    result = _run_async(_load())
    return jsonify({"ok": True, "result": f"Loaded {result} tracks"})


@app.route("/api/presets/delete", methods=["POST"])
def api_presets_delete():
    """Delete a saved preset."""
    data = request.json or request.form
    name = data.get("name", "").strip()
    if not name:
        return jsonify({"error": "Preset name required"}), 400
    from utils.presets import delete_preset

    if delete_preset(name):
        return jsonify({"ok": True})
    return jsonify({"error": f"Preset '{name}' not found"}), 404


# ── Helpers ───────────────────────────────────────────────────────────


def _get_builtin_lines(category: str) -> list:
    """Return the built-in lines for a category (hardcoded in dj.py)."""
    from utils.dj import (
        INTROS,
        HYPE_INTROS,
        HYPE_INTROS_LOUD,
        OUTROS,
        TRANSITIONS,
        TRANSITIONS_HYPE,
        TRANSITIONS_MELLOW,
        OUTROS_FINAL,
        STATION_IDS,
        CALLOUTS,
    )

    mapping = {
        "intros": INTROS,
        "hype_intros": HYPE_INTROS,
        "hype_intros_loud": HYPE_INTROS_LOUD,
        "outros": OUTROS,
        "transitions": TRANSITIONS,
        "transitions_hype": TRANSITIONS_HYPE,
        "transitions_mellow": TRANSITIONS_MELLOW,
        "outros_final": OUTROS_FINAL,
        "station_ids": STATION_IDS,
        "callouts": CALLOUTS,
    }
    return list(mapping.get(category, []))
