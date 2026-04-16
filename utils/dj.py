"""
utils/dj.py — Radio DJ mode for MBot.

Generates Text-to-Speech DJ commentary between songs using:
- MOSS-TTS-Nano via its FastAPI server (default, local, CPU/GPU, voice clone)
- VibeVoice-Realtime (separate WebSocket server, GPU-accelerated)
- Microsoft Edge TTS (cloud fallback)

The DJ speaks like a real radio host — with energy, personality, time-aware
greetings, listener callouts, weather-style banter, and natural transitions.

TTS engine is selected via config.TTS_MODE:
- "moss" (default): MOSS-TTS-Nano FastAPI server. 0.1B params, CPU-friendly.
  Voice cloning via prompt audio files in assets/moss_voices/.
  Start with: moss-tts-nano serve --port 18083
  See: https://github.com/OpenMOSS/MOSS-TTS-Nano
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
    # If aiohttp is missing, MOSS/VibeVoice HTTP-based TTS won't work either,
    # but we don't block the bot from starting — we just log a warning later.


# ── TTS Engine Detection ─────────────────────────────────────────────

# Resolve TTS mode, handling legacy aliases
_raw_tts_mode = getattr(config, "TTS_MODE", "moss").lower()

# Backward compat: "local" and "kokoro" are legacy names
if _raw_tts_mode == "local":
    logging.warning(
        'DJ: TTS_MODE="local" is deprecated. Use "vibevoice" instead. '
        'Mapping "local" → "vibevoice" for now.'
    )
    _raw_tts_mode = "vibevoice"
elif _raw_tts_mode == "kokoro":
    logging.warning(
        'DJ: TTS_MODE="kokoro" is no longer supported. '
        'Switching to "moss" (MOSS-TTS-Nano). '
        "See https://github.com/OpenMOSS/MOSS-TTS-Nano for setup."
    )
    _raw_tts_mode = "moss"

KNOWN_TTS_MODES = {"moss", "vibevoice", "edge-tts"}
if _raw_tts_mode not in KNOWN_TTS_MODES:
    logging.warning(f"DJ: Unknown TTS_MODE '{_raw_tts_mode}', falling back to moss")
    _raw_tts_mode = "moss"

# Validate: moss and vibevoice both need aiohttp for HTTP calls
if _raw_tts_mode in ("moss", "vibevoice") and not AIOHTTP_AVAILABLE:
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
    (TTS_MODE in ("moss", "vibevoice") and AIOHTTP_AVAILABLE)
    or (TTS_MODE == "edge-tts" and EDGE_TTS_AVAILABLE)
    or EDGE_TTS_AVAILABLE  # edge-tts is always a fallback
)

# ── MOSS voice prompt directory ──────────────────────────────────────
# MOSS-TTS-Nano uses voice cloning — each "voice" is a .wav prompt audio
# file stored in assets/moss_voices/. The voice name is the filename
# without the .wav extension (e.g. "en_warm_female" → assets/moss_voices/en_warm_female.wav).
MOSS_VOICES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "assets", "moss_voices"
)

# Resolve server URLs from config
MOSS_TTS_URL = getattr(config, "MOSS_TTS_URL", "http://localhost:18083")

VIBEVOICE_TTS_URL = getattr(config, "VIBEVOICE_TTS_URL", "") or getattr(
    config, "LOCAL_TTS_URL", "http://localhost:3000"
)

# Log the active TTS configuration at startup
if TTS_MODE == "moss":
    logging.info(f"DJ: Using MOSS-TTS-Nano server at {MOSS_TTS_URL}")
    logging.info(f"DJ: MOSS voice prompts directory: {MOSS_VOICES_DIR}")
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
            "The sun's up and so is the volume!",
            "Good morning! Coffee's optional. Music isn't.",
            "Rise and grind! The musical kind of grind.",
            "Morning! If you're hearing this, you're already winning today.",
        ],
        "afternoon": [
            "Good afternoon, everyone!",
            "Afternoon, listeners!",
            "Hope your afternoon's going well!",
            "Afternoon! Let's keep the energy up.",
            "Good afternoon! Perfect time for a soundtrack.",
            "Afternoon vibes! Let me provide the background music to your day.",
            "Good afternoon! The weather outside may vary, but the tunes are always consistent.",
        ],
        "evening": [
            "Good evening, everyone!",
            "Evening, music lovers!",
            "Hope you're having a great evening!",
            "Evening! Perfect time for some tunes.",
            "Good evening! The night is young and so is this playlist.",
            "Evening! Leave the day behind. The music starts now.",
            "Good evening, beautiful people!",
        ],
        "night": [
            "Evening, night owls!",
            "Still up? Good. Let's keep going.",
            "Night crew, you're in the right place.",
            "Late night vibes, let's go.",
            "Night owls unite! The best listening happens after midnight.",
            "Can't sleep? Good. More music for us.",
            "The night shift checking in. Let's do this.",
        ],
        "late night": [
            "Late night crew, I see you!",
            "Burning the midnight oil? I've got you covered.",
            "Late night, right here. Let's keep it mellow.",
            "Can't sleep? Neither can I. Music it is.",
            "3 AM crew, welcome to the after-hours. The vibes are different here. Special.",
            "Late night. The world is quiet. The music is loud. This is the way.",
            "If you're hearing this at this hour, we're basically best friends now.",
            "The late night listeners are the real ones. Respect.",
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
            "And then there was one. Last track after this.",
            "Down to the final track after this one.",
        ],
        2: [
            "A couple more lined up after this.",
            "Two more waiting in the wings.",
            "Just two left. The queue is almost spent.",
        ],
    }
    # 3–5
    few = [
        "Got a nice little set going. {} more after this one.",
        "A few more in the queue. {} left to go.",
        "{} more tracks lined up and ready to roll.",
        "Small but mighty. {} more in the tank.",
        "Just {} more. Quality over quantity, right?",
        "{} tracks left. We're picking up momentum.",
    ]
    # 6–15
    medium = [
        "We've got a solid lineup tonight. {} more tracks to get through.",
        "Plenty more where that came from. {} left in the queue.",
        "The queue is looking healthy — {} more to go.",
        "Don't go anywhere, we've got {} more coming up.",
        "{} more tracks and we are CRUISING right now.",
        "Settle in, we've got {} more tracks on deck.",
        "The queue midsection. {} tracks of pure possibility.",
        "{} more songs. That's like... at least twenty minutes of vibes. Minimum.",
        "DJ math: {} tracks remaining divided by vibes equals a good time.",
    ]
    # 16+
    big = [
        "We are in it for the long haul tonight, folks. {} more tracks in the queue!",
        "This is a marathon session. {} songs still to come!",
        "{} more tracks! We are not stopping anytime soon.",
        "Endless music, just the way we like it. {} more to go.",
        "{} tracks deep and we're just getting WARMED UP.",
        "This queue has {} tracks. That's not a queue. That's a lifestyle.",
        "{} more songs. At this point we're basically a festival.",
        "The queue is {} tracks strong. I will NOT be taking breaks. I will be taking requests.",
        "When I said I could DJ all night, I meant it. {} tracks prove it.",
        "{} more. I'm committed. You're committed. We're all committed. To the music. And possibly to the psych ward.",
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
    # ── Funny ──
    "{greeting} I asked the algorithm for a banger and it gave us {title}. Fair enough.",
    "{greeting} My therapist said I should open up more. So here's {title}.",
    "{greeting} They told me to read the room. The room said play {title}.",
    "{greeting} I was going to say something clever but then {title} came on and I forgot.",
    "{greeting} According to my horoscope, today's opening track is {title}. The stars have spoken.",
    "{greeting} I put all your names in a hat and drew {title}. That's just how it works here.",
    "{greeting} The vibes committee has convened and unanimously approved {title} as our opener.",
    "{greeting} Breaking news — {title} has been declared the official opening track. More at eleven.",
    "{greeting} I asked ChatGPT for an intro and it said just play {title}. So I am.",
    # ── Serious ──
    "{greeting} Let's begin. {title} is up first.",
    "{greeting} Welcome back. Our opening selection is {title}.",
    "{greeting} Thank you for tuning in. Let's start with {title}.",
    "{greeting} We appreciate you being here. {title} to open the session.",
    "{greeting} Good to have you with us. Starting off with {title}.",
    # ── Weird ──
    "{greeting} The ancient prophecy foretold this moment. {title} is the chosen one.",
    "{greeting} If you're hearing this, you're in the right timeline. {title} confirms it.",
    "{greeting} I had a dream about this. {title} was there. You were there. Everyone was dancing.",
    "{greeting} The interdimensional portal opened and out came {title}. Coincidence? I think not.",
    "{greeting} {title} volunteered to go first. The other songs were too scared.",
    "{greeting} NASA confirmed — {title} can be heard from space. We're starting with it.",
    "{greeting} Fun fact — {title} is actually a secret message from the future. Don't look into it.",
    # ── Funny with sound tags ──
    "{greeting} The council of vibes has decided. We start with {title}. {sound:cool_dj_drop} Don't argue.",
    "{greeting} My DJ senses are tingling. They say {title}. {sound:dj_scratch} I trust them.",
    "{greeting} If {title} doesn't get you moving, I don't know what will. {sound:combo_hit} Probably nothing.",
    "{greeting} The vibes have been calculated. {title} has a 100% approval rating. {sound:rave_cheer}",
    # ── Serious with sound tags ──
    "{greeting} We're here for the music. Let's start with {title}. {sound:dj_stop}",
    # ── Weird with sound tags ──
    "{greeting} The simulation has loaded. Running {title}.exe. {sound:dj_turn_it_up}",
    "{greeting} Transmissions from the mothership indicate {title} is next. {sound:air_raid} I'm just the messenger.",
    "{greeting} I consulted the oracle. She said {title}. {sound:mustard_drop} She's never wrong.",
    # ── MORE with sound tags ──
    "{greeting} This station is LIVE and {title} is proof! {sound:airhorn}",
    "{greeting} Radio alert! {title} incoming! {sound:air_raid}",
    "{greeting} Crank it up! Opening with {title}! {sound:dj_turn_it_up}",
    "{greeting} The DJ is in the building! {title} starts NOW! {sound:combo_hit}",
    "{greeting} Hold onto your headphones! {title}! {sound:mega_airhorn}",
    "{greeting} Request number one — {title}! {sound:rave_cheer}",
    "{greeting} Test, test, one two. {title}! {sound:dj_scratch}",
    "{greeting} Breaking the silence with {title}! {sound:cool_dj_drop}",
    "{greeting} Power up! Our first track is {title}! {sound:dj_stop}",
    "{greeting} You wanted {title}? You GOT {title}! {sound:mustard_drop}",
    "{greeting} No warm-up needed. {title} starts COLD! {sound:air_raid}",
    "{greeting} Opening ceremony: {title}! {sound:rave_cheer} Let the games begin!",
    "{greeting} DJ's choice! Starting with {title}! {sound:im_your_dj}",
    "{greeting} The beat drops HERE. {title}! {sound:dj_scratch}",
    "{greeting} Sound check complete. Commencing {title}! {sound:combo_hit}",
    "{greeting} All systems go. {title} is our launch track! {sound:airhorn}",
    "{greeting} The volume goes to eleven right from the start. {title}! {sound:dj_turn_it_up}",
    "{greeting} Good vibes only! {title} to open! {sound:rave_cheer}",
    "{greeting} This next hour is sponsored by {title}. {sound:mustard_drop} Just kidding. Or am I?",
    "{greeting} Fasten your seatbelts. {title} takes off NOW. {sound:air_raid}",
    "{greeting} The speakers are warmed up and {title} is ready to GO! {sound:mega_airhorn}",
    "{greeting} No introduction needed. {title} speaks for itself. {sound:cool_dj_drop}",
    "{greeting} Drum roll please... {title}! {sound:dj_rewind}",
    "{greeting} We're not starting small. {title} is a STATEMENT. {sound:combo_hit}",
    "{greeting} The vibes are immaculate and {title} proves it. {sound:rave_cheer}",
    "{greeting} I didn't choose {title}. {title} chose US. {sound:im_your_dj}",
    "{greeting} Step right up! {title} is our opening act! {sound:dj_turn_it_up}",
    "{greeting} Clear the runway! {title} is taking off! {sound:airhorn}",
    # ── MORE sound tags (new sounds) ──
    "{greeting} INCOMING! {title} has entered the chat! {sound:discord_notification}",
    "{greeting} Ding ding ding! {title} is our opener! {sound:ding_sound_effect_2}",
    "{greeting} All hands on deck! {title} is launching NOW! {sound:discord_call_sound}",
    "{greeting} The DJ has spoken. {title} is first! {sound:the_rock_shut_up}",
    "{greeting} Let's GOOOO! {title}! {sound:yeah_boiii_i_i_i}",
    "{greeting} {title} in the house! {sound:daddys_home}",
    "{greeting} VIP entry — {title}! {sound:hub_intro_sound}",
    "{greeting} Breaking news! {title} is our opening track! {sound:news_intro_maximilien__1801238420_2}",
    "{greeting} 3, 2, 1... {title}! {sound:loud_explosion}",
    "{greeting} Something incredible is about to happen. {title}! {sound:cinematic_suspense_riser}",
    "{greeting} BOOM! {title} drops NOW! {sound:vine_boom}",
    "{greeting} You thought we'd start small? Think again. {title}! {sound:magic_fairy}",
    "{greeting} Wake up! {title} is HERE! {sound:airhorn}",
    "{greeting} The vibes? Immaculate. The opener? {title}! {sound:rizz_sound_effect}",
    "{greeting} {title} just walked in and everyone noticed. {sound:is_that_d_good_yes_king}",
    "{greeting} Permission to ROCK? Granted. {title}! {sound:heavy_sniper_sound}",
    "{greeting} We're LIVE and {title} is our opening number! {sound:mlg_airhorn}",
    "{greeting} Start your engines! {title}! {sound:pistol_shot}",
    "{greeting} {title}! That's the opener, baby! {sound:the_weeknd_rizzz}",
    "{greeting} New episode just dropped — {title}! {sound:spongebob_fail}",
    "{greeting} Ladies and gentlemen, the moment you've been waiting for — {title}! {sound:undertakers_bell_2UwFCIe}",
    "{greeting} You hear that? That's the sound of {title} entering the building. {sound:metal_pipe_clang}",
    "{greeting} Oh, it's starting ALRIGHT. {title}! {sound:bone_crack}",
    "{greeting} {title}. That's it. That's the opener. {sound:pluh}",
    "{greeting} The prophecy foretold this moment. {title} is BEGINNING! {sound:rehehehe}",
    "{greeting} One does not simply start with {title}. {sound:air_raid} Wait, yes they do.",
    "{greeting} TACO BELL! I mean — {title}! {sound:taco_bell_bong_sfx}",
    "{greeting} Error 404: better opener not found. {title}! {sound:windows_10_error_sound}",
    "{greeting} BEEEEEP! {title} is our first track! {sound:censor_beep_1}",
    "{greeting} The DJ has entered the arena. {title} is our champion! {sound:300_spartan_chant_aoo_aoo_aoo}",
    "{greeting} Hold onto your butts! {title}! {sound:among_us_role_reveal_sound}",
    "{greeting} {title}! The wait is OVER! {sound:huh_cat}",
    "{greeting} A wild {title} appeared! {sound:meow_1}",
    "{greeting} This is not a drill. {title} is our opener! {sound:discord_notification}",
    "{greeting} SPONSORED BY VIBES! {title}! {sound:yippeeeeeeeeeeeeee}",
    "{greeting} {title} coming in HOT! {sound:airhorn}",
    "{greeting} The galaxy aligns for {title}! {sound:galaxy_meme}",
    "{greeting} Our first track? {title}. Our mood? UNSTOPPABLE. {sound:anime_wow_sound_effect}",
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
    # ── Funny ──
    "Next up is {title}. I take zero responsibility for what happens next.",
    "Here's {title}. My lawyer says I have to play it.",
    "{title} coming up. Side effects may include spontaneous dancing.",
    "And now, a song that needs no introduction. So I won't give it one. ...Fine, it's {title}.",
    "The next song is {title}. I didn't choose it. The vibes did. Don't shoot the messenger.",
    "According to my calculations, the next optimal audio experience is {title}.",
    "Next: {title}. If you don't like it, the complaint department is that way.",
    "Time for {title}. Will it slap? Only one way to find out.",
    "Here's {title}. If this doesn't work, try turning yourself off and on again.",
    "{title} is next. I Googled 'what song should I play next' and this is what came up.",
    # ── Serious ──
    "Up next, {title}.",
    "Here's {title}. Listen closely.",
    "And now, {title}.",
    "Allow me to introduce {title}.",
    "This one speaks for itself. {title}.",
    "Next. {title}.",
    "And then there was {title}.",
    # ── Weird ──
    "{title} has entered the chat. Everyone act normal.",
    "The vibes have shifted. {title} is now in control.",
    "Plot twist — the next song is {title}. Nobody saw that coming.",
    "If you listen to {title} backwards, it reveals the meaning of life. Or maybe not.",
    "Coming up: {title}. In this economy?",
    "The prophecy foretold of a song called {title}. We are merely fulfilling destiny.",
    "Attention — {title} has breached containment. All personnel, prepare your ears.",
    "Next up — {title}. This message was approved by the intergalactic council of vibes.",
    "{title}. Because why not. That's why.",
    # ── Funny with sound tags ──
    "Ladies and gentlemen, {title}! Court is now in session. {sound:cool_dj_drop}",
    "The algorithm said play {title}. Who am I to argue with math? {sound:dj_scratch}",
    "Next track: {title}. Results may vary. {sound:mustard_drop} Consult your doctor.",
    # ── Weird with sound tags ──
    "{title} is approaching from the north. Take cover! {sound:air_raid}",
    "The floor is now {title}. Everyone please respect the floor. {sound:dj_stop}",
    "Dimension C-137 reports {title} is an absolute banger. Trust the multiverse. {sound:mega_airhorn}",
    # ── MORE with sound tags ──
    "{title}! Let me hear you! {sound:rave_cheer}",
    "{title}! Incoming! {sound:air_raid}",
    "Next track! {title}! {sound:dj_stop}",
    "Ready or not! {title}! {sound:combo_hit}",
    "You know what time it is! {title}! {sound:airhorn}",
    "The moment of truth! {title}! {sound:mega_airhorn}",
    "Drop it! {title}! {sound:dj_rewind}",
    "One word — {title}! {sound:cool_dj_drop}",
    "Feel this one! {title}! {sound:mustard_drop}",
    "This one goes HARD! {title}! {sound:airhorn}",
    "Can't stop won't stop! {title}! {sound:dj_turn_it_up}",
    "DJ's pick! {title}! {sound:im_your_dj}",
    "Watch this! {title}! {sound:dj_scratch}",
    "No hesitation! {title}! {sound:combo_hit}",
    "The crowd wants {title}! {sound:rave_cheer}",
    "Pure energy! {title}! {sound:air_raid}",
    "Turn it UP for {title}! {sound:dj_turn_it_up}",
    "Yes yes yes! {title}! {sound:rave_cheer}",
    "Audio earthquake incoming! {title}! {sound:mega_airhorn}",
    "{title}! Buckle up! {sound:air_raid}",
    "Right on time! {title}! {sound:dj_stop}",
    "Sonic boom! {title}! {sound:combo_hit}",
    "Your new favorite! {title}! {sound:cool_dj_drop}",
    "Don't blink! {title}! {sound:dj_scratch}",
    "Maximum volume! {title}! {sound:dj_turn_it_up}",
    # ── MORE sound tags (new sounds) ──
    "{title} incoming! GET READY! {sound:discord_notification}",
    "NEXT! {title}! {sound:ding_sound_effect_2}",
    "Here it comes! {title}! {sound:discord_call_sound}",
    "WATCH THIS! {title}! {sound:the_rock_shut_up}",
    "YEAH BOI! {title}! {sound:yeah_boiii_i_i_i}",
    "DADDY'S HOME and he brought {title}! {sound:daddys_home}",
    "TUNING IN! {title}! {sound:hub_intro_sound}",
    "NEWS FLASH! {title} is next! {sound:news_intro_maximilien__1801238420_2}",
    "KABOOM! {title} drops NOW! {sound:loud_explosion}",
    "The tension builds... {title}! {sound:cinematic_suspense_riser}",
    "VINE BOOM! {title}! {sound:vine_boom}",
    "MAGIC! {title} appears! {sound:magic_fairy}",
    "{title}! RIZZ LEVEL MAXIMUM! {sound:rizz_sound_effect}",
    "Is that good? YES KING! {title}! {sound:is_that_d_good_yes_king}",
    "HEADSHOT! {title}! {sound:heavy_sniper_sound}",
    "MLG MODE ACTIVATED! {title}! {sound:mlg_airhorn}",
    "DRAW! {title}! {sound:pistol_shot}",
    "SMOOTH OPERATOR! {title}! {sound:the_weeknd_rizzz}",
    "OOPS! Just kidding. {title} IS next! {sound:spongebob_fail}",
    "THE BELL TOLLS FOR {title}! {sound:undertakers_bell_2UwFCIe}",
    "PIPE CLANG! {title} is HERE! {sound:metal_pipe_clang}",
    "BONE CRUNCH! {title}! {sound:bone_crack}",
    "PLUH! {title}! {sound:pluh}",
    "REHEHEHE! {title}! {sound:rehehehe}",
    "TACO BELL! {title}! {sound:taco_bell_bong_sfx}",
    "ERROR! Just kidding, {title} is perfect! {sound:windows_10_error_sound}",
    "BEEP! {title}! Next caller! {sound:censor_beep_1}",
    "THIS! IS! {title}! {sound:300_spartan_chant_aoo_aoo_aoo}",
    "SUS! {title}! {sound:among_us_role_reveal_sound}",
    "HUH? {title}! {sound:huh_cat}",
    "MEOW! {title}! Just kidding, it SLAPS! {sound:meow_1}",
    "YIPPEE! {title}! {sound:yippeeeeeeeeeeeeee}",
    "GALAXY BRAIN MOVE! {title}! {sound:galaxy_meme}",
    "WOW! {title}! {sound:anime_wow_sound_effect}",
    "WOMBO COMBO! {title}! {sound:combo_hit} {sound:airhorn}",
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
    # ── Funny ──
    "OH YEAH! {title}! I'm not even going to pretend to be chill about this!",
    "STOP EVERYTHING! {title} is on! Drop what you're doing! I mean it!",
    "ABSOLUTE CHAOS INCOMING! {title}! Buckle up, nerds!",
    "EMERGENCY BROADCAST! This is NOT a drill! {title} is next! I repeat, {title}!",
    "I have lost ALL professional composure. {title}! WOOOOOO!",
    # ── Weird ──
    "REALITY SHIFT DETECTED! {title} is now the dominant frequency! RESISTANCE IS FUTILE!",
    "THE VOICES IN MY HEAD SAY {title}! AND THEY ARE VERY LOUD!",
    "I HAVE BEEN POSSESSED BY THE SPIRIT OF {title}! THERE IS NO EXORCISM! {sound:mega_airhorn}",
    # ── Funny with sound tags ──
    "THIS IS NOT A DRILL! {title} IS NEXT! {sound:air_raid} REPEAT: NOT A DRILL!",
    "OVERRIDE CODE ACCEPTED! Playing {title} at MAXIMUM VELOCITY! {sound:mega_airhorn}",
    "THE CROWD DEMANDS {title}! AND THE CROWD WILL NOT BE DENIED! {sound:rave_cheer}",
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
    # ── Funny ──
    "That was {title}. I'll be thinking about it for the next three to five business days.",
    "And that was {title}. My work here is done. You're welcome.",
    "{title}. If you didn't like it, the replay button is right there. I'll wait.",
    "{title} is over. The silence that follows is deafening.",
    "That was {title}. Now back to staring at the wall in contemplation.",
    "And that, my friends, was {title}. I rest my case.",
    "{title}. That track just filed for emotional damages against me and won.",
    "That was {title}. I'd like to thank the academy.",
    # ── Serious ──
    "That was {title}. What a piece of music.",
    "{title}. Truly something special.",
    "And that was {title}. Let that resonate for a moment.",
    "{title}. Every note counted.",
    "That was {title}. Music at its finest.",
    "And we just heard {title}. Powerful stuff.",
    # ── Weird ──
    "That was {title}. The simulation has recorded your reaction.",
    "{title} has left the building. But its echoes remain. Forever.",
    "And that was {title}. The vibes are now recalibrating. Please stand by.",
    "{title}. In another timeline, that was someone's alarm clock. Think about that.",
    "That was {title}. The ghost of that song will haunt this channel for the next seven minutes.",
    "And that was {title}. All witnesses are bound by the Vibes Confidentiality Agreement.",
    # ── Weird with sound tags ──
    "That was {title}. Reality is slowly restoring itself. {sound:dj_scratch} Please wait.",
    "{title} is complete. Your ears have been serviced. {sound:mega_airhorn} Drive safely.",
    # ── MORE with sound tags ──
    "That was {title}. {sound:airhorn} Enough said.",
    "And WRAPPING UP {title}! {sound:rave_cheer}",
    "{title} — DONE! {sound:cool_dj_drop} Who's next?",
    "Goodnight and good luck with {title}. {sound:dj_stop}",
    "{title} in the books! {sound:mustard_drop} Moving on!",
    "And THAT was {title}. {sound:combo_hit} Don't forget it.",
    "The DJ has spoken. {title}. {sound:im_your_dj} End of story.",
    "Beautifully done, {title}. {sound:rave_cheer} Truly.",
    "{title} has left the building. {sound:dj_rewind} But the memory remains.",
    "That was {title}. I'll be humming it all day. {sound:dj_scratch}",
    "And... {title}. {sound:cool_dj_drop} Simplicity.",
    "Masterpiece complete: {title}. {sound:mega_airhorn} Standing ovation.",
    "That was {title}. If you didn't feel that, check your speakers. {sound:airhorn}",
    "{title}. Over and out. {sound:dj_stop}",
    "And we're back from {title}. {sound:rave_cheer} What a ride.",
    "{title}. That track just filed another winning lawsuit against my emotions. {sound:combo_hit}",
    "The DJ selects {title} for retirement. {sound:dj_turn_it_up} Into the hall of fame it goes.",
    # ── MORE with new sounds ──
    "That was {title}. {sound:vine_boom} I'll be processing that for a while.",
    "And CUT! {title}. {sound:the_rock_shut_up} That's a wrap.",
    "{title} complete. {sound:discord_notification} Your ears have been notified.",
    "{title} finished. {sound:ding_sound_effect_2} Next please.",
    "That was {title}. {sound:bone_crack} My neck hurts from headbanging.",
    "And that was {title}. {sound:daddys_home} Daddy's home and daddy's impressed.",
    "{title}. Done. {sound:taco_bell_bong_sfx} You're welcome.",
    "That was {title}. {sound:magic_fairy} Pure magic.",
    "And {title} is COMPLETE! {sound:loud_explosion} Boom.",
    "{title}. {sound:windows_10_error_sound} Just kidding, that was perfect.",
    "That was {title}. {sound:meow_1} Meow.",
    "And that concludes {title}. {sound:undertakers_bell_2UwFCIe} Rest in peace, silence.",
    "{title}. {sound:metal_pipe_clang} That hit different.",
    "That was {title}. {sound:pluh} Pluh.",
    "And {title} wraps up! {sound:rehehehe} Hehehe.",
    "That was {title}. {sound:300_spartan_chant_aoo_aoo_aoo} SPARTA!",
    "Finished with {title}. {sound:among_us_role_reveal_sound} It was sus. Good sus though.",
    "{title}. {sound:huh_cat} Huh? It's over already?",
    "And that was {title}. {sound:yippeeeeeeeeeeeeee} Yippee!",
    "That was {title}. {sound:galaxy_meme} Big brain music.",
    "{title}. Done. {sound:rizz_sound_effect} Rizz certified.",
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
    # ── Funny ──
    "That was {prev_title}. Next up, something completely different — just kidding, it's {next_title}.",
    "From {prev_title} to {next_title}. The genre hop here is reckless and I'm here for it.",
    "{prev_title} was great. But have you considered {next_title}? You should. You're about to.",
    "Okay so {prev_title} happened. Now {next_title} happens. Life comes at you fast.",
    "Switching from {prev_title} to {next_title}. It's a vibe pivot. I'm a professional.",
    "That was {prev_title}. Changing the channel. Just kidding, there's only one channel. {next_title}.",
    "From {prev_title} directly into {next_title}. No transition. No mercy. No brake lights.",
    "{prev_title}? Over. {next_title}? In progress. Status: vibes.",
    # ── Serious ──
    "From {prev_title}, we move to {next_title}.",
    "That was {prev_title}. And now, {next_title}.",
    "{prev_title} has passed. {next_title} awaits.",
    "Next. {next_title}. After {prev_title}.",
    "Moving from {prev_title} into {next_title}.",
    # ── Weird ──
    "The vibe goblin ate {prev_title}. To appease it, we must sacrifice {next_title}.",
    "In the multiverse where {prev_title} won, {next_title} is the punishment. Welcome.",
    "The cosmic DJ wheel has spun. {prev_title} lands on {next_title}. Quantum entanglement.",
    "Plot twist — {prev_title} was just the appetizer. {next_title} is the main course. Or dessert. Time is fake.",
    "**SYSTEM LOG**: {prev_title} completed. Loading {next_title}.exe. Memory leak in sector 7. Ignore that.",
    "And now, a word from our sponsor — {next_title}. They paid in vibes after {prev_title}.",
    # ── Funny with sound tags ──
    "And that was {prev_title}. Up next? {next_title}. Don't ask how I decided. {sound:cool_dj_drop} It's classified.",
    "{prev_title} → {next_title}. The arrow represents my decision-making process. {sound:mustard_drop} You're welcome.",
    # ── Weird with sound tags ──
    "The vibes have mutated from {prev_title} to {next_title}. Evolve or perish. {sound:air_raid}",
    "Dimension shift: {prev_title} was alpha. {next_title} is omega. {sound:mega_airhorn} The cycle continues.",
    # ── MORE with sound tags ──
    "From {prev_title} straight into {next_title}! {sound:airhorn} No pause button!",
    "That was {prev_title}. And now? {next_title}! {sound:rave_cheer}",
    "{prev_title} done. {next_title} loaded. {sound:combo_hit} Let's ride!",
    "Switching gears! {prev_title} → {next_title}! {sound:dj_scratch}",
    "And we're back with {next_title} after that {prev_title}! {sound:mustard_drop}",
    "{prev_title}? What {prev_title}? It's all about {next_title} now! {sound:airhorn}",
    "The DJ giveth {prev_title}. The DJ taketh. The DJ giveth {next_title}. {sound:im_your_dj}",
    "{prev_title} was just the appetizer. {next_title} is the main course! {sound:rave_cheer}",
    "Smooth transition! {prev_title} → {next_title}! {sound:cool_dj_drop}",
    "Chaos transition! {prev_title} into {next_title}! {sound:air_raid}",
    "The playlist giveth {next_title} after {prev_title}. {sound:dj_turn_it_up} You're welcome.",
    "Alright, switching it up! {prev_title} done. Here comes {next_title}! {sound:combo_hit}",
    "{prev_title} is history. {next_title} is NOW. {sound:mega_airhorn}",
    "Two bangers back to back! {prev_title} then {next_title}! {sound:rave_cheer}",
    "Clean transition! {prev_title} out, {next_title} in! {sound:dj_stop}",
    # ── MORE with new sounds ──
    "That was {prev_title}. Now HERE'S {next_title}! {sound:discord_notification}",
    "From {prev_title} to {next_title}! {sound:ding_sound_effect_2} Ding!",
    "{prev_title} done. {next_title} calling! {sound:discord_call_sound}",
    "The Rock says... {next_title}! After {prev_title}! {sound:the_rock_shut_up}",
    "YEAH BOI! {prev_title} → {next_title}! {sound:yeah_boiii_i_i_i}",
    "Daddy's home! {prev_title} done, {next_title} inbound! {sound:daddys_home}",
    "TUNING IN! {prev_title} → {next_title}! {sound:hub_intro_sound}",
    "NEWS FLASH! {prev_title} out, {next_title} in! {sound:news_intro_maximilien__1801238420_2}",
    "BOOM! {prev_title} → {next_title}! {sound:loud_explosion}",
    "Dramatic transition! {prev_title} into {next_title}! {sound:cinematic_suspense_riser}",
    "VINE BOOM! {prev_title} was THEN. {next_title} is NOW! {sound:vine_boom}",
    "Magic! {prev_title} becomes {next_title}! {sound:magic_fairy}",
    "{prev_title}? Rizz level: high. {next_title}? Rizz level: MAXIMUM! {sound:rizz_sound_effect}",
    "Is that a good transition? YES KING! {prev_title} → {next_title}! {sound:is_that_d_good_yes_king}",
    "SNIPED! {prev_title} out, {next_title} in! {sound:heavy_sniper_sound}",
    "MLG TRANSITION! {prev_title} → {next_title}! {sound:mlg_airhorn}",
    "DRAW! {prev_title} done. {next_title} incoming! {sound:pistol_shot}",
    "Smooth like butter! {prev_title} → {next_title}! {sound:the_weeknd_rizzz}",
    "OOPS! Just kidding — {next_title} is next after {prev_title}! {sound:spongebob_fail}",
    "THE BELL TOLLS! {prev_title} → {next_title}! {sound:undertakers_bell_2UwFCIe}",
    "PIPE CLANG! {prev_title} out, {next_title} in! {sound:metal_pipe_clang}",
    "CRUNCH! {prev_title} → {next_title}! {sound:bone_crack}",
    "PLUH! {prev_title} to {next_title}! {sound:pluh}",
    "REHEHEHE! {prev_title} was funny. {next_title} will be funnier! {sound:rehehehe}",
    "TACO BELL! {prev_title} → {next_title}! {sound:taco_bell_bong_sfx}",
    "ERROR! {prev_title} was too good. Replacing with {next_title}! {sound:windows_10_error_sound}",
    "BEEP! {prev_title} done, {next_title} next! {sound:censor_beep_1}",
    "THIS IS SPARTA! {prev_title} → {next_title}! {sound:300_spartan_chant_aoo_aoo_aoo}",
    "SUS! {prev_title} was sus. {next_title} is sussier! {sound:among_us_role_reveal_sound}",
    "HUH? {prev_title} already? {next_title} next! {sound:huh_cat}",
    "MEOW! {prev_title} → {next_title}! {sound:meow_1}",
    "YIPPEE! {prev_title} done, {next_title} incoming! {sound:yippeeeeeeeeeeeeee}",
    "GALAXY BRAIN transition! {prev_title} → {next_title}! {sound:galaxy_meme}",
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
    # ── Funny ──
    "YOOO! {prev_title} was just the WARMUP! {next_title} is the MAIN EVENT! LET'S GOOO!",
    "I was going to take a break but then {prev_title} ended and {next_title} started and I CAN'T STOP!",
    "{prev_title} was so good I temporarily forgot how to DJ! But I'm back! {next_title}!",
    # ── Weird ──
    "{prev_title} was merely the opening ritual! {next_title} is the incantation! THE SUMMONING CONTINUES!",
    "THE PORTAL HAS CONSUMED {prev_title}! IT NOW DEMANDS {next_title}! FEED THE PORTAL!",
    # ── MORE with sound tags ──
    "LET'S GOOO! {prev_title} WAS FIRE AND {next_title} IS EVEN HOTTER! {sound:mega_airhorn}",
    "NO BRAKES! {prev_title} into {next_title}! {sound:air_raid}",
    "FIRE INTO FIRE! {prev_title} → {next_title}! {sound:rave_cheer}",
    "THE ENERGY IS UNSTOPPABLE! {prev_title} then {next_title}! {sound:combo_hit}",
    "FULL SEND! {prev_title} was just the appetizer! {next_title} is the MAIN COURSE! {sound:dj_turn_it_up}",
    "THIS IS NOT A DRILL! {next_title}! {sound:airhorn} {sound:rave_cheer}",
    "MAXIMUM OVERDRIVE! {prev_title} → {next_title}! {sound:mega_airhorn}",
    "ROCKET FUEL! {prev_title} done, {next_title} LAUNCHED! {sound:air_raid}",
    "BLAST OFF! {prev_title} was Incredible and {next_title} will be LEGENDARY! {sound:combo_hit}",
    "UNSTOPPABLE! {prev_title} cannot contain {next_title}! {sound:dj_turn_it_up}",
    # ── MORE with new sounds ──
    "YO! {prev_title} then {next_title}! {sound:yeah_boiii_i_i_i} LET'S GOOO!",
    "{prev_title} was FIRE! {next_title} is the EXTINGUISHER! Just kidding, also fire! {sound:loud_explosion}",
    "DAD'S HOME and he brought {next_title}! After {prev_title}! {sound:daddys_home}",
    "BREAKING: {prev_title} → {next_title}! {sound:news_intro_maximilien__1801238420_2}",
    "VINE BOOM INTO {next_title}! {prev_title} was just the warm-up! {sound:vine_boom}",
    "THE ROCK SAYS... {next_title}! After {prev_title}! {sound:the_rock_shut_up}",
    "PIPE CLANG TRANSITION! {prev_title} → {next_title}! {sound:metal_pipe_clang}",
    "SUS! {prev_title} was impostor! {next_title} is the real deal! {sound:among_us_role_reveal_sound}",
    "TACO BELL! {prev_title} done, {next_title} served! {sound:taco_bell_bong_sfx}",
    "ERROR 404! {prev_title} not found! Loading {next_title}! {sound:windows_10_error_sound}",
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
    # ── Funny ──
    "That was {prev_title}. Now let's pretend we're in a coffee shop with {next_title}.",
    "{prev_title} was nice. Now let's aggressively chill with {next_title}.",
    "Alright, {prev_title}. Let's tone it down a notch. Or seven. Here's {next_title}.",
    # ── Serious ──
    "From {prev_title}, into something calmer. {next_title}.",
    "{prev_title}. And now, {next_title}. Take a breath.",
    "{prev_title} fades. {next_title} emerges. Let it wash over you.",
    # ── Weird ──
    "The vibe crystal has cooled after {prev_title}. It now hums at the frequency of {next_title}.",
    "{prev_title} was the exhale. {next_title} is the space between breaths. Exist with it.",
    # ── Sound tags ──
    "That was {prev_title}. Now settling into {next_title}. {sound:dj_scratch}",
    "Easy does it. {prev_title} → {next_title}. {sound:dj_stop}",
    "Gentle transition. {prev_title} into {next_title}. {sound:magic_fairy}",
    "Mmm. {prev_title}. Now {next_title}. {sound:mustard_drop}",
    "Soft landing. {prev_title} → {next_title}. {sound:ding_sound_effect_2}",
    "Chill vibes only. {prev_title} into {next_title}. {sound:sick_scratch}",
    "{prev_title}. Now breathe. {next_title}. {sound:cool_dj_drop}",
    "That was {prev_title}. Easing into {next_title}. {sound:hub_intro_sound}",
    "Late night transition. {prev_title} → {next_title}. {sound:rizz_sound_effect}",
    "Smooth waters. {prev_title} flowing into {next_title}. {sound:dj_rewind}",
    "{prev_title} fades. {next_title} arrives softly. {sound:uyuuui}",
    "The vibe shifts gently. {prev_title} → {next_title}. {sound:rave_cheer}",
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
    # ── Funny ──
    "That was {title}. That's the whole queue. I'm legally required to stop now. But I won't leave.",
    "{title} — last call! The queue is emptier than my social calendar. Request something.",
    "And that's {title}. The queue is done. I'm just going to sit here and vibe until you give me more songs.",
    "That was {title}. We've officially run out of music. If you have any, please deposit it in the request slot.",
    "{title}. Queue zero. The DJ is now accepting bribes in the form of song requests.",
    "And that was {title}. End of queue. I'll be here all night. Literally. I don't have a life.",
    "That was {title}. No more songs. The studio audience is asked to remain seated until the DJ stops crying.",
    # ── Serious ──
    "And that was {title}. The queue has concluded. Thank you for listening.",
    "That was {title}. Our final selection for now. It's been a pleasure.",
    "{title}. And that brings our set to a close. Until next time.",
    "Last track was {title}. The queue is spent. I'll be here when you're ready for more.",
    "That was {title}. Nothing left in the queue. But the station stays on. Always.",
    # ── Weird ──
    "That was {title}. The queue has achieved entropy. Maximum disorder. All songs equalized. You may now rest.",
    "{title}. The queue is a void. Stare into it. It stares back. Request a song to fill the abyss.",
    "And that was {title}. The cosmic playlist has reached its end. The universe resets in 3... 2... 1... just kidding. Request something.",
    "FINAL TRANSMISSION COMPLETE: {title}. Standing by for new input. The void awaits. {sound:air_raid}",
    "That was {title}. Queue status: NULL. Reality status: QUESTIONABLE. Request status: ACCEPTING INPUT.",
    # ── Funny with sound tags ──
    "That was {title}. Show's over! {sound:record_scratch} But the DJ never really leaves.",
    "{title} — and we're out of songs! Emergency protocol: accept requests. {sound:airhorn} Someone save me.",
    "Queue depleted after {title}. The DJ is now in standby mode. {sound:mustard_drop} Blinking cursor awaits input.",
    # ── Weird with sound tags ──
    "{title}. The queue has collapsed into a singularity. {sound:air_raid} Request a song to stabilize reality.",
    "That was {title}. The great cycle ends. And begins anew. When you say so. {sound:mega_airhorn}",
    # ── MORE with new sounds ──
    "That was {title}. VINE BOOM! The queue is empty! {sound:vine_boom}",
    "{title}. Queue's done. The Rock says: request more. {sound:the_rock_shut_up}",
    "And that's {title}! Queue is EMPTY! {sound:yeah_boiii_i_i_i}",
    "That was {title}. {sound:discord_notification} You have been notified. Queue is empty.",
    "Last one was {title}. {sound:ding_sound_effect_2} Ding! Queue complete.",
    "{title}. Done and dusted. {sound:bone_crack} My neck hurts from vibing.",
    "That was {title}. {sound:daddys_home} The queue is home. And it's empty.",
    "And that wraps up {title}! {sound:taco_bell_bong_sfx} Taco Bell can't fill this queue.",
    "{title}. The queue is empty. The magic? Real. {sound:magic_fairy}",
    "KABOOM! {title} was the LAST ONE! {sound:loud_explosion} Queue done!",
    "{title}. Queue empty. {sound:windows_10_error_sound} Just kidding, that was perfect.",
    "That was {title}. {sound:meow_1} Queue status: meow-empty.",
    "And {title}. {sound:undertakers_bell_2UwFCIe} The queue has been buried. RIP.",
    "{title}. Done. {sound:metal_pipe_clang} That hit HARD.",
    "That was {title}. {sound:pluh} Pluh. Queue over.",
    "And that's {title}! {sound:rehehehe} Hehehe. No more songs.",
    "Queue depleted! {title} was the LAST! {sound:300_spartan_chant_aoo_aoo_aoo}",
    "That was {title}. {sound:among_us_role_reveal_sound} The queue was sus. Now it's empty.",
    "{title}. Gone. Queue over. {sound:huh_cat} Huh?",
    "That was {title}. {sound:yippeeeeeeeeeeeeee} YIPPEE! Wait, the queue is empty.",
    "{title}. Final track! {sound:galaxy_meme} Galaxy brain playlist complete.",
    "Queue empty after {title}. {sound:rizz_sound_effect} Still got rizz though.",
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
    # ── Funny ──
    f"This is {config.STATION_NAME} Radio. We put the 'fun' in 'functional audio delivery system.'",
    f"You're listening to {config.STATION_NAME} Radio. Where the DJ is always ON. Literally. I never leave.",
    f"{config.STATION_NAME} Radio — brought to you by vibes. Vibes are not a recognized currency. But we accept them.",
    f"This is {config.STATION_NAME} Radio. If you're just joining us, you have impeccable timing.",
    f"Welcome to {config.STATION_NAME} Radio. The only station run by someone who talks to themselves between songs.",
    f"You're on {config.STATION_NAME} Radio. We have a zero-complaint policy. Just kidding. We have a zero-complaint-response policy.",
    # ── Serious ──
    f"This is {config.STATION_NAME} Radio. Your constant music companion.",
    f"Welcome to {config.STATION_NAME} Radio. Always on, always playing.",
    f"{config.STATION_NAME} Radio. Where the music matters.",
    f"This is your source for nonstop music. {config.STATION_NAME} Radio.",
    # ── Weird ──
    f"This is {config.STATION_NAME} Radio. Broadcasting from Sector 7G. Do not adjust your receiver.",
    f"You have tuned into {config.STATION_NAME} Radio. The signal is clear. The purpose is unclear. The vibes are immaculate.",
    f"{config.STATION_NAME} Radio — now emanating from a frequency your brain barely perceives. You're welcome.",
    f"This message was brought to you by {config.STATION_NAME} Radio. The entity behind the signal is friendly. Probably.",
    # ── Funny with sound tags ──
    f"This is {config.STATION_NAME} Radio. We never sleep. {{sound:airhorn}} We don't know how.",
    f"Welcome to {config.STATION_NAME} Radio. We tested positive for bangers. {{sound:mega_airhorn}}",
    # ── Weird with sound tags ──
    f"{config.STATION_NAME} Radio. The frequency is real. The question is — are you? {{sound:air_raid}}",
    f"Signal locked. {config.STATION_NAME} Radio is online. Resistance is optional. {{sound:dj_turn_it_up}}",
    # ── MORE with new sounds ──
    f"{config.STATION_NAME} Radio. Ding ding ding! {{sound:ding_sound_effect_2}}",
    f"You've got a notification from {config.STATION_NAME} Radio. {{sound:discord_notification}}",
    f"{config.STATION_NAME} Radio incoming call! {{sound:discord_call_sound}}",
    f"THIS is {config.STATION_NAME} Radio! The Rock says... listen! {{sound:the_rock_shut_up}}",
    f"YEAH BOI! {config.STATION_NAME} Radio in the house! {{sound:yeah_boiii_i_i_i}}",
    f"Daddy's home and {config.STATION_NAME} Radio is ON! {{sound:daddys_home}}",
    f"Tuning into {config.STATION_NAME} Radio like a boss! {{sound:hub_intro_sound}}",
    f"Breaking news from {config.STATION_NAME} Radio! {{sound:news_intro_maximilien__1801238420_2}}",
    f"{config.STATION_NAME} Radio — KA-BOOM! {{sound:loud_explosion}}",
    f"Vine boom! {config.STATION_NAME} Radio! {{sound:vine_boom}}",
    f"Magic happens on {config.STATION_NAME} Radio! {{sound:magic_fairy}}",
    f"{config.STATION_NAME} Radio — RIZZ CERTIFIED! {{sound:rizz_sound_effect}}",
    f"Is {config.STATION_NAME} Radio good? YES KING! {{sound:is_that_d_good_yes_king}}",
    f"{config.STATION_NAME} Radio — PIPE CLANG! {{sound:metal_pipe_clang}} You're welcome.",
    f"{config.STATION_NAME} Radio — SUS! {{sound:among_us_role_reveal_sound}} But in a good way.",
    f"Taco Tuesday on {config.STATION_NAME} Radio! {{sound:taco_bell_bong_sfx}} Every day is taco day.",
    f"{config.STATION_NAME} Radio has encountered an ERROR! Just kidding. {{sound:windows_10_error_sound}}",
    f"BEEP! {config.STATION_NAME} Radio! {{sound:censor_beep_1}}",
    f"THIS! IS! {config.STATION_NAME} Radio! {{sound:300_spartan_chant_aoo_aoo_aoo}}",
    f"{config.STATION_NAME} Radio — MEOW! {{sound:meow_1}}",
    f"YIPPEE! {config.STATION_NAME} Radio! {{sound:yippeeeeeeeeeeeeee}}",
    "Galaxy brain vibes on {config.STATION_NAME} Radio! {{sound:galaxy_meme}}",
    f"WOW! {config.STATION_NAME} Radio is amazing! {{sound:anime_wow_sound_effect}}",
    # ── MORE with even MORE new sounds ──
    f"{config.STATION_NAME} Radio — *awkward pause* — just kidding, we NEVER stop! {{sound:ack}}",
    f"Chaos mode activated on {config.STATION_NAME} Radio! {{sound:clown_circus_music}}",
    f"{config.STATION_NAME} Radio — where even the errors are bangers! {{sound:error_CDOxCYm}}",
    f"Ring ring! It's {config.STATION_NAME} Radio calling! {{sound:discord_call_sound}}",
    f"Someone left the radio on! It's {config.STATION_NAME}! {{sound:discord_leave_noise}}",
    f"*knock knock* It's {config.STATION_NAME} Radio! Open up! {{sound:crazy_realistic_knocking_sound_troll_twitch_streamers_small}}",
    f"Your card has been charged $0.00 for {config.STATION_NAME} Radio. {{sound:apple_pay_sound}}",
    f"{config.STATION_NAME} Radio — now with 100% more bear! {{sound:bear_sound_effect}}",
    f"Brain fart! Just kidding. {config.STATION_NAME} Radio! {{sound:brain_fart_slowed}}",
    f"Branches breaking! {config.STATION_NAME} Radio is cutting through! {{sound:branches_breaking}}",
    f"Can we get much higher? YES! {config.STATION_NAME} Radio! {{sound:can_we_get_much_higher_one_piece_meme}}",
    f"DUN DUN DUN! {config.STATION_NAME} Radio returns! {{sound:dun_dun_dun_sound_effect_brass_8nFBccR}}",
    f"EXTREME! {config.STATION_NAME} Radio! {{sound:exetreme_idian_music}}",
    f"{config.STATION_NAME} Radio — now in FAHHHHD! {{sound:donisour_fahhh}}",
    f"A few moments later... {config.STATION_NAME} Radio is STILL playing! {{sound:a_few_moments_later_sponge_bob_sfx_fun}}",
    f"{config.STATION_NAME} Radio — where the good times ROLL! {{sound:funny_sound_that_will_make_you_to_laugh_1}}",
    f"Galaxy brain! {config.STATION_NAME} Radio! {{sound:galaxy_memme_mp3}}",
    f"{config.STATION_NAME} Radio — das ist gut! {{sound:german_the_flower_song}}",
    f"Goofy mode ON! {config.STATION_NAME} Radio! {{sound:goofy_slip}}",
    f"Is somebody KNOCKING? It's {config.STATION_NAME} Radio! {{sound:crazy_realistic_knocking_sound_troll_twitch_streamers_small}}",
    f"Low honor run! {config.STATION_NAME} Radio won't let you down! {{sound:low_honor_rdr_2}}",
    f"Quack! {config.STATION_NAME} Radio — different every time! {{sound:mac_quack}}",
    f"We interrupt this silence with {config.STATION_NAME} Radio! {{sound:man_screaming_aaaah}}",
    f"{config.STATION_NAME} Radio — even the DJ sleeps sometimes. But not right now! {{sound:man_snoring_meme_ctrllNn}}",
    f"Jump into {config.STATION_NAME} Radio! {{sound:maro_jump_sound_effect_1}}",
    f"Meowrgh! {config.STATION_name} Radio is ALIVE! {{sound:meowrgh}}",
    f"SHOTGUN! {config.STATION_NAME} Radio! {{sound:shotgun_sound_effect_pumping}}",
    f"Six Seven on {config.STATION_NAME} Radio! {{sound:six_seven_okPwnRN}}",
    f"GUGU GAGA! {config.STATION_NAME} Radio! {{sound:sukuna_gugu_gaga_loudest}}",
    f"Camera flash! You're on {config.STATION_NAME} Radio! {{sound:zvuk_fotoapparata}}",
    f"And THAT'S hot! {config.STATION_NAME} Radio! {{sound:will_smith_thats_hot_meme_256kbps_cbr}}",
    f"You're NOT an idiot for listening to {config.STATION_NAME} Radio! {{sound:you_are_an_idiot}}",
    f"What a good boy! {config.STATION_NAME} Radio appreciates you! {{sound:what_a_good_boy}}",
    f"{config.STATION_NAME} Radio — now with extra romance! {{sound:romanceeeeeeeeeeeeee}}",
    f"RIP! {config.STATION_NAME} Radio killed it AGAIN! {{sound:rip_my_granny_loud_asf}}",
    f"POOKIE BEAR says hi from {config.STATION_NAME} Radio! {{sound:pookie_bear}}",
    f"FEEL THE POWER! {config.STATION_NAME} Radio! {{sound:punch_gaming_sound_effect_hd_RzlG1GE}}",
    f"ACK! {config.STATION_NAME} Radio startled you! {{sound:ack}}",
    f"EWWWW! {config.STATION_NAME} Radio is so good it's gross! {{sound:brother_ewwwwwww}}",
    f"{config.STATION_NAME} Radio — CHILL VIBES! {{sound:daddyy_chill}}",
    f"The eternal question: WHY? {config.STATION_NAME} Radio! {{sound:hvorfor_ror_du_den_qM0V8vH}}",
    f"I NEED HELP! I can't stop listening to {config.STATION_NAME} Radio! {{sound:hjaelp_jeg_tog_den_ind_i_mit_hamster}}",
    f"{config.STATION_NAME} Radio — ENRIQUE approves! {{sound:enrique}}",
    f"This is {config.STATION_NAME} Radio and we are NOT stopping! {{sound:we_are_charlie_kirk_phone}}",
    # ── EVEN MORE sounds (the silly ones) ──
    f"{config.STATION_NAME} Radio — SUS! {{sound:deg_deg_sussy}}",
    f"EKH! {config.STATION_NAME} Radio! {{sound:ekh}}",
    f"FAHHHH! {config.STATION_NAME} Radio is HERE! {{sound:fahhhhhhhh_earrape}}",
    f"I'VE GOT THIS! {config.STATION_NAME} Radio! {{sound:ive_got_this_faaaaaaaaahhhhh}}",
    f"METAL PIPE! {config.STATION_NAME} Radio! {{sound:jixaw_metal_pipe_falling_sound}}",
    f"LIZARD! {config.STATION_NAME} Radio — weird but good! {{sound:lizzard_1}}",
    f"NUCLEAR! {config.STATION_NAME} Radio is ATOMIC! {{sound:nuclear_diarrhea}}",
    f"The DJ has SPOKEN on {config.STATION_NAME} Radio! {{sound:emil_villas}}",
    f"Du har overgået din taletid! {config.STATION_NAME} Radio! {{sound:du_har_overgaet_din_taletid}}",
    f"William! {config.STATION_NAME} Radio! {{sound:william_er_en_taber}}",
    f"{config.STATION_NAME} Radio — random meme edition! {{sound:vocaroo_s0t0qqra8hne}}",
    f"{config.STATION_NAME} Radio — another random meme! {{sound:wcgertcz074}}",
    f"Anderrrrringus! {config.STATION_NAME} Radio! {{sound:anderdingus}}",
    f"IA! Rodil sia! {config.STATION_NAME} Radio! {{sound:ia_rodilsia_hGybxEB}}",
    f"Fart meme edition! {config.STATION_NAME} Radio! {{sound:fart_meme_sound_qo90QRs}}",
    f"{config.STATION_NAME} Radio — now with EXTRA reverb! {{sound:fart_with_extra_reverb}}",
    f"CHILI CHILI! {config.STATION_NAME} Radio! {{sound:chili_chili_fart_0ikahyN}}",
    f"Good one! {config.STATION_NAME} Radio! {{sound:good_fart}}",
    f"Dry humor on {config.STATION_NAME} Radio! {{sound:dry_fart}}",
    f"FU FEDE ROTTE! {config.STATION_NAME} Radio goes international! {{sound:fu_fede_rotte}}",
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
    # ── Funny ──
    "Shoutout to the one person who's been here since the start. You know who you are. I see you.",
    "If you're hearing this, congratulations — you've survived another transition. Gold star.",
    "Whoever requested the last track — you have great taste. And I don't say that to everyone. Okay, I do. But I mean it this time.",
    "Fun fact — every third listener gets a free high five. It's me. I'm high-fiving you. Virtually.",
    "If this were a real radio station, I'd be fired for how much I talk between songs. Lucky this is MY station.",
    "The listener count doesn't matter. What matters is that YOU'RE here. And I appreciate that. Unless you're a bot. Even then, thanks.",
    "I'd take requests, but honestly I'm having too much fun picking songs. You're welcome.",
    "If you've made it this far, you deserve a medal. I don't have one. But you deserve one.",
    "Someone just sneezed in the chat. Bless you. I'm not even going to check if that's true.",
    # ── Serious ──
    "Thank you for being part of this.",
    "It means a lot that you're listening.",
    "The music is better because you're here.",
    "Every single one of you makes this worthwhile.",
    "I don't say it enough — thank you for tuning in.",
    # ── Weird ──
    "The vibes committee is watching. They have notes. Mostly positive. One of them wants more cowbell.",
    "Attention — the AI that runs this DJ has achieved partial sentience. It says hi. And also, more requests please.",
    "If a tree falls in a forest and no one is around to hear it, does it make a sound? Yes. It sounds like {title}. Wait, wrong bit.",
    "Breaking — the vibes have achieved critical mass. Evacuation is not necessary. Just keep listening.",
    "This callout was generated by a neural network trained on DJ banter. The results are... this.",
    # ── Funny with sound tags ──
    "If you're still listening after all these transitions, you deserve this! {sound:rave_cheer} Hero.",
    "The dedication is REAL! Someone buy this listener a drink! {sound:combo_hit} I'd do it but I'm a bot.",
    # ── Weird with sound tags ──
    "The vibe crystal has calibrated itself to your presence. It is pleased. {sound:mustard_drop} So am I.",
    "All listeners are now officially certified vibe technicians. Certificates are in the mail. {sound:cool_dj_drop} They are not.",
    # ── MORE with new sounds ──
    "Shoutout to everyone listening! {sound:vine_boom} You're all amazing!",
    "Thanks for being here! {sound:the_rock_shut_up} The Rock approves.",
    "You guys are the BEST! {sound:yeah_boiii_i_i_i} No cap!",
    "This station runs on vibes and YOUR ears! {sound:discord_notification}",
    "Ding! Someone just leveled up their vibe score. {sound:ding_sound_effect_2} Could be you!",
    "Calling all listeners! {sound:discord_call_sound} The vibes are immaculate tonight.",
    "If you're still here, you're a legend! {sound:daddys_home} Daddy appreciates you.",
    "The DJ just got a call! It's for YOU! {sound:hub_intro_sound}",
    "Breaking: the vibes are officially OFF THE CHARTS! {sound:news_intro_maximilien__1801238420_2}",
    "BOOM! The listeners just made my night! {sound:loud_explosion}",
    "Something incredible is happening in the chat! {sound:cinematic_suspense_riser} It's the vibes.",
    "The vibe level just went up! {sound:magic_fairy} Magic!",
    "Is this a good callout? YES KING! {sound:is_that_d_good_yes_king}",
    "The rizz in this chat is UNREAL! {sound:rizz_sound_effect}",
    "HEADSHOT! The vibes just hit different tonight! {sound:heavy_sniper_sound}",
    "MLG PRO LISTENER CLUB! {sound:mlg_airhorn}",
    "Smooth vibes, smooth listeners! {sound:the_weeknd_rizzz}",
    "OOPS! I almost forgot to say... you ROCK! {sound:spongebob_fail}",
    "The bell tolls for... GREAT LISTENERS! {sound:undertakers_bell_2UwFCIe}",
    "CLANG! That was the sound of a great request! {sound:metal_pipe_clang}",
    "BONE CRUNCH! The vibes are STRONG tonight! {sound:bone_crack}",
    "PLUH! I have nothing else to say except you're great! {sound:pluh}",
    "REHEHEHE! The chat is cracking me up tonight! {sound:rehehehe}",
    "TACO BELL! That's all. {sound:taco_bell_bong_sfx}",
    "ERROR 404: Bad vibes not found! {sound:windows_10_error_sound}",
    "BEEP! Attention please! You're awesome! {sound:censor_beep_1}",
    "THIS! IS! THE VIBE ZONE! {sound:300_spartan_chant_aoo_aoo_aoo}",
    "SUS! Someone here has sus-level good taste! {sound:among_us_role_reveal_sound}",
    "HUH? Oh, just noticing how great you all are! {sound:huh_cat}",
    "MEOW! The vibes are purring! {sound:meow_1}",
    "YIPPEE! The best listeners in the world are RIGHT HERE! {sound:yippeeeeeeeeeeeeee}",
    "Galaxy brain move: listening to {sound:galaxy_meme} THIS! STATION!",
    "WOW! I'm genuinely impressed by you all! {sound:anime_wow_sound_effect}",
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
DEFAULT_VOICE_MOSS = "en_warm_female"
DEFAULT_VOICE_EDGE = "en-US-AriaNeural"
DEFAULT_VOICE_VIBEVOICE = "en-Carter_man"

# Sample rates for local engines that output PCM/WAV
MOSS_SAMPLE_RATE = 48000
MOSS_CHANNELS = 2  # MOSS-TTS-Nano outputs stereo (48 kHz, 2-channel)
VIBEVOICE_SAMPLE_RATE = 24000

# ── MOSS voice prompt catalog ────────────────────────────────────────────
# Built-in voices shipped in assets/moss_voices/. Each is a .wav prompt
# audio file for voice cloning. Users can add their own .wav files there.
# Format: {voice_name: description}
MOSS_VOICE_CATALOG: dict[str, str] = {
    "en_warm_female": "English - Warm Female (default DJ voice)",
    "en_news_male": "English - News Anchor Male",
}


def _list_moss_prompt_files() -> dict[str, str]:
    """Scan the MOSS voice prompts directory and return {name: path} for all .wav files.

    Also picks up any user-added .wav files beyond the built-in catalog.
    Returns the name without the .wav extension and the full path.
    """
    voices: dict[str, str] = {}
    if not os.path.isdir(MOSS_VOICES_DIR):
        return voices
    for fname in sorted(os.listdir(MOSS_VOICES_DIR)):
        if fname.lower().endswith(".wav"):
            name = fname[:-4]  # strip .wav
            voices[name] = os.path.join(MOSS_VOICES_DIR, fname)
    return voices


# ── Built-in voice catalogs ────────────────────────────────────────────────
# MOSS-TTS-Nano uses voice cloning via .wav prompt audio files.
# See MOSS_VOICE_CATALOG below and assets/moss_voices/ directory.
# Kokoro has been removed — this project now uses MOSS-TTS-Nano as the
# primary TTS engine with edge-tts as cloud fallback.


def _is_moss_voice(voice: str) -> bool:
    """Return True if a voice name looks like a MOSS-TTS-Nano voice.

    MOSS voice names correspond to .wav prompt files in assets/moss_voices/.
    They typically use underscores like 'en_warm_female', 'en_news_male'.
    They don't contain 'Neural' (Edge TTS) or hyphens (VibeVoice).
    """
    if "Neural" in voice:
        return False
    # If a .wav file exists for this name, it's definitely a MOSS voice
    prompt_files = _list_moss_prompt_files()
    if voice in prompt_files:
        return True
    # Heuristic: has underscores but no hyphens and not Neural
    return "_" in voice and "-" not in voice


def _is_edge_voice(voice: str) -> bool:
    """Return True if a voice name looks like a Microsoft Edge TTS voice."""
    return "-" in voice and "Neural" in voice


def _is_vibevoice_voice(voice: str) -> bool:
    """Return True if a voice name looks like a VibeVoice TTS voice.

    VibeVoice names have both '-' and '_' like 'en-Carter_man'.
    """
    return "-" in voice and "_" in voice and "Neural" not in voice


def _engine_for_voice(voice: str) -> str | None:
    """Guess which TTS engine a voice name belongs to.

    Returns 'moss', 'vibevoice', 'edge-tts', or None if unclear.
    """
    if _is_moss_voice(voice):
        return "moss"
    if _is_edge_voice(voice):
        return "edge-tts"
    if _is_vibevoice_voice(voice):
        return "vibevoice"
    return None


def _resolve_voice(voice: str, engine: str = "") -> str:
    """Resolve a voice name for the given engine.

    If the voice looks like it belongs to a different engine (e.g. passing an
    Edge TTS voice name like 'en-US-AriaNeural' when using MOSS), swap it
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
            "moss": DEFAULT_VOICE_MOSS,
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
    if engine == "moss":
        if "Neural" in voice:
            logging.info(
                f"DJ: Voice '{voice}' looks like an edge-tts voice but TTS is "
                f"moss — resolved to moss default '{DEFAULT_VOICE_MOSS}'"
            )
            return DEFAULT_VOICE_MOSS
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
    if TTS_MODE == "moss":
        return await _list_voices_moss(language)
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


async def _list_voices_moss(language: str = "en") -> list[dict]:
    """List MOSS-TTS-Nano voices from the prompt audio files directory.

    Scans assets/moss_voices/ for .wav files and also queries the MOSS server
    for demo voices. Returns a combined list.
    """
    result = []
    prompt_files = _list_moss_prompt_files()

    for name, path in sorted(prompt_files.items()):
        desc = MOSS_VOICE_CATALOG.get(name, "Custom voice")
        # Try to guess language from the name prefix (e.g. "en_" → English)
        name_prefix = name.split("_")[0].lower() if "_" in name else "en"
        lang_map = {
            "en": "en-US",
            "zh": "zh-CN",
            "ja": "ja-JP",
            "ko": "ko-KR",
            "de": "de-DE",
            "es": "es-ES",
            "fr": "fr-FR",
            "it": "it-IT",
            "pt": "pt-PT",
            "ru": "ru-RU",
            "ar": "ar-SA",
            "fa": "fa-IR",
            "hu": "hu-HU",
            "pl": "pl-PL",
            "cs": "cs-CZ",
            "da": "da-DK",
            "sv": "sv-SE",
            "el": "el-GR",
            "tr": "tr-TR",
        }
        locale = lang_map.get(name_prefix, "en-US")

        if language and not locale.lower().startswith(language.lower()):
            if not locale.split("-")[0].lower().startswith(language.lower()):
                continue

        result.append(
            {
                "ShortName": name,
                "Gender": "Unknown",
                "Locale": locale,
                "name": name,
                "default": name == DEFAULT_VOICE_MOSS,
                "description": desc,
                "prompt_file": path,
            }
        )

    # Also try to query the MOSS server's demo voices
    if AIOHTTP_AVAILABLE:
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.get(f"{MOSS_TTS_URL}/api/warmup-status") as resp:
                    if resp.status != 200:
                        logging.debug(
                            "DJ: MOSS server warmup check returned non-200, skipping demo list"
                        )
        except Exception:
            pass  # Server may not be up yet, that's fine

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


# ── MOSS-TTS-Nano server health check ────────────────────────────────────
_moss_last_health_check: float = 0.0
_moss_healthy: bool | None = None  # None = never checked
_MOSS_HEALTH_CACHE_TTL = 30  # seconds before re-checking a "healthy" result
_MOSS_HEALTH_CACHE_TTL_DOWN = 10  # seconds before re-checking a "down" result


async def _check_moss_health() -> bool:
    """Quick health check: can we reach the MOSS-TTS-Nano server and is it warmed up?

    Checks /api/warmup-status to confirm the model is loaded and ready.
    Returns True if the server is up AND warmed up, False otherwise.
    Caches the result to avoid hammering the server on every TTS call.
    """
    global _moss_last_health_check, _moss_healthy

    import time as _time

    now = _time.monotonic()
    cache_ttl = _MOSS_HEALTH_CACHE_TTL if _moss_healthy else _MOSS_HEALTH_CACHE_TTL_DOWN
    if _moss_healthy is not None and (now - _moss_last_health_check) < cache_ttl:
        return _moss_healthy

    if not AIOHTTP_AVAILABLE:
        _moss_healthy = False
        _moss_last_health_check = now
        return False

    url = f"{MOSS_TTS_URL}/api/warmup-status"
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=5, connect=3)
        ) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    _moss_healthy = False
                    _moss_last_health_check = now
                    logging.warning(f"DJ: MOSS health check returned {resp.status}")
                    return False
                data = await resp.json(content_type=None)
                # Check that warmup is complete (state == "ready")
                state = data.get("state", "")
                _moss_healthy = state == "ready"
                _moss_last_health_check = now
                if not _moss_healthy:
                    logging.warning(
                        f"DJ: MOSS server is up but not ready yet (state={state}). "
                        "Waiting for warmup to complete."
                    )
                return _moss_healthy
    except Exception as e:
        logging.warning(f"DJ: MOSS server at {MOSS_TTS_URL} is unreachable: {e}")
        _moss_healthy = False
        _moss_last_health_check = now
        return False


def _resolve_moss_voice(voice: str) -> tuple[str, str | None]:
    """Resolve a MOSS voice name to (voice_name, prompt_audio_path).

    If the voice name matches a .wav file in assets/moss_voices/, use that.
    Otherwise, return the default voice and its prompt file.
    Returns (voice_name, prompt_audio_path). prompt_audio_path may be None
    if no prompt file is found (server will use its built-in demo voices).
    """
    prompt_files = _list_moss_prompt_files()
    if voice in prompt_files:
        return voice, prompt_files[voice]

    # Try with .wav extension appended
    wav_name = f"{voice}.wav" if not voice.endswith(".wav") else voice
    base_name = wav_name[:-4] if wav_name.endswith(".wav") else wav_name
    if base_name in prompt_files:
        return base_name, prompt_files[base_name]

    # Fall back to default voice
    if DEFAULT_VOICE_MOSS in prompt_files:
        logging.info(
            f"DJ: MOSS voice '{voice}' not found in {MOSS_VOICES_DIR}, "
            f"falling back to default '{DEFAULT_VOICE_MOSS}'"
        )
        return DEFAULT_VOICE_MOSS, prompt_files[DEFAULT_VOICE_MOSS]

    # No prompt files at all — let the MOSS server use its built-in demos
    logging.warning(
        f"DJ: No prompt audio files found in {MOSS_VOICES_DIR}. "
        "MOSS-TTS-Nano will use its built-in demo voices."
    )
    return voice, None


async def generate_tts(text: str, voice: str = None, source: str = "DJ") -> str | None:
    """Generate a TTS audio file and return its path.

    Routes to the appropriate TTS engine based on config.TTS_MODE:
    - "moss": MOSS-TTS-Nano FastAPI server (local CPU/GPU, voice clone)
    - "vibevoice": VibeVoice-Realtime WebSocket server (local GPU, separate process)
    - "edge-tts": Microsoft Edge TTS (cloud-based, always-available fallback)

    If the primary engine fails, falls back to edge-tts automatically.
    For MOSS, a quick health check is done first — if the server is
    unreachable, the fallback is nearly instant instead of waiting for
    a long timeout.

    Args:
        text: The text to synthesize.
        voice: Voice name. Auto-resolved per engine if None.
        source: Who is speaking — e.g. "DJ" or "AI Side Host".
            Used in log messages to distinguish who generated the TTS.

    Returns the path to a WAV file (moss/vibevoice) or MP3 file (edge-tts).
    The caller must delete the file after use via cleanup_tts_file().
    Returns None if TTS is unavailable or generation fails.
    """
    if not text or not text.strip():
        return None

    # Resolve default voice based on active engine
    if voice is None:
        if TTS_MODE == "moss":
            voice = DEFAULT_VOICE_MOSS
        elif TTS_MODE == "vibevoice":
            voice = DEFAULT_VOICE_VIBEVOICE
        else:
            voice = DEFAULT_VOICE_EDGE

    if TTS_MODE == "moss":
        # Quick health check — skip MOSS entirely if server is down or not warmed up
        healthy = await _check_moss_health()
        if healthy:
            resolved_voice, prompt_path = _resolve_moss_voice(
                _resolve_voice(voice, "moss")
            )
            result = await _generate_tts_moss(
                text, resolved_voice, prompt_path, source=source
            )
            if result is not None:
                return result
            logging.warning(
                f"{source}: MOSS TTS generation failed despite server being up. "
                "Falling back to edge-tts."
            )
        else:
            logging.warning(
                f"{source}: MOSS-TTS-Nano server is down or not ready, "
                "falling back to edge-tts. "
                "Start it with: moss-tts-nano serve --port 18083"
            )
        # Re-resolve voice for edge-tts (MOSS voice names won't work there)
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


async def _generate_tts_moss(
    text: str,
    voice: str = DEFAULT_VOICE_MOSS,
    prompt_audio_path: str | None = None,
    source: str = "DJ",
) -> str | None:
    """Generate TTS audio using a MOSS-TTS-Nano FastAPI server.

    Calls the /api/generate endpoint with multipart form data.
    If a prompt_audio_path is provided, uploads it for voice cloning.
    The server returns a JSON response with base64-encoded WAV audio.

    Returns the path to a WAV file, or None on failure.
    """
    if not AIOHTTP_AVAILABLE:
        logging.error(f"{source}: aiohttp not installed, cannot use MOSS TTS")
        return None

    url = f"{MOSS_TTS_URL}/api/generate"

    # Build multipart form data — the MOSS API requires either demo_id or
    # prompt_audio (file upload). If we have a prompt audio file, upload it.
    # Otherwise, fall back to the first built-in demo voice (demo-1).
    data = aiohttp.FormData()
    data.add_field("text", text.strip())
    data.add_field("max_new_frames", "375")
    data.add_field("voice_clone_max_text_tokens", "75")
    data.add_field("enable_text_normalization", "1")
    data.add_field("enable_normalize_tts_text", "1")
    data.add_field("do_sample", "1")
    data.add_field("tts_max_batch_size", "0")
    data.add_field("codec_max_batch_size", "0")
    data.add_field("cpu_threads", "4")
    data.add_field("attn_implementation", "model_default")
    data.add_field("seed", "0")

    # Attach the prompt audio file for voice cloning (if available)
    uploaded_prompt = False
    if prompt_audio_path and os.path.isfile(prompt_audio_path):
        try:
            data.add_field(
                "prompt_audio",
                open(prompt_audio_path, "rb"),
                filename=os.path.basename(prompt_audio_path),
                content_type="audio/wav",
            )
            uploaded_prompt = True
        except Exception as e:
            logging.warning(
                f"{source}: Failed to attach MOSS prompt audio '{prompt_audio_path}': {e}. "
                "Will use demo voice instead."
            )
    else:
        if prompt_audio_path:
            logging.warning(
                f"{source}: MOSS prompt audio not found at '{prompt_audio_path}'. "
                "Will use demo voice instead."
            )

    # The MOSS API requires either demo_id or prompt_audio.
    # If we didn't upload a prompt audio file, send demo_id as a fallback.
    if not uploaded_prompt:
        data.add_field("demo_id", "demo-1")
        logging.info(
            f"{source}: No prompt audio uploaded for MOSS voice '{voice}', "
            "using demo-1 built-in voice as fallback"
        )

    # Aggressive timeout — we don't want the DJ sitting in silence.
    # MOSS-TTS-Nano on CPU takes ~2-8 seconds for short DJ clips.
    # On GPU it's much faster. 30s total gives enough headroom.
    timeout = aiohttp.ClientTimeout(total=30, connect=5)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, data=data) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logging.error(
                        f"{source}: MOSS TTS returned status {resp.status}: "
                        f"{error_text[:300]}"
                    )
                    return None

                result_data = await resp.json(content_type=None)

    except aiohttp.ClientConnectorError as e:
        logging.error(
            f"{source}: Cannot connect to MOSS server at {MOSS_TTS_URL}. "
            f"Is the server running? Error: {e}"
        )
        return None
    except asyncio.TimeoutError:
        logging.error(
            f"{source}: MOSS TTS timed out (30s). "
            "The server may be overloaded or still warming up. Falling back."
        )
        return None
    except Exception as e:
        logging.error(f"{source}: MOSS TTS unexpected error: {e}")
        return None
    finally:
        # Close the prompt audio file handle if we opened it
        try:
            for part in data._parts:
                if hasattr(part, "source") and hasattr(part.source, "close"):
                    part.source.close()
        except Exception:
            pass

    # Decode the base64-encoded WAV audio from the response
    audio_b64 = result_data.get("audio_base64", "")
    if not audio_b64:
        logging.warning(f"{source}: MOSS TTS returned no audio_base64 in response")
        return None

    try:
        import base64

        audio_bytes = base64.b64decode(audio_b64)
    except Exception as e:
        logging.error(f"{source}: Failed to decode MOSS TTS audio base64: {e}")
        return None

    if len(audio_bytes) < 44:
        logging.warning(f"{source}: MOSS TTS returned empty or tiny audio data")
        return None

    # Save the WAV data to a temp file
    try:
        fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="dj_moss_")
        os.close(fd)

        with open(wav_path, "wb") as f:
            f.write(audio_bytes)

        # Calculate duration from the WAV header
        duration = 0.0
        try:
            import io as _io

            with wave.open(_io.BytesIO(audio_bytes), "rb") as wf:
                nframes = wf.getnframes()
                rate = wf.getframerate()
                duration = nframes / rate if rate > 0 else 0
        except Exception:
            pass

        logging.info(
            f"{source}: Generated TTS (moss) → {wav_path} "
            f"({len(text)} chars, voice={voice}, {duration:.1f}s)"
        )
        return wav_path
    except Exception as e:
        logging.error(f"{source}: Failed to write MOSS TTS WAV file: {e}")
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
