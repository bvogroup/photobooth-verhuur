"""LED flash relay control via USB-serial relay board (CH340).

Sends 4-byte aan/uit commando's naar een single-channel relay board op een
COM-poort. Fout-tolerant: als de relay niet aangesloten is, worden on()/off()
no-ops zodat de photobooth zonder LED gewoon blijft draaien.

Port-detectie: standaard wordt het CH340-board (VID 0x1A86 / PID 0x7523)
auto-gevonden zodat Windows COM-port-renummering geen probleem is. Een vaste
poort kan worden geforceerd via config (bv. "COM3").
"""
import threading

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    serial = None


_CMD_ON = b'\xA0\x01\x01\xA2'
_CMD_OFF = b'\xA0\x01\x00\xA1'

# CH340 USB-serial chip (vrijwel alle goedkope relay-boards)
_CH340_VID = 0x1A86
_CH340_PID = 0x7523


def autodetect_port():
    """Vind de eerste COM-poort die een CH340 USB-serial chip is.

    Returns: device-string ('COM6') of None als niet gevonden.
    """
    if serial is None:
        return None
    # Primair: match op USB VID/PID (meest betrouwbaar)
    for p in serial.tools.list_ports.comports():
        if p.vid == _CH340_VID and p.pid == _CH340_PID:
            return p.device
    # Fallback: match op beschrijving / fabrikant (oudere drivers)
    for p in serial.tools.list_ports.comports():
        haystack = ((p.description or '') + ' ' + (p.manufacturer or '')).upper()
        if 'CH340' in haystack or 'WCH.CN' in haystack:
            return p.device
    return None


class LedRelay:
    def __init__(self, port="auto", baudrate=9600):
        self._lock = threading.Lock()
        self._serial = None

        if serial is None:
            print("[LED] pyserial niet geïnstalleerd — LED flash uitgeschakeld")
            return

        if not port or port == "auto":
            detected = autodetect_port()
            if detected is None:
                print("[LED] Geen CH340 relay-board gevonden — flash uitgeschakeld")
                return
            port = detected
            print(f"[LED] Relay auto-gevonden op {port}")

        try:
            self._serial = serial.Serial(port, baudrate, timeout=0.5)
            print(f"[LED] Relay verbonden op {port}")
        except Exception as e:
            print(f"[LED] Relay niet beschikbaar op {port}: {e} — flash uitgeschakeld")
            self._serial = None

    @property
    def available(self):
        return self._serial is not None and self._serial.is_open

    def on(self):
        if not self.available:
            return
        try:
            with self._lock:
                self._serial.write(_CMD_ON)
        except Exception as e:
            print(f"[LED] Fout bij aan-zetten: {e}")

    def off(self):
        if not self.available:
            return
        try:
            with self._lock:
                self._serial.write(_CMD_OFF)
        except Exception as e:
            print(f"[LED] Fout bij uit-zetten: {e}")

    def close(self):
        if self._serial is None:
            return
        try:
            self.off()
            self._serial.close()
        except Exception:
            pass
        self._serial = None
