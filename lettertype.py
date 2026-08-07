"""De merkletters meeleveren en inladen — zonder ze op Windows te installeren.

Het probleem dat dit oplost
---------------------------
De software vroeg op 381 plekken om "DM Sans", en main.py viel terug op "Segoe UI"
als die letter er niet was. Er werd echter nooit een lettertypebestand meegeleverd:
niet in de map, niet in bootharoo.spec. Tenzij iemand ooit met de hand DM Sans op
een tablet had geïnstalleerd, keek dus elke gast de hele avond naar Segoe UI — de
letter van Windows zelf, dezelfde als in het Configuratiescherm.

Dat is precies waarom de bediening er generiek uitzag. Een merk begint bij de
letter, en die kwam nooit aan.

Hoe het nu werkt
----------------
De lettertypebestanden staan in de map `fonts/` en gaan mee in de build (zie
bootharoo.spec). Bij het opstarten leest Qt ze rechtstreeks in met
QFontDatabase.addApplicationFont. Er hoeft niets geïnstalleerd te worden: geen
beheerdersrechten, geen handmatige stap per tablet, en het werkt op een verse
Windows-installatie meteen goed.

Wat je moet weten als je hieraan werkt
--------------------------------------
* **Qt leest TTF en OTF, geen WOFF2.** De bestanden uit het webproject van
  MyBoothBox zijn dus niet herbruikbaar; er moeten echte TTF's in `fonts/`.
* **Lever de vaste snedes, niet het variabele bestand.** Van een variabel
  lettertype (één bestand voor alle diktes) laadt Qt5 alleen de standaarddikte.
  Vet wordt dan door de computer nagemaakt en dat ziet er slecht uit. Dus:
  Regular, Medium en Bold als losse bestanden.
* **De licentie staat dit toe.** DM Sans en Plus Jakarta Sans staan allebei onder
  de SIL Open Font License 1.1. Die staat uitdrukkelijk toe om een lettertype met
  software mee te leveren, zolang het licentiebestand meegaat en het lettertype
  niet los wordt verkocht. Beide voorwaarden zijn hier vanzelf vervuld; zie
  fonts/LEESMIJ.md.

Ontbreekt de map, dan valt alles netjes terug op Segoe UI en werkt de software
gewoon — hij ziet er dan alleen uit zoals vóór deze wijziging.
"""

import os

import config
import merk

# Waar de letters vandaan komen. In een gebouwde .exe zet PyInstaller ze onder
# BUNDLE_DIR; tijdens ontwikkelen staan ze gewoon naast de broncode.
MAP_NAAM = "fonts"

# Wat we willen hebben, en waar het merk op terugvalt als het er niet is.
TERUGVAL = "Segoe UI"


def _mogelijke_mappen():
    """De plekken waar de lettermap kan staan, in volgorde van waarschijnlijkheid."""
    mappen = []
    for basis in (getattr(config, "BUNDLE_DIR", None), getattr(config, "BASE_DIR", None)):
        if basis:
            pad = os.path.join(basis, MAP_NAAM)
            if pad not in mappen:
                mappen.append(pad)
    return mappen


def laad_merkletters(stil=False):
    """Lees de meegeleverde lettertypen in en stel vast welke families er zijn.

    Past merk.LOPEND_LETTER en merk.KOP_LETTER aan als een van de twee ontbreekt,
    zodat de rest van de software nooit om een letter vraagt die er niet is.

    Geeft een verslag terug: {'geladen': [...], 'families': [...], 'map': pad}.
    """
    from PyQt5.QtGui import QFontDatabase

    verslag = {"geladen": [], "families": [], "map": None, "gemist": []}

    map_pad = None
    for kandidaat in _mogelijke_mappen():
        if os.path.isdir(kandidaat):
            map_pad = kandidaat
            break

    if not map_pad:
        verslag["gemist"].append(
            "map 'fonts' niet gevonden — de software valt terug op " + TERUGVAL
        )
        _stel_terugval_in(verslag)
        if not stil:
            _meld(verslag)
        return verslag

    verslag["map"] = map_pad

    for naam in sorted(os.listdir(map_pad)):
        if not naam.lower().endswith((".ttf", ".otf")):
            continue
        pad = os.path.join(map_pad, naam)
        kaart_id = QFontDatabase.addApplicationFont(pad)
        if kaart_id == -1:
            verslag["gemist"].append(f"{naam} — Qt kon dit bestand niet lezen")
            continue
        verslag["geladen"].append(naam)
        for familie in QFontDatabase.applicationFontFamilies(kaart_id):
            if familie not in verslag["families"]:
                verslag["families"].append(familie)

    _stel_terugval_in(verslag)
    if not stil:
        _meld(verslag)
    return verslag


def _stel_terugval_in(verslag):
    """Zorg dat merk.py alleen namen gebruikt van letters die er echt zijn."""
    from PyQt5.QtGui import QFontDatabase

    beschikbaar = set(verslag["families"]) | set(QFontDatabase().families())

    if merk.LOPEND_LETTER not in beschikbaar:
        verslag["gemist"].append(
            f"{merk.LOPEND_LETTER} ontbreekt — teruggevallen op {TERUGVAL}"
        )
        merk.LOPEND_LETTER = TERUGVAL
    if merk.KOP_LETTER not in beschikbaar:
        # Koppen vallen liever terug op de lopende merkletter dan op de
        # systeemletter: dan blijft in elk geval de rest van het scherm kloppen.
        vervanger = merk.LOPEND_LETTER
        verslag["gemist"].append(
            f"{merk.KOP_LETTER} ontbreekt — koppen gebruiken {vervanger}"
        )
        merk.KOP_LETTER = vervanger


def _meld(verslag):
    """Eén regel in het logboek, zodat je op de booth kunt zien wat er geladen is."""
    if verslag["geladen"]:
        print(f"[LETTER] {len(verslag['geladen'])} bestand(en) geladen uit "
              f"{verslag['map']}: {', '.join(verslag['families'])}", flush=True)
    for regel in verslag["gemist"]:
        print(f"[LETTER] LET OP: {regel}", flush=True)
    print(f"[LETTER] in gebruik — lopend: {merk.LOPEND_LETTER}, "
          f"koppen: {merk.KOP_LETTER}", flush=True)
