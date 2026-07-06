"""Discord Go Live / screen-share video streaming.

Implements video streaming into Discord voice channels by:
1. Monkey-patching Guild.change_voice_state to send self_stream=true
2. Capturing the OBS Xvfb display via FFmpeg (libvpx VP8)
3. Sending encrypted RTP video packets via the voice UDP socket

Discord's video protocol uses:
  - VP8 or H.264 video codec (we use VP8 for compatibility)
  - Separate video_ssrc (returned in voice READY payload)
  - RTP packet structure similar to audio but with video payload
  - Same encryption as audio (xsalsa20_poly1305 / aead_xchacha20)
  - Payload type 96 for video (vs 120 for audio in newer protocol)

The video SSRC and encryption key are the same as audio — Discord
reuses the audio secret key for video. We send VP8 keyframes and
delta frames as RTP packets with the video SSRC.

NOTE: This is a reverse-engineered implementation. Discord's video
protocol is undocumented and may change. The voice gateway sends
`video_ssrc` in the READY payload when `self_stream=true` is set
in the VOICE_STATE payload.

Usage:
    from utils.discord_video import DiscordVideoStreamer
    streamer = DiscordVideoStreamer(voice_client, display=":420")
    await streamer.start()
    # ... later
    await streamer.stop()
"""

import asyncio
import logging
import struct
import subprocess
import os
from typing import Optional

log = logging.getLogger("discord-video")

# ── Monkey-patch Guild.change_voice_state to add self_stream=true ─────────
# discord.py 2.7.1 does not support self_stream in the VOICE_STATE payload.
# We patch it to include self_stream=true so Discord assigns a video_ssrc.
_PATCHED = False


def _patch_voice_state():
    """Patch Guild.change_voice_state to send self_stream=true."""
    global _PATCHED
    if _PATCHED:
        return
    import discord

    original_change_voice_state = discord.Guild.change_voice_state

    async def patched_change_voice_state(self, *, channel, self_mute=False, self_deaf=False):
        ws = self._state._get_websocket(self.id)
        channel_id = channel.id if channel else None
        payload = {
            "op": ws.VOICE_STATE,
            "d": {
                "guild_id": self.id,
                "channel_id": channel_id,
                "self_mute": self_mute,
                "self_deaf": self_deaf,
                "self_stream": True,  # Request Go Live / screen share
            },
        }
        await ws.send_as_json(payload)

    discord.Guild.change_voice_state = patched_change_voice_state
    _PATCHED = True
    log.info("Patched Guild.change_voice_state to include self_stream=true")


# ── Video SSRC extraction from voice READY payload ───────────────────────
# The voice READY (opcode 2) payload includes video_ssrc when streaming is
# requested. We hook into the voice websocket's received_message to capture it.


def _patch_voice_ws_ready():
    """Patch the voice websocket to extract video_ssrc from READY."""
    import discord
    from discord.gateway import DiscordVoiceWebSocket

    original_initial_connection = DiscordVoiceWebSocket.initial_connection

    async def patched_initial_connection(self, data):
        state = self._connection
        state.ssrc = data["ssrc"]
        state.voice_port = data["port"]
        state.endpoint_ip = data["ip"]

        # Extract video_ssrc — Discord sends this when self_stream=true
        video_ssrc = data.get("video_ssrc", 0)
        if video_ssrc:
            state.video_ssrc = video_ssrc
            log.info(f"Video SSRC received: {video_ssrc}")
        else:
            log.warning("No video_ssrc in voice READY — self_stream may not be active")

        log.debug("Connecting to voice socket")
        await self.loop.sock_connect(state.socket, (state.endpoint_ip, state.voice_port))

        state.ip, state.port = await self.discover_ip()
        modes = [mode for mode in data["modes"] if mode in self._connection.supported_modes]
        log.debug("received supported encryption modes: %s", ", ".join(modes))

        mode = modes[0]
        await self.select_protocol(state.ip, state.port, mode)
        log.debug("selected the voice protocol for use (%s)", mode)

    DiscordVoiceWebSocket.initial_connection = patched_initial_connection
    log.info("Patched DiscordVoiceWebSocket.initial_connection for video_ssrc extraction")


def _ensure_video_ssrc_attr():
    """Ensure VoiceConnectionState has a video_ssrc attribute."""
    import discord
    from discord.voice_state import VoiceConnectionState

    if not hasattr(VoiceConnectionState, "video_ssrc"):
        # Add a default video_ssrc attribute to VoiceConnectionState
        VoiceConnectionState.video_ssrc = 0


class DiscordVideoStreamer:
    """Streams video from an Xvfb display into a Discord voice channel.

    Captures the specified X11 display using FFmpeg, encodes it as VP8,
    and sends encrypted RTP video packets via the voice client's UDP socket.

    Args:
        voice_client: The connected discord.VoiceClient instance
        display: X11 display to capture (e.g. ":420")
        width: Capture width
        height: Capture height
        framerate: Target framerate
        bitrate: Video bitrate in kbps
    """

    def __init__(
        self,
        voice_client,
        display: str = ":420",
        width: int = 1280,
        height: int = 720,
        framerate: int = 30,
        bitrate: int = 2500,
    ):
        self.voice_client = voice_client
        self.display = display
        self.width = width
        self.height = height
        self.framerate = framerate
        self.bitrate = bitrate

        self._process: Optional[subprocess.Process] = None
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._video_ssrc: int = 0
        self._sequence: int = 0
        self._timestamp: int = 0

    @property
    def is_running(self) -> bool:
        return self._running

    def _get_video_packet(self, data: bytes) -> bytes:
        """Build an encrypted RTP video packet.

        Uses the same encryption as audio (the secret key is shared).
        Payload type is 96 for VP8 video (vs 120/0x78 for audio).
        """
        vc = self.voice_client
        header = bytearray(12)

        # RTP header
        header[0] = 0x80  # Version 2, no padding, no extension, no CSRC
        header[1] = 0x60  # Payload type 96 (VP8), marker bit NOT set
        # Actually: VP8 payload type in Discord is typically 96.
        # The marker bit (0x80) should be set on the last packet of a frame.
        # For simplicity, we set the marker bit on every packet (each packet
        # is a complete frame at low resolution).
        header[1] = 0xE0  # Marker bit set + payload type 96

        struct.pack_into(">H", header, 2, self._sequence & 0xFFFF)
        struct.pack_into(">I", header, 4, self._timestamp)
        struct.pack_into(">I", header, 8, self._video_ssrc)

        # Use the same encryption as audio
        encrypt_packet = getattr(vc, "_encrypt_" + vc.mode)
        return encrypt_packet(header, data)

    async def _send_video_rtp(self, frame_data: bytes):
        """Send a single VP8 frame as an RTP packet."""
        if not self.voice_client or not self.voice_client.is_connected():
            return
        if not self._video_ssrc:
            log.warning("No video_ssrc — cannot send video")
            return

        # Discord expects VP8 RTP with a 1-byte payload descriptor prefix
        # See RFC 7741 for VP8 RTP payload format
        # Byte 0: Required VP8 payload descriptor
        #   X=0, R=0, N=0, S=1 (start of frame), PartID=0
        # Since we send one packet per frame, S=1 always
        vp8_descriptor = bytes([0x10])  # X=0, N=0, S=1, PartID=0

        payload = vp8_descriptor + frame_data

        packet = self._get_video_packet(payload)
        try:
            self.voice_client._connection.send_packet(packet)
        except OSError:
            log.debug("Video packet dropped (seq: %s, ts: %s)", self._sequence, self._timestamp)

        self._sequence = (self._sequence + 1) & 0xFFFF
        # Video timestamp: 90kHz clock (vs 48kHz for audio)
        # Each frame at 30fps = 90000/30 = 3000 ticks per frame
        self._timestamp = (self._timestamp + 3000) & 0xFFFFFFFF

    async def _capture_loop(self):
        """Capture the Xvfb display via FFmpeg and send VP8 frames."""
        env = os.environ.copy()
        env["DISPLAY"] = self.display

        # FFmpeg command: capture X11 display, encode as VP8, output raw
        # to stdout. We use VP8 with a low latency preset.
        cmd = [
            "ffmpeg",
            "-y",
            "-f", "x11grab",
            "-framerate", str(self.framerate),
            "-video_size", f"{self.width}x{self.height}",
            "-i", self.display,
            "-c:v", "libvpx",
            "-b:v", f"{self.bitrate}k",
            "-deadline", "realtime",
            "-cpu-used", "5",  # Fastest encoding
            "-error-resilient", "1",
            "-auto-alt-ref", "0",  # Disable alt-ref for low latency
            "-lag-in-frames", "0",
            "-f", "rtp",  # Output RTP format
            "-payload_type", "96",
            "pipe:1",
        ]

        log.info(f"Starting FFmpeg video capture: {' '.join(cmd)}")

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            self._running = True

            # Read RTP packets from FFmpeg stdout
            # FFmpeg RTP output gives us full RTP packets — but we need to
            # strip the RTP header and re-wrap with our own SSRC/encryption.
            # Actually, better approach: output raw VP8 and build RTP ourselves.
            # Let's use -f webm and parse VP8 frames, or just pipe raw VP8.
            # Simplest: use -f image2pipe with VP8 frames.

        except Exception as e:
            log.error(f"Failed to start FFmpeg: {e}")
            self._running = False
            return

        # Actually, the cleanest approach is to get raw VP8 frames from FFmpeg.
        # We use -f webm with -cluster_time_limit for streaming, but parsing
        # WebM is complex. Instead, let's use a different approach:
        # Use FFmpeg to output IVF (simple VP8 frame container) to stdout.

        # Kill the first process and restart with IVF output
        if self._process:
            self._process.kill()
            await self._process.wait()

        cmd2 = [
            "ffmpeg",
            "-y",
            "-f", "x11grab",
            "-framerate", str(self.framerate),
            "-video_size", f"{self.width}x{self.height}",
            "-i", self.display,
            "-c:v", "libvpx",
            "-b:v", f"{self.bitrate}k",
            "-deadline", "realtime",
            "-cpu-used", "5",
            "-error-resilient", "1",
            "-auto-alt-ref", "0",
            "-lag-in-frames", "0",
            "-f", "ivf",  # IVF: simple container with VP8 frame headers
            "pipe:1",
        ]

        log.info(f"Starting FFmpeg IVF capture: {' '.join(cmd)}")

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd2,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except Exception as e:
            log.error(f"Failed to start FFmpeg IVF: {e}")
            self._running = False
            return

        # Start stderr drain
        asyncio.ensure_future(self._drain_stderr())

        # Read IVF header (32 bytes)
        try:
            header = await self._process.stdout.readexactly(32)
            log.info(f"IVF header read ({len(header)} bytes)")
        except asyncio.IncompleteReadError:
            log.error("Failed to read IVF header")
            self._running = False
            return

        # Read frames
        frame_count = 0
        while self._running and self._process and self._process.returncode is None:
            try:
                # IVF frame header: 12 bytes (4 size + 8 timestamp)
                frame_header = await self._process.stdout.readexactly(12)
                frame_size = struct.unpack_from("<I", frame_header, 0)[0]
                # timestamp = struct.unpack_from("<Q", frame_header, 4)[0]

                if frame_size > 1000000:  # Sanity check
                    log.warning(f"Frame size too large: {frame_size}, skipping")
                    await self._process.stdout.readexactly(min(frame_size, 1000000))
                    continue

                frame_data = await self._process.stdout.readexactly(frame_size)
                await self._send_video_rtp(frame_data)
                frame_count += 1

                if frame_count % 300 == 0:
                    log.info(f"Sent {frame_count} video frames (ssrc={self._video_ssrc})")

            except asyncio.IncompleteReadError:
                log.info("FFmpeg stdout ended")
                break
            except Exception as e:
                log.error(f"Video capture loop error: {e}")
                break

        self._running = False
        log.info(f"Video capture loop ended (sent {frame_count} frames)")

    async def _drain_stderr(self):
        """Drain FFmpeg stderr to prevent pipe blocking."""
        if not self._process or not self._process.stderr:
            return
        while self._process.returncode is None:
            line = await self._process.stderr.readline()
            if not line:
                break
            log.debug(f"ffmpeg: {line.decode('utf-8', errors='replace').rstrip()}")

    async def start(self):
        """Start video streaming. Must be called after voice client connects.

        Patches should already be applied in on_ready. If video_ssrc is 0,
        the voice client needs to reconnect with self_stream=true (do
        ?leave then ?join then ?govideo).
        """
        # Wait for voice client to be connected
        if not self.voice_client or not self.voice_client.is_connected():
            log.error("Voice client not connected — cannot start video")
            return False

        # Get video_ssrc from connection state
        conn = self.voice_client._connection
        self._video_ssrc = getattr(conn, "video_ssrc", 0)

        if not self._video_ssrc:
            log.warning("No video_ssrc available — voice client must reconnect with self_stream=true")
            log.warning("Do ?leave then ?join then ?govideo to negotiate video")
            return False

        log.info(f"Starting video stream (ssrc={self._video_ssrc}, display={self.display})")
        self._task = asyncio.ensure_future(self._capture_loop())
        return True

    async def stop(self):
        """Stop video streaming."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        if self._process and self._process.returncode is None:
            self._process.kill()
            await self._process.wait()
            self._process = None

        log.info("Video stream stopped")