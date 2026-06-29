"""Gebatchte cloud-upload van de photobooth-logs.

Registreert een sink bij app_logger, buffert de geprefixte logregels en
pusht ze elke config.CLOUD_LOG_INTERVAL_SEC seconden in één batch naar de
edge function `ingest-photobooth-log` in het clixibo/Lovable-project.

Elke batch draagt de context mee zodat in het Lovable-project zichtbaar is
welke booth (serienummer) bij welke klant/event draaide:
    serial_number, hostname, event_id, customer, brand, token

Robuust:
  - Sink-callback is licht (alleen bufferen) → geen vertraging van print().
  - Mislukte upload → regels gaan terug in de buffer (retry volgende ronde).
  - Buffer is gecapt (_MAX_BUFFER) zodat offline draaien het geheugen niet
    laat vollopen; oudste regels vallen weg (ze staan al in booth.log).
"""

import socket
import threading
import time

try:
    import requests
except Exception:
    requests = None

import config

_MAX_BUFFER = 3000

_buffer = []
_buf_lock = threading.Lock()

_ctx = {"serial": "", "event_id": "", "customer": "", "brand": "hippe", "token": ""}
_ctx_lock = threading.Lock()

# Laatste status-snapshot (rijke heartbeat). Wordt op de main thread
# gebouwd (Qt-state) en hier thread-safe opgeslagen; elke flush stuurt 'm mee.
_status = None
_status_lock = threading.Lock()

_worker = None
_running = False
_started = False

try:
    _HOSTNAME = socket.gethostname()
except Exception:
    _HOSTNAME = "onbekend"


def update_context(serial=None, event_id=None, customer=None, brand=None, token=None):
    """Werk de meegestuurde context bij. Photobooth roept dit aan bij
    startup en wanneer serienummer/brand/koppeling verandert."""
    with _ctx_lock:
        if serial is not None:
            _ctx["serial"] = serial or ""
        if event_id is not None:
            _ctx["event_id"] = event_id or ""
        if customer is not None:
            _ctx["customer"] = customer or ""
        if brand is not None:
            _ctx["brand"] = brand or "hippe"
        if token is not None:
            _ctx["token"] = token or ""


def update_status(snapshot):
    """Zet de laatste status-snapshot (dict). Wordt elke flush meegestuurd
    als heartbeat. Photobooth roept dit elke ~20s aan vanaf de main thread."""
    global _status
    if not isinstance(snapshot, dict):
        return
    with _status_lock:
        _status = snapshot


def _on_log_line(formatted):
    """Sink vanuit app_logger — alleen bufferen, snel en zonder print()."""
    with _buf_lock:
        _buffer.append(formatted)
        if len(_buffer) > _MAX_BUFFER:
            del _buffer[0:len(_buffer) - _MAX_BUFFER]


def start():
    """Start de upload-worker (idempotent)."""
    global _worker, _running, _started
    if _started:
        return
    if not getattr(config, "CLOUD_LOG_ENABLED", True):
        return
    if requests is None:
        return
    _started = True
    try:
        from app_logger import register_sink
        register_sink(_on_log_line)
    except Exception:
        return
    _running = True
    _worker = threading.Thread(target=_loop, daemon=True, name="LogUploader")
    _worker.start()


def stop():
    global _running
    _running = False


def _loop():
    interval = max(5, int(getattr(config, "CLOUD_LOG_INTERVAL_SEC", 20)))
    while _running:
        # In kleine stapjes slapen zodat stop() snel reageert.
        for _ in range(interval):
            if not _running:
                break
            time.sleep(1)
        try:
            _flush()
        except Exception:
            pass


def _requeue(lines):
    """Mislukte upload → regels vooraan terug in de buffer voor retry."""
    with _buf_lock:
        _buffer[0:0] = lines
        if len(_buffer) > _MAX_BUFFER:
            del _buffer[0:len(_buffer) - _MAX_BUFFER]


def _flush():
    with _buf_lock:
        lines = list(_buffer)
        _buffer.clear()

    with _status_lock:
        status = dict(_status) if _status else None

    # Niets te sturen? (geen logregels én geen status-heartbeat)
    if not lines and not status:
        return

    with _ctx_lock:
        ctx = dict(_ctx)

    url = f"{config.CLIXIBO_SUPABASE_URL.rstrip('/')}/functions/v1/ingest-photobooth-log"
    payload = {
        "serial_number": ctx.get("serial", ""),
        "hostname": _HOSTNAME,
        "event_id": ctx.get("event_id", ""),
        "customer": ctx.get("customer", ""),
        "brand": ctx.get("brand", "hippe"),
        "token": ctx.get("token", ""),
        "lines": lines,
        "status": status,
    }
    try:
        resp = requests.post(
            url,
            headers={
                "Content-Type": "application/json",
                "apikey": config.CLIXIBO_ANON_KEY,
                "Authorization": f"Bearer {config.CLIXIBO_ANON_KEY}",
            },
            json=payload,
            timeout=15,
        )
        if resp.status_code != 200:
            _requeue(lines)
    except Exception:
        _requeue(lines)
