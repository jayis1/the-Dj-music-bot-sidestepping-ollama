#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  The Radio DJ Bot v420.0.4 — Self-Setup Launcher
#
#  Usage:
#    bash start.sh          → setup + run (first time or re-run)
#    bash start.sh start    → start in background (screen)
#    bash start.sh stop     → stop background session + OBS
#    bash start.sh restart  → restart background session
#    bash start.sh nuke     → kill all OBS processes + delete crash markers + reset config
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

# ── Flatpak OBS detection ──────────────────────────────────────────
# Flatpak OBS includes the browser_source plugin (obs-browser) which
# enables the full Mission Control overlay with real-time waveform
# visualizer. apt OBS on Debian 12 does NOT include obs-browser.
#
# When Flatpak OBS is detected:
#   - Config dir: ~/.var/app/com.obsproject.Studio/config/obs-studio/
#   - Launch: xvfb-run + flatpak run --socket=x11 --socket=dbus
#     --nosocket=wayland --share=network com.obsproject.Studio
#   - WebSocket: accessible on localhost (Flatpak allows by default)
#   - D-Bus: requires --socket=session-bus for host session bus access
OBS_FLATPAK_INSTALLED=false
if command -v flatpak &>/dev/null && flatpak list 2>/dev/null | grep -q "com.obsproject.Studio"; then
  OBS_FLATPAK_INSTALLED=true
fi
# Resolve OBS config directory based on install method
if [ "$OBS_FLATPAK_INSTALLED" = true ]; then
  OBS_CONFIG_BASE="$HOME/.var/app/com.obsproject.Studio/config/obs-studio"
  info "Flatpak OBS detected — using config dir: $OBS_CONFIG_BASE"
else
  OBS_CONFIG_BASE="$HOME/.config/obs-studio"
fi

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

  read -rp "$(echo -e "${BOLD}  DJ Name - on-air personality name (press Enter for Nova):${RESET} ")" DJ_NAME
  DJ_NAME="${DJ_NAME:-Nova}"

  read -rp "$(echo -e "${BOLD}  Web Dashboard Port (press Enter for 8080):${RESET} ")" WEB_PORT
  WEB_PORT="${WEB_PORT:-8080}"

  cat > .env <<EOF
DISCORD_TOKEN="${DISCORD_TOKEN}"
YOUTUBE_API_KEY="${YOUTUBE_API_KEY}"
LOG_CHANNEL_ID="${LOG_CHANNEL_ID}"
STATION_NAME="${STATION_NAME}"
DJ_NAME="${DJ_NAME}"
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
    success "OBS Studio already installed (apt)"
  fi
  # Also check for Flatpak OBS (preferred — includes browser_source)
  if [ "$OBS_FLATPAK_INSTALLED" = true ]; then
    OBS_INSTALLED=true
    success "OBS Studio already installed (Flatpak — browser source available!)"
  fi

  if [ "$OBS_INSTALLED" = false ]; then
    # Try Flatpak OBS first (includes browser_source plugin)
    if command -v flatpak &>/dev/null; then
      info "Installing OBS Studio via Flatpak (includes browser source for overlay visualizer)..."
      $SUDO flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo 2>/dev/null || true
      if $SUDO flatpak install -y flathub com.obsproject.Studio 2>/dev/null; then
        OBS_INSTALLED=true
        OBS_FLATPAK_INSTALLED=true
        OBS_CONFIG_BASE="$HOME/.var/app/com.obsproject.Studio/config/obs-studio"
        success "OBS Studio installed via Flatpak (browser source available!)"
      else
        warn "Flatpak OBS install failed, trying apt..."
      fi
    else
      info "Flatpak not available. Install it first: sudo apt install flatpak"
    fi

    # Fallback: install via apt (no browser source)
    if [ "$OBS_INSTALLED" = false ]; then
      info "Installing OBS Studio via apt (no browser source)..."
      $SUDO apt-get update -qq 2>/dev/null
      if $SUDO apt-get install -y -qq obs-studio 2>/dev/null; then
        OBS_INSTALLED=true
        OBS_CONFIG_BASE="$HOME/.config/obs-studio"
        success "OBS Studio installed via apt (no browser source — use native overlay)"
        warn "For browser source overlay + visualizer, install Flatpak OBS instead:"
        warn "  sudo apt install flatpak && sudo flatpak remote-add --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo && flatpak install flathub com.obsproject.Studio"
      else
        warn "Could not install OBS Studio automatically."
        warn "Install manually:  sudo apt update && sudo apt install obs-studio"
        warn "OBS is optional — the bot works without it."
      fi
    fi
  fi

  # ── Install headless support packages ──────────────────
  if [ "$OBS_INSTALLED" = true ]; then
    for pkg in xvfb dbus-x11 xdotool pulseaudio pulseaudio-utils chromium; do
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

  # Write WebSocket config to BOTH possible locations (apt + Flatpak)
  # so it works regardless of which OBS is being used.
  for WS_DIR in \
    "$HOME/.config/obs-studio/plugin_config/obs-websocket" \
    "$HOME/.var/app/com.obsproject.Studio/config/obs-studio/plugin_config/obs-websocket"; do
    mkdir -p "$WS_DIR" 2>/dev/null || true
    cat > "$WS_DIR/config.json" << EOF
{
    "server_enabled": true,
    "server_port": 4455,
    "server_password": "${OBS_WS_PASSWORD}",
    "server_password_enabled": true,
    "alerts_enabled": false
}
EOF
  done

  # Also write global.ini (some OBS versions read this instead)
  # Write to both apt and Flatpak config locations
  for OBS_GLOBAL_CONFIG in \
    "$HOME/.config/obs-studio/global.ini" \
    "$HOME/.var/app/com.obsproject.Studio/config/obs-studio/global.ini"; do
    mkdir -p "$(dirname "$OBS_GLOBAL_CONFIG")" 2>/dev/null || true
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
    else
      # Update password in existing config
      if grep -q "ServerPassword=" "$OBS_GLOBAL_CONFIG"; then
        sed -i "s|^ServerPassword=.*|ServerPassword=${OBS_WS_PASSWORD}|" "$OBS_GLOBAL_CONFIG"
      fi
    fi
  done
  success "obs-websocket configured (port 4455, password set)"

  # ── Copy default scene collection ──────────────────────
  # CRITICAL: If OBS is running, kill it first — otherwise it will
  # overwrite our file on exit with its in-memory (stale) version.
  # Check both apt OBS and Flatpak OBS processes.
  if pgrep -x obs &>/dev/null || pgrep -f "flatpak run com.obsproject.Studio" &>/dev/null; then
    info "Stopping OBS to update scene collection..."
    pkill -x obs 2>/dev/null || true
    pkill -f "flatpak run com.obsproject.Studio" 2>/dev/null || true
    sleep 2
    if pgrep -x obs &>/dev/null; then
      pkill -9 -x obs 2>/dev/null || true
    fi
    if pgrep -f "flatpak run com.obsproject.Studio" &>/dev/null; then
      pkill -9 -f "flatpak run com.obsproject.Studio" 2>/dev/null || true
    fi
    sleep 1
  fi

  # ── Nuke corrupted configs from previous OBS versions ──────────
  # OBS 32 migrates configs from the apt OBS dir (~/.config/obs-studio/).
  # If the old apt configs still have OBS 29 encoder names (aac) and
  # transitions (Cut/Fade), OBS 32 fails with errors and crashes.
  # Fix: also rewrite the apt OBS configs to be OBS 32 compatible.
  APT_OBS_BASE="$HOME/.config/obs-studio"
  for OBS_BASE in "$APT_OBS_BASE" "$OBS_CONFIG_BASE"; do
    # Fix aac → ffmpeg_aac in any basic.ini (both apt and Flatpak dirs)
    for inifile in "$OBS_BASE/basic/profiles/RadioDJ/basic.ini" \
                   "$OBS_BASE/basic/profiles/Untitled/basic.ini" \
                   "$OBS_BASE/basic/profiles/Unnamed/basic.ini"; do
      if [ -f "$inifile" ]; then
        sed -i 's/=aac/=ffmpeg_aac/g' "$inifile" 2>/dev/null || true
        # Also fix ApplyServiceSettings
        if grep -q "ApplyServiceSettings=true" "$inifile"; then
          sed -i 's/ApplyServiceSettings=true/ApplyServiceSettings=false/' "$inifile"
        fi
      fi
    done
    # Delete any OBS-created scene collection backups (corrupted)
    rm -f "$OBS_BASE/basic/scenes/Radio DJ.json.bak" 2>/dev/null
    rm -f "$OBS_BASE/basic/scenes/Radio DJ.json.bak.1" 2>/dev/null
    rm -f "$OBS_BASE/basic/scenes/Radio DJ.json.bak.2" 2>/dev/null
    rm -f "$OBS_BASE/basic/scenes/Untitled.json" 2>/dev/null
    rm -f "$OBS_BASE/basic/scenes/Untitled.json.bak" 2>/dev/null
    # Delete global.json (OBS 32 migration cache — stores old encoder IDs)
    rm -f "$OBS_BASE/global.json" 2>/dev/null
  done

  OBS_SCENES_DIR="$OBS_CONFIG_BASE/basic/scenes"
  OBS_PROFILES_DIR="$OBS_CONFIG_BASE/basic/profiles"
  mkdir -p "$OBS_SCENES_DIR" "$OBS_PROFILES_DIR/RadioDJ"

  # Always force-copy the scene collection and clean up stale files.
  # Even if OBS isn't running, the old file on disk may have stale
  # settings (ar=48000 ac=2, old scene layout, etc).
  rm -f "$OBS_SCENES_DIR/Radio DJ.json.bak" "$OBS_SCENES_DIR/Radio DJ.json.bak.1" 2>/dev/null
  rm -f "$OBS_SCENES_DIR/Untitled.json" "$OBS_SCENES_DIR/Untitled.json.bak" 2>/dev/null
  # OBS 32 may have saved a backup of the broken OBS 29 scene collection
  # which it couldn't load. Remove it so it doesn't fall back to it.
  rm -f "$OBS_SCENES_DIR/Radio DJ.json.bak.1" "$OBS_SCENES_DIR/Radio DJ.json.bak.2" 2>/dev/null

  # ── Scene collection strategy ──────────────────────────────────
  # We ALWAYS copy the minimal OBS 32-compatible template scene
  # collection. This template has one empty "📺 Overlay Only" scene
  # with no sources — the bot creates all sources programmatically
  # via WebSocket after connecting (ensure_scenes_exist +
  # create_browser_overlay / create_native_overlay + create_audio_source).
  #
  # OBS 32 changed the JSON schema and rejects OBS 29-era scene
  # collection files ("All scene data cleared" + crash). The old
  # multi-scene collection (️ Now Playing, 🎙️ DJ Speaking, ⏳ Waiting,
  # 📺 Overlay Only) with text_ft2_source sources causes OBS 32 to
  # crash on WebSocket connect because it can't enumerate a null scene.
  #
  # The template is safe because:
  #   - It has a valid current_scene ("📺 Overlay Only") — no null pointers
  #   - It has no sources — no schema incompatibilities
  #   - It has no transitions — no old transition names (Cut/Fade)
  #   - The bot creates everything at runtime after OBS stabilizes
  #
  if [ -f "$BOT_DIR/obs-studio/config/obs-studio/basic/scenes/Radio DJ.json" ]; then
    cp "$BOT_DIR/obs-studio/config/obs-studio/basic/scenes/Radio DJ.json" "$OBS_SCENES_DIR/Radio DJ.json"
    success "Installed OBS 32-compatible 'Radio DJ' scene collection (minimal — bot creates sources at runtime)"
  else
    # Fallback: write the minimal template inline
    cat > "$OBS_SCENES_DIR/Radio DJ.json" << 'SCENEEOF'
{
    "Name": "Radio DJ",
    "Items": {
        "📺 Overlay Only": {
            "fixed_function": false,
            "id_counter": 1,
            "name": "📺 Overlay Only",
            "private_settings": {},
            "sources": []
        }
    },
    "current_scene": "📺 Overlay Only",
    "current_program_scene": "📺 Overlay Only",
    "groups": [],
    "modules": {},
    "quick_transitions": [],
    "scaling_enabled": false,
    "scaling_level": 0,
    "transitions": []
}
SCENEEOF
    success "Wrote minimal OBS 32-compatible scene collection (bot creates sources at runtime)"
  fi

  # ── Copy OBS profile (basic.ini) from template ──────────────────
  # The template has ApplyServiceSettings=false, ffmpeg_aac encoder,
  # keyint_sec=2, Bitrate=3000, and all the video/audio settings.
  if [ -f "$BOT_DIR/obs-studio/config/obs-studio/basic/profiles/RadioDJ/basic.ini" ]; then
    cp "$BOT_DIR/obs-studio/config/obs-studio/basic/profiles/RadioDJ/basic.ini" "$OBS_PROFILES_DIR/RadioDJ/basic.ini"
    # Safety: ensure ApplyServiceSettings=false even if template was edited
    if grep -q "^ApplyServiceSettings=" "$OBS_PROFILES_DIR/RadioDJ/basic.ini"; then
      sed -i 's/^ApplyServiceSettings=.*/ApplyServiceSettings=false/' "$OBS_PROFILES_DIR/RadioDJ/basic.ini"
    fi
    # Safety: fix old encoder name
    sed -i 's/=aac/=ffmpeg_aac/g' "$OBS_PROFILES_DIR/RadioDJ/basic.ini"
    success "Installed 'RadioDJ' OBS profile"
  fi

  # ── Copy streamEncoder.json from template ─────────────────────
  # This file tells OBS to use keyint_sec=2 (keyframes every 2s)
  # instead of YouTube's default keyint=250 (8.3s @ 30fps → "Poor" health).
  # OBS reads this at startup — it MUST exist before OBS launches.
  if [ -f "$BOT_DIR/obs-studio/config/obs-studio/basic/profiles/RadioDJ/streamEncoder.json" ]; then
    cp "$BOT_DIR/obs-studio/config/obs-studio/basic/profiles/RadioDJ/streamEncoder.json" "$OBS_PROFILES_DIR/RadioDJ/streamEncoder.json"
    success "Installed streamEncoder.json (keyint_sec=2, bitrate=3000)"
  else
    # Fallback: write it inline if template doesn't exist
    cat > "$OBS_PROFILES_DIR/RadioDJ/streamEncoder.json" << 'ENCODEOF'
{
    "obs_x264": {
        "rate_control": "CBR",
        "bitrate": 3000,
        "buffer_size": 3000,
        "keyint_sec": 2,
        "preset": "veryfast",
        "profile": "high",
        "tune": "zerolatency",
        "x264opts": "keyint=60:min-keyint=60:bframes=0"
    }
}
ENCODEOF
    success "Wrote streamEncoder.json (keyint_sec=2, bitrate=3000)"
  fi

  # ── Write service.json template BEFORE OBS starts ────────────
  # OBS reads service.json from the profile dir at startup to know
  # which streaming service to use. Without it, OBS falls back to
  # RTMPS with no server/key → "Connection reset by peer" errors.
  # (The bot also writes this at startup with the actual stream key.)
  if [ ! -f "$OBS_PROFILES_DIR/RadioDJ/service.json" ]; then
    cat > "$OBS_PROFILES_DIR/RadioDJ/service.json" << 'SVCEOF'
{
    "type": "rtmp_custom",
    "settings": {
        "server": "rtmp://a.rtmp.youtube.com/live2",
        "key": ""
    }
}
SVCEOF
    info "Wrote template service.json (stream key will be filled by bot)"
  fi

  # ══════════════════════════════════════════════════════════════════
  # ── SYNC CONFIGS TO BOTH APT AND FLATPAK OBS DIRS ──────────────
  # ══════════════════════════════════════════════════════════════════
  # When Flatpak OBS starts for the first time, it MIGRATES configs
  # from the apt OBS dir (~/.config/obs-studio/). If the apt dir has
  # stale OBS 29 configs (aac encoder, Cut/Fade transitions, old
  # multi-scene JSON), Flatpak OBS 32 crashes.
  #
  # Similarly, if the Flatpak dir has stale configs and apt OBS is
  # being used, apt OBS 29 will crash on the OBS 32 JSON schema.
  #
  # Fix: ALWAYS ensure BOTH directories have identical OBS 32-
  # compatible configs. We sync scene collection, profile, stream
  # encoder, service, and user.ini to both dirs.
  # ══════════════════════════════════════════════════════════════════

  # _fix_user_ini: Helper function to create/fix user.ini for an OBS dir.
  # OBS reads user.ini on startup to determine which scene collection
  # and profile to load. If it says "Untitled" or doesn't exist, OBS
  # creates a blank "Scene" and ignores --collection/--profile flags.
  _fix_user_ini() {
    local _obs_base="$1"
    local _user_ini="$_obs_base/user.ini"
    mkdir -p "$_obs_base" 2>/dev/null || true

    if [ -f "$_user_ini" ]; then
      # Fix existing file — update Scene/Profile settings
      sed -i 's/^SceneCollection=.*/SceneCollection=Radio DJ/' "$_user_ini" 2>/dev/null || true
      sed -i 's/^SceneCollectionFile=.*/SceneCollectionFile=Radio DJ.json/' "$_user_ini" 2>/dev/null || true
      sed -i 's/^Profile=.*/Profile=RadioDJ/' "$_user_ini" 2>/dev/null || true
      sed -i 's/^ProfileDir=.*/ProfileDir=RadioDJ/' "$_user_ini" 2>/dev/null || true
      # ConfigOnNewProfile=true causes OBS to create a new blank profile
      # every startup, ignoring the --profile flag. Remove it.
      sed -i '/^ConfigOnNewProfile=/d' "$_user_ini" 2>/dev/null || true
    else
      # Create user.ini from scratch
      cat > "$_user_ini" << USERINIEOF
[General]
Pre197TagsInUse=true

[Basic]
Profile=RadioDJ
ProfileDir=RadioDJ
SceneCollection=Radio DJ
SceneCollectionFile=Radio DJ.json
USERINIEOF
    fi
  }

  # Fix user.ini for BOTH apt and Flatpak OBS dirs
  _fix_user_ini "$APT_OBS_BASE"
  info "Fixed user.ini for apt OBS dir ($APT_OBS_BASE)"
  if [ "$OBS_CONFIG_BASE" != "$APT_OBS_BASE" ]; then
    _fix_user_ini "$OBS_CONFIG_BASE"
    info "Fixed user.ini for Flatpak OBS dir ($OBS_CONFIG_BASE)"
  fi

  # Sync all config files to the OTHER OBS dir (the one we didn't write to).
  # Files were written to $OBS_CONFIG_BASE above. We sync them to the apt
  # dir too because Flatpak OBS migrates from apt on first run.
  for _SYNC_DIR in "$APT_OBS_BASE" "$OBS_CONFIG_BASE"; do
    [ -z "$_SYNC_DIR" ] && continue
    # Skip the primary dir — we already wrote configs there directly
    [ "$_SYNC_DIR" = "$OBS_CONFIG_BASE" ] && continue
    _SYNC_SCENES="$_SYNC_DIR/basic/scenes"
    _SYNC_PROFILES="$_SYNC_DIR/basic/profiles/RadioDJ"
    mkdir -p "$_SYNC_SCENES" "$_SYNC_PROFILES" 2>/dev/null || true

    # Copy scene collection (OBS 32-compatible minimal template)
    if [ -f "$OBS_SCENES_DIR/Radio DJ.json" ]; then
      cp "$OBS_SCENES_DIR/Radio DJ.json" "$_SYNC_SCENES/Radio DJ.json"
    fi

    # Copy profile files
    for _pfile in basic.ini streamEncoder.json service.json; do
      if [ -f "$OBS_PROFILES_DIR/RadioDJ/$_pfile" ]; then
        cp "$OBS_PROFILES_DIR/RadioDJ/$_pfile" "$_SYNC_PROFILES/$_pfile"
      fi
    done

    # Fix encoder name in copied basic.ini
    if [ -f "$_SYNC_PROFILES/basic.ini" ]; then
      sed -i 's/=aac/=ffmpeg_aac/g' "$_SYNC_PROFILES/basic.ini" 2>/dev/null || true
      # Ensure ApplyServiceSettings=false
      if grep -q "ApplyServiceSettings=true" "$_SYNC_PROFILES/basic.ini"; then
        sed -i 's/ApplyServiceSettings=true/ApplyServiceSettings=false/' "$_SYNC_PROFILES/basic.ini"
      fi
    fi

    # Delete Untitled scene collections (OBS falls back to these)
    rm -f "$_SYNC_SCENES/Untitled.json" "$_SYNC_SCENES/Untitled.json.bak" \
          "$_SYNC_SCENES/Untitled.json.bak.1" 2>/dev/null || true

    # Delete any scene collection backups (corrupted from previous runs)
    rm -f "$_SYNC_SCENES/Radio DJ.json.bak" \
          "$_SYNC_SCENES/Radio DJ.json.bak.1" \
          "$_SYNC_SCENES/Radio DJ.json.bak.2" 2>/dev/null || true
  done

  # Delete global.json migration cache (stores old encoder IDs that
  # OBS 32 inherits on first run — causes "Encoder ID 'aac' not found")
  for _SYNC_DIR in "$APT_OBS_BASE" "$OBS_CONFIG_BASE"; do
    rm -f "$_SYNC_DIR/global.json" 2>/dev/null || true
  done

  success "Synced OBS 32-compatible configs to both apt + Flatpak dirs"

  # ── Start headless OBS via xvfb-run ─────────────────────
  if [ "$OBS_INSTALLED" = true ]; then
    if pgrep -x obs &>/dev/null; then
      success "OBS Studio already running (apt, PID: $(pgrep -x obs | head -1))"
    elif pgrep -f "flatpak run com.obsproject.Studio" &>/dev/null; then
      success "OBS Studio already running (Flatpak, PID: $(pgrep -f 'flatpak run com.obsproject.Studio' | head -1))"
    else
      info "Starting headless OBS Studio..."

      # Kill any leftover OBS processes from a previous run.
      # Stale OBS processes hold crash markers and prevent clean startup.
      if pgrep -x obs &>/dev/null || pgrep -f "flatpak run com.obsproject.Studio" &>/dev/null; then
        info "Killing leftover OBS processes from previous run..."
        pkill -f "flatpak run com.obsproject.Studio" 2>/dev/null || true
        pkill -x obs 2>/dev/null || true
        sleep 2
      fi

      # ── Start D-Bus session daemon (OBS + Flatpak need it) ───────
      # OBS and Flatpak OBS both require a D-Bus session bus.
      # Without it, Flatpak OBS fails with:
      #   "Could not create dbus connection: Failed to execute child
      #    process 'dbus-launch' (No such file or directory)"
      #
      # We start a user-session dbus-daemon and export the address so
      # that both xvfb-run children AND the Flatpak sandbox (via
      # --socket=dbus) can reach it.
      #
      # dbus-x11 provides dbus-launch (fallback method).
      # dbus-daemon with --session is the preferred method.
      if [ -z "$DBUS_SESSION_BUS_ADDRESS" ] || ! pgrep -x dbus-daemon &>/dev/null; then
        # Method 1: Use dbus-launch (from dbus-x11 package)
        # This is the simplest and most portable approach — dbus-launch
        # starts a dbus-daemon and outputs the connection address.
        # We prefer this over manual dbus-daemon because it handles
        # all the config/session setup automatically.
        if command -v dbus-launch &>/dev/null; then
          eval $(dbus-launch --sh-syntax) 2>/dev/null || true
          if [ -n "$DBUS_SESSION_BUS_ADDRESS" ]; then
            export DBUS_SESSION_BUS_ADDRESS
            info "D-Bus session started via dbus-launch at $DBUS_SESSION_BUS_ADDRESS"
          fi
        fi

        # Method 2: If dbus-launch failed, try dbus-daemon directly
        if [ -z "$DBUS_SESSION_BUS_ADDRESS" ]; then
          _DBUS_SOCKET_DIR="/tmp/radio-dj-dbus"
          mkdir -p "$_DBUS_SOCKET_DIR"
          _DBUS_SOCKET="$_DBUS_SOCKET_DIR/session_bus_socket"

          if ! pgrep -x dbus-daemon &>/dev/null; then
            dbus-daemon --session --address="unix:path=$_DBUS_SOCKET" \
              --print-pid="$_DBUS_SOCKET_DIR/dbus-daemon.pid" --nofork &
            _dbus_wait=0
            while [ ! -S "$_DBUS_SOCKET" ] && [ $_dbus_wait -lt 10 ]; do
              sleep 0.5
              _dbus_wait=$((_dbus_wait + 1))
            done
            if [ -S "$_DBUS_SOCKET" ]; then
              export DBUS_SESSION_BUS_ADDRESS="unix:path=$_DBUS_SOCKET"
              info "D-Bus session started at $DBUS_SESSION_BUS_ADDRESS"
            else
              warn "Could not start D-Bus session. OBS may fail to start."
            fi
          fi
        fi

        # Method 3: Try the system bus as last resort
        if [ -z "$DBUS_SESSION_BUS_ADDRESS" ]; then
          if [ -S "/run/user/$(id -u)/bus" ]; then
            export DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus"
            info "Using system D-Bus session bus at $DBUS_SESSION_BUS_ADDRESS"
          else
            warn "No D-Bus session available. OBS/Flatpak may fail."
          fi
        fi
      else
        info "D-Bus session already active at $DBUS_SESSION_BUS_ADDRESS"
      fi

      # Start PulseAudio with null sink for virtual audio
      if ! pgrep -x pulseaudio &>/dev/null; then
        pulseaudio --start --fail=false --daemonize=true \
          --load="module-null-sink sink_name=radio_dj_sink sink_properties=device.description='Radio_DJ_Audio'" \
          2>/dev/null || true
        sleep 1
        pactl set-default-sink radio_dj_sink 2>/dev/null || true
      fi

      # ══ Final pre-launch config verification ════════════════
      # These are the LAST chances to fix configs before OBS reads them.
      # Both apt and Flatpak dirs are verified.

      for _PRECHECK_DIR in "$APT_OBS_BASE" "$OBS_CONFIG_BASE"; do
        # Fix user.ini one last time (OBS may have rewritten it from a previous run)
        _fix_user_ini "$_PRECHECK_DIR"

        # Force ApplyServiceSettings=false in basic.ini
        _PRECHECK_INI="$_PRECHECK_DIR/basic/profiles/RadioDJ/basic.ini"
        if [ -f "$_PRECHECK_INI" ]; then
          if grep -q "ApplyServiceSettings=true" "$_PRECHECK_INI"; then
            sed -i 's/ApplyServiceSettings=true/ApplyServiceSettings=false/' "$_PRECHECK_INI"
            info "Fixed ApplyServiceSettings=false in $_PRECHECK_INI"
          fi
        fi

        # Delete Untitled scene collections (OBS falls back to them)
        rm -f "$_PRECHECK_DIR/basic/scenes/Untitled.json" \
              "$_PRECHECK_DIR/basic/scenes/Untitled.json.bak" \
              "$_PRECHECK_DIR/basic/scenes/Untitled.json.bak.1" 2>/dev/null || true

        # ══ Delete OBS crash/safe-mode markers ═══════════════
        # When OBS crashes or is killed without clean shutdown, it writes
        # a crash sentinel file. On next start, OBS detects this and shows
        # a "Crash or unclean shutdown detected" dialog, then enters
        # safe mode — running with a blank scene and NOT starting the
        # WebSocket server. This means the bot can never connect.
        #
        # Fix: delete ALL crash markers before launching OBS so it
        # always starts fresh, regardless of how the previous run ended.
        #
        # Crash detection mechanism (OBS 29 + OBS 32):
        #   1. OBS writes a ".sentinel" file in the config root on startup
        #      (e.g. ~/.config/obs-studio/.sentinel or
        #       ~/.var/app/com.obsproject.Studio/config/obs-studio/.sentinel)
        #   2. OBS deletes it on clean exit
        #   3. If the file still exists on next launch → crash dialog
        #   4. The dialog BLOCKS the Qt event loop → WebSocket never starts
        #
        # OBS 29 and some OBS 30+ builds also use "crash_marker" files
        # in plugin directories. We delete both to be safe.
        #
        # Marker locations:
        #   - .sentinel in OBS config root (OBS 32+ primary mechanism)
        #   - crash_marker in plugin dirs (OBS 29 browser source)
        #   - plugin_config/obs-browser/crash_marker (browser source)
        #   - [General] CrashDuringStartup=true in basic.ini (OBS 30+)
        #
        # Use find to catch ALL instances recursively — OBS may have
        # created markers in unexpected subdirectories.
        find "$_PRECHECK_DIR" -name ".sentinel" -type f -delete 2>/dev/null || true
        find "$_PRECHECK_DIR" -name "crash_marker" -type f -delete 2>/dev/null || true
        find "$_PRECHECK_DIR" -name "safe_mode" -type f -delete 2>/dev/null || true
        find "$_PRECHECK_DIR" -name ".lock" -path "*/profiles/*" -delete 2>/dev/null || true
        find "$_PRECHECK_DIR" -name "lockfile" -path "*/profiles/*" -delete 2>/dev/null || true
        # Fix CrashDuringStartup flag in basic.ini (OBS 32 writes this
        # at the start of every launch and clears it on clean exit).
        # If OBS was killed, the flag remains true → crash dialog.
        _PRECHECK_INI_CRASH="$_PRECHECK_DIR/basic/profiles/RadioDJ/basic.ini"
        if [ -f "$_PRECHECK_INI_CRASH" ]; then
          if grep -q "CrashDuringStartup=true" "$_PRECHECK_INI_CRASH"; then
            sed -i 's/CrashDuringStartup=true/CrashDuringStartup=false/' "$_PRECHECK_INI_CRASH"
            info "Cleared CrashDuringStartup flag in $_PRECHECK_INI_CRASH"
          fi
        fi
      done

      # Start OBS headless
      # ══════════════════════════════════════════════════════════════════
      # CRITICAL: We do NOT use xvfb-run because we need to:
      #   1. Know the exact X11 display number for xdotool
      #   2. Keep Xvfb running independently of OBS (xvfb-run kills Xvfb
      #      when the child exits, but we need it persistent)
      #   3. Use xdotool to auto-dismiss OBS crash/config dialogs that
      #      block the Qt event loop and prevent WebSocket startup
      #
      # Strategy:
      #   - Start Xvfb on a dedicated display (:420)
      #   - Launch OBS on that display
      #   - Run a dialog dismissal watchdog that sends Enter/Space
      #     keypresses to auto-click through any blocking modal dialogs
      #     (crash dialog, missing files dialog, safe mode prompt, etc.)
      #   - This is more reliable than trying to prevent every possible
      #     crash detection path (sentinel files, CrashDuringStartup,
      #     plugin config markers, Flatpak runtime tracking, etc.)
      # ══════════════════════════════════════════════════════════════════

      # ── Start Xvfb on a dedicated display ───────────────────────
      # Pick a display number unlikely to clash with real X sessions.
      # :420 is the Radio DJ display — 420 for obvious reasons.
      # If Xvfb fails to start (e.g. missing Xvfb binary), we fall
      # back to xvfb-run which manages its own Xvfb instance.
      _OBS_DISPLAY=":420"
      _OBS_SCREEN="1280x720x24"  # 24-bit color depth, 720p
      _USE_XVFB_EXPLICIT=false
      if command -v Xvfb &>/dev/null; then
        if ! pgrep -f "Xvfb $_OBS_DISPLAY" &>/dev/null; then
          Xvfb "$_OBS_DISPLAY" -screen 0 "$_OBS_SCREEN" -ac +extension GLX +render -noreset &
          sleep 1
          if pgrep -f "Xvfb $_OBS_DISPLAY" &>/dev/null; then
            info "Xvfb started on display $_OBS_DISPLAY"
            _USE_XVFB_EXPLICIT=true
          else
            warn "Xvfb failed to start on $_OBS_DISPLAY — falling back to xvfb-run"
            _OBS_DISPLAY=""
          fi
        else
          info "Xvfb already running on $_OBS_DISPLAY"
          _USE_XVFB_EXPLICIT=true
        fi
      else
        warn "Xvfb binary not found — falling back to xvfb-run"
        _OBS_DISPLAY=""
      fi

      # Export DISPLAY for xdotool and OBS (only if we have a known display)
      if [ "$_USE_XVFB_EXPLICIT" = true ]; then
        export DISPLAY="$_OBS_DISPLAY"
      fi

      # Suppress Flatpak XDG_DATA_DIRS warning
      export XDG_DATA_DIRS="${XDG_DATA_DIRS:-/usr/local/share:/usr/share}:/var/lib/flatpak/exports/share:$HOME/.local/share/flatpak/exports/share"

      if [ "$OBS_FLATPAK_INSTALLED" = true ]; then
        info "Starting Flatpak OBS Studio (headless, browser source available)..."
        # ── Flatpak OBS headless launch flags ──────────────────────
        # --socket=x11:        Allow access to xvfb's X11 display (OBS needs real OpenGL)
        # --nosocket=wayland:  Prevent Wayland detection (we're using Xvfb)
        # --socket=session-bus: Allow access to host D-Bus session bus (prevents
        #                       "Could not create dbus connection" error).
        #                       NOTE: The valid Flatpak socket name is "session-bus",
        #                       NOT "dbus" — "dbus" will fail with:
        #                       "error: Unknown socket type dbus"
        # --share=network:     Allow browser source to reach localhost:8080
        #
        # NOTE: --disable-shutdown-check does NOT exist in OBS 32.
        # Crash dialog is auto-dismissed by our xdotool watchdog.
        # --safe-mode DISABLES WebSocket, so never use it.
        #
        # DBUS_SESSION_BUS_ADDRESS is exported above from our dbus-launch — the
        # --socket=session-bus flag lets the Flatpak sandbox inherit it.
        if [ "$_USE_XVFB_EXPLICIT" = true ]; then
          # We have a dedicated Xvfb on :420 — use it directly
        flatpak run --socket=x11 --nosocket=wayland \
          --socket=session-bus --share=network \
          --env=DISPLAY="$_OBS_DISPLAY" \
          --env=LC_ALL=C.UTF-8 \
          --env=OBS_BROWSER_DISABLE_GPU=1 \
          --env=QT_QPA_PLATFORM=xcb \
          com.obsproject.Studio \
          --minimize-to-tray --disable-missing-files-check \
          --collection "Radio DJ" --profile "RadioDJ" &
        else
          # Fallback: use xvfb-run (no xdotool dialog dismissal available)
          warn "Using xvfb-run fallback — crash dialogs cannot be auto-dismissed"
          xvfb-run -a flatpak run --socket=x11 --nosocket=wayland \
            --socket=session-bus --share=network \
            --env=LC_ALL=C.UTF-8 \
          --env=OBS_BROWSER_DISABLE_GPU=1 \
          --env=CEF_DISABLE_GPU=1 \
            com.obsproject.Studio \
            --minimize-to-tray --disable-missing-files-check \
            --collection "Radio DJ" --profile "RadioDJ" &
        fi
        OBS_PID=$!
      else
        info "Starting headless OBS Studio (apt, no browser source)..."
        # NOTE: --disable-shutdown-check does NOT exist in OBS 32.
        # Crash dialog prevention relies on xdotool + .sentinel file deletion.
        if [ "$_USE_XVFB_EXPLICIT" = true ]; then
          DISPLAY="$_OBS_DISPLAY" OBS_BROWSER_DISABLE_GPU=1 QT_QPA_PLATFORM=xcb \
            obs --minimize-to-tray --disable-missing-files-check \
            --collection "Radio DJ" --profile "RadioDJ" &
        else
          OBS_BROWSER_DISABLE_GPU=1 xvfb-run -a obs --minimize-to-tray --disable-missing-files-check \
            --collection "Radio DJ" --profile "RadioDJ" &
        fi
        OBS_PID=$!
      fi
      sleep 3

      # ── Dialog dismissal watchdog (xdotool) ─────────────────────
      # Even after deleting .sentinel files and fixing CrashDuringStartup,
      # OBS may still show a "Crash or unclean shutdown detected" dialog
      # on startup. This dialog BLOCKS the Qt event loop — the WebSocket
      # server never starts and the bot can never connect.
      #
      # This watchdog runs in the background and uses xdotool to send
      # Enter/Space keypresses to any OBS window with a modal dialog.
      # The "Launch Normally" button is focused by default in the crash
      # dialog, so pressing Enter dismisses it and OBS continues to load.
      #
      # It also handles other blocking dialogs like:
      #   - "Missing files" dialog (--disable-missing-files-check)
      #   - "Profile upgrade" dialog
      #   - "Plugin incompatible" dialog
      #   - Any other modal dialog with a default button
      #
      # The watchdog runs for up to 30 seconds, then auto-exits.
      _DIALOG_WATCHDOG_PID=""
      if command -v xdotool &>/dev/null && [ -n "$_OBS_DISPLAY" ]; then
        (
          for _dw_iter in $(seq 1 30); do
            # Use xdotool to press Enter on the active window.
            # --window: focus any window on the display
            # --clearmodifiers: release any stuck modifier keys
            # 2>/dev/null: suppress errors when no window exists yet
            DISPLAY="$_OBS_DISPLAY" xdotool key --clearmodifiers Return 2>/dev/null || true
            # Also try Tab+Enter (in case focus is on a non-default button)
            DISPLAY="$_OBS_DISPLAY" xdotool key --clearmodifiers Tab Return 2>/dev/null || true
            # Small delay between attempts — don't spam OBS
            sleep 1
          done
        ) &
        _DIALOG_WATCHDOG_PID=$!
        info "Dialog dismissal watchdog started (pid: $_DIALOG_WATCHDOG_PID)"
      else
        if [ -z "$_OBS_DISPLAY" ]; then
          warn "xdotool watchdog skipped (Xvfb not available)"
        else
          warn "xdotool not installed — install it for auto-dismiss of OBS crash dialogs"
        fi
      fi

      # ── Sentinel cleanup watchdog ───────────────────────────────
      # OBS creates a .sentinel file at the START of its launch, then
      # deletes it on clean exit. If OBS crashes DURING startup (e.g.
      # d-bus failure), the sentinel remains and triggers the crash
      # dialog on the NEXT launch — even though we deleted all sentinels
      # before launch.
      #
      # This watchdog runs in the background and periodically deletes
      # .sentinel files while OBS is starting up. This ensures that even
      # if OBS crashes during startup, the sentinel won't persist to
      # block the next launch attempt.
      #
      # It runs for up to 20 seconds (longer than OBS startup), then
      # exits automatically.
      (
        for _sw_iter in $(seq 1 20); do
          for _sw_dir in "$APT_OBS_BASE" "$OBS_CONFIG_BASE"; do
            [ -d "$_sw_dir" ] && find "$_sw_dir" -name ".sentinel" -type f -delete 2>/dev/null || true
          done
          sleep 1
        done
      ) &
      _SENTINEL_WATCHDOG_PID=$!

      # ── Verify OBS actually started (not just the process) ────
      # A simple `kill -0` only checks if the PID is alive — but OBS
      # can be frozen in a crash-recovery dialog with no WebSocket.
      # We poll the WebSocket port to confirm OBS is actually usable.
      _OBS_READY=false
      for _attempt in $(seq 1 30); do
        if python3 -c "
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(1)
try:
    s.connect(('127.0.0.1', 4455))
    s.close()
    sys.exit(0)
except:
    sys.exit(1)
" 2>/dev/null; then
          _OBS_READY=true
          break
        fi
        sleep 1
      done

      if [ "$_OBS_READY" = true ]; then
        success "OBS Studio started headless (PID: $OBS_PID, WebSocket ready on port 4455)"
        info "Mission Control will auto-connect to OBS"
      else
        # Check if process is even alive
        if kill -0 $OBS_PID 2>/dev/null; then
          warn "OBS process alive (PID: $OBS_PID) but WebSocket NOT responding after 30s"
          # Check if crash sentinel exists (OBS is stuck in crash dialog)
          _sentinel_found=false
          for _sd in "$APT_OBS_BASE" "$OBS_CONFIG_BASE"; do
            if [ -f "$_sd/.sentinel" ]; then
              _sentinel_found=true
              warn "Crash sentinel found: $_sd/.sentinel — OBS is stuck in crash dialog"
            fi
          done
          if [ "$_sentinel_found" = true ]; then
            warn "OBS detected a previous unclean shutdown and is showing a blocking dialog."
            warn "The WebSocket server cannot start while this dialog is open."
            warn "Try: bash start.sh nuke && bash start.sh"
          else
            warn "OBS may be stuck in a crash/safe-mode dialog or loading slowly."
            warn "Try: bash start.sh nuke && bash start.sh"
          fi
        else
          warn "OBS process died shortly after launch. Check: bash start.sh logs"
          # Show last few lines of OBS log if available
          _latest_obs_log=$(ls -t "$OBS_CONFIG_BASE/logs/"*.txt 2>/dev/null | head -1)
          if [ -n "$_latest_obs_log" ] && [ -f "$_latest_obs_log" ]; then
            warn "Last OBS log entries:"
            tail -5 "$_latest_obs_log" 2>/dev/null | while read line; do warn "  $line"; done
          fi
        fi
      fi

      # Kill the watchdogs if still running
      kill $_SENTINEL_WATCHDOG_PID 2>/dev/null || true
      [ -n "$_DIALOG_WATCHDOG_PID" ] && kill $_DIALOG_WATCHDOG_PID 2>/dev/null || true
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

  # Stop headless OBS (if we started it) — check both apt and Flatpak
  OBS_STOPPED=false
  if pgrep -x obs &>/dev/null; then
    info "Stopping headless OBS..."
    pkill -x obs 2>/dev/null || true
    sleep 1
    if pgrep -x obs &>/dev/null; then
      pkill -9 -x obs 2>/dev/null || true
    fi
    OBS_STOPPED=true
  fi
  # Flatpak OBS runs as a separate process name
  if pgrep -f "flatpak run com.obsproject.Studio" &>/dev/null; then
    info "Stopping Flatpak OBS..."
    pkill -f "flatpak run com.obsproject.Studio" 2>/dev/null || true
    sleep 1
    if pgrep -f "flatpak run com.obsproject.Studio" &>/dev/null; then
      pkill -9 -f "flatpak run com.obsproject.Studio" 2>/dev/null || true
    fi
    OBS_STOPPED=true
  fi
  if [ "$OBS_STOPPED" = true ]; then
    success "OBS stopped."
    # Clean up crash sentinel files immediately after killing OBS.
    # When OBS is killed (SIGTERM/SIGKILL), it doesn't get a chance
    # to delete its own .sentinel file. This means the next startup
    # would trigger the crash dialog. We delete them proactively.
    for _stop_dir in "$HOME/.config/obs-studio" "$HOME/.var/app/com.obsproject.Studio/config/obs-studio"; do
      [ -d "$_stop_dir" ] && find "$_stop_dir" -name ".sentinel" -type f -delete 2>/dev/null || true
    done
  fi

  # Stop Xvfb if we started it
  if pgrep -f "Xvfb :420" &>/dev/null; then
    info "Stopping Xvfb display :420..."
    pkill -f "Xvfb :420" 2>/dev/null || true
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

nuke_obs() {
  # ══ Nuclear OBS cleanup ═════════════════════════════════════
  # Kills ALL OBS processes, deletes crash markers, resets config
  # to a clean state. Use this when OBS is stuck in a crash dialog
  # or safe mode and won't respond to WebSocket connections.
  # ═════════════════════════════════════════════════════════════
  warn "Nuking all OBS state..."

  # Kill ALL OBS processes (apt + Flatpak) + Xvfb
  pkill -9 -x obs 2>/dev/null || true
  pkill -9 -f "flatpak run com.obsproject.Studio" 2>/dev/null || true
  pkill -9 -f "Xvfb :420" 2>/dev/null || true
  sleep 2

  # Check for stragglers
  if pgrep -x obs &>/dev/null || pgrep -f "flatpak run com.obsproject.Studio" &>/dev/null; then
    error "OBS processes still running after SIGKILL. Manual intervention needed."
  fi
  success "OBS + Xvfb processes killed"

  # Delete crash markers from BOTH dirs
  APT_OBS_BASE="$HOME/.config/obs-studio"
  FLATPAK_OBS_BASE="$HOME/.var/app/com.obsproject.Studio/config/obs-studio"
  for _NUKE_DIR in "$APT_OBS_BASE" "$FLATPAK_OBS_BASE"; do
    if [ -d "$_NUKE_DIR" ]; then
      find "$_NUKE_DIR" -name ".sentinel" -type f -delete 2>/dev/null || true
      find "$_NUKE_DIR" -name "crash_marker" -type f -delete 2>/dev/null || true
      find "$_NUKE_DIR" -name "safe_mode" -type f -delete 2>/dev/null || true
      find "$_NUKE_DIR" -name ".lock" -path "*/profiles/*" -delete 2>/dev/null || true
      find "$_NUKE_DIR" -name "lockfile" -path "*/profiles/*" -delete 2>/dev/null || true
      # Clear CrashDuringStartup in all basic.ini files
      find "$_NUKE_DIR" -name "basic.ini" -exec sed -i 's/CrashDuringStartup=true/CrashDuringStartup=false/g' {} \; 2>/dev/null || true
      # Delete Untitled scene collections
      rm -f "$_NUKE_DIR"/basic/scenes/Untitled.json* 2>/dev/null || true
      # Delete scene collection backups
      rm -f "$_NUKE_DIR"/basic/scenes/"Radio DJ.json.bak"* 2>/dev/null || true
      # Delete migration cache
      rm -f "$_NUKE_DIR"/global.json 2>/dev/null || true
    fi
  done
  success "Crash markers deleted"

  info "OBS nuke complete — run 'bash start.sh' to start fresh"
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
  nuke)
    stop_bot
    nuke_obs
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