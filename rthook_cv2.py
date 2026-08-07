"""Runtime hook for OpenCV (cv2) in PyInstaller frozen apps.

Problem: cv2's bootstrap pops itself from sys.modules and re-imports to load
the native cv2.pyd extension. If cv2 is in PyInstaller's PKG archive
(FrozenImporter), the second import is intercepted and triggers recursion
detection, causing ImportError.

Solution: cv2 is NOT added to hiddenimports (so FrozenImporter never knows
about it). Instead, cv2 files are on disk as datas/binaries. This hook ensures
_MEIPASS/cv2/ is in sys.path so Python's standard FileFinder loads cv2
directly from disk on both the first and second import.


LET OP — WAAROM HIERONDER EERST 'typing' WORDT GEIMPORTEERD
===========================================================

Het cv2-pakket bevat een submap die letterlijk `typing` heet
(_internal/cv2/typing/). Zodra we hieronder _internal/cv2/ vooraan op
sys.path zetten, staat die map VOOR de standaardbibliotheek. Elke `import
typing` die daarna nog moet gebeuren, komt dan bij OpenCV uit in plaats van
bij Python zelf. numpy importeert `typing` vroeg in zijn eigen opstart, en
dan valt de hele boel om met:

    cannot import name 'Any' from partially initialized module 'typing'
    (most likely due to a circular import)
    (...\\_internal\\cv2\\typing\\__init__.py)

Dat is precies wat er gebeurde in v1.99.147: de applicatie startte niet meer
op. Dezelfde botsing was al eerder gevonden voor het print-worker-subproces
(zie splash_starter.py), maar daar losgemaakt in plaats van bij de bron.

De oplossing is om `typing` te importeren VOORDAT de cv2-map op sys.path
komt. Daarmee staat de echte module uit de standaardbibliotheek in
sys.modules, en levert elke latere `import typing` — waar dan ook vandaan —
diezelfde module op. De volgorde van sys.path doet er dan niet meer toe.

Waarom niet gewoon achteraan op sys.path zetten: dat zou dit geval ook
oplossen, maar het verandert de manier waarop cv2 zichzelf terugvindt bij de
her-import hierboven, en dat is nou net het fragiele stuk dat deze hook moest
repareren. De import hieronder laat dat mechanisme volledig met rust.
"""
import sys
import os

# Standaardmodules die door een gelijknamige submap in cv2/ overschaduwd
# kunnen worden. Ze worden hier alvast geladen zodat ze in sys.modules staan
# voordat cv2/ op sys.path komt. Op dit moment botst alleen `typing`, maar de
# lijst is uitbreidbaar mocht OpenCV er ooit een toevoegen.
_AFSCHERMEN = ("typing",)

for _naam in _AFSCHERMEN:
    try:
        __import__(_naam)
    except Exception:
        # Lukt het importeren niet, dan is er iets veel ergers aan de hand dan
        # deze hook kan repareren. Nooit de opstart hierop laten stranden.
        pass

if hasattr(sys, '_MEIPASS'):
    cv2_dir = os.path.join(sys._MEIPASS, 'cv2')
    if os.path.isdir(cv2_dir):
        # Insert at position 0 so it takes priority over _MEIPASS root
        if cv2_dir in sys.path:
            sys.path.remove(cv2_dir)
        sys.path.insert(0, cv2_dir)

    # Vangnet: mocht een eerdere PyInstaller-hook `typing` al uit cv2 hebben
    # geladen voordat wij aan de beurt waren, dan gooien we die eruit en laten
    # we hem opnieuw laden — nu met de standaardbibliotheek op de juiste plek.
    _typing = sys.modules.get('typing')
    _bestand = (getattr(_typing, '__file__', '') or '').replace('\\', '/')
    if _typing is not None and '/cv2/' in _bestand:
        del sys.modules['typing']
        _oud = sys.path
        try:
            sys.path = [p for p in sys.path
                        if os.path.normcase(p) != os.path.normcase(cv2_dir)]
            __import__('typing')
        finally:
            sys.path = _oud
