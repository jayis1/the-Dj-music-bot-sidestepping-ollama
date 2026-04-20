# 🎙️ The DJ Music Bot — Hermes Agent Instructions

> **Skills & instructions for the [Hermes Agent](https://docs.ollama.com/integrations/hermes) to autonomously operate the [420 Radio DJ Music Bot](https://github.com/jayis1/the-Dj-music-bot-sidestepping-ollama).**

This repo contains everything Hermes needs to act as the **shadow controller** behind the 420 Radio DJ — fixing cookies, keeping the queue full, watching the YouTube Live stream, and discovering new music. The DJ bot never goes silent.

---

## What Hermes Does for the DJ Bot

| Skill | What | How Often |
|-------|------|-----------|
| **Cookie Fixer** | Detects stale/blocked YouTube cookies, extracts fresh ones from the Firefox cookie.txt plugin, injects them via the Mission Control API | Every 5 min |
| **Queue Watchdog** | Monitors queue depth, enables Auto-DJ and discovers playlists when the queue runs dry | Every 1 min |
| **Stream Monitor** | Watches the YouTube Live stream + OBS health, auto-restarts if the stream dies | Every 30 sec |
| **Playlist Finder** | Browses YouTube and discovers playlists matching the station vibe (lo-fi, rap, reggae, electro swing, EDM) | Every 30 min |
| **Discord Watcher** | *Optional* — Listens for fan-posted YouTube links in a Discord channel, queues them automatically | Disabled |
| **Suno Creator** | Hermes makes original music on Suno.com — reggae about life & weed, lo-fi chill, electro swing — tracks go straight into the DJ bot queue | Every 1 hr |

---

## Architecture

```
┌──────────────────────────────────────────────────┐
│  GUI VM (Debian + XFCE + Firefox)               │
│                                                  │
│  ┌────────────────┐   ┌──────────────────────┐  │
│  │ Firefox        │   │ Hermes Agent         │  │
│  │ (logged into   │◄──►│                      │  │
│  │  YouTube)      │   │  Ollama localhost    │  │
│  │                │   │  hermes3:8b          │  │
│  │  cookie.txt    │   │                      │  │
│  │  plugin ✅     │   │  shadow_controller/ │  │
│  │                │   │    6 autonomous loops│  │
│  │  YT Live tab   │   │    Playwright browser│  │
│  │  (monitoring)  │   │    Mission Control API│  │
│  └────────────────┘   └──────────┬───────────┘  │
│                                  │ HTTP (LAN)   │
└──────────────────────────────────┼──────────────┘
                                   │
                   ┌────────────────▼───────────────┐
                   │  DJ Bot LXC (Proxmox)           │
                   │  Mission Control API :8080      │
                   │  https://github.com/jayis1/     │
                   │  the-Dj-music-bot-sidestepping- │
                   │  ollama                         │
                   └─────────────────────────────────┘
```

---

## Setting Up Hermes

### 1. Install Hermes on your VM

```bash
# Via Ollama (recommended)
ollama launch hermes

# Or manual install
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
```

When the setup wizard asks:

| Prompt | Answer |
|--------|--------|
| **Provider** | More providers → Custom endpoint → `http://127.0.0.1:11434/v1` |
| **API key** | Leave blank (local Ollama, no key needed) |
| **Model** | `hermes3:8b` (or any model you have in Ollama) |
| **Context length** | Leave blank (auto-detect) |
| **Messaging** | Set up later (or connect Discord for alerts) |

### 2. Install the Shadow Controller

```bash
# Clone this repo
git clone https://github.com/jayis1/the-Dj-music-bot-sidestepping-hermes-instructions.git
cd the-Dj-music-bot-sidestepping-hermes-instructions/shadow_controller

# Run the setup wizard
bash setup.sh

# Edit config.yaml with your DJ bot's details
nano config.yaml

# Start the shadow controller
./run.sh
```

### 3. Firefox Setup (on the same VM)

1. **Log into YouTube** — Open Firefox → youtube.com → sign in
2. **Log into Suno** — Open a tab → suno.com → sign in (for Suno Creator)
3. **Install the cookie.txt plugin** — [https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/)
3. **Export cookies once** — Click the cookie.txt plugin button (it saves a `cookies.txt` file)
4. **Keep a YouTube Live tab open** — Navigate to your channel's live URL

The shadow controller reads the `cookies.txt` file exported by the plugin and injects fresh cookies into the DJ bot whenever they go stale.

### 4. Or install as a systemd service (auto-starts on boot)

```bash
# During setup.sh, answer "yes" to systemd installation
# Or manually:
sudo cp systemd/shadow-controller.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now shadow-controller

# Check status
sudo systemctl status shadow-controller

# View live logs
sudo journalctl -u shadow-controller -f
```

---

## Configuration

Copy `config.example.yaml` → `config.yaml` and fill in your settings.

### Required

| Setting | What | Example |
|---------|------|---------|
| `guild_id` | Your Discord server ID | `"123456789012345678"` |
| `bot_api_url` | DJ bot Mission Control URL | `"http://192.168.1.50:8080"` |
| `discord_webhook_url` | Discord webhook for alerts | `"https://discord.com/api/webhooks/..."` |

### Ollama (on the same VM)

| Setting | Default | What |
|---------|---------|------|
| `ollama_url` | `http://localhost:11434` | Ollama endpoint — Hermes runs here |
| `ollama_model` | `hermes3:8b` | Model Hermes uses for playlist decisions |

### Music Vibe

| Setting | Default | What |
|---------|---------|------|
| `genres` | lo-fi, rap, electro_swing, edm, chill_beats, reggae | Genres Hermes searches YouTube for |
| `min_playlist_songs` | `30` | Minimum track count for a playlist to be worth queuing |
| `queue_min_songs` | `3` | Refill queue when it drops below this |

### Stream Watching

| Setting | Default | What |
|---------|---------|------|
| `stream_should_be_live` | `true` | Should the YouTube Live stream always be running? |
| `stream_check_interval` | `30` | How often to check stream health (seconds) |
| `stream_restart_max_attempts` | `3` | Max auto-restart tries before alerting you |

### Cookie Health

| Setting | Default | What |
|---------|---------|------|
| `cookie_check_interval` | `300` | How often to check cookie freshness (seconds) |
| `cookie_max_age_days` | `5` | Cookies older than this get auto-refreshed |

### Firefox

| Setting | Default | What |
|---------|---------|------|
| `firefox_profile_path` | auto-detect | Path to Firefox profile with YouTube login |
| `cookie_txt_path` | auto-detect | Where the cookie.txt plugin exports to |
| `youtube_live_url` | blank | Your YouTube Live URL to keep in a browser tab |
| `headless` | `false` | `false` = use GUI Firefox, `true` = invisible |

### Fan Requests (Optional — Disabled by Default)

| Setting | Default | What |
|---------|---------|------|
| `fan_request_enabled` | `false` | Enable Discord fan request watching |
| `discord_watcher_token` | blank | **Separate** Discord bot token (not the DJ bot's) |
| `fan_request_channel_id` | blank | Channel to watch for fan YouTube links |

### Suno Creator (Enabled by Default)

| Setting | Default | What |
|---------|---------|------|
| `suno_enabled` | `true` | Enable original music creation on Suno.com |
| `suno_creation_interval` | `3600` | How often to create a new track (1 hour) |
| `suno_max_pending` | `3` | Max tracks waiting for generation before pausing |
| `suno_auto_queue` | `true` | Auto-queue finished tracks into the DJ bot |

---

## How Each Skill Works

### 🍪 Cookie Fixer

Hermes keeps the DJ bot's YouTube access alive. Cookies expire, YouTube blocks bots, the bot goes silent. Not on Hermes's watch.

```
Every 5 minutes:
  1. GET /api/ytcookies/health → how old are the cookies?
  2. GET /api/ytcookies/auth_status → is YouTube blocking the bot?
  3. If stale or blocked:
     a. Read cookies.txt from the Firefox cookie.txt plugin export
     b. Or extract cookies from the Playwright browser context
     c. POST /api/ytcookies/inject → fresh cookies into the DJ bot
     d. Verify auth block is cleared
  4. Alert via Discord webhook if fix succeeded or failed
```

### 🎵 Queue Watchdog

The station never goes silent. When the queue dips below 3 songs:

```
Every 1 minute:
  1. Check queue depth via Mission Control API
  2. If queue < 3 songs:
     a. Enable Auto-DJ if not already on
     b. Ask Playlist Finder for a playlist and set it as Auto-DJ source
     c. Load a saved preset as fallback
     d. Replay from recently-played history as last resort
  3. Alert if queue was refilled
```

### 📺 Stream Monitor

YouTube Live goes down, viewers leave. Hermes catches it in 30 seconds.

```
Every 30 seconds:
  1. GET /api/<guild_id>/youtube_stream/status → is the stream running?
  2. GET /api/obs/status → is OBS connected and streaming?
  3. If stream is down when it should be live:
     a. POST /api/obs/streaming/start → restart OBS streaming
     b. If that fails: POST /api/obs/streaming/configure_and_start → full restart
     c. If that fails: POST /api/<guild_id>/youtube_stream/toggle → toggle stream
  4. If OBS disconnected:
     a. POST /api/obs/reconnect → force reconnect
  5. Keep the YouTube Live browser tab alive
  6. Alert via Discord webhook on any recovery action
```

### 🔍 Playlist Finder

Hermes browses YouTube like a music director, finding playlists that match your station's vibe.

```
Every 30 minutes:
  1. Pick a genre (rotate: lo-fi, rap, reggae, electro swing, EDM, chill beats)
  2. Navigate YouTube search in the Playwright browser
  3. Extract search results (playlist titles + URLs)
  4. Ask Hermes (via Ollama) to evaluate:
     - 30+ songs? ✅
     - Matching genre? ✅
     - Recent? ✅
     - Good variety? ✅
  5. Set best playlist as Auto-DJ source:
     POST /api/<guild_id>/autodj_source
  6. Cache discovered playlists for the Queue Watchdog
```

**Hermes's reasoning prompt:**
```
You are the music director for an online radio station.
Pick the 2-3 BEST playlists that:
- Have 30+ songs (look for "50 videos", "100+ videos" etc)
- Match the genre (lo-fi/rap/reggae/electro swing/EDM)
- Are playlists (URLs containing /playlist?list=) NOT individual videos
- Are recent (2023-2025)
Reply with ONLY the YouTube playlist URLs, one per line.
```

### 💬 Discord Watcher (Optional)

Watch a Discord channel for fan-posted YouTube links and queue them.

```
On every message in the fan request channel:
  1. Extract YouTube URLs from message text
  2. Validate it's a video or playlist URL
  3. POST /api/<guild_id>/play → queue it in the DJ bot
  4. React with 🎵 emoji to acknowledge
  5. Alert via Discord webhook
```

**Needs a separate Discord bot token** (not the DJ bot's token). Create one at [Discord Developer Portal](https://discord.com/developers/applications) with Message Content Intent enabled.

### 🎶 Suno Creator

While Hermes waits between checks, it creates **original music** on Suno.com. The DJ bot already supports Suno URLs natively — so fresh originals go straight into the queue. Your station plays tracks that no other station has.

```
Every 1 hour:
  1. Generate a song idea:
     a. Ask Hermes for a creative concept (reggae, weed, life themes)
     b. Or pick from 15 preset ideas (reggae about mangoes & weed,
        dub about the herb garden, lo-fi about being a bot DJ, etc.)
  2. Open Suno.com/create in the browser
  3. Fill in the prompt (lyrical theme) and style (musical description)
  4. Click Create
  5. Wait for Suno to generate the track
  6. Extract the track URL from the page
  7. POST /api/<guild_id>/play → queue the original in the DJ bot
  8. Alert: "Original Suno track queued"
```

**Hermes's creative prompt:**
```
You are the creative director for a 24/7 radio station called MBot Radio.
Generate ONE original song idea for Suno.com.

Pick from these vibes: reggae about life and weed, lo-fi chill,
electro swing party, underground rap, cosmic EDM, radio station meta humor.

Reply in EXACT format:
GENRE: [genre]
PROMPT: [detailed song description 2-3 sentences — be creative, funny, specific]
STYLE: [musical style with tempo and instruments]
```

**Example presets Hermes can pick from:**
- `"A reggae song about a lazy Sunday, smoking weed on the porch, watching the world go by"` → roots reggae, 75 bpm
- `"A dub reggae instrumental about the herb garden growing tall, bass you can feel in your chest"` → dub reggae, 70 bpm
- `"A lo-fi track about being too high to change the song, the same chill beat loops forever"` → lo-fi hip hop, 65 bpm
- `"An electro swing song about a radio station that broadcasts 24/7 and never stops"` → electro swing, 128 bpm
- `"A rap song about running an underground radio station out of a server rack"` → underground hip hop, 90 bpm

**Requires:** Firefox logged into suno.com on the VM.

---

## Alert System

Hermes keeps you in the loop without spamming:

| Alert | When | Level |
|-------|------|-------|
| 🔴 Cookies expired — auto-refreshing... | Cookies stale or auth blocked | Warning |
| 🟢 Cookies refreshed successfully | Cookie fix worked | Success |
| 🔴 Cookie refresh FAILED | Both extraction methods failed | Error |
| 🟡 Queue running low (N songs) | Queue below threshold | Warning |
| 🔵 Playlist queued (auto-discovery) | New playlist set as Auto-DJ source | Info |
| 🔴 YouTube Live stream is DOWN | Stream died, attempting restart | Error |
| 🟢 Stream restarted | Recovery succeeded | Success |
| 🔴 OBS disconnected | OBS went offline | Error |
| 🔵 Fan request queued | Fan link added to queue | Info |
| 🎵 Suno track submitted | Original track creating on Suno | Info |
| 🎶 Original Suno track queued | Finished Suno track added to DJ bot queue | Info |

Alerts go to **Discord webhook** (instant) + **local log file** (history).
Same alert type won't fire twice within 30 seconds (configurable cooldown).

---

## API Endpoints Used

All communication goes through the DJ bot's existing Mission Control API. No modifications to the DJ bot are needed.

| Endpoint | Used By | Purpose |
|----------|---------|---------|
| `GET /api/ytcookies/health` | Cookie Fixer | Cookie age + freshness |
| `GET /api/ytcookies/auth_status` | Cookie Fixer | Is YouTube blocking the bot? |
| `POST /api/ytcookies/inject` | Cookie Fixer | Inject fresh cookies |
| `GET /api/<guild>/youtube_stream/status` | Stream Monitor | Is the stream alive? |
| `GET /api/obs/status` | Stream Monitor | OBS connected + streaming? |
| `POST /api/obs/streaming/start` | Stream Monitor | Start OBS streaming |
| `POST /api/obs/streaming/configure_and_start` | Stream Monitor | Full restart |
| `POST /api/obs/reconnect` | Stream Monitor | Reconnect to OBS |
| `POST /api/<guild>/play` | Queue Watchdog, Discord Watcher | Queue a song/playlist |
| `POST /api/<guild>/autodj_toggle` | Queue Watchdog | Enable Auto-DJ |
| `POST /api/<guild>/autodj_source` | Queue Watchdog, Playlist Finder | Set Auto-DJ playlist |
| `GET /api/<guild>/history` | Queue Watchdog | Recently played tracks |
| `POST /api/<guild>/presets/load` | Queue Watchdog | Load a saved preset |
| `GET /api/presets` | Queue Watchdog | List saved presets |

---

## File Structure

```
shadow_controller/
├── __init__.py               # 📦 Package — all modules exported
├── __main__.py               # 🚀 Entry point (python -m shadow_controller)
├── main.py                   # 🧠 Orchestrator — starts all 6 loops
├── api_client.py              # 📡 Mission Control API client
├── browser_manager.py         # 🦊 Firefox + Playwright + cookie.txt plugin
├── alerts.py                  # 🔔 Discord webhook + logging
├── cookie_fixer.py            # 🍪 Loop 1: cookie health + refresh
├── queue_watchdog.py          # 🎵 Loop 2: keep queue full
├── stream_monitor.py          # 📺 Loop 3: YouTube Live + OBS health
├── playlist_finder.py         # 🔍 Loop 4: Hermes + YouTube discovery
├── discord_watcher.py         # 💬 Loop 5: fan requests (optional)
├── suno_creator.py            # 🎶 Loop 6: Hermes makes original music on Suno
├── config.example.yaml         # ⚙️ Settings template
├── .env.example               # 🔑 Secret overrides template
├── requirements.txt           # 📦 Python dependencies
├── setup.sh                   # 🛠️ One-shot setup wizard
├── run.sh                     # ▶️ Quick start script
└── systemd/
    └── shadow-controller.service  # 🔧 Auto-start on boot
```

---

## Related

- **DJ Bot repo:** [the-Dj-music-bot-sidestepping-ollama](https://github.com/jayis1/the-Dj-music-bot-sidestepping-ollama) — the radio station itself
- **Hermes Agent:** [docs.ollama.com/integrations/hermes](https://docs.ollama.com/integrations/hermes) — the AI agent framework
- **Ollama:** [ollama.com](https://ollama.com) — local LLM runtime

## License

MIT — see [LICENSE](LICENSE)