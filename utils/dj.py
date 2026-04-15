"""
utils/dj.py — Radio DJ mode for MBot.

Generates Text-to-Speech DJ commentary between songs using Microsoft Edge TTS.
The DJ speaks like a real radio host — with energy, personality, time-aware
greetings, listener callouts, weather-style banter, and natural transitions.

Requires: pip install edge-tts
"""

import asyncio
import logging
import os
import random
import tempfile
from datetime import datetime

import config

try:
    import edge_tts

    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False
    logging.warning(
        "edge-tts not installed — DJ mode unavailable. Install with: pip install edge-tts"
    )


# ── Time-of-day helpers ────────────────────────────────────────────


def _time_of_day() -> str:
    """Return 'morning', 'afternoon', 'evening', or 'night'/'late night'."""
    h = datetime.now().hour
    if 5 <= h < 12:
        return "morning"
    elif 12 <= h < 17:
        return "afternoon"
    elif 17 <= h < 21:
        return "evening"
    else:
        return "late night" if h >= 23 or h < 3 else "night"


def _time_greeting() -> str:
    """Return a random time-aware greeting like 'Good evening everyone'."""
    tod = _time_of_day()
    greetings = {
        "morning": [
            "Good morning, early birds!",
            "Rise and shine, everyone!",
            "Morning, music lovers!",
            "Top of the morning to ya!",
        ],
        "afternoon": [
            "Good afternoon, everyone!",
            "Afternoon, listeners!",
            "Hope your afternoon's going well!",
            "Afternoon! Let's keep the energy up.",
        ],
        "evening": [
            "Good evening, everyone!",
            "Evening, music lovers!",
            "Hope you're having a great evening!",
            "Evening! Perfect time for some tunes.",
        ],
        "night": [
            "Evening, night owls!",
            "Still up? Good. Let's keep going.",
            "Night crew, you're in the right place.",
            "Late night vibes, let's go.",
        ],
        "late night": [
            "Late night crew, I see you!",
            "Burning the midnight oil? I've got you covered.",
            "Late night, right here. Let's keep it mellow.",
            "Can't sleep? Neither can I. Music it is.",
        ],
    }
    return random.choice(greetings.get(tod, ["Hey everyone!"]))


def _queue_banter(queue_size: int) -> str:
    """Return a comment about the remaining queue size."""
    if queue_size == 0:
        return ""
    options = {
        1: [
            "One more left in the queue.",
            "Just one more to go after this.",
            "Last one in the queue coming up.",
        ],
        2: [
            "A couple more lined up after this.",
            "Two more waiting in the wings.",
        ],
    }
    # 3–5
    few = [
        "Got a nice little set going. {} more after this one.",
        "A few more in the queue. {} left to go.",
        "{} more tracks lined up and ready to roll.",
    ]
    # 6–15
    medium = [
        "We've got a solid lineup tonight. {} more tracks to get through.",
        "Plenty more where that came from. {} left in the queue.",
        "The queue is looking healthy — {} more to go.",
        "Don't go anywhere, we've got {} more coming up.",
    ]
    # 16+
    big = [
        "We are in it for the long haul tonight, folks. {} more tracks in the queue!",
        "This is a marathon session. {} songs still to come!",
        "{} more tracks! We are not stopping anytime soon.",
        "Endless music, just the way we like it. {} more to go.",
    ]

    if queue_size in options:
        return random.choice(options[queue_size])
    elif 3 <= queue_size <= 5:
        return random.choice(few).format(queue_size)
    elif 6 <= queue_size <= 15:
        return random.choice(medium).format(queue_size)
    elif queue_size >= 16:
        return random.choice(big).format(queue_size)
    return ""


# ── DJ Message Templates ──────────────────────────────────────────

# Intros — played before the FIRST song of a session
INTROS = [
    "{greeting} Let's kick things off with {title}.",
    "{greeting} Starting things off right. Here's {title}.",
    "{greeting} We're getting started with a banger. {title}.",
    "{greeting} Let's get this party started! First up, {title}.",
    "Alright, {greeting} Here's our first track — {title}.",
    "{greeting} Let me set the mood for you. Starting with {title}.",
    "{greeting} Press play and let's go. Our opening track is {title}.",
    "{greeting} We're opening the show with {title}. Let's get into it.",
    "{greeting} The wait is over. Kicking off with {title}!",
    "{greeting} Let me start you off with something good. {title}.",
    "Here we go, {greeting} Our first song is {title}.",
    "{greeting} You picked a great one to start with. Here's {title}.",
]

# Song-specific hype intros (used instead of intros for 2nd+ songs)
HYPE_INTROS = [
    "Up next, {title}!",
    "Here comes {title}.",
    "Next up — {title}.",
    "And now, {title}.",
    "Let's keep it moving. {title}!",
    "You know this one. {title}!",
    "Oh, this is a good one. Here's {title}.",
    "Time for {title}.",
    "Here it is, {title}.",
    "Let's go! {title}!",
    "Turn it up for {title}!",
    "Alright, here's {title}.",
    "Coming up now, {title}.",
    "Get ready for {title}.",
]

# Enthusiastic intros (randomly picked ~25% of the time for extra energy)
HYPE_INTROS_LOUD = [
    "Oh yeah! It's time for {title}!",
    "This is the one! {title}!",
    "YES! {title}! Let's go!",
    "You already know what it is! {title}!",
    "Here. We. Go. {title}!",
    "This next one goes hard. {title}!",
    "Are you ready for this? {title}!",
    "I've been waiting to play this one! {title}!",
    "Turn this one up loud! {title}!",
]

# Outros — after a song when the next track is UNKNOWN
OUTROS = [
    "That was {title}.",
    "Great track. {title}.",
    "And that was {title}.",
    "{title}. Classic.",
    "Love that one. {title}.",
    "That was {title}. Good stuff.",
    "Mm, {title}. That hit the spot.",
    "And that's {title}. Nicely done.",
]

# Transitions — outro + intro combined (when we know both titles)
TRANSITIONS = [
    "That was {prev_title}. And up next, {next_title}.",
    "Moving on from {prev_title}. Here comes {next_title}!",
    "Loved {prev_title}. Now let's bring you {next_title}.",
    "{prev_title} — what a track. Up next? {next_title}.",
    "Alright, from {prev_title} straight into {next_title}.",
    "That was {prev_title}. Next up, {next_title}.",
    "Finished with {prev_title}. Let's get into {next_title}.",
    "{prev_title}. Nice. Alright, here's {next_title}.",
    "And that's a wrap on {prev_title}. Coming up, {next_title}!",
    "We're going from {prev_title}, right into {next_title}.",
    "{prev_title} is done. But don't touch that dial — {next_title} is next!",
    "That was {prev_title}. We're not slowing down. Here's {next_title}.",
    "Alright, {prev_title} in the books. Up next, we've got {next_title}!",
    "From {prev_title}, to {next_title}. Let's keep this going.",
    "That was {prev_title}. And we have absolutely no time to waste. Here's {next_title}!",
]

# Energetic transitions (~25% chance, replaces regular transition for hype moments)
TRANSITIONS_HYPE = [
    "Oh, we're going from {prev_title} right into {next_title}! Let's go!",
    "That was {prev_title} — and the next one is even better. {next_title}!",
    "YES! {prev_title}! And we're not stopping! {next_title} is next!",
    "Alright! {prev_title} was fire, and {next_title} is about to match that energy!",
    "{prev_title} was incredible. And {next_title}? Oh, just wait!",
]

# Mellow transitions (for late night / chill vibes)
TRANSITIONS_MELLOW = [
    "That was {prev_title}. Taking it easy with {next_title}.",
    "Mm, {prev_title}. Now let's slow it down a bit with {next_title}.",
    "Lovely track, {prev_title}. Here's {next_title} to keep the vibe going.",
    "{prev_title}. Hmm. Now let's ease into {next_title}.",
    "That was {prev_title}. Let's keep the mood going with {next_title}.",
]

# Final outros — when the queue is empty after this song
OUTROS_FINAL = [
    "And that was {title}. That's all for now, but I'm not going anywhere. Just holler when you want more.",
    "That was {title}. The queue's empty, but the radio stays on. I'll be right here.",
    "Well, {title} was our last one. Nothing left in the queue! You know where to find me when you want more.",
    "And that wraps up our set with {title}. The music never really stops around here. Just say the word and we'll go again.",
    "That was {title}. That's the end of the queue, folks. It's been a great session. Come back anytime.",
    "Last one was {title}. We're all out of songs, but hey — that just means you get to pick what's next.",
    "{title}. And... we're out! Empty queue. But don't worry, the DJ's still in the booth. Request something anytime.",
    "That was {title}. And we're done! For now. I'll be here if you need me.",
    "And that's {title}. We've burned through the whole queue! Great session, everyone. Until next time.",
    "{title} — and that's a wrap on tonight's set. The bar's open, the DJ's here, just no more songs. Yet.",
]

# Station IDs — randomly sprinkled in front of intros
STATION_IDS = [
    f"You're tuned in to {config.STATION_NAME} Radio.",
    f"This is {config.STATION_NAME} Radio, your non-stop music station.",
    f"{config.STATION_NAME} Radio — all music, all the time.",
    f"Welcome to {config.STATION_NAME} Radio.",
    f"This is your DJ on {config.STATION_NAME} Radio.",
    f"You're listening to {config.STATION_NAME}. Let's keep it going.",
    f"{config.STATION_NAME} Radio. The only station that never stops.",
    f"This is {config.STATION_NAME}, keeping the music alive, 24 7.",
    f"You're on {config.STATION_NAME} Radio, where the tunes never end.",
    f"From the {config.STATION_NAME} Radio studios, this is your DJ.",
]

# Listener callouts — randomly sprinkled for community feel
CALLOUTS = [
    "Shoutout to everyone listening right now.",
    "Love having you all here tonight.",
    "Glad you're tuning in.",
    "Thanks for rocking with us.",
    "You guys are the best listeners, seriously.",
    "Keep those requests coming, I love it.",
    "Someone's got great taste in music tonight.",
    "I see you in the chat. Let's keep going.",
    "The vibes are immaculate right now.",
    "This crowd never disappoints.",
]


# ── Message Generation ─────────────────────────────────────────────


def _pool(category: str) -> list[str]:
    """Return built-in + custom lines for a category, deduplicated."""
    from utils.custom_lines import load_custom_lines

    builtin = {
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
    }.get(category, [])
    custom = load_custom_lines().get(category, [])
    combined = list(builtin) + custom
    # Deduplicate while preserving order
    seen = set()
    result = []
    for line in combined:
        if line not in seen:
            seen.add(line)
            result.append(line)
    return result


def generate_intro(title: str, queue_size: int = 0) -> str:
    """Generate a DJ intro message before the first song of a session."""
    greeting = _time_greeting()
    msg = random.choice(_pool("intros")).format(greeting=greeting, title=title)

    # 30% chance to prepend a station ID
    if random.random() < 0.30:
        msg = random.choice(_pool("station_ids")) + " " + msg

    return msg


def generate_song_intro(title: str, queue_size: int = 0) -> str:
    """Generate a DJ intro before the 2nd+ song (not the session opener)."""
    tod = _time_of_day()

    # Late night? Go mellow 40% of the time
    if tod in ("night", "late night") and random.random() < 0.40:
        msg = random.choice(_pool("hype_intros")).format(title=title)
    # 20% chance of a loud/hype intro
    elif random.random() < 0.20:
        msg = random.choice(_pool("hype_intros_loud")).format(title=title)
    else:
        msg = random.choice(_pool("hype_intros")).format(title=title)

    # 15% chance to tack on a listener callout
    if random.random() < 0.15:
        msg += " " + random.choice(_pool("callouts"))

    # Add queue banter if songs are lined up
    banter = _queue_banter(queue_size)
    if banter:
        msg += " " + banter

    return msg


def generate_outro(
    title: str, has_next: bool, next_title: str = None, queue_size: int = 0
) -> str:
    """Generate a DJ outro message after a song ends."""
    tod = _time_of_day()

    if has_next and next_title:
        # We know both songs — use a transition
        # Late night? Go mellow sometimes
        if tod in ("night", "late night") and random.random() < 0.35:
            msg = random.choice(_pool("transitions_mellow")).format(
                prev_title=title, next_title=next_title
            )
        # 20% chance of a hype transition
        elif random.random() < 0.20:
            msg = random.choice(_pool("transitions_hype")).format(
                prev_title=title, next_title=next_title
            )
        else:
            msg = random.choice(_pool("transitions")).format(
                prev_title=title, next_title=next_title
            )

        # 25% chance to tack on queue banter
        if queue_size > 0 and random.random() < 0.25:
            banter = _queue_banter(queue_size)
            if banter:
                msg += " " + banter

    elif has_next:
        # Next track exists but we don't know its title
        msg = random.choice(_pool("outros")).format(title=title)
        banter = _queue_banter(queue_size)
        if banter:
            msg += " " + banter

    else:
        # Last song — queue is empty after this
        msg = random.choice(_pool("outros_final")).format(title=title)

    # 20% chance to prepend a station ID on the outro too
    if random.random() < 0.20:
        msg = random.choice(_pool("station_ids")) + " " + msg

    return msg


# ── TTS Generation ─────────────────────────────────────────────────

DEFAULT_VOICE = "en-US-AriaNeural"


async def list_voices(language: str = "en") -> list[dict]:
    """
    Return available TTS voices filtered by language prefix.
    Each entry is a dict with keys: Name, ShortName, Gender, Locale, etc.
    """
    if not EDGE_TTS_AVAILABLE:
        return []
    try:
        voices = await edge_tts.list_voices()
        return [v for v in voices if v["Locale"].startswith(language)]
    except Exception as e:
        logging.error(f"DJ: Failed to list TTS voices: {e}")
        return []


async def generate_tts(text: str, voice: str = DEFAULT_VOICE) -> str | None:
    """
    Generate a TTS audio file and return its path.
    Returns None if edge-tts is unavailable or generation fails.
    **The caller must delete the file after use via cleanup_tts_file().**
    """
    if not EDGE_TTS_AVAILABLE:
        logging.warning("DJ: edge-tts not available, skipping TTS.")
        return None

    if not text or not text.strip():
        return None

    path = None
    try:
        fd, path = tempfile.mkstemp(suffix=".mp3", prefix="dj_")
        os.close(fd)

        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(path)

        logging.info(f"DJ: Generated TTS → {path} ({len(text)} chars, voice={voice})")
        return path
    except Exception as e:
        logging.error(f"DJ: Failed to generate TTS: {e}")
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
        return None


def cleanup_tts_file(path: str):
    """Delete a generated TTS audio file. Safe to call from sync callbacks."""
    if path and os.path.exists(path):
        try:
            os.remove(path)
            logging.debug(f"DJ: Cleaned up TTS file: {path}")
        except Exception as e:
            logging.warning(f"DJ: Failed to clean up {path}: {e}")
