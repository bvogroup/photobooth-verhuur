"""Test: gebruik libusb0 backend (van libusb-win32 filter)."""
import os
import sys
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

import usb.core
import usb.backend.libusb0

# libusb0.dll uit de libusb-win32 install (zelfde bin folder)
LIBUSB0_DLL = r"C:\temp\libusb-win32-bin-1.2.7.3\bin\amd64\libusb0.dll"

print(f"Probeer libusb0 backend met: {LIBUSB0_DLL}")
print(f"  bestaat? {os.path.isfile(LIBUSB0_DLL)}")

backend = usb.backend.libusb0.get_backend(
    find_library=lambda x: LIBUSB0_DLL
)
print(f"  backend: {backend}")
if backend is None:
    print("FAIL: libusb0 backend kon niet geladen worden")
    raise SystemExit(1)

print("\nEnumeratie via libusb0:")
found = False
for dev in usb.core.find(find_all=True, backend=backend):
    print(f"  VID=0x{dev.idVendor:04x} PID=0x{dev.idProduct:04x}")
    if dev.idVendor == 0x1452 and dev.idProduct == 0x9201:
        found = True
        print("  ↑ QW410 GEVONDEN via libusb0!")
        try:
            cfg = dev.get_active_configuration()
            print(f"  Active config: bConfigurationValue={cfg.bConfigurationValue}")
            for intf in cfg:
                print(f"    Interface {intf.bInterfaceNumber} class=0x{intf.bInterfaceClass:02x}")
                for ep in intf:
                    direction = "IN" if ep.bEndpointAddress & 0x80 else "OUT"
                    ep_type = {0: "CTRL", 1: "ISOC", 2: "BULK", 3: "INTR"}.get(
                        ep.bmAttributes & 0x03, "?"
                    )
                    print(f"      EP 0x{ep.bEndpointAddress:02x} {direction} {ep_type} pkt={ep.wMaxPacketSize}")
        except Exception as e:
            print(f"  Config-fout: {e}")

if not found:
    print("\n!! QW410 NIET gevonden via libusb0 — filter werkt nog niet")
