# config.py

# It is recommended to use environment variables for sensitive data.
# However, you can hardcode the values here for simplicity.
import os
import warnings

BOT_VERSION = "v420.1.0"

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

# DJ Name — the on-air personality name for the main DJ bot.
# This is the name the DJ introduces herself as (e.g., "This is Nova on MBot Radio").
# Pick something that sounds like a badass radio host — short, punchy, memorable.
DJ_NAME = os.environ.get("DJ_NAME", "Nova")

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
#   Kokoro:    af_bella, af_sky, am_adam, bf_emma, etc. (prefix = language+gender)
#   MOSS:      name of a .wav file in assets/moss_voices/ (without extension)
#   VibeVoice: en-Carter_man, en-Journalist_woman, etc.
#   Edge TTS:   en-US-AriaNeural, en-US-GuyNeural, etc.
DJ_VOICE = os.environ.get("DJ_VOICE", "af_bella")

# ── TTS Engine ──────────────────────────────────────────────────────────
# The bot supports four TTS engines with a cascading fallback chain:
#
# 1. "kokoro" (default) — Kokoro-FastAPI via its OpenAI-compatible API.
#    GPU-accelerated (NVIDIA CUDA), best quality voice synthesis. 82M params.
#    Local, fast (~35-100x realtime on GPU), OpenAI-compatible /v1/audio/speech.
#    See: https://github.com/remsky/Kokoro-FastAPI
#    Docker: docker compose up -d  (includes kokoro-tts service)
#    Voice names: af_bella, af_sky, am_adam, am_michael, bf_emma, bf_isabella, etc.
#    Prefixes: af=American female, am=American male, bf=British female, bm=British male
#
# 2. "moss" — MOSS-TTS-Nano via its FastAPI server.
#    CPU-friendly, voice cloning via .wav prompt files. 48 kHz stereo, multilingual.
#    See: https://github.com/OpenMOSS/MOSS-TTS-Nano
#    Voices are .wav prompt audio files in assets/moss_voices/
#
# 3. "vibevoice" — Uses a separately-hosted VibeVoice-Realtime WebSocket server.
#    Lower latency (~300ms), runs on GPU/CPU in a separate process.
#    Requires: VibeVoice server running (see https://github.com/microsoft/VibeVoice)
#    Voice names like: en-Carter_man, en-Journalist_woman, de-Anna_woman
#    (The legacy alias "local" also works and maps to vibevoice.)
#
# 4. "edge-tts" — Microsoft Edge TTS voices (cloud-based).
#    Free, no server needed, 100+ voices in 40+ languages, but higher latency
#    and depends on Microsoft's cloud API. Used as automatic fallback.
#    Requires: pip install edge-tts
#    Voice names like: en-US-AriaNeural, en-US-GuyNeural, en-GB-SoniaNeural
#
# Fallback chain: kokoro → moss → edge-tts  (or moss → edge-tts, vibevoice → edge-tts)
# If the primary engine fails, the bot automatically falls back to the next in chain.
TTS_MODE = os.environ.get("TTS_MODE", "kokoro").lower()

# Kokoro-FastAPI server URL (only used when TTS_MODE=kokoro).
# Start with: docker compose up -d kokoro-tts
# Or: python start-cpu.sh / start-gpu.sh from the Kokoro-FastAPI repo.
# Docker: http://kokoro-tts:8880  |  Bare metal: http://localhost:8880
KOKORO_TTS_URL = os.environ.get("KOKORO_TTS_URL", "http://localhost:8880")

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
OLLAMA_DJ_ENABLED = os.environ.get("OLLAMA_DJ_ENABLED", "true").lower() == "true"

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
# Values outside [0.0, 1.0] are clamped to the valid range.
_OLLAMA_DJ_CHANCE_RAW = float(os.environ.get("OLLAMA_DJ_CHANCE", "0.25"))
OLLAMA_DJ_CHANCE = max(0.0, min(1.0, _OLLAMA_DJ_CHANCE_RAW))
if _OLLAMA_DJ_CHANCE_RAW != OLLAMA_DJ_CHANCE:
    import warnings

    warnings.warn(
        f"OLLAMA_DJ_CHANCE={_OLLAMA_DJ_CHANCE_RAW} is out of range [0.0, 1.0], "
        f"clamped to {OLLAMA_DJ_CHANCE}",
        stacklevel=2,
    )

# TTS voice for the AI side host (separate from the main DJ voice).
# This makes the two hosts sound like different people.
# Use ?djvoices in Discord to see available voices.
# NOTE: The default is determined by the active TTS engine at startup:
#   Kokoro:    am_adam (American male, contrasts with the default female DJ voice)
#   MOSS:      en_news_male (male, contrasts with the default female DJ voice)
#   VibeVoice: en-Carter_man
#   Edge TTS:  en-US-GuyNeural
# You can override this in .env with any valid voice name for your TTS engine.
_OLLAMA_DJ_VOICE_DEFAULTS = {
    "kokoro": "am_adam",
    "moss": "en_news_male",
    "vibevoice": "en-Carter_man",
    "edge-tts": "en-US-GuyNeural",
}
_ollama_dj_voice_fallback = _OLLAMA_DJ_VOICE_DEFAULTS.get(
    os.environ.get("TTS_MODE", "kokoro").lower(), "en-US-GuyNeural"
)
OLLAMA_DJ_VOICE = os.environ.get("OLLAMA_DJ_VOICE", _ollama_dj_voice_fallback)

# Timeout in seconds for Ollama API calls. If the LLM doesn't respond
# in this time, the side host is skipped (no dead air).
OLLAMA_DJ_TIMEOUT = int(os.environ.get("OLLAMA_DJ_TIMEOUT", "4"))

# Web Dashboard
WEB_HOST = os.environ.get("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("WEB_PORT", 8080))

WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "")

# ── Hermes Agent API ──────────────────────────────────────────────────
# Machine-to-machine API key for external agents (Hermes, automation,
# monitoring tools) to control the bot programmatically. When set,
# Hermes API endpoints are enabled and require this key as a Bearer
# token in the Authorization header:
#   Authorization: Bearer <HERMES_API_KEY>
#
# If empty/unset, the Hermes API is disabled entirely.
# Generate one with: python3 -c "import secrets; print(secrets.token_urlsafe(32))"
HERMES_API_KEY = os.environ.get("HERMES_API_KEY", "").strip()

# ── SilverBullet Knowledge Base ────────────────────────────────────────
# The Hermes Agent can document station events, incidents, session logs,
# and playback history to a SilverBullet PKM instance. This creates a
# queryable, hyperlinked knowledge base for the radio station.
#
# SILVERBULLET_URL: Base URL of the SilverBullet instance (no trailing slash)
#   e.g. "https://silver.istealyourdomain.org"
#
# SILVERBULLET_TOKEN: Bearer token for SB_AUTH_TOKEN (optional if no auth)
#   Only needed if the SilverBullet instance has SB_AUTH_TOKEN configured.
#
# SILVERBULLET_ENABLED: Set to "true" to enable auto-documentation.
#   When disabled, all SilverBullet write operations are silently skipped.
#
# SILVERBULLET_PREFIX: Page path prefix for all station pages.
#   Default: "station" — so pages land at station/Daily Log/2026-04-20,
#   station/Incidents/cookie-auth-block, etc.
SILVERBULLET_URL = os.environ.get("SILVERBULLET_URL", "").strip().rstrip("/")
SILVERBULLET_TOKEN = os.environ.get("SILVERBULLET_TOKEN", "").strip()
SILVERBULLET_ENABLED = os.environ.get("SILVERBULLET_ENABLED", "false").lower() == "true"
SILVERBULLET_PREFIX = os.environ.get("SILVERBULLET_PREFIX", "station")

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
# Stream the bot's audio to YouTube Live via RTMP or OBS Studio.
# Two modes: mirror (shadows Discord audio) or curated (Shadow DJ).
# No autonomous 24/7 mode — streams are started manually from Mission Control
# or via ?golive/?stoplive Discord commands.

YOUTUBE_STREAM_ENABLED = (
    os.environ.get("YOUTUBE_STREAM_ENABLED", "false").lower() == "true"
)
YOUTUBE_STREAM_KEY = os.environ.get("YOUTUBE_STREAM_KEY", "")
YOUTUBE_STREAM_URL = os.environ.get(
    "YOUTUBE_STREAM_URL", "rtmp://a.rtmp.youtube.com/live2"
)
YOUTUBE_STREAM_BACKUP_URL = os.environ.get(
    "YOUTUBE_STREAM_BACKUP_URL",
    "rtmp://b.rtmp.youtube.com/live2?backup=1",
)
YOUTUBE_STREAM_IMAGE = os.environ.get(
    "YOUTUBE_STREAM_IMAGE", ""
)  # Path to stream card image

# Animated GIF overlay for YouTube Live stream card.
# This GIF plays on top of the stream video — adds visual energy.
# Falls back to assets/giphy.gif if not set.
# Set to "" to disable the GIF overlay.
YOUTUBE_STREAM_GIF = os.environ.get("YOUTUBE_STREAM_GIF", "")

# Default playlist URL for YouTube Live curated (Shadow DJ) mode.
# When starting curated streaming from the web dashboard without
# specifying a playlist, this URL is used for initial queue filling.
# Can be a YouTube playlist URL or a search query.
# Example: https://youtube.com/playlist?list=PLxxxxxxx
YOUTUBE_STREAM_PLAYLIST = os.environ.get("YOUTUBE_STREAM_PLAYLIST", "")

# ── YouTube Live: OBS Overlay ──────────────────────────────────────────
# When streaming via OBS Studio, this URL is used as the browser source
# for the overlay page. Defaults to localhost:8080 (bare-metal) or can
# be set to http://bot:8080 (Docker) or any other URL.
OBS_OVERLAY_URL = os.environ.get("OBS_OVERLAY_URL", "http://localhost:8080/overlay")

# OBS Overlay Mode — how the overlay is rendered in OBS:
#   "browser" — Use a browser_source pointing to OBS_OVERLAY_URL.
#               This renders the full Mission Control overlay.html including
#               the real-time audio waveform visualizer, album art, SFX
#               animations, DJ text, and ticker. Best for stream health
#               (consistent pixel variation helps YouTube's encoder).
#               REQUIRES obs-browser plugin (included in Flatpak OBS, Ubuntu PPA,
#               Windows, macOS — NOT in Debian 12 apt OBS).
#
#   "native"  — Use native OBS color + text sources reading from /tmp/radio_*.txt
#               files. Works on ALL platforms (no obs-browser needed), but
#               does NOT include the waveform visualizer, album art, or SFX
#               animations. Pure text overlay.
#
#   "auto"    — Try browser_source first; if it fails (error 605 — obs-browser
#               not installed), fall back to native overlay. (DEFAULT)
#
OBS_OVERLAY_MODE = os.environ.get("OBS_OVERLAY_MODE", "auto").lower()

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

# ── OBS Studio Integration ──────────────────────────────────────────────
# Control OBS Studio from Mission Control via obs-websocket 5.x.
# Requires OBS Studio with obs-websocket plugin (bundled since OBS 28).
#
# OBS can be installed via apt (Debian) or Flatpak (recommended for
# browser source support). Set OBS_USE_FLATPAK=true if using Flatpak.
# start.sh and bot.py use this to choose the correct launch command and
# config directory paths.
#
# Setup:
#   1. Install OBS Studio (Flatpak recommended): https://obsproject.com
#   2. In OBS: Tools → WebSocket Server Settings → Enable
#   3. Set a password in OBS WebSocket settings
#   4. Set OBS_WS_PASSWORD in .env (and OBS_WS_HOST/OBS_WS_PORT if not localhost)
#
# If OBS_WS_PASSWORD is not set, the OBS page in Mission Control
# shows a "not configured" message instead of erroring.

# Set to "true" if OBS is installed via Flatpak. This changes:
#   - Launch command: `flatpak run com.obsproject.Studio` instead of `obs`
#   - Config dir: ~/.var/app/com.obsproject.Studio/config/obs-studio/
#   - Headless: Uses QT_QPA_PLATFORM=offscreen (xvfb not compatible with Flatpak)
# Default: "auto" — start.sh will auto-detect by checking if flatpak OBS is installed
OBS_USE_FLATPAK = os.environ.get("OBS_USE_FLATPAK", "auto").lower()

OBS_WS_ENABLED = os.environ.get("OBS_WS_ENABLED", "true").lower() == "true"
OBS_WS_HOST = os.environ.get("OBS_WS_HOST", "localhost")
OBS_WS_PORT = int(os.environ.get("OBS_WS_PORT", "4455"))
OBS_WS_PASSWORD = os.environ.get("OBS_WS_PASSWORD", "")

# OBS Auto Scene Switching — the bot automatically switches OBS scenes
# based on the current playback state. This makes the visual on YouTube Live
# match the audio (e.g., "Now Playing" during songs, "DJ Speaking" during DJ lines).
# Set to "true" to enable. Requires OBS_WS_ENABLED=true and OBS_WS_PASSWORD set.
# Scene names must match your OBS scene collection exactly.
OBS_AUTO_SCENES = os.environ.get("OBS_AUTO_SCENES", "false").lower() == "true"

# Scene name mappings — these are the scene names the bot will switch to.
# Change these if you rename scenes in OBS. Must match your OBS scene
# collection exactly (case-sensitive).
OBS_SCENE_NOW_PLAYING = os.environ.get("OBS_SCENE_NOW_PLAYING", "️ Now Playing")
OBS_SCENE_DJ_SPEAKING = os.environ.get("OBS_SCENE_DJ_SPEAKING", "🎙️ DJ Speaking")
OBS_SCENE_WAITING = os.environ.get("OBS_SCENE_WAITING", "⏳ Waiting")
OBS_SCENE_OVERLAY = os.environ.get("OBS_SCENE_OVERLAY", "📺 Overlay Only")

# ── Radio Commercial Breaks ────────────────────────────────────────────
# AI-generated "fake" radio commercials that play between songs for
# that authentic 24/7 radio feel. Uses Ollama for unique ad copy each
# time, with ~40 pre-written absurdist templates as fallback.
#
# Commercials play BEFORE the DJ intro for the next song:
#   [Song A ends] → [Commercial Break] → [DJ: "Up next, Song B!"] → [Song B]
#
# The chance of a commercial increases the longer it's been since the
# last one (base chance after MIN_SONGS, 2× after 6 songs, 3× after 10).

# Master switch — enable/disable commercial breaks globally.
# Per-guild toggle via ?commercials command overrides this.
COMMERCIAL_ENABLED = os.environ.get("COMMERCIAL_ENABLED", "true").lower() == "true"

# Probability of a commercial break per eligible song transition.
# 0.15 = ~15% chance, evaluated after each COMMERCIAL_MIN_SONGS songs.
COMMERCIAL_CHANCE = float(os.environ.get("COMMERCIAL_CHANCE", "0.15"))

# Minimum number of songs between commercial breaks.
# A commercial will never play if fewer than this many songs have played
# since the last commercial. Prevents ad overload.
COMMERCIAL_MIN_SONGS = int(os.environ.get("COMMERCIAL_MIN_SONGS", "3"))

# Maximum commercial duration in seconds. TTS output longer than this
# is truncated at a sentence boundary.
COMMERCIAL_MAX_DURATION = int(os.environ.get("COMMERCIAL_MAX_DURATION", "30"))

# Don't play commercials if the queue has fewer than this many songs.
# A commercial before the last song feels anticlimactic.
COMMERCIAL_MIN_QUEUE = int(os.environ.get("COMMERCIAL_MIN_QUEUE", "2"))

# TTS voices for commercials — 3 rotating "announcer" voices.
# Each commercial picks a random voice from this list so ads sound like
# different spokespersons, not the same DJ reading ad copy.
#
# Kokoro voices:  af_bella, af_sky, am_adam, am_michael, bf_emma, bf_isabella, bm_george, bm_lewis
# MOSS voices:    en_warm_female, en_news_male (or any .wav in assets/moss_voices/)
# Edge TTS:       en-US-AriaNeural, en-US-GuyNeural, en-GB-SoniaNeural
#
# If empty, the DJ voice is used for all commercials (not recommended —
# it sounds like Nova is reading ads, which kills the illusion).
_COMMERCIAL_VOICES_RAW = os.environ.get("COMMERCIAL_VOICES", "")
if _COMMERCIAL_VOICES_RAW:
    COMMERCIAL_VOICES = [
        v.strip() for v in _COMMERCIAL_VOICES_RAW.split(",") if v.strip()
    ]
else:
    # Default: 3 distinct Kokoro voices that don't overlap with the DJ
    # (af_bella is the default DJ voice, so we use different ones)
    COMMERCIAL_VOICES = ["am_adam", "bf_emma", "bm_george"]

# ── Station Wars: Frequency Hijack ──────────────────────────────────
# Instead of a normal commercial, a transmission from another dimension/kosmos
# "bleeds" onto the frequency for ~15 seconds. Then the DJ cuts back in
# with a recovery line. The hijack voices are the SAME 3 commercial voices
# — but they're from another kosmos, so they sound alien and wrong on your
# frequency.
#
# Station Wars is checked BEFORE normal commercials. If a hijack triggers,
# it replaces the commercial break entirely. If not, the normal commercial
# logic runs as usual.
#
# Flow: [Song A ends] → [Dimensional hijack] → [DJ recovery line] → [DJ intro] → [Song B]

# Master switch — enable/disable Station Wars frequency hijacks globally.
RADIO_HIJACK_ENABLED = os.environ.get("RADIO_HIJACK_ENABLED", "true").lower() == "true"

# Probability of a frequency hijack per eligible song transition.
# 0.05 = ~5% chance, evaluated before normal commercials. Rare = never gets old.
RADIO_HIJACK_CHANCE = float(os.environ.get("RADIO_HIJACK_CHANCE", "0.05"))

# Hijack voices share the same COMMERCIAL_VOICES pool — same 3 voices, but
# they're from another dimension. No separate voice config needed.
