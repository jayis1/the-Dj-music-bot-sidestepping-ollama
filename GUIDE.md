# MBot 6.2.0 — Comprehensive Technical Guide

> **Last Updated:** 2026-04-14
> **Version:** 6.2.0
> **License:** MIT

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
13. [Test Suite](#13-test-suite)
14. [Launcher Scripts](#14-launcher-scripts)
15. [Complete Command Reference](#15-complete-command-reference)
16. [Troubleshooting & Known Issues](#16-troubleshooting--known-issues)
17. [Development Guide](#17-development-guide)

---

## 1. Overview

**MBot 6.2.0** is a self-contained Discord music bot built with Python and `discord.py`. It plays audio from YouTube (URLs, searches, playlists) and Suno (direct song URLs) directly into Discord voice channels. The bot is designed to run as a persistent background service on Debian-based Linux servers, managed through `screen` sessions.

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
├── bot.py                  # Entry point — bot lifecycle, cog loading, logging
├── config.py               # Configuration loader (env vars + constants)
├── .env.example            # Template for environment variables
├── requirements.txt        # Python dependencies
├── launch.sh               # Minimal launcher (setup/start/stop/restart/attach/doctor)
├── start.sh                # Interactive launcher with colored output & setup wizard
├── LICENSE                 # MIT License
│
├── cogs/                   # Discord.py extension modules (loaded at runtime)
│   ├── __init__.py         # Auto-generated; makes cogs a Python package
│   ├── music.py            # Music commands & playback engine (785 lines)
│   ├── admin.py            # Admin/owner-only commands (shutdown, restart, cookies)
│   ├── youtube.py          # YTDLSource — yt-dlp extraction wrapper
│   └── logging.py          # Message/command/error logging to file
│
├── utils/                  # Helper modules
│   ├── __init__.py         # Auto-generated; makes utils a Python package
│   ├── discord_log_handler.py  # Ships log lines to a Discord channel
│   ├── dj.py               # Radio DJ mode — TTS message generation & voice synthesis
│   ├── suno.py             # Suno.com URL detection & audio resolution
│   ├── cookie_parser.py    # Parses Set-Cookie headers → Netscape cookie file
│   └── import_parser.py    # Parses bot log files (timestamped entries)
│
├── tests/                  # pytest test suite
│   ├── test_playlist.py    # Playlist & radio extraction tests
│   ├── test_suno.py        # Suno URL detection & track resolution tests
│   └── test_youtube.py     # YouTube single-video extraction test
│
├── yt_dlp_cache/           # yt-dlp metadata cache directory (auto-created)
├── bot_activity.log        # Runtime log file (auto-created)
├── bot.log                 # Screen session log (auto-created by launchers)
└── youtube_cookie.txt      # yt-dlp cookie file (created by ?fetch_and_set_cookies)
```

### How Modules Relate

```
bot.py
  ├── loads → cogs/music.py
  ├── loads → cogs/admin.py
  ├── skips → cogs/youtube.py (imported directly by music.py, NOT auto-loaded)
  ├── skips → cogs/logging.py (loaded manually, NOT auto-loaded)
  └── initializes → utils/discord_log_handler.py

cogs/music.py
  ├── imports → cogs/youtube.py (YTDLSource, FFMPEG_OPTIONS, YTDL_FORMAT_OPTIONS)
  ├── imports → utils/suno.py (is_suno_url, get_suno_track)
  ├── imports → utils/dj.py (EDGE_TTS_AVAILABLE, generate_intro, generate_outro, generate_tts, cleanup_tts_file, list_voices, DEFAULT_VOICE)
  └── imports → config.py (emojis, prefix)

cogs/admin.py
  ├── imports → utils/cookie_parser.py (parse_all_cookies)
  └── modifies → cogs/youtube.py (YTDL_FORMAT_OPTIONS["cookiefile"])
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
| `aiohttp` | latest | Async HTTP client (Suno, admin cookie fetch) |
| `edge-tts` | latest | Microsoft Edge TTS — generates DJ voice audio (optional but needed for DJ mode) |
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
```

| Variable | Required? | Source | Used By |
|---|---|---|---|
| `DISCORD_TOKEN` | **Yes** | [Discord Developer Portal](https://discord.com/developers/applications) → Your App → Bot → Token | `bot.py` → `bot.start()` |
| `YOUTUBE_API_KEY` | No (needed for `?search`) | [Google Cloud Console](https://console.cloud.google.com/apis/library/youtube.googleapis.com) | `cogs/music.py` → `search` command |
| `LOG_CHANNEL_ID` | No | Discord: right-click channel → Copy Channel ID (Developer Mode required) | `bot.py` → `DiscordLogHandler` init |
| `BOT_OWNER_ID` | No | Discord: right-click your username → Copy User ID | `cogs/admin.py` → `@commands.is_owner()` |

### `config.py` Constants

```python
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
COMMAND_PREFIX = "?"
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", 0) or 0) or None
```

| Constant | Default | Purpose |
|---|---|---|
| `DISCORD_TOKEN` | None | Bot authentication token |
| `YOUTUBE_API_KEY` | None | YouTube Data API v3 key |
| `COMMAND_PREFIX` | `?` | All commands are prefixed with this character |
| `LOG_CHANNEL_ID` | None | Channel ID for Discord log shipping |

### DJ Mode Configuration (`config.py`)

| Constant | Default | Purpose |
|---|---|---|
| `DJ_VOICE` | `en-US-AriaNeural` | Default TTS voice for DJ commentary |
| `DJ_EMOJI` | 🎙️ | Emoji used in DJ command embeds |

> **Note:** DJ mode is off by default and must be enabled per-guild with `?dj`. The `DJ_VOICE` setting is just the default — users can override it per-guild with `?djvoice`.

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

| Category | Count | Example |
|---|---|---|
| **Session Intros** | 12 | "Good evening, everyone! Let's kick things off with {title}.", "Rise and shine, everyone! Our opening track is {title}." |
| **Hype Intros** | 14 | "Up next, {title}!", "Oh, this is a good one. Here's {title}.", "Turn it up for {title}!" |
| **Hype Intros (loud)** | 9 | "Oh yeah! It's time for {title}!", "This next one goes hard. {title}!", "Here. We. Go. {title}!" |
| **Outros** | 8 | "That was {title}.", "Love that one. {title}.", "Mm, {title}. That hit the spot." |
| **Transitions** | 15 | "That was {prev_title}. And up next, {next_title}.", "{prev_title} in the books. Up next, {next_title}!" |
| **Hype Transitions** | 5 | "That was {prev_title} — and the next one is even better. {next_title}!" |
| **Mellow Transitions** | 5 | "That was {prev_title}. Taking it easy with {next_title}.", "Lovely track. Here's {next_title} to keep the vibe going." |
| **Final Outros** | 10 | "The queue's empty, but the radio stays on. I'll be right here.", "{title} — and that's a wrap on tonight's set." |
| **Station IDs** | 10 | "You're tuned in to MBot Radio.", "This is your DJ on MBot Radio." |
| **Listener Callouts** | 10 | "Shoutout to everyone listening right now.", "The vibes are immaculate right now." |
| **Queue Banter** | 10+ | "One more left in the queue.", "We are in it for the long haul tonight, folks. 20 more tracks!" |

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

The default voice is `en-US-AriaNeural` (female, American English). Use `?djvoices` to see all English voices, or `?djvoices <prefix>` for other languages (e.g., `?djvoices ja` for Japanese).

Popular voices include:
- `en-US-AriaNeural` — Female, American (default)
- `en-US-GuyNeural` — Male, American
- `en-GB-SoniaNeural` — Female, British
- `en-AU-NatashaNeural` — Female, Australian
- `ja-JP-NanamiNeural` — Female, Japanese

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

## 10. Admin Cog: `cogs/admin.py`

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

## 11. Logging Cog: `cogs/logging.py`

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

## 12. Utility Modules

### `utils/discord_log_handler.py` — DiscordLogHandler

A custom Python `logging.Handler` that ships log messages to a Discord text channel.

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

## 13. Test Suite

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

## 14. Launcher Scripts

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

## 15. Complete Command Reference

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

## 16. Troubleshooting & Known Issues

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

### Known Issues

1. **`launch.sh attach` shows blank screen** — The `screen -r` command may display a blank window in some SSH clients. Detach with Ctrl+A D and use `tail -f bot.log` instead.

2. **Progress bar resets on speed change** — When speed is changed, FFmpeg is restarted from position 0:00. The `song_start_time` is reset, so the progress bar restarts from the beginning.

3. **Suno tracks show 0:00 / 0:00** — Suno doesn't provide duration metadata via HTML scraping. The progress bar uses duration=0, which renders a static empty bar.

4. **`?restart` doesn't actually restart** — It only closes the bot. A process supervisor (like `screen` + launcher script, systemd, or pm2) is needed to detect the exit and start a new process.

5. **Log spam after `?leave`** (Fixed in 6.2.0) — Previously, the Now Playing update task continued running after leaving a voice channel. Now properly cancelled.

6. **Interaction already responded error** (Fixed in 6.2.0) — Previously, clicking the "queue" button multiple times would crash the bot. Now uses `ephemeral` responses.

7. **`?fetch_and_set_cookies` crashes with `AttributeError`** — The `admin.py` cog calls `cookie_parser.parse_all_cookies(header)` but this function does not exist in `utils/cookie_parser.py` (that file only contains log-parsing functions). The cookie-fetching admin command will fail at runtime.

8. **Speed values below 0.5 may cause FFmpeg errors** — The `atempo` FFmpeg filter only supports 0.5–2.0 per instance. While the bot's speed ladder starts at 0.25x, attempting to play at that speed may cause FFmpeg to fail. Values below 0.5 require chaining multiple `atempo` filters (e.g., `atempo=0.5,atempo=0.5` for 0.25x).

---

## 17. Development Guide

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