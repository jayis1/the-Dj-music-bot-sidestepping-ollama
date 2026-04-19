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
from contextlib import contextmanager

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

    # Minimum time between WebSocket connections (seconds).
    # Prevents rapid connect/disconnect cycles from overwhelming
    # OBS's internal event loop, which causes audio glitches.
    # Burst connections during startup are allowed, but steady-state
    # polling should respect this limit.
    MIN_CONNECT_INTERVAL = 0.2  # max ~5 WebSocket connections/sec

    def __init__(self, host: str = "localhost", port: int = 4455, password: str = "", enabled: bool = True):
        self.host = host
        self.port = port
        self.password = password
        self.enabled = enabled and bool(password)  # Don't try if no password
        self._last_status = None
        self._last_status_time = 0
        self._status_cache_ttl = 5  # seconds
        self._last_connect_fail = 0  # timestamp of last failed connection
        self._last_connect_time = 0  # timestamp of last successful connection
        self._connection_logged = False  # Only log "configured" once

        # SFX GIF cycling — always shows dans trollface, briefly flashes
        # to another troll when a sound plays, then reverts back
        self._sfx_gif_index = 0
        self._sfx_gif_paths = self._resolve_sfx_gif_paths()
        self._sfx_default_path = self._resolve_default_sfx_gif()
        self._sfx_revert_timer = None

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
          - We're in a rate-limit period (too many connections per second)

        Rate limiting: MIN_CONNECT_INTERVAL prevents rapid connect/disconnect
        cycles from overwhelming OBS's event loop, which causes audio glitches
        and dropouts. During burst operations (startup setup), calls that
        exceed the rate limit simply block briefly rather than fail.
        """
        obsws = _get_obsws()
        if obsws is None:
            return None

        # Check connection backoff — don't spam failed connects
        if not self._should_try_connect():
            return None

        # Rate limit: throttle WebSocket connections to prevent
        # overwhelming OBS's internal event loop.
        elapsed = time.time() - self._last_connect_time
        if elapsed < self.MIN_CONNECT_INTERVAL:
            # Brief sleep to respect the rate limit during bursts
            # (e.g., startup creates ~30 sources in quick succession)
            time.sleep(self.MIN_CONNECT_INTERVAL - elapsed)

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
            # Success — reset backoff and record connection time
            self._last_connect_fail = 0
            self._last_connect_time = time.time()
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

    @contextmanager
    def _batch(self):
        """Context manager that provides a single persistent WebSocket connection.

        Use this when making multiple sequential API calls (e.g., during
        overlay creation where 15+ source create + position calls happen).
        Instead of connect/disconnect per call, all calls share one
        connection — dramatically reducing the connection storm.

        Usage:
            with self._batch() as client:
                if client:
                    client.create_input(...)
                    client.set_scene_item_transform(...)
                    client.set_input_settings(...)

        Yields:
            obsws.ReqClient or None (if connection failed)
        """
        client = self._connect()
        try:
            yield client
        finally:
            if client:
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

    # ── Media Source Control ────────────────────────────────────────────

    def stop_media_source(self, source_name: str) -> dict:
        """Stop a media/ffmpeg source from playing.

        Used to deactivate the UDP audio source when streaming stops,
        preventing circular buffer overruns (the ffmpeg_source keeps
        reading from UDP but with no output consumer, frames pile up).
        """
        return self._safe_call(
            lambda c, sn=source_name: c.trigger_media_input_action(
                name=sn, action="OBS_WEBSOCKET_MEDIA_INPUT_ACTION_STOP"
            )
        )

    def restart_media_source(self, source_name: str) -> dict:
        """Restart a media/ffmpeg source.

        Used to reactivate the UDP audio source when streaming resumes.
        """
        return self._safe_call(
            lambda c, sn=source_name: c.trigger_media_input_action(
                name=sn, action="OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART"
            )
        )

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
        """Create a new scene in OBS. No-op if it already exists.
        
        Includes a retry mechanism because Flatpak OBS 32 can return
        success when creating a scene but its internal state may still
        be null (especially after crash dialog dismissal). Verifying
        the scene actually exists after creation prevents null pointer
        crashes when adding sources to the scene.
        """
        # Check if scene already exists — skip if so (idempotent)
        try:
            status = self.get_status()
            existing_scenes = status.get("scenes", [])
            if scene_name in existing_scenes:
                return {"connected": True, "status": "ok", "data": {"scene_name": scene_name, "already_exists": True}}
        except Exception:
            pass

        # Create the scene
        result = self._safe_call(
            lambda c, sn=scene_name: c.create_scene(name=sn)
        )
        
        # Wait a beat and verify the scene actually exists in OBS's
        # internal state. After crash dialog dismissal, OBS may report
        # successful scene creation but its internal pointer is still
        # null. Verifying prevents "basic_string: construction from
        # null is not valid" crashes when adding sources.
        if result.get("connected") and not result.get("error"):
            time.sleep(1)  # Let OBS process the scene creation
            for _verify in range(3):
                try:
                    status = self.get_status()
                    existing_scenes = status.get("scenes", [])
                    if scene_name in existing_scenes:
                        return result
                    # Scene not visible yet — retry creation
                    log.debug(f"OBS Bridge: Scene '{scene_name}' not found after creation, retrying...")
                    result = self._safe_call(
                        lambda c, sn=scene_name: c.create_scene(name=sn)
                    )
                    time.sleep(1)
                except Exception:
                    time.sleep(1)
            log.warning(f"OBS Bridge: Scene '{scene_name}' created but not visible in scene list after 3 retries")
        
        return result

    def set_current_scene(self, scene_name: str) -> dict:
        """Switch to a different OBS scene."""
        return self._safe_call(
            lambda c, sn=scene_name: c.set_current_program_scene(name=sn)
        )

    def _get_current_scene_name(self) -> str:
        """Get the name of the current OBS scene. Falls back to '📺 Overlay Only'."""
        try:
            status = self.get_status()
            scene = status.get("current_scene", "")
            # Return our overlay scene if current scene is empty, null, or
            # OBS's auto-created default "Scene" (which has no sources).
            if scene and scene not in ("", "Scene", "Untitled"):
                return scene
        except Exception:
            pass
        return "📺 Overlay Only"

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

    def _get_scene_item_id_from_client(self, client, scene_name: str, source_name: str) -> int:
        """Resolve a source name to its scene item ID using an EXISTING connection.

        Used inside _batch() contexts where we already have a connected client.
        Avoids opening an additional WebSocket connection just to look up an item ID.
        Returns -1 if not found.
        """
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

    def create_browser_overlay(self, scene_name: str = "", url: str = "") -> dict:
        """Create an OBS browser source overlay pointing to the Mission Control overlay page.

        This is the preferred overlay method when obs-browser is available
        (Flatpak OBS, Ubuntu PPA, Windows, macOS). It renders the full
        Mission Control overlay.html — station name, now-playing, DJ text,
        album art, SFX animations, AND the real-time audio waveform visualizer.

        The overlay page polls /api/overlay every ~1s and uses
        requestAnimationFrame for smooth visualizer bars driven by real
        PCMBroadcaster RMS data. This gives YouTube's encoder consistent
        pixel variation (good for stream health) without needing a separate
        visualizer rendering pipeline.

        Falls back gracefully: if browser_source creation fails (e.g. on
        Debian 12 apt OBS which lacks obs-browser), the caller should try
        create_native_overlay() instead.

        IMPORTANT: When using browser overlay, do NOT also create native text
        sources — the browser source renders everything. The only other
        sources needed on the scene are:
          - UDP audio source (ffmpeg_source)
          - GIF overlays (ffmpeg_source, optional)

        Args:
            scene_name: Scene to add the source to (empty = current scene).
            url: URL for the overlay page. Defaults to
                 http://localhost:8080/overlay (Mission Control).

        Returns:
            OBS WebSocket response dict, or {"error": ...} on failure.
        """
        if not self.enabled:
            return {"error": "OBS Bridge is disabled", "connected": False}

        overlay_url = url or "http://localhost:8080/overlay"

        if not scene_name:
            scene_name = self._get_current_scene_name()

        # Pre-flight: check if the overlay URL is reachable.
        # The browser source in OBS will silently show a blank page if
        # the URL is unreachable (e.g., Flatpak sandbox with no network
        # access, or Flask not started yet). We check now so we can log
        # a useful warning instead of a mysterious blank stream.
        try:
            import urllib.request
            req = urllib.request.Request(overlay_url, method="HEAD")
            resp = urllib.request.urlopen(req, timeout=3)
            log.info(f"OBS Bridge: Overlay URL reachable: {overlay_url} (HTTP {resp.status})")
        except Exception as e:
            log.warning(
                f"OBS Bridge: Overlay URL NOT reachable: {overlay_url} ({e}). "
                "Browser source will show a blank page. Ensure Flask is running "
                "and OBS has network access (--share=network for Flatpak)."
            )

        # CSS ensures transparent background so OBS composites correctly
        overlay_css = (
            "body { background-color: transparent !important; "
            "margin: 0px; padding: 0px; overflow: hidden; }"
        )

        def _create(c, _sn="Mission Control Overlay", _url=overlay_url,
                     _scene=scene_name, _css=overlay_css):
            # Check if source already exists — idempotent
            try:
                existing = c.get_input_settings(name=_sn)
                if existing:
                    log.debug(f"OBS Bridge: Browser overlay '{_sn}' already exists, skipping creation")
                    return existing
            except Exception:
                pass  # Source doesn't exist — proceed to create it

            try:
                result = c.create_input(
                    sceneName=_scene,
                    inputKind="browser_source",
                    inputName=_sn,
                    inputSettings={
                        "url": _url,
                        "width": 1280,
                        "height": 720,
                        "css": _css,
                        "reroute_audio": False,
                        # shutdown=False keeps the browser process alive even
                        # when the source is hidden — prevents reload delays
                        "shutdown": False,
                    },
                    sceneItemEnabled=True,
                )
                log.info(f"OBS Bridge: Browser overlay created → {_url}")
                return result
            except Exception as e:
                err_str = str(e)
                if "605" in err_str:
                    log.warning(
                        "OBS Bridge: browser_source not available (error 605). "
                        "Install obs-browser (Flatpak OBS or Ubuntu PPA) or use "
                        "OBS_OVERLAY_MODE=native."
                    )
                raise  # Re-raise so caller can catch and fall back

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

        IMPORTANT: All dynamic content (station name, title, DJ text, ticker)
        lives on this ONE scene. We do NOT switch to separate scenes for
        "Now Playing" / "DJ Speaking" / "Waiting" — instead, a "State"
        text source shows the current state (🎵 / 🎙️ / ⏳). This ensures
        the viewer always sees the full overlay with station name, title,
        and ticker, regardless of what the bot is doing.

        Args:
            scene_name: Scene to add sources to (empty = current scene)
        """
        if not self.enabled:
            return {"error": "OBS Bridge is disabled", "connected": False}

        if not scene_name:
            scene_name = self._get_current_scene_name()

        results = {}

        # ── Resolve overlay parameters ──────────────────────────────────
        logo_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "assets", "logo.png"
        )
        use_logo = os.path.isfile(logo_path)

        try:
            import config as _cfg
            gif_path = getattr(_cfg, "YOUTUBE_STREAM_GIF", "") or ""
        except ImportError:
            gif_path = ""

        # Write placeholder images if missing
        thumb_path = "/tmp/radio_thumbnail.jpg"
        if not os.path.exists(thumb_path):
            self._create_placeholder_thumbnail()

        # Resolve GIF path — prefer sounds.gif (wider, 500x281) over sound.gif (450x450)
        if not gif_path or not os.path.isfile(gif_path):
            assets_dir = os.path.dirname(os.path.dirname(__file__))
            assets_gif = os.path.join(assets_dir, "assets", "sounds.gif")
            if not os.path.isfile(assets_gif):
                assets_gif = os.path.join(assets_dir, "assets", "sound.gif")
            gif_path = assets_gif if os.path.isfile(assets_gif) else ""

        # ── Batch: Create ALL sources + position them in ONE connection ──
        # This replaces ~15 separate connect/disconnect cycles with a single
        # WebSocket session, dramatically reducing the connection storm that
        # overwhelms OBS's internal event loop during startup.
        with self._batch() as client:
            if client is None:
                return {"error": "OBS is not running", "connected": False}

            # ── 1. Background (logo.png or solid black) ────────────────
            try:
                try:
                    existing = client.get_input_settings(name="Overlay Background")
                    if existing and use_logo:
                        try:
                            client.set_input_settings(
                                name="Overlay Background",
                                settings={"file": logo_path, "unload": False},
                                overlay=True,
                            )
                        except Exception:
                            pass
                    if not existing:
                        raise Exception("create")
                except Exception:
                    if use_logo:
                        client.create_input(
                            sceneName=scene_name,
                            inputKind="image_source",
                            inputName="Overlay Background",
                            inputSettings={"file": logo_path, "unload": False},
                            sceneItemEnabled=True,
                        )
                    else:
                        client.create_input(
                            sceneName=scene_name,
                            inputKind="color_source_v3",
                            inputName="Overlay Background",
                            inputSettings={"color": 4278190080, "width": 1280, "height": 720},
                            sceneItemEnabled=True,
                        )
                results["background"] = {"connected": True, "status": "ok"}
            except Exception as e:
                results["background"] = {"error": str(e), "connected": True}

            # ── 2-6. Text sources (State, Station Name, Now Playing, DJ, Ticker) ──
            text_sources = {
                "State": _text_settings(
                    " ", "DejaVu Sans", "Bold", 28,
                    color=4294967295, align=0, valign=0,
                    read_from_file=True, file_path="/tmp/radio_state.txt",
                ),
                "Station Name": _text_settings(
                    " ", "DejaVu Sans", "Bold", 42,
                    color=4294967295, align=0, valign=0,
                    read_from_file=True, file_path="/tmp/radio_station.txt",
                ),
                "Now Playing": _text_settings(
                    "Waiting for playback...", "DejaVu Sans", "Bold", 56,
                    color=4294967264, align=0, valign=0,
                    read_from_file=True, file_path="/tmp/radio_title.txt",
                ),
                "DJ Speaking": _text_settings(
                    " ", "DejaVu Sans", "Regular", 36,
                    color=4278255872, align=0, valign=0,
                    read_from_file=True, file_path="/tmp/radio_dj.txt",
                ),
                "Ticker": _text_settings(
                    "Initializing...", "DejaVu Sans", "Regular", 24,
                    color=4294967295, align=0, valign=0,
                    read_from_file=True, file_path="/tmp/radio_waiting.txt",
                ),
            }

            text_keys = ["state_text", "station_text", "title_text", "dj_text", "ticker_text"]
            for (source_name, settings), result_key in zip(text_sources.items(), text_keys):
                try:
                    try:
                        existing = client.get_input_settings(name=source_name)
                        if existing:
                            # Push file-reading settings to existing source
                            try:
                                client.set_input_settings(
                                    name=source_name, settings=settings, overlay=True,
                                )
                            except Exception:
                                pass
                            results[result_key] = {"connected": True, "status": "ok"}
                            continue
                    except Exception:
                        pass
                    client.create_input(
                        sceneName=scene_name,
                        inputKind=_TEXT_INPUT_KIND,
                        inputName=source_name,
                        inputSettings=settings,
                        sceneItemEnabled=True,
                    )
                    results[result_key] = {"connected": True, "status": "ok"}
                except Exception as e:
                    results[result_key] = {"error": str(e), "connected": True}

            # ── 7. Song Thumbnail (image_source) ────────────────────────
            try:
                try:
                    existing = client.get_input_settings(name="Song Thumbnail")
                    if existing:
                        try:
                            client.set_input_settings(
                                name="Song Thumbnail",
                                settings={"file": thumb_path, "unload": False},
                                overlay=True,
                            )
                        except Exception:
                            pass
                        results["thumbnail"] = {"connected": True, "status": "ok"}
                    else:
                        raise Exception("create")
                except Exception:
                    client.create_input(
                        sceneName=scene_name,
                        inputKind="image_source",
                        inputName="Song Thumbnail",
                        inputSettings={"file": thumb_path, "unload": False},
                        sceneItemEnabled=True,
                    )
                    results["thumbnail"] = {"connected": True, "status": "ok"}
            except Exception as e:
                results["thumbnail"] = {"error": str(e), "connected": True}

            # ── 8. GIF Overlay (ffmpeg_source) ──────────────────────────
            if gif_path:
                try:
                    try:
                        existing = client.get_input_settings(name="GIF Overlay")
                        if existing:
                            try:
                                client.set_input_settings(
                                    name="GIF Overlay",
                                    settings={
                                        "is_local_file": True,
                                        "local_file": gif_path,
                                        "looping": True,
                                        "restart_on_activate": True,
                                        "close_when_inactive": False,
                                    },
                                    overlay=True,
                                )
                            except Exception:
                                pass
                            results["gif"] = {"connected": True, "status": "ok"}
                        else:
                            raise Exception("create")
                    except Exception:
                        client.create_input(
                            sceneName=scene_name,
                            inputKind="ffmpeg_source",
                            inputName="GIF Overlay",
                            inputSettings={
                                "is_local_file": True,
                                "local_file": gif_path,
                                "looping": True,
                                "restart_on_activate": True,
                                "close_when_inactive": False,
                            },
                            sceneItemEnabled=True,
                        )
                        results["gif"] = {"connected": True, "status": "ok"}
                except Exception as e:
                    results["gif"] = {"error": str(e), "connected": True}
            else:
                results["gif"] = {"error": "No GIF file found", "connected": True}

            # ── 9.5 SFX GIF source (troll face — always visible, flashes on SFX) ──
            if self._sfx_default_path:
                initial_sfx_gif = self._sfx_default_path
                try:
                    try:
                        existing = client.get_input_settings(name="SFX GIF")
                        if existing:
                            try:
                                client.set_input_settings(
                                    name="SFX GIF",
                                    settings={
                                        "is_local_file": True,
                                        "local_file": initial_sfx_gif,
                                        "looping": True,
                                        "restart_on_activate": True,
                                        "close_when_inactive": False,
                                    },
                                    overlay=True,
                                )
                            except Exception:
                                pass
                            results["sfx_gif"] = {"connected": True, "status": "ok"}
                        else:
                            raise Exception("create")
                    except Exception:
                        client.create_input(
                            sceneName=scene_name,
                            inputKind="ffmpeg_source",
                            inputName="SFX GIF",
                            inputSettings={
                                "is_local_file": True,
                                "local_file": initial_sfx_gif,
                                "looping": True,
                                "restart_on_activate": True,
                                "close_when_inactive": False,
                            },
                            sceneItemEnabled=True,
                        )
                        results["sfx_gif"] = {"connected": True, "status": "ok"}
                except Exception as e:
                    results["sfx_gif"] = {"error": str(e), "connected": True}

            # ── 10. Position ALL scene items in the same connection ─────
            # Background positioning
            try:
                bg_item_id = self._get_scene_item_id_from_client(client, scene_name, "Overlay Background")
                if bg_item_id >= 0 and use_logo:
                    client.set_scene_item_transform(
                        scene_name=scene_name, item_id=bg_item_id,
                        transform={"positionX": 0, "positionY": 0, "scaleX": 2.0, "scaleY": 2.0},
                    )
            except Exception as e:
                log.debug(f"OBS Bridge: Failed to position background: {e}")

            # Text overlay positioning
            text_positions = {
                "State": {"x": 40, "y": 30},
                "Station Name": {"x": 40, "y": 80},
                "Now Playing": {"x": 40, "y": 140},
                "DJ Speaking": {"x": 40, "y": 210},
                "Ticker": {"x": 40, "y": 640},
            }
            for source_name, pos in text_positions.items():
                try:
                    item_id = self._get_scene_item_id_from_client(client, scene_name, source_name)
                    if item_id >= 0:
                        client.set_scene_item_transform(
                            scene_name=scene_name, item_id=item_id,
                            transform={"positionX": pos["x"], "positionY": pos["y"]},
                        )
                except Exception as e:
                    log.debug(f"OBS Bridge: Failed to position '{source_name}': {e}")

            # Thumbnail positioning
            try:
                thumb_item_id = self._get_scene_item_id_from_client(client, scene_name, "Song Thumbnail")
                if thumb_item_id >= 0:
                    client.set_scene_item_transform(
                        scene_name=scene_name, item_id=thumb_item_id,
                        transform={"positionX": 1060, "positionY": 85, "scaleX": 0.5, "scaleY": 0.5},
                    )
            except Exception as e:
                log.debug(f"OBS Bridge: Failed to position thumbnail: {e}")

            # GIF positioning — full width at bottom (sounds.gif: 500x281 or sound.gif: 450x450)
            if gif_path:
                try:
                    gif_item_id = self._get_scene_item_id_from_client(client, scene_name, "GIF Overlay")
                    if gif_item_id >= 0:
                        # Pick scale based on which GIF file
                        gif_scale = 2.56 if "sounds.gif" in gif_path else 2.84
                        client.set_scene_item_transform(
                            scene_name=scene_name, item_id=gif_item_id,
                            transform={
                                # Full width at bottom of canvas
                                "positionX": 0,
                                "positionY": 640,
                                "scaleX": gif_scale,
                                "scaleY": gif_scale,
                            },
                        )
                except Exception as e:
                    log.debug(f"OBS Bridge: Failed to position GIF: {e}")

            # SFX GIF positioning (lower-right, ~200px wide)
            if self._sfx_default_path:
                try:
                    sfx_item_id = self._get_scene_item_id_from_client(client, scene_name, "SFX GIF")
                    if sfx_item_id >= 0:
                        client.set_scene_item_transform(
                            scene_name=scene_name, item_id=sfx_item_id,
                            transform={
                                "positionX": 1040,
                                "positionY": 400,
                                "scaleX": 0.4,
                                "scaleY": 0.4,
                            },
                        )
                except Exception as e:
                    log.debug(f"OBS Bridge: Failed to position SFX GIF: {e}")

        errors = [k for k, v in results.items() if v.get("error")]
        if errors:
            log.warning(f"OBS Bridge: Native overlay had errors: {errors}")
        else:
            log.info("OBS Bridge: Native overlay created ✅ (batch mode — single WebSocket connection)")

        # ── Post-batch: force-update existing text sources ──
        # Sources created by previous runs may still have static text
        # instead of reading from /tmp/radio_*.txt. We push the correct
        # settings to ensure they switch to file-reading mode.
        # This uses ~5 individual _safe_call() connections — acceptable after
        # the main batch just saved ~15 connections.
        self._update_existing_text_sources()

        return {"connected": True, "status": "ok", "sources_created": list(results.keys()), "errors": errors or None}

    def _update_existing_text_sources(self):
        """Force-update existing text sources to read from /tmp/radio_*.txt.

        Sources created by previous bot runs may have static text (e.g.
        "MBOT RADIO") instead of reading from dynamic files. This method
        pushes the correct file-reading settings to all overlay text sources.
        Uses overlay=True to force OBS to apply changes immediately.

        Batches all updates into a single WebSocket connection.
        """
        if not self.enabled:
            return

        # Map source name → (file_path, font_size, color)
        source_configs = {
            "State": ("/tmp/radio_state.txt", 28, 4294967295),        # White
            "Station Name": ("/tmp/radio_station.txt", 42, 4294967295), # White
            "Now Playing": ("/tmp/radio_title.txt", 56, 4294967264),    # Gold
            "DJ Speaking": ("/tmp/radio_dj.txt", 36, 4278255872),       # Cyan-green
            "Ticker": ("/tmp/radio_waiting.txt", 24, 4294967295),       # White
        }

        with self._batch() as client:
            if client is None:
                return
            for source_name, (file_path, font_size, color) in source_configs.items():
                try:
                    settings = _text_settings(
                        " ", "DejaVu Sans", "Bold" if source_name != "DJ Speaking" else "Regular",
                        font_size, color=color, align=0, valign=0,
                        read_from_file=True, file_path=file_path,
                    )
                    client.set_input_settings(
                        name=source_name, settings=settings, overlay=True,
                    )
                except Exception:
                    pass  # Source may not exist yet — that's OK

    def _position_background(self, scene_name: str):
        """Position and scale the background (logo.png) to fill the 1280×720 canvas.

        The logo is a 640×640 image that needs to be scaled up to fill the
        1280×720 canvas while maintaining aspect ratio and covering the
        entire area. We scale by 2.0x (640→1280 width, 640→1280 height)
        which crops the bottom but fills the width perfectly.
        Position: (0, 0) top-left corner.
        """
        item_id = self._get_scene_item_id(scene_name, "Overlay Background")
        if item_id < 0:
            return
        try:
            # logo.png is 640×640 → scale to 2.0 to fill 1280 width.
            # The height becomes 1280 (cropped to 720 by OBS canvas).
            # If using color_source_v3 fallback, no scaling needed.
            import os as _os
            logo_path = _os.path.join(
                _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                "assets", "logo.png"
            )
            if _os.path.isfile(logo_path):
                scale_x = 1280.0 / 640.0  # = 2.0
                scale_y = 1280.0 / 640.0  # = 2.0, crops bottom
                self._safe_call(
                    lambda c, sn=scene_name, iid=item_id, sx=scale_x, sy=scale_y:
                        c.set_scene_item_transform(
                            scene_name=sn, item_id=iid,
                            transform={
                                "positionX": 0,
                                "positionY": 0,
                                "scaleX": sx,
                                "scaleY": sy,
                            }
                        )
                )
        except Exception as e:
            log.debug(f"OBS Bridge: Failed to position background: {e}")

    def _position_overlay_items(self, scene_name: str):
        """Position overlay text sources on the 1280x720 canvas.

        Uses set_scene_item_transform to position each source.
        OBS scene item positions are in pixels from top-left.

        Layout (1280x720) with logo.png background:
          ┌────────────────────────────────────────────────────────────────┐
          │  LOGO.PNG (fills entire canvas as background, 2x scale)         │
          │                                                                │
          │  (40,30)  [State] 🎵 Now Playing                      ┌────┐  │
          │  (40,80)  Station Name                                │Thumb│  │
          │  (40,140) Now Playing title                           │150px│  │
          │  (40,210) DJ Speaking text                            └────┘  │
          │                                                          ┌─────┐│
          │                                                          │SFX  ││
          │                                                          │ GIF ││
          │  (40,640) Ticker                                         └─────┘│
          │  ████████████████████████████████████████████████████████████████  │
          │  (0,640)  ──── sounds.gif spanning full width ─────────────── │
          └────────────────────────────────────────────────────────────────┘
        """
        # Positions for the TEXT overlay elements
        positions = {
            "State": {"x": 40, "y": 30},
            "Station Name": {"x": 40, "y": 80},
            "Now Playing": {"x": 40, "y": 140},
            "DJ Speaking": {"x": 40, "y": 210},
            "Ticker": {"x": 40, "y": 640},
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
          - ffmpeg_options: "sample_rate=48000 channels=2" (48kHz, 2 channels)
        NOTE: ffmpeg_options uses av_dict_parse_string() format (key=value),
        and goes to avformat_open_input() — NOT avcodec_open2().
        This means you must use AVFormat-level option names:
          sample_rate (NOT ar) — for raw audio sample rate
          channels (NOT ac) — for raw audio channel count
        Using the wrong names (ar/ac) silently does nothing — FFmpeg
        defaults to 44100Hz mono, making 48kHz stereo audio play at
        ~0.92x speed and double volume (stereo channels summed to mono).

        STRATEGY: Only create the source if it doesn't already exist.
        Do NOT delete+recreate — OBS loads the source from the scene
        collection at startup with correct settings, and deleting it
        disconnects it from OBS's audio mixer. A recreated source may
        not properly route audio through the mixer.

        If the source exists but with stale settings, use set_input_settings
        with overlay=True to force OBS to apply new settings while keeping
        the source connected to the mixer.
        """
        if not self.enabled:
            return {"error": "OBS Bridge is disabled", "connected": False}

        # If no scene specified, use the current scene
        if not scene_name:
            scene_name = self._get_current_scene_name()

        input_settings = {
            "input": f"udp://127.0.0.1:{udp_port}?pkt_size=3840&buffer_size=262144&fifo_size=262144&overrun_nonfatal=1&reuse=1",
            "is_local_file": False,
            "input_format": "s16le",
            "ffmpeg_options": "sample_rate=48000 channels=2",
            # close_when_inactive MUST be False — if True, OBS closes the
            # UDP reader when no output is active, but then fails to
            # reopen it reliably when streaming starts, causing dead air.
            "close_when_inactive": False,
            "restart_on_activate": True,
        }

        def _create(c, _sn=source_name, _p=udp_port, _scene=scene_name, _settings=input_settings):
            # Check if source already exists — if so, update its settings
            # and ensure it's in the target scene.
            try:
                existing = c.get_input_settings(name=_sn)
                if existing:
                    # Always push the correct settings to the existing source.
                    # This fixes stale settings from previous runs (e.g.
                    # "ar=48000 ac=2" → "sample_rate=48000 channels=2").
                    # overlay=True forces OBS to apply changes immediately.
                    try:
                        c.set_input_settings(
                            name=_sn,
                            settings=_settings,
                            overlay=True,
                        )
                        log.info(f"OBS Bridge: Updated audio source '{_sn}' settings (overlay=True)")
                    except Exception as e:
                        log.debug(f"OBS Bridge: Could not update audio source settings: {e}")

                    # CRITICAL: Ensure audio routing — set monitor type + track
                    # When a source is created via WebSocket, OBS defaults to
                    # monitor_type=0 (None) and no audio tracks assigned.
                    # Without these, the source exists but its audio goes NOWHERE —
                    # the stream gets video but no audio.
                    try:
                        c.set_input_audio_monitor_type(name=_sn, mon_type=2)
                        log.info(f"OBS Bridge: Set '{_sn}' audio monitor → Monitor and Output (type 2)")
                    except Exception as e:
                        log.debug(f"OBS Bridge: Could not set audio monitor type: {e}")
                    try:
                        c.set_input_audio_tracks(name=_sn, track=1)
                        log.info(f"OBS Bridge: Set '{_sn}' audio track → track 1 (streaming)")
                    except Exception as e:
                        log.debug(f"OBS Bridge: Could not set audio tracks: {e}")

                    # Ensure the source is added to the target scene
                    try:
                        items = c.get_scene_item_list(name=_scene)
                        source_names = [
                            item.source_name if hasattr(item, 'source_name') else item.get("sourceName", "")
                            for item in (items.scene_items if hasattr(items, 'scene_items') else items.get("sceneItems", []))
                        ]
                        if _sn not in source_names:
                            log.info(f"OBS Bridge: Adding existing audio source '{_sn}' to scene '{_scene}'")
                            return c.create_scene_item(scene_name=_scene, source_name=_sn)
                    except Exception:
                        pass  # May already be in the scene
                    return existing
            except Exception:
                pass  # Source doesn't exist — proceed to create it

            # Source doesn't exist — create it fresh
            log.info(f"OBS Bridge: Creating audio source '{_sn}' (UDP port {_p})")
            result = c.create_input(
                sceneName=_scene,
                inputKind="ffmpeg_source",
                inputName=_sn,
                inputSettings=_settings,
                sceneItemEnabled=True,
            )

            # CRITICAL: Set audio routing AFTER creating the source.
            # Without this, the source's audio is not routed to the streaming
            # output — the stream gets video but NO audio.
            #   monitor_type=2 → "Monitor and Output" (audio goes to stream + desktop)
            #   track=1 → assigned to audio track 1 (the streaming track)
            try:
                c.set_input_audio_monitor_type(name=_sn, mon_type=2)
                log.info(f"OBS Bridge: Set '{_sn}' audio monitor → Monitor and Output")
            except Exception as e:
                log.warning(f"OBS Bridge: Could not set audio monitor type for '{_sn}': {e}")
            try:
                c.set_input_audio_tracks(name=_sn, track=1)
                log.info(f"OBS Bridge: Set '{_sn}' audio track → track 1 (streaming)")
            except Exception as e:
                log.warning(f"OBS Bridge: Could not set audio tracks for '{_sn}': {e}")

            return result

        return self._safe_call(_create)

    # ── Visual Overlay Sources ──────────────────────────────────────────────

    def set_encoder_settings(self, keyint_sec: int = 2, bitrate: int = 3000,
                              preset: str = "veryfast", rate_control: str = "CBR") -> dict:
        """Set the streaming encoder (x264) keyframe interval and bitrate.

        OBS's Advanced mode reads keyint_sec from basic.ini's [AdvOut] section,
        but may override it with cached encoder-specific data. This method
        explicitly pushes the correct encoder settings via the WebSocket API,
        which takes effect immediately (even while streaming).

        YouTube Live requires keyframes ≤4 seconds apart. With keyint_sec=2
        at 30 FPS, keyframes appear every 60 frames = 2 seconds — well within
        YouTube's spec and giving "Good" or "Excellent" stream health.

        Without this, OBS may use keyint=250 (default x264) which gives 8.3s
        keyframe intervals at 30fps — YouTube shows "Poor" stream health.

        Args:
            keyint_sec: Keyframe interval in seconds (default 2)
            bitrate: Video bitrate in kbps (default 3000)
            preset: x264 preset (default "veryfast")
            rate_control: Rate control mode (default "CBR")
        """
        if not self.enabled:
            return {"error": "OBS Bridge is disabled", "connected": False}

        client = self._connect()
        if client is None:
            return {"error": "OBS is not running", "connected": False}

        results = {}
        try:
            # Build updated encoder settings
            # For obs_x264, the key names are:
            #   keyint_sec → keyframe interval in seconds
            #   bitrate → video bitrate in kbps
            #   rate_control → CBR/CRF/VBR
            #   preset → x264 preset name
            #   x264opts → raw x264 options (OBS passes verbatim to libx264)
            #
            # CRITICAL: x264opts is the NUCLEAR override. OBS's
            # ApplyServiceSettings logic can override keyint_sec and bitrate
            # with YouTube defaults (keyint=250, bitrate=2500), but
            # x264opts is passed directly to libx264 and ALWAYS wins.
            # "keyint=60:min-keyint=60:bframes=0" forces 2-second keyframes
            # at 30fps regardless of what OBS thinks YouTube wants.
            encoder_settings = {
                "keyint_sec": str(keyint_sec),
                "bitrate": str(bitrate),
                "rate_control": rate_control,
                "preset": preset,
                "profile": "high",
                "tune": "zerolatency",
                "x264opts": "keyint=60:min-keyint=60:bframes=0",
            }

            # Push via raw OBS WebSocket RPC — SetStreamEncoderSettings
            # obsws-python does NOT have a named method for this, so we use
            # send() with the raw request type. The RPC expects:
            #   encoderSettings: dict of encoder key=value pairs
            #   encoderName: "obs_x264" (the encoder used for streaming)
            try:
                response = client.send(
                    "SetStreamEncoderSettings",
                    {"encoderSettings": encoder_settings, "encoderName": "obs_x264"},
                )
                results["encoder"] = "ok"
                log.info(
                    f"OBS Bridge: Encoder settings pushed — "
                    f"keyint_sec={keyint_sec}, bitrate={bitrate}, "
                    f"preset={preset}, rc={rate_control}"
                )
            except Exception as e:
                log.debug(f"OBS Bridge: SetStreamEncoderSettings RPC failed: {e}")
                results["encoder"] = f"failed: {e}"

        except Exception as e:
            results["error"] = str(e)
        finally:
            try:
                client.disconnect()
            except Exception:
                pass

        return {"connected": True, "status": "ok", "data": results}

    def create_thumbnail_source(self, scene_name: str = "") -> dict:
        """Create an image source that displays the current song's thumbnail.

        OBS's image_source reads from a local file path. The bot writes the
        current song's thumbnail to /tmp/radio_thumbnail.jpg (downloaded by
        youtube_stream.py play_song()). When the file changes, OBS's
        image_source auto-refreshes on the next frame render.

        On the 1280x720 canvas, the thumbnail is positioned in the
        right portion of the overlay (x=950, y=80, 300x300) — providing
        a nice "album art" area alongside the text on the left.

        A default placeholder image is written at startup so the source
        isn't blank on the first frame.
        """
        if not self.enabled:
            return {"error": "OBS Bridge is disabled", "connected": False}

        if not scene_name:
            scene_name = self._get_current_scene_name()

        # Write a default placeholder thumbnail if none exists
        thumb_path = "/tmp/radio_thumbnail.jpg"
        if not os.path.exists(thumb_path):
            self._create_placeholder_thumbnail()

        def _create(c, _scene=scene_name, _path=thumb_path):
            try:
                existing = c.get_input_settings(name="Song Thumbnail")
                if existing:
                    # Update the file path in case it was wrong
                    try:
                        c.set_input_settings(
                            name="Song Thumbnail",
                            settings={"file": _path, "unload": False},
                            overlay=True,
                        )
                    except Exception:
                        pass
                    return existing
            except Exception:
                pass
            return c.create_input(
                sceneName=_scene,
                inputKind="image_source",
                inputName="Song Thumbnail",
                inputSettings={
                    "file": _path,
                    "unload": False,  # Keep image in memory for fast refresh
                },
                sceneItemEnabled=True,
            )

        result = self._safe_call(_create)
        if not result.get("error"):
            # Position and scale the thumbnail
            self._position_thumbnail(scene_name)
            log.info("OBS Bridge: Song Thumbnail source created ✅")
        return result

    def _create_placeholder_thumbnail(self):
        """Create a simple placeholder thumbnail image at /tmp/radio_thumbnail.jpg.

        Generates a 300x300 dark gray image with a music note emoji equivalent
        using PIL if available, or a minimal valid JPEG as fallback.
        """
        thumb_path = "/tmp/radio_thumbnail.jpg"
        try:
            from PIL import Image, ImageDraw, ImageFont
            img = Image.new("RGB", (300, 300), color=(30, 30, 40))
            draw = ImageDraw.Draw(img)
            # Draw a music-note-like circle and simple text
            draw.ellipse([100, 60, 200, 160], fill=(60, 60, 80), outline=(100, 100, 120))
            draw.text((120, 90), "♪", fill=(140, 140, 160))
            draw.text((85, 200), "No Track", fill=(100, 100, 120))
            img.save(thumb_path, "JPEG")
        except ImportError:
            # PIL not available — write a minimal 1x1 JPEG and let it be replaced
            # when the first song plays
            import struct
            # Minimal JPEG: SOI + APP0 + minimal data + EOI
            # Just write a tiny valid JPEG
            try:
                with open(thumb_path, "wb") as f:
                    # 1x1 gray pixel JPEG
                    f.write(bytes([
                        0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46,
                        0x49, 0x46, 0x00, 0x01, 0x01, 0x00, 0x00, 0x01,
                        0x00, 0x01, 0x00, 0x00, 0xFF, 0xDB, 0x00, 0x43,
                        0x00, 0x08, 0x06, 0x06, 0x07, 0x06, 0x05, 0x08,
                        0x07, 0x07, 0x07, 0x09, 0x09, 0x08, 0x0A, 0x0C,
                        0x14, 0x0D, 0x0C, 0x0B, 0x0B, 0x0C, 0x19, 0x12,
                        0x13, 0x0F, 0x14, 0x1D, 0x1A, 0x1F, 0x1E, 0x1D,
                        0x1A, 0x1C, 0x1C, 0x20, 0x24, 0x2E, 0x27, 0x20,
                        0x22, 0x2C, 0x23, 0x1C, 0x1C, 0x28, 0x37, 0x29,
                        0x2C, 0x30, 0x31, 0x34, 0x34, 0x34, 0x1F, 0x27,
                        0x39, 0x3D, 0x38, 0x32, 0x3C, 0x2E, 0x33, 0x34,
                        0x32, 0xFF, 0xC0, 0x00, 0x0B, 0x08, 0x00, 0x01,
                        0x00, 0x01, 0x01, 0x01, 0x11, 0x00, 0xFF, 0xC4,
                        0x00, 0x1F, 0x00, 0x00, 0x01, 0x05, 0x01, 0x01,
                        0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00,
                        0x00, 0x00, 0x00, 0x00, 0x01, 0x02, 0x03, 0x04,
                        0x05, 0x06, 0x07, 0x08, 0xFF, 0xDA, 0x00, 0x08,
                        0x01, 0x01, 0x00, 0x00, 0x3F, 0x00, 0x7B, 0x94,
                        0x01, 0x00, 0xFF, 0xD9,
                    ]))
            except Exception:
                pass

    def _position_thumbnail(self, scene_name: str):
        """Position and scale the Song Thumbnail source on the canvas.

        Thumbnail layout on 1280x720:
          - Position: (1060, 85) — top-right corner of the overlay
          - Scale: ~0.5 (300×300 image → ~150×150 display)
          - Small "album art" next to text, not overwhelming
        """
        item_id = self._get_scene_item_id(scene_name, "Song Thumbnail")
        if item_id < 0:
            return
        try:
            self._safe_call(
                lambda c, sn=scene_name, iid=item_id: c.set_scene_item_transform(
                    scene_name=sn, item_id=iid,
                    transform={
                        "positionX": 1060,
                        "positionY": 85,
                        "scaleX": 0.5,
                        "scaleY": 0.5,
                    }
                )
            )
        except Exception as e:
            log.debug(f"OBS Bridge: Failed to position thumbnail: {e}")








    def create_gif_source(self, gif_path: str = "", scene_name: str = "") -> dict:
        """Create a media source (ffmpeg_source) that loops an animated GIF.

        OBS's image_source does NOT support animated GIFs — only static images.
        We use ffmpeg_source instead, which can play any media file including
        GIFs, with looping enabled.

        The GIF is positioned as background decoration — behind text but
        above the black background, adding visual energy to the stream.

        Args:
            gif_path: Path to the GIF file. Falls back to assets/sound.gif.
            scene_name: Scene to add the source to (empty = current scene)
        """
        if not self.enabled:
            return {"error": "OBS Bridge is disabled", "connected": False}

        if not scene_name:
            scene_name = self._get_current_scene_name()

        # Resolve GIF path — prefer sounds.gif (500x281) over sound.gif (450x450)
        if not gif_path or not os.path.isfile(gif_path):
            assets_dir = os.path.dirname(os.path.dirname(__file__))
            for name in ("sounds.gif", "sound.gif"):
                candidate = os.path.join(assets_dir, "assets", name)
                if os.path.isfile(candidate):
                    gif_path = candidate
                    break
            if not gif_path:
                log.warning(
                    "OBS Bridge: No GIF file found (set YOUTUBE_STREAM_GIF in .env "
                    "or place assets/sounds.gif). Skipping GIF source."
                )
                return {"error": "No GIF file found", "connected": True}

        def _create(c, _scene=scene_name, _gif=gif_path):
            try:
                existing = c.get_input_settings(name="GIF Overlay")
                if existing:
                    # Update the file path in case it changed
                    try:
                        c.set_input_settings(
                            name="GIF Overlay",
                            settings={
                                "is_local_file": True,
                                "local_file": _gif,
                                "looping": True,
                                "restart_on_activate": True,
                                "close_when_inactive": False,
                            },
                            overlay=True,
                        )
                    except Exception:
                        pass
                    return existing
            except Exception:
                pass
            return c.create_input(
                sceneName=_scene,
                inputKind="ffmpeg_source",
                inputName="GIF Overlay",
                inputSettings={
                    "is_local_file": True,
                    "local_file": _gif,
                    "looping": True,
                    "restart_on_activate": True,
                    "close_when_inactive": False,
                },
                sceneItemEnabled=True,
            )

        result = self._safe_call(_create)
        if not result.get("error"):
            self._position_gif(scene_name)
            log.info(f"OBS Bridge: GIF source created ✅ ({gif_path})")
        return result

    def _position_gif(self, scene_name: str):
        """Position the sound/sounds GIF overlay at the bottom, spanning the full width.

        The GIF is a beat/wave animation for the bottom of the stream.
        Two variants supported:
          - sounds.gif: 500x281 (wider, more frames) — scaleX=2.56
          - sound.gif:  450x450 (square) — scaleX=2.84
        Positioned at (0, 640) so it sits at the bottom edge of the 720 canvas.
        """
        item_id = self._get_scene_item_id(scene_name, "GIF Overlay")
        if item_id < 0:
            return

        # Detect which GIF we're using to get the right scale
        try:
            resp = self._safe_call(
                lambda c: c.get_input_settings(name="GIF Overlay")
            )
            settings = resp.get("data", {})
            if isinstance(settings, dict) and hasattr(settings, "get"):
                current_file = settings.get("local_file", settings.get("input", ""))
            elif isinstance(settings, dict):
                current_file = settings.get("local_file", settings.get("input", ""))
            else:
                current_file = ""
        except Exception:
            current_file = ""

        # Choose scale based on file: sounds.gif=500w → 2.56, sound.gif=450w → 2.84
        if "sounds.gif" in str(current_file):
            scale_x = 2.56  # 1280 / 500
        else:
            scale_x = 2.84  # 1280 / 450

        try:
            self._safe_call(
                lambda c, sn=scene_name, iid=item_id, sx=scale_x: c.set_scene_item_transform(
                    scene_name=sn, item_id=iid,
                    transform={
                        "positionX": 0,
                        "positionY": 640,
                        "scaleX": sx,
                        "scaleY": sx,
                    }
                )
            )
        except Exception as e:
            log.debug(f"OBS Bridge: Failed to position GIF: {e}")

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
        # We only create the "📺 Overlay Only" scene — this is the ONE
        # scene the bot uses. All dynamic content (state indicator,
        # station name, now playing, DJ text, ticker) lives here.
        # We do NOT switch between scenes for different states —
        # instead, a "State" text source (reads from /tmp/radio_state.txt)
        # shows the current state (🎵/🎙️/⏳).
        #
        # The old separate scenes (️ Now Playing, 🎙️ DJ Speaking,
        # ⏳ Waiting) have been removed — they caused problems because
        # switching to them would lose all overlay sources (station name,
        # ticker, etc) that only exist on the Overlay Only scene.
        # Determine the background source type based on whether logo.png exists
        logo_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "assets", "logo.png"
        )
        use_logo = os.path.isfile(logo_path)

        if use_logo:
            bg_source = ("image_source", "Overlay Background", {
                "file": logo_path, "unload": False,
            })
        else:
            bg_source = ("color_source_v3", "Overlay Background", {
                "color": 4278190080, "width": 1280, "height": 720,
            })

        scenes = {
            "📺 Overlay Only": {
                "sources": [bg_source],
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

            # Set this as the current scene BEFORE creating sources.
            # After crash dialog dismissal, OBS's current scene pointer
            # may be null. Sources created on a null current scene will
            # crash OBS with "basic_string: construction from null".
            try:
                self.set_current_scene(scene_name)
            except Exception:
                log.debug(f"OBS Bridge: Could not set current scene to '{scene_name}' before source creation")
            
            # Brief pause to let OBS set the current scene internally
            time.sleep(0.5)

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

        # ── Clean up OBS 32 auto-created "Scene" ──────────────────
        # When OBS 32 starts with a minimal/empty scene collection, it
        # auto-creates a default scene called "Scene". This scene is
        # useless — we use "📺 Overlay Only" instead. Leaving it
        # causes confusion and the bot might accidentally switch to it.
        # We remove it programmatically via WebSocket after creating
        # our real scene.
        try:
            status = self.get_status()
            all_scenes = status.get("scenes", [])
            current = status.get("current_scene", "")
            for stale_scene in ("Scene", "Untitled"):
                if stale_scene in all_scenes:
                    # Don't remove the current scene (would crash OBS)
                    if current == stale_scene and "📺 Overlay Only" in all_scenes:
                        self.set_current_scene("📺 Overlay Only")
                    if current != stale_scene:
                        self._safe_call(
                            lambda c, sn=stale_scene: c.remove_scene(name=sn)
                        )
                        log.info(f"OBS Bridge: Removed auto-created '{stale_scene}' scene")
        except Exception as e:
            log.debug(f"OBS Bridge: Could not clean up default scene: {e}")

        # Initialize /tmp/radio_*.txt files so text_ft2_source_v2 sources
        # with from_file=True have content to display from the first frame.
        # Without these files, the text sources render as blank.
        self._init_hud_files()

        return {"created": created, "errors": errors}

    @staticmethod
    def _init_hud_files():
        """Write /tmp/radio_*.txt files with initial content and create
        a placeholder thumbnail image.

        Always overwrites — these are temp files that should reflect
        the current bot state. We write them even if they already
        exist (previous runs may have stale content).

        OBS text sources with from_file=True read from these files on every
        frame. If the file doesn't exist, the source renders blank text.
        If the file contains an empty string, FreeType2 renders invisible
        zero-height text.

        CRITICAL: Every file must have at least a space " " or some visible
        text. Never leave a file empty (FreeType2 renders "" as invisible).
        """
        # Use config.STATION_NAME (which loads from .env via dotenv) — not
        # os.environ directly.  config.py correctly resolves defaults and
        # handles the .env → environ mapping.  Append " Radio" for the
        # HUD display so "MBot" → "MBot Radio" on the stream overlay.
        try:
            import config
            station_name = getattr(config, "STATION_NAME", "MBot") + " Radio"
        except ImportError:
            station_name = "MBot Radio"

        hud_files = {
            "/tmp/radio_station.txt": station_name,
            "/tmp/radio_state.txt": "⏳ Waiting",
            "/tmp/radio_title.txt": "Waiting for playback...",
            # Space, not empty — FreeType2 renders "" as invisible
            "/tmp/radio_dj.txt": " ",
            "/tmp/radio_waiting.txt": "Initializing...",
        }
        for path, default_text in hud_files.items():
            try:
                with open(path, "w") as f:
                    f.write(default_text)
            except Exception:
                pass

        # Create a placeholder thumbnail image so the OBS image_source
        # has something to display from the first frame (instead of blank/black).
        # This gets replaced with the actual song thumbnail when play_song() runs.
        thumb_path = "/tmp/radio_thumbnail.jpg"
        if not os.path.exists(thumb_path):
            OBSBridge._create_placeholder_thumbnail_static()

    @staticmethod
    def _create_placeholder_thumbnail_static():
        """Static version of placeholder thumbnail creation for _init_hud_files.

        Creates a minimal placeholder JPEG at /tmp/radio_thumbnail.jpg.
        This avoids needing `self` which isn't available in a @staticmethod.
        """
        thumb_path = "/tmp/radio_thumbnail.jpg"
        try:
            from PIL import Image, ImageDraw
            img = Image.new("RGB", (300, 300), color=(30, 30, 40))
            draw = ImageDraw.Draw(img)
            # Dark card with subtle circle
            draw.ellipse([100, 60, 200, 160], fill=(60, 60, 80), outline=(100, 100, 120))
            draw.ellipse([130, 90, 170, 130], fill=(80, 80, 100))
            draw.text((85, 200), "No Track", fill=(100, 100, 120))
            img.save(thumb_path, "JPEG")
        except ImportError:
            # PIL not available — write a minimal 1x1 valid JPEG
            # This will be replaced immediately when a song plays
            try:
                with open(thumb_path, "wb") as f:
                    # Minimal valid JPEG bytes (1x1 gray pixel)
                    f.write(bytes([
                        0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46,
                        0x49, 0x46, 0x00, 0x01, 0x01, 0x00, 0x00, 0x01,
                        0x00, 0x01, 0x00, 0x00, 0xFF, 0xDB, 0x00, 0x43,
                        0x00, 0x08, 0x06, 0x06, 0x07, 0x06, 0x05, 0x08,
                        0x07, 0x07, 0x07, 0x09, 0x09, 0x08, 0x0A, 0x0C,
                        0x14, 0x0D, 0x0C, 0x0B, 0x0B, 0x0C, 0x19, 0x12,
                        0x13, 0x0F, 0x14, 0x1D, 0x1A, 0x1F, 0x1E, 0x1D,
                        0x1A, 0x1C, 0x1C, 0x20, 0x24, 0x2E, 0x27, 0x20,
                        0x22, 0x2C, 0x23, 0x1C, 0x1C, 0x28, 0x37, 0x29,
                        0x2C, 0x30, 0x31, 0x34, 0x34, 0x34, 0x1F, 0x27,
                        0x39, 0x3D, 0x38, 0x32, 0x3C, 0x2E, 0x33, 0x34,
                        0x32, 0xFF, 0xC0, 0x00, 0x0B, 0x08, 0x00, 0x01,
                        0x00, 0x01, 0x01, 0x01, 0x11, 0x00, 0xFF, 0xC4,
                        0x00, 0x1F, 0x00, 0x00, 0x01, 0x05, 0x01, 0x01,
                        0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00,
                        0x00, 0x00, 0x00, 0x00, 0x01, 0x02, 0x03, 0x04,
                        0x05, 0x06, 0x07, 0x08, 0xFF, 0xDA, 0x00, 0x08,
                        0x01, 0x01, 0x00, 0x00, 0x3F, 0x00, 0x7B, 0x94,
                        0x01, 0x00, 0xFF, 0xD9,
                    ]))
            except Exception:
                pass

    # ── Reconnect ─────────────────────────────────────────────────────────

    def wait_for_obs(self, timeout: int = 60, poll_interval: float = 2.0) -> bool:
        """Wait for OBS to become available via WebSocket.

        Polls OBS WebSocket until it responds or timeout is reached.
        This is critical for startup — OBS (especially Flatpak OBS 32)
        can take 10-30 seconds to fully initialize after the process
        starts. Sending WebSocket commands before OBS is ready causes
        crashes ("basic_string: construction from null is not valid").

        Args:
            timeout: Maximum seconds to wait (default 60)
            poll_interval: Seconds between connection attempts (default 2)

        Returns:
            True if OBS connected, False if timeout reached.
        """
        log.info(f"OBS Bridge: Waiting for OBS to become ready (timeout={timeout}s)...")
        start_time = time.time()

        # Temporarily reduce the connection retry interval so we can
        # poll more frequently during startup
        original_retry_interval = self.CONNECTION_RETRY_INTERVAL
        self.CONNECTION_RETRY_INTERVAL = poll_interval

        try:
            while (time.time() - start_time) < timeout:
                # Clear backoff so each attempt actually tries to connect
                self._last_connect_fail = 0

                client = self._connect()
                if client is not None:
                    # Connection succeeded — OBS is ready
                    try:
                        client.disconnect()
                    except Exception:
                        pass
                    elapsed = time.time() - start_time
                    log.info(f"OBS Bridge: OBS is ready! (connected after {elapsed:.1f}s)")
                    return True

                # Not ready yet — wait and try again
                remaining = timeout - (time.time() - start_time)
                if remaining > 0:
                    time.sleep(min(poll_interval, remaining))

            log.warning(f"OBS Bridge: Timed out waiting for OBS after {timeout}s")
            return False

        finally:
            # Restore original retry interval
            self.CONNECTION_RETRY_INTERVAL = original_retry_interval

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

    # ── SFX GIF Cycling ───────────────────────────────────────────────

    @staticmethod
    def _resolve_sfx_gif_paths() -> list:
        """Resolve the 3 NON-default SFX GIF paths (for cycling on sound play).

        Returns only paths to files that actually exist on disk.
        The default GIF (dans trollface) is NOT in this list — it's the
        always-present face that we revert back to.
        """
        assets_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
        candidates = [
            "dans trolface2.gif",
            "mad troll.gif",
            "lol troll.gif",
        ]
        paths = []
        for name in candidates:
            full = os.path.join(assets_dir, name)
            if os.path.isfile(full):
                paths.append(full)
        if paths:
            log.info(f"OBS Bridge: Found {len(paths)} SFX GIFs for cycling")
        else:
            log.debug("OBS Bridge: No SFX GIFs found in assets/")
        return paths

    @staticmethod
    def _resolve_default_sfx_gif() -> str:
        """Resolve the default always-visible SFX GIF path.

        The default is 'dans trollface.gif' — this is always present on the
        stream overlay. When a sound effect plays, the GIF briefly changes
        to one of the other troll faces, then reverts back to this one.
        """
        assets_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
        default = os.path.join(assets_dir, "dans trollface.gif")
        if os.path.isfile(default):
            log.info("OBS Bridge: Default SFX GIF → dans trollface.gif")
            return default
        # Fallback: use the first available SFX GIF if dans trollface is missing
        for name in ("dans trolface2.gif", "mad troll.gif", "lol troll.gif"):
            alt = os.path.join(assets_dir, name)
            if os.path.isfile(alt):
                log.info(f"OBS Bridge: Default SFX GIF → {name} (fallback)")
                return alt
        return ""

    def create_sfx_gif_source(self, scene_name: str = "") -> dict:
        """Create an ffmpeg_source for the SFX GIF (dans trollface — always visible).

        The SFX GIF source always shows dans trollface.gif. When a sound
        effect plays, it briefly flashes to another troll face for ~3s,
        then reverts back to dans trollface.

        Falls back gracefully if no SFX GIFs are found.
        """
        if not self.enabled:
            return {"error": "OBS Bridge is disabled", "connected": False}

        if not self._sfx_default_path:
            return {"error": "No SFX GIFs found", "connected": True}

        if not scene_name:
            scene_name = self._get_current_scene_name()

        # Always start with the default (dans trollface)
        initial_gif = self._sfx_default_path

        def _create(c, _scene=scene_name, _gif=initial_gif):
            try:
                existing = c.get_input_settings(name="SFX GIF")
                if existing:
                    try:
                        c.set_input_settings(
                            name="SFX GIF",
                            settings={
                                "is_local_file": True,
                                "local_file": _gif,
                                "looping": True,
                                "restart_on_activate": True,
                                "close_when_inactive": False,
                            },
                            overlay=True,
                        )
                    except Exception:
                        pass
                    return existing
            except Exception:
                pass
            return c.create_input(
                sceneName=_scene,
                inputKind="ffmpeg_source",
                inputName="SFX GIF",
                inputSettings={
                    "is_local_file": True,
                    "local_file": _gif,
                    "looping": True,
                    "restart_on_activate": True,
                    "close_when_inactive": False,
                },
                sceneItemEnabled=True,
            )

        result = self._safe_call(_create)
        if not result.get("error"):
            self._position_sfx_gif(scene_name)
            log.info("OBS Bridge: SFX GIF source created ✅")
        return result

    def _position_sfx_gif(self, scene_name: str):
        """Position the SFX GIF in the lower-right area of the canvas.

        Always shows dans trollface.gif. Flashes to another troll face
        when a sound effect plays, then reverts back after 3 seconds.
        Placed at (1040, 400), scale 0.4 (~120-190px).
        """
        item_id = self._get_scene_item_id(scene_name, "SFX GIF")
        if item_id < 0:
            return
        try:
            self._safe_call(
                lambda c, sn=scene_name, iid=item_id: c.set_scene_item_transform(
                    scene_name=sn, item_id=iid,
                    transform={
                        "positionX": 1040,
                        "positionY": 400,
                        "scaleX": 0.4,
                        "scaleY": 0.4,
                    }
                )
            )
        except Exception as e:
            log.debug(f"OBS Bridge: Failed to position SFX GIF: {e}")

    def cycle_sfx_gif(self) -> dict:
        """Flash the SFX GIF to a random troll face, then revert to dans trollface.

        Called each time a sound effect plays. Picks one of the 3 alternate
        troll GIFs at random, shows it for ~3 seconds, then reverts back
        to the default (dans trollface.gif) which is always visible on stream.

        The revert uses a background thread with a 3-second sleep —
        non-blocking, fire-and-forget.
        """
        if not self.enabled:
            return {"error": "OBS Bridge is disabled", "connected": False}

        if not self._sfx_gif_paths or not self._sfx_default_path:
            return {"error": "No SFX GIFs found", "connected": True}

        # Pick a random alternate GIF (not the default)
        import random
        flash_gif = random.choice(self._sfx_gif_paths)

        # Cancel any pending revert timer
        if self._sfx_revert_timer and self._sfx_revert_timer.is_alive():
            self._sfx_revert_timer.cancel()

        # Flash to the alternate troll face
        def _flash(c, _gif=flash_gif):
            c.set_input_settings(
                name="SFX GIF",
                settings={
                    "is_local_file": True,
                    "local_file": _gif,
                    "looping": True,
                    "restart_on_activate": True,
                    "close_when_inactive": False,
                },
                overlay=True,
            )

        try:
            result = self._safe_call(_flash)
            log.info(f"OBS Bridge: SFX GIF flashed → {os.path.basename(flash_gif)}")
        except Exception as e:
            log.debug(f"OBS Bridge: Failed to flash SFX GIF: {e}")

        # Schedule revert back to default after 3 seconds
        import threading
        def _revert():
            try:
                self._safe_call(
                    lambda c, _gif=self._sfx_default_path: c.set_input_settings(
                        name="SFX GIF",
                        settings={
                            "is_local_file": True,
                            "local_file": _gif,
                            "looping": True,
                            "restart_on_activate": True,
                            "close_when_inactive": False,
                        },
                        overlay=True,
                    )
                )
                log.debug(f"OBS Bridge: SFX GIF reverted → {os.path.basename(self._sfx_default_path)}")
            except Exception:
                pass

        self._sfx_revert_timer = threading.Timer(3.0, _revert)
        self._sfx_revert_timer.daemon = True
        self._sfx_revert_timer.start()

        return {"connected": True, "status": "ok"}


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