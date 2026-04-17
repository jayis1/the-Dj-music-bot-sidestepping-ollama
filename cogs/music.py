import asyncio
import discord
from discord.ext import commands
from googleapiclient.discovery import build
import json
import os
import random
import re
import logging
import time
import config

from cogs.youtube import (
    YTDLSource,
    PlaceholderTrack,
    FFMPEG_OPTIONS,
    YTDL_FORMAT_OPTIONS,
)

try:
    from cogs.youtube import get_ytdl_format_options
except ImportError:
    # Fallback for older youtube.py that doesn't have this function yet
    get_ytdl_format_options = YTDL_FORMAT_OPTIONS.copy
from utils.suno import is_suno_url, get_suno_track
from utils.dj import (
    EDGE_TTS_AVAILABLE,
    TTS_MODE,
    TTS_AVAILABLE,
    generate_intro,
    generate_song_intro,
    generate_outro,
    generate_tts,
    cleanup_tts_file,
    list_voices,
)
from utils.llm_dj import (
    OLLAMA_DJ_AVAILABLE,
    generate_side_host_line,
    should_side_host_speak,
    check_ollama_available,
)

# ── YouTube Live streaming ──
YOUTUBE_STREAM_AVAILABLE = False
try:
    from utils.youtube_stream import YouTubeLiveStreamer

    if getattr(config, "YOUTUBE_STREAM_ENABLED", False) and getattr(
        config, "YOUTUBE_STREAM_KEY", ""
    ):
        YOUTUBE_STREAM_AVAILABLE = True
except ImportError:
    pass

# Valid even if not auto-started — allows manual ?golive
try:
    from utils.youtube_stream import YouTubeLiveStreamer as _YTStreamerClass

    YOUTUBE_STREAMER_CLASS = _YTStreamerClass
except ImportError:
    YOUTUBE_STREAMER_CLASS = None


class PCMBroadcasterWrapper:
    """Mock VoiceClient to transparently handle autonomous radio broadcasting without Discord VC."""
    def __init__(self, bot, guild_id, broadcaster):
        self.bot = bot
        self.guild_id = guild_id
        self.broadcaster = broadcaster
        self.source = None

    def is_playing(self):
        return self.source is not None

    def is_connected(self):
        return True
    
    def stop(self):
        if self.source:
            self.broadcaster.stop_source()
            self.source = None
            
    def pause(self):
        pass
        
    def resume(self):
        pass

    def play(self, source, after=None):
        self.source = source
        self.broadcaster.set_source(source, guild_id=self.guild_id, bot=self.bot, after=after)

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.song_queues = {}
        self.search_results = {}
        self.current_song = {}
        self.nowplaying_message = {}
        self.queue_message = {}
        self.playback_speed = {}
        self.youtube_speeds = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
        self.looping = {}
        self.song_start_time = {}
        self.nowplaying_tasks = {}
        self.current_volume = {}
        self.inactivity_timers = {}

        # Overlay States
        self._sfx_active = {}  # guild_id -> bool

        # DJ Mode state (per-guild)
        self.dj_enabled = {}  # guild_id -> bool
        self.dj_voice = {}  # guild_id -> str (TTS voice name)
        self.dj_playing_tts = {}  # guild_id -> bool (prevents re-entrant TTS during skip/stop)
        self._current_tts_path = {}  # guild_id -> str|None (path to clean up after TTS playback)
        self._dj_pending_sounds = {}  # guild_id -> list[str] (sound IDs to play after TTS, from {sound:name} tags)

        # Recently played history (per-guild, max 30 entries)
        self.recently_played = {}  # guild_id -> [{title, url, thumbnail, played_at}, ...]
        self._max_history = 30

        # Auto-DJ / Radio Autoplay (per-guild)
        self.autodj_enabled = {}  # guild_id -> bool
        self.autodj_source = {}  # guild_id -> str (YouTube playlist URL or preset name)

        # DJ Bed Music (ambient loop played under DJ voice)
        self._bed_playing = {}  # guild_id -> bool

        # AI Side Host (Ollama) — a second radio personality with its own voice
        self.ai_dj_enabled = {}  # guild_id -> bool (side host on/off)
        self.ai_dj_voice = {}  # guild_id -> str (TTS voice name for side host)
        self._ai_dj_pending_line = {}  # guild_id -> str|None (AI line waiting to be spoken)
        self._last_dj_line = {}  # guild_id -> str (what the main DJ just said, for AI context)

        # Battle of the Beats — live voting showdowns (per-guild)
        self._battles = {}  # guild_id -> dict {song_a, song_b, votes_a, votes_b, message_id, channel_id, timer_task, created_at}

        # YouTube auth state — tracks whether cookies are needed
        self._yt_auth_blocked = False  # True when YouTube requires auth (cookies)
        self._yt_auth_blocked_at = None  # Timestamp when auth was blocked (epoch)
        self._yt_auth_retries = 0  # How many times we've retried after cookie update
        self._pending_cookie_retry = (
            False  # True when cookies were just updated and queue should retry
        )

        # YouTube Live streaming — RTMP output to YouTube Live events
        self._yt_streamer = None  # YouTubeLiveStreamer instance (created on ?golive)
        self._yt_stream_active = False  # True while stream is running
        self._yt_stream_guild = None  # Guild ID of the streaming guild
        self._broadcasters = {}  # guild_id -> PCMBroadcaster
        self._headless_clients = {}  # guild_id -> PCMBroadcasterWrapper

        # ── DJ/TTS Pre-generation (assets/part2/) ──
        # While a song plays, the pregenerator creates TTS audio and DJ/AI
        # lines for the next songs in the queue, so transitions are instant.
        self._pregenerator = None  # Initialized lazily in on_ready

        # ── Persist voice settings across restarts ──
        self._voice_settings_file = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "voice_settings.json"
        )
        self._load_voice_settings()

    def _get_audio_client(self, guild_id: int):
        """Returns the real VoiceClient, OR the Headless PCMBroadcasterWrapper if streaming autonomously."""
        guild = self.bot.get_guild(guild_id)
        if guild and guild.voice_client:
            # If the user connected the bot to a real Discord voice channel,
            # sync it to the broadcaster matrix natively if YouTube is live
            if self._yt_stream_active and self._yt_stream_guild == guild_id:
                if guild_id not in self._broadcasters:
                    from utils.broadcaster import PCMBroadcaster
                    self._broadcasters[guild_id] = PCMBroadcaster(port=12345)
            return guild.voice_client
            
        if self._yt_stream_active and self._yt_stream_guild == guild_id:
            # Headless 24/7 Mode: The user wants it to stream autonomously
            if guild_id not in self._broadcasters:
                from utils.broadcaster import PCMBroadcaster
                self._broadcasters[guild_id] = PCMBroadcaster(port=12345)
            if guild_id not in self._headless_clients:
                self._headless_clients[guild_id] = PCMBroadcasterWrapper(
                    self.bot, guild_id, self._broadcasters[guild_id]
                )
            return self._headless_clients[guild_id]
            
        return None

    def _is_headless_override(self, guild_id: int):
        """Allows bypassing user-voice dependency for purely headless bot instances."""
        import config
        return (
            getattr(config, "YOUTUBE_STREAM_ENABLED", False)
            and getattr(self, "_yt_stream_active", False)
            and getattr(self, "_yt_stream_guild", None) == guild_id
        )

    def _dispatch_audio_play(self, guild_id: int, source, after=None):
        """Monolithic audio injection point. Replaces vc.play() to support multiplex UDP routing."""
        vc = self._get_audio_client(guild_id)
        if not vc:
            return False

        if self._yt_stream_active and self._yt_stream_guild == guild_id:
            if guild_id not in self._broadcasters:
                from utils.broadcaster import PCMBroadcaster
                self._broadcasters[guild_id] = PCMBroadcaster(port=12345)
                
            broadcaster = self._broadcasters[guild_id]
            
            if isinstance(vc, discord.VoiceClient):
                # Multiplex to both Discord and autonomous RTMP stream
                broadcaster.set_source(source, guild_id=guild_id, bot=self.bot, after=after)
                vc.play(broadcaster, after=None)
            else:
                # Pure Headless Wrapper handles set_source inherently
                vc.play(source, after=after)
        else:
            vc.play(source, after=after)
        return True

    # ── Auto-start YouTube Live autonomous stream on boot ──
    @commands.Cog.listener()
    async def on_ready(self):
        """Auto-start YouTube Live autonomous stream when bot connects.

        If YOUTUBE_STREAM_ENABLED=true and YOUTUBE_STREAM_KEY is set
        and YOUTUBE_STREAM_PLAYLIST (or AUTODJ_DEFAULT_SOURCE) has a
        playlist URL, the bot starts streaming in autonomous (24/7) mode
        immediately — no Discord voice channel needed.
        """
        if not getattr(config, "YOUTUBE_STREAM_ENABLED", False):
            return
        if not YOUTUBE_STREAMER_CLASS:
            return
        if self._yt_stream_active:
            return  # Already streaming

        key = getattr(config, "YOUTUBE_STREAM_KEY", "")
        if not key:
            return

        playlist_url = getattr(config, "YOUTUBE_STREAM_PLAYLIST", "") or getattr(
            config, "AUTODJ_DEFAULT_SOURCE", ""
        )

        # Wait a moment for the bot to fully connect
        await asyncio.sleep(3)

        # Use the first available guild
        guild = self.bot.guilds[0] if self.bot.guilds else None
        if not guild:
            logging.warning("YouTube Live: No guilds available for auto-start")
            return

        rtmp_url = getattr(
            config, "YOUTUBE_STREAM_URL", "rtmp://a.rtmp.youtube.com/live2"
        )
        stream_image = getattr(config, "YOUTUBE_STREAM_IMAGE", "") or None

        self.bot.loop.create_task(
            self.start_headless_stream(guild, key, rtmp_url, stream_image, playlist_url)
        )

    async def start_headless_stream(self, guild, key, rtmp_url, stream_image, playlist_url=None):
        """Starts the autonomous PCMBroadcaster-based stream. Can be called on boot or from UI."""
        if self._yt_stream_active:
            return

        try:
            self._yt_streamer = YOUTUBE_STREAMER_CLASS(
                stream_key=key,
                rtmp_url=rtmp_url,
                rtmp_backup_url=getattr(
                    config,
                    "YOUTUBE_STREAM_BACKUP_URL",
                    "rtmp://b.rtmp.youtube.com/live2?backup=1",
                ),
                stream_image=stream_image,
                stream_gif=getattr(config, "YOUTUBE_STREAM_GIF", "") or None,
                bitrate_audio=int(getattr(config, "YOUTUBE_AUDIO_BITRATE", 192)),
                bitrate_video=int(getattr(config, "YOUTUBE_VIDEO_BITRATE", 3000)),
            )
            self._yt_stream_guild = guild.id
            
            # EAGER INITIALIZATION: Start PCMBroadcaster immediately so it feeds silence to UDP
            from utils.broadcaster import PCMBroadcaster
            if guild.id not in getattr(self, "_broadcasters", {}):
                self._broadcasters[guild.id] = PCMBroadcaster(port=12345)
                
            await self._yt_streamer.start()
            
            # Start Headless AutoDJ Master Loop natively!
            class DummyContext:
                def __init__(self, bot, guild, wrapper):
                    self.bot = bot
                    self.guild = guild
                    self.author = guild.me
                    self.voice_client = wrapper
                    self.channel = guild.text_channels[0] if guild.text_channels else None
                    self.message = type("Mock", (), {"author": self.author})()
                async def send(self, *args, **kwargs):
                    pass
            
            wrapper = PCMBroadcasterWrapper(self.bot, guild.id, self._broadcasters[guild.id])
            ctx = DummyContext(self.bot, guild, wrapper)
            self.autodj_enabled[guild.id] = False
            self.dj_enabled[guild.id] = True
            self.ai_dj_enabled[guild.id] = True
            if playlist_url:
                self.autodj_source[guild.id] = playlist_url
            
            # Use Auto-DJ system natively to seed queue
            # And then explicitly start playback since we're the first track
            async def _fill_and_play():
                self.is_booting = True
                if playlist_url:
                    await self._autodj_fill(ctx)
                
                # Eagerly start pregeneration of DJ assets immediately to cover 
                # MOSS-TTS server cold-start timeouts and have lines ready early.
                try:
                    queue = self.song_queues.get(guild.id)
                    if queue and not queue.empty():
                        pregen = self._get_pregenerator()
                        # Pass empty current_title since this is the very start of the queue
                        asyncio.ensure_future(
                            pregen.pregenerate_upcoming(guild.id, queue, ""),
                            loop=self.bot.loop
                        )
                        
                        # Wait for either 25 tracks to generate, or 120 seconds max timeout
                        max_wait = 120
                        queued_target = min(25, queue.qsize())
                        if queued_target > 0:
                            for _ in range(max_wait):
                                if len(pregen.pregen_queue) >= queued_target:
                                    break
                                await asyncio.sleep(1)
                                
                except Exception as e:
                    logging.debug(f"Pregen: Failed eager startup pregen: {e}")
                    
                self.is_booting = False
                
                queue = self.song_queues.get(guild.id)
                if queue and not queue.empty():
                    asyncio.run_coroutine_threadsafe(self.play_next(ctx), self.bot.loop)
                
            self.bot.loop.create_task(_fill_and_play())
            
            self._yt_stream_active = True
            logging.info(
                f"YouTube Live: ✅ Auto-started autonomous 24/7 stream "
                f"with AutoDJ seamlessly multiplexed via PCMBroadcaster!"
            )
        except Exception as e:
            logging.error(f"YouTube Live: Auto-start failed: {e}")

    # ── Voice settings persistence ────────────────────────────────────

    def _load_voice_settings(self):
        """Load persisted voice settings from voice_settings.json.

        This file stores per-guild DJ voice, AI voice, and AI enabled state
        so they survive bot restarts. If the file doesn't exist or is corrupt,
        we just use the defaults from config.
        """
        try:
            if os.path.isfile(self._voice_settings_file):
                with open(self._voice_settings_file, "r") as f:
                    data = json.load(f)
                for guild_id_str, settings in data.items():
                    try:
                        gid = int(guild_id_str)
                    except (ValueError, TypeError):
                        continue
                    if "dj_voice" in settings:
                        self.dj_voice[gid] = settings["dj_voice"]
                    if "ai_dj_voice" in settings:
                        self.ai_dj_voice[gid] = settings["ai_dj_voice"]
                    if "ai_dj_enabled" in settings:
                        self.ai_dj_enabled[gid] = settings["ai_dj_enabled"]
                    if "dj_enabled" in settings:
                        self.dj_enabled[gid] = settings["dj_enabled"]
                loaded = sum(1 for k in data if isinstance(k, str))
                logging.info(
                    f"DJ: Loaded voice settings for {loaded} guilds from {self._voice_settings_file}"
                )
        except Exception as e:
            logging.warning(f"DJ: Failed to load voice settings: {e}")

    def _save_voice_settings(self):
        """Save current voice settings to voice_settings.json.

        Called automatically whenever a voice or enabled state changes.
        """
        all_guild_ids = (
            set(self.dj_voice.keys())
            | set(self.ai_dj_voice.keys())
            | set(self.ai_dj_enabled.keys())
            | set(self.dj_enabled.keys())
        )
        data = {}
        for gid in all_guild_ids:
            data[str(gid)] = {
                "dj_voice": self.dj_voice.get(gid, ""),
                "ai_dj_voice": self.ai_dj_voice.get(gid, ""),
                "ai_dj_enabled": self.ai_dj_enabled.get(gid, False),
                "dj_enabled": self.dj_enabled.get(gid, False),
            }
        try:
            with open(self._voice_settings_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logging.warning(f"DJ: Failed to save voice settings: {e}")

    async def get_queue(self, guild_id):
        if guild_id not in self.song_queues:
            self.song_queues[guild_id] = asyncio.Queue()
        return self.song_queues[guild_id]

    def clear_yt_auth_block(self):
        """Clear the YouTube auth-blocked flag after cookies are updated.
        Called from the cookie refresh API endpoint. Returns True if
        the flag was set (meaning a retry is needed)."""
        was_blocked = self._yt_auth_blocked
        self._yt_auth_blocked = False
        self._yt_auth_blocked_at = None
        self._yt_auth_retries = 0
        if was_blocked:
            logging.info(
                "YouTube auth block cleared — cookies updated via Mission Control"
            )
        return was_blocked

    async def retry_playback_after_cookie_fix(self, guild_id):
        """Attempt to resume playback after cookies were refreshed.
        Starts the inactivity timer which will trigger auto-DJ / queue processing,
        or directly calls play_next if there are items in the queue."""
        for guild in self.bot.guilds:
            if guild.id == guild_id:
                queue = self.song_queues.get(guild_id)
                voice = guild.voice_client
                if voice and queue and not queue.empty():
                    # Items in queue — trigger play_next via the inactivity callback
                    # which handles finding the right channel
                    logging.info(
                        f"Cookie fix: items in queue for guild {guild_id}, "
                        "starting inactivity timer to resume playback"
                    )
                    self._pending_cookie_retry = True
                    self._start_inactivity_timer(guild_id)
                elif voice:
                    # Queue empty — start inactivity timer for auto-DJ
                    logging.info(
                        f"Cookie fix: queue empty for guild {guild_id}, "
                        "starting inactivity timer for auto-DJ"
                    )
                    self._pending_cookie_retry = True
                    self._start_inactivity_timer(guild_id)
                return

    def _get_np_channel(self, guild):
        """Return the channel where now-playing messages should be sent.

        If NOWPLAYING_CHANNEL_ID is configured and valid, always use that channel.
        Otherwise return None (caller should fall back to ctx.channel).
        """
        np_id = getattr(config, "NOWPLAYING_CHANNEL_ID", 0)
        if np_id:
            channel = self.bot.get_channel(np_id)
            if channel:
                return channel
            logging.warning(
                f"NOWPLAYING_CHANNEL_ID={np_id} not found. Falling back to command channel."
            )
        return None

    def create_embed(self, title, description, color=discord.Color.blurple(), **kwargs):
        embed = discord.Embed(title=title, description=description, color=color)
        for key, value in kwargs.items():
            embed.add_field(name=key, value=value, inline=False)
        return embed

    def _get_progress_bar(self, current_time, total_duration, bar_length=20):
        if total_duration == 0:
            return "━━━━━━━━━━━━"  # Default empty bar

        progress = current_time / total_duration
        filled_length = int(bar_length * progress)
        bar = "━" * filled_length + "●" + "━" * (bar_length - filled_length - 1)
        return bar

    async def _disconnect_if_idle(self, guild_id):
        if guild_id in self.inactivity_timers:
            del self.inactivity_timers[guild_id]
        guild = self.bot.get_guild(guild_id)
        if guild and guild.voice_client and not guild.voice_client.is_playing():
            if self._yt_stream_active:
                logging.info(
                    f"Idle in {guild.name} — switching YouTube Live to autonomous 24/7 mode"
                )
                await self._switch_to_autonomous(guild_id)
                await guild.voice_client.disconnect()
                logging.info(
                    f"Bot disconnected from voice channel in {guild.name} (stream continues autonomously)."
                )
                return
            await guild.voice_client.disconnect()
            logging.info(
                f"Bot disconnected from voice channel in {guild.name} due to inactivity."
            )

    def _start_inactivity_timer(self, guild_id):
        if guild_id in self.inactivity_timers:
            self.inactivity_timers[guild_id].cancel()
        self.inactivity_timers[guild_id] = self.bot.loop.call_later(
            60, lambda: asyncio.ensure_future(self._disconnect_if_idle(guild_id))
        )

    @commands.command(name="join")
    async def join(self, ctx):
        logging.info(f"Join command invoked by {ctx.author} in {ctx.guild.name}")
        if not ctx.author.voice:
            logging.warning(
                f"User {ctx.author} not in a voice channel when trying to join."
            )
            return await ctx.send(
                embed=self.create_embed(
                    "Error",
                    f"{config.ERROR_EMOJI} You are not connected to a voice channel.",
                    discord.Color.red(),
                )
            )
        if ctx.voice_client:
            await ctx.voice_client.move_to(ctx.author.voice.channel)
            logging.info(
                f"Bot moved to voice channel {ctx.author.voice.channel} in {ctx.guild.name}"
            )
        else:
            await ctx.author.voice.channel.connect()
            logging.info(
                f"Bot joined voice channel {ctx.author.voice.channel} in {ctx.guild.name}"
            )
        await ctx.send(
            embed=self.create_embed(
                "Joined Channel",
                f"{config.SUCCESS_EMOJI} Joined `{ctx.author.voice.channel}`",
            )
        )

    @commands.command(name="leave")
    async def leave(self, ctx):
        logging.info(f"Leave command invoked by {ctx.author} in {ctx.guild.name}")
        guild_id = ctx.guild.id

        # Clean up DJ TTS state
        self.dj_playing_tts.pop(guild_id, None)
        self._dj_pending_sounds.pop(guild_id, None)
        pending = getattr(self, "_dj_pending", {}).pop(guild_id, None)
        tts_path = self._current_tts_path.pop(guild_id, None)
        if tts_path:
            cleanup_tts_file(tts_path)

        if ctx.voice_client:
            # If YouTube Live is active, switch to autonomous before disconnecting
            if self._yt_stream_active and self._yt_streamer:
                await self._switch_to_autonomous(guild_id)

            await ctx.voice_client.disconnect()
            logging.info(f"Bot disconnected from voice channel in {ctx.guild.name}")

            # Cancel nowplaying update task
            if (
                guild_id in self.nowplaying_tasks
                and self.nowplaying_tasks[guild_id]
                and not self.nowplaying_tasks[guild_id].done()
            ):
                self.nowplaying_tasks[guild_id].cancel()
                del self.nowplaying_tasks[guild_id]

            stream_msg = ""
            if self._yt_stream_active:
                stream_msg = (
                    "\n\n🔴 **YouTube Live** stream continues in autonomous 24/7 mode!"
                )
            await ctx.send(
                embed=self.create_embed(
                    "Left Channel",
                    f"{config.SUCCESS_EMOJI} Successfully disconnected from the voice channel.{stream_msg}",
                )
            )
        else:
            logging.warning(
                f"Leave command invoked but bot not in a voice channel in {ctx.guild.name}"
            )
            await ctx.send(
                embed=self.create_embed(
                    "Error",
                    f"{config.ERROR_EMOJI} I am not currently in a voice channel.",
                    discord.Color.red(),
                )
            )

    @commands.command(name="search")
    async def search(self, ctx, *, query):
        logging.info(
            f"Search command invoked by {ctx.author} in {ctx.guild.name} with query: {query}"
        )
        if not config.YOUTUBE_API_KEY:
            logging.error("YouTube API key is not set.")
            return await ctx.send(
                embed=self.create_embed(
                    "Error",
                    f"{config.ERROR_EMOJI} YouTube API key is not set.",
                    discord.Color.red(),
                )
            )
        try:
            youtube_service = build(
                "youtube", "v3", developerKey=config.YOUTUBE_API_KEY
            )
            search_response = (
                youtube_service.search()
                .list(q=query, part="snippet", maxResults=10, type="video")
                .execute()
            )

            if not search_response:
                logging.warning(
                    f"YouTube API returned empty response for query: {query}"
                )
                return await ctx.send(
                    embed=self.create_embed(
                        "Search Error",
                        "The YouTube API returned an empty response. Please check your API key.",
                        discord.Color.red(),
                    )
                )

            videos = [
                (item["snippet"]["title"], item["id"]["videoId"])
                for item in search_response.get("items", [])
            ]
            if not videos:
                logging.info(f"No videos found for query: {query}")
                return await ctx.send(
                    embed=self.create_embed(
                        "No Results",
                        f"{config.ERROR_EMOJI} No songs found for your query.",
                        discord.Color.orange(),
                    )
                )
            self.search_results[ctx.guild.id] = videos
            response = "\n".join(
                f"**{i + 1}.** {title}" for i, (title, _) in enumerate(videos)
            )
            logging.info(f"Found {len(videos)} search results for query: {query}")
            await ctx.send(embed=self.create_embed("Search Results", response))
        except Exception as e:
            logging.error(f"Error in search command for query '{query}': {e}")
            await ctx.send(
                embed=self.create_embed(
                    "Search Error", f"An error occurred: {e}", discord.Color.red()
                )
            )

    @commands.command(name="play")
    async def play(self, ctx, *, query):
        logging.info(f"Play command received with query: {query}")
        is_headless = self._is_headless_override(ctx.guild.id)
        if not getattr(ctx.author, "voice", None) and not is_headless:
            logging.warning("User not in a voice channel.")
            return await ctx.send(
                embed=self.create_embed(
                    "Error",
                    f"{config.ERROR_EMOJI} You must be in a voice channel to play music.",
                    discord.Color.red(),
                )
            )

        queue = await self.get_queue(ctx.guild.id)
        result = None  # Initialize result to avoid UnboundLocalError
        try:
            try:
                async with ctx.typing():
                    # --- Suno.com URL ---
                    if is_suno_url(query):
                        logging.info(f"Detected Suno URL: {query}")
                        track = await get_suno_track(query)
                        if not track:
                            return await ctx.send(
                                embed=self.create_embed(
                                    "Suno Error",
                                    f"{config.ERROR_EMOJI} Could not resolve that Suno song. It may be private or the URL is invalid.",
                                    discord.Color.red(),
                                )
                            )
                        await queue.put(track)
                        logging.info(f"Added Suno track '{track.title}' to queue.")
                        await ctx.send(
                            embed=self.create_embed(
                                "Song Added",
                                f"{config.QUEUE_EMOJI} Added `{track.title}` to the queue.",
                            )
                        )

                    # --- YouTube / search ---
                    else:
                        if query.isdigit() and ctx.guild.id in self.search_results:
                            video_id = self.search_results[ctx.guild.id][
                                int(query) - 1
                            ][1]
                            url = f"https://www.youtube.com/watch?v={video_id}"
                        else:
                            url = query

                        logging.info(f"Attempting to get YTDLSource from URL: {url}")
                        result = await YTDLSource.from_url(url, loop=self.bot.loop)
                        logging.info(
                            f"YTDLSource.from_url returned type: {type(result)}, content: {result}"
                        )

                        if not result:
                            logging.warning("Could not find any playable content.")
                            return await ctx.send(
                                embed=self.create_embed(
                                    "No Results",
                                    f"{config.ERROR_EMOJI} Could not find any playable content for your query.",
                                    discord.Color.orange(),
                                )
                            )

                        if isinstance(result, list):
                            logging.info(
                                f"YTDLSource.from_url returned a list. Number of entries: {len(result)}"
                            )
                            for entry in result:
                                await queue.put(entry)
                                logging.info(f"Added {entry.title} to queue.")
                            await ctx.send(
                                embed=self.create_embed(
                                    "Playlist Added",
                                    f"{config.QUEUE_EMOJI} Added {len(result)} songs to the queue.",
                                )
                            )
                        else:
                            logging.info("Found single entry.")
                            await queue.put(result)
                            await ctx.send(
                                embed=self.create_embed(
                                    "Song Added",
                                    f"{config.QUEUE_EMOJI} Added `{result.title}` to the queue.",
                                )
                            )
            except discord.DiscordServerError:
                logging.warning(
                    "Discord typing failed due to 503 error. Proceeding with remainder of play command."
                )

            # Join voice AFTER extraction succeeds so the connection doesn't sit idle
            vc = self._get_audio_client(ctx.guild.id)
            if not is_headless:
                if not vc:
                    logging.info("Bot not in a voice channel, joining.")
                    await ctx.author.voice.channel.connect(self_deaf=True)
                    vc = self._get_audio_client(ctx.guild.id)
                elif getattr(vc, "is_connected", lambda: True)() is False:
                    logging.info(
                        "Voice client exists but is not connected — force-reconnecting."
                    )
                    await ctx.voice_client.disconnect(force=True)
                    await asyncio.sleep(0.5)
                    await ctx.author.voice.channel.connect(self_deaf=True)
                    vc = self._get_audio_client(ctx.guild.id)

            if not vc:
                return

            if not getattr(vc, "is_playing", lambda: True)():
                logging.info("Voice client not playing, starting playback.")
                if ctx.guild.id in self.inactivity_timers:
                    self.inactivity_timers[ctx.guild.id].cancel()
                    del self.inactivity_timers[ctx.guild.id]
                await self.play_next(ctx)
        except Exception as e:
            logging.error(f"Error in play command: {e}")
            await ctx.send(
                embed=self.create_embed(
                    "Error", f"An error occurred: {e}", discord.Color.red()
                )
            )

    @commands.command(name="playlist")
    async def playlist(self, ctx, *, url):
        logging.info(f"Playlist command received with URL: {url}")
        is_headless = self._is_headless_override(ctx.guild.id)
        if not getattr(ctx.author, "voice", None) and not is_headless:
            logging.warning("User not in a voice channel.")
            return await ctx.send(
                embed=self.create_embed(
                    "Error",
                    f"{config.ERROR_EMOJI} You must be in a voice channel to play music.",
                    discord.Color.red(),
                )
            )

        vc = self._get_audio_client(ctx.guild.id)
        if not is_headless and not vc:
            logging.info("Bot not in a voice channel, joining.")
            await ctx.author.voice.channel.connect(self_deaf=True)
            vc = self._get_audio_client(ctx.guild.id)

        queue = await self.get_queue(ctx.guild.id)
        try:
            try:
                async with ctx.typing():
                    logging.info(f"Fast-extracting playlist from URL: {url}")

                    # Fast two-pass extraction:
                    # Pass 1: extract_flat=True — instant metadata only (title, ID)
                    # Pass 2: happens in play_next — resolve stream URL per-song
                    # No playlist_items limit — load the entire playlist
                    result = await PlaceholderTrack.from_playlist_url(
                        url, loop=self.bot.loop
                    )
                    logging.info(f"Playlist extraction returned {len(result)} entries")

                    if not result:
                        return await ctx.send(
                            embed=self.create_embed(
                                "No Playlist Found",
                                f"{config.ERROR_EMOJI} Could not find any playable playlist content for your URL, or it's not a valid playlist URL.",
                                discord.Color.orange(),
                            )
                        )

                    added_count = 0
                    for entry in result:
                        await queue.put(entry)
                        added_count += 1

                    if added_count > 0:
                        await ctx.send(
                            embed=self.create_embed(
                                "Playlist Added",
                                f"{config.QUEUE_EMOJI} Added {added_count} songs from the playlist to the queue.",
                            )
                        )
                    else:
                        await ctx.send(
                            embed=self.create_embed(
                                "No Songs Added",
                                f"{config.ERROR_EMOJI} No playable songs were found in the playlist.",
                                discord.Color.orange(),
                            )
                        )
            except discord.DiscordServerError:
                logging.warning("Discord typing failed in playlist. Proceeding.")

            if vc and not getattr(vc, "is_playing", lambda: True)():
                logging.info("Voice client not playing, starting playback.")
                if ctx.guild.id in self.inactivity_timers:
                    self.inactivity_timers[ctx.guild.id].cancel()
                    del self.inactivity_timers[ctx.guild.id]
                await self.play_next(ctx)
        except Exception as e:
            logging.error(f"Error in playlist command: {e}")
            await ctx.send(
                embed=self.create_embed(
                    "Error", f"An error occurred: {e}", discord.Color.red()
                )
            )

    @commands.command(name="radio")
    async def radio(self, ctx, *, url):
        logging.info(f"Radio command received with URL: {url}")
        is_headless = self._is_headless_override(ctx.guild.id)
        if not getattr(ctx.author, "voice", None) and not is_headless:
            logging.warning("User not in a voice channel.")
            return await ctx.send(
                embed=self.create_embed(
                    "Error",
                    f"{config.ERROR_EMOJI} You must be in a voice channel to play music.",
                    discord.Color.red(),
                )
            )

        vc = self._get_audio_client(ctx.guild.id)
        if not is_headless and not vc:
            logging.info("Bot not in a voice channel, joining.")
            await ctx.author.voice.channel.connect(self_deaf=True)
            vc = self._get_audio_client(ctx.guild.id)

        queue = await self.get_queue(ctx.guild.id)
        loading_msg = await ctx.send(
            embed=self.create_embed(
                "Loading Radio",
                f"{config.QUEUE_EMOJI} Fetching playlist songs, please wait...",
            )
        )

        try:
            try:
                async with ctx.typing():
                    logging.info(f"Fast-extracting radio playlist from URL: {url}")

                    # Fast two-pass extraction (same as ?playlist — entire playlist)
                    # No playlist_items limit — load the entire playlist
                    result = await PlaceholderTrack.from_playlist_url(
                        url, loop=self.bot.loop
                    )
                    logging.info(f"Radio extraction returned {len(result)} entries")

                    if not result:
                        await loading_msg.delete()
                        return await ctx.send(
                            embed=self.create_embed(
                                "No Radio Content",
                                f"{config.ERROR_EMOJI} Could not find any playable playlist content for your URL.",
                                discord.Color.orange(),
                            )
                        )

                    added_count = 0
                    for entry in result:
                        await queue.put(entry)
                        added_count += 1

                    await loading_msg.delete()
                    if added_count > 0:
                        await ctx.send(
                            embed=self.create_embed(
                                "Radio Started",
                                f"{config.SUCCESS_EMOJI} Loaded {added_count} songs from the radio playlist into the queue.",
                            )
                        )
                    else:
                        await ctx.send(
                            embed=self.create_embed(
                                "No Songs Added",
                                f"{config.ERROR_EMOJI} No playable songs were found.",
                                discord.Color.orange(),
                            )
                        )
            except discord.DiscordServerError:
                logging.warning("Discord typing failed in radio. Proceeding.")

            if vc and not getattr(vc, "is_playing", lambda: True)():
                logging.info("Voice client not playing, starting playback.")
                if ctx.guild.id in self.inactivity_timers:
                    self.inactivity_timers[ctx.guild.id].cancel()
                    del self.inactivity_timers[ctx.guild.id]
                await self.play_next(ctx)
        except Exception as e:
            try:
                await loading_msg.delete()
            except:
                pass
            logging.error(f"Error in radio command: {e}")
            await ctx.send(
                embed=self.create_embed(
                    "Error",
                    f"An error occurred while loading radio: {e}",
                    discord.Color.red(),
                )
            )

    async def play_next(self, ctx, _skip_count=0):
        logging.info("play_next called.")
        queue = await self.get_queue(ctx.guild.id)
        if not queue.empty() and ctx.voice_client:
            data = await queue.get()
            guild_id = ctx.guild.id
            logging.info(
                f"play_next: Dequeued {type(data).__name__} — "
                f"title={data.title}, url={str(data.url)[:60] if data.url else 'None'}"
            )

            # ── Resolve PlaceholderTracks lazily ────────────────────
            # Playlist/radio entries are fetched as PlaceholderTracks
            # (fast, metadata-only). We resolve the actual stream URL
            # right now, right before playback. This keeps playlist
            # loading instant while ensuring reliable playback.
            if isinstance(data, PlaceholderTrack):
                # Guard: if too many consecutive resolutions fail, stop
                # to avoid infinite recursion / rate limiting
                if _skip_count >= 5:
                    logging.error(
                        f"play_next: {_skip_count} consecutive resolution failures in "
                        f"{ctx.guild.name}. Stopping — possible site block or auth issue."
                    )
                    # ── Detect YouTube auth block specifically ──
                    # This triggers the self-healing cookie banner in Mission Control
                    self._yt_auth_blocked = True
                    self._yt_auth_blocked_at = time.time()
                    channel = self.bot.get_channel(ctx.channel.id)
                    # Build a more helpful error message
                    error_hint = (
                        "YouTube playback failed. This is usually caused by one of:\n"
                        "• **Outdated yt-dlp** — Run `pip install -U yt-dlp` and restart the bot\n"
                        "• **Missing cookies** — Open **Mission Control** → **Settings** → **Cookie Auth**\n"
                        "• **No YouTube API key** — Set `YOUTUBE_API_KEY` in `.env` for search\n\n"
                        "Fastest fix: `pip install -U yt-dlp` then restart the bot."
                    )
                    if channel:
                        await channel.send(
                            embed=self.create_embed(
                                "Playback Error",
                                f"{config.ERROR_EMOJI} {error_hint}",
                                discord.Color.red(),
                            )
                        )
                    await self.bot.change_presence(activity=None)
                    self._start_inactivity_timer(guild_id)
                    return

                resolve_url = data.webpage_url
                if not resolve_url:
                    logging.error(
                        f"play_next: PlaceholderTrack has no webpage_url, skipping. Title: {data.title}"
                    )
                    await asyncio.sleep(1)
                    await self.play_next(ctx, _skip_count=_skip_count + 1)
                    return

                logging.info(
                    f"play_next: Resolving PlaceholderTrack '{data.title}' → {resolve_url}"
                )
                try:
                    resolved = await YTDLSource.resolve(resolve_url, loop=self.bot.loop)
                    data = resolved
                    # Verify we actually got a usable stream URL
                    if not data.url or data.url.startswith(
                        "https://www.youtube.com/watch"
                    ):
                        logging.error(
                            f"play_next: Resolution returned a webpage URL, not a stream. "
                            f"Title: {data.title}, url: {data.url}"
                        )
                        await asyncio.sleep(2)
                        await self.play_next(ctx, _skip_count=_skip_count + 1)
                        return
                    logging.info(
                        f"play_next: Resolved PlaceholderTrack to '{data.title}' "
                        f"(stream url ready)"
                    )
                except Exception as e:
                    error_str = str(e).lower()
                    logging.error(
                        f"play_next: Failed to resolve PlaceholderTrack '{data.title}': {e}"
                    )
                    # Detect YouTube errors early (don't wait for 5 failures)
                    if "sign in to confirm" in error_str or (
                        "bot" in error_str and "resolve" in error_str
                    ):
                        self._yt_auth_blocked = True
                        self._yt_auth_blocked_at = time.time()
                        logging.warning(
                            "play_next: YouTube auth block detected — "
                            "cookies required. Mission Control can fix this."
                        )
                    elif "format is not available" in error_str:
                        # This usually means yt-dlp's cipher/signature solver is outdated.
                        # Cookies alone won't fix this — need to upgrade yt-dlp.
                        self._yt_auth_blocked = True
                        self._yt_auth_blocked_at = time.time()
                        logging.error(
                            "play_next: YouTube 'format not available' — "
                            "yt-dlp likely OUTDATED and can't solve YouTube's cipher. "
                            "UPGRADE: pip install -U yt-dlp  (cookies alone won't fix this)"
                        )
                    await asyncio.sleep(2)
                    await self.play_next(ctx, _skip_count=_skip_count + 1)
                    return

            # ── DJ Mode: Speak an intro before the song ────────────
            if (
                self.dj_enabled.get(guild_id, False)
                and TTS_AVAILABLE
                and not self.dj_playing_tts.get(guild_id, False)
            ):
                # Build the DJ intro text
                prev_song = self.current_song.get(guild_id)

                if prev_song:
                    # Transition from a previous track — outro the old,
                    # intro the new, all in one spoken line
                    peek_title = None
                    if not queue.empty() and queue.qsize() > 0:
                        peek_title = queue._queue[0].title
                    intro_text = generate_outro(
                        prev_song.title,
                        has_next=True,
                        next_title=data.title,
                        queue_size=queue.qsize(),
                    )
                else:
                    # First song in the session — full intro with greeting
                    intro_text = generate_intro(data.title, queue_size=queue.qsize())

                # Store the pending song so _on_tts_done can pick it up
                if not hasattr(self, "_dj_pending"):
                    self._dj_pending = {}
                self._dj_pending[guild_id] = (ctx, data, ctx.channel.id)

                spoke = await self._dj_speak(ctx.voice_client, intro_text, guild_id)
                if spoke:
                    # TTS started — song will play after the intro finishes
                    # (via _on_tts_done → _play_song_after_dj → _start_song_playback)
                    # Note: We don't start bed music here because Discord's voice
                    # client can only play one audio source at a time. The TTS is
                    # already playing, so bed music would fail with "Already playing
                    # audio". Bed music is started in _on_tts_done if there's a gap
                    # (e.g. sound effects) between TTS and the song.
                    return
                else:
                    # TTS failed — fall through to play the song directly
                    logging.warning(
                        f"DJ: TTS intro failed for guild {guild_id}, playing song directly."
                    )

            # ── Direct playback (no DJ intro, or TTS failed) ───────
            await self._start_song_playback(ctx, data, ctx.channel.id)
        else:
            # ── Queue empty ────────────────────────────────────────
            logging.info("Queue is empty, stopping playback.")
            guild_id = ctx.guild.id

            # Auto-DJ: refill the queue instead of going silent
            if self.autodj_enabled.get(guild_id, False):
                filled = await self._autodj_fill(ctx)
                if filled:
                    logging.info(f"Auto-DJ: Refilled queue for guild {guild_id}")
                    # Don't speak an outro — just keep going
                    await self.play_next(ctx)
                    return

            # DJ Mode: Speak an outro for the last song
            if (
                self.dj_enabled.get(guild_id, False)
                and TTS_AVAILABLE
                and guild_id in self.current_song
                and self.current_song[guild_id]
                and not self.dj_playing_tts.get(guild_id, False)
            ):
                last_song = self.current_song[guild_id]
                outro_text = generate_outro(last_song.title, has_next=False)
                await self._dj_speak(ctx.voice_client, outro_text, guild_id)

            await self.bot.change_presence(activity=None)
            self._start_inactivity_timer(guild_id)

    async def _update_nowplaying_message(self, guild_id, channel_id):
        logging.info(f"_update_nowplaying_message: Starting task for guild {guild_id}")
        while True:
            try:
                guild = self.bot.get_guild(guild_id)
                if not guild or not guild.voice_client:
                    logging.warning(
                        f"_update_nowplaying_message: Bot not in a voice channel for guild {guild_id}. Cancelling task."
                    )
                    break

                channel = self.bot.get_channel(channel_id)
                if not channel:
                    logging.warning(
                        f"_update_nowplaying_message: Channel ({channel_id}) not found. Cancelling task."
                    )
                    break

                await self._update_nowplaying_display(
                    guild_id, channel.id, silent_update=True
                )
                logging.debug(
                    f"_update_nowplaying_message: Message updated for guild {guild_id}. Stored Message ID: {self.nowplaying_message.get(guild_id).id if self.nowplaying_message.get(guild_id) else 'None'}"
                )
                await asyncio.sleep(40)  # Update every 40 seconds
            except asyncio.CancelledError:
                logging.info(
                    f"_update_nowplaying_message: Task cancelled for {guild_id}"
                )
                break
            except discord.DiscordServerError:
                logging.error(
                    f"_update_nowplaying_message: Discord 503 error for guild {guild_id}. Sleeping 60s."
                )
                await asyncio.sleep(60)
            except Exception as e:
                logging.error(
                    f"_update_nowplaying_message: Error updating message for guild {guild_id}: {e}",
                    exc_info=True,
                )
                await asyncio.sleep(10)  # Wait before retrying

    async def _update_nowplaying_display(
        self, guild_id, channel_id, silent_update=False
    ):
        logging.debug(
            f"_update_nowplaying_display: Called for guild {guild_id}, channel {channel_id}. Silent: {silent_update}."
        )
        guild = self.bot.get_guild(guild_id)
        channel = self.bot.get_channel(channel_id)

        if not guild or not channel:
            logging.warning(
                f"nowplaying_display: Guild ({guild_id}) or channel ({channel_id}) not found. Aborting update."
            )
            return

        current_nowplaying_message = self.nowplaying_message.get(guild_id)
        logging.debug(
            f"_update_nowplaying_display: Stored message object: {current_nowplaying_message.id if current_nowplaying_message else 'None'}"
        )

        if guild_id in self.current_song and self.current_song[guild_id]:
            data = self.current_song[guild_id]
            queue = await self.get_queue(guild_id)  # Pass guild_id directly

            current_time = int(time.time() - self.song_start_time[guild_id])
            duration = (
                data.duration or 0
            )  # Safety: None → 0 (shows live/unknown indicator)
            progress_bar = self._get_progress_bar(current_time, duration)

            duration_str = f"{duration // 60}:{duration % 60:02d}" if duration else "∞"
            embed = self.create_embed(
                f"{config.PLAY_EMOJI} Now Playing",
                f"[{data.title}]({data.webpage_url})\n\n{progress_bar} {current_time // 60}:{current_time % 60:02d} / {duration_str}",
                Queue=f"{queue.qsize()} songs remaining",
            )
            embed.set_thumbnail(url=data.thumbnail)

            view = discord.ui.View(timeout=None)
            view.add_item(
                discord.ui.Button(
                    label="Play/Resume",
                    emoji=config.PLAY_EMOJI,
                    style=discord.ButtonStyle.secondary,
                    custom_id="play",
                )
            )
            view.add_item(
                discord.ui.Button(
                    label="Pause",
                    emoji=config.PAUSE_EMOJI,
                    style=discord.ButtonStyle.secondary,
                    custom_id="pause",
                )
            )
            view.add_item(
                discord.ui.Button(
                    label="Skip",
                    emoji=config.SKIP_EMOJI,
                    style=discord.ButtonStyle.secondary,
                    custom_id="skip",
                )
            )
            view.add_item(
                discord.ui.Button(
                    label="Stop",
                    emoji=config.ERROR_EMOJI,
                    style=discord.ButtonStyle.danger,
                    custom_id="stop",
                )
            )
            view.add_item(
                discord.ui.Button(
                    label="Queue",
                    emoji=config.QUEUE_EMOJI,
                    style=discord.ButtonStyle.primary,
                    custom_id="queue",
                )
            )

            if current_nowplaying_message:
                try:
                    # Edit the stored message directly to reduce API calls (no fetch_message needed)
                    await current_nowplaying_message.edit(embed=embed, view=view)
                    logging.info(
                        f"nowplaying_display: Edited message {current_nowplaying_message.id} for {data.title} in {guild.name}"
                    )
                except (discord.NotFound, discord.Forbidden):
                    logging.warning(
                        f"nowplaying_display: Previous message {current_nowplaying_message.id} not found or no permission in {guild.name}. Sending new message."
                    )
                    self.nowplaying_message[guild_id] = await channel.send(
                        embed=embed, view=view
                    )
                    logging.info(
                        f"nowplaying_display: Sent new message {self.nowplaying_message[guild_id].id} for {data.title} in {guild.name}"
                    )
                except discord.DiscordServerError:
                    # Don't try to send a new message if the server is already down/unstable
                    logging.error(
                        f"nowplaying_display: Discord Server Error (5xx) while editing message in {guild.name}."
                    )
                    raise  # Re-raise for the background task to handle (it will sleep)
                except Exception as e:
                    logging.error(
                        f"nowplaying_display: Error editing message {current_nowplaying_message.id} for {data.title} in {guild.name}: {e}"
                    )
                    # Try sending a new message as a fallback for other errors
                    self.nowplaying_message[guild_id] = await channel.send(
                        embed=embed, view=view
                    )

            else:
                self.nowplaying_message[guild_id] = await channel.send(
                    embed=embed, view=view
                )
                logging.info(
                    f"nowplaying_display: Sent initial message {self.nowplaying_message[guild_id].id} for {data.title} in {guild.name}"
                )
        else:  # Nothing is playing
            logging.debug(
                f"nowplaying_display: Nothing playing for guild {guild_id}. Stored message: {current_nowplaying_message.id if current_nowplaying_message else 'None'}"
            )
            if current_nowplaying_message:
                try:
                    # Attempt to fetch before deleting to avoid NotFound error if already gone
                    fetched_message = await channel.fetch_message(
                        current_nowplaying_message.id
                    )
                    logging.debug(
                        f"nowplaying_display: Fetched message {fetched_message.id} for deletion."
                    )
                    await fetched_message.delete()
                    del self.nowplaying_message[guild_id]
                    logging.info(
                        f"nowplaying_display: Deleted previous message {current_nowplaying_message.id} as nothing is playing in {guild.name}"
                    )
                except discord.NotFound:
                    logging.warning(
                        f"nowplaying_display: Previous message {current_nowplaying_message.id} not found for deletion in {guild.name}. Already gone?"
                    )
                    pass  # Message already deleted
                except Exception as e:
                    logging.error(
                        f"nowplaying_display: Error deleting message {current_nowplaying_message.id} in {guild.name}: {e}",
                        exc_info=True,
                    )

            # Only send "Not Playing" if not a silent update and no message is currently displayed
            if not silent_update and not current_nowplaying_message:
                self.nowplaying_message[guild_id] = await channel.send(
                    embed=self.create_embed(
                        "Not Playing", "The bot is not currently playing anything."
                    )
                )
                logging.info(
                    f"nowplaying_display: Nothing playing in {guild.name}. Sent 'Not Playing' message."
                )
            elif (
                silent_update
                and current_nowplaying_message
                and current_nowplaying_message.embeds
                and current_nowplaying_message.embeds[0].title == "Not Playing"
            ):
                # If it's a silent update and the current message is "Not Playing", do nothing to avoid spam
                logging.debug(
                    f"nowplaying_display: Silent update, and 'Not Playing' message already present for {guild.name}. Skipping."
                )
                pass
            elif silent_update and not current_nowplaying_message:
                # If it's a silent update and no message is present, do nothing. A new message will be sent when a song starts.
                logging.debug(
                    f"nowplaying_display: Silent update, no message present for {guild.name}. Skipping sending 'Not Playing'."
                )
                pass
            else:
                # If it's not a silent update, or if there's an old song message, send a new "Not Playing" message
                if not silent_update:
                    self.nowplaying_message[guild_id] = await channel.send(
                        embed=self.create_embed(
                            "Not Playing", "The bot is not currently playing anything."
                        )
                    )
                    logging.info(
                        f"nowplaying_display: Nothing playing in {guild.name}. Sent 'Not Playing' message (non-silent or old message)."
                    )

    async def _after_playback(self, ctx, error):
        guild_id = ctx.guild.id
        if error:
            logging.error(f"Player error in {ctx.guild.name}: {error}", exc_info=True)
            channel = self.bot.get_channel(ctx.channel.id)
            if channel:
                try:
                    await channel.send(
                        embed=self.create_embed(
                            "Playback Error",
                            f"{config.ERROR_EMOJI} Playback error: {error}",
                            discord.Color.red(),
                        )
                    )
                except Exception:
                    pass  # Don't crash trying to report a crash

        # ── YouTube Live: Show waiting card between songs ──
        if self._yt_stream_active and self._yt_streamer:
            asyncio.ensure_future(
                self._yt_streamer.play_waiting(
                    f"Waiting for next track... | {config.STATION_NAME} Radio"
                ),
                loop=self.bot.loop,
            )

        queue = await self.get_queue(guild_id)

        # Check if looping is enabled
        if self.looping.get(guild_id):
            # If looping, re-add the current song to the queue
            current_song_data = self.current_song.get(guild_id)
            if current_song_data:
                await queue.put(current_song_data)
                logging.info(
                    f"Looping enabled. Re-added {current_song_data.get('title', 'Unknown')} to queue."
                )

        # Play the next song in the queue
        await self.play_next(ctx)

        # If queue is empty and not looping, cancel the nowplaying update task
        if queue.empty() and not self.looping.get(ctx.guild.id):
            if (
                ctx.guild.id in self.nowplaying_tasks
                and self.nowplaying_tasks[ctx.guild.id]
                and not self.nowplaying_tasks[ctx.guild.id].done()
            ):
                self.nowplaying_tasks[ctx.guild.id].cancel()
                del self.nowplaying_tasks[ctx.guild.id]

    @commands.command(name="volume")
    async def volume(self, ctx, volume: int):
        logging.info(
            f"Volume command invoked by {ctx.author} in {ctx.guild.name} with volume: {volume}"
        )
        guild_id = ctx.guild.id
        if not ctx.voice_client or not ctx.voice_client.is_playing():
            await ctx.send(
                embed=self.create_embed(
                    "Error",
                    f"{config.ERROR_EMOJI} Not currently playing anything to set volume for.",
                    discord.Color.red(),
                )
            )
            return

        if 0 <= volume <= 200:
            new_volume_float = volume / 100
            ctx.voice_client.source.volume = new_volume_float
            self.current_volume[guild_id] = new_volume_float  # Store the volume
            logging.info(
                f"Volume set to {volume}% in {ctx.guild.name}. Actual source volume: {ctx.voice_client.source.volume}"
            )
            await ctx.send(
                embed=self.create_embed(
                    "Volume Control", f"{config.SUCCESS_EMOJI} Volume set to {volume}%"
                )
            )
        else:
            logging.warning(
                f"Invalid volume {volume} provided by {ctx.author} in {ctx.guild.name}"
            )
            await ctx.send(
                embed=self.create_embed(
                    "Error",
                    f"{config.ERROR_EMOJI} Volume must be between 0 and 200.",
                    discord.Color.red(),
                )
            )

    @commands.command(name="nowplaying")
    async def nowplaying(self, ctx, silent=False):
        logging.info(
            f"Nowplaying command invoked by {ctx.author} in {ctx.guild.name} (silent: {silent})"
        )
        guild_id = ctx.guild.id

        # Determine which channel to send the now-playing message to.
        # If NOWPLAYING_CHANNEL_ID is set, always send there. Otherwise use ctx.channel.
        np_channel = self._get_np_channel(ctx.guild)
        target_channel = np_channel or ctx.channel
        channel_id = target_channel.id

        # If invoked by a user, send a new message and store it for future updates
        if not silent:
            # Delete previous nowplaying message if it exists
            if (
                guild_id in self.nowplaying_message
                and self.nowplaying_message[guild_id]
            ):
                try:
                    await self.nowplaying_message[guild_id].delete()
                    del self.nowplaying_message[guild_id]
                    logging.info(
                        f"nowplaying: Deleted previous nowplaying message for {ctx.guild.name}"
                    )
                except discord.NotFound:
                    pass
                except Exception as e:
                    logging.error(
                        f"nowplaying: Error deleting old message in {ctx.guild.name}: {e}",
                        exc_info=True,
                    )

            # Send a new message and store it
            if guild_id in self.current_song and self.current_song[guild_id]:
                data = self.current_song[guild_id]
                queue = await self.get_queue(ctx.guild.id)
                current_time = int(time.time() - self.song_start_time[guild_id])
                duration = data.duration or 0
                progress_bar = self._get_progress_bar(current_time, duration)
                duration_str = (
                    f"{duration // 60}:{duration % 60:02d}" if duration else "∞"
                )
                embed = self.create_embed(
                    f"{config.PLAY_EMOJI} Now Playing",
                    f"[{data.title}]({data.webpage_url})\n\n{progress_bar} {current_time // 60}:{current_time % 60:02d} / {duration_str}",
                    Queue=f"{queue.qsize()} songs remaining",
                )
                embed.set_thumbnail(url=data.thumbnail)
                view = discord.ui.View(timeout=None)
                view.add_item(
                    discord.ui.Button(
                        label="Play/Resume",
                        emoji=config.PLAY_EMOJI,
                        style=discord.ButtonStyle.secondary,
                        custom_id="play",
                    )
                )
                view.add_item(
                    discord.ui.Button(
                        label="Pause",
                        emoji=config.PAUSE_EMOJI,
                        style=discord.ButtonStyle.secondary,
                        custom_id="pause",
                    )
                )
                view.add_item(
                    discord.ui.Button(
                        label="Skip",
                        emoji=config.SKIP_EMOJI,
                        style=discord.ButtonStyle.secondary,
                        custom_id="skip",
                    )
                )
                view.add_item(
                    discord.ui.Button(
                        label="Stop",
                        emoji=config.ERROR_EMOJI,
                        style=discord.ButtonStyle.danger,
                        custom_id="stop",
                    )
                )
                view.add_item(
                    discord.ui.Button(
                        label="Queue",
                        emoji=config.QUEUE_EMOJI,
                        style=discord.ButtonStyle.primary,
                        custom_id="queue",
                    )
                )

                self.nowplaying_message[guild_id] = await target_channel.send(
                    embed=embed, view=view
                )
                logging.info(
                    f"nowplaying: Sent initial message {self.nowplaying_message[guild_id].id} for {data.title} in {ctx.guild.name}"
                )
            else:
                self.nowplaying_message[guild_id] = await target_channel.send(
                    embed=self.create_embed(
                        "Not Playing", "The bot is not currently playing anything."
                    )
                )
                logging.info(
                    f"nowplaying: Sent initial 'Not Playing' message for {ctx.guild.name} in #{target_channel.name}"
                )

        # The background task will call _update_nowplaying_display silently
        # This command itself doesn't need to call it if it just sent a new message
        # If it was a silent call (from the background task), then _update_nowplaying_display is already called by the task loop

    @commands.command(name="queue")
    async def queue_info(self, ctx):
        logging.info(f"Queue command invoked by {ctx.author} in {ctx.guild.name})")
        queue = await self.get_queue(
            ctx.guild.id
        )  # Ensure get_queue is called with guild_id
        if not queue.empty():
            queue_list = "\n".join(
                f"**{i + 1}.** {item.title}"
                for i, item in enumerate(list(queue._queue))
            )
            logging.info(
                f"Displaying queue with {queue.qsize()} songs for {ctx.guild.name})"
            )
            await ctx.send(
                embed=self.create_embed(
                    f"{config.QUEUE_EMOJI} Current Queue", queue_list
                )
            )
        else:
            logging.info(f"Queue is empty for {ctx.guild.name})")
            await ctx.send(
                embed=self.create_embed("Empty Queue", "The queue is currently empty.")
            )

    @commands.command(name="skip")
    async def skip(self, ctx):
        logging.info(f"Skip command invoked by {ctx.author} in {ctx.guild.name}")
        guild_id = ctx.guild.id
        vc = self._get_audio_client(guild_id)

        # If DJ is currently speaking, cancel the TTS and pending song
        if self.dj_playing_tts.get(guild_id, False):
            logging.info(f"DJ: Skipping TTS intro in {ctx.guild.name}")
            self.dj_playing_tts[guild_id] = False
            pending = getattr(self, "_dj_pending", {}).pop(guild_id, None)
            if vc and vc.is_playing():
                vc.stop()
            # Clean up TTS temp file
            tts_path = self._current_tts_path.get(guild_id)
            if tts_path:
                cleanup_tts_file(tts_path)
                self._current_tts_path[guild_id] = None

        if vc and vc.is_playing():
            vc.stop()
            logging.info(f"Song skipped in {ctx.guild.name}")
            await ctx.send(
                embed=self.create_embed(
                    "Song Skipped",
                    f"{config.SKIP_EMOJI} The current song has been skipped.",
                )
            )
        else:
            logging.warning(
                f"Skip command invoked but nothing is playing in {ctx.guild.name}"
            )
            await ctx.send(
                embed=self.create_embed(
                    "Error",
                    f"{config.ERROR_EMOJI} No song is currently playing to skip.",
                    discord.Color.red(),
                )
            )

    @commands.command(name="stop")
    async def stop(self, ctx):
        logging.info(f"Stop command invoked by {ctx.author} in {ctx.guild.name}")
        guild_id = ctx.guild.id
        vc = self._get_audio_client(guild_id)

        # Cancel any DJ TTS playback
        self.dj_playing_tts[guild_id] = False
        pending = getattr(self, "_dj_pending", {}).pop(guild_id, None)
        tts_path = self._current_tts_path.get(guild_id)
        if tts_path:
            cleanup_tts_file(tts_path)
            self._current_tts_path[guild_id] = None

        queue = await self.get_queue(ctx.guild.id)
        if not queue.empty():
            while not queue.empty():
                await queue.get()
            logging.info(f"Queue cleared in {ctx.guild.name}")
        if vc:
            vc.stop()
            logging.info(f"Voice client stopped in {ctx.guild.name}")

        # Cancel nowplaying update task
        if (
            ctx.guild.id in self.nowplaying_tasks
            and self.nowplaying_tasks[ctx.guild.id]
            and not self.nowplaying_tasks[ctx.guild.id].done()
        ):
            self.nowplaying_tasks[ctx.guild.id].cancel()
            del self.nowplaying_tasks[ctx.guild.id]

        await self.bot.change_presence(activity=None)
        await ctx.send(
            embed=self.create_embed(
                "Playback Stopped",
                f"{config.SUCCESS_EMOJI} Music has been stopped and the queue has been cleared.",
            )
        )

    @commands.command(name="pause")
    async def pause(self, ctx):
        logging.info(f"Pause command invoked by {ctx.author} in {ctx.guild.name}")
        vc = self._get_audio_client(ctx.guild.id)
        if vc and vc.is_playing():
            vc.pause()
            logging.info(f"Music paused in {ctx.guild.name}")
            await ctx.send(
                embed=self.create_embed(
                    "Playback Paused",
                    f"{config.PAUSE_EMOJI} The music has been paused.",
                )
            )
        else:
            logging.warning(
                f"Pause command invoked but nothing is playing or already paused in {ctx.guild.name}"
            )
            await ctx.send(
                embed=self.create_embed(
                    "Error",
                    f"{config.ERROR_EMOJI} No music is currently playing to pause.",
                    discord.Color.red(),
                )
            )

    @commands.command(name="resume")
    async def resume(self, ctx):
        logging.info(f"Resume command invoked by {ctx.author} in {ctx.guild.name}")
        vc = self._get_audio_client(ctx.guild.id)
        if vc:
            if vc.is_paused():
                vc.resume()
                logging.info(f"Music resumed in {ctx.guild.name}")
                await ctx.send(
                    embed=self.create_embed(
                        "Playback Resumed",
                        f"{config.PLAY_EMOJI} The music has been resumed.",
                    )
                )
            elif not vc.is_playing() and vc.source:
                # Fallback if state is inconsistent but source exists
                vc.resume()
                logging.info(f"Music resumed (fallback) in {ctx.guild.name}")
                await ctx.send(
                    embed=self.create_embed(
                        "Playback Resumed",
                        f"{config.PLAY_EMOJI} The music has been resumed.",
                    )
                )
            else:
                logging.warning(
                    f"Resume command invoked but nothing is paused or playing in {ctx.guild.name}"
                )
                await ctx.send(
                    embed=self.create_embed(
                        "Error",
                        f"{config.ERROR_EMOJI} No music is currently paused to resume.",
                        discord.Color.red(),
                    )
                )
        else:
            await ctx.send(
                embed=self.create_embed(
                    "Error",
                    f"{config.ERROR_EMOJI} I am not in a voice channel.",
                    discord.Color.red(),
                )
            )

    @commands.command(name="clear")
    async def clear(self, ctx):
        logging.info(f"Clear command invoked by {ctx.author} in {ctx.guild.name}")
        queue = await self.get_queue(ctx.guild.id)
        if not queue.empty():
            while not queue.empty():
                await queue.get()
            logging.info(f"Queue cleared by {ctx.author} in {ctx.guild.name}")
            await ctx.send(
                embed=self.create_embed(
                    "Queue Cleared",
                    f"{config.SUCCESS_EMOJI} The queue has been cleared.",
                )
            )
        else:
            logging.info(
                f"Clear command invoked but queue already empty in {ctx.guild.name}"
            )
            await ctx.send(
                embed=self.create_embed("Empty Queue", "The queue is already empty.")
            )

    @commands.command(name="remove")
    async def remove(self, ctx, number: int):
        logging.info(
            f"Remove command invoked by {ctx.author} in {ctx.guild.name} to remove song number {number}"
        )
        queue = await self.get_queue(ctx.guild.id)
        if number > 0 and number <= queue.qsize():
            removed_song = None
            temp_queue = asyncio.Queue()
            for i in range(queue.qsize()):
                song = await queue.get()
                if i + 1 == number:
                    removed_song = song
                else:
                    await temp_queue.put(song)

            self.song_queues[ctx.guild.id] = temp_queue

            if removed_song:
                logging.info(
                    f"Removed song '{removed_song.title}' (number {number}) from queue in {ctx.guild.name}"
                )
                await ctx.send(
                    embed=self.create_embed(
                        "Song Removed",
                        f"{config.SUCCESS_EMOJI} Removed `{removed_song.title}` from the queue.",
                    )
                )
            else:
                logging.error(
                    f"Failed to remove song at position {number} from queue in {ctx.guild.name}"
                )
                await ctx.send(
                    embed=self.create_embed(
                        "Error",
                        f"{config.ERROR_EMOJI} Could not find a song at that position.",
                        discord.Color.red(),
                    )
                )
        else:
            logging.warning(
                f"Invalid song number {number} provided by {ctx.author} for remove command in {ctx.guild.name}"
            )
            await ctx.send(
                embed=self.create_embed(
                    "Error",
                    f"{config.ERROR_EMOJI} Invalid song number.",
                    discord.Color.red(),
                )
            )

    @commands.command(name="loop")
    async def loop(self, ctx):
        logging.info(f"Loop command invoked by {ctx.author} in {ctx.guild.name}")
        guild_id = ctx.guild.id
        self.looping[guild_id] = not self.looping.get(guild_id, False)
        status = "enabled" if self.looping[guild_id] else "disabled"
        logging.info(f"Looping {status} for {ctx.guild.name}")
        await ctx.send(
            embed=self.create_embed(
                "Loop Toggled", f"{config.SUCCESS_EMOJI} Looping is now **{status}**."
            )
        )

    def _get_current_speed_index(self, guild_id):
        current_speed = self.playback_speed.get(guild_id, 1.0)
        try:
            return self.youtube_speeds.index(current_speed)
        except ValueError:
            return self.youtube_speeds.index(
                1.0
            )  # Default to 1.0 if current speed not in list

    async def _set_speed(self, ctx, new_speed):
        guild_id = ctx.guild.id
        if not ctx.voice_client or not ctx.voice_client.is_playing():
            await ctx.send(
                embed=self.create_embed(
                    "Error",
                    f"{config.ERROR_EMOJI} No song is currently playing to change speed.",
                    discord.Color.red(),
                )
            )
            return

        self.playback_speed[guild_id] = new_speed
        logging.info(f"Setting playback speed to {new_speed} for {ctx.guild.name}")

        # Re-create the player with the new speed
        current_song_data = self.current_song.get(guild_id)
        if current_song_data:
            # Stop current playback
            ctx.voice_client.stop()
            await asyncio.sleep(0.3)

            # Build FFmpeg options with atempo filter chain
            # FFmpeg atempo only supports 0.5-2.0 per instance.
            # For speeds below 0.5, chain multiple filters.
            player_options = FFMPEG_OPTIONS.copy()
            if new_speed != 1.0:
                atempo_filters = self._build_atempo_chain(new_speed)
                player_options["options"] += f' -filter:a "{",".join(atempo_filters)}"'
    
            # Create and play the new player with the updated speed
            source = discord.FFmpegPCMAudio(current_song_data.get("url"), **player_options)
            player = discord.PCMVolumeTransformer(source)
            player.volume = self.current_volume.get(guild_id, 1.0)
            self._dispatch_audio_play(
                guild_id,
                player,
                after=lambda e: self.bot.loop.create_task(self._after_playback(ctx, e)),
            )

            self.song_start_time[guild_id] = (
                time.time()
            )  # Reset start time for accurate progress bar
            await ctx.send(
                embed=self.create_embed(
                    "Speed Changed",
                    f"{config.SUCCESS_EMOJI} Playback speed set to **{new_speed}x**.",
                )
            )
            await self.nowplaying(
                ctx, silent=True
            )  # Update nowplaying message immediately
        else:
            await ctx.send(
                embed=self.create_embed(
                    "Error",
                    f"{config.ERROR_EMOJI} Could not apply speed change. No current song data.",
                    discord.Color.red(),
                )
            )

    @staticmethod
    def _build_atempo_chain(speed):
        """Build an FFmpeg atempo filter chain for any speed value.

        FFmpeg's atempo filter only supports 0.5-2.0 per instance.
        For speeds outside that range, chain multiple atempo filters.
        E.g. 0.25x = atempo=0.5,atempo=0.5
        """
        filters = []
        remaining = speed
        while remaining < 0.5:
            filters.append("atempo=0.5")
            remaining /= 0.5
        while remaining > 2.0:
            filters.append("atempo=2.0")
            remaining /= 2.0
        filters.append(f"atempo={remaining}")
        return filters

    @commands.command(name="speedhigher")
    async def speedhigher(self, ctx):
        logging.info(f"Speedhigher command invoked by {ctx.author} in {ctx.guild.name}")
        guild_id = ctx.guild.id
        current_index = self._get_current_speed_index(guild_id)
        if current_index < len(self.youtube_speeds) - 1:
            new_speed = self.youtube_speeds[current_index + 1]
            await self._set_speed(ctx, new_speed)
        else:
            await ctx.send(
                embed=self.create_embed(
                    "Speed Limit",
                    f"{config.ERROR_EMOJI} Already at maximum speed ({self.youtube_speeds[-1]}x).",
                    discord.Color.orange(),
                )
            )

    @commands.command(name="speedlower")
    async def speedlower(self, ctx):
        logging.info(f"Speedlower command invoked by {ctx.author} in {ctx.guild.name}")
        guild_id = ctx.guild.id
        current_index = self._get_current_speed_index(guild_id)
        if current_index > 0:
            new_speed = self.youtube_speeds[current_index - 1]
            await self._set_speed(ctx, new_speed)
        else:
            await ctx.send(
                embed=self.create_embed(
                    "Speed Limit",
                    f"{config.ERROR_EMOJI} Already at minimum speed ({self.youtube_speeds[0]}x).",
                    discord.Color.orange(),
                )
            )

    @commands.command(name="shuffle")
    async def shuffle(self, ctx):
        logging.info(f"Shuffle command invoked by {ctx.author} in {ctx.guild.name}")
        queue = await self.get_queue(ctx.guild.id)
        if queue.empty():
            await ctx.send(
                embed=self.create_embed(
                    "Empty Queue",
                    f"{config.ERROR_EMOJI} The queue is empty, nothing to shuffle.",
                    discord.Color.orange(),
                )
            )
            return

        # Get all items from the queue
        queue_list = []
        while not queue.empty():
            queue_list.append(await queue.get())

        # Shuffle the list
        random.shuffle(queue_list)

        # Put items back into the queue
        for item in queue_list:
            await queue.put(item)

        logging.info(f"Queue shuffled for {ctx.guild.name}")
        await ctx.send(
            embed=self.create_embed(
                "Queue Shuffled", f"{config.SUCCESS_EMOJI} The queue has been shuffled."
            )
        )

    # ── DJ Mode Commands ──────────────────────────────────────────

    @commands.command(name="dj")
    async def dj_toggle(self, ctx):
        """Toggle the radio DJ mode on or off."""
        logging.info(f"DJ toggle command invoked by {ctx.author} in {ctx.guild.name}")

        if not TTS_AVAILABLE:
            engine_hint = (
                "MOSS-TTS-Nano server running (TTS_MODE=moss)"
                if TTS_MODE == "moss"
                else "VibeVoice server running (TTS_MODE=vibevoice)"
                if TTS_MODE == "vibevoice"
                else "`edge-tts` package installed (pip install edge-tts)"
            )
            return await ctx.send(
                embed=self.create_embed(
                    "DJ Unavailable",
                    f"{config.ERROR_EMOJI} DJ mode requires a TTS engine. "
                    f"Make sure you have {engine_hint} and restart the bot.",
                    discord.Color.red(),
                )
            )

        guild_id = ctx.guild.id
        self.dj_enabled[guild_id] = not self.dj_enabled.get(guild_id, False)
        self._save_voice_settings()
        status = "ON" if self.dj_enabled[guild_id] else "OFF"
        voice_name = self.dj_voice.get(guild_id, config.DJ_VOICE)
        logging.info(f"DJ mode {status} for {ctx.guild.name} (voice: {voice_name})")

        embed = self.create_embed(
            f"{config.DJ_EMOJI} DJ Mode",
            f"{config.SUCCESS_EMOJI} DJ mode is now **{status}**.",
        )
        if self.dj_enabled[guild_id]:
            embed.add_field(name="Voice", value=f"`{voice_name}`", inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="aidj")
    async def ai_dj_toggle(self, ctx):
        """Toggle the AI side host — the studio joker who writes their own lines.

        The AI side host is a second radio personality powered by a local LLM
        (Ollama). It uses its own TTS voice so it sounds like a different person.
        It randomly chimes in with jokes, hot takes, and banter alongside the
        main DJ. Requires: Ollama running locally with a pulled model, and DJ mode on.
        """
        logging.info(f"AI DJ toggle invoked by {ctx.author} in {ctx.guild.name}")
        guild_id = ctx.guild.id

        if not OLLAMA_DJ_AVAILABLE:
            # Check what's wrong
            status = await check_ollama_available()
            error = status.get("error", "Unknown error")
            return await ctx.send(
                embed=self.create_embed(
                    "🃏 AI Side Host Unavailable",
                    f"{config.ERROR_EMOJI} The AI side host needs Ollama.\n"
                    f"**Issue:** {error}\n\n"
                    f"**Setup:**\n"
                    f"1. Install Ollama: https://ollama.com\n"
                    f"2. Pull a model: `ollama pull {getattr(config, 'OLLAMA_MODEL', 'gemma4:latest')}`\n"
                    f"3. Set `OLLAMA_DJ_ENABLED=true` in your `.env` file",
                    discord.Color.red(),
                )
            )

        if not TTS_AVAILABLE:
            return await ctx.send(
                embed=self.create_embed(
                    "🃏 AI Side Host Unavailable",
                    f"{config.ERROR_EMOJI} The AI side host needs a TTS engine "
                    f"for its own voice. Make sure a TTS engine is available "
                    f"(MOSS, VibeVoice, or edge-tts) and restart the bot.",
                    discord.Color.red(),
                )
            )

        self.ai_dj_enabled[guild_id] = not self.ai_dj_enabled.get(guild_id, False)
        self._save_voice_settings()
        status = "ON" if self.ai_dj_enabled[guild_id] else "OFF"

        ai_voice = self.ai_dj_voice.get(guild_id, config.OLLAMA_DJ_VOICE)
        chance = getattr(config, "OLLAMA_DJ_CHANCE", 0.25)
        model = getattr(config, "OLLAMA_MODEL", "gemma4:latest")

        logging.info(f"AI Side Host {status} for {ctx.guild.name} (voice: {ai_voice})")

        embed = self.create_embed(
            "🃏 AI Side Host",
            f"{config.SUCCESS_EMOJI} The studio joker is now **{status}**.",
        )
        if self.ai_dj_enabled[guild_id]:
            embed.add_field(name="Voice", value=f"`{ai_voice}`", inline=True)
            embed.add_field(name="Model", value=f"`{model}`", inline=True)
            embed.add_field(
                name="Chime-in chance",
                value=f"{int(chance * 100)}%",
                inline=True,
            )
            embed.add_field(
                name="Note",
                value="The AI side host will randomly chime in after the main "
                "DJ speaks, with its own voice and original lines.",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.command(name="aidjvoice")
    async def ai_dj_voice_cmd(self, ctx, *, voice_name: str = None):
        """Set the AI side host's TTS voice. Use ?djvoices to see available voices."""
        logging.info(
            f"AI DJ voice command invoked by {ctx.author} in {ctx.guild.name} "
            f"with voice: {voice_name}"
        )
        guild_id = ctx.guild.id

        if not OLLAMA_DJ_AVAILABLE:
            return await ctx.send(
                embed=self.create_embed(
                    "🃏 AI Side Host",
                    f"{config.ERROR_EMOJI} AI side host is not enabled. "
                    f"Set `OLLAMA_DJ_ENABLED=true` in `.env`.",
                    discord.Color.red(),
                )
            )

        if voice_name is None:
            current = self.ai_dj_voice.get(guild_id, config.OLLAMA_DJ_VOICE)
            return await ctx.send(
                embed=self.create_embed(
                    "🃏 AI Side Host Voice",
                    f"Current voice: `{current}`\n"
                    f"Use `?aidjvoice <name>` to change it.\n"
                    f"Use `?djvoices` to see available voices.",
                    discord.Color.blurple(),
                )
            )

        self.ai_dj_voice[guild_id] = voice_name
        self._save_voice_settings()
        await ctx.send(
            embed=self.create_embed(
                "🃏 AI Side Host Voice",
                f"{config.SUCCESS_EMOJI} AI side host voice set to `{voice_name}`.",
            )
        )

    @commands.command(name="djvoice")
    async def dj_voice_cmd(self, ctx, *, voice_name: str = None):
        """Set the DJ's TTS voice. Use ?djvoices to see available voices."""
        logging.info(
            f"DJ voice command invoked by {ctx.author} in {ctx.guild.name} with voice: {voice_name}"
        )
        guild_id = ctx.guild.id

        if not TTS_AVAILABLE:
            return await ctx.send(
                embed=self.create_embed(
                    "DJ Unavailable",
                    f"{config.ERROR_EMOJI} DJ mode requires a TTS engine. "
                    "Make sure MOSS, VibeVoice, or edge-tts is available.",
                    discord.Color.red(),
                )
            )

        if voice_name is None:
            current = self.dj_voice.get(guild_id, config.DJ_VOICE)
            return await ctx.send(
                embed=self.create_embed(
                    f"{config.DJ_EMOJI} DJ Voice",
                    f"Current voice: **`{current}`**\n\n"
                    f"Use `?djvoice <voice_name>` to change it.\n"
                    f"Use `?djvoices` to see available voices.",
                )
            )

        # Validate the voice exists
        try:
            voices = await list_voices()
            voice_names = [v["ShortName"] for v in voices]
            if voice_name not in voice_names:
                close = [v for v in voice_names if voice_name.lower() in v.lower()]
                suggestion = ""
                if close:
                    suggestion = f"\nDid you mean one of these?\n" + "\n".join(
                        f"• `{v}`" for v in close[:10]
                    )
                return await ctx.send(
                    embed=self.create_embed(
                        "Voice Not Found",
                        f"{config.ERROR_EMOJI} Voice `{voice_name}` not found."
                        f"{suggestion}\n\n"
                        f"Use `?djvoices` to see all available voices.",
                        discord.Color.red(),
                    )
                )
        except Exception as e:
            logging.warning(
                f"DJ: Could not validate voice '{voice_name}': {e}. Setting anyway."
            )

        self.dj_voice[guild_id] = voice_name
        self._save_voice_settings()
        logging.info(f"DJ voice set to '{voice_name}' for {ctx.guild.name}")
        await ctx.send(
            embed=self.create_embed(
                f"{config.DJ_EMOJI} DJ Voice Changed",
                f"{config.SUCCESS_EMOJI} DJ voice set to **`{voice_name}`**",
            )
        )

    @commands.command(name="djvoices")
    async def dj_voices_cmd(self, ctx, language: str = "en"):
        """List available TTS voices (filtered by language, default: en)."""
        logging.info(
            f"DJ voices command invoked by {ctx.author} in {ctx.guild.name} (lang: {language})"
        )

        if not TTS_AVAILABLE:
            return await ctx.send(
                embed=self.create_embed(
                    "DJ Unavailable",
                    f"{config.ERROR_EMOJI} DJ mode requires a TTS engine. "
                    "Make sure MOSS, VibeVoice, or edge-tts is available.",
                    discord.Color.red(),
                )
            )

        try:
            voices = await list_voices(language)
        except Exception as e:
            logging.error(f"DJ: Failed to list voices: {e}")
            return await ctx.send(
                embed=self.create_embed(
                    "Error",
                    f"{config.ERROR_EMOJI} Failed to fetch voices: {e}",
                    discord.Color.red(),
                )
            )

        if not voices:
            return await ctx.send(
                embed=self.create_embed(
                    "No Voices Found",
                    f"{config.ERROR_EMOJI} No voices found for language `{language}`. "
                    "Try a different prefix (e.g., `?djvoices en`, `?djvoices ja`).",
                    discord.Color.orange(),
                )
            )

        voice_list = []
        for v in voices[:25]:
            gender = v.get("Gender", "?")
            locale = v.get("Locale", "?")
            voice_list.append(f"`{v['ShortName']}` — {gender}, {locale}")

        description = "\n".join(voice_list)
        if len(voices) > 25:
            description += f"\n\n... and {len(voices) - 25} more."

        await ctx.send(
            embed=self.create_embed(
                f"{config.DJ_EMOJI} Available Voices ({language}*)",
                description,
            )
        )

    # ── DJ Playback Helpers ────────────────────────────────────────

    async def _dj_speak(
        self,
        voice_client_unused,  # Kept for signature compatibility
        text: str,
        guild_id: int,
        voice: str = None,
        is_ai: bool = False,
    ):
        """
        Generate TTS audio and play it through the voice client.
        Also plays any {sound:name} tags found in the text after TTS finishes.
        Returns True if TTS was played, False if skipped.

        Args:
            voice_client: The discord.VoiceClient to play through
            text: The text to speak (may contain {sound:name} tags)
            guild_id: The guild ID for state tracking
            voice: Override TTS voice name (defaults to guild DJ voice)
            is_ai: True if this is the AI side host speaking (don't overwrite dj_line context)
        """
        if not TTS_AVAILABLE:
            return False

        voice_client = self._get_audio_client(guild_id)
        if not voice_client:
            return False

        # Extract {sound:name} tags before TTS so they aren't spoken aloud
        from utils.dj import extract_sound_tags

        clean_text, sound_ids = extract_sound_tags(text)
        if sound_ids:
            logging.info(f"DJ: Extracted sound tags: {sound_ids} for guild {guild_id}")

        # ── Pre-generation cache check ──
        # If a TTS file for this transition was pre-generated while the
        # previous song was playing, use it instantly (zero latency!).
        # Files live permanently in assets/part2/ and are NOT deleted.
        pregen_entry = None
        if not is_ai:
            try:
                pregen = self._get_pregenerator()
                prev_title = ""
                current = self.current_song.get(guild_id)
                if current:
                    prev_title = getattr(current, "title", "")
                # The "upcoming" title is whatever _dj_speak is speaking about
                # which is typically contained in the text passed in
                pregen_entry = pregen.lookup(
                    guild_id,
                    text[:100],  # Use first 100 chars of text as a key hint
                    prev_title=prev_title,
                )
            except Exception:
                pregen_entry = None

        if (
            pregen_entry
            and pregen_entry.dj_tts_path
            and os.path.isfile(pregen_entry.dj_tts_path)
        ):
            # Use the pre-generated TTS file — skip TTS generation entirely
            if pregen_entry.dj_text:
                clean_text = pregen_entry.dj_text
            if pregen_entry.dj_sound_ids:
                sound_ids = pregen_entry.dj_sound_ids
            tts_path = pregen_entry.dj_tts_path
            voice = self.dj_voice.get(guild_id, config.DJ_VOICE)
            logging.info(
                f"DJ: Using pre-generated TTS for guild {guild_id}: "
                f"{clean_text[:60]}..."
            )
        else:
            pregen_entry = None  # No cache hit — generate on the fly

        # Store what the DJ is about to say so the AI side host can react to it.
        # Only store the main DJ's lines — not the AI's own lines.
        if not is_ai:
            self._last_dj_line[guild_id] = clean_text if clean_text else text

        if not pregen_entry:
            voice = voice or self.dj_voice.get(guild_id, config.DJ_VOICE)

            # ── TTS engine routing ──
            # Main DJ uses the configured TTS engine (MOSS → edge-tts fallback).
            # AI Side Host always uses edge-tts (cloud-based, different voice from the DJ)
            # to create a clear distinction between the two hosts.
            if is_ai:
                tts_engine = "edge-tts"
                # The AI side host needs a distinct MALE voice in edge-tts.
                # Config voice (en_news_male) is a MOSS name — use edge-tts equivalent instead.
                AI_SIDE_HOST_EDGE_VOICE = (
                    "en-US-GuyNeural"  # Deep male voice, distinct from DJ
                )
                voice = AI_SIDE_HOST_EDGE_VOICE
                logging.info(
                    f"AI Side Host: Speaking in guild {guild_id} with voice '{voice}' "
                    f"(source=AI Side Host, engine=edge-tts)"
                )
            else:
                tts_engine = (
                    TTS_MODE  # Use configured engine (MOSS → edge-tts fallback)
                )
                logging.info(
                    f"DJ: Speaking in guild {guild_id} with voice '{voice}' "
                    f"(source=DJ, engine={tts_engine}, "
                    f"guild_dj_voice={self.dj_voice.get(guild_id, '<unset>')}, "
                    f"config_default={config.DJ_VOICE})"
                )

            logging.info(
                f"DJ: Speaking in guild {guild_id} with voice '{voice}' "
                f"(source={'AI Side Host' if is_ai else 'DJ'}, "
                f"engine={tts_engine}, "
                f"guild_dj_voice={self.dj_voice.get(guild_id, '<unset>')}, "
                f"config_default={config.DJ_VOICE})"
            )
            tts_path = await generate_tts(
                clean_text if clean_text else text,
                voice,
                source="AI Side Host" if is_ai else "DJ",
                engine=tts_engine,
            )

        if not tts_path:
            logging.warning(f"DJ: TTS generation returned None for guild {guild_id}")
            return False

        self._current_tts_path[guild_id] = tts_path
        self.dj_playing_tts[guild_id] = True
        # Store pending sounds to play after TTS finishes
        self._dj_pending_sounds[guild_id] = sound_ids

        try:
            # TTS audio — no reconnect options needed (local file), no video.
            # Duration cap of 30s prevents FFmpeg from hanging on malformed headers.
            tts_source = discord.FFmpegPCMAudio(
                tts_path,
                before_options="-nostdin",
                options="-vn -t 30",
            )
            tts_player = discord.PCMVolumeTransformer(tts_source)
            tts_player.volume = self.current_volume.get(guild_id, 1.0)

            # We cannot await play() — it starts playback and returns immediately.
            # The 'after' callback fires when TTS playback finishes.
            loop = self.bot.loop
            self._dispatch_audio_play(
                guild_id,
                tts_player,
                after=lambda e: loop.call_soon_threadsafe(
                    self._on_tts_done, guild_id, e
                ),
            )

            # ── YouTube Live: Stream TTS audio ──
            if self._yt_stream_active and self._yt_streamer:
                display = clean_text if clean_text else text
                label = "AI Side Host" if is_ai else "DJ"
                asyncio.ensure_future(
                    self._yt_streamer.play_tts(tts_path, f"{label}: {display}"),
                    loop=self.bot.loop,
                )

            display = clean_text if clean_text else text
            logging.info(f"DJ: Speaking in guild {guild_id}: {display[:80]}…")
            if sound_ids:
                logging.info(f"DJ: Will play sounds after speech: {sound_ids}")
            return True
        except Exception as e:
            logging.error(f"DJ: Failed to play TTS in guild {guild_id}: {e}")
            self.dj_playing_tts[guild_id] = False
            cleanup_tts_file(tts_path)
            self._current_tts_path[guild_id] = None
            return False

    def _on_tts_done(self, guild_id, error):
        """
        Called from FFmpeg's after-callback thread when TTS playback finishes.
        Cleans up the temp file, plays any pending sound effects, then
        schedules playing the actual song.
        """
        self.dj_playing_tts[guild_id] = False

        tts_path = self._current_tts_path.get(guild_id)
        if tts_path:
            # Delay cleanup so YouTube Live FFmpeg stream has time to read it
            self.bot.loop.call_later(15, cleanup_tts_file, tts_path)
            self._current_tts_path[guild_id] = None

        if error:
            logging.error(f"DJ: TTS playback error for guild {guild_id}: {error}")

        # Check if this TTS was from the AI side host.
        # If so, skip the AI check when playing the song to prevent
        # the AI from speaking again and blocking the song indefinitely.
        ai_spoke = guild_id in self._ai_dj_pending_line

        # Play any pending sound effects from {sound:name} tags
        pending_sounds = self._dj_pending_sounds.pop(guild_id, [])

        if pending_sounds:
            # Start bed music for the gap between TTS and song start.
            # We couldn't start it earlier because TTS was using the voice client.
            # Now TTS is done, so bed music can play under the sound effects.
            # It'll be stopped by _stop_bed_music() in _start_song_playback.
            guild = self.bot.get_guild(guild_id)
            if guild and guild.voice_client:
                asyncio.ensure_future(
                    self._start_bed_music(guild.voice_client, guild_id),
                    loop=self.bot.loop,
                )

            logging.info(
                f"DJ: Playing {len(pending_sounds)} sound effects for guild {guild_id}"
            )
            asyncio.ensure_future(
                self._play_dj_sounds_then_song(guild_id, pending_sounds),
                loop=self.bot.loop,
            )
        else:
            logging.info(
                f"DJ: TTS done for guild {guild_id}, scheduling song playback (ai_spoke={ai_spoke})"
            )
            asyncio.ensure_future(
                self._play_song_after_dj(guild_id, skip_ai=ai_spoke),
                loop=self.bot.loop,
            )

    async def _play_dj_sounds_then_song(self, guild_id, sound_ids):
        """
        Play a sequence of sound effects, then play the pending song.
        Each sound is capped at MAX_SOUND_SECONDS to prevent long sounds
        from blocking the next song.
        """
        from utils.soundboard import get_sound_path
        import os as _os
        import discord

        vc = self._get_audio_client(guild_id)
        if not vc:
            # Voice gone — skip sounds, go straight to song
            await self._play_song_after_dj(guild_id)
            return

        for sound_id in sound_ids:
            path = get_sound_path(sound_id)
            if not path:
                logging.warning(f"DJ: Sound '{sound_id}' not found, skipping")
                continue

            try:
                # Cap sound duration via FFmpeg -t flag.
                # DJ lines can have longer sounds (up to 10s) since they're
                # supposed to be brief stingers that play before the song starts.
                # The soundboard web UI uses the shorter MAX_SOUND_SECONDS (8s).
                max_sec = min(getattr(config, "MAX_SOUND_SECONDS", 8) + 2, 10)
                ffmpeg_options = f"-vn -t {max_sec}"
                source = discord.FFmpegPCMAudio(
                    path,
                    before_options="-nostdin",
                    options=ffmpeg_options,
                )
                player = discord.PCMVolumeTransformer(source)
                player.volume = self.current_volume.get(guild_id, 1.0)

                # Play and wait for this sound to finish
                finished = asyncio.Event()

                def _after(e):
                    if e:
                        logging.error(f"DJ: Sound '{sound_id}' error: {e}")
                    self._sfx_active[guild_id] = False
                    self.bot.loop.call_soon_threadsafe(finished.set)

                self._sfx_active[guild_id] = True
                self._dispatch_audio_play(guild_id, player, after=_after)
                logging.info(
                    f"DJ: Playing sound effect '{sound_id}' in guild {guild_id}"
                )
                # ── YouTube Live: Stream sound effect ──
                if (
                    self._yt_stream_active
                    and self._yt_streamer
                    and _os.path.isfile(path)
                ):
                    display_name = (
                        sound_id.replace("_", " ").replace("-", " ").strip().title()
                    )
                    asyncio.ensure_future(
                        self._yt_streamer.play_sfx(path, f"SFX: {display_name}"),
                        loop=self.bot.loop,
                    )

                # Wait up to 12s for the sound to finish (sounds capped at 10s + margin)
                try:
                    await asyncio.wait_for(finished.wait(), timeout=12)
                except asyncio.TimeoutError:
                    logging.warning(
                        f"DJ: Sound '{sound_id}' timed out (may have been stopped)"
                    )

            except Exception as e:
                logging.error(f"DJ: Failed to play sound '{sound_id}': {e}")
                continue

        # All sounds done — play the song (AI side host handled in _play_song_after_dj)
        await self._play_song_after_dj(guild_id)

    async def _play_song_after_dj(self, guild_id, skip_ai=False):
        """
        Called after DJ TTS intro finishes. Plays the queued song that
        was already dequeued but held until the intro was spoken.
        'pending_song' is stored on the guild before TTS starts.

        Args:
            guild_id: The guild ID
            skip_ai: If True, skip the AI side host check (used when
                called after the AI side host's own TTS finishes, to
                prevent the AI from speaking again and blocking the song).
        """
        vc = self._get_audio_client(guild_id)
        if not vc:
            logging.warning(f"DJ: Bot not in voice for guild {guild_id} after TTS")
            return

        pending = getattr(self, "_dj_pending", {}).get(guild_id)
        if not pending:
            logging.warning(f"DJ: No pending song for guild {guild_id}")
            return

        ctx, data, channel_id = pending

        # ── AI Side Host: chime in after the main DJ, before the song ──
        # Only if skip_ai is False (we haven't just played an AI line)
        ai_enabled = self.ai_dj_enabled.get(guild_id, False)
        logging.info(
            f"AI Side Host check: enabled={ai_enabled}, ollama_available={OLLAMA_DJ_AVAILABLE}, "
            f"tts_available={TTS_AVAILABLE}, skip_ai={skip_ai}"
        )
        if not skip_ai and ai_enabled and OLLAMA_DJ_AVAILABLE and TTS_AVAILABLE:
            # Pass what the main DJ just said so the AI can react to it
            dj_line = self._last_dj_line.get(guild_id, "")
            logging.info(
                f"AI Side Host: awaiting line generation for guild {guild_id}..."
            )
            # AI side host is always generated LIVE (not pre-generated).
            # Only the main DJ (MOSS-TTS) lines are pre-generated.
            try:
                ai_line = await self._try_ai_side_host(guild_id, dj_line=dj_line)
            except Exception as e:
                logging.error(f"AI Side Host: error generating line: {e}")
                ai_line = None
            if ai_line:
                logging.info(
                    f"AI Side Host: got line for guild {guild_id}, speaking..."
                )
                voice = self.ai_dj_voice.get(guild_id, config.OLLAMA_DJ_VOICE)
                spoke = await self._dj_speak(
                    vc, ai_line, guild_id, voice=voice, is_ai=True
                )
                if spoke:
                    self._dj_pending[guild_id] = (ctx, data, channel_id)
                    self._ai_dj_pending_line[guild_id] = ai_line
                    logging.info(
                        f"AI Side Host: Spoke in guild {guild_id}, song will play after TTS"
                    )
                    return
                else:
                    logging.warning(
                        f"AI Side Host: TTS failed for guild {guild_id}, playing song directly"
                    )
            else:
                logging.info(f"AI Side Host: no line generated for guild {guild_id}")

        # Remove pending data now that we're done with it
        self._dj_pending.pop(guild_id, None)
        self._ai_dj_pending_line.pop(guild_id, None)

        # Now play the actual song
        logging.info(f"Playing song for guild {guild_id} (skip_ai={skip_ai})")
        await self._start_song_playback(ctx, data, channel_id)

    async def _try_ai_side_host(self, guild_id, dj_line: str = "") -> str | None:
        """Try to generate an AI side host line for this moment.

        Returns an AI-generated line, or None if the side host
        shouldn't speak right now (random chance, Ollama unavailable, etc.)

        Args:
            guild_id: The guild ID
            dj_line: What the main DJ just said (for reactive context)
        """
        if not should_side_host_speak():
            logging.info(f"AI Side Host: skipped (random chance or disabled)")
            return None

        logging.info(
            f"AI Side Host: generating line for guild {guild_id} (dj_line: {dj_line[:80]}...)"
        )

        # Gather context
        current = self.current_song.get(guild_id)
        queue = self.song_queues.get(guild_id)
        queue_size = queue.qsize() if queue else 0

        guild = self.bot.get_guild(guild_id)
        listener_count = 0
        if guild and guild.voice_client and guild.voice_client.channel:
            listener_count = sum(
                1 for m in guild.voice_client.channel.members if not m.bot
            )

        title = current.title if current else ""
        prev_title = ""
        next_title = ""

        # Check the queue for next song info
        if queue and not queue.empty():
            try:
                next_title = queue._queue[0].title
            except (IndexError, AttributeError):
                pass

        # Check if there was a previous song
        history = self.recently_played.get(guild_id, [])
        if history:
            prev_title = history[-1].get("title", "")

        line = await generate_side_host_line(
            title=title,
            prev_title=prev_title,
            next_title=next_title,
            queue_size=queue_size,
            listener_count=listener_count,
            station_name=self.bot.user.name if self.bot.user else config.STATION_NAME,
            dj_line=dj_line,
        )

        return line

    async def _speak_ai_side_host_then_song(self, guild_id, ai_line):
        """Speak an AI side host line, then play the pending song.

        This is called from _on_tts_done when there's a pending AI line
        but no pending sound effects. It speaks the AI line with the side
        host's voice, then plays the song.
        """
        vc = self._get_audio_client(guild_id)
        if not vc:
            logging.warning(f"AI Side Host: Bot not in voice for guild {guild_id}")
            await self._play_song_after_dj(guild_id)
            return

        voice = self.ai_dj_voice.get(guild_id, config.OLLAMA_DJ_VOICE)
        spoke = await self._dj_speak(
            vc, ai_line, guild_id, voice=voice, is_ai=True
        )

        if spoke:
            logging.info(
                f"AI Side Host: Speaking in guild {guild_id}: {ai_line[:60]}..."
            )
            # The TTS after-callback will handle playing the song
            # via _on_tts_done -> _play_song_after_dj
        else:
            # TTS failed — play the song directly
            await self._play_song_after_dj(guild_id)

    # ── DJ/TTS Pre-generation ──────────────────────────────────────

    def _get_pregenerator(self):
        """Get or create the DJ pregenerator (lazy init)."""
        if self._pregenerator is None:
            from utils.pregen import get_pregenerator

            self._pregenerator = get_pregenerator(self.bot)
        return self._pregenerator

    def _trigger_pregen(self, guild_id: int, current_title: str):
        """Fire off pre-generation of DJ lines for upcoming songs.

        Called when a new song starts playing. If the DJ is enabled,
        this kicks off a background task that pre-generates TTS audio
        for the next few songs in the queue.
        """
        if not self.dj_enabled.get(guild_id, False):
            return

        queue = self.song_queues.get(guild_id)
        if not queue or queue.empty():
            return

        try:
            pregen = self._get_pregenerator()
            asyncio.ensure_future(
                pregen.pregenerate_upcoming(guild_id, queue, current_title),
                loop=self.bot.loop,
            )
        except Exception as e:
            logging.debug(f"Pregen: Failed to trigger for guild {guild_id}: {e}")

    async def _start_song_playback(self, ctx, data, channel_id=None):
        """
        Core song playback — creates the FFmpeg player and starts it.
        Extracted from play_next so DJ intro can call it after TTS finishes.

        If NOWPLAYING_CHANNEL_ID is set in config, the now-playing embed
        will always be sent to that channel. Otherwise it goes to the channel
        where the play command was invoked (channel_id).
        """
        guild_id = ctx.guild.id

        # Resolve the now-playing channel: use configured channel if set,
        # otherwise fall back to the channel the command was invoked in.
        np_channel = self._get_np_channel(ctx.guild)
        if np_channel:
            channel_id = np_channel.id
            logging.info(
                f"Now-playing bound to configured channel #{np_channel.name} ({np_channel.id}) for guild {guild_id}"
            )
        elif channel_id is None:
            channel_id = ctx.channel.id

        vc = self._get_audio_client(guild_id)
        if not vc or not vc.is_connected():
            logging.warning(f"DJ: Voice client gone for guild {guild_id}")
            return

        # Stop any bed music before the song starts
        await self._stop_bed_music(guild_id)

        try:
            logging.info(
                f"Playing {data.title} in {ctx.guild.name} (url={data.url[:80]}…)"
                if data.url
                else f"Playing {data.title} — NO URL!"
            )

            current_speed = self.playback_speed.get(guild_id, 1.0)
            player_options = FFMPEG_OPTIONS.copy()

            # Build FFmpeg audio filter chain
            audio_filters = []

            # Speed filter (chain atempo for sub-0.5 and super-2.0 speeds)
            if current_speed != 1.0:
                audio_filters.extend(self._build_atempo_chain(current_speed))

            # Crossfade: fade in the first few seconds of a new song
            crossfade = getattr(config, "CROSSFADE_DURATION", 0)
            if crossfade > 0 and data.duration and data.duration > crossfade * 2:
                audio_filters.append(f"afade=t=in:st=0:d={crossfade}")

            if audio_filters:
                player_options["options"] += f' -filter:a "{",".join(audio_filters)}"'

            source = discord.FFmpegPCMAudio(data.url, **player_options)
            player = discord.PCMVolumeTransformer(source)
            player.volume = self.current_volume.get(guild_id, 1.0)

            logging.info(
                f"Playback initiated for {data.title} with speed {current_speed}x. "
                f"Applied volume: {player.volume}"
            )
            self._dispatch_audio_play(
                guild_id,
                player,
                after=lambda e: self.bot.loop.create_task(self._after_playback(ctx, e)),
            )
            self.current_song[guild_id] = data
            self.song_start_time[guild_id] = time.time()

            # ── Pre-generate DJ lines for upcoming songs ──
            # While this song plays (especially long songs), generate
            # TTS audio for the next few songs so transitions are instant.
            self._trigger_pregen(guild_id, data.title)

            # ── YouTube Live: Stream the song (with thumbnail) ──
            if self._yt_stream_active and self._yt_streamer and data.url:
                thumb = getattr(data, "thumbnail", None)

                async def _play_yt_song():
                    # Give the preceding FFmpeg process (DJ TTS / Waiting list)
                    # 2 seconds to natively flush its RTMP packets to YouTube
                    # perfectly over the internet before tearing down the pipe.
                    await asyncio.sleep(2.0)
                    await self._yt_streamer.play_song(
                        data.url, data.title or "Unknown", thumbnail=thumb
                    )

                asyncio.ensure_future(_play_yt_song(), loop=self.bot.loop)

            # Record to recently-played history
            self._record_history(guild_id, data)

            await self.bot.change_presence(
                activity=discord.Activity(
                    type=discord.ActivityType.listening, name=data.title
                )
            )

            # Cancel/Restart nowplaying update task
            if (
                guild_id in self.nowplaying_tasks
                and self.nowplaying_tasks[guild_id]
                and not self.nowplaying_tasks[guild_id].done()
            ):
                self.nowplaying_tasks[guild_id].cancel()
            self.nowplaying_tasks[guild_id] = self.bot.loop.create_task(
                self._update_nowplaying_message(guild_id, channel_id)
            )
        except Exception as e:
            logging.error(f"Error playing {data.title} in {ctx.guild.name}: {e}")
            channel = self.bot.get_channel(channel_id)
            if channel:
                await channel.send(
                    embed=self.create_embed(
                        "Error",
                        f"{config.ERROR_EMOJI} Could not play the next song: {e}",
                        discord.Color.red(),
                    )
                )
            await asyncio.sleep(2)
            await self.play_next(ctx)

    # ── Recently Played & Auto-DJ ──────────────────────────────────

    def _record_history(self, guild_id, track):
        """Record a track to the recently-played history."""
        import datetime

        entry = {
            "title": getattr(track, "title", "Unknown"),
            "url": getattr(track, "webpage_url", None),
            "thumbnail": getattr(track, "thumbnail", None),
            "duration": getattr(track, "duration", None),
            "played_at": datetime.datetime.now().strftime("%H:%M"),
        }
        if guild_id not in self.recently_played:
            self.recently_played[guild_id] = []
        self.recently_played[guild_id].insert(0, entry)
        # Trim to max
        if len(self.recently_played[guild_id]) > self._max_history:
            self.recently_played[guild_id] = self.recently_played[guild_id][
                : self._max_history
            ]

    async def _autodj_fill(self, ctx):
        """Auto-DJ: when the queue empties, refill it from the configured source.

        Source can be a YouTube playlist URL, a preset name, or the recently-played history.
        """
        guild_id = ctx.guild.id
        source = self.autodj_source.get(guild_id, "")
        if not source:
            source = getattr(config, "AUTODJ_DEFAULT_SOURCE", "")

        if not source:
            # No source configured — try to replay from history
            history = self.recently_played.get(guild_id, [])
            if len(history) < 2:
                logging.info(
                    f"Auto-DJ: No source and not enough history for guild {guild_id}"
                )
                return False
            # Pick a random track from history (skip the one that just finished)
            import random

            pick = random.choice(history[1:])
            url = pick.get("url")
            if not url:
                return False
            source = url

        queue = await self.get_queue(guild_id)
        from cogs.youtube import PlaceholderTrack

        try:
            if source.startswith("preset:"):
                # Load a preset by name
                preset_name = source[7:]
                from utils.presets import load_preset

                tracks = load_preset(preset_name)
                if not tracks:
                    logging.warning(f"Auto-DJ: Preset '{preset_name}' not found")
                    return False
                for t in tracks:
                    url = t.get("webpage_url") or t.get("url")
                    if url:
                        entry = {
                            "id": url.split("v=")[-1].split("&")[0]
                            if "v=" in url
                            else "",
                            "title": t.get("title", "Unknown"),
                            "url": url,
                            "ie_key": "Youtube",
                            "duration": t.get("duration"),
                            "thumbnail": t.get("thumbnail"),
                        }
                        await queue.put(PlaceholderTrack(entry))
                logging.info(
                    f"Auto-DJ: Loaded preset '{preset_name}' ({len(tracks)} tracks)"
                )
                return True
            elif "playlist" in source.lower() or "list=" in source:
                # YouTube playlist
                tracks = await PlaceholderTrack.from_playlist_url(
                    source, loop=self.bot.loop
                )
                for t in tracks:
                    await queue.put(t)
                logging.info(f"Auto-DJ: Loaded playlist ({len(tracks)} tracks)")
                return True
            else:
                # Single URL (maybe from history replay)
                entry = {
                    "id": source.split("v=")[-1].split("&")[0]
                    if "v=" in source
                    else "",
                    "title": "Auto-DJ",
                    "url": source,
                    "ie_key": "Youtube",
                }
                await queue.put(PlaceholderTrack(entry))
                logging.info(f"Auto-DJ: Queued single track from source")
                return True
        except Exception as e:
            logging.error(f"Auto-DJ: Failed to fill queue: {e}")
            return False

    @commands.command(name="autodj")
    async def autodj(self, ctx, *, source: str = ""):
        """Toggle Auto-DJ mode. Optionally set a source playlist/preset.

        Usage:
            ?autodj              — Toggle on/off (uses default or recently-played)
            ?autodj <YouTube URL>— Set source to a YouTube playlist
            ?autodj preset:Name  — Set source to a saved preset
            ?autodj off          — Disable Auto-DJ
        """
        guild_id = ctx.guild.id

        if source.lower() in ("off", "disable", "stop"):
            self.autodj_enabled[guild_id] = False
            await ctx.send(
                embed=self.create_embed(
                    "Auto-DJ Off",
                    f"{config.SUCCESS_EMOJI} Auto-DJ disabled. The radio will go silent when the queue empties.",
                    discord.Color.blurple(),
                )
            )
            return

        if source:
            self.autodj_source[guild_id] = source

        currently_on = self.autodj_enabled.get(guild_id, False)
        self.autodj_enabled[guild_id] = not currently_on

        status = "On" if self.autodj_enabled[guild_id] else "Off"
        source_desc = ""
        if self.autodj_enabled[guild_id]:
            s = self.autodj_source.get(guild_id, "")
            if s:
                source_desc = f"\n📡 Source: `{s}`"
            else:
                source_desc = "\n📡 Source: Recently played (shuffled replay)"

        await ctx.send(
            embed=self.create_embed(
                f"🔁 Auto-DJ {status}",
                f"{'Radio will never go silent!' if self.autodj_enabled[guild_id] else 'Radio will go silent when queue empties.'}{source_desc}",
                discord.Color.green()
                if self.autodj_enabled[guild_id]
                else discord.Color.orange(),
            )
        )

    # ── Shoutout Command ──────────────────────────────────────────

    @commands.command(name="shoutout")
    async def shoutout(self, ctx, *, user: discord.Member = None):
        """Give a shoutout to a user! The DJ will announce them over the air.

        Usage: ?shoutout @username
        """
        if not user:
            await ctx.send(
                embed=self.create_embed(
                    "Shoutout",
                    f"{config.ERROR_EMOJI} Mention a user to shout out! `?shoutout @username`",
                    discord.Color.orange(),
                )
            )
            return

        if not ctx.voice_client:
            await ctx.send(
                embed=self.create_embed(
                    "Shoutout",
                    f"{config.ERROR_EMOJI} I need to be in a voice channel for that!",
                    discord.Color.red(),
                )
            )
            return

        guild_id = ctx.guild.id

        # Build shoutout text
        from utils.dj import _format_line

        shoutout_lines = [
            "Big shoutout to {user}! You're a legend!",
            "Yo, shoutout to {user}! Thanks for rocking with us!",
            "This one goes out to {user}! You're the real MVP!",
            "Hey {user}! This one's for you, my friend!",
            "Shoutout to {user}! Keep doing what you do!",
            "Ladies and gentlemen, give it up for {user}!",
            "Where my {user} fans at? Shoutout to the one and only!",
            "And now, a very special shoutout to {user}!",
        ]
        import random

        line = random.choice(shoutout_lines)
        text = _format_line(line + " {sound:applause}", user=user.display_name)

        if self.dj_enabled.get(guild_id, False) and TTS_AVAILABLE:
            spoke = await self._dj_speak(ctx.voice_client, text, guild_id)
            if spoke:
                await ctx.send(
                    embed=self.create_embed(
                        "🎙️ Shoutout!",
                        f"Giving a shoutout to **{user.display_name}** over the air!",
                        discord.Color.purple(),
                    )
                )
                return

        # DJ not available — just send text
        await ctx.send(
            embed=self.create_embed(
                "📢 Shoutout!",
                f"🎵 Big shoutout to **{user.display_name}**! 🎵",
                discord.Color.gold(),
            )
        )

    # ── Bed Music (DJ Interlude) ──────────────────────────────────

    async def _start_bed_music(self, voice_client_unused, guild_id):
        """Start playing ambient bed music under the DJ's voice.

        Uses a loopable ambient track. The bed music is played at lower volume
        and will be stopped by _stop_bed_music() when the actual song starts.
        """
        import os
        
        vc = self._get_audio_client(guild_id)
        if not vc:
            return False

        bed_path = os.path.join("sounds", "bed_music.wav")
        if not os.path.exists(bed_path):
            # Try mp3
            bed_path = os.path.join("sounds", "bed_music.mp3")
        if not os.path.exists(bed_path):
            logging.debug(f"DJ Bed: No bed music file found, skipping")
            return False

        if self._bed_playing.get(guild_id, False):
            return True  # Already playing

        try:
            import discord

            # Can't play two sources at once on Discord's voice client.
            # If something is already playing (e.g. TTS, sound effect), skip.
            if vc.is_playing():
                logging.debug(
                    f"DJ Bed: Voice client busy in guild {guild_id}, skipping bed music"
                )
                return False

            source = discord.FFmpegPCMAudio(
                bed_path,
                before_options="-nostdin -stream_loop -1",
                options="-vn",
            )
            player = discord.PCMVolumeTransformer(source)
            player.volume = self.current_volume.get(guild_id, 1.0) * 0.3  # 30% volume

            self._dispatch_audio_play(
                guild_id,
                player,
                after=lambda e: self._on_bed_done(guild_id, e),
            )
            self._bed_playing[guild_id] = True
            logging.info(f"DJ Bed: Started bed music for guild {guild_id}")
            return True
        except Exception as e:
            logging.error(f"DJ Bed: Failed to start: {e}")
            return False

    def _on_bed_done(self, guild_id, error):
        """Callback when bed music finishes (or is stopped)."""
        self._bed_playing[guild_id] = False
        if error:
            logging.error(f"DJ Bed: Playback error for guild {guild_id}: {error}")

    async def _stop_bed_music(self, guild_id):
        """Stop bed music if it's playing."""
        vc = self._get_audio_client(guild_id)
        if not vc:
            self._bed_playing[guild_id] = False
            return
        if self._bed_playing.get(guild_id, False) and getattr(vc, 'is_playing', lambda: False)():
            vc.stop()
            self._bed_playing[guild_id] = False
            logging.info(f"DJ Bed: Stopped for guild {guild_id}")

    async def _switch_to_autonomous(self, guild_id):
        """Switch YouTube Live from mirror mode to autonomous 24/7 mode.

        Called when all humans leave the voice channel or the bot is
        disconnected, but the YouTube stream is still active. Keeps the
        stream running independently using the configured playlist.
        """
        if not self._yt_stream_active or not self._yt_streamer:
            return

        playlist_url = getattr(config, "YOUTUBE_STREAM_PLAYLIST", "") or getattr(
            config, "AUTODJ_DEFAULT_SOURCE", ""
        )
        if not playlist_url:
            logging.warning(
                "YouTube Live: Cannot switch to autonomous — no playlist URL "
                "configured. Set YOUTUBE_STREAM_PLAYLIST or AUTODJ_DEFAULT_SOURCE "
                "in .env. The YouTube stream will show a waiting card."
            )
            asyncio.ensure_future(
                self._yt_streamer.play_waiting(
                    f"Waiting for next track... | {config.STATION_NAME} Radio"
                ),
                loop=self.bot.loop,
            )
            return

        logging.info(
            f"YouTube Live: Switching from mirror mode to autonomous 24/7 "
            f"(playlist: {playlist_url[:60]})"
        )

        try:
            await self._yt_streamer.stop()
            self._yt_stream_active = False

            rtmp_url = getattr(
                config, "YOUTUBE_STREAM_URL", "rtmp://a.rtmp.youtube.com/live2"
            )
            key = getattr(config, "YOUTUBE_STREAM_KEY", "")
            if not key:
                key = self._yt_streamer.stream_key if self._yt_streamer else ""

            self._yt_streamer = YOUTUBE_STREAMER_CLASS(
                stream_key=key,
                rtmp_url=rtmp_url,
                rtmp_backup_url=getattr(
                    config,
                    "YOUTUBE_STREAM_BACKUP_URL",
                    "rtmp://b.rtmp.youtube.com/live2?backup=1",
                ),
                stream_image=getattr(config, "YOUTUBE_STREAM_IMAGE", "") or None,
                stream_gif=getattr(config, "YOUTUBE_STREAM_GIF", "") or None,
                station_name=getattr(config, "STATION_NAME", "MBot Radio"),
            )

            await self._yt_streamer.start_autonomous(
                playlist_url=playlist_url,
                loop_playlist=True,
                shuffle=True,
            )
            self._yt_stream_active = True

            logging.info(
                "YouTube Live: ✅ Successfully switched to autonomous 24/7 mode"
            )

            np_channel_id = getattr(config, "NOWPLAYING_CHANNEL_ID", 0)
            if np_channel_id:
                channel = self.bot.get_channel(np_channel_id)
                if channel:
                    await channel.send(
                        f"🎙️ **YouTube Live** switched to **autonomous 24/7 mode** — "
                        "the stream keeps running even without anyone in voice!"
                    )
        except Exception as e:
            logging.error(f"YouTube Live: Failed to switch to autonomous: {e}")
            self._yt_stream_active = False

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Detect when all humans leave the voice channel.

        If the YouTube stream is active in mirror mode and the bot's
        voice channel becomes empty (no human listeners), automatically
        switch to autonomous 24/7 mode so the stream keeps going.

        Also handles the bot being forcefully disconnected from voice.
        """
        if not self._yt_stream_active or not self._yt_streamer:
            return

        if not before or not before.channel:
            return

        if not before.channel.guild:
            return
        guild = before.channel.guild
        guild_id = guild.id
        if guild_id != getattr(self, "_yt_stream_guild", 0):
            return

        # Case 1: The bot itself was disconnected from voice
        if member.id == self.bot.user.id and after.channel is None:
            logging.info(
                f"YouTube Live: Bot disconnected from voice in {guild.name} — "
                "switching to autonomous 24/7 mode"
            )
            await self._switch_to_autonomous(guild_id)
            return

        # Case 2: A human left — check if the channel is now empty
        voice_client = guild.voice_client
        if not voice_client or not voice_client.channel:
            # Bot already left voice — switch to autonomous
            await self._switch_to_autonomous(guild_id)
            return
        if voice_client.channel.id != before.channel.id:
            return

        human_members = [m for m in voice_client.channel.members if not m.bot]
        if len(human_members) > 0:
            return

        logging.info(
            f"YouTube Live: Voice channel empty in {guild.name} — "
            "switching to autonomous 24/7 mode"
        )
        await self._switch_to_autonomous(guild_id)

    @commands.Cog.listener()
    async def on_interaction(self, interaction):
        if interaction.type == discord.InteractionType.component:
            custom_id = interaction.data["custom_id"]
            logging.info(
                f"Interaction received: {custom_id} by {interaction.user} in {interaction.guild.name}"
            )

            # Battle of the Beats vote buttons are handled by BattleView
            # — skip them here to avoid double-acknowledging
            if custom_id in ("battle_vote_a", "battle_vote_b"):
                return

            ctx = await self.bot.get_context(interaction.message)
            if custom_id == "play":
                await self.resume(ctx)
            elif custom_id == "pause":
                await self.pause(ctx)
            elif custom_id == "resume":
                await self.resume(ctx)
            elif custom_id == "skip":
                await self.skip(ctx)
            elif custom_id == "stop":
                await self.stop(ctx)
            elif custom_id == "queue":
                queue = await self.get_queue(ctx.guild.id)
                if not queue.empty():
                    queue_list = "\n".join(
                        f"**{i + 1}.** {item.title}"
                        for i, item in enumerate(list(queue._queue))
                    )
                    embed = self.create_embed(
                        f"{config.QUEUE_EMOJI} Current Queue", queue_list
                    )
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                else:
                    embed = self.create_embed(
                        "Empty Queue", "The queue is currently empty."
                    )
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                return  # Exit early as we've already responded
            await interaction.response.defer()

    # ── Battle of the Beats ──────────────────────────────────────────

    @commands.command(name="battle", aliases=["showdown", "versus", "vs"])
    async def battle(self, ctx, song_a: str, song_b: str):
        """Start a Battle of the Beats! Two songs go head-to-head and the community votes.

        Usage: ?battle <song_a_url> <song_b_url>
        Example: ?battle https://youtube.com/watch?v=xxx https://youtube.com/watch?v=yyy
        """
        guild_id = ctx.guild.id

        # Check if a battle is already running in this guild
        if guild_id in self._battles and self._battles[guild_id].get("active"):
            await ctx.send(
                embed=self.create_embed(
                    "⚔️ Battle in Progress",
                    "A Battle of the Beats is already running! Wait for it to finish.",
                    discord.Color.orange(),
                )
            )
            return

        # Validate that the bot is in a voice channel
        if not ctx.voice_client:
            await ctx.send(
                embed=self.create_embed(
                    "⚔️ Battle",
                    f"{config.ERROR_EMOJI} I need to be in a voice channel! Use `?join` first.",
                    discord.Color.red(),
                )
            )
            return

        # Extract titles from song URLs
        import yt_dlp

        title_a = song_a
        title_b = song_b
        ydl_opts = get_ytdl_format_options()
        ydl_opts["extract_flat"] = True
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_a = ydl.extract_info(song_a, download=False)
                if info_a:
                    title_a = info_a.get("title", song_a)
        except Exception:
            pass
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_b = ydl.extract_info(song_b, download=False)
                if info_b:
                    title_b = info_b.get("title", song_b)
        except Exception:
            pass

        # Limit title length for display
        title_a = title_a[:60] + "…" if len(title_a) > 60 else title_a
        title_b = title_b[:60] + "…" if len(title_b) > 60 else title_b

        # Create the battle state
        battle_data = {
            "song_a": song_a,
            "song_b": song_b,
            "title_a": title_a,
            "title_b": title_b,
            "votes_a": {},  # user_id -> timestamp
            "votes_b": {},  # user_id -> timestamp
            "message_id": None,
            "channel_id": ctx.channel.id,
            "active": True,
            "created_at": time.time(),
            "duration": 60,  # 60-second voting window
            "dj_announced": False,
        }
        self._battles[guild_id] = battle_data

        # Create the Discord voting embed with buttons
        embed = discord.Embed(
            title="⚔️ BATTLE OF THE BEATS",
            description=f"**🅰️ {title_a}**\nvs\n**🅱️ {title_b}**\n\nVote below! You have **60 seconds**.",
            color=0xE91E63,
        )
        embed.set_footer(text="⏱️ Voting ends in 60 seconds — vote now!")
        embed.add_field(name="🅰️", value="0 votes", inline=True)
        embed.add_field(name="🅱️", value="0 votes", inline=True)
        embed.add_field(name="📊", value="—", inline=True)

        view = BattleView(self, guild_id)
        msg = await ctx.send(embed=embed, view=view)

        # Store the message ID for live updates
        battle_data["message_id"] = msg.id

        # Start the countdown timer
        battle_data["timer_task"] = self.bot.loop.create_task(
            self._battle_countdown(guild_id, ctx.channel, msg)
        )

    async def _battle_countdown(self, guild_id, channel, message):
        """Countdown timer and live vote updates for a battle."""
        try:
            battle = self._battles.get(guild_id)
            if not battle:
                return

            duration = battle.get("duration", 60)
            start = battle.get("created_at", time.time())
            update_interval = 5  # Update every 5 seconds

            while True:
                await asyncio.sleep(update_interval)
                battle = self._battles.get(guild_id)
                if not battle or not battle.get("active"):
                    return

                elapsed = time.time() - start
                remaining = max(0, duration - elapsed)

                if remaining <= 0:
                    # Time's up — announce the winner
                    await self._battle_finish(guild_id, channel)
                    return

                # Update the embed with current votes
                votes_a = len(battle.get("votes_a", {}))
                votes_b = len(battle.get("votes_b", {}))
                total = votes_a + votes_b
                pct_a = (votes_a / total * 100) if total > 0 else 50
                pct_b = (votes_b / total * 100) if total > 0 else 50

                bar_len = 20
                bar_a = "█" * int(pct_a / 100 * bar_len) + "░" * (
                    bar_len - int(pct_a / 100 * bar_len)
                )
                bar_b = "█" * int(pct_b / 100 * bar_len) + "░" * (
                    bar_len - int(pct_b / 100 * bar_len)
                )

                embed = discord.Embed(
                    title="⚔️ BATTLE OF THE BEATS",
                    description=f"**🅰️ {battle['title_a']}**\nvs\n**🅱️ {battle['title_b']}**\n\n⏱️ **{int(remaining)}s** left to vote!",
                    color=0xE91E63,
                )
                embed.add_field(
                    name=f"🅰️ {votes_a} votes ({pct_a:.0f}%)",
                    value=f"`{bar_a}`",
                    inline=False,
                )
                embed.add_field(
                    name=f"🅱️ {votes_b} votes ({pct_b:.0f}%)",
                    value=f"`{bar_b}`",
                    inline=False,
                )

                try:
                    await message.edit(embed=embed)
                except (discord.NotFound, discord.Forbidden):
                    return

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logging.error(f"Battle countdown error for guild {guild_id}: {e}")

    async def _battle_finish(self, guild_id, channel):
        """Finish a battle: announce the winner and queue the winning song."""
        battle = self._battles.get(guild_id)
        if not battle:
            return

        battle["active"] = False

        votes_a = len(battle.get("votes_a", {}))
        votes_b = len(battle.get("votes_b", {}))
        title_a = battle["title_a"]
        title_b = battle["title_b"]

        # Determine winner
        if votes_a > votes_b:
            winner_key = "a"
            winner_title = title_a
            winner_url = battle["song_a"]
            result_text = f"🅰️ **{title_a}** wins with {votes_a}–{votes_b}!"
        elif votes_b > votes_a:
            winner_key = "b"
            winner_title = title_b
            winner_url = battle["song_b"]
            result_text = f"🅱️ **{title_b}** wins with {votes_b}–{votes_a}!"
        else:
            # Tie — pick randomly
            import random as _r

            winner_key = _r.choice(["a", "b"])
            winner_title = title_a if winner_key == "a" else title_b
            winner_url = battle["song_a"] if winner_key == "a" else battle["song_b"]
            result_text = (
                f"🤝 It's a tie! {votes_a}–{votes_b}. Random pick: **{winner_title}**"
            )

        # Build final embed
        total = votes_a + votes_b
        pct_a = (votes_a / total * 100) if total > 0 else 50
        pct_b = (votes_b / total * 100) if total > 0 else 50
        bar_len = 20
        bar_a = "█" * int(pct_a / 100 * bar_len) + "░" * (
            bar_len - int(pct_a / 100 * bar_len)
        )
        bar_b = "█" * int(pct_b / 100 * bar_len) + "░" * (
            bar_len - int(pct_b / 100 * bar_len)
        )

        embed = discord.Embed(
            title="⚔️ BATTLE RESULTS",
            description=f"**🅰️ {title_a}** ({votes_a} votes)\nvs\n**🅱️ {title_b}** ({votes_b} votes)\n\n🏆 {result_text}",
            color=0xFFD700,
        )
        embed.add_field(
            name=f"🅰️ {votes_a} votes ({pct_a:.0f}%)",
            value=f"`{bar_a}`",
            inline=False,
        )
        embed.add_field(
            name=f"🅱️ {votes_b} votes ({pct_b:.0f}%)",
            value=f"`{bar_b}`",
            inline=False,
        )
        embed.set_footer(text=f"🏆 {winner_title}")

        # Update the original message
        msg_id = battle.get("message_id")
        if msg_id:
            try:
                msg = await channel.fetch_message(msg_id)
                await msg.edit(embed=embed, view=None)
            except (discord.NotFound, discord.Forbidden):
                pass

        # Send a separate results message
        await channel.send(embed=embed)

        # Queue the winning song — insert at the FRONT of the queue
        # so it plays as the very next song after whatever is currently playing,
        # without stopping or interrupting the current song.
        try:
            from cogs.youtube import PlaceholderTrack

            # PlaceholderTrack expects a dict with at least "title" and "url"/"id"
            winner_data = {"title": winner_title, "url": winner_url}
            # If the URL looks like a YouTube video, also set the id and webpage_url
            if "youtube.com" in winner_url or "youtu.be" in winner_url:
                import urllib.parse

                parsed = urllib.parse.urlparse(winner_url)
                qs = urllib.parse.parse_qs(parsed.query)
                vid_id = qs.get("v", [None])[0]
                if vid_id:
                    winner_data["id"] = vid_id
                winner_data["webpage_url"] = winner_url

            winner_track = PlaceholderTrack(winner_data)
            if guild_id not in self.song_queues:
                self.song_queues[guild_id] = asyncio.Queue()
            # Insert at position 0 (front of queue) so it's the next song to play
            self.song_queues[guild_id]._queue.appendleft(winner_track)

            queue_pos = len(self.song_queues[guild_id]._queue)
            if queue_pos == 1:
                await channel.send(f"🎵 Winner **{winner_title}** is up next!")
            else:
                await channel.send(
                    f"🎵 Winner **{winner_title}** added as #1 in the queue ({queue_pos} songs total)!"
                )

            # If nothing is playing at all, start playback
            voice_client = channel.guild.voice_client
            if (
                voice_client
                and not voice_client.is_playing()
                and not voice_client.is_paused()
            ):

                class _FakeCtx:
                    pass

                fake_ctx = _FakeCtx()
                fake_ctx.guild = channel.guild
                fake_ctx.voice_client = voice_client
                fake_ctx.channel = channel
                fake_ctx.author = channel.guild.me
                await self.play_next(fake_ctx)
        except Exception as e:
            logging.error(f"Battle: Failed to queue winner: {e}")
            await channel.send(f"❌ Couldn't queue the winner: {e}")

    def get_battle_state(self, guild_id):
        """Get the current battle state for a guild (used by web API)."""
        return self._battles.get(guild_id)


class BattleView(discord.ui.View):
    """Interactive Discord button view for Battle of the Beats voting."""

    def __init__(self, music_cog, guild_id):
        super().__init__(timeout=70)  # Slightly longer than the 60s battle
        self.music_cog = music_cog
        self.guild_id = guild_id

        # Add vote buttons programmatically
        btn_a = discord.ui.Button(
            label="🅰️ Song A",
            style=discord.ButtonStyle.primary,
            custom_id="battle_vote_a",
        )
        btn_a.callback = self._vote_a
        self.add_item(btn_a)

        btn_b = discord.ui.Button(
            label="🅱️ Song B",
            style=discord.ButtonStyle.danger,
            custom_id="battle_vote_b",
        )
        btn_b.callback = self._vote_b
        self.add_item(btn_b)

    async def _vote_a(self, interaction: discord.Interaction):
        battle = self.music_cog._battles.get(self.guild_id)
        if not battle or not battle.get("active"):
            await interaction.response.send_message(
                "⏱️ This battle has ended!", ephemeral=True
            )
            return

        user_id = str(interaction.user.id)
        # Switching vote from B to A
        if user_id in battle.get("votes_b", {}):
            del battle["votes_b"][user_id]
        battle["votes_a"][user_id] = time.time()

        votes_a = len(battle["votes_a"])
        votes_b = len(battle["votes_b"])
        await interaction.response.send_message(
            f"🅰️ Voted for **{battle['title_a']}**! ({votes_a}–{votes_b})",
            ephemeral=True,
        )

    async def _vote_b(self, interaction: discord.Interaction):
        battle = self.music_cog._battles.get(self.guild_id)
        if not battle or not battle.get("active"):
            await interaction.response.send_message(
                "⏱️ This battle has ended!", ephemeral=True
            )
            return

        user_id = str(interaction.user.id)
        # Switching vote from A to B
        if user_id in battle.get("votes_a", {}):
            del battle["votes_a"][user_id]
        battle["votes_b"][user_id] = time.time()

        votes_a = len(battle["votes_a"])
        votes_b = len(battle["votes_b"])
        await interaction.response.send_message(
            f"🅱️ Voted for **{battle['title_b']}**! ({votes_a}–{votes_b})",
            ephemeral=True,
        )

    # ── YouTube Live Streaming Commands ────────────────────────────

    @commands.command(name="golive")
    @commands.is_owner()
    async def golive(self, ctx, stream_key: str = ""):
        """Start streaming to YouTube Live via RTMP.

        Usage:
          ?golive                    — Use the stream key from .env
          ?golive xxxx-xxxx-xxxx    — Use a specific stream key

        Get your stream key from YouTube Studio → Go Live → Stream Key.
        The bot streams audio + a static image card with song titles.
        """
        if not YOUTUBE_STREAMER_CLASS:
            return await ctx.send(
                embed=self.create_embed(
                    "Not Available",
                    f"{config.ERROR_EMOJI} YouTube Live streaming module not available. "
                    "Check that `utils/youtube_stream.py` exists and FFmpeg is installed.",
                    discord.Color.red(),
                )
            )

        if self._yt_stream_active:
            return await ctx.send(
                embed=self.create_embed(
                    "Already Live",
                    f"{config.SUCCESS_EMOJI} YouTube Live stream is already running! "
                    "Use `?stoplive` to stop it.",
                    discord.Color.blue(),
                )
            )

        # Voice connection is only required for mirror mode.
        # Autonomous (24/7) mode streams without Discord.
        has_voice = ctx.voice_client and ctx.voice_client.is_connected()

        # Use provided key, or fall back to .env config
        key = stream_key or getattr(config, "YOUTUBE_STREAM_KEY", "")
        if not key:
            return await ctx.send(
                embed=self.create_embed(
                    "No Stream Key",
                    f"{config.ERROR_EMOJI} No stream key provided. Get one from "
                    "YouTube Studio → Go Live → Stream Key.\n"
                    "Usage: `?golive your-stream-key-here`",
                    discord.Color.orange(),
                )
            )

        rtmp_url = getattr(
            config, "YOUTUBE_STREAM_URL", "rtmp://a.rtmp.youtube.com/live2"
        )
        stream_image = getattr(config, "YOUTUBE_STREAM_IMAGE", "") or None
        stream_gif = getattr(config, "YOUTUBE_STREAM_GIF", "") or None

        self._yt_streamer = YOUTUBE_STREAMER_CLASS(
            stream_key=key,
            rtmp_url=rtmp_url,
            stream_image=stream_image,
            stream_gif=stream_gif,
            station_name=getattr(config, "STATION_NAME", "MBot Radio"),
        )
        self._yt_stream_guild = ctx.guild.id

        # Check if user wants autonomous (24/7) mode or mirror mode
        # Autonomous: no Discord voice needed, plays from playlist
        # Mirror: shadows Discord playback
        playlist_url = getattr(config, "AUTODJ_DEFAULT_SOURCE", "") or ""
        use_autonomous = not has_voice or (playlist_url and not has_voice)

        if use_autonomous and playlist_url:
            # 24/7 autonomous mode — no Discord voice needed
            await self._yt_streamer.start_autonomous(
                playlist_url=playlist_url,
                loop_playlist=True,
                shuffle=True,
            )
            self._yt_stream_active = True
            key_display = f"{key[:4]}...{key[-4:]}" if len(key) > 8 else "***"
            await ctx.send(
                embed=self.create_embed(
                    "🔴 YouTube Live Started (24/7)",
                    f"{config.SUCCESS_EMOJI} Streaming to YouTube Live in **autonomous mode**!\n"
                    f"📡 RTMP: `{rtmp_url}`\n"
                    f"🔑 Key: `{key_display}`\n"
                    f"🖼️ Card: `{stream_image or 'logo.png'}`\n"
                    f"📜 Playlist: `{playlist_url[:60]}`\n\n"
                    "The stream runs 24/7 — no Discord voice channel needed. "
                    "Songs play automatically from the playlist.\n"
                    "Use `?stoplive` to stop. Use Mission Control for controls.",
                    discord.Color.green(),
                )
            )
        else:
            # Mirror mode — shadows Discord playback
            if not has_voice:
                return await ctx.send(
                    embed=self.create_embed(
                        "Not in Voice",
                        f"{config.ERROR_EMOJI} The bot must be in a voice channel for mirror mode. "
                        "Join a channel and use `?play` first, or set `AUTODJ_DEFAULT_SOURCE` "
                        "in .env for 24/7 autonomous streaming.",
                        discord.Color.red(),
                    )
                )

            await self._yt_streamer.start()
            self._yt_stream_active = True

            # If a song is currently playing, start streaming it immediately
            current = self.current_song.get(ctx.guild.id)
            if current and isinstance(current, dict) and current.get("url"):
                thumb = current.get("thumbnail")
                await self._yt_streamer.play_song(
                    current.get("url"), current.get("title", "Unknown"), thumbnail=thumb
                )

            key_display = f"{key[:4]}...{key[-4:]}" if len(key) > 8 else "***"
            await ctx.send(
                embed=self.create_embed(
                    "🔴 YouTube Live Started",
                    f"{config.SUCCESS_EMOJI} Streaming to YouTube Live (mirror mode)!\n"
                    f"📡 RTMP: `{rtmp_url}`\n"
                    f"🔑 Key: `{key_display}`\n"
                    f"🖼️ Card: `{stream_image or 'logo.png'}`\n\n"
                    "Go to **YouTube Studio → Go Live** to see the stream. "
                    "Use `?stoplive` to stop.",
                    discord.Color.green(),
                )
            )

    @commands.command(name="autolive")
    @commands.is_owner()
    async def autolive(self, ctx, playlist_url: str = ""):
        """Start YouTube Live in autonomous 24/7 mode.

        Usage:
          ?autolive <playlist_url>   — Stream from a specific playlist
          ?autolive                  — Use AUTODJ_DEFAULT_SOURCE from .env

        No Discord voice channel needed — runs 24/7 independently.
        """
        if not YOUTUBE_STREAMER_CLASS:
            return await ctx.send(
                embed=self.create_embed(
                    "Not Available",
                    f"{config.ERROR_EMOJI} YouTube Live streaming module not available. "
                    "Check that `utils/youtube_stream.py` exists and FFmpeg is installed.",
                    discord.Color.red(),
                )
            )

        if self._yt_stream_active:
            return await ctx.send(
                embed=self.create_embed(
                    "Already Live",
                    f"{config.SUCCESS_EMOJI} YouTube Live stream is already running! "
                    "Use `?stoplive` to stop it.",
                    discord.Color.blue(),
                )
            )

        # Resolve playlist URL
        if not playlist_url:
            playlist_url = getattr(config, "YOUTUBE_STREAM_PLAYLIST", "") or getattr(
                config, "AUTODJ_DEFAULT_SOURCE", ""
            )
        if not playlist_url:
            return await ctx.send(
                embed=self.create_embed(
                    "No Playlist",
                    f"{config.ERROR_EMOJI} No playlist URL provided. "
                    "Usage: `?autolive <playlist_url>`\n"
                    "Or set `YOUTUBE_STREAM_PLAYLIST` in .env",
                    discord.Color.orange(),
                )
            )

        key = getattr(config, "YOUTUBE_STREAM_KEY", "")
        if not key:
            return await ctx.send(
                embed=self.create_embed(
                    "No Stream Key",
                    f"{config.ERROR_EMOJI} Set `YOUTUBE_STREAM_KEY` in .env first.",
                    discord.Color.red(),
                )
            )

        rtmp_url = getattr(
            config, "YOUTUBE_STREAM_URL", "rtmp://a.rtmp.youtube.com/live2"
        )
        stream_image = getattr(config, "YOUTUBE_STREAM_IMAGE", "") or None

        self._yt_streamer = YOUTUBE_STREAMER_CLASS(
            stream_key=key,
            rtmp_url=rtmp_url,
            stream_image=stream_image,
            stream_gif=getattr(config, "YOUTUBE_STREAM_GIF", "") or None,
            station_name=getattr(config, "STATION_NAME", "MBot Radio"),
        )
        self._yt_stream_guild = ctx.guild.id

        await self._yt_streamer.start_autonomous(
            playlist_url=playlist_url,
            loop_playlist=True,
            shuffle=True,
        )
        self._yt_stream_active = True

        await ctx.send(
            embed=self.create_embed(
                "🔴 YouTube Live Started (24/7)",
                f"{config.SUCCESS_EMOJI} Autonomous streaming started!\n"
                f"📜 Playlist: `{playlist_url[:60]}`\n"
                f"🔁 Loop: On | 🔀 Shuffle: On\n\n"
                "No Discord voice channel needed — runs 24/7.\n"
                "Use `?stoplive` to stop. "
                "Use `?livestatus` to check position.",
                discord.Color.green(),
            )
        )

    @commands.command(name="stoplive")
    @commands.is_owner()
    async def stoplive(self, ctx):
        """Stop the YouTube Live stream."""
        if not self._yt_stream_active or not self._yt_streamer:
            return await ctx.send(
                embed=self.create_embed(
                    "Not Streaming",
                    "YouTube Live stream is not running. Use `?golive` or `?autolive` to start.",
                    discord.Color.blue(),
                )
            )

        await self._yt_streamer.stop()
        self._yt_stream_active = False
        self._yt_streamer = None
        self._yt_stream_guild = None

        await ctx.send(
            embed=self.create_embed(
                "YouTube Live Stopped",
                f"{config.SUCCESS_EMOJI} YouTube Live stream has been stopped.",
                discord.Color.green(),
            )
        )

    @commands.command(name="ytskip")
    @commands.is_owner()
    async def ytskip(self, ctx):
        """Skip to the next song in autonomous YouTube Live stream."""
        if not self._yt_stream_active or not self._yt_streamer:
            return await ctx.send("No active YouTube Live stream.")
        if not self._yt_streamer.is_autonomous:
            return await ctx.send(
                "Skip only works in autonomous mode. Use `?skip` for Discord playback."
            )

        await self._yt_streamer.skip_song()
        await ctx.send("⏭️ Skipping to next song in YouTube Live stream...")

    @commands.command(name="livestatus")
    async def livestatus(self, ctx):
        """Check the YouTube Live stream status."""
        if self._yt_stream_active and self._yt_streamer:
            status = "🔴 Live" if self._yt_streamer.is_running else "⚠️ Reconnecting"
            lines = [
                f"Status: **{status}**",
                f"Mode: **{'🤖 Autonomous (24/7)' if self._yt_streamer.is_autonomous else '🪞 Mirror (Discord)'}**",
            ]
            if self._yt_streamer.is_autonomous:
                lines.append(f"Playlist: `{self._yt_streamer.playlist_url[:60]}`")
                lines.append(
                    f"Position: {self._yt_streamer.playlist_index + 1}/{self._yt_streamer.playlist_size}"
                )
                lines.append(f"Songs streamed: {self._yt_streamer.song_count}")
                uptime = self._yt_streamer.uptime_seconds
                if uptime < 60:
                    lines.append(f"Uptime: {uptime:.0f}s")
                elif uptime < 3600:
                    lines.append(f"Uptime: {uptime / 60:.0f}m")
                else:
                    lines.append(f"Uptime: {uptime / 3600:.1f}h")
            else:
                current = self.current_song.get(ctx.guild.id)
                if current and isinstance(current, dict): current = current.get("webpage_url")
                if current:
                    song = self.current_song.get(ctx.guild.id)
                    lines.append(
                        f"Now Streaming: **{song.title if song else current[:60]}**"
                    )
                else:
                    lines.append("Now Streaming: Waiting card (between songs)")
            if self._yt_streamer.last_error:
                lines.append(f"⚠️ Last error: {self._yt_streamer.last_error[:80]}")
            await ctx.send(
                embed=self.create_embed(
                    "YouTube Live Status", "\n".join(lines), discord.Color.blue()
                )
            )
        else:
            await ctx.send(
                embed=self.create_embed(
                    "YouTube Live",
                    "Not streaming. Use `?golive` to start.",
                    discord.Color.dark_grey(),
                )
            )


async def setup(bot):
    await bot.add_cog(Music(bot))
