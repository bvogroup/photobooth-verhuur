"""
Voucher-systeem voor Bootharoo Photobooth.

Per-event voucher store in DATA_DIR/vouchers/<event_id>.json:

    {
      "config": {
        "prefix":         "BOOTH",
        "suffix":         "",
        "middle_length":  4,
        "middle_chars":   "readable"   # "digits"|"letters"|"alphanum"|"readable"
      },
      "codes": [
        {"code": "BOOTH-1A2B", "used": false, "used_at": null},
        {"code": "BOOTH-9X3K", "used": true,  "used_at": "2026-05-04T19:42:11"}
      ]
    }

Code-format: <prefix>-<middle>-<suffix>, met streepjes alleen als beide
delen niet leeg zijn. Voorbeeld:
    prefix="BOOTH", suffix="X23", mid="1A2B"  ->  "BOOTH-1A2B-X23"
    prefix="BOOTH", suffix="",    mid="1A2B"  ->  "BOOTH-1A2B"
    prefix="",      suffix="",    mid="1A2B"  ->  "1A2B"

Validatie is hoofdletter-ongevoelig — gebruiker kan typen zoals hij wil.

Geen externe dependencies. Geen koppeling met camera/print/etc.
"""

from __future__ import annotations

import csv
import io
import json
import os
import secrets
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import config


# ── Charset definitions ──────────────────────────────────────────────

_CHARSETS = {
    "digits":   "0123456789",
    "letters":  "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "alphanum": "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    # Geen 0/O/1/I/L — voorkomt verwarring bij typen
    "readable": "ABCDEFGHJKMNPQRSTUVWXYZ23456789",
}

# Defaults voor nieuwe configs
DEFAULT_CONFIG = {
    "prefix":        "",
    "suffix":        "",
    "middle_length": 4,
    "middle_chars":  "readable",
}

MIN_MIDDLE_LENGTH = 2
MAX_MIDDLE_LENGTH = 12


# ── Storage paths ────────────────────────────────────────────────────

def _vouchers_dir() -> str:
    """Map waar voucher-stores per event opgeslagen worden."""
    path = os.path.join(config.DATA_DIR, "vouchers")
    os.makedirs(path, exist_ok=True)
    return path


def _store_path(event_id: str) -> str:
    """Pad naar het voucher-store-bestand voor dit event."""
    return os.path.join(_vouchers_dir(), f"{event_id}.json")


# ── Load / save store ────────────────────────────────────────────────

def load_store(event_id: str) -> Dict:
    """Laad voucher-store voor event. Maakt lege store aan als die niet bestaat."""
    path = _store_path(event_id)
    if not os.path.isfile(path):
        return {"config": dict(DEFAULT_CONFIG), "codes": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Vul missende keys aan
        if "config" not in data or not isinstance(data["config"], dict):
            data["config"] = dict(DEFAULT_CONFIG)
        else:
            for k, v in DEFAULT_CONFIG.items():
                data["config"].setdefault(k, v)
        if "codes" not in data or not isinstance(data["codes"], list):
            data["codes"] = []
        return data
    except Exception as e:
        print(f"[VOUCHER] Kon store niet laden ({event_id}): {e}")
        return {"config": dict(DEFAULT_CONFIG), "codes": []}


def save_store(event_id: str, data: Dict) -> None:
    """Schrijf voucher-store atomisch (via tmp + rename)."""
    path = _store_path(event_id)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


# ── Code-format helpers ──────────────────────────────────────────────

def _normalize(code: str) -> str:
    """Hoofdletters + strip whitespace voor case-insensitive vergelijken."""
    return (code or "").strip().upper()


def _format_code(prefix: str, middle: str, suffix: str) -> str:
    """Plak prefix/middle/suffix samen met streepjes ertussen waar nodig."""
    parts = [p for p in (prefix, middle, suffix) if p]
    return "-".join(parts)


# ── Generate ─────────────────────────────────────────────────────────

def _gen_middle(length: int, charset: str, rng=None) -> str:
    """Genereer random string van `length` tekens uit `charset`."""
    chars = _CHARSETS.get(charset, _CHARSETS["readable"])
    pick = (rng.choice if rng else secrets.choice)
    return "".join(pick(chars) for _ in range(length))


def generate_codes(
    prefix: str,
    suffix: str,
    middle_length: int,
    middle_chars: str,
    count: int,
    existing: Optional[List[str]] = None,
) -> List[str]:
    """Genereer `count` unieke codes. Excludeert duplicaten met `existing`.

    Raises ValueError bij ongeldige parameters of als de gevraagde count
    niet haalbaar is binnen redelijke pogingen (charset te klein).
    """
    if middle_length < MIN_MIDDLE_LENGTH or middle_length > MAX_MIDDLE_LENGTH:
        raise ValueError(
            f"middle_length moet tussen {MIN_MIDDLE_LENGTH} en {MAX_MIDDLE_LENGTH}"
        )
    if middle_chars not in _CHARSETS:
        raise ValueError(f"onbekend middle_chars: {middle_chars}")
    if count < 1:
        raise ValueError("count moet >= 1")

    prefix = (prefix or "").strip().upper()
    suffix = (suffix or "").strip().upper()

    # Beschikbare ruimte
    space = len(_CHARSETS[middle_chars]) ** middle_length
    seen = {_normalize(c) for c in (existing or [])}
    if count + len(seen) > space:
        raise ValueError(
            f"Te veel codes gevraagd: charset levert max {space} unieke "
            f"middendelen, je hebt al {len(seen)} en vraagt nog {count}."
        )

    out: List[str] = []
    attempts = 0
    max_attempts = max(count * 50, 1000)  # ruim genoeg voor sparse charsets
    while len(out) < count and attempts < max_attempts:
        attempts += 1
        mid = _gen_middle(middle_length, middle_chars)
        code = _format_code(prefix, mid, suffix)
        if _normalize(code) in seen:
            continue
        seen.add(_normalize(code))
        out.append(code)

    if len(out) < count:
        raise RuntimeError(
            f"Kon na {attempts} pogingen maar {len(out)}/{count} unieke codes "
            f"genereren (vermoedelijk charset te krap)."
        )
    return out


# ── Validate / redeem ────────────────────────────────────────────────

def find_code(code: str, codes_list: List[Dict]) -> Optional[Dict]:
    """Zoek een code-entry (case-insensitive). Returnt dict of None."""
    norm = _normalize(code)
    for entry in codes_list:
        if _normalize(entry.get("code", "")) == norm:
            return entry
    return None


def validate(code: str, codes_list: List[Dict]) -> Tuple[bool, str]:
    """Check of code geldig + onbruikt is.

    Returns (ok, message_key) waar message_key een translation-key is.
    """
    if not code or not code.strip():
        return False, "voucher_empty"
    entry = find_code(code, codes_list)
    if entry is None:
        return False, "voucher_invalid"
    if entry.get("used"):
        return False, "voucher_already_used"
    return True, "voucher_ok"


def mark_used(event_id: str, code: str) -> bool:
    """Markeer code als gebruikt + sla op. Returns True bij succes."""
    data = load_store(event_id)
    entry = find_code(code, data["codes"])
    if entry is None or entry.get("used"):
        return False
    entry["used"] = True
    entry["used_at"] = datetime.now().isoformat(timespec="seconds")
    save_store(event_id, data)
    return True


# ── Stats ────────────────────────────────────────────────────────────

def stats(codes_list: List[Dict]) -> Dict[str, int]:
    """Tel total / used / available."""
    total = len(codes_list)
    used = sum(1 for c in codes_list if c.get("used"))
    return {"total": total, "used": used, "available": total - used}


# ── Export ───────────────────────────────────────────────────────────

def export_txt(codes_list: List[Dict], include_used: bool = True) -> str:
    """Genereer TXT — één code per regel."""
    lines = []
    for c in codes_list:
        if not include_used and c.get("used"):
            continue
        lines.append(c.get("code", ""))
    return "\n".join(lines) + "\n"


def export_csv(codes_list: List[Dict]) -> str:
    """Genereer CSV met code, status, used_at."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["code", "used", "used_at"])
    for c in codes_list:
        w.writerow([
            c.get("code", ""),
            "yes" if c.get("used") else "no",
            c.get("used_at") or "",
        ])
    return buf.getvalue()


# ── Helpers voor UI ──────────────────────────────────────────────────

def add_codes_to_store(event_id: str, new_codes: List[str]) -> int:
    """Voeg nieuwe codes toe aan store (skippt duplicaten). Returns added count."""
    data = load_store(event_id)
    existing_norm = {_normalize(c.get("code", "")) for c in data["codes"]}
    added = 0
    for code in new_codes:
        if _normalize(code) in existing_norm:
            continue
        data["codes"].append({"code": code, "used": False, "used_at": None})
        existing_norm.add(_normalize(code))
        added += 1
    save_store(event_id, data)
    return added


def update_config(event_id: str, cfg: Dict) -> None:
    """Update alleen het config-blok (prefix/suffix/lengte/charset)."""
    data = load_store(event_id)
    for k in DEFAULT_CONFIG:
        if k in cfg:
            data["config"][k] = cfg[k]
    save_store(event_id, data)


def all_used(codes_list: List[Dict]) -> bool:
    """True als alle codes gebruikt zijn (en lijst niet leeg is)."""
    return bool(codes_list) and all(c.get("used") for c in codes_list)
