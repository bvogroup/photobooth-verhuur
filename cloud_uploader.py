"""Cloud upload-queue voor Linked-modus (gekoppelde events).

Achtergrond-thread die foto's uit een lokale queue uploadt naar Cloudflare R2
via pre-signed URLs verkregen van de Supabase edge functions in
clixibo-clone-project. Bij wifi-uitval blijven foto's in de queue staan;
exponential backoff retry zorgt voor automatische upload zodra connectie
terug is.

Geen R2-secrets in de client: de pre-signed URLs zijn scoped op de
booking-folder en 24 uur geldig.

Structuur op disk:
    Documents/Bootharoo/upload_queue/<booking_id>/
        pending/           foto's wachten op upload
        uploaded/          gelukt — bewaard 30 dagen voor zekerheid
        queue.json         manifest per file

queue.json:
    {
        "files": {
            "<filename>": {
                "state": "pending|uploading|uploaded|failed",
                "attempts": 0,
                "last_error": "",
                "next_retry_at": 0.0,        # monotonic seconds
                "uploaded_at": "ISO8601",    # bij uploaded
                "object_key": "<booking_id>/<filename>",
                "taken_at": "ISO8601",
                "size_bytes": 12345
            }
        }
    }
"""

import json
import os
import shutil
import threading
import time
from datetime import datetime, timezone
from typing import Optional

try:
    import requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False

from PyQt5.QtCore import QObject, pyqtSignal

import config


# ── Constants ─────────────────────────────────────────────────────────

_BACKOFF_SECONDS = [30, 60, 120, 300]  # tot 5 min cap
_TICK_INTERVAL = 5                      # hoe vaak de loop wakker wordt
_UPLOAD_TIMEOUT = 60                    # seconden per PUT
_CLEANUP_AFTER_DAYS = 30                # uploaded/ files na deze tijd weg
_TICKET_LEAD_TIME_HOURS = 1             # vernieuw URL als < 1u geldig


def _data_root() -> str:
    """Documents/Bootharoo/ — overleeft software-updates."""
    return getattr(config, 'DATA_DIR',
                   os.path.join(os.path.expanduser("~"), "Documents", "Bootharoo"))


def queue_dir(booking_id: str) -> str:
    d = os.path.join(_data_root(), "upload_queue", booking_id)
    os.makedirs(os.path.join(d, "pending"), exist_ok=True)
    os.makedirs(os.path.join(d, "uploaded"), exist_ok=True)
    return d


def _atomic_write_json(path: str, data: dict) -> None:
    """Atomic write: tmp file + rename — overleeft crash mid-write."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def _read_queue(booking_id: str) -> dict:
    path = os.path.join(queue_dir(booking_id), "queue.json")
    if not os.path.isfile(path):
        return {"files": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"files": {}}


def _write_queue(booking_id: str, q: dict) -> None:
    _atomic_write_json(os.path.join(queue_dir(booking_id), "queue.json"), q)


def _backoff(attempts: int) -> float:
    idx = min(attempts, len(_BACKOFF_SECONDS) - 1)
    return _BACKOFF_SECONDS[idx]


# ── Public API ────────────────────────────────────────────────────────

def enqueue(booking_id: str, file_path: str, taken_at: Optional[str] = None) -> Optional[str]:
    """Voeg een foto toe aan de upload-queue van een event.

    Verplaats de file naar pending/ en registreer in queue.json.
    Returns het pad in pending/ of None bij fout.
    """
    if not booking_id or not os.path.isfile(file_path):
        return None
    qd = queue_dir(booking_id)
    pending = os.path.join(qd, "pending")
    filename = os.path.basename(file_path)
    dest = os.path.join(pending, filename)

    # Conflict-resolutie: hang timestamp aan filename
    if os.path.exists(dest):
        base, ext = os.path.splitext(filename)
        filename = f"{base}_{int(time.time())}{ext}"
        dest = os.path.join(pending, filename)

    try:
        shutil.copy2(file_path, dest)
    except Exception as e:
        print(f"[QUEUE] Kon foto niet kopiëren naar pending: {e}")
        return None

    q = _read_queue(booking_id)
    q.setdefault("files", {})[filename] = {
        "state": "pending",
        "attempts": 0,
        "last_error": "",
        "next_retry_at": 0.0,
        "taken_at": taken_at or datetime.now(timezone.utc).isoformat(),
        "size_bytes": os.path.getsize(dest),
        "object_key": "",
    }
    _write_queue(booking_id, q)
    print(f"[QUEUE] Foto in queue: {booking_id}/{filename} ({q['files'][filename]['size_bytes']} bytes)")
    return dest


def get_status(booking_id: str) -> dict:
    """Tel aantallen voor UI-progress: total, uploaded, pending, failed, uploading."""
    q = _read_queue(booking_id)
    counts = {"total": 0, "pending": 0, "uploading": 0, "uploaded": 0, "failed": 0}
    next_retry = None
    for meta in q.get("files", {}).values():
        counts["total"] += 1
        state = meta.get("state", "pending")
        if state in counts:
            counts[state] += 1
        if state == "pending":
            nra = meta.get("next_retry_at", 0.0)
            if nra and (next_retry is None or nra < next_retry):
                next_retry = nra
    counts["next_retry_at"] = next_retry
    return counts


# ── Background uploader ──────────────────────────────────────────────

class UploadWorker(QObject):
    """Background-thread die queue.json processeert per booking.

    Eén worker per (booking_id, token) actief. Stop via .stop().
    Emit progress_changed na elke state-overgang voor UI-refresh.
    """

    progress_changed = pyqtSignal(dict)  # {total, uploaded, pending, failed, ...}

    def __init__(self, booking_id: str, token: str, parent=None):
        super().__init__(parent)
        self.booking_id = booking_id
        self.token = token
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # cache van pre-signed URLs per filename (om niet elke retry opnieuw te vragen)
        self._url_cache: dict = {}  # filename → {url, object_key, expires_at_mono}

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True,
            name=f"UploadWorker-{self.booking_id[:8]}",
        )
        self._thread.start()
        print(f"[UPLOAD] Worker gestart voor {self.booking_id}")

    def stop(self, timeout: float = 5.0):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            print(f"[UPLOAD] Worker gestopt voor {self.booking_id}")

    # ── intern ────────────────────────────────────────────────────────

    def _run(self):
        # Race-fix bij restart: alle "uploading" rows terug naar "pending"
        q = _read_queue(self.booking_id)
        changed = False
        for fname, meta in q.get("files", {}).items():
            if meta.get("state") == "uploading":
                meta["state"] = "pending"
                meta["last_error"] = "restart recovery"
                changed = True
        if changed:
            _write_queue(self.booking_id, q)

        # Hoofd-loop
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                print(f"[UPLOAD] Loop fout (continue): {e}")
            # Wachten in kleine stappen om snel stoppen te ondersteunen
            for _ in range(_TICK_INTERVAL):
                if self._stop.is_set():
                    return
                time.sleep(1)

    def _tick(self):
        q = _read_queue(self.booking_id)
        files = q.get("files", {})
        if not files:
            return

        now_mono = time.monotonic()
        progress = False

        for filename, meta in list(files.items()):
            if self._stop.is_set():
                break
            state = meta.get("state", "pending")
            if state in ("uploaded", "failed"):
                continue
            next_retry = meta.get("next_retry_at", 0.0)
            if next_retry and next_retry > now_mono:
                continue  # nog niet tijd voor retry

            # Probeer te uploaden
            ok, err = self._upload_one(filename, meta)
            files[filename] = meta  # in-place updated
            progress = True

            # Persist na elke file (kleine writes, maar veilig)
            _write_queue(self.booking_id, q)

        # Cleanup uploaded/ files ouder dan 30 dagen
        self._cleanup_old_uploaded()

        if progress:
            self.progress_changed.emit(get_status(self.booking_id))

    def _upload_one(self, filename: str, meta: dict) -> tuple[bool, str]:
        """Probeer 1 foto te uploaden. Returns (success, error_msg)."""
        pending_dir = os.path.join(queue_dir(self.booking_id), "pending")
        src = os.path.join(pending_dir, filename)
        if not os.path.isfile(src):
            meta["state"] = "failed"
            meta["last_error"] = "file missing in pending/"
            return False, meta["last_error"]

        # Mark uploading (race-fix bij crash mid-upload)
        meta["state"] = "uploading"

        # 1. Pre-signed URL ophalen (gecached)
        ticket = self._get_ticket(filename)
        if not ticket:
            meta["state"] = "pending"
            meta["attempts"] = meta.get("attempts", 0) + 1
            meta["last_error"] = "kon geen upload-ticket krijgen"
            meta["next_retry_at"] = time.monotonic() + _backoff(meta["attempts"])
            return False, meta["last_error"]

        # 2. Pre-check: bestaat al in R2? (HEAD via signed URL is lastig;
        #    we vertrouwen op UNIQUE in mark-photobooth-upload + idempotente PUT)

        # 3. PUT naar R2
        try:
            with open(src, "rb") as f:
                resp = requests.put(
                    ticket["upload_url"],
                    data=f,
                    headers={"Content-Type": ticket.get("content_type", "image/jpeg")},
                    timeout=_UPLOAD_TIMEOUT,
                )
        except Exception as e:
            meta["state"] = "pending"
            meta["attempts"] = meta.get("attempts", 0) + 1
            meta["last_error"] = f"network: {e}"
            meta["next_retry_at"] = time.monotonic() + _backoff(meta["attempts"])
            return False, meta["last_error"]

        if resp.status_code not in (200, 201):
            meta["state"] = "pending"
            meta["attempts"] = meta.get("attempts", 0) + 1
            meta["last_error"] = f"R2 PUT {resp.status_code}: {resp.text[:200]}"
            meta["next_retry_at"] = time.monotonic() + _backoff(meta["attempts"])
            # Permanent fail na 10 pogingen → markeer failed (handmatige check nodig)
            if meta["attempts"] >= 10:
                meta["state"] = "failed"
            return False, meta["last_error"]

        # 4. Succes: log naar mark-photobooth-upload + verplaats naar uploaded/
        meta["object_key"] = ticket["object_key"]
        try:
            self._mark_upload(meta["object_key"], meta.get("size_bytes", 0), meta.get("taken_at"))
        except Exception as e:
            # Niet kritiek — upload zelf is gelukt, alleen log mist
            print(f"[UPLOAD] mark-upload faalde voor {filename}: {e}")

        # Move naar uploaded/
        uploaded_dir = os.path.join(queue_dir(self.booking_id), "uploaded")
        dest = os.path.join(uploaded_dir, filename)
        try:
            shutil.move(src, dest)
        except Exception as e:
            print(f"[UPLOAD] Kon file niet verplaatsen: {e}")

        meta["state"] = "uploaded"
        meta["uploaded_at"] = datetime.now(timezone.utc).isoformat()
        meta["last_error"] = ""
        # URL cache mag weg
        self._url_cache.pop(filename, None)
        return True, ""

    def _get_ticket(self, filename: str) -> Optional[dict]:
        """Haal pre-signed PUT-URL op. Cache 23 uur lokaal."""
        now = time.monotonic()
        cached = self._url_cache.get(filename)
        if cached and cached.get("expires_at_mono", 0) > now + _TICKET_LEAD_TIME_HOURS * 3600:
            return cached

        url = f"{config.SUPABASE_URL.rstrip('/')}/functions/v1/get-photobooth-r2-ticket"
        try:
            r = requests.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "apikey": config.SUPABASE_ANON_KEY,
                    "Authorization": f"Bearer {config.SUPABASE_ANON_KEY}",
                },
                json={"token": self.token, "filename": filename, "content_type": "image/jpeg"},
                timeout=15,
            )
        except Exception as e:
            print(f"[UPLOAD] Ticket-request fout: {e}")
            return None

        if r.status_code != 200:
            print(f"[UPLOAD] Ticket-status {r.status_code}: {r.text[:200]}")
            return None

        data = r.json()
        # 24u geldig; we cachen monotonic-aware
        data["expires_at_mono"] = now + 23 * 3600
        self._url_cache[filename] = data
        return data

    def _mark_upload(self, object_key: str, size: int, taken_at: Optional[str]) -> None:
        url = f"{config.SUPABASE_URL.rstrip('/')}/functions/v1/mark-photobooth-upload"
        try:
            r = requests.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "apikey": config.SUPABASE_ANON_KEY,
                    "Authorization": f"Bearer {config.SUPABASE_ANON_KEY}",
                },
                json={
                    "token": self.token,
                    "object_key": object_key,
                    "size_bytes": size,
                    "taken_at": taken_at,
                },
                timeout=10,
            )
            if r.status_code != 200:
                print(f"[UPLOAD] mark-upload {r.status_code}: {r.text[:120]}")
        except Exception as e:
            print(f"[UPLOAD] mark-upload exception: {e}")

    def _cleanup_old_uploaded(self) -> None:
        """Verwijder uploaded/ files ouder dan 30 dagen — disk-hygiene."""
        uploaded_dir = os.path.join(queue_dir(self.booking_id), "uploaded")
        cutoff = time.time() - _CLEANUP_AFTER_DAYS * 86400
        try:
            for name in os.listdir(uploaded_dir):
                p = os.path.join(uploaded_dir, name)
                try:
                    if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                        os.remove(p)
                        print(f"[QUEUE] Old upload opgeruimd: {name}")
                except Exception:
                    pass
        except FileNotFoundError:
            pass


# ── Singleton-stijl helper voor de photobooth-app ─────────────────────

_active_workers: dict = {}  # booking_id → UploadWorker
_workers_lock = threading.Lock()


def start_worker(booking_id: str, token: str) -> UploadWorker:
    """Start (of return bestaande) worker voor dit event."""
    with _workers_lock:
        existing = _active_workers.get(booking_id)
        if existing:
            return existing
        w = UploadWorker(booking_id, token)
        w.start()
        _active_workers[booking_id] = w
        return w


def stop_worker(booking_id: str) -> None:
    with _workers_lock:
        w = _active_workers.pop(booking_id, None)
    if w:
        w.stop()


def stop_all_workers() -> None:
    with _workers_lock:
        workers = list(_active_workers.values())
        _active_workers.clear()
    for w in workers:
        w.stop()
