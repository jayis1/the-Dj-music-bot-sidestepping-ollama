# 🎵 MBot 6.2.0 - The DJ Music Bot

MBot is a self-contained Discord music bot built with Python and `discord.py`. It plays audio from YouTube (URLs, searches, playlists) and Suno (direct song URLs) directly into Discord voice channels. 

## ✨ Features
- Play music directly from YouTube and Suno.com
- DJ Mode with TTS voice commentary between tracks (Detailed below)
- Support for playback of entire YouTube playlists and radio continuous play
- Built-in YouTube search
- Interactive Now Playing UI with button controls (Play/Pause/Skip/Stop/Queue)
- Fully configurable via interactive launcher scripts

---

## 🎙️ DJ Mode

MBot includes a unique **Radio DJ Mode**. When activated, the bot utilizes a Text-to-Speech (TTS) engine to speak between songs, just like a real radio DJ!

**What the DJ does:**
- Introduces the very first song in your session.
- Seamlessly transitions between songs by back-announcing what just played and introducing what's up next.
- Drops station IDs ("You're tuned to MBot Radio") randomly.
- Adapts personality based on the time of day (morning, afternoon, late-night crew).
- Plays a smooth outro when the queue runs out.

**Using DJ Mode:**
- Use `?dj` to toggle DJ Mode on or off for your server.
- The default voice is a female American voice (`en-US-AriaNeural`). 
- Change the DJ's voice anytime using `?djvoice <voice_name>`.
---

## 🌐 Web Dashboard & Custom DJ Lines
*(Available at `http://your-server:8080/`)*

MBot features a built-in web dashboard (powered by Flask) that starts automatically in the background alongside the Discord bot.

**Dashboard Features:**
- **Live Status:** View the now playing song, queue size, volume, playback speed, and DJ mode status for every server the bot is in.
- **Mission Control Theme:** A sleek, dark-themed UI that auto-refreshes every 30 seconds to keep you updated.

**Custom DJ Lines (`/dj-lines`):**
- **10 Categories:** Customize Intros, Song Intros, Hype Intros (Loud), Outros, Transitions, Hype Transitions, Mellow Transitions, Final Outros, Station IDs, and Listener Callouts.
- **Built-in vs Custom:** Mixes your custom lines (purple tags) with the built-in lines (gray tags) randomly so the DJ stays fresh.
- **Dynamic Variables:** Add placeholders like `{title}`, `{prev_title}`, `{next_title}`, and `{greeting}` right into your custom lines!
- Custom lines are saved instantly to `dj_custom_lines.json` and persist across reboots.

**Configuration:**
In your `.env` file, you can customize the host and port:
```env
WEB_HOST=0.0.0.0
WEB_PORT=8080
```
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
| `?playlist` | `?playlist <URL>` | Queues up to 25 songs from a YouTube playlist |
| `?radio` | `?radio <URL>` | Queues up to 100 songs for long sessions |
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
