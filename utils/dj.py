"""
utils/dj.py — Radio DJ mode for MBot.

Generates Text-to-Speech DJ commentary between songs using:
- Kokoro-TTS via Kokoro-FastAPI Docker server (default, local, OpenAI-compatible)
- VibeVoice-Realtime (separate WebSocket server, GPU-accelerated)
- Microsoft Edge TTS (cloud fallback)

The DJ speaks like a real radio host — with energy, personality, time-aware
greetings, listener callouts, weather-style banter, and natural transitions.

TTS engine is selected via config.TTS_MODE:
- "kokoro" (default): Kokoro-FastAPI Docker server. Local GPU, ~300ms first audio.
  Start with: docker run --gpus all -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-gpu:latest
  Voice names: af_heart, af_breeze, am_adam, bf_emma, bm_george, etc.
- "vibevoice": Uses a VibeVoice-Realtime WebSocket server on a separate port.
  (The legacy alias "local" also maps to vibevoice.)
- "edge-tts": Microsoft Edge TTS (cloud-based, always-available fallback).
"""

import asyncio
import logging
import os
import random
import re
import tempfile
import wave
from datetime import datetime

import config

try:
    import edge_tts

    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False
    logging.warning(
        "edge-tts not installed — DJ mode will use local TTS or be unavailable. Install with: pip install edge-tts"
    )

try:
    import aiohttp

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    # If aiohttp is missing, Kokoro/VibeVoice HTTP-based TTS won't work either,
    # but we don't block the bot from starting — we just log a warning later.


# ── TTS Engine Detection ─────────────────────────────────────────────

# Resolve TTS mode, handling legacy aliases
_raw_tts_mode = getattr(config, "TTS_MODE", "kokoro").lower()

# Backward compat: "local" was the old name for vibevoice
if _raw_tts_mode == "local":
    logging.warning(
        'DJ: TTS_MODE="local" is deprecated. Use "vibevoice" instead. '
        'Mapping "local" → "vibevoice" for now.'
    )
    _raw_tts_mode = "vibevoice"

KNOWN_TTS_MODES = {"kokoro", "vibevoice", "edge-tts"}
if _raw_tts_mode not in KNOWN_TTS_MODES:
    logging.warning(f"DJ: Unknown TTS_MODE '{_raw_tts_mode}', falling back to kokoro")
    _raw_tts_mode = "kokoro"

# Validate: kokoro and vibevoice both need aiohttp for HTTP calls
if _raw_tts_mode in ("kokoro", "vibevoice") and not AIOHTTP_AVAILABLE:
    logging.warning(
        f"DJ: TTS_MODE={_raw_tts_mode} but aiohttp not installed. "
        "Install with: pip install aiohttp"
    )
    if EDGE_TTS_AVAILABLE:
        logging.warning("DJ: Falling back to edge-tts")
        _raw_tts_mode = "edge-tts"
    else:
        logging.error("DJ: No TTS engine available! DJ mode will not work.")

TTS_MODE = _raw_tts_mode

# Is ANY TTS engine available? This is True when at least one engine is
# configured and its dependencies are installed. Used by cogs/music.py
# to gate DJ mode — DJ should work with any engine, not just edge-tts.
TTS_AVAILABLE = (
    (TTS_MODE in ("kokoro", "vibevoice") and AIOHTTP_AVAILABLE)
    or (TTS_MODE == "edge-tts" and EDGE_TTS_AVAILABLE)
    or EDGE_TTS_AVAILABLE  # edge-tts is always a fallback
)

# Resolve server URLs from config
# New dedicated URLs take priority; LOCAL_TTS_URL is a backward-compat fallback
KOKORO_TTS_URL = getattr(config, "KOKORO_TTS_URL", "") or getattr(
    config, "LOCAL_TTS_URL", "http://localhost:8880"
)
# If KOKORO_TTS_URL wasn't set and LOCAL_TTS_URL was the old default (port 3000),
# override to the Kokoro default port (8880)
if not getattr(config, "KOKORO_TTS_URL", "") and not getattr(
    config, "LOCAL_TTS_URL", ""
):
    KOKORO_TTS_URL = "http://localhost:8880"

VIBEVOICE_TTS_URL = getattr(config, "VIBEVOICE_TTS_URL", "") or getattr(
    config, "LOCAL_TTS_URL", "http://localhost:3000"
)

# Log the active TTS configuration at startup
if TTS_MODE == "kokoro":
    logging.info(f"DJ: Using Kokoro TTS server at {KOKORO_TTS_URL}")
elif TTS_MODE == "vibevoice":
    logging.info(f"DJ: Using VibeVoice TTS server at {VIBEVOICE_TTS_URL}")
elif TTS_MODE == "edge-tts":
    logging.info("DJ: Using Microsoft Edge TTS (cloud)")


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
    "{greeting} We're live and we're starting with {title}.",
    "{greeting} First track of the session. {title}.",
    "{greeting} Let's not waste any time. {title} to start us off.",
    # ── With sound tags ──
    "{greeting} We are LIVE! Let's kick it off with {title}. {sound:airhorn}",
    "{greeting} The station is on the air! First up, {title}. {sound:dj_turn_it_up}",
    "{greeting} Showtime, baby! Starting with {title}! {sound:combo_hit}",
    "{greeting} Let's get this session rolling. Here's {title}. {sound:dj_scratch}",
    "{greeting} Good vibes only. Kicking off with {title}. {sound:dj_stop}",
    "{greeting} You tuned in at the right time. {title} to start us off! {sound:airhorn}",
    "{greeting} The DJ is in the house! Opening with {title}. {sound:dj_turn_it_up}",
    "{greeting} Ladies and gentlemen, let's begin with {title}! {sound:rave_cheer}",
    "{greeting} Radio is live and we're starting strong. {title}! {sound:air_raid}",
    "{greeting} This is your captain speaking. Taking off with {title}. {sound:dj_scratch}",
    "{greeting} Music, music, music! First track — {title}. {sound:mustard_drop}",
    "{greeting} And we're back! Starting the session with {title}. {sound:another_one}",
    "{greeting} The one and only {title} to open the show! {sound:mega_airhorn}",
    "Welcome to the show! I'm your DJ and this is {title}. {sound:im_your_dj}",
    "{greeting} Let me hear you make some noise for {title}! {sound:rave_cheer}",
    "{greeting} Dropping the needle on {title}. {sound:sick_scratch}",
    "{greeting} Rewind! Let's start from the top with {title}. {sound:dj_rewind}",
    "{greeting} This one goes out to everyone listening. {title}! {sound:cool_dj_drop}",
    "{greeting} Big tune alert! Starting with {title}. {sound:uyuuui}",
    "{greeting} Let the Django drop! Opening with {title}. {sound:django}",
    "{greeting} Sound system activated. First track: {title}. {sound:dj_scratch}",
    "{greeting} Are you ready? I said, ARE YOU READY? {title}! {sound:mega_airhorn}",
    "{greeting} We're turning it up to eleven. {title} to start! {sound:dj_turn_it_up}",
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
    "And the next one is {title}.",
    "Here's what's next — {title}.",
    "Moving right along to {title}.",
    # ── With sound tags ──
    "{title} is next! {sound:mustard_drop}",
    "Incoming! {title}! {sound:airhorn}",
    "Next up, {title}. {sound:dj_scratch}",
    "Watch this. {title}! {sound:combo_hit}",
    "Here comes {title}. {sound:dj_stop}",
    "Let's rewind it back. {title}! {sound:dj_rewind}",
    "Another one! {title}! {sound:another_one}",
    "{title} coming atcha! {sound:sick_scratch}",
    "Dropping {title} right now! {sound:cool_dj_drop}",
    "Make some noise for {title}! {sound:rave_cheer}",
    "Big tune alert: {title}! {sound:mega_airhorn}",
    "Here's {title}! {sound:uyuuui}",
    "I'm your DJ and this is {title}! {sound:im_your_dj}",
    "The Django selects {title}! {sound:django}",
    "Turntables are spinning for {title}. {sound:dj_turn_it_up}",
    "{title} — you already know! {sound:uyuuui}",
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
    # ── With sound tags ──
    "THIS is the one! {title}! {sound:airhorn} Let's go!",
    "Are you ready?! {title}! {sound:air_raid}",
    "Oh yeah! Let me hear it for {title}! {sound:rave_cheer}",
    "THIS IS IT! {title}! {sound:combo_hit}",
    "Buckle up! {title} is about to blow your mind! {sound:dj_turn_it_up}",
    "Turn it ALL the way up! {title}! {sound:airhorn}",
    "The moment you've been waiting for! {title}! {sound:rave_cheer}",
    "DJ drop incoming! It's {title}! {sound:dj_turn_it_up}",
    "Loud and proud! {title}! {sound:airhorn}",
    "MEGA tune incoming! {title}! {sound:mega_airhorn}",
    "We're going crazy for {title}! {sound:rave_cheer}",
    "Rewind that! {title} is too good! {sound:dj_rewind}",
    "Another absolute banger — {title}! {sound:another_one}",
    "The crowd goes WILD for {title}! {sound:rave_cheer}",
    "This one's gonna tear the roof off! {title}! {sound:cool_dj_drop}",
    "SICK DROP ALERT! {title}! {sound:sick_scratch}",
    "I am YOUR DJ and I say we play {title}! {sound:im_your_dj}",
    "Maximum volume! {title}! {sound:mega_airhorn}",
    "Unhinged mode ACTIVATED! {title}! {sound:uyuuui}",
    "Django UNCHAINED! {title}! {sound:django}",
    "Let the DJ turn it up for {title}! {sound:dj_turn_it_up}",
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
    "Beautiful. {title}.",
    "{title} — what a ride.",
    # ── With sound tags ──
    "That was {title}. {sound:rave_cheer}",
    "And that's a wrap on {title}. {sound:record_scratch}",
    "{title}. What a track! {sound:rave_cheer}",
    "And that was {title}. Not bad, right? {sound:mustard_drop}",
    "{title} — done and dusted. {sound:uyuuui}",
    "{title}. I'll let that one sink in. {sound:cool_dj_drop}",
    "That was {title}. Give it up! {sound:rave_cheer}",
    "Rewind moment! What a tune — {title}. {sound:dj_rewind}",
    "{title}. Django-approved. {sound:django}",
    "And that was {title}. {sound:sick_scratch}",
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
    "{prev_title} is over. {next_title} is next. Let's go.",
    "That was {prev_title}. Changing gears to {next_title}.",
    # ── With sound tags ──
    "That was {prev_title}. Now, {next_title}! {sound:dj_scratch}",
    "{prev_title}? Great stuff. But wait — {next_title} is next! {sound:airhorn}",
    "From {prev_title} to {next_title}. In the mix! {sound:dj_stop}",
    "Alright, switching gears. {prev_title} done, here's {next_title}. {sound:mustard_drop}",
    "That was {prev_title}. And coming up next, {next_title}! {sound:combo_hit}",
    "{prev_title} is over. But {next_title} is just getting started! {sound:dj_turn_it_up}",
    "Moving right along! {prev_title} → {next_title}! {sound:dj_scratch}",
    "And that was {prev_title}. Hold on — {next_title} is up! {sound:airhorn}",
    "Rewind! {prev_title} was that good. Now {next_title}! {sound:dj_rewind}",
    "Another one! From {prev_title} to {next_title}! {sound:another_one}",
    "{prev_title} → {next_title}. Smooth transition! {sound:cool_dj_drop}",
    "The DJ selects {next_title} after that {prev_title}! {sound:im_your_dj}",
    "Going from {prev_title} right into {next_title}! {sound:dj_turn_it_up}",
    "{prev_title}, and now {next_title}. The Django demands it. {sound:django}",
    "Switching it up! {prev_title} done, {next_title} coming! {sound:sick_scratch}",
    "Make some noise! {prev_title} → {next_title}! {sound:rave_cheer}",
    "{prev_title} out. {next_title} in. Let's go! {sound:mega_airhorn}",
    "That was {prev_title}. But {next_title} is something special. {sound:uyuuui}",
    "The crowd wants {next_title}! After {prev_title}! {sound:rave_cheer}",
]

# Energetic transitions (~25% chance, replaces regular transition for hype moments)
TRANSITIONS_HYPE = [
    "Oh, we're going from {prev_title} right into {next_title}! Let's go!",
    "That was {prev_title} — and the next one is even better. {next_title}!",
    "YES! {prev_title}! And we're not stopping! {next_title} is next!",
    "Alright! {prev_title} was fire, and {next_title} is about to match that energy!",
    "{prev_title} was incredible. And {next_title}? Oh, just wait!",
    # ── With sound tags ──
    "That was {prev_title}! And NOW — {next_title}! {sound:airhorn} LET'S GO!",
    "{prev_title} was fire! But {next_title}? EVEN HOTTER! {sound:air_raid}",
    "YES! {prev_title}! And we keep going with {next_title}! {sound:combo_hit}",
    "Going from {prev_title} straight into {next_title}! {sound:airhorn} No brakes!",
    "{prev_title} was insane! And {next_title} is about to blow the roof off! {sound:rave_cheer}",
    "That was {prev_title}! Now brace yourself for {next_title}! {sound:dj_turn_it_up}",
    "Double trouble! {prev_title} done, {next_title} incoming! {sound:airhorn}",
]

# Mellow transitions (for late night / chill vibes)
TRANSITIONS_MELLOW = [
    "That was {prev_title}. Taking it easy with {next_title}.",
    "Mm, {prev_title}. Now let's slow it down a bit with {next_title}.",
    "Lovely track, {prev_title}. Here's {next_title} to keep the vibe going.",
    "{prev_title}. Hmm. Now let's ease into {next_title}.",
    "That was {prev_title}. Let's keep the mood going with {next_title}.",
    # ── With sound tags ──
    "That was {prev_title}. Now ease into {next_title}. {sound:dj_scratch}",
    "{prev_title} was beautiful. And {next_title} keeps the vibe alive. {sound:dj_stop}",
    "Mmm, {prev_title}. Smooth transition to {next_title}. {sound:mustard_drop}",
    "Vibing. {prev_title} → {next_title}. {sound:dj_scratch}",
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
    # ── With sound tags ──
    "That was {title}. And that's the show! {sound:rave_cheer} Until next time.",
    "{title} — and we're done! {sound:record_scratch} But the DJ's still in the booth!",
    "And that wraps it up with {title}! {sound:rave_cheer} Great session, everyone.",
    "End of the road with {title}. The station never sleeps though. {sound:dj_turn_it_up}",
    "That was {title}. Queue's empty! But I'll be right here. {sound:mustard_drop} Request anytime.",
    "{title} — final track! {sound:airhorn} What a session, everyone!",
    "And that's {title}. Show's over! {sound:rave_cheer} But you know where to find me.",
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
    # ── With sound tags ──
    f"You're tuned in to {config.STATION_NAME} Radio. {{sound:dj_turn_it_up}}",
    f"This is {config.STATION_NAME} Radio! {{sound:airhorn}} Your non-stop music station.",
    f"{config.STATION_NAME} Radio — on the air! {{sound:air_raid}}",
    f"Welcome to {config.STATION_NAME} Radio. {{sound:dj_turn_it_up}}",
    f"{config.STATION_NAME} Radio. All music. All the time. {{sound:combo_hit}}",
    f"You're listening to {config.STATION_NAME}. {{sound:dj_stop}}",
    f"This is {config.STATION_NAME} Radio! {{sound:mustard_drop}} Let's keep it going.",
    f"From the {config.STATION_NAME} Radio studios — we're live! {{sound:dj_turn_it_up}}",
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
    # ── With sound tags ──
    "Shoutout to everyone listening right now! {sound:rave_cheer}",
    "You guys are the best! {sound:rave_cheer} Seriously.",
    "Thanks for rocking with us! {sound:airhorn}",
    "Love having you all here! {sound:dj_stop}",
    "Glad you're tuning in! {sound:mustard_drop}",
    "Keep those requests coming! {sound:airhorn} I love it!",
    "This crowd never disappoints! {sound:rave_cheer}",
    "Someone's got great taste tonight! {sound:combo_hit}",
    "Vibes are immaculate! {sound:dj_turn_it_up}",
    "I see you out there! {sound:rave_cheer} Let's keep going!",
]


# ── Message Generation ─────────────────────────────────────────────


def _format_line(template: str, **kwargs) -> str:
    """Format a DJ line template, handling {sound:name} tags safely.

    Python's str.format() treats {sound:name} as a format field and
    raises KeyError. We extract sound tags first, format the rest,
    then re-append the sound tags at the end.
    """
    tags = re.findall(r"\{sound:[^}]+\}", template)
    # Remove sound tags so .format() doesn't choke on them
    cleaned = re.sub(r"\s*\{sound:[^}]+\}\s*", " ", template).strip()
    try:
        result = cleaned.format(**kwargs)
    except KeyError:
        # Fallback: if any weird placeholder remains, just use it as-is
        result = cleaned
    # Re-append sound tags at the end
    if tags:
        # Normalize tags to have single spaces
        tag_str = " ".join(tags)
        result = result.rstrip() + " " + tag_str
    return result


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


def extract_sound_tags(text: str) -> tuple[str, list[str]]:
    """
    Extract {sound:name} tags from a DJ line.
    Returns (cleaned_text, [sound_ids]).
    e.g. "In the mix! {sound:airhorn} {sound:combo_hit}" → ("In the mix!", ["airhorn", "combo_hit"])
    """
    tags = re.findall(r"\{sound:([^}]+)\}", text)
    cleaned = re.sub(r"\s*\{sound:[^}]+\}\s*", " ", text).strip()
    # Build the sound_id with the right extension
    from utils.soundboard import list_sounds

    available = {s["id"]: s["id"] for s in list_sounds()}
    resolved = []
    for tag in tags:
        # Try exact match first (e.g. "airhorn" matches "airhorn.wav")
        for sid in available:
            base = os.path.splitext(sid)[0]
            if base.lower() == tag.lower():
                resolved.append(sid)
                break
    return cleaned, resolved


def generate_intro(title: str, queue_size: int = 0) -> str:
    """Generate a DJ intro message before the first song of a session."""
    greeting = _time_greeting()
    msg = _format_line(random.choice(_pool("intros")), greeting=greeting, title=title)

    # 30% chance to prepend a station ID
    if random.random() < 0.30:
        msg = _format_line(random.choice(_pool("station_ids"))) + " " + msg

    return msg


def generate_song_intro(title: str, queue_size: int = 0) -> str:
    """Generate a DJ intro before the 2nd+ song (not the session opener)."""
    tod = _time_of_day()

    # Late night? Go mellow 40% of the time
    if tod in ("night", "late night") and random.random() < 0.40:
        msg = _format_line(random.choice(_pool("hype_intros")), title=title)
    # 20% chance of a loud/hype intro
    elif random.random() < 0.20:
        msg = _format_line(random.choice(_pool("hype_intros_loud")), title=title)
    else:
        msg = _format_line(random.choice(_pool("hype_intros")), title=title)

    # 15% chance to tack on a listener callout
    if random.random() < 0.15:
        msg += " " + _format_line(random.choice(_pool("callouts")))

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
            msg = _format_line(
                random.choice(_pool("transitions_mellow")),
                prev_title=title,
                next_title=next_title,
            )
        # 20% chance of a hype transition
        elif random.random() < 0.20:
            msg = _format_line(
                random.choice(_pool("transitions_hype")),
                prev_title=title,
                next_title=next_title,
            )
        else:
            msg = _format_line(
                random.choice(_pool("transitions")),
                prev_title=title,
                next_title=next_title,
            )

        # 25% chance to tack on queue banter
        if queue_size > 0 and random.random() < 0.25:
            banter = _queue_banter(queue_size)
            if banter:
                msg += " " + banter

    elif has_next:
        # Next track exists but we don't know its title
        msg = _format_line(random.choice(_pool("outros")), title=title)
        banter = _queue_banter(queue_size)
        if banter:
            msg += " " + banter

    else:
        # Last song — queue is empty after this
        msg = _format_line(random.choice(_pool("outros_final")), title=title)

    # 20% chance to prepend a station ID on the outro too
    if random.random() < 0.20:
        msg = _format_line(random.choice(_pool("station_ids"))) + " " + msg

    return msg


# ── TTS Generation ─────────────────────────────────────────────────

# Default voice names per engine — used when no voice is explicitly set
DEFAULT_VOICE_EDGE = "en-US-AriaNeural"
DEFAULT_VOICE_KOKORO = "af_heart"
DEFAULT_VOICE_VIBEVOICE = "en-Carter_man"

# Sample rates for local engines that output PCM/WAV
KOKORO_SAMPLE_RATE = 24000
VIBEVOICE_SAMPLE_RATE = 24000

# ── Built-in Kokoro voice catalog ──────────────────────────────────────
# These are the voices available in Kokoro-FastAPI v0.2.x.
# The bot also queries the server's /v1/audio/voices endpoint at runtime
# to get the authoritative list (which may include custom / combined voices).
KOKORO_VOICE_CATALOG: dict[str, str] = {
    "af_heart": "American Female - Heart (warm)",
    "af_bella": "American Female - Bella",
    "af_nicole": "American Female - Nicole",
    "af_sarah": "American Female - Sarah",
    "af_sky": "American Female - Sky",
    "am_adam": "American Male - Adam",
    "am_michael": "American Male - Michael",
    "bf_emma": "British Female - Emma",
    "bf_isabella": "British Female - Isabella",
    "bm_george": "British Male - George",
    "bm_lewis": "British Male - Lewis",
}


def _is_edge_voice(voice: str) -> bool:
    """Return True if a voice name looks like a Microsoft Edge TTS voice."""
    return "-" in voice and "Neural" in voice


def _is_kokoro_voice(voice: str) -> bool:
    """Return True if a voice name looks like a Kokoro TTS voice."""
    return "_" in voice and "Neural" not in voice and "-" not in voice.replace("_", "")


def _is_vibevoice_voice(voice: str) -> bool:
    """Return True if a voice name looks like a VibeVoice TTS voice.

    VibeVoice names have both '-' and '_' like 'en-Carter_man'.
    """
    return "-" in voice and "_" in voice and "Neural" not in voice


def _engine_for_voice(voice: str) -> str | None:
    """Guess which TTS engine a voice name belongs to.

    Returns 'kokoro', 'vibevoice', 'edge-tts', or None if unclear.
    """
    if _is_kokoro_voice(voice):
        return "kokoro"
    if _is_edge_voice(voice):
        return "edge-tts"
    if _is_vibevoice_voice(voice):
        return "vibevoice"
    return None


def _resolve_voice(voice: str, engine: str = "") -> str:
    """Resolve a voice name for the given engine.

    If the voice looks like it belongs to a different engine (e.g. passing an
    Edge TTS voice name like 'en-US-AriaNeural' when using Kokoro), swap it
    for that engine's default instead of silently producing incompatible audio.

    Logs a warning when a cross-engine swap happens so the user knows why
    their selected voice wasn't used.
    """
    if not engine:
        engine = TTS_MODE

    voice_engine = _engine_for_voice(voice)

    # If we can identify the voice's engine and it doesn't match the target,
    # swap to the target engine's default.
    if voice_engine and voice_engine != engine:
        defaults = {
            "kokoro": DEFAULT_VOICE_KOKORO,
            "vibevoice": DEFAULT_VOICE_VIBEVOICE,
            "edge-tts": DEFAULT_VOICE_EDGE,
        }
        new_voice = defaults.get(engine, voice)
        logging.info(
            f"DJ: Voice '{voice}' is a {voice_engine} voice but TTS engine is "
            f"{engine} — resolved to {engine} default '{new_voice}'"
        )
        return new_voice

    # Heuristic fallback: if we couldn't identify the engine, use pattern matching
    if engine == "kokoro":
        if "_" not in voice and "-" in voice and "Neural" in voice:
            logging.info(
                f"DJ: Voice '{voice}' looks like an edge-tts voice but TTS is "
                f"kokoro — resolved to kokoro default '{DEFAULT_VOICE_KOKORO}'"
            )
            return DEFAULT_VOICE_KOKORO
    elif engine == "vibevoice":
        if "Neural" in voice:
            logging.info(
                f"DJ: Voice '{voice}' looks like an edge-tts voice but TTS is "
                f"vibevoice — resolved to vibevoice default '{DEFAULT_VOICE_VIBEVOICE}'"
            )
            return DEFAULT_VOICE_VIBEVOICE
    elif engine == "edge-tts":
        if "_" in voice and "Neural" not in voice:
            logging.info(
                f"DJ: Voice '{voice}' looks like a local TTS voice but TTS is "
                f"edge-tts — resolved to edge-tts default '{DEFAULT_VOICE_EDGE}'"
            )
            return DEFAULT_VOICE_EDGE

    return voice


# ── Voice listing ────────────────────────────────────────────────────


async def list_voices(language: str = "en") -> list[dict]:
    """Return available TTS voices for the active engine.

    Each entry is a dict with keys: ShortName/name, Gender, Locale.
    """
    if TTS_MODE == "kokoro":
        return await _list_voices_kokoro(language)
    elif TTS_MODE == "vibevoice":
        return await _list_voices_vibevoice(language)

    # Default: edge-tts
    if not EDGE_TTS_AVAILABLE:
        return []
    try:
        voices = await edge_tts.list_voices()
        return [v for v in voices if v["Locale"].startswith(language)]
    except Exception as e:
        logging.error(f"DJ: Failed to list TTS voices: {e}")
        return []


async def _list_voices_kokoro(language: str = "en") -> list[dict]:
    """Fetch available voices from the Kokoro-FastAPI server.

    Queries the OpenAI-compatible /v1/audio/voices endpoint.
    Falls back to the built-in KOKORO_VOICE_CATALOG if the server is unreachable.
    """
    if not AIOHTTP_AVAILABLE:
        return _kokoro_catalog_to_list(language)

    url = f"{KOKORO_TTS_URL}/v1/audio/voices"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        ) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logging.warning(
                        f"DJ: Kokoro /v1/audio/voices returned {resp.status}, "
                        "using built-in catalog"
                    )
                    return _kokoro_catalog_to_list(language)
                data = await resp.json(content_type=None)
    except Exception as e:
        logging.warning(
            f"DJ: Failed to query Kokoro server at {KOKORO_TTS_URL}: {e}. "
            "Using built-in voice catalog."
        )
        return _kokoro_catalog_to_list(language)

    # The API returns {"voices": ["af_heart", "af_bella", ...]}
    voice_names = data.get("voices", [])
    if not voice_names:
        return _kokoro_catalog_to_list(language)

    result = []
    for name in voice_names:
        # Skip combined voices like "af_bella+af_heart" from the list
        # (they work for generation but clutter the voice picker)
        if "+" in name:
            continue

        desc = KOKORO_VOICE_CATALOG.get(name, "")
        # Parse language/gender from voice prefix
        # af = American Female, am = American Male, bf = British Female, bm = British Male
        prefix = name.split("_")[0] if "_" in name else "af"
        lang_map = {"a": "en-US", "b": "en-GB"}
        gender_map = {"f": "Female", "m": "Male"}
        locale = lang_map.get(prefix[0], "en-US")
        gender = gender_map.get(prefix[-1], "Female")

        # Filter by language prefix
        if language and not locale.lower().startswith(language.lower()):
            # Also allow loose match: "en" matches both en-US and en-GB
            if not locale.split("-")[0].lower().startswith(language.lower()):
                continue

        result.append(
            {
                "ShortName": name,
                "Gender": gender,
                "Locale": locale,
                "name": name,
                "default": name == DEFAULT_VOICE_KOKORO,
                "description": desc,
            }
        )

    return result


def _kokoro_catalog_to_list(language: str = "en") -> list[dict]:
    """Convert the built-in KOKORO_VOICE_CATALOG to the voice-list format."""
    result = []
    for name, desc in KOKORO_VOICE_CATALOG.items():
        prefix = name.split("_")[0] if "_" in name else "af"
        lang_map = {"a": "en-US", "b": "en-GB"}
        gender_map = {"f": "Female", "m": "Male"}
        locale = lang_map.get(prefix[0], "en-US")
        gender = gender_map.get(prefix[-1], "Female")

        if language and not locale.lower().startswith(language.lower()):
            if not locale.split("-")[0].lower().startswith(language.lower()):
                continue

        result.append(
            {
                "ShortName": name,
                "Gender": gender,
                "Locale": locale,
                "name": name,
                "default": name == DEFAULT_VOICE_KOKORO,
                "description": desc,
            }
        )
    return result


async def _list_voices_vibevoice(language: str = "en") -> list[dict]:
    """Fetch available voices from the VibeVoice-Realtime server.

    Calls the /config endpoint which returns:
    {"voices": ["en-Carter_man", ...], "default_voice": "en-Carter_man"}

    Returns a list of dicts with keys: name, gender, locale — matching
    the format expected by the web dashboard and ?djvoices command.
    """
    if not AIOHTTP_AVAILABLE:
        logging.error("DJ: aiohttp not installed, cannot list VibeVoice TTS voices")
        return []

    url = f"{VIBEVOICE_TTS_URL}/config"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        ) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logging.error(
                        f"DJ: VibeVoice /config returned status {resp.status}"
                    )
                    return []
                data = await resp.json(content_type=None)
    except Exception as e:
        logging.error(
            f"DJ: Failed to connect to VibeVoice server at {VIBEVOICE_TTS_URL}: {e}"
        )
        return []

    voice_names = data.get("voices", [])
    default_voice = data.get("default_voice", "")

    result = []
    for name in voice_names:
        # Parse voice name pattern: "en-Carter_man", "de-Anna_woman", "ja-Sakura_woman"
        parts = name.split("-", 1)
        locale = parts[0] if len(parts) == 2 else "en"
        gender = "female" if name.endswith(("_woman", "_girl")) else "male"

        if language and not locale.lower().startswith(language.lower()):
            continue

        result.append(
            {
                "ShortName": name,
                "Gender": gender.capitalize(),
                "Locale": f"{locale}-US"
                if locale == "en"
                else f"{locale}-{locale.upper()}",
                "name": name,
                "default": name == default_voice,
            }
        )

    return result


# ── TTS audio generation ──────────────────────────────────────────────


# ── Kokoro server health check ────────────────────────────────────────
# Caches the last health check result so we don't probe the server on
# every single TTS call. If the server was down 3 seconds ago, it's
# probably still down — skip straight to the fallback.
_kokoro_last_health_check: float = 0.0
_kokoro_healthy: bool | None = None  # None = never checked
_KOKORO_HEALTH_CACHE_TTL = 30  # seconds before re-checking a "healthy" result
_KOKORO_HEALTH_CACHE_TTL_DOWN = 10  # seconds before re-checking a "down" result


async def _check_kokoro_health() -> bool:
    """Quick health check: can we reach the Kokoro-FastAPI server?

    Sends a GET to /v1/audio/voices (lightweight endpoint) with a very
    short timeout. Returns True if the server responds, False otherwise.
    Caches the result to avoid hammering the server on every TTS call.
    """
    global _kokoro_last_health_check, _kokoro_healthy

    import time as _time

    now = _time.monotonic()
    cache_ttl = (
        _KOKORO_HEALTH_CACHE_TTL if _kokoro_healthy else _KOKORO_HEALTH_CACHE_TTL_DOWN
    )
    if _kokoro_healthy is not None and (now - _kokoro_last_health_check) < cache_ttl:
        return _kokoro_healthy

    if not AIOHTTP_AVAILABLE:
        _kokoro_healthy = False
        _kokoro_last_health_check = now
        return False

    url = f"{KOKORO_TTS_URL}/v1/audio/voices"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=3, connect=2)
        ) as session:
            async with session.get(url) as resp:
                _kokoro_healthy = resp.status == 200
                _kokoro_last_health_check = now
                if not _kokoro_healthy:
                    logging.warning(f"DJ: Kokoro health check returned {resp.status}")
                return _kokoro_healthy
    except Exception as e:
        logging.warning(f"DJ: Kokoro server at {KOKORO_TTS_URL} is unreachable: {e}")
        _kokoro_healthy = False
        _kokoro_last_health_check = now
        return False


async def generate_tts(text: str, voice: str = None, source: str = "DJ") -> str | None:
    """Generate a TTS audio file and return its path.

    Routes to the appropriate TTS engine based on config.TTS_MODE:
    - "kokoro": Kokoro-FastAPI Docker server (local GPU, OpenAI-compatible API)
    - "vibevoice": VibeVoice-Realtime WebSocket server (local GPU, separate process)
    - "edge-tts": Microsoft Edge TTS (cloud-based, always-available fallback)

    If the primary engine fails, falls back to edge-tts automatically.
    For Kokoro, a quick health check is done first — if the server is
    unreachable, the fallback is nearly instant instead of waiting for
    a long timeout.

    Args:
        text: The text to synthesize.
        voice: Voice name. Auto-resolved per engine if None.
        source: Who is speaking — e.g. "DJ" or "AI Side Host".
            Used in log messages to distinguish who generated the TTS.

    Returns the path to a WAV file (kokoro/vibevoice) or MP3 file (edge-tts).
    The caller must delete the file after use via cleanup_tts_file().
    Returns None if TTS is unavailable or generation fails.
    """
    if not text or not text.strip():
        return None

    # Resolve default voice based on active engine
    if voice is None:
        if TTS_MODE == "kokoro":
            voice = DEFAULT_VOICE_KOKORO
        elif TTS_MODE == "vibevoice":
            voice = DEFAULT_VOICE_VIBEVOICE
        else:
            voice = DEFAULT_VOICE_EDGE

    if TTS_MODE == "kokoro":
        # Quick health check — skip Kokoro entirely if server is down
        healthy = await _check_kokoro_health()
        if healthy:
            resolved = _resolve_voice(voice, "kokoro")
            result = await _generate_tts_kokoro(text, resolved, source=source)
            if result is not None:
                return result
            logging.warning(
                f"{source}: Kokoro TTS generation failed despite server being up. "
                "Falling back to edge-tts."
            )
        else:
            logging.warning(
                f"{source}: Kokoro server is down, falling back to edge-tts. "
                "Start it with: docker run --gpus all -p 8880:8880 "
                "ghcr.io/remsky/kokoro-fastapi-gpu:latest"
            )
        # Re-resolve voice for edge-tts (Kokoro voice names won't work there)
        fallback_voice = _resolve_voice(voice, "edge-tts")
        if fallback_voice != voice:
            logging.info(
                f"{source}: Voice '{voice}' won't work with edge-tts fallback, "
                f"using '{fallback_voice}' instead"
            )

    elif TTS_MODE == "vibevoice":
        resolved = _resolve_voice(voice, "vibevoice")
        result = await _generate_tts_vibevoice(text, resolved, source=source)
        if result is not None:
            return result
        logging.warning(f"{source}: VibeVoice TTS failed, falling back to edge-tts")
        fallback_voice = _resolve_voice(voice, "edge-tts")
        if fallback_voice != voice:
            logging.info(
                f"{source}: Voice '{voice}' won't work with edge-tts fallback, "
                f"using '{fallback_voice}' instead"
            )

    else:
        # TTS_MODE == "edge-tts" — use directly
        fallback_voice = _resolve_voice(voice, "edge-tts")

    # Final fallback: edge-tts
    if not EDGE_TTS_AVAILABLE:
        logging.error(
            f"{source}: edge-tts not installed — cannot fall back! "
            "Install with: pip install edge-tts"
        )
        return None

    logging.info(f"{source}: Using edge-tts fallback (voice={fallback_voice})")
    return await _generate_tts_edge(text, fallback_voice, source=source)


async def _generate_tts_kokoro(
    text: str, voice: str = DEFAULT_VOICE_KOKORO, source: str = "DJ"
) -> str | None:
    """Generate TTS audio using a Kokoro-FastAPI Docker server.

    Calls the OpenAI-compatible /v1/audio/speech endpoint.
    Kokoro-FastAPI serves audio in multiple formats (mp3, wav, pcm, etc).
    We request WAV for consistency with the existing pipeline.

    Returns the path to a WAV file, or None on failure.
    """
    if not AIOHTTP_AVAILABLE:
        logging.error("DJ: aiohttp not installed, cannot use Kokoro TTS")
        return None

    url = f"{KOKORO_TTS_URL}/v1/audio/speech"
    payload = {
        "model": "kokoro",
        "input": text.strip(),
        "voice": voice,
        "response_format": "wav",
        "speed": 1.0,
    }

    # Short, aggressive timeout — we don't want the DJ sitting in silence
    # for 30 seconds if the server is down. If Kokoro doesn't respond in
    # 5 seconds (connect) or 15 seconds (total), we bail and let edge-tts
    # take over. DJ speech clips are typically under 10 seconds of audio,
    # which Kokoro generates in <1 second on GPU.
    timeout = aiohttp.ClientTimeout(total=15, connect=5)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logging.error(
                        f"DJ: Kokoro TTS returned status {resp.status}: "
                        f"{error_text[:200]}"
                    )
                    return None

                audio_data = await resp.read()

    except aiohttp.ClientConnectorError as e:
        logging.error(
            f"DJ: Cannot connect to Kokoro server at {KOKORO_TTS_URL}. "
            f"Is the Docker container running? Error: {e}"
        )
        return None
    except asyncio.TimeoutError:
        logging.error(
            f"{source}: Kokoro TTS timed out (15s). "
            "The server may be overloaded or starting up. Falling back."
        )
        return None
    except Exception as e:
        logging.error(f"{source}: Kokoro TTS unexpected error: {e}")
        return None

    if not audio_data or len(audio_data) < 44:
        logging.warning(f"{source}: Kokoro TTS returned empty or tiny audio data")
        return None

    # Save the WAV data to a temp file.
    # Kokoro-FastAPI may send WAV files with a streaming header where the
    # data chunk size is 0xFFFFFFFF (unknown). Python's wave module and
    # FFmpeg misread this as a ~24-hour file, which causes FFmpeg to hang
    # waiting for data that never comes. We fix the header by re-writing
    # the WAV with the correct data size.
    try:
        fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="dj_kokoro_")
        os.close(fd)

        # Try to parse with wave module — if the header is sane, just save.
        # If the header is broken (streaming-style), rewrite it.
        import io as _io

        try:
            with wave.open(_io.BytesIO(audio_data), "rb") as wf:
                nframes = wf.getnframes()
                rate = wf.getframerate()
                channels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                expected_size = 44 + nframes * channels * sampwidth

                # If nframes claims a file bigger than what we actually received,
                # the WAV has a broken streaming-style header (chunk size 0xFFFFFFFF).
                # We need to rewrite it with the correct sizes so FFmpeg can play it.
                actual_pcm_bytes = len(audio_data) - 44  # rough estimate
                claimed_pcm_bytes = nframes * channels * sampwidth
                if claimed_pcm_bytes > actual_pcm_bytes + 1024:
                    # Header is broken — rewrite with correct data size
                    raw_frames = wf.readframes(
                        actual_pcm_bytes // (channels * sampwidth)
                    )

                    # Rewrite with correct header
                    with wave.open(wav_path, "wb") as wf_out:
                        wf_out.setnchannels(channels)
                        wf_out.setsampwidth(sampwidth)
                        wf_out.setframerate(rate)
                        wf_out.writeframes(raw_frames)

                    # Recalculate duration from actual file size
                    pcm_bytes = len(raw_frames)
                    duration = (
                        pcm_bytes / (rate * channels * sampwidth) if rate > 0 else 0
                    )
                else:
                    # Header is fine — just save as-is
                    with open(wav_path, "wb") as f:
                        f.write(audio_data)
                    duration = nframes / rate if rate > 0 else 0

        except Exception:
            # wave.open failed entirely — just save the raw bytes and hope
            # FFmpeg can handle it. This shouldn't happen with Kokoro output.
            with open(wav_path, "wb") as f:
                f.write(audio_data)
            duration = 0.0

        logging.info(
            f"{source}: Generated TTS (kokoro) → {wav_path} "
            f"({len(text)} chars, voice={voice}, {duration:.1f}s)"
        )
        return wav_path
    except Exception as e:
        logging.error(f"{source}: Failed to write Kokoro TTS WAV file: {e}")
        return None


async def _generate_tts_edge(
    text: str, voice: str = DEFAULT_VOICE_EDGE, source: str = "DJ"
) -> str | None:
    """Generate TTS audio using Microsoft Edge TTS (cloud-based).

    Returns the path to an MP3 file, or None on failure.
    """
    if not EDGE_TTS_AVAILABLE:
        return None

    path = None
    try:
        fd, path = tempfile.mkstemp(suffix=".mp3", prefix="dj_")
        os.close(fd)

        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(path)

        logging.info(
            f"{source}: Generated TTS (edge-tts) → {path} ({len(text)} chars, voice={voice})"
        )
        return path
    except Exception as e:
        logging.error(f"{source}: Failed to generate TTS (edge-tts): {e}")
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
        return None


async def _generate_tts_vibevoice(
    text: str, voice: str = DEFAULT_VOICE_VIBEVOICE, source: str = "DJ"
) -> str | None:
    """Generate TTS audio using a VibeVoice-Realtime WebSocket server.

    Connects to the server's /stream WebSocket endpoint, sends the text,
    and collects PCM16 audio chunks which are assembled into a WAV file.

    Returns the path to a WAV file, or None on failure.
    """
    if not AIOHTTP_AVAILABLE:
        logging.error(f"{source}: aiohttp not installed, cannot use VibeVoice TTS")
        return None

    # Build the WebSocket URL with query parameters
    params = f"text={text.strip()}"
    if voice:
        params += f"&voice={voice}"

    ws_url = f"{VIBEVOICE_TTS_URL.replace('http://', 'ws://').replace('https://', 'wss://')}/stream?{params}"

    audio_chunks = []
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        ) as session:
            try:
                async with session.ws_connect(ws_url) as ws:
                    # Collect PCM16 audio chunks from the WebSocket
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            audio_chunks.append(msg.data)
                        elif msg.type == aiohttp.WSMsgType.TEXT:
                            # JSON status messages — ignore
                            pass
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            logging.error(
                                f"{source}: VibeVoice WebSocket error: {ws.exception()}"
                            )
                            break
            except aiohttp.WSError as e:
                logging.error(
                    f"{source}: Failed to connect to VibeVoice server at {VIBEVOICE_TTS_URL}: {e}"
                )
                return None
            except asyncio.TimeoutError:
                logging.error(f"{source}: VibeVoice TTS connection timed out (30s)")
                return None

    except Exception as e:
        logging.error(f"{source}: VibeVoice TTS unexpected error: {e}")
        return None

    if not audio_chunks:
        logging.warning(f"{source}: VibeVoice TTS returned no audio data")
        return None

    # Combine all PCM16 chunks and write as a WAV file
    try:
        pcm_data = b"".join(audio_chunks)

        fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="dj_vv_")
        os.close(fd)

        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)  # Mono
            wf.setsampwidth(2)  # 16-bit = 2 bytes
            wf.setframerate(VIBEVOICE_SAMPLE_RATE)  # 24000 Hz
            wf.writeframes(pcm_data)

        logging.info(
            f"{source}: Generated TTS (vibevoice) → {wav_path} ({len(text)} chars, "
            f"voice={voice}, {len(pcm_data) / VIBEVOICE_SAMPLE_RATE:.1f}s)"
        )
        return wav_path
    except Exception as e:
        logging.error(f"{source}: Failed to write VibeVoice TTS WAV file: {e}")
        return None


def cleanup_tts_file(path: str):
    """Delete a generated TTS audio file. Safe to call from sync callbacks."""
    if path and os.path.exists(path):
        try:
            os.remove(path)
            logging.debug(f"DJ: Cleaned up TTS file: {path}")
        except Exception as e:
            logging.warning(f"DJ: Failed to clean up {path}: {e}")
