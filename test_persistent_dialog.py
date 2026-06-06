"""Test: open dialog 1x, doe N opeenvolgende reads, meet tijd per read.
Doel: bepalen of dialog open houden + Update klik + scrape snel genoeg
is voor 2-sec polling (target < 1 sec per poll)."""
import sys
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
import time
import subprocess
import uiautomation as auto

PRINTER = "DP-QW410 (Kopie 2)"

t0 = time.monotonic()
print(f"[{0:.2f}s] Open dialog 1x...")
proc = subprocess.Popen(["rundll32", "printui.dll,PrintUIEntry", "/e", "/n", PRINTER])

# Wacht op dialog
dlg = None
deadline = time.monotonic() + 6
while time.monotonic() < deadline:
    for w in auto.GetRootControl().GetChildren():
        try:
            name = w.Name or ""
            if PRINTER in name and w.ControlTypeName == "WindowControl":
                if "Settings" not in name and "Instellingen" not in name:
                    dlg = w
                    break
        except Exception:
            pass
    if dlg and dlg.Exists(0):
        break
    time.sleep(0.1)

if not dlg:
    print("FAIL: dialog niet gevonden")
    sys.exit(1)
print(f"[{time.monotonic()-t0:.2f}s] Dialog gevonden — verbergen off-screen + select Printer Info tab")

dlg.MoveWindow(-3000, -3000, 800, 800)
time.sleep(0.2)

# Vind tab + selecteer
def find_descendant(node, predicate, maxd=10):
    if maxd <= 0: return None
    try:
        for c in node.GetChildren():
            try:
                if predicate(c):
                    return c
            except Exception:
                pass
            r = find_descendant(c, predicate, maxd-1)
            if r: return r
    except Exception:
        pass
    return None

tab = find_descendant(dlg, lambda c: c.ControlTypeName == "TabItemControl"
                     and "Printer Info" in (c.Name or ""))
sel = tab.GetPattern(auto.PatternId.SelectionItemPattern)
if sel: sel.Select()
time.sleep(0.4)

btn = find_descendant(dlg, lambda c: c.ControlTypeName == "ButtonControl"
                     and (c.Name or "").startswith("Update"))
inv = btn.GetPattern(auto.PatternId.InvokePattern)

print(f"[{time.monotonic()-t0:.2f}s] Setup klaar — start 5 opeenvolgende reads:\n")

def scrape():
    """Scrape relevante text+edit controls. Returnt dict."""
    out = {"ints": [], "status_text": "", "serial": "", "firmware": "", "media": ""}
    def walk(node, depth=0, maxd=10):
        if depth > maxd: return
        try:
            for c in node.GetChildren():
                try:
                    tn = c.ControlTypeName
                    if tn in ("EditControl", "TextControl"):
                        text = (c.Name or "").strip()
                        if tn == "EditControl":
                            try:
                                vp = c.GetValuePattern()
                                if vp and vp.Value:
                                    text = vp.Value.strip()
                                    if text and tn == "EditControl":
                                        out["status_text"] = text
                            except Exception:
                                pass
                        if text:
                            if text.isdigit() and len(text) <= 7:
                                out["ints"].append(int(text))
                            elif text.startswith("QW410 ") and "." in text:
                                out["firmware"] = text
                            elif text.startswith("QW") and " " not in text and 10 <= len(text) <= 14:
                                out["serial"] = text
                            elif not out["media"] and "x" in text and len(text) < 10:
                                parts = text.lower().split("x")
                                if len(parts) == 2 and all(p.replace(".","").isdigit() for p in parts):
                                    out["media"] = text
                except Exception:
                    pass
                walk(c, depth+1, maxd)
        except Exception:
            pass
    walk(dlg)
    return out

for i in range(5):
    t_read = time.monotonic()
    inv.Invoke()
    time.sleep(0.4)  # wacht op driver USB-call
    data = scrape()
    dt = time.monotonic() - t_read
    print(f"  Read #{i+1}: {dt:.2f}s  status={data['status_text']!r}  "
          f"ints={data['ints'][:6]}  serial={data['serial']!r}  fw={data['firmware']!r}")
    time.sleep(0.3)  # kleine pauze tussen reads

print(f"\n[{time.monotonic()-t0:.2f}s] Sluiten...")
dlg.SendKeys("{Esc}")
proc.terminate()
print(f"[{time.monotonic()-t0:.2f}s] Klaar")
