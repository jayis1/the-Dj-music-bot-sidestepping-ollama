"""
utils/soundboard.py — Soundboard / DJ Drops for MBot.

Scans the `sounds/` directory for .mp3 files and makes them available
as soundboard buttons in the web dashboard. Clicking a button plays
the sound effect over the currently playing music in a guild.
"""

import os

SOUNDS_DIR = "sounds"


def list_sounds() -> list[dict]:
    """Return a list of available soundboard entries.
    Each entry: {"id": filename, "name": display_name, "file": path}
    """
    sounds = []
    if not os.path.isdir(SOUNDS_DIR):
        return sounds
    for fname in sorted(os.listdir(SOUNDS_DIR)):
        if fname.lower().endswith((".mp3", ".wav", ".ogg", ".flac")):
            name = os.path.splitext(fname)[0]
            # Turn underscores/dashes into spaces, title-case
            name = name.replace("_", " ").replace("-", " ").strip()
            name = name.title() if name else fname
            sounds.append(
                {
                    "id": fname,
                    "name": name,
                    "file": os.path.join(SOUNDS_DIR, fname),
                }
            )
    return sounds


def get_sound_path(sound_id: str) -> str | None:
    """Return the file path for a sound ID, or None if not found."""
    # Prevent directory traversal
    basename = os.path.basename(sound_id)
    path = os.path.join(SOUNDS_DIR, basename)
    if os.path.isfile(path):
        return path
    return None


def create_default_sounds():
    """Create a README in the sounds/ dir so users know where to put files."""
    readme = os.path.join(SOUNDS_DIR, "README.txt")
    if not os.path.exists(readme):
        with open(readme, "w") as f:
            f.write(
                "Drop your .mp3 sound effects here!\n\n"
                "The filename (without extension) becomes the button label.\n"
                "Examples: airhorn.mp3, record_scratch.mp3, applause.mp3\n\n"
                "Find free sound effects at:\n"
                "  https://freesound.org\n"
                "  https://pixabay.com/sound-effects\n"
            )
