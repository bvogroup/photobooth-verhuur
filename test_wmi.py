"""Test: WMI status voor QW410."""
import sys
sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
import wmi

c = wmi.WMI()
print("=" * 60)
print("  Win32_Printer status")
print("=" * 60)
for p in c.Win32_Printer():
    name = p.Name or ""
    if "QW410" in name.upper() or "DP-QW" in name.upper():
        print(f"\nName: {name!r}")
        for attr in [
            "PrinterStatus", "PrinterState", "DetectedErrorState", "ExtendedDetectedErrorState",
            "ExtendedPrinterStatus", "DeviceID", "Status", "StatusInfo", "WorkOffline",
            "Default", "PortName", "Local", "Network", "Shared", "Published",
            "Attributes", "Availability", "PowerManagementSupported"
        ]:
            try:
                val = getattr(p, attr, None)
                print(f"  {attr:34}= {val!r}")
            except Exception as e:
                print(f"  {attr:34}= <fout: {e}>")

print("\n" + "=" * 60)
print("  CIM_Printer status (uitgebreid)")
print("=" * 60)
try:
    for p in c.CIM_Printer():
        name = p.Name or ""
        if "QW410" in name.upper() or "DP-QW" in name.upper():
            print(f"\nName: {name!r}")
            for attr in p.properties.keys():
                try:
                    val = getattr(p, attr, None)
                    if val is not None:
                        print(f"  {attr:34}= {val!r}")
                except Exception:
                    pass
except Exception as e:
    print(f"CIM_Printer error: {e}")
