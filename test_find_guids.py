"""Vind GUIDs/CLSIDs in DPQW410UI.DLL door binair scan."""
import sys
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
import struct
import re

DLL = r"C:\Windows\System32\spool\drivers\x64\3\DPQW410UI.DLL"

with open(DLL, "rb") as f:
    data = f.read()

# GUID = 16 bytes binary: DWORD + WORD + WORD + 8 bytes BE
# String formaat: {xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx}

# Eerst: zoek strings (ASCII / UTF-16) die op GUID lijken
ascii_str = data.decode("latin-1", errors="replace")
utf16_str = data.decode("utf-16-le", errors="replace")

guid_pat = re.compile(r"\{[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}\}")
ascii_guids = set(guid_pat.findall(ascii_str))
utf16_guids = set(guid_pat.findall(utf16_str))

print("=== GUIDs als ASCII-string ===")
for g in sorted(ascii_guids):
    print(f"  {g}")
print()
print("=== GUIDs als UTF-16-string ===")
for g in sorted(utf16_guids):
    print(f"  {g}")

# Bekende print-COM CLSIDs/IIDs
KNOWN = {
    "{D945D9E5-99B3-46D8-A8F4-2C09D8E8E2E0}": "IPrintCoreUI3 (gokken)",
    "{0AEEE2A7-5A0D-4FCC-91CD-307A2A18F36E}": "IPrintCoreUI3 (gedocumenteerd)",
    "{D945D9E0-99B3-46D8-A8F4-2C09D8E8E2E0}": "PrintCore CLSID",
    "{6D6ABF26-9F38-11D1-882A-00C04FB961EC}": "IPrintOemUI3",
    "{2BF5A4A0-1E0A-11D2-9C70-00C04FB961EC}": "IPrintOemUI2",
    "{4159A4B0-1D6A-11D1-9082-006008059382}": "IPrintOemUI",
    "{B3F7E1B7-6D5B-4F7A-92B7-5B16E1E1A2A8}": "IPrintBidiCommunication (vermoed)",
    "{F8F70E04-D2E1-4FDC-8D17-E84A0D8F1C0E}": "IPrintBidiClient",
    "{08B25CA0-7E03-470B-93D9-CDFEBDDE2EB1}": "IPrintAsyncCover",
    "{00000001-0000-0000-C000-000000000046}": "IClassFactory",
    "{00000000-0000-0000-C000-000000000046}": "IUnknown",
}
print()
print("=== Bekende COM-interfaces gematched ===")
all_guids = ascii_guids | utf16_guids
for g in all_guids:
    if g.upper() in KNOWN:
        print(f"  ✓ {g} → {KNOWN[g.upper()]}")
    elif g.lower() in [k.lower() for k in KNOWN]:
        print(f"  ✓ {g} (case mismatch)")

# Print GUIDs in binary form ook
# IPrintCoreUI3 binary: D9 45 D9 E0/E5 ... etc
print()
print("=== Binary GUID scan ===")
# Microsoft IIDs voor print
known_iids_bin = {
    bytes.fromhex("D945D9E099B346D8A8F42C09D8E8E2E0"): "??",
    bytes.fromhex("E5D945D9B3990CFD8A8F42C09D8E8E2E"): "??",
}
# Maar makkelijker: scan voor patroon "26 D5 6A 6D 38 9F D1 11 88 2A 00 C0 4F B9 61 EC" (IPrintOemUI3 binary little-endian)
patterns = {
    bytes.fromhex("26BF6A6D389FD11188 2A 00 C0 4F B9 61 EC".replace(" ", "")): "IPrintOemUI3",
    bytes.fromhex("A0A4F52B0A1ED21 19C 7000 C0 4F B9 61 EC".replace(" ", "")): "IPrintOemUI2",
    bytes.fromhex("D9 45 D9 E5 99 B3 46 D8 A8 F4 2C 09 D8 E8 E2 E0".replace(" ", "")): "Guess1",
}
for bin_iid, label in patterns.items():
    idx = data.find(bin_iid)
    if idx >= 0:
        print(f"  ✓ {label} found at file-offset {idx}")
