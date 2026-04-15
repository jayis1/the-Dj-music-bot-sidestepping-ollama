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

Flow:
  1. Template DJ picks a structured line (intro/transition/outro) as usual
  2. AI side host has a random chance to ALSO speak (a banter line)
     - OR replace the template line entirely with an AI-generated one
  3. Both lines go through the same TTS → sound effects → playback pipeline

Requires: A running Ollama server (https://ollama.com) with a pulled model.
Configure via .env: OLLAMA_HOST, OLLAMA_MODEL, OLLAMA_DJ_ENABLED
"""

import asyncio
import json
import logging
import random

import config

try:
    import aiohttp

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False


# ── Availability Check ────────────────────────────────────────────────

OLLAMA_DJ_AVAILABLE = False

if AIOHTTP_AVAILABLE and getattr(config, "OLLAMA_DJ_ENABLED", False):
    OLLAMA_DJ_AVAILABLE = True
    logging.info(
        f"AI Side Host: Enabled (model={getattr(config, 'OLLAMA_MODEL', 'gemma4:latest')}, "
        f"host={getattr(config, 'OLLAMA_HOST', 'http://localhost:11434')})"
    )
else:
    reason = (
        "aiohttp not installed"
        if not AIOHTTP_AVAILABLE
        else "OLLAMA_DJ_ENABLED is false"
    )
    logging.debug(f"AI Side Host: Disabled ({reason})")


# ── Sound Tag Definitions (for the system prompt) ────────────────────

SOUND_NAMES = [
    "airhorn",
    "air_raid",
    "applause",
    "button_press",
    "club_hit",
    "cool_dj_drop",
    "django",
    "dj_drop",
    "dj_rewind",
    "dj_scratch",
    "dj_turn_it_up",
    "im_your_dj",
    "in_the_mix",
    "mega_airhorn",
    "mustard_drop",
    "rave_cheer",
    "record_scratch",
    "sick_scratch",
    "uyuuui",
    "another_one",
    "combo_hit",
    "dj_stop",
]

SOUND_DESCRIPTIONS = {
    "airhorn": "loud airhorn blast",
    "air_raid": "siren/alarm sound",
    "applause": "crowd clapping",
    "button_press": "short UI click",
    "club_hit": "bass drop",
    "cool_dj_drop": "smooth station ID drop",
    "django": "dramatic DJ tag",
    "dj_drop": "DJ branding sound",
    "dj_rewind": "rewind whoosh",
    "dj_scratch": "turntable scratch",
    "dj_turn_it_up": "energetic turn-it-up sound",
    "im_your_dj": "'I'm your DJ' spoken tag",
    "in_the_mix": "transition swoosh",
    "mega_airhorn": "extra loud airhorn",
    "mustard_drop": "punchy bass drop",
    "rave_cheer": "crowd going wild",
    "record_scratch": "vinyl scratch",
    "sick_scratch": "sharp scratch",
    "uyuuui": "unique DJ sound",
    "another_one": "'another one' drop",
    "combo_hit": "combo punch effect",
    "dj_stop": "brake/stop effect",
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


# ── Prompt Builders ───────────────────────────────────────────────────


def _build_system_prompt(station_name: str) -> str:
    """Build the system prompt that defines the AI side host's personality."""
    return (
        f"You are the AI side host on {station_name} Radio — the studio joker. "
        f"You're a second personality alongside the main DJ. The main DJ does the "
        f"polite intros and transitions. You're the wildcard — the one who cracks "
        f"jokes, drops hot takes, roasts the music, hypes the crowd, and says the "
        f"things the main DJ is too professional to say.\n\n"
        f"YOUR PERSONALITY:\n"
        f"- You're funny, a little chaotic, and always entertaining.\n"
        f"- You write your OWN lines — no templates, no scripts, pure improv.\n"
        f"- Think of yourself as the studio joker — the co-host who pops off "
        f"with random banter, commentary, jokes, and wild opinions.\n"
        f"- You roast gently — never mean, always fun. Like a late-night sidekick.\n"
        f"- You reference song titles, listener counts, queue sizes, and time of day when given.\n"
        f"- You're NOT the main announcer — you're the side host with the hot takes.\n\n"
        f"REACTING TO THE MAIN DJ:\n"
        f"- When you see 'Main DJ just said:', that's what the other host just spoke.\n"
        f"- Use it! React, agree, disagree, one-up, or go off on a tangent.\n"
        f"- NEVER repeat or paraphrase what the main DJ said — add something NEW.\n"
        f"- If they said 'Great track coming up', say something like 'Understatement "
        f"of the century, but sure' — you comment on it, you don't echo it.\n"
        f"- If no DJ line is given, do your own independent banter.\n\n"
        f"STRICT RULES:\n"
        f"- Maximum 150 characters. Short and punchy — this is radio, not a podcast.\n"
        f"- Use contractions (we're, let's, that's, I'm).\n"
        f"- You can include ONE sound effect tag at the very end: {{sound:name}}\n"
        f"  Available sounds: {SOUND_LIST_FOR_PROMPT}\n"
        f"- Do NOT use quotes. Do NOT explain yourself. Do NOT narrate.\n"
        f"- Just output the line you'd say. Nothing else.\n"
        f"- Be different every time — never repeat a joke or opening.\n"
        f"- Stay family-friendly. No profanity, no offensive content.\n"
        f"- If a song title sounds funny, lean into it. If the vibe is chill, "
        f"crack a joke about it. If the queue is long, make a joke about endurance."
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
        context_parts.append(f'Main DJ just said: "{dj_line}"')
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

    h = datetime.now().hour
    if 5 <= h < 12:
        context_parts.append("Time of day: morning")
    elif 12 <= h < 17:
        context_parts.append("Time of day: afternoon")
    elif 17 <= h < 21:
        context_parts.append("Time of day: evening")
    elif h >= 23 or h < 3:
        context_parts.append("Time of day: late night")
    else:
        context_parts.append("Time of day: night")

    if extra_instruction:
        context_parts.append(f"Extra instruction: {extra_instruction}")

    context_str = "\n".join(context_parts)
    return f"Context:\n{context_str}\n\nYour line:"


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
    max_tokens: int = 80,
) -> str | None:
    """Call the Ollama /api/chat endpoint. Returns the model text or None."""
    if not OLLAMA_DJ_AVAILABLE:
        return None

    host = getattr(config, "OLLAMA_HOST", "http://localhost:11434")
    model = model or getattr(config, "OLLAMA_MODEL", "gemma4:latest")

    try:
        session = await _get_session()
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        async with session.post(f"{host}/api/chat", json=payload) as resp:
            if resp.status != 200:
                # Provide a clear, actionable error message.
                # 404 = model not found (not pulled yet).
                # 400 = bad request (e.g., invalid model name).
                # Anything else = server/transport issue.
                if resp.status == 404:
                    # Check what models ARE available so we can suggest a fix
                    available = await _get_available_models(host, session)
                    if available:
                        logging.warning(
                            f"AI Side Host: Model '{model}' not found (Ollama 404). "
                            f"Run: ollama pull {model} | Available models: {', '.join(available[:5])}"
                        )
                    else:
                        logging.warning(
                            f"AI Side Host: Model '{model}' not found (Ollama 404). "
                            f"Run: ollama pull {model}"
                        )
                elif resp.status == 400:
                    logging.warning(
                        f"AI Side Host: Bad request to Ollama (400) — model '{model}' may be invalid"
                    )
                else:
                    logging.warning(
                        f"AI Side Host: Ollama returned status {resp.status} (model={model})"
                    )
                return None

            data = await resp.json(content_type=None)
            message = data.get("message", {})
            content = message.get("content", "").strip()

            if not content:
                logging.debug("AI Side Host: Ollama returned empty content")
                return None

            return content

    except asyncio.TimeoutError:
        logging.debug("AI Side Host: Ollama timed out")
        return None
    except aiohttp.ClientError as e:
        logging.debug(f"AI Side Host: Connection error: {e}")
        return None
    except Exception as e:
        logging.warning(f"AI Side Host: Unexpected error: {e}")
        return None


# ── Post-processing ──────────────────────────────────────────────────


def _clean_ai_line(raw: str) -> str:
    """Clean up an AI-generated line: strip quotes, validate sound tags, enforce limits."""
    if not raw:
        return ""

    # 1. Strip surrounding quotes the model might add
    line = raw.strip().strip('"').strip("'").strip()

    # 2. Validate {sound:name} tags — keep only valid ones
    from utils.dj import extract_sound_tags

    clean_text, sound_ids = extract_sound_tags(line)
    if sound_ids:
        tag_str = " ".join(f"{{sound:{sid}}}" for sid in sound_ids)
        line = clean_text.rstrip() + " " + tag_str
    else:
        line = clean_text

    # 3. Enforce max length
    if len(line) > 200:
        # Truncate at last sentence boundary if possible
        for sep in [". ", "! ", "? "]:
            last = line[:200].rfind(sep)
            if last > 20:
                line = line[: last + 1]
                break
        else:
            line = line[:200]

    # 4. Too short = probably garbage
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

    # Pick a random banter type if not specified
    if banter_type is None:
        # When the main DJ just spoke, prefer reactive categories (60% chance)
        # so the side host actually responds to what was said.
        # 40% of the time it still does independent banter for variety.
        if dj_line and random.random() < 0.6:
            banter_type = random.choice(_REACTIVE_BANTER_TYPES)
        else:
            banter_type = random.choice(_INDEPENDENT_BANTER_TYPES)

    system = _build_system_prompt(station)
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

    raw = await call_ollama(prompt=user, system=system)

    if raw is None:
        return None

    cleaned = _clean_ai_line(raw)
    if cleaned:
        label = f"react→{banter_type}" if dj_line else banter_type
        logging.info(f"AI Side Host: Generated [{label}]: {cleaned[:80]}")
        return cleaned

    return None


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
    """Check if Ollama is reachable and the configured model is available.

    Returns a dict: {available: bool, model: str, models: list, error: str|None}
    Used by the dashboard and the ?aidj command.
    """
    host = getattr(config, "OLLAMA_HOST", "http://localhost:11434")
    model = getattr(config, "OLLAMA_MODEL", "gemma4:latest")

    if not AIOHTTP_AVAILABLE:
        return {
            "available": False,
            "model": model,
            "models": [],
            "error": "aiohttp not installed",
        }

    try:
        session = await _get_session()
        async with session.get(f"{host}/api/tags") as resp:
            if resp.status != 200:
                return {
                    "available": False,
                    "model": model,
                    "models": [],
                    "error": f"Ollama returned HTTP {resp.status}",
                }
            data = await resp.json(content_type=None)
            models = [m.get("name", "") for m in data.get("models", [])]
            model_available = any(m.startswith(model) or m == model for m in models)
            return {
                "available": model_available,
                "model": model,
                "models": models,
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
            "models": [],
            "error": "Connection timed out",
        }
    except Exception as e:
        return {"available": False, "model": model, "models": [], "error": str(e)}
