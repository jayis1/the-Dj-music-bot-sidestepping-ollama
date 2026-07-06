#!/bin/bash
# ──────────────────────────────────────────────────────────────────────
# daily_improve.sh — Daily auto-improvement script for the DJ Music Bot
#
# This script runs daily via cron and performs several maintenance
# and improvement tasks:
#   1. Updates yt-dlp to the latest version (critical for YouTube)
#   2. Updates Python dependencies
#   3. Rotates bot_activity.log if it's too large (>50MB)
#   4. Checks bot health and restarts if crashed
#   5. Validates Python syntax of all bot files
#   6. Reports status to the log
# ──────────────────────────────────────────────────────────────────────

BOT_DIR="/root/the-Dj-music-bot-sidestepping-ollama"
LOG_FILE="$BOT_DIR/bot_activity.log"
IMPROVE_LOG="$BOT_DIR/scripts/daily_improve.log"
VENV="$BOT_DIR/venv/bin/activate"
MAX_LOG_SIZE=$((50 * 1024 * 1024))  # 50MB

cd "$BOT_DIR" || exit 1

timestamp() { date '+%Y-%m-%d %H:%M:%S'; }

echo "============================================" | tee -a "$IMPROVE_LOG"
echo "[$(timestamp)] Daily DJ Bot Improvement Run" | tee -a "$IMPROVE_LOG"
echo "============================================" | tee -a "$IMPROVE_LOG"

# ─── 1. Update yt-dlp ─────────────────────────────────────────────────
echo "[$(timestamp)] Updating yt-dlp..." | tee -a "$IMPROVE_LOG"
source "$VENV"
pip install --upgrade --quiet yt-dlp 2>&1 | tail -3 | tee -a "$IMPROVE_LOG"
echo "[$(timestamp)] yt-dlp version: $(python -c 'import yt_dlp; print(yt_dlp.version.__version__)' 2>/dev/null || echo 'unknown')" | tee -a "$IMPROVE_LOG"

# ─── 2. Update edge-tts and other deps ────────────────────────────────
echo "[$(timestamp)] Updating key Python dependencies..." | tee -a "$IMPROVE_LOG"
pip install --upgrade --quiet edge-tts aiohttp discord.py 2>&1 | tail -3 | tee -a "$IMPROVE_LOG"

# ─── 3. Rotate log if too large ───────────────────────────────────────
if [ -f "$LOG_FILE" ]; then
    LOG_SIZE=$(stat -c%s "$LOG_FILE" 2>/dev/null || echo 0)
    if [ "$LOG_SIZE" -gt "$MAX_LOG_SIZE" ]; then
        echo "[$(timestamp)] Log file is $(($LOG_SIZE / 1024 / 1024))MB — rotating..." | tee -a "$IMPROVE_LOG"
        # Keep last 1000 lines as recent, archive the rest
        tail -1000 "$LOG_FILE" > "$LOG_FILE.tmp"
        mv "$LOG_FILE" "${LOG_FILE}.$(date +%Y%m%d).bak"
        mv "$LOG_FILE.tmp" "$LOG_FILE"
        echo "[$(timestamp)] Log rotated. Old log archived." | tee -a "$IMPROVE_LOG"
    else
        echo "[$(timestamp)] Log size OK: $(($LOG_SIZE / 1024 / 1024))MB" | tee -a "$IMPROVE_LOG"
    fi
fi

# ─── 4. Validate Python syntax ────────────────────────────────────────
echo "[$(timestamp)] Validating Python syntax..." | tee -a "$IMPROVE_LOG"
SYNTAX_ERRORS=0
for pyfile in bot.py config.py utils/*.py cogs/*.py; do
    if [ -f "$pyfile" ]; then
        if ! python -m py_compile "$pyfile" 2>/dev/null; then
            echo "[$(timestamp)] SYNTAX ERROR in $pyfile!" | tee -a "$IMPROVE_LOG"
            SYNTAX_ERRORS=$((SYNTAX_ERRORS + 1))
        fi
    fi
done
if [ "$SYNTAX_ERRORS" -eq 0 ]; then
    echo "[$(timestamp)] All Python files pass syntax check." | tee -a "$IMPROVE_LOG"
else
    echo "[$(timestamp)] WARNING: $SYNTAX_ERRORS file(s) have syntax errors!" | tee -a "$IMPROVE_LOG"
fi

# ─── 5. Check bot health ──────────────────────────────────────────────
echo "[$(timestamp)] Checking bot health..." | tee -a "$IMPROVE_LOG"
if pgrep -f "python bot.py" > /dev/null 2>&1; then
    echo "[$(timestamp)] Bot is RUNNING (PID: $(pgrep -f 'python bot.py'))" | tee -a "$IMPROVE_LOG"
else
    echo "[$(timestamp)] Bot is NOT running! Attempting restart..." | tee -a "$IMPROVE_LOG"
    # Check if OBS is still running
    if pgrep -x obs > /dev/null 2>&1; then
        echo "[$(timestamp)] OBS is running. Starting bot..." | tee -a "$IMPROVE_LOG"
        cd "$BOT_DIR" && source "$VENV" && nohup python bot.py >> "$LOG_FILE" 2>&1 &
        sleep 5
        if pgrep -f "python bot.py" > /dev/null 2>&1; then
            echo "[$(timestamp)] Bot restarted successfully!" | tee -a "$IMPROVE_LOG"
        else
            echo "[$(timestamp)] Bot restart FAILED! Check logs." | tee -a "$IMPROVE_LOG"
        fi
    else
        echo "[$(timestamp)] OBS is not running. Full restart needed: bash start.sh start" | tee -a "$IMPROVE_LOG"
    fi
fi

# ─── 6. Check OBS health ──────────────────────────────────────────────
if pgrep -x obs > /dev/null 2>&1; then
    echo "[$(timestamp)] OBS is RUNNING (PID: $(pgrep -x obs))" | tee -a "$IMPROVE_LOG"
else
    echo "[$(timestamp)] OBS is NOT running!" | tee -a "$IMPROVE_LOG"
fi

# ─── 7. Disk space check ──────────────────────────────────────────────
DISK_USAGE=$(df -h "$BOT_DIR" | awk 'NR==2{print $5}' | tr -d '%')
if [ "$DISK_USAGE" -gt 90 ]; then
    echo "[$(timestamp)] WARNING: Disk usage at ${DISK_USAGE}%! Cleaning old logs..." | tee -a "$IMPROVE_LOG"
    # Clean old log backups
    find "$BOT_DIR" -name "*.log.bak" -mtime +7 -delete 2>/dev/null
    find "$BOT_DIR" -name "bot_activity.log.*.bak" -mtime +3 -delete 2>/dev/null
    echo "[$(timestamp)] Old log backups cleaned." | tee -a "$IMPROVE_LOG"
else
    echo "[$(timestamp)] Disk usage: ${DISK_USAGE}% — OK" | tee -a "$IMPROVE_LOG"
fi

# ─── 8. Summary ───────────────────────────────────────────────────────
echo "============================================" | tee -a "$IMPROVE_LOG"
echo "[$(timestamp)] Daily improvement run complete." | tee -a "$IMPROVE_LOG"
echo "============================================" | tee -a "$IMPROVE_LOG"
echo "" | tee -a "$IMPROVE_LOG"