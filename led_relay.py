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

# HARDE veiligheidslimiet: de LED gaat NOOIT langer dan dit aan, ongeacht
# wat de aanroepende code doet. Bij elke on() start een watchdog-timer die
# na deze tijd geforceerd off() stuurt. Voorkomt dat de flits blijft hangen
# (en mensen verblindt) als de normale off() niet bereikt wordt — bv. als
# de gast tussen seconde 0 en 1 op het kruisje klikt.
#
# 1.5s i.p.v. exact 1.0s: bij een webcam valt de opname op ~1.0s ná het
# aangaan van de LED; een cap van precies 1s zou de eigen belichting
# afkappen. De state-sweep in photobooth.py grijpt bij een onderbreking
# al binnen ~0.3s in, dus in de praktijk staat de LED nooit lang aan.
_HARD_MAX_ON_SEC = 1.5


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
        self._is_on = False          # logische toestand (voor idempotente off)
        self._watchdog = None        # threading.Timer die geforceerd uitzet
        self._port_cfg = port        # "auto" of vaste poort (bv. "COM3")
        self._baudrate = baudrate

        if serial is None:
            print("[LED] pyserial niet geïnstalleerd — LED flash uitgeschakeld")
            return

        # Eerste verbindpoging. Mislukt 'ie (board nog niet ingeplugd), dan
        # blijft de photobooth gewoon draaien; de periodieke sweep +
        # opname-trigger in photobooth.py proberen het daarna opnieuw.
        if not self.ensure_connected():
            print("[LED] Geen relay-board bij start — wordt periodiek opnieuw geprobeerd")

    def ensure_connected(self) -> bool:
        """(Her)verbind met het relay-board als het nu niet beschikbaar is.

        Veilig om herhaald aan te roepen (periodieke sweep elke ~10s + vóór
        elke opname): opent alleen als er nog geen open verbinding is, en
        detecteert een verdwenen poort (unplug / COM-renummering) zodat een
        los of laat ingeplugd board alsnog wordt opgepakt.

        Returns True als er na afloop verbinding is.
        """
        if serial is None:
            return False
        with self._lock:
            if self._serial is not None and self._serial.is_open:
                # Al open — check of de poort fysiek nog bestaat.
                try:
                    devices = [p.device for p in serial.tools.list_ports.comports()]
                    if self._serial.port in devices:
                        return True
                    print(f"[LED] Poort {self._serial.port} verdwenen — herverbinden")
                except Exception:
                    return True  # kon niet checken → ga uit van OK
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None
            elif self._serial is not None:
                # Dood handle opruimen.
                try:
                    self._serial.close()
                except Exception:
                    pass
                self._serial = None

            # Bepaal de poort. Bij "auto" elke keer opnieuw detecteren zodat
            # een later ingeplugd board / COM-renummering wordt opgepakt.
            port = self._port_cfg
            if not port or port == "auto":
                port = autodetect_port()
                if port is None:
                    return False
            try:
                self._serial = serial.Serial(port, self._baudrate, timeout=0.5)
                self._is_on = False
                print(f"[LED] Relay verbonden op {port}")
                return True
            except Exception as e:
                print(f"[LED] Relay (her)verbinden mislukt op {port}: {e}")
                self._serial = None
                return False

    @property
    def available(self):
        return self._serial is not None and self._serial.is_open

    def on(self, max_on_sec=_HARD_MAX_ON_SEC):
        """Zet de LED aan en start een watchdog die hem na max_on_sec
        gegarandeerd weer uitzet — ook als off() nooit wordt aangeroepen."""
        if not self.available:
            return
        try:
            with self._lock:
                self._serial.write(_CMD_ON)
                self._is_on = True
                # (Her)start de watchdog. Cancel een eventuele vorige zodat
                # een nieuwe flits niet door de oude timer wordt afgekapt.
                if self._watchdog is not None:
                    self._watchdog.cancel()
                self._watchdog = threading.Timer(max_on_sec, self._watchdog_off)
                self._watchdog.daemon = True
                self._watchdog.start()
        except Exception as e:
            print(f"[LED] Fout bij aan-zetten: {e}")

    def off(self):
        if not self.available:
            return
        try:
            with self._lock:
                if self._watchdog is not None:
                    self._watchdog.cancel()
                    self._watchdog = None
                self._serial.write(_CMD_OFF)
                self._is_on = False
        except Exception as e:
            print(f"[LED] Fout bij uit-zetten: {e}")

    def ensure_off(self):
        """Idempotente off: stuurt alleen een uit-commando als de LED
        logisch nog aan staat. Bedoeld voor de periodieke state-sweep,
        zodat die niet elke tick onnodig over de seriële poort schrijft."""
        if self._is_on:
            self.off()

    def _watchdog_off(self):
        """Wordt vanuit de timer-thread aangeroepen als de LED te lang aan
        stond. Stuurt geforceerd uit."""
        try:
            with self._lock:
                self._watchdog = None
                if not (self._serial is not None and self._serial.is_open):
                    return
                if not self._is_on:
                    return
                self._serial.write(_CMD_OFF)
                self._is_on = False
            print("[LED] Watchdog: LED geforceerd uit na max-duur")
        except Exception as e:
            print(f"[LED] Watchdog-fout: {e}")

    def close(self):
        if self._serial is None:
            return
        try:
            if self._watchdog is not None:
                self._watchdog.cancel()
                self._watchdog = None
            self.off()
            self._serial.close()
        except Exception:
            pass
        self._serial = None
