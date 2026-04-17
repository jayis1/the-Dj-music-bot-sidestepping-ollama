"""
utils/pregen.py — Pre-Generate DJ Lines & TTS Cache

While a long song plays, this module pre-generates DJ intro/outro lines
and TTS audio files for upcoming songs in the queue. When the current song
ends, the DJ line is already ready — zero latency between-song transitions.

Cache files are stored in assets/part2/:
  - DJ intros:   {guild_id}_{song_title_hash}_dj.wav
  - AI host:     {guild_id}_{song_title_hash}_ai.wav
  - SFX markers: {guild_id}_{song_title_hash}_sfx.json

Each cache entry has a TTL (default 30 min) so stale lines get refreshed.
"""

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import config

# ── Directory for pre-generated assets ──────────────────────────────────
PREGEN_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "part2")

# Cache TTL — how long a pre-generated line is considered fresh (seconds)
DEFAULT_TTL = 1800  # 30 minutes

# Maximum number of songs to pre-generate per guild
MAX_PREGEN_AHEAD = 5


@dataclass
class PregenEntry:
    """A pre-generated DJ line with its TTS audio file and metadata."""

    dj_text: str = ""  # The DJ line text (clean, no {sound:} tags)
    dj_tts_path: str = ""  # Path to the TTS audio file
    dj_sound_ids: list = field(default_factory=list)  # {sound:name} tags extracted
    ai_text: str = ""  # AI side host line text
    ai_tts_path: str = ""  # Path to AI TTS audio file
    title: str = ""  # Song title this entry is for
    prev_title: str = ""  # Previous song title (for transitions)
    created_at: float = 0.0  # Timestamp when this was generated
    guild_id: int = 0  # Guild this was generated for


def _title_hash(title: str, prev_title: str = "") -> str:
    """Create a short hash for a song title + prev title combination."""
    combo = f"{prev_title}|||{title}"
    return hashlib.sha256(combo.encode("utf-8")).hexdigest()[:12]


def _pregen_path(guild_id: int, title_hash: str, suffix: str) -> str:
    """Build a file path in the pregen directory."""
    return os.path.join(PREGEN_DIR, f"{guild_id}_{title_hash}{suffix}")


def ensure_pregen_dir() -> None:
    """Create the pregen directory if it doesn't exist."""
    os.makedirs(PREGEN_DIR, exist_ok=True)


def is_entry_fresh(entry: PregenEntry, ttl: float = DEFAULT_TTL) -> bool:
    """Check if a pre-generated entry is still within its TTL."""
    if not entry.created_at:
        return False
    # Also check that the TTS files still exist on disk
    if entry.dj_tts_path and not os.path.isfile(entry.dj_tts_path):
        return False
    if entry.ai_tts_path and not os.path.isfile(entry.ai_tts_path):
        return False
    return (time.time() - entry.created_at) < ttl


class DjPregenerator:
    """Pre-generates DJ lines and TTS audio for upcoming songs.

    Usage:
        pregen = DjPregenerator(bot)
        # Start pregenerating when a long song is playing
        await pregen.pregenerate_upcoming(guild_id, queue, current_song_title)
        # When the song ends, get the cached entry instantly
        entry = pregen.get(guild_id, song_title, prev_title)
        if entry and entry.dj_tts_path:
            # Use the pre-generated TTS — no TTS latency!
            play_tts(entry.dj_tts_path, entry.dj_text)
    """

    def __init__(self, bot):
        self.bot = bot
        # guild_id -> {title_hash: PregenEntry}
        self._cache: dict[int, dict[str, PregenEntry]] = {}
        # guild_id -> currently running pregeneration task
        self._tasks: dict[int, asyncio.Task] = {}
        ensure_pregen_dir()

    def _music_cog(self):
        """Get the Music cog instance."""
        return self.bot.get_cog("Music")

    def get(
        self,
        guild_id: int,
        title: str,
        prev_title: str = "",
    ) -> Optional[PregenEntry]:
        """Look up a pre-generated entry for a song transition.

        Returns the cached entry if found and fresh, otherwise None.
        """
        title_hash = _title_hash(title, prev_title)
        guild_cache = self._cache.get(guild_id, {})
        entry = guild_cache.get(title_hash)
        if entry and is_entry_fresh(entry):
            return entry
        return None

    def consume(
        self,
        guild_id: int,
        title: str,
        prev_title: str = "",
    ) -> Optional[PregenEntry]:
        """Look up and REMOVE a pre-generated entry (one-time use).

        Returns the cached entry if found and fresh, otherwise None.
        The entry is removed from the cache so TTS files can be cleaned up
        after playback.
        """
        entry = self.get(guild_id, title, prev_title)
        if entry:
            title_hash = _title_hash(title, prev_title)
            guild_cache = self._cache.get(guild_id, {})
            guild_cache.pop(title_hash, None)
        return entry

    def purge_guild(self, guild_id: int) -> int:
        """Remove all cached entries for a guild. Returns count purged."""
        guild_cache = self._cache.pop(guild_id, {})
        count = 0
        for entry in guild_cache.values():
            if entry.dj_tts_path and os.path.isfile(entry.dj_tts_path):
                try:
                    os.remove(entry.dj_tts_path)
                except OSError:
                    pass
                count += 1
            if entry.ai_tts_path and os.path.isfile(entry.ai_tts_path):
                try:
                    os.remove(entry.ai_tts_path)
                except OSError:
                    pass
        # Also cancel any running task
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

        Runs as a background task. Each song gets:
          1. A DJ intro/outro line generated from templates
          2. The TTS audio file pre-rendered
          3. An AI side host line (if enabled and Ollama available)

        Pre-generated entries are stored in self._cache for instant
        retrieval when play_next() needs them.
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
                    # Peek at the queue without removing items
                    upcoming = list(queue._queue)[:max_ahead]

                if not upcoming:
                    logging.debug(
                        f"Pregen: No upcoming songs in queue for guild {guild_id}"
                    )
                    return

                dj_enabled = music.dj_enabled.get(guild_id, False)
                ai_enabled = music.ai_dj_enabled.get(guild_id, False)

                if not dj_enabled:
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

                    # Check if we already have a fresh entry
                    existing_entry = self._cache.get(guild_id, {}).get(title_hash)
                    if existing_entry and is_entry_fresh(existing_entry):
                        logging.debug(
                            f"Pregen: Cached entry still fresh for "
                            f"'{title}' in guild {guild_id}, skipping"
                        )
                        continue

                    # ── Generate DJ intro line ──
                    try:
                        from utils.dj import (
                            generate_outro,
                            generate_song_intro,
                            generate_intro,
                            generate_tts,
                            extract_sound_tags,
                            TTS_AVAILABLE,
                        )

                        if not TTS_AVAILABLE:
                            continue

                        # Generate the text line
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

                        # Generate TTS audio
                        dj_tts_path = await generate_tts(
                            clean_text, voice=voice, source="Pregen-DJ"
                        )

                        entry = PregenEntry(
                            dj_text=clean_text,
                            dj_tts_path=dj_tts_path or "",
                            dj_sound_ids=sound_ids,
                            title=title,
                            prev_title=prev_title,
                            created_at=time.time(),
                            guild_id=guild_id,
                        )

                        # ── AI Side Host line (if enabled) ──
                        if ai_enabled:
                            try:
                                from utils.llm_dj import (
                                    generate_side_host_line,
                                    should_side_host_speak,
                                    OLLAMA_DJ_AVAILABLE,
                                )

                                if OLLAMA_DJ_AVAILABLE and should_side_host_speak():
                                    ai_line = await generate_side_host_line(
                                        title=title,
                                        prev_title=prev_title,
                                        next_title=(
                                            getattr(upcoming[i + 1], "title", "")
                                            if i + 1 < len(upcoming)
                                            else ""
                                        ),
                                        queue_size=max(0, len(upcoming) - i),
                                        listener_count=0,
                                        station_name=(
                                            self.bot.user.name
                                            if self.bot.user
                                            else config.STATION_NAME
                                        ),
                                        dj_line=clean_text,
                                    )
                                    if ai_line:
                                        # Use edge-tts for the AI host
                                        # (distinct voice from the main DJ)
                                        AI_EDGE_VOICE = "en-US-GuyNeural"
                                        ai_text, ai_sfx = extract_sound_tags(ai_line)
                                        ai_tts_path = await generate_tts(
                                            ai_text[:200],
                                            voice=AI_EDGE_VOICE,
                                            source="Pregen-AI",
                                            engine="edge-tts",
                                        )
                                        entry.ai_text = ai_text
                                        entry.ai_tts_path = ai_tts_path or ""

                            except Exception as e:
                                logging.warning(
                                    f"Pregen: AI side host failed for '{title}': {e}"
                                )

                        # Store in cache
                        if guild_id not in self._cache:
                            self._cache[guild_id] = {}
                        self._cache[guild_id][title_hash] = entry

                        logging.info(
                            f"Pregen: Cached DJ line for '{title}' "
                            f"in guild {guild_id} "
                            f"(AI: {'yes' if entry.ai_tts_path else 'no'})"
                        )

                    except Exception as e:
                        logging.warning(
                            f"Pregen: Failed for '{title}' in guild {guild_id}: {e}"
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
