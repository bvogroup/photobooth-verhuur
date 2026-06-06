"""Test: stuur DNP STATUS commando via libusb0."""
import sys
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
import usb.core, usb.util, usb.backend.libusb0

# Probeer eerst nieuwe install-path, val terug op oude
import os
DLL_PATHS = [
    r"C:\Program Files\LibUSB-Win32\bin\amd64\libusb0.dll",
    r"C:\Program Files (x86)\LibUSB-Win32\bin\amd64\libusb0.dll",
    r"C:\temp\libusb-bin-1.4.0.2\libusb-win32-bin-1.4.0.2\bin\amd64\libusb0.dll",
    r"C:\temp\libusb-win32-bin-1.2.7.3\bin\amd64\libusb0.dll",
]
DLL = next((p for p in DLL_PATHS if os.path.isfile(p)), None)
print(f"Using DLL: {DLL}")

backend = usb.backend.libusb0.get_backend(find_library=lambda x: DLL)
dev = usb.core.find(idVendor=0x1452, idProduct=0x9201, backend=backend)
if dev is None:
    print("FAIL: QW410 niet gevonden")
    raise SystemExit(1)

# Forceer configuration set (libusb-win32 vereist dat)
try:
    dev.set_configuration()
    print("✓ set_configuration() OK")
except Exception as e:
    print(f"set_configuration fout (mogelijk OK): {e}")

# Get endpoints
cfg = dev.get_active_configuration()
intf = cfg[(0, 0)]
out_ep = usb.util.find_descriptor(intf,
    custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
)
in_ep = usb.util.find_descriptor(intf,
    custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN
)
print(f"OUT: 0x{out_ep.bEndpointAddress:02x}  IN: 0x{in_ep.bEndpointAddress:02x}")

# Claim
try:
    usb.util.claim_interface(dev, intf.bInterfaceNumber)
    print("✓ Interface claimed")
except Exception as e:
    print(f"claim_interface fout: {e}")
    raise SystemExit(1)

def dnp_cmd(arg1: bytes, arg2: bytes = b"") -> bytes:
    hdr = bytearray(b" " * 32)
    hdr[0] = 0x1B; hdr[1] = 0x50
    hdr[2:2+min(len(arg1),6)] = arg1[:6]
    hdr[8:8+min(len(arg2),16)] = arg2[:16]
    hdr[24:32] = b"00000000"
    return bytes(hdr)

def query(arg1, arg2=b""):
    print(f"\n--- Query {arg1!r} {arg2!r} ---")
    cmd = dnp_cmd(arg1, arg2)
    print(f"Sending {len(cmd)} bytes: {cmd!r}")
    try:
        n = out_ep.write(cmd, timeout=3000)
        print(f"  wrote {n} bytes")
    except Exception as e:
        print(f"  WRITE FAIL: {e}")
        return
    try:
        len_buf = in_ep.read(8, timeout=3000)
        length = int(bytes(len_buf).decode("ascii").strip())
        print(f"  response length: {length} (raw={bytes(len_buf)!r})")
    except Exception as e:
        print(f"  READ LEN FAIL: {e}")
        return
    if length > 0:
        try:
            data = in_ep.read(length, timeout=3000)
            print(f"  data: {bytes(data)!r}")
            print(f"  ascii: {bytes(data).decode('ascii', errors='replace')}")
        except Exception as e:
            print(f"  READ DATA FAIL: {e}")

try:
    query(b"STATUS")
    query(b"INFO", b"SERIAL_NUMBER")
    query(b"INFO", b"FW_VER")
    query(b"INFO", b"MEDIA")
    query(b"MNT_RD", b"COUNTER_LIFE")
finally:
    try:
        usb.util.release_interface(dev, intf.bInterfaceNumber)
        usb.util.dispose_resources(dev)
    except Exception:
        pass
    print("\n✓ Done")
