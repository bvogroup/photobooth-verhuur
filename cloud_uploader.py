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
                "state": "pending|uploading|uploaded|failed|missing",
                "attempts": 0,
                "last_error": "",
                "next_retry_at": 0.0,        # wall-clock (time.time) — overleeft reboot
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

# Backoff: kort starten, snel terug naar 60s cap zodat we elke minuut
# blijven proberen tot 't lukt. Geen "geef het op na X pogingen".
_BACKOFF_NETWORK = [10, 20, 40, 60]      # netwerk-fout: snelle retry
_BACKOFF_SERVER = [30, 60, 60, 60]       # server-fout: max 1 min cap
_TICK_INTERVAL = 5                       # hoe vaak de loop wakker wordt
_UPLOAD_TIMEOUT = 60                     # seconden per PUT
_CLEANUP_AFTER_DAYS = 30                 # uploaded/ files na deze tijd weg
_TICKET_LEAD_TIME_HOURS = 1              # vernieuw URL als < 1u geldig

# Per-booking lock om race tussen enqueue() en worker te voorkomen.
# enqueue() schrijft nieuwe entries; worker leest queue + uploadt + schrijft
# resultaat terug. Zonder lock kan een mid-tick enqueue worden overschreven.
_queue_locks: dict = {}
_queue_locks_lock = threading.Lock()


def _get_queue_lock(booking_id: str) -> threading.Lock:
    with _queue_locks_lock:
        if booking_id not in _queue_locks:
            _queue_locks[booking_id] = threading.Lock()
        return _queue_locks[booking_id]


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


# ── Token-persistentie per queue ──────────────────────────────────────
# Het booking-token leefde voorheen alleen in het event-JSON (linked_token).
# Bij her-koppelen aan een nieuwe booking werd dat veld overschreven →
# wachtrijen van oudere events strandden voor eeuwig ("GEEN token").
# Door het token bij de wachtrij zelf op te slaan blijft elke queue
# zelfstandig uploadbaar, ongeacht welk event nu gekoppeld is.

def save_queue_token(booking_id: str, token: str, label: str = "") -> None:
    """Bewaar het booking-token in token.json naast de queue (idempotent)."""
    if not booking_id or not token:
        return
    try:
        _atomic_write_json(
            os.path.join(queue_dir(booking_id), "token.json"),
            {
                "token": token,
                "booking_label": label,
                "saved_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as e:
        print(f"[QUEUE] token.json schrijven mislukt voor {booking_id}: {e}")


def read_queue_token(booking_id: str) -> dict:
    """Lees token.json. Returns {"token": "", "booking_label": ""} bij afwezig."""
    path = os.path.join(_data_root(), "upload_queue", booking_id, "token.json")
    if not os.path.isfile(path):
        return {"token": "", "booking_label": ""}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {
            "token": data.get("token", "") or "",
            "booking_label": data.get("booking_label", "") or "",
        }
    except Exception:
        return {"token": "", "booking_label": ""}


# ── Mark-upload (registratie in klantenportaal-DB) ────────────────────

def post_mark_upload(token: str, object_key: str, size: int,
                     taken_at: Optional[str],
                     session_id: str = "", kind: str = "") -> bool:
    """Meld één geüploade foto aan bij mark-photobooth-upload.

    Idempotent server-side (upsert op booking_id+storage_path), dus
    veilig om te herhalen. Returns True bij HTTP 200.
    """
    url = f"{config.CLIXIBO_SUPABASE_URL.rstrip('/')}/functions/v1/mark-photobooth-upload"
    payload = {
        "token": token,
        "object_key": object_key,
        "size_bytes": size,
        "taken_at": taken_at,
    }
    # Album-metadata: alleen meesturen indien bekend (backwards-compat).
    if session_id:
        payload["session_id"] = session_id
    if kind:
        payload["kind"] = kind
    try:
        r = requests.post(
            url,
            headers={
                "Content-Type": "application/json",
                "apikey": config.CLIXIBO_ANON_KEY,
                "Authorization": f"Bearer {config.CLIXIBO_ANON_KEY}",
            },
            json=payload,
            timeout=10,
        )
        if r.status_code != 200:
            print(f"[UPLOAD] mark-upload {r.status_code}: {r.text[:120]}")
            return False
        return True
    except Exception as e:
        print(f"[UPLOAD] mark-upload exception: {e}")
        return False


def backfill_marks(booking_id: str, token: str) -> int:
    """Meld alle reeds-geüploade entries van deze queue alsnog aan.

    Achtergrond: de hippe_booking_photos tabel bestond aanvankelijk niet
    op de server, waardoor elke mark-upload stilletjes faalde — de foto's
    staan wél in R2 maar verschenen nooit in het digitale album. Deze
    backfill loopt alle 'uploaded' entries opnieuw langs (server-upsert =
    idempotent) en zet daarna een flag zodat 't maar één keer gebeurt.

    Returns aantal succesvol aangemelde foto's. Bij netwerkfouten wordt
    de flag NIET gezet zodat de volgende watchdog-tick het opnieuw probeert.
    """
    if not booking_id or not token or not _REQUESTS_AVAILABLE:
        return 0
    lock = _get_queue_lock(booking_id)
    with lock:
        q = _read_queue(booking_id)
    if q.get("marks_backfilled_at"):
        return 0
    todo = [
        (fname, meta) for fname, meta in q.get("files", {}).items()
        if meta.get("state") == "uploaded" and meta.get("object_key")
    ]
    if not todo:
        # Niks te backfillen — flag zetten zodat we niet blijven scannen
        with lock:
            q = _read_queue(booking_id)
            q["marks_backfilled_at"] = datetime.now(timezone.utc).isoformat()
            _write_queue(booking_id, q)
        return 0

    print(f"[BACKFILL] {booking_id}: {len(todo)} geüploade foto's aanmelden bij portaal")
    ok_count = 0
    failed = 0
    for fname, meta in todo:
        ok = post_mark_upload(
            token, meta["object_key"], meta.get("size_bytes", 0),
            meta.get("taken_at"),
            session_id=meta.get("session_id", ""),
            kind=meta.get("kind", ""),
        )
        if ok:
            ok_count += 1
        else:
            failed += 1
        time.sleep(0.15)  # niet de edge function platdrukken

    if failed == 0:
        with lock:
            q = _read_queue(booking_id)
            q["marks_backfilled_at"] = datetime.now(timezone.utc).isoformat()
            _write_queue(booking_id, q)
        print(f"[BACKFILL] {booking_id}: klaar — {ok_count} foto's aangemeld")
    else:
        print(f"[BACKFILL] {booking_id}: {ok_count} ok, {failed} mislukt — "
              f"retry bij volgende watchdog-tick")
    return ok_count


def _backoff(attempts: int, kind: str = "server") -> float:
    """Bepaal retry-wachttijd in seconden.

    kind="network" → snel terug (10/20/40/60s) — wifi-glitch is meestal kort.
    kind="server"  → 30→60s en blijft 60s — server-fout vereist niet veel
                     druk maar wel constante poging.
    Beide cap op 60s zodat we ALTIJD minstens 1× per minuut proberen.
    """
    table = _BACKOFF_NETWORK if kind == "network" else _BACKOFF_SERVER
    idx = min(max(0, attempts - 1), len(table) - 1)
    return float(table[idx])


# ── Public API ────────────────────────────────────────────────────────

def enqueue(booking_id: str, file_path: str, taken_at: Optional[str] = None,
            session_id: str = "", kind: str = "") -> Optional[str]:
    """Voeg een foto toe aan de upload-queue van een event.

    Verplaats de file naar pending/ en registreer in queue.json. Atomic onder
    booking-lock zodat een tegelijkertijd-draaiende worker geen entries verliest.
    Returns het pad in pending/ of None bij fout.

    session_id + kind ('photo'/'strip'/'gif') worden per file bewaard en
    bij mark-upload meegestuurd zodat het digitale album in het klanten-
    portaal foto's per sessie kan groeperen met de strip als 'held'.
    """
    if not booking_id or not os.path.isfile(file_path):
        return None
    qd = queue_dir(booking_id)
    pending = os.path.join(qd, "pending")
    filename = os.path.basename(file_path)
    dest = os.path.join(pending, filename)

    # Conflict-resolutie: hang timestamp aan filename als al bestaat
    if os.path.exists(dest):
        base, ext = os.path.splitext(filename)
        filename = f"{base}_{int(time.time())}{ext}"
        dest = os.path.join(pending, filename)

    try:
        shutil.copy2(file_path, dest)
    except Exception as e:
        print(f"[QUEUE] Kon foto niet kopiëren naar pending: {e}")
        return None

    lock = _get_queue_lock(booking_id)
    with lock:
        q = _read_queue(booking_id)
        q.setdefault("files", {})[filename] = {
            "state": "pending",
            "attempts": 0,
            "last_error": "",
            "next_retry_at": 0.0,
            "taken_at": taken_at or datetime.now(timezone.utc).isoformat(),
            "size_bytes": os.path.getsize(dest),
            "object_key": "",
            "session_id": session_id or "",
            "kind": kind or "",
        }
        _write_queue(booking_id, q)
    print(f"[QUEUE] Foto in queue: {booking_id}/{filename} ({q['files'][filename]['size_bytes']} bytes)")
    return dest


def get_status(booking_id: str) -> dict:
    """Tel aantallen voor UI-progress: total, uploaded, pending, failed, uploading.

    Onder de queue-lock: een ongelockte open() terwijl een writer
    os.replace() doet geeft op Windows een PermissionError bij de writer
    (geen FILE_SHARE_DELETE) — dat kon enqueue() laten falen of zelfs de
    worker-recovery doden.
    """
    with _get_queue_lock(booking_id):
        q = _read_queue(booking_id)
    counts = {"total": 0, "pending": 0, "uploading": 0, "uploaded": 0,
              "failed": 0, "missing": 0}
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
        self._url_cache: dict = {}  # filename → {url, object_key, expires_at_wall}

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
        # Recovery bij startup: race-fix + orphan-pickup.
        # In try/except: een fout hier (bv. PermissionError op queue.json)
        # mag de thread NIET doden — een dode worker blijft geregistreerd
        # in _active_workers en blokkeerde voorheen de watchdog permanent.
        try:
            lock = _get_queue_lock(self.booking_id)
            with lock:
                q = _read_queue(self.booking_id)
                files = q.setdefault("files", {})

                # 1) "uploading" rows die nooit klaar kwamen → terug naar pending
                for fname, meta in files.items():
                    if meta.get("state") == "uploading":
                        meta["state"] = "pending"
                        meta["last_error"] = "restart recovery"

                # 2) Orphan files in pending/ folder die NIET in queue.json staan
                #    (bv. door race-condition tijdens een eerdere sessie weggevallen)
                pending_dir = os.path.join(queue_dir(self.booking_id), "pending")
                try:
                    disk_files = set(os.listdir(pending_dir))
                except FileNotFoundError:
                    disk_files = set()
                for disk_fname in disk_files:
                    if disk_fname not in files:
                        full = os.path.join(pending_dir, disk_fname)
                        if not os.path.isfile(full):
                            continue
                        try:
                            size = os.path.getsize(full)
                        except OSError:
                            size = 0
                        files[disk_fname] = {
                            "state": "pending",
                            "attempts": 0,
                            "last_error": "orphan recovered",
                            "next_retry_at": 0.0,
                            "taken_at": datetime.now(timezone.utc).isoformat(),
                            "size_bytes": size,
                            "object_key": "",
                        }
                        print(f"[QUEUE] Orphan teruggevonden: {disk_fname}")

                _write_queue(self.booking_id, q)
        except Exception as e:
            print(f"[UPLOAD] Recovery-fout (continue naar hoofdloop): {e}")

        # Hoofd-loop
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                print(f"[UPLOAD] Loop fout (continue): {e}")
            for _ in range(_TICK_INTERVAL):
                if self._stop.is_set():
                    return
                time.sleep(1)

    def _tick(self):
        """Eén ronde: lees queue, upload pending files, schrijf resultaten terug.

        Schrijft per file UNDER LOCK + re-read zodat parallelle enqueue() calls
        niet verloren gaan (race-fix v1.99.11).
        """
        lock = _get_queue_lock(self.booking_id)
        with lock:
            q = _read_queue(self.booking_id)
            files = dict(q.get("files", {}))  # snapshot voor iteratie

        if not files:
            return

        # WALL-CLOCK (time.time) ipv monotonic — overleeft PC reboot.
        # Eerdere bug: monotonic reset bij boot, dus alle next_retry_at
        # waardes zaten in de "toekomst" → uploads bleven eeuwig gepauzeerd.
        now_wall = time.time()
        progress = False

        for filename, meta in files.items():
            if self._stop.is_set():
                break
            state = meta.get("state", "pending")
            # 'failed' wordt na de fix NIET meer gezet (we blijven retryen)
            # maar we respecteren oude failed-entries en zetten ze
            # automatisch terug naar pending zodat ze alsnog kans krijgen.
            # 'missing' = bronbestand weg uit pending/ — terminaal, skippen
            # (voorheen flipte dit elke 5s failed→pending→failed = livelock).
            if state in ("uploaded", "missing"):
                continue
            if state == "failed":
                meta["state"] = "pending"
                meta["attempts"] = 0
                meta["next_retry_at"] = 0.0
                state = "pending"
            next_retry = meta.get("next_retry_at", 0.0)
            if next_retry and next_retry > now_wall:
                continue  # nog niet tijd voor retry

            # Probeer te uploaden (kan minuten duren)
            ok, err = self._upload_one(filename, meta)
            progress = True

            # Persist resultaat met merge: re-read queue, update ALLEEN deze file,
            # behoud alle andere entries (incl. files toegevoegd tijdens upload).
            with lock:
                current = _read_queue(self.booking_id)
                current_files = current.setdefault("files", {})
                current_files[filename] = meta
                _write_queue(self.booking_id, current)

        # Cleanup uploaded/ files ouder dan 30 dagen
        self._cleanup_old_uploaded()

        if progress:
            self.progress_changed.emit(get_status(self.booking_id))

    def _upload_one(self, filename: str, meta: dict) -> tuple[bool, str]:
        """Probeer 1 foto te uploaden. Returns (success, error_msg)."""
        pending_dir = os.path.join(queue_dir(self.booking_id), "pending")
        src = os.path.join(pending_dir, filename)
        if not os.path.isfile(src):
            # Terminaal: bestand is weg (handmatig opgeruimd?). NIET als
            # 'failed' markeren — dat werd elke tick gereset naar pending
            # en gaf een eeuwige retry-livelock op een onbestaand bestand.
            meta["state"] = "missing"
            meta["last_error"] = "file missing in pending/"
            return False, meta["last_error"]

        # Mark uploading (race-fix bij crash mid-upload)
        meta["state"] = "uploading"

        # 1. Pre-signed URL ophalen (gecached)
        ticket, ticket_err = self._get_ticket(filename)
        if not ticket:
            meta["state"] = "pending"
            meta["attempts"] = meta.get("attempts", 0) + 1
            meta["last_error"] = f"ticket: {ticket_err}"
            # Onderscheid network vs server — 401/403 (auth) blijven we proberen
            # want token kan terugkomen na re-couple; geen permanent fail.
            kind = "network" if "network" in (ticket_err or "").lower() else "server"
            meta["next_retry_at"] = time.time() + _backoff(meta["attempts"], kind)
            return False, meta["last_error"]

        # 2. Pre-check: bestaat al in R2? (HEAD via signed URL is lastig;
        #    we vertrouwen op UNIQUE in mark-photobooth-upload + idempotente PUT)

        # 3. PUT naar R2
        network_err = False
        try:
            with open(src, "rb") as f:
                resp = requests.put(
                    ticket["upload_url"],
                    data=f,
                    headers={"Content-Type": ticket.get("content_type", "image/jpeg")},
                    timeout=_UPLOAD_TIMEOUT,
                )
        except (requests.ConnectionError, requests.Timeout) as e:
            network_err = True
            meta["state"] = "pending"
            meta["attempts"] = meta.get("attempts", 0) + 1
            meta["last_error"] = f"network: {e}"
            meta["next_retry_at"] = time.time() + _backoff(meta["attempts"], "network")
            return False, meta["last_error"]
        except Exception as e:
            meta["state"] = "pending"
            meta["attempts"] = meta.get("attempts", 0) + 1
            meta["last_error"] = f"unexpected: {e}"
            meta["next_retry_at"] = time.time() + _backoff(meta["attempts"], "server")
            return False, meta["last_error"]

        if resp.status_code not in (200, 201):
            meta["state"] = "pending"
            meta["attempts"] = meta.get("attempts", 0) + 1
            meta["last_error"] = f"R2 PUT {resp.status_code}: {resp.text[:200]}"
            meta["next_retry_at"] = time.time() + _backoff(meta["attempts"], "server")
            # GEEN permanent fail meer — blijft retryen elke minuut tot het
            # lukt. Operator kan via Settings → Geavanceerd handmatig
            # "Probeer alles opnieuw" doen, of failed wegsmijten.
            return False, meta["last_error"]

        # 4. Succes: log naar mark-photobooth-upload + verplaats naar uploaded/
        meta["object_key"] = ticket["object_key"]
        try:
            self._mark_upload(
                meta["object_key"], meta.get("size_bytes", 0),
                meta.get("taken_at"),
                session_id=meta.get("session_id", ""),
                kind=meta.get("kind", ""),
            )
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

    def _get_ticket(self, filename: str) -> tuple[Optional[dict], str]:
        """Haal pre-signed PUT-URL op. Cache 23 uur lokaal.

        Returns (ticket_dict, error_str). Bij succes: (dict, "").
        Bij faal: (None, "network: ..." | "server: ..." | "auth: ...").
        """
        now_wall = time.time()
        cached = self._url_cache.get(filename)
        if cached and cached.get("expires_at_wall", 0) > now_wall + _TICKET_LEAD_TIME_HOURS * 3600:
            return cached, ""

        url = f"{config.CLIXIBO_SUPABASE_URL.rstrip('/')}/functions/v1/get-photobooth-r2-ticket"
        try:
            r = requests.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "apikey": config.CLIXIBO_ANON_KEY,
                    "Authorization": f"Bearer {config.CLIXIBO_ANON_KEY}",
                },
                json={"token": self.token, "filename": filename, "content_type": "image/jpeg"},
                timeout=15,
            )
        except (requests.ConnectionError, requests.Timeout) as e:
            return None, f"network: {e}"
        except Exception as e:
            return None, f"unexpected: {e}"

        if r.status_code in (401, 403):
            # Auth-fout — token mogelijk verlopen / event ontkoppeld in portal.
            # We blijven retryen want token kan via re-couple weer geldig zijn.
            return None, f"auth: token afgekeurd ({r.status_code})"
        if r.status_code != 200:
            return None, f"server: status {r.status_code}: {r.text[:120]}"

        try:
            data = r.json()
        except Exception as e:
            return None, f"server: ongeldige response ({e})"
        # 24u geldig; we cachen wall-clock based zodat reboot 'm niet wegschiet
        data["expires_at_wall"] = now_wall + 23 * 3600
        self._url_cache[filename] = data
        return data, ""

    def _mark_upload(self, object_key: str, size: int, taken_at: Optional[str],
                     session_id: str = "", kind: str = "") -> None:
        ok = post_mark_upload(self.token, object_key, size, taken_at,
                              session_id=session_id, kind=kind)
        if not ok:
            print(f"[UPLOAD] mark-upload faalde voor {object_key}")

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
    """Start (of return bestaande) worker voor dit event.

    Twee belangrijke randgevallen:
    - Nieuw token (her-koppelen): de bestaande worker krijgt het nieuwe
      token + lege URL-cache. Voorheen bleef het oude (dode) token in
      gebruik tot een app-herstart → uploads faalden eeuwig.
    - Dode thread: een worker waarvan de thread gecrasht is blijft
      geregistreerd staan; die vervangen we door een verse.
    """
    if not _REQUESTS_AVAILABLE:
        print("[UPLOAD] requests-package mist — uploads niet mogelijk")
        return None
    # Vangnet: token altijd bij de queue persisteren zodat deze wachtrij
    # ook na her-koppelen aan een ander event uploadbaar blijft.
    save_queue_token(booking_id, token)
    with _workers_lock:
        existing = _active_workers.get(booking_id)
        if existing:
            thread_alive = (existing._thread is not None
                            and existing._thread.is_alive())
            if existing.token != token:
                print(f"[UPLOAD] Nieuw token voor {booking_id} — worker bijgewerkt")
                existing.token = token
                existing._url_cache.clear()
            if thread_alive:
                return existing
            # Thread dood (crash in een eerdere run) → verse worker
            print(f"[UPLOAD] Worker-thread voor {booking_id} was dood — herstart")
            _active_workers.pop(booking_id, None)
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


# ── Force-retry helpers (operator-actie via Settings UI) ─────────────

def force_retry_all(booking_id: str) -> dict:
    """Reset alle pending+failed entries naar 'klaar voor onmiddellijke retry'.

    Schaalt failed → pending, attempts → 0, next_retry_at → 0. De
    actieve worker pakt ze direct op bij z'n volgende tick (binnen 5s).
    Returns telling van wat is gereset: {failed_reset, pending_reset}.
    """
    lock = _get_queue_lock(booking_id)
    with lock:
        q = _read_queue(booking_id)
        files = q.setdefault("files", {})
        n_failed = 0
        n_pending = 0
        for fname, meta in files.items():
            state = meta.get("state", "pending")
            if state == "failed":
                meta["state"] = "pending"
                meta["attempts"] = 0
                meta["last_error"] = "manueel gereset door operator"
                meta["next_retry_at"] = 0.0
                n_failed += 1
            elif state == "pending" and meta.get("next_retry_at", 0):
                meta["attempts"] = 0
                meta["next_retry_at"] = 0.0
                meta["last_error"] = "manueel gereset door operator"
                n_pending += 1
        _write_queue(booking_id, q)
    return {"failed_reset": n_failed, "pending_reset": n_pending}


def clear_uploaded(booking_id: str) -> int:
    """Verwijder alle 'uploaded'-entries uit queue.json + uploaded/ files.

    Returns aantal verwijderd. Bedoeld voor schoonmaak via Settings.
    """
    qd = queue_dir(booking_id)
    uploaded_dir = os.path.join(qd, "uploaded")
    lock = _get_queue_lock(booking_id)
    n = 0
    with lock:
        q = _read_queue(booking_id)
        files = q.setdefault("files", {})
        to_remove = [f for f, m in files.items() if m.get("state") == "uploaded"]
        for f in to_remove:
            files.pop(f, None)
            try:
                os.remove(os.path.join(uploaded_dir, f))
            except FileNotFoundError:
                pass
            except Exception as e:
                print(f"[QUEUE] Kon uploaded file niet verwijderen: {e}")
            n += 1
        _write_queue(booking_id, q)
    return n


# ── Token-registry: vindt tokens uit events JSONs ────────────────────

def discover_pending_uploads(events_dir: str) -> dict:
    """Scan alle upload_queue/<booking_id>/ mappen en map ze naar tokens
    uit Documents/Bootharoo/events/*.json.

    Returns dict: {booking_id: {token, total, pending, failed, uploaded}}
    voor alle queues die op disk staan. Inclusief queues waar GEEN token
    voor gevonden is — die kunnen we niet uploaden tot er een coupling
    is, maar we tellen ze wel zodat de UI duidelijk maakt wat er stuck zit.
    """
    upload_root = os.path.join(_data_root(), "upload_queue")
    if not os.path.isdir(upload_root):
        return {}

    # Stap 1: verzamel tokens uit alle event-JSONs (linked_token velden)
    token_map = {}  # booking_id → token
    if os.path.isdir(events_dir):
        for f in os.listdir(events_dir):
            if not f.lower().endswith(".json"):
                continue
            try:
                with open(os.path.join(events_dir, f), "r", encoding="utf-8") as fp:
                    ev = json.load(fp)
                bid = ev.get("linked_booking_id") or ""
                tok = ev.get("linked_token") or ""
                if bid and tok:
                    token_map[bid] = tok
            except Exception:
                continue

    # Stap 2: scan upload_queue/<booking_id>/ folders
    result = {}
    for entry in os.listdir(upload_root):
        bdir = os.path.join(upload_root, entry)
        if not os.path.isdir(bdir):
            continue
        counts = get_status(entry)
        if counts["total"] == 0:
            # queue.json leeg/corrupt — maar staan er nog foto's in
            # pending/? Dan moet er tóch een worker komen: die bouwt via
            # z'n orphan-recovery het manifest opnieuw op. Zonder deze
            # check bleven zulke foto's voor eeuwig onge-upload.
            pending_dir = os.path.join(bdir, "pending")
            try:
                orphans = [f for f in os.listdir(pending_dir)
                           if os.path.isfile(os.path.join(pending_dir, f))]
            except FileNotFoundError:
                orphans = []
            if not orphans:
                continue
            print(f"[WATCHDOG] {entry}: queue.json leeg maar "
                  f"{len(orphans)} foto('s) in pending/ — orphan recovery")
            counts["total"] = len(orphans)
            counts["pending"] = len(orphans)
        # Token: eerst uit events/*.json (actueel gekoppeld), anders uit
        # token.json bij de queue zelf (bewaard bij eerdere koppeling) —
        # zo blijven wachtrijen van oude events gewoon uploaden zonder
        # her-koppelen.
        tok_info = read_queue_token(entry)
        result[entry] = {
            "token": token_map.get(entry, "") or tok_info["token"],
            "booking_label": tok_info["booking_label"],
            **counts,
        }
    return result


# ── Watchdog: altijd-actieve global retry-loop ───────────────────────

class CloudWatchdog:
    """Globale background-service die ALLE pending uploads in de gaten houdt.

    Start bij app-boot. Scant elke 30 sec de upload_queue/ folder; voor
    elke booking_id met een queue én een bekend token start hij een
    UploadWorker (of laat de bestaande draaien). Zo blijven oude
    queues uploaden ook als het event nooit meer wordt herkoppeld.

    Eén globaal singleton: gebruik get_watchdog() / start_watchdog().
    """

    def __init__(self, events_dir: str, scan_interval: float = 30.0):
        self._events_dir = events_dir
        self._scan_interval = scan_interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="CloudWatchdog")
        self._thread.start()
        print("[WATCHDOG] Cloud-watchdog gestart (scan elke 30s)")

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5.0)
        print("[WATCHDOG] Gestopt")

    def _run(self):
        # Eerste scan onmiddellijk, daarna elke `scan_interval` seconden
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                print(f"[WATCHDOG] Loop-fout: {e}")
            # Sleep maar reageer snel op stop
            for _ in range(int(self._scan_interval)):
                if self._stop.is_set():
                    return
                time.sleep(1)

    def _tick(self):
        """Eén ronde: vind nieuwe queues, start workers, log stale."""
        snapshots = discover_pending_uploads(self._events_dir)
        # Alleen LEVENDE workers tellen als actief — een gecrashte
        # worker-thread blijft geregistreerd staan en blokkeerde
        # voorheen de booking permanent (start_worker vervangt 'm nu).
        alive_keys = set()
        with _workers_lock:
            for bid, w in _active_workers.items():
                if w._thread is not None and w._thread.is_alive():
                    alive_keys.add(bid)

        for booking_id, info in snapshots.items():
            token = info.get("token", "")
            needs_work = (info["pending"] > 0
                          or info.get("failed", 0) > 0
                          # legacy queues met persisted 'uploading' states
                          # moeten ook een worker krijgen (recovery reset
                          # ze naar pending)
                          or info.get("uploading", 0) > 0)
            if not token:
                if needs_work:
                    print(f"[WATCHDOG] {booking_id}: "
                          f"{info['pending']}p/{info.get('failed', 0)}f maar GEEN token "
                          f"in events/. Koppel event opnieuw om te uploaden.")
                continue
            # Backfill: meld reeds-geüploade foto's alsnog aan bij het
            # album (eenmalig per queue — flag in queue.json). Ook voor
            # volledig-geüploade queues waar geen worker meer voor start.
            try:
                backfill_marks(booking_id, token)
            except Exception as e:
                print(f"[WATCHDOG] Backfill-fout {booking_id}: {e}")
            # Levende worker draait al? Skip.
            if booking_id in alive_keys:
                continue
            if needs_work:
                print(f"[WATCHDOG] Start worker voor {booking_id} "
                      f"({info['pending']}p/{info.get('failed', 0)}f"
                      f"/{info.get('uploading', 0)}u)")
                start_worker(booking_id, token)


_watchdog_instance: Optional[CloudWatchdog] = None
_watchdog_lock = threading.Lock()


def start_watchdog(events_dir: str) -> CloudWatchdog:
    """Singleton — start de globale watchdog. Idempotent."""
    global _watchdog_instance
    with _watchdog_lock:
        if _watchdog_instance is None:
            _watchdog_instance = CloudWatchdog(events_dir)
            _watchdog_instance.start()
        return _watchdog_instance


def stop_watchdog():
    global _watchdog_instance
    with _watchdog_lock:
        if _watchdog_instance is not None:
            _watchdog_instance.stop()
            _watchdog_instance = None
