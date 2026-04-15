# 🎵 The radio dj music bot

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
- Play music directly from YouTube and Suno.com
- DJ Mode with TTS voice commentary between tracks (Detailed below)
- Support for playback of entire YouTube playlists and radio continuous play
- Built-in YouTube search
- Interactive Now Playing UI with button controls (Play/Pause/Skip/Stop/Queue)
- **🕰️ Recently Played history** — replay any of the last 30 tracks with one click
- **🔁 Auto-DJ / Radio Autoplay** — keep the music going forever with a YouTube playlist, a saved preset, or your own listening history
- **🎧 Listener List** — see who's in the voice channel live on the dashboard
- **📢 `?shoutout @user`** — give a live on-air shoutout with TTS and sound effects
- **🎶 DJ Bed Music** — ambient looping pad plays softly under DJ commentary for a real radio feel
- **🎛️ Soundboard Keyboard Shortcuts** — press 1–9 to instantly fire sound effects
- **📊 Live Audio Visualizer** — animated frequency canvas on the dashboard while music plays
- Fully configurable via interactive launcher scripts

---

## 🎙️ DJ Mode

The radio dj music bot includes a unique **Radio DJ Mode**. When activated, the bot utilizes a Text-to-Speech (TTS) engine to speak between songs, just like a real radio DJ!

**What the DJ does:**
- Introduces the very first song in your session.
- Seamlessly transitions between songs by back-announcing what just played and introducing what's up next.
- Drops station IDs ("You're tuned to The radio dj music bot") randomly.
- Adapts personality based on the time of day (morning, afternoon, late-night crew).
- Plays a smooth outro when the queue runs out.

**Using DJ Mode:**
- Use `?dj` to toggle DJ Mode on or off for your server.
- The default voice is a female American voice (`en-US-AriaNeural`). 
- Change the DJ's voice anytime using `?djvoice <voice_name>`.
---

## 🌐 Web Dashboard & Custom DJ Lines
*(Available at `http://your-server:8080/`)*

The radio dj music bot features a built-in web dashboard (powered by Flask) that starts automatically in the background alongside the Discord bot.

**Dashboard Features:**
- **Live Status & Remote Control:** The dashboard isn't just for viewing! You can skip, pause, play, or stop the currently playing song directly from your browser.
- **Interactive UI & Toast Notifications:** Enjoy smooth real-time feedback with loading spinners and stylish pop-up notifications whenever you trigger actions!
- **Audio Sliders:** Adjust the bot's volume and playback speed on the fly. Speed changes **immediately** restart the currently playing song at the new rate — no waiting for the next track. Supports 0.25×–4.0× via a proper `atempo` FFmpeg filter chain.
- **Drag-and-Drop Queue Reordering:** Rearrange the upcoming songs effortlessly by dragging and dropping them, or use the "Play Next" shortcut to move them straight to the top of the queue.
- **🕰️ Recently Played:** See the last 30 tracks with album art and timestamps. Hit 🔁 Replay to instantly re-queue any of them.
- **🔁 Auto-DJ / Radio Autoplay:** When the queue empties, the bot automatically refills it from a YouTube playlist URL, a saved preset (`preset:Name`), or randomly from your recently played history.
- **🎧 Listener List:** Avatar pills showing exactly who is in the voice channel — live.
- **📻 Dedicated Radio Page (`/radio`):** A focused side panel for all radio and DJ configuration — DJ voice selector, Auto-DJ source config, who's listening, the compact Now Playing display, and the full Recently Played history. Keeps the main dashboard clean.
- **📊 Live Audio Visualizer:** A 48-bar animated frequency canvas that pulses while music plays. Toggle it on/off anytime.
- **⏱️ Live Song Progress Bar:** A gradient-filled progress bar with a real-time JavaScript ticker that updates every second (respecting playback speed). Shows elapsed/total time (e.g. `1:23 / 3:45`). Unknown-duration songs get a smooth pulsing animation instead.
- **📋 Queue & Session Duration:** The queue header shows total remaining playtime. Below the progress bar, a summary displays queue total and session total (current song + queue) so you always know how long the party lasts.
- **📋 Queue Manager Page (`/queue`):** A dedicated sidebar page for adding songs and managing the queue. Supports YouTube videos, full playlists (no track limit), and direct URLs. Features drag-and-drop reorder (SortableJS), Play Next shortcuts, per-track remove, one-click Clear Queue, and the full Presets panel.
- **Web-based "Add to Queue":** A built-in search bar allows you to paste YouTube links or search queries directly from the dashboard to instantly queue songs.
- **Live Lyrics Panel:** Automatically fetches and displays lyrics for the currently playing track via `syncedlyrics`.
- **Save / Load Custom Playlists (Presets):** Save your perfectly crafted queues directly into a preset JSON, and reload them with one click later.
- **Instant DJ Soundboard & Custom Uploads:** A 17-sound library of DJ drops from MyInstants, plus you can upload your own `.mp3` files and fire them via the dashboard or keyboard shortcuts (keys 1–9).
- **Smart DJ Audio Tags:** Type `{sound:filename}` anywhere inside your custom DJ lines (e.g. `In the mix! {sound:airhorn}`). The DJ will speak the intro and perfectly sequence your chosen sound effect right before the song drops!
- **🎶 DJ Bed Music:** A subtle ambient pad plays softly under DJ commentary for a polished radio-station feel. Configurable via `DJ_BED_MUSIC_ENABLED`.
- **Gapless Crossfade Playback:** Configure a dynamic crossfade overlap (e.g. 3 seconds) using `CROSSFADE_DURATION` in `config.py` for a flawless, club-style transition between songs!
- **DJ Voice Selector:** Easily switch the Edge-TTS radio host's voice dynamically via a drop-down menu.
- **Mission Control Theme:** A sleek, dark-themed UI that auto-refreshes to keep you seamlessly synced with the Discord session.

**Custom DJ Lines (`/dj-lines`):**
- **10 Categories:** Customize Intros, Song Intros, Hype Intros (Loud), Outros, Transitions, Hype Transitions, Mellow Transitions, Final Outros, Station IDs, and Listener Callouts.
- **Built-in vs Custom:** Mixes your custom lines (purple tags) with the built-in lines (gray tags) randomly so the DJ stays fresh.
- **Dynamic Variables:** Add placeholders like `{title}`, `{prev_title}`, `{next_title}`, and `{greeting}` right into your custom lines!
- Custom lines are saved instantly to `dj_custom_lines.json` and persist across reboots.

**Configuration:**
In your `.env` file, you can customize the host, port, and now-playing channel:
```env
WEB_HOST=0.0.0.0
WEB_PORT=8080

# Optional: bind all now-playing embeds to a specific Discord channel
# Leave as 0 (or omit) to send embeds wherever the command was typed
NOWPLAYING_CHANNEL_ID=0
```
> **Tip:** To get a channel ID, enable **Developer Mode** in Discord settings, then right-click the channel → **Copy Channel ID**.

*(If Flask isn't installed, the bot will log a warning and continue running normally without the dashboard).*

---

## 📜 Full Command Reference
*(The default prefix is `?`)*

### 🎧 Music Commands
| Command | Usage | Description |
|---|---|---|
| `?join` | `?join` | Bot joins or moves to your voice channel |
| `?leave` | `?leave` | Disconnects the bot from voice and cleans up |
| `?search` | `?search <query>` | Searches YouTube and shows the top 10 results |
| `?play` | `?play <URL/query>` | Plays audio from YouTube (link or query), Search result number, or Suno URL |
| `?playlist` | `?playlist <URL>` | Queues an entire YouTube playlist |
| `?radio` | `?radio <URL>` | Queues an entire YouTube playlist for long sessions |
| `?queue` | `?queue` | Displays all songs currently in the queue |
| `?skip` | `?skip` | Skips to the next track in the queue |
| `?stop` | `?stop` | Stops playback immediately and clears the entire queue |
| `?pause` | `?pause` | Pauses the current track |
| `?resume` | `?resume` | Resumes paused playback |
| `?clear` | `?clear` | Clears all queued songs (but doesn't stop the current song) |
| `?remove` | `?remove <number>` | Removes a specific song number from your queue |
| `?nowplaying` | `?nowplaying` | Shows the currently playing song with interactive controls |
| `?volume` | `?volume <0-200>` | Adjusts playback volume (100 = normal volume) |
| `?loop` | `?loop` | Toggles looping for the current song |
| `?speedhigher` | `?speedhigher` | Increases the playback speed by one step |
| `?speedlower` | `?speedlower` | Decreases the playback speed by one step |
| `?shuffle` | `?shuffle` | Randomizes the queue order |

### 🎙️ DJ Commands
| Command | Usage | Description |
|---|---|---|
| `?dj` | `?dj` | Toggles the DJ text-to-speech commentary on/off |
| `?djvoice` | `?djvoice [name]` | Shows the current DJ voice, or sets it to `<name>` |
| `?djvoices` | `?djvoices [prefix]`| Lists available voices (e.g. `ja` for Japanese, or no prefix for English) |

### ⚙️ Admin Commands (Bot Owner Only)
| Command | Usage | Description |
|---|---|---|
| `?fetch_and_set_cookies` | `?fetch_and_set_cookies <https URL>` | Fetches cookies from a URL. Great for age-restricted/member YouTube videos. |
| `?shutdown` | `?shutdown` | Safely shuts down the bot |
| `?restart` | `?restart` | Closes the connection (Will automatically reboot if run with launcher scripts) |

---

## 🛠️ Prerequisites

Before you start, you'll need a computer or server (like a Raspberry Pi or a cloud server) running a Linux operating system (like Ubuntu or Debian). Our setup script will try to install everything else you need automatically!

## 🚀 Step-by-Step Installation & Setup

Don't worry if you aren't super technical! We have created an interactive setup wizard that does all the heavy lifting for you. It automatically downloads the required software (like Python and audio tools) and prepares your bot.

**Step 1: Download the Bot's Code**
Open your terminal (command line) and type the following commands. Press `Enter` after each line:
```bash
git clone https://github.com/jayis1/the-Dj-music-bot.git
cd the-Dj-music-bot
```
*(This tells your computer to copy the bot's code onto your machine and open the bot's folder.)*

**Step 2: Get Your Discord Bot Token**
Before we can run the setup, you need to create a bot account on Discord.
1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and sign in.
2. Click **"New Application"** in the top right, give it a name, and hit create.
3. On the left sidebar, click **"Bot"**.
4. Scroll down to the **"Privileged Gateway Intents"** section and ensure **Message Content Intent** and **Voice State Intent** (if available) are turned on.
5. Look for the **"Token"** section. Click **"Reset Token"** and copy the long string of letters and numbers it gives you. Keep this secret! You'll need it in a moment.
6. Still in the Developer portal, go to **OAuth2 -> URL Generator**. Check `bot` and `applications.commands`. Then check giving it the necessary permissions (Administrator is easiest). Copy the generated URL and paste it in your browser to invite the bot to your server!

**Step 3: Run the Setup Wizard**
Now, go back to your terminal where you typed the commands in Step 1, and run our magic script:
```bash
bash start.sh
```
This script will start installing things. It might take a minute! Eventually, it will pause and ask you some questions:

1. **DISCORD_TOKEN**: Paste the long string of letters and numbers you copied in Step 2. This is the only required piece of information!
2. **YOUTUBE_API_KEY**: You can just press `Enter` to skip this. (It's only needed if you want to use the `?search` command later).
3. **LOG_CHANNEL_ID**: You can just press `Enter` to skip this.
4. **BOT_OWNER_ID**: You can just press `Enter` to skip this.

That's it! Your bot should now connect to your Discord server and be ready to play music.

## 🚑 Alternative Setup & Troubleshooting

If you run into issues with the interactive wizard (`start.sh`), or prefer to do things manually, you can use the minimalistic launcher:

1. **Manual Setup**:
   Instead of `start.sh`, run this command to install the required system and Python dependencies:
   ```bash
   chmod +x launch.sh
   ./launch.sh setup
   ```
2. **Create Configuration Manually**:
   Since there's no wizard here, you will have to create your `.env` file yourself:
   ```bash
   cp .env.example .env
   ```
   *Now, open the `.env` file in your favorite text editor (e.g., `nano .env`) and paste your `DISCORD_TOKEN` there.*
3. **Start the Bot in the Background**:
   Once configured, start the bot like this:
   ```bash
   ./launch.sh start
   ```
   *(This launches the bot silently in the background inside a "screen" session named 'musicbot'.)*

**Troubleshooting Commands:**
- **Bot online but won't play audio?** You might be missing dependencies. Run `./launch.sh doctor` which runs self-diagnostic tests to uncover the problem.
- **Can't figure out why it crashed?** If you started it entirely in the background, you can run `./launch.sh attach` to peek at the live background process and grab error logs. Press `Ctrl+A` followed by `D` to safely detach when you're done looking!

## 📚 Further Documentation
For detailed insights regarding architecture, cog layout, creating your own modules, or managing yt-dlp cached metadata, please refer directly to the comprehensive [GUIDE.md](GUIDE.md).

---

## 🐛 Developer Notes & Recent Fixes

### FFmpeg Filter Configuration Fix
During the rollout of the crossfade feature, an issue occurred where music playback failed (though DJ intros played fine).

**Root Cause:**
The crossfade feature had a missing closing quote in the FFmpeg filter string:
```python
# Before (broken):
player_options["options"] += f' -filter:a "{"+".join(audio_filters)}'
```
This resulted in FFmpeg receiving `-filter:a "atempo=1.5+afade=t=in:st=0:d=3` without the closing quotation mark, throwing a "No closing quotation" error and immediately terminating playback. 

**The Fix:**
A closing quote was properly appended:
```python
# After (fixed):
player_options["options"] += f' -filter:a "{"+".join(audio_filters)}"'
```

All combining filter scenarios have been verified to produce the correct FFmpeg arguments:
| Scenario | FFmpeg options | Status |
|---|---|---|
| No speed change, no crossfade | `-vn` (no filter added) | ✅ |
| Speed 1.5x only | `-vn -filter:a "atempo=1.5"` | ✅ |
| Crossfade 3s only | `-vn -filter:a "afade=t=in:st=0:d=3"` | ✅ |
| Speed 1.5x + crossfade | `-vn -filter:a "atempo=1.5+afade=t=in:st=0:d=3"` | ✅ |
| Speed 0.75x + crossfade 5s | `-vn -filter:a "atempo=0.75+afade=t=in:st=0:d=5"` | ✅ |

### Web Dashboard & DJ Lines Refactor

**1. Fixed Soundboard Play Endpoint:**
The `/api/<guild_id>/soundboard` endpoint was previously using a broken nested-async pattern that caused it to fail silently and return HTML error pages, triggering JSON parse errors in the frontend. It has been replaced with a streamlined `async def _play_sound()` routine that directly invokes the voice client cleanly.

**2. Dedicated Soundboard Sidebar Page:**
The Soundboard logic has been decoupled from the primary dashboard into its own dedicated page (`/soundboard`). This cleans up the main view and provides a dedicated space for upload cards, file previews, and a play button grid.

**3. Expanded DJ Lines & Sound Tags:**
The DJ now has access to 172 completely unique built-in broadcast lines, up from 98. More importantly, 74 of these lines natively embed the `{sound:name}` tag architecture (leveraging all 9 built-in sounds: airhorn, air_raid, applause, button_press, club_hit, dj_drop, in_the_mix, record_scratch, dj_scratch). 

*(Note: `STATION_IDS` now require double-braces `{{sound:name}}` due to concurrent f-string interpolation).*

**4. Enhanced DJ Lines Dashboard:**
The `/dj-lines` page now includes a comprehensive Soundboard Tags reference card. Placeholders like `{title}` render as blue graphical badges, while `{sound:name}` tags render as purple interactive badges, giving users an immediate visual understanding of how the dynamic prompt generation works under the hood.

---

### `KeyError: 'sound'` on DJ Line Generation

**Root Cause:**
With the introduction of the `{sound:name}` dynamic tags to the built-in DJ lines (e.g. `{sound:airhorn}`), a new bug emerged. When `generate_intro()`, `generate_song_intro()`, or `generate_outro()` called Python's native `.format(title=..., greeting=...)` on a template containing a sound tag, the native `str.format()` engine interpreted `{sound:airhorn}` as a format field named "sound" with a format config of "airhorn". Since no keyword argument named "sound" was actually passed to `.format()`, the process threw a `KeyError: 'sound'`.

**The Fix:**
A new `_format_line(template, **kwargs)` wrapper function was implemented to safely isolate sound tags during formatting:
1. It extracts all `{sound:...}` tags using a Regex findall (`re.findall`).
2. It completely strips those tags from the template string so `.format()` never processes them.
3. It performs the standard `.format(**kwargs)` variable replacement (e.g., substituting `{title}`).
4. Finally, it re-appends the preserved `{sound:...}` tags onto the tail end of the newly formatted broadcast line.

This ensures that format collisions no longer occur and the DJ correctly triggers the sound effects at the end of their introductory broadcast!

---

### Dashboard UX & Upload Bug Fixes

**1. Upload Button Reliability (Firefox/WebKit):**
The HTML file upload button originally utilized a `display: none` style to hide the `<input type="file">` tag. However, modern browsers (especially Firefox) will silently block programmatic `.click()` events on `display: none` elements for security reasons. This was mitigated by applying a modern CSS workaround (`position: absolute; opacity: 0; pointer-events: none;`), restoring file upload selection functionality across all browsers. 

**2. Conditional Auto-Refreshes:**
The `<meta http-equiv="refresh" content="30">` tag was indiscriminately reloading all pages every 30 seconds, ruthlessly interrupting ongoing audio file uploads and form submissions. The template architecture was upgraded to parse a Jinja conditional (`{% if auto_refresh %}`), ensuring only the live status dashboard (`/`) receives the periodic reload command, while interactive pages (`/soundboard` and `/dj-lines`) remain stable for user inputs.


---

### Dedicated Radio Page (`/radio`)

All radio/DJ-related configuration panels have been consolidated into a dedicated `/radio` sidebar page to keep the main dashboard focused on playback control. This page now hosts:
- 🗣️ **DJ Voice selector** — full dropdown with "DJ Active" status badge
- 🔁 **Auto-DJ config** — large toggle, source input with help text (playlist URL / `preset:Name` / blank for history replay)
- 🎧 **Listening Now** — live avatar chips for voice channel members
- ▶ **Now Playing** — compact thumbnail + title + duration display
- 🕰️ **Recently Played** — full 30-entry history with album art, timestamps, and 🔁 Replay buttons

The main dashboard retains the Auto-DJ toggle button for quick access; the Quick Links bar was updated to point to Radio, DJ Lines, and Soundboard.

---

### Long Sound Effects Blocking Next Song Playback

**Root Cause:**
Several MyInstants sounds are long (e.g. `airhorn.mp3` ~20s, `dj_turn_it_up.mp3` ~13s). After TTS completed, the bot played the full sound effect. While it was still running, `_play_dj_sounds_then_song` timed out and tried to start the next song — causing `discord.py` to raise `"already playing audio"`.

**Fixes Applied:**
1. **3-second FFmpeg cap** — `MAX_SOUND_SECONDS = 3` in `config.py`. All DJ sounds and soundboard effects pass `-vn -t 3` to FFmpeg, trimming any clip to a crisp stinger.
2. **Stop before play** — `voice_client.stop()` + `asyncio.sleep(0.1)` before each sound ensures clean transitions.
3. **Shorter timeout** — Per-sound wait reduced from 10 → 5 seconds (safe since sounds cap at 3s).
4. **Stale tag cleanup** — All `{sound:applause}` → `{sound:rave_cheer}` and `{sound:button_press}` → `{sound:uyuuui}` corrected after the `.wav` → `.mp3` library swap.

---

### Dashboard 500 Internal Server Error (Jinja `{% endif %}` Mismatch)

**Root Cause:**
When the DJ voice dropdown was migrated from `dashboard.html` to the new `/radio` page, an orphaned `{% endif %}` was left behind (line ~224). This tag had no matching `{% if %}`, causing Jinja to throw `Encountered unknown tag 'endif'` — it was still inside the `{% for g in guilds %}` loop and expected `{% endfor %}` or `{% else %}`, not `{% endif %}`.

**The Fix:**
Removed the orphaned `{% endif %}`. The corrected block structure in `dashboard.html`:
| Block | Closed At |
|---|---|
| `{% if g.in_voice %}` | line ~97 |
| `{% if g.current_song %}` | line ~165 |
| `{% if g.queue_size > 0 %}` | line ~203 |
| `{% if g.in_voice and g.listeners %}` | line ~222 |
| `{% for g in guilds %}` | end of template |

All 5 templates now parse cleanly: `dashboard.html` ✅ `radio.html` ✅ `base.html` ✅ `soundboard.html` ✅ `dj_lines.html` ✅

---

### Speed Slider Live-Apply Fix

**Root Cause:**
The `/api/speed` endpoint saved `playback_speed` to a dict but never restarted the FFmpeg audio source. The new speed only took effect on the next song.

**Fix:**
The endpoint now calls `_apply_speed()` if audio is playing:
1. Stops the current voice client playback
2. Rebuilds FFmpeg options with the new `atempo` filter
3. Starts a fresh `PCMVolumeTransformer` source
4. Resets `song_start_time` for accurate progress bar tracking

If nothing is playing it returns `{"note": "saved for next song"}`.

> **⚠️ Known Bug:** If a non-1.0× speed is set and you start playing a new song, the bot may throw `"already playing audio"` internally and **skip the song**. This happens because the speed-change restart (`stop()` → new FFmpeg source) races with the initial playback start. **Workaround:** Set speed back to `1.0×` before queuing a new song, or change speed only after the song has already started playing.

---

### FFmpeg `atempo` Full-Range Filter Chain (0.25×–4.0×)

**Root Cause:**
FFmpeg's `atempo` filter only accepts values between `0.5` and `2.0` per instance. Using `atempo=0.25` directly caused FFmpeg to error out.

**Fix — `_build_atempo_chain(speed)`:**
A static helper chains multiple `atempo` filters to cover any speed:
| Speed | Filter chain |
|---|---|
| 0.25× | `atempo=0.5,atempo=0.5` |
| 0.5× | `atempo=0.5` |
| 1.0× | `atempo=1.0` |
| 2.0× | `atempo=2.0` |
| 4.0× | `atempo=2.0,atempo=2.0` |

Applied to `_set_speed`, `_start_song_playback`, and the web API endpoint.

---

### Sound Duration Cap Updated (3s → 8s / 10s)

The 3-second cap was too aggressive for longer DJ effects. Updated in `config.py`:
- `MAX_SOUND_SECONDS = 8` (was 3)
- **Soundboard endpoint** (`app.py`): FFmpeg `-t 8` cap
- **DJ sound playback** (`music.py`): FFmpeg `-t 10` cap (`MAX_SOUND_SECONDS + 2`)
- **Sound wait timeout**: 12s (was 5s) to match the 10s maximum
