import os
import sys

# === App Version ===
VERSION = "v1.99.102"

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

# === LED Flash Relay (USB-serial CH340 board) ===
LED_RELAY_ENABLED = True
LED_RELAY_PORT = "auto"  # "auto" = vind CH340 board automatisch, of "COM3" voor vaste poort

# === Colors (Clixibo Design System) ===
# Warm beige/gold palette matching the Clixibo website
COLOR_BG = "#F7F5F1"              # Warm off-white background
COLOR_PRIMARY = "#D6C29B"         # Warm gold primary
COLOR_PRIMARY_HOVER = "#C7A878"   # Darker gold for hover
COLOR_PRIMARY_PRESSED = "#B89A6A" # Even darker for pressed
COLOR_SECONDARY = "#53565A"       # Dark gray secondary
COLOR_SECONDARY_HOVER = "#6B6E72" # Lighter gray hover
COLOR_TEXT = "#53565A"            # Dark gray text
COLOR_TEXT_DIM = "#A8A9AC"        # Medium gray for subtle text
COLOR_TEXT_ON_PRIMARY = "#FFFFFF" # White text on primary buttons
COLOR_SUCCESS = "#4A9B6E"         # Muted green (matching warm palette)
COLOR_SUCCESS_HOVER = "#5BAF7F"   # Lighter green hover
COLOR_DANGER = "#C0392B"          # Muted red for danger
COLOR_CARD_BG = "#EFEDE8"         # Slightly darker card background
COLOR_INPUT_BG = "#FFFFFF"        # White input background
COLOR_BORDER = "#D4D1CA"          # Warm gray border
COLOR_BORDER_FOCUS = "#D6C29B"    # Gold border on focus
COLOR_ACCENT = "#E8E4DC"          # Accent background for hover

# Ensure photo directory exists
os.makedirs(PHOTO_DIR, exist_ok=True)
os.makedirs(BACKGROUNDS_DIR, exist_ok=True)
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(EVENTS_DIR, exist_ok=True)
