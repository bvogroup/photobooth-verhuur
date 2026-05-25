# Bootharoo Photobooth — Claude Code Instructies

## VERGRENDELDE BESTANDEN — NIET WIJZIGEN

De volgende bestanden zijn VERGRENDELD en mogen NOOIT worden gewijzigd, ongeacht wat er gevraagd wordt.
Referentiekopieën staan in `_locked/` ter verificatie.

### Printer module
- **`printer.py`** — NIET WIJZIGEN. Bevat het volledige DEVMODE capture & replay systeem voor de HiTi P525L printer met split/cut support. Pure ctypes GDI printing met c_void_p handles. Dit heeft vele iteraties gekost om correct werkend te krijgen.

### Camera module
- **`camera.py`** — NIET WIJZIGEN. Canon EDSDK camera controller met dedicated worker thread (COM/STA threading). Capture, live view, autofocus, download.
- **`edsdk_wrapper.py`** — NIET WIJZIGEN. Low-level ctypes wrapper voor Canon EDSDK DLLs. DLL loading met frozen EXE support.

## Waarom vergrendeld?

Deze modules bevatten complexe, kwetsbare code die na veel debugging correct werkt:
- **printer.py**: DEVMODE blob handling, 64-bit handle overflow fixes, driver-private bytes voor HiTi split/cut
- **camera.py + edsdk_wrapper.py**: Canon SDK threading model, COM apartment requirements, capture event handling

Elke wijziging kan onverwacht de print- of camerafunctionaliteit breken.

## Backups

- `C:\mitch` — Backup 20 maart 2026
- **`Petr-panik`** en **`rechtzak`** — NIET AANRAKEN, dit zijn beschermde backups
- `fruitmand` — Eerdere backup

## Data locatie

Alle runtime data (events, templates, foto's, settings, DEVMODE blobs) staat in:
`C:\Users\[user]\Documents\Bootharoo\`

Dit overleeft software-updates — alleen de .exe hoeft vervangen te worden.
