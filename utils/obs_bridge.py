"""
utils/obs_bridge.py — OBS Studio WebSocket Bridge for Mission Control.

Provides bidirectional control of OBS Studio via the obs-websocket 5.x protocol.
Used by the Mission Control dashboard to manage scenes, sources, transitions,
streaming, recording, and filters — all from the web UI.

Protocol docs: https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.json
Python client: https://github.com/obs-websocket-community-project/obsws-python

obsws-python ReqClient API (v1.8.0):
  The ReqClient has NAMED methods for every request, e.g.:
    client.start_stream()
    client.stop_stream()
    client.toggle_stream()
    client.get_stream_status()
    client.set_current_program_scene(name="My Scene")
    client.set_current_scene_transition(name="Cut")
    client.set_studio_mode_enabled(enabled=True)
    client.set_scene_item_enabled(scene_name="Scene", item_id=1, enabled=True)
    client.set_input_mute(name="Mic", muted=True)
    client.set_input_volume(name="Mic", vol_db=0.0)
    client.get_input_settings(name="Browser Overlay")
    client.get_scene_item_list(name="Scene")
    client.save_source_screenshot(name="Source", img_format="png", file_path="", width=1280, height=720, quality=-1)
    ...

  IMPORTANT: obsws-python uses its OWN parameter names, NOT the OBS WebSocket RPC names.
  For example, the RPC "sceneName" becomes "name", "inputName" becomes "name",
  "sceneItemId" becomes "item_id", etc. Always check the actual Python signature
  before using a method.

  There is NO generic client.call() method. The low-level client.send(param, data, raw=True)
  sends a raw request and returns a raw dict, but named methods are preferred because
  they return properly typed dataclass objects.

Setup:
  1. Install OBS Studio + obs-websocket (bundled since OBS 28)
  2. bash start.sh — auto-installs OBS, configures WebSocket, starts headless
  3. Open Mission Control → OBS Studio page

Graceful degradation:
  If OBS is not running or not reachable, all API calls return
  {"connected": False, "error": "..."} — the bot never crashes.
  A connection-backoff prevents spamming connection attempts when OBS is down.
"""

import logging
import time
import os
import sys

log = logging.getLogger("obs-bridge")

# ── Platform-aware OBS source kinds ──────────────────────────────────────
# OBS on Windows uses text_gdiplus_v2 for text; Linux/macOS use text_freetype2_v2.
# Using the wrong kind returns obs-websocket error 605 "input kind not supported".
_IS_LINUX = sys.platform == "linux"
_TEXT_INPUT_KIND = "text_ft2_source_v2" if _IS_LINUX else "text_gdiplus_v2"


def _text_settings(text, font_face, font_style, font_size, color,
                    align=0, valign=0, read_from_file=False, file_path=""):
    """Build platform-appropriate text source settings.

    text_ft2_source_v2 (Linux/macOS FreeType2) uses:
      - color1/color2 for gradient (same = solid), from_file, text_file
    text_gdiplus_v2 (Windows GDI+) uses:
      - color1/color2 for gradient, read_from_file, file
    Both accept numeric align (0=left,1=center,2=right) and valign.
    """
    font = {"face": font_face, "style": font_style, "size": font_size}
    base = {
        "text": text,
        "font": font,
        "color1": color,
        "color2": color,
        "align": align,
        "valign": valign,
    }

    if _IS_LINUX:
        base["from_file"] = read_from_file
        if read_from_file and file_path:
            base["text_file"] = file_path
        base["drop_shadow"] = False
        base["outline"] = False
        base["use_color"] = True
    else:
        base["read_from_file"] = read_from_file
        if read_from_file and file_path:
            base["file"] = file_path
        base["opacity"] = 100
        base["gradient"] = False
        base["bk_color"] = 0
        base["bk_opacity"] = 0

    return base

# ── Suppress obsws-python's verbose logging ──────────────────────────────
# obsws_python logs "Connecting with parameters: ..." at INFO level on every
# connection attempt, plus full tracebacks on failure. We only want our own
# warnings, not the library's stdout spam.
logging.getLogger("obsws_python").setLevel(logging.CRITICAL)

# ── OBS WebSocket Connection ─────────────────────────────────────────────

# Lazy import — obsws is optional (not everyone needs OBS)
_obsws = None


def _get_obsws():
    """Lazily import obsws-python. Returns None if not installed."""
    global _obsws
    if _obsws is not None:
        return _obsws
    try:
        import obsws_python as obsws
        _obsws = obsws
        return _obsws
    except ImportError:
        log.debug("obsws-python not installed — OBS integration disabled")
        return None


class OBSBridge:
    """Manages the connection to OBS Studio via obs-websocket 5.x.

    This is a stateless request/response bridge — it connects, sends a
    request, and disconnects. This avoids the complexity of maintaining
    a persistent WebSocket connection with reconnection logic, event
    subscriptions, and thread safety concerns.

    Connection backoff:
      When OBS is unreachable, we record the failure time and don't try
      again for CONNECTION_RETRY_INTERVAL seconds. This prevents log spam
      and performance degradation from repeated failed TCP connects.

    For real-time event subscriptions (e.g., scene change notifications),
    a future version could add a persistent connection with callbacks.
    """

    # Don't retry connection for this many seconds after a failure
    CONNECTION_RETRY_INTERVAL = 30

    def __init__(self, host: str = "localhost", port: int = 4455, password: str = "", enabled: bool = True):
        self.host = host
        self.port = port
        self.password = password
        self.enabled = enabled and bool(password)  # Don't try if no password
        self._last_status = None
        self._last_status_time = 0
        self._status_cache_ttl = 5  # seconds
        self._last_connect_fail = 0  # timestamp of last failed connection
        self._connection_logged = False  # Only log "configured" once

        if self.enabled:
            obsws = _get_obsws()
            if obsws is None:
                self.enabled = False
                log.info("OBS Bridge: Disabled (obsws-python not installed)")
            else:
                log.info(f"OBS Bridge: Configured → {self.host}:{self.port}")
        else:
            if not password:
                log.info("OBS Bridge: Disabled (no OBS_WS_PASSWORD set)")

    def _should_try_connect(self):
        """Check if enough time has passed since the last failed connection."""
        if self._last_connect_fail == 0:
            return True  # Never tried before
        elapsed = time.time() - self._last_connect_fail
        return elapsed >= self.CONNECTION_RETRY_INTERVAL

    def _connect(self):
        """Create a new OBS WebSocket client connection.

        Returns None if:
          - obsws-python is not installed
          - OBS is not reachable (connection refused, timeout, etc.)
          - We're in a connection-backoff period
        """
        obsws = _get_obsws()
        if obsws is None:
            return None

        # Check connection backoff — don't spam failed connects
        if not self._should_try_connect():
            return None

        # Suppress the library's own verbose logging during connect
        obsws_logger = logging.getLogger("obsws_python")
        old_level = obsws_logger.level

        try:
            obsws_logger.setLevel(logging.CRITICAL)
            client = obsws.ReqClient(
                host=self.host,
                port=self.port,
                password=self.password,
                timeout=5,
            )
            # Success — reset backoff
            self._last_connect_fail = 0
            if not self._connection_logged:
                log.info(f"OBS Bridge: Connected to {self.host}:{self.port}")
                self._connection_logged = True
            return client

        except ConnectionRefusedError:
            # OBS is not running or WebSocket server is not listening
            self._last_connect_fail = time.time()
            log.warning(
                f"OBS Bridge: Connection refused on {self.host}:{self.port} — "
                f"OBS is not running or WebSocket is not enabled. "
                f"Install OBS with: sudo apt install obs-studio, "
                f"or start it with: bash start.sh. "
                f"Retrying in {self.CONNECTION_RETRY_INTERVAL}s."
            )
            return None

        except Exception as e:
            self._last_connect_fail = time.time()
            # Only log the first line of the error — obsws dumps full tracebacks
            error_brief = str(e).split('\n')[0]
            log.warning(
                f"OBS Bridge: Cannot connect to {self.host}:{self.port} — "
                f"{error_brief}. Will retry in {self.CONNECTION_RETRY_INTERVAL}s."
            )
            return None

        finally:
            obsws_logger.setLevel(old_level)

    def _safe_call(self, func, **kwargs):
        """Send a request to OBS using a named method on ReqClient.

        Connects, calls the method, disconnects — safe for Flask's
        synchronous context. Returns a result dict for the API.

        Args:
            func: One of the named ReqClient methods (e.g. client.start_stream).
            **kwargs: Keyword arguments passed to the method.

        Returns:
            dict with keys: connected (bool), status ("ok"/"error"), data (dict),
            and optionally error (str).
        """
        if not self.enabled:
            return {"error": "OBS Bridge is disabled", "connected": False}

        client = self._connect()
        if client is None:
            # Check if we're in backoff
            if self._last_connect_fail > 0:
                return {"error": "OBS is not running or WebSocket is not enabled", "connected": False}
            return {"error": "Could not connect to OBS", "connected": False}

        try:
            # Call the named method on the ReqClient
            response = func(client, **kwargs)
            result = {"connected": True, "status": "ok"}

            # obsws-python returns dataclass objects for typed responses,
            # or raw dicts when using send(raw=True). Named methods return
            # dataclasses. Convert to a dict for JSON serialization.
            if response is None:
                result["data"] = {}
            elif isinstance(response, dict):
                result["data"] = response
            elif hasattr(response, "__dict__") and not isinstance(response, (str, int, float, bool)):
                # Dataclass-like object — convert attributes to dict
                data = {}
                for key, value in response.__dict__.items():
                    if not key.startswith("_"):
                        # Recursively convert nested dataclasses
                        if hasattr(value, "__dict__") and not isinstance(value, (str, int, float, bool, list)):
                            data[key] = {
                                k: v for k, v in value.__dict__.items()
                                if not k.startswith("_")
                            }
                        elif isinstance(value, list):
                            data[key] = [
                                {
                                    k: v for k, v in item.__dict__.items()
                                    if not k.startswith("_")
                                }
                                if hasattr(item, "__dict__") and not isinstance(item, (str, int, float, bool))
                                else item
                                for item in value
                            ]
                        else:
                            data[key] = value
                result["data"] = data
            else:
                result["data"] = {"value": response}

            return result

        except ConnectionRefusedError:
            self._last_connect_fail = time.time()
            return {"error": "OBS is not running", "connected": False}

        except Exception as e:
            error_msg = str(e)
            log.warning(f"OBS Bridge: Request failed → {error_msg}")
            return {"error": error_msg, "connected": True}
        finally:
            try:
                client.disconnect()
            except Exception:
                pass

    # ══════════════════════════════════════════════════════════════════════
    # PUBLIC API — called from Flask routes
    # ══════════════════════════════════════════════════════════════════════

    def get_status(self) -> dict:
        """Get overall OBS status: streaming, recording, replay buffer, current scene."""
        # Use cached status if fresh
        if self._last_status and (time.time() - self._last_status_time) < self._status_cache_ttl:
            return self._last_status

        result = {
            "connected": False,
            "streaming": False,
            "recording": False,
            "replay_buffer": False,
            "current_scene": "",
            "scenes": [],
            "transitions": [],
        }

        if not self.enabled:
            return result

        client = self._connect()
        if client is None:
            return result

        result["connected"] = True

        try:
            # Get streaming status
            try:
                resp = client.get_stream_status()
                if hasattr(resp, "output_active"):
                    result["streaming"] = resp.output_active
                elif isinstance(resp, dict):
                    result["streaming"] = resp.get("outputActive", False)
            except Exception:
                pass

            # Get recording status
            try:
                resp = client.get_record_status()
                if hasattr(resp, "output_active"):
                    result["recording"] = resp.output_active
                elif isinstance(resp, dict):
                    result["recording"] = resp.get("outputActive", False)
            except Exception:
                pass

            # Get replay buffer status
            try:
                resp = client.get_replay_buffer_status()
                if hasattr(resp, "output_active"):
                    result["replay_buffer"] = resp.output_active
                elif isinstance(resp, dict):
                    result["replay_buffer"] = resp.get("outputActive", False)
            except Exception:
                pass

            # Get current scene
            try:
                resp = client.get_current_program_scene()
                if hasattr(resp, "scene_name"):
                    result["current_scene"] = resp.scene_name
                elif isinstance(resp, dict):
                    result["current_scene"] = resp.get("currentProgramSceneName", "")
            except Exception:
                pass

            # Get scene list
            try:
                resp = client.get_scene_list()
                if hasattr(resp, "scenes"):
                    result["scenes"] = [
                        s.scene_name if hasattr(s, "scene_name") else s.get("sceneName", "")
                        for s in resp.scenes
                    ]
                elif isinstance(resp, dict):
                    result["scenes"] = [
                        s.get("sceneName", str(s)) for s in resp.get("scenes", [])
                    ]
            except Exception:
                pass

            # Get transition list
            try:
                resp = client.get_scene_transition_list()
                if hasattr(resp, "transitions"):
                    result["transitions"] = [
                        t.name if hasattr(t, "name") else t.get("transitionName", "")
                        for t in resp.transitions
                    ]
                elif isinstance(resp, dict):
                    result["transitions"] = [
                        t.get("transitionName", str(t))
                        for t in resp.get("transitions", [])
                    ]
            except Exception:
                pass

        except Exception as e:
            log.debug(f"OBS Bridge: Status query failed → {e}")
            result["error"] = str(e)
        finally:
            try:
                client.disconnect()
            except Exception:
                pass

        self._last_status = result
        self._last_status_time = time.time()
        return result

    # ── Streaming Control ─────────────────────────────────────────────────

    def start_streaming(self) -> dict:
        """Start OBS streaming."""
        return self._safe_call(lambda c: c.start_stream())

    def stop_streaming(self) -> dict:
        """Stop OBS streaming."""
        return self._safe_call(lambda c: c.stop_stream())

    def toggle_streaming(self) -> dict:
        """Toggle OBS streaming on/off."""
        return self._safe_call(lambda c: c.toggle_stream())

    # ── Recording Control ─────────────────────────────────────────────────

    def start_recording(self) -> dict:
        """Start OBS recording."""
        return self._safe_call(lambda c: c.start_record())

    def stop_recording(self) -> dict:
        """Stop OBS recording."""
        return self._safe_call(lambda c: c.stop_record())

    def toggle_recording(self) -> dict:
        """Toggle OBS recording on/off."""
        return self._safe_call(lambda c: c.toggle_record())

    # ── Scene Control ─────────────────────────────────────────────────────

    def create_scene(self, scene_name: str) -> dict:
        """Create a new scene in OBS. No-op if it already exists."""
        # Check if scene already exists — skip if so (idempotent)
        try:
            status = self.get_status()
            existing_scenes = status.get("scenes", [])
            if scene_name in existing_scenes:
                return {"connected": True, "status": "ok", "data": {"scene_name": scene_name, "already_exists": True}}
        except Exception:
            pass

        return self._safe_call(
            lambda c, sn=scene_name: c.create_scene(name=sn)
        )

    def set_current_scene(self, scene_name: str) -> dict:
        """Switch to a different OBS scene."""
        return self._safe_call(
            lambda c, sn=scene_name: c.set_current_program_scene(name=sn)
        )

    def _get_current_scene_name(self) -> str:
        """Get the name of the current OBS scene. Falls back to 'Scene'."""
        try:
            status = self.get_status()
            return status.get("current_scene", "Scene") or "Scene"
        except Exception:
            return "Scene"

    # ── Source Control ────────────────────────────────────────────────────

    def get_source_list(self) -> dict:
        """Get all input sources."""
        return self._safe_call(lambda c: c.get_input_list())

    def set_source_visibility(self, scene_name: str, source_name: str, visible: bool) -> dict:
        """Toggle visibility of a source in a scene."""
        item_id = self._get_scene_item_id(scene_name, source_name)
        if item_id < 0:
            return {"error": f"Source '{source_name}' not found in scene '{scene_name}'", "connected": True}

        return self._safe_call(
            lambda c, sn=scene_name, iid=item_id, v=visible: c.set_scene_item_enabled(
                scene_name=sn, item_id=iid, enabled=v
            )
        )

    def _get_scene_item_id(self, scene_name: str, source_name: str) -> int:
        """Resolve a source name to its scene item ID (requires a live connection)."""
        client = self._connect()
        if client is None:
            return -1
        try:
            resp = client.get_scene_item_list(name=scene_name)
            items = resp.scene_items if hasattr(resp, "scene_items") else resp.get("sceneItems", [])
            for item in items:
                name = item.source_name if hasattr(item, "source_name") else item.get("sourceName", "")
                if name == source_name:
                    return item.scene_item_id if hasattr(item, "scene_item_id") else item.get("sceneItemId", -1)
            return -1
        except Exception:
            return -1
        finally:
            try:
                client.disconnect()
            except Exception:
                pass

    def set_source_mute(self, source_name: str, muted: bool) -> dict:
        """Mute/unmute an audio source."""
        return self._safe_call(
            lambda c, sn=source_name, m=muted: c.set_input_mute(name=sn, muted=m)
        )

    def toggle_source_mute(self, source_name: str) -> dict:
        """Toggle mute on an audio source."""
        return self._safe_call(
            lambda c, sn=source_name: c.toggle_input_mute(name=sn)
        )

    def set_source_volume(self, source_name: str, volume_db: float) -> dict:
        """Set volume of a source in dB."""
        return self._safe_call(
            lambda c, sn=source_name, v=volume_db: c.set_input_volume(
                name=sn, vol_db=v
            )
        )

    # ── Transition Control ────────────────────────────────────────────────

    def set_current_transition(self, transition_name: str) -> dict:
        """Set the active scene transition."""
        return self._safe_call(
            lambda c, tn=transition_name: c.set_current_scene_transition(name=tn)
        )

    def trigger_transition(self) -> dict:
        """Trigger the current transition to the preview scene."""
        return self._safe_call(
            lambda c: c.trigger_studio_mode_transition()
        )

    # ── Studio Mode ───────────────────────────────────────────────────────

    def enable_studio_mode(self) -> dict:
        """Enable OBS studio mode."""
        return self._safe_call(
            lambda c: c.set_studio_mode_enabled(enabled=True)
        )

    def disable_studio_mode(self) -> dict:
        """Disable OBS studio mode."""
        return self._safe_call(
            lambda c: c.set_studio_mode_enabled(enabled=False)
        )

    # ── Replay Buffer ────────────────────────────────────────────────────

    def start_replay_buffer(self) -> dict:
        """Start the replay buffer."""
        return self._safe_call(lambda c: c.start_replay_buffer())

    def stop_replay_buffer(self) -> dict:
        """Stop the replay buffer."""
        return self._safe_call(lambda c: c.stop_replay_buffer())

    def save_replay_buffer(self) -> dict:
        """Save the current replay buffer contents."""
        return self._safe_call(lambda c: c.save_replay_buffer())

    # ── Virtual Camera ────────────────────────────────────────────────────

    def start_virtual_camera(self) -> dict:
        """Start the virtual camera."""
        return self._safe_call(lambda c: c.start_virtual_cam())

    def stop_virtual_camera(self) -> dict:
        """Stop the virtual camera."""
        return self._safe_call(lambda c: c.stop_virtual_cam())

    # ── Screenshot ────────────────────────────────────────────────────────

    def take_screenshot(self, source_name: str = "") -> dict:
        """Take a screenshot of a source (or the main output).
        
        Note: obsws-python 1.8.0 save_source_screenshot signature is:
            save_source_screenshot(name, img_format, file_path, width, height, quality)
        We save to /tmp/ and return the path. If file_path is empty, OBS returns
        a base64-encoded image in the response instead.
        """
        target = source_name or "️ Now Playing"
        return self._safe_call(
            lambda c, sn=target: c.save_source_screenshot(
                name=sn,
                img_format="png",
                file_path="",
                width=1280,
                height=720,
                quality=-1,
            )
        )

    # ── Stream Settings ────────────────────────────────────────────────────

    def set_stream_settings(self, service: str = "", server: str = "", key: str = "") -> dict:
        """Configure OBS stream settings (RTMP server + stream key).

        This sets where OBS streams to when start_streaming() is called.
        For YouTube Live, use:
            service: "rtmp_custom" or "youtube"
            server: "rtmp://a.rtmp.youtube.com/live2"
            key: your YouTube stream key

        Uses obsws-python's set_stream_service_settings() which properly
        applies the stream service type and settings via the OBS WebSocket
        5.x protocol.

        Returns the result dict from OBS.
        """
        if not self.enabled:
            return {"error": "OBS Bridge is disabled", "connected": False}

        # Default to rtmp_custom if not specified
        stream_type = service or "rtmp_custom"
        stream_settings = {}
        if server:
            stream_settings["server"] = server
        if key:
            stream_settings["key"] = key

        # Use the proper obsws-python method instead of raw send()
        # This correctly applies the stream service settings via the
        # SetStreamServiceSettings request in OBS WebSocket 5.x
        return self._safe_call(
            lambda c: c.set_stream_service_settings(
                stream_type, stream_settings
            )
        )

    def get_stream_settings(self) -> dict:
        """Get current OBS stream settings (server, key, service)."""
        return self._safe_call(lambda c: c.get_stream_service_settings())

    # ── Source Creation ──────────────────────────────────────────────────

    def create_browser_source(self, source_name: str, url: str, width: int = 1280, height: int = 720, scene_name: str = "") -> dict:
        """Create a browser source in OBS pointing to a URL.

        Used to add the Mission Control overlay as a browser source
        that OBS can stream to YouTube Live.

        NOTE: On Debian 12, OBS 29.x does NOT include the obs-browser
        plugin, so browser_source will fail with error 605. Use
        create_native_overlay() instead for a color+text source approach
        that works on all platforms.

        Args:
            source_name: Name for the source in OBS
            url: URL the browser source will display
            width/height: Dimensions of the browser source
            scene_name: Scene to add the source to (empty = current scene)
        """
        if not self.enabled:
            return {"error": "OBS Bridge is disabled", "connected": False}

        # If no scene specified, use the current scene
        if not scene_name:
            scene_name = self._get_current_scene_name()

        def _create(c, _sn=source_name, _u=url, _w=width, _h=height, _scene=scene_name):
            # Check if source already exists — skip if so (idempotent)
            try:
                existing = c.get_input_settings(name=_sn)
                if existing:
                    log.debug(f"OBS Bridge: Source '{_sn}' already exists, skipping creation")
                    return existing
            except Exception:
                pass  # Source doesn't exist — proceed to create it
            return c.create_input(
                sceneName=_scene,
                inputKind="browser_source",
                inputName=_sn,
                inputSettings={
                    "url": _u,
                    "width": _w,
                    "height": _h,
                    "css": "body { background-color: transparent; margin: 0px; padding: 0px; overflow: hidden; }",
                    "reroute_audio": False,
                    "shutdown": True,
                },
                sceneItemEnabled=True,
            )

        return self._safe_call(_create)

    def create_native_overlay(self, scene_name: str = "") -> dict:
        """Create a native OBS overlay using color + text sources.

        This is the cross-platform alternative to browser_source — it works
        on Debian 12 where obs-browser is not available. It creates:
          1. A dark color_source_v3 as the background
          2. Multiple text sources (text_freetype2_v2 on Linux, text_gdiplus_v2
             on Windows) that read from /tmp/radio_*.txt files (written by
             youtube_stream.py update_hud())

        The text sources auto-update by reading from the .txt files on each
        frame render, so they stay in sync with the bot's playback state.

        Args:
            scene_name: Scene to add sources to (empty = current scene)
        """
        if not self.enabled:
            return {"error": "OBS Bridge is disabled", "connected": False}

        if not scene_name:
            scene_name = self._get_current_scene_name()

        results = {}

        # ── 1. Background color source ──────────────────────────────────
        def _create_bg(c, _scene=scene_name):
            try:
                existing = c.get_input_settings(name="Overlay Background")
                if existing:
                    return existing
            except Exception:
                pass
            return c.create_input(
                sceneName=_scene,
                inputKind="color_source_v3",
                inputName="Overlay Background",
                inputSettings={
                    "color": 4278190080,  # 0xFF000000 = opaque black (ARGB)
                    "width": 1280,
                    "height": 720,
                },
                sceneItemEnabled=True,
            )

        results["background"] = self._safe_call(_create_bg)

        # ── 2. Station name text source ────────────────────────────
        def _create_station(c, _scene=scene_name):
            try:
                existing = c.get_input_settings(name="Station Name")
                if existing:
                    return existing
            except Exception:
                pass
            return c.create_input(
                sceneName=_scene,
                inputKind=_TEXT_INPUT_KIND,
                inputName="Station Name",
                inputSettings=_text_settings(
                    "MBOT RADIO", "DejaVu Sans", "Bold", 42,
                    color=4294967295,  # White
                    align=0, valign=0,
                ),
                sceneItemEnabled=True,
            )

        results["station_text"] = self._safe_call(_create_station)

        # ── 3. Now Playing title text source (reads from file) ─────
        def _create_title(c, _scene=scene_name):
            try:
                existing = c.get_input_settings(name="Now Playing")
                if existing:
                    return existing
            except Exception:
                pass
            return c.create_input(
                sceneName=_scene,
                inputKind=_TEXT_INPUT_KIND,
                inputName="Now Playing",
                inputSettings=_text_settings(
                    "Waiting for playback...", "DejaVu Sans", "Bold", 56,
                    color=4294967264,  # Gold
                    align=0, valign=0,
                    read_from_file=True, file_path="/tmp/radio_title.txt",
                ),
                sceneItemEnabled=True,
            )

        results["title_text"] = self._safe_call(_create_title)

        # ── 4. DJ/Speaking text source (reads from file) ───────────
        def _create_dj(c, _scene=scene_name):
            try:
                existing = c.get_input_settings(name="DJ Speaking")
                if existing:
                    return existing
            except Exception:
                pass
            return c.create_input(
                sceneName=_scene,
                inputKind=_TEXT_INPUT_KIND,
                inputName="DJ Speaking",
                inputSettings=_text_settings(
                    " ", "DejaVu Sans", "Regular", 36,
                    color=4278255872,  # Cyan-green
                    align=0, valign=0,
                    read_from_file=True, file_path="/tmp/radio_dj.txt",
                ),
                sceneItemEnabled=True,
            )

        results["dj_text"] = self._safe_call(_create_dj)

        # ── 5. Waiting/ticker text source (reads from file) ────────
        def _create_waiting(c, _scene=scene_name):
            try:
                existing = c.get_input_settings(name="Ticker")
                if existing:
                    return existing
            except Exception:
                pass
            return c.create_input(
                sceneName=_scene,
                inputKind=_TEXT_INPUT_KIND,
                inputName="Ticker",
                inputSettings=_text_settings(
                    "Initializing...", "DejaVu Sans", "Regular", 24,
                    color=4294967295,  # White
                    align=1, valign=2,
                    read_from_file=True, file_path="/tmp/radio_waiting.txt",
                ),
                sceneItemEnabled=True,
            )

        results["ticker_text"] = self._safe_call(_create_waiting)

        # ── 6. Position scene items ────────────────────────────────
        # Set transform (position, size) for each text source so they
        # appear in the right places on the 1280x720 canvas.
        self._position_overlay_items(scene_name)

        errors = [k for k, v in results.items() if v.get("error")]
        if errors:
            log.warning(f"OBS Bridge: Native overlay had errors: {errors}")
        else:
            log.info("OBS Bridge: Native overlay created ✅")

        return {"connected": True, "status": "ok", "sources_created": list(results.keys()), "errors": errors or None}

    def _position_overlay_items(self, scene_name: str):
        """Position overlay text sources on the 1280x720 canvas.

        Uses set_scene_item_transform to position each source.
        OBS scene item positions are in pixels from top-left.
        """
        # Positions for the overlay elements
        positions = {
            "Station Name": {"x": 450, "y": 150},
            "Now Playing": {"x": 450, "y": 210},
            "DJ Speaking": {"x": 450, "y": 280},
            "Ticker": {"x": 0, "y": 670},
        }

        for source_name, pos in positions.items():
            item_id = self._get_scene_item_id(scene_name, source_name)
            if item_id < 0:
                continue  # Source not found — skip
            try:
                self._safe_call(
                    lambda c, sn=scene_name, iid=item_id, px=pos["x"], py=pos["y"]:
                        c.set_scene_item_transform(
                            scene_name=sn, item_id=iid, transform={"positionX": px, "positionY": py}
                        )
                )
            except Exception as e:
                log.debug(f"OBS Bridge: Failed to position '{source_name}': {e}")

    def create_audio_source(self, source_name: str, udp_port: int = 12345, scene_name: str = "") -> dict:
        """Create a FFmpeg audio source that reads from the PCMBroadcaster UDP pipe.

        This allows OBS to capture the bot's audio output (music, TTS, SFX)
        for streaming alongside the visual overlay.

        Args:
            source_name: Name for the source in OBS
            udp_port: UDP port to listen on (default 12345)
            scene_name: Scene to add the source to (empty = current scene)

        IMPORTANT: The UDP stream contains raw PCM s16le 48kHz stereo data.
        OBS's FFmpeg source cannot auto-detect the format of a raw PCM stream,
        so we must explicitly specify:
          - input_format: "s16le" (signed 16-bit little-endian)
          - ffmpeg_options: "ar=48000 ac=2" (48kHz, 2 channels)
        NOTE: ffmpeg_options uses av_dict_parse_string() format (key=value),
        NOT CLI flag format (-ar 48000 -ac 2). OBS logs "Failed to parse
        FFmpeg options: Invalid argument" if you use the flag format.
        Without these, OBS logs "MP: Failed to open media" and the source
        stays silent.

        REBUILD STRATEGY: We always delete+recreate the source rather than
        trying to detect whether existing settings are correct. This is
        because:
          - set_input_settings() does NOT force OBS to reconnect the media
            (the old FFmpeg context keeps running with broken options)
          - get_input_settings() dataclass doesn't reliably expose
            ffmpeg_options as an attribute, making detection fragile
          - OBS persists source settings between runs — if the source was
            created with wrong settings on a previous run, it reloads broken
          - A fresh create_input() guarantees the correct FFmpeg context

        After remove_input(), we sleep briefly (0.5s) to avoid race
        condition error 601 ("A source already exists") — OBS needs a
        moment to fully release the source name.
        """
        if not self.enabled:
            return {"error": "OBS Bridge is disabled", "connected": False}

        # If no scene specified, use the current scene
        if not scene_name:
            scene_name = self._get_current_scene_name()

        def _create(c, _sn=source_name, _p=udp_port, _scene=scene_name):
            # Always remove the existing source first (if any).
            # OBS persists sources between runs — a source created with
            # wrong ffmpeg_options on a previous launch will reload broken.
            # Deleting forces OBS to release the stale FFmpeg context.
            source_existed = False
            try:
                c.get_input_settings(name=_sn)
                source_existed = True
            except Exception:
                pass  # Source doesn't exist — good, proceed to create

            if source_existed:
                log.info(f"OBS Bridge: Removing existing audio source '{_sn}' to rebuild with correct settings")
                try:
                    c.remove_input(name=_sn)
                except Exception as e:
                    log.warning(f"OBS Bridge: Failed to remove '{_sn}': {e}")
                # Give OBS time to fully release the source name.
                # Without this delay, create_input() fails with error 601
                # "A source already exists" (race condition).
                time.sleep(0.5)

            return c.create_input(
                sceneName=_scene,
                inputKind="ffmpeg_source",
                inputName=_sn,
                inputSettings={
                    "input": f"udp://127.0.0.1:{_p}?pkt_size=3840&buffer_size=65536&reuse=1",
                    "is_local_file": False,
                    # CRITICAL: Raw PCM format specification — OBS cannot
                    # auto-detect the format of a raw UDP stream. Without these,
                    # OBS logs "MP: Failed to open media" and the source is silent.
                    "input_format": "s16le",
                    "ffmpeg_options": "ar=48000 ac=2",
                },
                sceneItemEnabled=True,
            )

        return self._safe_call(_create)

    # ── Scene Setup ─────────────────────────────────────────────────────────

    def ensure_scenes_exist(self) -> dict:
        """Create all required Radio DJ scenes and sources via WebSocket API.

        Instead of relying on OBS to correctly parse the scene collection JSON
        (which silently drops scenes with unknown source types), this method
        programmatically creates every scene and source that the bot needs.

        Safe to call repeatedly — idempotent (skips existing scenes/sources).

        Returns a dict with 'created' (list of created items) and 'errors'.
        """
        if not self.enabled:
            return {"error": "OBS Bridge is disabled"}

        created = []
        errors = []

        # ── Scene definitions ──
        scenes = {
            "️ Now Playing": {
                "sources": [
                    ("color_source_v3", "Background Color", {
                        "color": 4278190080, "width": 1280, "height": 720,
                    }),
                    (_TEXT_INPUT_KIND, "Now Playing Title", _text_settings(
                        "Now Playing", "DejaVu Sans", "Bold", 48,
                        color=4294967295, align=1, valign=1,
                    )),
                    (_TEXT_INPUT_KIND, "Station Name", _text_settings(
                        "Radio DJ Bot", "DejaVu Sans", "Regular", 32,
                        color=4286544650, align=1, valign=0,
                    )),
                ],
            },
            "🎙️ DJ Speaking": {
                "sources": [
                    ("color_source_v3", "Background Color", {
                        "color": 4278190303, "width": 1280, "height": 720,
                    }),
                    (_TEXT_INPUT_KIND, "DJ Speaking Title", _text_settings(
                        "🎙️ The DJ is Speaking...", "DejaVu Sans", "Bold", 56,
                        color=4294967295, align=1, valign=1,
                    )),
                ],
            },
            "⏳ Waiting": {
                "sources": [
                    ("color_source_v3", "Background Color", {
                        "color": 4286544384, "width": 1280, "height": 720,
                    }),
                    (_TEXT_INPUT_KIND, "Waiting Title", _text_settings(
                        "⏳ Waiting for next track...", "DejaVu Sans", "Regular", 48,
                        color=4294967295, align=1, valign=1,
                    )),
                ],
            },
            "📺 Overlay Only": {
                "sources": [
                    ("color_source_v3", "Overlay Background", {
                        "color": 4278190080, "width": 1280, "height": 720,
                    }),
                ],
            },
        }

        for scene_name, scene_def in scenes.items():
            # Create the scene (idempotent)
            result = self.create_scene(scene_name)
            if result.get("error") and not result.get("data", {}).get("already_exists"):
                errors.append(f"scene:{scene_name}: {result['error']}")
                continue  # Can't add sources to a scene that doesn't exist
            if not result.get("data", {}).get("already_exists"):
                created.append(f"scene:{scene_name}")

            # Create each source in the scene
            for input_kind, source_name, settings in scene_def["sources"]:
                # Check if source already exists globally
                try:
                    existing = self._safe_call(
                        lambda c, sn=source_name: c.get_input_settings(name=sn)
                    )
                    if existing and not existing.get("error"):
                        continue  # Source already exists
                except Exception:
                    pass

                def _make_src(c, _scene=scene_name, _kind=input_kind,
                              _name=source_name, _settings=settings):
                    return c.create_input(
                        sceneName=_scene,
                        inputKind=_kind,
                        inputName=_name,
                        inputSettings=_settings,
                        sceneItemEnabled=True,
                    )

                result = self._safe_call(_make_src)
                if result.get("error"):
                    errors.append(f"source:{scene_name}/{source_name}: {result['error']}")
                else:
                    created.append(f"source:{scene_name}/{source_name}")

        if errors:
            log.warning(f"OBS Bridge: Scene setup had errors: {errors}")
        else:
            log.info(f"OBS Bridge: Scene setup complete ✅ ({len(created)} items)")

        # Initialize /tmp/radio_*.txt files so text_ft2_source_v2 sources
        # with from_file=True have content to display from the first frame.
        # Without these files, the text sources render as blank.
        self._init_hud_files()

        return {"created": created, "errors": errors}

    @staticmethod
    def _init_hud_files():
        """Create /tmp/radio_*.txt files with non-empty initial content.

        OBS text sources with from_file=True read from these files on every
        frame. If the file doesn't exist when the source is created, the
        source renders blank text. Writing initial content ensures the
        overlay is visible from the first frame.

        CRITICAL: Empty string ("") renders as zero-height invisible text
        in text_ft2_source_v2 (FreeType2). Every file MUST have at least
        a space character " " or some visible placeholder text. Otherwise
        the DJ Speaking and Ticker sources will be invisible until the
        bot writes non-empty content — which might be never for the DJ
        source until the DJ actually speaks.
        """
        import os
        hud_files = {
            "/tmp/radio_title.txt": "Waiting for playback...",
            # Use a space, not empty string! FreeType2 renders "" as
            # zero-height invisible text. " " ensures the source stays
            # visible (allocated space) even when no DJ is speaking.
            "/tmp/radio_dj.txt": " ",
            "/tmp/radio_waiting.txt": "Initializing...",
        }
        for path, default_text in hud_files.items():
            if not os.path.exists(path):
                try:
                    with open(path, "w") as f:
                        f.write(default_text)
                except Exception:
                    pass

    # ── Reconnect ─────────────────────────────────────────────────────────

    def reconnect(self) -> dict:
        """Force a reconnection by clearing the backoff and status cache."""
        self._last_connect_fail = 0
        self._last_status = None
        self._connection_logged = False
        return self.get_status()

    # ── Auto Scene Switching ───────────────────────────────────────────────
    # Called by the bot when playback state changes. Non-blocking — fires
    # in a thread and never raises exceptions.

    def switch_scene(self, scene_name: str) -> bool:
        """Try to switch to a scene (non-blocking, fire-and-forget).

        Falls back to '📺 Overlay Only' if the target scene doesn't exist.
        Returns True if the request was sent (not guaranteed to succeed).
        Used by the bot for auto scene switching — failures are logged
        but never crash the bot.
        """
        if not self.enabled:
            return False
        try:
            result = self.set_current_scene(scene_name)
            if result.get("status") == "ok" or (result.get("connected") and not result.get("error")):
                return True
            # Scene not found? Fall back to overlay scene
            if result.get("error") and "No source" in str(result.get("error", "")):
                fallback = "📺 Overlay Only"
                log.debug(f"OBS Auto Scene: '{scene_name}' not found, falling back to '{fallback}'")
                result = self.set_current_scene(fallback)
                return result.get("status") == "ok" or (result.get("connected") and not result.get("error"))
            return False
        except Exception as e:
            log.debug(f"OBS Auto Scene: Exception switching to '{scene_name}': {e}")
            return False


# ══════════════════════════════════════════════════════════════════════════
# Module-level singleton — created by bot.py at startup
# ══════════════════════════════════════════════════════════════════════════

obs_bridge: OBSBridge | None = None


def init_bridge(host: str, port: int, password: str, enabled: bool = True):
    """Initialize the global OBS Bridge instance. Called once at startup."""
    global obs_bridge
    obs_bridge = OBSBridge(host=host, port=port, password=password, enabled=enabled)
    return obs_bridge


def get_bridge() -> OBSBridge | None:
    """Get the global OBS Bridge instance."""
    return obs_bridge