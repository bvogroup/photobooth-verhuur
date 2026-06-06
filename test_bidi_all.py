"""Test alle bidi mechanisms in Windows print-stack."""
import sys
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
import ctypes
from ctypes import wintypes
import win32print

PRINTER = "DP-QW410 (Kopie 1)"

winspool = ctypes.WinDLL("winspool.drv", use_last_error=True)

# Voor EnumPrinterKey: lijst ALLE bidi-subkeys
winspool.EnumPrinterKeyW.argtypes = [
    wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPWSTR,
    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
]
winspool.EnumPrinterKeyW.restype = wintypes.DWORD

# EnumPrinterDataEx
winspool.EnumPrinterDataExW.argtypes = [
    wintypes.HANDLE, wintypes.LPCWSTR, ctypes.c_void_p,
    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ctypes.POINTER(wintypes.DWORD),
]
winspool.EnumPrinterDataExW.restype = wintypes.DWORD


def list_keys(h, parent):
    needed = wintypes.DWORD(0)
    err = winspool.EnumPrinterKeyW(h, parent, None, 0, ctypes.byref(needed))
    if needed.value == 0:
        return [], err
    buf = ctypes.create_unicode_buffer(needed.value)
    err = winspool.EnumPrinterKeyW(h, parent, buf, needed.value * 2, ctypes.byref(needed))
    if err != 0:
        return [], err
    keys = [p for p in buf[:].split('\x00') if p]
    return keys, 0


def list_values(h, key):
    """Enumerate values via EnumPrinterDataEx."""
    needed = wintypes.DWORD(0)
    nvals = wintypes.DWORD(0)
    err = winspool.EnumPrinterDataExW(h, key, None, 0, ctypes.byref(needed), ctypes.byref(nvals))
    if needed.value == 0:
        return [], err
    buf = (ctypes.c_ubyte * needed.value)()
    err = winspool.EnumPrinterDataExW(h, key, buf, needed.value, ctypes.byref(needed), ctypes.byref(nvals))
    if err != 0:
        return [], err
    # Parse PRINTER_ENUM_VALUES structures
    # struct: PWSTR pValueName; DWORD cbValueName; DWORD dwType; LPBYTE pData; DWORD cbData
    sizeof_pew = 8 + 4 + 4 + 8 + 4  # 28 bytes per entry (with alignment maybe 32)
    raw = bytes(buf)
    # Better approach: use ctypes Structure
    class PRINTER_ENUM_VALUES(ctypes.Structure):
        _fields_ = [
            ("pValueName", wintypes.LPWSTR),
            ("cbValueName", wintypes.DWORD),
            ("dwType", wintypes.DWORD),
            ("pData", ctypes.c_void_p),
            ("cbData", wintypes.DWORD),
        ]

    Array = PRINTER_ENUM_VALUES * nvals.value
    arr = Array.from_buffer(buf)

    results = []
    for i in range(nvals.value):
        item = arr[i]
        name = item.pValueName or ""
        if item.cbData and item.pData:
            data = bytes((ctypes.c_ubyte * item.cbData).from_address(item.pData))
        else:
            data = b""
        results.append((name, item.dwType, data))
    return results, 0


def walk_keys(h, parent, depth=0):
    """Recursief alle keys + values doorlopen."""
    keys, err = list_keys(h, parent)
    if err != 0 and parent != "":
        return
    for k in keys:
        full = f"{parent}\\{k}" if parent else k
        print(f"{'  '*depth}[{full}]")
        # Values onder deze key
        vals, _err = list_values(h, full)
        for name, typ, data in vals:
            type_str = {1: "REG_SZ", 4: "REG_DWORD", 3: "REG_BINARY", 7: "REG_MULTI_SZ"}.get(typ, f"type{typ}")
            preview = ""
            if typ == 1 or typ == 7:
                try:
                    preview = data.decode("utf-16-le", errors="replace").rstrip("\x00")[:60]
                except Exception:
                    preview = repr(data[:30])
            elif typ == 4 and len(data) >= 4:
                preview = str(int.from_bytes(data[:4], "little"))
            elif typ == 3:
                preview = data[:24].hex()
            print(f"{'  '*depth}  {name!r} ({type_str}, {len(data)}b): {preview}")
        # Recursie
        walk_keys(h, full, depth + 1)


def main():
    h_obj = win32print.OpenPrinter(PRINTER)
    h = int(h_obj)
    print(f"=== Volledig PrinterData-tree voor {PRINTER} ===\n")
    walk_keys(h, "")
    win32print.ClosePrinter(h_obj)


if __name__ == "__main__":
    main()
