"""Companion-API client voor booking-lookup en design-fetch.

Praat met de Supabase edge functions in clixibo-clone-project:
- get-photobooth-booking → booking metadata via QR-token
- get-photostrip-design-url → signed URL voor de strip-design (bestaande functie)

Cachet booking-metadata en design-bestanden lokaal voor offline-fallback.
"""

import json
import os
import time
from datetime import datetime, timezone
from typing import Optional, Tuple

try:
    import requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

import config


# Aspect-ratio's voor format-validatie (canvas portrait, h/w)
ASPECT_RATIOS = {
    "standard": (3.0 / 2.0, 0.10),  # Canon dubbele strip 600x900 → 1:1.5, tolerantie 10%
    "premium":  (2.0, 0.10),         # DNP triple 600x1200 → 1:2, tolerantie 10%
}


def _cache_root() -> str:
    d = os.path.join(getattr(config, 'DATA_DIR', os.path.expanduser("~/Documents/Bootharoo")),
                     "cloud_cache")
    os.makedirs(d, exist_ok=True)
    return d


def _booking_cache_path(token: str) -> str:
    """Booking metadata cache file."""
    safe = "".join(c for c in token if c.isalnum() or c in "-_")[:64]
    return os.path.join(_cache_root(), f"booking_{safe}.json")


def _design_cache_path(booking_id: str, ext: str = "png") -> str:
    safe = "".join(c for c in booking_id if c.isalnum() or c in "-_")[:64]
    return os.path.join(_cache_root(), f"design_{safe}.{ext}")


# ── Booking lookup ───────────────────────────────────────────────────

def fetch_booking(token: str, use_cache_on_offline: bool = True) -> Tuple[Optional[dict], str]:
    """Haal booking-metadata op via QR-token.

    Returns (booking_dict, error_message).
    booking_dict bevat de hele response van de edge function:
        {booking, quote, photo_count_preset, printer_mode, has_design}

    Bij netwerk-fout en cache aanwezig: gebruikt cache + waarschuwing in error.
    """
    url = f"{config.SUPABASE_URL.rstrip('/')}/functions/v1/get-photobooth-booking"
    try:
        resp = requests.post(
            url,
            headers={
                "Content-Type": "application/json",
                "apikey": config.SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {config.SUPABASE_ANON_KEY}",
            },
            json={"token": token},
            timeout=15,
        )
    except Exception as e:
        # Network fail — probeer cache
        if use_cache_on_offline:
            cached = _read_booking_cache(token)
            if cached:
                return cached, f"offline (cache gebruikt): {e}"
        return None, f"Geen internet: {e}"

    if resp.status_code == 404:
        return None, "Booking niet gevonden — controleer of de QR-code klopt"
    if resp.status_code == 403:
        return None, "Booking is nog niet betaald"
    if resp.status_code != 200:
        return None, f"Server fout {resp.status_code}: {resp.text[:200]}"

    try:
        data = resp.json()
    except Exception as e:
        return None, f"Ongeldige response: {e}"

    # Schrijf naar cache voor offline gebruik
    _write_booking_cache(token, data)
    return data, ""


def _read_booking_cache(token: str) -> Optional[dict]:
    path = _booking_cache_path(token)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_booking_cache(token: str, data: dict) -> None:
    path = _booking_cache_path(token)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"_cached_at": datetime.now(timezone.utc).isoformat(),
                       "token": token, **data}, f, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        print(f"[CLOUD-BOOKING] Cache schrijven mislukt: {e}")


# ── Design fetch ──────────────────────────────────────────────────────

def fetch_design(token: str, design_path: str, booking_id: str) -> Tuple[Optional[str], str]:
    """Download het strip-design uit Supabase Storage via signed URL.

    Returns (local_file_path, error_message).
    Cachet lokaal in cloud_cache/design_<booking_id>.<ext>.
    """
    ext = (design_path.rsplit(".", 1)[-1] or "png").lower()
    if ext not in ("png", "jpg", "jpeg"):
        return None, f"Ongeldig design formaat: .{ext} (alleen PNG/JPG ondersteund)"

    local = _design_cache_path(booking_id, ext)

    # 1. Vraag signed URL via bestaande edge function
    url = f"{config.SUPABASE_URL.rstrip('/')}/functions/v1/get-photostrip-design-url"
    try:
        resp = requests.post(
            url,
            headers={
                "Content-Type": "application/json",
                "apikey": config.SUPABASE_ANON_KEY,
                "Authorization": f"Bearer {config.SUPABASE_ANON_KEY}",
            },
            json={"token": token, "path": design_path},
            timeout=15,
        )
    except Exception as e:
        # Gebruik cache als beschikbaar
        if os.path.isfile(local):
            return local, f"offline (cache gebruikt): {e}"
        return None, f"Geen internet voor design-fetch: {e}"

    if resp.status_code != 200:
        if os.path.isfile(local):
            return local, f"offline (cache gebruikt): server {resp.status_code}"
        return None, f"Design-fetch fout {resp.status_code}: {resp.text[:200]}"

    try:
        signed_url = resp.json().get("url")
    except Exception:
        return None, "Ongeldige response van design-url endpoint"

    if not signed_url:
        return None, "Geen signed URL terug van endpoint"

    # 2. Download de design-image
    try:
        dl = requests.get(signed_url, timeout=30)
    except Exception as e:
        if os.path.isfile(local):
            return local, f"download mislukt (cache gebruikt): {e}"
        return None, f"Download mislukt: {e}"

    if dl.status_code != 200:
        return None, f"Design download {dl.status_code}"

    tmp = local + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(dl.content)
        os.replace(tmp, local)
    except Exception as e:
        return None, f"Lokaal opslaan mislukt: {e}"

    return local, ""


# ── Format validatie ──────────────────────────────────────────────────

def validate_design_format(image_path: str, printer_mode: str) -> Tuple[bool, str]:
    """Check of de design-image het juiste aspect-ratio heeft voor de printer-modus.

    Returns (ok, error_message).
        standard (Canon)  → 1:1.5 (h/w = 1.5)
        premium  (DNP)    → 1:2   (h/w = 2)
    Tolerantie 10%.
    """
    if not _PIL_AVAILABLE:
        return False, "Pillow niet beschikbaar voor format-check"
    if printer_mode not in ASPECT_RATIOS:
        return False, f"Onbekende printer-modus: {printer_mode}"

    try:
        with Image.open(image_path) as img:
            w, h = img.size
    except Exception as e:
        return False, f"Kon image niet openen: {e}"

    if w <= 0:
        return False, "Image heeft breedte 0"

    actual = h / w
    target, tolerance = ASPECT_RATIOS[printer_mode]
    deviation = abs(actual - target) / target

    if deviation > tolerance:
        return False, (
            f"Design-formaat klopt niet voor {printer_mode}-printer.\n"
            f"Verwacht ~{target:.2f}:1 (h:b), gekregen {actual:.2f}:1.\n"
            f"Klant moet nieuwe design uploaden in het Clixibo portaal."
        )

    return True, ""
