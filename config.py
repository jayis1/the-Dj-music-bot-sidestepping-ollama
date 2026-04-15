# config.py

# It is recommended to use environment variables for sensitive data.
# However, you can hardcode the values here for simplicity.
import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass  # python-dotenv not installed; falling back to system environment variables

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")

# You can change the bot's command prefix here
COMMAND_PREFIX = "?"

# Radio DJ mode station name
STATION_NAME = os.environ.get("STATION_NAME", "MBot")

# Emojis for UI
PLAY_EMOJI = "▶️"
PAUSE_EMOJI = "⏸️"
SKIP_EMOJI = "⏭️"
QUEUE_EMOJI = "🎵"
ERROR_EMOJI = "❌"
SUCCESS_EMOJI = "✅"

# Discord Channel ID for sending bot logs (errors, warnings)
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", 0) or 0) or None

# DJ Mode — Default voice for the radio DJ (Microsoft Edge TTS voice name)
# Change this if you want a different default voice.
# Use ?djvoices in Discord to see available voices.
DJ_VOICE = "en-US-AriaNeural"

# Emojis for DJ mode
DJ_EMOJI = "🎙️"
