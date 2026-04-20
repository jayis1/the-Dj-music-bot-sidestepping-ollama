# ── Radio DJ Music Bot — Dockerfile ──────────────────────────────────────────
# Multi-stage build for smaller final image.
# Stage 1: Install Python dependencies (cached layer)
# Stage 2: Runtime image with only what's needed
#
# The bot and web dashboard run in the same container on port 8080.
# Supports both linux/amd64 and linux/arm64.
#
# Build:  docker build -t radio-dj-bot .
# Run:    docker run -d -p 8080:8080 --env-file .env radio-dj-bot
# Compose: docker compose up -d   (includes MOSS TTS + Ollama)
#
# Build args:
#   BUILD_DATE  — ISO 8601 build timestamp (set by CI)
#   VCS_REF     — Git commit SHA (set by CI)
#   VERSION     — Bot version tag (defaults to dev)
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: Builder — install Python packages ──────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build dependencies for native extensions (PyNaCl, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libsodium-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies into a separate prefix for clean copy
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt && \
    pip install --no-cache-dir --prefix=/install --upgrade --pre yt-dlp 2>/dev/null || true

# ── Stage 2: Runtime — minimal production image ─────────────────────────────
FROM python:3.11-slim

# Build arguments (set by CI/CD — safe defaults for local builds)
ARG BUILD_DATE=dev
ARG VCS_REF=dev
ARG VERSION=dev
ARG TARGETARCH

# Metadata — Open Container Initiative (OCI) standard labels
LABEL org.opencontainers.image.title="The Radio DJ Music Bot"
LABEL org.opencontainers.image.description="Self-hosted Discord radio station bot with DJ voice, AI side host, soundboard, and web dashboard"
LABEL org.opencontainers.image.source="https://github.com/jayis1/the-Dj-music-bot-sidestepping-ollama"
LABEL org.opencontainers.image.licenses="MIT"
LABEL org.opencontainers.image.version="${VERSION}"
LABEL org.opencontainers.image.revision="${VCS_REF}"
LABEL org.opencontainers.image.created="${BUILD_DATE}"

# ── System dependencies ───────────────────────────────────────────────────
# Core packages that exist on both amd64 and arm64 in Debian trixie (13).
# python:3.11-slim is now based on Debian trixie (13) — these packages
# are all available in trixie main repos for both architectures.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    xvfb \
    curl \
    libopus-dev \
    libsodium23 \
    && rm -rf /var/lib/apt/lists/*

# ── Architecture-specific packages ─────────────────────────────────────────
# chromium: x86_64 only in trixie main. Not available on arm64.
# VA-API drivers: available on both architectures.
# Intel GPU drivers: x86_64 only.
#
# On arm64, chromium is NOT in trixie main repos.
# YouTube Live overlay mode requires chromium — on arm64 the overlay
# feature will be unavailable (the bot works for everything else).
RUN if [ "$(uname -m)" = "x86_64" ]; then \
      apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        mesa-va-drivers \
        i965-va-driver \
        intel-media-va-driver \
      && rm -rf /var/lib/apt/lists/*; \
    elif [ "$(uname -m)" = "aarch64" ]; then \
      apt-get update && apt-get install -y --no-install-recommends \
        mesa-va-drivers \
      && rm -rf /var/lib/apt/lists/*; \
      echo "NOTE: chromium not available on arm64 — YouTube Live overlay disabled"; \
    fi

# Create a non-root user for security
RUN groupadd -r radiodj && useradd -r -g radiodj -d /app -s /sbin/nologin radiodj

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Copy source code
COPY . .

# Ensure MOSS voice prompt files exist (shipped in assets/moss_voices/)
RUN mkdir -p /app/assets/moss_voices && \
    echo "MOSS voices: $(ls /app/assets/moss_voices/ 2>/dev/null | wc -l) files" && \
    if [ -f /app/assets/moss_voices/en_warm_female.wav ]; then \
      echo "MOSS default voice: OK"; \
    else \
      echo "WARNING: MOSS voice files missing — DJ will use demo fallback"; \
    fi

# Create persistent data directories and set ownership
RUN mkdir -p /app/sounds /app/presets /app/yt_dlp_cache && \
    chown -R radiodj:radiodj /app

# Switch to non-root user
USER radiodj

# Web dashboard port
EXPOSE 8080

# Health check — polls the dashboard
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8080/ || exit 1

CMD ["python3", "bot.py"]