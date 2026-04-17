"""
utils/pregen.py — Pre-Generate DJ Lines & TTS for Queue

While a long song plays, this module pre-generates the main DJ intro/outro
TTS audio files for upcoming songs in the queue. Files are saved to
assets/part2/ with numbered filenames and are NEVER deleted — they persist
across restarts so the DJ always has lines ready.

Only the main DJ (MOSS-TTS) lines are pre-generated.
The AI side host TTS is NOT pre-generated — it stays live/on-demand.

File naming convention:
    dj_{guild_id}_{index}_{title_hash}.wav

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
    """

    dj_text: str = ""  # The DJ line text (clean, no {sound:} tags)
    dj_tts_path: str = ""  # Path to the TTS audio file (permanent)
    dj_sound_ids: list = field(default_factory=list)  # {sound:name} tags extracted
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

        ONLY generates main DJ (MOSS-TTS) lines — the AI side host
        stays live/on-demand. Files are saved permanently to assets/part2/.
        Each file is numbered by queue position so the DJ always has
        the right line ready for the right song.
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
                if queue and hasattr(queue, "_queue"):
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
                    generate_song_intro,
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

                        entry = PregenEntry(
                            dj_text=clean_text,
                            dj_tts_path=pregen_path,
                            dj_sound_ids=sound_ids,
                            title=title,
                            prev_title=prev_title,
                            queue_index=i,
                            created_at=time.time(),
                            guild_id=guild_id,
                        )

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
