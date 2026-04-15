"""
utils/dj.py — Radio DJ mode for MBot.

Generates Text-to-Speech DJ commentary between songs using either:
- Microsoft Edge TTS (default, cloud-based, pip install edge-tts)
- VibeVoice-Realtime (local, GPU-accelerated, https://github.com/microsoft/VibeVoice)

The DJ speaks like a real radio host — with energy, personality, time-aware
greetings, listener callouts, weather-style banter, and natural transitions.

TTS engine is selected via config.TTS_MODE:
- "edge-tts" (default): Uses Microsoft Edge TTS voices. Free, no server needed.
- "local": Uses a locally-hosted VibeVoice-Realtime WebSocket server.
  Lower latency (~300ms), runs on your GPU/CPU, no Microsoft API dependency.
"""

import asyncio
import logging
import os
import random
import re
import struct
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
        "edge-tts not installed — DJ mode unavailable. Install with: pip install edge-tts"
    )

try:
    import aiohttp

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    # If aiohttp is missing, local TTS won't work either, but we don't
    # block the bot from starting — we just log a warning later.


# ── TTS Engine Detection ─────────────────────────────────────────────

TTS_MODE = getattr(config, "TTS_MODE", "edge-tts").lower()
LOCAL_TTS_URL = getattr(config, "LOCAL_TTS_URL", "http://localhost:3000")

# Validate TTS mode and availability
if TTS_MODE == "local":
    if not AIOHTTP_AVAILABLE:
        logging.warning(
            "DJ: TTS_MODE=local but aiohttp not installed. "
            "Install with: pip install aiohttp"
        )
        TTS_MODE = "edge-tts"  # Fall back to edge-tts
    else:
        logging.info(f"DJ: Using local TTS server at {LOCAL_TTS_URL}")
elif TTS_MODE != "edge-tts":
    logging.warning(f"DJ: Unknown TTS_MODE '{TTS_MODE}', falling back to edge-tts")
    TTS_MODE = "edge-tts"


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

DEFAULT_VOICE = "en-US-AriaNeural"

# VibeVoice voices always default to Carter (male) since Edge TTS defaults to
# Aria (female). This gives a natural gender contrast for the main DJ voice.
DEFAULT_LOCAL_VOICE = "en-Carter_man"

# VibeVoice-Realtime outputs raw PCM16 at 24kHz, mono.
VIBEVOICE_SAMPLE_RATE = 24000


async def list_voices(language: str = "en") -> list[dict]:
    """Return available TTS voices.

    When TTS_MODE is "local", queries the VibeVoice-Realtime server's /config
    endpoint for its list of voice presets. When TTS_MODE is "edge-tts" (default),
    queries the Microsoft Edge TTS voice list.

    Each entry is a dict with keys: name, gender, locale.
    """
    if TTS_MODE == "local":
        return await _list_voices_local(language)

    # Default: edge-tts
    if not EDGE_TTS_AVAILABLE:
        return []
    try:
        voices = await edge_tts.list_voices()
        return [v for v in voices if v["Locale"].startswith(language)]
    except Exception as e:
        logging.error(f"DJ: Failed to list TTS voices: {e}")
        return []


async def _list_voices_local(language: str = "en") -> list[dict]:
    """Fetch available voices from the VibeVoice-Realtime server.

    Calls the /config endpoint which returns:
    {"voices": ["en-Carter_man", ...], "default_voice": "en-Carter_man"}

    Returns a list of dicts with keys: name, gender, locale — matching
    the format expected by the web dashboard and ?djvoices command.
    """
    if not AIOHTTP_AVAILABLE:
        logging.error("DJ: aiohttp not installed, cannot list local TTS voices")
        return []

    url = f"{LOCAL_TTS_URL}/config"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        ) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logging.error(
                        f"DJ: Local TTS /config returned status {resp.status}"
                    )
                    return []
                data = await resp.json(content_type=None)
    except Exception as e:
        logging.error(
            f"DJ: Failed to connect to local TTS server at {LOCAL_TTS_URL}: {e}"
        )
        return []

    voice_names = data.get("voices", [])
    default_voice = data.get("default_voice", "")

    result = []
    for name in voice_names:
        # Parse voice name pattern: "en-Carter_man", "de-Anna_woman", "ja-Sakura_woman"
        # Format: {lang}-{Name}_{gender_suffix}
        parts = name.split("-", 1)
        locale = parts[0] if len(parts) == 2 else "en"
        gender = "female" if name.endswith(("_woman", "_girl")) else "male"

        # Filter by language prefix
        if language and not locale.lower().startswith(language.lower()):
            continue

        result.append(
            {
                "ShortName": name,
                "Gender": gender.capitalize(),
                "Locale": f"{locale}-US"
                if locale == "en"
                else f"{locale}-{locale.upper()}",
                "name": name,  # For dashboard compatibility
                "default": name == default_voice,
            }
        )

    return result


async def generate_tts(text: str, voice: str = DEFAULT_VOICE) -> str | None:
    """Generate a TTS audio file and return its path.

    Routes to the appropriate TTS engine based on config.TTS_MODE:
    - "local": Uses VibeVoice-Realtime WebSocket server (low latency, local GPU)
    - "edge-tts" (default): Uses Microsoft Edge TTS (cloud-based, no server needed)

    Returns the path to a WAV file (local) or MP3 file (edge-tts).
    The caller must delete the file after use via cleanup_tts_file().
    Returns None if TTS is unavailable or generation fails.
    """
    if not text or not text.strip():
        return None

    if TTS_MODE == "local":
        result = await _generate_tts_local(text, voice)
        if result is not None:
            return result
        # Local TTS failed — fall back to edge-tts if available
        logging.warning("DJ: Local TTS failed, falling back to edge-tts")

    # Default / fallback: edge-tts
    if not EDGE_TTS_AVAILABLE:
        logging.warning("DJ: edge-tts not available, skipping TTS.")
        return None

    return await _generate_tts_edge(text, voice)


async def _generate_tts_edge(text: str, voice: str = DEFAULT_VOICE) -> str | None:
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
            f"DJ: Generated TTS (edge-tts) → {path} ({len(text)} chars, voice={voice})"
        )
        return path
    except Exception as e:
        logging.error(f"DJ: Failed to generate TTS (edge-tts): {e}")
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
        return None


async def _generate_tts_local(
    text: str, voice: str = DEFAULT_LOCAL_VOICE
) -> str | None:
    """Generate TTS audio using a VibeVoice-Realtime WebSocket server.

    Connects to the server's /stream WebSocket endpoint, sends the text,
    and collects PCM16 audio chunks which are assembled into a WAV file.

    Returns the path to a WAV file, or None on failure.
    """
    if not AIOHTTP_AVAILABLE:
        logging.error("DJ: aiohttp not installed, cannot use local TTS")
        return None

    # Build the WebSocket URL with query parameters
    # VibeVoice-Realtime: /stream?text=<text>&voice=<voice>
    params = f"text={text.strip()}"
    if voice:
        params += f"&voice={voice}"

    ws_url = f"{LOCAL_TTS_URL.replace('http://', 'ws://').replace('https://', 'wss://')}/stream?{params}"

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
                            # JSON status messages — ignore for now
                            pass
                        elif msg.type == aiohttp.WSMsgType.ERROR:
                            logging.error(
                                f"DJ: Local TTS WebSocket error: {ws.exception()}"
                            )
                            break
            except aiohttp.WSError as e:
                logging.error(
                    f"DJ: Failed to connect to local TTS server at {LOCAL_TTS_URL}: {e}"
                )
                return None
            except asyncio.TimeoutError:
                logging.error(f"DJ: Local TTS connection timed out (30s)")
                return None

    except Exception as e:
        logging.error(f"DJ: Local TTS unexpected error: {e}")
        return None

    if not audio_chunks:
        logging.warning("DJ: Local TTS returned no audio data")
        return None

    # Combine all PCM16 chunks and write as a WAV file
    try:
        pcm_data = b"".join(audio_chunks)

        fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="dj_")
        os.close(fd)

        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)  # Mono
            wf.setsampwidth(2)  # 16-bit = 2 bytes
            wf.setframerate(VIBEVOICE_SAMPLE_RATE)  # 24000 Hz
            wf.writeframes(pcm_data)

        logging.info(
            f"DJ: Generated TTS (local) → {wav_path} ({len(text)} chars, "
            f"voice={voice}, {len(pcm_data) / VIBEVOICE_SAMPLE_RATE:.1f}s)"
        )
        return wav_path
    except Exception as e:
        logging.error(f"DJ: Failed to write local TTS WAV file: {e}")
        return None


def cleanup_tts_file(path: str):
    """Delete a generated TTS audio file. Safe to call from sync callbacks."""
    if path and os.path.exists(path):
        try:
            os.remove(path)
            logging.debug(f"DJ: Cleaned up TTS file: {path}")
        except Exception as e:
            logging.warning(f"DJ: Failed to clean up {path}: {e}")
