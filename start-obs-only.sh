#!/bin/bash
# Start OBS headless only (bot is already running separately)
set -e
cd /root/the-Dj-music-bot-sidestepping-ollama

OBS_CONFIG_BASE="$HOME/.var/app/com.obsproject.Studio/config/obs-studio"
APT_OBS_BASE="$HOME/.config/obs-studio"

# Delete crash markers
for d in "$APT_OBS_BASE" "$OBS_CONFIG_BASE"; do
  [ -d "$d" ] && find "$d" -name ".sentinel" -type f -delete 2>/dev/null || true
  [ -d "$d" ] && find "$d" -name "crash_marker" -type f -delete 2>/dev/null || true
  [ -d "$d" ] && find "$d" -name "safe_mode" -type f -delete 2>/dev/null || true
done

# ── Force native overlay (no browser_source) ───────────────────────────
# The browser_source (Chromium CEF) GPU process crashes in headless Xvfb,
# killing OBS. Write a minimal scene collection with NO browser_source
# so OBS stays alive. The bot creates native text/image sources at runtime.
for SCENES_DIR in \
  "$OBS_CONFIG_BASE/basic/scenes" \
  "$APT_OBS_BASE/basic/scenes"; do
  mkdir -p "$SCENES_DIR" 2>/dev/null || true
  cat > "$SCENES_DIR/Radio DJ.json" << 'SCENEEOF'
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
done
echo "Scene collection reset (native mode — no browser_source)"

# Start D-Bus
if [ -z "$DBUS_SESSION_BUS_ADDRESS" ] || ! pgrep -x dbus-daemon >/dev/null 2>&1; then
  if command -v dbus-launch >/dev/null 2>&1; then
    eval "$(dbus-launch --sh-syntax)" 2>/dev/null || true
    [ -n "$DBUS_SESSION_BUS_ADDRESS" ] && export DBUS_SESSION_BUS_ADDRESS
  fi
fi
echo "DBUS: ${DBUS_SESSION_BUS_ADDRESS:-none}"

# Start PulseAudio
if ! pgrep -x pulseaudio >/dev/null 2>&1; then
  pulseaudio --start --fail=false --daemonize=true \
    --load="module-null-sink sink_name=radio_dj_sink sink_properties=device.description='Radio_DJ_Audio'" \
    2>/dev/null || true
  sleep 1
  pactl set-default-sink radio_dj_sink 2>/dev/null || true
fi

# Start Xvfb
if ! pgrep -f "Xvfb :420" >/dev/null 2>&1; then
  Xvfb :420 -screen 0 1280x720x24 -ac +extension GLX +render -noreset &
  sleep 1
fi
if pgrep -f "Xvfb :420" >/dev/null 2>&1; then
  echo "Xvfb running"
else
  echo "Xvfb FAILED"
  exit 1
fi

export DISPLAY=":420"
export XDG_DATA_DIRS="${XDG_DATA_DIRS:-/usr/local/share:/usr/share}:/var/lib/flatpak/exports/share:$HOME/.local/share/flatpak/exports/share"

# Start Flatpak OBS
flatpak run --socket=x11 --nosocket=wayland \
  --socket=session-bus --share=network \
  --filesystem=/root/the-Dj-music-bot-sidestepping-ollama \
  --filesystem=/tmp \
  --env=DISPLAY=":420" \
  --env=LC_ALL=C.UTF-8 \
  --env=OBS_BROWSER_DISABLE_GPU=1 \
  --env=CEF_DISABLE_GPU=1 \
  --env=QT_QPA_PLATFORM=xcb \
  com.obsproject.Studio \
  --minimize-to-tray --disable-missing-files-check \
  --collection "Radio DJ" --profile "RadioDJ" &

OBS_PID=$!
echo "OBS PID: $OBS_PID"

# Dialog dismissal watchdog
if command -v xdotool >/dev/null 2>&1; then
  (
    for i in $(seq 1 30); do
      DISPLAY=":420" xdotool key --clearmodifiers Return 2>/dev/null || true
      DISPLAY=":420" xdotool key --clearmodifiers Tab Return 2>/dev/null || true
      sleep 1
    done
  ) &
  echo "Watchdog started"
fi

# Sentinel cleanup watchdog
(
  for i in $(seq 1 20); do
    for d in "$APT_OBS_BASE" "$OBS_CONFIG_BASE"; do
      [ -d "$d" ] && find "$d" -name ".sentinel" -type f -delete 2>/dev/null || true
    done
    sleep 1
  done
) &

# Wait for WebSocket on port 4455
READY=false
for attempt in $(seq 1 45); do
  if python3 -c "
import socket, sys
s = socket.socket()
s.settimeout(1)
try:
    s.connect(('127.0.0.1', 4455))
    s.close()
    sys.exit(0)
except:
    sys.exit(1)
" 2>/dev/null; then
    READY=true
    break
  fi
  sleep 1
done

if [ "$READY" = true ]; then
  echo "OBS WebSocket READY on port 4455"
else
  echo "OBS WebSocket NOT ready after 45s"
  if kill -0 "$OBS_PID" 2>/dev/null; then
    echo "OBS process still alive but no WebSocket"
  else
    echo "OBS process died"
  fi
  # Show last OBS log
  LATEST_LOG=$(ls -t "$OBS_CONFIG_BASE/logs/"*.txt 2>/dev/null | head -1)
  if [ -n "$LATEST_LOG" ] && [ -f "$LATEST_LOG" ]; then
    echo "--- Last OBS log ---"
    tail -20 "$LATEST_LOG"
  fi
fi