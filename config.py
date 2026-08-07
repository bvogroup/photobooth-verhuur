import os
import sys

# === App Version ===
# updater.py vergelijkt deze waarde met de tag van de release op GitHub. Stijgt
# hij niet mee met een nieuwe release, dan ziet geen enkele booth de update.
VERSION = "v1.99.149-beta.1"

# === Base Directory (must be first - used by other paths) ===
# BASE_DIR = where the .exe lives (for runtime data like photos, events)
# BUNDLE_DIR = where bundled resources live (DLLs, web templates)
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = BASE_DIR

# === Camera Settings (digiCamControl) ===
DIGICAM_URL = "http://localhost:5513"
DIGICAM_EXE = r"C:\Program Files (x86)\digiCamControl\CameraControl.exe"
DIGICAM_AUTO_START = True     # Automatisch starten als het niet draait
DIGICAM_AUTO_MINIMIZE = True  # Minimaliseren na opstarten

# === Camera Settings ===
LIVE_VIEW_INTERVAL_MS = 33  # ~30 fps fallback (main loop runs as fast as HTTP allows)
CAPTURE_TIMEOUT_SEC = 20    # Max wait time for capture to complete

# === Printer Settings ===
# BEWUST een naam die op DNP-machines niets matcht: de operator MOET
# éénmalig zelf de juiste printer-queue aanklikken (opgeslagen in
# settings.json). Een matchende default ("DP-QW410") pakte via de
# partial match telkens een willekeurige queue (Kopie 1/Kopie 2) en
# sprong zo steeds terug naar een andere printer. Een niet-matchende
# default faalt sinds v1.99.95 luid (print_failed) ipv stil.
PRINTER_NAME = "HiTi P525"  # Must partially match the Windows printer name
PRINT_WIDTH_INCH = 4
PRINT_HEIGHT_INCH = 6
PRINT_DPI = 300
PRINT_COPIES = 1  # Number of copies to print (1 or 2)

# === Data Directory (user Documents, survives software updates) ===
DATA_DIR = os.path.join(os.path.expanduser("~"), "Documents", "Bootharoo")

# === Photo Storage ===
PHOTO_DIR = os.path.join(DATA_DIR, "photos")
BACKGROUNDS_DIR = os.path.join(DATA_DIR, "backgrounds")
TEMPLATES_DIR = os.path.join(DATA_DIR, "templates")
EVENTS_DIR = os.path.join(DATA_DIR, "events")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
FEEDBACK_FILE = os.path.join(DATA_DIR, "feedback.txt")

# === Web Server (QR download) ===
WEB_SERVER_PORT = 8080
QR_DISPLAY_SEC = 15  # How long QR code screen is shown

# === Cloud Storage (Cloudflare R2) ===
CLOUD_UPLOAD_ENABLED = True
R2_ACCOUNT_ID = "8ecc7aea7cfd3cfe405501ec7e7a75c5"
R2_ACCESS_KEY_ID = "728c44872931a8219a054b1eb677b3b8"
R2_SECRET_ACCESS_KEY = "1a43542c847769c881a92de98e289e069aa903becdbc1f0d5af0ea49999fd086"
R2_BUCKET_NAME = "photobooth-photos"
R2_ENDPOINT_URL = "https://8ecc7aea7cfd3cfe405501ec7e7a75c5.r2.cloudflarestorage.com"
CLOUD_WORKER_URL = "https://qr.bootharoo.com"
CLOUD_PHOTO_EXPIRY_MIN = 30    # Auto-delete after N minutes

# === Waar de QR-code van de gast naartoe wijst ===
# Vroeger hardgecodeerd als {CLOUD_WORKER_URL}/gallery/{session_id}. De
# fotopagina verhuist naar MyBoothBox, dus is dit instelbaar gemaakt.
#
# {session_id} in de sjabloon wordt vervangen door het sessie-id. Staat er
# geen {session_id} in, dan wordt het id er met een schuine streep achter
# geplakt — "https://myboothbox.com/fotos" wordt dan
# "https://myboothbox.com/fotos/<sessie-id>".
#
# LEEG LATEN = precies het oude gedrag ({CLOUD_WORKER_URL}/gallery/<id>).
# Booths die niet zijn bijgewerkt blijven daardoor werken zoals nu.
#
# Volgorde waarin de waarde wordt gezocht (zie cloud_storage.gallery_url_for):
#   1. omgevingsvariabele BOOTHAROO_GALLERY_URL   (handig om te testen)
#   2. gallery_url_template in booth_settings.json (per booth instelbaar,
#      zonder nieuwe build — Documents\Bootharoo\booth_settings.json)
#   3. deze constante                              (meegeleverd in de build)
#   4. terugval {CLOUD_WORKER_URL}/gallery/{session_id}
#
# De foto's blijven naar dezelfde R2-emmer gaan; alleen de link verandert.
CLOUD_GALLERY_URL_TEMPLATE = ""

# === Google Drive ===
GDRIVE_ENABLED = False  # Set to True after configuring client_secrets.json
GDRIVE_FOLDER_NAME = "Photobooth Event"  # Drive folder name for uploads

# === Boomerang GIF ===
BOOMERANG_ENABLED = True
BOOMERANG_BUFFER_FRAMES = 30       # ~2 seconds at 15fps
BOOMERANG_FRAME_DURATION_MS = 66   # playback speed per frame
BOOMERANG_SIZE = (480, 320)        # GIF dimensions in pixels

# === Bootharoo CRM / Authentication (Supabase) ===
# Legacy auth project — niet meer actief gebruikt in verhuur-versie,
# maar veld blijft voor compat met oude code-paden.
SUPABASE_URL = "https://aesimuddpsbvgipdbzsi.supabase.co"
SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImFlc2ltdWRkcHNidmdpcGRienNpIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzM0MzE2MTAsImV4cCI6MjA4OTAwNzYxMH0.ZujxXG32-uW-Yy_QsWULWPz4PFQFizlGfvE2Us9BIK4"

# === Clixibo Backend (Supabase) — Linked-modus events ===
# Booking lookup, design-fetch, R2 upload-tickets.
CLIXIBO_SUPABASE_URL = "https://xmfbsofhitxhrozhmpzi.supabase.co"
CLIXIBO_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InhtZmJzb2ZoaXR4aHJvemhtcHppIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjU1OTYzMjIsImV4cCI6MjA4MTE3MjMyMn0._nAetMPlV4KvXSvoEJFzcKDtw_QYWGYt0YyiGBkUYQU"

# === Email Settings ===
EMAIL_ENABLED = True  # SMTP configured in settings.json

# === UI Settings ===
COUNTDOWN_SECONDS = 5
INTRO_DURATION_SEC = 2       # Intro screen duration before countdown
ZERO_HOLD_MS = 200           # How long "0" is shown before capture
CAPTURE_SCREEN_DURATION_MS = 500  # How long capture screen is shown (ms)
REVIEW_TIMEOUT_SEC = 60  # Auto-print after this many seconds on review page
THANK_YOU_DURATION_SEC = 4
COUNTDOWN_BEEP = True     # Play beep sound during countdown

# === Filterscherm (na elke foto) ===
# Na elke gemaakte foto verschijnt een scherm waar de gast een filter kan
# kiezen (zwart-wit, sepia, ...). Links de foto, onderin de filters, rechts
# de knoppen. Er wordt ALLEEN op de gast gewacht (geen auto-timeout); de
# sessie gaat pas door bij een knopdruk. 0/False = filterscherm overslaan
# (oude gedrag: foto kort tonen en automatisch door).
FILTERS_ENABLED = True

# === LED Flash Relay (USB-serial CH340 board) ===
LED_RELAY_ENABLED = True
LED_RELAY_PORT = "auto"  # "auto" = vind CH340 board automatisch, of "COM3" voor vaste poort

# === Geavanceerd-tab toegangscode ===
# De Geavanceerd-tab (serienummer, backend-switch, printer-config) zit
# achter een aparte code, los van de settings-PIN (1350). Zo kan de
# operator wel bij Event/Layout/Print maar niet bij Geavanceerd.
ADVANCED_TAB_CODE = "2321"

# === Cloud-logs (real-time monitoring via Lovable-project) ===
# De getimestampte logs worden gebatcht (elke CLOUD_LOG_INTERVAL_SEC)
# naar de edge function gepusht, gekoppeld aan serienummer + event + klant.
CLOUD_LOG_ENABLED = True
CLOUD_LOG_INTERVAL_SEC = 20

# === Test-print-limiet ===
# Vóór de event-datum mogen er maximaal zoveel prints gemaakt worden (om
# te testen). Daarna is printen geblokkeerd tot de event-datum; vanaf de
# event-datum geldt geen limiet meer.
TEST_PRINT_LIMIT = 10

# === Schermhelderheid ===
# De Surface Pro 7 is fanless (passief gekoeld). Het scherm is een grote
# warmtebron; een vaste, niet-maximale helderheid scheelt warmte. Wordt bij
# elke app-start gezet (Windows/adaptive kan 'm anders terugzetten).
# 0 = niet aanraken.
DISPLAY_BRIGHTNESS = 90

# === Kleuren (MyBoothBox) ===
#
# Hier stond tot 7 augustus 2026 het "Clixibo Design System": achttien kleuren in
# warm beige en goud, overgenomen van de Clixibo-website. Dat was niet een beetje
# naast MyBoothBox, dat was de huisstijl van een ander merk. Vervangen op verzoek
# van de opdrachtgever: "het moet de MBB branding worden."
#
# De echte waarden staan nu in merk.py, dat rechtstreeks docs/MERK.md volgt. De
# namen hieronder blijven bestaan omdat er 716 plekken in de software naar
# verwijzen; ze wijzen nu alleen naar de goede kleur. Nieuwe schermen gebruiken
# merk.py rechtstreeks — daar staan ook de kant-en-klare knopstijlen.
#
# Twee dingen zijn onderweg rechtgezet, want ze waren onleesbaar:
#   COLOR_TEXT_DIM  stond op #A8A9AC = 2,16:1 op de achtergrond. Nu 4,80:1.
#   COLOR_PRIMARY   was goud met witte letters erop = 1,74:1. Nu de merkinkt met
#                   witte letters = 16,42:1.
#
# Waarom COLOR_PRIMARY de inkt wordt en niet het merkgroen: deze constante wordt
# 39x als vlak gebruikt én 35x als tekstkleur. Groen als tekst op een lichte
# achtergrond haalt 1,67:1 en is dan onzichtbaar. De inkt werkt allebei. Het
# merkgroen is de kleur van de hoofdknop en die komt per scherm uit
# merk.knop_hoofd(), bewust en één keer per scherm — niet via een kleur die overal
# tegelijk omslaat.

import merk

COLOR_BG = merk.PAPIER                  # #FAF8F4 warm gebroken wit
COLOR_PRIMARY = merk.INKT               # #16202D de inkt van het merk
COLOR_PRIMARY_HOVER = merk.INKT_AAN     # onder de vinger
COLOR_PRIMARY_PRESSED = merk.INKT_IN    # ingedrukt
COLOR_SECONDARY = merk.LEI              # neutrale tweede knop — wit erop 7,78:1
COLOR_SECONDARY_HOVER = "#5C6570"       # idem, lichter
COLOR_TEXT = merk.TEKST                 # lopende tekst — 7,53:1
COLOR_TEXT_DIM = merk.TEKST_GEDEMPT     # bijschriften — 4,80:1
COLOR_TEXT_ON_PRIMARY = merk.WIT        # wit op de inktknop — 16,42:1
COLOR_SUCCESS = merk.GOED               # werkt als tekst (4,95:1) en als vlak (5,25:1)
COLOR_SUCCESS_HOVER = "#5E8F00"         # idem, lichter
COLOR_DANGER = merk.FOUT                # 5,13:1 als tekst, 5,44:1 als vlak
COLOR_CARD_BG = merk.PAPIER_DIEPER      # kaart, een trapje dieper
COLOR_INPUT_BG = merk.WIT               # invoerveld
COLOR_BORDER = merk.RAND                # scheidingen
COLOR_BORDER_FOCUS = merk.GROEN_INKT    # focus is groen; op licht de donkere groentint,
                                        # want merk.GROEN haalt daar maar 1,67:1
COLOR_ACCENT = merk.HOVER_VLAK          # waar de vinger overheen gaat

# Het merkgroen zelf, voor schermen die de hoofdknop rechtstreeks zetten.
COLOR_BRAND_GREEN = merk.GROEN          # #94D60A — een VLAK met donkere letters erop

# Ensure photo directory exists
os.makedirs(PHOTO_DIR, exist_ok=True)
os.makedirs(BACKGROUNDS_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(EVENTS_DIR, exist_ok=True)
