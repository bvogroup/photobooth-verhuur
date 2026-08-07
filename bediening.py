"""De balk met knoppen die een gast aanraakt: onderin, in het midden.

Waarom dit bestand bestaat
--------------------------
De booth staat op een luidsprekerstatief. Tikt een gast rechts op het scherm,
dan draait de hele booth weg — hij staat scheef, de volgende foto klopt niet
meer en iemand moet hem rechtzetten. Een tik dicht bij het midden geeft geen
draaiing om de staander.

Daaruit volgt de regel voor alles wat een gast aanraakt: **horizontaal bij het
midden, verticaal laag.** Laag is bovendien makkelijker te raken, want de duim
komt van onderen.

Wat de VERHUURDER aanraakt valt hier bewust buiten: het slotje, de
instellingen, de opbouwschermen. Die persoon tikt bewust en voorzichtig en mag
zijn knoppen houden waar ze handig staan.

Hoe je dit gebruikt
-------------------
    import bediening

    balk = bediening.gastbalk(
        hoofd=self._filter_next_btn,      # de belangrijkste knop — op de hartlijn
        links=self._filter_retake_btn,    # wat daarnaast nodig is
        rechts=self._filter_stop_btn,
    )
    root.addWidget(balk)

Twee dingen liggen vast, en die zijn allebei de reden dat dit een eigen widget
is en geen QHBoxLayout met stretches:

1. **De hoofdknop staat op de hartlijn van het scherm.** Niet "ongeveer": de
   balk zet hem daar met een berekening neer. Bij een layout met stretches
   verdeelt Qt de RESTruimte, dus schuift de middelste knop zodra links een
   langere tekst komt te staan of rechts een knop wegvalt. De gast doorloopt op
   één avond het filterscherm, "zijn de foto's goed gelukt?", "wil je ze
   geprint?" en het deelscherm achter elkaar; op alle vier ligt de hoofdknop nu
   onder dezelfde duim.

2. **De knoppen ernaast staan ERNAAST, niet tegen de schermranden.** Ze vormen
   samen één groepje midden op het scherm. Drie knoppen over de volle breedte
   uitsmeren zou de twee buitenste precies op de hefboom zetten die de booth
   van zijn statief draait — dan is het middel erger dan de kwaal.

De zijkolommen zijn even breed, ook als er maar één knop staat. Anders zou de
hoofdknop van de hartlijn af schuiven zodra er een knop bij komt of weggaat.
"""

from PyQt5.QtCore import QSize, Qt
from PyQt5.QtWidgets import QSizePolicy, QWidget

import merk

# ── de maatvoering van de balk ────────────────────────────────────────────
#
# Alle schermen die een gast aanraakt gebruiken deze getallen, zodat de balk op
# elk scherm op dezelfde hoogte staat.

MARGE_ZIJ = merk.RUIMTE_KANTLIJN      # 32 — van de schermrand af
MARGE_BOVEN = merk.RUIMTE             # 16
MARGE_ONDER = merk.RUIMTE_RUIM        # 24 — niet tegen de onderrand aan: die
                                      # rand is met een duim juist lastig, en
                                      # er kan een Windows-balk opduiken
TUSSEN = merk.RUIMTE_RUIM             # 24 — tussen de knoppen

HOOFD_MIN_BREED = 320                 # de hoofdknop is ook zonder lange tekst
                                      # een groot doel
ZIJ_MIN_BREED = 220
MAX_BREED = 420                       # breder wordt een knop niet; anders wordt
                                      # het groepje een muur en ben je het
                                      # midden juist kwijt

BALK_HOOG = MARGE_BOVEN + merk.KNOP_HOOG + MARGE_ONDER      # 128

# ── hoe ver van het midden een gast nog mag tikken ────────────────────────
#
# Het gaat niet om een percentage van de breedte maar om een afstand: hoe
# verder van de staander, hoe groter de hefboom waarmee de booth wegdraait. Een
# percentage zou op een smal scherm te streng zijn en op een breed te soepel.
#
# De Surface Pro 7 is 2736 punten breed op 260 millimeter glas; op de
# gebruikelijke vergroting van 200% is één punt uit deze code dus 0,19
# millimeter. Zeventig millimeter uit het midden is ruim binnen de plek waar
# iemand het toestel met één hand tegenhoudt, en het is minder dan een derde
# van de halve schermbreedte.
#
# Ter ijking: in beta.6 stond "Ja" op "zijn de foto's goed gelukt?" op 87% van
# de breedte, ofwel 96 millimeter uit het midden. Dat is de knop waar de klacht
# over ging.
PUNT_MM = 0.19
MAX_UIT_MIDDEN_MM = 70


def zet_hoofdknop(knop):
    """De belangrijkste knop van een gastscherm: groen, hoog, breed genoeg."""
    knop.setCursor(Qt.PointingHandCursor)
    knop.setFont(merk.letter(merk.TEKST_KNOP, vet=True))
    knop.setFixedHeight(merk.KNOP_HOOG)
    knop.setStyleSheet(merk.knop_hoofd())
    knop.setProperty("gastknop", "hoofd")
    return knop


def zet_zijknop(knop, stijl=None):
    """Een knop naast de hoofdknop. Lager, maar nooit onder de 48 punten.

    48 punten is op dit scherm 9,1 millimeter, en dat is de ondergrens voor
    iemand met een glas in zijn hand in een donkere zaal. KNOP_NORMAAL (64) zit
    daar comfortabel boven.
    """
    knop.setCursor(Qt.PointingHandCursor)
    knop.setFont(merk.letter(merk.TEKST_KNOP, vet=True))
    knop.setFixedHeight(merk.KNOP_NORMAAL)
    knop.setStyleSheet(stijl if stijl is not None else merk.knop_tweede(op_donker=True))
    knop.setProperty("gastknop", "zij")
    return knop


def _gewenste_breedte(knop, minimum):
    """Hoe breed deze knop hoort te zijn: zijn tekst, binnen de grenzen."""
    if knop is None:
        return 0
    nodig = knop.sizeHint().width()
    return max(minimum, min(MAX_BREED, nodig))


class Gastbalk(QWidget):
    """De balk zelf. Plaatst zijn knoppen met een berekening, niet met een layout.

    Een Qt-layout kan niet garanderen dat één bepaald kind precies op de
    hartlijn uitkomt — dat hangt af van wat de buren aan minimumbreedte
    opeisen. Hier is het één regel rekenen, en dus toetsbaar: zie
    hartlijn_afwijking() en test_bediening.py.
    """

    def __init__(self, hoofd=None, links=None, rechts=None, doorzichtig=True):
        super().__init__()
        self.setStyleSheet("background: transparent;" if doorzichtig
                           else f"QWidget {{ background: {merk.INKT_VLAK}; }}")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._hoofd, self._links, self._rechts = hoofd, links, rechts
        for knop in (hoofd, links, rechts):
            if knop is not None:
                knop.setParent(self)
        self.setFixedHeight(BALK_HOOG)

    def sizeHint(self):
        return QSize(self._natuurlijke_breedte(), BALK_HOOG)

    def minimumSizeHint(self):
        # Bewust klein: de balk mag altijd smaller dan zijn wens, dan knijpen
        # de knoppen mee. Wat hij niet mag, is de pagina breder maken.
        return QSize(2 * MARGE_ZIJ + 3 * ZIJ_MIN_BREED // 2, BALK_HOOG)

    def _natuurlijke_breedte(self):
        zij = self._zijbreedte()
        hoofd = _gewenste_breedte(self._hoofd, HOOFD_MIN_BREED)
        return 2 * MARGE_ZIJ + hoofd + 2 * (zij + TUSSEN if zij else 0)

    def _zijbreedte(self):
        """Eén breedte voor beide zijkanten — anders schuift het midden.

        Ook als er maar één zijknop is, houdt de andere kant zijn plek. Zo
        staat de hoofdknop op elk scherm van de reeks op dezelfde plek, of er
        nu twee of drie knoppen op de balk staan.
        """
        if self._links is None and self._rechts is None:
            return 0
        return max(_gewenste_breedte(self._links, ZIJ_MIN_BREED),
                   _gewenste_breedte(self._rechts, ZIJ_MIN_BREED))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._plaats()

    def showEvent(self, event):
        super().showEvent(event)
        self._plaats()

    def _plaats(self):
        breed = self.width()
        if breed < 8:
            return
        zij = self._zijbreedte()
        hoofd = _gewenste_breedte(self._hoofd, HOOFD_MIN_BREED)

        # Past het groepje niet, dan knijpen alle knoppen evenredig mee. Op de
        # Surface Pro 7 gebeurt dat niet; op een kleiner scherm wel, en dan is
        # meeschalen beter dan half buiten beeld vallen.
        beschikbaar = breed - 2 * MARGE_ZIJ
        nodig = hoofd + (2 * (zij + TUSSEN) if zij else 0)
        if nodig > beschikbaar and nodig > 0:
            krimp = beschikbaar / float(nodig)
            hoofd = int(hoofd * krimp)
            zij = int(zij * krimp)

        midden_x = breed // 2
        rij_midden_y = MARGE_BOVEN + merk.KNOP_HOOG // 2

        def zet(knop, breedte, x_midden):
            if knop is None:
                return
            h = knop.height() or merk.KNOP_NORMAAL
            knop.setGeometry(int(x_midden - breedte / 2),
                             int(rij_midden_y - h / 2), breedte, h)

        zet(self._hoofd, hoofd, midden_x)
        zet(self._links, zij, midden_x - hoofd / 2 - TUSSEN - zij / 2)
        zet(self._rechts, zij, midden_x + hoofd / 2 + TUSSEN + zij / 2)


def gastbalk(hoofd, links=None, rechts=None, doorzichtig=True):
    """Bouw de balk: hoofdknop op de hartlijn, de rest ernaast.

    De knoppen houden hun tekst, hun signaal en hun stijl; deze functie zet
    alleen maat en plaats.
    """
    return Gastbalk(hoofd=hoofd, links=links, rechts=rechts,
                    doorzichtig=doorzichtig)


def hartlijn_afwijking(balk, knop):
    """Hoeveel punten staat `knop` naast het midden van `balk`?

    Bestaat om te toetsen in plaats van te kijken. Op een geplaatste balk hoort
    dit 0 of 1 te zijn (afronding op een oneven breedte).
    """
    midden_balk = balk.width() / 2.0
    midden_knop = knop.x() + knop.width() / 2.0
    return abs(midden_knop - midden_balk)
