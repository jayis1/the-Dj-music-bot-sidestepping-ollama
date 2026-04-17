# ── Radio DJ Music Bot — Dockerfile ──────────────────────────────────────────
# Python 3.11 slim + ffmpeg + all bot dependencies.
# The bot and web dashboard run in the same container on port 8080.
#
# Build:  docker build -t radio-dj-bot .
# Run:    docker run -d -p 8080:8080 --env-file .env radio-dj-bot
# Compose: docker compose up -d   (includes Kokoro TTS)
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

# Metadata
LABEL org.opencontainers.image.title="The Radio DJ Music Bot"
LABEL org.opencontainers.image.description="Self-hosted Discord radio station bot with DJ voice, AI side host, soundboard, and web dashboard"
LABEL org.opencontainers.image.source="https://github.com/jayis1/the-Dj-music-bot-sidestepping-ollama"
LABEL org.opencontainers.image.licenses="MIT"

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    xvfb \
    chromium \
    libopus-dev \
    libffi-dev \
    libsodium-dev \
    build-essential \
    python3-dev \
    git \
    curl \
    nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir --upgrade --pre yt-dlp 2>/dev/null || true

# Copy source code
COPY . .

# Ensure MOSS voice prompt files exist (Docker COPY may miss empty dirs)
RUN mkdir -p /app/assets/moss_voices && \
    if [ -d /app/default_assets/moss_voices ]; then \
      cp -n /app/default_assets/moss_voices/*.wav /app/assets/moss_voices/ 2>/dev/null || true; \
    fi && \
    echo "MOSS voices: $(ls /app/assets/moss_voices/ 2>/dev/null | wc -l) files" && \
    if [ -f /app/assets/moss_voices/en_warm_female.wav ]; then \
      echo "MOSS default voice: OK"; \
    else \
      echo "WARNING: MOSS voice files missing — DJ will use demo fallback"; \
    fi

# Create persistent data directories (these should be mounted as volumes)
RUN mkdir -p sounds presets yt_dlp_cache && \
    cp -r sounds default_sounds 2>/dev/null || true && \
    cp -r presets default_presets 2>/dev/null || true

# Web dashboard port
EXPOSE 8080

# Health check — polls the dashboard
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD curl -f http://localhost:8080/ || exit 1

CMD ["python3", "bot.py"]
