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

        self._paden = []          # de bronbestanden, op volgorde van aankomst
        self._miniaturen = []     # dezelfde volgorde, op tegelmaat
        self._stroken = []        # één brede pixmap per rij
        self._oude_stroken = {}   # rij -> (pixmap, tijdstip) tijdens een overvloeier
        self._volgende = 0        # welk vakje als eerstvolgende vervangen wordt

        self._logo = QPixmap()
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
        """Vervang de hele lijst. Alleen bij het opbouwen van het scherm."""
        L = self._zorg_layout()
        if L is None:
            self._paden = list(paden)
            return
        self._paden = list(paden)[-L.n:]          # bovengrens: het raster
        self._miniaturen = [self._maak_miniatuur(p) for p in self._paden]
        self._miniaturen = [m for m in self._miniaturen if m is not None]
        self._volgende = 0
        self._oude_stroken.clear()
        self._bouw_stroken()
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
    def _zorg_layout(self):
        if self.width() <= 0 or self.height() <= 0:
            return None
        if (self._layout is None or self._layout.W != self.width()
                or self._layout.H != self.height()):
            self._layout = Layout(self.width(), self.height())
            self._achtergrond = QPixmap()
            self._logo = QPixmap()
            self._tekst = QPixmap()
        return self._layout

    def _maak_miniatuur(self, pad):
        """Eén keer op tegelmaat brengen en bewaren. Dit is de dure kant."""
        L = self._layout
        bron = QPixmap(pad)
        if bron.isNull():
            return None
        geschaald = bron.scaled(L.tw, L.th, Qt.KeepAspectRatioByExpanding,
                                Qt.SmoothTransformation)
        # midden uitsnijden en de hoeken afronden
        tegel = QPixmap(L.tw, L.th)
        tegel.fill(Qt.transparent)
        p = QPainter(tegel)
        p.setRenderHint(QPainter.Antialiasing, True)
        pad_vorm = QPainterPath()
        pad_vorm.addRoundedRect(0, 0, L.tw, L.th, L.ronding, L.ronding)
        p.setClipPath(pad_vorm)
        p.drawPixmap(-(geschaald.width() - L.tw) // 2,
                     -(geschaald.height() - L.th) // 2, geschaald)
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
        strook = QPixmap(periode, L.th)
        strook.fill(Qt.transparent)
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
            self._achtergrond = QPixmap(L.W, L.H)
            self._achtergrond.fill(QColor(merk.INKT))
            return
        geschaald = self._achtergrond_bron.scaled(
            L.W, L.H, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        doek = QPixmap(L.W, L.H)
        doek.fill(QColor(merk.INKT))
        p = QPainter(doek)
        p.drawPixmap((L.W - geschaald.width()) // 2,
                     (L.H - geschaald.height()) // 2, geschaald)
        p.end()
        self._achtergrond = doek

    def _zorg_logo(self):
        L = self._layout
        if not self._logo.isNull():
            return
        pad = _asset("logo.png")
        bron = QPixmap(pad) if pad else QPixmap()
        if not bron.isNull():
            self._logo = bron.scaled(L.logo_w, L.logo_h, Qt.KeepAspectRatio,
                                     Qt.SmoothTransformation)

    def _zorg_tekst(self):
        """De instructie één keer rasteren.

        Als tekst en niet als plaatje, zodat hij mee kan met de taalkeuze —
        het huidige startscherm is Engels omdat de tekst in het beeld gebakken
        zit. Twee regels: de eerste wit, de tweede in het merkgroen.
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

        doek = QPixmap(L.txt_w, L.txt_h)
        doek.fill(Qt.transparent)
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
        dx = DRIFT_X * math.sin(2 * math.pi * t / DRIFT_PERIODE_X)
        dy = DRIFT_Y * math.sin(2 * math.pi * t / DRIFT_PERIODE_Y)
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
            p.drawPixmap(L.logo_x, L.logo_y, self._logo)
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
