# DNP QW410 status-rapportage

## ⚠️ Belangrijke conclusie na praktijk-test (juni 2026)

De libusb-win32 filter-driver — zelfs in de meest recente release (v1.4.0.2,
jan 2025) — **breekt het printen op de DNP QW410 onder Windows 11**.

Specifiek gedrag dat werd waargenomen:
- ✅ Status lezen via libusb0 werkt perfect (codes 1200 "Lint op", 1100
  "Papier op", 1000 "Klep open", media, serial, life counter — alles correct)
- ✅ Windows print spooler accepteert de jobs ("Normal" status)
- ❌ Maar er komt fysiek **niks** uit de printer — de USB pipe naar de
  DNP-driver wordt door de filter onderbroken
- ❌ Spooler-restart, USB replug, en device-reset lossen het niet op zolang
  de filter actief is

**Daarom: in deze setup wordt de filter NIET geïnstalleerd.** De software
detecteert dit automatisch en valt terug op basis-detectie via libusb-1.0
enumeratie zonder claim.

## Wat werkt zonder filter (huidige fallback)

| Conditie | Detectie |
|---|---|
| USB-kabel losgekoppeld | ✅ "Printer niet bereikbaar" overlay binnen 5s |
| Printer uitgezet | ✅ idem (device verdwijnt uit USB enum) |
| Printer aan + verbonden | ✅ "USB aangesloten" in lock-info popup |
| Klep open | ❌ niet detecteerbaar |
| Papier op | ❌ niet detecteerbaar |
| Lint op | ❌ niet detecteerbaar |
| Papier vast | ❌ niet detecteerbaar |

Voor de niet-detecteerbare condities: operator ziet de fysieke LEDs op de
printer (POWER + ERROR) en handelt manueel.

## Toekomstige opties (nog niet getest)

Als er ooit detail-status nodig is:

1. **USBDK** (Daynix) — alternatieve USB-redirect layer; werkt op architectureel
   ander niveau dan filter-drivers. **Risico:** kan ook printen breken.
2. **libusbK** filter — modernere variant; **risico:** vergelijkbaar met
   libusb-win32.
3. **DNP IDW SDK** aanvragen via NDA bij DNP-support. Officiële API met C-DLL
   + ctypes binding mogelijk.

Geen van deze opties heeft een gegarandeerde uitkomst — elke test betekent
risico op weer een gebroken print-pipeline.

## Code-structuur in `dnp_status.py`

De module is zo gebouwd dat hij **automatisch** detecteert welke methode
beschikbaar is en graceful degradeert:

1. **Pad libusb0** (libusb-win32 filter actief) → volledige detail-status met
   alle 13+ foutcodes + telemetrie. **In huidige QW410-setup niet bruikbaar
   wegens print-breakage.**
2. **Pad libusb1** (libusb-1.0 generic) → alleen device-enumeratie, geen claim.
   Geeft `connected=True/False`. **Huidige actieve pad.**

De UI (lock-info popup + fullscreen overlay) past zich automatisch aan op
basis van `level` en `connected` velden.

## Verwijderen filter (mocht 'm ooit nog ergens hangen)

Als de filter eerder is geïnstalleerd:

```cmd
"C:\Program Files\LibUSB-Win32\bin\install-filter.exe" uninstall --device="USB\VID_1452&PID_9201"
sc stop libusb0
sc delete libusb0
```

Daarna LibUSB-Win32 deinstalleren via Configuratiescherm → Apps. USB-kabel
los/aansluiten zodat de device-stack vers initialiseert.
