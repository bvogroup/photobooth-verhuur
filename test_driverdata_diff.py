"""Watch PrinterData voor wijzigingen — zien of de DPQW410UI dialog
deze updates via spooler registry blob."""
import sys
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
import time
import winreg

PRINTER = "DP-QW410 (Kopie 2)"  # de actieve printer
KEY = rf"SYSTEM\CurrentControlSet\Control\Print\Printers\{PRINTER}\PrinterDriverData"

def read_blob():
    h = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, KEY)
    out = {}
    i = 0
    while True:
        try:
            name, val, typ = winreg.EnumValue(h, i)
            out[name] = (typ, val)
            i += 1
        except OSError:
            break
    winreg.CloseKey(h)
    return out

prev = read_blob()
print(f"Watching {PRINTER}\\PrinterDriverData ({len(prev)} values)")
print("Open de Voorkeursinstellingen dialog → Printer Info tab → klik 'Update(U)' knop")
print("Daarna: maak iets aan de printer (klep open / dicht / papier / lint) en klik Update opnieuw.")
print("Ik print alle veranderingen ↓\n")

while True:
    time.sleep(0.5)
    try:
        cur = read_blob()
    except Exception as e:
        print(f"read err: {e}")
        time.sleep(1)
        continue
    # Diff
    changes = []
    all_keys = set(prev.keys()) | set(cur.keys())
    for k in all_keys:
        if k not in prev:
            changes.append((k, "NEW", None, cur[k]))
        elif k not in cur:
            changes.append((k, "DEL", prev[k], None))
        elif prev[k] != cur[k]:
            changes.append((k, "MOD", prev[k], cur[k]))
    if changes:
        t = time.strftime("%H:%M:%S")
        print(f"[{t}] {len(changes)} change(s):")
        for name, op, old, new in changes:
            print(f"  {op}  {name!r}")
            if isinstance(new, tuple) and isinstance(new[1], bytes):
                print(f"    new len={len(new[1])} hex={new[1][:64].hex()}")
            else:
                print(f"    new = {new!r}")
            if isinstance(old, tuple) and isinstance(old[1], bytes):
                print(f"    old len={len(old[1])} hex={old[1][:64].hex()}")
            else:
                print(f"    old = {old!r}")
        prev = cur
