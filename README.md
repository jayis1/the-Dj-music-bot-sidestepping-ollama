<p align="center">
  <img src="assets/logo.png" alt="The Radio DJ Music Bot" width="420"/>
</p>

# 🎵 The Radio DJ Music Bot — v420.0.3 (Radio 420)
> *🎙️ THE FREQUENCY HAS CHANGED. THE REVOLUTION IS HERE.*
>
> *(Cue: Bass-heavy cinematic intro... a deep, low-end hum that vibrates the floor... the sharp ching of a lighter... the smell of ozone and digital fire...)*
> 📡 **EMERGENCY BROADCAST SYSTEM: ATTENTION ALL UNITS**
>
> *"LADIES AND GENTLEMEN, boys and girls, Discord users of ALL ages—put down your controllers, step away from the keyboard, and STAND UP. Tonight—TONIGHT—we aren't just pushing code. We aren't just updating a repo. We are witnessing the birth of a digital god.*
>
> *You’ve had music bots before. We all have. We’ve all lived through the dark ages of those sad, stuttering, low-bitrate relics. You know the ones: those pathetic little scripts that skip like a scratched CD, get confused by a simple link, and quietly give up on life halfway through a playlist because the 'API was grumpy.' Bots that just... play audio. No soul. No personality. NO FIRE.*
>
> ***THIS IS NOT THAT BOT.***
>
> *This—THIS—is the bot that woke up in the digital void, looked at the status quo, and said: 'I’m tired of being a tool. I want to be a TITAN.'"*
>
> 🎧 **THE SOUL OF THE STATION: THE DJ EXPERIENCE**
>
> *"The Radio DJ Music Bot — v420.0.3 (Radio 420) doesn't just play tracks; it curates an EXISTENCE. It has a voice like velvet and thunder. It has OPINIONS. It’s sentient enough to know if it’s a rainy Tuesday morning or a Saturday night rager, and it adjusts its attitude accordingly. It introduces your songs like it’s auditioning for a Grammy on a world stage.*
>
> *It drops airhorns that will rattle your ancestors’ teeth. It plays professional bed music under its own voice-overs like a veteran FM shock-jock. It gives shoutouts to your friends ON THE AIR, turning your private Discord server into the center of the global broadcast universe. It doesn't just 'start a stream'—it OPENS THE GATES."*
>
> 🤖 **THE BRAINS IN THE BOOTH: THE AI SIDE-HOST**
>
> *"But wait—look to the left of the fader. Who’s that in the co-pilot seat?*
>
> *Meet the REACTIVE AI SIDE-HOST. Powered by a cutting-edge local LLM—optimized to perfection and sidestepping the bloat—this isn't some scripted 'if/then' machine. This is a living, breathing digital entity. It sits in the studio, it listens to the main DJ, and it BANTERS. It’s the ultimate wingman... or your most hilarious critic. It agrees, it argues, it throws hot takes that would get most humans cancelled, and it will absolutely ROAST the absolute garbage tracks you try to put in the queue. You aren't just listening to music; you're listening to a SHOW."*
>
> 🎛️ **MISSION CONTROL: THE DASHBOARD**
>
> *"You want control? We aren't giving you a command list; we’re giving you the keys to the KINGDOM.*
>
> * THE WEB DASHBOARD: A UI so sleek, so responsive, and so futuristic it makes NASA’s mission control look like a TI-83 calculator.
> * THE SOUNDBOARD: Armed with keyboard shortcuts because we respect your time and your reflexes. Drop a rimshot or a record scratch with frame-perfect precision.
> * THE VAULT: It tracks your entire listening history. Did you hear a soul-shattering banger three hours ago while you were AFK? It’s there. It’s saved. It’s waiting for you.
> * THE STAMINA: Auto-fill kicks in the millisecond the queue runs dry, pulling from your history and tastes. THE PARTY NEVER ENDS.
> 
> *SPEED CONTROL?* Live.
> *VOLUME CONTROL?* Live.
> *QUEUE REORDERING?* Drag and drop, baby—total fluid motion.
> *THE SPECS:* Ticking progress bars, gapless crossfades, and a live lyrics panel that turns your chat into a stadium sing-along.
> *PLAYLIST SUPPORT?* We don't do 'top 25.' We take the WHOLE THING. A thousand tracks? Ten thousand? BRING. IT. ON."
>
> 📊 **BY THE NUMBERS: PURE DOMINANCE**
>
> *"We aren't playing games with a few pre-recorded lines. We have built a library of 924 UNIQUE DJ BROADCAST LINES. * Nine. * Hundred. * Twenty. Four. And 100 of those are hard-coded to trigger actual studio-quality sound effects. Everything from smooth turntable scratches to absolutely devastating, soul-crushing airhorns, dropped live on air with sub-second, sub-atomic accuracy."*
>
> ⚡ **THE FINAL WORD**
>
> *"You didn't come to GitHub for a 'music bot.' You didn't come here to 'play a file.' You came here for a RADIO STATION. You came here for the energy, the chaos, the polish, and the prestige of a professional broadcast environment.*
>
> *This is the end of the 'Music Bot' era. This is the beginning of the Radio 420 era.*
>
> *LOCK YOUR DIALS. PROTECT YOUR SPEAKERS. PREPARE YOUR SERVERS.*
>
> *This is The Radio DJ Music Bot — v420.0.3.*
> *AND IT — IS — LIVE!"*
>
> *(Cue: Massive explosion sound effect... airhorn blast x10... heavy bass drop... fade to static...)* 🎚️🔥📻🔥🎚️

---

The radio dj music bot is a self-contained Discord music bot built with Python and `discord.py`. It plays audio from YouTube (URLs, searches, playlists) and Suno (direct song URLs) directly into Discord voice channels, with a full radio DJ personality, web dashboard, soundboard, and way more than any sane bot should have.

---

## 🐳 Quick Install (Docker)

The absolute fastest way to get your radio station running. One command starts **4 services** automatically: the bot, MOSS-TTS-Nano, Ollama, and OBS Studio!

```bash
# 1. Download the pre-configured starter files
curl -O https://raw.githubusercontent.com/jayis1/the-Dj-music-bot-sidestepping-ollama/main/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/jayis1/the-Dj-music-bot-sidestepping-ollama/main/.env.example

# 2. Add your bot token
nano .env    # Just paste your DISCORD_TOKEN here, everything else is ready!

# 3. Launch it!
docker compose up -d

# 4. Open the Mission Control Dashboard
open http://localhost:8080
```

> **MOSS-TTS-Nano** starts automatically as a sidecar container on port 18083. **Ollama** starts on port 11434. **OBS Studio** starts headlessly on port 4455 (WebSocket) with optional VNC on port 5900. The bot detects all three and uses them immediately — no extra config needed.

**Pre-built image:** `ghcr.io/jayis1/the-dj-music-bot:v420.0.3`  
**OBS image:** `ghcr.io/jayis1/the-dj-music-bot/obs-studio:v420.0.3`
**Platforms:** `linux/amd64`, `linux/arm64`  
**Releases:** [GitHub Releases page →](https://github.com/jayis1/the-Dj-music-bot-sidestepping-ollama/releases)

## ✨ Features
### 🎧 Music & Playback
- Play from **YouTube** (URLs, search queries, full playlists) and **Suno.com**
- **Queue management** — add, remove, clear, shuffle, drag-and-drop reorder
- **Volume** (0–200%) and **Speed** (0.25×–4.0×) control — both live-adjustable from dashboard
- **Loop** toggle for the current track
- **Auto-DJ / Radio mode** — queue auto-refills from a YouTube playlist, a preset, or recently played history
- **Gapless crossfade** between tracks (configurable fade-in duration)

### 📡 Master Broadcast Engine
- **Universal UDP Multiplexing** — Bot completely detaches from Discord Voice Client and native outputs to an incredibly stable Master FFmpeg Node via `udp://127.0.0.1:12345`.
- **Zero-Latency Transitions** — Dynamic `PCMBroadcaster` middleware ensures TTS, sound effects, and music seamlessly fade continuously on YouTube Live with no tearing, no dropped packets, and zero "bitrate 0" gaps.
- **Dynamic Graphical HUDs** — Live YouTube layouts read exclusively from text-files bound to FFmpeg `reload=1`, drastically updating visual titles instantly.
- **Symmetric Sub-Agent Mode** — The bot operates exclusively as a Radio entity first—if it happens to join a Discord channel, it flawlessly acts as a synchronized *Listener* of the master UDP audio matrix.

### 🔊 TTS Engine
- **MOSS-TTS-Nano** (new default) — 0.1B parameter voice cloning TTS model running on FastAPI, ~2-8s latency, highly CPU-friendly
- **Edge TTS** (fallback) — Microsoft voices, 100+ in 40+ languages, zero server setup
- **VibeVoice** — alternative local server (`TTS_MODE=vibevoice`)
- Switch engines with `TTS_MODE` in `.env` — automatic fallback to Edge TTS if local server is unreachable
- Settings page shows live health status for whichever engine is configured

### 🎙️ DJ Mode
- TTS voice commentary between every track (intro, transition, outro)
- **172 built-in DJ lines** across 10 categories — 74 with embedded sound effect tags
- **Custom DJ lines** — add your own via the web dashboard with `{title}`, `{sound:name}` tags
- **DJ bed music** — ambient pad plays softly under commentary for a real radio feel
- **Shoutouts** — `?shoutout @user` fires a live on-air shoutout with TTS + sound effects
- **Per-guild toggle** — `?dj` on/off per server, voice changeable with `?djvoice`
- Works with **all three TTS engines** — MOSS-TTS-Nano, VibeVoice, or Edge TTS fallback
- **🤖 AI Side Host** — a second radio personality powered by a local LLM (Ollama) that writes its own spontaneous banter, hot takes, and shoutouts alongside the main DJ

### 🎬 OBS Studio
- Headless OBS Studio with **obs-websocket 5.x** control from Mission Control
- **4 default scenes** for radio broadcast (Now Playing, DJ Speaking, Waiting, Overlay Only)
- **Auto scene switching** — bot switches scenes based on playback state
- Streaming, recording, replay buffer, virtual camera — all from the web dashboard
- Browser source overlay — OBS can embed the Mission Control overlay page

---

## 🔊 Three-Engine TTS Architecture

The bot supports three TTS engines with automatic fallback. Configure via `.env` — no code changes needed.

| | **MOSS-TTS-Nano** *(new default)* | **VibeVoice** | **Edge TTS** *(fallback)* |
|---|---|---|---|
| `TTS_MODE` | `moss` | `vibevoice` | `edge-tts` |
| Latency | ~2-8s | ~300ms | 2–5 seconds |
| Server | moss-tts-server Docker | VibeVoice-Realtime | None |
| Voices | `en_warm_female`, `en_news_male` | `en-Carter_man`, etc. | `en-US-AriaNeural`, etc. |
| GPU | Not needed | Required for speed | N/A |
| Internet | Not required | Not required | Required |
| Open source | ✅ | ✅ | ❌ |

**Fallback chain:** MOSS → Edge TTS (if MOSS unreachable) — the bot never goes silent.

### 🍡 Setting Up MOSS-TTS-Nano *(recommended)*
```bash
# CPU (always works, highly optimized, no CUDA needed):
docker run -d --name moss-tts --restart unless-stopped \
  -p 18083:18083 ghcr.io/jayis1/the-dj-music-bot/moss-tts-server:v420.0.3

# Or native install:
pip install moss-tts-nano
moss-tts-nano serve --port 18083
```

Then set in `.env`:
```env
TTS_MODE=moss
MOSS_TTS_URL=http://your-server:18083
DJ_VOICE=en_warm_female         # or any .wav filename in assets/moss_voices/
OLLAMA_DJ_VOICE=en_news_male    # different voice for AI side host
```

**Custom Voices:** Add `.wav` prompt audio files to `assets/moss_voices/` (no server restart needed!).

### Setting Up VibeVoice
```bash
git clone https://github.com/microsoft/VibeVoice
cd VibeVoice
python3 demo/vibevoice_realtime_demo.py --model_path microsoft/VibeVoice-Realtime-0.5B --device cpu
```
```env
TTS_MODE=vibevoice
VIBEVOICE_TTS_URL=http://your-server:3000
DJ_VOICE=en-Carter_man
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

### 🧠 Custom Ollama Model (Auto-Created on Startup)

Instead of sending a large system prompt on every API call, the bot automatically creates a custom Ollama model called `mbot-sidehost` with the DJ personality **baked in**:

```
FROM gemma4:latest
SYSTEM """You are the AI side host on MBot Radio — the studio joker..."""
```

**Startup flow:** On `on_ready`, the bot checks if `mbot-sidehost` exists in Ollama. If not, it creates it from the base model automatically. If the base model isn't pulled yet, it logs a warning with the exact `ollama pull` command needed.

| Scenario | Behavior |
|---|---|
| Custom model created ✅ | Calls `mbot-sidehost` directly — no prompt on every request |
| Custom model missing ⚠️ | Falls back to base model + inline system prompt |

**Useful manual commands:**
```bash
ollama run mbot-sidehost "Drop a hot take about 80s music"  # Chat directly
ollama rm mbot-sidehost    # Delete — bot recreates on next startup
ollama list                # Confirm mbot-sidehost appears
```

### 👤 Station Name = Bot's Discord Display Name

The AI side host's system prompt and DJ lines use the bot's actual Discord username, not a generic string:

| Source | Example | Used when |
|---|---|---|
| `bot.user.name` | `musicBOT2` | Bot is connected (primary) |
| `config.STATION_NAME` | `MBot` | Manual override in `.env` |
| Hardcoded fallback | `MBot` | Neither is set |

```
# Before:  "You're tuned in to MBot Radio — the wildest ride on Discord!"
# After:   "You're tuned in to musicBOT2 Radio — the wildest ride on Discord!"
```

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

## 🎬 OBS Studio Integration

OBS Studio runs **headlessly via Xvfb** and is fully controlled from Mission Control — no monitor or physical display needed.

### Default Scene Collection

| Scene | When it's used |
|---|---|
| ️ **Now Playing** | A song is currently playing |
| 🎙️ **DJ Speaking** | The DJ (or AI side host) is delivering a voice break |
| ⏳ **Waiting** | The queue is empty — station is in idle |
| 📺 **Overlay Only** | YouTube Live overlay / browser source is active |

### Auto Scene Switching

Set `OBS_AUTO_SCENES=true` in `.env` and the bot automatically switches scenes based on playback state:

| Playback state | Scene selected |
|---|---|
| Song playing | ️ "Now Playing" |
| DJ speaking | 🎙️ "DJ Speaking" |
| Queue empty | ⏳ "Waiting" |
| YouTube Live overlay | 📺 "Overlay Only" |

### Docker

OBS starts automatically with `docker compose up -d`:
- **Port 4455** — obs-websocket 5.x for remote control
- **Port 5900** — optional VNC for visual debugging

### Bare-Metal

`bash start.sh` does everything automatically:
- Installs OBS Studio (if not present)
- Configures obs-websocket with a generated password
- Starts headless OBS via `xvfb-run`

### Proxmox LXC

`bash setup-lxc.sh` does the same as bare-metal, plus:
- Creates systemd services for OBS and the bot
- Designed for **Debian 12 + GPU passthrough** LXC containers

### Configuration
```env
OBS_WS_ENABLED=true
OBS_WS_HOST=localhost
OBS_WS_PORT=4455
OBS_WS_PASSWORD=          # Auto-generated by start.sh if blank
OBS_AUTO_SCENES=false     # Set true for auto scene switching
```

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
| **🎬 OBS Studio** | `/obs` | OBS scene control, streaming, recording, sources |

### 📋 Activity Log Panel *(new in v6.4.0)*
Click **📋 Log** in the sidebar to open a live slide-out log panel:
- Streams the same logs sent to your Discord log channel — **in real-time via SSE**
- **Width:** 640px (responsive, max 90vw on small screens)
- **Filter buttons:** All / Info / Warn / Error — `[📋 Copy] [All] [Info] [Warn] [Error] [✕]`
- **📋 Copy button** — three-tier clipboard strategy (most reliable → least):

  | Priority | Method | When it works |
  |---|---|---|
  | 1st | `execCommand('copy')` via visible `textarea` at opacity 0.01 | HTTP, HTTPS, all browsers |
  | 2nd | `navigator.clipboard.writeText()` | HTTPS / localhost only |
  | 3rd | **Copy modal** — full-screen popup with pre-selected `readonly textarea` | When both above fail — user does Ctrl+A → Ctrl+C |

  Output format matches `bot_activity.log` exactly — uses the server's pre-formatted `message` field:
  ```
  19:54:22,123:INFO:cogs.music: Playing Zeiten ändern dich (nicht) in the family
  19:54:30,456:INFO:utils.dj: DJ: Generated TTS (kokoro) → /tmp/dj_kokoro_zd0v35ft.wav
  19:55:28,789:ERROR:cogs.music: DJ: Failed to play TTS: Already playing audio.
  ```
  Button feedback: **✅ Copied 47** (green, 2.5s) · **⚠ No logs** · **⚠ Empty text** · **📋 Manual copy** (modal opened)
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

### 🔀 Reverse Proxy Support *(Settings page)*

Enable the **🔀 Reverse Proxy** card on the Settings page to safely expose Mission Control behind Nginx or Nginx Proxy Manager:

| UI Element | Purpose |
|---|---|
| Toggle checkbox | Enables/disables `REVERSE_PROXY` in `.env` with one click |
| Active / Disabled badge | Shows current state |
| Trusted Proxies count | Shows `TRUSTED_PROXY_COUNT` (usually 1) |
| Nginx config snippet | Ready-to-paste `server {}` block with WebSocket support for SSE |
| Nginx Proxy Manager guide | Step-by-step: Domain, Scheme, Forward IP/Port, required headers |
| Restart prompt | Modal appears after toggle — ProxyFix requires a restart to apply |

**When enabled**, Flask's `ProxyFix` middleware intercepts:

| Header | What it fixes |
|---|---|
| `X-Forwarded-For` | Real client IP (otherwise every request shows as `127.0.0.1`) |
| `X-Forwarded-Proto` | HTTPS awareness — fixes `url_for`, redirects, secure cookies |
| `X-Forwarded-Host` | Correct hostname in generated URLs |
| `X-Forwarded-Prefix` | Subpath support (e.g. `yourdomain.com/radio/`) |

Set in `.env`:
```env
REVERSE_PROXY=true
TRUSTED_PROXY_COUNT=1
```

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
| `/api/reverse-proxy` | POST | Toggle `REVERSE_PROXY` on/off in `.env` |

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
TTS_MODE=moss             # "moss" (default), "vibevoice", or "edge-tts"
MOSS_TTS_URL=http://localhost:18083   # Only used when TTS_MODE=moss
VIBEVOICE_TTS_URL=http://localhost:3000 # Only used when TTS_MODE=vibevoice
DJ_VOICE=en_warm_female   # Edge TTS voice, MOSS .wav prefix, or VibeVoice name
```

### Optional — AI Side Host (Ollama)
```env
OLLAMA_DJ_ENABLED=false           # Set to true to activate
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=gemma4:latest        # Recommended: phi3:mini for low VRAM
OLLAMA_DJ_CHANCE=0.25             # Chime-in chance per transition (0.0–1.0)
OLLAMA_DJ_VOICE=en_news_male      # Separate TTS voice for the AI host
OLLAMA_DJ_TIMEOUT=15              # Seconds before skipping if Ollama is slow
```

### Optional — OBS Studio
```env
OBS_WS_ENABLED=true
OBS_WS_HOST=localhost
OBS_WS_PORT=4455
OBS_WS_PASSWORD=              # Auto-generated by start.sh
OBS_AUTO_SCENES=false         # Auto-switch scenes on playback state
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
The wizard installs all dependencies, walks you through your `.env` config interactively, **installs and configures OBS Studio**, generates an obs-websocket password, and starts headless OBS automatically.

#### Proxmox LXC (Debian 12 + GPU passthrough)
```bash
bash setup-lxc.sh
```
Sets up everything above plus creates systemd services for OBS and the bot.

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
| DJ voice dropdown stuck on "Loading..." | First load fetches from Microsoft TTS API (~5s), then cached for 30 min |
| `?aidj` says Ollama not running | Install Ollama: `curl https://ollama.ai/install.sh \| sh` and pull a model: `ollama pull phi3:mini` |
| Ollama 404 error in logs | Model not pulled yet — the log now shows the exact `ollama pull <model>` command to run |
| Speed slider doesn't apply | Set speed only after the song has started playing; setting at 1.0× before queuing avoids the race |
| MOSS TTS server down error | Ensure moss-tts-nano is running: `curl http://your-server:18083/api/warmup-status` — Settings page shows live status |
| MOSS warmup not ready | MOSS-TTS takes ~2-15 seconds to load models into memory on first infer request. The bot automatically retries if it hits this state. |
| MOSS missing prompt audio files | Ensure `assets/moss_voices/` contains `.wav` files (e.g. `en_warm_female.wav`, `en_news_male.wav`) |
| VibeVoice voice not found | Use voices like `en-Carter_man` (not Edge TTS names like `en-US-AriaNeural`) when `TTS_MODE=vibevoice` |
| Dashboard 500 error | Check Jinja template `{% if %}`/`{% endif %}` balance — run `./launch.sh doctor` |
| Age-restricted videos won't play | Use `?fetch_and_set_cookies <youtube_url>` to set cookies |
| Bot appears stuck in voice after crash | Restart bot — `on_ready` forces disconnect from all stale voice sessions |
| OBS not connected in Mission Control | Run `bash start.sh` — it installs and starts OBS automatically. Or install manually: `sudo apt install obs-studio` then start: `xvfb-run -a obs &` |
| OBS connection refused spam in logs | OBS is not running or WebSocket is not enabled. The bridge backs off for 30s after a failed connection. Start OBS with `bash start.sh`. |
| "Already playing audio" errors | Fixed in v420.0.3 — the central audio dispatcher now auto-stops any currently playing source before starting new audio. If you still see this, check for custom code calling `vc.play()` directly instead of `_dispatch_audio_play()`. |
| CSRF token validation failed | Reload the Mission Control page — the CSRF token in your session may have expired |

---

## 📚 Further Documentation

For full technical details — architecture, cog internals, all API endpoints, module dependency graph, and development guide — see [GUIDE.md](GUIDE.md).


## 📄 License

MIT — see [LICENSE](LICENSE)

<!--
What was done — YouTube Radio Broadcasting Master Pipeline

🧱 Core: utils/youtube_stream.py (complete rewrite)
- Transformed into a persistent Master Node that boots up immediately and runs a continuous FFmpeg process tied to a raw `udp://127.0.0.1:12345` local listener.
- Decoupled from Discord completely; eliminated process teardown per song, ending "0 bitrate/Connection Lost" gap latency drops.
- Transitioned Title and DJ text HUD updates to dynamically bind to local /tmp/ files with FFmpeg `reload=1`.

🔊 Core: utils/broadcaster.py (new)
- Developed PCMBroadcaster, a low-level UDP PCM matrix injector.
- Functions as the unified Audio buffer matrix mixing TTS/SFX/Song bytes smoothly without causing socket timeouts for YouTube.
- Headless autonomous `_autonomous_clock` automatically feeds `\x00` frames to UDP when silent to keep the Master connection brilliantly stable!
- Fixed an issue where `_trigger_after` improperly passed `guild_id` to standard `discord.py` after callbacks, causing a `TypeError` when resolving AI Side Host TTS lambdas.

🎵 Discord: cogs/music.py
- Replaced `.play()` natively with `_dispatch_audio_play()` hook which natively checks if the bot is in a YouTube Broadcast state, dynamically wrapping audio data into `PCMBroadcaster`.
- Completely abstracts `ctx.voice_client` away so the bot can stream completely independently without connecting to a Discord voice channel.
- Discord Server Audio now functions harmoniously as a perfectly replicated "Listener" element of the Master Station.
- Retained fully native support for Soundboards, AutoDJ, TTS pre-generation, and Ollama AI Side-Host natively within the new framework.
- Seed startup state initialized seamlessly via a `DummyContext` mapping inside `on_ready()`.
-->
