# ── MOSS-TTS-Nano Server — Dockerfile ──────────────────────────────────────
# Runs the MOSS-TTS-Nano FastAPI server for voice cloning TTS.
# 0.1B params, runs on CPU (no GPU needed), 48 kHz stereo output.
# See: https://github.com/OpenMOSS/MOSS-TTS-Nano
# ──────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /MOSS-TTS-Nano

# Clone the MOSS-TTS-Nano repository
RUN git clone https://github.com/OpenMOSS/MOSS-TTS-Nano.git .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir -e .

# Create directories
RUN mkdir -p /app/assets/moss_voices

# Expose the MOSS-TTS-Nano server port
EXPOSE 18083

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 \
    CMD curl -f http://localhost:18083/health || exit 1

# Start the MOSS-TTS-Nano server on CPU
CMD ["moss-tts-nano", "serve", "--host", "0.0.0.0", "--port", "18083"]