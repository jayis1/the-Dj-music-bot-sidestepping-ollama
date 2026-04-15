"""
web/app.py — Mission Control Dashboard for MBot.

A Flask web app that runs alongside the Discord bot, providing:
- Live dashboard (now playing, queue, bot status)
- DJ line management (add/remove custom lines per category)
- Bot controls (skip, stop, volume, DJ toggle)

The bot instance is passed in at startup so the dashboard can read
and modify bot state directly.
"""

import asyncio
import logging

from flask import (
    Flask,
    flash,
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

# Set by bot.py at startup
bot = None


def init_dashboard(discord_bot):
    """Called from bot.py to inject the running bot instance."""
    global bot
    bot = discord_bot


def _get_music_cog():
    """Return the Music cog from the running bot, or None."""
    if bot is None:
        return None
    return bot.get_cog("Music")


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
            queue_size = 0

            if music:
                current = music.current_song.get(guild_id)
                q = music.song_queues.get(guild_id)
                if q:
                    queue_size = q.qsize()

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
                    "queue_size": queue_size,
                    "dj_enabled": music.dj_enabled.get(guild_id, False)
                    if music
                    else False,
                    "dj_voice": music.dj_voice.get(guild_id, "") if music else "",
                    "volume": int(music.current_volume.get(guild_id, 1.0) * 100)
                    if music
                    else 100,
                    "looping": music.looping.get(guild_id, False) if music else False,
                    "speed": music.playback_speed.get(guild_id, 1.0) if music else 1.0,
                }
            )

    return render_template(
        "dashboard.html",
        guilds=guilds_data,
        bot_user=str(bot.user) if bot else "Not connected",
        bot_avatar=bot.user.display_avatar.url if bot and bot.user else None,
        guild_count=len(bot.guilds) if bot else 0,
    )


# ── DJ Lines ─────────────────────────────────────────────────────


@app.route("/dj-lines")
def dj_lines():
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
    return render_template("dj_lines.html", categories=categories)


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


# ── Helpers ───────────────────────────────────────────────────────


def _get_builtin_lines(category: str) -> list[str]:
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
