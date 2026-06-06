"""Brute-force test van GetPrinterDataEx met DNP-specifieke keys/values.
Hypothese: de DNP-driver registreert een callback die bij elke call live
USB-I/O doet, gebaseerd op de DPQW410UI.DLL imports (GetPrinterDataExW
+ RegQueryValueExW + Events)."""
import sys
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
import ctypes
from ctypes import wintypes
import win32print

PRINTER = "DP-QW410 (Kopie 2)"  # de actieve printer (USB011)

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


def show(label, key, value):
    data, dt, err = get(h, key, value)
    if data:
        print(f"  ✅ key={key!r} value={value!r} type={dt} len={len(data)}")
        # Toon decode
        if dt in (1, 7):
            try:
                s = data.decode("utf-16-le", errors="replace").rstrip("\x00")
                print(f"     str: {s[:120]!r}")
            except: pass
        if dt == 4 and len(data) >= 4:
            print(f"     int: {int.from_bytes(data[:4], 'little')}")
        # Hex
        print(f"     hex: {data[:48].hex()}")


h_obj = win32print.OpenPrinter(PRINTER)
h = int(h_obj)
print(f"Open: {PRINTER}, handle=0x{h:x}")

# Brute-force key x value combinations
# De DLL heeft strings "STATUS", "INFO ", "MEDIA", "MNT_RD", "COUNTER_LIFE",
# "FVER", "SERIAL_NUMBER", "MQTY", "TBL_RD", "CWD300_Version" — dat zijn
# DNP-commando args. We weten niet of ze als key OF als value gestuurd worden.

KEY_CANDIDATES = [
    "",
    "STATUS",
    "INFO",
    "INFO MEDIA",
    "INFO SERIAL_NUMBER",
    "INFO FVER",
    "INFO MQTY",
    "MNT_RD COUNTER_LIFE",
    "TBL_RD CWD300_Version",
    "MEDIA",
    "MQTY",
    "FVER",
    "COUNTER_LIFE",
    "SERIAL_NUMBER",
    "Status",
    "PrinterStatus",
    "DeviceStatus",
    "DPRT",
    "DNP",
    "QW410",
    "DP-QW410",
    "Bidi",
    "BidiStatus",
    "DriverPrint",
]

VALUE_CANDIDATES = [
    "",
    "STATUS",
    "GetStatus",
    "QUERY",
    "Query",
    "INFO MEDIA", "MEDIA", "Media",
    "INFO MQTY", "MQTY", "MediaQuantity",
    "INFO SERIAL_NUMBER", "SERIAL_NUMBER", "SerialNumber",
    "INFO FVER", "FVER", "Firmware", "FirmwareVersion",
    "MNT_RD COUNTER_LIFE", "COUNTER_LIFE", "Counter", "LifeCounter",
    "TBL_RD CWD300_Version",
    "PrinterStatus",
    "DeviceStatus",
    "StatusInformation",
    "DetectedErrorState",
    "DetectedSeverity",
    "PrinterState",
    "PaperMode",
    "PaperReady",
    "RibbonStatus",
]

print(f"\nProberen {len(KEY_CANDIDATES) * len(VALUE_CANDIDATES)} combinaties...\n")
found = 0
for k in KEY_CANDIDATES:
    for v in VALUE_CANDIDATES:
        data, dt, err = get(h, k, v)
        if data:
            found += 1
            kstr = k if k else "(root)"
            print(f"  ✅ key={kstr!r} value={v!r} type={dt} len={len(data)}")
            if dt in (1, 7):
                try:
                    s = data.decode("utf-16-le", errors="replace").rstrip("\x00")
                    print(f"     str: {s[:120]!r}")
                except: pass
            if dt == 4 and len(data) >= 4:
                print(f"     int: {int.from_bytes(data[:4], 'little')}")
            print(f"     hex: {data[:48].hex()}")
            print()

print(f"\nTotaal gevonden: {found}")
win32print.ClosePrinter(h_obj)
