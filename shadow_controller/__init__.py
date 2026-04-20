"""
Shadow Controller — Hermes-powered DJ Bot Operator

6 autonomous loops keep the 420 Radio DJ on the air:
  1. Cookie Fixer     — fresh YouTube cookies, always
  2. Queue Watchdog   — never run dry
  3. Stream Monitor    — YouTube Live stays live
  4. Playlist Finder   — discover music via Hermes
  5. Discord Watcher   — fan requests (optional)
  6. Suno Creator      — Hermes makes original music on Suno.com

Run with: python -m shadow_controller
"""

__version__ = "1.0.0"

from .main import ShadowController, load_config, main
from .api_client import MissionControlClient
from .browser_manager import BrowserManager
from .alerts import AlertSystem, AlertLevel
from .cookie_fixer import CookieFixer
from .queue_watchdog import QueueWatchdog
from .stream_monitor import StreamMonitor
from .playlist_finder import PlaylistFinder
from .discord_watcher import DiscordWatcher
from .suno_creator import SunoCreator
