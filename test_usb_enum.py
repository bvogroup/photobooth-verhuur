"""Enumeratie van USB-devices via pyusb — read-only, geen claim.
Doel: vinden welke vendor/product IDs de QW410 heeft."""
import sys
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)

import os
import libusb
import usb.core
import usb.backend.libusb1

# pyusb-backend expliciet wijzen naar de DLL die het 'libusb'-package levert
_dll_dir = os.path.join(os.path.dirname(libusb.__file__), '_platform', 'windows', 'x86_64')
if os.path.isdir(_dll_dir):
    os.add_dll_directory(_dll_dir)
    print(f"libusb DLL dir: {_dll_dir}")
_backend = usb.backend.libusb1.get_backend(
    find_library=lambda x: os.path.join(_dll_dir, 'libusb-1.0.dll')
)
if _backend is None:
    print("FAILED: pyusb kan libusb-1.0.dll niet laden")
    raise SystemExit(1)
print(f"backend: {_backend}")

# DNP / Citizen vendor IDs uit Gutenprint
DNP_VENDOR_IDS = {0x1343, 0x1452, 0x1209}  # Citizen, DNP, generic

print("=" * 64)
print("  USB enumeratie — alle DNP/Citizen devices")
print("=" * 64)
print()

found_any = False
for dev in usb.core.find(find_all=True, backend=_backend):
    try:
        if dev.idVendor in DNP_VENDOR_IDS:
            found_any = True
            print(f"FOUND: VID=0x{dev.idVendor:04x} PID=0x{dev.idProduct:04x}")
            try:
                print(f"  Manufacturer: {usb.util.get_string(dev, dev.iManufacturer)}")
            except Exception:
                print(f"  Manufacturer: (kon string niet lezen)")
            try:
                print(f"  Product:      {usb.util.get_string(dev, dev.iProduct)}")
            except Exception:
                print(f"  Product:      (kon string niet lezen)")
            try:
                print(f"  Serial:       {usb.util.get_string(dev, dev.iSerialNumber)}")
            except Exception:
                print(f"  Serial:       (kon string niet lezen)")
            print(f"  USB versie:   {dev.bcdUSB:#06x}")
            print(f"  Device class: {dev.bDeviceClass}")
            print(f"  Configs:      {dev.bNumConfigurations}")
            try:
                cfg = dev.get_active_configuration()
                for intf in cfg:
                    print(f"  Interface {intf.bInterfaceNumber} "
                          f"class=0x{intf.bInterfaceClass:02x} "
                          f"sub=0x{intf.bInterfaceSubClass:02x} "
                          f"proto=0x{intf.bInterfaceProtocol:02x}")
                    for ep in intf:
                        direction = "IN " if ep.bEndpointAddress & 0x80 else "OUT"
                        ep_type = {0: "CTRL", 1: "ISOC", 2: "BULK", 3: "INTR"}.get(
                            ep.bmAttributes & 0x03, "?"
                        )
                        print(f"    endpoint 0x{ep.bEndpointAddress:02x} "
                              f"{direction} {ep_type} max_pkt={ep.wMaxPacketSize}")
            except Exception as e:
                print(f"  (config lezen: {e})")
            print()
    except Exception as e:
        print(f"  fout bij device: {e}")

if not found_any:
    print("Geen DNP/Citizen device gevonden via deze VIDs. Alle devices:")
    for dev in usb.core.find(find_all=True, backend=_backend):
        try:
            try:
                product = usb.util.get_string(dev, dev.iProduct) or "?"
            except Exception:
                product = "?"
            print(f"  VID=0x{dev.idVendor:04x} PID=0x{dev.idProduct:04x}  {product}")
        except Exception:
            pass
