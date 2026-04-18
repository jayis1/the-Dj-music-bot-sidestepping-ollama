#!/bin/bash
# ════════════════════════════════════════════════════════════════════════
#  The Radio DJ Bot — Proxmox LXC (Debian 12) Setup Script
# ════════════════════════════════════════════════════════════════════════
#
#  Run INSIDE a Debian 12 LXC container on Proxmox to set up
#  the Radio DJ Bot stack natively (no Docker — LXC shares the
#  host kernel so GPU access is direct).
#
#  PREREQUISITES on the PROXMOX HOST (run before creating the LXC):
#
#  1. Create a Debian 12 LXC:
#     - Template: debian-12-standard
#     - Unprivileged: Yes
#     - CPU: 2 cores minimum
#     - RAM: 2048 MB minimum (4096 MB if running Ollama)
#     - Disk: 20 GB minimum
#
#  2. Pass through GPU (AMD APU/iGPU):
#     Add to /etc/pve/lxc/<CTID>.conf:
#       lxc.cgroup2.devices.allow: c 226:0 rwm     # /dev/dri/card0
#       lxc.cgroup2.devices.allow: c 226:128 rwm   # /dev/dri/renderD128
#       lxc.cgroup2.devices.allow: c 235:0 rwm     # /dev/kfd (ROCm only)
#       lxc.mount.entry: /dev/dri dev/dri none bind,optional,create=dir
#       lxc.mount.entry: /dev/kfd dev/kfd none bind,optional,create=dir
#
#  3. Run this script inside the container:
#     bash setup-lxc.sh
#
# ════════════════════════════════════════════════════════════════════════

set -e

BOT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$BOT_DIR/venv"
VENV_PYTHON="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"
OBS_WS_CONFIG_DIR="$HOME/.config/obs-studio/plugin_config/obs-websocket"

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
    ffmpeg libopus-dev libsodium23 \
    curl wget git screen \
    mesa-va-drivers vainfo \
    obs-studio xvfb dbus pulseaudio pulseaudio-utils \
    chromium chromium-browser \
    2>/dev/null || warn "Some packages may not be available"

success "System packages installed"

# ══════════════════════════════════════════════════════════
#  STEP 2: GPU detection
# ══════════════════════════════════════════════════════════

echo ""
info "─── GPU Detection ─────────────────────────────────"

HAS_VAAPI=false
if [ -d "/dev/dri" ] && [ -e "/dev/dri/renderD128" ]; then
    info "/dev/dri found — GPU device nodes present"
    if command -v vainfo &>/dev/null && vainfo 2>/dev/null | grep -q "VAProfile"; then
        HAS_VAAPI=true
        success "VA-API: ✅ Working — hardware encoding available for YouTube Live"
    else
        warn "VA-API: No profiles found — may need firmware or mesa-va-drivers"
    fi
else
    warn "/dev/dri not found — no GPU passthrough"
    warn "YouTube Live will use software encoding (libx264, higher CPU)"
    warn "Add GPU passthrough in Proxmox LXC config to enable VA-API"
fi

# ══════════════════════════════════════════════════════════
#  STEP 3: Ollama (AI Side Host)
# ══════════════════════════════════════════════════════════

echo ""
info "─── Ollama (AI Side Host) ─────────────────────────"

if command -v ollama &>/dev/null; then
    success "Ollama already installed"
else
    info "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh 2>/dev/null || warn "Ollama install failed"
fi

if command -v ollama &>/dev/null; then
    if ! pgrep -x ollama &>/dev/null; then
        info "Starting Ollama server..."
        ollama serve &
        sleep 3
    fi
    MODEL="${OLLAMA_MODEL:-gemma3:4b}"
    info "Pulling AI side host model: ${MODEL} (may take a few minutes on first run)..."
    ollama pull "$MODEL" 2>/dev/null || warn "Model pull failed — pull manually: ollama pull $MODEL"
fi

# ══════════════════════════════════════════════════════════
#  STEP 4: OBS Studio (headless, auto-configured)
# ══════════════════════════════════════════════════════════

echo ""
info "─── OBS Studio (Headless) ────────────────────────"

# Generate OBS WebSocket password
OBS_WS_PASSWORD=""
if [ -f "$BOT_DIR/.env" ]; then
    OBS_WS_PASSWORD=$(grep -E "^OBS_WS_PASSWORD=" "$BOT_DIR/.env" 2>/dev/null | cut -d= -f2 | tr -d '"' | tr -d "'")
fi

if [ -z "$OBS_WS_PASSWORD" ] || [ "$OBS_WS_PASSWORD" = "your_obs_password" ]; then
    OBS_WS_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))" 2>/dev/null \
        || openssl rand -hex 16 2>/dev/null || echo "djbot-$(date +%s)")
    info "Generated OBS WebSocket password: ${OBS_WS_PASSWORD}"
fi

# Configure obs-websocket
mkdir -p "$OBS_WS_CONFIG_DIR"
cat > "$OBS_WS_CONFIG_DIR/config.json" << EOF
{
    "server_enabled": true,
    "server_port": 4455,
    "server_password": "${OBS_WS_PASSWORD}",
    "server_password_enabled": true,
    "alerts_enabled": false
}
EOF

OBS_GLOBAL_CONFIG="$HOME/.config/obs-studio/global.ini"
mkdir -p "$(dirname "$OBS_GLOBAL_CONFIG")"
cat > "$OBS_GLOBAL_CONFIG" <<EOF
[General]
Pre197TagsInUse=true
[OBSWebSocket]
ServerEnabled=true
ServerPort=4455
AuthRequired=true
ServerPassword=${OBS_WS_PASSWORD}
EOF
success "obs-websocket configured (port 4455)"

# Copy default scene collection
OBS_SCENES_DIR="$HOME/.config/obs-studio/basic/scenes"
OBS_PROFILES_DIR="$HOME/.config/obs-studio/basic/profiles"
mkdir -p "$OBS_SCENES_DIR" "$OBS_PROFILES_DIR/RadioDJ"

if [ -f "$BOT_DIR/obs-studio/config/obs-studio/basic/scenes/Radio DJ.json" ]; then
    cp "$BOT_DIR/obs-studio/config/obs-studio/basic/scenes/Radio DJ.json" "$OBS_SCENES_DIR/Radio DJ.json"
    success "Installed 'Radio DJ' scene collection"
fi
if [ -f "$BOT_DIR/obs-studio/config/obs-studio/basic/profiles/RadioDJ/basic.ini" ]; then
    cp "$BOT_DIR/obs-studio/config/obs-studio/basic/profiles/RadioDJ/basic.ini" "$OBS_PROFILES_DIR/RadioDJ/basic.ini"
    success "Installed 'RadioDJ' OBS profile"
fi

# Start headless OBS
if pgrep -x obs &>/dev/null; then
    success "OBS already running"
else
    info "Starting headless OBS via xvfb-run..."
    if ! pgrep -x dbus-daemon &>/dev/null; then
        eval $(dbus-launch --sh-syntax 2>/dev/null) || true
        export DBUS_SESSION_BUS_ADDRESS
    fi
    if ! pgrep -x pulseaudio &>/dev/null; then
        pulseaudio --start --fail=false --daemonize=true \
            --load="module-null-sink sink_name=radio_dj_sink sink_properties=device.description='Radio_DJ_Audio'" \
            2>/dev/null || true
        sleep 1
        pactl set-default-sink radio_dj_sink 2>/dev/null || true
    fi
    xvfb-run -a obs --minimize-to-tray --disable-shutdown-check --collection "Radio DJ" &
    sleep 3
    if pgrep -x obs &>/dev/null; then
        success "OBS Studio started headless"
    else
        warn "OBS may not have started. Try: xvfb-run -a obs &"
    fi
fi

# ══════════════════════════════════════════════════════════
#  STEP 5: Python virtual environment + bot
# ══════════════════════════════════════════════════════════

echo ""
info "─── Bot Python Environment ────────────────────────"

if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR" || error "Failed to create venv"
    success "Virtual environment created"
fi

info "Installing Python packages..."
"$VENV_PIP" install --upgrade pip -q
"$VENV_PIP" install -r "$BOT_DIR/requirements.txt" -q
"$VENV_PIP" install --upgrade --pre yt-dlp -q 2>/dev/null || true
success "Python packages installed"

touch "$BOT_DIR/cogs/__init__.py" "$BOT_DIR/utils/__init__.py" 2>/dev/null || true
mkdir -p "$BOT_DIR/yt_dlp_cache" "$BOT_DIR/sounds" "$BOT_DIR/presets" "$BOT_DIR/assets/moss_voices"

# ══════════════════════════════════════════════════════════
#  STEP 6: .env configuration
# ══════════════════════════════════════════════════════════

echo ""
info "─── Configuration ────────────────────────────────"

if [ ! -f "$BOT_DIR/.env" ]; then
    cp "$BOT_DIR/.env.example" "$BOT_DIR/.env"
    warn ".env created from .env.example — you MUST set DISCORD_TOKEN"
fi

# Write OBS password to .env
if grep -q "^OBS_WS_PASSWORD=" "$BOT_DIR/.env"; then
    sed -i "s|^OBS_WS_PASSWORD=.*|OBS_WS_PASSWORD=${OBS_WS_PASSWORD}|" "$BOT_DIR/.env"
else
    echo "OBS_WS_PASSWORD=${OBS_WS_PASSWORD}" >> "$BOT_DIR/.env"
fi
sed -i 's/^OBS_WS_ENABLED=.*/OBS_WS_ENABLED=true/' "$BOT_DIR/.env" 2>/dev/null || echo "OBS_WS_ENABLED=true" >> "$BOT_DIR/.env"
success "OBS config written to .env"

# Auto-detect VA-API
if [ "$HAS_VAAPI" = true ]; then
    if ! grep -q "^AMD_GPU_VAAPI=" "$BOT_DIR/.env"; then
        echo "AMD_GPU_VAAPI=1" >> "$BOT_DIR/.env"
    else
        sed -i 's/^AMD_GPU_VAAPI=.*/AMD_GPU_VAAPI=1/' "$BOT_DIR/.env"
    fi
    success "AMD_GPU_VAAPI=1 set in .env"
fi

# ══════════════════════════════════════════════════════════
#  STEP 7: Systemd services
# ══════════════════════════════════════════════════════════

echo ""
info "─── Systemd Services ──────────────────────────────"

# Bot service
if [ ! -f "/etc/systemd/system/radio-dj-bot.service" ]; then
    cat > "/etc/systemd/system/radio-dj-bot.service" << EOF
[Unit]
Description=The Radio DJ Bot — Discord Radio Station
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${BOT_DIR}
ExecStart=${VENV_PYTHON} ${BOT_DIR}/bot.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal
EnvironmentFile=${BOT_DIR}/.env

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable radio-dj-bot 2>/dev/null || true
    success "Bot systemd service created"
fi

# OBS headless service
if [ ! -f "/etc/systemd/system/obs-headless.service" ]; then
    cat > "/etc/systemd/system/obs-headless.service" << EOF
[Unit]
Description=Headless OBS Studio for Radio DJ Bot
After=network-online.target pulseaudio.service
Wants=network-online.target

[Service]
Type=simple
Environment=DISPLAY=:99
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/0/bus
ExecStartPre=/bin/sh -c 'pulseaudio --start --fail=false --daemonize=true --load="module-null-sink sink_name=radio_dj_sink" 2>/dev/null || true'
ExecStartPre=/bin/sh -c 'Xvfb :99 -screen 0 1280x720x24 -ac +extension GLX +render -noreset &'
ExecStartPre=/bin/sleep 2
ExecStart=xvfb-run -a obs --minimize-to-tray --disable-shutdown-check --collection "Radio DJ"
Restart=on-failure
RestartSec=15
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable obs-headless 2>/dev/null || true
    success "OBS headless systemd service created"
fi

# ══════════════════════════════════════════════════════════
#  STEP 8: Final summary
# ══════════════════════════════════════════════════════════

echo ""
echo -e "${BOLD}${CYAN}═══════════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}${GREEN}  ✅ Setup Complete!${RESET}"
echo -e "${BOLD}${CYAN}═══════════════════════════════════════════════════════${RESET}"
echo ""
echo -e "  ${BOLD}1.${RESET} Set your Discord token:"
echo -e "     nano ${BOT_DIR}/.env"
echo ""
echo -e "  ${BOLD}2.${RESET} Start everything:"
echo -e "     systemctl start obs-headless radio-dj-bot"
echo ""
echo -e "  ${BOLD}3.${RESET} Check logs:"
echo -e "     journalctl -u radio-dj-bot -f"
echo -e "     journalctl -u obs-headless -f"
echo ""
echo -e "  ${BOLD}4.${RESET} Open Mission Control:"
echo -e "     http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo 'your-ip'):8080"
echo ""
echo -e "  ${CYAN}─── Or run manually ──────────────────────────${RESET}"
echo -e "  cd ${BOT_DIR}"
echo -e "  bash start.sh           # Full setup + foreground run"
echo ""