"""Verify dat oude events correct migreren naar nieuw payment_method veld."""

import json
import os
import sys
import tempfile

sys.path.insert(0, "C:\\Photobooth")
from event_model import Event


def test_legacy_sumup_migrates():
    """Oud event met sumup_enabled=true -> payment_method='sumup'."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
    json.dump({
        "id": "old1",
        "name": "Bruiloft 2024",
        "sumup_enabled": True,
        "payment_enabled": False,
    }, tmp)
    tmp.close()
    try:
        ev = Event.load(tmp.name)
        assert ev.payment_method == "sumup", f"got {ev.payment_method}"
        assert ev.sumup_enabled is True, "old field should still be set"
        print("OK  legacy sumup -> payment_method='sumup'")
    finally:
        os.unlink(tmp.name)


def test_legacy_stripe_migrates():
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
    json.dump({
        "id": "old2",
        "name": "Old Stripe",
        "payment_enabled": True,
        "sumup_enabled": False,
    }, tmp)
    tmp.close()
    try:
        ev = Event.load(tmp.name)
        assert ev.payment_method == "stripe", f"got {ev.payment_method}"
        print("OK  legacy stripe -> payment_method='stripe'")
    finally:
        os.unlink(tmp.name)


def test_no_legacy_payment():
    """Event zonder payment-velden -> payment_method='none'."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
    json.dump({"id": "old3", "name": "Geen betaling"}, tmp)
    tmp.close()
    try:
        ev = Event.load(tmp.name)
        assert ev.payment_method == "none", f"got {ev.payment_method}"
        print("OK  no legacy payment -> payment_method='none'")
    finally:
        os.unlink(tmp.name)


def test_explicit_payment_method_wins():
    """Als payment_method al expliciet gezet is, niet overschrijven door migratie."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
    json.dump({
        "id": "new1",
        "name": "Voucher event",
        "payment_method": "voucher",
        "sumup_enabled": True,  # tegenstrijdige legacy data
    }, tmp)
    tmp.close()
    try:
        ev = Event.load(tmp.name)
        assert ev.payment_method == "voucher", f"got {ev.payment_method}"
        print("OK  explicit payment_method wint van legacy")
    finally:
        os.unlink(tmp.name)


def test_save_roundtrip_preserves_method():
    """Save + load = behoudt payment_method."""
    tmp_dir = tempfile.mkdtemp()
    try:
        ev = Event.create_new("Test")
        ev.payment_method = "voucher"
        ev.save(tmp_dir)
        loaded = Event.load(os.path.join(tmp_dir, f"{ev.id}.json"))
        assert loaded.payment_method == "voucher"
        print("OK  save+load roundtrip behoudt payment_method")
    finally:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    print(f"Running {len(tests)} migration tests...\n")
    for fn in tests:
        try:
            fn()
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
            sys.exit(1)
        except Exception as e:
            print(f"ERR  {fn.__name__}: {type(e).__name__}: {e}")
            sys.exit(1)
    print("\nALLE MIGRATIE-TESTS GESLAAGD")
