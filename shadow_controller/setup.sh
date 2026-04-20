#!/bin/bash
# ────────────────────────────────────────────────────────────────────
# Shadow Controller — Setup Script
# Installs dependencies, configures Firefox profile, sets up systemd
# ────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}"
echo "╔══════════════════════════════════════════════════════════╗"
echo "║        Shadow Controller — Setup Wizard v1.0             ║"
echo "║        The silent operator behind the 420 Radio DJ       ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# ── Step 1: System dependencies ────────────────────────────────
echo -e "${YELLOW}▸ Step 1: Checking system dependencies...${NC}"

if command -v python3 &>/dev/null; then
    PYVER=$(python3 --version 2>&1)
    echo -e "  ${GREEN}✓${NC} Python: $PYVER"
else
    echo -e "  ${RED}✗${NC} Python 3 not found — installing..."
    sudo apt update && sudo apt install -y python3 python3-pip python3-venv
fi

if command -v firefox &>/dev/null || command -v firefox-esr &>/dev/null; then
    echo -e "  ${GREEN}✓${NC} Firefox installed"
else
    echo -e "  ${YELLOW}⚡${NC} Firefox not found — installing firefox-esr..."
    sudo apt update && sudo apt install -y firefox-esr
fi

# ── Step 2: Python venv + packages ─────────────────────────────
echo -e "${YELLOW}▸ Step 2: Setting up Python environment...${NC}"

if [ ! -d "venv" ]; then
    python3 -m venv venv || {
        echo -e "  ${RED}✗${NC} Failed to create virtual environment"
        echo -e "  Make sure python3-venv is installed: sudo apt install python3-venv"
        exit 1
    }
    echo -e "  ${GREEN}✓${NC} Virtual environment created"
else
    echo -e "  ${GREEN}✓${NC} Virtual environment already exists"
fi

# Activate the venv (check the activate script exists first)
if [ ! -f "venv/bin/activate" ]; then
    echo -e "  ${RED}✗${NC} venv/bin/activate not found — venv is broken, removing and recreating..."
    rm -rf venv
    python3 -m venv venv || {
        echo -e "  ${RED}✗${NC} Failed to create virtual environment"
        echo -e "  Make sure python3-venv is installed: sudo apt install python3-venv"
        exit 1
    }
fi

source venv/bin/activate || {
    echo -e "  ${RED}✗${NC} Failed to activate virtual environment"
    exit 1
}
echo -e "  ${GREEN}✓${NC} Virtual environment activated"

pip install --upgrade pip --quiet 2>&1 || {
    echo -e "  ${YELLOW}⚠${NC} pip upgrade had issues (continuing anyway)"
}

if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt --quiet 2>&1 || {
        echo -e "  ${YELLOW}⚠${NC} Some packages failed to install, retrying without quiet mode..."
        pip install -r requirements.txt || {
            echo -e "  ${RED}✗${NC} Package installation failed"
            exit 1
        }
    }
    echo -e "  ${GREEN}✓${NC} Python packages installed"
else
    echo -e "  ${RED}✗${NC} requirements.txt not found"
    exit 1
fi

# Install Playwright browsers
echo -e "${YELLOW}▸ Step 3: Installing Playwright browser...${NC}"
if command -v playwright &>/dev/null; then
    playwright install firefox 2>&1 || {
        echo -e "  ${YELLOW}⚠${NC} Playwright Firefox install had issues (may already be installed)"
    }
    playwright install-deps firefox 2>&1 || {
        echo -e "  ${YELLOW}⚠${NC} Some system deps for Playwright may need: sudo playwright install-deps firefox"
    }
    echo -e "  ${GREEN}✓${NC} Playwright ready"
else
    echo -e "  ${YELLOW}⚠${NC} playwright command not found — will be available after pip install"
fi

# ── Step 4: Configuration ─────────────────────────────────────
echo -e "${YELLOW}▸ Step 4: Configuration...${NC}"

if [ ! -f "config.yaml" ]; then
    cp config.example.yaml config.yaml
    echo -e "  ${GREEN}✓${NC} Created config.yaml from template"
    echo ""
    echo -e "  ${RED}⚠  IMPORTANT: Edit config.yaml before running!${NC}"
    echo -e "  Required settings:"
    echo -e "    • guild_id          — Your Discord server ID"
    echo -e "    • bot_api_url       — DJ bot Mission Control URL"
    echo -e "    • discord_webhook_url — Discord webhook for alerts"
    echo ""
else
    echo -e "  ${GREEN}✓${NC} config.yaml already exists"
fi

if [ ! -f ".env" ]; then
    cp .env.example .env
    echo -e "  ${GREEN}✓${NC} Created .env from template"
else
    echo -e "  ${GREEN}✓${NC} .env already exists"
fi

# ── Step 5: Firefox profile detection ───────────────────────────
echo -e "${YELLOW}▸ Step 5: Firefox profile...${NC}"

FF_PROFILE=""
for base_dir in "$HOME/.mozilla/firefox" "$HOME/snap/firefox/common/.mozilla/firefox"; do
    if [ -d "$base_dir" ]; then
        # Find default profile
        if [ -f "$base_dir/profiles.ini" ]; then
            FF_PROFILE=$(grep -A5 "\[Profile" "$base_dir/profiles.ini" | grep "Path=" | head -1 | cut -d= -f2)
            if [ -n "$FF_PROFILE" ]; then
                FF_PROFILE="$base_dir/$FF_PROFILE"
                break
            fi
        fi
        # Fallback: find .default-release
        FF_PROFILE=$(find "$base_dir" -maxdepth 1 -name "*.default-release" -type d 2>/dev/null | head -1)
        if [ -n "$FF_PROFILE" ]; then
            break
        fi
    fi
done

if [ -n "$FF_PROFILE" ]; then
    echo -e "  ${GREEN}✓${NC} Firefox profile found: $FF_PROFILE"
    echo -e "  ${CYAN}ℹ${NC}  Make sure you've logged into YouTube in this Firefox profile!"
    echo -e "  ${CYAN}ℹ${NC}  Make sure the cookie.txt plugin is installed: https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/"
else
    echo -e "  ${YELLOW}⚠${NC} No Firefox profile found"
    echo -e "  ${CYAN}ℹ${NC}  Launch Firefox, log into YouTube, then re-run this script"
fi

# ── Step 6: Discord Watcher Bot (Optional) ──────────────────────
echo ""
echo -e "${YELLOW}▸ Step 6: Discord Watcher Bot (optional)...${NC}"
echo ""
echo -e "  ${CYAN}Fan request watching is DISABLED by default.${NC}"
echo "  To enable it later, you need a SEPARATE Discord bot token"
echo "  (not the DJ bot's token). Create one at:"
echo "  ${CYAN}https://discord.com/developers/applications${NC}"
echo ""
echo "  Then set in config.yaml:"
echo "    fan_request_enabled: true"
echo "    discord_watcher_token: YOUR_TOKEN"
echo "    fan_request_channel_id: YOUR_CHANNEL_ID"

# ── Step 7: Systemd service ─────────────────────────────────────
echo ""
echo -e "${YELLOW}▸ Step 7: Systemd service...${NC}"

echo "  Install as a system service? This lets Shadow Controller"
echo "  start automatically on boot and restart on crashes."
echo ""
read -rp "  Install systemd service? [y/N]: " INSTALL_SERVICE

if [[ "$INSTALL_SERVICE" =~ ^[Yy]$ ]]; then
    # Update paths in the service file
    SERVICE_FILE="systemd/shadow-controller.service"
    sed -i "s|__SCRIPT_DIR__|$SCRIPT_DIR|g" "$SERVICE_FILE"
    sed -i "s|__USER__|$USER|g" "$SERVICE_FILE"
    
    sudo cp "$SERVICE_FILE" /etc/systemd/system/shadow-controller.service
    sudo systemctl daemon-reload
    sudo systemctl enable shadow-controller
    
    echo -e "  ${GREEN}✓${NC} Service installed and enabled"
    echo ""
    echo "  Commands:"
    echo "    sudo systemctl start shadow-controller   # Start now"
    echo "    sudo systemctl status shadow-controller   # Check status"
    echo "    sudo journalctl -u shadow-controller -f   # View logs"
else
    echo -e "  ${YELLOW}⊘${NC} Skipped systemd service installation"
    echo "  You can run manually: ./run.sh"
fi

# ── Done ────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗"
echo -e "║  Shadow Controller setup complete!                       ║"
echo -e "╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "  Next steps:"
echo "    1. Edit ${CYAN}config.yaml${NC} — add your guild ID and webhook URL"
echo "    2. Log into YouTube in Firefox (if not already)"
echo "    3. Export cookies once with the cookie.txt plugin"
echo "    4. Start: ${GREEN}./run.sh${NC} or ${GREEN}sudo systemctl start shadow-controller${NC}"
echo ""
echo "  Optional later:"
echo "    • Enable Discord fan requests in config.yaml (needs separate bot token)"
echo ""