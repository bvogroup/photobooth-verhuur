"""Probeer Windows Bidi Schema namespaces — officiele Windows print bidi standaard."""
import sys
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
import ctypes
from ctypes import wintypes
import win32print

PRINTER = "DP-QW410 (Kopie 2)"

winspool = ctypes.WinDLL("winspool.drv", use_last_error=True)
winspool.GetPrinterDataExW.argtypes = [
    wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPCWSTR,
    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
]
winspool.GetPrinterDataExW.restype = wintypes.DWORD

def get(h, key, value, sz=8192):
    dt = wintypes.DWORD(0); nd = wintypes.DWORD(0)
    buf = (ctypes.c_ubyte * sz)()
    err = winspool.GetPrinterDataExW(h, key, value, ctypes.byref(dt),
                                      buf, sz, ctypes.byref(nd))
    if err == 0 and nd.value > 0:
        return bytes(buf[:nd.value]), dt.value, 0
    return None, dt.value, err

h_obj = win32print.OpenPrinter(PRINTER)
h = int(h_obj)
print(f"Open: {PRINTER}, handle=0x{h:x}\n")

# Windows Bidi Schema-keys (officiele MS namespace)
# https://learn.microsoft.com/en-us/windows-hardware/drivers/print/standard-schema-public-namespaces
BIDI_SCHEMA_KEYS = [
    # Standard public namespaces
    "\\Printer.Status",
    "\\Printer.Status:State",
    "\\Printer.Status:Severity",
    "\\Printer.Status:Reasons",
    "\\Printer.Status:DetectedErrorState",
    "\\Printer.Status:DetectedErrorReasons",
    "\\Printer.Consumables",
    "\\Printer.Consumables:Cartridge",
    "\\Printer.Consumables:Color",
    "\\Printer.Consumables:Level",
    "\\Printer.Consumables:Type",
    "\\Printer.Configuration",
    "\\Printer.Configuration:Memory",
    "\\Printer.Configuration:Duplexer",
    "\\Printer.DeviceInfo",
    "\\Printer.DeviceInfo:DeviceID",
    "\\Printer.DeviceInfo:Manufacturer",
    "\\Printer.DeviceInfo:Model",
    "\\Printer.DeviceInfo:SerialNumber",
    "\\Printer.DeviceInfo:FirmwareVersion",
    "\\Printer.Counts",
    "\\Printer.Counts:LifeCount",
    "\\Printer.Counts:LifetimePages",
    "\\Printer.Trays",
    "\\Printer.Trays:Tray1",
    "\\Printer.Trays:Tray1:MediaName",
    "\\Printer.Trays:Tray1:RemainingCount",
    # XPS / WSD print-schema gestylde keys
    "\\Printer",
    "\\Printer:Status",
    "Printer.Status",
    "Printer:Status",
    "PrinterStatus",
]

print("=== Probeer als KEY (value='') ===")
for k in BIDI_SCHEMA_KEYS:
    data, dt, err = get(h, k, "")
    if data:
        print(f"  ✅ KEY {k!r} type={dt} len={len(data)} err={err}")
        if dt in (1, 7):
            try:
                print(f"     {data.decode('utf-16-le', errors='replace').rstrip(chr(0))[:120]!r}")
            except: pass
        elif dt == 4 and len(data) >= 4:
            print(f"     int: {int.from_bytes(data[:4], 'little')}")
        else:
            print(f"     hex: {data[:48].hex()}")

print()
print("=== Probeer als (key=root, value=schemastring) ===")
for v in BIDI_SCHEMA_KEYS:
    data, dt, err = get(h, "", v)
    if data:
        print(f"  ✅ VALUE {v!r} type={dt} len={len(data)} err={err}")

print()
print("=== Probeer met split key/value op ':' ===")
for k_full in BIDI_SCHEMA_KEYS:
    if ":" not in k_full: continue
    k, v = k_full.rsplit(":", 1)
    data, dt, err = get(h, k, v)
    if data:
        print(f"  ✅ split key={k!r} value={v!r} type={dt} len={len(data)}")

win32print.ClosePrinter(h_obj)
print("\nDone")
