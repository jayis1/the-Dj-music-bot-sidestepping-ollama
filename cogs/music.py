import asyncio
import discord
from discord.ext import commands
from googleapiclient.discovery import build
import random
import logging
import time

import config

from cogs.youtube import (
    YTDLSource,
    PlaceholderTrack,
    FFMPEG_OPTIONS,
    YTDL_FORMAT_OPTIONS,
)
from utils.suno import is_suno_url, get_suno_track
from utils.dj import (
    EDGE_TTS_AVAILABLE,
    generate_intro,
    generate_song_intro,
    generate_outro,
    generate_tts,
    cleanup_tts_file,
    list_voices,
    DEFAULT_VOICE,
)


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

        # DJ Mode state (per-guild)
        self.dj_enabled = {}  # guild_id -> bool
        self.dj_voice = {}  # guild_id -> str (Edge TTS voice name)
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

    async def get_queue(self, guild_id):
        if guild_id not in self.song_queues:
            self.song_queues[guild_id] = asyncio.Queue()
        return self.song_queues[guild_id]

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

            await ctx.send(
                embed=self.create_embed(
                    "Left Channel",
                    f"{config.SUCCESS_EMOJI} Successfully disconnected from the voice channel.",
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
        if not ctx.author.voice:
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
            if not ctx.voice_client:
                logging.info("Bot not in a voice channel, joining.")
                await ctx.author.voice.channel.connect(self_deaf=True)
            elif not ctx.voice_client.is_connected():
                logging.info(
                    "Voice client exists but is not connected — force-reconnecting."
                )
                await ctx.voice_client.disconnect(force=True)
                await asyncio.sleep(0.5)
                await ctx.author.voice.channel.connect(self_deaf=True)

            if not ctx.voice_client.is_playing():
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
        if not ctx.author.voice:
            logging.warning("User not in a voice channel.")
            return await ctx.send(
                embed=self.create_embed(
                    "Error",
                    f"{config.ERROR_EMOJI} You must be in a voice channel to play music.",
                    discord.Color.red(),
                )
            )

        if not ctx.voice_client:
            logging.info("Bot not in a voice channel, joining.")
            await ctx.author.voice.channel.connect(self_deaf=True)

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

            if not ctx.voice_client.is_playing():
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
        if not ctx.author.voice:
            logging.warning("User not in a voice channel.")
            return await ctx.send(
                embed=self.create_embed(
                    "Error",
                    f"{config.ERROR_EMOJI} You must be in a voice channel to play music.",
                    discord.Color.red(),
                )
            )

        if not ctx.voice_client:
            logging.info("Bot not in a voice channel, joining.")
            await ctx.author.voice.channel.connect(self_deaf=True)

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

            if not ctx.voice_client.is_playing():
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
                    channel = self.bot.get_channel(ctx.channel.id)
                    if channel:
                        await channel.send(
                            embed=self.create_embed(
                                "Playback Error",
                                f"{config.ERROR_EMOJI} Multiple songs failed to resolve. "
                                "YouTube may be blocking requests. Try `?fetch_and_set_cookies` "
                                "or check your network.",
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
                    logging.error(
                        f"play_next: Failed to resolve PlaceholderTrack '{data.title}': {e}"
                    )
                    await asyncio.sleep(2)
                    await self.play_next(ctx, _skip_count=_skip_count + 1)
                    return

            # ── DJ Mode: Speak an intro before the song ────────────
            if (
                self.dj_enabled.get(guild_id, False)
                and EDGE_TTS_AVAILABLE
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
                    # Start bed music under the DJ voice
                    await self._start_bed_music(ctx.voice_client, guild_id)
                    # TTS started — song will play after the intro finishes
                    # (via _on_tts_done → _play_song_after_dj → _start_song_playback)
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
                and EDGE_TTS_AVAILABLE
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

        queue = await self.get_queue(guild_id)

        # Check if looping is enabled
        if self.looping.get(guild_id):
            # If looping, re-add the current song to the queue
            current_song_data = self.current_song.get(guild_id)
            if current_song_data:
                await queue.put(current_song_data)
                logging.info(
                    f"Looping enabled. Re-added {current_song_data.title} to queue."
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

        # If DJ is currently speaking, cancel the TTS and pending song
        if self.dj_playing_tts.get(guild_id, False):
            logging.info(f"DJ: Skipping TTS intro in {ctx.guild.name}")
            self.dj_playing_tts[guild_id] = False
            pending = getattr(self, "_dj_pending", {}).pop(guild_id, None)
            if ctx.voice_client and ctx.voice_client.is_playing():
                ctx.voice_client.stop()
            # Clean up TTS temp file
            tts_path = self._current_tts_path.get(guild_id)
            if tts_path:
                cleanup_tts_file(tts_path)
                self._current_tts_path[guild_id] = None

        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.stop()
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
        if ctx.voice_client:
            ctx.voice_client.stop()
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
        if ctx.voice_client and ctx.voice_client.is_playing():
            ctx.voice_client.pause()
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
        if ctx.voice_client:
            if ctx.voice_client.is_paused():
                ctx.voice_client.resume()
                logging.info(f"Music resumed in {ctx.guild.name}")
                await ctx.send(
                    embed=self.create_embed(
                        "Playback Resumed",
                        f"{config.PLAY_EMOJI} The music has been resumed.",
                    )
                )
            elif not ctx.voice_client.is_playing() and ctx.voice_client.source:
                # Fallback if state is inconsistent but source exists
                ctx.voice_client.resume()
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

            # Dynamically create FFMPEG options with atempo filter
            player_options = FFMPEG_OPTIONS.copy()
            if new_speed != 1.0:
                player_options["options"] += f' -filter:a "atempo={new_speed}"'

            # Create and play the new player with the updated speed
            source = discord.FFmpegPCMAudio(current_song_data.url, **player_options)
            player = discord.PCMVolumeTransformer(source)
            player.volume = self.current_volume.get(
                guild_id, 1.0
            )  # Apply stored volume
            ctx.voice_client.play(
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

        if not EDGE_TTS_AVAILABLE:
            return await ctx.send(
                embed=self.create_embed(
                    "DJ Unavailable",
                    f"{config.ERROR_EMOJI} DJ mode requires the `edge-tts` package. "
                    "Install it with `pip install edge-tts` and restart the bot.",
                    discord.Color.red(),
                )
            )

        guild_id = ctx.guild.id
        self.dj_enabled[guild_id] = not self.dj_enabled.get(guild_id, False)
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

    @commands.command(name="djvoice")
    async def dj_voice_cmd(self, ctx, *, voice_name: str = None):
        """Set the DJ's TTS voice. Use ?djvoices to see available voices."""
        logging.info(
            f"DJ voice command invoked by {ctx.author} in {ctx.guild.name} with voice: {voice_name}"
        )
        guild_id = ctx.guild.id

        if not EDGE_TTS_AVAILABLE:
            return await ctx.send(
                embed=self.create_embed(
                    "DJ Unavailable",
                    f"{config.ERROR_EMOJI} DJ mode requires the `edge-tts` package.",
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

        if not EDGE_TTS_AVAILABLE:
            return await ctx.send(
                embed=self.create_embed(
                    "DJ Unavailable",
                    f"{config.ERROR_EMOJI} DJ mode requires the `edge-tts` package.",
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

    async def _dj_speak(self, voice_client, text: str, guild_id: int):
        """
        Generate TTS audio and play it through the voice client.
        Also plays any {sound:name} tags found in the text after TTS finishes.
        Returns True if TTS was played, False if skipped.
        """
        if not EDGE_TTS_AVAILABLE:
            return False

        # Extract {sound:name} tags before TTS so they aren't spoken aloud
        from utils.dj import extract_sound_tags

        clean_text, sound_ids = extract_sound_tags(text)
        if sound_ids:
            logging.info(f"DJ: Extracted sound tags: {sound_ids} for guild {guild_id}")

        voice = self.dj_voice.get(guild_id, config.DJ_VOICE)
        tts_path = await generate_tts(clean_text if clean_text else text, voice)

        if not tts_path:
            logging.warning(f"DJ: TTS generation returned None for guild {guild_id}")
            return False

        self._current_tts_path[guild_id] = tts_path
        self.dj_playing_tts[guild_id] = True
        # Store pending sounds to play after TTS finishes
        self._dj_pending_sounds[guild_id] = sound_ids

        try:
            # TTS audio — no reconnect options needed (local file), no video
            tts_source = discord.FFmpegPCMAudio(
                tts_path,
                before_options="-nostdin",
                options="-vn",
            )
            tts_player = discord.PCMVolumeTransformer(tts_source)
            tts_player.volume = self.current_volume.get(guild_id, 1.0)

            # We cannot await play() — it starts playback and returns immediately.
            # The 'after' callback fires when TTS playback finishes.
            loop = self.bot.loop
            voice_client.play(
                tts_player,
                after=lambda e: loop.call_soon_threadsafe(
                    self._on_tts_done, guild_id, e
                ),
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
            cleanup_tts_file(tts_path)
            self._current_tts_path[guild_id] = None

        if error:
            logging.error(f"DJ: TTS playback error for guild {guild_id}: {error}")

        # Play any pending sound effects from {sound:name} tags
        pending_sounds = self._dj_pending_sounds.pop(guild_id, [])

        if pending_sounds:
            logging.info(
                f"DJ: Playing {len(pending_sounds)} sound effects for guild {guild_id}"
            )
            asyncio.ensure_future(
                self._play_dj_sounds_then_song(guild_id, pending_sounds),
                loop=self.bot.loop,
            )
        else:
            logging.info(f"DJ: TTS done for guild {guild_id}, scheduling song playback")
            asyncio.ensure_future(
                self._play_song_after_dj(guild_id),
                loop=self.bot.loop,
            )

    async def _play_dj_sounds_then_song(self, guild_id, sound_ids):
        """
        Play a sequence of sound effects, then play the pending song.
        Each sound is capped at MAX_SOUND_SECONDS to prevent long sounds
        from blocking the next song (discord.py raises "already playing"
        if we try to start a song while a sound is still going).
        """
        from utils.soundboard import get_sound_path
        import discord

        guild = self.bot.get_guild(guild_id)
        if not guild or not guild.voice_client:
            # Voice gone — skip sounds, go straight to song
            await self._play_song_after_dj(guild_id)
            return

        for sound_id in sound_ids:
            path = get_sound_path(sound_id)
            if not path:
                logging.warning(f"DJ: Sound '{sound_id}' not found, skipping")
                continue

            try:
                # Cap sound duration at MAX_SOUND_SECONDS using FFmpeg -t flag.
                # This prevents 20-second airhorn blasts from blocking the next song.
                # DJ sound effects should be short stingers, not full soundscapes.
                max_sec = getattr(config, "MAX_SOUND_SECONDS", 3)
                ffmpeg_options = f"-vn -t {max_sec}"
                source = discord.FFmpegPCMAudio(
                    path,
                    before_options="-nostdin",
                    options=ffmpeg_options,
                )
                player = discord.PCMVolumeTransformer(source)
                player.volume = self.current_volume.get(guild_id, 1.0)

                # Stop anything currently playing before playing the sound
                if guild.voice_client.is_playing():
                    guild.voice_client.stop()
                    await asyncio.sleep(0.1)  # Brief pause to let stop take effect

                # Play and wait for this sound to finish
                finished = asyncio.Event()

                def _after(e):
                    if e:
                        logging.error(f"DJ: Sound '{sound_id}' error: {e}")
                    self.bot.loop.call_soon_threadsafe(finished.set)

                guild.voice_client.play(player, after=_after)
                logging.info(
                    f"DJ: Playing sound effect '{sound_id}' in guild {guild_id}"
                )

                # Wait up to 5s for the sound to finish (sounds are capped at 3s + margin)
                try:
                    await asyncio.wait_for(finished.wait(), timeout=5)
                except asyncio.TimeoutError:
                    logging.warning(
                        f"DJ: Sound '{sound_id}' timed out (may have been stopped)"
                    )

            except Exception as e:
                logging.error(f"DJ: Failed to play sound '{sound_id}': {e}")
                continue

        # All sounds done — play the song
        await self._play_song_after_dj(guild_id)

    async def _play_song_after_dj(self, guild_id):
        """
        Called after DJ TTS intro finishes. Plays the queued song that
        was already dequeued but held until the intro was spoken.
        'pending_song' is stored on the guild before TTS starts.
        """
        guild = self.bot.get_guild(guild_id)
        if not guild or not guild.voice_client:
            logging.warning(f"DJ: Bot not in voice for guild {guild_id} after TTS")
            return

        pending = getattr(self, "_dj_pending", {}).get(guild_id)
        if not pending:
            logging.warning(f"DJ: No pending song for guild {guild_id}")
            return

        ctx, data, channel_id = pending
        del self._dj_pending[guild_id]

        # Now play the actual song
        await self._start_song_playback(ctx, data, channel_id)

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

        if not ctx.voice_client or not ctx.voice_client.is_connected():
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

            # Speed filter
            if current_speed != 1.0:
                audio_filters.append(f"atempo={current_speed}")

            # Crossfade: fade in the first few seconds of a new song
            crossfade = getattr(config, "CROSSFADE_DURATION", 0)
            if crossfade > 0 and data.duration and data.duration > crossfade * 2:
                audio_filters.append(f"afade=t=in:st=0:d={crossfade}")

            if audio_filters:
                player_options["options"] += f' -filter:a "{"+".join(audio_filters)}"'

            source = discord.FFmpegPCMAudio(data.url, **player_options)
            player = discord.PCMVolumeTransformer(source)
            player.volume = self.current_volume.get(guild_id, 1.0)

            logging.info(
                f"Playback initiated for {data.title} with speed {current_speed}x. "
                f"Applied volume: {player.volume}"
            )
            ctx.voice_client.play(
                player,
                after=lambda e: self.bot.loop.create_task(self._after_playback(ctx, e)),
            )
            self.current_song[guild_id] = data
            self.song_start_time[guild_id] = time.time()

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

        if self.dj_enabled.get(guild_id, False) and EDGE_TTS_AVAILABLE:
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

    async def _start_bed_music(self, voice_client, guild_id):
        """Start playing ambient bed music under the DJ's voice.

        Uses a loopable ambient track. The bed music is played at lower volume
        and will be stopped by _stop_bed_music() when the actual song starts.
        """
        import os

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

            source = discord.FFmpegPCMAudio(
                bed_path,
                before_options="-nostdin -stream_loop -1",
                options="-vn",
            )
            player = discord.PCMVolumeTransformer(source)
            player.volume = self.current_volume.get(guild_id, 1.0) * 0.3  # 30% volume

            voice_client.play(
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
        guild = self.bot.get_guild(guild_id)
        if not guild or not guild.voice_client:
            self._bed_playing[guild_id] = False
            return
        if self._bed_playing.get(guild_id, False) and guild.voice_client.is_playing():
            guild.voice_client.stop()
            self._bed_playing[guild_id] = False
            logging.info(f"DJ Bed: Stopped for guild {guild_id}")

    @commands.Cog.listener()
    async def on_interaction(self, interaction):
        if interaction.type == discord.InteractionType.component:
            custom_id = interaction.data["custom_id"]
            logging.info(
                f"Interaction received: {custom_id} by {interaction.user} in {interaction.guild.name}"
            )
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


async def setup(bot):
    await bot.add_cog(Music(bot))
