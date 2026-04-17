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
    """
    def __init__(self, port=12345):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Maximize the UDP send buffer for robust local delivery
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1048576)
        self.target = ("127.0.0.1", port)
        
        self._source = None
        self._source_lock = threading.Lock()
        
        self._running = True
        self._is_discord_clocking = False
        
        self._after_callback = None
        self._guild_id = None
        self._bot = None
        
        self._thread = threading.Thread(target=self._autonomous_clock, daemon=True)
        self._thread.start()
        log.info(f"PCMBroadcaster initialized: Streaming all audio outputs seamlessly to {self.target}")
        
    def set_source(self, source, guild_id=None, bot=None, after=None):
        """Binds a new FFmpegPCMAudio stream (Song, TTS, SFX) into the broadcast matrix."""
        with self._source_lock:
            if self._source:
                # If we are abruptly swapping sources, trigger the previous callback first
                self._trigger_after()
                if hasattr(self._source, 'cleanup'):
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
                if hasattr(self._source, 'cleanup'):
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
        """Called automatically by Discord VoiceClient. Drives the clock native to the server."""
        self._is_discord_clocking = True
        data = b''
        source = None
        
        with self._source_lock:
            source = self._source
            
        if source:
            try:
                data = source.read()
            except Exception as e:
                log.error(f"Broadcaster read error: {e}")
                data = b''
            
            if not data:
                with self._source_lock:
                    if self._source is source:
                        self._trigger_after()
                        try:
                            self._source.cleanup()
                        except Exception:
                            pass
                        self._source = None
                        
        payload = data if data else b'\x00' * 3840
        try:
            self.sock.sendto(payload, self.target)
        except BlockingIOError:
            pass
        return payload
        
    def stop(self):
        """Terminates the autonomous broadcast lock."""
        self._running = False
        with self._source_lock:
            if self._source and hasattr(self._source, 'cleanup'):
                try:
                    self._source.cleanup()
                except Exception:
                    pass
                self._source = None
                
    def _autonomous_clock(self):
        """The headless 24/7 pulse. Only activates when Discord drops its connection."""
        silence = b'\x00' * 3840
        next_time = time.perf_counter()
        while self._running:
            if self._is_discord_clocking:
                time.sleep(0.1)
                self._is_discord_clocking = False
                next_time = time.perf_counter()
                continue
                
            data = b''
            source = None
            
            with self._source_lock:
                source = self._source
                
            if source:
                try:
                    data = source.read()
                except Exception:
                    data = b''
                    
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
                time.sleep(delay)
            else:
                next_time = time.perf_counter()
