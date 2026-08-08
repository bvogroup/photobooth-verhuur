"""
MyBoothBox Photobooth Application
=================================
Photobooth software for Microsoft Surface with:
- Canon EOS 1200D camera (via digiCamControl)
- HiTi P525L printer
- Live preview, 4x6 inch prints
- QR code download via WiFi

Usage:
    python main.py              Start fullscreen
    python main.py --windowed   Start in windowed mode
    pythonw splash_starter.pyw  Start with instant splash (production)

Keyboard shortcuts:
    F11     Toggle fullscreen
    F12     Open template editor
    Space   Take photo (when in preview mode)
    Escape  Open settings / return to idle
"""

import sys
import os

# Ensure the app directory is in the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont, QFontDatabase

import config
import merk
import lettertype


def _check_single_instance():
    """Ensure only one instance of the app is running using a lock file.
    Returns True if this is the only instance, False if another is already running."""
    import tempfile
    import msvcrt
    # Deze naam blijft bewust bootharoo_*. Het slot bestaat om te voorkomen dat
    # er twee photobooths tegelijk draaien, en het is een sleutel waarop een
    # draaiende versie wordt teruggevonden — geen naam die iemand ziet. Zou een
    # nieuwe versie een ander slot pakken dan de oude, dan zouden die twee
    # elkaar niet meer zien en samen kunnen opkomen.
    lock_path = os.path.join(tempfile.gettempdir(), "bootharoo_photobooth.lock")
    try:
        lock_fd = open(lock_path, "w")
        msvcrt.locking(lock_fd.fileno(), msvcrt.LK_NBLCK, 1)
        _check_single_instance._lock_fd = lock_fd
        return True
    except (OSError, IOError):
        return False


def _migrate_data():
    """Migrate data from old app directory to Documents/Bootharoo if needed."""
    import shutil
    app_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = config.DATA_DIR

    if os.path.isdir(data_dir):
        return

    folders_to_migrate = ["events", "templates", "photos", "backgrounds"]
    files_to_migrate = ["settings.json"]

    has_old_data = any(
        os.path.exists(os.path.join(app_dir, name))
        for name in folders_to_migrate + files_to_migrate
    )

    if not has_old_data:
        return

    print(f"[MIGRATE] Gegevens verplaatsen naar {data_dir}...")
    os.makedirs(data_dir, exist_ok=True)

    for folder in folders_to_migrate:
        src = os.path.join(app_dir, folder)
        dst = os.path.join(data_dir, folder)
        if os.path.isdir(src) and not os.path.isdir(dst):
            try:
                shutil.copytree(src, dst)
                print(f"[MIGRATE]   {folder}/ gekopieerd")
            except Exception as e:
                print(f"[MIGRATE]   {folder}/ fout: {e}")

    for fname in files_to_migrate:
        src = os.path.join(app_dir, fname)
        dst = os.path.join(data_dir, fname)
        if os.path.isfile(src) and not os.path.isfile(dst):
            try:
                shutil.copy2(src, dst)
                print(f"[MIGRATE]   {fname} gekopieerd")
            except Exception as e:
                print(f"[MIGRATE]   {fname} fout: {e}")

    print(f"[MIGRATE] Klaar! Data staat nu in: {data_dir}")


def _set_display_brightness():
    """Zet de schermhelderheid van het interne Surface-scherm via WMI.

    De Surface Pro 7 is fanless; het scherm is een grote warmtebron. Een
    vaste, niet-maximale helderheid scheelt warmte. Wordt bij elke start
    gezet omdat Windows/adaptive brightness 'm anders kan terugzetten.
    config.DISPLAY_BRIGHTNESS = 0 → niet aanraken.
    """
    import subprocess
    try:
        import config as _cfg
        pct = int(getattr(_cfg, "DISPLAY_BRIGHTNESS", 0) or 0)
    except Exception:
        pct = 0
    if pct <= 0:
        return
    pct = max(10, min(100, pct))
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-WmiObject -Namespace root/WMI -Class "
             f"WmiMonitorBrightnessMethods).WmiSetBrightness(1,{pct})"],
            capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        print(f"[BRIGHTNESS] Schermhelderheid op {pct}% gezet")
    except Exception as e:
        print(f"[BRIGHTNESS] Kon helderheid niet zetten: {e}")


def _ensure_firewall_rule():
    """Add Windows Firewall rule for the local web server (port 8080).

    Required so phones on the same WiFi can access the photo gallery.
    Silently skips if not running as admin or rule already exists.
    """
    import subprocess
    # Ook deze naam blijft staan. Het is de sleutel waarop de regel in de
    # Windows Firewall wordt teruggevonden: hernoemen zou op elke bestaande
    # booth de oude regel laten staan én er een tweede bij zetten. Het is geen
    # naam die de gebruiker in het programma tegenkomt.
    rule_name = "Bootharoo Photobooth"
    try:
        # Check if rule already exists
        result = subprocess.run(
            ["netsh", "advfirewall", "firewall", "show", "rule",
             f"name={rule_name}"],
            capture_output=True, text=True, timeout=10
        )
        if rule_name in result.stdout:
            print(f"[FIREWALL] Regel '{rule_name}' bestaat al")
            return
        # Add inbound TCP rule for port 8080
        result = subprocess.run(
            ["netsh", "advfirewall", "firewall", "add", "rule",
             f"name={rule_name}", "dir=in", "action=allow",
             "protocol=TCP", "localport=8080",
             "profile=any", "enable=yes"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            print(f"[FIREWALL] Regel '{rule_name}' toegevoegd (port 8080 inbound)")
        else:
            print(f"[FIREWALL] Kon regel niet toevoegen: {result.stderr.strip()}")
    except FileNotFoundError:
        print("[FIREWALL] netsh niet gevonden (geen Windows?)")
    except subprocess.TimeoutExpired:
        print("[FIREWALL] Timeout bij firewall check")
    except Exception as e:
        print(f"[FIREWALL] Fout: {e}")


def _install_crash_logger():
    """Vang alle uncaught exceptions + log naar app_crash.log met timestamp."""
    import traceback
    from datetime import datetime
    crash_log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_crash.log")

    def _excepthook(exc_type, exc_value, exc_tb):
        # KeyboardInterrupt → laat door
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        try:
            with open(crash_log, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"CRASH @ {datetime.now().isoformat()}\n")
                f.write(f"{'='*60}\n")
                traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
                f.flush()
        except Exception:
            pass
        # Ook naar console
        traceback.print_exception(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook
    print(f"[CRASH-LOG] Crashes worden gelogd naar {crash_log}")


def main():
    # Getimestampte logging zo vroeg mogelijk activeren — vangt alle
    # print()-statements en schrijft ze naar DATA_DIR/logs/booth.log.
    if not os.environ.get("PB_NO_LOGGING"):
        try:
            import config as _cfg
            from app_logger import install_logging
            install_logging(_cfg.DATA_DIR)
        except Exception as _e:
            print(f"[APP-LOGGER] niet geactiveerd: {_e}")

    _install_crash_logger()

    # High DPI support for Surface displays
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    app = QApplication(sys.argv)
    app.setApplicationName("MyBoothBox Photobooth")

    # Het merkicoon op elk venster van de applicatie.
    #
    # Windows pakt voor een venster zónder eigen icoon dat van de exe, en dat
    # is via bootharoo.spec al icon.ico. Dat werkt alleen zolang de app
    # bevroren draait; start iemand hem met python main.py, dan is de exe
    # python.exe en staat er een slangetje in de taakbalk. Hier expliciet
    # zetten dekt allebei de gevallen af, en zorgt er meteen voor dat ook
    # losse dialoogvensters het merk dragen in plaats van het Qt-standaardje.
    #
    # Cosmetisch, dus in een try: een photobooth op een feest moet opkomen,
    # ook als dit bestand ontbreekt.
    try:
        from PyQt5.QtGui import QIcon
        _icoon = os.path.join(config.BUNDLE_DIR, "icon.ico")
        if not os.path.exists(_icoon):
            _icoon = os.path.join(config.BASE_DIR, "icon.ico")
        if os.path.exists(_icoon):
            app.setWindowIcon(QIcon(_icoon))
    except Exception as _e:
        print(f"[ICOON] venstericoon niet gezet: {_e}", flush=True)

    # Single instance check
    if not _check_single_instance():
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.warning(None, "MyBoothBox",
                            "MyBoothBox draait al!\n\nEr kan maar één instantie tegelijk draaien.")
        sys.exit(0)

    # Migrate data from old location to Documents/Bootharoo
    _migrate_data()

    # DE TWEE TRAGE WINDOWS-KLUSSEN ZIJN NAAR ACHTEREN VERHUISD.
    #
    # Hier stonden _ensure_firewall_rule() en _set_display_brightness(), allebei
    # vóór het venster. Samen zijn dat drie subprocessen: twee keer `netsh
    # advfirewall` en een keer PowerShell met Get-WmiObject. PowerShell starten
    # kost op een fanless Surface al gauw twee tellen, netsh advfirewall ook —
    # en de gebruiker keek al die tijd naar het bureaublad, want de splash uit
    # splash_starter.pyw is dan net gesloten en het Qt-venster bestaat nog niet.
    #
    # Geen van beide heeft iets met opkomen te maken: de firewallregel is voor
    # binnenkomend verkeer en de helderheid mag een tel later. Ze gebeuren nu
    # ná het venster, op een eigen draad. Zie _klussen_op_de_achtergrond().

    # De merkletters inladen uit de meegeleverde map fonts/ — zie lettertype.py.
    #
    # Hier stond eerder alleen een terugval: vraag om "DM Sans" en pak "Segoe UI"
    # als die er niet is. Er werd echter nooit een lettertypebestand meegeleverd,
    # dus die terugval trad altijd op en elke gast keek naar de Windows-letter.
    # Nu gaan de bestanden mee in de build en worden ze hier ingelezen; er hoeft
    # niets geïnstalleerd te worden op de tablet.
    #
    # Dit staat in een try/except omdat het cosmetisch is: een photobooth op een
    # feest moet altijd opkomen, ook als er iets mis is met een lettertype. Zonder
    # dit vangnet bleef de app hangen op een foutmelding die niemand zag — de
    # bouwstraat liep er in de installatie-rookproef op vast.
    try:
        lettertype.laad_merkletters()
        app.setFont(merk.letter(merk.TEKST_LOPEND))
    except Exception as _e:
        print(f"[LETTER] inladen mislukt, we gaan door zonder: {_e}", flush=True)
        app.setFont(QFont("Segoe UI", 14))

    windowed = "--windowed" in sys.argv

    # Import and create window
    from photobooth import PhotoboothWindow
    window = PhotoboothWindow()

    # De lokale webserver is verwijderd. Hij luisterde op 0.0.0.0:8080 en
    # bestond uitsluitend om de terugval-QR te bedienen: een QR naar het adres
    # van de booth in het plaatselijke netwerk. Die werkte alleen als de gast
    # op hetzelfde wifi zat, wat op een feest vrijwel nooit zo is. Nu die QR
    # weg is, heeft de server geen enkele taak meer — en staat er een
    # luisterende poort minder open op een machine die bij klanten in de zaal
    # staat.

    # Het venster staat er al: PhotoboothWindow toont zichzelf met het merk
    # erop nog voordat de camera wordt aangesproken. Deze twee regels zetten
    # alleen de eindstand vast — dat is niet meer het moment waarop de
    # gebruiker voor het eerst iets ziet.
    if windowed:
        window.showMaximized()
    else:
        window.showFullScreen()

    # En nu pas de klussen die niets met opkomen te maken hebben.
    QTimer.singleShot(0, _klussen_op_de_achtergrond)

    sys.exit(app.exec_())


def _klussen_op_de_achtergrond():
    """De firewallregel en de schermhelderheid, buiten het opstarten om.

    Allebei subprocessen die op Windows seconden kunnen duren, en allebei niet
    nodig om een venster te tonen. Ze draaien op een eigen draad zodat ook het
    startscherm er niet op hoeft te wachten; er komt geen Qt aan te pas, dus
    dat mag.
    """
    import threading

    def _werk():
        _ensure_firewall_rule()
        _set_display_brightness()

    threading.Thread(target=_werk, daemon=True).start()


if __name__ == "__main__":
    # Check if invoked as subprocess print worker (PyInstaller frozen builds)
    if "--print-worker" in sys.argv:
        idx = sys.argv.index("--print-worker")
        sys.argv = [sys.argv[0]] + sys.argv[idx + 1:]
        from print_worker import main as pw_main
        pw_main()
        sys.exit(0)
    main()
