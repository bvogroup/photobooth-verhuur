"""Quick unit tests voor voucher.py — geen externe dependencies."""

import os
import shutil
import sys
import tempfile

# Setup tijdelijke DATA_DIR vóór voucher import (config.DATA_DIR wordt
# bij import vastgesteld)
_TMPROOT = tempfile.mkdtemp(prefix="bootharoo_voucher_test_")
os.environ.setdefault("BOOTHAROO_TEST_DIR", _TMPROOT)

# Patch config.DATA_DIR door eerst config te laden, dan te overrulen
sys.path.insert(0, "C:\\Photobooth")
import config
config.DATA_DIR = _TMPROOT

import voucher


def assert_eq(actual, expected, msg=""):
    if actual != expected:
        raise AssertionError(f"{msg}\n  expected: {expected!r}\n  actual:   {actual!r}")


def test_format_code():
    assert_eq(voucher._format_code("BOOTH", "1A2B", "X23"), "BOOTH-1A2B-X23")
    assert_eq(voucher._format_code("BOOTH", "1A2B", ""),    "BOOTH-1A2B")
    assert_eq(voucher._format_code("",      "1A2B", "X23"), "1A2B-X23")
    assert_eq(voucher._format_code("",      "1A2B", ""),    "1A2B")
    print("OK  _format_code")


def test_generate_unique():
    codes = voucher.generate_codes("EVT", "", 4, "alphanum", 10)
    assert_eq(len(codes), 10, "should generate 10")
    assert_eq(len(set(codes)), 10, "all codes must be unique")
    for c in codes:
        assert c.startswith("EVT-"), f"missing prefix: {c}"
        # Middendeel = 4 alphanum
        mid = c.split("-")[1]
        assert_eq(len(mid), 4)
        for ch in mid:
            assert ch in voucher._CHARSETS["alphanum"]
    print("OK  generate_codes (10 unieke)")


def test_generate_no_prefix_suffix():
    codes = voucher.generate_codes("", "", 6, "digits", 5)
    for c in codes:
        assert "-" not in c, f"unexpected dash in {c}"
        assert_eq(len(c), 6)
        assert c.isdigit()
    print("OK  generate_codes zonder prefix/suffix")


def test_generate_readable_no_confusing():
    codes = voucher.generate_codes("", "", 8, "readable", 20)
    for c in codes:
        for ch in c:
            assert ch not in "01OIL", f"confusing char in {c}"
    print("OK  generate_codes (readable charset, geen 0/1/O/I/L)")


def test_generate_too_many():
    # charset=digits, length=2 -> 100 mogelijkheden, vraag 200
    try:
        voucher.generate_codes("", "", 2, "digits", 200)
    except ValueError as e:
        print(f"OK  generate_codes raised ValueError (te veel): {e}")
        return
    raise AssertionError("expected ValueError")


def test_validate_and_mark_used():
    event_id = "test_event_validate"
    # Reset
    path = voucher._store_path(event_id)
    if os.path.isfile(path):
        os.remove(path)

    codes = voucher.generate_codes("TST", "", 4, "alphanum", 3)
    added = voucher.add_codes_to_store(event_id, codes)
    assert_eq(added, 3)

    data = voucher.load_store(event_id)
    cl = data["codes"]
    assert_eq(len(cl), 3)

    # Validate
    ok, _ = voucher.validate(codes[0], cl)
    assert ok, "first code should be valid"

    # Mark used
    assert voucher.mark_used(event_id, codes[0])

    # Re-load: should now be marked
    data2 = voucher.load_store(event_id)
    entry = voucher.find_code(codes[0], data2["codes"])
    assert entry["used"], "should be used now"
    assert entry["used_at"], "should have timestamp"

    # Re-validate -> already used
    ok2, key = voucher.validate(codes[0], data2["codes"])
    assert not ok2
    assert_eq(key, "voucher_already_used")

    # Wrong code
    ok3, key3 = voucher.validate("NOPE-X1Y2", cl)
    assert not ok3
    assert_eq(key3, "voucher_invalid")

    # Empty
    ok4, key4 = voucher.validate("", cl)
    assert not ok4
    assert_eq(key4, "voucher_empty")
    print("OK  validate + mark_used (case-sensitive store, hl-ongevoelig zoeken)")


def test_case_insensitive():
    event_id = "test_event_case"
    path = voucher._store_path(event_id)
    if os.path.isfile(path):
        os.remove(path)
    codes = voucher.generate_codes("PARTY", "", 4, "letters", 1)
    voucher.add_codes_to_store(event_id, codes)
    data = voucher.load_store(event_id)
    # Lowercase + spaties
    typed = "  " + codes[0].lower() + "  "
    ok, _ = voucher.validate(typed, data["codes"])
    assert ok, f"should be hl-ongevoelig: typed={typed!r}, stored={codes[0]!r}"
    print("OK  case-insensitive + whitespace-strip")


def test_export_formats():
    codes_list = [
        {"code": "A-1234", "used": False, "used_at": None},
        {"code": "A-5678", "used": True,  "used_at": "2026-05-04T19:00:00"},
    ]
    txt = voucher.export_txt(codes_list)
    assert "A-1234" in txt
    assert "A-5678" in txt

    txt_only_unused = voucher.export_txt(codes_list, include_used=False)
    assert "A-1234" in txt_only_unused
    assert "A-5678" not in txt_only_unused

    csv_text = voucher.export_csv(codes_list)
    assert "code,used,used_at" in csv_text
    assert "A-1234,no," in csv_text
    assert "A-5678,yes,2026-05-04T19:00:00" in csv_text
    print("OK  export TXT + CSV")


def test_stats_and_all_used():
    codes_list = [
        {"code": "X1", "used": True, "used_at": "x"},
        {"code": "X2", "used": False, "used_at": None},
        {"code": "X3", "used": True, "used_at": "x"},
    ]
    s = voucher.stats(codes_list)
    assert_eq(s, {"total": 3, "used": 2, "available": 1})
    assert not voucher.all_used(codes_list)
    codes_all_used = [{"code": c, "used": True} for c in ("a", "b")]
    assert voucher.all_used(codes_all_used)
    assert not voucher.all_used([])
    print("OK  stats + all_used")


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    print(f"Running {len(tests)} tests... DATA_DIR={config.DATA_DIR}\n")
    for fn in tests:
        try:
            fn()
        except AssertionError as e:
            print(f"FAIL {fn.__name__}: {e}")
            shutil.rmtree(_TMPROOT, ignore_errors=True)
            sys.exit(1)
        except Exception as e:
            print(f"ERR  {fn.__name__}: {type(e).__name__}: {e}")
            shutil.rmtree(_TMPROOT, ignore_errors=True)
            sys.exit(1)
    shutil.rmtree(_TMPROOT, ignore_errors=True)
    print("\nALLE TESTS GESLAAGD")
