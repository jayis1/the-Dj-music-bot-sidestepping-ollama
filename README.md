# 🎵 The Radio DJ Music Bot — v6.3.0

> *🎙️ "LADIES AND GENTLEMEN, boys and girls, Discord users of ALL ages — put your hands together, because tonight — TONIGHT — we have something truly, genuinely, historically SPECIAL for you.*
>
> *You've had music bots before. We all have. Those sad little bots that stutter, skip, and quietly give up on life halfway through a playlist. Bots that just... play audio. No soul. No personality. No FIRE.*
>
> ***This is not that bot.***
>
> *This — THIS — is the bot that woke up one day and said 'you know what? I want to be a RADIO STATION.' It has a DJ voice. It has OPINIONS about what time of day it is. It introduces your songs like it's auditioning for a Grammy. It drops airhorns. It plays BED MUSIC under its own voice-overs like a professional. It gives shoutouts to your friends ON THE AIR.*
>
> *It has a WEB DASHBOARD. It has a SOUNDBOARD. It has KEYBOARD SHORTCUTS on the soundboard because it respects your time. It tracks your listening history so you can replay bangers from three hours ago. It auto-fills the queue when you run out of songs SO THE PARTY NEVER HAS TO END.*
>
> *Speed control? Live. Volume control? Live. Queue reordering? Drag and drop, baby. Progress bar? Ticking every second, accurate to your playback speed. Crossfade? Gapless. Lyrics panel? Right there. Playlist support? THE WHOLE PLAYLIST — not just 25 tracks — THE WHOLE THING.*
>
> *It has 172 unique DJ broadcast lines. Seventy. Two. And 74 of them trigger actual sound effects. From the internet. It picked them itself.*
>
> ***You didn't come here for a music bot. You came for a radio station. And a radio station is exactly what you're gonna get.***
>
> *This is The Radio DJ Music Bot. And IT — IS — LIVE."* 🎚️🔥

---

The radio dj music bot is a self-contained Discord music bot built with Python and `discord.py`. It plays audio from YouTube (URLs, searches, playlists) and Suno (direct song URLs) directly into Discord voice channels, with a full radio DJ personality, web dashboard, soundboard, and way more than any sane bot should have.


## ✨ Features
### 🎧 Music & Playback
- Play from **YouTube** (URLs, search queries, full playlists) and **Suno.com**
- **Queue management** — add, remove, clear, shuffle, drag-and-drop reorder
- **Volume** (0–200%) and **Speed** (0.25×–4.0×) control — both live-adjustable from dashboard
- **Loop** toggle for the current track
- **Auto-DJ / Radio mode** — queue auto-refills from a YouTube playlist, a preset, or recently played history
- **Gapless crossfade** between tracks (configurable fade-in duration)

### 🔊 TTS Engine
- **Edge TTS** (default) — Microsoft voices, 100+ in 40+ languages, no server needed
- **Local TTS** — routes to a [VibeVoice-Realtime](https://github.com/microsoft/VibeVoice) server on your hardware (~300ms, no cloud)
- Switch modes with `TTS_MODE=local` in `.env` — zero code changes required
- Settings page shows live TTS engine status

### 🎙️ DJ Mode
- TTS voice commentary between every track (intro, transition, outro)
- **172 built-in DJ lines** across 10 categories — 74 with embedded sound effect tags
- **Custom DJ lines** — add your own via the web dashboard with `{title}`, `{sound:name}` tags
- **DJ bed music** — ambient pad plays softly under commentary for a real radio feel
- **Shoutouts** — `?shoutout @user` fires a live on-air shoutout with TTS + sound effects
- **Per-guild toggle** — `?dj` on/off per server, voice changeable with `?djvoice`
- Works with **both TTS engines** — Edge TTS or Local VibeVoice
- **🤖 AI Side Host** — a second radio personality powered by a local LLM (Ollama) that writes its own spontaneous banter, hot takes, and shoutouts alongside the main DJ

---

## 🔊 TTS Engine — Local vs Edge

The bot supports two TTS engines, switchable via `.env` with no code changes.

| | **Edge TTS** (default) | **Local TTS (VibeVoice-Realtime)** |
|---|---|---|
| Set via | `TTS_MODE=edge-tts` | `TTS_MODE=local` |
| Latency | 2–5 seconds | ~300ms |
| Requires | `pip install edge-tts` | VibeVoice server running locally |
| Voices | 100+ in 40+ languages (e.g. `en-US-AriaNeural`) | Custom voices (e.g. `en-Carter_man`) |
| Cloud | Microsoft TTS API | Fully local — GPU/CPU on your machine |
| Internet | Required | Not required |

### Setting Up Local TTS
```bash
# 1. Clone and start VibeVoice-Realtime
git clone https://github.com/microsoft/VibeVoice
cd VibeVoice
pip install -r requirements.txt
python demo/vibevoice_realtime_demo.py --model_path microsoft/VibeVoice-Realtime-0.5B

# 2. Set in .env
TTS_MODE=local
LOCAL_TTS_URL=http://localhost:3000
DJ_VOICE=en-Carter_man   # Use a VibeVoice voice name
```

Voice names for local mode look like: `en-Carter_man`, `en-Journalist_woman`, `de-Anna_woman`.  
Use `?djvoices` in Discord or the voice dropdown on the Radio page to browse available voices.

> The **Settings page** shows a live TTS Engine status card — green if the configured engine is reachable, red with instructions if it's not.

---

## 🎙️ DJ Mode — Details

When DJ Mode is on (`?dj`), the bot speaks between every track like a real radio host:

- **Intro** — introduces the first song of the session
- **Transitions** — back-announces what just played, introduces what's next
- **Station IDs** — drops "You're tuned in to [Station] Radio" randomly
- **Outros** — plays a smooth sign-off when the queue empties
- **Time-of-day adaptation** — different tone for morning, afternoon, evening, late night

**Sound Effect Tags:** Embed `{sound:airhorn}` anywhere in a custom DJ line and the bot will play that sound right after speaking. 22 sounds available.

---

## 🤖 AI Side Host — Details

The AI side host is a **second radio personality** that chimes in alongside the main DJ.

### How It Works
1. Main DJ picks a structured line (intro/transition/outro) as usual
2. AI side host has a random chance to also speak (controlled by `OLLAMA_DJ_CHANCE`)
3. The AI **receives the main DJ's spoken line** as context and can react to it
4. Both lines go through the same TTS → sound effects → playback pipeline
5. Each host has its own voice so they sound like different people

### Banter Categories

**Independent (fires any time):**
| Category | What the AI does |
|---|---|
| `random_thought` | Drops a funny off-script observation |
| `listener_shoutout` | Hypes or jokes about the crowd |
| `song_roast` | Gently roasts the current/next song |
| `station_trivia` | Deadpan absurd station facts |
| `queue_hype` | Jokes about the queue length |
| `vibe_check` | Rates the mood with comedy |
| `hot_take` | Spicy harmless music opinion |
| `request_prompt` | Begs listeners to request songs |

**Reactive (fires when AI knows what the DJ just said — 60% weight):**
| Category | What the AI does |
|---|---|
| `react_agree` | Agrees with the DJ + adds a funny twist |
| `react_disagree` | Playfully pushes back on the DJ |
| `react_one_up` | Escalates the DJ's joke |
| `react_tangent` | Takes the DJ's line somewhere unexpected |

### Quick Start
```bash
# 1. Install Ollama
curl https://ollama.ai/install.sh | sh

# 2. Pull a model (see recommendations below)
ollama pull phi3:mini

# 3. Set in .env
OLLAMA_DJ_ENABLED=true
OLLAMA_MODEL=phi3:mini

# 4. Toggle on in Discord
?aidj
```

### Recommended Models by VRAM
| Model | VRAM | Speed | Quality |
|---|---|---|---|
| `gemma2:2b` | ~1.5 GB | ⚡ Fastest | Good |
| `llama3.2:3b` | ~2.0 GB | ⚡ Very fast | Very good |
| `phi3:mini` (3.8B) | ~2.3 GB | ⚡ Very fast | **Recommended** |
| `gemma4:latest` | ~3.5 GB | Fast | Best quality |
| `mistral:7b-q4` | ~4.1 GB | Slower | Avoid on ≤4 GB VRAM |

---

## 🌐 Web Dashboard — Details

Available at `http://your-server:8080/`

### Pages
| Page | Path | Description |
|---|---|---|
| **Dashboard** | `/` | Live playback, queue, volume/speed controls |
| **Radio** | `/radio` | DJ voice, Auto-DJ config, AI voice selector, Recently Played |
| **Queue Manager** | `/queue` | Full queue management with drag-and-drop |
| **Soundboard** | `/soundboard` | Sound effects grid + upload |
| **DJ Lines** | `/dj-lines` | Custom DJ line CRUD with visual `{sound:name}` badges |
| **Settings** | `/settings` | System info, Ollama status, Restart/Shutdown |

### 📋 Activity Log Panel *(new in v6.3.0)*
Click **📋 Log** in the sidebar to open a live slide-out log panel:
- Streams the same logs sent to your Discord log channel — **in real-time via SSE**
- **Filter buttons:** All / Info / Warn / Error
- Color-coded severity badges (INFO=blue, WARNING=amber, ERROR=red, DEBUG=gray)
- Auto-reconnects if the connection drops
- No new dependencies — uses native browser `EventSource` API

### 🔄 Dashboard Auto-Refresh System
The dashboard uses 4 independent refresh layers that never interfere with each other:

| System | Interval | What it updates |
|---|---|---|
| **Progress bar ticker** | Every 1 second | Only the progress fill width + elapsed time text |
| **Soft refresh** | Every 30 seconds | Guild status badges, DJ controls, queue, listeners, volume, song title |
| **Song-end refresh** | When progress hits 100% | Full dashboard state for the new track |
| **Fallback refresh** | Every 3 minutes | Full page reload as safety net |

**Why the progress bar never jumps:** The 30-second soft refresh deliberately skips `.progress-bar-fill` and `#elapsed-*` elements. The progress bar runs on a client-side 1-second timer — if the soft refresh also touched it, the bar would jump to wherever the server says the elapsed time is, causing visible jitter. By leaving it alone, the bar stays buttery smooth.

**When a new song starts:** If the soft refresh detects a title change, it replaces the progress bar section and reinitializes the JS timer using the `data-elapsed`, `data-duration`, and `data-speed` attributes on the guild card.

### AI Side Host Dashboard Controls
- 🃏 **AI On/Off button** on dashboard cards (glows purple when active)
- 🃏 **AI badge** on guild cards when side host is enabled
- **AI Side Host Voice selector** on the Radio page
- **Ollama status check** on the Settings page with setup instructions

### API Endpoints (selected)
| Endpoint | Method | Description |
|---|---|---|
| `/api/<guild_id>/play` | POST | Play/resume |
| `/api/<guild_id>/skip` | POST | Skip track |
| `/api/<guild_id>/volume` | POST | Set volume |
| `/api/<guild_id>/speed` | POST | Set playback speed |
| `/api/<guild_id>/ai_dj_toggle` | POST | Toggle AI side host |
| `/api/<guild_id>/ai_dj_voice` | POST | Set AI side host voice |
| `/api/<guild_id>/ai_dj_status` | GET | Get AI side host status |
| `/api/ollama/status` | GET | Check Ollama connectivity + model availability |
| `/api/logs/recent` | GET | Last N log entries as JSON |
| `/api/logs/stream` | GET | SSE stream of live log entries |
| `/api/voices` | GET | List TTS voices (30-min cached) |

### Configuration
```env
WEB_HOST=0.0.0.0
WEB_PORT=8080

# Leave blank for open access, or set a password to enable login
WEB_PASSWORD=

# Optional: pin all now-playing embeds to a specific Discord channel
NOWPLAYING_CHANNEL_ID=0
```

---

## 📜 Command Reference

*(Default prefix: `?`)*

### 🎧 Music Commands
| Command | Description |
|---|---|
| `?join` | Join your voice channel |
| `?leave` | Disconnect from voice |
| `?play <URL/query>` | Play from YouTube (URL, search, or Suno link) |
| `?search <query>` | Search YouTube — shows top 10, pick with `?play <number>` |
| `?playlist <URL>` | Queue an entire YouTube playlist |
| `?radio <URL>` | Queue a YouTube playlist for long radio sessions |
| `?queue` | Show the current queue |
| `?skip` | Skip to next track |
| `?stop` | Stop playback and clear queue |
| `?pause` / `?resume` | Pause / Resume |
| `?clear` | Clear queue (keeps current song) |
| `?remove <number>` | Remove a specific track |
| `?nowplaying` | Show Now Playing embed with controls |
| `?volume <0-200>` | Set volume (100 = normal) |
| `?loop` | Toggle loop for current song |
| `?shuffle` | Shuffle the queue |
| `?speedhigher` / `?speedlower` | Adjust playback speed |

### 🎙️ DJ Commands
| Command | Description |
|---|---|
| `?dj` | Toggle DJ mode on/off |
| `?djvoice [name]` | Show or set the DJ's TTS voice |
| `?djvoices [prefix]` | List available voices (e.g. `?djvoices ja` for Japanese) |
| `?shoutout @user` | Give a live on-air shoutout with TTS + sound effects |

### 🤖 AI Side Host Commands
| Command | Description |
|---|---|
| `?aidj` | Toggle AI side host on/off — shows model, voice, chime-in chance |
| `?aidjvoice [name]` | Show or set the AI side host's separate TTS voice |

### ⚙️ Admin Commands *(Bot owner only)*
| Command | Description |
|---|---|
| `?shutdown` | Safely shut down the bot |
| `?restart` | Restart (auto-reboots if using launcher scripts) |
| `?fetch_and_set_cookies <URL>` | Fetch cookies for age-restricted YouTube content |

---

## ⚙️ Configuration Reference

### Required
```env
DISCORD_TOKEN=your_discord_bot_token
```

### Optional — Core
```env
YOUTUBE_API_KEY=          # Needed for ?search command
LOG_CHANNEL_ID=           # Discord channel ID for log shipping
BOT_OWNER_ID=             # Your Discord user ID (for admin commands)
STATION_NAME=MBot         # Station name in DJ lines ("You're tuned in to MBot Radio")
AUTODJ_SOURCE=            # YouTube playlist URL, "preset:Name", or blank for history replay
NOWPLAYING_CHANNEL_ID=    # Pin now-playing embeds to a specific channel (0 = follow command)
```

### Optional — Web Dashboard
```env
WEB_HOST=0.0.0.0
WEB_PORT=8080
WEB_PASSWORD=             # Leave blank for open access
```

### Optional — TTS Engine
```env
TTS_MODE=edge-tts         # "edge-tts" (default) or "local" (VibeVoice-Realtime)
LOCAL_TTS_URL=http://localhost:3000   # Only used when TTS_MODE=local
DJ_VOICE=en-US-AriaNeural # Edge TTS voice, or VibeVoice name if TTS_MODE=local
```

### Optional — AI Side Host (Ollama)
```env
OLLAMA_DJ_ENABLED=false           # Set to true to activate
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=gemma4:latest        # Recommended: phi3:mini for low VRAM
OLLAMA_DJ_CHANCE=0.25             # Chime-in chance per transition (0.0–1.0)
OLLAMA_DJ_VOICE=en-US-GuyNeural  # Separate TTS voice for the AI host
OLLAMA_DJ_TIMEOUT=15              # Seconds before skipping if Ollama is slow
```

---

## 🚀 Installation

### Step 1 — Clone the repo
```bash
git clone https://github.com/jayis1/the-Dj-music-bot-sidestepping-ollama.git
cd "the-Dj-music-bot-sidestepping-ollama"
```

### Step 2 — Get your Discord Bot Token
1. Go to [Discord Developer Portal](https://discord.com/developers/applications) → New Application → Bot
2. Enable **Message Content Intent** and **Voice State Intent**
3. Copy your **Token**
4. Go to **OAuth2 → URL Generator**, check `bot` + `Administrator`, and invite the bot

### Step 3 — Run the setup wizard *(recommended)*
```bash
bash start.sh
```
The wizard installs all dependencies and walks you through your `.env` config interactively.

### Alternative — Manual setup
```bash
chmod +x launch.sh
./launch.sh setup   # Install deps + create venv
cp .env.example .env
nano .env           # Paste your DISCORD_TOKEN
./launch.sh start   # Start in background (screen session)
```

### Troubleshooting
```bash
./launch.sh doctor   # Runs diagnostics (pytest checks)
./launch.sh attach   # Peek at live background process (Ctrl+A, D to detach)
```

---

## 🔧 Troubleshooting

| Problem | Solution |
|---|---|
| Bot won't play audio | Run `./launch.sh doctor` — likely missing `ffmpeg` or `libopus-dev` |
| DJ voice dropdown stuck on "Loading..." | Fixed in v6.3.0 — update to latest. First load fetches from Microsoft TTS API (~5s), then cached for 30 min |
| `?aidj` says Ollama not running | Install Ollama: `curl https://ollama.ai/install.sh \| sh` and pull a model: `ollama pull phi3:mini` |
| Ollama 404 error in logs | Model not pulled yet — the log now shows the exact `ollama pull <model>` command to run |
| Speed slider doesn't apply | Set speed only after the song has started playing; setting at 1.0× before queuing avoids the race |
| Local TTS not working | Ensure VibeVoice-Realtime is running at `LOCAL_TTS_URL` — Settings page shows live status |
| Local TTS voice not found | Use voices like `en-Carter_man` (not Edge TTS names like `en-US-AriaNeural`) when `TTS_MODE=local` |
| Dashboard 500 error | Check Jinja template `{% if %}`/`{% endif %}` balance — run `./launch.sh doctor` |
| Age-restricted videos won't play | Use `?fetch_and_set_cookies <youtube_url>` to set cookies |
| Bot appears stuck in voice after crash | Restart bot — `on_ready` forces disconnect from all stale voice sessions |

---

## 📚 Further Documentation

For full technical details — architecture, cog internals, all API endpoints, module dependency graph, and development guide — see [GUIDE.md](GUIDE.md).

---

## 🐛 Bugs Fixed in v6.3.0

| Feature | Summary |
|---|---|
| 🔊 **Local TTS Engine** | `TTS_MODE=local` routes DJ speech to a VibeVoice-Realtime server — ~300ms latency, no cloud, runs on your GPU |
| 📋 **Activity Log Panel** | Live Discord-channel-style log panel in Mission Control — real-time SSE streaming, severity filters |
| 🎙️ **Voice Dropdown Fixes** | DJ & AI voice dropdowns now load instantly (30-min server-side cache, DOMContentLoaded fix) |
| 🃏 **AI Reactive Banter** | AI side host now *reacts* to what the main DJ just said — 4 new reactive banter categories |
| 🔧 **Ollama Error Handling** | 404 errors now show the pull command + available models instead of just "status 404" |
| 🔄 **Default Model Update** | Default Ollama model changed from `llama3.2` → `gemma4:latest` across all configs |

---

## 📄 License

MIT — see [LICENSE](LICENSE)
