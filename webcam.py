"""
Webcam camera backend.

Provides the same interface as camera.py (Camera class) but uses
OpenCV (cv2) for USB webcam capture instead of Canon EDSDK.

Live view frames are emitted as JPEG bytes via Qt signals,
identical to the EDSDK worker — so the rest of the app
doesn't need to know which camera type is active.
"""

import os
import sys
import time
import threading

try:
    import cv2
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False
    print("[WEBCAM] OpenCV (cv2) niet beschikbaar")

from PyQt5.QtCore import QThread, pyqtSignal, Qt


class WebcamWorker(QThread):
    """Background thread that reads webcam frames.

    Emits frames as PNG bytes (lossless) for maximum quality live view.
    Stores raw BGR frames for high-quality capture.
    """

    frame_ready = pyqtSignal(bytes)

    def __init__(self, cap, parent=None):
        super().__init__(parent)
        self.cap = cap
        self._running = False
        self._last_raw_frame = None  # Full resolution BGR frame for capture
        self._frame_lock = threading.Lock()
        # Camera-instellingen worden NIET rechtstreeks vanaf een andere draad
        # gezet. cv2.VideoCapture is niet draadveilig, en deze draad zit
        # vrijwel continu in cap.read(). Verzoeken komen in deze wachtrij en
        # worden hier tussen twee frames door afgehandeld.
        self._wachtrij = []
        self._wachtrij_slot = threading.Lock()

    # Live-view = schermpreview, geen eindproduct. Daarom bewust zuinig:
    #   - encode-breedte beperkt tot 1280 (schermpreview is nooit groter en
    #     de UI schaalt toch naar de label-grootte) → veel kleinere JPEG,
    #     dus snellere encode én decode in de UI;
    #   - JPEG-kwaliteit 80 i.p.v. 95 (op een preview onzichtbaar, ~2× sneller);
    #   - ~25 fps i.p.v. 33 → ~25% minder werk.
    # De CAPTURE-foto gebruikt _last_raw_frame (volledige resolutie), die blijft
    # onaangetast — kwaliteit van de échte foto's verandert niet.
    _LV_MAX_W = 1280
    _LV_JPEG_Q = 80
    _LV_SLEEP_MS = 40  # ~25 fps

    def zet_prop_async(self, prop_id, waarde, klaar=None):
        """Vraag om een camera-instelling. Wordt door de leesdraad uitgevoerd.

        `klaar` is een optionele functie die de teruggelezen waarde krijgt,
        zodat de aanvrager kan zien of de driver de instelling écht overnam.
        """
        with self._wachtrij_slot:
            self._wachtrij.append((prop_id, waarde, klaar))

    def lees_prop(self, prop_id):
        """Lees een camera-instelling. Alleen lezen is onschuldig genoeg om
        rechtstreeks te doen."""
        try:
            return float(self.cap.get(prop_id))
        except Exception:
            return 0.0

    def snapshot(self):
        """Kopie van het laatste volledige frame, of None."""
        with self._frame_lock:
            if self._last_raw_frame is None:
                return None
            return self._last_raw_frame.copy()

    def _verwerk_wachtrij(self):
        with self._wachtrij_slot:
            taken = self._wachtrij
            self._wachtrij = []
        for prop_id, waarde, klaar in taken:
            gelezen = None
            try:
                self.cap.set(prop_id, waarde)
                gelezen = float(self.cap.get(prop_id))
            except Exception as e:
                print(f"[BELICHTING] Instelling {prop_id} zetten mislukt: {e}")
            if klaar:
                try:
                    klaar(gelezen)
                except Exception:
                    pass

    def run(self):
        self._running = True
        while self._running and self.cap and self.cap.isOpened():
            if self._wachtrij:
                self._verwerk_wachtrij()
            ret, frame = self.cap.read()
            if ret:
                # Store full-res frame for capture (ongewijzigd, volle kwaliteit)
                with self._frame_lock:
                    self._last_raw_frame = frame

                # Downscale voor de preview-stream (capture gebruikt de raw frame)
                h, w = frame.shape[:2]
                if w > self._LV_MAX_W:
                    scale = self._LV_MAX_W / w
                    frame = cv2.resize(frame, (self._LV_MAX_W, int(h * scale)),
                                       interpolation=cv2.INTER_AREA)

                _, buf = cv2.imencode('.jpg', frame,
                                      [cv2.IMWRITE_JPEG_QUALITY, self._LV_JPEG_Q])
                self.frame_ready.emit(buf.tobytes())
            self.msleep(self._LV_SLEEP_MS)

    # JPEG bewaart standaard maar een kwart van de kleurinformatie (4:2:0):
    # het helderheidskanaal blijft op volle resolutie, maar de twee
    # kleurkanalen worden gehalveerd in breedte én hoogte. Op een foto van een
    # gezicht valt dat nauwelijks op, maar op harde kleurovergangen wel — de
    # rand van een gekleurd overlay-kader, een logo, gekleurde tekst op de
    # strip. Daar ontstaan dan uitgelopen randjes, en die worden zichtbaarder
    # naarmate de strip verder wordt bewerkt: de opname wordt geschaald,
    # geplakt in een sjabloon en nog een keer als JPEG opgeslagen.
    #
    # 4:4:4 bewaart de kleur op volle resolutie. De kwaliteit blijft 98 — dit
    # staat daar los van; kwaliteit regelt hoe grof er wordt afgerond, de
    # bemonstering regelt hoeveel kleur er überhaupt wordt bewaard.
    #
    # Gemeten op de beelden in deze repo (kwaliteit 98):
    #   1920x1280   219 KB -> 293 KB   (+34%)
    #   2763x1842   328 KB -> 449 KB   (+37%)
    #   1080x1920   171 KB -> 246 KB   (+44%)
    # gemiddeld ongeveer +37%. Op een beeld dat alleen uit kleurruis bestaat —
    # de theoretische bovengrens — verdubbelt het bestand. In de praktijk komt
    # dat niet voor.
    #
    # Die groei geldt alleen voor de ruwe opname op de schijf van de booth. Wat
    # naar de cloud gaat wordt apart gecomprimeerd (zie compress_photo in
    # cloud_storage.py), dus de upload en de wachttijd voor de gast veranderen
    # hier niet door.
    _JPEG_PARAMS_CAPTURE = [cv2.IMWRITE_JPEG_QUALITY, 98] if _CV2_AVAILABLE else []
    if _CV2_AVAILABLE and hasattr(cv2, 'IMWRITE_JPEG_SAMPLING_FACTOR_444'):
        _JPEG_PARAMS_CAPTURE = _JPEG_PARAMS_CAPTURE + [
            cv2.IMWRITE_JPEG_SAMPLING_FACTOR,
            cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444,
        ]

    def get_capture_frame(self):
        """Get the latest full-resolution frame as high-quality JPEG bytes."""
        with self._frame_lock:
            if self._last_raw_frame is not None:
                _, buf = cv2.imencode('.jpg', self._last_raw_frame,
                                      self._JPEG_PARAMS_CAPTURE)
                return buf.tobytes()
        return None

    def stop(self):
        self._running = False
        self.wait(2000)


class WebcamCamera:
    """Webcam camera backend with the same interface as Camera class.

    Usage:
        cam = WebcamCamera()
        cam.connect(device_index=0, resolution="1920x1080")
        cam._worker.frame_ready.connect(on_frame)
        cam.start_live_view()
        # ... later:
        cam.capture_photo()  # saves current frame
        cam.disconnect()
    """

    def __init__(self):
        self.cap = None
        self._connected = False
        self._worker = None
        self._live_running = False
        self._last_frame = None
        self._save_dir = ""
        self._photo_counter = 0
        self._camera_name = "Webcam"

    def connect(self, device_index=0, resolution="", camera_name=""):
        """Open webcam via OpenCV.

        Args:
            device_index: OpenCV camera index (from list_cameras)
            resolution: Optional "WxH" string (e.g. "1920x1080")
            camera_name: Camera name for fallback matching if index fails

        Returns:
            True if connected successfully.
        """
        if not _CV2_AVAILABLE:
            print("[WEBCAM] OpenCV niet beschikbaar")
            return False

        print(f"[WEBCAM] Verbinden met OpenCV index {device_index} (naam={camera_name})...")
        try:
            self.cap = cv2.VideoCapture(device_index, cv2.CAP_DSHOW)
            self._apply_resolution(self.cap, resolution)

            self._connected = self.cap.isOpened()

            # If saved index fails and we have a name, try to find camera by name
            if not self._connected and camera_name:
                print(f"[WEBCAM] Index {device_index} mislukt, zoeken op naam '{camera_name}'...")
                self.cap.release()
                try:
                    cameras = self.list_cameras()
                    for idx, name in cameras:
                        if camera_name.lower() in name.lower() or name.lower() in camera_name.lower():
                            print(f"[WEBCAM] Naam match gevonden: index {idx} = '{name}'")
                            self.cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
                            self._apply_resolution(self.cap, resolution)
                            self._connected = self.cap.isOpened()
                            if self._connected:
                                device_index = idx
                                break
                            self.cap.release()
                except Exception as e:
                    print(f"[WEBCAM] Naam-zoeken mislukt: {e}")

            # If still not connected, try all indices as last resort
            if not self._connected:
                print(f"[WEBCAM] Fallback: alle indices proberen...")
                for try_idx in range(5):
                    if try_idx == device_index:
                        continue  # Already tried
                    try:
                        self.cap = cv2.VideoCapture(try_idx, cv2.CAP_DSHOW)
                        if self.cap.isOpened():
                            self._apply_resolution(self.cap, resolution)
                            self._connected = True
                            device_index = try_idx
                            print(f"[WEBCAM] Fallback gevonden op index {try_idx}")
                            break
                        self.cap.release()
                    except Exception:
                        continue

            if self._connected:
                actual_w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                actual_h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                self._camera_name = f"Webcam {device_index} ({actual_w}x{actual_h})"
                print(f"[WEBCAM] Verbonden: {self._camera_name}")

                # Create worker (but don't start yet)
                self._worker = WebcamWorker(self.cap)
                # Store last frame for capture
                self._worker.frame_ready.connect(self._store_frame)
            else:
                print(f"[WEBCAM] Geen webcam gevonden (index {device_index} + naam '{camera_name}' + fallback)")

            return self._connected
        except Exception as e:
            print(f"[WEBCAM] Verbindingsfout: {e}")
            return False

    @staticmethod
    def _apply_resolution(cap, resolution):
        """Set capture resolution. Lege string = hoogste beschikbare.

        OpenCV clamp-t te hoge waarden naar de echte max van de camera, dus
        3840x2160 zetten resulteert in de werkelijke max-resolutie van het
        apparaat (vaak 1920x1080 bij laptops/tablets, hoger bij externe cams).
        """
        if resolution:
            try:
                w, h = map(int, resolution.split("x"))
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, w)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
                return
            except (ValueError, AttributeError):
                pass
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 3840)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 2160)

    def _store_frame(self, jpeg_bytes):
        """Keep reference to latest frame for live view."""
        self._last_frame = jpeg_bytes

    def capture_high_res(self):
        """Get the latest full-resolution frame at high quality.
        Returns JPEG bytes or None."""
        # Get full-res frame from worker (no race condition with cap.read)
        if self._worker:
            data = self._worker.get_capture_frame()
            if data:
                return data
        # Fallback to last live view frame
        return self._last_frame

    # ── Belichtingskalibratie ────────────────────────────────────────
    #
    # Zie exposure.py voor het waarom. Hier staat alleen de koppeling met de
    # camera: meten op het laatste live-frame, en een instelling zetten via
    # de wachtrij van de leesdraad.
    #
    # Alles hieronder is faalveilig. Lukt er iets niet, dan blijft de camera
    # gewoon staan zoals hij stond en gaat de sessie door. Een booth die geen
    # foto meer maakt is oneindig veel erger dan een foto die een tik te
    # donker is.

    def beoordeel_beeld(self):
        """Oordeel over het laatste live-frame. Zie exposure.beoordeel()."""
        try:
            import exposure
            if not self._worker:
                return None
            return exposure.beoordeel(self._worker.snapshot())
        except Exception as e:
            print(f"[BELICHTING] Beoordelen mislukt: {e}")
            return None

    def beoordeel_bestand(self, pad):
        """Oordeel over een opgeslagen foto. Gebruikt om per foto vast te
        leggen wat er daadwerkelijk op de plaat staat."""
        try:
            import exposure
            if not _CV2_AVAILABLE:
                return None
            beeld = cv2.imread(pad)
            return exposure.beoordeel(beeld)
        except Exception as e:
            print(f"[BELICHTING] Beoordelen van {pad} mislukt: {e}")
            return None

    def probeer_belichtingsinstellingen(self):
        """Zoek uit welke camera-instelling op dit apparaat echt werkt.

        Duurt even (er worden waarden gezet en teruggelezen) en laat het beeld
        zichtbaar schommelen. Daarom nooit tijdens een sessie aanroepen —
        alleen bij het opbouwen van de booth.
        """
        try:
            import exposure, time as _t
            if not self._worker:
                return {"instelling": "none", "gevoeligheid": 0.0, "basis": 0.0}

            def lees_frame():
                # Even wachten zodat de camera de nieuwe instelling echt in
                # het beeld heeft doorgevoerd. Webcams lopen een paar frames
                # achter op een cap.set().
                _t.sleep(0.35)
                return self._worker.snapshot()

            def zet(pid, waarde):
                klaar = threading.Event()
                self._worker.zet_prop_async(pid, waarde, lambda _g: klaar.set())
                klaar.wait(timeout=2.0)

            return exposure.probeer_instellingen(
                lees_frame, zet, self._worker.lees_prop)
        except Exception as e:
            print(f"[BELICHTING] Instellingen uitproberen mislukt: {e}")
            return {"instelling": "none", "gevoeligheid": 0.0, "basis": 0.0}

    def zet_belichtingswaarde(self, instelling_naam, waarde):
        """Zet een belichtingsinstelling. Levert (gelukt, teruggelezen)."""
        try:
            import exposure
            pid = exposure._prop_id(instelling_naam)
            if pid is None or not self._worker:
                return False, None
            resultaat = {}
            klaar = threading.Event()

            def af(gelezen):
                resultaat["gelezen"] = gelezen
                klaar.set()

            self._worker.zet_prop_async(pid, waarde, af)
            if not klaar.wait(timeout=2.0):
                return False, None
            gelezen = resultaat.get("gelezen")
            if gelezen is None:
                return False, None
            # Alleen 'gelukt' als de driver de waarde ook echt overnam.
            return abs(float(gelezen) - float(waarde)) < 0.51, gelezen
        except Exception as e:
            print(f"[BELICHTING] Waarde zetten mislukt: {e}")
            return False, None

    def is_connected(self):
        return self._connected

    def start_live_view(self):
        """Start live view (worker thread emits frames)."""
        if self._worker and not self._live_running:
            self._worker.start()
            self._live_running = True
            print("[WEBCAM] Live view gestart")

    def stop_live_view(self):
        """Stop live view."""
        if self._worker and self._live_running:
            self._worker.stop()
            self._live_running = False
            print("[WEBCAM] Live view gestopt")

    def capture_photo(self, use_af=False):
        """Capture current frame as a JPEG photo.

        The photo is saved to the configured save directory.
        Emits photo_received signal on the worker.
        """
        if not self._last_frame:
            print("[WEBCAM] Geen frame beschikbaar voor capture")
            return

        # Determine save path
        save_dir = self._save_dir or os.path.join(
            os.path.expanduser("~"), "Documents", "Bootharoo", "photos"
        )
        os.makedirs(save_dir, exist_ok=True)

        self._photo_counter += 1
        filename = f"WEBCAM_{self._photo_counter:04d}.jpg"
        filepath = os.path.join(save_dir, filename)

        # Save JPEG bytes directly
        with open(filepath, 'wb') as f:
            f.write(self._last_frame)

        size_kb = len(self._last_frame) // 1024
        print(f"[WEBCAM] Foto opgeslagen: {filepath} ({size_kb}KB)")

        # Emit photo_received signal (same as EDSDK camera)
        if self._worker:
            # Use a delayed callback to simulate async download
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(100, lambda: self._emit_photo(filepath))

    def _emit_photo(self, filepath):
        """Emit photo received (called from main thread via QTimer)."""
        # The photobooth.py expects the camera to have a callback mechanism
        # We use the same pattern as EDSDK: camera.photo_received(path)
        if hasattr(self, '_photo_callback') and self._photo_callback:
            self._photo_callback(filepath)

    def set_photo_callback(self, callback):
        """Set callback for when a photo is captured."""
        self._photo_callback = callback

    def configure_save_folder(self):
        """Set save directory (called by photobooth)."""
        pass

    def set_session_folder(self, folder_path):
        """Set session-specific save folder."""
        self._save_dir = folder_path
        os.makedirs(folder_path, exist_ok=True)

    def get_camera_properties(self):
        """Return camera info dict."""
        return {"name": self._camera_name}

    def get_property(self, name):
        return ""

    def set_property(self, name, value):
        pass

    def list_property_values(self, name):
        return []

    def pre_focus(self, label=""):
        pass

    def cancel_focus(self):
        pass

    def pump_events(self):
        pass

    def disconnect(self):
        """Release webcam."""
        if self._worker:
            self._worker.stop()
            self._worker = None
        if self.cap:
            self.cap.release()
            self.cap = None
        self._connected = False
        self._live_running = False
        print("[WEBCAM] Verbinding verbroken")

    def shutdown(self):
        """Clean shutdown."""
        self.disconnect()

    @staticmethod
    def list_cameras_with_opencv_index():
        """List cameras with their correct OpenCV index.

        Returns list of (opencv_index, name, max_resolution) tuples.
        First gets names from WMI (fast), then maps to OpenCV indices
        by testing unique capabilities (resolution support).
        """
        if not _CV2_AVAILABLE:
            return []

        # Step 1: Get camera names from WMI (fast)
        wmi_names = []
        try:
            import subprocess
            result = subprocess.run(
                ['wmic', 'path', 'Win32_PnPEntity', 'where',
                 "PNPClass='Camera' or PNPClass='Image'",
                 'get', 'Name', '/value'],
                capture_output=True, text=True, timeout=3,
                creationflags=0x08000000
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    line = line.strip()
                    if line.startswith('Name='):
                        wmi_names.append(line[5:].strip())
        except Exception:
            pass

        # Step 2: Open each OpenCV index and fingerprint by max resolution
        opencv_cameras = []
        for i in range(5):
            try:
                cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
                if cap.isOpened():
                    # Test max resolution as fingerprint
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 3840)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 2160)
                    max_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    max_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    # Reset to default
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    opencv_cameras.append((i, max_w, max_h))
                    cap.release()
                else:
                    break
            except Exception:
                break

        # Step 3: Build result — use WMI name if available, else generic
        result = []
        for j, (cv_idx, max_w, max_h) in enumerate(opencv_cameras):
            if j < len(wmi_names):
                name = wmi_names[j]
            else:
                name = f"Camera {cv_idx}"
            result.append((cv_idx, name, f"{max_w}x{max_h}"))

        return result

    @staticmethod
    def list_cameras():
        """Enumerate available webcams with names and OpenCV indices.

        Uses pygrabber (DirectShow) for camera names — these indices
        match OpenCV's VideoCapture indices exactly.
        Falls back to generic names if pygrabber is not available.

        Returns list of (opencv_index, name) tuples.
        """
        # Use pygrabber on main thread only (COM requires STA)
        # In background threads, fall through to OpenCV scan
        import threading
        if threading.current_thread() is threading.main_thread():
            try:
                from pygrabber.dshow_graph import FilterGraph
                graph = FilterGraph()
                devices = graph.get_input_devices()
                cameras = [(i, name) for i, name in enumerate(devices)]
                if cameras:
                    print(f"[WEBCAM] {len(cameras)} camera('s) gevonden via DirectShow")
                    return cameras
            except ImportError:
                pass
            except Exception as e:
                print(f"[WEBCAM] DirectShow fout: {e}")

        # Fallback: OpenCV scan (no names, just indices)
        if not _CV2_AVAILABLE:
            return []

        cameras = []
        for i in range(5):
            try:
                cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
                if cap.isOpened():
                    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    cameras.append((i, f"Camera {i} ({w}x{h})"))
                    cap.release()
                else:
                    break
            except Exception:
                break

        if cameras:
            print(f"[WEBCAM] {len(cameras)} camera('s) gevonden via OpenCV")
        return cameras

    @staticmethod
    def list_resolutions(device_index=0):
        """Return standard webcam resolutions.

        No scanning — just offer common resolutions.
        The webcam will use the closest supported resolution automatically.
        """
        return [
            "640x480",
            "1280x720",
            "1920x1080",
            "2560x1440",
            "3840x2160",
        ]
