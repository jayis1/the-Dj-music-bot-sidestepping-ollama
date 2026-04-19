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

        # ── Audio Level Metering (for OBS beat-pulse visualizer) ──
        # Tracks a beat-pulse level that jumps on transients and decays fast.
        # Updated on every read() / autonomous clock tick (every 20ms).
        # This creates a pulsing bar that reacts to drum hits and bass,
        # not a slow VU meter.
        self._audio_level = 0.0          # Current beat-pulse level (0.0–1.0)
        self._audio_level_peak = 0.0     # Peak hold
        self._audio_level_lock = threading.Lock()

        # Beat detection state:
        # _energy_short  = RMS of the current 20ms chunk (instantaneous)
        # _energy_long   = rolling average of recent chunks (background level)
        # When short >> long, a beat (transient) is detected.
        self._energy_long = 0.0          # Long-term energy average

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

    @staticmethod
    def _compute_rms(pcm_data: bytes) -> float:
        """Compute RMS (root-mean-square) audio level from s16le PCM data.

        Returns a float in 0.0–1.0 range where 1.0 = 0 dBFS (maximum).
        This is used by the OBS audio visualizer bar to show audio activity.

        The PCM data is signed 16-bit little-endian stereo:
          - 3840 bytes = 960 samples × 2 channels × 2 bytes/sample
          - Each sample is an int16 in the range -32768 to 32767
        """
        if not pcm_data or len(pcm_data) < 4:
            return 0.0

        # Number of int16 samples
        n_samples = len(pcm_data) // 2
        if n_samples == 0:
            return 0.0

        # Unpack all samples at once (fast C-level operation via array module)
        try:
            import array
            samples = array.array('h')
            samples.frombytes(pcm_data[:n_samples * 2])
            total = sum(s * s for s in samples)
        except Exception:
            # Fallback: struct.unpack for odd-length data
            import struct as _struct
            total = 0.0
            for i in range(0, len(pcm_data) - 1, 2):
                sample = _struct.unpack_from('<h', pcm_data, i)[0]
                total += sample * sample

        # RMS = sqrt(mean(squared_samples)) / 32768.0
        rms = (total / n_samples) ** 0.5
        # Normalize to 0.0–1.0 (32768 is the max s16le value)
        return min(1.0, rms / 32768.0)

    def get_audio_level(self) -> float:
        """Get the current smoothed audio level (0.0–1.0).

        This is thread-safe and can be called from any thread/task
        (e.g., OBS visualizer polling loop).
        The value decays smoothly when audio stops.
        """
        with self._audio_level_lock:
            return self._audio_level

    def get_audio_level_peak(self) -> float:
        """Get the current peak audio level (0.0–1.0) with hold."""
        with self._audio_level_lock:
            return self._audio_level_peak

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

        # Update audio level metering for OBS visualizer
        self._update_audio_level(payload)

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

    def _update_audio_level(self, pcm_data: bytes):
        """Update the beat-pulse audio level from a PCM chunk.

        Creates a pulsing visualizer bar that reacts to beats/transients
        (drums, bass hits), NOT a smooth VU meter.

        Algorithm:
          1. Compute short-term RMS energy of the 20ms chunk
          2. Maintain a long-term energy average (exponential moving average)
          3. If short-term energy > long-term average × threshold → BEAT detected
             → bar jumps to peak instantly (attack = 1.0)
          4. Bar decays fast between beats (factor 0.65 per tick ≈ 15ms drop)
             → creates the "pulse" shape: sharp rise, fast fall
          5. Small silence-floor: when no audio at all, bar rests at ~0.02
             instead of 0.0 so the bar is always faintly visible.

        The result: during music, the bar pulses with the kick drum / beat.
        During silence or quiet passages, the bar drops to minimum quickly.
        During speech (DJ), the bar moves with syllable emphasis.
        """
        rms = self._compute_rms(pcm_data)

        with self._audio_level_lock:
            # Update long-term energy average (slow-moving background)
            # α = 0.01 → very slow adaptation, takes ~100 ticks (2 sec) to settle
            self._energy_long = self._energy_long * 0.99 + rms * 0.01

            # Beat detection threshold: short-term energy must exceed
            # long-term average by this multiplier to count as a beat.
            # 1.3x = mild beat (speech sibilance), 1.5x = clear beat (kick),
            # 2.0x+ = strong transient (snare hit).
            # We use a dynamic threshold that adapts to the song's loudness.
            beat_threshold = max(self._energy_long * 1.4, 0.03)

            if rms > beat_threshold:
                # BEAT DETECTED → instant attack to peak
                # Scale the bar height by how hard the beat hits:
                # A quiet beat → 0.4-0.6, medium → 0.6-0.8, slam → 0.9-1.0
                intensity = min(1.0, rms / max(beat_threshold, 0.001))
                target = min(1.0, 0.3 + intensity * 0.7)  # 0.3 min on beat, 1.0 max
                # Instant attack — bar snaps to target immediately
                self._audio_level = target
            else:
                # NO BEAT → fast exponential decay
                # 0.65 per 20ms tick → bar drops to ~12% in 100ms, ~1% in 200ms
                # This creates the "pulse" shape: sharp rise on beat, fast drop
                self._audio_level *= 0.65
                # If very quiet (near silence), drop faster
                if rms < 0.01:
                    self._audio_level *= 0.4
                # Minimum floor so bar is always faintly visible
                if self._audio_level < 0.02:
                    self._audio_level = 0.02

            # Peak hold with faster decay than before
            if rms > self._audio_level_peak:
                self._audio_level_peak = rms
            else:
                self._audio_level_peak *= 0.90

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

            # Update audio level metering for OBS visualizer
            self._update_audio_level(payload)

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


# ══════════════════════════════════════════════════════════════════════════
# Module-level Broadcaster Registry
# ══════════════════════════════════════════════════════════════════════════
# The PCMBroadcaster is typically created once and shared across the app.
# The music cog (and web app) register their broadcaster instance here so
# that the OBS visualizer polling loop can read audio levels without needing
# a direct reference to the cog or Discord voice client.

_registered_broadcaster: PCMBroadcaster | None = None


def register_broadcaster(broadcaster: PCMBroadcaster):
    """Register the global PCMBroadcaster instance for audio level readout.

    Called once by the music cog when it creates the broadcaster.
    The OBS visualizer reads from this to update the audio level bar.
    """
    global _registered_broadcaster
    _registered_broadcaster = broadcaster
    log.info("PCMBroadcaster: Registered global instance for audio level readout")


def get_broadcaster() -> PCMBroadcaster | None:
    """Get the registered global PCMBroadcaster instance (or None)."""
    return _registered_broadcaster


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
