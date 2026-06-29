"""Centrale logging voor de photobooth — NIET-BLOKKEREND.

Onderschept sys.stdout / sys.stderr. Voor elke regel:
  1. de console krijgt de RAUWE tekst direct + flush (zoals voorheen —
     de terminal blijft live scrollen, voelt niet als 'hangen');
  2. een getimestampte kopie ([YYYY-MM-DD HH:MM:SS] prefix) gaat via een
     wachtrij naar een achtergrond-thread die ze wegschrijft naar
     DATA_DIR/logs/booth.log (roterend) en doorgeeft aan sinks
     (de cloud-uploader).

KRITIEK: de schrijfkant (bestand-IO + sinks) draait op een aparte thread,
zodat print() vanaf de Qt-hoofdthread NOOIT blokkeert op trage schijf-IO.
Een eerdere versie deed de file-flush synchroon ín write() — dat kon de
UI laten haperen / 'blijven laden'.
"""

import os
import queue
import sys
import threading
from datetime import datetime

_MAX_BYTES = 5 * 1024 * 1024   # roteer boven 5 MB
_BACKUPS = 3

_log_path = None
_queue = queue.Queue(maxsize=20000)
_writer_thread = None

_sinks = []
_sink_lock = threading.Lock()


def register_sink(callback):
    """callback(formatted_line: str) voor elke geprefixte regel. De
    callback MOET licht zijn (alleen bufferen) en niet zelf print()'en."""
    with _sink_lock:
        _sinks.append(callback)


def get_log_path():
    return _log_path


class _Tee:
    """Vervangt sys.stdout/sys.stderr. Console = rauw + direct flush;
    bestand/sinks = getimestampt via de achtergrond-wachtrij."""

    def __init__(self, original):
        self._original = original
        self._buf = ""
        self._lock = threading.Lock()

    def write(self, text):
        # 1. Console: rauw doorschrijven + flushen (origineel gedrag).
        if self._original is not None:
            try:
                self._original.write(text)
                self._original.flush()
            except Exception:
                pass
        # 2. Per hele regel een getimestampte kopie in de wachtrij zetten.
        try:
            with self._lock:
                self._buf += text
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    if not line:
                        continue
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    try:
                        _queue.put_nowait(f"[{ts}] {line}")
                    except queue.Full:
                        pass  # nooit de schrijver laten blokkeren
        except Exception:
            pass

    def flush(self):
        if self._original is not None:
            try:
                self._original.flush()
            except Exception:
                pass

    def isatty(self):
        return False

    def fileno(self):
        if self._original is not None and hasattr(self._original, "fileno"):
            return self._original.fileno()
        raise OSError("geen fileno")


def _writer_loop():
    """Achtergrond-thread: leegt de wachtrij naar bestand + sinks."""
    fh = None
    size = 0

    def _open():
        nonlocal fh, size
        try:
            size = os.path.getsize(_log_path) if os.path.exists(_log_path) else 0
            fh = open(_log_path, "a", encoding="utf-8", errors="replace")
        except Exception:
            fh = None
            size = 0

    def _rotate():
        nonlocal fh, size
        try:
            if fh is not None:
                fh.close()
        except Exception:
            pass
        fh = None
        try:
            for i in range(_BACKUPS, 0, -1):
                src = _log_path if i == 1 else f"{_log_path}.{i - 1}"
                dst = f"{_log_path}.{i}"
                if os.path.exists(src):
                    if os.path.exists(dst):
                        os.remove(dst)
                    os.rename(src, dst)
        except Exception:
            pass
        _open()

    _open()
    while True:
        line = _queue.get()
        if line is None:
            break
        # Bestand
        if fh is not None:
            try:
                data = line + "\n"
                fh.write(data)
                fh.flush()
                size += len(data.encode("utf-8", "replace"))
                if size > _MAX_BYTES:
                    _rotate()
            except Exception:
                pass
        # Sinks (cloud-uploader)
        with _sink_lock:
            sinks = list(_sinks)
        for s in sinks:
            try:
                s(line)
            except Exception:
                pass


def install_logging(data_dir):
    """Activeer de getimestampte, niet-blokkerende logging. Zo vroeg
    mogelijk in main() aanroepen."""
    global _log_path, _writer_thread
    try:
        log_dir = os.path.join(data_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)
        _log_path = os.path.join(log_dir, "booth.log")
        _writer_thread = threading.Thread(target=_writer_loop, daemon=True,
                                          name="LogWriter")
        _writer_thread.start()
        sys.stdout = _Tee(sys.__stdout__)
        sys.stderr = _Tee(sys.__stderr__)
    except Exception as e:
        try:
            (sys.__stdout__ or sys.stdout).write(f"[APP-LOGGER] init mislukt: {e}\n")
        except Exception:
            pass
