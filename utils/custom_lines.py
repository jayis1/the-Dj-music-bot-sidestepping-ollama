"""
Custom DJ Lines — persistent JSON storage for user-added lines.

Lines are stored in dj_custom_lines.json in the bot's working directory.
Each category is a list of format strings using {title}, {prev_title},
{next_title}, and {greeting} placeholders.
"""

import json
import logging
from pathlib import Path

CUSTOM_LINES_FILE = "dj_custom_lines.json"

# These are the categories the web dashboard can manage
LINE_CATEGORIES = [
    "intros",
    "hype_intros",
    "hype_intros_loud",
    "outros",
    "transitions",
    "transitions_hype",
    "transitions_mellow",
    "outros_final",
    "station_ids",
    "callouts",
]

# Friendly labels for the dashboard
CATEGORY_LABELS = {
    "intros": "Session Intros",
    "hype_intros": "Song Intros",
    "hype_intros_loud": "Hype Intros (Loud)",
    "outros": "Outros",
    "transitions": "Transitions",
    "transitions_hype": "Hype Transitions",
    "transitions_mellow": "Mellow Transitions",
    "outros_final": "Final Outros (queue empty)",
    "station_ids": "Station IDs",
    "callouts": "Listener Callouts",
}

# Which placeholders each category supports
CATEGORY_PLACEHOLDERS = {
    "intros": ["{greeting}", "{title}", "{sound:name}"],
    "hype_intros": ["{title}", "{sound:name}"],
    "hype_intros_loud": ["{title}", "{sound:name}"],
    "outros": ["{title}", "{sound:name}"],
    "transitions": ["{prev_title}", "{next_title}", "{sound:name}"],
    "transitions_hype": ["{prev_title}", "{next_title}", "{sound:name}"],
    "transitions_mellow": ["{prev_title}", "{next_title}", "{sound:name}"],
    "outros_final": ["{title}", "{sound:name}"],
    "station_ids": ["{sound:name}"],
    "callouts": ["{sound:name}"],
}


def load_custom_lines() -> dict:
    """Load custom lines from JSON file. Returns {category: [str, ...]}."""
    path = Path(CUSTOM_LINES_FILE)
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if k in LINE_CATEGORIES}
    except Exception as e:
        logging.error(f"DJ: Failed to load custom lines: {e}")
        return {}


def save_custom_lines(lines: dict) -> None:
    """Save custom lines to JSON file."""
    try:
        with open(CUSTOM_LINES_FILE, "w", encoding="utf-8") as f:
            json.dump(lines, f, indent=2, ensure_ascii=False)
        logging.info(f"DJ: Saved {sum(len(v) for v in lines.values())} custom lines")
    except Exception as e:
        logging.error(f"DJ: Failed to save custom lines: {e}")


def add_line(category: str, line: str) -> bool:
    """Add a custom line to a category. Returns True if successful."""
    if category not in LINE_CATEGORIES:
        return False
    lines = load_custom_lines()
    if category not in lines:
        lines[category] = []
    lines[category].append(line)
    save_custom_lines(lines)
    return True


def remove_line(category: str, index: int) -> bool:
    """Remove a custom line by index. Returns True if successful."""
    if category not in LINE_CATEGORIES:
        return False
    lines = load_custom_lines()
    if category not in lines or index < 0 or index >= len(lines[category]):
        return False
    lines[category].pop(index)
    if not lines[category]:
        del lines[category]
    save_custom_lines(lines)
    return True
