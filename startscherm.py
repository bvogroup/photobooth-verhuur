"""Het startscherm van MyBoothBox — de collage.

Het scherm dat de hele avond staat te wachten. Er staat precies drie dingen op:
de collage, één instructie, en het logo. Verder niets.

De collage loopt in de loop van de avond vol — leeg, één rij, twee rijen, vol —
en de rijen schuiven continu opzij. Is hij vol, dan vervangt de nieuwste foto de
oudste op zijn plek.

Het ontwerp staat in het MyBoothBox-project onder docs/startscherm/. De
maatvoering hieronder is daar letterlijk uit overgenomen (render/ontwerp.py,
klasse Layout) en niet opnieuw uitgerekend; wijkt er iets af, dan is dat een
fout hier en niet daar.

Waarom dit zo gebouwd is
------------------------
Dit draait op een fanless Surface Pro 7 naast een camera, een printer en een
live camerabeeld. Een animatie die op een ontwikkelmachine mooi is maar op de
booth hapert, is slechter dan stilstaand beeld. Daarom is er eerst gemeten
(meet_startscherm.py, draait mee in de bouwstraat):

    elke tegel per beeldje opnieuw schalen     18   ms
    rijen vooraf samenstellen, dan schuiven     0,5 ms
    stilstaand                                  0,5 ms

Schuiven kost dus drie tot zeven procent meer dan stilstaan. Het dure is het
opnieuw schalen, niet de beweging. Vandaar de opzet hier:

  * elke foto wordt ÉÉN keer op tegelmaat gebracht en bewaard;
  * elke rij wordt ÉÉN keer tot een brede afbeelding samengesteld;
  * per beeldje wordt er alleen een stuk van die afbeelding getekend;
  * de instructie en het logo zijn vooraf gerasterd en verschuiven alleen.

Er wordt per beeldje dus niets geschaald en niets opnieuw samengesteld. Dat
gebeurt alleen als er een foto bijkomt, en dat is één keer per sessie.

Twee dingen die in beta.5 op de echte booth misgingen
-----------------------------------------------------
Allebei onzichtbaar in de bouwstraat, en allebei hier gerepareerd. Ze staan
erbij omdat ze makkelijk terugkomen.

**De maat komt later dan de foto's.** photobooth.py maakt deze widget aan en
zet er meteen de foto's in, vóórdat hij in de stapel hangt. Op dat moment is
een QWidget 640 x 480 — de standaardmaat van Qt — en werden de miniaturen dus
op 120 x 75 gemaakt. Daarna kreeg de widget zijn echte maat, maar de miniaturen
en de rijen werden niet opnieuw gemaakt. Het scherm stond vol met tegels van een
kwart van de bedoelde maat, in rijen die veel te smal waren: rafelige kolommen
met grote gaten ertussen. Vandaar dat de maatvoering nu op één plek wordt
bewaakt (_zorg_layout) en dat een maatwijziging de miniaturen en de rijen
ongeldig maakt.

**De schermschaal telt mee.** Een Windows-tablet staat vrijwel altijd op 150%
of 200%. Qt rekent dan in LOGISCHE punten en blaast het resultaat daarna op naar
de echte pixels. Wie een pixmap op de logische maat maakt, laat hem dus door Qt
uitrekken — en dat is precies waarom de instructie en het logo op de booth wazig
waren. Elke pixmap hier wordt daarom op logische maat x schermschaal gemaakt,
met setDevicePixelRatio() erop, zodat Qt hem één op één neerzet. Het tekenen
zelf blijft in logische punten; alleen het doek is fijner.
"""

import math
import os
import re
from datetime import datetime

from PyQt5.QtCore import Qt, QTimer, QRect, QElapsedTimer
from PyQt5.QtGui import QPixmap, QPainter, QColor, QPainterPath, QFontMetrics
from PyQt5.QtWidgets import QWidget

import config
import merk

# ── maatvoering — overgenomen uit docs/startscherm/render/ontwerp.py ────────
REF = 1824          # referentie op de korte zijde; alles schaalt hiermee mee
M_V = 64            # marge boven en onder
GAP = 26            # tussenruimte tussen tegels
TILE_W, TILE_H = 456, 285      # tegel 16:10
TXT_W, TXT_H = 1420, 267       # het instructieblok
LOGO_W, LOGO_H = 520, 376
RONDING = 12        # hoekafronding van een tegel

# De letter van de instructie. Plus Jakarta Sans ExtraBold meldt zich bij Qt aan
# als een EIGEN familie en niet als een dikte binnen "Plus Jakarta Sans" — zie
# fonts/LEESMIJ.md. Hij moet dus op deze naam aangevraagd worden.
KOP_ZWAAR = "Plus Jakarta Sans ExtraBold"

# Hoe de collage vult en beweegt.
SCHUIF_PX_S = 12.0      # schuifsnelheid van een rij, punten per seconde
BEELDJES_S = 25         # tekenfrequentie
OVERVLOEI_MS = 1200     # een vervangen tegel vloeit in 1,2 s over

# Verschuiving tegen inbranden: de hele opbouw kruipt heel langzaam rond, met
# perioden die geen deler gemeen hebben, zodat het pad zich pas na uren
# herhaalt. Topsnelheid 0,53 px/s — dat is niet te zien.
DRIFT_X, DRIFT_Y = 56, 34
DRIFT_PERIODE_X = 11 * 60.0
DRIFT_PERIODE_Y = 17 * 60.0


class Layout:
    """De indeling. De enige plek waar maten staan — net als in het ontwerp."""

    def __init__(self, W, H):
        self.W, self.H = W, H
        self.liggend = W > H
        self.s = (H if self.liggend else W) / float(REF)
        r = lambda v: int(round(v * self.s))

        self.mv, self.gap = r(M_V), r(GAP)
        self.tw, self.th = r(TILE_W), r(TILE_H)
        self.txt_w, self.txt_h = r(TXT_W), r(TXT_H)
        self.logo_w, self.logo_h = r(LOGO_W), r(LOGO_H)
        self.ronding = max(4, r(RONDING))

        # Het raster kantelt mee met het scherm: 5x3 liggend, 3x5 staand.
        self.kolommen, self.rijen = (5, 3) if self.liggend else (3, 5)
        self.n = self.kolommen * self.rijen

        self.raster_b = self.kolommen * self.tw + (self.kolommen - 1) * self.gap
        self.raster_h = self.rijen * self.th + (self.rijen - 1) * self.gap
        self.raster_x = (W - self.raster_b) // 2
        self.raster_y = self.mv

        self.logo_x = (W - self.logo_w) // 2
        self.logo_y = H - self.mv - self.logo_h
        self.txt_x = (W - self.txt_w) // 2

        self.vrij_boven = self.mv
        self.vrij_onder = self.logo_y

    def collage_onderkant(self, rijen_zichtbaar):
        if rijen_zichtbaar <= 0:
            return self.raster_y
        return (self.raster_y + rijen_zichtbaar * self.th
                + (rijen_zichtbaar - 1) * self.gap)

    def tekst_y(self, rijen_zichtbaar):
        """De instructie staat altijd midden in de ruimte die de collage overlaat."""
        boven = (self.collage_onderkant(rijen_zichtbaar) if rijen_zichtbaar
                 else self.vrij_boven)
        return boven + (self.vrij_onder - boven - self.txt_h) / 2.0

    def rij_y(self, r):
        return self.raster_y + r * (self.th + self.gap)


# ── de foto's ───────────────────────────────────────────────────────────────

_NAAM = re.compile(r"^(\d{2}-\d{2}-\d{4}_\d{2}\.\d{2}\.\d{2})_(\d+)\.jpe?g$",
                   re.IGNORECASE)


def _sessies_uit_map(raw_dir):
    """Eén foto per sessie uit raw/, op volgorde.

    Twee dingen die hier misgaan als je ze niet weet, allebei uit het
    ontwerpverslag:

    * Bij een spiegelreflex staat elke opname DUBBEL op schijf: de EDSDK
      dumpt hem ook in de wortel van photos/. Daarom wordt hier alleen raw/
      gelezen en nooit recursief.
    * De tijdstempel heeft secondeprecisie en is geen unieke sleutel. Hij wordt
      hier alleen gebruikt om te groeperen en te sorteren, niet als sleutel.

    En de klok van een booth kan mis staan — die van de testbooth liep acht uur
    achter. Daarom wordt de naam ontleed in plaats van de bestandstijd gelezen:
    een klok die verkeerd staat maar niet verspringt, geeft nog steeds de
    goede volgorde. Tijdens het draaien telt bovendien de aankomstvolgorde,
    want dan komt elke foto er via nieuwe_foto() bij.
    """
    if not raw_dir or not os.path.isdir(raw_dir):
        return []

    per_sessie = {}
    for naam in os.listdir(raw_dir):
        m = _NAAM.match(naam)
        if not m:
            continue
        stempel, nummer = m.group(1), int(m.group(2))
        per_sessie.setdefault(stempel, []).append((nummer, naam))

    def sorteersleutel(stempel):
        try:
            return (0, datetime.strptime(stempel, "%d-%m-%Y_%H.%M.%S"))
        except ValueError:
            return (1, datetime.min)

    uit = []
    for stempel in sorted(per_sessie, key=sorteersleutel):
        opnamen = sorted(per_sessie[stempel])
        # De tweede opname. Op de eerste kijkt vaak nog iemand naar de
        # aftelring. Is er maar één, dan die.
        gekozen = next((n for nr, n in opnamen if nr == 2), opnamen[0][1])
        uit.append(os.path.join(raw_dir, gekozen))
    return uit


# ── het scherm ──────────────────────────────────────────────────────────────

class Collage(QWidget):
    """Het hele startscherm in één widget met één paintEvent.

    Tekent achter elkaar: de achtergrond, de schuivende rijen, de instructie en
    het logo. Geen enkele laag wordt per beeldje opnieuw gerasterd of geschaald.
    """

    def __init__(self, achtergrond_pad="", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

        self._achtergrond_bron = QPixmap(achtergrond_pad) if achtergrond_pad else QPixmap()
        self._achtergrond = QPixmap()     # op schermmaat, één keer geschaald
        self._layout = None
        self._dpr = 1.0           # schermschaal; 2,0 op een tablet die op 200% staat
        self._beeld_verouderd = False

        self._paden = []          # de bronbestanden, op volgorde van aankomst
        self._miniaturen = []     # dezelfde volgorde, op tegelmaat
        self._stroken = []        # één brede pixmap per rij
        self._oude_stroken = {}   # rij -> (pixmap, tijdstip) tijdens een overvloeier
        self._volgende = 0        # welk vakje als eerstvolgende vervangen wordt

        self._logo = QPixmap()
        self._logo_x = self._logo_y = 0
        self._tekst = QPixmap()

        self._klok = QElapsedTimer()
        self._klok.start()
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / BEELDJES_S))
        self._timer.timeout.connect(self._tik)

    # ── leven ──────────────────────────────────────────────────────────────
    def start(self):
        if not self._timer.isActive():
            self._timer.start()

    def stop(self):
        """Zet de tekenlus stil. Hoort te gebeuren zodra dit scherm weg is —
        anders staat er een timer van 25 keer per seconde te draaien terwijl de
        gast aan het fotograferen is."""
        self._timer.stop()

    def _tik(self):
        # Alleen hertekenen wat beweegt: het gebied van de collage, plus de
        # strook waar de instructie staat. Het logo staat stil (op de
        # verschuiving na, en die kruipt met een halve pixel per seconde).
        self.update()

    # ── inhoud ─────────────────────────────────────────────────────────────
    def zet_fotos(self, paden):
        """Vervang de hele lijst. Alleen bij het opbouwen van het scherm.

        Dit wordt aangeroepen vóórdat de widget zijn maat heeft — photobooth.py
        maakt hem aan en vult hem meteen. De paden worden daarom altijd
        bewaard; de miniaturen worden pas gemaakt zodra er een maat is, en
        opnieuw zodra die maat verandert. Zie _zorg_layout.
        """
        self._paden = list(paden)
        self._beeld_verouderd = True
        self._zorg_layout()
        self.update()

    def nieuwe_foto(self, pad):
        """Eén foto erbij, na afloop van een sessie.

        Dit is het enige moment waarop er geschaald en samengesteld wordt. Is
        de collage vol, dan vervangt de nieuwste de oudste op zijn plek — het
        raster staat dan stil en alleen de inhoud van één vakje wisselt.
        """
        L = self._zorg_layout()
        if L is None or not pad or not os.path.isfile(pad):
            return
        mini = self._maak_miniatuur(pad)
        if mini is None:
            return

        if len(self._miniaturen) < L.n:
            # de collage is nog aan het vollopen
            self._paden.append(pad)
            self._miniaturen.append(mini)
            self._bouw_stroken()
            self.update()
            return

        # Vol. De nieuwste vervangt de oudste OP ZIJN PLEK: het raster staat
        # vanaf nu stil en alleen de inhoud van één vakje wisselt. Dat is de
        # goedkoopste beweging die er is, en het scherm ziet er nooit meer
        # half leeg of net-opnieuw-begonnen uit.
        plek = self._volgende % L.n
        rij = plek // L.kolommen
        if rij < len(self._stroken):
            self._oude_stroken[rij] = (self._stroken[rij], self._klok.elapsed())
        self._paden[plek] = pad
        self._miniaturen[plek] = mini
        self._volgende = (plek + 1) % L.n
        self._bouw_strook(rij)
        self.update()

    def rijen_zichtbaar(self):
        """Een rij verschijnt pas als hij vol is."""
        L = self._zorg_layout()
        if L is None:
            return 0
        return min(L.rijen, len(self._miniaturen) // L.kolommen)

    # ── opbouw ─────────────────────────────────────────────────────────────
    def resizeEvent(self, event):
        """De widget krijgt zijn echte maat pas als hij in de stapel hangt.

        Dat is ná zet_fotos(), dus hier moet alles wat aan de maat vastzit
        opnieuw. Zonder dit bleven de miniaturen op de maat staan die een leeg
        QWidget toevallig heeft (640 x 480) — de fout van beta.5.
        """
        super().resizeEvent(event)
        self._zorg_layout()

    def _zorg_layout(self):
        """De enige plek waar de maatvoering vandaan komt.

        Verandert de schermmaat of de schermschaal, dan is ALLES wat eraan
        vastzit ongeldig: de achtergrond, het logo, de instructie, en ook de
        miniaturen en de rijen. Die laatste twee werden in beta.5 vergeten.
        """
        if self.width() <= 0 or self.height() <= 0:
            return None
        dpr = float(self.devicePixelRatioF() or 1.0)
        if (self._layout is None or self._layout.W != self.width()
                or self._layout.H != self.height()
                or abs(dpr - self._dpr) > 1e-6):
            self._dpr = dpr
            self._layout = Layout(self.width(), self.height())
            self._achtergrond = QPixmap()
            self._logo = QPixmap()
            self._tekst = QPixmap()
            self._beeld_verouderd = True
            self._meld_maatvoering()
        if self._beeld_verouderd:
            self._beeld_verouderd = False
            self._herbouw_beeld()
        return self._layout

    def _meld_maatvoering(self):
        """Eén regel in het logboek: wat er werkelijk gekozen is.

        Anders is de enige manier om erachter te komen dat er iets mis is met
        de maatvoering, een foto van het scherm. Dat is deze keer de dure weg
        gebleken.
        """
        L = self._layout
        f = lambda v: int(round(v * self._dpr))
        print(f"[COLLAGE] scherm {L.W}x{L.H} logisch @ {self._dpr:g}x = "
              f"{f(L.W)}x{f(L.H)} fysiek | raster {L.kolommen}x{L.rijen} "
              f"({L.n} tegels) | tegel {L.tw}x{L.th} logisch = "
              f"{f(L.tw)}x{f(L.th)} fysiek | rasterbreedte {L.raster_b}, "
              f"zijmarge {L.raster_x}", flush=True)

    def _herbouw_beeld(self):
        """Miniaturen en rijen opnieuw maken op de maat die nu geldt."""
        L = self._layout
        self._oude_stroken.clear()
        paden = self._paden[-L.n:]           # bovengrens: het raster
        self._paden, self._miniaturen = [], []
        for p in paden:
            mini = self._maak_miniatuur(p)
            if mini is not None:
                self._paden.append(p)
                self._miniaturen.append(mini)
        self._volgende = 0
        self._bouw_stroken()

    def _doek(self, b, h, vulling=None):
        """Een leeg doek op de FYSIEKE maat, met de schermschaal erop gezet.

        Zonder dit wordt er op de logische maat getekend en rekt Qt het
        resultaat daarna op naar de echte pixels — de waas op de booth. Met de
        schaal erop tekent Qt hem één op één, en blijven de coördinaten
        hieronder gewoon logische punten.
        """
        pm = QPixmap(max(1, int(round(b * self._dpr))),
                     max(1, int(round(h * self._dpr))))
        pm.setDevicePixelRatio(self._dpr)
        pm.fill(Qt.transparent if vulling is None else vulling)
        return pm

    def _maak_miniatuur(self, pad):
        """Eén keer op tegelmaat brengen en bewaren. Dit is de dure kant."""
        L = self._layout
        bron = QPixmap(pad)
        if bron.isNull():
            return None
        # Op de FYSIEKE tegelmaat schalen. Op de logische maat schalen levert
        # een tegel op die Qt daarna nog een keer moet oprekken.
        geschaald = bron.scaled(int(round(L.tw * self._dpr)),
                                int(round(L.th * self._dpr)),
                                Qt.KeepAspectRatioByExpanding,
                                Qt.SmoothTransformation)
        geschaald.setDevicePixelRatio(self._dpr)
        # midden uitsnijden en de hoeken afronden
        tegel = self._doek(L.tw, L.th)
        p = QPainter(tegel)
        p.setRenderHint(QPainter.Antialiasing, True)
        pad_vorm = QPainterPath()
        pad_vorm.addRoundedRect(0, 0, L.tw, L.th, L.ronding, L.ronding)
        p.setClipPath(pad_vorm)
        p.drawPixmap(int(round(-(geschaald.width() / self._dpr - L.tw) / 2.0)),
                     int(round(-(geschaald.height() / self._dpr - L.th) / 2.0)),
                     geschaald)
        p.end()
        return tegel

    def _bouw_stroken(self):
        """Elke rij één keer tot een brede afbeelding samenstellen.

        De strook is één periode lang (het raster plus één tussenruimte), zodat
        hij naadloos herhaald kan worden door hem twee keer naast elkaar te
        tekenen. Schuiven is daarna alleen nog een uitsnede.
        """
        L = self._layout
        self._stroken = []
        if not self._miniaturen:
            return
        self._stroken = [self._maak_strook(r) for r in range(L.rijen)]

    def _bouw_strook(self, r):
        """Eén rij opnieuw samenstellen — na het vervangen van één tegel."""
        if 0 <= r < len(self._stroken):
            self._stroken[r] = self._maak_strook(r)

    def _maak_strook(self, r):
        L = self._layout
        periode = L.raster_b + L.gap
        strook = self._doek(periode, L.th)
        p = QPainter(strook)
        for c in range(L.kolommen):
            i = r * L.kolommen + c
            if i < len(self._miniaturen):
                p.drawPixmap(c * (L.tw + L.gap), 0, self._miniaturen[i])
        p.end()
        return strook

    def _zorg_achtergrond(self):
        """De achtergrond één keer op schermmaat brengen — niet per beeldje.

        _BgWidget in photobooth.py doet dat wél per beeldje, met
        SmoothTransformation en zonder cache. Op vijf megapixel is dat het
        duurste dat er in dat bestand staat; dat patroon is hier bewust niet
        overgenomen.
        """
        L = self._layout
        if not self._achtergrond.isNull():
            return
        if self._achtergrond_bron.isNull():
            self._achtergrond = self._doek(L.W, L.H, QColor(merk.INKT))
            return
        geschaald = self._achtergrond_bron.scaled(
            int(round(L.W * self._dpr)), int(round(L.H * self._dpr)),
            Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        geschaald.setDevicePixelRatio(self._dpr)
        doek = self._doek(L.W, L.H, QColor(merk.INKT))
        p = QPainter(doek)
        p.drawPixmap(int(round((L.W - geschaald.width() / self._dpr) / 2.0)),
                     int(round((L.H - geschaald.height() / self._dpr) / 2.0)),
                     geschaald)
        p.end()
        self._achtergrond = doek

    def _zorg_logo(self):
        """Het logo op de fysieke maat rasteren.

        De bron is 1948 x 1407 en dus ruim groter dan waar hij naartoe gaat —
        er wordt altijd verkleind, nooit vergroot. De waas op de booth kwam
        niet van de bron maar hiervandaan: hij werd op de LOGISCHE maat
        geschaald en daarna door Qt weer opgerekt.
        """
        L = self._layout
        if not self._logo.isNull():
            return
        pad = _asset("logo.png")
        bron = QPixmap(pad) if pad else QPixmap()
        if bron.isNull():
            return
        self._logo = bron.scaled(int(round(L.logo_w * self._dpr)),
                                 int(round(L.logo_h * self._dpr)),
                                 Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._logo.setDevicePixelRatio(self._dpr)
        # KeepAspectRatio laat aan één kant ruimte over; daar wordt op
        # gecentreerd, anders staat het logo net niet in het midden.
        self._logo_x = L.logo_x + int(round(
            (L.logo_w - self._logo.width() / self._dpr) / 2.0))
        self._logo_y = L.logo_y + int(round(
            (L.logo_h - self._logo.height() / self._dpr) / 2.0))

    def _zorg_tekst(self):
        """De instructie één keer rasteren, op de fysieke maat.

        Als tekst en niet als plaatje, zodat hij mee kan met de taalkeuze —
        het huidige startscherm is Engels omdat de tekst in het beeld gebakken
        zit. Twee regels: de eerste wit, de tweede in het merkgroen.

        Het doek staat op de schermschaal, dus Qt rastert de letters op de
        echte pixels van de tablet. Dat is even scherp als rechtstreeks tekenen
        en het scheelt het zetwerk van vijfentwintig keer per seconde — en dat
        laatste was de reden om hem überhaupt te bewaren.
        """
        L = self._layout
        if not self._tekst.isNull():
            return
        try:
            from translations import t
            regel1 = t("idle_tap_line1")
            regel2 = t("idle_tap_line2")
            if not regel1 or regel1 == "idle_tap_line1":
                raise KeyError
        except Exception:
            regel1, regel2 = "Druk op het scherm", "om een foto te maken"

        doek = self._doek(L.txt_w, L.txt_h)
        p = QPainter(doek)
        p.setRenderHint(QPainter.TextAntialiasing, True)

        # Corps zo groot dat de twee regels samen het blok vullen.
        korps = int(L.txt_h / 2.0 / 1.14)
        letter = merk.letter(korps, vet=True)
        letter.setFamily(KOP_ZWAAR)
        letter.setLetterSpacing(letter.PercentageSpacing, 98.2)
        p.setFont(letter)
        fm = QFontMetrics(letter)
        regelhoogte = int(korps * 1.14)
        boven = (L.txt_h - 2 * regelhoogte) // 2

        for i, (regel, kleur) in enumerate(((regel1, merk.OP_DONKER),
                                            (regel2, merk.GROEN))):
            p.setPen(QColor(kleur))
            b = fm.horizontalAdvance(regel) if hasattr(fm, "horizontalAdvance") \
                else fm.width(regel)
            p.drawText(int((L.txt_w - b) / 2),
                       boven + i * regelhoogte + fm.ascent(), regel)
        p.end()
        self._tekst = doek

    # ── tekenen ────────────────────────────────────────────────────────────
    def paintEvent(self, event):
        L = self._zorg_layout()
        if L is None:
            return
        self._zorg_achtergrond()
        self._zorg_logo()
        self._zorg_tekst()

        t = self._klok.elapsed() / 1000.0
        p = QPainter(self)

        # 1. achtergrond — één blit, nooit geschaald tijdens het draaien
        p.drawPixmap(0, 0, self._achtergrond)

        # 2. de verschuiving tegen inbranden: de hele opbouw kruipt mee, als
        #    één laag, zodat de onderlinge verhoudingen kloppen blijven.
        #    De uitslag schaalt met het scherm, net als in het ontwerp — 56 px
        #    is gemeten op de ontwerpmaat, niet op logische punten.
        dx = DRIFT_X * L.s * math.sin(2 * math.pi * t / DRIFT_PERIODE_X)
        dy = DRIFT_Y * L.s * math.sin(2 * math.pi * t / DRIFT_PERIODE_Y)
        p.translate(dx, dy)

        # 3. de schuivende rijen
        zichtbaar = self.rijen_zichtbaar()
        if zichtbaar and self._stroken:
            periode = L.raster_b + L.gap
            for r in range(zichtbaar):
                if r >= len(self._stroken):
                    break
                y = L.rij_y(r)
                # om en om de andere kant op; dat leest rustiger dan alles
                # dezelfde kant op
                richting = -1 if (r % 2 == 0) else 1
                verschuiving = (richting * SCHUIF_PX_S * t) % periode
                p.setClipRect(QRect(L.raster_x, int(y), L.raster_b, L.th))
                x0 = L.raster_x + int(verschuiving)
                p.drawPixmap(x0, int(y), self._stroken[r])
                p.drawPixmap(x0 - periode, int(y), self._stroken[r])

                # een vervangen tegel vloeit over: de oude strook eroverheen,
                # aflopend doorzichtig. Kost twee blits, 1,2 s lang.
                oud = self._oude_stroken.get(r)
                if oud is not None:
                    verstreken = self._klok.elapsed() - oud[1]
                    if verstreken >= OVERVLOEI_MS:
                        self._oude_stroken.pop(r, None)
                    else:
                        p.setOpacity(1.0 - verstreken / float(OVERVLOEI_MS))
                        p.drawPixmap(x0, int(y), oud[0])
                        p.drawPixmap(x0 - periode, int(y), oud[0])
                        p.setOpacity(1.0)
                p.setClipping(False)

        # 4. de instructie — een gecachte pixmap die alleen van plek verandert
        if not self._tekst.isNull():
            p.drawPixmap(L.txt_x, int(L.tekst_y(zichtbaar)), self._tekst)

        # 5. het logo — staat onderaan, gecentreerd, in elke toestand
        if not self._logo.isNull():
            p.drawPixmap(self._logo_x, self._logo_y, self._logo)
        p.end()


def _asset(naam):
    """Zoek een meegeleverd bestand, zowel los als in een gebouwde .exe."""
    for basis in (getattr(config, "BUNDLE_DIR", None), getattr(config, "BASE_DIR", None)):
        if not basis:
            continue
        pad = os.path.join(basis, "startscherm", naam)
        if os.path.isfile(pad):
            return pad
    return ""


def collage_aan(event):
    """Staat de collage aan voor dit event?

    Een schakelaar, geen ontwerpkeuze: niet elk feest wil dit. Een
    bedrijfsfeest, een schoolfeest, een bruidspaar dat het liever niet heeft.
    Staat hij uit, dan valt het scherm terug op de lege toestand — en die is
    af, dus dat kost niets.

    Standaard aan; wie hem uit wil zetten, doet dat per event.
    """
    if event is None:
        return False
    return bool(getattr(event, "collage_enabled", True))


def fotos_van_event(raw_dir):
    """De foto's die op het startscherm horen te staan, op volgorde."""
    return _sessies_uit_map(raw_dir)
