# DNP QW410 status-rapportage — eenmalige setup

De Photobooth-verhuur software kan de QW410 z'n exacte foutcodes (klep open,
lint op, papier op, jam, etc.) en telemetrie (resterende prints op de rol)
ophalen — *mits* een libusb-win32 filter-driver is geïnstalleerd.

**Belangrijk:** de filter-driver vervangt NIETS. De DNP-printer-driver blijft
gewoon werken voor het printen. libusb-win32 voegt zich er parallel naast als
read-only kanaal voor onze status-queries.

## Setup (5 min, eenmalig per PC)

1. Download:
   `libusb-win32-bin-1.2.7.3.zip` →
   <https://sourceforge.net/projects/libusb-win32/files/libusb-win32-releases/1.2.7.3/>

2. Pak het zip-bestand uit op een tijdelijke locatie, bijvoorbeeld
   `C:\temp\libusb-win32\`.

3. Open een **Command Prompt als Administrator** (rechts-klik op Start →
   "Terminal (Admin)" of "Command Prompt (Admin)").

4. Run de filter-installer:
   ```
   cd C:\temp\libusb-win32\bin
   install-filter-win.exe
   ```

5. In het venster dat opent:
   - Kies **"Install a device filter"**.
   - Selecteer in de device-lijst: `vid:1452 pid:9201` (de DNP DP-QW410).
   - Klik **Install**.

6. Reboot Windows (de filter wordt pas actief na een herstart).

7. Test of het werkt:
   ```
   cd C:\Photobooth-verhuur
   python dnp_status.py
   ```

   Bij succes zie je `Level: ok`, `Code: 0`, plus media + serial + firmware.
   Bij `Level: unknown` met `Method: claim_failed` is de filter niet correct
   geïnstalleerd — herhaal stap 4-6.

## Wat de software doet ná installatie

- Polling om de 5 seconden in idle (skipt automatisch tijdens een sessie)
- Fullscreen rode overlay als de printer een fout meldt (klep open / lint op /
  papier op / jam / etc.) met exacte foutcode + advies-tekst
- Print-knop wordt grijs zolang er een fout staat
- Live "nog X prints over" indicator op het lock-info-popup

## Verwijderen (mocht het ooit nodig zijn)

Run `install-filter-win.exe`, kies "Remove a device filter", selecteer de
QW410, klik Remove. Reboot.

## Wat als ik geen filter installeer?

De software werkt onverminderd door — alleen mis je de gedetailleerde
foutrapportage. Je krijgt nog wel een melding bij "USB-printer niet bereikbaar"
(via pyusb-enumeratie, geen filter nodig).
