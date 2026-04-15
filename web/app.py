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

import asyncio
import logging
import re
import time
import urllib.parse

from flask import (
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
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

app = Flask(__name__)
app.secret_key = "mbot-mission-control"
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16MB max upload


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
    """Make the bot's name available in all templates."""
    name = bot.user.name if bot and bot.user else "MBot"
    return {"bot_name": name}


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
                    # Peek at up to 50 items without consuming them
                    try:
                        queue_items = list(q._queue)[:50]
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
                    "dj_voice": music.dj_voice.get(guild_id, "") if music else "",
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
    music.playback_speed[guild_id] = speed
    return jsonify({"ok": True, "speed": speed})


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
    from utils.dj import list_voices, EDGE_TTS_AVAILABLE

    if not EDGE_TTS_AVAILABLE:
        return jsonify({"voices": [], "error": "edge-tts not installed"})
    lang = request.args.get("lang", "en")
    voices = _run_async(list_voices(lang))
    if voices is None:
        voices = []
    return jsonify(
        {
            "voices": [
                {
                    "name": v["ShortName"],
                    "gender": v.get("Gender", "?"),
                    "locale": v.get("Locale", "?"),
                }
                for v in voices
            ]
        }
    )


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
            tracks = await PlaceholderTrack.from_playlist_url(
                query, loop=bot.loop, playlist_items="1-25"
            )
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

    Sounds are capped at 3 seconds to prevent long effects from
    blocking subsequent audio (discord.py raises "already playing"
    if a new source is played while one is still going).
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
                options="-vn -t 3",  # Cap at 3 seconds max
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
