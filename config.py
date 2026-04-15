# config.py

# It is recommended to use environment variables for sensitive data.
# However, you can hardcode the values here for simplicity.
import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed; falling back to system environment variables

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")

# You can change the bot's command prefix here
COMMAND_PREFIX = "?"

# Radio DJ mode station name
STATION_NAME = os.environ.get("STATION_NAME", "MBot")

# Emojis for UI
PLAY_EMOJI = "▶️"
PAUSE_EMOJI = "⏸️"
SKIP_EMOJI = "⏭️"
QUEUE_EMOJI = "🎵"
ERROR_EMOJI = "❌"
SUCCESS_EMOJI = "✅"

# Discord Channel ID for sending bot logs (errors, warnings)
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", 0) or 0) or None

# DJ Mode — Default voice for the radio DJ (Microsoft Edge TTS voice name)
# Change this if you want a different default voice.
# Use ?djvoices in Discord to see available voices.
# When using a local TTS server (TTS_MODE=local), this should be a VibeVoice
# voice name like "en-Carter_man" instead of an Edge TTS voice.
DJ_VOICE = os.environ.get("DJ_VOICE", "en-US-AriaNeural")

# ── TTS Engine ──────────────────────────────────────────────────────────
# The bot supports two TTS engines:
#
# 1. "edge-tts" (default) — Uses Microsoft Edge TTS voices via the edge-tts
#    Python package. Free, no server needed, 100+ voices in 40+ languages.
#    Requires: pip install edge-tts
#    Voice names like: en-US-AriaNeural, en-US-GuyNeural, en-GB-SoniaNeural
#
# 2. "local" — Uses a locally-hosted VibeVoice-Realtime TTS server.
#    Lower latency (~300ms first audio), runs on your GPU/CPU, no Microsoft API.
#    Requires: VibeVoice server running (see https://github.com/microsoft/VibeVoice)
#    Voice names like: en-Carter_man, en-Journalist_woman, de-Anna_woman
#    The bot connects via WebSocket to stream audio in real-time.
TTS_MODE = os.environ.get("TTS_MODE", "edge-tts").lower()

# Local TTS server URL (only used when TTS_MODE=local).
# This is the VibeVoice-Realtime WebSocket server address.
# Start it with: python demo/vibevoice_realtime_demo.py --model_path microsoft/VibeVoice-Realtime-0.5B
LOCAL_TTS_URL = os.environ.get("LOCAL_TTS_URL", "http://localhost:3000")

# Emojis for DJ mode
DJ_EMOJI = "🎙️"

# Crossfade — overlap duration in seconds when transitioning between songs
# Set to 0 to disable crossfade. Recommended: 3-5 seconds.
CROSSFADE_DURATION = 3

# Auto-DJ — default source when the queue empties (optional)
# Can be a YouTube playlist URL, "preset:Name", or "" (uses recently-played history)
AUTODJ_DEFAULT_SOURCE = os.environ.get("AUTODJ_SOURCE", "")

# DJ Bed Music — ambient loop played under the DJ's voice between songs
# The bot looks for sounds/bed_music.wav or sounds/bed_music.mp3
# Set to True to enable (if a bed music file exists).
DJ_BED_MUSIC_ENABLED = True

# Maximum duration for soundboard/DJ sound effects (seconds).
# Sounds longer than this are truncated via FFmpeg to prevent blocking
# the next song. Discord.py raises "already playing" if a long sound
# overlaps with song playback.
# 8 seconds is the soft cap (normal sounds), 10 is the hard cap (a
# few longer effects are acceptable).
MAX_SOUND_SECONDS = 8

# ── AI Side Host (Ollama) ──────────────────────────────────────────────
# The AI side host is a second radio personality powered by a local LLM.
# It writes its own original banter, hot takes, shoutouts, and commentary
# — like a co-host who chimes in alongside the main template DJ.
# Requires Ollama running locally with a pulled model (e.g., `ollama pull gemma4:latest`).

# Enable the AI side host. Set to "true" in .env to activate.
OLLAMA_DJ_ENABLED = os.environ.get("OLLAMA_DJ_ENABLED", "false").lower() == "true"

# Ollama server URL (default: http://localhost:11434)
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# Ollama model to use for side host lines (default: gemma4:latest)
# Make sure you've pulled the model: ollama pull <model>
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:latest")

# How often the side host chimes in (0.0–1.0).
# 0.25 = ~25% chance after each template DJ line.
# 0.5 = ~50% chance. 1.0 = always speaks (alongside the main DJ).
OLLAMA_DJ_CHANCE = float(os.environ.get("OLLAMA_DJ_CHANCE", "0.25"))

# TTS voice for the AI side host (separate from the main DJ voice).
# This makes the two hosts sound like different people.
# Use ?djvoices in Discord to see available voices.
OLLAMA_DJ_VOICE = os.environ.get("OLLAMA_DJ_VOICE", "en-US-GuyNeural")

# Timeout in seconds for Ollama API calls. If the LLM doesn't respond
# in this time, the side host is skipped (no dead air).
OLLAMA_DJ_TIMEOUT = int(os.environ.get("OLLAMA_DJ_TIMEOUT", "4"))

# Web Dashboard
WEB_HOST = os.environ.get("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("WEB_PORT", 8080))

WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "")

# Now-Playing Channel — Discord channel ID where the bot sends its
# now-playing embed. If set, all now-playing messages go to this one
# channel regardless of which channel the user typed the command in.
# If blank or 0, the bot sends the message in the same channel as the command.
# Set this to a channel ID like 1234567890123456789 to bind it.
NOWPLAYING_CHANNEL_ID = int(os.environ.get("NOWPLAYING_CHANNEL_ID", 0) or 0)
