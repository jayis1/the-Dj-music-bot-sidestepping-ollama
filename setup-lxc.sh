#!/bin/bash
# ════════════════════════════════════════════════════════════════════════
#  The Radio DJ Bot — Proxmox LXC (Debian 12) Setup Script
# ════════════════════════════════════════════════════════════════════════
#
#  This script runs INSIDE a Debian 12 LXC container on Proxmox to set up
#  the Radio DJ Bot stack natively (no Docker needed — LXC shares the
#  host kernel so GPU access is direct).
#
#  PREREQUISITES on the PROXMOX HOST (run before creating the LXC):
#
#  1. Create a Debian 12 LXC:
#     - Template: debian-12-standard
#     - Unprivileged: Yes (recommended)
#     - CPU: 2 cores minimum
#     - RAM: 2048 MB minimum (4096 MB if running Ollama)
#     - Disk: 20 GB minimum
#     - DHCP or static IP
#
#  2. Pass through GPU (AMD APU/iGPU):
#     Add to the LXC config (/etc/pve/lxc/<CTID>.conf):
#
#       # AMD GPU passthrough (for VA-API encoding + optional ROCm)
#       lxc.cgroup2.devices.allow: c 226:0 rwm    # /dev/dri/card0
#       lxc.cgroup2.devices.allow: c 226:128 rwm  # /dev/dri/renderD128
#       lxc.cgroup2.devices.allow: c 235:0 rwm    # /dev/kfd (ROCm only)
#       lxc.mount.entry: /dev/dri dev/dri none bind,optional,create=dir
#       lxc.mount.entry: /dev/kfd dev/kfd none bind,optional,create=dir
#
#     Then: pct stop <CTID> && pct start <CTID>
#
#  3. Run this script inside the container:
#     curl -sL https://raw.githubusercontent.com/jayis1/the-Dj-music-bot-sidestepping-ollama/main/setup-lxc.sh | bash
#
#     Or: Clone the repo and run directly:
#       git clone https://github.com/jayis1/the-Dj-music-bot-sidestepping-ollama.git
#       cd the-Dj-music-bot-sidestepping-ollama
#       bash setup-lxc.sh
#
# ════════════════════════════════════════════════════════════════════════

set -e

BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$BOT_DIR/venv"
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

BOLD="\e[1m"
GREEN="\e[32m"
CYAN="\e[36m"
YELLOW="\e[33m"
RED="\e[31m"
RESET="\e[0m"

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*"; exit 1; }

echo ""
echo -e "${BOLD}${CYAN}  ╔═══════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${CYAN}  ║   🏴‍☠️ The Radio DJ Bot — LXC Setup          ║${RESET}"
echo -e "${BOLD}${CYAN}  ║   Debian 12 · Proxmox · AMD GPU             ║${RESET}"
echo -e "${BOLD}${CYAN}  ╚═══════════════════════════════════════════════╝${RESET}"
echo ""

# ══════════════════════════════════════════════════════════
#  STEP 1: System packages
# ══════════════════════════════════════════════════════════

info "Updating system packages..."
apt-get update -qq

info "Installing system dependencies..."
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    ffmpeg \
    libopus-dev libsodium23 \
    curl wget git screen \
    mesa-va-drivers vainfo \
    xvfb x11vbc \
    dbus pulseaudio pulseaudio-utils \
    2>/dev/null || warn "Some packages may not be available (non-fatal)"

# ══════════════════════════════════════════════════════════
#  STEP 2: GPU detection
# ══════════════════════════════════════════════════════════

echo ""
info "─── GPU Detection ─────────────────────────────────"

HAS_GPU=false
HAS_VAAPI=false
HAS_ROCM=false

if [ -d "/dev/dri" ]; then
    info "/dev/dri found — GPU device nodes present"
    ls -la /dev/dri/ 2>/dev/null || true

    # Check VA-API
    if command -v vainfo &>/dev/null; then
        if vainfo 2>/dev/null | grep -q "VAProfile"; then
            HAS_VAAPI=true
            success "VA-API: ✅ Working — hardware encoding available for YouTube Live"
            vainfo 2>/dev/null | head -10
        else
            warn "VA-API: vainfo ran but no profiles found (may need firmware)"
        fi
    else
        warn "VA-API: vainfo not installed — install: apt install vainfo"
    fi

    # Check for render node
    if [ -e "/dev/dri/renderD128" ]; then
        success "DRI render node: /dev/dri/renderD128 — FFmpeg h264_vaapi will work"
        HAS_GPU=true
    fi

    # Check ROCm (for Ollama GPU inference)
    if [ -e "/dev/kfd" ]; then
        info "ROCm: /dev/kfd found — GPU compute available"
        HAS_ROCM=true

        # Detect GPU architecture
        GPU_ARCH="unknown"
        if [ -f "/sys/class/drm/card0/device/gpu_id" ]; then
            GPU_ARCH=$(cat /sys/class/drm/card0/device/gpu_id 2>/dev/null || echo "unknown")
        fi
        if [ "$GPU_ARCH" = "unknown" ]; then
            # Try from card1 if card0 is empty
            for card in /sys/class/drm/card*/device/gpu_id; do
                [ -f "$card" ] && GPU_ARCH=$(cat "$card" 2>/dev/null | head -1) && break
            done
        fi
        info "GPU architecture: ${GPU_ARCH}"

        #gfx90c check (Ryzen 5700U, 5800H, etc. — VA-API works but ROCm doesn't natively)
        if echo "$GPU_ARCH" | grep -qi "gfx90c"; then
            warn "gfx90c detected — VA-API encoding works, but ROCm/LLM falls back to CPU"
            warn "Ollama will use CPU on this GPU — this is fine, it's fast enough"
            HAS_ROCM=false  # gfx90c isn't truly ROCm-capable for compute
        fi
    else
        info "ROCm: /dev/kfd not found — no GPU compute (Ollama will use CPU)"
    fi
else
    warn "/dev/dri not found — no GPU passthrough"
    warn "YouTube Live will use software encoding (libx264, higher CPU usage)"
    warn "Add GPU passthrough in Proxmox LXC config to enable VA-API"
fi

# ══════════════════════════════════════════════════════════
#  STEP 3: Ollama (AI Side Host)
# ══════════════════════════════════════════════════════════

echo ""
info "─── Ollama (AI Side Host) ─────────────────────────"

if command -v ollama &>/dev/null; then
    success "Ollama already installed: $(ollama --version 2>/dev/null || echo 'installed')"
else
    info "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh 2>/dev/null || {
        warn "Ollama auto-install failed. Install manually:"
        warn "  curl -fsSL https://ollama.com/install.sh | sh"
        warn "  Or download from: https://ollama.com/download/linux"
    }
fi

if command -v ollama &>/dev/null; then
    # Start Ollama service
    if ! pgrep -x ollama &>/dev/null; then
        info "Starting Ollama server..."
        ollama serve &
        sleep 3
    fi

    # Pull recommended model
    MODEL="${OLLAMA_MODEL:-gemma3:4b}"
    info "Pulling AI side host model: ${MODEL}"
    info "(This downloads ~2.5 GB on first run — skip with Ctrl+C if you want a different model)"
    ollama pull "$MODEL" 2>/dev/null || warn "Model pull failed — you can pull manually: ollama pull $MODEL"
    success "Ollama ready with model: ${MODEL}"
else
    warn "Ollama not installed — AI Side Host will be disabled"
    warn "Set OLLAMA_DJ_ENABLED=false in .env to silence warnings"
fi

# ══════════════════════════════════════════════════════════
#  STEP 4: MOSS-TTS-Nano (DJ Voice)
# ══════════════════════════════════════════════════════════

echo ""
info "─── MOSS-TTS-Nano (DJ Voice) ──────────────────────"

MOSS_RUNNING=false
if curl -sf http://localhost:18083/health &>/dev/null; then
    MOSS_RUNNING=true
    success "MOSS-TTS-Nano server already running on port 18083"
else
    # Try installing and starting
    if [ -d "$BOT_DIR/moss-tts-server" ]; then
        info "Installing MOSS-TTS-Nano from local source..."
        pip install moss-tts-nano 2>/dev/null || warn "pip install failed — trying with venv later"
    else
        info "Installing MOSS-TTS-Nano..."
        "$VENV_PIP" install moss-tts-nano 2>/dev/null || pip install moss-tts-nano 2>/dev/null || warn "MOSS install failed — will use Edge TTS fallback"
    fi
fi

# ══════════════════════════════════════════════════════════
#  STEP 5: OBS Studio (optional)
# ══════════════════════════════════════════════════════════

echo ""
info "─── OBS Studio (Optional) ────────────────────────"

if command -v obs &>/dev/null; then
    success "OBS Studio already installed"
else
    info "OBS Studio is optional (for YouTube Live scene switching)"
    info "To install: apt install obs-studio"
    info "Or skip — the bot works fine without OBS"
fi

# ══════════════════════════════════════════════════════════
#  STEP 6: Python virtual environment + bot setup
# ══════════════════════════════════════════════════════════

echo ""
info "─── Bot Python Environment ────────────────────────"

if [ ! -d "$VENV_DIR" ]; then
    info "Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR" || error "Failed to create venv. Install: apt install python3-venv"
    success "Virtual environment created"
fi

info "Installing Python packages..."
"$VENV_PIP" install --upgrade pip -q
"$VENV_PIP" install -r "$BOT_DIR/requirements.txt" -q
"$VENV_PIP" install --upgrade --pre yt-dlp -q 2>/dev/null || true
success "Python packages installed"

# Init project structure
touch "$BOT_DIR/cogs/__init__.py" "$BOT_DIR/utils/__init__.py" 2>/dev/null || true
mkdir -p "$BOT_DIR/yt_dlp_cache" "$BOT_DIR/sounds" "$BOT_DIR/presets" "$BOT_DIR/assets/moss_voices"
success "Project structure OK"

# ══════════════════════════════════════════════════════════
#  STEP 7: .env configuration
# ══════════════════════════════════════════════════════════

echo ""
info "─── Configuration ────────────────────────────────"

if [ ! -f "$BOT_DIR/.env" ]; then
    cp "$BOT_DIR/.env.example" "$BOT_DIR/.env"
    warn ".env created from .env.example — you MUST set DISCORD_TOKEN"
fi

# Auto-detect and set GPU flags
if [ "$HAS_VAAPI" = true ]; then
    # Add AMD_GPU_VAAPI to .env if not already there
    if ! grep -q "^AMD_GPU_VAAPI=" "$BOT_DIR/.env"; then
        echo "" >> "$BOT_DIR/.env"
        echo "# Auto-detected by setup-lxc.sh" >> "$BOT_DIR/.env"
        echo "AMD_GPU_VAAPI=1" >> "$BOT_DIR/.env"
        success "Added AMD_GPU_VAAPI=1 to .env (VA-API hardware encoding)"
    else
        sed -i 's/^AMD_GPU_VAAPI=.*/AMD_GPU_VAAPI=1/' "$BOT_DIR/.env"
        success "Set AMD_GPU_VAAPI=1 in .env"
    fi
fi

# ══════════════════════════════════════════════════════════
#  STEP 8: Systemd service (auto-start on boot)
# ══════════════════════════════════════════════════════════

echo ""
info "─── Systemd Service ──────────────────────────────"

SERVICE_NAME="radio-dj-bot"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

if [ -f "$SERVICE_FILE" ]; then
    success "Systemd service already exists at ${SERVICE_FILE}"
else
    cat > "$SERVICE_FILE" << EOF
[Unit]
Description=The Radio DJ Bot — Discord Radio Station
After=network-online.target pulseaudio.service
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${BOT_DIR}
ExecStart=${VENV_PYTHON} ${BOT_DIR}/bot.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

# GPU access (LXC passthrough)
DeviceAllow=/dev/dri/card0 rwm
DeviceAllow=/dev/dri/renderD128 rwm
DeviceAllow=/dev/kfd rwm

# Environment
EnvironmentFile=${BOT_DIR}/.env
Environment=AMD_GPU_VAAPI=${HAS_VAAPI:+1}

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME" 2>/dev/null || warn "Could not enable systemd service"
    success "Systemd service created: ${SERVICE_NAME}"
    info "Start with:  systemctl start ${SERVICE_NAME}"
    info "Logs:        journalctl -u ${SERVICE_NAME} -f"
fi

# ══════════════════════════════════════════════════════════
#  STEP 9: MOSS-TTS systemd service
# ══════════════════════════════════════════════════════════

if [ "$MOSS_RUNNING" = false ] && command -v moss-tts-nano &>/dev/null; then
    MOSS_SERVICE="moss-tts"
    MOSS_SERVICE_FILE="/etc/systemd/system/${MOSS_SERVICE}.service"

    if [ ! -f "$MOSS_SERVICE_FILE" ]; then
        cat > "$MOSS_SERVICE_FILE" << EOF
[Unit]
Description=MOSS-TTS-Nano Voice Cloning Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${BOT_DIR}
ExecStart=${VENV_PYTHON} -m moss_tts_nano.serve --host 0.0.0.0 --port 18083 --device auto
Restart=on-failure
RestartSec=15
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

        systemctl daemon-reload
        systemctl enable "$MOSS_SERVICE" 2>/dev/null || warn "Could not enable MOSS service"
        success "MOSS-TTS systemd service created"
        info "Start with:  systemctl start ${MOSS_SERVICE}"
    fi
fi

# ══════════════════════════════════════════════════════════
#  STEP 10: Final summary
# ══════════════════════════════════════════════════════════

echo ""
echo -e "${BOLD}${CYAN}═══════════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}${GREEN}  ✅ Setup Complete!${RESET}"
echo -e "${BOLD}${CYAN}═══════════════════════════════════════════════════════${RESET}"
echo ""
echo -e "  ${CYAN}Next steps:${RESET}"
echo ""
echo -e "  ${BOLD}1.${RESET} Set your Discord token:"
echo -e "     nano ${BOT_DIR}/.env"
echo -e "     ${YELLOW}(set DISCORD_TOKEN=your_token_here)${RESET}"
echo ""
echo -e "  ${BOLD}2.${RESET} Start the bot:"
echo -e "     systemctl start radio-dj-bot"
echo ""
echo -e "  ${BOLD}3.${RESET} Start MOSS TTS (if not using Docker):"
echo -e "     systemctl start moss-tts"
echo ""
echo -e "  ${BOLD}4.${RESET} Check logs:"
echo -e "     journalctl -u radio-dj-bot -f"
echo ""
echo -e "  ${BOLD}5.${RESET} Open Mission Control:"
echo -e "     http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'your-server-ip'):8080"
echo ""

if [ "$HAS_VAAPI" = true ]; then
    echo -e "  ${GREEN}🎬 VA-API hardware encoding: ENABLED${RESET}"
    echo -e "     YouTube Live will use h264_vaapi (low CPU usage)"
elif [ "$HAS_GPU" = true ]; then
    echo -e "  ${YELLOW}🎬 GPU detected but VA-API not working${RESET}"
    echo -e "     YouTube Live will use libx264 (software encoding, higher CPU)"
    echo -e "     Install mesa-va-drivers: apt install mesa-va-drivers vainfo"
else
    echo -e "  ${YELLOW}🎬 No GPU detected${RESET}"
    echo -e "     YouTube Live will use libx264 (software encoding)"
    echo -e "     Add GPU passthrough in Proxmox LXC config for VA-API"
fi

echo ""
echo -e "  ${CYAN}─── Service Management ───────────────────────${RESET}"
echo -e "  Start all:   systemctl start radio-dj-bot moss-tts"
echo -e "  Stop all:    systemctl stop radio-dj-bot moss-tts"
echo -e "  Bot logs:    journalctl -u radio-dj-bot -f"
echo -e "  MOSS logs:   journalctl -u moss-tts -f"
echo -e "  Auto-start:  systemctl enable radio-dj-bot moss-tts"
echo ""
echo -e "  ${CYAN}─── Or run manually (foreground mode) ──────${RESET}"
echo -e "  cd ${BOT_DIR}"
echo -e "  bash start.sh           # Interactive setup + foreground run"
echo -e "  bash start.sh start     # Background screen session"
echo ""