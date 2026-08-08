"""Elke tik is meteen te zien, en telt precies één keer.

Waarom dit bestaat
------------------
"Ik wil altijd als je op een knop drukt directe respons, zodat je ziet dat er
iets gebeurt." Een gast met een glas in zijn hand tikt op een knop, ziet niets
gebeuren, en tikt door. Tien tikken later doet de booth tien dingen.

Twee dingen die je moet weten voordat je hier iets aan verandert:

**Op een geblokkeerde hoofddraad tekent Qt niets.** Gebeurt er zwaar werk in
de handler van een knop — een bestand schrijven, een printer aanroepen, een
foto van twintig megapixel inladen — dan bevriest het hele scherm. De
ingedrukt-stand die in het stijlblad staat wordt dan niet eens getekend, en
een draaiend teken staat stil. Een spinner erbij zetten lost dus niets op; die
verschijnt niet eens. Eerst moet het werk van de draad af, en pas wat daarna
nog merkbaar duurt verdient een teken. Zie het rapport bij beta.15 voor wat
er per knop gemeten is.

**Qt bewaart tikken die tijdens een blokkade binnenkomen.** Ze staan in de
wachtrij en worden alsnog afgevuurd zodra de draad vrijkomt — tien keer
dezelfde handler achter elkaar. Dat is waarom de booth "vastliep" bij
doortikken. De remedie staat hieronder in eenmalig(): een uitgezette knop
krijgt van Qt geen muisgebeurtenissen meer, dus die gebufferde tikken
verdwijnen in plaats van dat ze zich opstapelen.

Hoe je dit gebruikt
-------------------
    import respons

    respons.eenmalig(self._filter_next_btn, self._filter_next)

in plaats van

    self._filter_next_btn.clicked.connect(self._filter_next)

En waar er ná de reparatie nog merkbaar tijd overheen gaat:

    respons.wacht_in_knop(knop)              # teken in de knop zelf
    scherm = respons.Wachtscherm(venster, "Je foto's worden klaargemaakt")

De maat van een knop verandert nooit
------------------------------------
De gastknoppen staan onderin het midden omdat de booth op een
luidsprekerstatief draait als je rechts tikt (zie bediening.py), en ze hebben
een ondergrens van merk.KNOP_MIN. Een teken dat de knop breder of smaller
maakt zou die plaatsing om zeep helpen, en de gast zou een knop onder zijn
duim voelen wegschuiven. Het draaiende teken is daarom een KIND van de knop
met een eigen plek — geen letter in de tekst, geen pictogram in de layout.
"""

import time

from PyQt5.QtCore import QRectF, QTimer, Qt
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QLabel, QVBoxLayout, QWidget

import merk

# ── de grendel ────────────────────────────────────────────────────────────
#
# Na een tik die van scherm wisselt zijn ALLE gastknoppen even ongevoelig.
# Niet alleen de knop die geraakt werd: de reeks schermen na een sessie zet de
# hoofdknop bewust steeds op dezelfde plek ("Ja" → "Ja, print" → "Klaar"), dus
# een tweede tik van dezelfde vinger komt op de VOLGENDE knop terecht. Zonder
# grendel klikt een ongeduldige gast zich zo in twee tellen door drie schermen
# heen en heeft hij een print besteld die hij niet wilde.
#
# Vierhonderd milliseconde. Een mens die een nieuwe vraag op het scherm ziet,
# leest hem, beslist en tikt — dat kost ruim een halve seconde, dus een
# BEDOELDE tik komt hier nooit onder. Een doortik van dezelfde beweging komt
# er wel onder, en die hoort weg.
GRENDEL_MS = 400

_grendel_tot = 0.0


def zet_grendel(ms=GRENDEL_MS):
    """Sluit de gastknoppen voor `ms` milliseconde."""
    global _grendel_tot
    _grendel_tot = max(_grendel_tot, time.monotonic() + ms / 1000.0)


def grendel_dicht():
    """Staat er nu een tik in de weg?"""
    return time.monotonic() < _grendel_tot


def open_grendel():
    """Alles weer vrij. Voor de toetsen, en voor een scherm dat lang wacht."""
    global _grendel_tot
    _grendel_tot = 0.0


# ── de koppeling ──────────────────────────────────────────────────────────

_EIGENSCHAP = "respons_bezig"


def eenmalig(knop, handler, grendelt=True, uitzetten=True):
    """Verbind `handler` met `knop`, zo dat één tik precies één keer telt.

    Wat er bij een tik gebeurt, in deze volgorde:

    1. Staat de grendel dicht, of loopt deze knop al? Dan verdwijnt de tik.
    2. De knop gaat UIT en wordt METEEN getekend — niet aan het eind van de
       gebeurtenislus, want dan komt het pas ná de handler en ziet de gast er
       niets van. Dit is de "directe respons": binnen een beeldje staat de
       knop zichtbaar anders.
    3. Pas daarna draait de handler. Alles wat er in die tijd binnentikt, komt
       aan bij een uitgezette knop en wordt door Qt weggegooid.
    4. Na afloop gaat de grendel dicht en komt de knop een tel later weer vrij.

    `grendelt=False` voor knoppen die alleen iets aan hetzelfde scherm
    veranderen en meteen klaar zijn — de filtertegels. Die mogen elkaar snel
    opvolgen; het is juist de bedoeling dat een gast er een paar probeert.

    `uitzetten=False` voor diezelfde tegels: die zijn aanvinkbaar en dragen
    zelf de keuze, dus een tegel die een halve seconde grijs wordt zou de
    gast doen denken dat zijn filter niet aankwam. Ze kosten twee
    milliseconde, dus er valt ook niets te blokkeren. De grendel geldt er wél
    voor: is de gast net doorgegaan naar de volgende foto, dan hoort een
    natikkende vinger geen filter meer te kiezen op een foto die weg is.
    """
    def _tik(*_negeer):
        if grendel_dicht() or knop.property(_EIGENSCHAP):
            return
        knop.setProperty(_EIGENSCHAP, True)
        if uitzetten:
            knop.setEnabled(False)
        knop.repaint()
        try:
            handler()
        finally:
            if grendelt:
                zet_grendel()
            if uitzetten:
                QTimer.singleShot(GRENDEL_MS, lambda: _vrij(knop))
            else:
                knop.setProperty(_EIGENSCHAP, False)

    knop.clicked.connect(_tik)
    return knop


def _vrij(knop):
    """De knop mag weer. Wat er intussen binnentikte is al weggegooid.

    Staat er een draaiend teken in, dan is de knop nóg bezig en blijft hij
    uit. Wie dat teken heeft neergezet, ruimt het op met klaar_met_wachten().
    Zonder deze uitzondering zou de knop bij "Volgende foto maken" na
    vierhonderd milliseconde weer oplichten terwijl de strook nog gebouwd
    wordt — en dan staat er een knop klaar die de gast niets meer oplevert.
    """
    try:
        if knop.property(_DRAAIER) is not None:
            return
        knop.setProperty(_EIGENSCHAP, False)
        knop.setEnabled(True)
    except RuntimeError:
        pass          # de knop is intussen opgeruimd — niets aan de hand


def klaar_met_wachten(knop):
    """Teken weg, tekst terug, knop weer bruikbaar. Veilig om vaker te roepen."""
    try:
        stop_wachten(knop)
        knop.setProperty(_EIGENSCHAP, False)
        knop.setEnabled(True)
    except RuntimeError:
        pass


# ── het draaiende teken ───────────────────────────────────────────────────

BOOG_MS = 900            # één ronde
BOOG_DIKTE = 3
BOOG_MAAT = 22           # de doorsnede van het teken


class Draaier(QWidget):
    """Een boogje dat rondgaat. Tekent alleen zichzelf, verder niets.

    Het draait op een eigen timer van 25 beeldjes per seconde. Blokkeert de
    hoofddraad, dan staat hij stil — dat is geen fout in dit bestand maar een
    aanwijzing dat er nog werk op de hoofddraad staat dat er niet hoort.
    """

    def __init__(self, ouder=None, kleur=None, maat=BOOG_MAAT):
        super().__init__(ouder)
        self._kleur = QColor(kleur or merk.INKT)
        self.setFixedSize(maat, maat)
        # De dikte loopt mee met de maat: een boogje van drie punten op een
        # teken van tweeënzeventig is een haartje en dat leest niet.
        self._dikte = max(BOOG_DIKTE, round(maat / 8.0))
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._hoek = 0
        self._timer = QTimer(self)
        self._timer.setInterval(1000 // 25)
        self._timer.timeout.connect(self._tik)

    def start(self):
        self._hoek = 0
        if not self._timer.isActive():
            self._timer.start()
        self.show()
        self.raise_()

    def stop(self):
        self._timer.stop()
        self.hide()

    def _tik(self):
        self._hoek = (self._hoek + int(360 * self._timer.interval() / BOOG_MS)) % 360
        self.update()

    def paintEvent(self, _gebeurtenis):
        verf = QPainter(self)
        verf.setRenderHint(QPainter.Antialiasing, True)
        rand = self._dikte / 2.0 + 0.5
        vak = QRectF(rand, rand,
                     self.width() - 2 * rand, self.height() - 2 * rand)
        # De staart: dezelfde kleur, doorzichtig, zodat de ronde zichtbaar is
        # zonder dat er een tweede kleur bij komt.
        staart = QColor(self._kleur)
        staart.setAlpha(60)
        verf.setPen(QPen(staart, self._dikte, Qt.SolidLine, Qt.RoundCap))
        verf.drawArc(vak, 0, 360 * 16)
        verf.setPen(QPen(self._kleur, self._dikte, Qt.SolidLine, Qt.RoundCap))
        verf.drawArc(vak, -self._hoek * 16, -100 * 16)
        verf.end()


# ── een knop die wacht ────────────────────────────────────────────────────

_DRAAIER = "respons_draaier"


WACHT_TEKEN = 44          # de doorsnede van het teken in een knop: de helft
                          # van merk.KNOP_HOOG, dus altijd ruim binnen de rand

_TEKST = "respons_tekst"


def wacht_in_knop(knop, kleur=None):
    """Zet een draaiend teken IN de knop, zonder dat hij van maat verandert.

    Drie dingen, en alle drie om dezelfde reden — de knop moet blijven staan
    waar hij staat:

    * **De maat wordt eerst vastgezet.** Daarna kan er niets meer aan de
      inhoud gebeuren dat hem breder of smaller maakt. De hoofdknop hoort op
      de hartlijn van het scherm te staan (zie bediening.py) en de gast heeft
      er zijn duim op.
    * **Het teken is een kind met een eigen plek**, geen letter in de tekst en
      geen pictogram in een layout. Het kan de knop dus niet oprekken.
    * **De tekst gaat weg zolang het teken er staat.** Hij stond eerst
      gewoon door; dan liep het teken dwars door de letters heen en was het
      allebei slecht te zien. Er valt op dat moment ook niets meer te lezen:
      de gast heeft de knop net aangeraakt en weet wat hij gekozen heeft. Wat
      er intussen gebeurt, staat op het wachtscherm eroverheen.

    Het teken is GROEN. Een uitgezette knop is een gedempt donker vlak
    (merk.UIT_VLAK), en daar valt de inkt van het merk op weg — precies de
    kleur die je op de afdruk niet meer terugvindt.
    """
    if knop.property(_DRAAIER):
        return knop.property(_DRAAIER)
    knop.setFixedSize(knop.width(), knop.height())
    draaier = Draaier(knop, kleur=kleur or merk.GROEN, maat=WACHT_TEKEN)
    draaier.move((knop.width() - draaier.width()) // 2,
                 (knop.height() - draaier.height()) // 2)
    knop.setProperty(_TEKST, knop.text())
    knop.setText("")
    knop.setProperty(_DRAAIER, draaier)
    draaier.start()
    knop.repaint()
    return draaier


def stop_wachten(knop):
    """Haal het teken weg en geef de knop zijn tekst en vrije maat terug."""
    draaier = knop.property(_DRAAIER)
    if draaier is None:
        return
    try:
        draaier.stop()
        draaier.setParent(None)
        draaier.deleteLater()
    except RuntimeError:
        pass
    knop.setProperty(_DRAAIER, None)
    tekst = knop.property(_TEKST)
    if tekst is not None:
        knop.setText(tekst)
        knop.setProperty(_TEKST, None)
    # De vaste maat weer los: de gastbalk rekent hem zelf uit.
    knop.setMinimumSize(0, 0)
    knop.setMaximumSize(16777215, 16777215)


# ── een scherm dat wacht ──────────────────────────────────────────────────

class Wachtscherm(QWidget):
    """Een vlak over het hele venster: draaiend teken plus één regel uitleg.

    Twee gebruiken, allebei "er staat iets terwijl er iets gebeurt":

    * **Tijdens het samenstellen van de fotostrook**, nadat de laatste foto
      gemaakt is. Dat werk draait op een aparte draad, dus dit teken draait
      ook echt.
    * **Bij het opstarten van de booth** (`logo=` meegeven). Dan is dit het
      eerste wat er op het scherm komt, nog voordat de camera aangesproken is
      en de schermen gebouwd zijn — zie PhotoboothWindow._toon_opkomscherm.

    Bewust geen knoppen. Er valt hier niets te kiezen en er is niets af te
    breken; wie hier iets aanklikbaars neerzet, geeft de gast een knop die de
    sessie halverwege kan slopen.
    """

    TEKEN_MAAT = 96
    LOGO_BREED = 460

    def __init__(self, ouder, tekst, logo="", teken=True):
        super().__init__(ouder)
        vak = QVBoxLayout(self)
        vak.setContentsMargins(merk.RUIMTE_KANTLIJN, merk.RUIMTE_KANTLIJN,
                               merk.RUIMTE_KANTLIJN, merk.RUIMTE_KANTLIJN)
        vak.setSpacing(merk.RUIMTE_KANTLIJN)
        vak.addStretch()

        self._logo = None
        if logo:
            from PyQt5.QtGui import QPixmap
            bron = QPixmap(logo)
            if not bron.isNull():
                self._logo = QLabel(self)
                self._logo.setPixmap(bron.scaledToWidth(
                    self.LOGO_BREED, Qt.SmoothTransformation))
                self._logo.setAlignment(Qt.AlignCenter)
                self._logo.setStyleSheet("background: transparent;")
                vak.addWidget(self._logo, 0, Qt.AlignHCenter)

        self._draaier = None
        if teken:
            self._draaier = Draaier(self, kleur=merk.GROEN, maat=self.TEKEN_MAAT)
            vak.addWidget(self._draaier, 0, Qt.AlignHCenter)

        self._label = QLabel(tekst, self)
        self._label.setAlignment(Qt.AlignCenter)
        self._label.setFont(merk.letter(merk.TEKST_KOP, vet=True, kop=True))
        self._label.setStyleSheet(merk.tekst(merk.OP_DONKER))
        self._label.setWordWrap(True)
        vak.addWidget(self._label)
        vak.addStretch()

    def paintEvent(self, _gebeurtenis):
        """Het vlak zelf vullen.

        Via het stijlblad zou dit niet werken: een kaal QWidget tekent zijn
        `background` pas als WA_StyledBackground gezet is, en een selector
        `QWidget { ... }` zou meteen ook alle kinderen inkleuren. Zelf vullen
        is één regel en laat geen twijfel over wat er onder de tekst ligt —
        het scherm ligt over een foto heen, dus dat moet dekkend zijn.
        """
        verf = QPainter(self)
        verf.fillRect(self.rect(), QColor(merk.INKT))
        verf.end()

    def toon(self):
        """Neerzetten en METEEN tekenen — nog vóór het werk begint."""
        ouder = self.parentWidget()
        if ouder is not None:
            self.setGeometry(ouder.rect())
        self.show()
        self.raise_()
        if self._draaier is not None:
            self._draaier.start()
        self.repaint()

    def verberg(self):
        if self._draaier is not None:
            self._draaier.stop()
        self.hide()

    def zet_tekst(self, tekst):
        self._label.setText(tekst)


def wachtscherm(venster, tekst):
    """Bouw en toon een wachtscherm over `venster`. Geeft het scherm terug."""
    scherm = Wachtscherm(venster, tekst)
    scherm.toon()
    return scherm


__all__ = ["eenmalig", "zet_grendel", "grendel_dicht", "open_grendel",
           "Draaier", "wacht_in_knop", "stop_wachten", "klaar_met_wachten",
           "Wachtscherm", "wachtscherm", "GRENDEL_MS"]
