# MBot 6.3.0 — Comprehensive Technical Guide

> **Last Updated:** 2026-04-15
> **Version:** 6.3.0
> **License:** MIT

---

## What's New in 6.3.0

### 🧡 Kokoro-TTS Engine (New Default TTS)

The bot now supports **three TTS engines** with Kokoro-TTS as the new default. Kokoro runs as a Docker container on your GPU via [Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI), providing truly local, open-weight TTS with ~300ms first-audio latency and zero cloud dependency.

**Engine priority:** `kokoro` (default) → `vibevoice` → `edge-tts` (cloud fallback)

**How it works:**
- The bot sends `POST /v1/audio/speech` to the Kokoro-FastAPI Docker server (OpenAI-compatible REST API) and receives a WAV file.
- A built-in health check (`GET /v1/audio/voices`) caches server reachability — if the server is down, the bot bails in ≤3 seconds and falls back to edge-tts instantly (not the old 30-second timeout).
- Voice names: `af_heart`, `af_bella`, `am_adam`, `bm_george`, etc. (11 built-in voices).
- The legacy `TTS_MODE=local` alias is still supported and maps to `vibevoice` with a deprecation warning.

**Setup:**
```bash
docker run --gpus all -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-gpu:v0.2.1
```
Then set in `.env`:
```ini
TTS_MODE=kokoro
KOKORO_TTS_URL=http://localhost:8880
DJ_VOICE=af_heart
```

### 🔧 Kokoro WAV Streaming Header Fix (Critical Bug Fix)

The Kokoro-FastAPI server sends WAV files with **streaming-style headers** where the data chunk size is `0xFFFFFFFF` (unknown). This caused two cascading failures:

1. Python's `wave` module read `nframes=2,147,483,647` → reported **89478.5s** duration (24 hours) for a 3-second clip.
2. FFmpeg read the broken header and **hung waiting for data that never came** — the TTS `after` callback never fired, `is_playing()` stayed True forever, and every subsequent `play()` call failed with "Already playing audio", silently skipping the entire queue.

**Fix (3 layers):**
- **Layer 1 (root cause):** After downloading WAV data from Kokoro, the bot detects the broken streaming header (`nframes` claims more data than received) and **rewrites the WAV file** with correct chunk sizes using Python's `wave` module. Output now shows correct duration: `3.2s` instead of `89478.5s`.
- **Layer 2 (defense in depth):** FFmpeg options for TTS playback now include `-t 30` — even if a header lies about duration, FFmpeg stops after 30 seconds and the `after` callback fires.
- **Layer 3 (emergency recovery):** Before every `voice_client.play()` call (both TTS and song playback), the code now checks `is_playing()`. If the voice client is stuck playing something from a previous failed playback, it force-stops it with a 300ms cooldown. This prevents the "Already playing audio" cascade that was skipping through the entire queue with no audio output.

### 🗣️ Three-Engine TTS Architecture

The TTS system has been refactored from a 2-engine system (`edge-tts` / `local`) to a 3-engine system:

| Priority | Engine | Where | When Used |
|---|---|---|---|
| 1st | **Kokoro-TTS** (`kokoro`) | Docker container on your GPU | Default — truly local, ~300ms latency |
| 2nd | **VibeVoice** (`vibevoice`) | Separate WebSocket server | If explicitly configured |
| 3rd | **Edge TTS** (`edge-tts`) | Microsoft Cloud | Automatic fallback when local engines fail |

**New config variables:** `KOKORO_TTS_URL`, `KOKORO_VOICE`, `VIBEVOICE_TTS_URL`. The old `LOCAL_TTS_URL` still works as a backward-compatible alias.

**How fallback works:**
```
TTS_MODE=kokoro → health check → server up? → generate WAV → ✅ success
                                     ↓ server down (≤3s detection)
                                     → log warning with docker command
                                     → resolve voice name for edge-tts
                                     → edge-tts generates MP3 → ✅ success
```

**Voice name auto-resolution:** The `_resolve_voice()` function detects mismatched voice names (e.g. passing `en-US-AriaNeural` when using Kokoro) and swaps them for the target engine's default — so switching `TTS_MODE` doesn't require changing `DJ_VOICE`.

**The `TTS_AVAILABLE` flag:** A new boolean that's `True` when *any* TTS engine is available (not just edge-tts). The DJ mode and AI side host commands now gate on `TTS_AVAILABLE` instead of `EDGE_TTS_AVAILABLE`, so DJ works with Kokoro even if edge-tts isn't installed.

### 📡 Mission Control: 30-Second Soft Refresh

The Mission Control dashboard now auto-refreshes every **30 seconds** via AJAX, updating all guild data (DJ status, queue, listeners, controls) **without** touching the progress bar. The progress bar keeps ticking independently on its 1-second client-side timer with zero jitter.

**How it works:**
- Every 30 seconds, `fetch()` grabs fresh HTML from the server with a cache-busting param.
- A `DOMParser` extracts updated content from each guild card.
- Surgical DOM patching replaces: guild header (status badges), control buttons, song title, thumbnail, queue list, listener list, volume label.
- **The progress bar is deliberately skipped** — the 1-second JS timer keeps it smooth. Only when a song *changes* (different title detected) does the progress bar get replaced and the JS timer reinitialized from the server's `current_elapsed`.
- When a song ends (progress hits 100%), the soft refresh is triggered instead of `location.reload()`.
- Guild cards now carry `data-guild-card`, `data-elapsed`, `data-duration`, `data-speed` attributes for the JS DOM patcher.

**Three independent refresh systems:**

| System | Interval | What | Method |
|---|---|---|---|
| Progress bar ticker | 1 second | Bar fill width + elapsed time | Client-side JS only |
| Soft refresh | 30 seconds | All other dashboard data | AJAX + DOM patching |
| Fallback full reload | 3 minutes | Full page | `location.reload()` (only when nothing is playing) |

### 📋 Activity Log Panel (Mission Control)

A live, Discord-channel-style activity log panel now slides out from the right side of Mission Control when you click **📋 Log** in the sidebar. It streams the exact same log messages that are shipped to the Discord log channel — in real-time, with no Discord API round-trip.

**How it works:**
- A thread-safe ring buffer (`deque(maxlen=200)`) is attached to `DiscordLogHandler` — every `emit()` pushes a structured log entry to this buffer alongside the existing Discord flush.
- Two new Flask endpoints expose the buffer: `GET /api/logs/recent` (initial load, returns last N entries as JSON) and `GET /api/logs/stream` (Server-Sent Events for real-time streaming).
- The browser uses `EventSource` (SSE) to receive new entries as they're emitted, with auto-reconnect and heartbeat keep-alive.
- **Filter buttons** (All / Info / Warn / Error) let you narrow what's visible — client-side only.
- Each entry shows a timestamp, color-coded severity badge (INFO=blue, WARNING=amber, ERROR=red, DEBUG=gray), and the message in monospace.
- The panel is a 420px slide-out on desktop, 100% width on mobile.
- No new dependencies — SSE is native browser API.

### 🎙️ Voice Dropdown Fixes (Radio Page)

The "DJ Voice" and "AI Side Host Voice" dropdowns on the Radio page were permanently stuck at "Loading voices..." due to two bugs:

1. **Script ordering** — Inline `<script>` tags called `loadVoices()` before the function was defined (the definitions were at the bottom of the page). Fixed: functions are now called via `DOMContentLoaded` after all scripts load. The current voice is stored in a `data-current` attribute on the `<select>` element.
2. **No voice caching** — Every dropdown open called `edge_tts.list_voices()` which makes a live HTTP request to Microsoft's TTS API (5–15 seconds). Fixed: server-side cache with 30-minute TTL; first call fetches from Microsoft, all subsequent calls return the cached list instantly. If the API times out, stale cache is returned (graceful degradation). Descriptive error messages now appear in the dropdown when `edge-tts` isn't installed or the API is unreachable.

### 🃏 AI Side Host — Reactive Banter (DJ Context Awareness)

The AI side host now **knows what the main DJ just said** and can react to it. Instead of generating blind banter, the side host receives the main DJ's spoken line as context when calling Ollama, enabling two-host chemistry like a real radio show.

**How it works:**
- `_dj_speak()` stores the clean spoken text in `self._last_dj_line[guild_id]` (after stripping `{sound:name}` tags).
- When the AI side host is about to speak, the last DJ line is passed through `_try_ai_side_host(dj_line=...)` → `generate_side_host_line(dj_line=...)` → `_build_user_prompt(dj_line=...)` → included as `Main DJ just said: "..."` in the Ollama prompt.
- An `is_ai=True` flag on `_dj_speak()` prevents the AI's own lines from overwriting the main DJ's stored line — the AI always reacts to the main DJ, not to itself.
- **4 new reactive banter categories:** `react_agree` (agree + funny twist), `react_disagree` (playful pushback), `react_one_up` (escalate the joke), `react_tangent` (go off on a funny tangent).
- **Smart category selection:** When a DJ line is provided, 60% chance of a reactive category, 40% chance of independent banter (for variety).
- The system prompt now includes a "REACTING TO THE MAIN DJ" section teaching the model to respond to the DJ's line without repeating it.

### 🔧 Ollama Error Handling Improvements

The 404 error from Ollama when a model isn't pulled now shows an actionable message:
- **Before:** `AI Side Host: Ollama returned status 404`
- **After:** `AI Side Host: Model 'llama3.2' not found (Ollama 404). Run: ollama pull llama3.2 | Available models: gemma4:latest`

On 404, the handler now queries `/api/tags` to list what's actually available and includes the pull command in the log. The `check_ollama_available()` function also now includes `Run: ollama pull <model>` in its error message.

### 🔄 Default Model Change

The default Ollama model has been changed from `llama3.2` to `gemma4:latest` across all files (`config.py`, `.env.example`, `utils/llm_dj.py`, `web/app.py`, `cogs/music.py`). The `.env.example` now lists `gemma4:latest`, `phi3:mini`, `llama3.2`, and `gemma2:2b` as recommended models.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture & Project Structure](#2-architecture--project-structure)
3. [Prerequisites & System Requirements](#3-prerequisites--system-requirements)
4. [Installation & Setup](#4-installation--setup)
5. [Configuration Deep Dive](#5-configuration-deep-dive)
6. [Entry Point: bot.py](#6-entry-point-botpy)
7. [YouTube Source: cogs/youtube.py](#7-youtube-source-cogsyoutubepy)
8. [Music Cog: cogs/music.py](#8-music-cog-cogsmusicpy)
9. [DJ Mode: utils/dj.py](#9-dj-mode-utilsdjpy--music-cog-integration)
10. [Admin Cog: cogs/admin.py](#10-admin-cog-cogsadminpy)
11. [Logging Cog: cogs/logging.py](#11-logging-cog-cogsloggingpy)
12. [Utility Modules](#12-utility-modules)
13. [Web Dashboard: Mission Control](#13-web-dashboard-mission-control)
14. [Soundboard System](#14-soundboard-system)
15. [DJ Custom Lines](#15-dj-custom-lines)
16. [Test Suite](#16-test-suite)
17. [Launcher Scripts](#17-launcher-scripts)
18. [Complete Command Reference](#18-complete-command-reference)
19. [Troubleshooting & Known Issues](#19-troubleshooting--known-issues)
20. [Development Guide](#20-development-guide)

---

## 1. Overview

**MBot 6.3.0** is a self-contained Discord music bot built with Python and `discord.py`. It plays audio from YouTube (URLs, searches, playlists) and Suno (direct song URLs) directly into Discord voice channels. The bot is designed to run as a persistent background service on Debian-based Linux servers, managed through `screen` sessions.

### Key Features

| Category | Feature | Details |
|---|---|---|
| **Playback** | Play, Pause, Resume, Skip, Stop | Full playback lifecycle |
| **Sources** | YouTube URLs, searches, playlists | Via `yt-dlp` |
| **Sources** | Suno.com / app.suno.ai song URLs | Direct CDN MP3 streaming |
| **Queue** | Add, Remove, Clear, Shuffle, View | Per-guild asyncio.Queue |
| **Audio** | Volume (0–200%), Speed (0.25x–2.0x) | PCMVolumeTransformer + FFmpeg atempo |
| **Looping** | Toggle loop for current song | Re-adds song to queue after playback |
| **UI** | Now Playing embed with progress bar | Auto-updates every 40 seconds |
| **UI** | Interactive button controls | Play, Pause, Skip, Stop, Queue buttons |
| **DJ Mode** | Radio DJ between tracks | TTS voice commentary: intros, outros, transitions |
| **DJ Mode** | Soundboard sound tags in DJ lines | `{sound:airhorn}` in any line plays a sound effect after the DJ speaks |
| **DJ Mode** | **Kokoro-TTS (default)** | `TTS_MODE=kokoro` — Docker GPU server, ~300ms latency, 11 voices, zero cloud dependency |
| **DJ Mode** | VibeVoice-Realtime | `TTS_MODE=vibevoice` — separate WebSocket server, ~300ms latency, `en-Carter_man` etc. |
| **DJ Mode** | Edge TTS fallback | Automatic fallback to Microsoft cloud TTS when local engines fail |
| **DJ Mode** | 172 built-in DJ line templates | 74 with sound tags across 10 categories |
| **DJ Mode** | Custom DJ lines | Add/remove via web dashboard, persisted in JSON |
| **AI Side Host** | Ollama-powered studio joker | Second radio personality with own voice, writes original banter, hot takes, and jokes |
| **AI Side Host** | Separate TTS voice | `OLLAMA_DJ_VOICE` in `.env` — sounds like a different person |
| **AI Side Host** | Tunable chime-in frequency | `OLLAMA_DJ_CHANCE` (0.0–1.0) controls how often the side host speaks |
| **AI Side Host** | 8 banter categories | Random thoughts, shoutouts, song roasts, station trivia, queue hype, vibe checks, hot takes, request prompts |
| **AI Side Host** | 4 reactive banter categories | react_agree, react_disagree, react_one_up, react_tangent — AI reacts to what the main DJ just said |
| **AI Side Host** | DJ context awareness | Main DJ's spoken line is passed to Ollama so the AI can respond to it, not just talk over it |
| **AI Side Host** | Actionable Ollama errors | 404 errors show the model name, pull command, and available models instead of just "status 404" |
| **Soundboard** | Web-based sound effects board | 9 built-in sounds + upload your own via browser |
| **Crossfade** | Fade-in on new songs | Configurable crossfade duration via `CROSSFADE_DURATION` |
| **Lyrics** | Synced lyrics lookup | `syncedlyrics` (primary) + web scraping fallbacks |
| **Presets** | Save/load playlist presets | Queue state persisted as JSON, loadable from web or Discord |
| **Web Dashboard** | Mission Control | Flask web app: playback controls, queue drag-and-drop, volume/speed sliders, DJ voice picker, search-to-queue |
| **Web Dashboard** | Login & password protection | Optional password set via `WEB_PASSWORD` in `.env` — dashboard is open access if blank |
| **Web Dashboard** | Settings page | Restart/shutdown controls, bot status info |
| **Web Dashboard** | Activity Log Panel | 📋 Log sidebar item opens a slide-out panel with real-time SSE log streaming, severity filtering, and auto-reconnect |
| **Web Dashboard** | Voice caching | TTS voice list cached server-side for 30 minutes — instant dropdown load after first fetch |
| **Web Dashboard** | Join/Leave voice | Dashboard buttons to connect/disconnect the bot from voice |
| **Auto-Disconnect** | 60-second inactivity timer | Disconnects from voice when idle |
| **Admin** | Remote shutdown & restart | Bot owner only |
| **Admin** | Cookie management for yt-dlp | Fetch and set cookies from any HTTPS URL |
| **Logging** | File + console + Discord channel | Buffered log shipping to a Discord channel |
| **Deployment** | Two launcher scripts | `launch.sh` (minimal) and `start.sh` (interactive wizard) |
| **Auto-Setup** | System deps, venv, pip, .env wizard | First-run experience needs zero manual config |

---

## 2. Architecture & Project Structure

```
this2.0/
├── bot.py                  # Entry point — bot lifecycle, cog loading, web server thread, logging
├── config.py               # Configuration loader (env vars + constants including DJ, crossfade, web)
├── .env.example            # Template for environment variables
├── requirements.txt        # Python dependencies (flask, edge-tts, syncedlyrics added)
├── launch.sh               # Minimal launcher (setup/start/stop/restart/attach/doctor)
├── start.sh                # Interactive launcher with colored output & setup wizard
├── GUIDE.md                # This comprehensive guide
├── LICENSE                 # MIT License
│
├── cogs/                   # Discord.py extension modules (loaded at runtime)
│   ├── __init__.py         # Auto-generated; makes cogs a Python package
│   ├── music.py            # Music commands & playback engine (~1900 lines) with DJ integration, PlaceholderTrack, crossfade
│   ├── admin.py            # Admin/owner-only commands (shutdown, restart, cookies)
│   ├── youtube.py          # YTDLSource, PlaceholderTrack — yt-dlp extraction + lazy resolution
│   └── logging.py          # Message/command/error logging to file
│
├── utils/                  # Helper modules
│   ├── __init__.py         # Auto-generated; makes utils a Python package
│   ├── dj.py               # Radio DJ mode — 3-engine TTS (Kokoro/VibeVoice/Edge), 172 templates, WAV header fix, health check, sound tag support
│   ├── llm_dj.py           # AI side host — Ollama client, studio joker personality, 8 banter categories
│   ├── custom_lines.py     # JSON persistence for custom DJ lines (CRUD operations)
│   ├── soundboard.py       # Sound listing, path resolution, directory traversal prevention
│   ├── lyrics.py           # Synced lyrics lookup (syncedlyrics + web scraping fallbacks)
│   ├── presets.py          # Save/load/delete playlist presets as JSON
│   ├── suno.py             # Suno.com URL detection & audio resolution
│   ├── discord_log_handler.py  # Ships log lines to a Discord channel (buffered)
│   ├── cookie_parser.py    # Parses Set-Cookie headers → Netscape cookie file (NOTE: parse_all_cookies missing)
│   └── import_parser.py    # Parses bot log files (timestamped entries)
│
├── web/                    # Flask Mission Control Dashboard
│   ├── app.py              # Flask app — 25+ API endpoints, login auth, settings, template filters, soundboard/upload/delete
│   ├── __init__.py
│   ├── templates/          # Jinja2 HTML templates
│   │   ├── base.html       # Dark layout, sidebar nav, dynamic bot name, conditional auto-refresh, logout link
│   │   ├── login.html      # Standalone login page (not extending base.html)
│   │   ├── dashboard.html  # Full interactive dashboard with all playback controls, join/leave buttons
│   │   ├── settings.html   # Settings page — restart, shutdown, system info
│   │   ├── soundboard.html # Dedicated soundboard page with upload, play, delete
│   │   ├── dj_lines.html   # DJ line CRUD with {sound:name} visual highlights
│   │   ├── radio.html      # Auto-DJ, voice picker, recently played history
│   │   └── queue_manager.html # Full queue management with drag-and-drop reorder
│   └── static/
│       └── style.css        # Dark mission control theme
│
├── sounds/                 # Soundboard audio files (9 generated .wav effects)
│   ├── airhorn.wav         # Classic airhorn blast
│   ├── air_raid.wav        # Siren/air raid alarm
│   ├── applause.wav        # Crowd applause
│   ├── button_press.wav    # UI button click
│   ├── club_hit.wav        # Bass drop / club hit
│   ├── dj_drop.wav         # DJ drop / station ID effect
│   ├── in_the_mix.wav      # "In the mix" transition effect
│   ├── record_scratch.wav  # Vinyl record scratch
│   └── dj_scratch.wav # Turntable motor spin-up
│
├── presets/                # Saved playlist JSON files (created at runtime)
├── tests/                  # pytest test suite
├── yt_dlp_cache/           # yt-dlp metadata cache directory (auto-created)
├── bot_activity.log        # Runtime log file (auto-created)
├── bot.log                 # Screen session log (auto-created by launchers)
├── dj_custom_lines.json    # Custom DJ lines persistence (auto-created)
└── youtube_cookie.txt      # yt-dlp cookie file (created by ?fetch_and_set_cookies)
```

### How Modules Relate

```
bot.py
  ├── loads → cogs/music.py
  ├── loads → cogs/admin.py
  ├── skips → cogs/youtube.py (imported directly by music.py, NOT auto-loaded)
  ├── skips → cogs/logging.py (loaded manually, NOT auto-loaded)
  ├── initializes → utils/discord_log_handler.py
  ├── starts → web/app.py (Flask dashboard in background thread)
  └── creates → sounds/, presets/ directories

cogs/music.py
  ├── imports → cogs/youtube.py (YTDLSource, PlaceholderTrack, FFMPEG_OPTIONS, YTDL_FORMAT_OPTIONS)
  ├── imports → utils/suno.py (is_suno_url, get_suno_track)
   ├── imports → utils/dj.py (EDGE_TTS_AVAILABLE, TTS_MODE, TTS_AVAILABLE, generate_intro, generate_song_intro, generate_outro, generate_tts, cleanup_tts_file, extract_sound_tags, list_voices, KOKORO_TTS_URL, VIBEVOICE_TTS_URL)
   ├── imports → utils/lyrics.py (get_lyrics)
   ├── imports → utils/presets.py (save_preset, load_preset, queue_to_tracks)
   ├── imports → utils/soundboard.py (list_sounds, get_sound_path)
   ├── imports → utils/llm_dj.py (OLLAMA_DJ_AVAILABLE, generate_side_host_line, should_side_host_speak, check_ollama_available)
   ├── state → ai_dj_enabled[guild_id], ai_dj_voice[guild_id] (AI side host per-guild toggle + own TTS voice)
  └── imports → config.py (emojis, prefix, CROSSFADE_DURATION, STATION_NAME, etc.)

cogs/admin.py
  ├── imports → utils/cookie_parser.py (parse_all_cookies) ⚠️ BUG: function not defined
  └── modifies → cogs/youtube.py (YTDL_FORMAT_OPTIONS["cookiefile"])

web/app.py
  ├── imports → config (WEB_PASSWORD for auth), hashlib/hmac (password comparison)
  ├── imports → utils/custom_lines.py (LINE_CATEGORIES, add_line, load_custom_lines, remove_line)
  ├── imports → utils/soundboard.py (list_sounds, get_sound_path, SOUNDS_DIR)
  ├── imports → utils/presets.py (list_presets, save_preset, load_preset, delete_preset)
  ├── imports → utils/lyrics.py (get_lyrics)
  ├── @app.before_request → require_login() — session auth guard when WEB_PASSWORD is set
  ├── routes → /login, /logout — password authentication
  ├── routes → /settings — settings page (system info, restart/shutdown)
  ├── routes → /api/restart, /api/shutdown — restart/shutdown endpoints
  ├── calls → cogs/music.py (via bot.get_cog("Music")) for playback state
  └── renders → web/templates/*.html (Jinja2 with custom filters)

utils/dj.py
   ├── uses → utils/soundboard.py (list_sounds — for resolving {sound:name} tags)
   ├── uses → utils/custom_lines.py (load_custom_lines — merges built-in + custom)
   ├── uses → config.py (STATION_NAME, TTS_MODE, KOKORO_TTS_URL, KOKORO_VOICE, VIBEVOICE_TTS_URL)
   └── TTS engines → Kokoro-FastAPI REST (default), VibeVoice-Realtime WebSocket, or edge_tts.Communicate (cloud fallback)
       ├── _generate_tts_kokoro() — POST /v1/audio/speech → WAV, with broken-header rewrite
       ├── _generate_tts_vibevoice() — WebSocket /stream → PCM16 → WAV
       ├── _generate_tts_edge() — edge_tts.Communicate → MP3
       ├── _check_kokoro_health() — GET /v1/audio/voices (cached, 3s timeout)
       └── generate_tts() — routes to active engine, falls back to edge-tts on failure
```

> **Why are `youtube.py` and `logging.py` excluded from auto-loading?**
> `bot.py` line 63 explicitly skips them: `filename != 'youtube.py' and filename != 'logging.py'`.
> - `youtube.py` is a library module (no `setup()` function, no `commands.Cog`), imported directly by `music.py`.
> - `logging.py` sets up its own file handler that conflicts with the root logger configured in `bot.py`. It can be loaded manually if needed but is excluded from auto-loading to prevent double-logging.

---

## 3. Prerequisites & System Requirements

### Hardware

| Resource | Minimum | Recommended |
|---|---|---|
| RAM | 512 MB | 1 GB |
| CPU | 1 core | 2 cores |
| Disk | 500 MB | 1 GB |
| Network | Stable internet | Low-latency connection |

### Software

| Dependency | Version | Purpose |
|---|---|---|
| **Python** | 3.9+ | Runtime (uses `dict \| None` union syntax from 3.10+) |
| **ffmpeg** | Any recent | Audio decoding/encoding for voice streaming |
| **libopus-dev** | — | Opus audio codec required by discord.py voice |
| **screen** | — | Background session management (used by launcher scripts) |
| **git** | — | Cloning the repository |
| **apt-get** | — | System package manager (Debian/Ubuntu only) |

### Target OS

**Primary:** Debian 12 (Bookworm). Both launcher scripts use `apt-get` to install system dependencies (ffmpeg, libopus-dev, screen).

**Other Linux:** Works if you manually install ffmpeg, libopus-dev, and screen.

**macOS/Windows:** Not officially supported. You'd need to install ffmpeg/libopus manually and run `bot.py` directly without the launcher scripts.

### Python Dependencies (`requirements.txt`)

| Package | Version Constraint | Purpose |
|---|---|---|
| `discord.py[voice]` | >=2.4.0 | Discord API wrapper with voice support |
| `yt-dlp` | latest | YouTube video/audio extraction |
| `google-api-python-client` | ==2.110.0 | YouTube Data API v3 (search command) |
| `PyNaCl` | ==1.5.0 | Audio encryption for Discord voice |
| `python-dotenv` | latest | Load `.env` file into environment variables |
| `audioop-lts` | latest | Audio operations (Python 3.13+ compatibility) |
| `aiohttp` | latest | Async HTTP client (Suno, admin cookie fetch, Kokoro TTS, VibeVoice TTS) |
| `edge-tts` | latest | Microsoft Edge TTS — generates DJ voice audio (optional but needed for DJ mode) |
| `flask` | latest | Web dashboard (Mission Control) — serves interactive control panel |
| `psutil` | latest | System/process monitoring — memory & CPU stats on the Settings page |
| `syncedlyrics` | latest | Fetches synced (LRC) lyrics for the currently playing song |
| `pytest` | latest | Test framework |
| `pytest-asyncio` | latest | Async test support for pytest |
| `aioresponses` | latest | Mock aiohttp requests in tests |

---

## 4. Installation & Setup

There are **two launcher scripts** that handle setup. Choose one:

### Method A: `start.sh` (Recommended — Interactive Wizard)

```bash
bash start.sh
```

Running `start.sh` with no arguments (or `run`) performs a **full first-time setup** and starts the bot in the foreground:

1. **Installs system dependencies** — Checks for and installs (via apt-get): python3, python3-pip, python3-venv, ffmpeg, libopus-dev, screen. Uses `sudo` if available.
2. **Creates a Python virtual environment** — `venv/` directory with its own pip.
3. **Installs Python packages** — `pip install -r requirements.txt` + upgrades yt-dlp.
4. **Runs the .env setup wizard** — Interactive prompts for:
   - Discord Bot Token (required)
   - YouTube API Key (optional, press Enter to skip)
   - Log Channel ID (optional, press Enter to skip)
5. **Initializes project structure** — Creates `cogs/__init__.py`, `utils/__init__.py`, `yt_dlp_cache/` directory.
6. **Starts the bot in foreground** — `exec venv/bin/python bot.py`

### Method B: `launch.sh` (Minimal — No Wizard)

```bash
chmod +x launch.sh
./launch.sh setup    # Creates venv, installs deps, creates __init__.py files
```

You **must manually create a `.env` file** before starting:

```bash
cp .env.example .env
nano .env
```

Then start:

```bash
./launch.sh start    # Starts in a screen session named "musicbot"
```

### Comparison of Launcher Scripts

| Feature | `start.sh` | `launch.sh` |
|---|---|---|
| Interactive .env wizard | ✅ Yes | ❌ No |
| Colored terminal output | ✅ Yes | ❌ No |
| Auto-installs Python3 | ✅ Yes | ❌ No |
| Auto-installs screen | ✅ Yes | ❌ No |
| Screen session name | `mbot` | `musicbot` |
| Default run mode | Foreground | Background (screen) |
| `doctor` subcommand | ❌ No | ✅ Yes (runs pytest) |
| `logs` subcommand | ✅ Yes | ❌ (use `attach` instead) |

---

## 5. Configuration Deep Dive

### Environment Variables (`.env` file)

The `.env` file is loaded at the top of `config.py` via `python-dotenv`. If `python-dotenv` is not installed, the code falls back to system environment variables.

```ini
# Required — The bot cannot start without this
DISCORD_TOKEN=your_discord_bot_token

# Optional — Required only for ?search command
YOUTUBE_API_KEY=your_youtube_api_key

# Optional — Discord channel for receiving bot logs
LOG_CHANNEL_ID=1234567890

# Optional — Discord user ID of the bot owner (for admin commands)
BOT_OWNER_ID=24

# Optional — Custom station name for DJ station IDs (default: "MBot")
STATION_NAME=MyAwesomeRadio

# Optional — Web dashboard port (default: 8080)
WEB_PORT=8080

# Optional — Password to protect the web dashboard (default: no password / open access)
# If set, all dashboard pages require login. If blank or unset, dashboard is open to everyone.
WEB_PASSWORD=
```

| Variable | Required? | Source | Used By |
|---|---|---|---|
| `DISCORD_TOKEN` | **Yes** | [Discord Developer Portal](https://discord.com/developers/applications) → Your App → Bot → Token | `bot.py` → `bot.start()` |
| `YOUTUBE_API_KEY` | No (needed for `?search`) | [Google Cloud Console](https://console.cloud.google.com/apis/library/youtube.googleapis.com) | `cogs/music.py` → `search` command |
| `LOG_CHANNEL_ID` | No | Discord: right-click channel → Copy Channel ID (Developer Mode required) | `bot.py` → `DiscordLogHandler` init |
| `BOT_OWNER_ID` | No | Discord: right-click your username → Copy User ID | `cogs/admin.py` → `@commands.is_owner()` |
| `WEB_PASSWORD` | No | Any string you choose | `web/app.py` → `require_login()` before_request guard |
| `OLLAMA_DJ_ENABLED` | No | `"true"` or `"false"` | `config.py` → `utils/llm_dj.py` enables AI side host |
| `OLLAMA_HOST` | No | Any URL | `config.py` → `utils/llm_dj.py` Ollama server address |
| `OLLAMA_MODEL` | No | Any Ollama model name | `config.py` → `utils/llm_dj.py` LLM model for side host |
| `OLLAMA_DJ_CHANCE` | No | `0.0`–`1.0` | `config.py` → frequency of side host chime-ins |
| `OLLAMA_DJ_VOICE` | No | Edge TTS voice name | `config.py` → `cogs/music.py` separate TTS voice for AI host |
| `OLLAMA_DJ_TIMEOUT` | No | Seconds (int) | `config.py` → `utils/llm_dj.py` API call timeout |

### `config.py` Constants

```python
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
COMMAND_PREFIX = "?"
WEB_PASSWORD = os.environ.get("WEB_PASSWORD", "")
OLLAMA_DJ_ENABLED = os.environ.get("OLLAMA_DJ_ENABLED", "false").lower() == "true"
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:latest")
OLLAMA_DJ_CHANCE = float(os.environ.get("OLLAMA_DJ_CHANCE", "0.25"))
OLLAMA_DJ_VOICE = os.environ.get("OLLAMA_DJ_VOICE", "am_adam")
OLLAMA_DJ_TIMEOUT = int(os.environ.get("OLLAMA_DJ_TIMEOUT", "15"))
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", 0) or 0) or None
TTS_MODE = os.environ.get("TTS_MODE", "kokoro").lower()
KOKORO_TTS_URL = os.environ.get("KOKORO_TTS_URL", "http://localhost:8880")
KOKORO_VOICE = os.environ.get("KOKORO_VOICE", "af_heart")
VIBEVOICE_TTS_URL = os.environ.get("VIBEVOICE_TTS_URL", "http://localhost:3000")
```

| Constant | Default | Purpose |
|---|---|---|
| `DISCORD_TOKEN` | None | Bot authentication token |
| `YOUTUBE_API_KEY` | None | YouTube Data API v3 key |
| `COMMAND_PREFIX` | `?` | All commands are prefixed with this character |
| `LOG_CHANNEL_ID` | None | Channel ID for Discord log shipping |
| `WEB_PASSWORD` | `""` | Password for dashboard login (blank = open access) |
| `OLLAMA_DJ_ENABLED` | `false` | Enable AI side host (requires Ollama) |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | `gemma4:latest` | Ollama model for side host lines |
| `OLLAMA_DJ_CHANCE` | `0.25` | Side host chime-in probability (0.0–1.0) |
| `OLLAMA_DJ_VOICE` | `am_adam` | TTS voice for the AI side host (separate from main DJ, Kokoro `am_adam` by default) |
| `OLLAMA_DJ_TIMEOUT` | `15` | Ollama API call timeout in seconds |
| `TTS_MODE` | `kokoro` | TTS engine: `"kokoro"` (local Docker), `"vibevoice"` (WebSocket), or `"edge-tts"` (cloud) |
| `KOKORO_TTS_URL` | `http://localhost:8880` | Kokoro-FastAPI Docker server URL |
| `KOKORO_VOICE` | `af_heart` | Default Kokoro voice |
| `VIBEVOICE_TTS_URL` | `http://localhost:3000` | VibeVoice-Realtime server URL |
| `LOCAL_TTS_URL` | `""` | Backward-compatible alias for older .env files |

### DJ Mode Configuration (`config.py`)

| Constant | Default | Purpose |
|---|---|---|
| `DJ_VOICE` | `af_heart` | Default TTS voice for DJ commentary (Kokoro `af_heart`, VibeVoice `en-Carter_man`, or Edge TTS `en-US-AriaNeural`) |
| `DJ_EMOJI` | 🎙️ | Emoji used in DJ command embeds |
| `STATION_NAME` | From `.env` or `"MBot"` | Station name used in station ID lines ("You're tuned in to {STATION_NAME} Radio") |
| `CROSSFADE_DURATION` | `3` (seconds) | Fade-in duration when a new song starts |

### TTS Engine Configuration (`config.py`)

The DJ mode and AI side host voices can be generated by three TTS engines, controlled by the `TTS_MODE` setting:

| Constant | Default | Purpose |
|---|---|---|
| `TTS_MODE` | `kokoro` | Which TTS engine to use: `"kokoro"`, `"vibevoice"`, or `"edge-tts"` |
| `KOKORO_TTS_URL` | `http://localhost:8880` | Kokoro-FastAPI Docker server URL (only when `TTS_MODE=kokoro`) |
| `KOKORO_VOICE` | `af_heart` | Default Kokoro voice (only when `TTS_MODE=kokoro`) |
| `VIBEVOICE_TTS_URL` | `http://localhost:3000` | VibeVoice-Realtime server URL (only when `TTS_MODE=vibevoice`) |
| `LOCAL_TTS_URL` | `""` | Backward-compatible alias — if set, used as fallback for `KOKORO_TTS_URL` or `VIBEVOICE_TTS_URL` |

```ini
# .env — TTS Engine

# "kokoro" (default) — Kokoro-TTS Docker server (local GPU, ~300ms latency).
# "vibevoice" — VibeVoice-Realtime WebSocket server (local GPU/CPU).
# "edge-tts" — Microsoft Edge TTS (cloud fallback, always available).
TTS_MODE=kokoro

# Kokoro-FastAPI server URL (only used when TTS_MODE=kokoro).
# Start with: docker run --gpus all -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-gpu:v0.2.1
KOKORO_TTS_URL=http://localhost:8880

# Default Kokoro voice. Popular: af_heart, af_bella, am_adam, bf_emma, bm_george
KOKORO_VOICE=af_heart

# VibeVoice server URL (only used when TTS_MODE=vibevoice).
VIBEVOICE_TTS_URL=http://localhost:3000
```

> **Fallback chain:** If the primary engine fails, the bot automatically falls back to edge-tts. For Kokoro, a quick health check (`GET /v1/audio/voices`, 3-second timeout) determines if the Docker container is reachable. If not, the fallback is nearly instant instead of waiting for a long timeout. The health check result is cached (30s for healthy, 10s for down) to avoid hammering the server on every TTS call.

> **Voice name resolution:** The `_resolve_voice()` function detects mismatched voice names (e.g. `en-US-AriaNeural` when using Kokoro) and swaps them for the target engine's default. This means switching `TTS_MODE` doesn't require changing `DJ_VOICE` — the bot adapts automatically.

> **Legacy alias:** `TTS_MODE=local` still works and maps to `vibevoice` with a deprecation warning in the logs. `LOCAL_TTS_URL` is used as a fallback URL if `KOKORO_TTS_URL` or `VIBEVOICE_TTS_URL` aren't set.

> **Note:** DJ mode is off by default and must be enabled per-guild with `?dj`. The `DJ_VOICE` setting is just the default — users can override it per-guild with `?djvoice` or via the web dashboard.

### Kokoro-TTS Setup (Default, Recommended)

Kokoro-TTS is the default engine — it's the most local option with the best latency and no cloud dependency. It runs as a Docker container on your GPU.

1. **Install Docker** with NVIDIA Container Toolkit (for GPU support):
   ```bash
   # Install Docker
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker $USER

   # Install NVIDIA Container Toolkit
   curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
   curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
     sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
     sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
   sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
   sudo nvidia-ctk runtime configure --runtime=docker
   sudo systemctl restart docker
   ```

2. **Start the Kokoro-FastAPI server:**
   ```bash
   # GPU version (recommended):
   docker run --gpus all -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-gpu:v0.2.1

   # CPU version (no GPU, slower):
   docker run -p 8880:8880 ghcr.io/remsky/kokoro-fastapi-cpu:latest
   ```

3. **Verify it's running:**
   ```bash
   curl http://localhost:8880/v1/audio/voices
   # Should return JSON with a "voices" list
   ```

4. **Configure MBot** — in your `.env` file:
   ```ini
   TTS_MODE=kokoro
   KOKORO_TTS_URL=http://localhost:8880
   DJ_VOICE=af_heart
   OLLAMA_DJ_VOICE=am_adam
   ```

5. **Restart the bot.**

**Available Kokoro voices:**

| Voice Name | Description |
|---|---|
| `af_heart` | American Female — Heart (warm) — **default** |
| `af_bella` | American Female — Bella |
| `af_nicole` | American Female — Nicole |
| `af_sarah` | American Female — Sarah |
| `af_sky` | American Female — Sky |
| `am_adam` | American Male — Adam |
| `am_michael` | American Male — Michael |
| `bf_emma` | British Female — Emma |
| `bf_isabella` | British Female — Isabella |
| `bm_george` | British Male — George |
| `bm_lewis` | British Male — Lewis |

> **Voice combos:** The Kokoro-FastAPI server supports voice mixing with `+` syntax, e.g. `af_bella+af_heart` for a 50/50 blend or `af_bella(2)+af_heart(1)` for a 67/33 weighted mix. These work in MBot too — just set `DJ_VOICE=af_bella+af_heart`.

### Kokoro WAV Header Bug (Technical Detail)

The Kokoro-FastAPI server sends WAV files with **streaming-style headers** where the RIFF and data chunk sizes are set to `0xFFFFFFFF` (meaning "unknown size"). This is valid for HTTP streaming but breaks consumers that expect correct sizes.

**What the bot does:** After downloading the WAV from Kokoro, the `_generate_tts_kokoro()` function:
1. Opens the raw bytes with Python's `wave` module in memory.
2. Checks if `nframes` claims more data than what was actually received.
3. If broken: calls `readframes()` to extract the actual PCM data, then rewrites the file with correct chunk sizes using `wave.open(path, 'wb')`.
4. If fine: saves the bytes as-is.

Additionally, FFmpeg options for TTS playback include `-t 30` as a duration cap — even if a header lies, FFmpeg stops after 30 seconds and the `after` callback fires normally.

### VibeVoice-Realtime Setup (Alternative Local TTS)

To use the local TTS engine instead of Microsoft Edge TTS:

1. **Install VibeVoice:**
   ```bash
   git clone https://github.com/microsoft/VibeVoice.git
   cd VibeVoice
   pip install -e .[streamingtts]
   ```

2. **Download the model:**
   The model is automatically downloaded from Hugging Face on first run (`microsoft/VibeVoice-Realtime-0.5B`, ~1 GB).

3. **Start the server:**
   ```bash
   python demo/vibevoice_realtime_demo.py --model_path microsoft/VibeVoice-Realtime-0.5B
   ```
   This starts a FastAPI + WebSocket server on `http://localhost:3000`.

4. **Configure MBot:**
   In your `.env` file:
   ```ini
   TTS_MODE=vibevoice
   VIBEVOICE_TTS_URL=http://localhost:3000
   DJ_VOICE=en-Carter_man
   OLLAMA_DJ_VOICE=en-Journalist_woman
   ```

5. **Restart the bot.**

**Key differences between TTS engines:**

| Feature | Kokoro-TTS (Docker) | VibeVoice-Realtime | Edge TTS (cloud) |
|---|---|---|---|
| Latency | ~300ms first audio | ~300ms first audio | 2-5 seconds |
| Server needed | Docker container (:8880) | WebSocket server (:3000) | No |
| GPU required | Recommended (NVIDIA) | Recommended | No |
| Voice names | `af_heart`, `am_adam`, etc. | `en-Carter_man`, etc. | `en-US-AriaNeural`, etc. |
| Multilingual | English + British | English primary, 9 experimental | 40+ languages |
| Quality | Natural, expressive | Natural, expressive | Natural, consistent |
| Cost | Free (runs locally) | Free (runs locally) | Free (no API key) |
| Internet required | No (after image pull) | No (after model download) | Yes |
| Voice mixing | Yes (`af_bella+af_heart`) | No | No |
| Open source | Yes (Apache 2.0) | Yes (MIT) | No (cloud service) |

**Available VibeVoice voice presets:**
- `en-Carter_man` — Male, warm (default)
- `en-Journalist_woman` — Female, professional
- Plus 9 experimental voices in German, French, Italian, Japanese, Korean, Dutch, Polish, Portuguese, and Spanish (download with `bash demo/download_experimental_voices.sh`)

> **Note:** When any local TTS engine (Kokoro or VibeVoice) is unreachable, MBot will fall back to Edge TTS automatically (if `edge-tts` is installed). For Kokoro, a health check detects failures in ≤3 seconds. This provides graceful degradation — if your local GPU server goes down, the DJ still works, just with higher latency.

### Web Dashboard Configuration (`config.py`)

| Constant | Default | Purpose |
|---|---|---|
| `WEB_HOST` | `0.0.0.0` | Flask server listen address (all interfaces) |
| `WEB_PORT` | From `.env` or `8080` | Flask server listen port |
| `WEB_PASSWORD` | From `.env` or `""` | Password to access the dashboard. If empty/blank, no login is required (open access). If set, all dashboard pages require authentication via the login page. |

> **Security note:** `WEB_PASSWORD` is compared using `hmac.compare_digest()` with SHA-256 hashes to prevent timing attacks. The password is never stored in plaintext in session — only a boolean `authenticated` flag is kept in the Flask session.

### Emoji Configuration (`config.py`)

These emojis are used in embeds throughout the bot for consistent UI:

| Constant | Emoji | Used In |
|---|---|---|
| `PLAY_EMOJI` | ▶️ | Now Playing title, Play/Resume button label |
| `PAUSE_EMOJI` | ⏸️ | Pause button label, pause confirmation |
| `SKIP_EMOJI` | ⏭️ | Skip button label, skip confirmation |
| `QUEUE_EMOJI` | 🎵 | Queue button label, "Song Added" / "Playlist Added" messages |
| `ERROR_EMOJI` | ❌ | Error messages, Stop button label |
| `SUCCESS_EMOJI` | ✅ | Success confirmations |

---

## 6. Entry Point: `bot.py`

`bot.py` is the main entry point. It runs when you execute `python bot.py` (or via the launchers).

### Logging Configuration (Lines 7–18)

```python
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s:%(levelname)s:%(name)s: %(message)s',
    handlers=[
        logging.FileHandler("bot_activity.log"),
        logging.StreamHandler()
    ]
)
```

- **Level:** `INFO` — all INFO, WARNING, ERROR, and CRITICAL messages are captured.
- **Format:** `2026-04-14 12:00:00,123:INFO:cogs.music: Playing Song Title`
- **File handler:** Writes to `bot_activity.log` in the working directory.
- **Stream handler:** Also prints to the console (visible in screen sessions and foreground).

### Discord Intents (Lines 20–22)

```python
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
```

| Intent | Why |
|---|---|
| `default()` | Enables standard events (guilds, messages, reactions, etc.) |
| `message_content` | Required to read message content for command processing |
| `voice_states` | Required to detect users in voice channels and manage voice connections |

> **Important:** You must enable these intents in the Discord Developer Portal under your application's Bot → Privileged Gateway Intents, or the bot will not start.

### Bot Instance (Line 24)

```python
bot = commands.Bot(command_prefix=config.COMMAND_PREFIX, intents=intents)
```

Creates the `commands.Bot` instance with the prefix from `config.py` (default `?`).

### `on_ready` Event (Lines 28–48)

This fires once when the bot successfully connects to Discord.

**1. Logs connection info:**
```python
logging.info(f'Logged in as {bot.user} (ID: {bot.user.id})')
logging.info(f"Intents: {bot.intents}")
```

**2. Cleans up stale voice sessions:**
```python
for guild in bot.guilds:
    if guild.voice_client:
        logging.info(f"Cleaning up stale voice connection in {guild.name}")
        await guild.voice_client.disconnect(force=True)
```

If the bot crashed or was killed without a clean shutdown, it may still appear connected to a voice channel in Discord's state. Discord rejects new connections with error **4006 "Session no longer valid"** if stale sessions exist. This cleanup forces disconnection from all guilds to prevent that error.

**3. Initializes the Discord log handler:**
```python
if config.LOG_CHANNEL_ID and config.LOG_CHANNEL_ID != "YOUR_LOG_CHANNEL_ID":
    discord_log_handler = DiscordLogHandler(bot, config.LOG_CHANNEL_ID)
    logging.getLogger().addHandler(discord_log_handler)
```

If a valid `LOG_CHANNEL_ID` is configured, a `DiscordLogHandler` is attached to the root logger. All future log messages will also be shipped to that Discord channel. If not set, a warning is logged and Discord logging is disabled.

### `main()` Async Function (Lines 50–75)

**1. Creates yt-dlp cache directory:**
```python
cache_dir = "yt_dlp_cache"
if not os.path.exists(cache_dir):
    os.makedirs(cache_dir)
```

While not strictly required for streaming (since `download=False`), `yt-dlp` may use this directory for metadata caching.

**2. Loads cog extensions:**
```python
async with bot:
    for filename in os.listdir('./cogs'):
        if filename.endswith('.py') and filename != '__init__.py' and filename != 'youtube.py' and filename != 'logging.py':
            try:
                await bot.load_extension(f'cogs.{filename[:-3]}')
                logging.info(f'Successfully loaded extension: {filename}')
            except Exception as e:
                logging.error(f'Failed to load extension {filename}: {e}')
```

Scans the `cogs/` directory for `.py` files and loads them as discord.py extensions. **Skips:**
- `__init__.py` — Not a cog
- `youtube.py` — Library module, imported directly by `music.py`, has no `setup()` function
- `logging.py` — Has a `setup()` but is excluded to prevent duplicate log handlers (it adds its own `FileHandler` to the `discord` logger, which would conflict with the root logger's `bot_activity.log`)

Each loaded cog must export an `async def setup(bot)` function at module level.

**3. Starts the bot:**
```python
try:
    await bot.start(config.DISCORD_TOKEN)
except discord.errors.LoginFailure:
    logging.error("Error: Invalid Discord Token. Please check your DISCORD_TOKEN in config.py.")
except Exception as e:
    logging.error(f"Error when starting bot: {e}")
```

Catches `LoginFailure` specifically to give a helpful error message for the most common startup failure.

### Entry Point (Lines 77–81)

```python
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped.")
```

`asyncio.run()` creates a new event loop, runs `main()`, and closes the loop when done. `KeyboardInterrupt` (Ctrl+C) is caught to log a clean shutdown message.

---

## 7. YouTube Source: `cogs/youtube.py`

This module provides the `YTDLSource` class and the FFmpeg/yt-dlp option dictionaries used by `music.py`. It is **not** a discord.py Cog — it has no `setup()` function and is not auto-loaded by `bot.py`.

### `FFMPEG_OPTIONS` (Lines 6–9)

```python
FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}
```

| Key | Value | Purpose |
|---|---|---|
| `before_options` | `-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5` | Tells FFmpeg to automatically reconnect if the stream drops. Max 5 seconds between reconnection attempts. Prevents playback from dying on transient network issues. |
| `options` | `-vn` | "Video None" — strips the video stream, keeping only audio. Reduces bandwidth and CPU usage. |

These options are passed to `discord.FFmpegPCMAudio()`. The `music.py` `play_next` method may append additional options (like `-filter:a "atempo=1.5"` for speed changes) to the `options` key.

### `YTDL_FORMAT_OPTIONS` (Lines 12–29)

```python
YTDL_FORMAT_OPTIONS = {
    "format": "bestaudio*/best",
    "outtmpl": "%(extractor)s-%(id)s-%(title)s.%(ext)s",
    "restrictfilenames": True,
    "noplaylist": True,
    "nocheckcertificate": True,
    "ignoreerrors": False,
    "logtostderr": False,
    "quiet": True,
    "no_warnings": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
    "cookiefile": "youtube_cookie.txt" if __import__('os').path.exists("youtube_cookie.txt") else None,
    "http_headers": {
        "User-Agent": "Mozilla/5.0 ..."
    },
    "extract_flat": "discard_in_playlist",
}
```

| Key | Value | Purpose |
|---|---|---|
| `format` | `bestaudio*/best` | Prefer best audio-only format; fall back to best combined format. The `*` marks preferred format. |
| `outtmpl` | `%(extractor)s-%(id)s-%(title)s.%(ext)s` | Output filename template (not used since `download=False`, but kept for metadata). |
| `restrictfilenames` | `True` | Restricts filenames to ASCII characters only. |
| `noplaylist` | `True` | **Default: treat URLs as single videos, not playlists.** Overridden to `False` by `?playlist` and `?radio` commands. |
| `nocheckcertificate` | `True` | Skips SSL certificate verification (helps with some CDN issues). |
| `ignoreerrors` | `False` | Raises exceptions on extraction errors instead of skipping silently. |
| `logtostderr` / `quiet` / `no_warnings` | `False` / `True` / `True` | Suppresses yt-dlp console output to keep logs clean. |
| `default_search` | `ytsearch` | When a non-URL query is provided, searches YouTube and returns the first result. |
| `source_address` | `0.0.0.0` | Binds to all network interfaces for extraction. |
| `cookiefile` | `"youtube_cookie.txt"` or `None` | If the cookie file exists, yt-dlp uses it. This is critical for age-restricted or member-only content. Set dynamically by `?fetch_and_set_cookies`. |
| `http_headers` | Custom User-Agent |Spoofs a Chrome browser User-Agent to avoid bot detection by YouTube. |
| `extract_flat` | `discard_in_playlist` | When processing playlists, extracts only metadata without resolving each entry fully at first. |

### `YTDLSource` Class (Lines 31–56)

```python
class YTDLSource:
    def __init__(self, data):
        self.data = data
        self.title = data.get("title")
        self.url = data.get("filepath") or data.get("url")
        self.duration = data.get("duration")
        self.thumbnail = data.get("thumbnail")
        self.webpage_url = data.get("webpage_url")
```

**Constructor** — Takes a `data` dictionary (raw yt-dlp extraction output) and extracts the fields the music cog needs:

| Attribute | Source Key | Type | Purpose |
|---|---|---|---|
| `data` | Full dict | dict | Preserves the full yt-dlp extraction output |
| `title` | `title` | str | Song title (displayed in embeds and queue) |
| `url` | `filepath` or `url` | str | The direct audio stream URL that FFmpeg will play |
| `duration` | `duration` | int | Duration in seconds (used for progress bar) |
| `thumbnail` | `thumbnail` | str | Thumbnail URL (displayed in Now Playing embed) |
| `webpage_url` | `webpage_url` | str | The original YouTube page URL (used as embed link) |

> **Why `filepath` OR `url`?** Some yt-dlp extractions return a local `filepath` (when downloaded), while streaming returns a remote `url`. This fallback ensures the source works in both modes.

#### `from_url` Classmethod (Lines 40–56)

```python
@classmethod
async def from_url(cls, url, *, loop=None, ytdl_opts=None):
    loop = loop or asyncio.get_event_loop()
    options = ytdl_opts if ytdl_opts is not None else YTDL_FORMAT_OPTIONS.copy()
    data = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(options).extract_info(url, download=False))
    
    if "entries" in data:
        return [cls(entry) for entry in data["entries"]]
    else:
        return [cls(data)]
```

**This is the core extraction method.** It:

1. **Runs yt-dlp in a thread executor** — `run_in_executor(None, ...)` offloads the blocking `yt_dlp.YoutubeDL.extract_info()` call to a thread pool, preventing it from blocking the async event loop.
2. **Passes `download=False`** — Streams audio instead of downloading files to disk.
3. **Accepts custom `ytdl_opts`** — The `?playlist` and `?radio` commands pass modified options (e.g., `noplaylist: False`, `playlist_items: "1-25"`).
4. **Always returns a list** — Even for single videos, the result is wrapped in a list `[cls(data)]`. This simplifies the consuming code in `music.py` — it always iterates over a list.
5. **Detects playlists** — If the yt-dlp data contains an `"entries"` key, it's a playlist, and each entry becomes a separate `YTDLSource` object.

---

## 8. Music Cog: `cogs/music.py`

The largest and most complex module (785 lines). Handles all music playback, queue management, the Now Playing UI, and button interactions.

### Cog Registration

```python
class Music(commands.Cog):
    def __init__(self, bot):
        ...

async def setup(bot):
    await bot.add_cog(Music(bot))
```

The `setup()` function is called by `bot.load_extension('cogs.music')` in `bot.py`.

### Per-Guild State Dictionaries

The cog tracks state per Discord guild (server) using dictionaries keyed by `guild_id`:

| Dictionary | Key | Value Type | Default | Purpose |
|---|---|---|---|---|
| `song_queues` | `guild_id` | `asyncio.Queue` | Created on demand | The song queue for this guild |
| `search_results` | `guild_id` | `list[tuple]` | — | Cached YouTube search results `[(title, videoId), ...]` |
| `current_song` | `guild_id` | `YTDLSource` / `SunoTrack` | — | The currently playing track object |
| `nowplaying_message` | `guild_id` | `discord.Message` | — | The most recent Now Playing embed message (for editing) |
| `queue_message` | `guild_id` | `discord.Message` | — | (Reserved) Queue display message |
| `playback_speed` | `guild_id` | `float` | `1.0` | Current playback speed multiplier |
| `looping` | `guild_id` | `bool` | `False` | Whether the current song should loop |
| `song_start_time` | `guild_id` | `float` | — | `time.time()` when playback started (for progress bar) |
| `nowplaying_tasks` | `guild_id` | `asyncio.Task` | — | The background task that updates the Now Playing embed |
| `current_volume` | `guild_id` | `float` | `1.0` | Current volume (0.0–2.0, mapped from 0–200%) |
| `inactivity_timers` | `guild_id` | `asyncio.TimerHandle` | — | 60-second timer handle for auto-disconnect |

### Helper Methods

#### `get_queue(guild_id)` (Lines 30–33)

```python
async def get_queue(self, guild_id):
    if guild_id not in self.song_queues:
        self.song_queues[guild_id] = asyncio.Queue()
    return self.song_queues[guild_id]
```

Lazy-initializes the queue for a guild. Called by nearly every command that interacts with the queue.

#### `create_embed(title, description, color, **kwargs)` (Lines 35–39)

```python
def create_embed(self, title, description, color=discord.Color.blurple(), **kwargs):
    embed = discord.Embed(title=title, description=description, color=color)
    for key, value in kwargs.items():
        embed.add_field(name=key, value=value, inline=False)
    return embed
```

Factory method for consistent embeds. Extra keyword arguments become fields. Used throughout the cog for error messages, confirmations, and the Now Playing display.

#### `_get_progress_bar(current_time, total_duration, bar_length=20)` (Lines 41–48)

```python
def _get_progress_bar(self, current_time, total_duration, bar_length=20):
    if total_duration == 0:
        return "━━━━━━━━━━━━"
    progress = (current_time / total_duration)
    filled_length = int(bar_length * progress)
    bar = "━" * filled_length + "●" + "━" * (bar_length - filled_length - 1)
    return bar
```

Generates a text-based progress bar like `━━━━━━●━━━━━━━`. Returns a static empty bar if duration is 0 (e.g., Suno tracks with unknown duration).

#### `_disconnect_if_idle(guild_id)` (Lines 50–56)

Called by the inactivity timer after 60 seconds. Disconnects the bot from the voice channel if nothing is playing.

#### `_start_inactivity_timer(guild_id)` (Lines 58–61)

```python
def _start_inactivity_timer(self, guild_id):
    if guild_id in self.inactivity_timers:
        self.inactivity_timers[guild_id].cancel()
    self.inactivity_timers[guild_id] = self.bot.loop.call_later(
        60, lambda: asyncio.ensure_future(self._disconnect_if_idle(guild_id))
    )
```

Cancels any existing timer, then schedules a new one 60 seconds from now. Uses `call_later` (not `asyncio.sleep`) because this is a non-async callback.

### Commands

---

#### `?join` (Lines 63–75)

**Usage:** `?join`

**Behavior:**
1. Checks if the invoking user is in a voice channel. If not, sends an error embed.
2. If the bot is already in a voice channel, **moves** to the user's channel.
3. If the bot is not in any channel, **connects** to the user's channel.
4. Sends a confirmation embed: `✅ Joined <channel_name>`.

**Edge cases:**
- User not in a voice channel → error
- Bot is in a different channel → moves (no disconnect first)

---

#### `?leave` (Lines 77–92)

**Usage:** `?leave`

**Behavior:**
1. If the bot is connected, disconnects from voice.
2. **Cancels the Now Playing background update task** — This is critical. Without this cancellation, the task would continue running and trying to edit deleted messages, causing log spam.
3. Sends a confirmation embed.

**Edge cases:**
- Bot not in a voice channel → error embed

---

#### `?search` (Lines 94–118)

**Usage:** `?search <query>`

**Behavior:**
1. Checks if `YOUTUBE_API_KEY` is configured. If not, sends an error.
2. Uses the YouTube Data API v3 to search for videos matching the query. Requests up to **10** results, type `"video"` only.
3. Stores results in `self.search_results[guild_id]` as a list of `(title, videoId)` tuples.
4. Displays results as a numbered list embed.
5. Users can then play a specific result with `?play 3` (the number corresponds to the result position).

**API call:**
```python
youtube_service.search().list(
    q=query, part="snippet", maxResults=10, type="video"
).execute()
```

**Edge cases:**
- No `YOUTUBE_API_KEY` → error
- Empty API response → error embed
- No results found → orange "No Results" embed
- API error → error embed with exception details

---

#### `?play` (Lines 120–189)

**Usage:** `?play <URL or search query or search result number>`

This is the most complex command. It handles three input types:

**Input Type 1 — Suno URL:**
If `is_suno_url(query)` returns `True` (from `utils/suno.py`):
1. Calls `get_suno_track(query)` to resolve the Suno song.
2. If the track can't be resolved (private song, invalid URL), sends an error.
3. Adds the `SunoTrack` directly to the queue.

**Input Type 2 — Search result number:**
If `query.isdigit()` and the guild has cached search results:
1. Looks up the `videoId` from the cached search results.
2. Constructs a YouTube URL: `https://www.youtube.com/watch?v={videoId}`
3. Falls through to the YouTube extraction path.

**Input Type 3 — YouTube URL or search query:**
1. Calls `YTDLSource.from_url(url, loop=self.bot.loop)` with default options (no playlist, `default_search: "ytsearch"`).
2. If the result is a list (playlist detected even without `?playlist`), adds all entries to the queue.
3. If it's a single entry, adds it to the queue.
4. Uses `ctx.typing()` to show "Bot is typing..." while extracting.

**Voice connection (after extraction succeeds):**
```python
if not ctx.voice_client:
    await ctx.author.voice.channel.connect(self_deaf=True)
elif not ctx.voice_client.is_connected():
    await ctx.voice_client.disconnect(force=True)
    await asyncio.sleep(0.5)
    await ctx.author.voice.channel.connect(self_deaf=True)
```

The bot **connects after extraction** (not before). This is intentional — it avoids holding an idle voice connection during the potentially slow yt-dlp extraction. `self_deaf=True` makes the bot deafen itself to save bandwidth.

If the voice client exists but is in a disconnected state (e.g., after a network issue), it force-disconnects and reconnects.

**Playback:**
```python
if not ctx.voice_client.is_playing():
    await self.play_next(ctx)
```

If nothing is currently playing, immediately starts playback. If something is already playing, the new song just sits in the queue.

**Error handling:**
- `discord.DiscordServerError` (503) from `ctx.typing()` is caught and ignored — the bot proceeds with playback even if the typing indicator fails.
- Generic exceptions are caught and displayed as error embeds.

---

#### `?playlist` (Lines 192–241)

**Usage:** `?playlist <YouTube playlist URL>`

**Behavior:**
1. Requires the user to be in a voice channel.
2. Connects to voice if not already connected.
3. Overrides yt-dlp options:
   - `noplaylist = False` — enables playlist extraction
   - `playlist_items = "1-25"` — limits to the first **25 songs** in the playlist
4. Calls `YTDLSource.from_url()` with the custom options.
5. Adds all extracted songs to the queue.
6. Starts playback if nothing is currently playing.

**Edge cases:**
- Not a playlist URL / no entries returned → orange "No Playlist Found" embed
- Zero playable songs → orange "No Songs Added" embed

---

#### `?radio` (Lines 244–301)

**Usage:** `?radio <YouTube playlist URL>`

**Behavior:**
Nearly identical to `?playlist`, but:
- Loads up to **100 songs** (`playlist_items = "1-100"`)
- Sends an initial "Loading Radio" message, then deletes it once loading is complete.

Designed for long playlist/radio-style listening sessions.

---

#### `?volume` (Lines 486–502)

**Usage:** `?volume <0–200>`

**Behavior:**
1. Checks if the bot is currently playing. If not → error.
2. Validates the range: 0 to 200 (0% = muted, 100% = normal, 200% = double).
3. Converts to a float: `volume / 100` (so `150` → `1.5`).
4. Sets `ctx.voice_client.source.volume = new_volume_float` (works because the source is a `PCMVolumeTransformer`).
5. Stores the volume in `self.current_volume[guild_id]` so it persists across songs (the next song will be created with this stored volume).

**Edge cases:**
- Not playing → error
- Volume out of range → error

---

#### `?nowplaying` (Lines 504–547)

**Usage:** `?nowplaying`

**Behavior:**
1. Deletes any previous Now Playing message for this guild.
2. If a song is currently playing, creates a detailed embed with:
   - Title (linked to YouTube/Suno page)
   - Progress bar with elapsed/total time
   - Thumbnail
   - Queue size ("X songs remaining")
   - Interactive buttons (Play, Pause, Skip, Stop, Queue)
3. Sends the new embed and stores the message reference in `self.nowplaying_message[guild_id]`.
4. If nothing is playing, sends a "Not Playing" embed.

The background task `_update_nowplaying_message` also calls this logic every 40 seconds to keep the progress bar current.

---

#### `?queue` (Lines 549–559)

**Usage:** `?queue`

**Behavior:**
1. Gets the queue for the guild.
2. If not empty, lists all songs numbered 1 through N.
3. Accesses the internal `_queue` attribute of `asyncio.Queue` via `list(queue._queue)` to iterate without consuming items.

**Edge cases:**
- Empty queue → "Empty Queue" embed

---

#### `?skip` (Lines 561–570)

**Usage:** `?skip`

**Behavior:**
1. Calls `ctx.voice_client.stop()` which stops the current FFmpeg player.
2. The `after` callback on `voice_client.play()` (see `play_next`) fires, which calls `play_next` again to play the next song in the queue.

**Edge cases:**
- Nothing playing → error

---

#### `?stop` (Lines 572–590)

**Usage:** `?stop`

**Behavior:**
1. **Clears the entire queue** by draining all items.
2. Stops the voice client (stops current playback).
3. **Cancels the Now Playing update task**.
4. Clears the bot's Discord presence (removes "Listening to...").
5. Sends a confirmation embed.

---

#### `?pause` (Lines 592–601)

**Usage:** `?pause`

**Behavior:**
1. Pauses the voice client playback.
2. The FFmpeg process is suspended (not killed), so it can be resumed instantly.

**Edge cases:**
- Nothing playing / already paused → error

---

#### `?resume` (Lines 603–620)

**Usage:** `?resume`

**Behavior:**
1. Primary check: `is_paused()` → calls `resume()`
2. Fallback check: `not is_playing() and voice_client.source` → calls `resume()` anyway (handles inconsistent states where Discord reports not paused but a source exists)

**Edge cases:**
- Nothing paused → error
- Not in a voice channel → error

---

#### `?clear` (Lines 622–633)

**Usage:** `?clear`

**Behavior:**
Drains all items from the queue. Unlike `?stop`, this does **not** stop the currently playing song — it only clears queued songs.

**Edge cases:**
- Already empty queue → "Empty Queue" embed

---

#### `?remove` (Lines 637–661)

**Usage:** `?remove <song number>`

**Behavior:**
1. Drains the entire queue into a temporary queue, skipping the item at position `number`.
2. Replaces the guild's queue with the temporary queue.
3. This is necessary because `asyncio.Queue` does not support random access or removal.

**Example:**
```
Queue: [Song A, Song B, Song C, Song D]
?remove 2
Queue: [Song A, Song C, Song D]
```

**Edge cases:**
- Invalid number (out of range) → error
- Number ≤ 0 → error

---

#### `?loop` (Lines 663–670)

**Usage:** `?loop`

**Behavior:**
Toggles the looping flag for the current guild. When looping is enabled, the `_after_playback` callback re-adds the current song to the queue after it finishes, effectively looping it forever.

---

#### `?speedhigher` (Lines 711–720)

**Usage:** `?speedhigher`

**Behavior:**
Moves up one step in the YouTube-style speed ladder: `0.25 → 0.5 → 0.75 → 1.0 → 1.25 → 1.5 → 1.75 → 2.0`.

If already at maximum (2.0x), sends an orange "Speed Limit" embed.

---

#### `?speedlower` (Lines 722–731)

**Usage:** `?speedlower`

**Behavior:**
Moves down one step in the speed ladder. If already at minimum (0.25x), sends an orange "Speed Limit" embed.

---

#### `_set_speed(ctx, new_speed)` (Lines 679–709)

Internal method called by `speedhigher`/`speedlower`:

1. Stores the new speed in `self.playback_speed[guild_id]`.
2. **Stops current playback** (`ctx.voice_client.stop()`).
3. Creates a new FFmpeg player with the `atempo` filter: `-filter:a "atempo=1.5"`.
4. Creates a new `PCMVolumeTransformer` with the stored volume.
5. Starts playback with the new speed.
6. Resets `song_start_time` (the progress bar restarts from 0:00 since FFmpeg doesn't track position across restarts).

**Why restart FFmpeg?** FFmpeg doesn't support changing the `atempo` filter on a running process. The only way to change speed is to stop and recreate the player with new options.

> **Note:** The `atempo` FFmpeg filter supports values between 0.5 and 2.0. Values outside that range require chaining multiple `atempo` filters. The bot's speed ladder stays within 0.25–2.0, but values below 0.5 may cause FFmpeg errors.

---

#### `?shuffle` (Lines 733–754)

**Usage:** `?shuffle`

**Behavior:**
1. Drains the entire queue into a Python list.
2. Calls `random.shuffle()` on the list.
3. Puts all items back into the queue in the new order.

**Edge cases:**
- Empty queue → orange "Empty Queue" embed

---

### Core Playback Method: `play_next(ctx)` (Lines 304–346)

```python
async def play_next(self, ctx):
    queue = await self.get_queue(ctx.guild.id)
    if not queue.empty() and ctx.voice_client:
        data = await queue.get()
        current_speed = self.playback_speed.get(ctx.guild.id, 1.0)
        player_options = FFMPEG_OPTIONS.copy()
        if current_speed != 1.0:
            player_options['options'] += f' -filter:a "atempo={current_speed}"'
        source = discord.FFmpegPCMAudio(data.url, **player_options)
        player = discord.PCMVolumeTransformer(source)
        player.volume = self.current_volume.get(ctx.guild.id, 1.0)
        ctx.voice_client.play(player, after=lambda e: self.bot.loop.create_task(self._after_playback(ctx, e)))
        self.current_song[ctx.guild.id] = data
        self.song_start_time[ctx.guild.id] = time.time()
        await self.bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name=data.title))
        # Start nowplaying update task
        self.nowplaying_tasks[ctx.guild.id] = self.bot.loop.create_task(
            self._update_nowplaying_message(ctx.guild.id, ctx.channel.id)
        )
    else:
        await self.bot.change_presence(activity=None)
        self._start_inactivity_timer(ctx.guild.id)
```

**Step-by-step flow:**

1. **Get next song from queue** — `await queue.get()` removes and returns the next item.
2. **Build FFmpeg options** — Merges base `FFMPEG_OPTIONS` with speed filter if needed.
3. **Create audio source chain:**
   - `FFmpegPCMAudio(data.url)` — Decodes the audio stream.
   - `PCMVolumeTransformer(source)` — Wraps the source with volume control.
4. **Apply stored volume** — Persists volume across songs.
5. **Start playback** — `voice_client.play(player, after=callback)`. The `after` callback fires when the song ends (or errors).
6. **Update state** — Stores current song, start time, and updates bot presence to "Listening to <title>".
7. **Start Now Playing task** — Spawns a background task that updates the Now Playing embed every 40 seconds.
8. **If queue is empty** — Clears presence and starts the 60-second inactivity timer.

**Error recovery:**
If `play_next` itself throws an exception (e.g., FFmpeg can't open the URL), it logs the error, waits 2 seconds, and tries the next song recursively.

---

### After Playback Callback: `_after_playback(ctx, error)` (Lines 461–484)

```python
async def _after_playback(self, ctx, error):
    if error:
        logging.error(f"Player error: {error}")
    
    queue = await self.get_queue(ctx.guild.id)
    
    if self.looping.get(ctx.guild.id):
        current_song_data = self.current_song.get(ctx.guild.id)
        if current_song_data:
            await queue.put(current_song_data)
    
    await self.play_next(ctx)
    
    if queue.empty() and not self.looping.get(ctx.guild.id):
        if ctx.guild.id in self.nowplaying_tasks:
            self.nowplaying_tasks[ctx.guild.id].cancel()
            del self.nowplaying_tasks[ctx.guild.id]
```

**Looping logic:**
When looping is enabled, the current song is re-added to the queue **before** `play_next` is called. So the looped song will be the next one dequeued and played.

**Now Playing task cancellation:**
The Now Playing update task is cancelled when the queue is empty and looping is off. This prevents the task from continuously trying to edit a "Not Playing" message.

---

### Now Playing UI System

The bot maintains a rich, auto-updating Now Playing display with interactive buttons.

#### `_update_nowplaying_message(guild_id, channel_id)` (Lines 348–373)

A **background task** that runs every 40 seconds:

```python
async def _update_nowplaying_message(self, guild_id, channel_id):
    while True:
        # Check bot is still in voice
        guild = self.bot.get_guild(guild_id)
        if not guild or not guild.voice_client:
            break
        
        await self._update_nowplaying_display(guild_id, channel_id, silent_update=True)
        await asyncio.sleep(40)  # Update every 40 seconds
```

- Silently edits the existing Now Playing embed (updates progress bar and time).
- Handles `DiscordServerError` (503) by sleeping 60 seconds before retrying.
- Handles general errors by sleeping 10 seconds before retrying.
- Exits the loop when cancelled or when the bot leaves voice.

**Why 40 seconds?** Discord rate-limits message edits. Updating every 40 seconds provides a reasonably current progress bar without hitting rate limits. The `/60` and `%60` time formatting gives minute:second times.

#### `_update_nowplaying_display(guild_id, channel_id, silent_update)` (Lines 375–459)

Creates or edits the Now Playing embed. Key behaviors:

**When a song is playing:**
1. Builds an embed with title, progress bar, time, thumbnail, and queue size.
2. Creates 5 interactive buttons:
   - ▶️ Play/Resume
   - ⏸️ Pause
   - ⏭️ Skip
   - ❌ Stop
   - 🎵 Queue
3. **Edits the existing message** if one exists (to avoid spamming the channel).
4. **Sends a new message** if the old one was deleted or not found.

**When nothing is playing:**
1. Deletes the previous Now Playing message (if it showed a song).
2. In **silent mode** (background task): Does nothing — avoids sending "Not Playing" messages repeatedly.
3. In **non-silent mode** (user invoked `?nowplaying`): Sends a "Not Playing" embed.

**The embed looks like:**
```
▶️ Now Playing
Rick Astley - Never Gonna Give You Up
━━━━━━━━●━━━━━━ 2:15 / 3:33
Queue: 5 songs remaining
[thumbnail image]

[▶️ Play] [⏸️ Pause] [⏭️ Skip] [❌ Stop] [🎵 Queue]
```

---

### Button Interaction Handler: `on_interaction` (Lines 756–782)

```python
@commands.Cog.listener()
async def on_interaction(self, interaction):
    if interaction.type == discord.InteractionType.component:
        custom_id = interaction.data["custom_id"]
        # Route to appropriate command
        if custom_id == "play":
            await self.resume(ctx)
        elif custom_id == "pause":
            await self.pause(ctx)
        elif custom_id == "skip":
            await self.skip(ctx)
        elif custom_id == "stop":
            await self.stop(ctx)
        elif custom_id == "queue":
            # Send queue as ephemeral message
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        await interaction.response.defer()
```

Listens for button clicks on the Now Playing embed. Most buttons delegate to existing commands. The **Queue** button is special — it sends the queue list as an **ephemeral** message (only visible to the clicking user) instead of posting it in the channel.

---

## 9. DJ Mode: `utils/dj.py` + Music Cog Integration

### What It Does

When DJ mode is enabled, the bot speaks between songs like a real radio DJ:
- **Before the first song in a session**: Intro — announces the song title.
- **Between songs (transition)**: Outro + intro — names the song that just finished and the one coming up.
- **When the queue ends**: Final outro — announces the last song and that the queue is empty.
- **Random station IDs**: ~20% of intros get a "You're listening to MBot Radio" tagline.

### How It Works

```
User: ?play Never Gonna Give You Up
  → DJ mode ON?
    → YES: Generate TTS "Up next, Never Gonna Give You Up!"
      → Play TTS audio in voice channel
      → When TTS finishes → play the actual song
    → NO: Play song directly (no change)

Song finishes → _after_playback → play_next
  → DJ mode ON? + previous song exists?
    → YES: Generate TTS "That was Yesterday. Coming up next, Billie Jean!"
      → Play TTS, then the actual song
    → NO: Play next song directly

Queue empty?
  → DJ mode ON? + last song exists?
    → YES: Generate TTS "That was Never Gonna Give You Up, and that's all for now!"
    → NO: Just clear presence and start inactivity timer
```

### Technology

**Text-to-Speech**: [Microsoft Edge TTS](https://github.com/rany2/edge-tts) (`edge-tts` Python package) — free, no API key required, 100+ voices in 40+ languages.

**Flow**:
1. `generate_intro()` or `generate_outro()` picks a random message template and fills in song titles.
2. `edge_tts.Communicate(text, voice)` generates an MP3 file to a temp path.
3. `FFmpegPCMAudio` plays the temp MP3 through the voice channel.
4. The `after` callback on the TTS player triggers `_on_tts_done()`, which cleans up the temp file and calls `_play_song_after_dj()` to start the real song.
5. `cleanup_tts_file()` deletes the temp MP3.

### Per-Guild State

| Dictionary | Key | Value | Purpose |
|---|---|---|---|
| `dj_enabled` | `guild_id` | `bool` | Whether DJ mode is on for this guild |
| `dj_voice` | `guild_id` | `str` | Edge TTS voice name (default: `en-US-AriaNeural`) |
| `dj_playing_tts` | `guild_id` | `bool` | Whether a TTS intro is currently playing (prevents re-entrance) |
| `_current_tts_path` | `guild_id` | `str\|None` | Path to the temp MP3 file (for cleanup) |
| `_dj_pending` | `guild_id` | `(ctx, data, channel_id)` | Held song data waiting for TTS intro to finish |

### Message Templates

The DJ has **172 built-in line templates** across 10 categories. 74 of these include `{sound:name}` tags that trigger sound effects after the DJ speaks.

| Category | Count | With Sound Tags | Example |
|---|---|---|---|
| **Session Intros** | 23 | 11 | `"{greeting} We are LIVE! Let's kick it off with {title}. {sound:airhorn}"` |
| **Song Intros** | 19 | 5 | `"Up next, {title}!", "Incoming! {title}! {sound:airhorn}"` |
| **Hype Intros (Loud)** | 18 | 9 | `"YES! {title}! Let's go!", "Buckle up! {title}! {sound:air_raid}"` |
| **Outros** | 13 | 5 | `"That was {title}.", "{title} — done and dusted. {sound:button_press}"` |
| **Transitions** | 23 | 8 | `"That was {prev_title}. Next up, {next_title}.", "From {prev_title} to {next_title}. In the mix! {sound:in_the_mix}"` |
| **Hype Transitions** | 12 | 7 | `"That was {prev_title}! And NOW — {next_title}! {sound:airhorn} LET'S GO!"` |
| **Mellow Transitions** | 9 | 4 | `"Lovely track. Here's {next_title} to keep the vibe going. {sound:dj_scratch}"` |
| **Final Outros** | 17 | 7 | `"The queue's empty but the radio stays on.", "End of the road. {sound:applause}"` |
| **Station IDs** | 18 | 8 | `"You're tuned in to MBot Radio.", "MBot Radio — on the air! {sound:air_raid}"` |
| **Listener Callouts** | 20 | 10 | `"Shoutout to everyone listening right now.", "You guys are the best! {sound:applause}"` |
| **Queue Banter** | 10+ | — | `"One more left in the queue.", "We are in it for the long haul, 20 more tracks!"` |

### Sound Tags in DJ Lines (`{sound:name}`)

Any DJ line can include a `{sound:name}` tag. When the DJ speaks that line, the text before the tag is spoken via TTS, then the sound effect plays in the voice channel after the TTS finishes.

**Example:** `"In the mix! {sound:airhorn}"` → DJ says "In the mix!" → then the airhorn sound plays.

**How it works:**
1. `_format_line(template, **kwargs)` — Strips `{sound:...}` tags before calling `str.format()`, then re-appends them. This prevents `KeyError: 'sound'` from Python's format method.
2. `extract_sound_tags(text)` — Called by `_dj_speak()`. Returns `(cleaned_text, [sound_ids])`. The cleaned text goes to TTS; the sound IDs are queued to play after TTS finishes.
3. `_on_tts_done()` — Plays any pending sound effects from `{sound:name}` tags, then starts the next song.

**Available sounds** (filenames in `sounds/` directory, referenced without extension):

| Sound Tag | File | Description |
|---|---|---|
| `{sound:airhorn}` | `airhorn.wav` | Classic airhorn blast |
| `{sound:air_raid}` | `air_raid.wav` | Siren/alarm |
| `{sound:applause}` | `applause.wav` | Crowd applause |
| `{sound:button_press}` | `button_press.wav` | UI button click |
| `{sound:club_hit}` | `club_hit.wav` | Bass drop |
| `{sound:dj_drop}` | `dj_drop.wav` | DJ drop/station ID effect |
| `{sound:in_the_mix}` | `in_the_mix.wav` | Transition effect |
| `{sound:record_scratch}` | `record_scratch.wav` | Vinyl scratch |
| `{sound:dj_scratch}` | `dj_scratch.wav` | Turntable spin-up |

> **Important:** Station IDs (which use Python f-strings for `{config.STATION_NAME}`) must use **doubled braces** for sound tags: `{{sound:dj_drop}}` produces the literal `{sound:dj_drop}` at runtime.

### Time-Aware Behavior

The DJ adjusts its personality based on the time of day:

| Time | Greeting Style | Transition Style |
|---|---|---|
| **Morning** (5–12) | "Rise and shine!", "Top of the morning to ya!" | Normal energy |
| **Afternoon** (12–17) | "Good afternoon!", "Hope your afternoon's going well!" | Normal energy |
| **Evening** (17–21) | "Good evening, everyone!", "Perfect time for some tunes." | Normal energy |
| **Night** (21–23) | "Evening, night owls!", "Night crew, you're in the right place." | 35% chance of mellow transitions |
| **Late Night** (23–5) | "Late night crew, I see you!", "Burning the midnight oil?" | 40% chance of mellow transitions |

### Available Voices

**Edge TTS (cloud, default):** The default voice is `en-US-AriaNeural` (female, American English). Use `?djvoices` to see all English voices, or `?djvoices <prefix>` for other languages (e.g., `?djvoices ja` for Japanese).

Popular Edge TTS voices include:
- `en-US-AriaNeural` — Female, American (default)
- `en-US-GuyNeural` — Male, American
- `en-GB-SoniaNeural` — Female, British
- `en-AU-NatashaNeural` — Female, Australian
- `ja-JP-NanamiNeural` — Female, Japanese

**VibeVoice-Realtime (local TTS, when `TTS_MODE=local`):** Voice presets are loaded from the VibeVoice server's `/config` endpoint. Available voices depend on which `.pt` preset files are in the `demo/voices/streaming_model/` directory.

Default VibeVoice voice presets include:
- `en-Carter_man` — Male, warm (default)
- `en-Journalist_woman` — Female, professional

Additional experimental voices (9 languages, downloadable separately):
- `de-Anna_woman`, `fr-Margot_woman`, `it-Luca_man`, `jp-Yuki_woman`, `kr-Sunny_woman`, `nl-Nora_woman`, `pl-Aleksander_man`, `pt-Rita_woman`, `es-Mateo_man`

The web dashboard Radio page automatically shows the correct voice list based on the active TTS engine and clearly labels which engine is in use.

### Skip/Stop Behavior

- **`?skip`** during a DJ intro: Cancels the TTS, cleans up the temp file, and skips the pending song so the next song in the queue starts immediately (with its own DJ intro if applicable).
- **`?stop`**: Cancels any ongoing TTS, clears the queue, and stops playback. No outro is spoken.
- **`?leave`**: Cleans up all DJ state for the guild.

### Graceful Degradation

If `edge-tts` is **not installed**, DJ mode degrades cleanly:
- `?dj` returns an error embed explaining how to install `edge-tts`.
- `?djvoice` and `?djvoices` also return an error.
- Songs play normally without any TTS commentary.
- The `EDGE_TTS_AVAILABLE` flag in `utils/dj.py` controls this behavior.
- No crash, no hang, no broken audio — the bot simply falls through to direct playback.

### Configuration

Add to `config.py` or `.env`:
```python
DJ_VOICE = "en-US-AriaNeural"  # Default voice
DJ_EMOJI = '🎙️'                # Emoji for DJ command embeds
```

---

## 10. AI Side Host: `utils/llm_dj.py` — The Studio Joker

A second radio personality powered by a local LLM (Ollama). Unlike the main DJ that picks from 172 pre-written templates, the AI side host **writes its own original lines from scratch** — spontaneous banter, hot takes, song roasts, jokes, and commentary that a template system simply cannot produce.

### Concept

Think of it like a real radio show with two hosts:
- **Main DJ** (template-based) — handles the structured moments: "That was *Bohemian Rhapsody*. Up next, *Don't Stop Believin'*."
- **AI Side Host** (Ollama-powered) — the studio joker who chimes in randomly: "Rhapsody? More like Rhapsno-dy! ...I'll see myself out. {sound:record_scratch}"

The side host uses its **own TTS voice** (configurable via `OLLAMA_DJ_VOICE`) so listeners hear two distinct personalities. The main DJ always speaks first (structured intro/transition/outro), and then the side host randomly drops in with unstructured banter.

### How It Works

```
Song ends → Main DJ speaks template line (intro/transition/outro)
              │
              ├─ ai_dj_enabled + should_side_host_speak() → True?
              │    ├─ Yes → generate_side_host_line() → Ollama generates original banter
              │    │         ├─ Ollama responds in time → Side host speaks with own voice → Song plays
              │    │         └─ Timeout/error → Skip side host, song plays immediately
              │    └─ No → Song plays immediately
              │
              └─ Song plays
```

### Banter Categories

The side host picks a random category each time it speaks. When the main DJ just spoke (a DJ line is available), the side host **prefers reactive categories** (60% chance) so it responds to what was said.

#### Independent Categories (unstructured banter)

| Category | Description | Example Output |
|---|---|---|
| `random_thought` | A random funny observation | "Is it just me or does every queue end up being 80% songs from 2009?" |
| `listener_shoutout` | Hype up the listeners | "Three people listening right now and all of them have great taste!" |
| `song_roast` | Gently roast the current/next song | "This one? Oh we're going BACK to the 80s. No complaints. {sound:dj_scratch}" |
| `station_trivia` | Fun (or fake) station facts | "Fun fact: this station has been running for 847 years. Don't check that." |
| `queue_hype` | Hype up the queue with comedy | "20 songs in the queue! We're in it for the long haul, people." |
| `vibe_check` | Rate the current mood | "Vibes at 73%. Room for improvement. Let's fix that." |
| `hot_take` | Spicy but harmless music opinion | "Unpopular opinion: the best album of all time is the Frozen soundtrack." |
| `request_prompt` | Funny reminder to request songs | "Taking requests! Please. The queue is looking thin and my job depends on it." |

#### Reactive Categories (respond to the main DJ) — *New in 6.3.0*

When a DJ line is available, the side host preferentially picks from these categories to create two-host chemistry:

| Category | What the AI Does | Example (after DJ says "Great track coming up") |
|---|---|---|
| `react_agree` | Agree + add a funny twist or exaggeration | "Yeah! And it only took us 3 songs to find a good one." |
| `react_disagree` | Playfully disagree or offer a cheeky alternative | "Debatable. I've heard better from a microwave." |
| `react_one_up` | Escalate the joke with something wilder/absurd | "That's cute, but wait'll you hear what's next." |
| `react_tangent` | Go off on a funny tangent triggered by what was said | "Speaking of great, did I mention we run 24/7? No sleep. Just vibes." |

**Smart category selection:** When a DJ line is provided, 60% chance of a reactive category, 40% chance of independent banter (for variety). When no DJ line is available (e.g., first song of session), only independent categories are used.

### DJ Context Awareness — *New in 6.3.0*

The AI side host now receives the **main DJ's spoken line** as context when generating its banter. This means the two hosts can actually interact like a real radio duo, rather than the AI talking over the DJ with unrelated jokes.

**Data flow:**
```
Main DJ speaks: "Good afternoon! We're starting with Loving U."
   ↓
_dj_speak() stores clean text → self._last_dj_line[guild_id]
   ↓
_play_song_after_dj() reads _last_dj_line → passes to _try_ai_side_host(dj_line=...)
   ↓
generate_side_host_line(dj_line="Good afternoon! We're starting with Loving U.")
   ↓
_build_user_prompt includes: Main DJ just said: "Good afternoon! We're starting with Loving U."
   ↓
Ollama generates reactive banter: "Understatement of the century, but sure. {sound:airhorn}"
```

**Key implementation details:**
- `_dj_speak()` accepts an `is_ai` flag — only the main DJ's lines are stored in `_last_dj_line`. When the AI side host calls `_dj_speak()` with `is_ai=True`, it doesn't overwrite the main DJ's line, preventing context contamination.
- The DJ line is stripped of `{sound:name}` tags before being passed to Ollama (the AI sees the actual spoken words, not the sound tags).
- The system prompt includes a "REACTING TO THE MAIN DJ" section instructing the model to respond to the DJ's line without repeating or paraphrasing it.

### System Prompt

The AI side host is defined by a detailed system prompt that:
- Sets the personality: "the studio joker — the co-host who cracks jokes, drops hot takes, roasts the music, and says the things the main DJ is too professional to say"
- Enforces format constraints: max 150 chars, contractions, short and punchy
- Instructs the model to include `{sound:name}` tags (which flow through the existing `extract_sound_tags()` pipeline)
- Prevents meta-commentary, quotes, or off-brand behavior
- Enforces family-friendly output

### Configuration

```ini
# .env
OLLAMA_DJ_ENABLED=true       # Enable the AI side host
OLLAMA_HOST=http://localhost:11434  # Ollama server URL
OLLAMA_MODEL=gemma4:latest    # Model to use (must be pulled first)
OLLAMA_DJ_CHANCE=0.25         # 25% chance to chime in after each DJ line
OLLAMA_DJ_VOICE=en-US-GuyNeural  # Separate TTS voice for the side host
OLLAMA_DJ_TIMEOUT=15          # Timeout in seconds (larger models need more time)
```

| Setting | Default | Values | Purpose |
|---|---|---|---|
| `OLLAMA_DJ_ENABLED` | `false` | `true`/`false` | Master switch for AI side host |
| `OLLAMA_HOST` | `http://localhost:11434` | Any URL | Ollama server address |
| `OLLAMA_MODEL` | `gemma4:latest` | Any pulled model | LLM model for generation |
| `OLLAMA_DJ_CHANCE` | `0.25` | `0.0`–`1.0` | How often the side host chimes in |
| `OLLAMA_DJ_VOICE` | `en-US-GuyNeural` | Edge TTS voice name | Separate voice so 2 hosts sound different |
| `OLLAMA_DJ_TIMEOUT` | `15` | Seconds | Max wait before falling back |

### Voice Configuration

The main DJ and AI side host have **separate TTS voices** to create two distinct on-air personalities:

| Host | Default Voice | Config Key | Discord Command | Web Dashboard |
|---|---|---|---|---|
| Main DJ | `en-US-AriaNeural` (female) | `DJ_VOICE` | `?djvoice <name>` | Radio page → "🗣️ DJ Voice" |
| AI Side Host | `en-US-GuyNeural` (male) | `OLLAMA_DJ_VOICE` | `?aidjvoice <name>` | Radio page → "🃏 AI Side Host Voice" |

Use `?djvoices` to see all available voices.

### Discord Commands

| Command | Usage | Description |
|---|---|---|
| `?aidj` | `?aidj` | Toggle the AI side host on/off for this server |
| `?aidjvoice` | `?aidjvoice` | Show the current AI side host voice |
| `?aidjvoice` | `?aidjvoice <name>` | Set the AI side host's TTS voice |

### Web Dashboard Controls

- **🃏 AI On/Off button** — Toggle button on each guild card (next to 🎙️ DJ and 🔁 Auto)
- **🃏 AI badge** — Purple badge shown when AI side host is active
- **AI Side Host Voice selector** — On the Radio page, visible when AI is enabled
- **Ollama Status** — On the Settings page, shows server connectivity, model availability, and setup instructions

### Per-Guild State

| Dictionary | Key | Value | Purpose |
|---|---|---|---|
| `ai_dj_enabled` | `guild_id` | `bool` | Whether the AI side host is on for this guild |
| `ai_dj_voice` | `guild_id` | `str` | Edge TTS voice name for the side host (override) |
| `_last_dj_line` | `guild_id` | `str` | What the main DJ just said (for AI reactive context). Not overwritten when the AI itself speaks. |

### Graceful Degradation

Like the main DJ, the AI side host degrades cleanly:

- **Ollama not running** → Side host is skipped, main DJ works normally. No crash, no hang, no dead air.
- **Ollama times out** → Side host is skipped. The configurable timeout ensures no perceptible delay.
- **`OLLAMA_DJ_ENABLED=false`** → Module never loads. Zero overhead.
- **`edge-tts` not installed** → Both DJs are unavailable. Songs play normally.
- **Model not pulled (404)** → Actionable log message with model name, pull command, and list of available models. Example: `Model 'llama3.2' not found (Ollama 404). Run: ollama pull llama3.2 | Available models: gemma4:latest`
- **Model not pulled** → `?aidj` shows setup instructions with the pull command.
- **Invalid `{sound:name}` tags** → `extract_sound_tags()` silently strips unknown sounds.

### Recommended Models

| Model | Size | Speed | Quality | Notes |
|---|---|---|---|---|
| `gemma4:latest` | 9.6 GB | Moderate (~2s) | Excellent | **Default.** Best quality for reactive banter. Needs longer timeout. |
| `phi3:mini` | 2.3 GB | Fast (~1s) | Good | Compact, good for short banter |
| `llama3.2:3b` | 2 GB | Fast (~1s) | Good | Good balance for real-time radio |
| `gemma2:2b` | 1.4 GB | Very fast (~0.5s) | Decent | Fastest, good for short banter |
| `mistral:7b` | 4.1 GB | Slower (~3s) | Great | May hit timeout on slower hardware |

Pull before use: `ollama pull gemma4:latest`

---

## 11. Admin Cog: `cogs/admin.py`

Owner-only commands for bot management. All commands use `@commands.is_owner()` which checks `BOT_OWNER_ID` (or the bot's application owner in the Discord Developer Portal).

### `?fetch_and_set_cookies` (Lines 15–89)

**Usage:** `?fetch_and_set_cookies <HTTPS URL>`

**Purpose:** Fetches cookies from a URL and saves them to `youtube_cookie.txt` for yt-dlp. This is needed when YouTube requires authentication (age-restricted content, member-only videos).

**Flow:**
1. Validates the URL starts with `https://`.
2. Makes an HTTP GET request to the URL using `aiohttp`.
3. Extracts all `Set-Cookie` headers from the response.
4. Parses each cookie header using `cookie_parser.parse_all_cookies()`.
5. For each cookie, extracts:
   - **Domain** — from `Domain=` attribute (or empty)
   - **Path** — from `Path=` attribute (default `/`)
   - **Secure** — `TRUE` if `Secure` flag present
   - **Expiration** — parsed from `Expires=` attribute and converted to Unix timestamp
   - **Flag** — `TRUE` if domain starts with `.` (indicates subdomain inclusion)
6. Writes all cookies in **Netscape cookie file format**:
   ```
   # Netscape HTTP Cookie File
   .youtube.com	TRUE	/	TRUE	1234567890	VISITOR_INFO1_LIVE	abc123
   ```
7. Updates `YTDL_FORMAT_OPTIONS["cookiefile"] = "youtube_cookie.txt"` so future yt-dlp extractions use the cookies.

**Netscape cookie file format:**
```
domain\tflag\tpath\tsecure\texpiration\tname\tvalue
```

---

### `?shutdown` (Lines 91–98)

**Usage:** `?shutdown`

**Behavior:** Sends a confirmation embed, then calls `bot.close()` which gracefully disconnects from Discord and stops the event loop.

---

### `?restart` (Lines 100–107)

**Usage:** `?restart`

**Behavior:** Same as shutdown, but intended to be used with a process manager (like systemd or the launcher scripts) that auto-restarts the bot when it exits.

> **Note:** This does **not** actually restart the Python process. It only closes the Discord connection. The bot process exits, and an external supervisor (like `screen` + `launch.sh restart`) must restart it.

---

## 12. Logging Cog: `cogs/logging.py`

**Not auto-loaded** by `bot.py` (explicitly excluded on line 63). Can be loaded manually if needed.

### Listeners

| Event | Action |
|---|---|
| `on_message` | Logs `Message from <author>: <content>` (skips bot messages) |
| `on_command` | Logs `Command '<name>' invoked by <author>` |
| `on_command_error` | Logs `Command '<name>' raised an error: <error>` |

### File Handler

Creates its own `FileHandler` writing to `bot_activity.log` in write mode (`mode='w'`), which **overwrites** the file on each cog load. This is why it's excluded from auto-loading — it would conflict with the root logger's `bot_activity.log` handler (which uses append mode).

---

## 13. Utility Modules

### `utils/discord_log_handler.py` — DiscordLogHandler

A custom Python `logging.Handler` that ships log messages to a Discord text channel. Also powers the Mission Control Activity Log Panel via an in-memory ring buffer.

#### Module-level: `log_buffer`

```python
log_buffer = deque(maxlen=200)
```

A thread-safe ring buffer that stores the last 200 log entries as structured dicts (`timestamp`, `level`, `message`, `created`). Flask reads from this buffer via `/api/logs/recent` and `/api/logs/stream`. Every `emit()` call appends to this buffer alongside the existing Discord flush.

#### Class: `DiscordLogHandler`

```python
class DiscordLogHandler(logging.Handler):
    def __init__(self, bot_instance, log_channel_id, level=logging.INFO):
        super().__init__(level)
        self.bot = bot_instance
        self.log_channel_id = log_channel_id
        self.queue = asyncio.Queue()
        self.task = None
        self.buffer = []
        self.buffer_lock = asyncio.Lock()
        self.flush_interval = 5  # seconds
```

| Attribute | Purpose |
|---|---|
| `bot` | The discord.py Bot instance (used to get the channel) |
| `log_channel_id` | The Discord channel ID to send logs to |
| `buffer` | A list that accumulates log messages between flushes |
| `buffer_lock` | An asyncio Lock to prevent concurrent buffer access |
| `flush_interval` | How often (seconds) to send buffered messages to Discord |

#### How it works

1. **`emit(record)`** — Called by the logging framework for each log message. Formats the record and appends it to `self.buffer`. If no flush task is running, starts one.
2. **`flush_buffer()`** — Waits `flush_interval` seconds (5s), then:
   - Takes all buffered messages
   - Joins them with newlines
   - Splits into 1900-character chunks (Discord's message limit is 2000, with markdown code block overhead)
   - Sends each chunk as a code block: ` ```\n<log text>\n``` `
   - If the bot isn't ready, puts messages back in the buffer for later

**Why buffered?** Sending each log message individually would rapidly hit Discord's rate limits. Buffering for 5 seconds and sending in bulk reduces API calls by ~95%.

**Why 1900-character chunks?** Discord has a 2000-character message limit. Wrapping in a code block adds `6` characters (` ``` ` × 2 + newlines), so 1900 is a safe ceiling.

---

### `utils/suno.py` — Suno Integration

Resolves Suno.com song URLs into playable audio tracks.

#### Supported URL Patterns

```
https://suno.com/song/<uuid>
https://app.suno.ai/song/<uuid>
```

Where `<uuid>` is a 36-character UUID like `0b65f620-32b0-40db-b09a-e455e3adb2c9`.

#### Regex: `_SUNO_RE`

```python
_SUNO_RE = re.compile(
    r'https?://(?:app\.suno\.ai|suno\.com)/song/([0-9a-f-]{36})',
    re.IGNORECASE
)
```

Matches both `suno.com` and `app.suno.ai` domains, captures the UUID.

#### `is_suno_url(text)`

Returns `True` if the text contains a Suno song URL. Used by `music.py`'s `?play` command to route Suno URLs to the Suno handler instead of yt-dlp.

#### `_extract_song_id(url)`

Returns the UUID from a Suno URL, or `None` if not found.

#### Class: `SunoTrack`

```python
class SunoTrack:
    def __init__(self, song_id, title, thumbnail, webpage_url):
        self.song_id = song_id
        self.title = title
        self.url = f"{CDN_BASE}/{song_id}.mp3"  # https://cdn1.suno.ai/<uuid>.mp3
        self.duration = 0       # Unknown — progress bar shows 0:00/0:00
        self.thumbnail = thumbnail
        self.webpage_url = webpage_url
```

Compatible with `YTDLSource` — has the same attributes (`title`, `url`, `duration`, `thumbnail`, `webpage_url`). This allows `music.py` to treat Suno tracks and YouTube tracks identically.

**The audio URL** is constructed as `https://cdn1.suno.ai/<song_id>.mp3`. This is Suno's CDN URL for the MP3 audio file.

**Duration is 0** — Suno doesn't provide duration in the page HTML, so the progress bar can't be calculated.

#### `get_suno_track(url)` — Async

**Flow:**
1. Extracts the song UUID from the URL.
2. Fetches the Suno song page (`https://suno.com/song/<uuid>`).
3. Scrapes OpenGraph meta tags from the HTML:
   - `og:title` → song title (fallback: `<title>` tag)
   - `og:image` → thumbnail URL
4. Performs a **HEAD request** to the CDN URL to verify the MP3 is accessible (HTTP 200 or 206).
5. If the CDN returns a non-200/206 status, returns `None` (song may be private or deleted).
6. Returns a `SunoTrack` object.

**Error handling:**
- Could not extract song ID → `None`
- HTTP error fetching page → `None`
- CDN HEAD check failed → `None`
- Network error → `None`

---

### `utils/cookie_parser.py` — Cookie Parsing & Log File Parsing

This module has **two unrelated functionalities**:

#### 1. `parse_all_cookies(header)` — USED BUT NOT DEFINED ⚠️

The function `cookie_parser.parse_all_cookies(header)` is called in `cogs/admin.py` (line 42):
```python
from utils import cookie_parser
# ...
parsed_cookies = cookie_parser.parse_all_cookies(header)
```

However, **`parse_all_cookies` is NOT defined anywhere in this file.** The file only contains `parse_log_entry()` and `parse_log_file()` — both for log parsing, not cookie parsing. This means the `?fetch_and_set_cookies` command will raise an `AttributeError: module 'utils.cookie_parser' has no attribute 'parse_all_cookies'` at runtime when it tries to parse cookies.

**To fix this bug**, you would need to implement `parse_all_cookies()` in `cookie_parser.py` — a function that takes a `Set-Cookie` header string and returns a dictionary of `{name: value}` pairs.

#### 2. Log File Parsing — Standalone utility (actually defined in the file)

| Function | Purpose |
|---|---|
| `parse_log_entry(log_line)` | Parses a single log line into a dict with keys: `timestamp`, `datetime`, `level`, `name`, `message` |
| `parse_log_file(file_path)` | Reads a log file and returns a list of parsed entries |

**Log format expected:**
```
2026-04-14 12:00:00,123:INFO:cogs.music: Playing Song Title
```

**`main()` function:**
When run directly, this script parses two hard-coded log file paths from `/root/.local/bot6/` and prints all ERROR/WARNING entries. This is a diagnostic utility for the bot operator.

---

### `utils/import_parser.py` — Log Parsing Utility

Nearly identical to `cookie_parser.py`'s log parsing functionality, but:
- Has a cleaner `parse_log_file()` that handles `FileNotFoundError` explicitly.
- Includes a `__main__` block that creates a dummy log file, parses it, and cleans up — useful as a self-test.

**Purpose:** This is likely a refactored version of the log parser or a second instance used by a different part of the system. Both files have the same `parse_log_entry()` and `parse_log_file()` functions with minor implementation differences.

---

### `utils/soundboard.py` — Soundboard System

Manages the `sounds/` directory and provides sound listing/path resolution.

| Function | Purpose |
|---|---|
| `list_sounds()` | Scans `sounds/` for audio files, returns `[{id, name, file}, ...]` |
| `get_sound_path(sound_id)` | Returns file path for a sound ID (with directory traversal prevention via `os.path.basename()`) |
| `create_default_sounds()` | Creates a `README.txt` in `sounds/` explaining where to drop files |
| `SOUNDS_DIR` | Constant: `"sounds"` (relative to bot working directory) |

---

### `utils/custom_lines.py` — Custom DJ Line Persistence

Stores user-added DJ lines in `dj_custom_lines.json` (same directory as bot.py).

| Function | Purpose |
|---|---|
| `load_custom_lines()` | Load from JSON → `{category: [lines]}` |
| `save_custom_lines(lines)` | Write to JSON |
| `add_line(category, line)` | Append a line to a category |
| `remove_line(category, index)` | Remove a specific custom line by index |
| `LINE_CATEGORIES` | List of valid category keys |
| `CATEGORY_LABELS` | Friendly labels (`"intros"` → `"Session Intros"`) |
| `CATEGORY_PLACEHOLDERS` | Available placeholders per category (all include `{sound:name}`) |

---

### `utils/llm_dj.py` — AI Side Host (Ollama)

The AI side host — the studio joker. Writes its own original DJ banter by calling a local LLM via Ollama's HTTP API.

| Function | Purpose |
|---|---|
| `generate_side_host_line(...)` | Generate an original DJ line from the AI side host. Returns `str` or `None` if unavailable. |
| `should_side_host_speak(chance)` | Decide if the side host chimes in this time (random chance). Returns `bool`. |
| `check_ollama_available()` | Check Ollama server + model availability. Returns `{available, model, models, error}`. |
| `call_ollama(prompt, system, ...)` | Low-level Ollama `/api/chat` HTTP client. Returns model text or `None`. |
| `OLLAMA_DJ_AVAILABLE` | Module-level flag: `True` if `aiohttp` installed and `OLLAMA_DJ_ENABLED=true` |

**Post-processing pipeline:** AI output → strip quotes → validate `{sound:name}` tags via `extract_sound_tags()` → enforce 200 char max → skip if < 5 chars → return clean line.

---

### `utils/lyrics.py` — Lyrics Lookup

Fetches lyrics for the currently playing song using multiple providers with fallbacks.

1. **syncedlyrics** (primary) — Fetches synced (LRC format) lyrics
2. **lyricslrc.co** (fallback 1) — Web scraping
3. **Musixmatch** (fallback 2) — Web scraping

Returns a string of lyrics, or `None` if no provider finds them.

---

### `utils/presets.py` — Playlist Presets

Save and load queue state as JSON files in the `presets/` directory.

| Function | Purpose |
|---|---|
| `save_preset(name, tracks)` | Save a list of track dicts as `presets/<name>.json` |
| `load_preset(name)` | Load tracks from a preset JSON |
| `list_presets()` | List all saved presets with `[{name, count}, ...]` |
| `delete_preset(name)` | Delete a preset file |
| `queue_to_tracks(queue)` | Serialize an asyncio.Queue of track objects to `[dict, ...]` |

---

## 14. Web Dashboard: Mission Control

The Flask web dashboard runs alongside the Discord bot in a background thread, providing a browser-based "Mission Control" interface for remote control.

### How It Starts

In `bot.py`, the `run_web_server()` function runs in a daemon thread:
```python
web_thread = threading.Thread(target=run_web_server, daemon=True)
web_thread.start()
```

The Flask app (`web/app.py`) receives the bot instance via `init_dashboard(bot)` so it can access the Music cog's state directly.

### Authentication (Login Page)

The dashboard supports optional password protection via the `WEB_PASSWORD` environment variable.

**How it works:**

1. Set `WEB_PASSWORD=your_password` in your `.env` file.
2. When a password is configured, a `@app.before_request` handler (`require_login()`) intercepts all requests.
3. Unauthenticated users are redirected to `/login` — a standalone dark-themed login page.
4. On successful password entry (verified via `hmac.compare_digest()` with SHA-256 hashes), `session["authenticated"] = True` is set.
5. Authenticated users see all dashboard pages normally. A 🔓 **Log Out** link appears at the bottom of the sidebar.
6. The `/logout` route clears the session and redirects back to login.

**If `WEB_PASSWORD` is blank or not set** — the dashboard is open access (no login required, same as before).

**Security details:**
- Passwords are compared using `hmac.compare_digest()` to prevent timing attacks.
- Only a boolean `authenticated` flag is stored in the Flask session — the password is never stored client-side.
- The login page is standalone (does not extend `base.html`) so it renders even when the user is not authenticated.
- API endpoints are also protected by the same session check — unauthenticated API calls are redirected to login.

### Pages

| Page | Route | Purpose |
|---|---|---|
| **Login** | `/login` | Password entry (only shown when `WEB_PASSWORD` is set) |
| **Dashboard** | `/` | Live status & remote control: playback buttons, join/leave voice, volume/speed sliders, queue drag-and-drop, DJ voice picker, lyrics, presets, search-to-queue |
| **DJ Lines** | `/dj-lines` | Browse, add, and remove custom DJ lines per category |
| **Soundboard** | `/soundboard` | Play sound effects, upload new sounds, delete existing ones |
| **Radio** | `/radio` | Auto-DJ config, DJ voice picker, recently played history |
| **Queue** | `/queue-manager` | Full queue management with drag-and-drop reorder |
| **Settings** | `/settings` | Bot status info, restart and shutdown controls |

### Navigation

The sidebar in `base.html` provides navigation between all pages. The bot's name is dynamically injected from `bot.user.name` (not hardcoded). When authenticated, a 🔓 **Log Out** link appears at the bottom of the sidebar.

### Activity Log Panel — *New in 6.3.0*

Clicking **📋 Log** in the sidebar opens a 420px slide-out panel from the right side that shows real-time bot activity logs — the same messages shipped to the Discord log channel.

**Features:**
- **Real-time streaming** via Server-Sent Events (SSE) — new log entries appear as they're emitted, with no page refresh.
- **Severity filtering** — All / Info / Warn / Error buttons filter visible entries client-side.
- **Color-coded entries** — each entry shows a timestamp, color-coded severity badge (INFO=blue, WARNING=amber, ERROR=red, DEBUG=gray), and message in monospace.
- **Auto-reconnect** — the `EventSource` API reconnects automatically on disconnect. On reconnect, `/api/logs/recent` is called to backfill missed entries.
- **Backdrop overlay** — clicking the dark backdrop or the ✕ button closes the panel.
- **Responsive** — full-width on mobile (≤768px), 420px slide-out on desktop.

**Architecture:**
```
DiscordLogHandler.emit()
   ├─→ self.buffer (Discord channel flush, existing)
   └─→ log_buffer (deque(maxlen=200), new ring buffer)
            ↓
       Flask endpoints
         /api/logs/recent → JSON (initial load, reconnect)
         /api/logs/stream → SSE (real-time push)
            ↓
       Browser EventSource → renders entries
```

**Voice caching:** The `/api/<guild_id>/voices` endpoint now caches the edge-tts voice list server-side for 30 minutes. The first request fetches from Microsoft's TTS API (5–15 seconds); all subsequent requests return the cached list instantly. If the API times out, stale cache is returned (graceful degradation).

### Dashboard Voice Controls (Join/Leave)

The dashboard provides **🔌 Join** and **🔌 Leave** buttons for each guild card:

- **Join** (shown when bot is not in voice) — Calls `/api/<guild_id>/join` to connect the bot to the first voice channel with a human member.
- **Leave** (shown when bot is in voice) — Calls `/api/<guild_id>/leave` to disconnect the bot from the voice channel.

These buttons are placed alongside the existing playback controls (Pause, Skip, Stop, DJ, Auto-DJ).

### Settings Page

The Settings page (⚙️ in sidebar) provides:

**Bot Status card:**
- Bot name and connection status
- Server count
- Python version
- Platform info
- Memory usage (via `psutil`)
- CPU usage (via `psutil`)

**Danger Zone card:**
- 🔄 **Restart Bot** — Calls `/api/restart` which uses `os.execv()` to re-execute the Python process. Shows a confirmation modal before executing.
- 🔴 **Shut Down Bot** — Calls `/api/shutdown` which sends `SIGTERM` to the bot process. Shows a confirmation modal before executing.

Both actions display a JavaScript confirmation modal before executing. After restart, the page auto-refreshes after 8 seconds to reconnect when the bot comes back online.

> **Note:** If `psutil` is not installed, memory and CPU show as 0. Install with `pip install psutil`.

### Auto-Refresh

The dashboard page auto-refreshes **when a song ends** instead of on a fixed timer. The live progress bar (which ticks every second in JavaScript) detects when `elapsed >= duration` and triggers a page reload after a 1.5-second delay so the user sees the bar fill to 100%. This means the page updates exactly at the right moment — when the next song starts — without any jarring mid-song refreshes.

If no progress bar is tracking a song (e.g., the bot is idle, or playing a track with unknown duration like a livestream), a 3-minute fallback refresh catches state changes.

Other pages (DJ Lines, Soundboard, Radio, Queue, Settings) **never** auto-refresh, so file uploads and form submissions are never interrupted.

### API Endpoints

#### Playback & Voice

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/<guild_id>/skip` | POST | Skip current song |
| `/api/<guild_id>/pause` | POST | Toggle pause/resume |
| `/api/<guild_id>/stop` | POST | Stop playback and clear queue |
| `/api/<guild_id>/join` | POST | Join the first voice channel with a human member |
| `/api/<guild_id>/leave` | POST | Disconnect from the voice channel |
| `/api/<guild_id>/volume` | POST | Set volume (0–200) |
| `/api/<guild_id>/speed` | POST | Set speed (0.25–2.0) |
| `/api/<guild_id>/play` | POST | Add URL/search to queue and start playback |
| `/api/<guild_id>/lyrics` | GET | Fetch lyrics for currently playing song |

#### DJ & Auto-DJ

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/<guild_id>/dj_toggle` | POST | Toggle DJ mode on/off |
| `/api/<guild_id>/dj_voice` | POST | Set DJ TTS voice |
| `/api/<guild_id>/voices` | GET | List available TTS voices (cached for 30 minutes) |
| `/api/<guild_id>/ai_dj_toggle` | POST | Toggle AI side host on/off |
| `/api/<guild_id>/ai_dj_voice` | POST | Set AI side host TTS voice |
| `/api/<guild_id>/ai_dj_status` | GET | Get AI side host status (enabled, voice, model, chance) |
| `/api/<guild_id>/autodj_toggle` | POST | Toggle Auto-DJ on/off |
| `/api/<guild_id>/autodj_source` | POST | Set Auto-DJ source playlist/preset |
| `/api/<guild_id>/listeners` | GET | Get list of users in the bot's voice channel |
| `/api/<guild_id>/history` | GET | Get recently played history |
| `/api/<guild_id>/history/replay/<index>` | POST | Re-add a track from history to the queue |

#### Activity Log (SSE) — *New in 6.3.0*

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/logs/recent` | GET | Return last N log entries from the in-memory ring buffer as JSON. Accepts `?count=N` (max 200). |
| `/api/logs/stream` | GET | Server-Sent Events (SSE) endpoint for real-time log streaming. Polls the ring buffer every 0.5s and pushes new entries. Sends heartbeat comments every ~5s to keep connections alive. |

#### Queue

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/<guild_id>/queue/<index>` | DELETE | Remove item from queue |
| `/api/<guild_id>/queue/clear` | POST | Clear the entire queue |
| `/api/<guild_id>/queue/reorder` | POST | Reorder queue (expects `{"order": [2,0,1,3]}`) |
| `/api/<guild_id>/queue/play_next/<index>` | POST | Move queue item to position 0 (next to play) |

#### Soundboard

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/sounds` | GET | List available soundboard sounds |
| `/api/sounds/upload` | POST | Upload a sound file (multipart/form-data) |
| `/api/sounds/delete` | POST | Delete a sound file |
| `/api/<guild_id>/soundboard` | POST | Play a sound effect in voice |

#### Presets

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/presets` | GET | List all saved presets |
| `/api/presets/delete` | POST | Delete a saved preset |
| `/api/<guild_id>/presets/save` | POST | Save current queue as named preset |
| `/api/<guild_id>/presets/load` | POST | Load a preset into the queue |

#### System

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/restart` | POST | Restart the bot process (`os.execv()`) |
| `/api/shutdown` | POST | Shut down the bot process (`SIGTERM`) |
| `/api/ollama/status` | GET | Check Ollama server availability, model list, enabled status |

### Flask ↔ Discord.py Bridge

Flask runs synchronous request handlers on its own threads. Discord.py requires operations on its async event loop. The bridge is `_run_async()`:

```python
def _run_async(coro):
    future = asyncio.run_coroutine_threadsafe(coro, bot.loop)
    return future.result(timeout=10)
```

This submits a coroutine to the bot's event loop and blocks the Flask thread until it completes (with a 10-second timeout).

### Template Filters

| Filter | Purpose |
|---|---|
| `highlight_sound_tags` | Wraps `{sound:name}` in purple `<span class="sound-tag">🔊 name</span>` for visual highlighting |
| `highlight_placeholders` | Highlights both `{sound:name}` (purple) and `{title}/{prev_title}/{next_title}/{greeting}` (blue) in DJ line display |

### Discord Snowflake IDs in JavaScript

Discord guild IDs are 64-bit integers (snowflakes) that exceed JavaScript's `Number.MAX_SAFE_INTEGER` (2^53 - 1). Passing them as bare numbers in JS truncates them. **All guild IDs in HTML/JS must be wrapped in single quotes** (`'{{ g.id }}'`), not rendered as bare numbers (`{{ g.id }}`). Flask's `<int:guild_id>` route correctly parses them server-side.

---

## 15. Soundboard System

The soundboard lets users play sound effects in the bot's voice channel. It works both from the web dashboard and from `{sound:name}` tags in DJ lines.

### Architecture

```
sounds/ directory
  ├── airhorn.wav
  ├── air_raid.wav
  ├── applause.wav
  └── ... (user-uploaded .mp3/.wav/.ogg/.flac files)

utils/soundboard.py
  ├── list_sounds() → [{"id": "airhorn.wav", "name": "Airhorn", "file": "sounds/airhorn.wav"}, ...]
  └── get_sound_path(sound_id) → "sounds/airhorn.wav" or None

web/app.py
  ├── /api/<guild_id>/soundboard [POST]  → plays a sound
  ├── /api/sounds/upload [POST]           → saves file to sounds/
  └── /api/sounds/delete [POST]           → removes file from sounds/
```

### The 9 Built-in Sounds

These were generated programmatically (sine waves, noise bursts) — no copyright issues:

| File | Effect | Used In DJ Lines |
|---|---|---|
| `airhorn.wav` | Airhorn blast | Intros, hype intros, transitions, callouts |
| `air_raid.wav` | Siren/alarm | Hype intros (loud), station IDs |
| `applause.wav` | Crowd applause | Outros, final outros, callouts |
| `button_press.wav` | UI click | Transitions, outros |
| `club_hit.wav` | Bass drop | Intros, transitions, station IDs |
| `dj_drop.wav` | DJ drop effect | Station IDs, transitions |
| `in_the_mix.wav` | Transition effect | Intros, transitions, callouts |
| `record_scratch.wav` | Vinyl scratch | Final outros |
| `dj_scratch.wav` | Turntable spin-up | Intros, mellow transitions |

### Soundboard Play Endpoint

The `/api/<guild_id>/soundboard` endpoint was rewritten to fix a `KeyError` and a JSON parse error:

**Old (broken):**
```python
# Nested async mess — run_in_executor inside run_coroutine_threadsafe
future = asyncio.run_coroutine_threadsafe(
    bot.loop.run_in_executor(None, _play_sound), bot.loop
)
```

**New (working):**
```python
async def _play_sound():
    source = discord.FFmpegPCMAudio(path, before_options="-nostdin", options="-vn")
    guild.voice_client.play(source)
    return True

result = _run_async(_play_sound())
```

`voice_client.play()` is synchronous but **must** run on the bot's event loop thread for thread safety. The `_run_async` helper ensures this.

### Upload

The soundboard page uses a hidden `<input type="file">` with `position:absolute; opacity:0; width:0; height:0` (not `display:none`, which some browsers reject for programmatic `.click()`). Maximum file size: 16MB (`MAX_CONTENT_LENGTH`). Allowed extensions: `.mp3`, `.wav`, `.ogg`, `.flac`.

---

## 16. DJ Custom Lines

Users can add custom DJ lines alongside the 172 built-in ones. Custom lines are persisted in `dj_custom_lines.json` and merged at runtime.

### Architecture

```
utils/custom_lines.py
  ├── LINE_CATEGORIES = ["intros", "hype_intros", "hype_intros_loud", "outros", ...]
  ├── CATEGORY_LABELS = {"intros": "Session Intros", ...}
  ├── CATEGORY_PLACEHOLDERS = {"intros": ["{greeting}", "{title}", "{sound:name}"], ...}
  ├── add_line(category, line) → bool
  ├── remove_line(category, index) → bool
  └── load_custom_lines() → {"intros": ["My custom intro!"], ...}

dj_custom_lines.json (auto-created)
  {"intros": ["My custom intro with {sound:airhorn}"], "transitions": [...]

Web Dashboard (/dj-lines page)
  ├── Shows built-in + custom lines per category
  ├── Add form (category dropdown + text input)
  ├── Delete button on custom lines
  └── Visual highlights: {sound:name} in purple, {title} etc in blue
```

### Placeholders

| Placeholder | Available In | Replaced With |
|---|---|---|
| `{greeting}` | Intros only | Time-based greeting ("Good evening, everyone!") |
| `{title}` | Intros, outros, final outros | Current song title |
| `{prev_title}` | Transitions (all 3 types) | Previous song title |
| `{next_title}` | Transitions (all 3 types) | Next song title |
| `{sound:name}` | ALL categories | Plays sound effect after DJ speaks |

### Merging

The `_pool(category)` function in `utils/dj.py` merges built-in + custom lines and deduplicates them. All `generate_*()` functions pick from this combined pool, so custom lines are mixed in naturally with built-in ones.

### Web DJ Lines Page Features

- **Sound tag reference card** — Shows all available sound names as clickable chips. Click a chip to copy `{sound:name}` to clipboard.
- **Visual highlighting** — Built-in lines display `{sound:name}` tags in purple and `{title}` etc. in blue (via `highlight_placeholders` Jinja filter).
- **Count badges** — Shows "X built-in · Y custom" per category.

---

## 17. Test Suite

### `tests/test_playlist.py`

| Test | What It Does |
|---|---|
| `test_playlist_extraction` | Extracts 2 items from a real YouTube playlist and verifies they're returned as a list |
| `test_radio_extraction` | Extracts 10 items from the same playlist (simulating radio mode) |
| `test_single_video_as_list` | Searches YouTube for "never gonna give you up" and verifies it returns a single-element list |

> **Note:** These tests make real network requests to YouTube. They will fail without internet access or if YouTube rate-limits the IP.

### `tests/test_suno.py`

| Test | What It Does |
|---|---|
| `test_is_suno_url` | Validates URL detection: `suno.com/song/<uuid>` ✅, `app.suno.ai/song/<uuid>` ✅, invalid paths ❌, YouTube URLs ❌ |
| `test_get_suno_track_success` | Mocks aiohttp responses: page returns OG tags, CDN HEAD returns 200. Verifies track title, thumbnail, URL. |
| `test_get_suno_track_invalid_url` | Passes a non-song URL and verifies `None` is returned |
| `test_get_suno_track_cdn_unreachable` | Mocks a 404 from CDN and verifies `None` is returned |

Uses `aioresponses` to mock HTTP requests — these tests run without network access.

### `tests/test_youtube.py`

| Test | What It Does |
|---|---|
| `test_ytdlsource_extraction` | Extracts "never gonna give you up" via `ytsearch1:` and verifies title, url, and duration are not None |

This is an integration test that requires internet access.

### Running Tests

```bash
# Via launch.sh
./launch.sh doctor

# Directly
venv/bin/python -m pytest tests/ -v

# Only Suno tests (no network required)
venv/bin/python -m pytest tests/test_suno.py -v
```

---

## 18. Launcher Scripts

### `launch.sh`

**Session name:** `musicbot`

| Subcommand | Behavior |
|---|---|
| `start` | Runs `setup_environment()`, then starts the bot in a new screen session. Output goes to `bot.log`. |
| `stop` | Sends `quit` to the screen session. |
| `restart` | Stops, waits 2 seconds, re-runs setup, starts. |
| `attach` | Prints last 1000 lines of `bot.log`, then attaches to the screen session (Ctrl+A D to detach). |
| `setup` | Runs `setup_environment()` only: installs ffmpeg, libopus, creates venv, installs pip packages, creates `__init__.py` files, makes scripts executable. |
| `doctor` | Runs `setup_environment()` + `pytest tests/ -v`. Returns exit code 0 if all tests pass. |

**`setup_environment()` does:**
1. Installs `ffmpeg` via `apt-get` if not found
2. Installs `libopus-dev` via `apt-get` if not found
3. Creates a Python venv in `venv/` if it doesn't exist
4. Installs/updates all pip dependencies
5. Forces reinstall of `yt-dlp` (ensures latest version for YouTube compatibility)
6. Creates `cogs/__init__.py` and `utils/__init__.py`
7. Makes all `.py` files executable

---

### `start.sh`

**Session name:** `mbot`

| Subcommand | Behavior |
|---|---|
| *(none)* or `run` | Full setup + foreground run (default for first-time use) |
| `start` | Full setup + background screen session |
| `stop` | Stops the screen session |
| `restart` | Stops, waits 1 second, re-runs setup, starts background |
| `logs` | Shows last 50 lines of `bot.log`, then attaches to screen session |
| `setup` | Full setup without starting the bot |

**Additional features compared to `launch.sh`:**

1. **Interactive `.env` setup wizard** — If `.env` doesn't exist or contains placeholder values, prompts the user for:
   - Discord Bot Token (required — exits if empty)
   - YouTube API Key (optional — press Enter to skip)
   - Log Channel ID (optional — press Enter to skip)
   Writes the values to `.env`.

2. **Colored terminal output** — Uses ANSI escape codes for `[INFO]`, `[OK]`, `[WARN]`, `[ERROR]` prefixes.

3. **Banner** — Displays `🎵 MBot6.2.0 Launcher` on every run.

4. **Auto-installs Python3** — Checks for and installs `python3`, `python3-pip`, `python3-venv`.

5. **Auto-installs screen** — Checks for and installs the `screen` utility.

6. **Sudo detection** — Uses `sudo` only if available and passwordless sudo works, otherwise runs without it.

---

## 19. Complete Command Reference

**Default prefix:** `?` (configurable in `config.py`)

### Music Commands

| Command | Usage | Permission | Description |
|---|---|---|---|
| `?join` | `?join` | Any user in voice | Bot joins or moves to your voice channel |
| `?leave` | `?leave` | Any user | Bot disconnects from voice |
| `?search` | `?search <query>` | Any user | Searches YouTube, shows top 10 results |
| `?play` | `?play <URL or query>` | Any user in voice | Plays from YouTube URL, search query, search result number, or Suno URL |
| `?playlist` | `?playlist <URL>` | Any user in voice | Adds up to 25 songs from a YouTube playlist |
| `?radio` | `?radio <URL>` | Any user in voice | Adds up to 100 songs from a YouTube playlist (radio mode) |
| `?queue` | `?queue` | Any user | Displays all songs in the queue |
| `?skip` | `?skip` | Any user | Skips the current song |
| `?stop` | `?stop` | Any user | Stops playback and clears the entire queue |
| `?pause` | `?pause` | Any user | Pauses the current song |
| `?resume` | `?resume` | Any user | Resumes a paused song |
| `?clear` | `?clear` | Any user | Clears all songs from the queue (does not stop current song) |
| `?remove` | `?remove <number>` | Any user | Removes a specific song from the queue by position |
| `?nowplaying` | `?nowplaying` | Any user | Shows the currently playing song with progress bar and controls |
| `?volume` | `?volume <0-200>` | Any user | Sets playback volume (100 = normal) |
| `?loop` | `?loop` | Any user | Toggles looping for the current song |
| `?speedhigher` | `?speedhigher` | Any user | Increases playback speed one step |
| `?speedlower` | `?speedlower` | Any user | Decreases playback speed one step |
| `?shuffle` | `?shuffle` | Any user | Randomizes the order of songs in the queue |

### DJ Mode Commands

| Command | Usage | Permission | Description |
|---|---|---|---|
| `?dj` | `?dj` | Any user | Toggle DJ mode on/off (radio DJ speaks between songs) |
| `?djvoice` | `?djvoice <name>` | Any user | Set the DJ's TTS voice (default: `en-US-AriaNeural`) |
| `?djvoice` | `?djvoice` | Any user | Show the current DJ voice |
| `?djvoices` | `?djvoices` | Any user | List available TTS voices (default: English voices) |
| `?djvoices` | `?djvoices ja` | Any user | List available Japanese TTS voices (or any language prefix) |

### AI Side Host Commands

| Command | Usage | Permission | Description |
|---|---|---|---|
| `?aidj` | `?aidj` | Any user | Toggle the AI side host (studio joker) on/off. Requires Ollama + `OLLAMA_DJ_ENABLED=true`. |
| `?aidjvoice` | `?aidjvoice <name>` | Any user | Set the AI side host's TTS voice (separate from main DJ). |
| `?aidjvoice` | `?aidjvoice` | Any user | Show the current AI side host voice. |

### Admin Commands (Bot Owner Only)

| Command | Usage | Permission | Description |
|---|---|---|---|
| `?fetch_and_set_cookies` | `?fetch_and_set_cookies <https URL>` | Bot owner only | Fetches cookies from URL and saves for yt-dlp |
| `?shutdown` | `?shutdown` | Bot owner only | Shuts down the bot |
| `?restart` | `?restart` | Bot owner only | Closes the bot (requires external supervisor to restart) |

### Interactive Buttons (Now Playing UI)

| Button | Action | Visible To |
|---|---|---|
| ▶️ Play/Resume | Resumes paused playback | All users in channel |
| ⏸️ Pause | Pauses playback | All users in channel |
| ⏭️ Skip | Skips current song | All users in channel |
| ❌ Stop | Stops playback and clears queue | All users in channel |
| 🎵 Queue | Shows queue (ephemeral — only visible to clicker) | Clicking user only |

---

## 20. Troubleshooting & Known Issues

### Common Problems

| Problem | Cause | Solution |
|---|---|---|
| **Bot is offline** | Invalid token, network issue, crashed process | Check `bot.log` for errors. Run `./launch.sh attach` or `bash start.sh logs`. Verify `DISCORD_TOKEN` in `.env`. |
| **No audio playing** | ffmpeg missing or broken, libopus missing | Install: `sudo apt install ffmpeg libopus-dev`. Check logs for ffmpeg/opus errors. |
| **`?search` not working** | Missing or invalid `YOUTUBE_API_KEY` | Set the key in `.env`. Ensure YouTube Data API v3 is enabled in Google Cloud Console. |
| **Error 4006 "Session no longer valid"** | Stale voice connection from previous crash | The bot auto-cleans on startup (`on_ready`). If still happening, manually kick the bot from the voice channel in Discord. |
| **`?play` shows "Could not find any playable content"** | YouTube blocked the IP, age restriction, geo-blocking | Use `?fetch_and_set_cookies` with a YouTube URL after logging in to YouTube in a browser. |
| **"Format not available" error** | YouTube changed formats or yt-dlp is outdated | Update: `pip install --upgrade yt-dlp`. The launchers do this automatically. |
| **Bot crashes on speed change** | FFmpeg atempo filter out of range (below 0.5) | Use the `?speedhigher`/`?speedlower` commands instead of direct speed modification. They stay within YouTube's supported range. |
| **Blank screen on `attach`** | Terminal incompatibility with `screen -r` | Workaround: `tail -f bot.log` in a separate terminal. |
| **Soundboard "JSON parse" error** | Old endpoint used `run_in_executor` inside `run_coroutine_threadsafe` | Fixed — endpoint now uses `_run_async()` with a simple async wrapper. (Fixed in 6.2.0) |
| **`KeyError: 'sound'` on playlist play** | DJ lines with `{sound:name}` crash Python's `.format()` | Fixed — `_format_line()` strips sound tags before `.format()`, re-appends after. (Fixed in 6.2.0) |
| **Upload button doesn't work on Soundboard** | File input had `display:none`; auto-refresh killed uploads | Fixed — uses `opacity:0; position:absolute`; auto-refresh disabled on non-dashboard pages. (Fixed in 6.2.0) |
| **"No closing quotation" FFmpeg error** | Crossfade filter string missing closing `"` on `atempo` | Fixed — crossfade filter now properly closes the FFmpeg quote. (Fixed in 6.2.0) |
| **Dashboard buttons broken for some guilds** | Guild IDs passed as JS numbers instead of strings, truncating >53-bit snowflakes | Fixed — all guild IDs in HTML/JS wrapped in single quotes. (Fixed in 6.2.0) |
| **Dashboard 500 error after login** | Stray `{% endif %}` in `dashboard.html` with no matching `{% if %}` — Jinja2 `TemplateSyntaxError` | Fixed — removed the orphaned `{% endif %}` tag. (Fixed in 6.2.0) |
| **Settings page shows 0 MB / 0% CPU** | `psutil` not installed | Install with `pip install psutil`. The page gracefully falls back to 0 if not installed. |
| **Voice dropdowns stuck at "Loading voices..."** | Script ordering bug — inline scripts called functions before they were defined; also no server-side caching (every request hit Microsoft's API) | Fixed in 6.3.0 — functions called via `DOMContentLoaded`; voice list cached for 30 minutes; current voice stored in `data-current` attribute. |
| **"Ollama returned status 404" with no details** | Model not pulled but error gave no actionable info | Fixed in 6.3.0 — now shows model name, pull command, and available models. |

### Known Issues

1. **`launch.sh attach` shows blank screen** — The `screen -r` command may display a blank window in some SSH clients. Detach with Ctrl+A D and use `tail -f bot.log` instead.

2. **Progress bar resets on speed change** — When speed is changed, FFmpeg is restarted from position 0:00. The `song_start_time` is reset, so the progress bar restarts from the beginning.

3. **Suno tracks show 0:00 / 0:00** — Suno doesn't provide duration metadata via HTML scraping. The progress bar uses duration=0, which renders a static empty bar.

4. **`?restart` doesn't actually restart** — It only closes the bot. A process supervisor (like `screen` + launcher script, systemd, or pm2) is needed to detect the exit and start a new process.

5. **Log spam after `?leave`** (Fixed in 6.2.0) — Previously, the Now Playing update task continued running after leaving a voice channel. Now properly cancelled.

6. **Interaction already responded error** (Fixed in 6.2.0) — Previously, clicking the "queue" button multiple times would crash the bot. Now uses `ephemeral` responses.

7. **`?fetch_and_set_cookies` crashes with `AttributeError`** — The `admin.py` cog calls `cookie_parser.parse_all_cookies(header)` but this function does not exist in `utils/cookie_parser.py` (that file only contains log-parsing functions). The cookie-fetching admin command will fail at runtime.

8. **Speed values below 0.5 may cause FFmpeg errors** — The `atempo` FFmpeg filter only supports 0.5–2.0 per instance. While the bot's speed ladder starts at 0.25x, attempting to play at that speed may cause FFmpeg to fail. Values below 0.5 require chaining multiple `atempo` filters (e.g., `atempo=0.5,atempo=0.5` for 0.25x).

### Bugs Fixed in 6.3.0

| Bug | Root Cause | Fix |
|---|---|---|
| **Voice dropdowns permanently stuck at "Loading voices..."** | Inline `<script>` tags called `loadVoices()`/`loadAiVoices()` before they were defined (definitions were at the bottom of the page). Also no server-side caching — every dropdown fetch called `edge_tts.list_voices()` which makes a live HTTP request to Microsoft (5–15s). | Functions now called via `DOMContentLoaded`; current voice stored in `data-current` attribute; voice list cached server-side for 30 minutes with stale-cache fallback on timeout |
| **"Ollama returned status 404" with no actionable info** | `call_ollama()` logged only the HTTP status code (404) without model name, pull command, or available alternatives | On 404, handler now queries `/api/tags` for available models and logs: `Model 'X' not found. Run: ollama pull X \| Available: Y,Z` |
| **Wrong default Ollama model** | `config.py`, `llm_dj.py`, `app.py`, `music.py` all hardcoded `llama3.2` as fallback — but that model wasn't pulled | Changed default to `gemma4:latest` across all files; created `.env` file with correct model |

### Bugs Fixed in 6.2.0

| Bug | Root Cause | Fix |
|---|---|---|
| **Soundboard `KeyError` / JSON parse error** | `run_in_executor(None, _play_sound)` nested inside `run_coroutine_threadsafe` — returned HTML error page instead of JSON | Replaced with `_run_async(_play_sound())` — simple async wrapper using the existing helper |
| **`KeyError: 'sound'` on playlist play with DJ on** | `generate_intro().format(title=...)` interprets `{sound:airhorn}` as a Python format field named `sound` | Added `_format_line()` — extracts `{sound:...}` tags before `.format()`, re-appends after |
| **Upload button not working on Soundboard page** | `<input type="file" style="display:none">` — browsers reject `.click()` on `display:none` file inputs; also page auto-refresh every 30s killed uploads | Changed to `position:absolute; opacity:0; width:0; height:0`; made auto-refresh conditional (dashboard only) |
| **FFmpeg "No closing quotation" error** | Crossfade filter string `' -filter:a "atempo=...+afade=...'` was missing the closing `"` | Added closing `"'` to the crossfade FFmpeg options string |
| **Dashboard buttons broken for large guild IDs** | Guild IDs rendered as bare JS numbers (`{{ g.id }}`) — Discord snowflakes exceed JS `Number.MAX_SAFE_INTEGER` (2^53-1), causing silent truncation | All guild IDs in HTML/JS wrapped in single quotes (`'{{ g.id }}'`) |
| **PlaceholderTrack `webpage_url` set to bare video ID** | `yt-dlp` `extract_flat=True` returns bare IDs in `url` field (e.g., `"dQw4w9WgXcQ"`). The `or` fallback in `__init__` set `webpage_url` to the bare ID (truthy), preventing proper URL construction | Check if `url` starts with `http` before using it as `webpage_url` |
| **Jinja `is none` check on JS variable** | Soundboard guild selection used Jinja `{% if soundboardGuild is none %}` on a JS variable — Jinja renders at template time | Build JS array of in-voice guild IDs and pick first one at runtime |
| **Dashboard 500 error (TemplateSyntaxError)** | Stray `{% endif %}` tag at line 102 in `dashboard.html` with no matching `{% if %}` — Jinja2 error: "Encountered unknown tag 'endif'" | Removed the orphaned `{% endif %}` — the volume/speed sliders were already outside the `{% if g.in_voice %}` block |
| **Dashboard Join button did nothing** | `joinVoice()` JavaScript function was referenced in the HTML `onclick` handler but never defined — clicking Join had no effect | Added `joinVoice()` and `leaveVoice()` JavaScript functions that call the join/leave API endpoints with loading states and toast feedback |

---

## 21. Development Guide

### Adding a New Cog

1. Create a new file in `cogs/`, e.g., `cogs/fun.py`:
   ```python
   from discord.ext import commands
   import discord

   class Fun(commands.Cog):
       def __init__(self, bot):
           self.bot = bot

       @commands.command(name="hello")
       async def hello(self, ctx):
           await ctx.send("Hello!")

   async def setup(bot):
       await bot.add_cog(Fun(bot))
   ```

2. The cog will be **auto-loaded** by `bot.py` (it scans `cogs/*.py`).

3. To **exclude** a cog from auto-loading (e.g., a library module), add it to the exclusion check in `bot.py` line 63:
   ```python
   if filename.endswith('.py') and filename != '__init__.py' and filename != 'youtube.py' and filename != 'logging.py' and filename != 'fun.py':
   ```

### Adding a New Music Source

If you want to support a new audio source (e.g., SoundCloud, Spotify):

1. Create a new utility module in `utils/` (e.g., `utils/soundcloud.py`).
2. Implement a track class compatible with `YTDLSource`:
   ```python
   class SoundCloudTrack:
       def __init__(self, title, url, duration, thumbnail, webpage_url):
           self.title = title
           self.url = url           # Direct audio stream URL
           self.duration = duration  # Seconds (0 if unknown)
           self.thumbnail = thumbnail
           self.webpage_url = webpage_url
   ```
3. Add a detection function: `is_soundcloud_url(text) -> bool`
4. Add a resolution function: `async def get_soundcloud_track(url) -> SoundCloudTrack | None`
5. In `cogs/music.py`, add the detection to the `?play` command:
   ```python
   elif is_soundcloud_url(query):
       track = await get_soundcloud_track(query)
       if not track:
           return await ctx.send(embed=...)
       await queue.put(track)
   ```

### Adding a New Command to the Music Cog

1. Define the command in the `Music` class using the `@commands.command()` decorator.
2. Use `self.create_embed()` for consistent embeds.
3. Use `config.*_EMOJI` for consistent emoji in messages.
4. Always log at the start: `logging.info(f"X command invoked by {ctx.author} in {ctx.guild.name}")`.
5. Handle edge cases (not in voice, not playing, empty queue) with appropriate error embeds.

### Code Conventions

| Convention | Example |
|---|---|
| **Command names** | Lowercase, no underscores: `speedhigher`, not `speed_higher` |
| **Embed colors** | `discord.Color.red()` for errors, `discord.Color.orange()` for warnings, `discord.Color.blurple()` for info |
| **Logging** | Every command logs its invocation with user and guild name |
| **Error messages** | Always use embeds with emoji prefix from `config.py` |
| **Queue access** | Always via `await self.get_queue(guild_id)` — never access `self.song_queues` directly |
| **Voice connection** | Use `self_deaf=True` when connecting to save bandwidth |
| **Playback after** | Always use `self.bot.loop.create_task(self._after_playback(ctx, e))` for the `after` callback |

### Project File Conventions

| File Type | Convention |
|---|---|
| `cogs/*.py` | Each is a discord.py Cog with an `async def setup(bot)` function |
| `utils/*.py` | Standalone utility modules, no Cog setup |
| `__init__.py` | Auto-created by launchers; required for Python package imports |
| `.env` | NEVER commit to git — contains secrets |
| `youtube_cookie.txt` | NEVER commit to git — contains session cookies |

---

**End of Guide**