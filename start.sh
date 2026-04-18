#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  The Radio DJ Bot v420.0.3 — Self-Setup Launcher
#
#  Usage:
#    bash start.sh          → setup + run (first time or re-run)
#    bash start.sh start    → start in background (screen)
#    bash start.sh stop     → stop background session + OBS
#    bash start.sh restart  → restart background session
#    bash start.sh logs     → view live logs
#    bash start.sh setup    → only run setup, don't start
# ═══════════════════════════════════════════════════════════════════

set -e
cd "$(dirname "$0")"

BOT_DIR="$(pwd)"
SESSION_NAME="mbot"
OBS_SESSION="obs-headless"
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

banner() {
  echo ""
  echo -e "${BOLD}${CYAN}  ╔════════════════════════════════════════════╗${RESET}"
  echo -e "${BOLD}${CYAN}  ║  🏴‍☠️ The Radio DJ Bot v420.0.3            ║${RESET}"
  echo -e "${BOLD}${CYAN}  ╚════════════════════════════════════════════╝${RESET}"
  echo ""
}

info()    { echo -e "${CYAN}[INFO]${RESET}  $*"; }
success() { echo -e "${GREEN}[OK]${RESET}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*"; exit 1; }

# ─── STEP 1: System Dependencies ──────────────────────────────────
install_system_deps() {
  info "Checking system dependencies..."

  # Try to use sudo if available
  SUDO=""
  if command -v sudo &>/dev/null && sudo -n true 2>/dev/null; then
    SUDO="sudo"
  elif [ "$(id -u)" -eq 0 ]; then
    SUDO=""
  fi

  # Python3
  if ! command -v python3 &>/dev/null; then
    info "Installing Python3..."
    $SUDO apt-get update -qq && $SUDO apt-get install -y -qq python3 python3-pip python3-venv || \
      error "Could not install Python3. Please install it manually."
  fi

  # pip / venv
  if ! python3 -m pip --version &>/dev/null 2>&1; then
    $SUDO apt-get install -y -qq python3-pip python3-venv 2>/dev/null || true
  fi

  # ffmpeg
  if ! command -v ffmpeg &>/dev/null; then
    info "Installing ffmpeg..."
    $SUDO apt-get update -qq && $SUDO apt-get install -y -qq ffmpeg || \
      warn "Could not auto-install ffmpeg. Install manually: sudo apt install ffmpeg"
  fi

  # libopus (required for discord.py voice)
  if ! ldconfig -p 2>/dev/null | grep -q libopus && ! dpkg -s libopus-dev &>/dev/null 2>&1; then
    info "Installing libopus-dev..."
    $SUDO apt-get install -y -qq libopus-dev 2>/dev/null || true
  fi

  # screen (for background mode)
  if ! command -v screen &>/dev/null; then
    info "Installing screen..."
    $SUDO apt-get install -y -qq screen 2>/dev/null || warn "screen not installed. Background mode won't work."
  fi

  success "System dependencies OK"
}

# ─── STEP 2: Python Virtual Environment ───────────────────────────
setup_venv() {
  if [ ! -d "$VENV_DIR" ]; then
    info "Creating Python virtual environment..."
    python3 -m venv "$VENV_DIR" || error "Failed to create venv. Install: sudo apt install python3-venv"
    success "Virtual environment created"
  fi

  info "Installing/upgrading Python packages..."
  "$VENV_PIP" install --upgrade pip -q
  "$VENV_PIP" install -r requirements.txt -q
  "$VENV_PIP" install --upgrade yt-dlp -q
  "$VENV_PIP" install --upgrade --pre yt-dlp -q 2>/dev/null || true

  # DJ Mode dependency — edge-tts (optional but recommended for radio DJ voice)
  if "$VENV_PYTHON" -c "import edge_tts" 2>/dev/null; then
    success "edge-tts installed (DJ mode available)"
  else
    info "Installing edge-tts for DJ mode..."
    "$VENV_PIP" install edge-tts -q 2>/dev/null
    if "$VENV_PYTHON" -c "import edge_tts" 2>/dev/null; then
      success "edge-tts installed — DJ mode is available"
    else
      warn "Could not install edge-tts. DJ mode will be unavailable."
    fi
  fi

  success "Python packages installed"
}

# ─── STEP 3: .env Setup Wizard ────────────────────────────────────
setup_env() {
  if [ -f ".env" ]; then
    current_token=$(grep -E "^DISCORD_TOKEN=" .env | cut -d= -f2 | tr -d '"')
    if [ -n "$current_token" ] && [ "$current_token" != "your_discord_bot_token" ]; then
      success ".env already configured, skipping wizard."
      return
    fi
  fi

  echo ""
  echo -e "${BOLD}${YELLOW}══════════════════════════════════════════${RESET}"
  echo -e "${BOLD}  🔧 First-Time Configuration Wizard${RESET}"
  echo -e "${BOLD}${YELLOW}══════════════════════════════════════════${RESET}"
  echo ""
  echo -e "  You'll need the following before continuing:"
  echo -e "  ${CYAN}1.${RESET} Discord Bot Token  → discord.com/developers"
  echo -e "  ${CYAN}2.${RESET} YouTube API Key     → console.cloud.google.com"
  echo -e "  ${CYAN}3.${RESET} A Discord channel ID for bot logs (optional)"
  echo ""

  read -rp "$(echo -e "${BOLD}  Discord Bot Token:${RESET} ")" DISCORD_TOKEN
  [ -z "$DISCORD_TOKEN" ] && error "Discord token cannot be empty."

  read -rp "$(echo -e "${BOLD}  YouTube API Key (press Enter to skip):${RESET} ")" YOUTUBE_API_KEY
  YOUTUBE_API_KEY="${YOUTUBE_API_KEY:-}"

  read -rp "$(echo -e "${BOLD}  Log Channel ID (press Enter to skip):${RESET} ")" LOG_CHANNEL_ID
  LOG_CHANNEL_ID="${LOG_CHANNEL_ID:-0}"

  read -rp "$(echo -e "${BOLD}  Radio Station Name (press Enter for MBot):${RESET} ")" STATION_NAME
  STATION_NAME="${STATION_NAME:-MBot}"

  read -rp "$(echo -e "${BOLD}  Web Dashboard Port (press Enter for 8080):${RESET} ")" WEB_PORT
  WEB_PORT="${WEB_PORT:-8080}"

  cat > .env <<EOF
DISCORD_TOKEN="${DISCORD_TOKEN}"
YOUTUBE_API_KEY="${YOUTUBE_API_KEY}"
LOG_CHANNEL_ID="${LOG_CHANNEL_ID}"
STATION_NAME="${STATION_NAME}"
WEB_PORT="${WEB_PORT}"
EOF

  success ".env file created successfully!"
  echo ""
}

# ─── STEP 4: OBS Studio Auto-Setup ─────────────────────────────────
# Installs OBS Studio, configures obs-websocket with a password,
# writes that password to .env, copies default scene collection,
# and starts headless OBS via xvfb-run.
setup_obs() {
  echo ""
  info "─── OBS Studio Setup ────────────────────────────"

  SUDO=""
  if [ "$(id -u)" -eq 0 ]; then SUDO=""; elif command -v sudo &>/dev/null; then SUDO="sudo"; fi

  # ── Install OBS Studio ──────────────────────────────────
  OBS_INSTALLED=false
  if command -v obs &>/dev/null; then
    OBS_INSTALLED=true
    success "OBS Studio already installed"
  fi

  if [ "$OBS_INSTALLED" = false ]; then
    info "Installing OBS Studio (v29.x with WebSocket 5.x)..."
    $SUDO apt-get update -qq 2>/dev/null
    if $SUDO apt-get install -y -qq obs-studio 2>/dev/null; then
      OBS_INSTALLED=true
      success "OBS Studio installed"
    else
      warn "Could not install OBS Studio automatically."
      warn "Install manually:  sudo apt update && sudo apt install obs-studio"
      warn "OBS is optional — the bot works without it."
    fi
  fi

  # ── Install headless support packages ──────────────────
  if [ "$OBS_INSTALLED" = true ]; then
    for pkg in xvfb dbus pulseaudio pulseaudio-utils chromium; do
      if ! dpkg -s "$pkg" &>/dev/null 2>&1; then
        info "Installing $pkg for headless OBS + YouTube Live overlay..."
        $SUDO apt-get install -y -qq "$pkg" 2>/dev/null || warn "$pkg install failed"
      fi
    done
    # chromium-browser is an alternative package name on some distros
    if ! command -v chromium &>/dev/null && ! command -v chromium-browser &>/dev/null; then
      $SUDO apt-get install -y -qq chromium-browser 2>/dev/null || warn "chromium-browser not found either — YouTube Live overlay will not work"
    fi
  fi

  # ── Generate or read OBS WebSocket password ────────────
  OBS_WS_PASSWORD=""
  if [ -f ".env" ]; then
    OBS_WS_PASSWORD=$(grep -E "^OBS_WS_PASSWORD=" .env 2>/dev/null | cut -d= -f2 | tr -d '"' | tr -d "'")
  fi

  if [ -z "$OBS_WS_PASSWORD" ] || [ "$OBS_WS_PASSWORD" = "your_obs_password" ]; then
    # Generate a random password
    OBS_WS_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(16))" 2>/dev/null \
      || openssl rand -hex 16 2>/dev/null \
      || echo "djbot-$(date +%s)")
    info "Generated OBS WebSocket password: ${OBS_WS_PASSWORD}"

    # Write to .env
    if [ -f ".env" ]; then
      if grep -q "^OBS_WS_PASSWORD=" .env; then
        sed -i "s|^OBS_WS_PASSWORD=.*|OBS_WS_PASSWORD=${OBS_WS_PASSWORD}|" .env
      else
        echo "" >> .env
        echo "# Auto-generated by start.sh" >> .env
        echo "OBS_WS_PASSWORD=${OBS_WS_PASSWORD}" >> .env
      fi
      success "OBS_WS_PASSWORD written to .env"
    fi
  else
    success "OBS_WS_PASSWORD already set in .env"
  fi

  # Ensure OBS_WS_ENABLED=true in .env
  if [ -f ".env" ]; then
    if ! grep -q "^OBS_WS_ENABLED=" .env; then
      echo "OBS_WS_ENABLED=true" >> .env
    else
      sed -i 's/^OBS_WS_ENABLED=.*/OBS_WS_ENABLED=true/' .env
    fi
  fi

  # ── Configure obs-websocket 5.x ──────────────────────
  # The WebSocket config is read by OBS on startup.
  # Two config locations needed for different OBS versions:
  info "Configuring obs-websocket 5.x..."
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

  # Also write global.ini (some OBS versions read this instead)
  OBS_GLOBAL_CONFIG="$HOME/.config/obs-studio/global.ini"
  mkdir -p "$(dirname "$OBS_GLOBAL_CONFIG")"
  if [ ! -f "$OBS_GLOBAL_CONFIG" ] || ! grep -q "OBSWebSocket" "$OBS_GLOBAL_CONFIG"; then
    cat > "$OBS_GLOBAL_CONFIG" <<EOF
[General]
Pre197TagsInUse=true
[OBSWebSocket]
ServerEnabled=true
ServerPort=4455
AuthRequired=true
ServerPassword=${OBS_WS_PASSWORD}
EOF
    success "OBS global.ini configured"
  else
    # Update password in existing config
    if grep -q "ServerPassword=" "$OBS_GLOBAL_CONFIG"; then
      sed -i "s|^ServerPassword=.*|ServerPassword=${OBS_WS_PASSWORD}|" "$OBS_GLOBAL_CONFIG"
    fi
  fi
  success "obs-websocket configured (port 4455, password set)"

  # ── Copy default scene collection ──────────────────────
  OBS_SCENES_DIR="$HOME/.config/obs-studio/basic/scenes"
  OBS_PROFILES_DIR="$HOME/.config/obs-studio/basic/profiles"
  mkdir -p "$OBS_SCENES_DIR" "$OBS_PROFILES_DIR/RadioDJ"

  if [ ! -f "$OBS_SCENES_DIR/Radio DJ.json" ] && [ -f "$BOT_DIR/obs-studio/config/obs-studio/basic/scenes/Radio DJ.json" ]; then
    cp "$BOT_DIR/obs-studio/config/obs-studio/basic/scenes/Radio DJ.json" "$OBS_SCENES_DIR/Radio DJ.json"
    success "Installed 'Radio DJ' scene collection (4 scenes)"
  fi
  if [ ! -f "$OBS_PROFILES_DIR/RadioDJ/basic.ini" ] && [ -f "$BOT_DIR/obs-studio/config/obs-studio/basic/profiles/RadioDJ/basic.ini" ]; then
    cp "$BOT_DIR/obs-studio/config/obs-studio/basic/profiles/RadioDJ/basic.ini" "$OBS_PROFILES_DIR/RadioDJ/basic.ini"
    success "Installed 'RadioDJ' OBS profile"
  fi

  # ── Start headless OBS via xvfb-run ─────────────────────
  if [ "$OBS_INSTALLED" = true ]; then
    if pgrep -x obs &>/dev/null; then
      success "OBS Studio already running (PID: $(pgrep -x obs | head -1))"
    else
      info "Starting headless OBS Studio..."

      # Start D-Bus (OBS needs it)
      if ! pgrep -x dbus-daemon &>/dev/null; then
        eval $(dbus-launch --sh-syntax 2>/dev/null) || true
        export DBUS_SESSION_BUS_ADDRESS
      fi

      # Start PulseAudio with null sink for virtual audio
      if ! pgrep -x pulseaudio &>/dev/null; then
        pulseaudio --start --fail=false --daemonize=true \
          --load="module-null-sink sink_name=radio_dj_sink sink_properties=device.description='Radio_DJ_Audio'" \
          2>/dev/null || true
        sleep 1
        pactl set-default-sink radio_dj_sink 2>/dev/null || true
      fi

      # Start OBS headless via xvfb-run (handles Xvfb lifecycle automatically)
      xvfb-run -a obs --minimize-to-tray --disable-shutdown-check --collection "Radio DJ" &
      OBS_PID=$!
      sleep 3

      if kill -0 $OBS_PID 2>/dev/null; then
        success "OBS Studio started headless (PID: $OBS_PID)"
        info "WebSocket: ws://localhost:4455  (password: ${OBS_WS_PASSWORD:0:4}***)"
        info "Mission Control will auto-connect to OBS"
      else
        warn "OBS may not have started. Try: xvfb-run -a obs &"
      fi
    fi
  fi

  info "─── OBS Setup Complete ──────────────────────────"
}

# ─── STEP 5: Init directories & __init__.py files ─────────────────
init_project() {
  touch cogs/__init__.py utils/__init__.py 2>/dev/null || true
  mkdir -p yt_dlp_cache sounds presets assets/moss_voices
  success "Project structure OK"
}

# ─── Bot Control ──────────────────────────────────────────────────
start_foreground() {
  info "Starting bot in foreground (Ctrl+C to stop)..."
  echo ""
  exec "$VENV_PYTHON" bot.py
}

start_background() {
  if screen -list 2>/dev/null | grep -q "$SESSION_NAME"; then
    warn "Bot is already running. Use 'bash start.sh restart' or 'bash start.sh logs'."
    exit 0
  fi
  info "Starting bot in background screen session '${SESSION_NAME}'..."
  screen -dmS "$SESSION_NAME" bash -c "cd '$BOT_DIR' && '$VENV_PYTHON' bot.py 2>&1 | tee -a bot.log"
  sleep 1
  if screen -list 2>/dev/null | grep -q "$SESSION_NAME"; then
    success "Bot is running in the background!"
    echo -e "  ${CYAN}→ View logs:${RESET}  bash start.sh logs"
    echo -e "  ${CYAN}→ Stop bot:${RESET}   bash start.sh stop"
  else
    warn "Bot may have crashed. Check logs: bash start.sh logs"
  fi
}

stop_bot() {
  # Stop bot screen session
  if screen -list 2>/dev/null | grep -q "$SESSION_NAME"; then
    screen -S "$SESSION_NAME" -X quit
    success "Bot stopped."
  else
    warn "Bot is not currently running."
  fi

  # Stop headless OBS (if we started it)
  if pgrep -x obs &>/dev/null; then
    info "Stopping headless OBS..."
    pkill -x obs 2>/dev/null || true
    sleep 1
    if pgrep -x obs &>/dev/null; then
      pkill -9 -x obs 2>/dev/null || true
    fi
    success "OBS stopped."
  fi
}

show_logs() {
  if [ -f "bot.log" ]; then
    echo -e "${CYAN}━━━━━━━━━━━━━ bot.log (last 50 lines) ━━━━━━━━━━━━━${RESET}"
    tail -n 50 bot.log
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
  fi
  if screen -list 2>/dev/null | grep -q "$SESSION_NAME"; then
    echo ""
    info "Attaching to live session (Ctrl+A then D to detach)..."
    sleep 1
    screen -r "$SESSION_NAME"
  else
    warn "Bot is not currently running."
  fi
}

# ─── Main Entry Point ─────────────────────────────────────────────
banner

case "${1:-run}" in
  start)
    install_system_deps
    setup_venv
    setup_env
    setup_obs
    init_project
    start_background
    ;;
  stop)
    stop_bot
    ;;
  restart)
    stop_bot
    sleep 1
    install_system_deps
    setup_venv
    setup_env
    setup_obs
    init_project
    start_background
    ;;
  logs)
    show_logs
    ;;
  setup)
    install_system_deps
    setup_venv
    setup_env
    setup_obs
    init_project
    success "Setup complete! Run 'bash start.sh start' to launch the bot."
    ;;
  run|*)
    # Default: full setup then run in foreground (e.g. direct `bash start.sh`)
    install_system_deps
    setup_venv
    setup_env
    setup_obs
    init_project
    start_foreground
    ;;
esac