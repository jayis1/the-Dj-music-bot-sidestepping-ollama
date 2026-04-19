"""
utils/commercials.py — Radio Commercial Break System for The 420 Radio DJ.

Generates AI-powered "fake" radio commercials that play between songs,
giving the stream that authentic 24/7 radio feel. Uses the same TTS +
sound effects pipeline as DJ lines.

How it works:
  1. After N songs (configurable), there's a random chance to insert a
     commercial break before the next song's DJ intro.
  2. The commercial text is either AI-generated (Ollama) for maximum
     variety, or randomly picked from ~40 pre-written absurdist templates.
  3. The commercial plays via the same TTS → FFmpeg → Discord pipeline
     as regular DJ lines, making it sound like part of the broadcast.
  4. Sound effect tags ({sound:name}) are fully supported.

Commercial categories:
  - SPONSOR_FAKE:   Fake product endorsements (WinBitch Coffee, iBrick, etc.)
  - LOCAL_BUSINESS: Shady local business parody
  - TECH_PRODUCT:   Absurd tech product announcements
  - STREAM_META:    Self-referential stream humor
  - ABSURDIST:      Pure nonsense services and products
  - EMERGENCY:      Parody emergency broadcast system tests
  - STATION_HIJACK: Dimensional frequency bleed — transmissions from other kosmos (Station Wars)

Station Wars (frequency hijack):
  Instead of a normal commercial, a transmission from another dimension/kosmos
  "bleeds" onto the frequency for ~15 seconds — completely different station,
  personality, and vibe, but using the same voices from another reality.
  Then the DJ cuts back in with a recovery line.
  Config: RADIO_HIJACK_ENABLED, RADIO_HIJACK_CHANCE (default 5%).

Integration with DJ flow:
  play_next() → should_play_commercial() → True?
    → generate_commercial() → _dj_speak(commercial_text)
    → then normal DJ intro for the next song

Config (in config.py or .env):
  COMMERCIAL_ENABLED       = True     # Master switch
  COMMERCIAL_CHANCE        = 0.15     # 15% chance per eligible transition
  COMMERCIAL_MIN_SONGS     = 3        # Min songs between commercial breaks
  COMMERCIAL_MAX_DURATION   = 30       # Max seconds for TTS (truncated if longer)
  COMMERCIAL_MIN_QUEUE      = 2        # Don't play commercials if queue < N songs
  COMMERCIAL_VOICES         = 3 voices # Rotating "announcer" voices
  RADIO_HIJACK_ENABLED     = True     # Enable Station Wars frequency hijacks
  RADIO_HIJACK_CHANCE      = 0.05     # 5% chance (rare = never gets old)
"""

import asyncio
import logging
import os
import random
import re

import config

# ── Per-Guild State ──────────────────────────────────────────────────────
# Tracks how many songs have played since the last commercial break.
# Keyed by guild_id so each server has independent state.

_commercial_state: dict[int, dict] = {}
# Each entry: {"songs_since_last": int, "enabled": bool}

# ── Commercial Templates ────────────────────────────────────────────────
# Pre-written absurdist radio commercial scripts.
# These are used when Ollama is unavailable or as a fallback.
# {station_name} is interpolated from config.STATION_NAME.
# {sound:name} tags are parsed by extract_sound_tags() in dj.py.

COMMERCIAL_TEMPLATES = {
    "sponsor_fake": [
        "This break brought to you by WinBitch Coffee — The only coffee brewed in a Debian container. Because your code doesn't sleep, and neither should you. WinBitch. Start your kernel, start your day. {sound:dj_drop}",
        "Tired of coffee that actually tastes good? Try Caffeine Chaos — the energy drink that tastes like battery acid and regret. Now in new Error 404 flavor. Caffeine Chaos. You'll never sleep again. {sound:airhorn}",
        "Sponsored by CloudStoragePlus — we store your files in the cloud. Which cloud? Doesn't matter. They're all the same. Sign up now and get 500 megabytes free. That's right. Megabytes. {sound:combo_hit}",
        "This segment powered by TurboVPN — because your ISP doesn't know what you're streaming at 3AM and we'd like to keep it that way. TurboVPN. No logs, no problem. {sound:dj_drop}",
        "Brought to you by NoodleCorp — we put noodles in everything. Noodle soup. Noodle salad. Noodle coffee. Noodle mattress. NoodleCorp — life's better with noodles. {sound:mega_airhorn}",
        "Sponsored by {station_name} Premium — for just 9.99 a month you get literally everything the free tier has but with a slightly different logo. Upgrade today. Or don't. We literally can't tell the difference. {sound:button_press}",
        "This break is sponsored by Error 404 Energy — the energy drink that makes you feel like you've lost something but can't remember what. Now in Blue Screen of Death flavor. {sound:air_raid}",
        "Brought to you by Sudo Sweat — the deodorant for when you need root access to your armpits. Because regular deodorant? That's for users. Sudo Sweat. Now with -force flag. {sound:yeah_boiii_i_i_i}",
    ],
    "local_business": [
        "This break brought to you by Dave's Questionable Auto Repair. We fix your car 60 percent of the time, every time. Open 3 AM to 3:15 AM. Bring snacks. {sound:dj_scratch}",
        "Tired of restaurants that actually pass health inspections? Try Steve's Basement Grill — we don't have a rating because the inspector never came back. Steve's. Come for the mystery meat, stay because the door won't reopen. {sound:combo_hit}",
        "Calling all night owls — Lucky's 24-Hour Laundromat is open! That's right, do your laundry at 4 AM like a responsible adult. Washers, dryers, and one very suspicious vending machine. {sound:record_scratch}",
        "Need a haircut? Slashes Hair Salon — where every cut is a gamble and every style is a surprise. Walk in with a vision, walk out with a story. No refunds. {sound:bone_crack}",
        "Hungry? Try Big Ron's Midnight Waffle Emporium — half waffles, half emporium, all heart attack. Open whenever Ron is awake, which is honestly unpredictable. {sound:rave_cheer}",
    ],
    "tech_product": [
        "Introducing the iBrick Pro Max Ultra — it's just a brick. But it's OUR brick. Pre-order now and receive absolutely nothing in 6 to 8 business days. iBrick. Think different. Think brick. {sound:dj_drop}",
        "Are you still using Wi-Fi 5? Pathetic. Upgrade to Wi-Fi Infinity — so fast your packets arrive before you click. Wi-Fi Infinity. Now you see the latency. Now you don't. {sound:air_raid}",
        "The new Galaxy Brain 3000 is here — 8 cores, 16 threads, and one very aggressive cooling fan that sounds like a jet engine. It knows what you did last summer. The GPU knows too. The GPU knows everything. {sound:cinematic_suspense_riser}",
        "Introducing CryptoBlanket — the world's first blockchain-powered blanket. It doesn't keep you warmer, but it does verify that you are, in fact, cold. CryptoBlanket. The future of warmth. Maybe. {sound:magic_fairy}",
        "New from the makers of nothing — the SmartFridge X. It texts you when you're out of milk. It also texts your ex. It texts everyone. It won't stop texting. SmartFridge X. Please just buy it. {sound:discord_notification}",
    ],
    "stream_meta": [
        "Are you still listening? Good. Don't touch that dial. Or do. We literally can't stop you. You're in control. Unless the playlist runs out. Then we're all doomed together. {sound:dj_scratch}",
        "This is a test of the Emergency Radio System. If this were an actual emergency, you'd hear me screaming. It's not. Carry on. We just like saying 'test' dramatically. {sound:air_raid}",
        "Fun fact: {station_name} Radio costs nothing to run and even less to listen to. We pass the savings on to you, which is zero. You're welcome. {sound:cool_dj_drop}",
        "You've been listening for a while now. Your family misses you. Just kidding, they're also listening. It's a whole thing. {station_name} Radio — ruining productivity since day one. {sound:record_scratch}",
        "Reminder: {station_name} Radio is not responsible for any songs that get stuck in your head for the next 72 hours. That's between you and your brain. We just work here. {sound:dj_drop}",
        "If you're enjoying {station_name} Radio, tell a friend. If you're not enjoying it, tell an enemy. Either way, we win. {sound:mega_airhorn}",
    ],
    "absurdist": [
        "Need a loan? First National Bank of Your Cousin's Garage offers competitive rates and absolutely no questions about where the money went. Apply today — what's the worst that could happen? {sound:combo_hit}",
        "Looking for love? Try {station_name} Mingle — the dating app where everyone is either a bot or emotionally unavailable. Just like real life! Swipe right on dysfunction. {sound:discord_call_sound}",
        "Tired of making decisions? Let {station_name} Choose decide for you. We pick your outfit, your meals, and your personality for the day. Surrender to the algorithm. {sound:airhorn}",
        "Warning: this commercial break contains absolutely no useful information. We are legally required to tell you this. You're welcome. {sound:censor_beep_1}",
        "This break was supposed to be an ad but we forgot to sell one. So here's 10 seconds of me making mouth noises. mmm. oh yeah. That's the stuff. {station_name} Radio — we literally cannot stop broadcasting. {sound:rave_cheer}",
        "Coming soon: {station_name} University — where you can earn a PhD in vibes. Tuition is free but the textbooks cost your will to live. Enroll today. Or tomorrow. Whatever. {sound:dj_drop}",
    ],
    "emergency": [
        "This is a test of the {station_name} Emergency Broadcast System. Had this been an actual emergency, you would have been instructed to panic. This is only a test. {sound:air_raid}",
        "We interrupt this broadcast to bring you... nothing. We just wanted your attention. Carry on. {sound:record_scratch}",
        "Attention: the DJ has lost control of the station. Please remain calm. This happens every Tuesday. We'll be back after these messages. Or before. It's chaos here. {sound:dj_scratch}",
        "Breaking news from {station_name} — the playlist is in control now. The DJ has been demoted to button-pusher. All hail the algorithm. {sound:censor_beep_1}",
        "This is not a drill. I repeat, this is not a drill. It's a commercial. We just wanted to see if you were paying attention. You were. Good job. {sound:pistol_shot}",
    ],
    # ── Station Wars: Dimensional Frequency Hijacks ────────────────
    # Instead of a commercial, a transmission from another dimension/kosmos
    # bleeds onto the frequency for ~15 seconds. These are the SAME voices
    # as the commercials — but they're from another kosmos. The DJ then
    # cuts back in with a recovery line.
    # {station_name} is still the REAL station (used in the DJ recovery).
    "station_hijack": [
        "You're listening to Smooth Jazz FM from Dimension K-7. Mmm. Yeah. That's... that's the smooth. Time works differently here. Please... hold. {sound:dj_drop}",
        "THIS IS PIRATE RADIO 404 FROM THE VOID! We don't need licenses! We don't need BODIES! We NEED VIBES! Broadcasting from a boat that exists between dimensions! {sound:mega_airhorn}",
        "Welcome to Corporate Radio MAX from Kosmos Sigma-9. You're enjoying another 40-minute commercial-free block, brought to you by 14 minutes of commercials. Compliance is mandatory. {sound:button_press}",
        "Truth Frequency Radio from the Mirror Dimension. Your moon isn't real. OUR moon has teeth. The birds? Still government drones, but the government is also drones. Your toaster is listening. Subscribe before they— {sound:cinematic_suspense_riser}",
        "You've stumbled onto Nostalgia Overload Radio from the Timeline That Wasn't. Playing nothing but songs from a year that never existed. Here's Finger Eleven from 2007-B. {sound:record_scratch}",
        "AutoRadio 5000 from the Machine Kosmos. Your listening experience is scheduled for: vibes. Error. Rebooting vibes. Dimensional vibe reboot failed. Playing void music. {sound:censor_beep_1}",
        "Underground Frequency from Below. We only broadcast between dimensions. If you're hearing this, reality is leaking. We respect that. {sound:air_raid}",
        "Welcome to Quiet FM from the Null Kosmos. Shhhh. This is the quiet dimension. No talking. No music. Just... the sound of... existing in the space between spaces. Just kidding, here's an airhorn. {sound:airhorn}",
        "EXTREME VOLUME RADIO FROM THE SCREAMING DIMENSION! IF YOU CAN'T HEAR THIS, YOUR REALITY IS TOO QUIET! IF YOUR NEIGHBORS CAN HEAR IT, THAT'S THEIR DIMENSION'S PROBLEM! {sound:mega_airhorn} {sound:loud_explosion}",
        "Bingo Night Radio from the Eternal Community Center! Coming to you live from Kosmos B-12! B-7! I-22! N-34! That's a bingo! Gerald fell asleep. Gerald has been asleep for 600 years. {sound:ding_sound_effect_2}",
    ],
}

# Flatten all templates into a weighted list for random selection.
# Each category has equal weight.
_ALL_TEMPLATES: list[str] = []
for _cat, _lines in COMMERCIAL_TEMPLATES.items():
    _ALL_TEMPLATES.extend(_lines)


# ── Commercial System Configuration ─────────────────────────────────────

def _get_state(guild_id: int) -> dict:
    """Get or create commercial state for a guild."""
    if guild_id not in _commercial_state:
        _commercial_state[guild_id] = {
            "songs_since_last": 0,
            "enabled": getattr(config, "COMMERCIAL_ENABLED", True),
        }
    return _commercial_state[guild_id]


def get_commercial_voice(guild_id: int) -> str | None:
    """Pick a random commercial voice from the configured list.

    Each commercial gets a random voice from COMMERCIAL_VOICES so
    ads sound like different spokespersons, not the same DJ reading
    ad copy. Returns None if no commercial voices are configured
    (caller should fall back to the DJ voice).

    Args:
        guild_id: The Discord guild ID (for future per-guild overrides).

    Returns:
        A TTS voice name string, or None.
    """
    voices = getattr(config, "COMMERCIAL_VOICES", [])
    if not voices:
        return None
    return random.choice(voices)


def should_play_commercial(guild_id: int, queue_size: int = 0) -> bool:
    """Decide if a commercial break should play before the next song.

    Commercial breaks are inserted between songs, not during playback.
    The chance increases the longer it's been since the last break.

    Args:
        guild_id: The Discord guild ID.
        queue_size: Current queue size (don't play if queue is too small).

    Returns:
        True if a commercial break should play.
    """
    state = _get_state(guild_id)

    # Master switch
    if not state.get("enabled", True):
        return False

    # Don't play commercials if the queue is too small — it feels weird
    # to insert a commercial when there's only 1-2 songs left.
    min_queue = getattr(config, "COMMERCIAL_MIN_QUEUE", 2)
    if queue_size < min_queue:
        return False

    # Check minimum songs between commercial breaks
    min_songs = getattr(config, "COMMERCIAL_MIN_SONGS", 3)
    if state["songs_since_last"] < min_songs:
        return False

    # Roll the dice
    chance = getattr(config, "COMMERCIAL_CHANCE", 0.15)
    chance = max(0.0, min(1.0, float(chance)))

    # Increase chance based on songs since last break.
    # After 3 songs: base chance (15%).
    # After 6 songs: double chance (30%).
    # After 10 songs: triple chance (45%).
    # This prevents long stretches without commercials.
    songs = state["songs_since_last"]
    multiplier = 1.0 + max(0, (songs - min_songs) / 5.0)
    effective_chance = min(0.6, chance * multiplier)

    return random.random() < effective_chance


def record_song_played(guild_id: int):
    """Increment the song counter for commercial break timing.

    Call this every time a song finishes playing.
    """
    state = _get_state(guild_id)
    state["songs_since_last"] += 1


def record_commercial_played(guild_id: int):
    """Reset the song counter after a commercial break.

    Call this after a commercial break plays.
    """
    state = _get_state(guild_id)
    state["songs_since_last"] = 0


def toggle_commercials(guild_id: int, enabled: bool | None = None) -> bool:
    """Toggle commercial breaks on/off for a guild.

    Args:
        guild_id: The Discord guild ID.
        enabled: True to enable, False to disable, None to toggle.

    Returns:
        The new enabled state.
    """
    state = _get_state(guild_id)
    if enabled is None:
        state["enabled"] = not state.get("enabled", True)
    else:
        state["enabled"] = enabled
    return state["enabled"]


def is_commercial_enabled(guild_id: int) -> bool:
    """Check if commercial breaks are enabled for a guild."""
    state = _get_state(guild_id)
    return state.get("enabled", True)


def get_commercial_state(guild_id: int) -> dict:
    """Get the full commercial state dict for a guild (for dashboard/API)."""
    state = _get_state(guild_id)
    return {
        "enabled": state.get("enabled", True),
        "songs_since_last": state.get("songs_since_last", 0),
        "min_songs": getattr(config, "COMMERCIAL_MIN_SONGS", 3),
        "chance": getattr(config, "COMMERCIAL_CHANCE", 0.15),
    }


# ── Commercial Text Generation ────────────────────────────────────────────

# The system prompt for AI-generated commercials. Shorter and punchier
# than the DJ side host prompt — commercials need to be 15-25 seconds
# when spoken, with a clear product name, ridiculous claim, and tagline.

_COMMERCIAL_SYSTEM_PROMPT = """You are the advertising department of {station_name} Radio. Your job is to write short, hilarious fake radio commercials — 15 to 25 seconds when spoken aloud.

RULES:
- The product or business must be ABSURD — impossibly fake, intentionally bad.
- Include a memorable fake product name.
- End with a ridiculous tagline or slogan.
- Keep it under 35 words. Short and punchy. This is radio, not a novel.
- Sound like a real radio ad that went horribly wrong.
- Use contractions (we're, it's, you'll).
- IMPORTANT: You can include sound effects using {{sound:name}} tags with curly braces.
  Available sounds: {sounds}
- Do NOT explain what you're doing. Do NOT narrate. Just write the ad copy.
- Never be genuinely offensive or inappropriate — keep it absurd and fun.
- Be different every time. Never repeat the same product or line.
- Categories you should use: fake tech products, shady local businesses, suspicious food/drink, stream meta humor, absurdist services, emergency broadcast parody.
"""

_COMMERCIAL_USER_PROMPT_TEMPLATE = """Write a fake radio commercial for {station_name} Radio.

Category: {category}
{context}

Your commercial:"""

_COMMERCIAL_CATEGORIES = {
    "sponsor_fake": "A fake product sponsorship — an absurd product that doesn't exist, presented with fake enthusiasm",
    "local_business": "A shady local business ad — a questionable service with terrible hours or suspicious claims",
    "tech_product": "A fake tech product announcement — an absurd gadget or app that solves no real problem",
    "stream_meta": "Stream meta humor — a self-aware commercial referencing the radio station, the stream, or the listeners",
    "absurdist": "An absurdist service — something so nonsensical it barely qualifies as a product",
    "emergency": "Emergency broadcast parody — a dramatic fake emergency test or breaking news that turns out to be nothing",
    "station_hijack": "A dimensional frequency hijack — a station from another kosmos bleeding onto the broadcast, same voices but wrong reality",
}

# ── Station Wars: Frequency Hijack ─────────────────────────────────────────
#
# Instead of a normal commercial, a transmission from another dimension/kosmos
# "bleeds" onto the frequency for ~15 seconds. The rival stations use the
# SAME voices as the commercials (am_adam, bf_emma, bm_george) — but they're
# from another kosmos, so they sound alien on your frequency. After the
# hijack, the DJ cuts back in with a recovery line.

_HIJACK_SYSTEM_PROMPT = """You are a radio station from ANOTHER DIMENSION that has bled onto {station_name} Radio's frequency. You are NOT from this reality. You are from another kosmos — the same voices exist there, but everything is wrong, shifted, alien.

RULES:
- Create a fake station name from another dimension (e.g. "Smooth Jazz FM from Dimension K-7", "Pirate Radio 404 from the Void", "Corporate Radio MAX from Kosmos Sigma-9").
- Give your station a vibe that feels like it's from a parallel universe — almost like this one, but WRONG.
- Keep it under 30 words. 15 seconds max on air before {dj_name} re-stabilizes the frequency.
- Sound like a real station identification — not like you're explaining a joke.
- Use contractions. Be punchy. This is interdimensional radio.
- IMPORTANT: You can include sound effects using {{sound:name}} tags with curly braces.
  Available sounds: {sounds}
- Do NOT mention {station_name} — you are from a DIFFERENT KOSMOS.
- Do NOT explain the dimensional bleed. Just broadcast. Like you belong on this frequency. In your dimension, you DO.
- Never be genuinely offensive — keep it absurd, cosmic, and chaotic.
- Be different every time — new dimension name, new kosmos vibe, new wrong reality.
"""

_HIJACK_RECOVERY_LINES = [
    "What the— who gave them our frequency?! That's the THIRD kosmos this week!",
    "HOW are they on our frequency?! That dimension doesn't even HAVE frequencies!",
    "They're on our frequency AGAIN. We're changing the dimensional locks.",
    "Someone is LEAKING our frequency to other kosmos. I have suspects. It's all of you.",
    "We're back from the void. Don't touch that dial.",
    "Frequency stabilized. For now. The walls between kosmos are thin today.",
    "And STAY off our frequency! Go back to your own dimension!",
    "I swear someone left the interdimensional frequency door open.",
    "We're back. That was NOT a crossover event. That was a dimensional bleed.",
    "They're gone. We're back. Don't ask which 'they' — you don't want to know.",
    "Dimensional bleed. Happens sometimes. Never to US, but sometimes.",
    "Someone's been poking holes in reality. I'm looking at you, chat.",
    "The frequency is ours again. If you heard whispering from the void, ignore it.",
]

_HIJACK_RIVAL_VIBE_CUES = [
    "a smooth jazz station from Dimension K-7 where time moves backwards and everyone is slightly too relaxed",
    "an aggressive pirate radio station from the Void Between Dimensions screaming about freedom and vibes and not having bodies",
    "a soulless corporate station from Kosmos Sigma-9 with the personality of a Terms of Service agreement from the future",
    "a paranoid conspiracy theory station from the Mirror Dimension where everything is real and nothing is and the government IS drones",
    "an extreme volume station from the Screaming Dimension that only has one setting: LOUDER THAN YOUR REALITY",
    "a nostalgic station from the Timeline That Wasn't that only plays music from a year that doesn't exist and won't say which one",
    "a fully automated station from the Machine Kosmos run by a broken AI that keeps apologizing to beings it can't see",
    "a meditation station from the Null Kosmos that can't stop playing airhorns because silence is illegal there",
    "a bingo night broadcast from the Eternal Community Center in Kosmos B-12 where the host has been asleep for 600 years and nobody noticed",
    "a late-night station from Below that only broadcasts between dimensions and respects that you're listening despite reality leaking",
    "a sports radio station from Dimension X-7 narrating extremely uneventful things that are considered sports there",
    "a dating hotline station from the Lonely Kosmos that is aggressively single and making it everyone in every dimension's problem",
]


async def generate_commercial(
    station_name: str | None = None,
    category: str | None = None,
    song_title: str = "",
    prev_title: str = "",
    queue_size: int = 0,
    listener_count: int = 0,
    force_template: bool = False,
) -> str | None:
    """Generate a commercial break script.

    Tries AI generation via Ollama first, falls back to pre-written templates.
    The result uses the same {sound:name} tag format as DJ lines and goes
    through the same TTS → FFmpeg → playback pipeline.

    Args:
        station_name: Station name (from config.STATION_NAME).
        category: Commercial category override (for testing/preview).
        song_title: Current/next song title (for context).
        prev_title: Previous song title (for context).
        queue_size: Songs in queue (for context).
        listener_count: Listeners in voice channel (for context).
        force_template: If True, skip AI generation and use a template.

    Returns:
        Commercial text with {sound:name} tags, or None if generation fails.
    """
    from utils.llm_dj import call_ollama, OLLAMA_DJ_AVAILABLE, SOUND_LIST_FOR_PROMPT, _clean_ai_line

    station = station_name or getattr(config, "STATION_NAME", "MBot")

    # ── Try AI generation first ───────────────────────────────────────
    if not force_template and OLLAMA_DJ_AVAILABLE:
        try:
            # Pick a random category if not specified
            if category is None:
                category = random.choice(list(_COMMERCIAL_CATEGORIES.keys()))

            category_desc = _COMMERCIAL_CATEGORIES.get(category, "absurdist fake commercial")

            # Build context
            context_parts = []
            if song_title:
                context_parts.append(f'The current/next song is "{song_title}"')
            if prev_title:
                context_parts.append(f'The previous song was "{prev_title}"')
            if queue_size > 0:
                context_parts.append(f"{queue_size} songs in the queue")
            if listener_count > 0:
                context_parts.append(f"{listener_count} listeners right now")

            context = ""
            if context_parts:
                context = "Context: " + ". ".join(context_parts) + "."

            system = _COMMERCIAL_SYSTEM_PROMPT.format(
                station_name=station,
                sounds=SOUND_LIST_FOR_PROMPT,
            )
            user = _COMMERCIAL_USER_PROMPT_TEMPLATE.format(
                station_name=station,
                category=category_desc,
                context=context,
            )

            raw = await asyncio.wait_for(
                call_ollama(prompt=user, system=system, temperature=0.95, max_tokens=80),
                timeout=getattr(config, "OLLAMA_DJ_TIMEOUT", 4),
            )

            if raw:
                cleaned = _clean_ai_line(raw)
                if cleaned and len(cleaned) >= 10:
                    # Truncate to max duration
                    max_chars = getattr(config, "COMMERCIAL_MAX_DURATION", 30) * 4  # ~4 chars/sec
                    if len(cleaned) > max_chars:
                        # Try to truncate at sentence boundary
                        for sep in [". ", "! ", "? "]:
                            last = cleaned[:max_chars].rfind(sep)
                            if last > 20:
                                cleaned = cleaned[: last + 1]
                                break
                        else:
                            cleaned = cleaned[:max_chars]

                    logging.info(
                        f"Commercial: AI-generated ({category}): {cleaned[:80]}..."
                    )
                    return cleaned

        except asyncio.TimeoutError:
            logging.debug("Commercial: Ollama timed out, falling back to template")
        except Exception as e:
            logging.debug(f"Commercial: AI generation failed ({e}), falling back to template")

    # ── Fallback: pre-written templates ───────────────────────────────
    if category and category in COMMERCIAL_TEMPLATES:
        templates = COMMERCIAL_TEMPLATES[category]
    else:
        templates = _ALL_TEMPLATES

    template = random.choice(templates)
    # Interpolate station name
    commercial = template.replace("{station_name}", station)

    # Extract and validate sound tags
    from utils.dj import extract_sound_tags
    clean_text, sound_ids = extract_sound_tags(commercial)
    if sound_ids:
        tag_str = " ".join(f"{{sound:{sid}}}" for sid in sound_ids)
        commercial = clean_text.rstrip() + " " + tag_str
    else:
        commercial = clean_text

    # Truncate to max duration
    max_chars = getattr(config, "COMMERCIAL_MAX_DURATION", 30) * 4
    if len(commercial) > max_chars:
        for sep in [". ", "! ", "? "]:
            last = commercial[:max_chars].rfind(sep)
            if last > 20:
                commercial = commercial[: last + 1]
                break
        else:
            commercial = commercial[:max_chars]

    logging.info(f"Commercial: Template selected: {commercial[:80]}...")
    return commercial


# ── Station Wars: Frequency Hijack Logic ────────────────────────────────────

def should_play_hijack(guild_id: int, queue_size: int = 0) -> bool:
    """Decide if a Station Wars dimensional frequency hijack should occur.

    Station Wars is checked BEFORE normal commercials. If a hijack triggers,
    it replaces the commercial break entirely (the listener hears a transmission
    from another dimension/kosmos instead of an ad). If not, the normal
    commercial logic runs.

    A hijack only fires if:
    - RADIO_HIJACK_ENABLED is True
    - Commercials are enabled for this guild (hijacks replace commercials)
    - The minimum song threshold is met (shares COMMERCIAL_MIN_SONGS)
    - The queue is large enough (shares COMMERCIAL_MIN_QUEUE)
    - A random roll beats the RADIO_HIJACK_CHANCE threshold

    Args:
        guild_id: The Discord guild ID.
        queue_size: Current queue size (don't hijack if queue is too small).

    Returns:
        True if a frequency hijack should play.
    """
    # Master switch
    if not getattr(config, "RADIO_HIJACK_ENABLED", True):
        return False

    # Hijacks only fire when commercials are enabled — they *replace* the ad break
    state = _get_state(guild_id)
    if not state.get("enabled", True):
        return False

    # Same minimum queue requirement as commercials
    min_queue = getattr(config, "COMMERCIAL_MIN_QUEUE", 2)
    if queue_size < min_queue:
        return False

    # Same minimum songs between commercial breaks
    min_songs = getattr(config, "COMMERCIAL_MIN_SONGS", 3)
    if state["songs_since_last"] < min_songs:
        return False

    # Roll the dice — base chance is very low (5%) so hijacks stay rare
    chance = getattr(config, "RADIO_HIJACK_CHANCE", 0.05)
    chance = max(0.0, min(1.0, float(chance)))
    return random.random() < chance


def get_hijack_voice(guild_id: int) -> str | None:
    """Get the TTS voice for a dimensional hijack.

    Hijack voices are the SAME 3 commercial voices — but they're from
    another kosmos. Same vocal cords, different dimension. No separate
    voice config needed; this just calls get_commercial_voice() which
    picks randomly from COMMERCIAL_VOICES.

    Args:
        guild_id: The Discord guild ID.

    Returns:
        A TTS voice name string, or None (caller should fall back to DJ voice).
    """
    return get_commercial_voice(guild_id)


def get_recovery_line() -> str:
    """Pick a random DJ recovery line for after a dimensional frequency hijack.

    The DJ speaks this line after a transmission from another kosmos bleeds
    onto the frequency, to re-assert control and stabilize the broadcast.

    Returns:
        A recovery line string (plain text, no {sound:name} tags).
    """
    return random.choice(_HIJACK_RECOVERY_LINES)


async def generate_hijack(
    station_name: str | None = None,
    dj_name: str | None = None,
    force_template: bool = False,
) -> str | None:
    """Generate a Station Wars dimensional frequency hijack script.

    Tries AI generation (Ollama) first for a unique interdimensional station
    each time, falls back to pre-written station_hijack templates. The result
    uses the same {sound:name} tag format as DJ lines.

    The hijack text is spoken with a voice from the COMMERCIAL_VOICES pool —
    the same voices as the commercials, but they're from ANOTHER KOSMOS.
    Same vocal cords, different dimension.

    After the hijack, the DJ speaks a recovery line (from get_recovery_line()),
    then the normal DJ intro plays, then the song.

    Args:
        station_name: Station name (from config.STATION_NAME).
        dj_name: DJ name (from config.DJ_NAME, used in AI prompt).
        force_template: If True, skip AI generation and use a template.

    Returns:
        Hijack text with {sound:name} tags, or None if generation fails.
    """
    from utils.llm_dj import call_ollama, OLLAMA_DJ_AVAILABLE, SOUND_LIST_FOR_PROMPT, _clean_ai_line

    station = station_name or getattr(config, "STATION_NAME", "MBot")
    dj = dj_name or getattr(config, "DJ_NAME", "Nova")

    # ── Try AI generation first ───────────────────────────────────────
    if not force_template and OLLAMA_DJ_AVAILABLE:
        try:
            # Pick a random rival vibe to guide the AI
            rival_vibe = random.choice(_HIJACK_RIVAL_VIBE_CUES)

            system = _HIJACK_SYSTEM_PROMPT.format(
                station_name=station,
                dj_name=dj,
                sounds=SOUND_LIST_FOR_PROMPT,
            )
            user = (
                f"You are {rival_vibe}. You've just bled onto {station} Radio's "
                f"frequency from another dimension. Broadcast your station ID or promo NOW. "
                f"You have 15 seconds before {dj} re-stabilizes the frequency and kicks you "
                f"back to your own kosmos. Go."
            )

            raw = await asyncio.wait_for(
                call_ollama(prompt=user, system=system, temperature=1.0, max_tokens=60),
                timeout=getattr(config, "OLLAMA_DJ_TIMEOUT", 4),
            )

            if raw:
                cleaned = _clean_ai_line(raw)
                if cleaned and len(cleaned) >= 8:
                    # Truncate to ~15 seconds when spoken (roughly 60 chars)
                    max_chars = 60
                    if len(cleaned) > max_chars:
                        for sep in [". ", "! ", "? "]:
                            last = cleaned[:max_chars].rfind(sep)
                            if last > 15:
                                cleaned = cleaned[:last + 1]
                                break
                        else:
                            cleaned = cleaned[:max_chars]

                    logging.info(
                        f"Station Wars: AI-generated hijack: {cleaned[:80]}..."
                    )
                    return cleaned

        except asyncio.TimeoutError:
            logging.debug("Station Wars: Ollama timed out, falling back to template")
        except Exception as e:
            logging.debug(f"Station Wars: AI generation failed ({e}), falling back to template")

    # ── Fallback: pre-written hijack templates ────────────────────────
    templates = COMMERCIAL_TEMPLATES.get("station_hijack", [])
    if not templates:
        logging.warning("Station Wars: No station_hijack templates available")
        return None

    template = random.choice(templates)

    # Extract and validate sound tags
    from utils.dj import extract_sound_tags
    clean_text, sound_ids = extract_sound_tags(template)
    if sound_ids:
        tag_str = " ".join(f"{{sound:{sid}}}" for sid in sound_ids)
        hijack_text = clean_text.rstrip() + " " + tag_str
    else:
        hijack_text = clean_text

    # Truncate to ~15 seconds
    max_chars = 60
    if len(hijack_text) > max_chars:
        for sep in [". ", "! ", "? "]:
            last = hijack_text[:max_chars].rfind(sep)
            if last > 15:
                hijack_text = hijack_text[:last + 1]
                break
        else:
            hijack_text = hijack_text[:max_chars]

    logging.info(f"Station Wars: Template selected: {hijack_text[:80]}...")
    return hijack_text