"""Unit tests voor booth_settings.py — geïsoleerd, geen Event-koppeling."""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, "C:\\Photobooth")
import config

# Override DATA_DIR vóór booth_settings import zodat tests een eigen tmp gebruiken
_TMPROOT = tempfile.mkdtemp(prefix="bootharoo_bs_test_")
config.DATA_DIR = _TMPROOT

import booth_settings
from booth_settings import BoothSettings, field_names


def assert_eq(actual, expected, msg=""):
    if actual != expected:
        raise AssertionError(f"{msg}\n  expected: {expected!r}\n  actual:   {actual!r}")


def cleanup():
    p = BoothSettings.path()
    if os.path.isfile(p):
        os.remove(p)


def test_defaults():
    cleanup()
    bs = BoothSettings()
    assert_eq(bs.countdown_seconds, 3)
    assert_eq(bs.photo_delay_ms, 5000)
    assert_eq(bs.camera_mode, "dslr")
    assert_eq(bs.print_enabled, True)
    assert_eq(bs.payment_method, "none")
    assert_eq(bs.pin_code, "")
    print("OK  defaults matchen Event-defaults")


def test_save_load_roundtrip():
    cleanup()
    bs = BoothSettings()
    bs.countdown_seconds = 7
    bs.photo_delay_ms = 8000
    bs.camera_mode = "webcam"
    bs.payment_method = "voucher"
    bs.pin_code = "1234"
    bs.email_subject = "Test éè emoji \U0001F389"
    bs.save()

    assert BoothSettings.exists()
    loaded = BoothSettings.load()
    assert_eq(loaded.countdown_seconds, 7)
    assert_eq(loaded.photo_delay_ms, 8000)
    assert_eq(loaded.camera_mode, "webcam")
    assert_eq(loaded.payment_method, "voucher")
    assert_eq(loaded.pin_code, "1234")
    assert_eq(loaded.email_subject, "Test éè emoji \U0001F389")
    # Onveranderde velden moeten op default blijven
    assert_eq(loaded.print_enabled, True)
    print("OK  save/load roundtrip + unicode")


def test_load_missing_file_returns_defaults():
    cleanup()
    assert not BoothSettings.exists()
    bs = BoothSettings.load()
    assert_eq(bs.countdown_seconds, 3)
    assert_eq(bs.payment_method, "none")
    print("OK  load van missend bestand geeft defaults")


def test_load_corrupt_file_returns_defaults():
    cleanup()
    # Schrijf garbage
    with open(BoothSettings.path(), "w", encoding="utf-8") as f:
        f.write("{ this is not json")
    bs = BoothSettings.load()
    assert_eq(bs.countdown_seconds, 3)
    print("OK  load van corrupt JSON valt terug op defaults")


def test_load_unknown_fields_ignored():
    cleanup()
    # Schrijf JSON met onbekende velden + bekende velden
    with open(BoothSettings.path(), "w", encoding="utf-8") as f:
        json.dump({
            "countdown_seconds": 9,
            "unknown_field_xyz": "garbage",
            "camera_mode": "dslr",
            "another_unknown": 42,
        }, f)
    bs = BoothSettings.load()
    assert_eq(bs.countdown_seconds, 9)
    assert_eq(bs.camera_mode, "dslr")
    # unknown fields niet als attribute toegevoegd
    assert not hasattr(bs, "unknown_field_xyz")
    print("OK  load filtert onbekende velden")


def test_atomic_write():
    cleanup()
    bs = BoothSettings()
    bs.save()
    # Zorg dat de .tmp-file weg is na een succesvolle save
    assert not os.path.isfile(BoothSettings.path() + ".tmp")
    print("OK  atomic write — tmp opgeruimd")


def test_field_names_helper():
    names = field_names()
    # Verifieer dat een paar verwachte velden erin zitten
    for f in ("countdown_seconds", "photo_delay_ms", "camera_mode",
              "payment_method", "pin_code", "auto_print"):
        assert f in names, f"missing field in field_names(): {f}"
    # En een paar per-event velden niet
    for f in ("id", "name", "date", "template_name", "intro_text",
              "session_count", "payment_link_url"):
        assert f not in names, f"per-event field should NOT be in field_names(): {f}"
    print(f"OK  field_names() returns {len(names)} booth-wide velden, geen per-event")


def test_field_names_count():
    """Tel veldnamen om scope expliciet vast te leggen."""
    names = field_names()
    # Snapshot — als dit getal verandert is dat een bewuste keuze
    expected_count = 47
    assert_eq(len(names), expected_count,
              "Aantal booth-wide velden gewijzigd?")
    print(f"OK  exact {expected_count} booth-wide velden")


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
            failed = True
    shutil.rmtree(_TMPROOT, ignore_errors=True)
    print()
    if failed:
        sys.exit(1)
    print("ALLE TESTS GESLAAGD")
