"""
Event model for photobooth event management.

An Event defines per-event settings:
- Name, date, location
- Template, background, print settings
- Email/gallery toggles
- Session and photo counters

Events are saved as JSON files in the events directory.
"""

import json
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Optional


@dataclass
class Event:
    """A photobooth event with its own settings."""
    id: str                          # Unique ID (UUID)
    name: str                        # Event name
    date: str = ""                   # Event date (YYYY-MM-DD)
    location: str = ""               # Venue / location
    status: str = "draft"            # draft / active / completed
    template_name: str = ""          # Selected template name
    idle_screen_mode: str = "default" # "default" or "custom"
    idle_background: str = ""        # Custom idle background path
    background_path: str = ""        # Print achtergrond pad
    print_copies: int = 1            # Legacy — mapped to auto_print_copies on load
    auto_print_copies: int = 1       # Number of copies for auto-print
    cut_enabled: bool = True         # Print with cut lines (strip mode)
    print_enabled: bool = True       # Printing on/off
    auto_print: bool = True          # Auto-print on capture (no button needed)
    max_prints: int = 1              # Max total prints per session (auto-print OFF)
    extra_prints_allowed: int = 0    # Extra manual prints when auto-print ON (0=no button)
    email_enabled: bool = False      # Email sharing on/off
    email_collect: bool = False      # Collect email addresses to CSV
    data_collect_enabled: bool = False  # Gegevensverzameling aan/uit
    data_collect_timing: str = "after"  # "before" (voor foto) of "after" (na foto)
    data_collect_fields: str = "email"  # Komma-gescheiden velden: email,naam,telefoon,adres,geboortedatum
    data_collect_auto_email: bool = True  # Foto automatisch mailen na invullen
    email_subject: str = "Jouw Photobooth Foto's!"  # Customizable email subject
    email_body: str = "Bedankt voor je bezoek aan de photobooth!\n\nIn de bijlage vind je jouw foto's.\n\nGroetjes,\nDe Photobooth"
    share_single_strip: bool = False  # Share single strip (left half) instead of double
    compress_sharing: bool = False   # Compress photos for faster upload/email
    email_send_strip: bool = True    # Attach the photo strip
    email_send_originals: bool = False  # Attach original photos
    email_send_gif: bool = True      # Attach boomerang GIF
    gallery_enabled: bool = False    # Online gallery on/off
    # QR-code branding: eigen bedrijfsgegevens onderaan de Cloudflare gallery-pagina
    # (vervangt "Powered by Bootharoo" bij scan van de QR-code).
    qr_branding_enabled: bool = False
    qr_branding_text: str = ""       # Multi-line tekst
    countdown_seconds: int = 3       # Countdown duration before capture
    photo_delay_ms: int = 5000       # Delay between photos / capture preview (ms)
    sharing_timeout: int = 30        # Sharing screen timeout in seconds
    lock_icon_size: int = 60         # Lock icon size in pixels
    pin_code: str = ""               # PIN code for settings access (empty = no PIN)
    camera_mode: str = "dslr"        # "dslr" or "webcam"
    camera_mirror: bool = False      # Horizontally mirror photos
    camera_rotation: int = 0         # Rotate photos: 0, 90, 180, 270 degrees
    # Verticale uitlijning van de live view pixmap binnen het preview-scherm.
    # "center" = midden gecentreerd (huidig + default gedrag, ongewijzigd).
    # "top"    = boven uitgelijnd (handig bij portret/lange monitors).
    live_view_position: str = "center"
    webcam_index: int = 0            # Webcam device index (0 = first)
    webcam_name: str = ""            # Webcam name (for matching after reboot)
    webcam_resolution: str = ""      # Webcam resolution (e.g. "1920x1080")
    intro_screen_path: str = ""      # Image shown before countdown (empty = default text)
    intro_duration: int = 2          # Intro screen duration in seconds
    intro_text: str = "We gaan {n} foto's maken"  # Text on intro screen ({n} = photo count)
    intro_text_enabled: bool = True  # Show intro text overlay
    capture_screen_path: str = ""    # Image shown at moment of capture (empty = white flash)
    capture_text: str = "Blijf lachen tot de tweede klik"  # Text on capture screen
    capture_text_enabled: bool = True  # Show capture text overlay
    payment_enabled: bool = False      # Stripe payment mode on/off (legacy — zie payment_method)
    sumup_enabled: bool = False        # SumUp payment terminal on/off (legacy — zie payment_method)
    payment_link_url: str = ""         # Stripe Payment Link URL (legacy, now in cloud)
    payment_screen_text: str = "Scan om te betalen"  # Text shown on payment idle screen
    payment_bg_path: str = ""          # Custom background for payment idle screen
    # Nieuwe single source of truth voor de betaalmethode. Mogelijke waardes:
    #   "none"    — geen betaling vereist
    #   "stripe"  — Stripe online betaal-link
    #   "sumup"   — SumUp betaalterminal
    #   "voucher" — Voucher-codes (codes worden los opgeslagen in vouchers/<id>.json)
    #   "custom"  — Custom flow (verborgen): keuze tussen gratis digitaal of betaalde print
    # Bij oude events wordt dit veld bij load afgeleid uit payment_enabled/sumup_enabled.
    payment_method: str = "none"
    # Custom flow — verborgen functie, ontgrendeld via wachtwoord-dialog in
    # Betalingen-tab. Combineert keuzescherm + Stripe + voucher + data-collection.
    # Velden alleen actief als payment_method == "custom".
    custom_flow_unlocked: bool = False    # ontgrendeld voor dit event
    custom_choice_bg_path: str = ""       # achtergrond keuzescherm (image pad)
    custom_payment_bg_path: str = ""      # achtergrond betaalscherm (image pad)
    custom_choice_timeout: int = 30       # seconden voor keuzescherm
    custom_payment_timeout: int = 120     # seconden voor betaalscherm
    save_photos_locally: bool = True   # Save photos to local disk
    # Printer-modus (verhuur-versie):
    #   "4x3"     = 1 grote print op 4x3 paper (geen mirror/cut)
    #   "4x6"     = oude Canon dubbele strip op 4x6 paper (links gemirrord
    #               naar rechts; cut tussen 2 helften)
    #   "3strips" = DNP triple strip — 600x1200 design 3× gestapeld op 4x6
    #               vel met 2-inch cut
    # Legacy waarden 'canon' / 'dnp' worden bij load gemigreerd naar
    # respectievelijk '4x6' / '3strips' (zie _migrate_legacy_printer_mode).
    printer_mode: str = "3strips"
    # Backend-brand: "hippe" (default, DNP QW410) of "huren"
    # (Verhuurophalen — HiTi P525L, 1200x1800 dubbele strip)
    backend_brand: str = "hippe"
    # Booth-modus + Linked-koppeling (verhuur-versie)
    booth_mode: str = "standalone"
    linked_booking_id: str = ""
    linked_token: str = ""
    linked_booking_label: str = ""
    linked_design_path: str = ""
    linked_photo_count: int = 3
    # Pakket-type uit booking ("standard"/"premium"/"" indien onbekend).
    # Stuurt de print-delay aan op de "Foto wordt geprint" spinner.
    linked_package: str = ""
    session_count: int = 0           # Number of sessions run
    photo_count: int = 0             # Total photos taken
    # Print-quotum per event — onafhankelijk van auto_print_copies/max_prints
    # (die zijn per-sessie, dit is voor het hele event).
    # 0 = onbeperkt printen. >0 = maximaal X prints over alle sessies.
    # Per-event (NIET in booth_settings.field_names) zodat elke klant zijn
    # eigen pakket-limiet heeft.
    event_print_quota: int = 0
    event_prints_used: int = 0       # Teller, increment na elke succesvolle print
    created_at: str = ""             # ISO timestamp
    updated_at: str = ""             # ISO timestamp

    def save(self, events_dir: str):
        """Save event as JSON file + propagate booth-wide velden naar booth_settings.

        Het event JSON blijft volledig — inclusief booth-wide velden — voor
        backward-compat. Maar elke save propagateert ook naar booth_settings
        zodat een wijziging via één event direct in alle andere events
        zichtbaar is.
        """
        self.updated_at = datetime.now().isoformat()
        if not self.created_at:
            self.created_at = self.updated_at
        data = asdict(self)
        path = os.path.join(events_dir, f"{self.id}.json")
        os.makedirs(events_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        # Propageer booth-wide velden naar het gedeelde booth_settings.json.
        # Tellers (session_count, photo_count) en per-event velden gaan NIET
        # naar booth_settings — die staan niet in field_names().
        try:
            import booth_settings as _bs_module
            bs = (_bs_module.BoothSettings.load()
                  if _bs_module.BoothSettings.exists()
                  else _bs_module.BoothSettings())
            for fname in _bs_module.field_names():
                if hasattr(self, fname):
                    setattr(bs, fname, getattr(self, fname))
            bs.save()
        except Exception as ex:
            print(f"[EVENT] Booth-propagatie overgeslagen: {ex}")

    @classmethod
    def load(cls, path: str) -> "Event":
        """Load event from JSON file (incl. legacy migratie + booth-wide overlay)."""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Filter only known fields
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        ev = cls(**filtered)
        # Legacy-migratie: als payment_method niet expliciet gezet is (default "none"),
        # leid 'm af uit de oude payment_enabled / sumup_enabled velden.
        if ev.payment_method == "none":
            if ev.sumup_enabled:
                ev.payment_method = "sumup"
            elif ev.payment_enabled:
                ev.payment_method = "stripe"
        # Migratie printer_mode: legacy 'canon'/'dnp' → '4x6'/'3strips'
        if ev.printer_mode == "canon":
            ev.printer_mode = "4x6"
        elif ev.printer_mode == "dnp":
            ev.printer_mode = "3strips"
        # Booth-wide overlay: als booth_settings.json bestaat, neem die waarden.
        # Per-event velden (id, name, date, template_name, intro_*, capture_*,
        # session_count, photo_count, etc.) blijven uit het event-JSON komen.
        # Eerste opstart na v2.27 upgrade: booth_settings bestaat nog niet —
        # photobooth.py roept dan migrate_from_event() aan op het actieve event.
        try:
            import booth_settings as _bs_module
            if _bs_module.BoothSettings.exists():
                bs = _bs_module.BoothSettings.load()
                for fname in _bs_module.field_names():
                    if hasattr(ev, fname):
                        setattr(ev, fname, getattr(bs, fname))
        except Exception as ex:
            print(f"[EVENT] Booth-overlay overgeslagen: {ex}")
        return ev

    @classmethod
    def create_new(cls, name: str, date: str = "", location: str = "") -> "Event":
        """Create a new event with a unique ID.

        Booth-wide instellingen worden geërfd van het gedeelde booth_settings.json
        (als dat bestaat), zodat een nieuw event meteen dezelfde camera-/print-/
        deel-/betaling-instellingen heeft als alle andere events. Zonder deze
        overlay zou de dataclass-defaults de booth_settings overschrijven bij
        de eerste save.
        """
        ev = cls(
            id=uuid.uuid4().hex[:12],
            name=name,
            date=date,
            location=location,
            created_at=datetime.now().isoformat(),
            updated_at=datetime.now().isoformat(),
        )
        try:
            import booth_settings as _bs_module
            if _bs_module.BoothSettings.exists():
                bs = _bs_module.BoothSettings.load()
                for fname in _bs_module.field_names():
                    if hasattr(ev, fname):
                        setattr(ev, fname, getattr(bs, fname))
        except Exception as ex:
            print(f"[EVENT] Booth-overlay bij create_new overgeslagen: {ex}")
        return ev

    def delete(self, events_dir: str):
        """Delete event JSON file."""
        path = os.path.join(events_dir, f"{self.id}.json")
        if os.path.isfile(path):
            os.remove(path)

    def increment_session(self, events_dir: str):
        """Increment session count and save."""
        self.session_count += 1
        self.save(events_dir)

    def increment_photos(self, count: int, events_dir: str):
        """Add to photo count and save."""
        self.photo_count += count
        self.save(events_dir)


def migrate_from_event(event: "Event") -> bool:
    """One-shot migratie: schrijf de booth-wide velden van dit event naar
    booth_settings.json.

    Bedoeld voor eerste opstart na de v2.27 upgrade: het actieve event wordt
    de bron voor de gedeelde instellingen. Daarna is booth_settings.json de
    source of truth en wordt overlay toegepast bij elke Event.load().

    Doet niets als booth_settings.json al bestaat (idempotent).
    Returnt True als migratie is uitgevoerd, False bij overslaan/fout.
    """
    try:
        import booth_settings as _bs_module
        if _bs_module.BoothSettings.exists():
            return False  # al gemigreerd
        bs = _bs_module.BoothSettings()
        for fname in _bs_module.field_names():
            if hasattr(event, fname):
                setattr(bs, fname, getattr(event, fname))
        bs.save()
        print(f"[BOOTH-SETTINGS] Migratie uitgevoerd vanuit event '{event.name}' "
              f"-> {_bs_module.BoothSettings.path()}")
        return True
    except Exception as ex:
        print(f"[BOOTH-SETTINGS] Migratie mislukt: {ex}")
        return False


def list_events(events_dir: str) -> List[Event]:
    """List all events, sorted by date descending (newest first)."""
    events = []
    if not os.path.isdir(events_dir):
        return events
    for fname in os.listdir(events_dir):
        if fname.lower().endswith(".json"):
            try:
                e = Event.load(os.path.join(events_dir, fname))
                events.append(e)
            except Exception as ex:
                print(f"[EVENT] Fout bij laden {fname}: {ex}")
    # Sort by date descending, then by name
    events.sort(key=lambda e: (e.date or "0000-00-00", e.name), reverse=True)
    return events
