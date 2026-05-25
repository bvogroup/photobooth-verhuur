"""LED flash relay control via USB-serial relay board (CH340).

Sends 4-byte aan/uit commando's naar een single-channel relay board op een
COM-poort. Fout-tolerant: als de relay niet aangesloten is, worden on()/off()
no-ops zodat de photobooth zonder LED gewoon blijft draaien.
"""
import threading

try:
    import serial
except ImportError:
    serial = None


_CMD_ON = b'\xA0\x01\x01\xA2'
_CMD_OFF = b'\xA0\x01\x00\xA1'


class LedRelay:
    def __init__(self, port, baudrate=9600):
        self._lock = threading.Lock()
        self._port = port
        self._serial = None
        if serial is None:
            print("[LED] pyserial niet geïnstalleerd — LED flash uitgeschakeld")
            return
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
