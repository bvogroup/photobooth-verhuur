"""De balk met knoppen die een gast aanraakt: onderin, in het midden.

Waarom dit bestand bestaat
--------------------------
De booth staat op een luidsprekerstatief. Tikt een gast rechts op het scherm,
dan draait de hele booth weg — hij staat scheef, de volgende foto klopt niet
meer en iemand moet hem rechtzetten. Een tik in het midden geeft geen draaiing
om de staander.

Daaruit volgt de regel voor alles wat een gast aanraakt: **horizontaal
gecentreerd, verticaal laag.** Laag is bovendien makkelijker te raken, want de
duim komt van onderen.

Wat de VERHUURDER aanraakt valt hier bewust buiten: het slotje, de
instellingen, de opbouwschermen. Die persoon tikt bewust en voorzichtig en mag
zijn knoppen houden waar ze handig staan.

Hoe je dit gebruikt
-------------------
    import bediening

    balk = bediening.gastbalk(
        hoofd=self._filter_next_btn,      # de belangrijkste knop — in het midden
        links=self._filter_retake_btn,    # wat daarnaast nodig is
        rechts=self._filter_stop_btn,
    )
    root.addWidget(balk)

De hoofdknop staat op de horizontale hartlijn van het scherm, ongeacht of er
één, twee of drie knoppen op de balk staan. Dat is met opzet: de gast doorloopt
op één avond het filterscherm, "zijn de foto's goed gelukt?", "wil je ze
geprint?" en het deelscherm achter elkaar, en op alle vier ligt de
hoofdknop dan onder dezelfde duim. Een knop die tussen twee schermen verspringt
is erger dan een knop op een onhandige plek.

Waarom een raster en geen QHBoxLayout met stretches
---------------------------------------------------
Bij een QHBoxLayout met `addStretch()` ertussen verdeelt Qt de RESTruimte
gelijk, niet de ruimte zelf. Staat er links een brede knop en rechts een smalle,
dan schuift de middelste mee — precies wat hier niet mag. Een raster met drie
kolommen van gelijke breedte houdt de middelste kolom op zijn plek, wat er
links en rechts ook staat, ook als er niets staat.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QGridLayout, QSizePolicy, QWidget

import merk

# ── de maatvoering van de balk ────────────────────────────────────────────
#
# Alle schermen die een gast aanraakt gebruiken deze getallen, zodat de balk
# op elk scherm op dezelfde hoogte staat. De hoogte hieronder is precies wat
# `gastbalk()` opeist; wie de balk in een vak met een vaste hoogte zet, kan
# BALK_HOOG gebruiken om dat vak te maten.

MARGE_ZIJ = merk.RUIMTE_KANTLIJN      # 32 — van de schermrand af
MARGE_BOVEN = merk.RUIMTE             # 16
MARGE_ONDER = merk.RUIMTE_RUIM        # 24 — niet tegen de onderrand aan: die
                                      # rand is met een duim juist lastig, en
                                      # er kan een Windows-balk opduiken
KOLOM_RUIMTE = merk.RUIMTE            # 16 — tussen de kolommen

HOOFD_MIN_BREED = 320                 # de hoofdknop is ook zonder lange tekst
                                      # een groot doel
ZIJ_MIN_BREED = 220
MAX_BREED = 460                       # breder wordt een knop niet: op 1368
                                      # punten zou de balk anders één muur
                                      # worden en verlies je het midden

BALK_HOOG = MARGE_BOVEN + merk.KNOP_HOOG + MARGE_ONDER      # 128


def zet_hoofdknop(knop):
    """De belangrijkste knop van een gastscherm: groen, hoog, breed genoeg."""
    knop.setCursor(Qt.PointingHandCursor)
    knop.setFont(merk.letter(merk.TEKST_KNOP, vet=True))
    knop.setMinimumHeight(merk.KNOP_HOOG)
    knop.setMaximumHeight(merk.KNOP_HOOG)
    knop.setMinimumWidth(HOOFD_MIN_BREED)
    knop.setMaximumWidth(MAX_BREED)
    knop.setStyleSheet(merk.knop_hoofd())
    return knop


def zet_zijknop(knop, stijl=None):
    """Een knop naast de hoofdknop. Lager, maar nooit onder de 48 punten.

    48 punten is op dit scherm 9,1 millimeter, en dat is de ondergrens voor
    iemand met een glas in zijn hand in een donkere zaal. KNOP_NORMAAL (64)
    zit daar comfortabel boven.
    """
    knop.setCursor(Qt.PointingHandCursor)
    knop.setFont(merk.letter(merk.TEKST_KNOP, vet=True))
    knop.setMinimumHeight(merk.KNOP_NORMAAL)
    knop.setMaximumHeight(merk.KNOP_NORMAAL)
    knop.setMinimumWidth(ZIJ_MIN_BREED)
    knop.setMaximumWidth(MAX_BREED)
    knop.setStyleSheet(stijl if stijl is not None else merk.knop_tweede(op_donker=True))
    return knop


def gastbalk(hoofd, links=None, rechts=None, doorzichtig=True):
    """Bouw de balk: hoofdknop op de hartlijn, de rest ernaast.

    `hoofd` mag None zijn — dan blijft de middenkolom leeg maar houden de
    zijkolommen wél hun plek, zodat de balk niet verspringt zodra de hoofdknop
    terugkomt.

    De knoppen worden NIET van eigenaar gewisseld qua gedrag: hun tekst, hun
    signaal en hun stijl blijven van de aanroeper. Deze functie zet alleen
    maat en plaats.
    """
    balk = QWidget()
    balk.setStyleSheet("background: transparent;" if doorzichtig
                       else f"QWidget {{ background: {merk.INKT_VLAK}; }}")
    balk.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    raster = QGridLayout(balk)
    raster.setContentsMargins(MARGE_ZIJ, MARGE_BOVEN, MARGE_ZIJ, MARGE_ONDER)
    raster.setHorizontalSpacing(KOLOM_RUIMTE)
    raster.setVerticalSpacing(0)

    # Drie kolommen van gelijke breedte. Dít is wat de hoofdknop op de
    # hartlijn houdt; zonder de gelijke stretch verschuift hij zodra links
    # een langere tekst komt te staan.
    for k in range(3):
        raster.setColumnStretch(k, 1)

    for kolom, knop in ((0, links), (1, hoofd), (2, rechts)):
        if knop is None:
            continue
        knop.setParent(balk)
        raster.addWidget(knop, 0, kolom, alignment=Qt.AlignCenter)

    return balk


def hartlijn_afwijking(balk, knop):
    """Hoeveel punten staat `knop` naast het midden van `balk`?

    Bestaat om te toetsen in plaats van te kijken. Op een geplaatste balk
    hoort dit 0 of 1 te zijn (afronding op een oneven breedte).
    """
    midden_balk = balk.width() / 2.0
    midden_knop = knop.x() + knop.width() / 2.0
    return abs(midden_knop - midden_balk)
