# DNP QW410 status-rapportage — eenmalige setup

De Photobooth-verhuur software leest QW410-foutcodes (klep open, lint op,
papier op, jam, etc.) en telemetrie (resterende prints op de rol) rechtstreeks
uit de printer — *mits* een libusb-win32 filter-driver is geïnstalleerd.

**Belangrijk:** de filter-driver vervangt NIETS. De DNP-printer-driver blijft
volledig functioneel voor printen. libusb-win32 voegt zich er parallel naast
als read-only kanaal voor status-queries.

## Setup (5 min, eenmalig per PC) — Windows 10/11

> **Gebruik versie 1.4.0.2 of nieuwer.** De oudere 1.2.7.3 (van SourceForge) is
> niet Windows-11 compatibel — driver-signing werkt niet meer.

### Stap 1 — Download

Ga naar: <https://github.com/mcuee/libusb-win32/releases/latest>

Download deze twee files:
- `libusb-win32-devel-filter-1.4.0.2.exe` (officiële installer, ~1.8 MB)
- `libusb-win32-bin-1.4.0.2.zip` (heeft de nieuwe Win11-signed .sys, ~2.5 MB)

Sla beide op in `C:\temp\` (of een andere tijdelijke locatie).

### Stap 2 — Installer runnen

1. **Sluit Bootharoo eerst af** (anders houdt 'm de printer-USB nog vast).
2. Right-click `libusb-win32-devel-filter-1.4.0.2.exe` → **"Run as administrator"**.
3. Klik door de wizard: **Next → I Agree → Next → Install → Finish**.
4. Bij Windows compatibiliteits-assistent: kies **"Dit programma is correct geïnstalleerd"**.

### Stap 3 — Verse .sys file forceren

De installer plaatst niet altijd de nieuwste `libusb0.sys` in `System32\drivers\`.
Pak `libusb-win32-bin-1.4.0.2.zip` uit en kopieer handmatig:

```cmd
copy /Y "C:\temp\libusb-win32-bin-1.4.0.2\bin\amd64\libusb0.sys" "C:\Windows\System32\drivers\libusb0.sys"
```

(Open Command Prompt **als administrator** voor dit commando.)

### Stap 4 — Filter koppelen aan de QW410

Open Command Prompt **als administrator** en run:

```cmd
"C:\Program Files\LibUSB-Win32\bin\install-filter.exe" install --device="USB\VID_1452&PID_9201"
```

Verwachte output:
```
install-filter:[install_service] creating libusb0 service..
install-filter:[insert_device_filter] inserting device upper filter VID_1452&PID_9201&REV_0100..
install-filter:[insert_device_filter] restarting device VID_1452&PID_9201&REV_0100..
```

Een waarschuwing `err [set_device_state] calling class installer failed` mag je
negeren — die wordt opgelost door de volgende stap.

### Stap 5 — USB-kabel los/aansluiten

Trek de USB-kabel van de printer los, wacht 3 seconden, en sluit weer aan. Dit
forceert Windows om de filter-driver te laden voor het device.

### Stap 6 — Testen

```cmd
cd C:\Photobooth-verhuur
python dnp_status.py
```

**Bij succes** zie je iets als:
```
Level:        ok
Code:         0
Label:        Klaar
Connected:    True
Media:        4×6" (QW410)
Life counter: 38
Serial:       'QW4C45020823'
Method:       libusb (libusb0)
Blocking:     False
```

Bij `Level: unknown` + `Method: claim_failed`: filter niet correct geladen —
controleer of de libusb0-service draait (`sc query libusb0`), of herhaal
stap 3-5.

## Wat de software doet ná installatie

- Polling om de 5 seconden in idle (skipt automatisch tijdens een sessie)
- Fullscreen rode overlay als de printer een fout meldt (klep open / lint op /
  papier op / jam / etc.) met exacte foutcode + Nederlands advies
- Print-knop wordt grijs zolang er een blokkerende fout staat
- Live "X prints geprint" indicator + serial + media-formaat in de lock-info popup

## Verwijderen (mocht het ooit nodig zijn)

```cmd
"C:\Program Files\LibUSB-Win32\bin\install-filter.exe" uninstall --device="USB\VID_1452&PID_9201"
```

Daarna kun je LibUSB-Win32 volledig deinstalleren via Configuratiescherm →
Apps → LibUSB-Win32 v1.4.0.2.

## Wat als ik geen filter installeer?

De software werkt onverminderd door — alleen mis je de gedetailleerde
foutrapportage. Je krijgt nog wel:
- USB plug/unplug detectie (printer uit / kabel los → overlay)
- "USB-printer aangesloten" indicator in de lock-info popup
- Bij blocking fout zie je geen specifieke code, alleen "USB niet bereikbaar"
