"""Test: stuur DNP STATUS commando via WritePrinter, lees response via ReadPrinter (ctypes).

pywin32 wrapt `ReadPrinter` niet, dus we doen 'm direct via ctypes/winspool.drv.
"""
import sys
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

import time
import ctypes
from ctypes import wintypes
import win32print

PRINTER_NAME = "DP-QW410 (Kopie 1)"

# Win32 API binding voor ReadPrinter
winspool = ctypes.WinDLL("winspool.drv", use_last_error=True)
winspool.ReadPrinter.argtypes = [
    wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)
]
winspool.ReadPrinter.restype = wintypes.BOOL


def read_printer(handle: int, buf_size: int = 4096) -> bytes | None:
    """Lees bytes terug via winspool.drv ReadPrinter."""
    buf = (ctypes.c_ubyte * buf_size)()
    n_read = wintypes.DWORD(0)
    ok = winspool.ReadPrinter(handle, buf, buf_size, ctypes.byref(n_read))
    if not ok:
        err = ctypes.get_last_error()
        return None, err
    return bytes(buf[:n_read.value]), 0


def dnp_cmd(arg1: bytes, arg2: bytes = b"", payload: bytes = b"") -> bytes:
    hdr = bytearray(b" " * 32)
    hdr[0] = 0x1B
    hdr[1] = 0x50
    hdr[2:2+min(len(arg1), 6)] = arg1[:6]
    hdr[8:8+min(len(arg2), 16)] = arg2[:16]
    plen = f"{len(payload):08d}".encode("ascii")
    hdr[24:32] = plen
    return bytes(hdr) + payload


def hexdump(b: bytes, prefix: str = "    ") -> str:
    lines = []
    for i in range(0, len(b), 16):
        chunk = b[i:i+16]
        hex_part = " ".join(f"{x:02x}" for x in chunk)
        ascii_part = "".join(chr(x) if 0x20 <= x < 0x7f else "." for x in chunk)
        lines.append(f"{prefix}{i:04x}  {hex_part:<48}  {ascii_part}")
    return "\n".join(lines)


def attempt_query(cmd_name: bytes, arg2: bytes = b"") -> bytes | None:
    print(f"\n--- Query: arg1={cmd_name!r}, arg2={arg2!r} ---")
    cmd = dnp_cmd(cmd_name, arg2)

    # OpenPrinter met PRINTER_ACCESS_USE zodat StartDocPrinter werkt
    h_obj = win32print.OpenPrinter(PRINTER_NAME)
    # Haal het echte HANDLE-getal op (intern is dit een PyHANDLE)
    h_int = int(h_obj)
    try:
        job_id = win32print.StartDocPrinter(h_obj, 1, ("DNP query", None, "RAW"))
        try:
            win32print.StartPagePrinter(h_obj)
            n = win32print.WritePrinter(h_obj, cmd)
            print(f"  WritePrinter: {n} bytes verzonden")
            win32print.EndPagePrinter(h_obj)

            # Probeer meerdere keren te lezen, met korte delay
            total = b""
            for attempt in range(5):
                resp, err = read_printer(h_int, 4096)
                if resp is None:
                    print(f"  ReadPrinter attempt {attempt}: FOUT (winerr={err})")
                    break
                if resp:
                    total += resp
                    print(f"  ReadPrinter attempt {attempt}: {len(resp)} bytes")
                else:
                    print(f"  ReadPrinter attempt {attempt}: 0 bytes")
                time.sleep(0.2)

            if total:
                print(f"  TOTAL response ({len(total)} bytes):")
                print(hexdump(total))
            else:
                print(f"  Geen response data ontvangen")
            return total or None
        finally:
            try:
                win32print.EndDocPrinter(h_obj)
            except Exception as e:
                print(f"  EndDoc fout (negeerbaar): {e}")
            # Cleanup
            try:
                win32print.SetJob(h_obj, job_id, 0, None, win32print.JOB_CONTROL_DELETE)
            except Exception:
                pass
    finally:
        win32print.ClosePrinter(h_obj)


def main():
    print(f"========================================================")
    print(f"  Test: DNP STATUS via Windows spooler bidi (ctypes ReadPrinter)")
    print(f"  Printer: {PRINTER_NAME}")
    print(f"========================================================")

    for arg1, arg2 in [
        (b"STATUS", b""),
        (b"INFO",   b"MEDIA"),
        (b"INFO",   b"SERIAL_NUMBER"),
    ]:
        attempt_query(arg1, arg2)
        time.sleep(0.3)


if __name__ == "__main__":
    main()
