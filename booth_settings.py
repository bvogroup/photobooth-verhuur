"""
Booth-wide settings — gedeeld over alle events.

Voorheen werden veel instellingen per-event opgeslagen, wat klanten verwarrend
vonden (camera-instellingen die wisselen wanneer je een ander event kiest).
Dit module verschuift die instellingen naar één gedeeld bestand.

PER-EVENT BLIJFT (in `events/<id>.json`):
- Event-metadata: id, name, date, location, status
- Template/layout: template_name
- Idle/Intro/Capture overlays (event-specifieke branding):
  idle_screen_mode, idle_background, intro_*, capture_*
- Tellers: session_count, photo_count
- Voucherpool: in vouchers/<id>.json (apart bestand, ongewijzigd)

BOOTH-WIDE (in `booth_settings.json`):
- Camera, print, delen, betaling, geavanceerd — zie BoothSettings dataclass

Het bestand staat in DATA_DIR (Documents\\Bootharoo\\booth_settings.json).
Bij eerste opstart na de v2.27 upgrade wordt het bestand automatisch
aangemaakt vanuit het huidige actieve event (zie event_model.Event.load).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Set

import config


@dataclass
class BoothSettings:
    """Gedeelde instellingen over alle events. Zelfde defaults als Event."""

    # ── Print ──────────────────────────────────────────────────
    background_path: str = ""        # Print achtergrond pad
    print_copies: int = 1            # Legacy — mapped to auto_print_copies on load
    auto_print_copies: int = 1
    cut_enabled: bool = True
    print_enabled: bool = True
    auto_print: bool = True
    max_prints: int = 1
    extra_prints_allowed: int = 0

    # ── Delen / Email ──────────────────────────────────────────
    email_enabled: bool = False
    email_collect: bool = False
    data_collect_enabled: bool = False
    data_collect_timing: str = "after"
    data_collect_fields: str = "email"
    data_collect_auto_email: bool = True
    email_subject: str = "Jouw Photobooth Foto's!"
    email_body: str = (
        "Bedankt voor je bezoek aan de photobooth!\n\n"
        "In de bijlage vind je jouw foto's.\n\n"
        "Groetjes,\nDe Photobooth"
    )
    share_single_strip: bool = False
    compress_sharing: bool = False
    email_send_strip: bool = True
    email_send_originals: bool = False
    email_send_gif: bool = True
    gallery_enabled: bool = False
    sharing_timeout: int = 30
    # QR-branding tekst voor Cloudflare gallery-pagina (vervangt
    # "Powered by Bootharoo"). Booth-wide want representeert jouw zaak.
    qr_branding_enabled: bool = False
    qr_branding_text: str = ""

    # ── Camera ─────────────────────────────────────────────────
    countdown_seconds: int = 3
    photo_delay_ms: int = 5000
    camera_mode: str = "dslr"
    camera_mirror: bool = False
    camera_rotation: int = 0
    live_view_position: str = "center"
    webcam_index: int = 0
    webcam_name: str = ""
    webcam_resolution: str = ""
    save_photos_locally: bool = True

    # ── Betaling ───────────────────────────────────────────────
    payment_enabled: bool = False
    sumup_enabled: bool = False
    payment_method: str = "none"
    payment_screen_text: str = "Scan om te betalen"
    payment_bg_path: str = ""
    custom_flow_unlocked: bool = False
    custom_choice_bg_path: str = ""
    custom_payment_bg_path: str = ""
    custom_choice_timeout: int = 30
    custom_payment_timeout: int = 120

    # ── Geavanceerd ────────────────────────────────────────────
    lock_icon_size: int = 60
    pin_code: str = ""

    # ── Persistence ────────────────────────────────────────────

    @classmethod
    def path(cls) -> str:
        """Pad naar het booth_settings.json bestand."""
        return os.path.join(config.DATA_DIR, "booth_settings.json")

    def save(self) -> None:
        """Atomisch wegschrijven (tmp + rename) om kapotte bestanden te voorkomen."""
        p = self.path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, indent=2, ensure_ascii=False)
        os.replace(tmp, p)

    @classmethod
    def load(cls) -> "BoothSettings":
        """Laad booth-settings. Bij ontbrekend of corrupt bestand: defaults."""
        p = cls.path()
        if not os.path.isfile(p):
            return cls()
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as ex:
            print(f"[BOOTH-SETTINGS] Kon niet laden ({ex}); defaults gebruikt")
            return cls()
        # Filter only known fields zodat oude json-bestanden met onbekende
        # velden niet crashen en nieuwe velden defaults krijgen.
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    @classmethod
    def exists(cls) -> bool:
        """True als het booth_settings.json bestand al bestaat."""
        return os.path.isfile(cls.path())


# ── Field-set helper voor event_model integratie ──────────────────────

def field_names() -> Set[str]:
    """Verzameling veldnamen die booth-wide zijn. Wordt gebruikt in
    event_model.Event.load/save om overlay + propagatie te doen.
    """
    return {f.name for f in BoothSettings.__dataclass_fields__.values()}
