"""
utils/presets.py — Save/Load playlist presets for MBot.

Saves the current queue as a named preset (JSON file) and loads them back.
Presets are stored in the presets/ directory as JSON files.
"""

import json
import os
import logging
from pathlib import Path

PRESETS_DIR = "presets"


def _ensure_dir():
    if not os.path.isdir(PRESETS_DIR):
        os.makedirs(PRESETS_DIR, exist_ok=True)


def list_presets() -> list[dict]:
    """Return all saved presets. Each: {"name": str, "count": int, "created": str}"""
    _ensure_dir()
    presets = []
    for fname in sorted(os.listdir(PRESETS_DIR)):
        if fname.endswith(".json"):
            path = os.path.join(PRESETS_DIR, fname)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                presets.append(
                    {
                        "name": data.get("name", os.path.splitext(fname)[0]),
                        "count": len(data.get("tracks", [])),
                        "created": data.get("created", "unknown"),
                    }
                )
            except Exception:
                pass
    return presets


def save_preset(name: str, tracks: list[dict]) -> bool:
    """Save a preset. tracks = [{"title": str, "url": str, "webpage_url": str, ...}]"""
    _ensure_dir()
    if not name or not tracks:
        return False
    # Sanitize name for filename
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in name)
    path = os.path.join(PRESETS_DIR, f"{safe_name}.json")
    from datetime import datetime

    data = {
        "name": name,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "tracks": tracks,
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logging.info(f"Presets: Saved preset '{name}' ({len(tracks)} tracks)")
        return True
    except Exception as e:
        logging.error(f"Presets: Failed to save preset '{name}': {e}")
        return False


def load_preset(name: str) -> list[dict] | None:
    """Load a preset by name. Returns list of track dicts, or None."""
    _ensure_dir()
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in name)
    path = os.path.join(PRESETS_DIR, f"{safe_name}.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("tracks", [])
    except Exception as e:
        logging.error(f"Presets: Failed to load preset '{name}': {e}")
        return None


def delete_preset(name: str) -> bool:
    """Delete a preset by name."""
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in name)
    path = os.path.join(PRESETS_DIR, f"{safe_name}.json")
    if os.path.isfile(path):
        try:
            os.remove(path)
            return True
        except Exception:
            return False
    return False


def queue_to_tracks(queue, music_cog=None, guild_id=None) -> list[dict]:
    """Convert a queue of songs to a serializable list of dicts.

    Accepts either an asyncio.Queue directly or uses the music cog's
    peek_queue() method for safe access without private attribute access.
    """
    if music_cog and guild_id is not None:
        items = music_cog.peek_queue(guild_id)
    else:
        # Fallback: direct queue access (legacy path)
        items = list(queue._queue)
    tracks = []
    for item in items:
        track = {
            "title": getattr(item, "title", "Unknown"),
            "url": getattr(item, "url", None),
            "webpage_url": getattr(item, "webpage_url", None),
            "duration": getattr(item, "duration", None),
            "thumbnail": getattr(item, "thumbnail", None),
        }
        # For PlaceholderTracks, use webpage_url as the key to play it back
        if not track["url"] and track["webpage_url"]:
            track["url"] = track["webpage_url"]
        tracks.append(track)
    return tracks
