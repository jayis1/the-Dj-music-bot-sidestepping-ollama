import socket
import threading
import time
import discord
import logging

log = logging.getLogger("broadcaster")


class PCMBroadcaster(discord.AudioSource):
    """
    Acts as the universal Master Audio Engine for the Bot.
    Intercepts the 20ms PCM audio chunks natively decoded by FFmpegPCMAudio,
    and universally routes them to a local UDP socket (127.0.0.1:12345).

    If Discord is connected, Discord's VoiceClient `read()` naturally pulses this matrix.
    If Discord is empty/disconnected, the internal `_autonomous_clock` automatically
    takes over and ensures the UDP pipe is fed perfectly seamlessly to maintain
    the headless YouTube Live broadcast!

    Thread safety:
        _source and _after_callback are protected by _source_lock.
        _is_discord_clocking uses an Event-based protocol so the autonomous
        clock thread and the Discord voice thread coordinate without races.
    """

    def __init__(self, port=12345):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Maximize the UDP send buffer for robust local delivery
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1048576)
        self.target = ("127.0.0.1", port)

        self._source = None
        self._source_lock = threading.Lock()

        self._running = True
        # Replaced bare bool with proper event-based coordination.
        # _discord_clock_event is set by read() when Discord is driving playback,
        # and cleared by the autonomous clock so it knows to yield.
        self._discord_clock_event = threading.Event()

        self._after_callback = None
        self._guild_id = None
        self._bot = None

        # _stop_event allows clean, responsive shutdown of the autonomous clock.
        self._stop_event = threading.Event()

        self._thread = threading.Thread(target=self._autonomous_clock, daemon=True)
        self._thread.start()
        log.info(
            f"PCMBroadcaster initialized: Streaming all audio outputs seamlessly to {self.target}"
        )

    def set_source(self, source, guild_id=None, bot=None, after=None):
        """Binds a new FFmpegPCMAudio stream (Song, TTS, SFX) into the broadcast matrix."""
        with self._source_lock:
            if self._source:
                # If we are abruptly swapping sources, trigger the previous callback first
                self._trigger_after()
                if hasattr(self._source, "cleanup"):
                    try:
                        self._source.cleanup()
                    except Exception:
                        pass

            self._source = source
            self._guild_id = guild_id
            self._bot = bot
            self._after_callback = after

    def stop_source(self):
        """Stops the current track gracefully and evokes the callback (used for skipping)."""
        with self._source_lock:
            if self._source:
                self._trigger_after()
                if hasattr(self._source, "cleanup"):
                    try:
                        self._source.cleanup()
                    except Exception:
                        pass
                self._source = None

    def _trigger_after(self, error=None):
        """Fires the after-play callback back onto the main asyncio loop."""
        cb = self._after_callback
        guild_id = self._guild_id
        bot = self._bot

        self._after_callback = None

        if cb and bot:
            try:
                bot.loop.call_soon_threadsafe(cb, error)
            except Exception as e:
                log.error(f"Broadcaster: Failed to trigger after-callback: {e}")

    def read(self) -> bytes:
        """Called automatically by Discord VoiceClient. Drives the clock native to the server.

        Signals the autonomous clock to stand down via _discord_clock_event
        so there is no race between the two clock threads.
        """
        self._discord_clock_event.set()
        data = b""
        source = None

        with self._source_lock:
            source = self._source

        if source:
            try:
                data = source.read()
            except Exception as e:
                log.error(f"Broadcaster read error: {e}")
                data = b""

            if not data:
                with self._source_lock:
                    if self._source is source:
                        self._trigger_after()
                        try:
                            self._source.cleanup()
                        except Exception:
                            pass
                        self._source = None

        payload = data if data else b"\x00" * 3840
        try:
            self.sock.sendto(payload, self.target)
        except BlockingIOError:
            pass
        return payload

    def stop(self):
        """Terminates the autonomous broadcast lock."""
        self._running = False
        self._stop_event.set()  # Wake the autonomous clock so it exits cleanly
        with self._source_lock:
            if self._source and hasattr(self._source, "cleanup"):
                try:
                    self._source.cleanup()
                except Exception:
                    pass
                self._source = None

    def _autonomous_clock(self):
        """The headless 24/7 pulse. Only activates when Discord drops its connection.

        Uses _discord_clock_event for thread-safe coordination with read():
        - When Discord is actively calling read(), the event is set and the
          autonomous clock idles (yielding CPU via a short timeout).
        - When Discord disconnects, the event stays clear and the autonomous
          clock takes over, feeding silence or audio to the UDP pipe at 20ms intervals.
        """
        silence = b"\x00" * 3840
        next_time = time.perf_counter()
        while self._running:
            # If Discord is driving playback, yield and wait.
            # Use a 100ms timeout so we re-check _running and the event state regularly.
            if self._discord_clock_event.is_set():
                # Discord is clocking — wait for it to stop or for shutdown.
                # Clear the event first so we don't spin, then wait briefly.
                self._discord_clock_event.clear()
                # Sleep a short interval and re-check.
                # _stop_event.wait() gives us clean shutdown responsiveness.
                stopped = self._stop_event.wait(timeout=0.1)
                if stopped or not self._running:
                    return
                next_time = time.perf_counter()
                continue

            data = b""
            source = None

            with self._source_lock:
                source = self._source

            if source:
                try:
                    data = source.read()
                except Exception:
                    data = b""

                if not data:
                    with self._source_lock:
                        if self._source is source:
                            self._trigger_after()
                            try:
                                self._source.cleanup()
                            except Exception:
                                pass
                            self._source = None

            payload = data if data else silence
            try:
                self.sock.sendto(payload, self.target)
            except Exception:
                pass

            next_time += 0.02
            delay = next_time - time.perf_counter()
            if delay > 0:
                # Use _stop_event.wait() instead of time.sleep() for
                # responsive shutdown — wakes immediately on stop().
                stopped = self._stop_event.wait(timeout=delay)
                if stopped:
                    return
            else:
                next_time = time.perf_counter()


class PCMBroadcasterWrapper:
    """Mock VoiceClient that transparently handles autonomous radio broadcasting
    without requiring a real Discord VoiceClient.

    Used when the bot runs in headless mode (no Discord connection) or when
    YouTube Live is streaming independently. Routes audio through the
    PCMBroadcaster subsystem to the UDP master pipeline.

    This class lives in utils/broadcaster.py so it can be imported from both
    cogs/music.py and web/app.py without circular imports.
    """

    def __init__(self, bot, guild_id, broadcaster):
        self.bot = bot
        self.guild_id = guild_id
        self.broadcaster = broadcaster
        self.source = None

    def is_playing(self):
        return self.source is not None

    def is_connected(self):
        return True

    def is_paused(self):
        return False

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
        self.broadcaster.set_source(
            source, guild_id=self.guild_id, bot=self.bot, after=after
        )
