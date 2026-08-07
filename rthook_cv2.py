"""Runtime hook for OpenCV (cv2) in PyInstaller frozen apps.

Probleem dat deze hook oorspronkelijk oploste
=============================================
cv2's bootstrap haalt zichzelf uit sys.modules en importeert opnieuw om de
native cv2-extensie te laden. Zit cv2 in het PKG-archief van PyInstaller
(FrozenImporter), dan wordt die tweede import onderschept en slaat de
recursiedetectie toe met een ImportError. Daarom staat cv2 NIET in
hiddenimports maar als gewone bestanden op schijf, zodat Python's eigen
FileFinder het pakket laadt.


WAAROM DEZE HOOK NIETS MEER OP sys.path ZET
===========================================
De oude versie deed `sys.path.insert(0, _MEIPASS/cv2)`. Dat was het
probleem, niet de oplossing.

Het cv2-pakket heeft op zijn hoogste niveau bestanden en mappen met namen die
al bezet zijn:

    cv2/typing/     botst met de standaardbibliotheek
    cv2/config.py   botst met config.py van deze applicatie
    cv2/misc/, cv2/utils/, cv2/data/ ...

Zolang die map vooraan op sys.path staat, wint hij van alles. Dat leverde
achter elkaar twee storingen op:

    v1.99.147, bij het opstarten:
        cannot import name 'Any' from partially initialized module 'typing'
        (...\\_internal\\cv2\\typing\\__init__.py)

    en direct daarna, toen alleen 'typing' was afgeschermd:
        NameError: name 'LOADER_DIR' is not defined
        (...\\_internal\\cv2\\config.py)

Dat tweede is `import config` van deze applicatie, die bij OpenCV uitkwam.

Per naam een uitzondering maken is dweilen: elke nieuwe OpenCV-versie kan er
een toevoegen. De map hoort er gewoon niet te staan.

En dat kan ook, want cv2 wordt prima gevonden zonder: _MEIPASS staat al op
sys.path en bevat de map cv2/, dus `import cv2` komt daar via de gewone
FileFinder terecht — precies wat deze hook wilde bereiken. cv2 regelt zijn
eigen zoekpaden voor de native extensie verder zelf.

De zelftest (`MyBoothBox.exe --selftest`) controleert allebei de gevallen
expliciet: dat cv2 laadt, én dat `typing` en `config` van de juiste plek
komen. De bouwstraat draait die test op de geinstalleerde applicatie, dus
een terugval hierin houdt de bouw tegen.


WAAROM DE OUDE build.bat HIER GEEN LAST VAN HAD
===============================================
Bij PyInstaller 5 stond de FrozenImporter vooraan in sys.meta_path: modules
uit het archief wonnen altijd, ongeacht sys.path. Vanaf PyInstaller 6 komt
het zoeken op sys.path eerder aan de beurt, en dan gaat een map met bezette
namen op positie 0 wél pijn doen. De handmatige build op de ontwikkelmachine
gebruikte de oudere PyInstaller en merkte er daarom niets van.
"""
import sys

# Standaardmodules die een gelijknamige submap in cv2/ heeft. Ze worden hier
# alvast geladen zodat ze in sys.modules staan. Strikt genomen overbodig nu de
# cv2-map niet meer op sys.path komt, maar het kost niets en het beschermt
# tegen een toekomstige wijziging die die map er alsnog op zet.
for _naam in ("typing",):
    try:
        __import__(_naam)
    except Exception:
        # Nooit de opstart van de booth op deze hook laten stranden.
        pass
