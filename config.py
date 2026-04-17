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

# DJ Mode — Default voice for the radio DJ.
# Change this if you want a different default voice.
# Use ?djvoices in Discord to see available voices.
# The voice name format depends on the active TTS engine:
#   MOSS:       name of a .wav file in assets/moss_voices/ (without extension)
#   VibeVoice:  en-Carter_man, en-Journalist_woman, etc.
#   Edge TTS:   en-US-AriaNeural, en-US-GuyNeural, etc.
DJ_VOICE = os.environ.get("DJ_VOICE", "en_warm_female")

# ── TTS Engine ──────────────────────────────────────────────────────────
# The bot supports three TTS engines:
#
# 1. "moss" (default) — MOSS-TTS-Nano via its FastAPI server.
#    Local, open-weight, 0.1B params, runs on CPU or GPU. Voice clone via
#    prompt audio files. 48 kHz stereo output, multilingual (20 languages).
#    See: https://github.com/OpenMOSS/MOSS-TTS-Nano
#    Start with: docker compose up -d  (includes MOSS-TTS-Nano server)
#    Voices are .wav prompt audio files in assets/moss_voices/
#
# 2. "vibevoice" — Uses a separately-hosted VibeVoice-Realtime WebSocket server.
#    Lower latency (~300ms), runs on GPU/CPU in a separate process.
#    Requires: VibeVoice server running (see https://github.com/microsoft/VibeVoice)
#    Voice names like: en-Carter_man, en-Journalist_woman, de-Anna_woman
#    (The legacy alias "local" also works and maps to vibevoice.)
#
# 3. "edge-tts" — Microsoft Edge TTS voices (cloud-based).
#    Free, no server needed, 100+ voices in 40+ languages, but higher latency
#    and depends on Microsoft's cloud API. Used as automatic fallback.
#    Requires: pip install edge-tts
#    Voice names like: en-US-AriaNeural, en-US-GuyNeural, en-GB-SoniaNeural
#
# Fallback chain: moss → edge-tts  (or vibevoice → edge-tts)
# If the primary engine fails, the bot automatically falls back to edge-tts.
TTS_MODE = os.environ.get("TTS_MODE", "moss").lower()

# MOSS-TTS-Nano server URL (only used when TTS_MODE=moss).
# Start with: moss-tts-nano serve --port 18083
# Or use docker-compose which starts the MOSS server automatically.
MOSS_TTS_URL = os.environ.get("MOSS_TTS_URL", "http://localhost:18083")

# Default MOSS-TTS-Nano voice — name of a prompt audio file in assets/moss_voices/
# (without the .wav extension). The bot ships with: en_warm_female, en_news_male
# Add your own .wav files to assets/moss_voices/ to create new voices.
MOSS_VOICE = os.environ.get("MOSS_VOICE", "en_warm_female")

# VibeVoice server URL (only used when TTS_MODE=vibevoice).
# This is the VibeVoice-Realtime WebSocket server address.
# Start it with: python demo/vibevoice_realtime_demo.py --model_path microsoft/VibeVoice-Realtime-0.5B
VIBEVOICE_TTS_URL = os.environ.get("VIBEVOICE_TTS_URL", "http://localhost:3000")

# Backward-compatible alias — LOCAL_TTS_URL still works for older .env files
LOCAL_TTS_URL = os.environ.get("LOCAL_TTS_URL", "")

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
# The bot automatically creates a custom model from this base model
# with the DJ personality baked in (named "mbot-sidehost" by default).
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:latest")

# Custom Ollama model name — the bot will auto-create this from the base
# model + a Modelfile that bakes in the DJ personality. Once created, it
# persists in Ollama until manually deleted (ollama rm mbot-sidehost).
# You can also chat with it directly: ollama run mbot-sidehost
OLLAMA_CUSTOM_MODEL = os.environ.get("OLLAMA_CUSTOM_MODEL", "mbot-sidehost")

# How often the side host chimes in (0.0–1.0).
# 0.25 = ~25% chance after each template DJ line.
# 0.5 = ~50% chance. 1.0 = always speaks (alongside the main DJ).
OLLAMA_DJ_CHANCE = float(os.environ.get("OLLAMA_DJ_CHANCE", "0.25"))

# TTS voice for the AI side host (separate from the main DJ voice).
# This makes the two hosts sound like different people.
# Use ?djvoices in Discord to see available voices.
# NOTE: The default is determined by the active TTS engine at startup:
#   MOSS:      en_news_male (male, contrasts with the default female DJ voice)
#   VibeVoice: en-Carter_man
#   Edge TTS:  en-US-GuyNeural
# You can override this in .env with any valid voice name for your TTS engine.
_OLLAMA_DJ_VOICE_DEFAULTS = {
    "moss": "en_news_male",
    "vibevoice": "en-Carter_man",
    "edge-tts": "en-US-GuyNeural",
}
_ollama_dj_voice_fallback = _OLLAMA_DJ_VOICE_DEFAULTS.get(
    os.environ.get("TTS_MODE", "moss").lower(), "en-US-GuyNeural"
)
OLLAMA_DJ_VOICE = os.environ.get("OLLAMA_DJ_VOICE", _ollama_dj_voice_fallback)

# Timeout in seconds for Ollama API calls. If the LLM doesn't respond
# in this time, the side host is skipped (no dead air).
OLLAMA_DJ_TIMEOUT = int(os.environ.get("OLLAMA_DJ_TIMEOUT", "4"))

# Web Dashboard
WEB_HOST = os.environ.get("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("WEB_PORT", 8080))

WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "")

# Reverse Proxy support (e.g. Nginx Proxy Manager, Caddy, Traefik, Cloudflare Tunnel).
# When enabled, Flask's ProxyFix middleware is applied so the dashboard
# correctly respects X-Forwarded-For, X-Forwarded-Proto, and X-Forwarded-Host
# headers. This fixes HTTPS redirects, real client IPs in logs, and
# correct URL generation when the dashboard is behind a reverse proxy.
# Set to "true" if you access the dashboard through Nginx, Caddy, Traefik,
# Nginx Proxy Manager, Cloudflare Tunnel, or any other reverse proxy.
REVERSE_PROXY = os.environ.get("REVERSE_PROXY", "false").lower() == "true"

# Number of reverse proxy hops to trust (only used when REVERSE_PROXY=true).
# Set this to the number of proxies in front of the bot. Most setups have 1
# (e.g. Nginx Proxy Manager → Bot). Set to 2 if you have two proxies
# (e.g. Cloudflare Tunnel → Nginx → Bot).
TRUSTED_PROXY_COUNT = int(os.environ.get("TRUSTED_PROXY_COUNT", "1"))

# Now-Playing Channel — Discord channel ID where the bot sends its
# now-playing embed. If set, all now-playing messages go to this one
# channel regardless of which channel the user typed the command in.
# If blank or 0, the bot sends the message in the same channel as the command.
# Set this to a channel ID like 1234567890123456789 to bind it.
NOWPLAYING_CHANNEL_ID = int(os.environ.get("NOWPLAYING_CHANNEL_ID", 0) or 0)

# ── YouTube Live Streaming ──────────────────────────────────────────────
# Stream the bot's audio to a YouTube Live event via RTMP.
# Get your stream key from YouTube Studio → Go Live → Stream Key.
# The bot streams audio + a static image card with song titles.

YOUTUBE_STREAM_ENABLED = (
    os.environ.get("YOUTUBE_STREAM_ENABLED", "false").lower() == "true"
)
YOUTUBE_STREAM_KEY = os.environ.get("YOUTUBE_STREAM_KEY", "")
YOUTUBE_STREAM_URL = os.environ.get(
    "YOUTUBE_STREAM_URL", "rtmp://a.rtmp.youtube.com/live2"
)
YOUTUBE_STREAM_IMAGE = os.environ.get(
    "YOUTUBE_STREAM_IMAGE", ""
)  # Path to stream card image

# ── yt-dlp Cookie Authentication ──────────────────────────────────────────
# YouTube increasingly requires authentication to avoid "Sign in to confirm
# you're not a bot" errors. There are two ways to provide cookies:
#
# 1. cookies_from_browser: yt-dlp can extract cookies directly from an
#    installed browser (Chrome, Firefox, Brave, Edge, Opera, Vivaldi, etc).
#    Set this to the browser name, e.g. "chrome", "firefox", "brave".
#    The browser must be installed on the same machine as the bot.
#    You must have logged into YouTube at least once in that browser.
#    NOTE: On Linux, Chrome may require "keyring" or "secretstorage" packages
#    for cookie decryption. Firefox is usually easier on headless servers.
#    Format:Just the browser name, e.g. "chrome" or "firefox"
#    Can also specify profile: "chrome:Profile 1" or "firefox:default"
#
# 2. cookiefile: A Netscape-format cookies.txt file exported from your browser.
#    Use a browser extension like "Get cookies.txt LOCALLY" (Chrome/Firefox)
#    to export YouTube cookies. Place the file as "youtube_cookie.txt" in the
#    bot's root directory. This works on headless servers without a browser.
#
# Priority: cookies_from_browser > cookiefile > none
# If neither is set, yt-dlp runs without cookies (may get blocked by YouTube).
YTDDL_COOKIES_FROM_BROWSER = os.environ.get("YTDDL_COOKIES_FROM_BROWSER", "")
YTDDL_COOKIEFILE = os.environ.get("YTDDL_COOKIEFILE", "youtube_cookie.txt")
