# ── MOSS-TTS-Nano Server — Dockerfile ──────────────────────────────────────
# Runs the MOSS-TTS-Nano FastAPI server for voice cloning TTS.
# 0.1B params, runs on CPU (no GPU needed), 48 kHz stereo output.
# See: https://github.com/OpenMOSS/MOSS-TTS-Nano
#
# Multi-stage build:
#   Stage 1 (builder): Clone + install all Python deps into a prefix
#   Stage 2 (runtime): Slim image with only runtime deps + installed packages
#
# NOTE: We use a regular (non-editable) pip install so that all package
# files are copied into /install and then into the runtime image. An editable
# install (-e) would create .egg-link files pointing to /build/moss which
# doesn't exist in the runtime stage, causing ModuleNotFoundError.
# ──────────────────────────────────────────────────────────────────────────

# ── Stage 1: Builder ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Clone MOSS-TTS-Nano (with retry — git clone can flake in CI)
RUN for i in 1 2 3; do git clone https://github.com/OpenMOSS/MOSS-TTS-Nano.git /build/moss && break || (echo "Clone attempt $i failed, retrying..." && sleep 5); done

# Install Python dependencies + the package itself into a separate prefix.
# IMPORTANT: Do NOT use -e (editable) install! It creates .egg-link files
# that reference /build/moss, which doesn't exist in the runtime image.
# Regular install copies all package files into /install/site-packages.
WORKDIR /build/moss
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt && \
    pip install --no-cache-dir --prefix=/install .

# ── Stage 2: Runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim

LABEL org.opencontainers.image.title="MOSS-TTS-Nano Server"
LABEL org.opencontainers.image.description="Lightweight TTS server with voice cloning — CPU-only, 48kHz stereo, 0.1B params"
LABEL org.opencontainers.image.source="https://github.com/OpenMOSS/MOSS-TTS-Nano"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL org.opencontainers.image.version="v420.0.3"

# Runtime dependencies only (no build tools)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /MOSS-TTS-Nano

# Copy installed Python packages from builder (includes moss_tts_nano)
COPY --from=builder /install /usr/local

# Copy MOSS-TTS-Nano source for assets, configs, and model weights
COPY --from=builder /build/moss /MOSS-TTS-Nano

# Create voice prompt directory
RUN mkdir -p /app/assets/moss_voices && \
    mkdir -p /MOSS-TTS-Nano/assets/moss_voices

EXPOSE 18083

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 \
    CMD curl -f http://localhost:18083/health || exit 1

# Start the MOSS-TTS-Nano server on CPU
CMD ["moss-tts-nano", "serve", "--host", "0.0.0.0", "--port", "18083"]