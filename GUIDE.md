# MBot v420.0.3 — Comprehensive Technical Guide

> **Last Updated:** 2026-04-18
> **Version:** v420.0.3
> **License:** MIT

---

## What's New in v420.0.3

### 🧡 MOSS-TTS-Nano Engine (New Default TTS)

The bot now supports **three TTS engines** with MOSS-TTS-Nano as the new default. MOSS-TTS-Nano is a 0.1B parameter voice cloning TTS model that runs on CPU (no GPU needed), supports 20+ languages, and outputs 48 kHz stereo audio via a FastAPI server.

**Engine priority:** `moss` (default) → `vibevoice` → `edge-tts` (cloud fallback)

**How it works:**
- The bot sends `POST /api/generate` to the MOSS-TTS-Nano server with **multipart form data**. The server requires either a `prompt_audio` file upload (for voice cloning) **or** a `demo_id` parameter (for built-in demo voices). If the bot finds a `.wav` prompt file in `assets/moss_voices/` matching the voice name, it uploads it. Otherwise, it sends `demo_id=demo-1` to use the server's built-in English demo voice as a fallback.
- The server returns JSON with `audio_base64` (base64-encoded WAV), `sample_rate` (48000), and `run_status`.
- A built-in health check (`GET /api/warmup-status`) caches server reachability — checks `state == "ready"` to confirm the server has finished loading the model and running its warmup synthesis (~30s on CPU). If the server is down or still warming up, the bot falls back to edge-tts. Health check results are cached (30s healthy / 10s down).
- Voice names correspond to `.wav` prompt audio files in `assets/moss_voices/`. Built-in catalog: `en_warm_female` (default DJ voice), `en_news_male` (good for AI side host). Add your own `.wav` files to create custom voices. If no prompt files exist, the bot falls back to `demo-1` (the MOSS server's first built-in demo voice).
- The legacy `TTS_MODE=local` alias is still supported and maps to `vibevoice` with a deprecation warning.
- **Backward compatibility:** If someone has `TTS_MODE=kokoro`, it auto-redirects to `moss` with a warning in the logs.

**MOSS-TTS-Nano API reference (`POST /api/generate`):**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `text` | Form (required) | — | The text to synthesize |
| `demo_id` | Form | `""` | Built-in demo voice ID (e.g. `demo-1`, `demo-2`). Required if no `prompt_audio` is uploaded. |
| `prompt_audio` | File upload | `None` | `.wav` prompt audio file for voice cloning. Required if no `demo_id` is provided. |
| `max_new_frames` | Form | `375` | Max generation frames (375 ≈ ~15s audio) |
| `voice_clone_max_text_tokens` | Form | `75` | Max text tokens from prompt for voice cloning |
| `enable_text_normalization` | Form | `"1"` | Enable WeTextProcessing normalization |
| `enable_normalize_tts_text` | Form | `"1"` | Enable TTS-specific text normalization |
| `do_sample` | Form | `"1"` | Enable sampling (1=yes, 0=no) |
| `cpu_threads` | Form | `4` | Number of CPU threads for torch |
| `attn_implementation` | Form | `"model_default"` | Attention backend: `model_default`, `sdpa`, or `eager` |
| `seed` | Form | `"0"` | Random seed (0 = non-deterministic) |

**Response (200 OK):**
```json
{
  "audio_base64": "<base64-encoded WAV audio>",
  "sample_rate": 48000,
  "run_status": "Done | mode=voice_clone | prompt=zh_1 | attn=eager | exec=cpu | audio=2.56s | elapsed=5.20s",
  "prompt_audio_path": "Uploaded: en_warm_female.wav",
  "warmup_status_text": "Warmup complete. device=cpu elapsed=23.29s | WeTextProcessing ready.",
  "text_normalization_status_text": "..."
}
```

**Response (400 Bad Request):**
```json
{"error": "demo_id is required unless prompt speech is uploaded."}
```
This happens when neither `demo_id` nor `prompt_audio` is provided. The bot handles this automatically by sending `demo_id=demo-1` when no custom prompt audio is available.

**Setup:**
```bash
# Install and start the MOSS-TTS-Nano server on port 18083
pip install moss-tts-nano
moss-tts-nano serve --host 0.0.0.0 --port 18083 --device auto
```
Or use docker-compose (which includes a `moss-tts` service built from `./moss-tts-server/Dockerfile`).

> **Note:** On CPU-only systems, `--device auto` is ignored and forced to `cpu`. The first startup takes ~30-60 seconds for model download + warmup synthesis. The bot's health check will return `False` until the server reports `state: "ready"`, and it'll fall back to edge-tts during that time.

Then set in `.env`:
```ini
TTS_MODE=moss
MOSS_TTS_URL=http://localhost:18083    # or http://172.16.1.125:18083 for a remote server
DJ_VOICE=en_warm_female
```

Add prompt audio files (5-30s clean speech recordings) to `assets/moss_voices/` for custom voices. If no prompt files are present, the bot uses the MOSS server's built-in demo voices (`demo-1`).

### 🗣️ Three-Engine TTS Architecture

The TTS system has been refactored from a 2-engine system (`edge-tts` / `local`) to a 3-engine system:

| Priority | Engine | Where | When Used |
|---|---|---|---|
| 1st | **MOSS-TTS-Nano** (`moss`) | FastAPI server (CPU-friendly) | Default — truly local, voice cloning, ~2-8s latency on CPU |
| 2nd | **VibeVoice** (`vibevoice`) | Separate WebSocket server | If explicitly configured |
| 3rd | **Edge TTS** (`edge-tts`) | Microsoft Cloud | Automatic fallback when local engines fail |

**New config variables:** `MOSS_TTS_URL`, `MOSS_VOICE`, `VIBEVOICE_TTS_URL`. The old `LOCAL_TTS_URL` still works as a backward-compatible alias.

**How fallback works:**
```
TTS_MODE=moss → health check → server up & ready? → generate WAV → ✅ success
                                      ↓ server down or warming up (cached 30s/10s)
                                      → log warning with setup instructions
                                      → resolve voice name for edge-tts
                                      → edge-tts generates MP3 → ✅ success

MOSS voice resolution:
  voice name → .wav file exists in assets/moss_voices/?
    → YES: upload prompt_audio for voice cloning → custom voice
    → NO:  send demo_id=demo-1 → server's built-in demo voice
```

**Voice name auto-resolution:** The `_resolve_voice()` function detects mismatched voice names (e.g. passing `en-US-AriaNeural` when using MOSS) and swaps them for the target engine's default — so switching `TTS_MODE` doesn't require changing `DJ_VOICE`.

**The `TTS_AVAILABLE` flag:** A new boolean that's `True` when *any* TTS engine is available (not just edge-tts). The DJ mode and AI side host commands now gate on `TTS_AVAILABLE` instead of `EDGE_TTS_AVAILABLE`, so DJ works with MOSS even if edge-tts isn't installed.

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
- The panel is now **640px wide** (up from 420px) with `max-width: 90vw` for responsive sizing, giving much more room to read log messages.
- **📋 Copy button** — Copies all currently visible log entries to clipboard. Respects the active filter (All/Info/Warn/Error), so if you're filtering to errors only, only errors get copied. Output format is plain text, one line per entry — ready to paste into Discord, a bug report, or a support ticket.
- **Three-tier copy strategy** (works on HTTP and HTTPS):
  1. `document.execCommand('copy')` with a nearly-invisible textarea (primary — works on HTTP)
  2. `navigator.clipboard.writeText()` (secondary — HTTPS/localhost only)
  3. **Copy modal** (last resort) — full-screen popup with a readonly textarea pre-selected. User just does Ctrl+A → Ctrl+C. Closes with ✕, Escape, or clicking outside.
- No new dependencies — SSE is native browser API.

### 🎙️ Voice Dropdown Fixes (Radio Page)

The "DJ Voice" and "AI Side Host Voice" dropdowns on the Radio page were permanently stuck at "Loading voices..." due to two bugs:

1. **Script ordering** — Inline `<script>` tags called `loadVoices()` before the function was defined (the definitions were at the bottom of the page). Fixed: functions are now called via `DOMContentLoaded` after all scripts load. The current voice is stored in a `data-current` attribute on the `<select>` element.
2. **No voice caching** — Every dropdown open called `edge_tts.list_voices()` which makes a live HTTP request to Microsoft's TTS API (5–15 seconds). Fixed: server-side cache with 30-minute TTL; first call fetches from Microsoft, all subsequent calls return the cached list instantly. If the API times out, stale cache is returned (graceful degradation). Descriptive error messages now appear in the dropdown when `edge-tts` isn't installed or the API is unreachable.

### 🃏 AI Side Host — Auto-Created Custom Ollama Model

The AI side host now **auto-creates a custom Ollama model** (`mbot-sidehost` by default) on startup that bakes the DJ personality into the model itself. Instead of sending the full system prompt on every API call, the personality is built into the model via an Ollama Modelfile:

```
FROM gemma4:latest
SYSTEM """You are the AI side host on MBot Radio — the studio joker..."""
```

**Startup flow:**
1. Bot starts → calls `ensure_custom_model()` in `on_ready()`
2. Checks if `mbot-sidehost` exists in Ollama (`GET /api/tags`)
3. If not → checks if the base model (e.g. `phi3:latest`) is pulled
4. If base model exists → creates the custom model
5. All subsequent API calls use `mbot-sidehost` — system prompt is baked in, so only the user prompt (context) is sent per call

**Three-method model creation (bug workaround):**

Ollama's `/api/create` JSON endpoint has a known bug where complex Modelfiles with triple-quoted `SYSTEM """..."""` blocks containing braces (`{sound:name}`), quotes, and multi-line text cause `"neither 'from' or 'files' was specified"`. The bot works around this with three methods tried in order:

| Priority | Method | How | When Used |
|----------|--------|-----|-----------|
| **1st** | **CLI** (preferred) | Write Modelfile to `/tmp`, run `ollama create <name> -f <path>` via subprocess | Ollama on same machine as bot |
| **2nd** | **API (JSON)** | POST `/api/create` with `{"modelfile": "..."}` | Remote Ollama, simple Modelfiles |
| **3rd** | **API (multipart)** | POST `/api/create` with `FormData` file upload | Remote Ollama, complex Modelfiles (fallback) |

For local Ollama: Method 1 works perfectly because it uses the exact same file parser as the CLI `ollama create -f Modelfile`.

For remote Ollama (e.g. Ollama at `172.16.1.26:11434`, bot on a different machine): Method 1 fails with `FileNotFoundError` (no `ollama` binary), then Methods 2 and 3 are tried.

**Benefits:**
- Faster inference (smaller payload per call — no ~2KB system prompt sent every time)
- Personality is persistent — even raw `ollama run mbot-sidehost` in the terminal gets the DJ persona
- You can interact with the model directly: `ollama run mbot-sidehost "Drop a hot take about 80s music"`
- To recreate after a personality update: `ollama rm mbot-sidehost` and restart the bot

**Config name:** `OLLAMA_CUSTOM_MODEL` (default: `mbot-sidehost`) — change it in `.env` if you want a different name.

**Fallback:** If the custom model can't be created (Ollama down, base model not pulled, API bugs), the bot falls back to the base model + system prompt on every call — zero functionality loss.

The AI side host now **knows what the main DJ just said** and can react to it. Instead of generating blind banter, the side host receives the main DJ's spoken line as context when calling Ollama, enabling two-host chemistry like a real radio show.

**How it works:**
- `_dj_speak()` stores the clean spoken text in `self._last_dj_line[guild_id]` (after stripping `{sound:name}` tags).
- When the AI side host is about to speak, the last DJ line is passed through `_try_ai_side_host(dj_line=...)` → `generate_side_host_line(dj_line=...)` → `_build_user_prompt(dj_line=...)` → included as `Main DJ just said: "..."` in the Ollama prompt.
- An `is_ai=True` flag on `_dj_speak()` prevents the AI's own lines from overwriting the main DJ's stored line — the AI always reacts to the main DJ, not to itself.
- **4 new reactive banter categories:** `react_agree` (agree + funny twist), `react_disagree` (playful pushback), `react_one_up` (escalate the joke), `react_tangent` (go off on a funny tangent).
- **Smart category selection:** When a DJ line is provided, 60% chance of a reactive category, 40% chance of independent banter (for variety).
- The system prompt now includes a "REACTING TO THE MAIN DJ" section teaching the model to respond to the DJ's line without repeating it.

### 🎵 DJ Bed Music Fix: "Already playing audio"

The DJ bed music (ambient track played under the DJ's voice during intros) was trying to start **while the TTS was still playing**, causing `voice_client.play()` to raise "Already playing audio" because Discord's voice client can only play one source at a time.

**Fix:**
1. Removed the `_start_bed_music()` call from `play_next()` after TTS starts — it's impossible to play bed music and TTS simultaneously on a single voice connection.
2. Moved bed music start to `_on_tts_done()` — it now starts **after** TTS finishes, only when there are sound effects (e.g. `{sound:dj_turn_it_up}`) to fill the gap between the DJ speech and the song. If there are no sounds, the song starts immediately and bed music isn't needed.
3. Added an `is_playing()` guard in `_start_bed_music()` so it gracefully skips (with a `DEBUG` log) instead of crashing when something else is already using the voice client.

**Before (broken flow):**
```
TTS starts playing → _start_bed_music() → "Already playing audio" ERROR
TTS finishes → sound effects → song
```

**After (fixed flow):**
```
TTS starts playing
TTS finishes → _start_bed_music() → bed under sound effects → song starts → bed stops
```

### 🔧 Stuck-State Recovery & Audio Race Condition (Voice Client)

Even with the WAV header fix, there's a possibility of a previous playback getting stuck (e.g. a corrupted file, an FFmpeg crash, or an OS-level audio issue). Before this fix, any stuck playback would cause **every subsequent `play()` call** to fail with "Already playing audio", silently skipping the entire queue with no audio output — the bot would just cycle through songs forever with no sound.

Additionally, the soundboard UI, DJ sound effects, and bed music paths could race with each other — e.g. TTS finishes while bed music is starting, or soundboard plays over a DJ sound effect — each independently calling `vc.play()` and hitting "Already playing audio".

**Fix (two layers):**

1. **`_dispatch_audio_play()` central guard** (v420.0.3) — The monolithic audio injection point now checks `vc.is_playing()` before every `vc.play()` call. If something is already playing, it calls `vc.stop()` first. This single guard fixes all race conditions across every audio path (soundboard, DJ sounds, bed music, TTS, song playback) — no caller needs to worry about stopping the current source manually.

2. **Legacy per-call guards** (still present in `_dj_speak()` and `_start_song_playback()`) — These were the original fix that added `is_playing()` + `stop()` + 300ms sleep before individual `play()` calls. They remain as defense-in-depth but are now redundant — the central `_dispatch_audio_play()` guard handles it.

```python
# Central guard in _dispatch_audio_play (new):
if vc.is_playing():
    vc.stop()
vc.play(source, after=callback)
```

The 404 error from Ollama when a model isn't pulled now shows an actionable message:
- **Before:** `AI Side Host: Ollama returned status 404`
- **After:** `AI Side Host: Model 'llama3.2' not found (Ollama 404). Run: ollama pull llama3.2 | Available models: gemma4:latest`

On 404, the handler now queries `/api/tags` to list what's actually available and includes the pull command in the log. The `check_ollama_available()` function also now includes `Run: ollama pull <model>` in its error message.

### 🔄 Default Model Change

The default Ollama model has been changed from `llama3.2` to `gemma4:latest` across all files (`config.py`, `.env.example`, `utils/llm_dj.py`, `web/app.py`, `cogs/music.py`). The `.env.example` now lists `gemma4:latest`, `phi3:mini`, `llama3.2`, and `gemma2:2b` as recommended models.

### 🎬 OBS Studio Integration (New)

The bot now integrates with OBS Studio via obs-websocket 5.x for full radio station visual control from Mission Control. OBS runs headlessly and is controlled entirely from the web dashboard.

- **OBS Bridge** (`utils/obs_bridge.py`) — Stateless request/response WebSocket client connecting to obs-websocket 5.x. Uses `obsws_python.ReqClient` with named methods (e.g., `client.start_stream()`, `client.set_current_program_scene()`). Connection backoff prevents log spam when OBS is down (30s cooldown after failed connection). Graceful degradation — all API calls return `{"connected": false}` when OBS is unreachable, never crash the bot.

- **OBS Docker image** (`obs-studio/Dockerfile`) — Headless OBS container with Xvfb virtual display, PulseAudio null sink for virtual audio, VNC server for remote UI debugging (port 5900), and the obs-websocket 5.x plugin. Includes a default scene collection "Radio DJ" with 4 scenes: "️ Now Playing", "🎙️ DJ Speaking", "⏳ Waiting", "📺 Overlay Only". WebSocket password auto-configured from `OBS_WEBSOCKET_PASSWORD` env var. amd64 only (OBS + VNC on arm64 is impractical in Debian bookworm).

- **Auto scene switching** — The bot can automatically switch OBS scenes based on playback state. Opt-in via `OBS_AUTO_SCENES=true`. Scene switches run in daemon threads (non-blocking, fire-and-forget). Mappings: song playing → "Now Playing", DJ speaking → "DJ Speaking", queue empty → "Waiting", YouTube Live overlay → "Overlay Only". Scene names configurable via `OBS_SCENE_NOW_PLAYING`, `OBS_SCENE_DJ_SPEAKING`, `OBS_SCENE_WAITING`, `OBS_SCENE_OVERLAY`.

- **`start.sh` auto-setup** — The setup wizard now installs OBS Studio (`apt install obs-studio`), installs headless support packages (xvfb, dbus, pulseaudio), generates a random WebSocket password and writes it to `.env`, configures obs-websocket 5.x (writes both `config.json` and `global.ini`), copies the default "Radio DJ" scene collection and profile, and starts headless OBS via `xvfb-run -a obs`.

### 🔒 CSRF Protection (New)

The Mission Control web dashboard now has CSRF (Cross-Site Request Forgery) protection on all POST/PUT/DELETE/PATCH endpoints.

- A random 64-char hex token is generated per session and stored in `session["_csrf_token"]`
- The token is exposed as a `<meta name="csrf-token">` tag in `base.html`
- A global JavaScript `fetch()` shim in `base.html` automatically injects the `X-CSRFToken` header on all mutating requests
- A Flask `before_request` hook validates the token — exempted endpoints: login (no session yet), cookie bridge CORS endpoints, overlay API (public)
- DJ Lines HTML forms include a hidden `_csrf_token` field
- Returns 403 JSON for API endpoints, 403 HTML for pages

### 🐛 Bug Fixes

- **`login_required` not defined** — The `@login_required` decorator was used on 19 OBS API routes but never defined, crashing the entire web dashboard on import with `NameError`. Fixed by removing the redundant decorators — `require_login()` already handles auth globally via `@app.before_request`.

- **`_MissingSentinel` crash on shutdown** — `asyncio.get_event_loop()` returns an internal `_MissingSentinel` object during Python teardown. `discord_log_handler.py` tried calling `.is_closed()` on it. Fixed with `hasattr(loop, 'is_closed')` guard.

- **OBS Bridge API method mapping** — `_call()` used `client.call(request_type, data)` which doesn't exist on `obsws_python.ReqClient`. Replaced with `_safe_call()` using lambda-based named method dispatch: `lambda c: c.start_stream()`, `lambda c, sn=scene_name: c.set_current_program_scene(sceneName=sn)`.

- **OBS Bridge connection spam** — When OBS is not running, every API call produced a full Python traceback (6+ per minute from dashboard polling). Fixed with 30-second connection backoff and `logging.getLogger("obsws_python").setLevel(logging.CRITICAL)` to suppress the library's verbose logging.

- **`queue._queue` private attribute access** — 11 external references to `asyncio.Queue._queue` replaced with public API methods: `peek_queue(guild_id, max_items)`, `peek_queue_first(guild_id)`, and `queue_push_front(guild_id, item)`.

- **Shallow copy of `YTDL_FORMAT_OPTIONS`** — `get_ytdl_format_options()` returned `dict.copy()` (shallow), meaning nested dicts like `http_headers` were shared references. Changed to `copy.deepcopy()` in `cogs/youtube.py`, `cogs/music.py`, `web/app.py`, and `tests/test_playlist.py`.

- **OBS healthcheck in docker-compose.yml** — The old healthcheck used `curl -sf http://localhost:4455 || exit 0` which always succeeded (`|| exit 0`). WebSocket port 4455 doesn't respond to HTTP GET. Fixed with proper TCP socket check: `python3 -c "import socket; s=socket.socket(); s.settimeout(2); s.connect(('localhost',4455)); s.close()"` plus `pgrep -x obs`.

- **"Already playing audio" race condition** — Discord's `VoiceClient.play()` raises `ClientException: Already playing audio` when called while audio is still active. This happened in multiple code paths: soundboard playing over DJ sound effects, DJ sounds starting while bed music was playing, and song playback racing with lingering TTS/sounds. The fix is in `_dispatch_audio_play()` — the central audio injection point now checks `vc.is_playing()` and calls `vc.stop()` before starting any new audio source. This single guard fixes all race conditions across the soundboard, DJ sound effects, bed music, TTS, and song playback paths. The soundboard web API also now explicitly stops bed music before playing a sound.

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
10. [AI Side Host: utils/llm_dj.py](#10-ai-side-host-utilsllm_djpy--the-studio-joker)
11. [Admin Cog: cogs/admin.py](#11-admin-cog-cogsadminpy)
12. [Logging Cog: cogs/logging.py](#12-logging-cog-cogsloggingpy)
13. [Utility Modules](#13-utility-modules)
14. [OBS Studio Integration](#14-obs-studio-integration)
15. [CSRF Protection](#15-csrf-protection)
16. [Web Dashboard: Mission Control](#16-web-dashboard-mission-control)
17. [Soundboard System](#17-soundboard-system)
18. [DJ Custom Lines](#18-dj-custom-lines)
19. [Test Suite](#19-test-suite)
20. [Launcher Scripts](#20-launcher-scripts)
21. [Complete Command Reference](#21-complete-command-reference)
22. [Troubleshooting & Known Issues](#22-troubleshooting--known-issues)
23. [Development Guide](#23-development-guide)

---

## 1. Overview

**MBot v420.0.3** is a self-contained Discord music bot built with Python and `discord.py`. It plays audio from YouTube (URLs, searches, playlists) and Suno (direct song URLs) directly into Discord voice channels. The bot is designed to run as a persistent background service on Debian-based Linux servers, managed through `screen` sessions.

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
| **DJ Mode** | **MOSS-TTS-Nano (default)** | `TTS_MODE=moss` — CPU-friendly FastAPI server, voice cloning via prompt audio, ~2-8s latency, no GPU needed |
| **DJ Mode** | VibeVoice-Realtime | `TTS_MODE=vibevoice` — separate WebSocket server, ~300ms latency, `en-Carter_man` etc. |
| **DJ Mode** | Edge TTS fallback | Automatic fallback to Microsoft cloud TTS when local engines fail |
| **DJ Mode** | ~480 built-in DJ line templates | ~150 with sound tags across 10 categories, plus funny/serious/weird variants |
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
| **OBS Studio** | Visual broadcast control | obs-websocket 5.x integration — stream/record, scene switching, source controls from Mission Control |
| **OBS Studio** | Auto scene switching | `OBS_AUTO_SCENES=true` — automatically switch OBS scenes based on playback state (Now Playing, DJ Speaking, Waiting, Overlay) |
| **OBS Studio** | Headless OBS Docker image | `obs-studio/Dockerfile` — Xvfb + PulseAudio + VNC + obs-websocket 5.x |
| **CSRF Protection** | Session-based CSRF tokens | 64-char hex tokens, global fetch() shim, before_request validation |

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
│   ├── dj.py               # Radio DJ mode — 3-engine TTS (MOSS/VibeVoice/Edge), ~480 templates, health check, sound tag support
│   ├── llm_dj.py           # AI side host — Ollama client, studio joker personality, 8 banter categories
│   ├── obs_bridge.py       # OBS WebSocket bridge — Mission Control → OBS control
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
│   ├── app.py              # Flask app — 40+ API endpoints, login auth, settings, template filters, soundboard/upload/delete, 19 OBS routes
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
├── assets/
│   └── moss_voices/        # MOSS-TTS-Nano prompt audio files (voice cloning references)
│       ├── en_warm_female.wav  # Default DJ voice (warm female)
│       └── en_news_male.wav    # Default AI side host voice (news male)
│
├── moss-tts-server/        # MOSS-TTS-Nano Docker server
│   └── Dockerfile          # Docker build for MOSS TTS server
│
├── obs-studio/             # Headless OBS Studio Docker image
│   ├── Dockerfile           # Multi-stage OBS + Xvfb + VNC + WebSocket
│   ├── entrypoint.sh        # Xvfb → PulseAudio → OBS startup sequence
│   ├── .dockerignore
│   └── config/             # Default OBS config baked into the image
│       └── obs-studio/
│           ├── global.ini
│           └── basic/
│               ├── profiles/RadioDJ/  # Default OBS profile
│               └── scenes/             # "Radio DJ" scene collection
│
├── setup-lxc.sh             # Proxmox LXC (Debian 12) one-shot setup script
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
   ├── imports → utils/dj.py (EDGE_TTS_AVAILABLE, TTS_MODE, TTS_AVAILABLE, generate_intro, generate_song_intro, generate_outro, generate_tts, cleanup_tts_file, extract_sound_tags, list_voices, MOSS_TTS_URL, VIBEVOICE_TTS_URL)
   ├── imports → utils/lyrics.py (get_lyrics)
   ├── imports → utils/presets.py (save_preset, load_preset, queue_to_tracks)
   ├── imports → utils/soundboard.py (list_sounds, get_sound_path)
   ├── imports → utils/llm_dj.py (OLLAMA_DJ_AVAILABLE, generate_side_host_line, should_side_host_speak, check_ollama_available)
   ├── imports → utils/obs_bridge.py (OBSBridge, switch_scene — auto scene switching via _obs_switch_scene)
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
  ├── imports → utils/obs_bridge.py (OBSBridge — 19 OBS API routes under /api/obs/*)
  ├── @app.before_request → require_login() — session auth guard when WEB_PASSWORD is set
  ├── @app.before_request → validate_csrf() — CSRF token validation on mutating requests
  ├── routes → /login, /logout — password authentication
  ├── routes → /settings — settings page (system info, restart/shutdown)
  ├── routes → /api/restart, /api/shutdown — restart/shutdown endpoints
  ├── calls → cogs/music.py (via bot.get_cog("Music")) for playback state
  └── renders → web/templates/*.html (Jinja2 with custom filters)

utils/dj.py
   ├── uses → utils/soundboard.py (list_sounds — for resolving {sound:name} tags)
   ├── uses → utils/custom_lines.py (load_custom_lines — merges built-in + custom)
   ├── uses → config.py (STATION_NAME, TTS_MODE, MOSS_TTS_URL, MOSS_VOICE, VIBEVOICE_TTS_URL)
   └── TTS engines → MOSS-TTS-Nano REST (default), VibeVoice-Realtime WebSocket, or edge_tts.Communicate (cloud fallback)
       ├── _generate_tts_moss() — POST /api/generate → multipart form (text + prompt_audio OR demo_id) → base64 WAV (48 kHz stereo)
       ├── _generate_tts_vibevoice() — WebSocket /stream → PCM16 → WAV
       ├── _generate_tts_edge() — edge_tts.Communicate → MP3
        ├── _check_moss_health() — GET /api/warmup-status (checks state==ready, cached)
        └── generate_tts() — routes to active engine, falls back to edge-tts on failure

utils/obs_bridge.py
    ├── uses → obsws_python.ReqClient (named methods, NOT generic call)
    ├── called by → web/app.py (19 OBS API routes under /api/obs/*)
    ├── called by → cogs/music.py (auto scene switching via _obs_switch_scene)
    ├── stateless → connects, sends request, disconnects
    └── gracefully degrades → returns {connected: false} when OBS is down
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
| **OBS Studio** | 28+ (29.x on Debian 12) | Optional — visual radio broadcast control via Mission Control |

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
| `aiohttp` | latest | Async HTTP client (Suno, admin cookie fetch, MOSS TTS, VibeVoice TTS) |
| `edge-tts` | latest | Microsoft Edge TTS — generates DJ voice audio (optional but needed for DJ mode) |
| `flask` | latest | Web dashboard (Mission Control) — serves interactive control panel |
| `psutil` | latest | System/process monitoring — memory & CPU stats on the Settings page |
| `syncedlyrics` | latest | Fetches synced (LRC) lyrics for the currently playing song |
| `pytest` | latest | Test framework |
| `pytest-asyncio` | latest | Async test support for pytest |
| `aioresponses` | latest | Mock aiohttp requests in tests |
| `obsws-python` | latest | OBS Studio WebSocket client — control OBS from Mission Control |

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
4. **Installs OBS Studio + obs-websocket 5.x** — `apt install obs-studio`, `xvfb`, `dbus`, `pulseaudio`.
5. **Configures obs-websocket** — Generates a random WebSocket password, writes it to `.env`, copies default scene collection and profile.
6. **Starts headless OBS Studio** — `xvfb-run -a obs --collection 'Radio DJ'`.
7. **Runs the .env setup wizard** — Interactive prompts for:
   - Discord Bot Token (required)
   - YouTube API Key (optional, press Enter to skip)
   - Log Channel ID (optional, press Enter to skip)
8. **Initializes project structure** — Creates `cogs/__init__.py`, `utils/__init__.py`, `yt_dlp_cache/` directory.
9. **Starts the bot in foreground** — `exec venv/bin/python bot.py`

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

### Method C: Proxmox LXC

```bash
# For Proxmox LXC containers with GPU passthrough (Debian 12)
bash setup-lxc.sh
```

This installs everything including systemd services for auto-start on boot.

### Comparison of Launcher Scripts

| Feature | `start.sh` | `launch.sh` | `setup-lxc.sh` |
|---|---|---|---|
| Interactive .env wizard | ✅ Yes | ❌ No | ❌ No |
| Colored terminal output | ✅ Yes | ❌ No | ❌ No |
| Auto-installs Python3 | ✅ Yes | ❌ No | ✅ Yes |
| Auto-installs screen | ✅ Yes | ❌ No | ✅ Yes |
| OBS Studio auto-setup | ✅ Yes | ❌ No | ✅ Yes |
| systemd auto-start | ❌ No | ❌ No | ✅ Yes |
| Screen session name | `mbot` | `musicbot` | — (systemd) |
| Default run mode | Foreground | Background (screen) | Systemd service |
| `doctor` subcommand | ❌ No | ✅ Yes (runs pytest) | ❌ No |
| `logs` subcommand | ✅ Yes | ❌ (use `attach` instead) | `journalctl` |

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
OLLAMA_DJ_VOICE = os.environ.get("OLLAMA_DJ_VOICE", "en_news_male")
OLLAMA_DJ_TIMEOUT = int(os.environ.get("OLLAMA_DJ_TIMEOUT", "15"))
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", 0) or 0) or None
TTS_MODE = os.environ.get("TTS_MODE", "moss").lower()
MOSS_TTS_URL = os.environ.get("MOSS_TTS_URL", "http://localhost:18083")
MOSS_VOICE = os.environ.get("MOSS_VOICE", "en_warm_female")
VIBEVOICE_TTS_URL = os.environ.get("VIBEVOICE_TTS_URL", "http://localhost:3000")
OBS_WS_ENABLED = os.environ.get("OBS_WS_ENABLED", "true").lower() == "true"
OBS_WS_HOST = os.environ.get("OBS_WS_HOST", "localhost")
OBS_WS_PORT = int(os.environ.get("OBS_WS_PORT", "4455"))
OBS_WS_PASSWORD = os.environ.get("OBS_WS_PASSWORD", "")
OBS_AUTO_SCENES = os.environ.get("OBS_AUTO_SCENES", "false").lower() == "true"
OBS_SCENE_NOW_PLAYING = os.environ.get("OBS_SCENE_NOW_PLAYING", "️ Now Playing")
OBS_SCENE_DJ_SPEAKING = os.environ.get("OBS_SCENE_DJ_SPEAKING", "🎙️ DJ Speaking")
OBS_SCENE_WAITING = os.environ.get("OBS_SCENE_WAITING", "⏳ Waiting")
OBS_SCENE_OVERLAY = os.environ.get("OBS_SCENE_OVERLAY", "📺 Overlay Only")
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
| `OLLAMA_DJ_VOICE` | `en_news_male` | TTS voice for the AI side host (separate from main DJ, MOSS `en_news_male` by default) |
| `OLLAMA_DJ_TIMEOUT` | `15` | Ollama API call timeout in seconds |
| `OLLAMA_CUSTOM_MODEL` | `mbot-sidehost` | Custom Ollama model name — auto-created from base model + Modelfile with DJ personality |
| `TTS_MODE` | `moss` | TTS engine: `"moss"` (local CPU), `"vibevoice"` (WebSocket), or `"edge-tts"` (cloud) |
| `MOSS_TTS_URL` | `http://localhost:18083` | MOSS-TTS-Nano server URL |
| `MOSS_VOICE` | `en_warm_female` | Default MOSS voice (corresponds to `assets/moss_voices/en_warm_female.wav`) |
| `VIBEVOICE_TTS_URL` | `http://localhost:3000` | VibeVoice-Realtime server URL |
| `OBS_WS_ENABLED` | `true` | Enable OBS WebSocket integration |
| `OBS_WS_HOST` | `localhost` | OBS WebSocket server hostname |
| `OBS_WS_PORT` | `4455` | OBS WebSocket server port |
| `OBS_WS_PASSWORD` | `""` | OBS WebSocket auth password (auto-generated by start.sh) |
| `OBS_AUTO_SCENES` | `false` | Auto-switch OBS scenes based on playback state |
| `OBS_SCENE_NOW_PLAYING` | `️ Now Playing` | Scene name when a song is playing |
| `OBS_SCENE_DJ_SPEAKING` | `🎙️ DJ Speaking` | Scene name when the DJ is speaking |
| `OBS_SCENE_WAITING` | `⏳ Waiting` | Scene name when the queue is empty |
| `OBS_SCENE_OVERLAY` | `📺 Overlay Only` | Scene name for YouTube Live overlay |
| `LOCAL_TTS_URL` | `""` | Backward-compatible alias for older .env files |

### DJ Mode Configuration (`config.py`)

| Constant | Default | Purpose |
|---|---|---|
| `DJ_VOICE` | `en_warm_female` | Default TTS voice for DJ commentary (MOSS `en_warm_female`, VibeVoice `en-Carter_man`, or Edge TTS `en-US-AriaNeural`) |
| `DJ_EMOJI` | 🎙️ | Emoji used in DJ command embeds |
| `STATION_NAME` | From `.env` or `"MBot"` | Station name used in station ID lines ("You're tuned in to {STATION_NAME} Radio") |
| `CROSSFADE_DURATION` | `3` (seconds) | Fade-in duration when a new song starts |

### TTS Engine Configuration (`config.py`)

The DJ mode and AI side host voices can be generated by three TTS engines, controlled by the `TTS_MODE` setting:

| Constant | Default | Purpose |
|---|---|---|
| `TTS_MODE` | `moss` | Which TTS engine to use: `"moss"`, `"vibevoice"`, or `"edge-tts"` |
| `MOSS_TTS_URL` | `http://localhost:18083` | MOSS-TTS-Nano server URL (only when `TTS_MODE=moss`) |
| `MOSS_VOICE` | `en_warm_female` | Default MOSS voice (only when `TTS_MODE=moss`). Corresponds to `assets/moss_voices/en_warm_female.wav` |
| `VIBEVOICE_TTS_URL` | `http://localhost:3000` | VibeVoice-Realtime server URL (only when `TTS_MODE=vibevoice`) |
| `LOCAL_TTS_URL` | `""` | Backward-compatible alias — if set, used as fallback for `MOSS_TTS_URL` or `VIBEVOICE_TTS_URL` |

```ini
# .env — TTS Engine

# "moss" (default) — MOSS-TTS-Nano server (local CPU, voice cloning, ~2-8s latency).
# "vibevoice" — VibeVoice-Realtime WebSocket server (local GPU/CPU).
# "edge-tts" — Microsoft Edge TTS (cloud fallback, always available).
TTS_MODE=moss

# MOSS-TTS-Nano server URL (only used when TTS_MODE=moss).
# Start with: moss-tts-nano serve --port 18083
# Or use docker-compose which includes it automatically.
MOSS_TTS_URL=http://localhost:18083

# Default MOSS voice. Voice names correspond to .wav files in assets/moss_voices/.
# Built-in: en_warm_female, en_news_male. Add your own .wav files for custom voices.
MOSS_VOICE=en_warm_female

# VibeVoice server URL (only used when TTS_MODE=vibevoice).
VIBEVOICE_TTS_URL=http://localhost:3000
```

> **Fallback chain:** If the primary engine fails, the bot automatically falls back to edge-tts. For MOSS, a health check (`GET /api/warmup-status`, checks `state == "ready"`) determines if the server is warmed up and reachable. If not, the fallback is nearly instant instead of waiting for a long timeout. The health check result is cached (30s for healthy, 10s for down) to avoid hammering the server on every TTS call.

> **Voice name resolution:** The `_resolve_voice()` function detects mismatched voice names (e.g. `en-US-AriaNeural` when using MOSS) and swaps them for the target engine's default. This means switching `TTS_MODE` doesn't require changing `DJ_VOICE` — the bot adapts automatically.

> **Legacy alias:** `TTS_MODE=local` still works and maps to `vibevoice` with a deprecation warning in the logs. `TTS_MODE=kokoro` auto-redirects to `moss` with a warning. `LOCAL_TTS_URL` is used as a fallback URL if `MOSS_TTS_URL` or `VIBEVOICE_TTS_URL` aren't set.

> **Note:** DJ mode is off by default and must be enabled per-guild with `?dj`. The `DJ_VOICE` setting is just the default — users can override it per-guild with `?djvoice` or via the web dashboard.

### MOSS-TTS-Nano Setup (Default, Recommended)

MOSS-TTS-Nano is the default engine — it's CPU-friendly (no GPU needed) and uses voice cloning via prompt audio files for natural-sounding output.

1. **Install MOSS-TTS-Nano:**
    ```bash
    pip install moss-tts-nano
    ```
    Or use docker-compose which includes it automatically (the `moss-tts` service is built from `./moss-tts-server/Dockerfile`).

2. **Start the MOSS-TTS-Nano server:**
    ```bash
    # On the same machine:
    moss-tts-nano serve --host 0.0.0.0 --port 18083 --device auto

    # On a remote machine (e.g. 172.16.1.125):
    moss-tts-nano serve --host 0.0.0.0 --port 18083 --device auto
    ```
    On CPU-only systems, `--device auto` is ignored and forced to `cpu`. The server will warm up on first start (~30 seconds for model download + warmup synthesis). The bot's health check polls `GET /api/warmup-status` and waits until `state == "ready"` before sending TTS requests.

3. **Verify it's running:**
    ```bash
    curl http://localhost:18083/api/warmup-status
    # Look for "state": "ready" in the response
    ```

4. **Test TTS generation:**
    ```bash
    curl -X POST http://localhost:18083/api/generate \
      -F "text=Hello world" \
      -F "demo_id=demo-1" \
      -F "max_new_frames=200" \
      -o test_response.json
    # The response contains "audio_base64" with base64-encoded 48 kHz stereo WAV
    ```

5. **Configure MBot** — in your `.env` file:
    ```ini
    TTS_MODE=moss
    MOSS_TTS_URL=http://localhost:18083       # same machine
    # MOSS_TTS_URL=http://172.16.1.125:18083  # remote server
    DJ_VOICE=en_warm_female
    OLLAMA_DJ_VOICE=en_news_male
    ```

6. **Add custom prompt audio files** (optional but recommended):
    Place `.wav` files in `assets/moss_voices/`. The voice name is the filename without the `.wav` extension (e.g. `assets/moss_voices/custom_voice.wav` → voice name `custom_voice`). When a prompt file exists, the bot uploads it for voice cloning. When no prompt file exists, the bot sends `demo_id=demo-1` to use the server's built-in demo English voice.

> **Important:** The `POST /api/generate` endpoint **requires** either `prompt_audio` (file upload) or `demo_id` (string). Sending neither returns `400 Bad Request` with `{"error": "demo_id is required unless prompt speech is uploaded."}`. The bot handles this automatically — if no `.wav` prompt file is found for the requested voice, it sends `demo_id=demo-1` as a fallback.

**Built-in MOSS voices:**

| Voice Name | File | Description |
|---|---|---|
| `en_warm_female` | `assets/moss_voices/en_warm_female.wav` | Warm female voice — **default DJ voice** |
| `en_news_male` | `assets/moss_voices/en_news_male.wav` | News-style male voice — good for AI side host |

**MOSS server demo voices** (available even without prompt audio files):

| Demo ID | Language | Description |
|---|---|---|
| `demo-1` | English? | First built-in demo voice — used as fallback when no prompt audio is available |
| `demo-2`+ | Various | Additional built-in demo voices from the MOSS server's `assets/demo.jsonl` |

> **Custom voices:** To add your own MOSS voice, place a `.wav` file (ideally 5-15 seconds of clean speech, no background music/noise) in `assets/moss_voices/`. The voice name becomes available automatically — e.g. `my_voice.wav` → `?djvoice my_voice`. The DJ will speak in the cloned voice of whoever is in that recording.

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

| Feature | MOSS-TTS-Nano | VibeVoice-Realtime | Edge TTS (cloud) |
|---|---|---|---|
| Latency | ~2-8s per clip on CPU | ~300ms first audio | 2-5 seconds |
| Server needed | FastAPI server (:18083) | WebSocket server (:3000) | No |
| GPU required | No (CPU-friendly, 0.1B params) | Recommended | No |
| Voice names | `en_warm_female`, `en_news_male`, etc. (prompt audio files) | `en-Carter_man`, etc. | `en-US-AriaNeural`, etc. |
| Multilingual | 20+ languages | English primary, 9 experimental | 40+ languages |
| Quality | Natural, voice cloning from prompt audio | Natural, expressive | Natural, consistent |
| Cost | Free (runs locally) | Free (runs locally) | Free (no API key) |
| Internet required | No (after pip install) | No (after model download) | Yes |
| Voice cloning | Yes (prompt audio files) | No | No |
| Output | 48 kHz stereo WAV | PCM16 → WAV | MP3 |
| Open source | Yes (Apache 2.0) | Yes (MIT) | No (cloud service) |

**Available VibeVoice voice presets:**
- `en-Carter_man` — Male, warm (default)
- `en-Journalist_woman` — Female, professional
- Plus 9 experimental voices in German, French, Italian, Japanese, Korean, Dutch, Polish, Portuguese, and Spanish (download with `bash demo/download_experimental_voices.sh`)

> **Note:** When any local TTS engine (MOSS or VibeVoice) is unreachable, MBot will fall back to Edge TTS automatically (if `edge-tts` is installed). For MOSS, a health check detects failures quickly (cached 30s/10s). This provides graceful degradation — if your local server goes down, the DJ still works, just with higher latency.

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
3. Accesses the queue via `peek_queue(guild_id)` (public API method) to iterate without consuming items.

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

**Text-to-Speech**: The bot supports three TTS engines — MOSS-TTS-Nano (default, voice cloning via prompt audio), VibeVoice-Realtime (WebSocket), and Microsoft Edge TTS (cloud fallback). The active engine is controlled by `TTS_MODE` in config.

**Flow (with MOSS-TTS-Nano):**
1. `generate_intro()` or `generate_outro()` picks a random message template and fills in song titles.
2. `_generate_tts_moss()` sends `POST /api/generate` with multipart form data (text + prompt_audio file) to the MOSS server, receives JSON with `audio_base64` (base64-encoded WAV).
3. The WAV data is decoded and saved to a temp file.
4. `FFmpegPCMAudio` plays the temp WAV through the voice channel.
5. The `after` callback on the TTS player triggers `_on_tts_done()`, which cleans up the temp file and calls `_play_song_after_dj()` to start the real song.
6. `cleanup_tts_file()` deletes the temp file.

### Per-Guild State

| Dictionary | Key | Value | Purpose |
|---|---|---|---|
| `dj_enabled` | `guild_id` | `bool` | Whether DJ mode is on for this guild |
| `dj_voice` | `guild_id` | `str` | Edge TTS voice name (default: `en-US-AriaNeural`) |
| `dj_playing_tts` | `guild_id` | `bool` | Whether a TTS intro is currently playing (prevents re-entrance) |
| `_current_tts_path` | `guild_id` | `str\|None` | Path to the temp MP3 file (for cleanup) |
| `_dj_pending` | `guild_id` | `(ctx, data, channel_id)` | Held song data waiting for TTS intro to finish |

### Message Templates

The DJ has **~480 built-in line templates** across 10 categories, including funny, serious, and weird variants. ~150 of these include `{sound:name}` tags that trigger sound effects after the DJ speaks.

| Category | Count | With Sound Tags | Example |
|---|---|---|---|
| **Session Intros** | 67 | ~25 | `"{greeting} We are LIVE! Let's kick it off with {title}. {sound:airhorn}"`, `"{greeting} My therapist said I should open up more. So here's {title}."` |
| **Song Intros** | 65 | ~20 | `"Up next, {title}!"`, `"Incoming! {title}! {sound:airhorn}"`, `"{title} has entered the chat. Everyone act normal."` |
| **Hype Intros (Loud)** | 41 | ~20 | `"YES! {title}! Let's go!"`, `"Buckle up! {title}! {sound:air_raid}"`, `"REALITY SHIFT DETECTED! {title}!"` |
| **Outros** | 42 | ~12 | `"That was {title}."`, `"{title} — done and dusted. {sound:button_press}"`, `"That was {title}. The simulation has recorded your reaction."` |
| **Transitions** | 59 | ~20 | `"That was {prev_title}. Next up, {next_title}."`, `"From {prev_title} to {next_title}. In the mix! {sound:dj_scratch}"`, `"The vibe goblin ate {prev_title}."` |
| **Hype Transitions** | 17 | ~10 | `"That was {prev_title}! And NOW — {next_title}! {sound:airhorn} LET'S GO!"` |
| **Mellow Transitions** | 17 | ~6 | `"Lovely track. Here's {next_title} to keep the vibe going. {sound:dj_scratch}"`, `"Let's pretend we're in a coffee shop with {next_title}."` |
| **Final Outros** | 39 | ~12 | `"The queue's empty but the radio stays on."`, `"End of the road. {sound:applause}"`, `"Queue status: NULL. Reality status: QUESTIONABLE."` |
| **Station IDs** | 36 | ~12 | `"You're tuned in to MBot Radio."`, `"MBot Radio — on the air! {sound:air_raid}"`, `"Broadcasting from Sector 7G."` |
| **Listener Callouts** | 43 | ~14 | `"Shoutout to everyone listening right now."`, `"You guys are the best! {sound:applause}"`, `"Someone just sneezed in the chat. Bless you."` |
| **Queue Banter** | 25 | — | `"One more left in the queue."`, `"That's not a queue. That's a lifestyle."`, `"DJ math: {} tracks remaining divided by vibes equals a good time."` |

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

**MOSS-TTS-Nano (local TTS, default):** Voice names correspond to `.wav` prompt audio files in `assets/moss_voices/`. The voice name is the filename without the `.wav` extension. Built-in voices:
- `en_warm_female` — Warm female voice (default DJ voice)
- `en_news_male` — News-style male voice (good for AI side host)

Add your own voices by placing `.wav` files (5-15 seconds of clean speech) in `assets/moss_voices/`.

**Edge TTS (cloud, fallback):** The fallback voice is `en-US-AriaNeural` (female, American English). Use `?djvoices` to see all English voices, or `?djvoices <prefix>` for other languages (e.g., `?djvoices ja` for Japanese).

Popular Edge TTS voices include:
- `en-US-AriaNeural` — Female, American (default fallback)
- `en-US-GuyNeural` — Male, American
- `en-GB-SoniaNeural` — Female, British
- `en-AU-NatashaNeural` — Female, Australian
- `ja-JP-NanamiNeural` — Female, Japanese

**VibeVoice-Realtime (local TTS, when `TTS_MODE=vibevoice`):** Voice presets are loaded from the VibeVoice server's `/config` endpoint. Available voices depend on which `.pt` preset files are in the `demo/voices/streaming_model/` directory.

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

A second radio personality powered by a local LLM (Ollama). Unlike the main DJ that picks from ~480 pre-written templates, the AI side host **writes its own original lines from scratch** — spontaneous banter, hot takes, song roasts, jokes, and commentary that a template system simply cannot produce.

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
OLLAMA_DJ_VOICE=en_news_male  # Separate TTS voice for the side host (TTS-engine-aware default)
OLLAMA_DJ_TIMEOUT=15          # Timeout in seconds (larger models need more time)
```

| Setting | Default | Values | Purpose |
|---|---|---|---|
| `OLLAMA_DJ_ENABLED` | `false` | `true`/`false` | Master switch for AI side host |
| `OLLAMA_HOST` | `http://localhost:11434` | Any URL | Ollama server address |
| `OLLAMA_MODEL` | `gemma4:latest` | Any pulled model | LLM model for generation |
| `OLLAMA_DJ_CHANCE` | `0.25` | `0.0`–`1.0` | How often the side host chimes in |
| `OLLAMA_DJ_VOICE` | `en_news_male` (moss) / `en-Carter_man` (vibevoice) / `en-US-GuyNeural` (edge-tts) | TTS voice name | Separate voice so 2 hosts sound different |
| `OLLAMA_DJ_TIMEOUT` | `15` | Seconds | Max wait before falling back |

### Voice Configuration

The main DJ and AI side host have **separate TTS voices** to create two distinct on-air personalities:

| Host | Default Voice | Config Key | Discord Command | Web Dashboard |
|---|---|---|---|---|
| Main DJ | `en_warm_female` (moss) / `en-US-AriaNeural` (edge-tts) | `DJ_VOICE` | `?djvoice <name>` | Radio page → "🗣️ DJ Voice" |
| AI Side Host | `en_news_male` (moss) / `en-US-GuyNeural` (edge-tts) | `OLLAMA_DJ_VOICE` | `?aidjvoice <name>` | Radio page → "🃏 AI Side Host Voice" |

Use `?djvoices` to see all available voices for the active TTS engine.

> **Note:** When you change a voice in the web dropdown, the change takes effect immediately for the *next* DJ or AI side host line — no restart needed. If the primary TTS engine is down and falls back to edge-tts, voices from other engines (MOSS, VibeVoice) are automatically swapped to the closest compatible default so audio still plays. The dropdown shows a warning when the current voice isn't available in the active TTS engine.

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

### `utils/obs_bridge.py` — OBS WebSocket Bridge

A **stateless** WebSocket bridge connecting to obs-websocket 5.x for full radio station visual control from Mission Control. Each API call connects, sends the request, and disconnects — no persistent WebSocket session is maintained.

**OBSBridge class** — The main interface for OBS control. All methods return dicts with a `"connected"` key indicating OBS reachability.

**Connection backoff:** After a failed connection attempt, the bridge waits 30 seconds (`CONNECTION_RETRY_INTERVAL`) before trying again. This prevents log spam — the dashboard polls OBS status every ~10 seconds, which would otherwise generate 6+ full Python tracebacks per minute when OBS is down.

**Graceful degradation:** All methods return `{"connected": False, "error": "..."}` when OBS is unreachable. The bot never crashes due to OBS being down.

**Auto scene switching:** The `switch_scene(scene_name)` method runs in a daemon thread (fire-and-forget, non-blocking). It never raises exceptions to the caller — if OBS is down, the scene switch silently fails (logged at DEBUG level). Called by `cogs/music.py` via `_obs_switch_scene()` when `OBS_AUTO_SCENES=true`.

**Library interaction:** Uses `obsws_python.ReqClient` with named methods via lambda dispatch in `_safe_call()` — NOT the generic `client.call(request_type, data)` pattern which doesn't exist on `ReqClient`. Example: `lambda c: c.start_stream()`, `lambda c, sn=scene_name: c.set_current_program_scene(sceneName=sn)`.

**Configuration:** `OBS_WS_HOST`, `OBS_WS_PORT`, `OBS_WS_PASSWORD`, `OBS_WS_ENABLED` (from `config.py`).

**Public API methods:**

| Method | Purpose |
|---|---|
| `get_status()` | Returns OBS connection status, current scene, streaming state, recording state |
| `start_streaming()` / `stop_streaming()` / `toggle_streaming()` | Stream lifecycle control |
| `start_recording()` / `stop_recording()` / `toggle_recording()` | Recording lifecycle control |
| `set_current_scene(scene_name)` | Switch to a specific OBS scene |
| `set_source_visibility(source_name, visible)` | Show/hide a source in the current scene |
| `set_source_mute(source_name, muted)` | Mute/unmute an audio source |
| `set_source_volume(source_name, volume)` | Set audio source volume (0.0–1.0) |
| `set_current_transition(transition_name)` | Set the active scene transition |
| `enable_studio_mode()` / `disable_studio_mode()` | Toggle OBS studio mode |
| `start_replay_buffer()` / `stop_replay_buffer()` / `save_replay_buffer()` | Replay buffer control |
| `start_virtual_camera()` / `stop_virtual_camera()` | Virtual camera control |
| `take_source_screenshot(source_name)` | Capture a screenshot of a source |

---

## 14. OBS Studio Integration

The bot integrates with OBS Studio via obs-websocket 5.x, enabling full visual radio broadcast control from Mission Control. OBS runs headlessly (no physical display needed) and is controlled entirely through the web dashboard.

### OBS Bridge (`utils/obs_bridge.py`)

A **stateless** WebSocket bridge that connects to obs-websocket 5.x on demand. Each API call connects, sends the request, and disconnects — no persistent WebSocket session.

**Key design decisions:**
- Uses `obsws_python.ReqClient` with **named methods** (e.g., `client.start_stream()`, `client.set_current_program_scene(sceneName=...)`), NOT the generic `client.call(request_type, data)` pattern which doesn't exist on `ReqClient`.
- Lambda-based named method dispatch in `_safe_call()` ensures each OBS action calls the correct method with the correct parameters.
- **Connection backoff:** After a failed connection attempt, the bridge waits 30 seconds (`CONNECTION_RETRY_INTERVAL`) before trying again. This prevents log spam when OBS is down — the dashboard polls OBS status every 10 seconds, which would otherwise generate 6+ full Python tracebacks per minute.
- **Graceful degradation:** All methods return `{"connected": False, "error": "..."}` when OBS is unreachable. The bot **never crashes** due to OBS being down — the web dashboard shows "OBS Disconnected" status and all OBS controls are greyed out.
- **Library logging suppression:** `logging.getLogger("obsws_python").setLevel(logging.CRITICAL)` suppresses the library's verbose connection error logging.

**Public API methods:**

| Method | Purpose |
|---|---|
| `get_status()` | Returns OBS connection status, current scene, streaming state, recording state |
| `start_streaming()` / `stop_streaming()` / `toggle_streaming()` | Stream lifecycle control |
| `start_recording()` / `stop_recording()` / `toggle_recording()` | Recording lifecycle control |
| `set_current_scene(scene_name)` | Switch to a specific OBS scene |
| `set_source_visibility(source_name, visible)` | Show/hide a source in the current scene |
| `set_source_mute(source_name, muted)` | Mute/unmute an audio source |
| `set_source_volume(source_name, volume)` | Set audio source volume (0.0–1.0) |
| `set_current_transition(transition_name)` | Set the active scene transition |
| `enable_studio_mode()` / `disable_studio_mode()` | Toggle OBS studio mode |
| `start_replay_buffer()` / `stop_replay_buffer()` / `save_replay_buffer()` | Replay buffer control |
| `start_virtual_camera()` / `stop_virtual_camera()` | Virtual camera control |
| `take_source_screenshot(source_name)` | Capture a screenshot of a source |

### Auto Scene Switching

When `OBS_AUTO_SCENES=true`, the bot automatically switches OBS scenes based on playback state. Scene switches run in **daemon threads** (non-blocking, fire-and-forget) so they never delay song playback or DJ speech.

| Playback State | Default Scene Name | Config Variable |
|---|---|---|
| Song playing | "️ Now Playing" | `OBS_SCENE_NOW_PLAYING` |
| DJ speaking | "🎙️ DJ Speaking" | `OBS_SCENE_DJ_SPEAKING` |
| Queue empty | "⏳ Waiting" | `OBS_SCENE_WAITING` |
| YouTube Live overlay | "📺 Overlay Only" | `OBS_SCENE_OVERLAY` |

The `switch_scene()` method is fire-and-forget — it runs in a daemon thread and never raises exceptions to the caller. If OBS is down, the scene switch silently fails (logged at DEBUG level).

### OBS Docker Image (`obs-studio/Dockerfile`)

A self-contained headless OBS container for running OBS on servers without a physical display:

**Components:**
- **Xvfb** — Virtual X display (`:99`) for headless rendering
- **PulseAudio** — Null sink for virtual audio routing
- **VNC server** — Remote UI debugging on port 5900 (optional, for troubleshooting scenes)
- **obs-websocket 5.x** — Configured with password from `OBS_WEBSOCKET_PASSWORD` env var

**Default scene collection ("Radio DJ"):**
- "️ Now Playing" — Main scene with song display
- "🎙️ DJ Speaking" — Scene shown when the DJ is talking
- "⏳ Waiting" — Idle/waiting scene
- "📺 Overlay Only" — YouTube Live overlay scene

**Platform:** amd64 only (OBS + VNC on arm64 is impractical in Debian bookworm).

**Usage with docker-compose:**
```yaml
obs:
  build: ./obs-studio
  ports:
    - "4455:4455"   # WebSocket
    - "5900:5900"   # VNC (optional)
  environment:
    - OBS_WEBSOCKET_PASSWORD=${OBS_WEBSOCKET_PASSWORD}
  healthcheck:
    test: ["CMD", "python3", "-c", "import socket; s=socket.socket(); s.settimeout(2); s.connect(('localhost',4455)); s.close()"]
    interval: 30s
    timeout: 10s
    retries: 3
```

### `start.sh` OBS Auto-Setup

The `start.sh` setup wizard now automatically:

1. Installs OBS Studio (`apt install obs-studio`)
2. Installs headless support packages (`xvfb`, `dbus`, `pulseaudio`)
3. Generates a random WebSocket password and writes it to `.env` as `OBS_WEBSOCKET_PASSWORD`
4. Configures obs-websocket 5.x (writes both `config.json` and `global.ini`)
5. Copies the default "Radio DJ" scene collection and profile into `~/.config/obs-studio/`
6. Starts headless OBS via `xvfb-run -a obs --collection 'Radio DJ'`

If OBS is not available (e.g., non-Debian systems), the setup gracefully skips these steps and logs a warning.

### Web Dashboard OBS Controls

Mission Control provides 19 OBS API endpoints under `/api/obs/*` for full visual broadcast control. When OBS is connected, the dashboard shows:

- **Stream/Record controls** — Start, stop, toggle streaming and recording
- **Scene switcher** — Dropdown to switch between OBS scenes
- **Source controls** — Show/hide sources, mute/unmute audio, adjust volumes
- **Scene transition** — Set the active transition type
- **Studio mode** — Enable/disable OBS studio mode
- **Status indicators** — Live connection status, current scene, stream/recording state

When OBS is disconnected, all controls show "OBS Disconnected" and are greyed out. The dashboard polls OBS status every 10 seconds.

---

## 15. CSRF Protection

The Mission Control web dashboard now has CSRF (Cross-Site Request Forgery) protection on all mutating endpoints (POST, PUT, DELETE, PATCH). This prevents malicious websites from making unauthorized requests to the dashboard on behalf of an authenticated user.

### How It Works

**1. Token Generation:**
```python
# In app.py — generated per session
if "_csrf_token" not in session:
    session["_csrf_token"] = secrets.token_hex(64)
```
A random 64-character hex token is generated per session and stored in `session["_csrf_token"]`.

**2. Token Exposure:**
```html
<!-- In base.html — available to all JavaScript -->
<meta name="csrf-token" content="{{ session.get('_csrf_token', '') }}">
```
The token is exposed as a `<meta>` tag in the base template, making it available to all JavaScript on every page.

**3. Automatic Header Injection:**
```javascript
// In base.html — global fetch() shim
const _origFetch = window.fetch;
window.fetch = function(url, options = {}) {
    if (options.method && ['POST', 'PUT', 'DELETE', 'PATCH'].includes(options.method.toUpperCase())) {
        const token = document.querySelector('meta[name="csrf-token"]')?.content;
        if (token) {
            options.headers = options.headers || {};
            options.headers['X-CSRFToken'] = token;
        }
    }
    return _origFetch.apply(this, [url, options]);
};
```
A global `fetch()` shim in `base.html` automatically injects the `X-CSRFToken` header on all mutating requests. This means all existing JavaScript code works without modification.

**4. Server-Side Validation:**
```python
@app.before_request
def validate_csrf():
    if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
        token = session.get('_csrf_token')
        header_token = request.headers.get('X-CSRFToken')
        form_token = request.form.get('_csrf_token')
        if not hmac.compare_digest(token or '', header_token or form_token or ''):
            # Return 403
```
A Flask `before_request` hook validates the token from either the `X-CSRFToken` header (AJAX requests) or the `_csrf_token` form field (HTML form submissions).

**5. Exempted Endpoints:**
- `/login` — No session exists yet when the user logs in
- Cookie bridge CORS endpoints — These are cross-origin API endpoints
- Overlay API — Public endpoints accessed by OBS browser sources

**6. DJ Lines Forms:**
HTML forms on the DJ Lines page (`dj_lines.html`) include a hidden `_csrf_token` field:
```html
<input type="hidden" name="_csrf_token" value="{{ session.get('_csrf_token', '') }}">
```

**7. Error Responses:**
- **API endpoints** (URLs starting with `/api/`) → `403 JSON` with `{"error": "CSRF token missing or invalid"}`
- **Page endpoints** → `403 HTML` error page

### Configuration

CSRF protection is always enabled when `WEB_PASSWORD` is set (session-based auth). When the dashboard is in open-access mode (no password), CSRF is still active but provides less security since there's no session to protect.

### Token Lifetime & Rotation

The CSRF token is generated once per session and **never rotates** for the lifetime of that session. This is acceptable for a self-hosted dashboard because:

- The dashboard is typically used by one person (the bot operator)
- Sessions are short-lived (browser tab lifecycle)
- The threat model is cross-site request forgery, not session hijacking

**For multi-user deployments:** If you expose Mission Control to multiple users, consider rotating the token on each request or implementing a per-request nonce. Flask's `session` is cookie-based (signed, not encrypted by default), so the CSRF token is visible in the cookie. The `hmac.compare_digest` check prevents timing attacks on token comparison.

---

## 16. Web Dashboard: Mission Control

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

#### OBS Studio — *New in v420.0.3*

| Endpoint | Method | Purpose |
|---|---|---|
| `/api/obs/status` | GET | Get OBS connection status, current scene, streaming/recording state |
| `/api/obs/streaming/start` | POST | Start OBS streaming |
| `/api/obs/streaming/stop` | POST | Stop OBS streaming |
| `/api/obs/streaming/toggle` | POST | Toggle OBS streaming |
| `/api/obs/recording/start` | POST | Start OBS recording |
| `/api/obs/recording/stop` | POST | Stop OBS recording |
| `/api/obs/recording/toggle` | POST | Toggle OBS recording |
| `/api/obs/scene` | POST | Set current OBS scene (expects `{"scene": "name"}`) |
| `/api/obs/source/visibility` | POST | Set source visibility (expects `{"source": "name", "visible": true}`) |
| `/api/obs/source/mute` | POST | Mute/unmute audio source |
| `/api/obs/source/volume` | POST | Set audio source volume |
| `/api/obs/transition` | POST | Set current scene transition |
| `/api/obs/studio_mode/enable` | POST | Enable OBS studio mode |
| `/api/obs/studio_mode/disable` | POST | Disable OBS studio mode |
| `/api/obs/replay_buffer/start` | POST | Start replay buffer |
| `/api/obs/replay_buffer/stop` | POST | Stop replay buffer |
| `/api/obs/replay_buffer/save` | POST | Save replay buffer |
| `/api/obs/virtual_camera/start` | POST | Start virtual camera |
| `/api/obs/virtual_camera/stop` | POST | Stop virtual camera |

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

## 17. Soundboard System

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

## 18. DJ Custom Lines

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

## 19. Test Suite

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

## 20. Launcher Scripts

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

7. **OBS Studio auto-setup** — Installs OBS Studio, obs-websocket 5.x, headless support packages (xvfb, dbus, pulseaudio), generates WebSocket password, configures obs-websocket, copies default scene collection and profile, and starts headless OBS via `xvfb-run -a obs --collection 'Radio DJ'`.

---

### `setup-lxc.sh` — Proxmox LXC Setup

**For:** Proxmox LXC containers with GPU passthrough (Debian 12).

```bash
bash setup-lxc.sh
```

A one-shot setup script that installs everything including systemd services for auto-start on boot. This is the recommended method for deploying the bot as a Proxmox LXC container with GPU passthrough for hardware-accelerated OBS encoding.

---

## 21. Complete Command Reference

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

## 22. Troubleshooting & Known Issues

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
| **MOSS TTS not generating audio** | MOSS server not running or hasn't warmed up | Start the server: `moss-tts-nano serve --port 18083`. Check `curl http://localhost:18083/api/warmup-status` — wait until `state == "ready"`. |
| **MOSS TTS falls back to edge-tts** | MOSS server unreachable or health check cached as down | Check the server URL in `.env` (`MOSS_TTS_URL`). Health check results are cached (30s healthy, 10s down) — if server was recently down, wait for cache to expire. |
| **MOSS voice not found** | Missing prompt audio `.wav` file in `assets/moss_voices/` | Voice names must match a `.wav` file: e.g. `en_warm_female` → `assets/moss_voices/en_warm_female.wav`. Add your own `.wav` files for custom voices. If no prompt file exists, the bot uses `demo_id=demo-1` (built-in MOSS demo voice) automatically. |
| **MOSS TTS returns 400 Bad Request** | Missing `demo_id` or `prompt_audio` in the API request | The MOSS API requires either `demo_id` or `prompt_audio` file upload. This should be handled automatically by the bot — if you see this in logs, check that `assets/moss_voices/` exists and that the bot has the latest `utils/dj.py`. |
| **MOSS server takes 30+ seconds to become ready** | Model download + warmup synthesis on first start | Normal behavior on CPU. The bot will fall back to edge-tts during warmup. Wait for `/api/warmup-status` to show `state: "ready"`. |
| **MOSS server on remote machine unreachable** | `MOSS_TTS_URL` points to `localhost` instead of remote IP | Update `.env`: `MOSS_TTS_URL=http://172.16.1.125:18083`. Ensure the server was started with `--host 0.0.0.0` (not just `localhost`). |

### Known Issues

1. **`launch.sh attach` shows blank screen** — The `screen -r` command may display a blank window in some SSH clients. Detach with Ctrl+A D and use `tail -f bot.log` instead.

2. **Progress bar resets on speed change** — When speed is changed, FFmpeg is restarted from position 0:00. The `song_start_time` is reset, so the progress bar restarts from the beginning.

3. **Suno tracks show 0:00 / 0:00** — Suno doesn't provide duration metadata via HTML scraping. The progress bar uses duration=0, which renders a static empty bar.

4. **`?restart` doesn't actually restart** — It only closes the bot. A process supervisor (like `screen` + launcher script, systemd, or pm2) is needed to detect the exit and start a new process.

5. **Log spam after `?leave`** (Fixed in 6.2.0) — Previously, the Now Playing update task continued running after leaving a voice channel. Now properly cancelled.

6. **Interaction already responded error** (Fixed in 6.2.0) — Previously, clicking the "queue" button multiple times would crash the bot. Now uses `ephemeral` responses.

7. **`?fetch_and_set_cookies` crashes with `AttributeError`** — The `admin.py` cog calls `cookie_parser.parse_all_cookies(header)` but this function does not exist in `utils/cookie_parser.py` (that file only contains log-parsing functions). The cookie-fetching admin command will fail at runtime.

8. **Speed values below 0.5 may cause FFmpeg errors** — The `atempo` FFmpeg filter only supports 0.5–2.0 per instance. While the bot's speed ladder starts at 0.25x, attempting to play at that speed may cause FFmpeg to fail. Values below 0.5 require chaining multiple `atempo` filters (e.g., `atempo=0.5,atempo=0.5` for 0.25x).

### Bugs Fixed in v420.0.3

| Bug | Root Cause | Fix |
|---|---|---|
| **`login_required` not defined — web dashboard crash** | `@login_required` decorator used on 19 OBS API routes but never defined — `NameError` on import | Removed redundant `@login_required` decorators — `require_login()` already handles auth globally via `@app.before_request` |
| **`_MissingSentinel` crash on shutdown** | `asyncio.get_event_loop()` returns internal `_MissingSentinel` object during Python teardown; `discord_log_handler.py` called `.is_closed()` on it | Added `hasattr(loop, 'is_closed')` guard before calling `.is_closed()` |
| **OBS Bridge API method mapping** | `_call()` used `client.call(request_type, data)` which doesn't exist on `obsws_python.ReqClient` | Replaced with `_safe_call()` using lambda-based named method dispatch: `lambda c: c.start_stream()`, `lambda c, sn=scene_name: c.set_current_program_scene(sceneName=sn)` |
| **OBS Bridge connection spam** | When OBS not running, every API call produced full Python traceback (6+/min from dashboard polling) | 30-second connection backoff + `logging.getLogger("obsws_python").setLevel(logging.CRITICAL)` |
| **`queue._queue` private attribute access** | 11 external references to `asyncio.Queue._queue` — fragile, breaks if internal implementation changes | Replaced with public API methods: `peek_queue()`, `peek_queue_first()`, `queue_push_front()` |
| **Shallow copy of `YTDL_FORMAT_OPTIONS`** | `get_ytdl_format_options()` returned `dict.copy()` (shallow) — nested dicts like `http_headers` were shared references between callers | Changed to `copy.deepcopy()` in `cogs/youtube.py`, `cogs/music.py`, `web/app.py`, and `tests/test_playlist.py` |
| **OBS healthcheck always succeeds in docker-compose** | Healthcheck used `curl -sf http://localhost:4455 \|\| exit 0` — always returned 0; WebSocket port 4455 doesn't respond to HTTP GET | Proper TCP socket check: `python3 -c "import socket; ..."` plus `pgrep -x obs` |

### Bugs Fixed in 6.4.0

| Bug | Root Cause | Fix |
|---|---|---|
| **Voice dropdown changes don't affect TTS output** | Multiple issues: (1) `data-current` on dropdowns was empty string when no guild voice was set, so the dropdown never showed the actual current voice; (2) `OLLAMA_DJ_VOICE` defaulted to `en-US-GuyNeural` (edge-tts) regardless of active TTS engine — if using MOSS, that voice name got silently swapped to `en_warm_female` by `_resolve_voice()`; (3) when MOSS/VibeVoice fell back to edge-tts, the selected voice was swapped to the engine default with no logging; (4) `_resolve_voice()` used fragile heuristic patterns instead of identifying which engine a voice belongs to | (1) `dj_voice`/`ai_dj_voice` template vars now fall back to `config.DJ_VOICE`/`config.OLLAMA_DJ_VOICE` when no guild voice is set; (2) `OLLAMA_DJ_VOICE` default is now TTS-engine-aware (`en_news_male` for moss, `en-Carter_man` for vibevoice, `en-US-GuyNeural` for edge-tts); (3) `_resolve_voice()` logs all cross-engine swaps with the reason; (4) new helper functions `_is_edge_voice()`, `_is_moss_voice()`, `_is_vibevoice_voice()`, `_engine_for_voice()` provide reliable engine detection; (5) dropdown shows "(current — not available in moss)" warning when the saved voice doesn't exist in the active TTS engine |
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

## 23. Development Guide

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