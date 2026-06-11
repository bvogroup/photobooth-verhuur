"""
DNP QW410 status-module — leest live status van de printer.

Hoofdpad: **UI Automation** scrape van de DNP "Voorkeursinstellingen → Printer
Info" dialog (DPQW410UI.DLL). De DNP-driver doet zelf intern de USB-I/O voor
deze dialog; wij openen het off-screen, lezen de waardes, sluiten het.

Voordelen t.o.v. libusb-win32 filter (geprobeerd, faalde):
  - GEEN filter-driver nodig (printen blijft 100% werkend)
  - GEEN eenmalige setup per PC (uiautomation + pywin32 zijn pip-installs)
  - ALLE 13+ DNP statuscodes + counter + serial + firmware + media

Fallback: libusb-1.0 USB-device-enumeratie (alleen plug/unplug detectie)
voor wanneer uiautomation niet beschikbaar is.

Geverifieerd op echte hardware (QW410 SN QW4C45020823, juni 2026).
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ── Status-codes uit Gutenprint dnpds40_print.c ─────────────────────
# (gemapt op de UI-strings die DPQW410UI.DLL toont)
STATUS_CODES = {
    0:    ("Klaar",                 "ok"),
    1:    ("Bezig",                 "info"),
    500:  ("Bezig met printen",     "info"),
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
    2300: ("Cutter-fout",           "error"),
    2500: ("Kop te heet",           "warning"),
    3000: ("Systeem-fout",          "error"),
    9999: ("Communicatie-fout",     "error"),
}

# Map de Engelse UI-string die de DNP-driver toont naar de DNP-foutcode.
# Vastgesteld empirisch op echte hardware. Lowercased match.
UI_STATUS_TO_CODE = {
    "waiting":             0,    # OK / klaar
    "ready":               0,
    "printing":            500,
    "paused":              1,
    "top door open":       1000,
    "no scrap box":        1010,
    "paper end":           1100,
    "ribbon end":          1200,
    "paper jam":           1300,
    "ribbon error":        1400,
    "paper error":         1500,
    "media error":         1500,
    "data error":          1600,
    "head voltage error":  2000,
    "head position error": 2100,
    "cutter error":        2300,
    "head over heat":      2500,
    "head temperature":    2500,
    "system error":        3000,
    "communication error": 9999,
}


class StatusLevel(Enum):
    OK = "ok"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass
class DNPStatus:
    """Snapshot van de QW410-status."""
    level: StatusLevel = StatusLevel.UNKNOWN
    code: Optional[int] = None
    label: str = ""
    detail: str = ""
    media: str = ""
    media_code: str = ""           # rauwe code
    prints_remaining: Optional[int] = None
    prints_total: Optional[int] = None
    life_counter: Optional[int] = None
    serial: str = ""
    firmware: str = ""
    color_profile_300dpi: str = ""
    color_profile_low_speed: str = ""
    error_method: str = ""
    connected: bool = False
    timestamp: float = field(default_factory=time.time)

    def is_blocking(self) -> bool:
        return self.level == StatusLevel.ERROR or not self.connected


# ── UI Automation pad (primair) ──────────────────────────────────────

class _PersistentDialog:
    """Houdt de DPQW410UI Voorkeursinstellingen dialog continu open
    (off-screen), zodat elke status-poll alleen Update klik + scrape
    hoeft te doen (~1 sec) ipv volledige open-sluit cyclus (~3.6 sec).

    Thread-safe via interne lock. Auto-recovers als dialog gesloten wordt.
    """
    def __init__(self, printer_name: str):
        self.printer_name = printer_name
        # RLock: read() roept bij fouten close() aan terwijl de lock al
        # vastgehouden wordt — met een gewone Lock is dat een deadlock
        # die de poller-thread (mét lock) voor eeuwig laat hangen.
        self._lock = threading.RLock()
        self._proc: Optional[subprocess.Popen] = None
        self._dlg = None
        self._update_btn = None
        self._tab = None
        self._tab_selector = None
        self._update_invoker = None
        self._fail_count = 0

    def _ensure_open(self) -> bool:
        """Garandeert dat dialog open + Printer Info tab actief is.
        Returnt True bij succes."""
        # Check of bestaande dialog nog leeft
        if self._dlg is not None:
            try:
                if self._dlg.Exists(0.2):
                    return True
            except Exception:
                pass
            self._dlg = None
            self._update_invoker = None

        # Spawn nieuwe dialog
        try:
            import uiautomation as auto
        except ImportError:
            return False

        try:
            # Ruim eerst een eventueel oud (zombie) rundll32-proces op —
            # anders lekt elke mislukte poging een proces + dialog die
            # de window-match kan vervuilen.
            if self._proc is not None:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=1.0)
                except Exception:
                    pass
                self._proc = None
            # Subprocess startupinfo: hide eventueel zichtbaar window
            try:
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = 0  # SW_HIDE
            except Exception:
                si = None
            self._proc = subprocess.Popen(
                ["rundll32", "printui.dll,PrintUIEntry", "/e", "/n", self.printer_name],
                startupinfo=si,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            # Wacht op dialog (max 6 sec). Match in twee passes: eerst
            # exact (titel eindigt op de printernaam), dan pas substring —
            # zo pakt "DP-QW410" niet per ongeluk de dialog van
            # "DP-QW410 (Kopie 2)" als beide queues bestaan.
            def _is_candidate(w):
                try:
                    name = w.Name or ""
                    return (self.printer_name in name
                            and w.ControlTypeName == "WindowControl"
                            and "Settings" not in name
                            and "Instellingen" not in name)
                except Exception:
                    return False

            deadline = time.monotonic() + 6
            while time.monotonic() < deadline:
                candidates = [w for w in auto.GetRootControl().GetChildren()
                              if _is_candidate(w)]
                exact = [w for w in candidates
                         if (w.Name or "").rstrip().endswith(self.printer_name)]
                pick = exact[0] if exact else (candidates[0] if candidates else None)
                if pick is not None:
                    self._dlg = pick
                if self._dlg and self._dlg.Exists(0):
                    break
                time.sleep(0.1)
            if not self._dlg:
                return False
            # Move off-screen
            try:
                self._dlg.MoveWindow(-3000, -3000, 800, 800)
            except Exception:
                pass
            time.sleep(0.2)
            # Selecteer Printer Info tab
            self._tab = _find_descendant(self._dlg, lambda c:
                c.ControlTypeName == "TabItemControl"
                and "Printer Info" in (c.Name or "")
            )
            if self._tab:
                try:
                    sel = self._tab.GetPattern(auto.PatternId.SelectionItemPattern)
                    if sel: sel.Select()
                except Exception:
                    pass
                time.sleep(0.3)
            # Cache de Update knop
            self._update_btn = _find_descendant(self._dlg, lambda c:
                c.ControlTypeName == "ButtonControl"
                and (c.Name or "").startswith("Update")
            )
            if self._update_btn:
                try:
                    self._update_invoker = self._update_btn.GetPattern(
                        auto.PatternId.InvokePattern
                    )
                except Exception:
                    self._update_invoker = None
            return True
        except Exception as e:
            print(f"[DNP-STATUS] _ensure_open faal: {e}")
            return False

    def read(self) -> Optional[DNPStatus]:
        """Doe 1 status-read. Returnt None bij faal."""
        with self._lock:
            if not self._ensure_open():
                self._fail_count += 1
                return None
            try:
                # Click Update via Invoke (zonder muis — werkt off-screen)
                if self._update_invoker:
                    try:
                        self._update_invoker.Invoke()
                    except Exception:
                        # Cache verlopen — kill & retry next cycle
                        self.close()
                        self._fail_count += 1
                        return None
                    time.sleep(0.45)  # geef driver tijd voor USB-call
                # Scrape
                controls = []
                def gather(node, depth=0, maxd=10):
                    if depth > maxd: return
                    try:
                        for c in node.GetChildren():
                            controls.append(c)
                            gather(c, depth+1, maxd)
                    except Exception:
                        pass
                gather(self._dlg)
                if not controls:
                    self._fail_count += 1
                    return None
                status = DNPStatus(error_method="ui_automation_persistent")
                _parse_controls(controls, status)
                status.connected = True
                self._fail_count = 0
                return status
            except Exception as e:
                self._fail_count += 1
                if self._fail_count >= 3:
                    # 3 keer op rij faal → herstart dialog
                    self.close()
                print(f"[DNP-STATUS] read faal #{self._fail_count}: {e}")
                return None

    def close(self):
        """Sluit dialog netjes — GEEN SendKeys (steelt keyboard-focus van
        actieve apps). Alleen subprocess terminate; Windows ruimt z'n
        eigen dialog op."""
        with self._lock:
            try:
                if self._proc:
                    self._proc.terminate()
                    self._proc.wait(timeout=2.0)
            except Exception:
                pass
            self._dlg = None
            self._tab = None
            self._update_btn = None
            self._update_invoker = None
            self._proc = None


def read_via_ui_automation(printer_name: str, timeout_sec: float = 8.0) -> Optional[DNPStatus]:
    """Open DPQW410UI Voorkeursinstellingen dialog off-screen, scrape data.

    Returnt None als uiautomation niet beschikbaar of de dialog niet
    binnen `timeout_sec` opent. Verstoort het printen NIET — gebruikt
    geen libusb, alleen de bestaande Windows-driver via z'n eigen UI.
    """
    try:
        import uiautomation as auto
    except ImportError:
        return None

    status = DNPStatus(error_method="ui_automation")
    proc = None
    dlg = None
    try:
        # 1. Start dialog
        proc = subprocess.Popen([
            "rundll32", "printui.dll,PrintUIEntry",
            "/e", "/n", printer_name
        ], creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))

        # 2. Wacht op dialog. We zoeken een venster waarvan de naam
        # ontwerp-onafhankelijk de printernaam bevat. Localisatie-veilig:
        # zowel "Voorkeursinstellingen voor afdrukken voor X" (NL) als
        # "Printing preferences for X" (EN) en varianten matchen.
        deadline = time.monotonic() + timeout_sec
        dlg = None
        while time.monotonic() < deadline:
            for w in auto.GetRootControl().GetChildren():
                try:
                    name = w.Name or ""
                    if printer_name in name and w.ControlTypeName == "WindowControl":
                        # Niet het hoofdscherm van Windows Settings
                        if "Settings" in name or "Instellingen" in name:
                            continue
                        dlg = w
                        break
                except Exception:
                    pass
            if dlg and dlg.Exists(0):
                break
            time.sleep(0.1)
        if not dlg:
            return None

        # 3. Move off-screen
        try:
            dlg.MoveWindow(-3000, -3000, 800, 800)
        except Exception:
            pass

        # 4. Vind Printer Info tab + selecteer via UIA pattern (geen muis)
        time.sleep(0.2)
        tab = _find_descendant(dlg, lambda c:
            c.ControlTypeName == "TabItemControl"
            and ("Printer Info" in (c.Name or "") or "Printerinfo" in (c.Name or ""))
        )
        if tab:
            try:
                sel = tab.GetPattern(auto.PatternId.SelectionItemPattern)
                if sel: sel.Select()
            except Exception:
                pass
            time.sleep(0.4)

        # 5. Klik Update knop via Invoke pattern
        btn = _find_descendant(dlg, lambda c:
            c.ControlTypeName == "ButtonControl"
            and (c.Name or "").startswith("Update")
        )
        if btn:
            try:
                inv = btn.GetPattern(auto.PatternId.InvokePattern)
                if inv: inv.Invoke()
            except Exception:
                pass
            time.sleep(0.8)

        # 6. Scrape alle text en edit controls met posities
        controls = []
        def gather(node, depth=0, maxd=10):
            if depth > maxd: return
            try:
                for c in node.GetChildren():
                    controls.append(c)
                    gather(c, depth+1, maxd)
            except Exception:
                pass
        gather(dlg)

        # 7. Map naar status-velden via positie heuristiek + label-match
        _parse_controls(controls, status)
        status.connected = True
        return status

    except Exception as e:
        status.detail = f"UIA fout: {e}"
        return status if status.serial or status.code is not None else None
    finally:
        # Cleanup: sluit dialog
        try:
            if dlg:
                dlg.SendKeys("{Esc}")
        except Exception:
            pass
        try:
            if proc:
                proc.terminate()
                proc.wait(timeout=2.0)
        except Exception:
            pass


def _find_descendant(node, predicate, maxd=10):
    """Zoek recursief eerste descendant die voldoet."""
    if maxd <= 0: return None
    try:
        for c in node.GetChildren():
            try:
                if predicate(c):
                    return c
            except Exception:
                pass
            r = _find_descendant(c, predicate, maxd-1)
            if r: return r
    except Exception:
        pass
    return None


def _parse_controls(controls: list, status: DNPStatus):
    """Heuristisch alle waardes uit de scraped controls extraheren.

    De DPQW410UI Printer Info tab heeft een vaste layout:
      - Bovenin: "<media>" + "<remaining> / <total>" + progressbar
      - Printer Status: EditControl met statustekst ("Waiting"/"Top door open"/...)
      - Total Count: TextControl met integer
      - Firmware Version: TextControl met versie-string
      - Serial No.: TextControl met serial
      - Color Control Data: TextControls met "QW410_SD_*.CWD" + 4-char checksum

    We pakken dit door positie + content-match.
    """
    # Verzamel alleen Text/Edit controls met niet-lege text
    items = []
    for c in controls:
        try:
            tn = c.ControlTypeName
            if tn not in ("EditControl", "TextControl"):
                continue
            text = (c.Name or "").strip()
            if tn == "EditControl":
                try:
                    vp = c.GetValuePattern()
                    if vp and vp.Value: text = vp.Value.strip()
                except Exception:
                    pass
            if not text:
                continue
            rect = c.BoundingRectangle
            items.append((rect.top, rect.left, tn, text))
        except Exception:
            pass
    items.sort()

    # Heuristieken — patroon-gebaseerd (positie-onafhankelijk want off-screen
    # dialog heeft negatieve coordinaten)
    integers = []  # collect raw ints, decide later
    for top, left, tn, text in items:
        # Edit met statustekst → status code
        if tn == "EditControl":
            tl = text.lower()
            if tl in UI_STATUS_TO_CODE:
                code = UI_STATUS_TO_CODE[tl]
                status.code = code
                if code in STATUS_CODES:
                    lbl, lvl = STATUS_CODES[code]
                    status.label = lbl
                    status.level = StatusLevel(lvl)
            else:
                # Bekende fout-substring zoeken. Hoogste code eerst:
                # error-strings (1000+) winnen van benigne ("waiting",
                # "ready") zodat een samengestelde tekst als
                # "Waiting - paper end" niet onterecht als OK matcht.
                matched = False
                for ui_text, code in sorted(UI_STATUS_TO_CODE.items(),
                                            key=lambda kv: -kv[1]):
                    if ui_text in tl:
                        status.code = code
                        if code in STATUS_CODES:
                            lbl, lvl = STATUS_CODES[code]
                            status.label = lbl
                            status.level = StatusLevel(lvl)
                        matched = True
                        break
                if not matched and not status.label:
                    status.label = text
                    status.level = StatusLevel.WARNING
            continue

        # Firmware: "QW410 X.YZ" — bevat spatie + punt
        if text.startswith("QW410 ") and "." in text and len(text) < 20:
            status.firmware = text
            continue
        # Color profiles: "QW410_SD_*.CWD"
        if "QW410_SD_300_" in text:
            status.color_profile_300dpi = text
            continue
        if "QW410_SD_310_" in text:
            status.color_profile_low_speed = text
            continue
        # Serial: alphanum-only, geen spatie/punt/underscore, lengte 10-14
        if (text.startswith("QW")
            and " " not in text
            and "." not in text
            and "_" not in text
            and 10 <= len(text) <= 14):
            status.serial = text
            continue
        # Media (typisch "4x6", "4.5x6", etc.)
        if not status.media and "x" in text and len(text) < 10:
            try:
                parts = text.lower().split("x")
                if len(parts) == 2 and all(p.replace(".", "").isdigit() for p in parts):
                    status.media = f"{parts[0]}×{parts[1]} inch"
                    status.media_code = text
                    continue
            except Exception:
                pass
        # Verzamel pure integers voor later
        if text.isdigit() and len(text) <= 7:
            integers.append(int(text))

    # Integers: heuristisch verdelen.
    # Bij QW410 zien we typisch:
    #   - prints_remaining: 100-9999 (klein)
    #   - prints_total:     100-9999 (vaak iets groter)
    #   - life_counter:     0-999999 (totale levensduur)
    # Plus duplicaten (de progressbar toont total 2×).
    # Strategie: unieke set, sorteer, kleinste = life_counter, mid = remaining,
    # grootste in 100-9999 range = total. NB: life kan groter zijn dan remaining
    # dus we doen het op MOMENT van verzameling: als we al 2 ints in 'remaining'
    # range hebben, daarna komen weer ints, dat is life_counter.
    # ── VOORKEURSPAD: vaste QW410-rolgroottes als anker ───────────────
    # De QW410 kent maar twee mediaformaten met VASTE capaciteit:
    # 4x6" = 150 prints, 4.5x8" = 110 prints. Staat die waarde letterlijk
    # tussen de integers, dan is dát de total; remaining = de grootste
    # waarde ≤ total (na het wegnemen van één total-occurrence). Alles
    # erboven is de levensteller.
    # Dit voorkomt de 150/3275-bug: de levensteller (bv. 3275) valt óók
    # in de oude 50-9999 band waardoor remaining=150 (rolgrootte!) en
    # total=3275 (levensteller) gerapporteerd werd terwijl er echt nog
    # maar 46 prints over waren.
    m = (status.media or "").lower().replace(" ", "")
    if m.startswith("4x6"):
        media_totals = [150]
    elif m.startswith("4.5"):
        media_totals = [110]
    else:
        media_totals = [150, 110]  # media onbekend: beide proberen
    anchor_total = next((t for t in media_totals if t in integers), None)
    if anchor_total is not None:
        rest = list(integers)
        rest.remove(anchor_total)  # één occurrence = de total-weergave zelf
        rem_cands = [v for v in rest if v <= anchor_total]
        if rem_cands:
            # Grootste ≤ total: negeert losse strooi-nullen, pakt bij een
            # verse rol de tweede total-occurrence (150/150) en bij bijna
            # leeg gewoon het kleine getal (3/150).
            status.prints_remaining = max(rem_cands)
            status.prints_total = anchor_total
        else:
            status.prints_total = anchor_total
        life_cands = [v for v in rest if v > anchor_total]
        if life_cands and status.life_counter is None:
            status.life_counter = max(life_cands)
        return

    # ── FALLBACK: oude band-heuristiek (geen rolgrootte herkend) ─────
    unique = []
    seen = set()
    dup_values = set()
    for v in integers:
        if v not in seen:
            unique.append(v); seen.add(v)
        else:
            dup_values.add(v)
    # Eerste 2 grote ints (50-9999): remaining + total
    # Andere: life_counter (typisch klein bij nieuwe printer)
    pr_total_candidates = [v for v in unique if 50 <= v <= 9999]
    other = [v for v in unique if v not in pr_total_candidates]
    if len(pr_total_candidates) >= 2:
        # Eerste = remaining (kleiner, telt af), tweede = total
        status.prints_remaining = pr_total_candidates[0]
        status.prints_total = pr_total_candidates[1]
        # Resterende ints in deze categorie = life_counter mogelijk
        if len(pr_total_candidates) >= 3:
            other.append(pr_total_candidates[2])
    elif len(pr_total_candidates) == 1 and pr_total_candidates[0] in dup_values:
        # Verse rol: remaining == total (bv. 400/400). De dedup vouwt die
        # twee waardes samen tot 1 — maar als dezelfde waarde dubbel in de
        # ruwe lijst stond is het vrijwel zeker remaining==total.
        status.prints_remaining = pr_total_candidates[0]
        status.prints_total = pr_total_candidates[0]
    elif len(pr_total_candidates) == 1:
        # Bijna-lege rol: remaining < 50 valt buiten de candidate-band en
        # belandt in 'other'. Als er precies één duidelijke total is én een
        # kleine waarde (0-49) in other, dan is dat de remaining. Belangrijk:
        # juist bij bijna-leeg moet de teller blijven werken (de >10 prints
        # anti-verspillingscheck hangt ervan af).
        small = [v for v in other if 0 <= v < 50]
        if small:
            status.prints_remaining = small[0]
            status.prints_total = pr_total_candidates[0]
            other = [v for v in other if v != small[0]]
    if other and status.life_counter is None:
        # Pak de kleinste als life counter (begin-printer telt nog laag op)
        # of grootste — beide kunnen. Voor QW410 met 41 prints is dat 41.
        status.life_counter = min(other) if min(other) > 0 else max(other)


# ── Libusb-1.0 fallback pad (enumeratie only — geen claim) ──────────

_USB_BACKEND = None
_USB_BACKEND_ERR = ""
try:
    import usb.core
    import usb.backend.libusb1
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
    if _USB_BACKEND is None:
        _USB_BACKEND_ERR = "libusb-1.0.dll niet gevonden"
except Exception as e:
    _USB_BACKEND_ERR = f"pyusb/libusb import faal: {e}"


DNP_VENDOR_ID = 0x1452
QW410_PRODUCT_IDS = (0x9201,)


def read_via_usb_enum() -> DNPStatus:
    """Enumereer USB-devices, return DNPStatus met alleen connected-veld."""
    status = DNPStatus(error_method="libusb1_enum")
    if _USB_BACKEND is None:
        status.detail = _USB_BACKEND_ERR
        status.level = StatusLevel.UNKNOWN
        return status
    try:
        for dev in usb.core.find(find_all=True, backend=_USB_BACKEND):
            if dev.idVendor == DNP_VENDOR_ID and dev.idProduct in QW410_PRODUCT_IDS:
                status.connected = True
                status.level = StatusLevel.UNKNOWN  # we weten verder niks
                status.label = "USB-printer aangesloten"
                return status
    except Exception as e:
        status.detail = f"USB-enumeratie fout: {e}"
        return status
    # Niet gevonden
    status.connected = False
    status.level = StatusLevel.ERROR
    status.label = "Printer niet aangesloten"
    status.detail = "Controleer USB-kabel en stroom"
    return status


# ── Hoog-niveau API ─────────────────────────────────────────────────

def read_qw410_status(printer_name: str = "DP-QW410 (Kopie 2)",
                       detailed: bool = True,
                       timeout_ms: int = 8000) -> DNPStatus:
    """Lees de QW410-status. Returnt altijd een DNPStatus-object.

    1. Probeer UI Automation (volledige status). Vereist uiautomation +
       printer met DNP-driver geïnstalleerd.
    2. Bij faal: fallback naar USB-enumeratie (alleen plug/unplug).
    """
    # Pad 1: UI Automation
    ui_status = read_via_ui_automation(printer_name, timeout_sec=timeout_ms/1000)
    if ui_status and (ui_status.serial or ui_status.code is not None or ui_status.firmware):
        return ui_status

    # Pad 2: USB enumeratie fallback
    return read_via_usb_enum()


# ── Thread-safe poller voor UI-integratie ────────────────────────────

class StatusPoller:
    """Background-poller met persistent off-screen DPQW410UI dialog.

    Performance:
      - Setup: ~1.7s eenmalig bij start
      - Per poll: ~1s (Update klik + scrape)
      - Standaard interval: 2.0 sec → near-realtime updates

    Tijdens pause (typisch tijdens een actieve print) wordt de poll
    geskipt maar dialog blijft open — geen heropen-overhead na resume.
    """
    def __init__(self, interval_sec: float = 2.0, printer_name: str = "DP-QW410"):
        self._interval = interval_sec
        self._printer_name = printer_name
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._status: Optional[DNPStatus] = None
        self._callbacks: list = []
        self._paused = False
        self._dialog = _PersistentDialog(printer_name)
        self._offline_override_active = False  # log-throttle voor cross-check

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10.0)
        # Sluit dialog netjes bij shutdown
        try:
            self._dialog.close()
        except Exception:
            pass

    def pause(self, paused: bool):
        self._paused = paused

    def get(self) -> Optional[DNPStatus]:
        with self._lock:
            return self._status

    def on_change(self, callback):
        self._callbacks.append(callback)

    def force_refresh(self):
        """Forceer onmiddellijke poll (bv. na 'Opnieuw checken' klik).

        Gebruikt exact dezelfde cross-check als _loop — anders kan een
        stale UI-Automation read (driver toont cached data bij offline
        printer) de error-overlay onterecht sluiten en een print naar
        een dode printer sturen.
        """
        try:
            new_status = self._dialog.read()
            if new_status is None:
                new_status = read_via_usb_enum()
            else:
                new_status = self._apply_usb_cross_check(new_status)
        except Exception as e:
            new_status = DNPStatus(
                level=StatusLevel.UNKNOWN,
                error_method="exception",
                detail=str(e),
            )
        self._update(new_status)

    def _apply_usb_cross_check(self, ui_status: DNPStatus) -> DNPStatus:
        """KRITISCHE CROSS-CHECK: UI Automation kan een 'succesvolle' read
        teruggeven ook als de printer offline is — de Windows-driver toont
        gewoon de laatste cached waardes. USB-enum detecteert écht
        plug/unplug.

        NB: read_via_usb_enum zet error_method altijd op 'libusb1_enum';
        alleen level==ERROR garandeert 'backend werkt + device niet
        gevonden'. Bij ontbrekende backend is level=UNKNOWN — dan kunnen
        we niets zeggen en blijft de UI-status staan.
        """
        try:
            usb_check = read_via_usb_enum()
            if (usb_check.error_method == "libusb1_enum"
                    and not usb_check.connected
                    and usb_check.level == StatusLevel.ERROR):
                # Log alleen bij transitie — niet elke 2 sec opnieuw
                if not self._offline_override_active:
                    print(f"[DNP-STATUS] USB-enum zegt OFFLINE; "
                          f"UI Automation gaf {ui_status.label!r} "
                          f"(stale). Override naar offline.")
                    self._offline_override_active = True
                # Bewaar UI Automation tellers indien aanwezig
                # (handig voor 'prints over' bij re-connect)
                usb_check.prints_remaining = ui_status.prints_remaining
                usb_check.prints_total = ui_status.prints_total
                return usb_check
            self._offline_override_active = False
            # Indien UI Automation 'Communication error' code gaf —
            # eveneens markeren als offline (driver eigen detectie).
            if ui_status.code == 9999:
                ui_status.connected = False
                if ui_status.level != StatusLevel.ERROR:
                    ui_status.level = StatusLevel.ERROR
        except Exception as ce:
            print(f"[DNP-STATUS] USB cross-check faalde: {ce}")
        return ui_status

    def _update(self, new_status: DNPStatus):
        with self._lock:
            old = self._status
            changed = (
                old is None
                or old.level != new_status.level
                or old.code != new_status.code
                # connected-flip moet de UI ook bereiken (offline overlay)
                or old.connected != new_status.connected
                # prints-teller live houden in event-info popup
                or old.prints_remaining != new_status.prints_remaining
            )
            self._status = new_status
        if changed:
            for cb in list(self._callbacks):
                try:
                    cb(new_status)
                except Exception as e:
                    print(f"[DNP-STATUS] Callback-fout: {e}")

    def _loop(self):
        # Eerste poll: gebruik persistent dialog (opent dialog)
        while not self._stop_event.is_set():
            if not self._paused:
                try:
                    new_status = self._dialog.read()
                    if new_status is None:
                        # Dialog kapot of UI Automation niet beschikbaar →
                        # fallback USB-enumeratie
                        new_status = read_via_usb_enum()
                    else:
                        new_status = self._apply_usb_cross_check(new_status)
                except Exception as e:
                    new_status = DNPStatus(
                        level=StatusLevel.UNKNOWN,
                        error_method="poller_exception",
                        detail=str(e),
                    )
                self._update(new_status)
            self._stop_event.wait(self._interval)

    def kill_dialog(self):
        """Snelle cleanup bij app-exit: termineer het rundll32-proces
        zonder locks of joins (exit-pad mag nooit blokkeren)."""
        try:
            proc = self._dialog._proc
            if proc is not None:
                proc.terminate()
        except Exception:
            pass


# ── CLI test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    print("=" * 64)
    print("  DNP QW410 status-test (UI Automation pad)")
    print("=" * 64)
    t0 = time.monotonic()
    status = read_qw410_status()
    dt = time.monotonic() - t0
    print(f"Tijd:         {dt:.1f}s")
    print(f"Level:        {status.level.value}")
    print(f"Code:         {status.code}")
    print(f"Label:        {status.label}")
    print(f"Connected:    {status.connected}")
    print(f"Media:        {status.media} (raw: {status.media_code!r})")
    print(f"Prints left:  {status.prints_remaining}/{status.prints_total}")
    print(f"Life counter: {status.life_counter}")
    print(f"Serial:       {status.serial!r}")
    print(f"Firmware:     {status.firmware!r}")
    print(f"Color300dpi:  {status.color_profile_300dpi!r}")
    print(f"ColorLowSpd:  {status.color_profile_low_speed!r}")
    print(f"Method:       {status.error_method}")
    print(f"Detail:       {status.detail}")
    print(f"Blocking:     {status.is_blocking()}")
