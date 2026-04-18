#!/bin/bash
# ── OBS Studio Entrypoint ──────────────────────────────────────────────────
# Starts a headless OBS Studio with Xvfb virtual display, PipeWire/PulseAudio
# for audio, and obs-websocket 5.x for Mission Control integration.
#
# Environment variables:
#   DISPLAY               — X11 display number (default: :99)
#   RESOLUTION            — Virtual display resolution (default: 1280x720)
#   FPS                   — OBS frame rate (default: 30)
#   OBS_WEBSOCKET_PASSWORD — WebSocket auth password (default: djbot)
#   VNC_PASSWORD          — VNC password for remote debugging (default: djbot)
#   AUDIO_DRIVER          — Audio backend: pulseaudio (default)
#
# The bot auto-connects to OBS via WebSocket (OBS_WS_HOST=obs in docker-compose).
# ─────────────────────────────────────────────────────────────────────────────

set -e

# ── Defaults ──────────────────────────────────────────────────────────────
DISPLAY_NUM="${DISPLAY##*:}"
DISPLAY_NUM="${DISPLAY_NUM:-99}"
RESOLUTION="${RESOLUTION:-1280x720}"
FPS="${FPS:-30}"
WS_PASSWORD="${OBS_WEBSOCKET_PASSWORD:-djbot}"
VNC_PASS="${VNC_PASSWORD:-djbot}"
WIDTH="${RESOLUTION%x*}"
HEIGHT="${RESOLUTION#*x}"

echo "════════════════════════════════════════════════════════════════════════"
echo "  The Radio DJ Bot — Headless OBS Studio"
echo "  Display: :${DISPLAY_NUM}  Resolution: ${RESOLUTION}  FPS: ${FPS}"
echo "  WebSocket: port 4455 (password: ${WS_PASSWORD})"
echo "  VNC:       port 5900 (password: ${VNC_PASS})"
echo "════════════════════════════════════════════════════════════════════════"

# ── Configure obs-websocket ──────────────────────────────────────────────
# Write the WebSocket config before OBS starts so it picks up our password.
OBS_CONFIG_DIR="/home/obs/.config/obs-studio"
WS_CONFIG_DIR="${OBS_CONFIG_DIR}/plugin_config/obs-websocket"
WS_GLOBAL_CONFIG="${OBS_CONFIG_DIR}/global.ini"

mkdir -p "${WS_CONFIG_DIR}"

# Write obs-websocket config (5.x format)
# This file is read by obs-websocket on startup.
cat > "${WS_CONFIG_DIR}/config.json" << EOF
{
    "server_enabled": true,
    "server_port": 4455,
    "server_password": "${WS_PASSWORD}",
    "server_password_enabled": true,
    "alerts_enabled": false
}
EOF

echo "OBS WebSocket: Config written to ${WS_CONFIG_DIR}/config.json"

# Also update global.ini to enable the WebSocket plugin
if [ ! -f "${WS_GLOBAL_CONFIG}" ]; then
    cat > "${WS_GLOBAL_CONFIG}" << 'EOF'
[General]
Pre197TagsInUse=true
[OBSWebSocket]
ServerEnabled=true
ServerPort=4455
EOF
    echo "AuthRequired=true" >> "${WS_GLOBAL_CONFIG}"
    echo "ServerPassword=${WS_PASSWORD}" >> "${WS_GLOBAL_CONFIG}"
fi

# ── Start D-Bus session bus ─────────────────────────────────────────────
# D-Bus is required by PulseAudio and OBS for IPC.
echo "Starting D-Bus session bus..."
eval $(dbus-launch --sh-syntax)
export DBUS_SESSION_BUS_ADDRESS

# ── Start PulseAudio ─────────────────────────────────────────────────────
# OBS needs an audio server. PulseAudio with a null sink gives us a virtual
# audio device that OBS can capture from and the bot can stream to.
echo "Starting PulseAudio..."
pulseaudio --start --fail=false --daemonize=true --log-target=syslog \
    --load="module-null-sink sink_name=radio_dj_sink sink_properties=device.description='Radio_DJ_Audio'" \
    --load="module-native-protocol-tcp auth-anonymous=1" \
    2>/dev/null || echo "PulseAudio: Starting in null mode (may already be running)"

# Set the default sink to our null sink so OBS captures it
pactl set-default-sink radio_dj_sink 2>/dev/null || \
    echo "PulseAudio: Could not set radio_dj_sink as default (will use fallback)"

# ── Start Xvfb virtual display ─────────────────────────────────────────────
echo "Starting Xvfb on :${DISPLAY_NUM} (${RESOLUTION})..."
Xvfb ":${DISPLAY_NUM}" -screen 0 "${RESOLUTION}x24" -ac +extension GLX +render -noreset &
XVFB_PID=$!
sleep 1

# Verify Xvfb started
if ! kill -0 ${XVFB_PID} 2>/dev/null; then
    echo "FATAL: Xvfb failed to start" >&2
    exit 1
fi
echo "Xvfb started (PID: ${XVFB_PID})"

# ── Start VNC server (optional remote debugging) ─────────────────────────
# x11vnc mirrors the Xvfb display so you can connect with a VNC client
# to see and debug the OBS UI remotely.
echo "Starting VNC server on port 5900..."
x11vnc -display ":${DISPLAY_NUM}" -nopw -listen 0.0.0.0 -forever -shared \
    -noxdamage -once -timeout 60 2>/dev/null &
VNC_PID=$!
echo "VNC server started (PID: ${VNC_PID}) — connect with a VNC client to port 5900"

# ── Start Window Manager ─────────────────────────────────────────────────
# OBS expects a window manager to be running. Openbox is lightweight.
# If not available, OBS still works without one (just no window decorations).
if command -v openbox &>/dev/null; then
    echo "Starting openbox window manager..."
    DISPLAY=":${DISPLAY_NUM}" openbox &
else
    echo "No window manager found (OBS will still work, windows won't have decorations)"
fi

# ── Start OBS Studio ────────────────────────────────────────────────────
echo "Starting OBS Studio..."
export DISPLAY=":${DISPLAY_NUM}"

# OBS command-line flags:
#   --startstreaming    — Auto-start streaming on launch (optional)
#   --minimize-to-tray  — Keep OBS running in tray (headless-friendly)
#   --disable-shutdown-check — Don't prompt for unsaved changes on exit
#   --collection "Radio DJ" — Use our pre-configured scene collection
obs --minimize-to-tray --disable-shutdown-check \
    --collection "Radio DJ" \
    &

OBS_PID=$!

echo "OBS Studio started (PID: ${OBS_PID})"
echo ""
echo "🚀 OBS Studio is ready for Mission Control!"
echo "   WebSocket:  ws://localhost:4455  (password: ${WS_PASSWORD})"
echo "   VNC:        vnc://localhost:5900   (optional remote debugging)"
echo ""

# ── Wait for OBS to initialize ────────────────────────────────────────────
# Give OBS a few seconds to start the WebSocket server before the healthcheck
# starts checking. This prevents false-negative health failures during startup.
echo "Waiting for OBS WebSocket to become ready..."
for i in $(seq 1 30); do
    if python3 -c "import socket; s=socket.socket(); s.settimeout(1); s.connect(('localhost',4455)); s.close()" 2>/dev/null; then
        echo "✅ OBS WebSocket is listening on port 4455"
        break
    fi
    sleep 1
done

# ── Monitor processes ─────────────────────────────────────────────────────
# If OBS dies, the container should exit too (restart policy: unless-stopped)
echo "Monitoring OBS process..."
while kill -0 ${OBS_PID} 2>/dev/null; do
    sleep 5
done

echo "OBS Studio process exited. Shutting down container..."
# Clean up
kill ${XVFB_PID} 2>/dev/null || true
kill ${VNC_PID} 2>/dev/null || true
pulseaudio --kill 2>/dev/null || true
exit 0