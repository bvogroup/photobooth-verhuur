"""
DNP QW410 status-module — leest fout-codes en telemetrie via libusb.

Protocol gebaseerd op Gutenprint dnpds40 backend (reverse-engineered,
open-source). Print blijft via Windows-driver / printer.py — deze module
draait er parallel naast voor read-only status queries.

Vereist één-malige setup: installeer libusb-win32 (filter mode) via
Zadig, target de QW410 (VID=0x1452 PID=0x9201). Zonder die filter-driver
faalt de claim en geeft de module DNPStatus(level='unknown') terug —
de rest van de app blijft werken.

Protocol-frame (32 bytes, header):
  byte  0:    0x1B (ESC)
  byte  1:    0x50 ('P')
  bytes 2-7:  command   (6 bytes, space-padded)
  bytes 8-23: argument  (16 bytes, space-padded)
  bytes 24-31: payload-len (8 bytes, decimal-ASCII)

Response uitlezen:
  - 8 bytes: decimal-ASCII lengte van payload N
  - N bytes: payload data
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# Lazy imports — laat de hele module overleven zonder pyusb/libusb
_USB_BACKEND = None
_USB_BACKEND_ERR = ""

# libusb0 DLL (van libusb-win32 filter — Premium status pad).
# Eerst nieuwste install-locaties proberen, dan ingebouwde fallback uit
# de libusb pip-package (libusb-1.0 generic — werkt alleen zonder filter).
_LIBUSB0_DLL_CANDIDATES = [
    r"C:\Program Files\LibUSB-Win32\bin\amd64\libusb0.dll",
    r"C:\Program Files (x86)\LibUSB-Win32\bin\amd64\libusb0.dll",
    r"C:\temp\libusb-bin-1.4.0.2\libusb-win32-bin-1.4.0.2\bin\amd64\libusb0.dll",
]

try:
    import usb.core
    import usb.util
    import usb.backend.libusb0
    import usb.backend.libusb1

    # ── Pad 1: libusb0 (libusb-win32 filter) ── geeft volledige status
    _libusb0_dll = next((p for p in _LIBUSB0_DLL_CANDIDATES if os.path.isfile(p)), None)
    if _libusb0_dll:
        try:
            _USB_BACKEND = usb.backend.libusb0.get_backend(
                find_library=lambda x: _libusb0_dll
            )
            if _USB_BACKEND is not None:
                _USB_BACKEND_NAME = "libusb0"
        except Exception as e:
            _USB_BACKEND_ERR = f"libusb0 backend faal: {e}"

    # ── Pad 2: libusb-1.0 (generic) ── alleen voor USB-enumeratie
    # zonder claim. Filter niet vereist maar geen detail-status mogelijk.
    if _USB_BACKEND is None:
        try:
            import libusb as _libusb_pkg
            _dll_dir = os.path.join(
                os.path.dirname(_libusb_pkg.__file__),
                "_platform", "windows", "x86_64",
            )
            if os.path.isdir(_dll_dir):
                try:
                    os.add_dll_directory(_dll_dir)
                except Exception:
                    pass
                _USB_BACKEND = usb.backend.libusb1.get_backend(
                    find_library=lambda x: os.path.join(_dll_dir, "libusb-1.0.dll")
                )
                if _USB_BACKEND is not None:
                    _USB_BACKEND_NAME = "libusb1"
        except Exception as e:
            _USB_BACKEND_ERR = f"libusb1 fallback faal: {e}"

    if _USB_BACKEND is None and not _USB_BACKEND_ERR:
        _USB_BACKEND_ERR = "geen libusb0.dll of libusb-1.0.dll gevonden"
except Exception as e:
    _USB_BACKEND_ERR = f"pyusb/libusb import faal: {e}"
    _USB_BACKEND_NAME = "none"
else:
    if "_USB_BACKEND_NAME" not in dir():
        _USB_BACKEND_NAME = "none"


# DNP QW410 USB identifiers (van Gutenprint dnpds40_backend.c)
DNP_VENDOR_ID = 0x1452
QW410_PRODUCT_IDS = (0x9201,)  # andere DS40-family PIDs zouden hier kunnen


# ── Status-codes uit Gutenprint dnpds40_print.c ─────────────────────
STATUS_CODES = {
    0:    ("Klaar",                 "ok"),
    1:    ("Bezig",                 "info"),
    500:  ("Bezig met printen",     "info"),
    510:  ("Bezig — buffer vol",    "info"),
    900:  ("Onderhouden",           "info"),
    1000: ("Klep open",             "error"),
    1010: ("Geen opvangbak",        "error"),
    1100: ("Papier op",             "error"),
    1200: ("Lint op",               "error"),
    1300: ("Papier vast",           "error"),
    1400: ("Lint-fout",             "error"),
    1500: ("Papier-definitie fout", "error"),
    1600: ("Data-fout",             "error"),
    2000: ("Kop-voltage fout",      "error"),
    2100: ("Kop-positie fout",      "error"),
    2200: ("Voeding-fout",          "error"),
    2300: ("Cutter-fout",           "error"),
    2400: ("Pinch-roller fout",     "error"),
    2500: ("Kop te heet",           "warning"),
    2600: ("Motor te heet",         "warning"),
    2610: ("Papier-stuck",          "error"),
    2700: ("Ribbon-tension fout",   "error"),
    2800: ("RFID lees-fout",        "error"),
    3000: ("Systeem-fout",          "error"),
    3010: ("Plate-fout",            "error"),
    9999: ("Communicatie-fout",     "error"),
}


# ── Media-codes (uit INFO MEDIA response) ────────────────────────────
MEDIA_CODES = {
    "4x6":   "4×6 inch (DNP standaard)",
    "5x7":   "5×7 inch",
    "6x8":   "6×8 inch",
    "6x9":   "6×9 inch",
    "8x10":  "8×10 inch",
    "8x12":  "8×12 inch",
    "4x8":   "4×8 inch (QW410)",
    "4x4":   "4×4 inch (QW410)",
    "4x4.5": "4×4.5 inch (QW410)",
    "4x4_5": "4×4.5 inch (QW410)",
    "4.5x4": "4.5×4 inch (QW410)",
    "4_5x4": "4.5×4 inch (QW410)",
    "4.5x4.5": "4.5×4.5 inch (QW410)",
    "4_5x4_5": "4.5×4.5 inch (QW410)",
    "4.5x6": "4.5×6 inch (QW410)",
    "4_5x6": "4.5×6 inch (QW410)",
    "4.5x8": "4.5×8 inch (QW410)",
    "4_5x8": "4.5×8 inch (QW410)",
}


class StatusLevel(Enum):
    OK = "ok"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    UNKNOWN = "unknown"  # filter niet geinstalleerd / device weg / etc.


@dataclass
class DNPStatus:
    """Volledige snapshot van de QW410-status."""
    level: StatusLevel = StatusLevel.UNKNOWN
    code: Optional[int] = None
    label: str = ""
    detail: str = ""              # vrije tekst, bv. "Sluit de klep en wacht 10s"
    media: str = ""               # "4×6 inch (DNP standaard)" etc.
    media_code: str = ""          # rauwe code zoals uit printer komt
    life_counter: Optional[int] = None
    serial: str = ""
    firmware: str = ""
    error_method: str = ""        # debug: hoe is dit gemeten
    connected: bool = False       # USB-aanwezig?
    timestamp: float = field(default_factory=time.time)

    def is_blocking(self) -> bool:
        """True als deze status het printen blokkeert."""
        return self.level == StatusLevel.ERROR or not self.connected


# ── USB-laag ────────────────────────────────────────────────────────

def _build_cmd(arg1: bytes, arg2: bytes = b"", payload: bytes = b"") -> bytes:
    """Bouw een 32-byte DNP-commando-frame + payload."""
    hdr = bytearray(b" " * 32)
    hdr[0] = 0x1B
    hdr[1] = 0x50
    hdr[2:2+min(len(arg1), 6)] = arg1[:6]
    hdr[8:8+min(len(arg2), 16)] = arg2[:16]
    plen = f"{len(payload):08d}".encode("ascii")
    hdr[24:32] = plen
    return bytes(hdr) + payload


def _find_device():
    """Zoek de QW410. Returnt (device, error_str)."""
    if _USB_BACKEND is None:
        return None, f"USB-backend niet beschikbaar: {_USB_BACKEND_ERR}"
    try:
        for dev in usb.core.find(find_all=True, backend=_USB_BACKEND):
            if dev.idVendor == DNP_VENDOR_ID and dev.idProduct in QW410_PRODUCT_IDS:
                return dev, ""
    except Exception as e:
        return None, f"USB-enumeratie fout: {e}"
    return None, "QW410 niet aangesloten"


def _claim_interface(dev):
    """Open device + claim interface. Returnt (in_ep, out_ep, intf_num, error).

    Voor libusb-win32 filter: set_configuration() is verplicht vóór claim.
    Voor libusb-1.0 zonder filter: claim faalt (DNP-driver houdt device),
    we returnen graceful met error-string.
    """
    # set_configuration verplicht voor libusb0; faalt veilig bij libusb1
    try:
        dev.set_configuration()
    except Exception:
        pass
    try:
        cfg = dev.get_active_configuration()
    except Exception as e:
        return None, None, None, (
            f"Geen libusb-toegang: {e} "
            f"(libusb-win32 filter niet geïnstalleerd of niet actief?)"
        )

    # Zoek bulk IN + bulk OUT endpoints op interface 0 (printer-class)
    in_ep = None
    out_ep = None
    intf_num = None
    for intf in cfg:
        try:
            usb.util.claim_interface(dev, intf.bInterfaceNumber)
        except Exception as e:
            return None, None, None, f"claim_interface({intf.bInterfaceNumber}) fout: {e}"
        intf_num = intf.bInterfaceNumber
        for ep in intf:
            ep_type = ep.bmAttributes & 0x03
            if ep_type != 0x02:  # BULK
                continue
            if ep.bEndpointAddress & 0x80:
                in_ep = ep
            else:
                out_ep = ep
        if in_ep and out_ep:
            break
    if not (in_ep and out_ep):
        return None, None, intf_num, "Geen bulk IN/OUT endpoints gevonden"
    return in_ep, out_ep, intf_num, ""


def _send_cmd_get_response(dev, in_ep, out_ep, cmd_bytes, timeout_ms=2000):
    """Stuur een DNP-commando + lees response. Returnt (response_bytes, error)."""
    try:
        out_ep.write(cmd_bytes, timeout=timeout_ms)
    except Exception as e:
        return None, f"write fout: {e}"
    # Eerst 8-byte length header
    try:
        len_buf = in_ep.read(8, timeout=timeout_ms)
    except Exception as e:
        return None, f"read len fout: {e}"
    try:
        length = int(bytes(len_buf).decode("ascii").strip())
    except Exception as e:
        return None, f"len parse fout: {e} (raw={bytes(len_buf)!r})"
    if length <= 0:
        return b"", ""
    # Dan de data
    try:
        data = in_ep.read(length, timeout=timeout_ms)
    except Exception as e:
        return None, f"read data fout: {e}"
    return bytes(data), ""


# ── Hoog-niveau API ─────────────────────────────────────────────────

def read_qw410_status(detailed: bool = True, timeout_ms: int = 2000) -> DNPStatus:
    """Lees de QW410-status. Returnt altijd een DNPStatus-object — nooit raise.

    Bij elke faal-modus krijg je een sensible default zodat de UI iets
    kan tonen:
      - geen pyusb/libusb beschikbaar      → level=UNKNOWN, error_method="no_backend"
      - QW410 niet aangesloten             → level=ERROR,   connected=False
      - filter-driver niet geïnstalleerd   → level=UNKNOWN, error_method="claim_failed"
      - timeout                            → level=UNKNOWN, error_method="timeout"
      - succes                             → level=ok/warning/error obv code, connected=True
    """
    status = DNPStatus()

    dev, err = _find_device()
    if dev is None:
        if "niet aangesloten" in err:
            status.level = StatusLevel.ERROR
            status.connected = False
            status.label = "Printer niet aangesloten"
            status.detail = "Controleer USB-kabel en stroom"
            status.error_method = "not_found"
        else:
            status.level = StatusLevel.UNKNOWN
            status.error_method = "no_backend"
            status.detail = err
        return status

    status.connected = True

    in_ep, out_ep, intf_num, err = _claim_interface(dev)
    if in_ep is None:
        status.level = StatusLevel.UNKNOWN
        status.error_method = "claim_failed"
        status.detail = f"Filter-driver niet geïnstalleerd of niet actief? ({err})"
        try:
            usb.util.dispose_resources(dev)
        except Exception:
            pass
        return status

    try:
        # 1. STATUS query — pure numeriek antwoord (bv "01200")
        resp, err = _send_cmd_get_response(
            dev, in_ep, out_ep,
            _build_cmd(b"STATUS"),
            timeout_ms=timeout_ms,
        )
        if resp is None:
            status.level = StatusLevel.UNKNOWN
            status.error_method = "timeout"
            status.detail = err
            return status

        code = _parse_int(resp)
        status.code = code
        if code in STATUS_CODES:
            label, level_str = STATUS_CODES[code]
            status.label = label
            status.level = StatusLevel(level_str)
        else:
            status.label = f"Onbekende code {code}"
            status.level = StatusLevel.WARNING
        status.error_method = f"libusb ({_USB_BACKEND_NAME})"

        if detailed:
            # 2. INFO MEDIA — antwoord "MT00000" → mediatype 0
            resp, _err = _send_cmd_get_response(
                dev, in_ep, out_ep,
                _build_cmd(b"INFO", b"MEDIA"),
                timeout_ms=timeout_ms,
            )
            if resp:
                media_raw = _strip_prefix(resp, b"MT")
                status.media_code = media_raw
                status.media = _decode_media(media_raw)

            # 3. MNT_RD COUNTER_LIFE — antwoord "CL0000038"
            resp, _err = _send_cmd_get_response(
                dev, in_ep, out_ep,
                _build_cmd(b"MNT_RD", b"COUNTER_LIFE"),
                timeout_ms=timeout_ms,
            )
            if resp:
                cnt_str = _strip_prefix(resp, b"CL")
                try:
                    status.life_counter = int(cnt_str.strip())
                except Exception:
                    pass

            # 4. INFO SERIAL_NUMBER — antwoord "QW4C45020823" gevolgd door \r + padding
            resp, _err = _send_cmd_get_response(
                dev, in_ep, out_ep,
                _build_cmd(b"INFO", b"SERIAL_NUMBER"),
                timeout_ms=timeout_ms,
            )
            if resp:
                status.serial = _clean_response(resp)

            # 5. INFO FW_VER — niet door alle modellen ondersteund (QW410 lijkt te timeouten)
            try:
                resp, _err = _send_cmd_get_response(
                    dev, in_ep, out_ep,
                    _build_cmd(b"INFO", b"FW_VER"),
                    timeout_ms=max(timeout_ms // 2, 500),
                )
                if resp:
                    status.firmware = _clean_response(resp)
            except Exception:
                pass  # FW_VER unsupported = OK

        return status
    finally:
        # Altijd interface release + dispose, anders kan de DNP-driver er
        # niet meer bij voor de volgende print
        try:
            if intf_num is not None:
                usb.util.release_interface(dev, intf_num)
        except Exception:
            pass
        try:
            usb.util.dispose_resources(dev)
        except Exception:
            pass


# ── Response-parsers (DNP-specifieke prefixes) ──────────────────────

def _clean_response(b: bytes) -> str:
    """Strip \\r, null-bytes, en trailing whitespace."""
    try:
        s = b.decode("ascii", errors="replace")
    except Exception:
        return ""
    # Knip op \r en \x00, dan strippen
    for sep in ("\r", "\x00"):
        idx = s.find(sep)
        if idx >= 0:
            s = s[:idx]
    return s.strip()


def _strip_prefix(b: bytes, prefix: bytes) -> str:
    """Strip de DNP-respons prefix (bv "MT" of "CL") en clean."""
    if b.startswith(prefix):
        b = b[len(prefix):]
    return _clean_response(b)


def _parse_int(b: bytes) -> int:
    """Parse een numerieke DNP-respons. Return -1 bij faal."""
    try:
        return int(_clean_response(b))
    except Exception:
        return -1


def _decode_media(code: str) -> str:
    """Decode DNP media-code naar leesbare omschrijving."""
    # Strip leading zeros (response is meestal zero-padded "00000")
    try:
        code = str(int(code)) if code.strip() else "0"
    except ValueError:
        pass
    # Tabel uit Gutenprint dnpds40_print.c voor DS40/DS620/QW410-familie
    table = {
        "0": "Geen media geladen",
        "1": "5×3.5\" (DS40)",
        "2": "6×4\"",
        "3": "5×7\"",
        "4": "6×8\"",
        "5": "6×9\"",
        "100": "4×4\" (QW410)",
        "101": "4×4.5\" (QW410)",
        "102": "4×6\" (QW410)",
        "103": "4×8\" (QW410)",
        "104": "4.5×4\" (QW410)",
        "105": "4.5×4.5\" (QW410)",
        "106": "4.5×6\" (QW410)",
        "107": "4.5×8\" (QW410)",
    }
    return table.get(code, f"Media-code {code}")


# ── Thread-safe poller voor UI-integratie ────────────────────────────

class StatusPoller:
    """Background-poller met thread-safe access tot de laatste DNPStatus.

    Niet automatisch gestart — caller doet .start() / .stop().
    Read laatste status via .get(); registreer callback met .on_change().
    """
    def __init__(self, interval_sec: float = 5.0):
        self._interval = interval_sec
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._status: Optional[DNPStatus] = None
        self._callbacks: list = []
        self._paused = False

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3.0)

    def pause(self, paused: bool):
        """Tijdelijk pauzeren — bv. tijdens print zelf (anders kunnen we
        de USB-toegang verstoren)."""
        self._paused = paused

    def get(self) -> Optional[DNPStatus]:
        with self._lock:
            return self._status

    def on_change(self, callback):
        """Callback ontvangt nieuwe DNPStatus bij elke verandering van level/code."""
        self._callbacks.append(callback)

    def _loop(self):
        while not self._stop_event.is_set():
            if not self._paused:
                try:
                    new_status = read_qw410_status(detailed=True, timeout_ms=2000)
                except Exception as e:
                    new_status = DNPStatus(
                        level=StatusLevel.UNKNOWN,
                        error_method="poller_exception",
                        detail=str(e),
                    )
                with self._lock:
                    changed = (
                        self._status is None
                        or self._status.level != new_status.level
                        or self._status.code != new_status.code
                    )
                    self._status = new_status
                if changed:
                    for cb in list(self._callbacks):
                        try:
                            cb(new_status)
                        except Exception as e:
                            print(f"[DNP-STATUS] Callback-fout (niet kritiek): {e}")
            self._stop_event.wait(self._interval)


# ── CLI test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    print("=" * 60)
    print("  DNP QW410 status-test (libusb)")
    print("=" * 60)
    if _USB_BACKEND is None:
        print(f"⚠️  USB-backend niet beschikbaar: {_USB_BACKEND_ERR}")
    else:
        print(f"✓ libusb-backend geladen")
    print()

    status = read_qw410_status(detailed=True)
    print(f"Level:        {status.level.value}")
    print(f"Code:         {status.code}")
    print(f"Label:        {status.label}")
    print(f"Detail:       {status.detail}")
    print(f"Connected:    {status.connected}")
    print(f"Media:        {status.media} (raw: {status.media_code!r})")
    print(f"Life counter: {status.life_counter}")
    print(f"Serial:       {status.serial!r}")
    print(f"Firmware:     {status.firmware!r}")
    print(f"Method:       {status.error_method}")
    print(f"Blocking:     {status.is_blocking()}")
