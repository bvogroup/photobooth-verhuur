"""Centrale logging voor de photobooth.

Onderschept sys.stdout / sys.stderr en geeft ELKE regel een
[YYYY-MM-DD HH:MM:SS] prefix. De geprefixte regels gaan naar:
  1. de originele console (indien aanwezig — in een frozen exe niet)
  2. een roterend logbestand in DATA_DIR/logs/booth.log
  3. geregistreerde sinks (de cloud-uploader leest hier de regels uit)

Zo krijgen alle bestaande print()-statements automatisch een tijdstempel
en worden ze gepersist + naar de cloud gesynct, zonder de aanroepende
code te wijzigen.
"""

import os
import sys
import threading
from datetime import datetime

_MAX_BYTES = 5 * 1024 * 1024   # roteer boven 5 MB
_BACKUPS = 3                    # booth.log.1 .. booth.log.3

_log_path = None
_fh = None
_size = 0
_io_lock = threading.RLock()

_sinks = []
_sink_lock = threading.Lock()


def register_sink(callback):
    """Registreer een callback(formatted_line: str) die voor elke
    geprefixte logregel wordt aangeroepen. Gebruikt door de cloud-
    uploader. De callback MOET licht zijn (alleen bufferen) en mag
    NIET zelf print()'en — dat zou recursie geven."""
    with _sink_lock:
        _sinks.append(callback)


def get_log_path():
    return _log_path


def _open_fh():
    global _fh, _size
    try:
        _size = os.path.getsize(_log_path) if os.path.exists(_log_path) else 0
        _fh = open(_log_path, "a", encoding="utf-8", errors="replace")
    except Exception:
        _fh = None
        _size = 0


def _rotate():
    """Sluit, schuif backups op (booth.log -> booth.log.1 -> ...), heropen."""
    global _fh, _size
    try:
        if _fh is not None:
            _fh.close()
    except Exception:
        pass
    _fh = None
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
    _open_fh()


def _write_line(formatted):
    """Schrijf één geprefixte regel naar het logbestand (met rotatie)."""
    global _fh, _size
    with _io_lock:
        if _fh is None:
            _open_fh()
        if _fh is None:
            return
        try:
            data = formatted + "\n"
            _fh.write(data)
            _fh.flush()
            _size += len(data.encode("utf-8", "replace"))
            if _size > _MAX_BYTES:
                _rotate()
        except Exception:
            pass


def _dispatch_sinks(formatted):
    with _sink_lock:
        sinks = list(_sinks)
    for s in sinks:
        try:
            s(formatted)
        except Exception:
            pass


class _Tee:
    """Vervangt sys.stdout/sys.stderr: prefixt per regel met een timestamp
    en spiegelt naar console + bestand + sinks."""

    def __init__(self, original):
        self._original = original
        self._buf = ""
        self._lock = threading.Lock()

    def write(self, text):
        # 1. Ongeprefixt naar de echte console (live debugging)
        try:
            if self._original is not None:
                self._original.write(text)
        except Exception:
            pass
        # 2. Per volledige regel prefixen + persisten + dispatchen
        try:
            with self._lock:
                self._buf += text
                while "\n" in self._buf:
                    line, self._buf = self._buf.split("\n", 1)
                    if line == "":
                        continue
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    formatted = f"[{ts}] {line}"
                    _write_line(formatted)
                    _dispatch_sinks(formatted)
        except Exception:
            pass

    def flush(self):
        try:
            if self._original is not None:
                self._original.flush()
        except Exception:
            pass

    def isatty(self):
        return False

    def fileno(self):
        if self._original is not None and hasattr(self._original, "fileno"):
            return self._original.fileno()
        raise OSError("geen fileno")


def install_logging(data_dir):
    """Activeer de getimestampte logging. Roep dit zo vroeg mogelijk in
    main() aan, vóór andere print()-statements."""
    global _log_path
    try:
        log_dir = os.path.join(data_dir, "logs")
        os.makedirs(log_dir, exist_ok=True)
        _log_path = os.path.join(log_dir, "booth.log")
        _open_fh()
        sys.stdout = _Tee(sys.__stdout__)
        sys.stderr = _Tee(sys.__stderr__)
    except Exception as e:
        # Logging mag de app nooit breken
        try:
            sys.__stdout__.write(f"[APP-LOGGER] init mislukt: {e}\n")
        except Exception:
            pass
