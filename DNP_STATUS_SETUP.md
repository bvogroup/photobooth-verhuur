# DNP QW410 status-rapportage

## ✅ Zero setup — werkt out-of-the-box

De Photobooth-verhuur software leest **live** alle status-info van de
QW410 zonder enige eenmalige installatie per PC:

- Status code (0 Klaar / 1000 Klep open / 1100 Papier op / 1200 Lint op / etc.)
- Prints over op de rol (bv. `146 / 150`)
- Total Count / Life Counter
- Firmware-versie
- Serial number
- Media-formaat (4×6, 4×4.5, etc.)
- Color profile versies

**Geen Zadig, geen libusb-win32, geen filter-driver, geen reboot, geen
admin-rechten nodig.** Print + cut blijft 100% normaal werken via de
standaard Windows DNP-driver.

## Hoe het werkt

De DNP printer-driver wordt geleverd met een eigen UI-DLL
(`DPQW410UI.DLL`) die in de Voorkeursinstellingen → Printer Info tab de
live status van de printer toont. Die DLL praat intern via een Windows
print-API met de driver, en de driver doet de USB-I/O.

Photobooth-verhuur **automatiseert dat dialog**: opent het off-screen,
klikt programmatisch op "Update", leest alle waardes via Windows UI
Automation, sluit het dialog. Niets is zichtbaar voor de gebruiker, de
hele cyclus duurt ~4 seconden.

Voordelen t.o.v. eerder onderzochte paden:
- ❌ libusb-win32 filter — werkte voor status MAAR brak het printen op
  Windows 11 (verifieerd empirisch juni 2026, alle filter-versies)
- ❌ Win32 spooler `GetPrinter` / WMI `Win32_Printer` — blind voor QW410
- ❌ `WritePrinter` + `ReadPrinter` bidi — ReadPrinter geeft
  `INVALID_HANDLE` op client-side handle
- ❌ `GetPrinterDataEx` met allerlei keys/values — geen treffer
- ✅ **UI Automation van de bestaande driver-UI** — werkt direct, geen
  conflict met print-pipeline

## Vereisten (al opgelost door installatie)

In `requirements.txt` staan:
- `pywin32` — voor `OpenPrinter` calls (al aanwezig voor printer.py)
- `uiautomation` — voor UI Automation scrape (nieuw)
- `pyusb` + `libusb` — voor fallback USB-enumeratie

## Werkt zonder DNP-driver?

Nee. Voor de detailstatus moet de DNP DP-QW410 driver geïnstalleerd zijn
(die zit standaard bij de QW410). Zonder driver valt de software terug
op USB-enumeratie via libusb-1.0 — alleen `aangesloten ja/nee`
detectie, geen detail-status.

## Eventuele problemen

**De dialog flitst over het scherm.** Dat zou niet moeten gebeuren — de
software zet 'm direct op coordinaten (-3000, -3000). Op
multi-monitor-setups kan dat alsnog op een ander scherm uitkomen. Stuur
me een melding als dat gebeurt, dan vergroot ik de offset.

**Verkeerde printer-naam in `settings.json`.** Bootharoo gebruikt de
printer die in `PRINTER_NAME` staat. Controleer of dat dezelfde print
queue is als waar je daadwerkelijk naar print. Bij QW410 heten die vaak
`DP-QW410`, `DP-QW410 (Kopie 1)`, `DP-QW410 (Kopie 2)` enzovoort. Test
welke queue daadwerkelijk fysiek print, en zet díe naam in
`settings.json` onder `printer_name`.

**Status update voelt vertraagd.** Default polling interval is 30
seconden. Onder de motorkap doet elke poll een ~4-sec UI-scrape, dus
sneller pollen is verspilling. Bij blokkerende foutcodes (klep open /
papier op / lint op) verschijnt direct een fullscreen overlay met
"Opnieuw checken" knop voor on-demand refresh.
