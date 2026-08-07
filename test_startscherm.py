"""Toetst het startscherm op de dingen die op de echte booth zijn misgegaan.

Waarom dit bestaat
------------------
Beta.5 kwam door de bouwstraat met schermafdrukken die er goed uitzagen, en was
op de booth onbruikbaar. Drie fouten, alle drie onzichtbaar in de proef:

  1. De achtergrond die de applicatie eronder legt (mbb-ready{breedte}.jpg)
     bevat de instructie en het logo AL. De widget tekende ze er nog een keer
     overheen: alles dubbel. In de proef werd er geen achtergrond meegegeven,
     dus daar viel niets te zien.
  2. De widget wordt aangemaakt en gevuld vóórdat hij zijn maat heeft. De
     miniaturen werden dus op 640 x 480 gemaakt — de standaardmaat van een leeg
     QWidget — en na het toekennen van de echte maat niet vernieuwd. In de
     proef kreeg de widget zijn maat vooraf, dus daar klopte het.
  3. De schermschaal (150% of 200% op een Windows-tablet) werd genegeerd, dus
     tekende Qt alles op de logische maat en rekte het daarna op: wazig. In de
     proef stond de schaal op 1.

Daar is dit op gebouwd. Alles hieronder toetst het gedrag zoals de echte
applicatie het aanroept, met een echte mappenstructuur en de achtergrond die er
werkelijk onder gaat — en het valt om als het niet klopt. Een plaatje waar
niemand naar kijkt bewijst niets; dit is de reden dat er nu gemeten wordt.

Draait zonder beeldscherm, dus ook op een bouwserver:

    python test_startscherm.py
"""

import os
import re
import shutil
import sys
import tempfile

APP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt, QRect                             # noqa: E402
from PyQt5.QtGui import QPixmap, QPainter, QColor              # noqa: E402
from PyQt5.QtWidgets import (QApplication, QStackedWidget,       # noqa: E402
                             QWidget)

import startscherm                                             # noqa: E402

# De Surface Pro 7: 2736 x 1824 fysiek. Op 200% schaling rekent Qt in 1368 x
# 912 logische punten; op 150% in 1824 x 1216. De maatvoering hoort in beide
# gevallen op dezelfde FYSIEKE tegel uit te komen.
FYSIEK_B, FYSIEK_H = 2736, 1824

fouten = []


def eis(voorwaarde, boodschap):
    if voorwaarde:
        print(f"  ok    {boodschap}", flush=True)
    else:
        print(f"  FOUT  {boodschap}", flush=True)
        fouten.append(boodschap)


def onderdeel(naam, doen, *args):
    """Eén onderdeel draaien; klapt het om, dan is dat gewoon een fout.

    Zonder dit stopt de hele toets bij de eerste ontbrekende eigenschap en zie
    je de rest niet meer — terwijl je juist wil weten wat er allemaal mis is.
    """
    try:
        return doen(*args)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"  FOUT  {naam} klapte om: {type(exc).__name__}: {exc}", flush=True)
        fouten.append(f"{naam} klapte om: {type(exc).__name__}: {exc}")
        return None


# ── een echte mappenstructuur, geen losse plaatshouders ────────────────────
def bouw_event(basis, sessies=18, opnamen=3):
    """photos/<event>/{raw,strips}/ zoals photobooth.py hem werkelijk schrijft.

    De stroken in strips/ staan er als lokaas. Leest de collage ooit uit de
    verkeerde map, dan komen ze op het scherm en valt deze toets om — precies
    de klacht die van de booth kwam. In de vorige proef bestond strips/ niet,
    dus viel er niets te betrappen.
    """
    raw = os.path.join(basis, "raw")
    strips = os.path.join(basis, "strips")
    os.makedirs(raw, exist_ok=True)
    os.makedirs(strips, exist_ok=True)

    for s in range(sessies):
        stempel = f"07-08-2026_{20 + s // 60:02d}.{s % 60:02d}.{(s * 7) % 60:02d}"
        for n in range(1, opnamen + 1):
            # Losse opname: liggend 3:2, net als een spiegelreflex of webcam.
            # De kleur verraadt welke opname het is, zodat te toetsen is dat
            # de tweede gekozen wordt.
            pm = QPixmap(1800, 1200)
            pm.fill(QColor.fromHsv((s * 37) % 360, 200, 60 + n * 60))
            pm.save(os.path.join(raw, f"{stempel}_{n}.jpg"), "JPG")
        # De samengestelde strook: staand 1200 x 1800, twee kolommen van drie.
        strook = QPixmap(1200, 1800)
        strook.fill(QColor(255, 0, 255))
        strook.save(os.path.join(strips, f"{stempel}.jpg"), "JPG")
        strook.save(os.path.join(strips, f"{stempel}_enkel.jpg"), "JPG")
    return raw, strips


# ── 1. de foto's komen uit raw/, één per sessie ────────────────────────────
def toets_fotokeuze(raw, strips, sessies):
    print("\nDe foto's", flush=True)
    paden = startscherm.fotos_van_event(raw)

    eis(len(paden) == sessies,
        f"één foto per sessie ({len(paden)} van {sessies})")
    eis(all(os.path.dirname(p) == raw for p in paden),
        "alles komt uit raw/")
    eis(not any(os.path.commonpath([strips, p]) == strips
                for p in paden if os.path.isabs(p)),
        "geen enkele foto komt uit strips/")
    eis(all(re.search(r"_(\d+)\.jpe?g$", os.path.basename(p)) for p in paden),
        "alleen losse opnamen (naam eindigt op _cijfer)")
    eis(all(os.path.basename(p).endswith("_2.jpg") for p in paden),
        "de tweede opname van elke sessie")

    oplopend = [os.path.basename(p) for p in paden]
    eis(oplopend == sorted(oplopend), "op volgorde van tijd")

    # En de omgekeerde proef: wijst iemand de collage per ongeluk naar strips/,
    # dan hoort daar niets uit te komen — de stroken hebben geen _cijfer.
    eis(startscherm.fotos_van_event(strips) == [],
        "strips/ levert niets op, ook niet als er per ongeluk naar gewezen wordt")


# ── 2. de opbouw, precies zoals photobooth.py hem aanroept ─────────────────
def toets_vult_het_scherm(page, dpr):
    """Het raster hoort de breedte te vullen, en de tegels de ontwerpmaat.

    Op de booth besloeg het raster maar de halve breedte met een groot leeg
    vlak ernaast. Let op de rekenregel: de maatvoering schaalt met de KORTE
    zijde, dus de rasterbreedte volgt uit de HOOGTE — ongeveer 1,31 x de
    hoogte. Staat het raster op de halve breedte, dan denkt de widget dat hij
    half zo hoog is, niet half zo breed. Vandaar dat hier op allebei gemeten
    wordt.
    """
    print("\nVult de collage het scherm", flush=True)
    L = page._layout
    deel = L.raster_b / float(L.W)
    print(f"        raster {L.raster_b} van {L.W} punten breed", flush=True)
    eis(0.80 <= deel <= 0.95,
        f"het raster vult {deel*100:.0f}% van de breedte (ontwerp: 87%)")
    eis(abs(L.raster_b - 1.307 * L.H) <= 0.02 * L.raster_b,
        f"de rasterbreedte klopt met de hoogte ({L.raster_b} tegen "
        f"{1.307*L.H:.0f} verwacht)")
    eis(L.raster_x > 0 and L.raster_x + L.raster_b <= L.W,
        f"het raster past binnen het scherm (marge {L.raster_x})")

    # Een samengestelde rij moet net zo breed zijn als het raster. Zou de rij
    # op de logische maat gemaakt worden en de tegels op de fysieke, dan liep
    # dat hier uit elkaar.
    strook = page._stroken[0]
    eis(abs(strook.width() / dpr - (L.raster_b + L.gap)) <= 2,
        f"een samengestelde rij is {strook.width()} px = "
        f"{strook.width()/dpr:.0f} punten, raster + tussenruimte is "
        f"{L.raster_b + L.gap}")
    eis(abs(strook.width() / dpr / L.kolommen - (L.tw + L.gap)) <= 2,
        "en er passen precies vijf tegels in")

    # En de instructie hoort ONDER de collage te staan, over de volle breedte
    # gecentreerd — niet ernaast.
    onder = L.collage_onderkant(3)
    eis(L.tekst_y(3) >= onder,
        f"de instructie staat onder de collage (y {L.tekst_y(3):.0f} tegen "
        f"onderkant {onder})")
    eis(abs((L.txt_x + L.txt_w / 2) - L.W / 2) <= 2,
        "de instructie staat horizontaal in het midden")
    eis(abs((L.raster_x + L.raster_b / 2) - L.W / 2) <= 2,
        "en de collage ook — ze staan onder elkaar, niet naast elkaar")


def toets_maat_van_de_stapel(achtergrond, paden, dpr):
    """De widget hangt in een QStackedWidget en kan een maat hebben die nergens
    op slaat.

    QStackedLayout geeft in zijn gewone stand alleen de pagina die vooraan
    staat een maat. Een pagina die daar nog nooit gestaan heeft houdt dus de
    maat die hij toevallig had, en de stapel kan bovendien groter uitvallen dan
    het scherm. In beide gevallen werd de indeling op de verkeerde maat
    gemaakt — dat is wat er op de booth misging.

    Hier wordt precies dat nagebootst: de pagina wordt in een stapel gehangen
    ZONDER ooit vooraan te staan, en die stapel is groter dan het scherm.
    """
    print("\nEen stapel die groter is dan het scherm", flush=True)
    from PyQt5.QtWidgets import QApplication
    sg = QApplication.primaryScreen().geometry()
    print(f"        scherm is {sg.width()}x{sg.height()} punten", flush=True)

    page = startscherm.Collage(achtergrond)
    page.zet_fotos(paden)
    stapel = QStackedWidget()
    vulling = QWidget()
    stapel.addWidget(vulling)          # deze staat vooraan
    stapel.addWidget(page)             # en deze dus niet
    stapel.setFixedSize(sg.width() * 2, int(sg.height() * 0.6))
    stapel.move(0, 0)
    stapel.show()
    QApplication.processEvents()
    page.show()
    QApplication.processEvents()

    L = page._layout
    eis(L is not None, "er is een indeling")
    if L is None:
        return
    print(f"        widget denkt {page.width()}x{page.height()}, "
          f"indeling op {L.W}x{L.H}", flush=True)
    eis(L.W <= sg.width() + 2 and L.H <= sg.height() + 2,
        f"de indeling blijft binnen het scherm ({L.W}x{L.H} tegen "
        f"{sg.width()}x{sg.height()})")
    # Hoeveel procent van de breedte het raster vult hangt van de
    # beeldverhouding af — op 3:2 is dat 87%, op 4:3 bijna alles. Wat áltijd
    # moet gelden: het raster volgt de hoogte, en het past binnen de breedte.
    eis(abs(L.raster_b - 1.307 * L.H) <= 0.02 * L.raster_b,
        f"het raster volgt de hoogte ({L.raster_b} tegen {1.307*L.H:.0f})")
    eis(L.raster_x >= 0 and L.raster_x + L.raster_b <= L.W,
        f"en past binnen de breedte ({L.raster_b} van {L.W}, marge "
        f"{L.raster_x})")
    eis(page._vlak.width() > 0 and page._vlak.height() > 0,
        f"het zichtbare vlak is bepaald ({page._vlak.width()}x"
        f"{page._vlak.height()} op +{page._vlak.x()}+{page._vlak.y()})")
    stapel.hide()


def bouw_zoals_de_applicatie(achtergrond, paden, logisch_b, logisch_h):
    """De widget wordt aangemaakt en gevuld vóórdat hij zijn maat heeft.

    Dat is geen kunstgreep maar wat _build_idle_page() letterlijk doet: de
    Collage wordt gemaakt, meteen gevuld met zet_fotos(), en pas daarna in de
    stapel gehangen — en dán krijgt hij zijn maat. Wie hem hier eerst op maat
    zet, toetst een situatie die op de booth niet bestaat.
    """
    page = startscherm.Collage(achtergrond)
    startmaat = (page.width(), page.height())
    # Er is hier geen tablet; zonder beeldscherm meldt Qt 800 x 600. De maat
    # van de Surface wordt daarom opgegeven, net als in schermafdrukken.py.
    page.zet_zichtbaar_vlak(QRect(0, 0, logisch_b, logisch_h))
    page.zet_fotos(paden)
    stapel = QStackedWidget()
    stapel.addWidget(page)
    stapel.setFixedSize(logisch_b, logisch_h)
    stapel.show()
    QApplication.processEvents()
    return page, stapel, startmaat


def toets_opbouw(achtergrond, paden, dpr):
    logisch_b, logisch_h = int(FYSIEK_B / dpr), int(FYSIEK_H / dpr)
    print(f"\nDe opbouw op {logisch_b}x{logisch_h} logisch bij {dpr:g}x "
          f"(= {FYSIEK_B}x{FYSIEK_H} fysiek)", flush=True)

    page, stapel, startmaat = bouw_zoals_de_applicatie(
        achtergrond, paden, logisch_b, logisch_h)
    L = page._layout

    eis(startmaat != (logisch_b, logisch_h),
        f"de widget had bij het vullen nog niet zijn eindmaat {startmaat} — "
        f"zo gaat het op de booth ook")
    eis((L.W, L.H) == (logisch_b, logisch_h),
        f"de maatvoering staat op de eindmaat ({L.W}x{L.H})")
    eis(abs(page._dpr - dpr) < 1e-6,
        f"de schermschaal is overgenomen ({page._dpr:g}x)")

    # Het raster uit het ontwerp: 5x3 liggend, 3x5 staand, allebei vijftien.
    eis((L.kolommen, L.rijen) == (5, 3), f"raster {L.kolommen}x{L.rijen}")
    eis(L.n == 15, f"vijftien tegels ({L.n})")
    eis(page.rijen_zichtbaar() == 3, "drie rijen vol")

    # De tegel hoort 456 x 285 FYSIEKE pixels te zijn, ongeacht de schaal.
    # Dat is de maat waarop het ontwerp is doorgerekend (43 mm op het glas).
    tegel = page._miniaturen[0]
    eis(abs(tegel.width() - 456) <= 2 and abs(tegel.height() - 285) <= 2,
        f"de tegel is {tegel.width()}x{tegel.height()} fysieke pixels "
        f"(ontwerp: 456x285)")
    eis(abs(tegel.devicePixelRatio() - dpr) < 1e-6,
        "de tegel weet van de schermschaal")
    eis(len(page._miniaturen) == 15, f"vijftien miniaturen ({len(page._miniaturen)})")

    # Elke rij is één brede afbeelding — geen smalle kolom. Was dit ooit
    # andersom, dan kreeg je precies de "verticale stroken" van de klacht.
    strook = page._stroken[0]
    eis(strook.width() > strook.height() * 3,
        f"een rij is breed, geen kolom ({strook.width()}x{strook.height()} fysiek)")
    eis(abs(strook.width() / dpr - (L.raster_b + L.gap)) <= 2,
        "een rij is precies één periode breed")

    # De instructie en het logo op de fysieke maat, anders rekt Qt ze op.
    page.render(QPixmap(8, 8))          # dwingt _zorg_tekst en _zorg_logo af
    eis(abs(page._tekst.devicePixelRatio() - dpr) < 1e-6,
        "de instructie is op de fysieke maat gerasterd")
    eis(abs(page._logo.devicePixelRatio() - dpr) < 1e-6,
        "het logo is op de fysieke maat gerasterd")
    # Het logo hoort 520 FYSIEKE pixels breed te zijn, net als de tegel op
    # 456 uitkomt — ongeacht of de tablet op 100, 150 of 200 procent staat.
    eis(abs(page._logo.width() - 520) <= 4,
        f"het logo is {page._logo.width()} fysieke pixels breed (ontwerp: 520)")

    return page, stapel


# ── 2b. alles wat stil kan blijven staan, beweegt ──────────────────────────
def toets_verschuiving(page, dpr):
    """Niets op dit scherm mag uren op precies dezelfde plek staan.

    De collage beschermt zichzelf, want die vakjes wisselen van inhoud. De
    instructie, het logo, de achtergrond, het slotje en het serienummer niet.
    """
    print("\nDe verschuiving tegen inbranden", flush=True)
    L = page._layout

    ax, ay = page.verschuiving_bereik()
    eis(abs(ax * dpr - 56) <= 2 and abs(ay * dpr - 34) <= 2,
        f"de uitslag is {ax}x{ay} punten = {ax*dpr:g}x{ay*dpr:g} fysiek "
        f"(ontwerp: 56x34)")

    # Over een uur moet er werkelijk verplaatsing in zitten, en binnen een
    # minuut mag er vrijwel niets te zien zijn.
    standen = [page._verschuiving(t) for t in range(0, 3600, 30)]
    breedte = max(x for x, _ in standen) - min(x for x, _ in standen)
    eis(breedte > ax, f"de opbouw legt in een uur {breedte:.0f} punten af")
    per_minuut = max(abs(page._verschuiving(t + 60)[0] - page._verschuiving(t)[0])
                     for t in range(0, 660, 30))
    eis(per_minuut * dpr < 40,
        f"in een minuut hooguit {per_minuut*dpr:.0f} fysieke pixels — niet te zien")

    # De schuivende achtergrond staat sinds beta.8 UIT. Op de booth liep hij
    # schokkerig, en een haperende beweging leest als een storing. Het is sfeer
    # en geen bescherming: een wazig verloop zonder scherpe rand brandt niet in.
    eis(page._parallax is False, "de schuivende achtergrond staat standaard uit")
    bg = [page._achtergrond_verschuiving(t) for t in range(0, 1800, 30)]
    eis(len(set(bg)) == 1, "en de achtergrond staat dus werkelijk stil")
    eis(not page.beweegt() or page._schuiven,
        "met alleen de achtergrond uit hoeft er niets extra's getekend te worden")

    # Aanzetten kan wel, en dan hoort hij te schuiven zonder zichtbaar te zijn.
    page.zet_parallax(True)
    eis(page._achtergrond.width() / dpr > L.W + 8,
        f"het achtergrondveld staat overmaats klaar "
        f"({page._achtergrond.width()}x{page._achtergrond.height()} fysiek voor "
        f"een scherm van {int(L.W*dpr)}x{int(L.H*dpr)})")
    bg = [page._achtergrond_verschuiving(t) for t in range(0, 1800, 30)]
    bg_b = max(x for x, _ in bg) - min(x for x, _ in bg)
    bg_h = max(y for _, y in bg) - min(y for _, y in bg)
    eis(bg_b > 20 and bg_h > 10,
        f"aangezet schuift hij {bg_b:.0f}x{bg_h:.0f} punten")
    bg_per_min = max(abs(page._achtergrond_verschuiving(t + 60)[0]
                         - page._achtergrond_verschuiving(t)[0])
                     for t in range(0, 660, 30))
    # De bovengrens komt uit het ontwerp: daar staat de pan van de achtergrond
    # op 4,3 fysieke pixels per seconde, ofwel een schermbreedte per kwartier,
    # en dat is als niet te zien beoordeeld. 258 px per minuut dus.
    eis(bg_per_min * dpr < 258,
        f"en hooguit {bg_per_min*dpr:.0f} fysieke pixels per minuut (ontwerp "
        f"staat 258 toe)")
    page.zet_parallax(False)

    # Het slotje en het serienummer liggen buiten deze widget en moeten het te
    # horen krijgen; zonder dit signaal staan juist zij de hele avond stil.
    gemeld = []
    page.verschoven.connect(lambda x, y: gemeld.append((x, y)))
    page._gemeld = None
    page._meld_verschuiving()
    eis(len(gemeld) == 1,
        "de verschuiving wordt naar buiten gemeld, voor het slotje en het "
        "serienummer")
    page._meld_verschuiving()
    page._meld_verschuiving()
    eis(len(gemeld) == 1,
        "en alleen als er werkelijk een punt verschoven is, niet per beeldje")

    # En het schuiven van de foto's mag uit ZONDER dat de bescherming meegaat.
    # Dat is de hele reden dat die twee gescheiden zijn.
    page.zet_schuiven(False)
    eis(page._schuiven is False, "het schuiven kan uit")
    eis(page._timer.interval() >= 400,
        f"met schuiven uit tekent het scherm nog {1000/page._timer.interval():.0f} "
        f"keer per seconde in plaats van 25")
    standen_uit = [page._verschuiving(t) for t in range(0, 3600, 30)]
    eis(standen_uit == standen,
        "de verschuiving tegen inbranden loopt gewoon door met schuiven uit")
    page.zet_schuiven(True)
    eis(page._schuiven is True and page._timer.interval() <= 40,
        "en weer aan")


# ── 2c. wat een beeldje kost ───────────────────────────────────────────────
def toets_tekentijd(page, dpr):
    """Meten, niet gissen. De opdrachtgever zag beta.5 haperen.

    Beta.5 tekende alles dubbel en negeerde de schermschaal, dus elk beeldje
    werd door Windows opgeblazen — dat is het dure geval. Hier staat wat het
    ná de reparatie werkelijk kost, met schuiven aan en uit, zodat er op
    cijfers gekozen kan worden.
    """
    print("\nWat een beeldje kost", flush=True)
    from PyQt5.QtCore import QElapsedTimer

    def meet(aan, n=40):
        page.zet_schuiven(aan)
        doek = QPixmap(int(page.width() * dpr), int(page.height() * dpr))
        doek.setDevicePixelRatio(dpr)
        page.render(doek)                     # eerste keer bouwt de caches op
        klok = QElapsedTimer()
        klok.start()
        for _ in range(n):
            page.render(doek)
        return klok.nsecsElapsed() / 1e6 / n

    aan = meet(True)
    uit = meet(False)
    print(f"        volle collage, schuiven aan: {aan:.2f} ms per beeldje", flush=True)
    print(f"        volle collage, schuiven uit: {uit:.2f} ms per beeldje", flush=True)
    eis(aan < 1000.0 / 25,
        f"met schuiven aan past een beeldje in de 40 ms van 25 b/s ({aan:.2f} ms)")
    eis(uit <= aan + 1.0,
        f"met schuiven uit is het niet duurder ({uit:.2f} tegen {aan:.2f} ms)")
    page.zet_schuiven(True)

    # En de lege toestand. Die was in de vooraf gemaakte meting het DUURSTE
    # onderdeel van het scherm — zeven tot zeventien keer de hele schuivende
    # collage — omdat de achtergrond per beeldje opnieuw geschaald werd. Hij
    # wordt nu één keer overmaats klaargezet en per beeldje alleen verschoven,
    # dus dat hoort weg te zijn. Juist deze toestand staat op een rustige avond
    # het langst.
    leeg = startscherm.Collage(page._achtergrond_bron_pad)
    leeg.zet_zichtbaar_vlak(QRect(0, 0, page.width(), page.height()))
    stapel = QStackedWidget()
    stapel.addWidget(leeg)
    stapel.setFixedSize(page.width(), page.height())
    stapel.show()
    QApplication.processEvents()
    doek = QPixmap(int(page.width() * dpr), int(page.height() * dpr))
    doek.setDevicePixelRatio(dpr)
    leeg.render(doek)
    klok = QElapsedTimer()
    klok.start()
    for _ in range(40):
        leeg.render(doek)
    leeg_ms = klok.nsecsElapsed() / 1e6 / 40
    print(f"        lege collage:                {leeg_ms:.2f} ms per beeldje", flush=True)
    eis(leeg_ms < 1000.0 / 25,
        f"de lege toestand past ook in de 40 ms ({leeg_ms:.2f} ms)")
    eis(leeg_ms < aan * 3,
        f"de lege toestand is niet meer het duurste onderdeel "
        f"({leeg_ms:.2f} tegen {aan:.2f} ms vol)")


# ── 3. de achtergrond bevat geen tekst en geen logo ────────────────────────
def _telt_merkinkt(pad):
    """Hoeveel pixels lijken op de instructie (wit) of het logo (merkgroen)?

    Dit is de toets die beta.5 had moeten tegenhouden. De achtergrond waar de
    collage overheen tekent hoort ALLEEN het verloop te zijn. Zit de tekst of
    het logo er al in, dan staat straks alles dubbel op het scherm, en dat is
    aan het bestand zelf te zien: het verloop is donker en onverzadigd, de
    letters zijn wit en het logo is #94D60A.
    """
    im = QPixmap(pad).toImage().convertToFormat(4)   # RGB32
    wit = groen = 0
    stap = max(1, im.width() // 400)
    for y in range(0, im.height(), stap):
        for x in range(0, im.width(), stap):
            c = QColor(im.pixel(x, y))
            r, g, b = c.red(), c.green(), c.blue()
            if r > 210 and g > 210 and b > 210:
                wit += 1
            elif g > 150 and r < g - 40 and b < g - 80:
                groen += 1
    return wit, groen


def toets_achtergronden():
    print("\nDe meegeleverde achtergronden", flush=True)
    mapje = os.path.join(APP, "idle_defaults")
    ready = {}
    collage = {}
    for f in os.listdir(mapje):
        m = re.match(r"mbb-ready(\d+)\.jpe?g$", f, re.IGNORECASE)
        if m:
            ready[int(m.group(1))] = os.path.join(mapje, f)
        m = re.match(r"mbb-collage(\d+)\.jpe?g$", f, re.IGNORECASE)
        if m:
            collage[int(m.group(1))] = os.path.join(mapje, f)

    eis(bool(collage), "er is een kale collage-achtergrond meegeleverd")
    eis(set(ready) == set(collage),
        f"elke breedte heeft er een: ready {sorted(ready)} / "
        f"collage {sorted(collage)}")

    for breedte in sorted(collage):
        wit, groen = _telt_merkinkt(collage[breedte])
        eis(wit == 0 and groen == 0,
            f"mbb-collage{breedte}: geen tekst en geen logo ingebakken "
            f"({wit} witte, {groen} groene pixels)")
    for breedte in sorted(ready):
        wit, groen = _telt_merkinkt(ready[breedte])
        eis(wit > 0 and groen > 0,
            f"mbb-ready{breedte}: tekst en logo staan er wél in — die is "
            f"voor booths zonder collage ({wit} witte, {groen} groene pixels)")


# ── 4. de applicatie geeft de kale achtergrond mee ─────────────────────────
def toets_bedrading():
    """photobooth.py importeren kan hier niet — dat trekt de camera mee.

    Daarom op de tekst. Het gaat om één regel, en juist die regel was fout:
    de collage kreeg het beeld mét tekst en logo mee in plaats van het kale.
    """
    print("\nDe bedrading in photobooth.py", flush=True)
    bron = open(os.path.join(APP, "photobooth.py"), encoding="utf-8").read()
    eis("startscherm.Collage(self._collage_achtergrond())" in bron,
        "de collage krijgt _collage_achtergrond() mee, niet het lege startscherm")
    eis("def _collage_achtergrond(self)" in bron,
        "_collage_achtergrond() bestaat")
    eis("startscherm.Collage(idle_bg" not in bron,
        "de collage krijgt nergens meer idle_bg mee (dat is mbb-ready, mét tekst)")


def main():
    app = QApplication(sys.argv)
    try:
        import lettertype
        lettertype.laad_merkletters()
    except Exception as e:
        print(f"  let op: merkletters niet geladen ({e})", flush=True)

    werkmap = tempfile.mkdtemp(prefix="startscherm-toets-")
    try:
        sessies = 18
        raw, strips = bouw_event(os.path.join(werkmap, "Testfeest"), sessies)
        onderdeel("de fotokeuze", toets_fotokeuze, raw, strips, sessies)
        paden = startscherm.fotos_van_event(raw)

        onderdeel("de achtergronden", toets_achtergronden)
        onderdeel("de bedrading", toets_bedrading)

        # De kale achtergrond gaat er echt onder, net als op de booth.
        achtergrond = os.path.join(APP, "idle_defaults", "mbb-collage2763.jpg")
        if not os.path.isfile(achtergrond):
            achtergrond = ""

        dpr = float(os.environ.get("QT_SCALE_FACTOR", "1") or 1)
        uit = onderdeel("de opbouw", toets_opbouw, achtergrond, paden, dpr)
        if uit is not None:
            page, _stapel = uit
            onderdeel("het vullen van het scherm", toets_vult_het_scherm, page, dpr)
            onderdeel("de verschuiving", toets_verschuiving, page, dpr)
            onderdeel("de tekentijd", toets_tekentijd, page, dpr)
        onderdeel("de maat van de stapel", toets_maat_van_de_stapel,
                  achtergrond, paden, dpr)
    finally:
        shutil.rmtree(werkmap, ignore_errors=True)

    print("", flush=True)
    if fouten:
        print(f"STARTSCHERM: {len(fouten)} fout(en)", flush=True)
        for f in fouten:
            print(f"  - {f}", flush=True)
        return 1
    print("STARTSCHERM: alles klopt", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
