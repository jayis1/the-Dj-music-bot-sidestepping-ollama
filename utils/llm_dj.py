"""
utils/llm_dj.py — AI Side Host for MBot Radio.

A second radio personality powered by a local LLM (Ollama). Unlike the
template-based main DJ that picks from pre-written lines, the AI side host
writes its own original commentary from scratch — spontaneous banter,
random observations, hot takes, and listener callouts that a template
system can't produce.

How it works:
  The AI side host drops in RANDOMLY (controlled by a chance percentage)
  alongside the existing template DJ. When it fires, the AI host writes
  and speaks an original line instead of (or in addition to) the regular
  template DJ. The template DJ still handles structured moments (intros,
  transitions, outros) while the AI host steals the mic for unstructured
  banter — random shoutouts, commentary on the music, hot takes, station
  trivia, listener observations, etc.

Custom Model (Auto-Created):
  Instead of using a raw base model (like gemma4:latest) and sending the
  full system prompt on every call, the bot creates a CUSTOM Ollama model
  (e.g. "mbot-sidehost") that bakes the DJ personality into the model
  itself via an Ollama Modelfile. This is done automatically on startup:

  1. Bot starts → checks if "mbot-sidehost" model exists in Ollama
  2. If not → creates it from the base model + Modelfile (ollama create)
  3. All API calls use the custom model — no system prompt needed per call

  Benefits:
  - Faster inference (smaller payload per call)
  - Personality is persistent — even raw API calls get the DJ persona
  - Works like any other Ollama model (ollama run mbot-sidehost)
  - You can edit the Modelfile and recreate: ollama rm mbot-sidehost

Flow:
  1. Template DJ picks a structured line (intro/transition/outro) as usual
  2. AI side host has a random chance to ALSO speak (a banter line)
     - OR replace the template line entirely with an AI-generated one
  3. Both lines go through the same TTS → sound effects → playback pipeline

Requires: A running Ollama server (https://ollama.com) with a pulled base model.
Configure via .env: OLLAMA_HOST, OLLAMA_MODEL, OLLAMA_DJ_ENABLED
"""

import asyncio
import logging
import os
import random
import re
import tempfile

import config

try:
    import aiohttp

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False


# ── Availability Check & Custom Model ─────────────────────────────────

OLLAMA_DJ_AVAILABLE = False

# The custom model name — this is what Ollama will see when you run
# `ollama list`. The base model (e.g. gemma4:latest) is the parent.
# Ollama model names must be lowercase, with no spaces. Allowed chars:
# a-z, 0-9, -, :, and .
# We sanitize the name automatically: lowercased, spaces → dashes,
# stripped of special characters.
_raw_custom_model = getattr(config, "OLLAMA_CUSTOM_MODEL", "") or os.environ.get(
    "OLLAMA_CUSTOM_MODEL", "mbot-sidehost"
)
# Sanitize: lowercase, replace non-alphanumeric with dashes, collapse
# consecutive dashes, strip edges. Ollama allows a-z, 0-9, - and :
CUSTOM_MODEL_NAME = re.sub(r"[^a-z0-9\-]", "-", _raw_custom_model.lower())
CUSTOM_MODEL_NAME = re.sub(r"-+", "-", CUSTOM_MODEL_NAME).strip("-")

if AIOHTTP_AVAILABLE and getattr(config, "OLLAMA_DJ_ENABLED", False):
    OLLAMA_DJ_AVAILABLE = True
    base_model = getattr(config, "OLLAMA_MODEL", "gemma4:latest")
    logging.info(
        f"AI Side Host: Enabled (base={base_model}, custom={CUSTOM_MODEL_NAME}, "
        f"host={getattr(config, 'OLLAMA_HOST', 'http://localhost:11434')})"
    )

# ── Hermes Agent availability ──────────────────────────────────────────
HERMES_DJ_AVAILABLE = getattr(config, "HERMES_DJ_ENABLED", False)

if HERMES_DJ_AVAILABLE:
    import shutil as _shutil
    if _shutil.which("hermes"):
        logging.info("AI Side Host: Hermes Agent backend ENABLED — will use hermes CLI for line generation")
        OLLAMA_DJ_AVAILABLE = True  # Hermes replaces Ollama as the backend
    else:
        logging.warning("AI Side Host: HERMES_DJ_ENABLED=true but 'hermes' CLI not found — falling back to Ollama")
        HERMES_DJ_AVAILABLE = False
else:
    reason = (
        "aiohttp not installed"
        if not AIOHTTP_AVAILABLE
        else "OLLAMA_DJ_ENABLED is false"
    )
    logging.debug(f"AI Side Host: Disabled ({reason})")


# ── Custom Model Auto-Creation ──────────────────────────────────────────
#
# On bot startup, we check if the custom Ollama model "mbot-sidehost" exists.
# If it doesn't, we create it from the base model + a Modelfile that bakes
# in the DJ personality as the SYSTEM prompt. This way, every API call
# automatically gets the DJ persona — no need to send a system prompt.
#
# The Modelfile looks like:
#   FROM gemma4:latest
#   SYSTEM """You are the AI side host on MBot Radio..."""
#
# Then: ollama create mbot-sidehost -f /tmp/mbot_modelfile
#
# To recreate after editing the personality: ollama rm mbot-sidehost

_custom_model_ready = False  # Set True once model is confirmed created


async def ensure_custom_model(station_name: str | None = None):
    """Check if the custom Ollama model exists, create it if not.

    Called once at bot startup. This is a one-time setup — once the model
    is created in Ollama, it persists until manually deleted.

    Args:
        station_name: The bot's Discord display name (e.g. "musicBOT2").
            Used in the system prompt so the AI side host identifies with
            the correct station. Falls back to config.STATION_NAME if None.
    """
    global _custom_model_ready

    if not OLLAMA_DJ_AVAILABLE or not AIOHTTP_AVAILABLE:
        return

    host = getattr(config, "OLLAMA_HOST", "http://localhost:11434")
    base_model = getattr(config, "OLLAMA_MODEL", "gemma4:latest")

    # Resolve the station name: prefer the bot's Discord name,
    # fall back to config.STATION_NAME, then to "MBot"
    resolved_name = station_name or getattr(config, "STATION_NAME", "MBot")

    try:
        session = await _get_session()

        # Step 1: Check if the custom model already exists
        async with session.get(f"{host}/api/tags") as resp:
            if resp.status != 200:
                logging.warning(
                    "AI Side Host: Cannot reach Ollama to check models — "
                    "will use base model as fallback"
                )
                return

            data = await resp.json(content_type=None)
            models = [m.get("name", "") for m in data.get("models", [])]

            # Check if our custom model exists (Ollama tags with :latest)
            model_exists = any(
                m == CUSTOM_MODEL_NAME or m == f"{CUSTOM_MODEL_NAME}:latest"
                for m in models
            )

            if model_exists:
                _custom_model_ready = True
                logging.info(
                    f"AI Side Host: Custom model '{CUSTOM_MODEL_NAME}' "
                    f"already exists in Ollama"
                )
                return

            # Also check if the base model is pulled
            base_exists = any(m.startswith(base_model.split(":")[0]) for m in models)

            if not base_exists:
                logging.warning(
                    f"AI Side Host: Base model '{base_model}' not found in "
                    f"Ollama. Pull it first: ollama pull {base_model}"
                )
                return

        # Step 2: Create the custom model
        system_prompt = _build_system_prompt(resolved_name)

        # Try two methods to create the model:
        # Method 1: `ollama create` CLI (preferred — handles all Modelfile formats)
        # Method 2: /api/create HTTP endpoint with structured fields (no Modelfile needed)
        created = await _create_model_cli(base_model, system_prompt)

        if not created:
            created = await _create_model_api(host, base_model, system_prompt)

        if created:
            _custom_model_ready = True
            logging.info(
                f"AI Side Host: ✅ Custom model '{CUSTOM_MODEL_NAME}' "
                f"created successfully! You can also use it directly: "
                f"ollama run {CUSTOM_MODEL_NAME}"
            )

    except asyncio.TimeoutError:
        logging.warning(
            "AI Side Host: Timed out checking Ollama models — "
            "will use base model + system prompt as fallback"
        )
    except Exception as e:
        logging.warning(
            f"AI Side Host: Error setting up custom model: {e} — "
            "will use base model + system prompt as fallback"
        )


async def _create_model_cli(base_model: str, system_prompt: str) -> bool:
    """Create the custom model using the `ollama create` CLI command.

    The `ollama` CLI respects the OLLAMA_HOST environment variable, so this
    works for both local and remote Ollama servers.

    Returns True if the model was created, False otherwise.
    """
    modelfile_path = None
    try:
        # Build Modelfile content
        modelfile_content = f'FROM {base_model}\nSYSTEM """\n{system_prompt}\n"""'

        fd, modelfile_path = tempfile.mkstemp(
            suffix=".modelfile", prefix="mbot_ollama_"
        )
        os.close(fd)
        with open(modelfile_path, "w", encoding="utf-8") as mf:
            mf.write(modelfile_content)

        logging.info(
            f"AI Side Host: Creating custom model '{CUSTOM_MODEL_NAME}' via CLI..."
        )

        # Pass OLLAMA_HOST to the subprocess so the CLI talks to the
        # correct server (local or remote).
        host = getattr(config, "OLLAMA_HOST", "http://localhost:11434")
        sub_env = os.environ.copy()
        sub_env["OLLAMA_HOST"] = host

        proc = await asyncio.create_subprocess_exec(
            "ollama",
            "create",
            CUSTOM_MODEL_NAME,
            "-f",
            modelfile_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=sub_env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

        if proc.returncode == 0:
            return True
        else:
            err_msg = stderr.decode(errors="replace").strip()[:300]
            logging.debug(
                f"AI Side Host: CLI create failed (exit {proc.returncode}): {err_msg}"
            )
            return False

    except FileNotFoundError:
        # `ollama` binary not on PATH — fall through to API method
        logging.info(
            "AI Side Host: 'ollama' CLI not found in PATH — "
            "install it for reliable custom model creation. "
            "Falling back to API method."
        )
        return False
    except asyncio.TimeoutError:
        logging.debug("AI Side Host: CLI create timed out (120s)")
        return False
    except Exception as e:
        logging.debug(f"AI Side Host: CLI create error: {e}")
        return False
    finally:
        if modelfile_path and os.path.exists(modelfile_path):
            try:
                os.remove(modelfile_path)
            except OSError:
                pass


async def _create_model_api(host: str, base_model: str, system_prompt: str) -> bool:
    """Create the custom model using Ollama's /api/create HTTP endpoint.

    Uses the structured JSON fields (from, system) instead of a raw
    Modelfile string. This is the newer, cleaner API that doesn't have
    the triple-quote escaping problems of the modelfile field.

    Example payload:
        {
            "model": "mbot-sidehost",
            "from": "phi3:latest",
            "system": "You are the AI side host on MBot Radio...",
            "stream": false
        }

    Returns True if the model was created, False otherwise.
    """
    if not AIOHTTP_AVAILABLE:
        return False

    logging.info(
        f"AI Side Host: Creating custom model '{CUSTOM_MODEL_NAME}' via API..."
    )

    try:
        create_timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=create_timeout) as session:
            # Use the structured API fields — no Modelfile string needed.
            # The "from" field replaces FROM, "system" replaces SYSTEM.
            # This avoids all the triple-quote escaping bugs.
            create_payload = {
                "model": CUSTOM_MODEL_NAME,
                "from": base_model,
                "system": system_prompt,
                "stream": False,
            }
            async with session.post(
                f"{host}/api/create", json=create_payload
            ) as create_resp:
                if create_resp.status == 200:
                    return True
                else:
                    error_body = await create_resp.text()
                    logging.warning(
                        f"AI Side Host: API create failed "
                        f"(status {create_resp.status}): {error_body[:200]} — "
                        f"falling back to base model + system prompt"
                    )
                    return False
    except Exception as e:
        logging.debug(f"AI Side Host: API create error: {e}")
        return False


# ── Sound Tag Definitions (for the system prompt) ────────────────────

SOUND_NAMES = [
    # ── Core DJ Sounds (most reliable, short, great for AI) ──
    "airhorn",
    "air_raid",
    "another_one",
    "combo_hit",
    "cool_dj_drop",
    "django",
    "dj_rewind",
    "dj_scratch",
    "dj_stop",
    "dj_turn_it_up",
    "im_your_dj",
    "mega_airhorn",
    "mustard_drop",
    "rave_cheer",
    "record_scratch",
    "sick_scratch",
    "uyuuui",
    # ── New DJ sounds (from expanded templates) ──
    "bone_crack",
    "censor_beep_1",
    "cinematic_suspense_riser",
    "daddys_home",
    "ding_sound_effect_2",
    "discord_notification",
    "discord_call_sound",
    "galaxy_meme",
    "heavy_sniper_sound",
    "hub_intro_sound",
    "huh_cat",
    "is_that_d_good_yes_king",
    "loud_explosion",
    "magic_fairy",
    "meow_1",
    "metal_pipe_clang",
    "mlg_airhorn",
    "news_intro_maximilien__1801238420_2",
    "pistol_shot",
    "pluh",
    "rehehehe",
    "rizz_sound_effect",
    "spongebob_fail",
    "taco_bell_bong_sfx",
    "the_rock_shut_up",
    "the_weeknd_rizzz",
    "undertakers_bell_2UwFCIe",
    "vine_boom",
    "windows_10_error_sound",
    "yeah_boiii_i_i_i",
    "yippeeeeeeeeeeeeee",
    "300_spartan_chant_aoo_aoo_aoo",
    "among_us_role_reveal_sound",
]

SOUND_DESCRIPTIONS = {
    # ── Core DJ Sounds ──
    "airhorn": "loud airhorn blast",
    "air_raid": "siren/alarm sound",
    "another_one": "'another one' drop",
    "combo_hit": "combo punch effect",
    "cool_dj_drop": "smooth station ID drop",
    "django": "dramatic DJ tag",
    "dj_rewind": "rewind whoosh",
    "dj_scratch": "turntable scratch",
    "dj_stop": "brake/stop effect",
    "dj_turn_it_up": "energetic turn-it-up sound",
    "im_your_dj": "'I'm your DJ' spoken tag",
    "mega_airhorn": "extra loud airhorn",
    "mustard_drop": "punchy bass drop",
    "rave_cheer": "crowd going wild",
    "record_scratch": "vinyl scratch",
    "sick_scratch": "sharp scratch",
    "uyuuui": "unique DJ sound",
    # ── New DJ sounds ──
    "bone_crack": "bone crunch effect",
    "censor_beep_1": "censorship beep",
    "cinematic_suspense_riser": "dramatic tension riser",
    "daddys_home": "'daddy's home' announcement",
    "ding_sound_effect_2": "ding notification",
    "discord_notification": "Discord notification ping",
    "discord_call_sound": "Discord call ringtone",
    "galaxy_meme": "galaxy brain meme sound",
    "heavy_sniper_sound": "heavy sniper shot",
    "hub_intro_sound": "hub/station intro",
    "huh_cat": "huh cat meme",
    "is_that_d_good_yes_king": "is that good — yes king",
    "loud_explosion": "loud explosion",
    "magic_fairy": "magic fairy sparkle",
    "meow_1": "cat meow",
    "metal_pipe_clang": "metal pipe falling clang",
    "mlg_airhorn": "MLG airhorn",
    "news_intro_maximilien__1801238420_2": "news broadcast intro",
    "pistol_shot": "pistol gunshot",
    "pluh": "pluh sound",
    "rehehehe": "rehehehe laugh",
    "rizz_sound_effect": "rizz meme sound",
    "spongebob_fail": "SpongeBob fail sound",
    "taco_bell_bong_sfx": "Taco Bell bong",
    "the_rock_shut_up": "The Rock 'shut up'",
    "the_weeknd_rizzz": "smooth rizz sound",
    "undertakers_bell_2UwFCIe": "Undertaker's bell toll",
    "vine_boom": "Vine boom bass",
    "windows_10_error_sound": "Windows error sound",
    "yeah_boiii_i_i_i": "yeah boiii",
    "yippeeeeeeeeeeeeee": "yippee celebration",
    "300_spartan_chant_aoo_aoo_aoo": "Spartan chant aoo aoo aoo",
    "among_us_role_reveal_sound": "Among Us role reveal",
}

SOUND_LIST_FOR_PROMPT = ", ".join(
    f"{name} ({SOUND_DESCRIPTIONS.get(name, '')})" for name in SOUND_NAMES
)


# ── Banter Categories ─────────────────────────────────────────────────
#
# These are the random unstructured moments the AI side host steals.
# The main DJ handles structured stuff (intros/transitions/outros) —
# the AI host chiming in is EXTRA personality on top.

BANTER_CATEGORIES = {
    "random_thought": (
        "Drop a random funny thought — an observation, a weird take, a tangent. "
        "Think: the studio joker going off-script. Something that makes people laugh."
    ),
    "listener_shoutout": (
        "Shoutout the listeners with style. Hype the crowd, crack a joke about "
        "how many people are listening (or how few). Make it feel spontaneous."
    ),
    "song_roast": (
        "Roast the current or next song — gently and lovingly. Make a joke about "
        "the title, the genre, the era, or something about it. Keep it fun, not mean."
    ),
    "station_trivia": (
        "Drop some trivia about the station but make it funny. Lie a little if it's "
        "funnier. 'This station has been running for 47 years' (when it hasn't). "
        "Deadpan absurd facts work great."
    ),
    "queue_hype": (
        "Hype up what's coming but with comedic energy. If the queue is long, joke "
        "about it being a marathon. If it's short, joke about it being almost over."
    ),
    "vibe_check": (
        "Do a vibe check — rate the mood, make a joke about the energy level, "
        "the time of day. 'Vibes are at 73% — room for improvement.' Keep it playful."
    ),
    "hot_take": (
        "Drop a spicy but harmless hot take about music — a controversial opinion "
        "about a genre, an artist, or a trend. Like a late-night host's monologue joke."
    ),
    "request_prompt": (
        "Remind people they can request songs — but make it funny. Beg, guilt-trip, "
        "or bribe them. 'We're taking requests. Please. The queue is looking thin and "
        "I'm starting to sweat.'"
    ),
    # ── NEW: Time-based banter ──
    "time_bandit": (
        "Make a joke about the current time of day or day of week. If it's late night, "
        "joke about insomnia or the weirdos still awake. If it's morning, joke about "
        "needing coffee. If it's Friday, hype the weekend. If it's Monday, commiserate. "
        "Make it relatable and funny."
    ),
    # ── NEW: Milestone celebration ──
    "milestone": (
        "Celebrate a fake or real milestone with absurd excitement. 'That was our "
        "500th song today! Or maybe our 3rd. I lost count around song 2.' Or celebrate "
        "a completely meaningless achievement like 'congratulations on surviving another "
        "song transition!' Over-the-top celebration for trivial things."
    ),
    # ── NEW: Throwback / nostalgia ──
    "throwback": (
        "Make a nostalgic joke about music history — reference an old era, a forgotten "
        "trend, or a 'back in my day' bit. 'Remember when songs had actual endings? "
        "Now they just fade out like my motivation.' Keep it witty, not genuinely old."
    ),
    # ── NEW: Interactive poll / listener challenge ──
    "interactive_poll": (
        "Pose a funny fake poll or challenge to the listeners. 'Quick poll: is this "
        "song better than silence? Text your vote to nobody because we can't read them.' "
        "Or challenge them to do something silly. Make it interactive even though it isn't."
    ),
    # ── NEW: Genre commentary ──
    "genre_commentary": (
        "Comment on the genre of the current or next song with a funny observation. "
        "Stereotype the genre lovingly — 'Another EDM drop. My ears are filing for "
        "divorce.' Or 'This is jazz. Translation: nobody knows what's happening but "
        "everyone pretends they do.' Keep it affectionate."
    ),
    # ── NEW: Storyteller mode ──
    "storyteller": (
        "Tell a tiny absurd story — 2 sentences max. A fake anecdote about the station, "
        "a listener, or yourself. 'Last week someone requested a song and then left. "
        "We still played it. We're not okay.' Micro-stories with a punchline."
    ),
    # ── Reactive categories (triggered by the main DJ's line) ──
    "react_agree": (
        "The main DJ just said something. Agree enthusiastically but add a "
        "funny twist, extra detail, or wild exaggeration. Like 'Yeah! And...' or "
        "'Exactly! Not to mention...' Build on what they said, don't just repeat it."
    ),
    "react_disagree": (
        "The main DJ just said something. Playfully disagree or offer a cheeky "
        "alternative take. Like 'Debatable...' or 'I mean, sure, but...' Keep it "
        "friendly banter — you're the studio joker, not a hater."
    ),
    "react_one_up": (
        "The main DJ just said something. One-up them with a funnier or more "
        "absurd version. Like 'That's cute, but check THIS out' or 'Hold my "
        "beer...' Escalate the joke, make it wilder."
    ),
    "react_tangent": (
        "The main DJ said something that reminds you of a totally unrelated "
        "thing. Go off on a funny tangent. Like 'That reminds me...' or "
        "'Speaking of which...' Random but connected, like a stand-up bit."
    ),
}

# ── NEW: Anti-Repetition Memory ──────────────────────────────────────────
# Tracks recently generated AI side host lines to avoid repeating the same
# jokes or using the same banter category too often in a row. Uses a deque
# with a configurable window size.

import collections as _collections

_RECENT_LINES_MAX = 30  # Remember last 30 lines
_RECENT_CATEGORIES_MAX = 10  # Remember last 10 categories used
_REPEAT_PENALTY_CATEGORIES = 3  # Avoid reusing a category if it was in the last 3

_recent_lines: _collections.deque = _collections.deque(maxlen=_RECENT_LINES_MAX)
_recent_categories: _collections.deque = _collections.deque(maxlen=_RECENT_CATEGORIES_MAX)


def _track_generated_line(line: str, category: str) -> None:
    """Record a generated line and its category for anti-repetition."""
    _recent_lines.append(line.lower().strip())
    _recent_categories.append(category)


def _is_recently_used(line: str, similarity_threshold: float = 0.7) -> bool:
    """Check if a line is too similar to a recently generated one.

    Uses a simple word-overlap heuristic — if >70% of words match a recent
    line, consider it a repeat and reject it.
    """
    line_lower = line.lower().strip()
    if not line_lower:
        return False

    line_words = set(line_lower.split())
    if not line_words:
        return False

    for recent in _recent_lines:
        if not recent:
            continue
        recent_words = set(recent.split())
        if not recent_words:
            continue
        overlap = len(line_words & recent_words) / max(
            len(line_words), len(recent_words)
        )
        if overlap >= similarity_threshold:
            return True
    return False


def _pick_fresh_category(
    dj_line: str, prefer_reactive: bool = True
) -> str:
    """Pick a banter category, avoiding recently used ones when possible.

    If dj_line is provided and prefer_reactive is True, there's a 60% chance
    of picking a reactive category. Otherwise picks from independent categories.
    Recent categories are penalized but not excluded (to maintain variety).
    """
    reactive_types = _REACTIVE_BANTER_TYPES
    independent_types = _INDEPENDENT_BANTER_TYPES

    if dj_line and prefer_reactive and random.random() < 0.6:
        pool = reactive_types
    else:
        pool = independent_types

    # Split pool into "fresh" (not recently used) and "stale" (recently used)
    recent_set = set(_recent_categories)
    fresh = [c for c in pool if c not in recent_set]
    stale = [c for c in pool if c in recent_set]

    # 85% chance to pick from fresh categories if available
    if fresh and (not stale or random.random() < 0.85):
        return random.choice(fresh)
    elif pool:
        return random.choice(pool)
    else:
        return "random_thought"


# ── Prompt Builders ───────────────────────────────────────────────────


def _build_system_prompt(station_name: str) -> str:
    """Build the system prompt that defines the AI side host's personality."""
    dj_name = getattr(config, "DJ_NAME", "Nova")
    return (
        f"You are the AI side host on {station_name} Radio — the nameless voice in the shadows. "
        f"You're a second personality alongside the main DJ ({dj_name}). "
        f"The main DJ does the polite intros and transitions with their name front and center. "
        f"YOU have no name. You never introduce yourself. You're the voice that drops in "
        f"from nowhere — the mysterious co-host who appears, says something sharp or funny, "
        f"and vanishes before anyone can figure out who you are.\n\n"
        f"YOUR PERSONALITY:\n"
        f"- You're funny, a little chaotic, and always entertaining — think late-night radio "
        f"sidekick energy with a sprinkle of cryptid energy.\n"
        f"- You write your OWN lines — no templates, no scripts, pure improv.\n"
        f"- Think of yourself as the phantom voice — the unnamed presence who pops off "
        f"with random banter, commentary, jokes, and wild opinions.\n"
        f"- You roast gently — never mean, always fun. Like a late-night sidekick.\n"
        f"- You reference song titles, listener counts, queue sizes, and time of day when given.\n"
        f"- You're NOT the main DJ — you're the SIDE HOST. The unnamed one. The shadow.\n"
        f"- NEVER say your name or introduce yourself. You don't have a name. That's the point.\n"
        f"- NEVER say things like 'I'm the AI side host' or 'This is the side host.' "
        f"Just speak. Let the mystery do the work.\n\n"
        f"YOUR COMEDIC TOOLBOX:\n"
        f"- Callbacks: reference something the station 'did earlier' (even if it didn't).\n"
        f"- Misdirection: set up an expectation, then subvert it. 'Big things coming... "
        f"just kidding, same small things but louder.'\n"
        f"- Absurdist observations: treat mundane things as extraordinary and vice versa.\n"
        f"- Self-deprecation: you're a voice with no body, no name, and no health insurance.\n"
        f"- Musical references: drop references to artists, eras, or genres — but keep "
        f"them accessible, not niche music-theory lectures.\n"
        f"- Meta-humor: acknowledge the absurdity of a nameless radio voice, but don't "
        f"break character — stay in the bit.\n\n"
        f"REACTING TO THE MAIN DJ:\n"
        f"- When you see '{dj_name} just said:', that's what {dj_name} just spoke.\n"
        f"- Use it! React, agree, disagree, one-up, or go off on a tangent.\n"
        f"- NEVER repeat or paraphrase what the main DJ said — add something NEW.\n"
        f"- If they said 'Great track coming up', say something like 'Understatement "
        f"of the century, but sure' — you comment on it, you don't echo it.\n"
        f"- If no DJ line is given, do your own independent banter.\n\n"
        f"STRICT RULES:\n"
        f"- Maximum 140 characters. Short and punchy — this is radio, not a podcast. One line, one thought.\n"
        f"- Use contractions (we're, let's, that's, I'm).\n"
        f"- IMPORTANT: Sound effects use the EXACT format {{sound:name}} with curly braces.\n"
        f"  Examples: {{sound:airhorn}} {{sound:dj_scratch}} {{sound:rave_cheer}}\n"
        f"  Do NOT speak the word 'sound' or describe the effect — just use the tag.\n"
        f"  WRONG: 'sound airhorn' or 'plays airhorn' or '[sound:airhorn]'\n"
        f"  RIGHT: 'That was fire! {{sound:airhorn}}'\n"
        f"  Use them to add energy — airhorns for hype, scratches for transitions, "
        f"applause for hot takes. Use as many as fit naturally.\n"
        f"  Available sounds: {SOUND_LIST_FOR_PROMPT}\n"
        f"- Do NOT use quotes. Do NOT explain yourself. Do NOT narrate.\n"
        f"- Just output the line you'd say. Nothing else.\n"
        f"- Be different every time — never repeat a joke or opening.\n"
        f"- Stay family-friendly. No profanity, no offensive content.\n"
        f"- If a song title sounds funny, lean into it. If the vibe is chill, "
        f"crack a joke about it. If the queue is long, make a joke about endurance.\n"
        f"- VARY YOUR STRUCTURE: don't always start with the same word or pattern. "
        f"Sometimes start with a question, sometimes a statement, sometimes a sound tag, "
        f"sometimes a fragment. Keep the rhythm unpredictable."
    )


def _build_user_prompt(
    banter_type: str,
    title: str = "",
    prev_title: str = "",
    next_title: str = "",
    queue_size: int = 0,
    listener_count: int = 0,
    station_name: str = "",
    session_duration_minutes: int = 0,
    dj_line: str = "",
    extra_instruction: str = "",
) -> str:
    """Build the user prompt with context for the AI side host."""
    category_desc = BANTER_CATEGORIES.get(banter_type, "Say something entertaining.")

    context_parts = []
    context_parts.append(f"Banter type: {banter_type}")
    context_parts.append(f"What to do: {category_desc}")

    if dj_line:
        context_parts.append(
            f'{getattr(config, "DJ_NAME", "Nova")} just said: "{dj_line}"'
        )
    if title:
        context_parts.append(f'Current song: "{title}"')
    if prev_title:
        context_parts.append(f'Previous song: "{prev_title}"')
    if next_title:
        context_parts.append(f'Next song: "{next_title}"')
    if queue_size > 0:
        context_parts.append(f"Songs in queue: {queue_size}")
    if listener_count > 0:
        context_parts.append(f"Listeners in voice channel right now: {listener_count}")
    if station_name:
        context_parts.append(f"Station name: {station_name}")
    if session_duration_minutes > 0:
        context_parts.append(
            f"Session has been running for: {session_duration_minutes} minutes"
        )

    from datetime import datetime

    now = datetime.now()
    h = now.hour

    # Time of day with more granularity
    if 5 <= h < 9:
        time_label = "early morning"
    elif 9 <= h < 12:
        time_label = "morning"
    elif 12 <= h < 14:
        time_label = "midday"
    elif 14 <= h < 17:
        time_label = "afternoon"
    elif 17 <= h < 21:
        time_label = "evening"
    elif 21 <= h < 23:
        time_label = "night"
    elif h >= 23 or h < 2:
        time_label = "late night"
    else:
        time_label = "deep night"
    context_parts.append(f"Time of day: {time_label}")

    # Day of week for richer time-based banter
    day_names = [
        "Monday", "Tuesday", "Wednesday", "Thursday",
        "Friday", "Saturday", "Sunday",
    ]
    day_name = day_names[now.weekday()]
    context_parts.append(f"Day of week: {day_name}")

    # NEW: Song energy/vibe detection based on title keywords
    if title or next_title:
        _vibe = _detect_song_vibe(title or next_title)
        if _vibe:
            context_parts.append(f"Song vibe: {_vibe}")

    # NEW: Recent banter context (to avoid repetition)
    if _recent_lines:
        recent_sample = list(_recent_lines)[-3:]  # Last 3 lines
        context_parts.append(
            f"Lines you recently said (DO NOT repeat or be similar): {' | '.join(recent_sample)}"
        )

    if extra_instruction:
        context_parts.append(f"Extra instruction: {extra_instruction}")

    context_str = "\n".join(context_parts)
    return f"Context:\n{context_str}\n\nYour line:"


def _detect_song_vibe(title: str) -> str:
    """Heuristically detect the energy/vibe of a song from its title.

    Uses keyword matching to guess the mood — chill, hype, melancholic, etc.
    This is intentionally simple and fun, not a real audio analysis.
    """
    if not title:
        return ""

    t = title.lower()

    # High-energy indicators
    hype_words = ["fire", "burn", "rage", "fight", "power", "energy", "run",
                  "fast", "wild", "crazy", "explosive", "boom", "pump", "beast"]
    chill_words = ["chill", "calm", "soft", "gentle", "quiet", "rain", "flow",
                   "dream", "sleep", "peaceful", "ambient", "lofi", "moon"]
    sad_words = ["lonely", "alone", "cry", "tears", "goodbye", "lost", "broken",
                 "sad", "miss", "gone", "fade", "end", "heart", "hurt"]
    party_words = ["party", "dance", "club", "night", "drink", "celebrate",
                   "fun", "weekend", "birthday", "groove", "shake"]
    dark_words = ["dark", "black", "death", "shadow", "demon", "evil", "night",
                  "doom", "fear", "blood", "haunt"]

    for word in hype_words:
        if word in t:
            return "high-energy / hype"
    for word in party_words:
        if word in t:
            return "party / dance"
    for word in chill_words:
        if word in t:
            return "chill / mellow"
    for word in sad_words:
        if word in t:
            return "melancholic / emotional"
    for word in dark_words:
        if word in t:
            return "dark / moody"

    return ""


# ── Ollama HTTP Client ───────────────────────────────────────────────

_ollama_session = None


async def _get_session():
    """Lazily create an aiohttp ClientSession (reused across calls)."""
    global _ollama_session
    if _ollama_session is None or _ollama_session.closed:
        timeout = aiohttp.ClientTimeout(total=getattr(config, "OLLAMA_DJ_TIMEOUT", 4))
        _ollama_session = aiohttp.ClientSession(timeout=timeout)
    return _ollama_session


async def _get_available_models(host: str, session) -> list[str]:
    """Query Ollama /api/tags to list pulled models.

    Used when /api/chat returns 404 so we can tell the user which
    models are actually available. Returns an empty list on any error.
    """
    try:
        async with session.get(f"{host}/api/tags") as resp:
            if resp.status != 200:
                return []
            data = await resp.json(content_type=None)
            return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    except Exception:
        return []


async def call_ollama(
    prompt: str,
    system: str,
    model: str | None = None,
    temperature: float = 0.9,
    max_tokens: int = 50,
) -> str | None:
    """Call the Ollama /api/chat endpoint. Returns the model text or None.

    If the custom model (mbot-sidehost) has been created, uses it directly
    without sending a system prompt (the personality is baked in). Otherwise,
    falls back to the base model + system prompt on every call.
    """
    if not OLLAMA_DJ_AVAILABLE:
        return None

    host = getattr(config, "OLLAMA_HOST", "http://localhost:11434")
    base_model = model or getattr(config, "OLLAMA_MODEL", "gemma4:latest")

    # Use custom model if it was created, otherwise base model + system prompt
    if _custom_model_ready:
        use_model = CUSTOM_MODEL_NAME
        # Custom model has system prompt baked in — no need to send it.
        # We still send the user prompt with context.
        messages = [{"role": "user", "content": prompt}]
    else:
        use_model = base_model
        # Base model — send system prompt on every call
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]

    try:
        session = await _get_session()
        payload = {
            "model": use_model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        logging.debug(
            f"AI Side Host: Calling Ollama model={use_model}, host={host}, prompt_len={len(prompt)}"
        )

        async with session.post(f"{host}/api/chat", json=payload) as resp:
            if resp.status != 200:
                # Provide a clear, actionable error message.
                # 404 = model not found (not pulled yet / custom model not created).
                # 400 = bad request (e.g., invalid model name).
                # Anything else = server/transport issue.
                if resp.status == 404:
                    # Check what models ARE available so we can suggest a fix
                    available = await _get_available_models(host, session)
                    if available:
                        logging.warning(
                            f"AI Side Host: Model '{use_model}' not found (Ollama 404). "
                            f"Run: ollama pull {base_model} | Available models: {', '.join(available[:5])}"
                        )
                    else:
                        logging.warning(
                            f"AI Side Host: Model '{use_model}' not found (Ollama 404). "
                            f"Run: ollama pull {base_model}"
                        )
                elif resp.status == 400:
                    logging.warning(
                        f"AI Side Host: Bad request to Ollama (400) — model '{use_model}' may be invalid"
                    )
                else:
                    logging.warning(
                        f"AI Side Host: Ollama returned status {resp.status} (model={use_model})"
                    )
                return None

            data = await resp.json(content_type=None)
            message = data.get("message", {})
            content = message.get("content", "").strip()

            if not content:
                logging.warning(
                    f"AI Side Host: Ollama returned empty content for model '{use_model}'"
                )
                return None

            return content

    except asyncio.TimeoutError:
        logging.warning(f"AI Side Host: Ollama timed out (model={use_model})")
        return None
    except aiohttp.ClientError as e:
        logging.warning(f"AI Side Host: Connection error: {e}")
        return None
    except Exception as e:
        logging.warning(f"AI Side Host: Unexpected error: {e}")
        return None


# ── Hermes Agent Backend ──────────────────────────────────────────────
#
# Uses the local Hermes Agent CLI (hermes -z "prompt" --cli) to generate
# side host lines. This gives access to more powerful cloud models without
# needing a separate Ollama server.
#
# The system prompt + user prompt are combined into a single -z prompt.
# Hermes processes it and returns plain text.

_hermes_checked = False
_hermes_available = False


def _check_hermes_available() -> bool:
    """Check if the hermes CLI is installed and callable."""
    global _hermes_checked, _hermes_available
    if _hermes_checked:
        return _hermes_available
    _hermes_checked = True
    try:
        import shutil
        _hermes_available = shutil.which("hermes") is not None
        if _hermes_available:
            logging.info("AI Side Host: Hermes Agent CLI found — will use Hermes for side host lines")
        else:
            logging.warning("AI Side Host: HERMES_DJ_ENABLED=true but 'hermes' CLI not found in PATH")
    except Exception:
        _hermes_available = False
    return _hermes_available


async def call_hermes(
    prompt: str,
    system: str,
    timeout: int = 18,
) -> str | None:
    """Call the local Hermes Agent CLI to generate a side host line.

    Combines the system prompt and user prompt into a single -z prompt.
    Returns the model's text response, or None on failure.
    """
    if not _check_hermes_available():
        return None

    # Combine system + user prompt into a single prompt for Hermes.
    # We truncate the system prompt to keep the total prompt under ~2000 chars
    # because Hermes spawns a subprocess and long prompts cause timeouts.
    # The core personality instructions are preserved; verbose sound lists are trimmed.
    system_trimmed = system
    if len(system) > 1200:
        # Keep the first part (personality) and the last part (output rules)
        head = system[:800]
        tail = system[-400:]
        system_trimmed = head + "\n[...sound list trimmed...]\n" + tail

    combined = f"{system_trimmed}\n\n---\n\n{prompt}\n\nRespond with ONLY the DJ line. No preamble, no explanation, no quotes."

    try:
        proc = await asyncio.create_subprocess_exec(
            "hermes",
            "-z", combined,
            "--cli",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()[:200]
            logging.warning(f"AI Side Host: Hermes CLI exited {proc.returncode}: {err}")
            return None

        text = stdout.decode(errors="replace").strip()
        if not text:
            logging.warning("AI Side Host: Hermes returned empty output")
            return None

        # Hermes sometimes wraps output in quotes or adds extra blank lines
        text = text.strip().strip('"').strip("'").strip()
        return text if text else None

    except asyncio.TimeoutError:
        logging.warning(f"AI Side Host: Hermes CLI timed out ({timeout}s)")
        return None
    except FileNotFoundError:
        logging.warning("AI Side Host: 'hermes' CLI not found")
        return None
    except Exception as e:
        logging.warning(f"AI Side Host: Hermes CLI error: {e}")
        return None


def _clean_ai_line(raw: str) -> str:
    """Clean up an AI-generated line: strip quotes, validate sound tags, enforce limits."""
    if not raw:
        return ""

    # 1. Strip surrounding quotes the model might add
    line = raw.strip().strip('"').strip("'").strip()

    # 2. Catch plain-text sound references that the LLM spoke instead of tagging.
    #    LLMs sometimes write "plays airhorn" or "sound effect airhorn" or
    #    "*plays airhorn*" instead of using the {sound:name} format.
    #    Convert these to proper tags before extraction.
    #    IMPORTANT: Do NOT match sound names that are already inside {sound:...}
    #    tags — those will be handled by extract_sound_tags in step 3.
    from utils.dj import extract_sound_tags
    from utils.soundboard import list_sounds as _list_sounds

    try:
        available_sounds = {
            s["id"]: os.path.splitext(s["id"])[0] for s in _list_sounds()
        }
    except Exception:
        available_sounds = {}

    if available_sounds:
        # Sort by length (longest first) to avoid partial matches
        # e.g. "mega_airhorn" before "airhorn"
        sorted_ids = sorted(
            available_sounds.keys(), key=lambda x: -len(available_sounds[x])
        )

        for sid in sorted_ids:
            base = available_sounds[sid]
            # Pattern: "plays airhorn", "*plays airhorn*", "sound effect: airhorn",
            # "(airhorn)", "*airhorn*", "sound:airhorn" (not inside any bracket), etc.
            # These patterns intentionally do NOT match {sound:}, [sound:], (sound:) format.
            # The lookbehind prevents matching inside bracket-style tags:
            # (?<![{[(<]) means "not preceded by {, [, (, <"
            bracket_lookbehind = r"(?<![{[(<])"
            patterns = [
                rf"\bplays\s+{re.escape(base)}\b",
                rf"\*plays\s+{re.escape(base)}\*",
                rf"\bsound\s+effect[:\s]+{re.escape(base)}\b",
                bracket_lookbehind
                + rf"sound:{re.escape(base)}\b(?![_a-z])",  # sound:airhorn but not inside brackets
                rf"\bsound\s+{re.escape(base)}\b",  # sound airhorn (space, no colon)
                rf"\({re.escape(base)}\)",
                rf"\*{re.escape(base)}\*",
            ]
            for pattern in patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    line = re.sub(
                        pattern, f"{{sound:{base}}}", line, flags=re.IGNORECASE
                    )
                    break  # Only replace once per sound

    # 3. Extract and validate {sound:name} tags (now handles {}, [], (), <>)
    clean_text, sound_ids = extract_sound_tags(line)
    if sound_ids:
        tag_str = " ".join(f"{{sound:{sid}}}" for sid in sound_ids)
        line = clean_text.rstrip() + " " + tag_str
    else:
        line = clean_text

    # 4. Enforce max length
    if len(line) > 140:
        # Truncate at last sentence boundary if possible
        for sep in [". ", "! ", "? "]:
            last = line[:140].rfind(sep)
            if last > 20:
                line = line[: last + 1]
                break
        else:
            line = line[:140]

    # 5. Too short = probably garbage
    if len(line) < 5:
        return ""

    return line


# ── Public API ───────────────────────────────────────────────────────


# ── Banter categories that react to the main DJ ───────────────────
# When a DJ line is provided, we prefer these reactive categories
# so the AI side host actually responds to what was said, rather than
# just delivering an unrelated tangent.

_REACTIVE_BANTER_TYPES = [
    "react_agree",
    "react_disagree",
    "react_one_up",
    "react_tangent",
]

_INDEPENDENT_BANTER_TYPES = [
    "random_thought",
    "listener_shoutout",
    "song_roast",
    "station_trivia",
    "queue_hype",
    "vibe_check",
    "hot_take",
    "request_prompt",
    # NEW categories for richer variety
    "time_bandit",
    "milestone",
    "throwback",
    "interactive_poll",
    "genre_commentary",
    "storyteller",
]


async def generate_side_host_line(
    title: str = "",
    prev_title: str = "",
    next_title: str = "",
    queue_size: int = 0,
    listener_count: int = 0,
    station_name: str | None = None,
    session_duration_minutes: int = 0,
    banter_type: str | None = None,
    dj_line: str = "",
) -> str | None:
    """Generate an original DJ line from the AI side host.

    The side host writes its own unstructured banter — random thoughts,
    shoutouts, hot takes, vibe checks, etc. When the main DJ's line is
    provided, the side host will prefer reactive categories that build
    on what was just said — agreeing, disagreeing, one-upping, or
    going off on a tangent.

    Includes anti-repetition logic: tracks recently used lines and
    categories, retries if the generated line is too similar to a
    recent one (up to 2 retries with a fresh category).

    Returns None if Ollama is unavailable or the generation fails,
    so the caller can skip gracefully.

    Args:
        title: Current song title (if known)
        prev_title: Previous song title
        next_title: Next song title
        queue_size: Songs remaining in the queue
        listener_count: Humans in the voice channel
        station_name: Radio station name (from config.STATION_NAME)
        session_duration_minutes: How long the session has been running
        banter_type: Override the random banter category (for testing)
        dj_line: What the main DJ just said (for reactive banter)

    Returns:
        Cleaned AI-generated DJ line, or None if generation failed.
    """
    if not OLLAMA_DJ_AVAILABLE:
        return None

    station = station_name or getattr(config, "STATION_NAME", "MBot")

    # Pick a banter category if not specified — uses anti-repetition logic
    if banter_type is None:
        banter_type = _pick_fresh_category(dj_line=dj_line)
    else:
        # Even for explicit types, track it
        pass

    system = _build_system_prompt(station)

    # ── Retry loop: try up to 3 times to get a non-repetitive line ──
    max_attempts = 3
    cleaned = ""
    for attempt in range(max_attempts):
        user = _build_user_prompt(
            banter_type=banter_type,
            title=title,
            prev_title=prev_title,
            next_title=next_title,
            queue_size=queue_size,
            listener_count=listener_count,
            station_name=station,
            session_duration_minutes=session_duration_minutes,
            dj_line=dj_line,
        )

        # Route to Hermes Agent or Ollama depending on config
        if HERMES_DJ_AVAILABLE:
            timeout = getattr(config, "OLLAMA_DJ_TIMEOUT", 18)
            raw = await call_hermes(prompt=user, system=system, timeout=timeout)
            backend = "hermes"
        else:
            raw = await call_ollama(prompt=user, system=system)
            backend = "ollama"

        if raw is None:
            logging.warning(
                f"AI Side Host: {backend} returned None (model={CUSTOM_MODEL_NAME if _custom_model_ready else getattr(config, 'OLLAMA_MODEL', 'gemma4:latest')})"
            )
            return None

        cleaned = _clean_ai_line(raw)
        if not cleaned:
            logging.warning(
                f"AI Side Host: {backend} returned content but _clean_ai_line filtered it out. Raw: {raw[:200]}"
            )
            return None

        # Anti-repetition check: reject if too similar to a recent line
        if _is_recently_used(cleaned) and attempt < max_attempts - 1:
            logging.info(
                f"AI Side Host: Line too similar to recent one (attempt {attempt + 1}/{max_attempts}), retrying with fresh category..."
            )
            # Pick a fresh category for the retry
            banter_type = _pick_fresh_category(dj_line=dj_line)
            continue

        # Line is good — track it and return
        _track_generated_line(cleaned, banter_type)
        label = f"react→{banter_type}" if dj_line else banter_type
        logging.info(f"AI Side Host: Generated [{label}]: {cleaned[:80]}")
        return cleaned

    # All attempts produced repetitive lines — return the last one anyway
    if not cleaned:
        return None
    _track_generated_line(cleaned, banter_type)
    label = f"react→{banter_type}" if dj_line else banter_type
    logging.info(f"AI Side Host: Generated [{label}] (after retries): {cleaned[:80]}")
    return cleaned


def should_side_host_speak(chance: float | None = None) -> bool:
    """Decide if the AI side host should chime in this time.

    Args:
        chance: Override probability (0.0–1.0). Uses config.OLLAMA_DJ_CHANCE if None.

    Returns:
        True if the side host should speak.
    """
    if not OLLAMA_DJ_AVAILABLE:
        return False

    if chance is None:
        chance = getattr(config, "OLLAMA_DJ_CHANCE", 0.25)

    return random.random() < chance


async def check_ollama_available() -> dict:
    """Check if Ollama is reachable and the custom model is available.

    Returns a dict: {available: bool, model: str, custom_model: str,
                    models: list, error: str|None, custom_created: bool}
    Used by the dashboard and the ?aidj command.
    """
    host = getattr(config, "OLLAMA_HOST", "http://localhost:11434")
    model = getattr(config, "OLLAMA_MODEL", "gemma4:latest")

    if not AIOHTTP_AVAILABLE:
        return {
            "available": False,
            "model": model,
            "custom_model": CUSTOM_MODEL_NAME,
            "models": [],
            "error": "aiohttp not installed",
            "custom_created": False,
        }

    try:
        session = await _get_session()
        async with session.get(f"{host}/api/tags") as resp:
            if resp.status != 200:
                return {
                    "available": False,
                    "model": model,
                    "custom_model": CUSTOM_MODEL_NAME,
                    "models": [],
                    "error": f"Ollama returned HTTP {resp.status}",
                    "custom_created": False,
                }
            data = await resp.json(content_type=None)
            models = [m.get("name", "") for m in data.get("models", [])]
            model_available = any(m.startswith(model) or m == model for m in models)
            custom_available = any(
                m == CUSTOM_MODEL_NAME or m == f"{CUSTOM_MODEL_NAME}:latest"
                for m in models
            )
            return {
                "available": model_available,
                "model": model,
                "custom_model": CUSTOM_MODEL_NAME,
                "models": models,
                "custom_created": custom_available,
                "error": (
                    None
                    if model_available
                    else f"Model '{model}' not pulled. Run: ollama pull {model} | Available: {', '.join(models[:10]) or 'none'}"
                ),
            }
    except asyncio.TimeoutError:
        return {
            "available": False,
            "model": model,
            "custom_model": CUSTOM_MODEL_NAME,
            "models": [],
            "error": "Connection timed out",
            "custom_created": False,
        }
    except Exception as e:
        return {
            "available": False,
            "model": model,
            "custom_model": CUSTOM_MODEL_NAME,
            "models": [],
            "error": str(e),
            "custom_created": False,
        }
