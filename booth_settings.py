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
    # Printer-modus: "canon" = oude flow (single/double strip op HiTi),
    # "dnp" = nieuwe flow (triple strip 5x10cm op DNP QW410 met 2-inch cut).
    printer_mode: str = "dnp"

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
            instance = cls()
        else:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Filter only known fields zodat oude json-bestanden met onbekende
                # velden niet crashen en nieuwe velden defaults krijgen.
                known = {f.name for f in cls.__dataclass_fields__.values()}
                filtered = {k: v for k, v in data.items() if k in known}
                instance = cls(**filtered)
            except Exception as ex:
                print(f"[BOOTH-SETTINGS] Kon niet laden ({ex}); defaults gebruikt")
                instance = cls()
        cls._apply_verhuur_overrides(instance)
        return instance

    @staticmethod
    def _apply_verhuur_overrides(instance: "BoothSettings") -> None:
        """Force verhuur-specific values regardless of what is stored on disk.

        Houdt de software simpel: de gebruiker kan deze waarden niet via de UI
        veranderen, dus elke afwijking in booth_settings.json wordt genegeerd en
        teruggezet bij elke load.
        """
        instance.countdown_seconds = 5
        instance.photo_delay_ms = 3000
        instance.sharing_timeout = 30
        instance.camera_mode = "webcam"
        instance.camera_rotation = 0
        instance.live_view_position = "center"
        instance.webcam_resolution = ""  # leeg = hoogste beschikbare
        instance.gallery_enabled = True
        instance.email_enabled = False
        instance.email_collect = False
        instance.data_collect_enabled = False
        instance.qr_branding_enabled = False
        instance.qr_branding_text = ""
        instance.auto_print = True
        instance.auto_print_copies = 1
        instance.print_copies = 1
        instance.max_prints = 1
        instance.extra_prints_allowed = 0
        instance.payment_enabled = False
        instance.sumup_enabled = False
        instance.payment_method = "none"
        instance.custom_flow_unlocked = False
        instance.pin_code = "1350"
        instance.lock_icon_size = 60
        instance.save_photos_locally = True

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
