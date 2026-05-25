"""End-to-end test van Event.load() overlay + migrate_from_event."""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, "C:\\Photobooth")
import config

# Eigen DATA_DIR vóór imports
_TMPROOT = tempfile.mkdtemp(prefix="bootharoo_overlay_test_")
config.DATA_DIR = _TMPROOT

import booth_settings
import event_model
from event_model import Event, migrate_from_event
from booth_settings import BoothSettings


def assert_eq(a, b, msg=""):
    if a != b:
        raise AssertionError(f"{msg}\n  expected: {b!r}\n  actual:   {a!r}")


def cleanup():
    p = BoothSettings.path()
    if os.path.isfile(p):
        os.remove(p)


def make_event_json(events_dir, **overrides):
    """Schrijf een event JSON met gegeven veld-overrides."""
    os.makedirs(events_dir, exist_ok=True)
    ev = Event.create_new(overrides.pop("name", "Test"))
    for k, v in overrides.items():
        setattr(ev, k, v)
    ev.save(events_dir)
    return ev


def test_load_without_booth_settings():
    """Event.load() zonder booth_settings: gebruikt event JSON-waarden."""
    cleanup()
    events_dir = os.path.join(_TMPROOT, "events1")
    ev = make_event_json(events_dir, name="A", countdown_seconds=7,
                          camera_mode="webcam", pin_code="9999")
    reloaded = Event.load(os.path.join(events_dir, f"{ev.id}.json"))
    assert_eq(reloaded.countdown_seconds, 7)
    assert_eq(reloaded.camera_mode, "webcam")
    assert_eq(reloaded.pin_code, "9999")
    print("OK  zonder booth_settings: event-JSON-waarden gelezen")


def make_event_in_memory(**overrides):
    """Maak event-object zonder te saven (geen booth_settings propagatie)."""
    ev = Event.create_new(overrides.pop("name", "InMem"))
    for k, v in overrides.items():
        setattr(ev, k, v)
    return ev


def test_migrate_from_event_copies_booth_fields():
    """migrate_from_event maakt booth_settings.json met event's booth-velden."""
    cleanup()
    # Geen save — zuiver in-memory event om migrate-flow geïsoleerd te testen
    ev = make_event_in_memory(name="Source",
                              countdown_seconds=11, photo_delay_ms=8888,
                              camera_mirror=True, pin_code="4321",
                              payment_method="voucher")
    migrated = migrate_from_event(ev)
    assert migrated, "migratie moet True returnen bij eerste keer"
    assert BoothSettings.exists()

    bs = BoothSettings.load()
    assert_eq(bs.countdown_seconds, 11)
    assert_eq(bs.photo_delay_ms, 8888)
    assert_eq(bs.camera_mirror, True)
    assert_eq(bs.pin_code, "4321")
    assert_eq(bs.payment_method, "voucher")
    print("OK  migrate_from_event kopieert booth-velden correct")


def test_migrate_is_idempotent():
    """Tweede migrate_from_event mag bestaande booth_settings niet overschrijven."""
    cleanup()
    ev1 = make_event_in_memory(name="First", countdown_seconds=5)
    assert migrate_from_event(ev1)

    # Tweede event met andere waarden — migratie moet skip
    ev2 = make_event_in_memory(name="Second", countdown_seconds=99)
    skipped = migrate_from_event(ev2)
    assert not skipped, "migratie moet False returnen als booth_settings al bestaat"

    bs = BoothSettings.load()
    assert_eq(bs.countdown_seconds, 5, "eerste migratie's waarde moet behouden blijven")
    print("OK  migrate_from_event is idempotent")


def test_overlay_after_migration():
    """Na migratie: Event.load() overlay vervangt event-JSON-waarden met booth-waarden."""
    cleanup()
    events_dir = os.path.join(_TMPROOT, "events4")
    # Event 1 in-memory (geen save) → migrate seeded booth_settings met ev1's waarden
    ev1 = make_event_in_memory(name="Active",
                               countdown_seconds=8, camera_mode="dslr",
                               pin_code="1234")
    migrate_from_event(ev1)

    # Event 2 op disk met ANDERE waarden in JSON. Direct schrijven om
    # propagatie te omzeilen (anders zou ev2's save de booth_settings
    # opnieuw overschrijven met ev2's waarden — dat is by design correct
    # gedrag, maar voor deze test willen we de zuivere overlay verifiëren).
    os.makedirs(events_dir, exist_ok=True)
    ev2_path = os.path.join(events_dir, "ev2.json")
    raw_data = {
        "id": "ev2",
        "name": "Other",
        "countdown_seconds": 999,
        "camera_mode": "webcam",
        "pin_code": "0000",
        "template_name": "MijnTemplate",
    }
    with open(ev2_path, "w", encoding="utf-8") as f:
        json.dump(raw_data, f)

    reloaded = Event.load(ev2_path)

    # Booth-wide velden komen uit booth_settings (ev1's waarden)
    assert_eq(reloaded.countdown_seconds, 8)
    assert_eq(reloaded.camera_mode, "dslr")
    assert_eq(reloaded.pin_code, "1234")
    # Per-event velden komen uit event JSON
    assert_eq(reloaded.name, "Other")
    assert_eq(reloaded.template_name, "MijnTemplate")
    print("OK  overlay vervangt booth-velden maar laat per-event velden ongemoeid")


def test_intro_capture_stay_per_event():
    """Intro/Capture overlays moeten per-event blijven (gebruiker's keuze)."""
    cleanup()
    events_dir = os.path.join(_TMPROOT, "events5")
    ev1 = make_event_json(events_dir, name="Active",
                          intro_text="Welkom bij event A",
                          capture_text="Lach voor A")
    migrate_from_event(ev1)

    ev2 = make_event_json(events_dir, name="Bruiloft",
                          intro_text="Welkom bij bruiloft Jurgen",
                          capture_text="Lach Jurgen!")
    reloaded = Event.load(os.path.join(events_dir, f"{ev2.id}.json"))
    # Intro/Capture velden moeten event-specifiek blijven
    assert_eq(reloaded.intro_text, "Welkom bij bruiloft Jurgen")
    assert_eq(reloaded.capture_text, "Lach Jurgen!")
    print("OK  intro_text + capture_text blijven event-specifiek")


def test_legacy_payment_migration_still_works():
    """De bestaande v2.24 legacy-migratie (sumup_enabled -> payment_method)
    moet blijven werken, óók in combinatie met de nieuwe overlay."""
    cleanup()
    events_dir = os.path.join(_TMPROOT, "events6")
    # Schrijf oude event JSON zonder payment_method maar met sumup_enabled
    os.makedirs(events_dir, exist_ok=True)
    old_path = os.path.join(events_dir, "legacy.json")
    with open(old_path, "w", encoding="utf-8") as f:
        json.dump({
            "id": "legacy",
            "name": "Pre-v2.24",
            "sumup_enabled": True,
            "payment_enabled": False,
        }, f)
    # GEEN booth_settings — dus geen overlay
    reloaded = Event.load(old_path)
    assert_eq(reloaded.payment_method, "sumup")
    assert_eq(reloaded.sumup_enabled, True)
    print("OK  legacy payment-migratie werkt nog (zonder booth_settings)")


def test_save_does_not_break():
    """Event.save() blijft schrijven zoals voorheen + propageert naar booth_settings."""
    cleanup()
    events_dir = os.path.join(_TMPROOT, "events7")
    ev = make_event_json(events_dir, name="SaveTest", countdown_seconds=15)
    # Lees JSON terug en check dat countdown_seconds erin staat
    with open(os.path.join(events_dir, f"{ev.id}.json"), "r", encoding="utf-8") as f:
        data = json.load(f)
    assert_eq(data["countdown_seconds"], 15)
    assert_eq(data["name"], "SaveTest")
    print("OK  Event.save() blijft volledige JSON wegschrijven")


def test_save_propagates_to_booth_settings():
    """Wijziging via event.save() moet booth_settings.json updaten."""
    cleanup()
    events_dir = os.path.join(_TMPROOT, "events8")
    # Migreer eerst een initieel event
    ev_init = make_event_json(events_dir, name="Init", countdown_seconds=3)
    migrate_from_event(ev_init)

    # Wijzig nu een booth-wide veld via een andere event.save
    ev_change = make_event_json(events_dir, name="Changer")
    ev_change.countdown_seconds = 12
    ev_change.camera_mirror = True
    ev_change.pin_code = "7777"
    ev_change.save(events_dir)

    # booth_settings.json moet nu de nieuwe waarden bevatten
    bs = BoothSettings.load()
    assert_eq(bs.countdown_seconds, 12)
    assert_eq(bs.camera_mirror, True)
    assert_eq(bs.pin_code, "7777")
    print("OK  Event.save() propageert booth-velden naar booth_settings.json")


def test_save_does_not_propagate_event_specific_fields():
    """Per-event velden (template_name, intro_text) moeten NIET in booth_settings komen."""
    cleanup()
    events_dir = os.path.join(_TMPROOT, "events9")
    ev_init = make_event_json(events_dir, name="Init", countdown_seconds=3)
    migrate_from_event(ev_init)

    ev = make_event_json(events_dir, name="Wedding",
                          template_name="StripVerticaal",
                          intro_text="Welkom bij bruiloft",
                          capture_text="Lach voor altijd")
    ev.save(events_dir)

    bs = BoothSettings.load()
    # Per-event velden NIET als attributes op BoothSettings
    assert not hasattr(bs, "template_name"), "template_name moet per-event blijven"
    assert not hasattr(bs, "intro_text"), "intro_text moet per-event blijven"
    assert not hasattr(bs, "capture_text"), "capture_text moet per-event blijven"
    print("OK  per-event velden lekken niet naar booth_settings")


def test_create_new_inherits_booth_settings():
    """Event.create_new() moet booth-wide velden erven, NIET defaults gebruiken.

    Bug die in v2.27 ontdekt werd: bij nieuw event aanmaken kreeg het event
    dataclass-defaults (bv. countdown=3, pin_code=""), en de eerste save
    propageerde die DEFAULTS naar booth_settings → alle andere events kregen
    reset. Fix: create_new past nu eerst overlay toe vanuit booth_settings.
    """
    cleanup()
    # Seed booth_settings met afwijkende waarden
    ev_init = make_event_in_memory(name="Bron",
                                   countdown_seconds=10,
                                   camera_mirror=True,
                                   pin_code="9876",
                                   payment_method="voucher")
    migrate_from_event(ev_init)

    # Nieuw event moet die waarden erven
    ev_new = Event.create_new("Verse")
    assert_eq(ev_new.countdown_seconds, 10,
              "nieuwe event moet booth-countdown erven, niet default 3")
    assert_eq(ev_new.camera_mirror, True,
              "nieuwe event moet booth-mirror erven, niet default False")
    assert_eq(ev_new.pin_code, "9876",
              "nieuwe event moet booth-pin erven, niet default lege string")
    assert_eq(ev_new.payment_method, "voucher",
              "nieuwe event moet booth-payment_method erven")
    # Per-event velden zijn nog default (intro_text etc.)
    assert "{n}" in ev_new.intro_text, "intro_text default blijft per-event"
    print("OK  Event.create_new() erft booth-wide velden")


def test_create_new_save_does_not_clobber_booth_settings():
    """Vervolg op vorige test: na save van het nieuwe event mogen booth-velden
    in booth_settings NIET teruggezet zijn naar defaults."""
    cleanup()
    events_dir = os.path.join(_TMPROOT, "events_clobber")
    # Seed booth_settings met afwijkende waarden
    ev_init = make_event_in_memory(countdown_seconds=15, pin_code="5555")
    migrate_from_event(ev_init)

    # Maak + save nieuw event
    ev_new = Event.create_new("Nieuw")
    ev_new.save(events_dir)

    # booth_settings moet NOG STEEDS de geseede waarden hebben
    bs = BoothSettings.load()
    assert_eq(bs.countdown_seconds, 15,
              "save van nieuw event mag booth-waarden niet reseten")
    assert_eq(bs.pin_code, "5555",
              "save van nieuw event mag booth-waarden niet reseten")
    print("OK  save van nieuw event reset booth_settings niet")


def test_full_cross_event_sync():
    """Realistisch scenario: wijzig camera-instelling op event A, laad event B,
    krijg dezelfde waarde (synced via booth_settings)."""
    cleanup()
    events_dir = os.path.join(_TMPROOT, "events10")
    # Initiële migratie
    ev_a = make_event_json(events_dir, name="A",
                          countdown_seconds=3, camera_mirror=False)
    migrate_from_event(ev_a)
    ev_b = make_event_json(events_dir, name="B",
                          countdown_seconds=3, camera_mirror=False)

    # User wijzigt countdown via event A
    ev_a.countdown_seconds = 10
    ev_a.camera_mirror = True
    ev_a.save(events_dir)

    # Laad event B opnieuw — moet de nieuwe waarden hebben
    reloaded_b = Event.load(os.path.join(events_dir, f"{ev_b.id}.json"))
    assert_eq(reloaded_b.countdown_seconds, 10)
    assert_eq(reloaded_b.camera_mirror, True)
    # Per-event veld blijft ev_b's eigen waarde
    assert_eq(reloaded_b.name, "B")
    print("OK  cross-event sync: wijziging op A is zichtbaar bij B")


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    print(f"Running {len(tests)} tests... DATA_DIR={config.DATA_DIR}\n")
    failed = False
    for fn in tests:
        try:
            fn()
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
            failed = True
        except Exception as e:
            print(f"ERR  {fn.__name__}: {type(e).__name__}: {e}")
            import traceback; traceback.print_exc()
            failed = True
    shutil.rmtree(_TMPROOT, ignore_errors=True)
    print()
    if failed:
        sys.exit(1)
    print("ALLE TESTS GESLAAGD")
