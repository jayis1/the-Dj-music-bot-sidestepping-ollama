# ── Headless OBS Studio for Radio DJ Bot ─────────────────────────────────────
# Runs OBS Studio in a container with Xvfb virtual display, VNC for optional
# remote debugging, and obs-websocket 5.x for Mission Control integration.
#
# The bot auto-connects via OBS WebSocket to control scenes, streaming, and recording.
# OBS scene collections and settings persist in the obs_config Docker volume.
#
# Build:  docker build -t radio-dj-obs .
# Run:    See docker-compose.yml (OBS is a default service)
#
# Ports:
#   4455  — WebSocket server (Mission Control connects here)
#   5900  — VNC (optional remote desktop for debugging OBS UI)
#
# Only linux/amd64 is supported. OBS Studio + Chromium + VNC on arm64
# is not practical in Debian bookworm. If you need OBS on arm64, install
# OBS natively on the host and point OBS_WS_HOST to it in .env.
# ─────────────────────────────────────────────────────────────────────────────

FROM debian:bookworm-slim

# Build arguments (set by CI/CD — safe defaults for local builds)
ARG BUILD_DATE=dev
ARG VCS_REF=dev
ARG VERSION=dev

# Metadata — Open Container Initiative (OCI) standard labels
LABEL org.opencontainers.image.title="The Radio DJ Bot — OBS Studio"
LABEL org.opencontainers.image.description="Headless OBS Studio with obs-websocket for the Radio DJ Bot Docker stack"
LABEL org.opencontainers.image.source="https://github.com/jayis1/the-Dj-music-bot-sidestepping-ollama"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.version="${VERSION}"
LABEL org.opencontainers.image.revision="${VCS_REF}"
LABEL org.opencontainers.image.created="${BUILD_DATE}"

# ── Install OBS Studio + dependencies ─────────────────────────────────────
# OBS requires a display server (Xvfb), audio server (PipeWire/PulseAudio),
# and the obs-websocket plugin for remote control.
#
# Packages:
#   obs-studio          — OBS Studio (Debian bookworm includes 30.x with WebSocket 5.x)
#   obs-websocket       — OBS WebSocket 5.x plugin (bundled with OBS 28+,
#                         but installed separately on Debian for reliability)
#   xvfb                — Virtual X11 display server (OBS needs X11 even headless)
#   x11vnc              — VNC server for optional remote debugging of the OBS UI
#   pulseaudio-utils    — Audio utilities for OBS audio capture
#   ffmpeg              — For streaming output (OBS can use system FFmpeg)
#   dbus                — D-Bus session bus (required by PipeWire/PulseAudio)
#   pipewire            — Modern audio/video stream management
#   pipewire-pulse      — PulseAudio compatibility layer for PipeWire
#   curl                — Healthcheck
#   python3             — For OBS Python scripts and healthcheck
#   procps              — pgrep for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    obs-studio \
    xvfb \
    x11vnc \
    pulseaudio \
    pulseaudio-utils \
    ffmpeg \
    dbus \
    curl \
    python3 \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Try to install obs-websocket plugin separately (may not be in all repos)
# OBS 30.x on Debian bookworm may already include it built-in
RUN apt-get update && apt-get install -y --no-install-recommends \
    obs-websocket 2>/dev/null || echo "obs-websocket plugin: bundling varies by distro (may be built-in)" \
    && rm -rf /var/lib/apt/lists/*

# ── Create non-root user ─────────────────────────────────────────────────
RUN groupadd -r obs && useradd -r -g obs -d /home/obs -s /sbin/nologin obs

# ── Copy configuration ───────────────────────────────────────────────────
# Default OBS profile and scene collection for the Radio DJ Bot.
# These are baked into the image as defaults — if the volume mount has
# its own config, the user's config takes precedence.
COPY --chown=obs:obs config/ /home/obs/.config/obs-studio/

# ── Copy entrypoint script ───────────────────────────────────────────────
COPY --chown=root:root entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# ── Environment variables ────────────────────────────────────────────────
ENV DISPLAY=:99
ENV RESOLUTION=1280x720
ENV FPS=30
ENV OBS_WEBSOCKET_PASSWORD=djbot
ENV VNC_PASSWORD=djbot
ENV AUDIO_DRIVER=pulseaudio

# ── Ports ─────────────────────────────────────────────────────────────────
# 4455 — obs-websocket server (Mission Control connects here)
# 5900 — VNC server (optional remote desktop for debugging)
EXPOSE 4455 5900

# ── Volume for persistent config ──────────────────────────────────────────
# Mount this to persist OBS scene collections and settings across restarts.
VOLUME ["/home/obs/.config/obs-studio"]

# ── Health check ─────────────────────────────────────────────────────────
# Check that OBS process is running and the WebSocket port is listening.
# The WebSocket protocol doesn't respond to HTTP GET with 200 — it returns
# a 4xx/upgrade response. So we use a Python socket connect instead of curl.
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD pgrep -x obs > /dev/null && python3 -c "import socket; s=socket.socket(); s.settimeout(2); s.connect(('localhost',4455)); s.close()" || exit 1

# ── Start OBS ─────────────────────────────────────────────────────────────
USER obs
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]