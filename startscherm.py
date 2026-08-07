"""Het startscherm van MyBoothBox — de collage.

Het scherm dat de hele avond staat te wachten. Van boven naar beneden: een rij
foto's, de instructie, nog een rij foto's, en onderin de onderbouw met de
verhuurvraag, het logo en het slotje. Verder niets.

Wat er tussen die dingen staat is LUCHT DIE UITGEREKEND WORDT en geen marge die
ergens is ingetikt — zie de klasse Layout. Dat is de belangrijkste regel van dit
bestand: de vorige opzet hing de collage aan de bovenrand, het logo aan de
onderrand en de instructie in het midden van wat er overbleef, en het oordeel
daarover was "een zwervend hoopje rommel".

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

En wat er in beta.6 nog niet klopte
-----------------------------------
**De maat van de widget is niet de maat van het scherm.** De collage is de
idle-pagina, en die hangt in een QStackedWidget. QStackedLayout geeft in zijn
gewone stand ALLEEN de pagina die vooraan staat een maat; een pagina die nog
nooit vooraan gestaan heeft, houdt de maat die hij toevallig had. Daar komt bij
dat de stapel groter kan uitvallen dan het scherm. De indeling werd dus gemaakt
op een maat die nergens op sloeg, en het raster besloeg nog maar de halve
breedte terwijl de tegels te klein bleven.

De indeling gaat daarom niet meer over self.width()/height() maar over het vlak
dat werkelijk te zien is: de doorsnede van deze widget met het scherm. Elders in
photobooth.py stond die omrekening al, voor het slotje (_position_idle_lock), en
om precies dezelfde reden.

Let op de rekenregel die hierbij hoort: de rasterbreedte volgt uit de HOOGTE,
niet uit de breedte. De maatvoering schaalt met de korte zijde, dus
rasterbreedte is ongeveer 1,31 x de hoogte. Zie je het raster op de halve
breedte staan, dan is de hoogte die de widget denkt te hebben de helft van wat
hij hoort te zijn — en niet de breedte. Dat staat er ook bij in het logboek.
"""

import math
import os
import re
from datetime import datetime

from PyQt5.QtCore import (Qt, QTimer, QRect, QPoint, QElapsedTimer,
                          pyqtSignal)
from PyQt5.QtGui import (QPixmap, QPainter, QColor, QPainterPath,
                         QFontMetrics, QPen)
from PyQt5.QtWidgets import QWidget

import config
import merk

# ── maatvoering — overgenomen uit docs/startscherm/render/ontwerp.py ────────
REF = 1824          # referentie op de korte zijde; alles schaalt hiermee mee
M_V = 64            # de kleinste marge die er onderaan overblijft; zie Layout
GAP = 26            # tussenruimte tussen tegels binnen één rijgroep
TILE_W, TILE_H = 456, 285      # tegel 16:10

# Het instructieblok. Was 1420 x 267; de opdrachtgever wilde de tekst "misschien
# 15 procent" groter, en dat is het geworden: 1633 x 307.
#
# Wat die vergroting kost, staat in het rekenmodel van Layout en is dus na te
# rekenen in plaats van te beoordelen. Op de Surface (1824 hoog, dus s = 1) gaat
# er 40 ontwerppixels aan inhoud bij — 2,2% van de schermhoogte. Die 40 komen
# uit de lucht, en omdat die over vier openingen verdeeld wordt, krimpt elke
# opening met 10 punten: van 90 naar 80, ofwel van 4,9% naar 4,4% van de hoogte.
#
# Dat is het waard. De instructie is het enige wat een gast op dit scherm moet
# lezen, en hij leest hem van twee meter afstand in een donkere zaal. De
# verhouding tussen de vier openingen blijft exact gelijk — dat is wat het model
# bewaakt, en niet de absolute maat.
#
# De breedte gaat met dezelfde 15% mee. Niet omdat de tekst hem nodig heeft (de
# breedste regel haalt ongeveer 1270 van de 1633), maar omdat het blok anders
# van vorm verandert zodra de letter groeit, en dan kapt het doek de tekst af bij
# een langere vertaling.
TXT_W, TXT_H = 1633, 307

LOGO_W, LOGO_H = 520, 376
RONDING = 12        # hoekafronding van een tegel

# De verhuurvraag linksonder: een regeltje, een kronkelende pijl, en een QR
# naar de site. Alle drie in ONTWERPPIXELS, net als de rest hierboven.
#
# Over de maat van die QR. Hij moet gescand worden van de afstand waarop iemand
# geïnteresseerd raakt, en dat is niet de meter waarop je het scherm ziet maar
# de halve meter waarop je ernaartoe loopt. 300 ontwerppixels is 28 mm op het
# glas; daar komt een telefoon vanaf een centimeter of veertig doorheen. Groter
# zou beter scannen en het scherm overnemen — dit is een uitnodiging, geen
# advertentie.
#
# En het adres blijft kort en zonder toevoegingen (config.BOOTH_QR_URL). Elk
# teken erbij maakt de code dichter en dus slechter leesbaar; de herkomst wordt
# aan de serverkant vastgelegd.
QR_MAAT = 300       # het witte vlak waar de code in staat
QR_RAND = 20        # stille zone binnen dat vlak — hoort erbij, niet weglaten
QR_TEKST_W = 620    # breed genoeg voor "Ook een photobooth huren?" op twee regels
QR_TEKST_H = 130
# Ruimte tussen het regeltje en de code, waar de pijl loopt. Was 130; de
# opdrachtgever vond dat het geheel zweefde — "tekst, dan niets, dan een QR".
# De pijl begint nu NAAST het laatste woord in plaats van eronder, dus die
# ruimte is er niet meer voor nodig ook.
QR_PIJL_H = 96

# De pijl zelf, in verhoudingen tot zijn eigen lijndikte. Zie _teken_pijl().
PIJL_DIK = 7.0          # lijndikte in ontwerppixels; overal even dik
PIJL_PUNT_LANG = 4.2    # lengte van de pijlpunt, in lijndiktes
PIJL_PUNT_BREED = 1.55  # halve basisbreedte van de punt, in lijndiktes
PIJL_HOEK_EIND = 112.0  # onder welke hoek hij op de code aankomt, graden met
                        # de y-as naar beneden: overwegend omlaag, iets naar
                        # links, zodat de punt de code aanwijst en niet langs
                        # de code scheert
PIJL_MIK = 0.55         # waar op de bovenrand van de code hij aanwijst
PIJL_ZWAAI = 1.15       # hoe ver hij naar rechts uitzwaait voordat hij
                        # terugkomt, als deel van de ruimte rechts van het
                        # aanlooppunt. Boven 1 ligt het stuurpunt buiten het
                        # doek; dat mag — alleen de kromme wordt getekend, en
                        # die haalt ongeveer 42% van die afstand. Was 1,35: dan
                        # schoot de pijl de lege ruimte rechts van de QR in en
                        # zweefde het geheel. Korter houdt tekst, pijl en code
                        # bij elkaar.
PIJL_DAAL = 0.55        # hoeveel van de daling hij op de heenweg al maakt.
                        # Bij nul liggen heen- en terugweg op dezelfde hoogte
                        # en knijpt de bocht rechts tot een punt — precies het
                        # hoekige waar de klacht over ging. Bij 0,30 was het nog
                        # een langgerekte punt; bij 0,55 is het een ronde bocht.
                        # Vier waarden naast elkaar gezet en bekeken, niet
                        # beredeneerd.
PIJL_GRIP = 0.45        # hoe recht hij op de code aankomt; deel van de afstand
                        # tussen begin en punt

# De letter van de instructie. Plus Jakarta Sans ExtraBold meldt zich bij Qt aan
# als een EIGEN familie en niet als een dikte binnen "Plus Jakarta Sans" — zie
# fonts/LEESMIJ.md. Hij moet dus op deze naam aangevraagd worden.
KOP_ZWAAR = "Plus Jakarta Sans ExtraBold"

# Hoe de collage vult en beweegt.
SCHUIF_PX_S = 12.0      # schuifsnelheid van een rij, punten per seconde
OVERVLOEI_MS = 1200     # een vervangen tegel vloeit in 1,2 s over

# En hoe een rij VOOR HET EERST verschijnt, tijdens het opbouwen van het scherm.
#
# De tegels worden in stukjes gemaakt zodat het scherm meteen bruikbaar is (zie
# _herbouw_beeld). Daardoor komen de rijen er ná elkaar bij, en tot nu toe
# ploften ze er hard in: "bij het opbouwen glitcht het een beetje". Een rij die
# in een kwart seconde opkomt leest als iets dat zich vult; een rij die er
# ineens staat leest als een storing.
#
# Het kost niets: het is één extra getal op de verf, geen extra tekenwerk.
INVLOEI_MS = 260

# ...maar het schuiven staat STANDAARD UIT. De opdrachtgever heeft er twee keer
# zelf over begonnen: "misschien moeten we het maar gewoon statisch houden" en
# daarna "die foto's bewegen nog steeds heen en weer en heel traag".
#
# Het kost inderdaad niets — gemeten op de booth zelf: zes milliseconde van de
# veertig, met het schuiven aan. Maar goedkoop is geen reden om iets aan te
# laten staan dat er niet uitziet. De collage doet zijn werk stilstaand net zo
# goed: de foto's van het feest staan er, en er komt elke sessie een nieuwe bij.
#
# De schakelaar blijft; alleen de standaardstand gaat om.
SCHUIVEN_STANDAARD = False

# Twee tekenfrequenties, want er zijn twee soorten beweging en ze kosten niet
# hetzelfde. Zie de klasse Collage: schuiven is versiering en mag uit; de
# verschuiving tegen inbranden is bescherming en blijft altijd aan. Staat het
# schuiven uit, dan hoeft er nog maar twee keer per seconde getekend te worden
# — er verandert dan hooguit één pixel per twee seconden.
BEELDJES_S = 25
BEELDJES_S_STIL = 2

# Verschuiving tegen inbranden: de hele opbouw kruipt heel langzaam rond, met
# perioden die geen deler gemeen hebben, zodat het pad zich pas na uren
# herhaalt. Topsnelheid 0,53 px/s — dat is niet te zien.
DRIFT_X, DRIFT_Y = 56, 34
DRIFT_PERIODE_X = 11 * 60.0
DRIFT_PERIODE_Y = 17 * 60.0

# En de achtergrond schuift er zelfstandig achterlangs. Dat is niet dezelfde
# beweging als hierboven: die verplaatst de hele opbouw als één laag, en als de
# achtergrond meeging zou er niets aan veranderen.
#
# Waarom dit moet. Bij een volle collage beweegt er van alles en is het risico
# klein. Bij een LEGE collage is de achtergrond het enige wat er is — het begin
# van de avond, en de hele avond bij een event waar de collage uit staat. Juist
# die stand staat het langst stil.
#
# En het is gratis: het veld staat overmaats klaar en er wordt een bewegend
# deelvlak uit getekend. Eén blit, net als een stilstaande achtergrond; er wordt
# per beeldje niets geschaald.
BG_OVERMAAT = 1.15      # hoeveel groter dan het scherm het veld staat
BG_PERIODE_X = 10 * 60.0
BG_PERIODE_Y = 13 * 60.0

# ...maar STANDAARD UIT. Op de echte booth liep die pan niet vloeiend: "de ene
# keer doet hij het wel, de andere keer niet". Een beweging die hapert leest als
# een storing; een stilstaande achtergrond leest als niets. Bij twijfel wint dus
# stilstand.
#
# Dat het scherm daarmee onbeschermd zou zijn, klopt niet. De achtergrond is een
# wazig verloop zonder één scherpe rand — daar brandt niets van in. Het risico
# zit in de instructie, het logo, het slotje en het serienummer, en die worden
# beschermd door de verschuiving hierboven. Die kost een verplaatsing van de
# tekenpositie en verder niets, en blijft dus altijd aan.
#
# De pan is aan te zetten met de schakelaar bij de instellingen, voor wie hem op
# zijn eigen booth wil proberen.
PARALLAX_STANDAARD = False


class Layout:
    """De indeling. De enige plek waar maten staan — net als in het ontwerp.

    DE VERTICALE INDELING IS EEN VERDELING, GEEN RIJTJE MARGES
    ----------------------------------------------------------
    Dit is de kern van dit bestand en het is met opzet één som. Het oordeel over
    de vorige opzet was "een zwervend hoopje rommel", en dat kwam niet doordat
    er verkeerde getallen stonden maar doordat er getallen stonden. De collage
    hing met een vaste marge aan de bovenrand, het logo met een vaste marge aan
    de onderrand, en de instructie centreerde zich in wat er toevallig
    tussenbleef. Werd één van die drie een maatje anders, dan verschoof alleen
    de instructie mee en liep de rest uit de pas.

    Nu wordt het andersom gerekend. Van boven naar beneden staan er vier
    BLOKKEN op het scherm, met tussen en boven elk blok een OPENING:

        opening 1
        de bovenste rijgroep foto's
        opening 2
        de instructie ("Druk op het scherm om een foto te maken")
        opening 3
        de onderste rijgroep foto's
        opening 4
        de onderbouw: de verhuurvraag met de QR, het logo, het slotje

    De som is: tel op wat de blokken aan hoogte kosten, trek dat af van de
    hoogte die te verdelen valt, en deel de rest in EVENVEEL GELIJKE OPENINGEN
    als er blokken zijn. Er staat dus nergens meer een marge tussen twee dingen.
    Verandert een tegel van maat, wordt de instructie groter, of komt er een
    regel bij in de verhuurvraag, dan verschuift alles vanzelf mee en blijven de
    openingen onderling gelijk.

    Op de Surface Pro 7 (2736 x 1824, en 1824 is precies REF, dus s = 1) komt
    dat uit op:

        bovenste rij foto's      285   15,6%
        de instructie            307   16,8%
        onderste rij foto's      285   15,6%
        de onderbouw             564   30,9%
        ---------------------------------------
        inhoud                  1441   79,0%
        vier openingen van        80    4,4%   (samen 17,5%)
        ondermarge                64    3,5%

    De ondermarge staat er los van: die is de bodem waar de onderbouw op rust,
    niet een opening tussen twee dingen. Vandaar dat er onder de onderbouw geen
    vijfde opening zit.

    Past de inhoud niet — en dat kan alleen als er onderin iets groots bij komt
    — dan wijkt er een RIJ FOTO'S en niet een stuk lucht. Zie _verdeel().

    RUIMTE VOOR IETS DAT NIET VAN DEZE WIDGET IS
    --------------------------------------------
    `onderruimte` is hoeveel er onderaan vrij moet blijven, gemeten vanaf de
    onderrand, voor iets dat een ander neerzet. In de praktijk is dat de wifi-tip
    die photobooth.py erover legt. Die balk lag over de onderste helft van het
    logo heen — je zag "MY BOOTH" en niet "BOX".

    Het gaat de verdeling niet slopen, want het is één getal in dezelfde som:
    het verlaagt de hoogte die te verdelen valt, en dus krimpen de vier
    openingen alle vier evenveel. De volgorde en de verhoudingen blijven staan.
    Nul betekent: niets aan de hand. Er blijft daarmee ook geen gat staan waar de
    balk gestaan heeft, want de indeling wordt opnieuw uitgerekend en niet
    bijgewerkt.

    Het is met opzet een AANTAL PUNTEN en geen schakelaar: de balk kan van maat
    veranderen (langere tekst, andere schermschaal) en dan hoort de indeling mee
    te bewegen.
    """

    def __init__(self, W, H, onderruimte=0):
        self.W, self.H = W, H
        self.liggend = W > H
        self.s = (H if self.liggend else W) / float(REF)
        r = lambda v: int(round(v * self.s))

        self.mv, self.gap = r(M_V), r(GAP)
        self.tw, self.th = r(TILE_W), r(TILE_H)
        self.txt_w, self.txt_h = r(TXT_W), r(TXT_H)
        self.logo_w, self.logo_h = r(LOGO_W), r(LOGO_H)
        self.ronding = max(4, r(RONDING))

        # HET RASTER LOOPT VAN RAND TOT RAND.
        #
        # Niet vijf tegels met een marge ernaast, maar zoveel hele tegels als
        # er nodig zijn om de breedte te dekken, met aan weerszijden een stuk
        # dat buiten beeld valt. Je ziet aan de randen dus halve foto's, en dat
        # is de bedoeling: dan leest het als een wand met foto's die doorloopt,
        # in plaats van een blok dat ergens ophoudt.
        #
        # Hoeveel er minimaal moet overhangen is geen smaak maar een som. De
        # hele opbouw kruipt heen en weer tegen inbranden, DRIFT_X punten naar
        # weerszijden. Hangt het raster minder ver over dan die uitslag, dan
        # komt op het uiterste punt van die beweging de rand van het raster
        # het scherm binnen en staat er een lege strook. Vandaar de uitslag
        # plus twee punten speling.
        self.overhang_min = int(math.ceil(DRIFT_X * self.s)) + 2
        nodig = W + 2 * self.overhang_min
        self.kolommen = max(2, int(math.ceil(
            (nodig + self.gap) / float(self.tw + self.gap))))
        # TWEE RIJEN LIGGEND, DRIE STAAND.
        #
        # Waren er drie en vijf. Met een raster dat van rand tot rand loopt
        # werd dat te druk, en de hoogte die vrijkomt gaat naar RUST — niet
        # naar grotere tegels. De tegelmaat blijft dus wat hij was; er komt
        # alleen lucht tussen de collage en de instructie.
        #
        # Staand is met dezelfde verhouding meegegaan (vijf gedeeld door
        # anderhalf is drie); dat scherm is veel hoger, dus daar valt die
        # ruimte ook echt als ruimte.
        self.rijen_max = 2 if self.liggend else 3

        self.raster_b = self.kolommen * self.tw + (self.kolommen - 1) * self.gap
        # Deelt zich vanzelf symmetrisch: links en rechts evenveel eraf.
        self.raster_x = (W - self.raster_b) // 2
        self.overhang = (self.raster_b - W) / 2.0

        # DE ONDERBOUW. Eén blok met drie dingen erin: de verhuurvraag met de
        # QR linksonder, het logo in het midden, en (buiten deze widget) het
        # slotje met het serienummer rechtsonder.
        self.qr_maat = r(QR_MAAT)
        self.qr_rand = max(2, r(QR_RAND))
        self.qr_tekst_w = r(QR_TEKST_W)
        self.qr_tekst_h = r(QR_TEKST_H)
        self.qr_pijl_h = r(QR_PIJL_H)
        # Het hoogste van de drie is de verhuurvraag: regeltje, pijl, code.
        qr_blok_h = self.qr_tekst_h + self.qr_pijl_h + self.qr_maat
        # Het logo staat verticaal op de CODE gecentreerd en niet op het hele
        # blok. Dat is het "iets lager en iets dichter bij de QR-code": zo
        # staan het logo en de code op één band in plaats van dat het logo
        # boven de code uit begint te zweven. Het logo is hoger dan de code, dus
        # het steekt er aan weerszijden een stukje buiten — de onderste helft
        # daarvan telt mee in de hoogte van het blok, anders klopt de som niet.
        self.logo_uitloop = max(0, (self.logo_h - self.qr_maat) // 2)
        self.onderbouw_h = qr_blok_h + self.logo_uitloop

        # ── de verdeling ───────────────────────────────────────────────────
        #
        # Hier gebeurt het. Zie de klasse-uitleg hierboven: de blokken kosten
        # wat ze kosten, en wat er overblijft wordt in gelijke openingen
        # verdeeld. Nergens hieronder staat nog een marge tussen twee dingen.
        self.onderruimte = max(0, int(onderruimte))
        # De bodem waar de onderbouw op rust. Normaal de ondermarge; staat er
        # een melding onderin, dan wat die melding bezet houdt.
        self.bodem = H - max(self.mv, self.onderruimte)

        # WAT ER WIJKT ALS HET NIET PAST.
        #
        # De inhoud kost bijna vier vijfde van de hoogte. Komt er onderin een
        # melding bij die groot genoeg is, dan blijft er niets te verdelen over
        # en zouden de blokken door elkaar heen gaan lopen — en "niets mag
        # overlappen" is een harde eis, geen streven.
        #
        # Dus gaat er dan een RIJ FOTO'S af, en niet een stuk lucht. De collage
        # is versiering; de instructie, de verhuurvraag en het logo zijn waar
        # het scherm voor is. Bij de melding die er nu staat gebeurt dit niet —
        # het is een vangnet voor als die balk ooit groter wordt.
        self.rijen = self.rijen_max
        while True:
            self._verdeel()
            if self.lucht >= 0 or self.rijen <= 0:
                break
            self.rijen -= 1
        self.n = self.kolommen * self.rijen

    def _verdeel(self):
        """De som zelf, voor het aantal rijen dat er nu staat."""
        W, H = self.W, self.H

        # De rijen gaan uit elkaar, met de instructie ertussen. Ze stonden tegen
        # elkaar aan als één blok bovenin; de bovenste helft gaat nu boven de
        # instructie staan en de onderste eronder. Bij twee rijen is dat één en
        # één; bij drie (staand) twee en één, en die twee houden onderling de
        # gewone tussenruimte.
        self.rijen_boven = (self.rijen + 1) // 2
        self.rijen_onder = self.rijen - self.rijen_boven
        groep_h = lambda n: (n * self.th + (n - 1) * self.gap) if n > 0 else 0
        self.groep_boven_h = groep_h(self.rijen_boven)
        self.groep_onder_h = groep_h(self.rijen_onder)

        # Alleen blokken die er WERKELIJK zijn tellen mee, en er is één opening
        # boven elk blok. Zonder dat zou een lege rijgroep twee openingen achter
        # elkaar zetten en stond er ineens dubbele lucht op één plek.
        self.blokken = [(naam, h) for naam, h in (
            ("rijen boven", self.groep_boven_h),
            ("instructie", self.txt_h),
            ("rijen onder", self.groep_onder_h),
            ("onderbouw", self.onderbouw_h),
        ) if h > 0]
        self.openingen = len(self.blokken)
        self.inhoud_h = sum(h for _, h in self.blokken)
        self.lucht = self.bodem - self.inhoud_h
        self.opening = max(0.0, self.lucht / float(max(1, self.openingen)))

        # De y van elk blok: opening, blok, opening, blok, ... en het laatste
        # blok eindigt precies op de bodem.
        y = 0.0
        plek = {}
        for naam, h in self.blokken:
            y += self.opening
            plek[naam] = int(round(y))
            y += h
        self.raster_y = plek.get("rijen boven", plek.get("instructie", 0))
        self.txt_y = plek["instructie"]
        self.groep_onder_y = plek.get("rijen onder", self.txt_y + self.txt_h)
        self.onderbouw_y = plek["onderbouw"]

        # Wat er in de onderbouw staat, van links naar rechts.
        self.txt_x = (W - self.txt_w) // 2
        self.qr_tekst_x = self.mv
        self.qr_tekst_y = self.onderbouw_y
        self.qr_x = self.mv
        self.qr_y = self.qr_tekst_y + self.qr_tekst_h + self.qr_pijl_h
        # De basislijn van de onderbouw: de onderkant van de QR-code. Het logo
        # centreert erop, en photobooth.py zet het slotje met het serienummer
        # er met zijn onderkant op — zo staan de drie op één lijn.
        self.basislijn = self.qr_y + self.qr_maat
        self.logo_x = (W - self.logo_w) // 2
        self.logo_y = self.qr_y + (self.qr_maat - self.logo_h) // 2

    def rij_y(self, r):
        """Waar rij `r` staat. De bovenste groep bovenin, de rest onder de tekst."""
        if r < self.rijen_boven:
            return self.raster_y + r * (self.th + self.gap)
        return self.groep_onder_y + (r - self.rijen_boven) * (self.th + self.gap)


# ── de foto's ───────────────────────────────────────────────────────────────

_NAAM = re.compile(r"^(\d{2}-\d{2}-\d{4}_\d{2}\.\d{2}\.\d{2})_(\d+)\.jpe?g$",
                   re.IGNORECASE)


# ── miniaturen bewaren over paginawissels heen ──────────────────────────────
# Een miniatuur maken is de dure bewerking: een foto van zes megapixel inlezen
# en op tegelmaat brengen. Zonder deze cache gebeurde dat opnieuw bij elke
# herbouw van de idle-pagina — en die wordt herbouwd bij elke eventwissel en
# elke keer dat de licentiebanner omgaat.
#
# De sleutel is het pad plus de maat plus de schermschaal, want op een andere
# maat is het een andere miniatuur. De grens is ruim: vijftig tegels van 456 x
# 285 is ongeveer 26 MB.
_MINI_CACHE = {}
_MINI_CACHE_MAX = 50


def _cache_zet(sleutel, tegel):
    if len(_MINI_CACHE) >= _MINI_CACHE_MAX:
        # de oudste eruit; dit is geen echte LRU en hoeft dat ook niet te zijn
        for oud in list(_MINI_CACHE)[:len(_MINI_CACHE) - _MINI_CACHE_MAX + 1]:
            _MINI_CACHE.pop(oud, None)
    _MINI_CACHE[sleutel] = tegel


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

    TWEE SOORTEN BEWEGING, en ze zijn met opzet gescheiden.

    *Het schuiven van de rijen* is versiering. Het mag uit, en dat is een
    schakelaar bij de instellingen en geen keuze in de code — wie het op zijn
    booth niet wil, hoeft niet op een nieuwe versie te wachten. Staat het uit,
    dan staan de laatste foto's gewoon stil op het scherm en zakt het tekenen
    van vijfentwintig naar twee keer per seconde.

    *De trage verschuiving van de hele opbouw* is bescherming tegen inbranden
    en blijft altijd aan. Het is een verplaatsing van de tekenpositie, geen
    hertekening van de inhoud, en het kruipt met een halve punt per seconde.
    Dat kost vrijwel niets en het is het enige wat de instructie, het logo, het
    slotje en het serienummer ervan weerhoudt uren op dezelfde pixels te staan.

    En er wordt bijgehouden wat een beeldje werkelijk kost. Zie _tik(): elke
    minuut komt er een regel in het logboek met het gemiddelde en het duurste
    beeldje. Zonder dat staan we bij een klacht over haperen opnieuw naar
    foto's van een scherm te kijken.
    """

    # De verschuiving tegen inbranden, in logische punten. Het slotje en het
    # serienummer liggen BUITEN deze widget — photobooth.py legt ze er als losse
    # elementen overheen — en moeten dus meegetrokken worden, anders staan juist
    # die twee de hele avond stil. En dat zijn de gevaarlijkste van het scherm:
    # klein, contrastrijk, in een hoek waar verder niets gebeurt.
    verschoven = pyqtSignal(int, int)

    def __init__(self, achtergrond_pad="", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

        self._achtergrond_bron_pad = achtergrond_pad
        self._achtergrond_bron = QPixmap(achtergrond_pad) if achtergrond_pad else QPixmap()
        self._achtergrond = QPixmap()     # op schermmaat, één keer geschaald
        self._layout = None
        self._vlak = QRect()      # het zichtbare deel van deze widget
        self._vast_vlak = None    # opgegeven vlak; alleen voor gereedschap
        self._dpr = 1.0           # schermschaal; 2,0 op een tablet die op 200% staat
        self._beeld_verouderd = False
        self._in_paint = False
        self._parallax = PARALLAX_STANDAARD

        self._paden = []          # de bronbestanden, op volgorde van aankomst
        self._miniaturen = []     # dezelfde volgorde, op tegelmaat
        self._stroken = []        # één brede pixmap per rij
        self._oude_stroken = {}   # rij -> (pixmap, tijdstip) tijdens een overvloeier
        self._invloei = {}        # rij -> tijdstip waarop hij begon op te komen
        self._rijen_getoond = 0   # hoeveel rijen er de vorige keer stonden
        self._volgende = 0        # welk vakje als eerstvolgende vervangen wordt
        self._onderruimte = 0     # wat er onderaan voor iets anders vrij blijft

        self._logo = QPixmap()
        self._logo_x = self._logo_y = 0
        self._tekst = QPixmap()
        self._gemeld = None       # laatst gemelde verschuiving, hele punten

        self._schuiven = SCHUIVEN_STANDAARD
        self._klok = QElapsedTimer()
        self._klok.start()
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / BEELDJES_S))
        self._timer.timeout.connect(self._tik)

        # Een APARTE klok voor het opkomen en overvloeien.
        #
        # Die twee duren een kwart tot ruim één seconde en moeten in die tijd
        # vloeiend getekend worden. De gewone tekenlus staat standaard op twee
        # beeldjes per seconde — het schuiven van de foto's staat immers uit —
        # en dan zou een overvloeier van 1,2 seconde in twee sprongen gebeuren.
        # Dat is precies het haperen waar de klacht over ging.
        #
        # Hij hangt bewust NIET aan beweegt(): dat gaat over blijvende beweging
        # en bepaalt het grondtempo. Dit is eenmalig, dus het zet zichzelf ook
        # weer stil zodra er niets meer te vervagen valt.
        self._vloeitimer = QTimer(self)
        self._vloeitimer.setInterval(int(1000 / BEELDJES_S))
        self._vloeitimer.timeout.connect(self._vloei_tik)

        # Wat een beeldje werkelijk kost — de uitweg voor als het op de booth
        # alsnog hapert terwijl de meting zegt dat het niet kan.
        self._teken_klok = QElapsedTimer()
        self._beeldjes = 0
        self._teken_som = 0.0
        self._teken_ergste = 0.0
        self._volgend_verslag = 0
        self._drift_vorige = None
        self._drift_stappen = 0
        self._drift_pad = 0.0
        self._drift_grootste_stap = 0.0

        # Het opbouwen van de miniaturen loopt in stukjes, buiten het tonen om.
        self._wachtrij = []
        self._bouw_begonnen = None
        self._bouwtimer = QTimer(self)
        self._bouwtimer.setInterval(0)
        self._bouwtimer.timeout.connect(self._bouw_stukje)
        # Hoe lang het duurde voordat er iets te zien was, geteld vanaf het
        # moment dat photobooth.py dit scherm toonde.
        self._getoond_op = None
        self._eerste_beeldje_gemeld = False

        self._qr = QPixmap()
        self._qr_tekst = QPixmap()

    # ── leven ──────────────────────────────────────────────────────────────
    def start(self):
        """Het scherm wordt getoond. Vanaf hier telt de tijd tot het eerste
        beeldje — dat is wat een gast als traag ervaart."""
        if self._getoond_op is None:
            self._getoond_op = self._klok.elapsed()
            self._eerste_beeldje_gemeld = False
        self._stel_tempo_in()
        if not self._timer.isActive():
            self._timer.start()
        # Meteen één keer melden, zodat het slotje en het serienummer op hun
        # plek staan voordat de eerste verschuiving komt.
        self._gemeld = None
        self._meld_verschuiving()

    def stop(self):
        """Zet de tekenlus stil. Hoort te gebeuren zodra dit scherm weg is —
        anders staat er een timer van 25 keer per seconde te draaien terwijl de
        gast aan het fotograferen is."""
        self._timer.stop()
        self._vloeitimer.stop()

    # ── opkomen en overvloeien ─────────────────────────────────────────────
    def vloeit(self):
        """Is er op dit moment iets aan het opkomen of overvloeien?

        Op de TIJD en niet op de lijstjes. Die lijstjes worden pas opgeruimd
        door het tekenen zelf (_dekking en de overvloeier in paintEvent), en er
        wordt niet getekend als de widget niet te zien is. Wie hier op de
        lijstjes zou kijken, zou dus eeuwig wachten.

        Bestaat voor gereedschap dat wil wachten tot het beeld af is:
        schermafdrukken.py en test_startscherm.py. Zonder dit maakten die hun
        afdruk middenin het opkomen, en dan staan er halfdoorzichtige tegels op
        — wat er op de booth een kwart seconde te zien is, en niet wat er
        beoordeeld moet worden.
        """
        nu = self._klok.elapsed()
        if any(nu - begin < INVLOEI_MS for begin in self._invloei.values()):
            return True
        return any(nu - t < OVERVLOEI_MS for _, t in self._oude_stroken.values())

    def _zorg_vloeitimer(self):
        """De vloeiklok loopt precies zolang er iets te vervagen valt."""
        if self.vloeit():
            if not self._vloeitimer.isActive():
                self._vloeitimer.start()
        elif self._vloeitimer.isActive():
            self._vloeitimer.stop()

    def _vloei_tik(self):
        self.update()
        self._zorg_vloeitimer()

    def _laat_opkomen(self, rijen):
        """Deze rijen staan er nu voor het eerst — laat ze opkomen.

        Wordt aangeroepen zodra het opbouwen een rij vol heeft. Een rij die er
        ineens staat leest als een storing; een rij die in een kwart seconde
        opkomt leest als iets dat zich vult.
        """
        nu = self._klok.elapsed()
        for r in rijen:
            self._invloei.setdefault(r, nu)
        self._zorg_vloeitimer()

    def _dekking(self, r):
        """Hoe ver deze rij is opgekomen: 0 tot 1. Weg uit de lijst als hij er is."""
        begin = self._invloei.get(r)
        if begin is None:
            return 1.0
        verstreken = self._klok.elapsed() - begin
        if verstreken >= INVLOEI_MS:
            self._invloei.pop(r, None)
            return 1.0
        return max(0.0, min(1.0, verstreken / float(INVLOEI_MS)))

    def zet_schuiven(self, aan):
        """Schuiven aan of uit. Raakt de verschuiving tegen inbranden niet.

        Uit betekent: de laatste foto's staan stil op het scherm, en er hoeft
        nog maar twee keer per seconde getekend te worden in plaats van
        vijfentwintig.
        """
        aan = bool(aan)
        if aan == self._schuiven:
            return
        self._schuiven = aan
        self._stel_tempo_in()
        print(f"[COLLAGE] schuiven {'AAN' if aan else 'UIT'} — de verschuiving "
              f"tegen inbranden blijft hoe dan ook aan", flush=True)
        self.update()

    def _stel_tempo_in(self):
        """Hoe vaak er getekend moet worden, gegeven wat er beweegt."""
        b = BEELDJES_S if self.beweegt() else BEELDJES_S_STIL
        self._timer.setInterval(int(1000 / b))
        print(f"[COLLAGE] {b} beeldjes per seconde", flush=True)

    def _tik(self):
        # Alleen hertekenen wat beweegt: het gebied van de collage, plus de
        # strook waar de instructie staat. Het logo staat stil (op de
        # verschuiving na, en die kruipt met een halve pixel per seconde).
        self.update()
        self._meld_verschuiving()
        self._verslag_tekentijd()

    def _verslag_tekentijd(self):
        """Elke minuut één regel: wat kostte een beeldje werkelijk.

        De uitweg voor als het op de booth alsnog hapert terwijl de meting op
        een ontwikkelmachine zegt dat het niet kan. Zonder dit staan we
        opnieuw naar foto's van een scherm te kijken.
        """
        nu = self._klok.elapsed()
        if nu < self._volgend_verslag or not self._beeldjes:
            return
        self._volgend_verslag = nu + 60_000
        gem = self._teken_som / self._beeldjes
        L = self._layout
        print(f"[COLLAGE] tekentijd over {self._beeldjes} beeldjes: gemiddeld "
              f"{gem:.2f} ms, duurste {self._teken_ergste:.2f} ms "
              f"(schuiven {'aan' if self._schuiven else 'uit'}, achtergrond "
              f"{'aan' if self._parallax else 'uit'}, "
              f"{self._timer.interval()} ms per beeldje beschikbaar) | "
              f"zichtbaar {self._vlak.width()}x{self._vlak.height()}, raster "
              f"{L.raster_b if L else 0} = "
              f"{100.0 * (L.raster_b if L else 0) / max(1, self._vlak.width()):.0f}% "
              f"met {L.overhang if L else 0:.0f} overhang per kant, "
              f"rij {self._stroken[0].width() if self._stroken else 0} px",
              flush=True)
        # En wat de verschuiving tegen inbranden werkelijk doet. Die hoort
        # onzichtbaar te zijn; dat is af te lezen aan de stapgrootte. Springt
        # hij met meer dan een fysieke pixel tegelijk, dan valt hij op.
        dx, dy = self._verschuiving()
        print(f"[COLLAGE] verschuiving staat op {dx * self._dpr:+.0f}, "
              f"{dy * self._dpr:+.0f} fysieke pixels | {self._drift_stappen} "
              f"stappen sinds de start, grootste {self._drift_grootste_stap:.0f} "
              f"fysieke pixel(s), samen {self._drift_pad * self._dpr:.0f} pixels "
              f"afgelegd", flush=True)
        self._beeldjes = 0
        self._teken_som = 0.0
        self._teken_ergste = 0.0

    # ── de verschuiving tegen inbranden ────────────────────────────────────
    def verschuiving_bereik(self):
        """Hoe ver de opbouw maximaal uitslaat, in logische punten.

        photobooth.py heeft dit nodig om het slotje en het serienummer zo neer
        te zetten dat ze de hele slag kunnen maken zonder van het scherm te
        lopen.
        """
        L = self._layout
        if L is None:
            return 0, 0
        return int(round(DRIFT_X * L.s)), int(round(DRIFT_Y * L.s))

    def _verschuiving(self, t=None):
        """Waar de opbouw nu staat, in logische punten.

        Perioden van 11 en 17 minuten, die geen deler gemeen hebben, dus het
        pad herhaalt zich pas na ruim drie uur en staat nergens stil. De
        uitslag schaalt met het scherm — 56 punten is gemeten op de
        ontwerpmaat, niet op logische punten.

        De uitkomst wordt op HELE FYSIEKE PIXELS gezet. Twee redenen, en de
        tweede is de belangrijkste:

        * Zonder dat staat elke laag per beeldje op een andere onderpixel en
          moet Qt hem opnieuw bemonsteren. Dat maakt de instructie zachter en
          kost rekenwerk, terwijl er niets tegenover staat.
        * En zo is de beweging meetbaar in plaats van te beoordelen: de
          kleinste stap is per definitie één fysieke pixel — 0,095 mm op dit
          scherm — en hoe vaak zo'n stap valt, staat in het logboek. Bij een
          topsnelheid van 0,53 pixel per seconde is dat ongeveer twee keer per
          seconde één pixel, en dat is niet te zien.
        """
        L = self._layout
        if L is None:
            return 0.0, 0.0
        if t is None:
            t = self._klok.elapsed() / 1000.0
        dx = DRIFT_X * L.s * math.sin(2 * math.pi * t / DRIFT_PERIODE_X)
        dy = DRIFT_Y * L.s * math.sin(2 * math.pi * t / DRIFT_PERIODE_Y)
        stap = 1.0 / max(1e-6, self._dpr)
        return round(dx / stap) * stap, round(dy / stap) * stap

    def _tel_verschuiving(self, dx, dy):
        """Bijhouden hoe de verschuiving zich werkelijk gedraagt.

        Dit is de derde ronde op dit scherm, dus dit hoort niet meer van een
        foto van een beeldscherm af te hangen. Zie _verslag_tekentijd() voor
        de regel die hieruit volgt.
        """
        vorig = self._drift_vorige
        self._drift_vorige = (dx, dy)
        if vorig is None or (dx, dy) == vorig:
            return
        sprong = math.hypot(dx - vorig[0], dy - vorig[1])
        self._drift_stappen += 1
        self._drift_pad += sprong
        self._drift_grootste_stap = max(self._drift_grootste_stap,
                                        sprong * self._dpr)

    def _meld_verschuiving(self):
        """Melden zodra er een hele punt verschoven is, niet vaker.

        De verschuiving kruipt met ruim een halve punt per seconde. Widgets
        vijfentwintig keer per seconde verplaatsen die niet bewogen zijn is
        verspilling; zo komt er ongeveer één melding per twee seconden door.
        """
        dx, dy = self._verschuiving()
        nieuw = (int(round(dx)), int(round(dy)))
        if nieuw != self._gemeld:
            self._gemeld = nieuw
            self.verschoven.emit(nieuw[0], nieuw[1])

    def _achtergrond_verschuiving(self, t):
        """Waar het achtergrondveld nu staat — een uitsnede, geen hertekening.

        Staat de pan uit (en dat is de standaard), dan wordt het veld netjes
        gecentreerd neergezet en staat het stil.
        """
        L = self._layout
        if L is None or self._achtergrond.isNull():
            return 0.0, 0.0
        speling_x = max(0.0, self._achtergrond.width() / self._dpr - L.W)
        speling_y = max(0.0, self._achtergrond.height() / self._dpr - L.H)
        if not self._parallax:
            return -speling_x / 2.0, -speling_y / 2.0
        fx = 0.5 + 0.5 * math.sin(2 * math.pi * t / BG_PERIODE_X)
        fy = 0.5 + 0.5 * math.sin(2 * math.pi * t / BG_PERIODE_Y + 1.1)
        return -speling_x * fx, -speling_y * fy

    def zet_parallax(self, aan):
        """De schuivende achtergrond aan of uit. Standaard uit.

        Op de echte booth liep die pan niet vloeiend — "de ene keer doet hij
        het wel, de andere keer niet". Een beweging die hapert leest als een
        storing, een stilstaande achtergrond leest als niets, dus bij twijfel
        wint stilstand. Het is sfeer, geen bescherming: de achtergrond is een
        wazig verloop zonder scherpe rand en daar brandt niets van in. Wat wél
        beschermd moet worden — de instructie, het logo, het slotje, het
        serienummer — wordt beschermd door de trage verschuiving, en die staat
        hier los van en blijft altijd aan.
        """
        aan = bool(aan)
        if aan == self._parallax:
            return
        self._parallax = aan
        self._stel_tempo_in()
        print(f"[COLLAGE] schuivende achtergrond {'AAN' if aan else 'UIT'} — de "
              f"verschuiving tegen inbranden blijft hoe dan ook aan", flush=True)
        self.update()

    def beweegt(self):
        """Beweegt er iets dat per beeldje hertekend moet worden?

        Zo niet, dan hoeft er nog maar twee keer per seconde getekend te
        worden: de verschuiving tegen inbranden verplaatst hooguit één punt
        per twee seconden.
        """
        return self._schuiven or self._parallax

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
            # Wordt hier een rij vol, dan komt hij op — net als tijdens het
            # opbouwen. Anders staat er halverwege de avond ineens een hele rij
            # foto's op het scherm.
            nu_te_zien = self.rijen_zichtbaar()
            if nu_te_zien > self._rijen_getoond:
                self._laat_opkomen(range(self._rijen_getoond, nu_te_zien))
                self._rijen_getoond = nu_te_zien
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
        # De overvloeier duurt 1,2 seconde en moet in die tijd vloeiend
        # getekend worden. Met het schuiven uit staat de gewone tekenlus op twee
        # beeldjes per seconde; dan zou die overvloeier in twee sprongen gaan.
        self._zorg_vloeitimer()
        self.update()

    def rijen_zichtbaar(self):
        """Een rij verschijnt pas als hij vol is."""
        L = self._zorg_layout()
        if L is None:
            return 0
        return min(L.rijen, len(self._miniaturen) // L.kolommen)

    def onderbouw_hoek(self):
        """Waar het slotje met het serienummer hoort te staan.

        De rechterbenedenhoek van de onderbouw, als (rechts, onder) in logische
        punten binnen deze widget — dus dezelfde marge als de verhuurvraag links
        aanhoudt, en dezelfde onderlijn als de QR-code.

        Die twee liggen buiten deze widget (photobooth.py legt ze er als losse
        widgets overheen) maar horen wél bij de onderbouw: linksonder de
        verhuurvraag met de QR, in het midden het logo, rechtsonder het slotje.
        Zonder dit hing de rechterhoek aan de ONDERRAND van het scherm terwijl
        de andere twee met de verdeling meebewegen — en dan staat de band niet
        meer op één lijn zodra er een melding onderin komt.

        Nog geen indeling? Dan None: houd je eigen plek maar aan.
        """
        L = self._layout
        if L is None:
            return None
        return (self._vlak.x() + L.W - L.mv, self._vlak.y() + L.basislijn)

    # ── ruimte voor iets dat niet van deze widget is ───────────────────────
    def zet_onderruimte(self, punten):
        """Houd onderaan zoveel punten vrij, gemeten vanaf de onderrand.

        photobooth.py legt daar de wifi-tip neer als de booth geen internet
        heeft. Die balk lag over de onderste helft van het logo: je zag "MY
        BOOTH" en niet "BOX".

        Het is één getal in het rekenmodel en geen los duwtje aan het logo. Het
        verlaagt de bodem waar de onderbouw op rust, dus er valt minder te
        verdelen, dus krimpen de vier openingen alle vier evenveel — de volgorde
        en de verhoudingen blijven staan. Alles gaat samen omhoog: de foto's, de
        instructie, de verhuurvraag, het logo, en via basislijn() ook het slotje.

        Nul zet alles terug op de gewone plek. Er blijft dus geen gat staan waar
        de balk gestaan heeft; dat is een aparte eis, en het volgt hieruit omdat
        de indeling opnieuw uitgerekend wordt en niet bijgewerkt.
        """
        punten = max(0, int(punten))
        if punten == self._onderruimte:
            return
        self._onderruimte = punten
        if self._layout is not None:
            # Alleen de PLEKKEN veranderen, niet de maten: de tegels, de
            # instructie en de QR zijn even groot als daarnet. De miniaturen
            # hier ongeldig maken zou achttien foto's opnieuw laten schalen om
            # een balk van zeventig punten — dat is precies de halve seconde
            # stilstand die er net uit gehaald is.
            self._layout = Layout(self._vlak.width(), self._vlak.height(),
                                  onderruimte=self._onderruimte)
            self._logo = QPixmap()      # zijn plek wordt in _zorg_logo bepaald
            self.update()
        L = self._layout
        gevolg = (f" — de vier openingen worden {L.opening:.0f} punten"
                  if L is not None else "")
        print(f"[COLLAGE] onderruimte {punten} punten vrij voor de melding "
              f"onderin{gevolg}", flush=True)

    # ── opbouw ─────────────────────────────────────────────────────────────
    def resizeEvent(self, event):
        """De widget krijgt zijn echte maat pas als hij in de stapel hangt.

        Dat is ná zet_fotos(), dus hier moet alles wat aan de maat vastzit
        opnieuw. Zonder dit bleven de miniaturen op de maat staan die een leeg
        QWidget toevallig heeft (640 x 480) — de fout van beta.5.
        """
        super().resizeEvent(event)
        self._zorg_layout()

    def moveEvent(self, event):
        """Verschuift de widget, dan verschuift het zichtbare vlak mee."""
        super().moveEvent(event)
        self._zorg_layout()

    def zet_zichtbaar_vlak(self, vlak):
        """Leg het zichtbare vlak vast in plaats van het van het scherm af te
        leiden.

        Alleen voor gereedschap: de schermafdrukken en de toetsen tekenen op de
        maat van de tablet terwijl er geen tablet is — zonder beeldscherm meldt
        Qt een scherm van 800 x 600 en zou de indeling daarop uitkomen. Op de
        booth wordt dit nooit gezet en blijft het scherm de baas.
        """
        self._vast_vlak = QRect(vlak) if vlak is not None else None
        self._zorg_layout()

    def _zichtbaar_vlak(self):
        """Het vlak waarop de indeling gemaakt wordt: wat er wérkelijk te zien is.

        Niet zomaar self.width() en self.height(). Deze widget IS de
        idle-pagina en hangt in een QStackedWidget. QStackedLayout geeft in
        zijn gewone stand alleen de pagina die vooraan staat een maat, dus een
        pagina die daar nog niet gestaan heeft houdt de maat die hij toevallig
        had. En de stapel kan groter uitvallen dan het scherm. In beide
        gevallen wordt de indeling gemaakt op een maat die niet klopt, en dat
        is waarom het raster op de booth de halve breedte besloeg.

        Elders in photobooth.py staat deze omrekening al, voor het slotje
        (_position_idle_lock), en om precies dezelfde reden.

        Geeft een QRect terug in de coördinaten van deze widget.
        """
        eigen = QRect(0, 0, max(0, self.width()), max(0, self.height()))
        if self._vast_vlak is not None:
            snee = eigen.intersected(self._vast_vlak)
            return snee if not snee.isEmpty() else eigen
        try:
            from PyQt5.QtWidgets import QApplication
            scherm = self.screen() if hasattr(self, "screen") else None
            if scherm is None:
                scherm = QApplication.primaryScreen()
            if scherm is not None:
                sg = scherm.geometry()
                op_scherm = QRect(self.mapFromGlobal(sg.topLeft()), sg.size())
                snee = eigen.intersected(op_scherm)
                # Alleen vertrouwen als er werkelijk een scherm gevonden is.
                # Een doorsnede van niets betekent dat de widget nog nergens
                # staat; dan is zijn eigen maat de beste gok die er is.
                if snee.width() >= 320 and snee.height() >= 240:
                    return snee
        except Exception:
            pass
        return eigen

    def _zorg_layout(self):
        """De enige plek waar de maatvoering vandaan komt.

        Verandert het zichtbare vlak of de schermschaal, dan is ALLES wat
        eraan vastzit ongeldig: de achtergrond, het logo, de instructie, en
        ook de miniaturen en de rijen. Die laatste twee werden in beta.5
        vergeten.
        """
        vlak = self._zichtbaar_vlak()
        if vlak.width() <= 0 or vlak.height() <= 0:
            return None
        dpr = float(self.devicePixelRatioF() or 1.0)
        if (self._layout is None or vlak != self._vlak
                or abs(dpr - self._dpr) > 1e-6):
            self._dpr = dpr
            self._vlak = vlak
            self._layout = Layout(vlak.width(), vlak.height(),
                                  onderruimte=self._onderruimte)
            self._achtergrond = QPixmap()
            self._logo = QPixmap()
            self._tekst = QPixmap()
            self._qr = QPixmap()
            self._qr_tekst = QPixmap()
            self._beeld_verouderd = True
            self._meld_maatvoering()
        if self._beeld_verouderd:
            if self._in_paint:
                # Vijftien miniaturen maken kost een paar honderd milliseconde.
                # Dat middenin een beeldje doen geeft precies het haperen waar
                # de opdrachtgever over viel. Dus: even later, buiten het
                # tekenen om.
                QTimer.singleShot(0, self._herbouw_nu)
            else:
                self._beeld_verouderd = False
                self._herbouw_beeld()
        return self._layout

    def _herbouw_nu(self):
        if self._beeld_verouderd and self._layout is not None:
            self._beeld_verouderd = False
            self._herbouw_beeld()
            self.update()

    def _meld_maatvoering(self):
        """Eén regel in het logboek: wat er werkelijk gekozen is.

        Anders is de enige manier om erachter te komen dat er iets mis is met
        de maatvoering, een foto van het scherm. Dat is nu twee keer de dure
        weg gebleken.

        De verhouding achteraan is de belangrijkste: dat is hoeveel van de
        breedte het raster vult. Hoort ongeveer 87% te zijn. Staat daar de
        helft, dan denkt de widget dat hij half zo hoog is als hij is — de
        rasterbreedte volgt namelijk uit de HOOGTE, niet uit de breedte.

        En sinds de verticale indeling een verdeling is: de derde regel is de
        som zelf, in percentages van de schermhoogte. Wie op de booth vindt dat
        het scheef staat, kan hem daar narekenen in plaats van er een foto van
        te maken.
        """
        L = self._layout
        f = lambda v: int(round(v * self._dpr))
        eigen = f"{self.width()}x{self.height()}"
        vlak = f"{self._vlak.width()}x{self._vlak.height()}"
        plek = f"+{self._vlak.x()}+{self._vlak.y()}"
        print(f"[COLLAGE] widget {eigen} | zichtbaar {vlak}{plek} logisch @ "
              f"{self._dpr:g}x = {f(L.W)}x{f(L.H)} fysiek"
              f"{'  (LET OP: widget wijkt af van zichtbaar vlak)' if eigen != vlak else ''}",
              flush=True)
        genoeg = "ja" if L.overhang >= L.overhang_min else "TE WEINIG"
        print(f"[COLLAGE] raster {L.kolommen}x{L.rijen} ({L.n} tegels) | tegel "
              f"{L.tw}x{L.th} logisch = {f(L.tw)}x{f(L.th)} fysiek | "
              f"rasterbreedte {L.raster_b} van {L.W} = "
              f"{100.0 * L.raster_b / max(1, L.W):.0f}% (hoort >100%: het "
              f"raster loopt tot voorbij de rand) | overhang {L.overhang:.0f} "
              f"punten per kant, nodig {L.overhang_min} voor de verschuiving "
              f"— {genoeg}", flush=True)

        # De verticale verdeling, in procenten van de schermhoogte.
        pct = lambda v: 100.0 * v / max(1, L.H)
        blokken = " + ".join(f"{n} {pct(h):.1f}%" for n, h in L.blokken)
        gewijkt = ("" if L.rijen == L.rijen_max else
                   f"  (LET OP: {L.rijen_max - L.rijen} rij(en) foto's "
                   f"weggelaten, anders paste het niet)")
        print(f"[COLLAGE] verdeling: {blokken} = {pct(L.inhoud_h):.1f}% inhoud "
              f"| {L.openingen} openingen van {L.opening:.0f} punten = "
              f"{pct(L.opening):.1f}% elk ({pct(L.lucht):.1f}% samen) | bodem "
              f"op {L.bodem} van {L.H} ({L.H - L.bodem} punten voor de "
              f"ondermarge en een melding){gewijkt}", flush=True)

    def _herbouw_beeld(self):
        """Miniaturen en rijen opnieuw maken op de maat die nu geldt.

        NIET IN ÉÉN KEER. Achttien foto's van zes megapixel op tegelmaat
        brengen kost een halve seconde, en als dat gebeurt op het moment dat
        het scherm getoond wordt, blijft het scherm die halve seconde weg.
        Precies dat viel op: "als je naar dat scherm gaat wordt het wel echt
        wat traag".

        Dus: de achtergrond, de instructie, het logo en de QR staan er meteen,
        en de tegels komen er in stukjes bij. Een scherm dat direct verschijnt
        en zich in een halve seconde vult, voelt sneller dan een scherm dat een
        halve seconde wegblijft — en het is ook echt eerder bruikbaar, want
        aanraken kan al.

        Waarom dat gespreide opbouwen daarna toch zichtbaar was
        ------------------------------------------------------
        "Bij het opbouwen glitcht het een beetje." Twee oorzaken, en de eerste
        was de grote:

        1. **De instructie sprong twee keer omlaag.** Hij centreerde zich in de
           ruimte die de collage overliet, en die ruimte krimpt bij elke rij die
           erbij komt. Op de Surface is dat 142 en daarna 156 fysieke pixels
           binnen twee tienden van een seconde — de grootste tekst op het scherm
           die tweemaal verspringt. Dát was het meeste van wat er glitcht, en
           niet de tegels zelf. Weg sinds de indeling een VERDELING is (zie
           Layout): elk blok heeft zijn eigen plek, of er nu nul, één of twee
           rijen klaar zijn. Er valt niets meer te verspringen.
        2. **De rijen ploften erin.** Een rij verschijnt pas als hij vol is, dus
           er stond eerst niets en dan ineens een halve wand foto's. Die komt nu
           in een kwart seconde op: zie INVLOEI_MS. Dat kost geen tekenwerk —
           het is één getal op de verf — dus de snelheid blijft.

        De winst blijft: er wordt niets vooraf uitgerekend en niets vooruit
        gewacht. Het scherm staat er nog steeds bij het eerste beeldje.
        """
        L = self._layout
        self._oude_stroken.clear()
        self._invloei.clear()
        self._rijen_getoond = 0
        # Alles wat er is: de foto's die al een miniatuur hebben ÉN wat er nog
        # in de wachtrij stond. Dat tweede is makkelijk te vergeten — er komt
        # tijdens het opbouwen een tweede herbouw langs, want de widget krijgt
        # zijn echte maat pas ná het vullen, en dan verdwijnt de halve lijst.
        alle = (self._paden + self._wachtrij)[-L.n:]
        self._miniaturen = []
        self._volgende = 0
        self._stroken = []
        self._wachtrij = list(alle)
        self._paden = []
        self._bouw_begonnen = self._klok.elapsed()
        if self._wachtrij:
            self._bouwtimer.start()

    def _bouw_stukje(self):
        """Een paar miniaturen per keer, met een klok erop.

        Acht milliseconde per beurt: dat is korter dan een beeldje van
        vijfentwintig per seconde duurt, dus het tekenen blijft doorlopen
        terwijl de collage zich vult.
        """
        L = self._layout
        if L is None or not self._wachtrij:
            self._klaar_met_bouwen()
            return
        klok = QElapsedTimer()
        klok.start()
        gewijzigd = set()
        while self._wachtrij and klok.elapsed() < 8:
            pad = self._wachtrij.pop(0)
            mini = self._maak_miniatuur(pad)
            if mini is None:
                continue
            gewijzigd.add(len(self._miniaturen) // L.kolommen)
            self._paden.append(pad)
            self._miniaturen.append(mini)
        while len(self._stroken) < L.rijen:
            self._stroken.append(None)
        for r in gewijzigd:
            if 0 <= r < L.rijen:
                self._stroken[r] = self._maak_strook(r)
        # Wat er door dit stukje bij is gekomen, komt op in plaats van erin te
        # ploffen. Een rij verschijnt pas als hij vol is, dus dit gebeurt hooguit
        # twee keer (liggend) of drie keer (staand) per opbouw.
        nu_te_zien = self.rijen_zichtbaar()
        if nu_te_zien > self._rijen_getoond:
            self._laat_opkomen(range(self._rijen_getoond, nu_te_zien))
            self._rijen_getoond = nu_te_zien
        self.update()
        if not self._wachtrij:
            self._klaar_met_bouwen()

    def _klaar_met_bouwen(self):
        self._bouwtimer.stop()
        if self._bouw_begonnen is None:
            return
        duur = self._klok.elapsed() - self._bouw_begonnen
        self._bouw_begonnen = None
        print(f"[COLLAGE] {len(self._miniaturen)} tegels klaar in {duur} ms "
              f"(in stukjes, het scherm bleef bruikbaar)", flush=True)

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
        sleutel = (pad, L.tw, L.th, self._dpr)
        klaar = _MINI_CACHE.get(sleutel)
        if klaar is not None:
            return klaar
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
        _cache_zet(sleutel, tegel)
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
        # Overmaats klaarzetten, zodat het schuiven een uitsnede is en geen
        # hertekening. Het veld is een wazig verloop; bilineair een stukje
        # opschalen is daar met het blote oog niet van te onderscheiden.
        bw, bh = L.W * BG_OVERMAAT, L.H * BG_OVERMAAT
        geschaald = self._achtergrond_bron.scaled(
            int(round(bw * self._dpr)), int(round(bh * self._dpr)),
            Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        geschaald.setDevicePixelRatio(self._dpr)
        doek = self._doek(bw, bh, QColor(merk.INKT))
        p = QPainter(doek)
        p.drawPixmap(int(round((bw - geschaald.width() / self._dpr) / 2.0)),
                     int(round((bh - geschaald.height() / self._dpr) / 2.0)),
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

    # ── de verhuurvraag met de QR ──────────────────────────────────────────
    def _zorg_qr(self):
        """De QR naar de site, één keer gemaakt.

        Twee dingen die anders misgaan:

        * HARDE RANDEN. De code wordt op de exacte pixelmaat gemaakt en met
          FastTransformation opgeschaald, niet met SmoothTransformation. Een
          vervaagde QR is een slechter leesbare QR — de camera moet zwart van
          wit kunnen scheiden.
        * DE STILLE ZONE. Rondom de code hoort wit te blijven, anders leest
          een telefoon hem niet. Vandaar het witte vlak eromheen; dat is geen
          versiering.

        Lukt het maken niet, dan blijft de QR gewoon weg. Een startscherm mag
        nooit omvallen over een reclameregeltje.
        """
        L = self._layout
        if not self._qr.isNull():
            return
        url = (getattr(config, "BOOTH_QR_URL", "") or "").strip()
        if not url:
            return
        try:
            from qr_generator import generate_qr_pixmap
            binnen = int(round((L.qr_maat - 2 * L.qr_rand) * self._dpr))
            code = generate_qr_pixmap(url, size=binnen, smooth=False)
            if code.isNull():
                return
        except Exception as e:
            print(f"[COLLAGE] QR niet gemaakt: {e}", flush=True)
            return

        doek = self._doek(L.qr_maat, L.qr_maat)
        p = QPainter(doek)
        p.setRenderHint(QPainter.Antialiasing, True)
        vorm = QPainterPath()
        vorm.addRoundedRect(0, 0, L.qr_maat, L.qr_maat, L.ronding, L.ronding)
        p.fillPath(vorm, QColor(merk.WIT))
        code.setDevicePixelRatio(self._dpr)
        p.drawPixmap(L.qr_rand, L.qr_rand, code)
        p.end()
        self._qr = doek

    def _zorg_qr_tekst(self):
        """Het regeltje boven de QR, met de kronkelende pijl eronder.

        Tekst en pijl zitten in één pixmap: ze horen bij elkaar, verschuiven
        samen, en zo is het één blit in plaats van drie.

        Over de pijl, want die was op de booth "een beetje een zootje"
        ------------------------------------------------------------
        Hij werd getekend als één gebogen lijn PLUS twee losse streepjes voor de
        punt, alle drie met dezelfde ronde-kop-pen. Daar zitten twee fouten in
        die je pas op glas ziet:

        * **Drie ronde koppen op één punt.** Het einde van de kromme en het begin
          van de twee streepjes vielen samen. Ronde koppen steken een halve
          lijndikte voorbij hun eindpunt uit, dus daar lag een klodder van drie
          over elkaar heen gestapelde halve rondjes. Dat is de vlek op de foto.
        * **Een punt die geen punt is.** Twee streepjes van 30 ontwerppixels bij
          een lijn van 7 dik lezen als een vinkje dat toevallig aan de lijn
          vastzit, niet als een pijlpunt. En omdat ze even dik zijn als de lijn
          staat er niets in verhouding.

        Nu is het één kromme plus één GEVULDE driehoek. De kromme houdt op waar
        de basis van de driehoek ligt, dus de ronde kop van de kromme valt
        helemaal binnen de driehoek — één ononderbroken vorm, overal even dik,
        met een punt die vier lijndiktes lang is. De richting waarin hij aankomt
        wordt niet meer uit de kromme gepeild (dat was een schatting over 7% van
        de lengte) maar staat vast: PIJL_HOEK_EIND, overwegend omlaag, iets naar
        links. Daar wordt de kromme naartoe gerekend in plaats van andersom.

        Antialiasing stond al aan; het probleem zat in de vorm, niet in de
        instelling.
        """
        L = self._layout
        if not self._qr_tekst.isNull():
            return
        try:
            from translations import t
            regel = t("idle_huur_vraag")
            if not regel or regel == "idle_huur_vraag":
                raise KeyError
        except Exception:
            regel = "Ook een photobooth huren?"

        hoogte = L.qr_tekst_h + L.qr_pijl_h
        doek = self._doek(L.qr_tekst_w, hoogte)
        p = QPainter(doek)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        p.setRenderHint(QPainter.Antialiasing, True)

        korps = max(10, int(L.qr_tekst_h / 2.0 / 1.2))
        letter = merk.letter(korps, vet=True)
        letter.setFamily(KOP_ZWAAR)
        p.setFont(letter)
        fm = QFontMetrics(letter)
        breed = lambda s: (fm.horizontalAdvance(s) if hasattr(fm, "horizontalAdvance")
                           else fm.width(s))

        # over hooguit twee regels afbreken op een spatie
        regels, huidig = [], ""
        for woord in regel.split():
            proef = (huidig + " " + woord).strip()
            if huidig and breed(proef) > L.qr_tekst_w:
                regels.append(huidig)
                huidig = woord
            else:
                huidig = proef
        if huidig:
            regels.append(huidig)
        regels = regels[:2]

        p.setPen(QColor(merk.OP_DONKER))
        regelhoogte = int(korps * 1.2)
        for i, r in enumerate(regels):
            p.drawText(0, i * regelhoogte + fm.ascent(), r)
        # Waar de laatste regel ophoudt: daar begint de pijl. Niet eronder, maar
        # ernaast — zo loopt hij de zin uit in plaats van er los onder te hangen.
        staart_x = breed(regels[-1]) if regels else 0
        staart_y = (len(regels) - 1) * regelhoogte + fm.ascent() - korps * 0.28

        self._teken_pijl(p, L, hoogte, staart_x, staart_y, korps)
        p.end()
        self._qr_tekst = doek

    def _teken_pijl(self, p, L, hoogte, staart_x, staart_y, korps):
        """De kronkelende pijl van het regeltje naar de code.

        Eén vloeiende kromme met een gevulde punt eraan. De maatvoering staat
        bovenaan dit bestand; hier staat alleen hoe de vier punten van de kromme
        eruit volgen.
        """
        dik = max(2.5, PIJL_DIK * L.s)

        # WAAR HIJ AANKOMT. Op de bovenrand van de code, iets rechts van het
        # midden — daar zit ook de tekst, dus de pijl kruist zichzelf niet. De
        # punt komt onder een vaste hoek binnen: overwegend omlaag, iets naar
        # links, zodat hij de code aanwijst in plaats van er langs te scheren.
        hoek = math.radians(PIJL_HOEK_EIND)
        rx, ry = math.cos(hoek), math.sin(hoek)
        punt_lang = PIJL_PUNT_LANG * dik
        punt_half = PIJL_PUNT_BREED * dik
        tip_x = L.qr_maat * PIJL_MIK
        tip_y = hoogte - dik * 0.5
        # De basis van de driehoek. Daar houdt de kromme op: de ronde kop van de
        # kromme steekt een halve lijndikte vooruit en valt dus binnen de
        # driehoek, die daar al 1,4 lijndikte breed is. Geen klodder meer.
        bas_x = tip_x - rx * punt_lang
        bas_y = tip_y - ry * punt_lang

        # WAAR HIJ VERTREKT. Vlak achter het laatste woord, op de hoogte van dat
        # woord. Dat is het "logische aanlooppunt": de pijl loopt de zin uit.
        # Stond hij eronder, dan hing hij tussen tekst en code in en las het als
        # drie losse dingen. Blijft kloppen als de vertaling korter of langer is,
        # want het volgt de werkelijk gezette regel.
        x0 = min(L.qr_tekst_w * 0.62, staart_x + korps * 0.45)
        y0 = staart_y

        # DE BOCHT. Eerst naar rechts uitzwaaien, dan terug naar links op de code
        # af. Dat uitzwaaien is waarom het een pijl is en geen streep: hij komt
        # ergens vandaan en wijst ergens naartoe.
        c1_x = x0 + (L.qr_tekst_w - x0) * PIJL_ZWAAI
        c1_y = y0 + (bas_y - y0) * PIJL_DAAL
        # Het tweede stuurpunt ligt op de aankomstrichting, achter de basis van
        # de punt. Zo komt de kromme daar gegarandeerd onder de juiste hoek aan
        # en hoeft er niets gepeild te worden.
        grip = PIJL_GRIP * math.hypot(bas_x - x0, bas_y - y0)
        c2_x = bas_x - rx * grip
        c2_y = bas_y - ry * grip

        boog = QPainterPath()
        boog.moveTo(x0, y0)
        boog.cubicTo(c1_x, c1_y, c2_x, c2_y, bas_x, bas_y)
        p.setPen(QPen(QColor(merk.GROEN), dik, Qt.SolidLine, Qt.RoundCap,
                      Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(boog)

        # De punt: één gevulde driehoek, geen twee streepjes.
        punt = QPainterPath()
        punt.moveTo(tip_x, tip_y)
        punt.lineTo(bas_x - ry * punt_half, bas_y + rx * punt_half)
        punt.lineTo(bas_x + ry * punt_half, bas_y - rx * punt_half)
        punt.closeSubpath()
        p.setPen(Qt.NoPen)
        p.fillPath(punt, QColor(merk.GROEN))

    # ── tekenen ────────────────────────────────────────────────────────────
    def paintEvent(self, event):
        self._in_paint = True
        try:
            L = self._zorg_layout()
        finally:
            self._in_paint = False
        if L is None:
            return
        self._zorg_achtergrond()
        self._zorg_logo()
        self._zorg_tekst()
        self._zorg_qr()
        self._zorg_qr_tekst()

        self._teken_klok.start()
        t = self._klok.elapsed() / 1000.0
        p = QPainter(self)

        # Alles wordt getekend binnen het ZICHTBARE vlak. Is de widget groter
        # dan het scherm — en dat kan, zie _zichtbaar_vlak — dan ligt dat vlak
        # niet op (0,0) en moet er dus verschoven worden.
        if self._vlak.topLeft() != QPoint(0, 0):
            p.translate(self._vlak.x(), self._vlak.y())

        # 1. achtergrond — één blit van een uitsnede uit het overmaatse veld.
        #    Er wordt hier niets geschaald: dat is bij het klaarzetten al
        #    gebeurd.
        ox, oy = self._achtergrond_verschuiving(t)
        p.drawPixmap(int(round(ox)), int(round(oy)), self._achtergrond)

        # 2. de verschuiving tegen inbranden: de hele opbouw kruipt mee, als
        #    één laag, zodat de onderlinge verhoudingen kloppen blijven. Het
        #    slotje en het serienummer liggen buiten deze widget en krijgen
        #    dezelfde verschuiving via het signaal `verschoven`.
        dx, dy = self._verschuiving(t)
        self._tel_verschuiving(dx, dy)
        p.translate(dx, dy)

        # 3. de schuivende rijen
        zichtbaar = self.rijen_zichtbaar()
        if zichtbaar and self._stroken:
            periode = L.raster_b + L.gap
            for r in range(zichtbaar):
                if r >= len(self._stroken) or self._stroken[r] is None:
                    continue
                y = L.rij_y(r)
                # ALLE RIJEN DEZELFDE KANT OP.
                #
                # Hier ging het om en om: rij 0 en 2 naar links, rij 1 naar
                # rechts. Bedoeld als "leest rustiger", maar op de booth
                # leverde het precies de klacht op — "die foto's bewegen heen
                # en weer". Wie naar het scherm kijkt ziet niet drie rijen die
                # ieder één kant op gaan, hij ziet foto's die tegen elkaar in
                # bewegen. Eén richting dus.
                #
                # Staat het schuiven uit — en dat is de standaard — dan staat
                # elke rij netjes op zijn plek. De foto's zijn dan gewoon te
                # zien, alleen niet in beweging.
                if self._schuiven:
                    verschuiving = (-SCHUIF_PX_S * t) % periode
                else:
                    verschuiving = 0
                p.setClipRect(QRect(L.raster_x, int(y), L.raster_b, L.th))
                x0 = L.raster_x + int(verschuiving)
                # Komt deze rij nog op, dan komt hij op in plaats van erin te
                # ploffen. Zie INVLOEI_MS.
                dekking = self._dekking(r)
                if dekking < 1.0:
                    p.setOpacity(dekking)
                p.drawPixmap(x0, int(y), self._stroken[r])
                p.drawPixmap(x0 - periode, int(y), self._stroken[r])
                if dekking < 1.0:
                    p.setOpacity(1.0)

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

        # 4. de instructie — een gecachte pixmap op een VASTE plek.
        #    Hij centreerde zich vroeger in de ruimte die de collage overliet,
        #    en die ruimte krimpt bij elke rij die er tijdens het opbouwen
        #    bijkomt: twee sprongen van ruim 140 fysieke pixels binnen twee
        #    tienden van een seconde, met de grootste tekst van het scherm. Dát
        #    was het grootste deel van "bij het opbouwen glitcht het een beetje".
        #    Sinds de indeling een verdeling is (zie Layout) staat hij stil,
        #    ongeacht hoeveel rijen er al klaar zijn.
        if not self._tekst.isNull():
            p.drawPixmap(L.txt_x, L.txt_y, self._tekst)

        # 5. het logo — in de onderbouw, op de QR-code gecentreerd
        if not self._logo.isNull():
            p.drawPixmap(self._logo_x, self._logo_y, self._logo)

        # 6. de verhuurvraag met de QR, linksonder. Staat BINNEN de
        #    verschuiving, dus hij kruipt mee — een QR is een scherp,
        #    contrastrijk vlak en dat is precies wat inbrandt.
        if not self._qr_tekst.isNull():
            p.drawPixmap(L.qr_tekst_x, L.qr_tekst_y, self._qr_tekst)
        if not self._qr.isNull():
            p.drawPixmap(L.qr_x, L.qr_y, self._qr)
        p.end()

        # Hoe lang duurde het voordat er iets stond? Dat is wat een gast als
        # traag ervaart, niet de tijd per beeldje.
        if self._getoond_op is not None and not self._eerste_beeldje_gemeld:
            self._eerste_beeldje_gemeld = True
            wacht = self._klok.elapsed() - self._getoond_op
            print(f"[COLLAGE] eerste beeldje {wacht} ms na het tonen van het "
                  f"scherm ({len(self._miniaturen)} van "
                  f"{len(self._miniaturen) + len(self._wachtrij)} tegels al "
                  f"klaar)", flush=True)

        # Wat kostte dit beeldje. Zie _verslag_tekentijd().
        kosten = self._teken_klok.nsecsElapsed() / 1e6
        self._beeldjes += 1
        self._teken_som += kosten
        self._teken_ergste = max(self._teken_ergste, kosten)


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
