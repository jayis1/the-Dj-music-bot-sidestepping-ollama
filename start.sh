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

# ── Flatpak OBS detection ──────────────────────────────────────────
# Flatpak OBS includes the browser_source plugin (obs-browser) which
# enables the full Mission Control overlay with real-time waveform
# visualizer. apt OBS on Debian 12 does NOT include obs-browser.
#
# When Flatpak OBS is detected:
#   - Config dir: ~/.var/app/com.obsproject.Studio/config/obs-studio/
#   - Launch: flatpak run com.obsproject.Studio (no xvfb — Flatpak
#     runs in its own sandbox with QT_QPA_PLATFORM=offscreen)
#   - WebSocket: accessible on localhost (Flatpak allows by default)
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

  # Fix OBS's user.ini to point to "Radio DJ" scene collection.
  # OBS stores the active collection name in [Basic] and if it says
  # "Untitled", OBS loads the wrong (blank) scene collection even
  # when --collection "Radio DJ" is passed on the command line.
  OBS_USER_INI="$OBS_CONFIG_BASE/user.ini"
  mkdir -p "$(dirname "$OBS_USER_INI")"
  if [ ! -f "$OBS_USER_INI" ]; then
    # First run — create user.ini from scratch with correct settings
    cat > "$OBS_USER_INI" << EOF
[General]
[Basic]
SceneCollection=Radio DJ
SceneCollectionFile=Radio DJ.json
Profile=RadioDJ
ProfileDir=RadioDJ
EOF
    success "Created user.ini with Radio DJ scene collection"
  elif grep -q "^SceneCollection=" "$OBS_USER_INI"; then
    sed -i 's/^SceneCollection=.*/SceneCollection=Radio DJ/' "$OBS_USER_INI"
    sed -i 's/^SceneCollectionFile=.*/SceneCollectionFile=Radio DJ.json/' "$OBS_USER_INI"
  else
    # Add [Basic] section if missing
    echo "" >> "$OBS_USER_INI"
    echo "[Basic]" >> "$OBS_USER_INI"
    echo "SceneCollection=Radio DJ" >> "$OBS_USER_INI"
    echo "SceneCollectionFile=Radio DJ.json" >> "$OBS_USER_INI"
  fi
  # Also fix Profile/ProfileDir if they point to "Untitled"
  if grep -q "^Profile=Untitled" "$OBS_USER_INI"; then
    sed -i 's/^Profile=Untitled/Profile=RadioDJ/' "$OBS_USER_INI"
    sed -i 's/^ProfileDir=Untitled/ProfileDir=RadioDJ/' "$OBS_USER_INI"
  fi
  # Ensure Profile/ProfileDir exist even if missing
  if ! grep -q "^Profile=" "$OBS_USER_INI"; then
    echo "Profile=RadioDJ" >> "$OBS_USER_INI"
    echo "ProfileDir=RadioDJ" >> "$OBS_USER_INI"
  fi
  # Also handle the "Unnamed" profile that OBS 29 sometimes creates
  if grep -q "^Profile=Unnamed" "$OBS_USER_INI"; then
    sed -i 's/^Profile=Unnamed/Profile=RadioDJ/' "$OBS_USER_INI"
    sed -i 's/^ProfileDir=Unnamed/ProfileDir=RadioDJ/' "$OBS_USER_INI"
  fi

  # Verify user.ini is correct before OBS starts
  SC_VAL=$(grep "^SceneCollection=" "$OBS_USER_INI" 2>/dev/null | cut -d= -f2)
  if [ "$SC_VAL" = "Radio DJ" ]; then
    success "user.ini SceneCollection = Radio DJ ✅"
  else
    warn "user.ini SceneCollection = '$SC_VAL' — forcing to 'Radio DJ'"
    sed -i 's/^SceneCollection=.*/SceneCollection=Radio DJ/' "$OBS_USER_INI"
    sed -i 's/^SceneCollectionFile=.*/SceneCollectionFile=Radio DJ.json/' "$OBS_USER_INI"
  fi

  if [ -f "$BOT_DIR/obs-studio/config/obs-studio/basic/scenes/Radio DJ.json" ]; then
    cp "$BOT_DIR/obs-studio/config/obs-studio/basic/scenes/Radio DJ.json" "$OBS_SCENES_DIR/Radio DJ.json"
    # Adapt scene collection to current platform.
    # Linux: text_ft2_source (OBS internal ID for FreeType2 plugin)
    # Windows: text_gdiplus_v2 (OBS internal ID for GDI+ plugin)
    # The scene collection JSON uses OBS INTERNAL IDs (not the versioned
    # WebSocket API kinds like text_ft2_source_v2 / text_gdiplus_v2).
    if command -v python3 &>/dev/null; then
      python3 - "$OBS_SCENES_DIR/Radio DJ.json" <<'PYEOF'
import json, sys, platform
fp = sys.argv[1]
with open(fp) as f: data = json.load(f)
is_linux = platform.system() == "Linux"
# Scene collection JSON uses OBS internal source IDs
want = "text_ft2_source" if is_linux else "text_gdiplus_v2"
known = ("text_ft2_source", "text_gdiplus_v2")
changed = False
for scene in data.get("Items", {}).values():
    for src in scene.get("sources", []):
        if src.get("type") in known and src["type"] != want:
            src["type"] = want
            s = src.get("settings", {})
            if is_linux:
                # Convert GDI+ property names to FreeType2 equivalents
                # read_from_file → from_file, file → text_file
                if "read_from_file" in s:
                    s["from_file"] = s.pop("read_from_file")
                if "file" in s:
                    s["text_file"] = s.pop("file")
                # Remove GDI+-only keys
                for k in ("bk_color","bk_opacity","chatlog","chatlog_lines",
                          "custom_font","ext","gradient","gradient_color",
                          "gradient_dir","gradient_opacity","opacity","vertical"):
                    s.pop(k, None)
                # FreeType2 uses color1/color2 + use_color=true (same as GDI+)
                s["use_color"] = True
                s["drop_shadow"] = s.get("drop_shadow", False)
            else:
                # Convert FreeType2 property names to GDI+ equivalents
                if "from_file" in s:
                    s["read_from_file"] = s.pop("from_file")
                if "text_file" in s:
                    s["file"] = s.pop("text_file")
                # Remove FreeType2-only keys
                for k in ("use_color", "drop_shadow", "custom_width", "word_wrap"):
                    s.pop(k, None)
            changed = True
if changed:
    with open(fp, "w") as f: json.dump(data, f, indent=4)
    print("Scene collection adapted for", "Linux (text_ft2_source)" if is_linux else "Windows (GDI+)")
else:
    print("Scene collection already compatible")
PYEOF
    fi
    success "Installed 'Radio DJ' scene collection (1 scene + overlay sources + audio)"
  fi

  # ── ALSO copy scene collection + profile to apt OBS dir ──────────
  # When Flatpak OBS starts for the first time, it MIGRATES configs from
  # the apt OBS dir (~/.config/obs-studio/). If the apt dir has stale
  # OBS 29 configs (aac encoder, Cut/Fade transitions), OBS 32 crashes.
  # Fix: ensure BOTH directories have clean OBS 32-compatible configs.
  if [ "$OBS_FLATPAK_INSTALLED" = true ] && [ "$OBS_CONFIG_BASE" != "$APT_OBS_BASE" ]; then
    APT_SCENES_DIR="$APT_OBS_BASE/basic/scenes"
    APT_PROFILES_DIR="$APT_OBS_BASE/basic/profiles"
    mkdir -p "$APT_SCENES_DIR" "$APT_PROFILES_DIR/RadioDJ" 2>/dev/null || true

    if [ -f "$OBS_SCENES_DIR/Radio DJ.json" ]; then
      cp "$OBS_SCENES_DIR/Radio DJ.json" "$APT_SCENES_DIR/Radio DJ.json"
    fi
    if [ -f "$OBS_PROFILES_DIR/RadioDJ/basic.ini" ]; then
      cp "$OBS_PROFILES_DIR/RadioDJ/basic.ini" "$APT_PROFILES_DIR/RadioDJ/basic.ini"
    fi
    if [ -f "$OBS_PROFILES_DIR/RadioDJ/streamEncoder.json" ]; then
      cp "$OBS_PROFILES_DIR/RadioDJ/streamEncoder.json" "$APT_PROFILES_DIR/RadioDJ/streamEncoder.json"
    fi
    if [ -f "$OBS_PROFILES_DIR/RadioDJ/service.json" ]; then
      cp "$OBS_PROFILES_DIR/RadioDJ/service.json" "$APT_PROFILES_DIR/RadioDJ/service.json"
    fi
    # Fix aac in the apt copy too
    if [ -f "$APT_PROFILES_DIR/RadioDJ/basic.ini" ]; then
      sed -i 's/=aac/=ffmpeg_aac/g' "$APT_PROFILES_DIR/RadioDJ/basic.ini" 2>/dev/null || true
    fi
    # Fix apt user.ini too
    APT_USER_INI="$APT_OBS_BASE/user.ini"
    if [ -f "$APT_USER_INI" ]; then
      if grep -q "^SceneCollection=" "$APT_USER_INI"; then
        sed -i 's/^SceneCollection=.*/SceneCollection=Radio DJ/' "$APT_USER_INI"
        sed -i 's/^SceneCollectionFile=.*/SceneCollectionFile=Radio DJ.json/' "$APT_USER_INI"
      fi
    fi
    success "Synced configs to apt OBS dir (prevents migration corruption)"
  fi

  if [ -f "$BOT_DIR/obs-studio/config/obs-studio/basic/profiles/RadioDJ/basic.ini" ]; then
    cp "$BOT_DIR/obs-studio/config/obs-studio/basic/profiles/RadioDJ/basic.ini" "$OBS_PROFILES_DIR/RadioDJ/basic.ini"
    # CRITICAL: Ensure ApplyServiceSettings=false in the copied profile.
    # OBS overwrites this with "true" when it respects YouTube's recommended
    # settings, which overrides our custom keyint_sec=2 and bitrate=3000
    # with YouTube defaults (keyint=250, bitrate=2500 → "Poor" stream health).
    if grep -q "^ApplyServiceSettings=" "$OBS_PROFILES_DIR/RadioDJ/basic.ini"; then
      sed -i 's/^ApplyServiceSettings=.*/ApplyServiceSettings=false/' "$OBS_PROFILES_DIR/RadioDJ/basic.ini"
    fi
    # OBS 32+ renamed the AAC encoder from "aac" to "ffmpeg_aac".
    # If the copied basic.ini still has the old name, fix it.
    # Without this, OBS 32+ errors: "Encoder ID 'aac' not found"
    sed -i 's/=aac/=ffmpeg_aac/g' "$OBS_PROFILES_DIR/RadioDJ/basic.ini"
    success "Installed 'RadioDJ' OBS profile"
  fi

  # ── Copy streamEncoder.json from template ─────────────────────
  # This file tells OBS 29 to use keyint_sec=2 (keyframes every 2s)
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

  # ── Start headless OBS via xvfb-run ─────────────────────
  if [ "$OBS_INSTALLED" = true ]; then
    if pgrep -x obs &>/dev/null; then
      success "OBS Studio already running (apt, PID: $(pgrep -x obs | head -1))"
    elif pgrep -f "flatpak run com.obsproject.Studio" &>/dev/null; then
      success "OBS Studio already running (Flatpak, PID: $(pgrep -f 'flatpak run com.obsproject.Studio' | head -1))"
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

       # Final user.ini verification RIGHT before OBS starts.
       # OBS reads user.ini on startup and if SceneCollection=Untitled,
       # it creates a blank scene and ignores --collection "Radio DJ".
       # This is our last chance to ensure it's correct.
       OBS_USER_INI_PRE="$OBS_CONFIG_BASE/user.ini"
       if [ -f "$OBS_USER_INI_PRE" ]; then
         CURRENT_SC=$(grep "^SceneCollection=" "$OBS_USER_INI_PRE" 2>/dev/null | head -1 | cut -d= -f2)
         if [ "$CURRENT_SC" != "Radio DJ" ]; then
           warn "user.ini still says SceneCollection='$CURRENT_SC' — forcing to 'Radio DJ' one last time"
           sed -i 's/^SceneCollection=.*/SceneCollection=Radio DJ/' "$OBS_USER_INI_PRE"
           sed -i 's/^SceneCollectionFile=.*/SceneCollectionFile=Radio DJ.json/' "$OBS_USER_INI_PRE"
           sed -i 's/^Profile=Unnamed/Profile=RadioDJ/' "$OBS_USER_INI_PRE" 2>/dev/null
           sed -i 's/^Profile=Untitled/Profile=RadioDJ/' "$OBS_USER_INI_PRE" 2>/dev/null
           sed -i 's/^ProfileDir=Untitled/ProfileDir=RadioDJ/' "$OBS_USER_INI_PRE" 2>/dev/null
         fi
       fi

       # CRITICAL: Force ApplyServiceSettings=false in basic.ini RIGHT BEFORE OBS starts.
       # OBS overwrites this to "true" when it connects to YouTube, which causes
       # it to use YouTube's recommended encoder settings (keyint=250, bitrate=2500)
       # instead of our custom ones (keyint_sec=2, bitrate=3000).
       # By forcing it to "false" right before OBS launches, we ensure OBS reads
       # our custom encoder values from streamEncoder.json and basic.ini.
       OBS_BASIC_INI="$OBS_PROFILES_DIR/RadioDJ/basic.ini"
       if [ -f "$OBS_BASIC_INI" ]; then
         if grep -q "ApplyServiceSettings=true" "$OBS_BASIC_INI"; then
           sed -i 's/ApplyServiceSettings=true/ApplyServiceSettings=false/' "$OBS_BASIC_INI"
           info "Fixed ApplyServiceSettings=false in basic.ini (was true)"
         elif ! grep -q "ApplyServiceSettings" "$OBS_BASIC_INI"; then
           # Not present at all — add it after [AdvOut]
           if grep -q '\[AdvOut\]' "$OBS_BASIC_INI"; then
             sed -i '/\[AdvOut\]/a ApplyServiceSettings=false' "$OBS_BASIC_INI"
           else
             echo "" >> "$OBS_BASIC_INI"
             echo "[AdvOut]" >> "$OBS_BASIC_INI"
             echo "ApplyServiceSettings=false" >> "$OBS_BASIC_INI"
           fi
           info "Added ApplyServiceSettings=false to basic.ini"
         fi
       fi

      # Delete any Untitled scene backups that OBS may have auto-created
      # from a previous run — OBS falls back to these if it can't find
      # the referenced collection.
      rm -f "$OBS_CONFIG_BASE/basic/scenes/Untitled.json" 2>/dev/null
      rm -f "$OBS_CONFIG_BASE/basic/scenes/Untitled.json.bak" 2>/dev/null
      rm -f "$OBS_CONFIG_BASE/basic/scenes/Untitled.json.bak.1" 2>/dev/null

      # Start OBS headless
      # Both apt OBS and Flatpak OBS need xvfb for headless rendering
      # (OBS requires a real OpenGL context — QT_QPA_PLATFORM=offscreen
      # doesn't work). Flatpak OBS needs --socket=x11 so the sandbox
      # can access the virtual X display from xvfb.
      if [ "$OBS_FLATPAK_INSTALLED" = true ]; then
        info "Starting Flatpak OBS Studio (headless via xvfb, browser source available)..."
        xvfb-run -a flatpak run --socket=x11 --nosocket=wayland \
          com.obsproject.Studio \
          --minimize-to-tray --disable-shutdown-check \
          --collection "Radio DJ" --profile "RadioDJ" &
        OBS_PID=$!
      else
        info "Starting headless OBS Studio (apt, no browser source)..."
        xvfb-run -a obs --minimize-to-tray --disable-shutdown-check \
          --collection "Radio DJ" --profile "RadioDJ" &
        OBS_PID=$!
      fi
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