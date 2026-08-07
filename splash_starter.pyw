"""
Bootharoo Instant Splash Screen + App Launcher.

Two-phase startup:
1. Phase 1: Show tkinter splash INSTANTLY (<100ms) - no heavy imports
2. Phase 2: Import and start the real PyQt5 app, close tkinter splash

tkinter is built into Python and imports in <100ms vs PyQt5's ~3-5 seconds.
This gives users immediate visual feedback that the app is loading.

This is the entry point for the EXE build.
"""
import sys
import os
import ssl

# Use Windows certificate store instead of bundled certifi certificates.
# This fixes SSL errors on PCs with corporate proxies, antivirus SSL
# interception, or missing/outdated root certificates in the bundle.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    # Fallback: disable certificate verification (less secure but functional)
    ssl._create_default_https_context = ssl._create_unverified_context

# Set working directory to the script/exe location FIRST
if getattr(sys, 'frozen', False):
    app_dir = os.path.dirname(sys.executable)
    os.chdir(app_dir)
else:
    app_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(app_dir)

# Ensure app dir is in path
sys.path.insert(0, app_dir)

# Handle --print-worker subprocess mode BEFORE any log redirect.
# The subprocess is spawned with capture_output=True — stdout/stderr must
# stay as pipes so the main app can read errors. DO NOT redirect them here.
if "--print-worker" in sys.argv:
    # rthook_cv2.py inserts _MEIPASS/cv2/ at sys.path[0] so OpenCV can
    # self-import from disk. This causes two problems for the print worker:
    #   1. `import config` finds cv2/config.py instead of our app's config.py
    #   2. `from typing import Any` finds cv2/typing/__init__.py instead of
    #      stdlib typing, breaking numpy imports in Pillow's chain.
    # The print worker never uses cv2, so remove it from sys.path entirely.
    if hasattr(sys, '_MEIPASS'):
        cv2_dir = os.path.join(sys._MEIPASS, 'cv2')
        while cv2_dir in sys.path:
            sys.path.remove(cv2_dir)
        # If cv2's typing stub was partially loaded into sys.modules before
        # our removal above, clear it so stdlib typing can be loaded correctly.
        _typing_mod = sys.modules.get('typing')
        if _typing_mod is not None:
            _typing_file = getattr(_typing_mod, '__file__', '') or ''
            if 'cv2' in _typing_file.replace('\\', '/'):
                del sys.modules['typing']
    # Force-cache stdlib typing now (before any numpy/PIL/PyQt5 import touches it)
    import typing as _typing_stdlib  # noqa: F401  – side effect: caches in sys.modules

    try:
        idx = sys.argv.index("--print-worker")
        args = sys.argv[idx + 1:]  # image_path, printer_name, copies, data_dir
        if len(args) >= 3:
            from print_worker import main as print_main
            sys.argv = ["print_worker.py"] + args
            print_main()
        else:
            print(f"[PRINT-WORKER] Te weinig argumenten: {args}", file=sys.stderr)
            sys.exit(2)
    except SystemExit:
        raise  # preserve exit code from print_worker
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)
    sys.exit(0)

# ── Zelftest ────────────────────────────────────────────────────────────
# `Bootharoo.exe --selftest` importeert alles wat de app bij het opstarten
# nodig heeft, controleert een paar dingen die eerder stuk zijn gegaan, en
# sluit af met code 0 (goed) of 1 (fout). Geen venster, geen camera, geen
# printer — puur de vraag: komt deze build overeind?
#
# Dit bestaat omdat v1.99.147 een installer opleverde die keurig werd
# gebouwd en vervolgens op de booth niet meer opstartte. De bouwstraat had
# alleen gecontroleerd DAT er een installer uit kwam, niet DAT die werkte.
# Deze vlag wordt in de bouwstraat op de geinstalleerde exe gedraaid; faalt
# hij, dan faalt de bouw en komt er geen artefact en geen release.
#
# De uitvoer gaat naar een bestand omdat de exe zonder console wordt gebouwd
# (console=False): geschreven tekst zou anders nergens terechtkomen.
if "--selftest" in sys.argv:
    import traceback

    _uitvoer = os.path.join(
        os.environ.get("TEMP", os.path.expanduser("~")), "bootharoo_selftest.txt"
    )
    _regels = []

    def _log(regel):
        _regels.append(regel)

    _fouten = 0

    def _controleer(naam, functie):
        global _fouten
        try:
            resultaat = functie()
            _log(f"OK    {naam}" + (f" — {resultaat}" if resultaat else ""))
        except Exception as exc:
            _fouten += 1
            _log(f"FOUT  {naam} — {type(exc).__name__}: {exc}")
            _log(traceback.format_exc())

    def _typing_is_standaard():
        # DE regressietest voor v1.99.147. cv2 bevat een submap 'typing'; als
        # die de standaardbibliotheek overschaduwt, klapt numpy om en start de
        # app niet meer op. Zie rthook_cv2.py.
        import typing
        bestand = (getattr(typing, "__file__", "") or "").replace("\\", "/")
        if "/cv2/" in bestand:
            raise RuntimeError(
                f"'typing' komt uit cv2 in plaats van de standaardbibliotheek: {bestand}"
            )
        return bestand or "ingebouwd"

    def _numpy():
        import numpy
        return numpy.__version__

    def _cv2():
        import cv2
        return cv2.__version__

    def _pyqt():
        # Offscreen zodat er geen scherm nodig is. Een echt QApplication
        # aanmaken bewijst dat de Qt-plugins mee zijn gebouwd — een kale
        # `import PyQt5` zou dat niet aantonen.
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt5.QtWidgets import QApplication
        app = QApplication.instance() or QApplication(sys.argv[:1])
        return "QApplication aangemaakt"

    def _pillow():
        from PIL import Image, ImageDraw, ImageFont, ImageOps  # noqa: F401
        import PIL
        return PIL.__version__

    def _boto3():
        import boto3, botocore  # noqa: F401
        # De datamappen van botocore zijn eerder eens uit de build gevallen;
        # een client aanmaken dwingt af dat ze er echt zijn.
        boto3.client("s3", region_name="auto",
                     endpoint_url="https://example.invalid",
                     aws_access_key_id="x", aws_secret_access_key="y")
        return "S3-client aangemaakt"

    def _overige_pakketten():
        import flask, qrcode, requests, serial  # noqa: F401
        import win32print  # noqa: F401
        return "flask, qrcode, requests, pyserial, pywin32"

    def _config_is_van_ons():
        # Tweede regressietest. cv2 bevat ook een cv2/config.py; kwam die map
        # op sys.path, dan haalde `import config` OpenCV's bestand op in plaats
        # van dat van deze applicatie, en viel de app om met
        # "NameError: name 'LOADER_DIR' is not defined". Zie rthook_cv2.py.
        import config
        bestand = (getattr(config, "__file__", "") or "").replace("\\", "/")
        if "/cv2/" in bestand:
            raise RuntimeError(f"'config' komt uit cv2: {bestand}")
        versie = getattr(config, "VERSION", "")
        if not versie.startswith("v"):
            raise RuntimeError(
                f"'config' heeft geen geldige VERSION ({versie!r}) — "
                f"verkeerde module geladen? {bestand}"
            )
        return f"VERSION {versie}"

    def _eigen_modules():
        # De echte app-modules. photobooth.py is het grote bestand; als daar
        # iets in staat dat bij het importeren al struikelt, valt dat hier op.
        import photobooth  # noqa: F401
        import main  # noqa: F401
        import updater, cloud_storage, booth_settings, webcam  # noqa: F401
        import printer, camera, qr_generator, web_server  # noqa: F401
        return "photobooth, main en de rest"

    _log(f"Bootharoo zelftest — frozen={getattr(sys, 'frozen', False)}")
    _log(f"Python {sys.version.split()[0]}")
    _log("")

    _controleer("typing komt uit de standaardbibliotheek", _typing_is_standaard)
    _controleer("numpy", _numpy)
    _controleer("cv2 (OpenCV)", _cv2)
    _controleer("PyQt5", _pyqt)
    _controleer("Pillow", _pillow)
    _controleer("boto3 / botocore", _boto3)
    _controleer("overige pakketten", _overige_pakketten)
    _controleer("config is die van de applicatie", _config_is_van_ons)
    _controleer("eigen modules", _eigen_modules)

    _log("")
    _log("RESULTAAT: " + ("GOED" if _fouten == 0 else f"{_fouten} FOUT(EN)"))
    _tekst = "\n".join(_regels) + "\n"
    try:
        with open(_uitvoer, "w", encoding="utf-8") as _f:
            _f.write(_tekst)
    except Exception:
        pass
    try:
        # Als er wel een console is (niet-frozen aanroep) ook daar tonen.
        if sys.stdout is not None:
            sys.stdout.write(_tekst)
            sys.stdout.flush()
    except Exception:
        pass
    sys.exit(1 if _fouten else 0)

# Main app: redirect stdout/stderr to log file (frozen exe has no console)
if getattr(sys, 'frozen', False):
    # Log to writable user directory (Program Files is read-only)
    _log_dir = os.path.join(os.path.expanduser("~"), "Documents", "Bootharoo")
    os.makedirs(_log_dir, exist_ok=True)
    _log_path = os.path.join(_log_dir, "bootharoo.log")
    try:
        _log_file = open(_log_path, 'w', encoding='utf-8')
        sys.stdout = _log_file
        sys.stderr = _log_file
    except Exception:
        pass
else:
    if not sys.stdout:
        sys.stdout = open(os.devnull, 'w')
        sys.stderr = open(os.devnull, 'w')

import tkinter as tk
import threading
import time


def run():
    """Show instant splash, then load and start the real app."""
    # ── Phase 1: Instant tkinter splash ──
    root = tk.Tk()
    root.title("Bootharoo")
    root.overrideredirect(True)  # No title bar

    sw, sh = 600, 400
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    x = (screen_w - sw) // 2
    y = (screen_h - sh) // 2
    root.geometry(f"{sw}x{sh}+{x}+{y}")
    root.attributes('-topmost', True)

    bg_color = "#1a1a2e"
    root.configure(bg=bg_color)

    frame = tk.Frame(root, bg=bg_color)
    frame.place(relx=0.5, rely=0.5, anchor='center')

    # Camera logo image — no text, just the icon
    _logo_path = os.path.join(
        getattr(sys, '_MEIPASS', app_dir), "bootharoo-camera.png"
    )
    _logo_shown = False
    if os.path.exists(_logo_path):
        try:
            _img = tk.PhotoImage(file=_logo_path)
            _img = _img.subsample(2, 2)  # 274px → 137px
            _logo_lbl = tk.Label(frame, image=_img, bg=bg_color)
            _logo_lbl.image = _img  # keep reference
            _logo_lbl.pack(pady=(0, 10))
            _logo_shown = True
        except Exception:
            pass
    if not _logo_shown:
        tk.Label(
            frame, text="📷",
            font=("Segoe UI", 42),
            fg="#e0e0e0", bg=bg_color
        ).pack(pady=(0, 10))

    loading_var = tk.StringVar(value="Photobooth wordt geladen...")
    loading_label = tk.Label(
        frame, textvariable=loading_var,
        font=("Segoe UI", 16),
        fg="#888888", bg=bg_color
    )
    loading_label.pack(pady=(0, 20))

    # Accent bar at bottom
    accent = "#c8a96e"
    bar_canvas = tk.Canvas(root, height=6, bg=bg_color, highlightthickness=0)
    bar_canvas.place(relx=0, rely=1.0, anchor='sw', relwidth=1.0)
    bar_rect = bar_canvas.create_rectangle(0, 0, 0, 6, fill=accent, outline="")

    progress = {'value': 0.0, 'done': False}

    def animate_bar():
        if progress['done']:
            return
        progress['value'] = min(progress['value'] + 0.006, 0.95)
        bar_canvas.coords(bar_rect, 0, 0, int(sw * progress['value']), 6)
        root.after(50, animate_bar)

    animate_bar()

    # ── Phase 2: Load real app in background thread ──
    app_ready = threading.Event()

    def load_app():
        """Import the heavy modules in a background thread."""
        try:
            print("[SPLASH] Importeren van modules...", flush=True)
            t0 = time.time()

            # These are the slow imports
            from PyQt5.QtWidgets import QApplication
            from PyQt5.QtCore import Qt
            from PyQt5.QtGui import QFont, QFontDatabase

            print(f"[SPLASH] PyQt5 geladen in {time.time()-t0:.1f}s", flush=True)

            import config  # noqa

            print(f"[SPLASH] Alle imports klaar in {time.time()-t0:.1f}s", flush=True)
            app_ready.set()
        except Exception as e:
            print(f"[SPLASH] Import fout: {e}", flush=True)
            import traceback
            traceback.print_exc()
            app_ready.set()

    loader = threading.Thread(target=load_app, daemon=True)
    loader.start()

    def check_ready():
        """Poll until imports are done, then close splash and start app."""
        if app_ready.is_set():
            # Fill progress bar to 100%
            bar_canvas.coords(bar_rect, 0, 0, sw, 6)
            progress['done'] = True
            loading_var.set("Bijna klaar...")
            root.update()
            # Small delay so user sees 100%
            root.after(200, lambda: _start_main_app(root))
        else:
            root.after(100, check_ready)

    def _start_main_app(splash_root):
        """Destroy splash and start the real PyQt5 app."""
        try:
            splash_root.destroy()
        except Exception:
            pass

        # Now start the real app (on the main thread — required for PyQt5)
        try:
            from main import main
            main()
        except Exception as e:
            print(f"[SPLASH] App fout: {e}", flush=True)
            import traceback
            traceback.print_exc()
            sys.exit(1)

    # Start checking after splash is visible
    root.after(100, check_ready)

    # Safety net: force close splash after 60 seconds
    root.after(60000, lambda: _start_main_app(root))

    try:
        root.mainloop()
    except Exception:
        pass


if __name__ == "__main__":
    run()
