"""
utils/pregen.py — Pre-Generate DJ Lines & TTS for Queue

While a long song plays, this module pre-generates DJ intro/outro TTS audio
files for upcoming songs in the queue. Files are saved to assets/part2/ with
numbered filenames and are NEVER deleted — they persist across restarts so
the DJ always has lines ready.

Both the main DJ and the AI Side Host lines are pre-generated (when the AI
host is enabled). The main DJ uses template-based lines (generate_intro/
generate_outro), while the AI host uses the LLM to generate commentary.

File naming convention:
    dj_{guild_id}_{index}_{title_hash}.wav       — Main DJ
    ai_{guild_id}_{index}_{title_hash}.wav       — AI Side Host

Index numbers correspond to queue position (0 = next song, 1 = after that, etc.)
"""

import asyncio
import hashlib
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import config

# ── Directory for pre-generated DJ assets ───────────────────────────────
PREGEN_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "part2")

# How long a pre-generated entry is considered fresh (seconds).
# After this, the DJ line text is regenerated (TTS file stays on disk).
DEFAULT_TTL = 1800  # 30 minutes

# Maximum number of songs to pre-generate per guild
MAX_PREGEN_AHEAD = 5


@dataclass
class PregenEntry:
    """A pre-generated DJ line with its TTS audio file and metadata.

    Stored permanently in assets/part2/.

    Includes optional commercial break, Station Wars hijack, and
    DJ recovery line data — all pre-generated for zero-gap transitions.
    """

    dj_text: str = ""  # The DJ line text (clean, no {sound:} tags)
    dj_tts_path: str = ""  # Path to the DJ TTS audio file (permanent)
    dj_sound_ids: list = field(default_factory=list)  # {sound:name} tags extracted
    ai_text: str = ""  # The AI side host line text (clean)
    ai_tts_path: str = ""  # Path to the AI side host TTS audio file (permanent)
    # ── Commercial break (pre-generated) ──
    commercial_text: str = (
        ""  # Commercial script text (clean, with {sound:} tags stripped)
    )
    commercial_tts_path: str = ""  # Path to the commercial TTS audio file (permanent)
    commercial_sound_ids: list = field(
        default_factory=list
    )  # Sound tags from commercial text
    commercial_voice: str = ""  # Voice name used for this commercial
    # ── Station Wars: Frequency Hijack (pre-generated) ──
    hijack_text: str = ""  # Hijack script text (clean, with {sound:} tags stripped)
    hijack_tts_path: str = ""  # Path to the hijack TTS audio file (permanent)
    hijack_sound_ids: list = field(default_factory=list)  # Sound tags from hijack text
    hijack_voice: str = ""  # Voice name used for this hijack
    # ── DJ Recovery line after hijack (pre-generated) ──
    recovery_text: str = ""  # DJ recovery line text
    recovery_tts_path: str = ""  # Path to recovery TTS audio file (permanent)
    # ── Common metadata ──
    title: str = ""  # Song title this entry is for
    prev_title: str = ""  # Previous song title (for transitions)
    queue_index: int = 0  # Position in queue (0 = next song)
    created_at: float = 0.0  # Timestamp when this was generated
    guild_id: int = 0  # Guild this was generated for


def _title_hash(title: str, prev_title: str = "") -> str:
    """Create a short hash for a song title + prev title combination."""
    combo = f"{prev_title}|||{title}"
    return hashlib.sha256(combo.encode("utf-8")).hexdigest()[:12]


def _pregen_filename(guild_id: int, index: int, title_hash: str) -> str:
    """Build a numbered pregen filename: dj_{guild}_{index}_{hash}.wav"""
    return f"dj_{guild_id}_{index}_{title_hash}.wav"


def _pregen_path(guild_id: int, index: int, title_hash: str) -> str:
    """Full path to a pregen file in assets/part2/."""
    return os.path.join(PREGEN_DIR, _pregen_filename(guild_id, index, title_hash))


def _pregen_meta_path(guild_id: int, index: int, title_hash: str) -> str:
    """Full path to a pregen metadata JSON file."""
    return os.path.join(PREGEN_DIR, f"dj_{guild_id}_{index}_{title_hash}.json")


def _ai_pregen_filename(guild_id: int, index: int, title_hash: str) -> str:
    """Build a numbered AI host pregen filename: ai_{guild}_{index}_{hash}.wav"""
    return f"ai_{guild_id}_{index}_{title_hash}.wav"


def _ai_pregen_path(guild_id: int, index: int, title_hash: str) -> str:
    """Full path to an AI host pregen file in assets/part2/."""
    return os.path.join(PREGEN_DIR, _ai_pregen_filename(guild_id, index, title_hash))


def _ai_pregen_meta_path(guild_id: int, index: int, title_hash: str) -> str:
    """Full path to an AI host pregen metadata JSON file."""
    return os.path.join(PREGEN_DIR, f"ai_{guild_id}_{index}_{title_hash}.json")


def _commercial_pregen_filename(guild_id: int, index: int, title_hash: str) -> str:
    """Build a numbered commercial pregen filename: com_{guild}_{index}_{hash}.wav"""
    return f"com_{guild_id}_{index}_{title_hash}.wav"


def _commercial_pregen_path(guild_id: int, index: int, title_hash: str) -> str:
    """Full path to a commercial pregen file in assets/part2/."""
    return os.path.join(
        PREGEN_DIR, _commercial_pregen_filename(guild_id, index, title_hash)
    )


def _hijack_pregen_filename(guild_id: int, index: int, title_hash: str) -> str:
    """Build a numbered hijack pregen filename: hj_{guild}_{index}_{hash}.wav"""
    return f"hj_{guild_id}_{index}_{title_hash}.wav"


def _hijack_pregen_path(guild_id: int, index: int, title_hash: str) -> str:
    """Full path to a hijack pregen file in assets/part2/."""
    return os.path.join(
        PREGEN_DIR, _hijack_pregen_filename(guild_id, index, title_hash)
    )


def _recovery_pregen_filename(guild_id: int, index: int, title_hash: str) -> str:
    """Build a numbered recovery pregen filename: rec_{guild}_{index}_{hash}.wav"""
    return f"rec_{guild_id}_{index}_{title_hash}.wav"


def _recovery_pregen_path(guild_id: int, index: int, title_hash: str) -> str:
    """Full path to a recovery pregen file in assets/part2/."""
    return os.path.join(
        PREGEN_DIR, _recovery_pregen_filename(guild_id, index, title_hash)
    )


def ensure_pregen_dir() -> None:
    """Create the pregen directory if it doesn't exist."""
    os.makedirs(PREGEN_DIR, exist_ok=True)


def is_entry_fresh(entry: PregenEntry, ttl: float = DEFAULT_TTL) -> bool:
    """Check if a pre-generated entry is still within its TTL."""
    if not entry.created_at:
        return False
    if entry.dj_tts_path and not os.path.isfile(entry.dj_tts_path):
        return False
    return (time.time() - entry.created_at) < ttl


class DjPregenerator:
    """Pre-generates DJ lines and TTS audio for upcoming songs.

    Unlike a cache that gets consumed and deleted, pregen files live
    permanently in assets/part2/ so they persist across bot restarts.

    Usage:
        pregen = DjPregenerator(bot)

        # While a long song plays, kick off pregen for the queue:
        await pregen.pregenerate_upcoming(guild_id, queue, current_title)

        # When a song ends and DJ needs to speak, look up the ready file:
        entry = pregen.lookup(guild_id, song_title, prev_title)
        if entry and entry.dj_tts_path:
            play_tts(entry.dj_tts_path, entry.dj_text)  # Zero latency!
    """

    def __init__(self, bot):
        self.bot = bot
        # guild_id -> {title_hash: PregenEntry} — in-memory cache
        self._cache: dict[int, dict[str, PregenEntry]] = {}
        # guild_id -> currently running pregeneration task
        self._tasks: dict[int, asyncio.Task] = {}
        ensure_pregen_dir()
        # Load any existing pregen files from disk on startup
        self._load_from_disk()

    def _music_cog(self):
        """Get the Music cog instance."""
        return self.bot.get_cog("Music")

    def _load_from_disk(self):
        """Load pregen metadata JSON files from assets/part2/ on startup."""
        if not os.path.isdir(PREGEN_DIR):
            return
        count = 0
        for fname in os.listdir(PREGEN_DIR):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(PREGEN_DIR, fname)
            try:
                import json

                with open(fpath, "r") as f:
                    data = json.load(f)
                entry = PregenEntry(
                    dj_text=data.get("dj_text", ""),
                    dj_tts_path=data.get("dj_tts_path", ""),
                    dj_sound_ids=data.get("dj_sound_ids", []),
                    ai_text=data.get("ai_text", ""),
                    ai_tts_path=data.get("ai_tts_path", ""),
                    commercial_text=data.get("commercial_text", ""),
                    commercial_tts_path=data.get("commercial_tts_path", ""),
                    commercial_sound_ids=data.get("commercial_sound_ids", []),
                    commercial_voice=data.get("commercial_voice", ""),
                    hijack_text=data.get("hijack_text", ""),
                    hijack_tts_path=data.get("hijack_tts_path", ""),
                    hijack_sound_ids=data.get("hijack_sound_ids", []),
                    hijack_voice=data.get("hijack_voice", ""),
                    recovery_text=data.get("recovery_text", ""),
                    recovery_tts_path=data.get("recovery_tts_path", ""),
                    title=data.get("title", ""),
                    prev_title=data.get("prev_title", ""),
                    queue_index=data.get("queue_index", 0),
                    created_at=data.get("created_at", 0),
                    guild_id=data.get("guild_id", 0),
                )
                # Only load if the TTS file still exists on disk
                if entry.dj_tts_path and os.path.isfile(entry.dj_tts_path):
                    title_hash = _title_hash(entry.title, entry.prev_title)
                    gid = entry.guild_id
                    if gid not in self._cache:
                        self._cache[gid] = {}
                    self._cache[gid][title_hash] = entry
                    count += 1
            except Exception:
                pass
        if count:
            logging.info(f"Pregen: Loaded {count} pre-generated DJ lines from disk")

    def _save_entry_to_disk(self, entry: PregenEntry):
        """Save a pregen entry's metadata as a JSON file next to the WAV."""
        if not entry.dj_tts_path or not entry.guild_id:
            return
        title_hash = _title_hash(entry.title, entry.prev_title)
        meta_path = _pregen_meta_path(entry.guild_id, entry.queue_index, title_hash)
        try:
            import json

            with open(meta_path, "w") as f:
                json.dump(
                    {
                        "dj_text": entry.dj_text,
                        "dj_tts_path": entry.dj_tts_path,
                        "dj_sound_ids": entry.dj_sound_ids,
                        "ai_text": entry.ai_text,
                        "ai_tts_path": entry.ai_tts_path,
                        "commercial_text": entry.commercial_text,
                        "commercial_tts_path": entry.commercial_tts_path,
                        "commercial_sound_ids": entry.commercial_sound_ids,
                        "commercial_voice": entry.commercial_voice,
                        "hijack_text": entry.hijack_text,
                        "hijack_tts_path": entry.hijack_tts_path,
                        "hijack_sound_ids": entry.hijack_sound_ids,
                        "hijack_voice": entry.hijack_voice,
                        "recovery_text": entry.recovery_text,
                        "recovery_tts_path": entry.recovery_tts_path,
                        "title": entry.title,
                        "prev_title": entry.prev_title,
                        "queue_index": entry.queue_index,
                        "created_at": entry.created_at,
                        "guild_id": entry.guild_id,
                    },
                    f,
                    indent=2,
                )
        except Exception as e:
            logging.warning(f"Pregen: Failed to save metadata: {e}")

    def lookup(
        self,
        guild_id: int,
        title: str,
        prev_title: str = "",
    ) -> Optional[PregenEntry]:
        """Look up a pre-generated entry for a song transition.

        Returns the cached entry if found and fresh, otherwise None.
        Does NOT remove the entry — files stay on disk permanently.
        """
        title_hash = _title_hash(title, prev_title)
        guild_cache = self._cache.get(guild_id, {})
        entry = guild_cache.get(title_hash)
        if entry and is_entry_fresh(entry):
            return entry
        return None

    def lookup_commercial(
        self,
        guild_id: int,
        title: str,
        prev_title: str = "",
    ) -> Optional[tuple]:
        """Look up a pre-generated commercial for a song transition.

        Returns (commercial_tts_path, commercial_text, commercial_sound_ids,
                 commercial_voice) if found, otherwise None.
        """
        entry = self.lookup(guild_id, title, prev_title)
        if (
            entry
            and entry.commercial_tts_path
            and os.path.isfile(entry.commercial_tts_path)
        ):
            return (
                entry.commercial_tts_path,
                entry.commercial_text,
                entry.commercial_sound_ids,
                entry.commercial_voice,
            )
        return None

    def lookup_hijack(
        self,
        guild_id: int,
        title: str,
        prev_title: str = "",
    ) -> Optional[tuple]:
        """Look up a pre-generated Station Wars hijack for a song transition.

        Returns (hijack_tts_path, hijack_text, hijack_sound_ids,
                 hijack_voice) if found, otherwise None.
        """
        entry = self.lookup(guild_id, title, prev_title)
        if entry and entry.hijack_tts_path and os.path.isfile(entry.hijack_tts_path):
            return (
                entry.hijack_tts_path,
                entry.hijack_text,
                entry.hijack_sound_ids,
                entry.hijack_voice,
            )
        return None

    def lookup_recovery(
        self,
        guild_id: int,
        title: str,
        prev_title: str = "",
    ) -> Optional[tuple]:
        """Look up a pre-generated DJ recovery line for after a hijack.

        Returns (recovery_tts_path, recovery_text) if found, otherwise None.
        """
        entry = self.lookup(guild_id, title, prev_title)
        if (
            entry
            and entry.recovery_tts_path
            and os.path.isfile(entry.recovery_tts_path)
        ):
            return (entry.recovery_tts_path, entry.recovery_text)
        return None

    def purge_guild(self, guild_id: int) -> int:
        """Remove in-memory cache for a guild. Files stay on disk."""
        count = 0
        guild_cache = self._cache.pop(guild_id, {})
        count = len(guild_cache)
        # Cancel any running task
        task = self._tasks.pop(guild_id, None)
        if task and not task.done():
            task.cancel()
        return count

    async def pregenerate_upcoming(
        self,
        guild_id: int,
        queue,  # asyncio.Queue of track objects
        current_title: str,
        max_ahead: int = MAX_PREGEN_AHEAD,
    ) -> None:
        """Pre-generate DJ lines + TTS for the next songs in the queue.

        Generates both main DJ lines and AI Side Host lines (when enabled).
        Files are saved permanently to assets/part2/ so they persist
        across bot restarts.
        """
        # Cancel any existing pregeneration task for this guild
        existing = self._tasks.get(guild_id)
        if existing and not existing.done():
            existing.cancel()

        async def _pregen():
            music = self._music_cog()
            if not music:
                return

            try:
                # Get the next N songs from the queue without consuming them
                upcoming = []
                if music and guild_id:
                    upcoming = music.peek_queue(guild_id, max_items=max_ahead)
                elif queue and hasattr(queue, "_queue"):
                    # Fallback: direct queue access (legacy path)
                    upcoming = list(queue._queue)[:max_ahead]

                if not upcoming:
                    logging.debug(
                        f"Pregen: No upcoming songs in queue for guild {guild_id}"
                    )
                    return

                dj_enabled = music.dj_enabled.get(guild_id, False)
                if not dj_enabled:
                    return

                from utils.dj import (
                    generate_outro,
                    generate_intro,
                    generate_tts,
                    extract_sound_tags,
                    TTS_AVAILABLE,
                )

                if not TTS_AVAILABLE:
                    return

                voice = music.dj_voice.get(guild_id, config.DJ_VOICE)

                for i, track in enumerate(upcoming):
                    title = getattr(track, "title", "Unknown")
                    # The "previous" title for the first song is the current song
                    # For subsequent songs, it's the previous song in the queue
                    prev_title = (
                        current_title
                        if i == 0
                        else (
                            getattr(upcoming[i - 1], "title", "Unknown")
                            if i > 0
                            else ""
                        )
                    )
                    title_hash = _title_hash(title, prev_title)

                    # Check if we already have a fresh entry on disk
                    existing_entry = self._cache.get(guild_id, {}).get(title_hash)
                    if existing_entry and is_entry_fresh(existing_entry):
                        logging.debug(
                            f"Pregen: #{i} '{title}' already cached for guild {guild_id}"
                        )
                        continue

                    # Check if a pregen file already exists on disk
                    pregen_path = _pregen_path(guild_id, i, title_hash)
                    ai_pregen_path = _ai_pregen_path(guild_id, i, title_hash)
                    if os.path.isfile(pregen_path):
                        # File exists! Just load it — no TTS generation needed
                        logging.info(
                            f"Pregen: #{i} '{title}' found on disk for guild {guild_id}"
                        )
                        # Generate the text line (fast, no TTS needed)
                        if prev_title:
                            dj_text = generate_outro(
                                prev_title,
                                has_next=True,
                                next_title=title,
                                queue_size=max(0, len(upcoming) - i),
                            )
                        else:
                            dj_text = generate_intro(
                                title,
                                queue_size=max(0, len(upcoming) - i),
                            )

                        clean_text, sound_ids = extract_sound_tags(dj_text)

                        # Check if AI host pregen also exists on disk
                        ai_text = ""
                        ai_tts_path = ""
                        if os.path.isfile(ai_pregen_path):
                            ai_text = clean_text  # Use DJ text as fallback
                            ai_tts_path = ai_pregen_path
                            # Try to load AI text from metadata
                            ai_meta_path = _ai_pregen_meta_path(guild_id, i, title_hash)
                            if os.path.isfile(ai_meta_path):
                                try:
                                    import json as _json

                                    with open(ai_meta_path, "r") as _f:
                                        _ai_data = _json.load(_f)
                                    ai_text = _ai_data.get("ai_text", ai_text)
                                except Exception:
                                    pass

                        entry = PregenEntry(
                            dj_text=clean_text,
                            dj_tts_path=pregen_path,
                            dj_sound_ids=sound_ids,
                            ai_text=ai_text,
                            ai_tts_path=ai_tts_path,
                            title=title,
                            prev_title=prev_title,
                            queue_index=i,
                            created_at=time.time(),
                            guild_id=guild_id,
                        )

                        # Also load commercial/hijack/recovery from disk if they exist
                        com_path = _commercial_pregen_path(guild_id, i, title_hash)
                        if os.path.isfile(com_path):
                            entry.commercial_tts_path = com_path
                            # Try to load commercial metadata
                            try:
                                import json as _json2

                                with open(
                                    _pregen_meta_path(guild_id, i, title_hash), "r"
                                ) as _mf:
                                    _meta = _json2.load(_mf)
                                entry.commercial_text = _meta.get("commercial_text", "")
                                entry.commercial_sound_ids = _meta.get(
                                    "commercial_sound_ids", []
                                )
                                entry.commercial_voice = _meta.get(
                                    "commercial_voice", ""
                                )
                            except Exception:
                                pass

                        hj_path = _hijack_pregen_path(guild_id, i, title_hash)
                        if os.path.isfile(hj_path):
                            entry.hijack_tts_path = hj_path
                            try:
                                import json as _json3

                                with open(
                                    _pregen_meta_path(guild_id, i, title_hash), "r"
                                ) as _mf2:
                                    _meta2 = _json3.load(_mf2)
                                entry.hijack_text = _meta2.get("hijack_text", "")
                                entry.hijack_sound_ids = _meta2.get(
                                    "hijack_sound_ids", []
                                )
                                entry.hijack_voice = _meta2.get("hijack_voice", "")
                            except Exception:
                                pass

                        rec_path = _recovery_pregen_path(guild_id, i, title_hash)
                        if os.path.isfile(rec_path):
                            entry.recovery_tts_path = rec_path
                            try:
                                import json as _json4

                                with open(
                                    _pregen_meta_path(guild_id, i, title_hash), "r"
                                ) as _mf3:
                                    _meta3 = _json4.load(_mf3)
                                entry.recovery_text = _meta3.get("recovery_text", "")
                            except Exception:
                                pass

                        if guild_id not in self._cache:
                            self._cache[guild_id] = {}
                        self._cache[guild_id][title_hash] = entry
                        # Save metadata to disk
                        self._save_entry_to_disk(entry)
                        logging.info(
                            f"Pregen: #{i} '{title}' loaded from disk "
                            f"for guild {guild_id}"
                        )
                        continue

                    # ── Generate DJ intro line + TTS audio ──
                    try:
                        if prev_title:
                            dj_text = generate_outro(
                                prev_title,
                                has_next=True,
                                next_title=title,
                                queue_size=max(0, len(upcoming) - i),
                            )
                        else:
                            dj_text = generate_intro(
                                title,
                                queue_size=max(0, len(upcoming) - i),
                            )

                        clean_text, sound_ids = extract_sound_tags(dj_text)

                        # Generate TTS using MOSS (main DJ voice)
                        tts_path = await generate_tts(
                            clean_text, voice=voice, source="Pregen-DJ"
                        )

                        if not tts_path:
                            logging.warning(
                                f"Pregen: TTS generation failed for #{i} '{title}'"
                            )
                            continue

                        # Move the TTS file from /tmp to assets/part2/ permanently
                        # so it persists across restarts
                        permanent_path = pregen_path
                        if tts_path != permanent_path:
                            try:
                                import shutil

                                shutil.copy2(tts_path, permanent_path)
                                logging.info(f"Pregen: Copied TTS to {permanent_path}")
                                # Clean up the /tmp original
                                from utils.dj import cleanup_tts_file

                                cleanup_tts_file(tts_path)
                            except Exception as e:
                                logging.warning(
                                    f"Pregen: Could not copy to permanent path: {e}. "
                                    f"Using temp file {tts_path}"
                                )
                                permanent_path = tts_path
                        else:
                            permanent_path = tts_path

                        entry = PregenEntry(
                            dj_text=clean_text,
                            dj_tts_path=permanent_path,
                            dj_sound_ids=sound_ids,
                            title=title,
                            prev_title=prev_title,
                            queue_index=i,
                            created_at=time.time(),
                            guild_id=guild_id,
                        )

                        # ── Pregenerate AI Side Host line (if enabled) ──
                        # The AI host uses the LLM to generate commentary about
                        # the song/transition, then synthesizes TTS with a
                        # different voice from the main DJ.
                        ai_text = ""
                        ai_tts_path = ""
                        try:
                            from utils.llm_dj import (
                                generate_side_host_line,
                                OLLAMA_DJ_AVAILABLE,
                            )

                            ai_enabled = music.ai_dj_enabled.get(guild_id, False)
                            if ai_enabled and OLLAMA_DJ_AVAILABLE:
                                ai_line = await generate_side_host_line(
                                    guild_id,
                                    dj_line=clean_text,
                                    song_title=title,
                                    prev_song=prev_title or current_title or "",
                                )
                                if ai_line:
                                    ai_clean, _ = extract_sound_tags(ai_line)
                                    ai_voice = music.ai_dj_voice.get(
                                        guild_id, config.OLLAMA_DJ_VOICE
                                    )
                                    ai_tts_result = await generate_tts(
                                        ai_clean,
                                        voice=ai_voice,
                                        source="Pregen-AI",
                                    )
                                    if ai_tts_result:
                                        # Move AI TTS to permanent path
                                        ai_perm = _ai_pregen_path(
                                            guild_id, i, title_hash
                                        )
                                        try:
                                            import shutil

                                            shutil.copy2(ai_tts_result, ai_perm)
                                            from utils.dj import cleanup_tts_file

                                            cleanup_tts_file(ai_tts_result)
                                            ai_tts_path = ai_perm
                                        except Exception:
                                            ai_tts_path = ai_tts_result
                                        ai_text = ai_clean
                                        entry.ai_text = ai_text
                                        entry.ai_tts_path = ai_tts_path
                                        logging.info(
                                            f"Pregen: #{i} AI host line cached for "
                                            f"'{title}' in guild {guild_id}"
                                        )
                        except Exception as ai_e:
                            logging.debug(
                                f"Pregen: AI side host skipped for #{i} '{title}': {ai_e}"
                            )

                        # ── Pregenerate Commercial Break (if enabled & likely) ──
                        # While a song plays, pre-generate a commercial ad
                        # so there's zero gap between song end → commercial → DJ intro.
                        # Each commercial gets a random ANNOUNCER voice from
                        # COMMERCIAL_VOICES, different from the main DJ voice.
                        try:
                            from utils.commercials import (
                                is_commercial_enabled as _com_enabled,
                                should_play_commercial as _should_com,
                                generate_commercial as _gen_com,
                                get_commercial_voice as _get_com_voice,
                                generate_hijack as _gen_hijack,
                                get_hijack_voice as _get_hijack_voice,
                                get_recovery_line as _get_recovery,
                                should_play_hijack as _should_hijack,
                            )

                            if _com_enabled(guild_id):
                                queue_size = len(upcoming) if upcoming else 0

                                # ── Commercial pre-generation ──
                                # Pre-gen a commercial even if it might not play —
                                # the 0.2s TTS generation cost is worth eliminating
                                # the 1-3s gap for the listener.
                                com_path = _commercial_pregen_path(
                                    guild_id, i, title_hash
                                )
                                if not os.path.isfile(com_path):
                                    com_text = await _gen_com(
                                        station_name=getattr(
                                            config, "STATION_NAME", "MBot"
                                        ),
                                        song_title=title,
                                        prev_title=prev_title,
                                        queue_size=queue_size,
                                        listener_count=0,
                                    )
                                    if com_text:
                                        com_clean, com_sounds = extract_sound_tags(
                                            com_text
                                        )
                                        com_voice = _get_com_voice(guild_id) or voice
                                        com_tts = await generate_tts(
                                            com_clean,
                                            voice=com_voice,
                                            source="Pregen-Commercial",
                                        )
                                        if com_tts:
                                            try:
                                                import shutil

                                                shutil.copy2(com_tts, com_path)
                                                from utils.dj import cleanup_tts_file

                                                cleanup_tts_file(com_tts)
                                            except Exception:
                                                com_path = com_tts
                                            entry.commercial_text = com_clean
                                            entry.commercial_tts_path = com_path
                                            entry.commercial_sound_ids = com_sounds
                                            entry.commercial_voice = com_voice
                                            logging.info(
                                                f"Pregen: #{i} Commercial cached for "
                                                f"'{title}' in guild {guild_id} "
                                                f"(voice={com_voice})"
                                            )

                                # ── Hijack pre-generation ──
                                # Pre-gen a Station Wars transmission so it's
                                # ready if the 5% roll hits at play time.
                                hj_path = _hijack_pregen_path(guild_id, i, title_hash)
                                if not os.path.isfile(hj_path):
                                    hj_text = await _gen_hijack(
                                        station_name=getattr(
                                            config, "STATION_NAME", "MBot"
                                        ),
                                        dj_name=getattr(config, "DJ_NAME", "Nova"),
                                    )
                                    if hj_text:
                                        hj_clean, hj_sounds = extract_sound_tags(
                                            hj_text
                                        )
                                        hj_voice = _get_hijack_voice(guild_id) or voice
                                        hj_tts = await generate_tts(
                                            hj_clean,
                                            voice=hj_voice,
                                            source="Pregen-Hijack",
                                        )
                                        if hj_tts:
                                            try:
                                                import shutil

                                                shutil.copy2(hj_tts, hj_path)
                                                from utils.dj import cleanup_tts_file

                                                cleanup_tts_file(hj_tts)
                                            except Exception:
                                                hj_path = hj_tts
                                            entry.hijack_text = hj_clean
                                            entry.hijack_tts_path = hj_path
                                            entry.hijack_sound_ids = hj_sounds
                                            entry.hijack_voice = hj_voice
                                            logging.info(
                                                f"Pregen: #{i} Station Wars hijack cached for "
                                                f"'{title}' in guild {guild_id} "
                                                f"(voice={hj_voice})"
                                            )

                                # ── Recovery line pre-generation ──
                                # Pre-gen the DJ's comeback line after a hijack.
                                # Uses the main DJ voice (not a commercial voice).
                                rec_path = _recovery_pregen_path(
                                    guild_id, i, title_hash
                                )
                                if not os.path.isfile(rec_path):
                                    rec_text = _get_recovery()
                                    if rec_text:
                                        rec_clean, _ = extract_sound_tags(rec_text)
                                        rec_tts = await generate_tts(
                                            rec_clean,
                                            voice=voice,  # DJ's own voice
                                            source="Pregen-Recovery",
                                        )
                                        if rec_tts:
                                            try:
                                                import shutil

                                                shutil.copy2(rec_tts, rec_path)
                                                from utils.dj import cleanup_tts_file

                                                cleanup_tts_file(rec_tts)
                                            except Exception:
                                                rec_path = rec_tts
                                            entry.recovery_text = rec_clean
                                            entry.recovery_tts_path = rec_path
                                            logging.info(
                                                f"Pregen: #{i} Recovery line cached for "
                                                f"'{title}' in guild {guild_id}"
                                            )
                        except ImportError:
                            logging.debug(
                                f"Pregen: Commercials module not available for #{i}"
                            )
                        except Exception as com_e:
                            logging.debug(
                                f"Pregen: Commercial/hijack pre-gen skipped for #{i}: {com_e}"
                            )

                        # Store in memory cache
                        if guild_id not in self._cache:
                            self._cache[guild_id] = {}
                        self._cache[guild_id][title_hash] = entry

                        # Save metadata to disk
                        self._save_entry_to_disk(entry)

                        logging.info(
                            f"Pregen: #{i} Cached DJ line for '{title}' "
                            f"in guild {guild_id} "
                            f"(sounds: {sound_ids})"
                            f"{' + AI host' if ai_text else ''}"
                            f"{' + commercial' if entry.commercial_tts_path else ''}"
                            f"{' + hijack' if entry.hijack_tts_path else ''}"
                            f"{' + recovery' if entry.recovery_tts_path else ''}"
                        )

                    except Exception as e:
                        logging.warning(
                            f"Pregen: Failed for #{i} '{title}' "
                            f"in guild {guild_id}: {e}"
                        )
                        continue

                    # Small delay between generations to avoid
                    # hammering the TTS server
                    await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                logging.debug(f"Pregen: Cancelled for guild {guild_id}")
            except Exception as e:
                logging.error(f"Pregen: Unexpected error for guild {guild_id}: {e}")

        # Fire and forget — runs in the background
        self._tasks[guild_id] = asyncio.create_task(_pregen())


# ── Global instance ─────────────────────────────────────────────────────
_pregenerator: Optional[DjPregenerator] = None


def get_pregenerator(bot) -> DjPregenerator:
    """Get or create the global DJ pregenerator instance."""
    global _pregenerator
    if _pregenerator is None:
        _pregenerator = DjPregenerator(bot)
    return _pregenerator
