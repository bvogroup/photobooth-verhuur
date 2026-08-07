# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec file voor MyBoothBox Photobooth (verhuur).

Build with:
    cd C:\\Photobooth-verhuur
    pyinstaller bootharoo.spec --noconfirm

Output: dist\Bootharoo\Bootharoo.exe  (folder mode, fast startup)

Het product heet MyBoothBox, de exe heet Bootharoo.exe. Dat is met opzet: wat
de gast en de verhuurder zien — het opstartscherm, het startscherm, het icoon,
de teksten — draagt het merk, maar alles waar Windows mee werkt houdt de naam
waaronder de 25 booths in het veld al draaien. Zie de toelichting bovenin
installer.iss.
"""

import os
from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None
app_dir = os.path.dirname(os.path.abspath(SPEC))

# boto3/botocore hebben datamappen (endpoints.json e.d.) die mee moeten in de
# build; zonder die bestanden faalt de R2-upload met een cryptische fout.
# Deze werden hiervoor opgehaald via een hardgecodeerd pad naar
# ...\Python314\Lib\site-packages\... Dat pad bestaat alleen op de
# ontwikkelmachine: op een bouwserver (of na een Python-upgrade) is de map er
# niet en levert de build stilzwijgend een kapotte exe op. collect_data_files
# vraagt het pad aan de geïnstalleerde pakketten zelf, dus dit werkt op elke
# machine en bij elke Python-versie. De bestemming binnen de bundel blijft
# gelijk (boto3/data en botocore/data).
boto3_datas = collect_data_files('boto3')
botocore_datas = collect_data_files('botocore')

# Collect ALL cv2 files: datas, binaries, hidden imports
# This is required because cv2's bootstrap re-imports itself natively
cv2_datas, cv2_binaries, cv2_hiddenimports = collect_all('cv2')

# Collect the 'libusb' pip package incl. libusb-1.0.dll. Zonder dit ontbreekt
# de DLL in de frozen build en faalt de DNP USB-statuscheck met
# "libusb-1.0.dll niet gevonden" (dnp_status.py zoekt 'm in
# libusb/_platform/windows/x86_64/). Het primaire status-pad is UI Automation;
# libusb is de USB plug/unplug cross-check.
libusb_datas, libusb_binaries, libusb_hiddenimports = collect_all('libusb')

a = Analysis(
    [os.path.join(app_dir, 'splash_starter.pyw')],
    pathex=[app_dir],
    binaries=[
        # Canon EDSDK DLLs (required for camera control)
        (os.path.join(app_dir, 'EDSDK.dll'), '.'),
        (os.path.join(app_dir, 'EdsImage.dll'), '.'),
        # Windows printer spooler — required by printer.py via ctypes.WinDLL('winspool.drv')
        (r'C:\Windows\System32\winspool.drv', '.'),
    ] + cv2_binaries + libusb_binaries,
    datas=[
        # Default idle screens for different resolutions
        (os.path.join(app_dir, 'idle_defaults'), 'idle_defaults'),
        # Sound effects (printer-busy MP3, evt. beeps)
        (os.path.join(app_dir, 'sounds'), 'sounds'),
        # DNP QW410 visuele hulp-plaatjes (vertaald uit handleiding)
        (os.path.join(app_dir, 'resources', 'dnp_help'),
         os.path.join('resources', 'dnp_help')),
        # Print worker subprocess script
        (os.path.join(app_dir, 'print_worker.py'), '.'),
        # Het merkteken van MyBoothBox: de camera van één lijn. Twee
        # uitvoeringen, want er zijn twee ondergronden. De groene lijn staat
        # op het donkere opstartscherm (splash_starter.pyw); de inktkleurige
        # staat op de witte kaart van het inlogscherm (photobooth.py). Groen
        # op wit haalt maar 1,7:1 en is daar dus onleesbaar — zie docs/MERK.md
        # in het myboothbox-project.
        (os.path.join(app_dir, 'myboothbox-camera.png'), '.'),
        (os.path.join(app_dir, 'myboothbox-camera-inkt.png'), '.'),
        # Het oude Bootharoo-cameraatje gaat nog mee als vangnet: mocht er
        # ergens code overblijven die er nog naar wijst, dan valt die niet
        # om op een ontbrekend bestand.
        (os.path.join(app_dir, 'bootharoo-camera.png'), '.'),
        # Het programma-icoon, ook als los bestand in de bundel. Het zit al
        # in de exe (zie icon= hieronder), maar main.py zet het venstericoon
        # expliciet en heeft daarvoor een bestand nodig dat het kan inlezen.
        (os.path.join(app_dir, 'icon.ico'), '.'),
        # De merkletters (DM Sans en Plus Jakarta Sans). Zonder deze map valt de
        # software terug op Segoe UI en ziet de bediening er generiek uit — zie
        # lettertype.py. Ze worden bij het opstarten ingelezen, niet op Windows
        # geïnstalleerd. Beide staan onder de SIL Open Font License 1.1, die
        # meeleveren met software uitdrukkelijk toestaat; het licentiebestand
        # gaat daarom mee in dezelfde map.
        (os.path.join(app_dir, 'fonts'), 'fonts'),
        # Het logo voor de collage op het startscherm (startscherm.py).
        (os.path.join(app_dir, 'startscherm'), 'startscherm'),
    # boto3/botocore data files (required for S3/R2 cloud upload) — zie de
    # toelichting bovenaan; opgehaald bij de pakketten zelf i.p.v. via een
    # hardgecodeerd Python-pad.
    ] + boto3_datas + botocore_datas + cv2_datas + libusb_datas,
    hiddenimports=[
        # Eigen modules die pas binnen een functie geimporteerd worden en die
        # PyInstaller daardoor kan missen.
        'merk', 'lettertype', 'startscherm',
        # Qt5
        'PyQt5', 'PyQt5.QtWidgets', 'PyQt5.QtCore', 'PyQt5.QtGui',
        'PyQt5.QtPrintSupport',
        # Imaging
        'PIL', 'PIL.Image', 'PIL.ImageOps', 'PIL.ImageDraw', 'PIL.ImageFont',
        # HTTP
        'requests', 'requests.adapters', 'urllib3',
        # QR code
        'qrcode', 'qrcode.image.pil',
        # Windows printing (pywin32)
        'win32print', 'win32ui', 'win32con',
        # Cloud storage (Cloudflare R2 via boto3)
        'boto3', 'botocore', 'botocore.regions', 'botocore.loaders',
        'botocore.parsers', 'botocore.serialize', 'botocore.handlers',
        'botocore.hooks', 'botocore.endpoint', 'botocore.auth',
        'botocore.retryhandler', 'botocore.translate', 'botocore.configprovider',
        'botocore.credentials', 'botocore.httpsession', 'botocore.response',
        'botocore.session', 'botocore.signers', 'botocore.utils',
        's3transfer', 's3transfer.manager', 's3transfer.futures',
        # Python stdlib modules that PyInstaller misses
        'xml', 'xml.etree', 'xml.etree.ElementTree', 'xml.parsers',
        'xml.parsers.expat', 'xml.sax', 'xml.dom',
        # Email
        'email', 'email.mime', 'email.mime.text', 'email.mime.multipart',
        'email.mime.base',
        # SSL certificate trust (Windows cert store)
        'truststore',
        # USB-enumeratie voor DNP-statuscheck (libusb-1.0 backend via pyusb)
        'usb', 'usb.core', 'usb.backend', 'usb.backend.libusb1', 'libusb',
        # Eigen modules (function-level imports — expliciet voor de zekerheid)
        'filters', 'dnp_ref_devmode', 'bediening',
        'PIL.ImageFilter', 'PIL.ImageEnhance', 'PIL.ImageDraw',
    ] + libusb_hiddenimports + [
        # NOTE: cv2 is intentionally NOT here — it must be loaded from disk
        # by Python's FileFinder, not by PyInstaller's FrozenImporter.
        # cv2 files are included via cv2_datas + cv2_binaries above.
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[os.path.join(app_dir, 'rthook_cv2.py')],
    excludes=[
        # Unused heavy modules
        'matplotlib', 'numpy.testing', 'scipy', 'pandas',
        'pytest', 'IPython', 'notebook', 'sphinx',
        'tensorflow', 'torch',
        # xml.etree.ElementTree is REQUIRED by boto3/botocore - do NOT exclude!
        # Unused Google/OAuth modules (replaced with SMTP)
        'pydrive2', 'google.auth', 'google.oauth2',
        'google_auth_oauthlib', 'googleapiclient',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Folder mode (--onedir) for FAST startup — no extraction needed
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # Binaries go in the COLLECT folder
    # De naam van de exe. Deze komt terug in Taakbeheer, in de Taakplanner-taak
    # en in installer.iss (MyAppExeName) — die drie moeten gelijk blijven
    # lopen, en ze staan zo op elke booth in het veld. Hernoemen betekent daar
    # een nieuwe autostart-taak, dus dat gebeurt niet voor de sier.
    name='Bootharoo',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # No UPX — faster startup, slightly larger files
    console=False,            # No console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(app_dir, 'icon.ico'),
)

# COLLECT bundles everything into a folder (fast startup, no extraction)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    # De mapnaam onder dist\. installer.iss haalt de bestanden op uit
    # dist\Bootharoo\ — verandert deze naam, dan moet die regel mee, en de
    # controle in .github/workflows/build-installer.yml erbij.
    name='Bootharoo',
)
