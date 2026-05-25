"""
Camera controller for Canon EOS cameras via EDSDK.

Architecture:
  All EDSDK calls run on a single dedicated worker thread (EDSDKWorker)
  to satisfy Windows COM/STA threading requirements. The main/UI thread
  communicates via a command queue and receives results via Qt signals.

  - Camera: public API, thread-safe, dispatches to EDSDKWorker
  - EDSDKWorker: QThread that owns the EDSDK session
  - CaptureThread: waits for download event after capture
"""

import io
import os
import time
import glob
import queue
import threading
from PIL import Image, ImageFilter
from PyQt5.QtCore import QThread, pyqtSignal, QTimer, Qt

import config
from edsdk_wrapper import (
    EDSDK, EDSDKError,
    kEdsObjectEvent_DirItemRequestTransfer,
    kEdsCameraCommand_ExtendShutDownTimer,
    kEdsPropID_AEMode, kEdsPropID_ISOSpeed,
    kEdsPropID_Tv, kEdsPropID_Av,
)

_edsdk_instance = None


def ensure_digicam_running():
    """No-op — EDSDK doesn't need external software."""
    return True


def stop_digicam():
    """Terminate EDSDK on app shutdown."""
    global _edsdk_instance
    if _edsdk_instance:
        try:
            _edsdk_instance.terminate()
        except Exception:
            pass
        _edsdk_instance = None
    print("[CAMERA] EDSDK beëindigd")


# ── EDSDK Worker Thread ───────────────────────────────────────────

class EDSDKWorker(QThread):
    """Dedicated thread for ALL EDSDK operations.

    EDSDK on Windows uses COM (STA) — all SDK calls must happen on
    the same thread. This worker thread owns the entire EDSDK lifecycle:
    init, connect, live view, capture, events, shutdown.

    The main thread sends commands via a queue. Results come back
    via Qt signals or shared state with threading.Event.
    """

    frame_ready = pyqtSignal(bytes)
    connection_lost = pyqtSignal()
    connected = pyqtSignal(str)        # camera name
    connect_failed = pyqtSignal(str)   # error message
    capture_done = pyqtSignal(str)     # file path
    capture_failed = pyqtSignal(str)   # error message

    def __init__(self):
        super().__init__()
        self._cmd_queue = queue.Queue()
        self._running = False
        self._edsdk = None
        self._live_view_active = False
        self._lv_fail_count = 0
        self._lv_frame_count = 0
        self._camera_name = ""
        # Shared state for capture
        self._download_dir = config.PHOTO_DIR
        self._captured_file = None
        self._capture_event = threading.Event()

    def run(self):
        """Main loop: process commands + poll live view + pump events."""
        self._running = True
        try:
            self._edsdk = EDSDK()
        except Exception as e:
            print(f"[EDSDK-WORKER] DLL laden mislukt: {e}")
            self.connect_failed.emit(f"EDSDK DLL niet gevonden: {e}")
            return
        self._pump_counter = 0
        self._keepalive_counter = 0

        while self._running:
            # 1. Process pending commands (max 10 per iteration)
            try:
                self._process_commands()
            except Exception as e:
                print(f"[EDSDK-WORKER] Command processing error: {e}")

            # 2. Poll live view frame if active
            if self._live_view_active and self._edsdk and self._edsdk._camera:
                try:
                    frame = self._edsdk.get_live_view_frame()
                    if frame:
                        self._lv_fail_count = 0
                        self._lv_frame_count += 1
                        # Emit all frames immediately — no warmup skip
                        if self._lv_frame_count == 1:
                            print(f"[LIVEVIEW] Eerste frame: {len(frame)} bytes")
                        elif self._lv_frame_count == 100:
                            print(f"[LIVEVIEW] 100 frames bereikt")
                        self.frame_ready.emit(frame)
                        # AF at live view start disabled — only AF during countdown
                        # Pump events every 30th frame (~1x per second)
                        self._pump_counter += 1
                        if self._pump_counter >= 30:
                            self._pump_counter = 0
                            try:
                                self._edsdk.pump_events()
                            except Exception:
                                pass
                        # Keep-alive: prevent camera sleep every ~60s (~1500 frames)
                        self._keepalive_counter += 1
                        if self._keepalive_counter >= 1500:
                            self._keepalive_counter = 0
                            try:
                                self._edsdk.extend_shutdown_timer()
                            except Exception:
                                pass
                        self.msleep(2)  # Pace frames ~25fps, prevents UI thread overload
                    else:
                        self._lv_fail_count += 1
                        # Pump events every 10th fail to keep EDSDK state machine running
                        if self._lv_fail_count % 10 == 0:
                            try:
                                self._edsdk.pump_events()
                            except Exception:
                                pass
                        # Auto-restart live view after 200 consecutive fails (~2s)
                        if self._lv_fail_count == 200:
                            print("[LIVEVIEW] 200 fails — herstart live view")
                            try:
                                self._edsdk.stop_live_view()
                                self.msleep(300)
                                self._edsdk.start_live_view()
                                self._lv_fail_count = 0
                                self._lv_frame_count = 0
                            except Exception as e:
                                print(f"[LIVEVIEW] Herstart mislukt: {e}")
                        elif self._lv_fail_count > 1200:  # ~60 sec of no frames
                            print("[LIVEVIEW] FOUT: Geen frames meer (60s)")
                            self._lv_fail_count = 0
                            self.connection_lost.emit()
                        self.msleep(1)
                except EDSDKError as e:
                    self._lv_fail_count += 1
                    if self._lv_fail_count % 100 == 0:
                        print(f"[LIVEVIEW] Frame error ({self._lv_fail_count}x): {e}")
                    # Auto-restart live view after 200 consecutive fails
                    if self._lv_fail_count == 200:
                        print("[LIVEVIEW] 200 fails (EDSDKError) — herstart live view")
                        try:
                            self._edsdk.stop_live_view()
                            self.msleep(300)
                            self._edsdk.start_live_view()
                            self._lv_fail_count = 0
                            self._lv_frame_count = 0
                        except Exception as re:
                            print(f"[LIVEVIEW] Herstart mislukt: {re}")
                    elif self._lv_fail_count > 600:
                        print("[LIVEVIEW] Te veel fouten, connection lost")
                        self._lv_fail_count = 0
                        self.connection_lost.emit()
                    self.msleep(1)
                except Exception as e:
                    self._lv_fail_count += 1
                    if self._lv_fail_count > 600:
                        self._lv_fail_count = 0
                        self.connection_lost.emit()
                    self.msleep(1)
            else:
                # No live view: pump events + keep-alive + slower loop
                if self._edsdk and self._edsdk._initialized:
                    try:
                        self._edsdk.pump_events()
                    except Exception:
                        pass
                    # Keep-alive every ~30s (30ms * 1000 = 30s)
                    self._keepalive_counter += 1
                    if self._keepalive_counter >= 1000:
                        self._keepalive_counter = 0
                        if self._edsdk._camera:
                            try:
                                self._edsdk.extend_shutdown_timer()
                            except Exception:
                                pass
                self.msleep(30)

        # Cleanup
        if self._edsdk:
            try:
                self._edsdk.terminate()
            except Exception:
                pass

    def send_command(self, cmd, **kwargs):
        """Send a command to the worker thread (thread-safe)."""
        self._cmd_queue.put((cmd, kwargs))

    def _process_commands(self):
        """Process all queued commands."""
        while not self._cmd_queue.empty():
            try:
                cmd, kwargs = self._cmd_queue.get_nowait()
                self._handle_command(cmd, kwargs)
            except queue.Empty:
                break

    def _handle_command(self, cmd, kwargs):
        """Execute a command on the EDSDK thread."""
        try:
            if cmd == "connect":
                self._do_connect()
            elif cmd == "disconnect":
                self._do_disconnect()
            elif cmd == "start_live_view":
                self._do_start_live_view()
            elif cmd == "stop_live_view":
                self._do_stop_live_view()
            elif cmd == "capture":
                use_af = kwargs.get("use_af", False)
                self._do_capture(use_af)
            elif cmd == "pre_focus":
                label = kwargs.get("label", "Pre-focus")
                self._do_pre_focus(label)
            elif cmd == "cancel_focus":
                try:
                    self._edsdk.do_autofocus(on=False)
                except Exception:
                    pass
            elif cmd == "set_download_dir":
                self._download_dir = kwargs.get("path", config.PHOTO_DIR)
                if self._edsdk:
                    self._edsdk._download_dir = self._download_dir
            elif cmd == "shutdown":
                self._do_disconnect()
                self._running = False
        except Exception as e:
            print(f"[EDSDK-WORKER] Commando '{cmd}' mislukt: {e}")

    def _do_connect(self):
        """Initialize EDSDK and connect to camera."""
        global _edsdk_instance
        try:
            if not self._edsdk:
                self.connect_failed.emit("EDSDK niet geladen")
                return

            self._edsdk.initialize()
            _edsdk_instance = self._edsdk

            cameras = self._edsdk.get_camera_list()
            if not cameras:
                self.connect_failed.emit("Geen camera gevonden.\n"
                    "Controleer USB-verbinding en zet de camera aan.")
                return

            name = self._edsdk.open_session(0)

            # Configure internal download
            self._edsdk._download_dir = self._download_dir
            self._edsdk._download_event = self._capture_event

            def _on_object_event(event, ref):
                try:
                    if event == kEdsObjectEvent_DirItemRequestTransfer:
                        self._captured_file = self._edsdk._downloaded_file
                        print(f"[CAMERA] Foto ontvangen: {self._captured_file}")
                except Exception as e:
                    print(f"[CAMERA] Object event error: {e}")

            self._edsdk.on_object_event = _on_object_event

            def _on_state_event(event, param):
                from edsdk_wrapper import kEdsStateEvent_Shutdown
                if event == kEdsStateEvent_Shutdown:
                    print("[CAMERA] Camera uitgeschakeld! Connection lost.")
                    self._live_view_active = False
                    self.connection_lost.emit()

            self._edsdk.on_state_event = _on_state_event
            # Configure camera for optimal photobooth settings
            try:
                self._edsdk.configure_for_photobooth()
            except Exception as e:
                print(f"[CAMERA] Photobooth config mislukt (genegeerd): {e}")
            self._camera_name = name
            self.connected.emit(name)
            print(f"[CAMERA] Verbonden: {name}")
        except EDSDKError as e:
            msg = str(e)
            if "0xC0" in msg or "vastgelopen" in msg:
                msg += "\n\nZet de camera UIT en weer AAN."
            self.connect_failed.emit(msg)
            print(f"[CAMERA] Verbinding mislukt: {e}")
        except Exception as e:
            self.connect_failed.emit(f"Onverwachte fout: {e}")
            print(f"[CAMERA] Verbinding mislukt: {e}")

    def _do_disconnect(self):
        """Close camera session."""
        self._live_view_active = False
        try:
            if self._edsdk and self._edsdk._initialized and self._edsdk._camera:
                try:
                    self._edsdk.stop_live_view()
                except Exception:
                    pass
                self._edsdk.close_session()
        except Exception as e:
            print(f"[CAMERA] Disconnect fout (genegeerd): {e}")

    def _do_start_live_view(self):
        """Start live view on camera with continuous autofocus."""
        if not self._edsdk or not self._edsdk._camera:
            print("[CAMERA] Live view: geen camera verbonden, probeer reconnect...")
            # Try to reconnect
            try:
                self._do_connect()
            except Exception as e:
                print(f"[CAMERA] Reconnect mislukt: {e}")
            if not self._edsdk or not self._edsdk._camera:
                print("[CAMERA] Live view: reconnect mislukt, geen camera")
                return
        try:
            self._edsdk.start_live_view()
            self._live_view_active = True
            self._lv_fail_count = 0
            self._lv_frame_count = 0
            # Pump events aggressively to kick-start EVF frame delivery
            import time as _t2
            for i in range(20):
                try:
                    self._edsdk.pump_events()
                except Exception:
                    pass
                frame = self._edsdk.get_live_view_frame()
                if frame:
                    self._lv_frame_count = 1
                    self.frame_ready.emit(frame)
                    print(f"[LIVEVIEW] Eerste frame: {len(frame)} bytes (na {i+1} pumps)")
                    break
                _t2.sleep(0.05)
            print("[CAMERA] Live view gestart")
        except EDSDKError as e:
            print(f"[CAMERA] Live view start mislukt (EDSDK): {e}")
            self._live_view_active = False
            # If camera handle is invalid, try full reconnect
            if "0x" in str(e):
                print("[CAMERA] Probeer volledige reconnect...")
                try:
                    self._do_disconnect()
                    self.msleep(1000)
                    self._do_connect()
                    if self._edsdk and self._edsdk._camera:
                        self._edsdk.start_live_view()
                        self._live_view_active = True
                        self._lv_fail_count = 0
                        self._lv_frame_count = 0
                        print("[CAMERA] Live view gestart na reconnect")
                except Exception as re:
                    print(f"[CAMERA] Reconnect live view mislukt: {re}")
                    self._live_view_active = False
        except Exception as e:
            print(f"[CAMERA] Live view start mislukt: {e}")
            self._live_view_active = False

    def _do_stop_live_view(self):
        """Stop live view."""
        self._live_view_active = False
        try:
            self._edsdk.stop_live_view()
        except Exception:
            pass

    def _do_capture(self, use_af):
        """Execute capture sequence with robust error handling."""
        import time as _t
        t0 = _t.time()
        self._capture_event.clear()
        self._captured_file = None

        try:
            # Stop live view before capture (minimal delay)
            was_lv = self._live_view_active
            if self._live_view_active:
                self._live_view_active = False
                try:
                    self._edsdk.stop_live_view()
                except Exception:
                    pass
                _t.sleep(0.1)

            # Pump events to clear pending callbacks before capture
            for _ in range(5):
                try:
                    self._edsdk.pump_events()
                except Exception:
                    pass
                _t.sleep(0.05)

            # Send capture command with retry
            # Always use TakePicture — works in ALL modes (P, Av, Tv, M, A-DEP, Auto)
            # PressShutterButton/NonAF does NOT work in all modes
            capture_sent = False
            for attempt in range(5):
                try:
                    self._edsdk.take_picture()
                    capture_sent = True
                    print(f"[CAMERA] Capture verzonden (poging {attempt+1})")
                    break
                except EDSDKError as e:
                    err_str = str(e)
                    if "0x00000081" in err_str or "BUSY" in err_str.upper():
                        print(f"[CAMERA] Camera bezig, wacht... (poging {attempt+1})")
                        _t.sleep(1.0)
                        try:
                            self._edsdk.pump_events()
                        except Exception:
                            pass
                    else:
                        print(f"[CAMERA] TakePicture mislukt: {err_str} (poging {attempt+1})")
                        _t.sleep(0.5)
                except Exception as e:
                    print(f"[CAMERA] Capture fout: {e} (poging {attempt+1})")
                    _t.sleep(0.5)

            if not capture_sent:
                self.capture_failed.emit("Camera reageert niet op capture commando.")
                return

            # Wait for download (pump events while waiting)
            t_start = time.time()
            timeout = getattr(config, 'CAPTURE_TIMEOUT_SEC', 20)
            while time.time() - t_start < timeout:
                try:
                    self._edsdk.pump_events()
                except Exception:
                    pass
                if self._capture_event.is_set():
                    filepath = self._captured_file
                    # File may still be writing — brief retry
                    for retry in range(15):
                        if filepath and os.path.isfile(filepath):
                            try:
                                size_kb = os.path.getsize(filepath) / 1024
                                if size_kb > 10:  # >10KB = valid photo
                                    total_ms = (time.time() - t_start) * 1000
                                    print(f"[CAPTURE] Klaar in {total_ms:.0f}ms: "
                                          f"{filepath} ({size_kb:.0f}KB)")
                                    self._had_capture = True
                                    self.capture_done.emit(filepath)
                                    return
                            except OSError:
                                pass
                        time.sleep(0.2)
                    # File not found after retries
                    print(f"[CAPTURE] Bestand niet gevonden na download event: {filepath}")
                    self.capture_failed.emit(
                        "Foto opgeslagen maar bestand niet gevonden.\n"
                        "Controleer opslagruimte."
                    )
                    return
                time.sleep(0.05)

            self.capture_failed.emit(
                f"Capture timeout na {timeout}s.\n\n"
                "Camera reageert niet. Controleer de USB-verbinding."
            )
        except Exception as e:
            print(f"[CAMERA] Capture exception: {e}")
            self.capture_failed.emit(f"Capture mislukt: {e}")

    def _do_pre_focus(self, label):
        """Trigger EVF autofocus (best-effort, may not work in all modes)."""
        try:
            self._edsdk.do_autofocus(on=True)
            print(f"[CAMERA] {label}: AF gestart")
        except EDSDKError as e:
            print(f"[CAMERA] {label}: AF niet beschikbaar ({e}) — genegeerd")
        except Exception as e:
            print(f"[CAMERA] {label}: AF fout ({e}) — genegeerd")

    def get_camera_properties(self):
        """Get camera properties (call from worker thread only)."""
        if not self._edsdk or not self._edsdk._camera:
            return ""
        try:
            name = self._edsdk.get_product_name()
            battery = self._edsdk.get_battery_level()
            shots = self._edsdk.get_available_shots()
            info = f"Camera: {name} | Batterij: {battery}% | Shots: {shots}"
            return info
        except Exception:
            return ""


# ── Camera class (public API) ─────────────────────────────────────

class CameraError(Exception):
    pass


class Camera:
    """Canon EOS camera interface via EDSDK.

    All EDSDK operations are dispatched to EDSDKWorker thread.
    This class is safe to use from the UI/main thread.
    """

    def __init__(self):
        self._worker = EDSDKWorker()
        self._connected = False
        self._live_view_started = False
        self._focus_done = False
        self._focus_cancelled = False
        self._use_edsdk = True

        # Capture state (shared with worker)
        self._edsdk_download_path = config.PHOTO_DIR
        self._edsdk_captured_file = None
        self._edsdk_capture_event = self._worker._capture_event

        # Connect signals
        self._connect_event = threading.Event()
        self._connect_result = None
        self._worker.connected.connect(self._on_connected, Qt.DirectConnection)
        self._worker.connect_failed.connect(self._on_connect_failed, Qt.DirectConnection)
        self._worker.capture_done.connect(self._on_capture_done, Qt.DirectConnection)
        self._worker.capture_failed.connect(self._on_capture_failed_internal, Qt.DirectConnection)

        # Start worker thread
        self._worker.start()

    def _on_connected(self, name):
        self._connected = True
        self._connect_result = name
        self._connect_event.set()

    def _on_connect_failed(self, error):
        self._connected = False
        self._connect_result = None
        self._connect_event.set()

    def _on_capture_done(self, filepath):
        self._edsdk_captured_file = filepath

    def _on_capture_failed_internal(self, error):
        pass

    def connect(self):
        """Initialize SDK and connect to camera. Blocks until done."""
        self._connect_event.clear()
        self._worker.send_command("connect")
        # Wait for result (with timeout)
        self._connect_event.wait(timeout=15)
        if self._connected:
            # Get camera properties on the worker thread via sync call
            self._worker.send_command("set_download_dir", path=config.PHOTO_DIR)
        return self._connected

    def disconnect(self):
        self._connected = False
        self._live_view_started = False
        self._worker.send_command("disconnect")
        print("[CAMERA] Verbinding gesloten")

    def is_connected(self):
        return self._connected

    def start_live_view(self):
        # Always send command — worker handles reconnect if needed
        self._worker.send_command("start_live_view")
        self._live_view_started = True
        return True

    def stop_live_view(self):
        if self._live_view_started:
            self._worker.send_command("stop_live_view")
        self._live_view_started = False

    def get_live_view_frame(self):
        """Not used directly — worker emits frame_ready signal."""
        return None

    def pre_focus(self, label="Pre-focus"):
        self._focus_done = False
        self._focus_cancelled = False
        self._worker.send_command("pre_focus", label=label)

    def cancel_focus(self):
        self._focus_cancelled = True
        self._worker.send_command("cancel_focus")

    def capture_photo(self, use_af=False):
        """Trigger capture on worker thread."""
        self._edsdk_capture_event.clear()
        self._edsdk_captured_file = None
        self._live_view_started = False
        self._worker.send_command("capture", use_af=use_af)

    def configure_save_folder(self):
        os.makedirs(config.PHOTO_DIR, exist_ok=True)
        self._edsdk_download_path = config.PHOTO_DIR
        self._worker.send_command("set_download_dir", path=config.PHOTO_DIR)

    def set_session_folder(self, folder_path):
        os.makedirs(folder_path, exist_ok=True)
        self._edsdk_download_path = folder_path
        self._worker.send_command("set_download_dir", path=folder_path)

    def get_camera_properties(self):
        """Get camera info. Runs sync on worker."""
        # Quick hack: read from worker's cached name
        info = f"Camera: {self._worker._camera_name}"
        print(f"[CAMERA] {info}")
        return info

    def get_property(self, name):
        """Get camera property (stub for settings UI compatibility)."""
        return ''

    def set_property(self, name, value):
        print(f"[CAMERA] set_property({name}, {value}) niet ondersteund via EDSDK")

    def list_property_values(self, name):
        return []

    def pump_events(self):
        """No-op — worker thread handles event pumping."""
        pass

    def shutdown(self):
        """Shutdown worker thread cleanly."""
        self._connected = False
        self._live_view_started = False
        self._worker.send_command("shutdown")
        if not self._worker.wait(5000):
            print("[CAMERA] Worker thread reageert niet, forceer stop")
            self._worker._running = False
            self._worker.wait(2000)


# ── Capture Thread ──────────────────────────────────────────────────

class CaptureThread(QThread):
    """Waits for capture completion from EDSDKWorker.

    The worker handles the actual EDSDK capture + download.
    This thread just waits for the result signal.
    """

    capture_complete = pyqtSignal(str)
    capture_failed = pyqtSignal(str)

    def __init__(self, camera, use_af=False, search_folders=None,
                 existing_files=None):
        super().__init__()
        self.camera = camera
        self.use_af = use_af
        self._search_folders = search_folders or set()
        self._existing_files = existing_files or set()

    def start_capture(self):
        """Trigger capture via worker, then wait in background."""
        import time as _time
        self._capture_t0 = _time.time()
        print("[CAPTURE] >>> Capture commando verzenden...")
        self.camera.capture_photo(use_af=self.use_af)
        self.start()

    def run(self):
        """Wait for capture event from worker."""
        t_start = getattr(self, '_capture_t0', time.time())
        timeout = getattr(config, 'CAPTURE_TIMEOUT_SEC', 15)

        while time.time() - t_start < timeout:
            if self.camera._edsdk_capture_event.is_set():
                # Try multiple sources for filepath (race condition mitigation)
                filepath = self.camera._edsdk_captured_file
                if not filepath:
                    filepath = getattr(self.camera._worker, '_captured_file', None)
                if not filepath:
                    # Worker may still be processing — wait briefly
                    for _ in range(10):
                        time.sleep(0.1)
                        filepath = self.camera._edsdk_captured_file
                        if not filepath:
                            filepath = getattr(self.camera._worker, '_captured_file', None)
                        if filepath:
                            break
                # File may still be writing — brief retry
                for _ in range(10):
                    if filepath and os.path.isfile(filepath):
                        try:
                            size_kb = os.path.getsize(filepath) / 1024
                            if size_kb > 10:  # >10KB = valid photo
                                total_ms = (time.time() - t_start) * 1000
                                print(f"[CAPTURE] Klaar in {total_ms:.0f}ms: "
                                      f"{filepath} ({size_kb:.0f}KB)")
                                self.capture_complete.emit(filepath)
                                return
                        except OSError:
                            pass
                    time.sleep(0.2)
                print(f"[CAPTURE] Bestand niet gevonden: filepath={filepath}")
                self.capture_failed.emit(
                    "Foto opgeslagen maar bestand niet gevonden.\n"
                    "Controleer opslagruimte."
                )
                return
            time.sleep(0.1)

        self.capture_failed.emit(
            f"Capture timeout na {timeout}s.\n\n"
            "Camera reageert niet. Controleer de USB-verbinding."
        )


# ── Helper functions ────────────────────────────────────────────────

def get_search_folders(camera):
    """Return folders where photos are saved."""
    folders = {config.PHOTO_DIR}
    if hasattr(camera, '_edsdk_download_path') and camera._edsdk_download_path:
        folders.add(camera._edsdk_download_path)
    return folders


def snapshot_files(folders):
    """Get snapshot of existing image files in folders."""
    files = set()
    extensions = ("*.jpg", "*.jpeg", "*.png", "*.cr2", "*.cr3")
    for folder in folders:
        for ext in extensions:
            files.update(glob.glob(os.path.join(folder, ext)))
    return files
