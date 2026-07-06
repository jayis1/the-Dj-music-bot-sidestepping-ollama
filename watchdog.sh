#!/bin/bash
# DJ Bot Watchdog — monitors bot.py and restarts on crash
# Usage: bash watchdog.sh
cd "$(dirname "$0")"
BOT_DIR="$(pwd)"
VENV_PYTHON="$BOT_DIR/venv/bin/python"
PID_FILE="$BOT_DIR/.bot.pid"
LOG_FILE="$BOT_DIR/bot.log"
MAX_RESTARTS=10
RESTART_DELAY=5

restart_count=0

while true; do
  # Check if bot process is alive
  BOT_PID=$(pgrep -f "python.*bot.py" 2>/dev/null | head -1)

  if [ -z "$BOT_PID" ]; then
    restart_count=$((restart_count + 1))
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WATCHDOG] Bot not running (restart #$restart_count)"

    if [ "$restart_count" -gt "$MAX_RESTARTS" ]; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WATCHDOG] Max restarts ($MAX_RESTARTS) exceeded. Stopping watchdog."
      exit 1
    fi

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WATCHDOG] Waiting ${RESTART_DELAY}s before restart..."
    sleep "$RESTART_DELAY"

    # Start bot in foreground within this background process
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WATCHDOG] Starting bot..."
    "$VENV_PYTHON" bot.py 2>&1 | tee -a "$LOG_FILE" &
    BOT_PID=$!
    echo "$BOT_PID" > "$PID_FILE"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WATCHDOG] Bot started (PID: $BOT_PID)"

    # Wait for the bot process to exit (crash or normal)
    wait "$BOT_PID"
    EXIT_CODE=$?
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WATCHDOG] Bot exited with code $EXIT_CODE"

    if [ "$EXIT_CODE" -eq 0 ]; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] [WATCHDOG] Bot exited cleanly. Not restarting."
      break
    fi
    # Otherwise loop and restart
  else
    # Bot is running, just wait and check again
    sleep 10
  fi
done