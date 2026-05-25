"""
Canon EDSDK Python wrapper using ctypes.

Provides direct camera control for Canon EOS cameras without digiCamControl.
Supports: connect, live view, autofocus, capture, download.

DLL files required in app directory:
  - EDSDK.dll
  - EdsImage.dll
"""

import os
import sys
import ctypes
from ctypes import (
    c_void_p, c_uint32, c_int32, c_uint64, c_int64, c_char, c_bool,
    POINTER, byref, Structure, CFUNCTYPE, WINFUNCTYPE
)

# ── Locate DLL ──────────────────────────────────────────────────────

_DLL_SEARCH_PATHS = [
    os.path.dirname(os.path.abspath(__file__)),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "edsdk"),
]
# When running as frozen EXE, bundled DLLs are in sys._MEIPASS
if getattr(sys, 'frozen', False):
    _DLL_SEARCH_PATHS.insert(0, sys._MEIPASS)

_edsdk = None

def _load_edsdk():
    global _edsdk
    if _edsdk is not None:
        return _edsdk
    for path in _DLL_SEARCH_PATHS:
        dll_path = os.path.join(path, "EDSDK.dll")
        if os.path.isfile(dll_path):
            # Add DLL directory so EdsImage.dll can be found
            os.add_dll_directory(path)
            _edsdk = ctypes.WinDLL(dll_path)
            print(f"[EDSDK] DLL geladen: {dll_path}")
            return _edsdk
    raise FileNotFoundError(
        "EDSDK.dll niet gevonden. Plaats EDSDK.dll en EdsImage.dll in de app-map."
    )


# ── Types ───────────────────────────────────────────────────────────

EdsBaseRef = c_void_p
EdsCameraListRef = c_void_p
EdsCameraRef = c_void_p
EdsStreamRef = c_void_p
EdsEvfImageRef = c_void_p
EdsDirectoryItemRef = c_void_p
EdsError = c_uint32
EdsPropertyID = c_uint32
EdsCameraCommand = c_uint32
EdsUInt32 = c_uint32
EdsInt32 = c_int32
EdsUInt64 = c_uint64
EdsBool = c_uint32

EDS_MAX_NAME = 256


class EdsDeviceInfo(Structure):
    _fields_ = [
        ("szPortName", c_char * EDS_MAX_NAME),
        ("szDeviceDescription", c_char * EDS_MAX_NAME),
        ("deviceSubType", c_uint32),
        ("reserved", c_uint32),
    ]


class EdsDirectoryItemInfo(Structure):
    _fields_ = [
        ("size", c_uint64),
        ("isFolder", c_uint32),
        ("groupID", c_uint32),
        ("option", c_uint32),
        ("szFileName", c_char * EDS_MAX_NAME),
        ("format", c_uint32),
        ("dateTime", c_uint32),
    ]


class EdsCapacity(Structure):
    _fields_ = [
        ("numberOfFreeClusters", c_int32),
        ("bytesPerSector", c_int32),
        ("reset", c_uint32),
    ]


class EdsPoint(Structure):
    _fields_ = [
        ("x", c_int32),
        ("y", c_int32),
    ]


class EdsSize(Structure):
    _fields_ = [
        ("width", c_int32),
        ("height", c_int32),
    ]


class EdsRect(Structure):
    _fields_ = [
        ("point", EdsPoint),
        ("size", EdsSize),
    ]


# ── Constants ───────────────────────────────────────────────────────

# Error codes
EDS_ERR_OK = 0

# Property IDs
kEdsPropID_ProductName = 0x00000002
kEdsPropID_SaveTo = 0x0000000b
kEdsPropID_Evf_OutputDevice = 0x00000500
kEdsPropID_Evf_Mode = 0x00000501
kEdsPropID_Evf_AFMode = 0x0000050E
kEdsPropID_Evf_Zoom = 0x00000507
kEdsPropID_Evf_ZoomPosition = 0x00000508
kEdsPropID_Evf_CoordinateSystem = 0x00000540
kEdsPropID_Evf_ZoomRect = 0x00000541
kEdsPropID_ImageQuality = 0x00000100
kEdsPropID_AEMode = 0x00000400
kEdsPropID_ISOSpeed = 0x00000402
kEdsPropID_Av = 0x00000405
kEdsPropID_Tv = 0x00000406
kEdsPropID_AvailableShots = 0x0000040a
kEdsPropID_BatteryLevel = 0x00000008
kEdsPropID_FlashOn = 0x00000412
kEdsPropID_FlashMode = 0x00000414
kEdsPropID_DC_Strobe = 0x00000601

# SaveTo values
kEdsSaveTo_Camera = 1
kEdsSaveTo_Host = 2
kEdsSaveTo_Both = 3

# EVF output device
kEdsEvfOutputDevice_TFT = 1
kEdsEvfOutputDevice_PC = 2
kEdsEvfOutputDevice_PC_Small = 8

# Camera commands
kEdsCameraCommand_TakePicture = 0x00000000
kEdsCameraCommand_PressShutterButton = 0x00000004
kEdsCameraCommand_DoEvfAf = 0x00000102
kEdsCameraCommand_ExtendShutDownTimer = 0x00000001

# Shutter button states
kEdsCameraCommand_ShutterButton_OFF = 0x00000000
kEdsCameraCommand_ShutterButton_Halfway = 0x00000001
kEdsCameraCommand_ShutterButton_Completely = 0x00000003
kEdsCameraCommand_ShutterButton_Halfway_NonAF = 0x00010001
kEdsCameraCommand_ShutterButton_Completely_NonAF = 0x00010003

# EvfAf
kEdsCameraCommand_EvfAf_OFF = 0
kEdsCameraCommand_EvfAf_ON = 1

# Object events
kEdsObjectEvent_All = 0x00000200
kEdsObjectEvent_DirItemCreated = 0x00000204
kEdsObjectEvent_DirItemRequestTransfer = 0x00000208

# State events
kEdsStateEvent_All = 0x00000300
kEdsStateEvent_Shutdown = 0x00000301
kEdsStateEvent_WillSoonShutDown = 0x00000303

# Property events
kEdsPropertyEvent_All = 0x00000100
kEdsPropertyEvent_PropertyChanged = 0x00000101

# File create disposition
kEdsFileCreateDisposition_CreateNew = 0
kEdsFileCreateDisposition_CreateAlways = 1
kEdsFileCreateDisposition_OpenExisting = 2
kEdsFileCreateDisposition_OpenAlways = 3

# Access
kEdsAccess_Read = 0
kEdsAccess_Write = 1
kEdsAccess_ReadWrite = 2


# ── Callback types ──────────────────────────────────────────────────

# EdsObjectEventHandler: EdsError (*)(EdsObjectEvent, EdsBaseRef, EdsVoid*)
EdsObjectEventHandler = WINFUNCTYPE(c_uint32, c_uint32, c_void_p, c_void_p)

# EdsStateEventHandler: EdsError (*)(EdsStateEvent, EdsUInt32, EdsVoid*)
EdsStateEventHandler = WINFUNCTYPE(c_uint32, c_uint32, c_uint32, c_void_p)

# EdsPropertyEventHandler: EdsError (*)(EdsPropertyEvent, EdsPropertyID, EdsUInt32, EdsVoid*)
EdsPropertyEventHandler = WINFUNCTYPE(c_uint32, c_uint32, c_uint32, c_uint32, c_void_p)


# ── API Functions ───────────────────────────────────────────────────

def _check(err, func_name=""):
    """Check EDSDK return code, raise on error."""
    if err != EDS_ERR_OK:
        raise EDSDKError(f"EDSDK fout in {func_name}: 0x{err:08X}")
    return err


class EDSDKError(Exception):
    pass


class EDSDK:
    """High-level wrapper around Canon EDSDK functions."""

    def __init__(self):
        self._dll = _load_edsdk()
        self._initialized = False
        self._camera = None
        self._camera_list = None
        # Keep references to prevent garbage collection of callbacks
        self._object_cb = None
        self._state_cb = None
        self._property_cb = None
        # External callback handlers
        self.on_object_event = None  # func(event, ref)
        self.on_state_event = None   # func(event, param)
        # Internal download handling
        self._download_dir = None      # Directory to save captured photos
        self._downloaded_file = None   # Path of last downloaded file
        self._download_event = None    # threading.Event to signal download complete

    def initialize(self):
        """Initialize the EDSDK. Must be called before any other function."""
        if self._initialized:
            return  # Already initialized
        err = self._dll.EdsInitializeSDK()
        _check(err, "EdsInitializeSDK")
        self._initialized = True
        print("[EDSDK] SDK geïnitialiseerd")

        # Register cleanup (only once)
        if not getattr(self, '_atexit_registered', False):
            import atexit
            atexit.register(self.terminate)
            self._atexit_registered = True

    def terminate(self):
        """Terminate the EDSDK and release all resources."""
        if self._camera:
            try:
                self.close_session()
            except Exception:
                pass
        if self._camera_list:
            self._dll.EdsRelease(self._camera_list)
            self._camera_list = None
        if self._initialized:
            self._dll.EdsTerminateSDK()
            self._initialized = False
            print("[EDSDK] SDK beëindigd")

    def get_camera_list(self):
        """Get list of connected cameras.

        Returns list of (index, name, port) tuples.
        """
        camera_list = c_void_p()
        err = self._dll.EdsGetCameraList(byref(camera_list))
        _check(err, "EdsGetCameraList")

        if self._camera_list:
            self._dll.EdsRelease(self._camera_list)
        self._camera_list = camera_list

        count = c_uint32()
        err = self._dll.EdsGetChildCount(camera_list, byref(count))
        _check(err, "EdsGetChildCount")

        cameras = []
        for i in range(count.value):
            cam_ref = c_void_p()
            err = self._dll.EdsGetChildAtIndex(camera_list, i, byref(cam_ref))
            if err != EDS_ERR_OK:
                continue
            info = EdsDeviceInfo()
            err = self._dll.EdsGetDeviceInfo(cam_ref, byref(info))
            if err == EDS_ERR_OK:
                name = info.szDeviceDescription.decode("utf-8", errors="replace")
                port = info.szPortName.decode("utf-8", errors="replace")
                cameras.append((i, name, port))
            self._dll.EdsRelease(cam_ref)

        print(f"[EDSDK] {len(cameras)} camera('s) gevonden")
        return cameras

    def open_session(self, camera_index=0):
        """Open a session with the camera at the given index."""
        if not self._camera_list:
            self.get_camera_list()

        cam_ref = c_void_p()
        err = self._dll.EdsGetChildAtIndex(self._camera_list, camera_index, byref(cam_ref))
        _check(err, "EdsGetChildAtIndex")

        err = self._dll.EdsOpenSession(cam_ref)
        if err == 0xC0:  # EDS_ERR_SESSION_ALREADY_OPEN
            print("[EDSDK] Sessie vastgelopen (0xC0), USB-reset recovery...")
            import time as _time
            import os as _os
            import subprocess as _sp

            # Release everything and terminate SDK completely
            try:
                self._dll.EdsCloseSession(cam_ref)
            except Exception:
                pass
            self._dll.EdsRelease(cam_ref)
            if self._camera_list:
                self._dll.EdsRelease(self._camera_list)
                self._camera_list = None
            self._dll.EdsTerminateSDK()

            # Kill other Python processes that may hold EDSDK.dll
            my_pid = _os.getpid()
            try:
                r = _sp.run(
                    ["powershell", "-NoProfile", "-Command",
                     "Get-Process python -ErrorAction SilentlyContinue | "
                     "Select-Object -ExpandProperty Id"],
                    capture_output=True, text=True, timeout=5
                )
                for line in r.stdout.strip().split("\n"):
                    line = line.strip()
                    if line.isdigit() and int(line) != my_pid:
                        pid = int(line)
                        print(f"[EDSDK] Kill stale python PID {pid}")
                        try:
                            _sp.run(["taskkill", "/F", "/PID", str(pid)],
                                    capture_output=True, timeout=3)
                        except Exception:
                            pass
            except Exception:
                pass

            # USB device reset via cfgmgr32 (no admin needed)
            # Find any Canon camera (VID 04A9) on any USB port
            try:
                _cfgmgr32 = ctypes.windll.cfgmgr32
                dev_inst = c_uint32()
                found_usb = False
                # Enumerate WPD devices to find Canon cameras dynamically
                r = _sp.run(
                    ["powershell", "-NoProfile", "-Command",
                     "pnputil /enum-devices /class WPD /connected"],
                    capture_output=True, text=True, timeout=5
                )
                for line in (r.stdout or "").split("\n"):
                    line = line.strip()
                    if line.startswith("Instance ID:"):
                        inst_id = line.split(":", 1)[1].strip()
                        if "VID_04A9" in inst_id:
                            ret = _cfgmgr32.CM_Locate_DevNodeW(
                                byref(dev_inst), inst_id, 0
                            )
                            if ret == 0:
                                parent_inst = c_uint32()
                                _cfgmgr32.CM_Get_Parent(
                                    byref(parent_inst), dev_inst.value, 0
                                )
                                _cfgmgr32.CM_Reenumerate_DevNode(
                                    parent_inst.value, 0
                                )
                                print(f"[EDSDK] USB hub reenumerate: {inst_id}")
                                found_usb = True
                                break
                if not found_usb:
                    print("[EDSDK] Geen Canon USB device gevonden voor reset")
            except Exception:
                pass

            _time.sleep(3)

            # Retry loop with SDK reinit
            for attempt in range(5):
                delay = 2 + attempt
                print(f"[EDSDK] Retry {attempt + 1}/5 (wacht {delay}s)...")
                _time.sleep(delay)

                self._dll.EdsInitializeSDK()
                camera_list = c_void_p()
                self._dll.EdsGetCameraList(byref(camera_list))
                self._camera_list = camera_list

                count = c_uint32()
                self._dll.EdsGetChildCount(camera_list, byref(count))
                if count.value == 0:
                    print("[EDSDK] Geen camera gevonden...")
                    self._dll.EdsTerminateSDK()
                    continue

                cam_ref = c_void_p()
                self._dll.EdsGetChildAtIndex(
                    self._camera_list, camera_index, byref(cam_ref)
                )
                err = self._dll.EdsOpenSession(cam_ref)
                if err == EDS_ERR_OK:
                    print("[EDSDK] Recovery gelukt!")
                    break
                print(f"[EDSDK] Nog steeds 0x{err:08X}")
                self._dll.EdsRelease(cam_ref)
                self._dll.EdsTerminateSDK()
                self._camera_list = None

        if err == 0xC0:
            raise EDSDKError(
                "Camera sessie vastgelopen (0xC0).\n"
                "Zet de camera UIT, wacht 5 seconden, en zet weer AAN.\n"
                "De USB-kabel mag erin blijven."
            )
        if err != EDS_ERR_OK:
            _check(err, "EdsOpenSession")

        self._camera = cam_ref

        # Register event handlers
        self._register_callbacks()

        # Wait for camera to be ready before configuring (EOS 1100D needs time)
        # Camera can be BUSY (0x81) for several seconds after session open
        import time as _t
        ready = False
        for i in range(30):  # Max ~15s wait
            try:
                self.pump_events()
            except Exception:
                pass
            # Test readiness by reading a simple property
            test = c_uint32()
            err2 = self._dll.EdsGetPropertyData(
                self._camera, kEdsPropID_SaveTo, 0,
                ctypes.sizeof(test), byref(test))
            if err2 == EDS_ERR_OK:
                print(f"[EDSDK] Camera klaar na {i*0.5:.1f}s")
                ready = True
                break
            if i % 5 == 4:
                print(f"[EDSDK] Camera nog bezig (0x{err2:08X}), wacht...")
            _t.sleep(0.5)
        if not ready:
            print("[EDSDK] WAARSCHUWING: Camera nog steeds bezig na 15s, ga toch door")

        # Set save to host (photos download to PC)
        self._set_save_to_host()

        # Get camera name
        name = self.get_product_name()
        print(f"[EDSDK] Sessie geopend: {name}")
        return name

    def close_session(self):
        """Close the camera session."""
        if self._camera:
            self._dll.EdsCloseSession(self._camera)
            self._dll.EdsRelease(self._camera)
            self._camera = None
            print("[EDSDK] Sessie gesloten")

    def _register_callbacks(self):
        """Register event handlers on the camera."""
        if not self._camera:
            return

        # Object event handler (photo captured, transfer request)
        # NOTE: ref is a raw ctypes pointer — must be used inside this callback,
        # not passed to Python code that might lose the pointer context.
        def _obj_handler(event, ref, context):
            if event == kEdsObjectEvent_DirItemRequestTransfer and ref:
                try:
                    self._internal_download(ref)
                except Exception as e:
                    print(f"[EDSDK] Download in callback mislukt: {e}")
                    if self._download_event:
                        self._download_event.set()
            if self.on_object_event:
                try:
                    self.on_object_event(event, None)
                except Exception as e:
                    print(f"[EDSDK] Object event fout: {e}")
            return EDS_ERR_OK

        self._object_cb = EdsObjectEventHandler(_obj_handler)
        self._dll.EdsSetObjectEventHandler(
            self._camera, kEdsObjectEvent_All, self._object_cb, None
        )

        # State event handler (shutdown, etc.)
        def _state_handler(event, param, context):
            if event == kEdsStateEvent_WillSoonShutDown:
                # Extend the timer so camera doesn't sleep
                try:
                    self.send_command(kEdsCameraCommand_ExtendShutDownTimer)
                    print("[EDSDK] Camera sleep timer verlengd")
                except Exception:
                    pass
            elif event == kEdsStateEvent_Shutdown:
                print("[EDSDK] Camera is uitgeschakeld!")
                self._camera = None
            if self.on_state_event:
                try:
                    self.on_state_event(event, param)
                except Exception as e:
                    print(f"[EDSDK] State event fout: {e}")
            return EDS_ERR_OK

        self._state_cb = EdsStateEventHandler(_state_handler)
        self._dll.EdsSetCameraStateEventHandler(
            self._camera, kEdsStateEvent_All, self._state_cb, None
        )

    def _set_save_to_host(self):
        """Configure camera to send photos to the PC (with BUSY retry)."""
        import time as _t

        # Retry loop for BUSY camera
        for attempt in range(5):
            save_to = c_uint32(kEdsSaveTo_Host)
            err = self._dll.EdsSetPropertyData(
                self._camera, kEdsPropID_SaveTo, 0,
                ctypes.sizeof(save_to), byref(save_to)
            )
            if err == EDS_ERR_OK:
                print("[EDSDK] SaveTo ingesteld: Host")
                break
            if err == 0x81:  # BUSY
                if attempt < 4:
                    try:
                        self.pump_events()
                    except Exception:
                        pass
                    _t.sleep(1.0)
                    continue
            # Try Both as fallback
            save_to = c_uint32(kEdsSaveTo_Both)
            err = self._dll.EdsSetPropertyData(
                self._camera, kEdsPropID_SaveTo, 0,
                ctypes.sizeof(save_to), byref(save_to)
            )
            if err == EDS_ERR_OK:
                print("[EDSDK] SaveTo ingesteld: Both")
                break
            if err == 0x81 and attempt < 4:
                try:
                    self.pump_events()
                except Exception:
                    pass
                _t.sleep(1.0)
                continue
            # Read current value
            current = c_uint32()
            r = self._dll.EdsGetPropertyData(
                self._camera, kEdsPropID_SaveTo, 0,
                ctypes.sizeof(current), byref(current)
            )
            if r == EDS_ERR_OK and current.value in (2, 3):
                print(f"[EDSDK] SaveTo staat al op Host/Both — OK")
                break
            if attempt == 4:
                print(f"[EDSDK] WAARSCHUWING: SaveTo niet instelbaar na 5 pogingen")

        # Set capacity (tell camera PC has space) — also retry on BUSY
        for attempt in range(5):
            capacity = EdsCapacity()
            capacity.numberOfFreeClusters = 0x7FFFFFFF
            capacity.bytesPerSector = 0x1000
            capacity.reset = 1
            err = self._dll.EdsSetCapacity(self._camera, capacity)
            if err == EDS_ERR_OK:
                print("[EDSDK] Capaciteit ingesteld")
                break
            if err == 0x81 and attempt < 4:
                try:
                    self.pump_events()
                except Exception:
                    pass
                _t.sleep(1.0)
            elif attempt == 4:
                print(f"[EDSDK] WAARSCHUWING: EdsSetCapacity mislukt (0x{err:08X})")

    def get_product_name(self):
        """Get the camera product name."""
        if not self._camera:
            return ""
        buf = ctypes.create_string_buffer(EDS_MAX_NAME)
        err = self._dll.EdsGetPropertyData(
            self._camera, kEdsPropID_ProductName, 0,
            EDS_MAX_NAME, buf
        )
        if err == EDS_ERR_OK:
            return buf.value.decode("utf-8", errors="replace")
        return ""

    def get_battery_level(self):
        """Get battery level (0-100 or 0xFFFFFFFF for AC)."""
        if not self._camera:
            return -1
        val = c_uint32()
        err = self._dll.EdsGetPropertyData(
            self._camera, kEdsPropID_BatteryLevel, 0,
            ctypes.sizeof(val), byref(val)
        )
        if err == EDS_ERR_OK:
            return val.value
        return -1

    def get_available_shots(self):
        """Get number of available shots remaining."""
        if not self._camera:
            return -1
        val = c_uint32()
        err = self._dll.EdsGetPropertyData(
            self._camera, kEdsPropID_AvailableShots, 0,
            ctypes.sizeof(val), byref(val)
        )
        if err == EDS_ERR_OK:
            return val.value
        return -1

    def _get_uint_property(self, prop_id):
        """Get a uint32 property value from the camera."""
        if not self._camera:
            return 0
        val = c_uint32()
        err = self._dll.EdsGetPropertyData(
            self._camera, prop_id, 0,
            ctypes.sizeof(val), byref(val)
        )
        return val.value if err == EDS_ERR_OK else 0

    def set_iso_auto(self):
        """Set ISO to Auto (0x00000000) for automatic exposure."""
        if not self._camera:
            return
        iso_auto = c_uint32(0)  # 0 = Auto ISO
        err = self._dll.EdsSetPropertyData(
            self._camera, kEdsPropID_ISOSpeed, 0,
            ctypes.sizeof(iso_auto), byref(iso_auto)
        )
        if err == EDS_ERR_OK:
            print("[EDSDK] ISO ingesteld op Auto")
        else:
            print(f"[EDSDK] ISO Auto niet instelbaar (0x{err:08X})")

    def configure_for_photobooth(self):
        """Configure camera with optimal settings for photobooth use.
        All settings are best-effort — failures are logged but never crash."""
        if not self._camera:
            return

        # 1. Don't change ISO — let user control it manually for consistent exposure
        # self.set_iso_auto()

        # 2. Read current AE mode (shooting mode)
        ae_mode = self._get_uint_property(kEdsPropID_AEMode)
        mode_names = {
            0: "P (Program)", 1: "Tv (Shutter Priority)",
            2: "Av (Aperture Priority)", 3: "M (Manual)",
            6: "A-DEP", 9: "Auto", 10: "Night Portrait",
            11: "Sports", 12: "Landscape", 13: "Close-up",
        }
        mode_name = mode_names.get(ae_mode, f"Unknown({ae_mode})")
        print(f"[EDSDK] Camera modus: {mode_name}")

        # 3. Set image quality — retry on BUSY
        import time as _t_cfg
        for _qa in range(3):
            try:
                quality = c_uint32(0x01130011)  # Large Normal JPEG
                err = self._dll.EdsSetPropertyData(
                    self._camera, kEdsPropID_ImageQuality, 0,
                    ctypes.sizeof(quality), byref(quality)
                )
                if err == EDS_ERR_OK:
                    print("[EDSDK] Beeldkwaliteit: Large Normal JPEG")
                    break
                elif err == 0x81:
                    _t_cfg.sleep(1.0)
                    try: self.pump_events()
                    except: pass
                else:
                    print(f"[EDSDK] Beeldkwaliteit niet instelbaar (0x{err:08X})")
                    break
            except Exception:
                break

        # 4. Set metering mode to evaluative (best-effort, ignore BUSY)
        for _ma in range(3):
            try:
                metering = c_uint32(3)  # 3 = Evaluative metering
                err = self._dll.EdsSetPropertyData(
                    self._camera, 0x00000403, 0,
                    ctypes.sizeof(metering), byref(metering)
                )
                if err == EDS_ERR_OK:
                    print("[EDSDK] Meetmodus: Evaluatief")
                    break
                elif err == 0x81:
                    _t_cfg.sleep(1.0)
                    try: self.pump_events()
                    except: pass
                else:
                    break
            except Exception:
                break

    def disable_flash(self):
        """Best-effort flash disable. Only works on compact cameras (DC_Strobe).
        For DSLRs: set mode dial to M (Manual) to prevent flash pop-up."""
        if not self._camera:
            return
        try:
            strobe_off = c_uint32(3)  # kEdsDcStrobeOff = 3
            err = self._dll.EdsSetPropertyData(
                self._camera, kEdsPropID_DC_Strobe, 0,
                ctypes.sizeof(strobe_off), byref(strobe_off)
            )
            if err == EDS_ERR_OK:
                print("[EDSDK] Flash uitgeschakeld (DC_Strobe=Off)")
        except Exception:
            pass

    # ── Live View ───────────────────────────────────────────────────

    def start_live_view(self):
        """Start live view (EVF to PC) with robust BUSY handling."""
        if not self._camera:
            raise EDSDKError("Geen camera verbonden")

        import time as _time

        for attempt in range(15):  # More retries for BUSY cameras
            # Pump events to process pending camera state
            try:
                self.pump_events()
            except Exception:
                pass

            # Enable EVF mode
            evf_mode = c_uint32(1)
            err1 = self._dll.EdsSetPropertyData(
                self._camera, kEdsPropID_Evf_Mode, 0,
                ctypes.sizeof(evf_mode), byref(evf_mode)
            )

            # Set output device to PC
            device = c_uint32(kEdsEvfOutputDevice_PC)
            err2 = self._dll.EdsSetPropertyData(
                self._camera, kEdsPropID_Evf_OutputDevice, 0,
                ctypes.sizeof(device), byref(device)
            )

            if err1 == EDS_ERR_OK and err2 == EDS_ERR_OK:
                break

            if attempt < 14:
                wait = 0.5 if attempt < 5 else 1.0
                if attempt % 3 == 0:
                    print(f"[EDSDK] Live view start retry {attempt + 1} "
                          f"(mode=0x{err1:02X}, device=0x{err2:02X})")
                _time.sleep(wait)

        self._evf_err_count = 0
        print("[EDSDK] Live view gestart")

    def stop_live_view(self):
        """Stop live view."""
        if not self._camera:
            return

        # Set output device back to TFT only
        device = c_uint32(kEdsEvfOutputDevice_TFT)
        self._dll.EdsSetPropertyData(
            self._camera, kEdsPropID_Evf_OutputDevice, 0,
            ctypes.sizeof(device), byref(device)
        )

        # Disable EVF mode
        evf_mode = c_uint32(0)
        self._dll.EdsSetPropertyData(
            self._camera, kEdsPropID_Evf_Mode, 0,
            ctypes.sizeof(evf_mode), byref(evf_mode)
        )

        print("[EDSDK] Live view gestopt")

    def get_live_view_frame(self):
        """Download one live view frame as JPEG bytes. Returns bytes or None."""
        if not self._camera:
            return None

        # Create memory stream
        stream = c_void_p()
        err = self._dll.EdsCreateMemoryStream(c_uint64(0), byref(stream))
        if err != EDS_ERR_OK:
            return None

        # Create EVF image ref
        evf_image = c_void_p()
        err = self._dll.EdsCreateEvfImageRef(stream, byref(evf_image))
        if err != EDS_ERR_OK:
            self._dll.EdsRelease(stream)
            return None

        # Download EVF image
        err = self._dll.EdsDownloadEvfImage(self._camera, evf_image)
        if err != EDS_ERR_OK:
            if not hasattr(self, '_evf_err_count'):
                self._evf_err_count = 0
            self._evf_err_count += 1
            if self._evf_err_count <= 3 or self._evf_err_count % 100 == 0:
                print(f"[EDSDK] EVF download fout: 0x{err:08X} (#{self._evf_err_count})", flush=True)
            self._dll.EdsRelease(evf_image)
            self._dll.EdsRelease(stream)
            return None

        # Get image data from stream
        data = None
        try:
            length = c_uint64()
            err = self._dll.EdsGetLength(stream, byref(length))
            if err == EDS_ERR_OK and length.value > 0:
                pointer = c_void_p()
                err = self._dll.EdsGetPointer(stream, byref(pointer))
                if err == EDS_ERR_OK and pointer.value:
                    data = ctypes.string_at(pointer.value, length.value)
        except Exception:
            data = None

        # Cleanup (always release)
        self._dll.EdsRelease(evf_image)
        self._dll.EdsRelease(stream)

        return data

    # ── Autofocus ───────────────────────────────────────────────────

    def do_autofocus(self, on=True):
        """Trigger EVF autofocus (live view AF)."""
        param = kEdsCameraCommand_EvfAf_ON if on else kEdsCameraCommand_EvfAf_OFF
        self.send_command(kEdsCameraCommand_DoEvfAf, param)

    def half_press_shutter(self):
        """Half-press shutter (trigger AF in non-live-view mode)."""
        self.send_command(
            kEdsCameraCommand_PressShutterButton,
            kEdsCameraCommand_ShutterButton_Halfway
        )

    def release_shutter_button(self):
        """Release the shutter button."""
        self.send_command(
            kEdsCameraCommand_PressShutterButton,
            kEdsCameraCommand_ShutterButton_OFF
        )

    # ── Capture ─────────────────────────────────────────────────────

    def take_picture(self):
        """Take a picture.

        The photo will be delivered via the object event handler.
        Uses TakePicture command which works reliably on all EOS cameras.
        """
        self.send_command(kEdsCameraCommand_TakePicture)

    def take_picture_no_af(self):
        """Take a picture WITHOUT autofocus.

        Uses PressShutterButton with NonAF to skip AF.
        Requires shutter release after capture.
        """
        import time as _t
        # Half-press without AF (meters exposure — needs time to settle)
        self.send_command(
            kEdsCameraCommand_PressShutterButton,
            kEdsCameraCommand_ShutterButton_Halfway_NonAF
        )
        _t.sleep(0.4)
        # Full press without AF (captures)
        self.send_command(
            kEdsCameraCommand_PressShutterButton,
            kEdsCameraCommand_ShutterButton_Completely_NonAF
        )
        _t.sleep(0.3)
        # Release shutter button
        self.send_command(
            kEdsCameraCommand_PressShutterButton,
            kEdsCameraCommand_ShutterButton_OFF
        )

    def take_picture_with_af(self):
        """Take a picture with autofocus (full cycle)."""
        self.send_command(kEdsCameraCommand_TakePicture)

    def send_command(self, command, param=0):
        """Send a command to the camera."""
        if not self._camera:
            raise EDSDKError("Geen camera verbonden")
        err = self._dll.EdsSendCommand(self._camera, command, param)
        _check(err, f"EdsSendCommand(0x{command:X})")

    # ── File Download ───────────────────────────────────────────────

    def download_file(self, dir_item_ref, save_path):
        """Download a file from the camera to disk.

        Args:
            dir_item_ref: EdsDirectoryItemRef from object event
            save_path: Full path to save the file

        Returns:
            save_path on success
        """
        # Get file info
        info = EdsDirectoryItemInfo()
        err = self._dll.EdsGetDirectoryItemInfo(dir_item_ref, byref(info))
        _check(err, "EdsGetDirectoryItemInfo")

        filename = info.szFileName.decode("utf-8", errors="replace")
        file_size = info.size

        # Determine save path
        if os.path.isdir(save_path):
            save_path = os.path.join(save_path, filename)

        # Create file stream
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        stream = c_void_p()
        err = self._dll.EdsCreateFileStream(
            save_path.encode("utf-8"),
            kEdsFileCreateDisposition_CreateAlways,
            kEdsAccess_ReadWrite,
            byref(stream)
        )
        _check(err, "EdsCreateFileStream")

        # Download
        err = self._dll.EdsDownload(dir_item_ref, c_uint64(file_size), stream)
        _check(err, "EdsDownload")

        # Signal download complete
        err = self._dll.EdsDownloadComplete(dir_item_ref)
        _check(err, "EdsDownloadComplete")

        # Release
        self._dll.EdsRelease(stream)

        print(f"[EDSDK] Bestand gedownload: {save_path} ({file_size} bytes)")
        return save_path

    def download_to_memory(self, dir_item_ref):
        """Download a file from camera to memory.

        Returns (filename, bytes_data).
        """
        info = EdsDirectoryItemInfo()
        err = self._dll.EdsGetDirectoryItemInfo(dir_item_ref, byref(info))
        _check(err, "EdsGetDirectoryItemInfo")

        filename = info.szFileName.decode("utf-8", errors="replace")
        file_size = info.size

        # Create memory stream
        stream = c_void_p()
        err = self._dll.EdsCreateMemoryStream(c_uint64(0), byref(stream))
        _check(err, "EdsCreateMemoryStream")

        # Download
        err = self._dll.EdsDownload(dir_item_ref, c_uint64(file_size), stream)
        _check(err, "EdsDownload")

        err = self._dll.EdsDownloadComplete(dir_item_ref)
        _check(err, "EdsDownloadComplete")

        # Read data
        pointer = c_void_p()
        length = c_uint64()
        self._dll.EdsGetPointer(stream, byref(pointer))
        self._dll.EdsGetLength(stream, byref(length))

        data = ctypes.string_at(pointer.value, length.value) if pointer.value else b""

        self._dll.EdsRelease(stream)

        return filename, data

    # ── Event processing ────────────────────────────────────────────

    def pump_events(self):
        """Process pending EDSDK events. Call regularly from main thread."""
        if self._initialized:
            self._dll.EdsGetEvent()

    def _internal_download(self, dir_item_ref):
        """Download file inside the event callback (where ctypes pointer is valid).

        Uses self._download_dir for save location.
        Sets self._downloaded_file and signals self._download_event when done.
        """
        save_dir = self._download_dir
        if not save_dir:
            print("[EDSDK] _internal_download: geen download directory ingesteld")
            return

        try:
            # Wrap raw integer from callback as c_void_p to avoid OverflowError
            ref = c_void_p(dir_item_ref)

            # Get file info
            info = EdsDirectoryItemInfo()
            err = self._dll.EdsGetDirectoryItemInfo(ref, byref(info))
            _check(err, "EdsGetDirectoryItemInfo")

            filename = info.szFileName.decode("utf-8", errors="replace")
            file_size = info.size

            save_path = os.path.join(save_dir, filename)
            os.makedirs(save_dir, exist_ok=True)

            # Create file stream
            stream = c_void_p()
            err = self._dll.EdsCreateFileStream(
                save_path.encode("utf-8"),
                kEdsFileCreateDisposition_CreateAlways,
                kEdsAccess_ReadWrite,
                byref(stream)
            )
            _check(err, "EdsCreateFileStream")

            # Download
            err = self._dll.EdsDownload(ref, c_uint64(file_size), stream)
            _check(err, "EdsDownload")

            # Signal download complete to camera
            err = self._dll.EdsDownloadComplete(ref)
            _check(err, "EdsDownloadComplete")

            # Release stream
            self._dll.EdsRelease(stream)

            self._downloaded_file = save_path
            print(f"[EDSDK] Intern download klaar: {save_path} ({file_size} bytes)")
        except Exception as e:
            print(f"[EDSDK] Intern download mislukt: {e}")
            self._downloaded_file = None
            # Try to cancel download to prevent camera from hanging
            try:
                ref = c_void_p(dir_item_ref)
                self._dll.EdsDownloadCancel(ref)
            except Exception:
                pass
        finally:
            if self._download_event:
                self._download_event.set()

    def _reset_usb_device(self):
        """Try to reset the Canon camera USB device via Windows API.

        Dynamically finds Canon cameras (VID 04A9) on any USB port.
        Uses cfgmgr32.dll to disable/re-enable the USB device.
        """
        try:
            import subprocess as _sp
            cfgmgr32 = ctypes.windll.cfgmgr32
            dev_inst = c_uint32()

            # Find Canon camera dynamically via pnputil
            canon_id = None
            try:
                r = _sp.run(
                    ["powershell", "-NoProfile", "-Command",
                     "pnputil /enum-devices /class WPD /connected"],
                    capture_output=True, text=True, timeout=5
                )
                for line in (r.stdout or "").split("\n"):
                    line = line.strip()
                    if line.startswith("Instance ID:"):
                        inst_id = line.split(":", 1)[1].strip()
                        if "VID_04A9" in inst_id:
                            canon_id = inst_id
                            break
            except Exception:
                pass

            if not canon_id:
                print("[EDSDK] USB-reset: Geen Canon camera gevonden")
                return

            result = cfgmgr32.CM_Locate_DevNodeW(
                byref(dev_inst), canon_id, 0
            )
            if result != 0:
                print(f"[EDSDK] USB-reset: CM_Locate_DevNodeW = {result}")
                return

            # Disable device
            result = cfgmgr32.CM_Disable_DevNode(dev_inst, 0)
            if result == 0:
                print("[EDSDK] USB device uitgeschakeld")
            else:
                print(f"[EDSDK] USB disable: code {result} (mogelijk geen admin-rechten)")

            import time as _time
            _time.sleep(2)

            # Re-enable device
            result = cfgmgr32.CM_Enable_DevNode(dev_inst, 0)
            if result == 0:
                print("[EDSDK] USB device weer ingeschakeld")
            else:
                print(f"[EDSDK] USB enable: code {result}")

        except Exception as e:
            print(f"[EDSDK] USB-reset mislukt: {e}")

    def extend_shutdown_timer(self):
        """Prevent camera from going to sleep."""
        if self._camera:
            try:
                self.send_command(kEdsCameraCommand_ExtendShutDownTimer)
            except Exception:
                pass
