"""Proof-of-concept: open DPQW410UI Voorkeursinstellingen dialog,
klik Update, scrape alle waarden, sluit dialog. Alles automatisch."""
import sys
sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
import time
import subprocess
import uiautomation as auto

PRINTER = "DP-QW410 (Kopie 2)"

t0 = time.monotonic()
print(f"[{0:.1f}s] Starten Voorkeursinstellingen dialog...")

# Start dialog via printui /e (= preferences)
proc = subprocess.Popen([
    "rundll32", "printui.dll,PrintUIEntry",
    "/e", "/n", PRINTER
], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)

# Wacht op dialog
print(f"[{time.monotonic()-t0:.1f}s] Wachten op dialog...")
# Window title patroon: "Voorkeursinstellingen voor afdrukken voor DP-QW410 (Kopie 2)"
dlg = auto.WindowControl(searchDepth=1, Name=f"Voorkeursinstellingen voor afdrukken voor {PRINTER}")
if not dlg.Exists(maxSearchSeconds=8):
    print("FAIL: dialog niet gevonden")
    sys.exit(1)
print(f"[{time.monotonic()-t0:.1f}s] Dialog gevonden!")

# Off-screen — SelectionItemPattern werkt zonder muis dus geen probleem
try:
    dlg.MoveWindow(-3000, -3000, 800, 800)
except Exception:
    pass

# Even wachten op render
time.sleep(0.3)

# Debug: dump volledige hiërarchie
print(f"[{time.monotonic()-t0:.1f}s] Volledige UI-tree dump:")
def dump(node, depth=0, maxd=6):
    if depth > maxd: return
    try:
        children = node.GetChildren()
    except Exception:
        return
    for c in children:
        try:
            name = (c.Name or "").strip()[:60]
            tn = c.ControlTypeName
            print(f"  {'  '*depth}[{tn}] {name!r}")
            dump(c, depth+1, maxd)
        except Exception:
            pass
dump(dlg, maxd=4)

# Eerst: enumeer alle TabItems
print(f"[{time.monotonic()-t0:.1f}s] Enumereer beschikbare tabs:")
def find_tab_items(node, results=None):
    if results is None: results = []
    try:
        for c in node.GetChildren():
            if c.ControlTypeName == "TabItemControl":
                results.append(c)
            find_tab_items(c, results)
    except Exception:
        pass
    return results

tabs = find_tab_items(dlg)
for t in tabs:
    print(f"  - Tab: name={t.Name!r}  AutomationId={t.AutomationId!r}")

# Tab "Printer Info" klikken (case-insensitive match)
tab = None
for t in tabs:
    if t.Name and "Printer Info" in t.Name:
        tab = t; break
if tab is None:
    print("FAIL: 'Printer Info' tab niet gevonden in enumeratie")
    dlg.SendKeys("{Esc}")
    sys.exit(1)
print(f"[{time.monotonic()-t0:.1f}s] Tab 'Printer Info' selecteren via UIA pattern...")
# Probeer SelectionItemPattern (geen muis nodig)
try:
    sel_pat = tab.GetPattern(auto.PatternId.SelectionItemPattern)
    if sel_pat:
        sel_pat.Select()
        print("  ✓ SelectionItemPattern.Select() OK")
except Exception as e:
    print(f"  ✗ SelectionItemPattern fail: {e}")
    # Fallback: gewone click (vereist on-screen window)
    tab.Click(simulateMove=False, waitTime=0.3)
time.sleep(1.0)  # geef property-page tijd om te renderen

# Re-enumeer dialog children — propsheet swap kan structuur veranderen
print(f"[{time.monotonic()-t0:.1f}s] UI-tree na tab-klik:")
dump(dlg, maxd=4)
print()

# Update klik (refresh) via Invoke pattern (geen muis nodig)
time.sleep(0.1)
btn = dlg.ButtonControl(Name="Update(U)")
if btn.Exists(0.5):
    try:
        inv = btn.GetPattern(auto.PatternId.InvokePattern)
        if inv:
            inv.Invoke()
            print(f"[{time.monotonic()-t0:.1f}s] Update geinvoked (geen muis) — wachten op data...")
    except Exception as e:
        print(f"  Invoke fail: {e}")
        btn.Click(simulateMove=False, waitTime=0.05)
    time.sleep(0.8)  # geef de driver tijd om USB-call af te ronden

# Zoek ECHT de Printer Info pane (na klikken wordt die actief)
# Strategy: vind alle Edit-controls + nearby Text-labels op de hele tree,
# dan filter op KEYWORDS zoals 'Total Count', 'Serial', 'Firmware'.
print(f"[{time.monotonic()-t0:.1f}s] Scraping alle Edit-controls met buurman-labels...")

KEYWORDS = ["Total Count", "Serial No.", "Serial Number", "Firmware",
            "Printer Status", "Color Control", "Version", "Checksum",
            "MQTY", "Media", "Aantal", "Status"]

all_controls = []
def gather(node, depth=0, maxd=10):
    if depth > maxd: return
    try:
        for c in node.GetChildren():
            all_controls.append((depth, c))
            gather(c, depth+1, maxd)
    except Exception:
        pass
gather(dlg)
print(f"[{time.monotonic()-t0:.1f}s] {len(all_controls)} total controls")

# Filter Edit + TextControls
results = {}
for d, c in all_controls:
    try:
        tn = c.ControlTypeName
        if tn not in ("EditControl", "TextControl"):
            continue
        name = (c.Name or "").strip()
        # Probeer value via ValuePattern (alleen EditControl)
        val = ""
        try:
            vp = c.GetValuePattern()
            if vp: val = (vp.Value or "").strip()
        except Exception:
            pass
        # Geef voorkeur aan value > name voor Edit
        text = val if (tn == "EditControl" and val) else name
        # Skip empty
        if not text: continue
        # Print alle:
        rect = c.BoundingRectangle
        pos = f"({rect.left},{rect.top})"
        print(f"  [{tn}] {pos} {text!r}")
        # Match keywords
        for kw in KEYWORDS:
            if kw.lower() in text.lower():
                results.setdefault(kw, []).append(text)
    except Exception:
        pass

print()
print(f"[{time.monotonic()-t0:.1f}s] Matched results:")
for kw, vals in results.items():
    print(f"  {kw}: {vals}")

# Sluit dialog
print(f"[{time.monotonic()-t0:.1f}s] Sluiten dialog...")
try:
    dlg.SendKeys("{Esc}")
except Exception:
    pass
try:
    proc.terminate()
except Exception:
    pass

print(f"[{time.monotonic()-t0:.1f}s] Klaar")
