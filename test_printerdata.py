"""Test: GetPrinterDataEx via Windows spooler API — bidi channel.

Probeert verschillende bidi-keys + DNP-specifieke namespaces die de
DPQW410UI.DLL mogelijk gebruikt om status te lezen.
"""
import sys
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
import ctypes
from ctypes import wintypes
import win32print

PRINTER = "DP-QW410 (Kopie 1)"

# Win32 API binding voor GetPrinterDataExW
winspool = ctypes.WinDLL("winspool.drv", use_last_error=True)
winspool.GetPrinterDataExW.argtypes = [
    wintypes.HANDLE,            # hPrinter
    wintypes.LPCWSTR,           # pKeyName
    wintypes.LPCWSTR,           # pValueName
    ctypes.POINTER(wintypes.DWORD),   # pType
    ctypes.c_void_p,            # pData (LPBYTE)
    wintypes.DWORD,             # nSize
    ctypes.POINTER(wintypes.DWORD),   # pcbNeeded
]
winspool.GetPrinterDataExW.restype = wintypes.DWORD

# EnumPrinterDataExW om alle bidi keys op te sommen
winspool.EnumPrinterDataExW.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCWSTR,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
]
winspool.EnumPrinterDataExW.restype = wintypes.DWORD

# EnumPrinterKey - lijst alle subkeys van een PrinterData-key
winspool.EnumPrinterKeyW.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCWSTR,
    wintypes.LPWSTR,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
winspool.EnumPrinterKeyW.restype = wintypes.DWORD


def get_printer_data_ex(h, key, value, max_size=4096):
    """Roep GetPrinterDataEx aan. Return (data_bytes, type_int, error_int)."""
    data_type = wintypes.DWORD(0)
    needed = wintypes.DWORD(0)
    buf = (ctypes.c_ubyte * max_size)()
    err = winspool.GetPrinterDataExW(
        h, key, value, ctypes.byref(data_type),
        buf, max_size, ctypes.byref(needed)
    )
    if err == 0:
        return bytes(buf[:needed.value]), data_type.value, 0
    return None, data_type.value, err


def list_keys(h, parent_key=""):
    """Lijst subkeys van een PrinterData-pad."""
    needed = wintypes.DWORD(0)
    # First call: get needed size
    err = winspool.EnumPrinterKeyW(
        h, parent_key, None, 0, ctypes.byref(needed)
    )
    if needed.value == 0:
        return [], err
    buf = ctypes.create_unicode_buffer(needed.value // 2)
    err = winspool.EnumPrinterKeyW(
        h, parent_key, buf, needed.value, ctypes.byref(needed)
    )
    if err != 0:
        return [], err
    # buf bevat \0 gescheiden strings, eindigt met \0\0
    raw = buf[:]
    keys = []
    parts = raw.split('\x00')
    for p in parts:
        if p:
            keys.append(p)
    return keys, 0


def main():
    h_obj = win32print.OpenPrinter(PRINTER)
    h = int(h_obj)
    print(f"Printer: {PRINTER}, handle=0x{h:x}\n")

    # Stap 1: list alle root keys
    keys, err = list_keys(h, "")
    print(f"=== Root keys (err={err}) ===")
    for k in keys:
        print(f"  {k}")
    print()

    # Stap 2: voor elke key, list values
    interesting_keys = [""] + keys + [
        # Bekende Windows bidi-namespaces om handmatig te proberen
        "PrinterDataKey",
        r"Bidi\Status",
        r"Bidi\Job",
        r"Status",
        r"DsDriver",
        r"DsSpooler",
        r"PrinterDriverData",
        # DNP-specifieke gokken op basis van DPQW410UI.DLL strings
        r"DPRT",
        r"DPRT\Status",
        r"DP-QW410",
        r"DP-QW410\Status",
    ]
    for key in set(interesting_keys):
        # Voor elke key: probeer een aantal value-namen
        for value in [
            "STATUS", "Status",
            "INFO MEDIA", "MEDIA", "Media",
            "MNT_RD COUNTER_LIFE", "COUNTER_LIFE", "LifeCounter",
            "INFO SERIAL_NUMBER", "SERIAL_NUMBER", "SerialNumber",
            "INFO FVER", "FVER", "Firmware",
            "PrinterStatus", "DeviceStatus",
            "BidiStatus", "DetectedErrorState",
        ]:
            data, dtype, err = get_printer_data_ex(h, key, value)
            if data is not None and len(data) > 0:
                key_str = key if key else "(root)"
                print(f"  ✓ key={key_str!r} value={value!r} type={dtype} len={len(data)}")
                # Toon eerste 80 bytes als ascii/hex
                ascii_str = "".join(chr(b) if 0x20 <= b < 0x7e else "." for b in data[:80])
                hex_str = " ".join(f"{b:02x}" for b in data[:32])
                print(f"    hex:   {hex_str}")
                print(f"    ascii: {ascii_str}")

    win32print.ClosePrinter(h_obj)
    print("\nDone")


if __name__ == "__main__":
    main()
