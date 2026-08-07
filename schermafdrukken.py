"""Schermafdrukken maken van de bediening, op de werkelijke maat van de tablet.

Waarvoor dit bestaat
--------------------
De opdrachtgever oordeelt op wat hij ziet, niet op wat er in de code staat. Dit
script tekent de merkstijlen met de échte Qt-motor — dus met de echte
lettertypen, de echte afrondingen en de echte knoptoestanden — en schrijft er
PNG's van weg op 2736 x 1824, de resolutie van de Surface Pro 7.

Het draait zonder beeldscherm (offscreen), dus het werkt ook op een bouwserver.

Gebruik
-------
    python schermafdrukken.py                 -> schrijft naar schermafdrukken/
    python schermafdrukken.py --map ergens    -> schrijft daarheen

Wat je krijgt
-------------
    proefblad.png    alle knoppen, kaarten, velden en tekstmaten naast elkaar,
                     met hun toestanden (rust, aangeraakt, uitgeschakeld)
    deelscherm.png   het paneel van het deelscherm, opgebouwd met exact dezelfde
                     merk-aanroepen als het echte scherm in photobooth.py

Let op wat dit NIET is: het echte scherm uit photobooth.py. Dat scherm hangt aan
een camera, een printer en een sessie, en is niet los te tekenen. Wat hier staat
gebruikt dezelfde stijlen uit merk.py, zodat je ziet wat die stijlen doen.

Wat er na beta.5 aan veranderd is
---------------------------------
De afdrukken van het startscherm zagen er goed uit terwijl het scherm op de
booth onbruikbaar was. Dat kwam doordat er drie dingen anders gingen dan echt:
er ging geen achtergrond onder, er was geen echte mappenstructuur, en de
schermschaal stond op 1. Alle drie zijn nu wél zoals op de tablet. Zie
test_startscherm.py — daar wordt het ook gemeten, want een plaatje waar niemand
naar kijkt houdt niets tegen.
"""

import os
import sys

# De app-map op het pad, net als main.py doet.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Zonder beeldscherm tekenen. Moet vóór de Qt-import gezet worden.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# De tablet staat op 200%. Qt rekent dan in logische punten en zet het
# resultaat op de dubbele pixelmaat neer. Zonder dit tekent alles hier op
# schaal 1 en zie je niet wat er op de booth wazig is.
os.environ.setdefault("QT_SCALE_FACTOR", "2")

from PyQt5.QtCore import Qt, QRect                             # noqa: E402
from PyQt5.QtGui import QPixmap, QColor                        # noqa: E402
from PyQt5.QtWidgets import (QApplication, QWidget, QLabel,    # noqa: E402
                             QPushButton, QLineEdit, QVBoxLayout,
                             QHBoxLayout, QGridLayout, QProgressBar,
                             QStackedWidget)

QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)

import merk                                                    # noqa: E402
import lettertype                                              # noqa: E402
import startscherm                                             # noqa: E402

# De Surface Pro 7: 2736 x 1824 op 12,3 inch. Bij 200% vergroting is dat 1368 x
# 912 aan punten, en dat is de maat waarin de code rekent.
PUNTEN_BREED, PUNTEN_HOOG = 1368, 912
VERGROTING = 2


def _label(tekst, punten, kleur, vet=False, kop=False):
    lab = QLabel(tekst)
    lab.setFont(merk.letter(punten, vet=vet, kop=kop))
    lab.setStyleSheet(merk.tekst(kleur))
    lab.setWordWrap(True)
    return lab


def _knop(tekst, stijl, hoogte):
    k = QPushButton(tekst)
    k.setFont(merk.letter(merk.TEKST_KNOP, vet=True))
    k.setMinimumHeight(hoogte)
    k.setStyleSheet(stijl)
    return k


def _nepevent(map_naam, sessies=15):
    """Een echte photos/<event>/{raw,strips}/ met plaatshouders erin.

    Niet een platte map met losse bestanden, want dan wordt er nooit gekozen
    tussen raw/ en strips/ en toets je juist het ding niet dat op de booth
    misging. De stroken staan er als lokaas: leest de collage ooit uit de
    verkeerde map, dan zie je het meteen op de afdruk.
    """
    basis = os.path.join(map_naam, "_nepevent")
    raw = os.path.join(basis, "raw")
    strips = os.path.join(basis, "strips")
    os.makedirs(raw, exist_ok=True)
    os.makedirs(strips, exist_ok=True)
    for i in range(sessies):
        stempel = f"01-01-2026_2{i // 60}.{i % 60:02d}.{(i * 7) % 60:02d}"
        for n in (1, 2, 3):
            # losse opname: liggend 3:2, zoals een camera hem levert
            pm = QPixmap(1800, 1200)
            pm.fill(QColor.fromHsv((i * 37) % 360, 90,
                                   90 + n * 20 + (i * 11) % 60))
            pm.save(os.path.join(raw, f"{stempel}_{n}.jpg"), "JPG")
        # de samengestelde strook: staand 1200 x 1800
        strook = QPixmap(1200, 1800)
        strook.fill(QColor(255, 0, 255))
        strook.save(os.path.join(strips, f"{stempel}.jpg"), "JPG")
    return raw


def _collage_achtergrond(breedte):
    """Dezelfde kale achtergrond die photobooth.py eronder legt.

    In beta.5 ging hier niets onder, en op de booth een beeld met de
    instructie en het logo er al in gebakken. Vandaar dat alles dubbel stond
    en dat de afdrukken er niettemin goed uitzagen.
    """
    mapje = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "idle_defaults")
    import re
    kandidaten = []
    if os.path.isdir(mapje):
        for f in os.listdir(mapje):
            m = re.match(r"mbb-collage(\d+)\.jpe?g$", f, re.IGNORECASE)
            if m:
                kandidaten.append((int(m.group(1)), os.path.join(mapje, f)))
    if not kandidaten:
        print("[AFDRUK] LET OP: geen mbb-collage*.jpg gevonden — de afdrukken "
              "tonen dan een egale ondergrond, niet wat de booth laat zien.",
              flush=True)
        return ""
    kandidaten.sort(key=lambda k: abs(k[0] - breedte))
    return kandidaten[0][1]


def bouw_proefblad():
    """Alle bouwstenen naast elkaar, met hun toestanden."""
    blad = QWidget()
    blad.setFixedSize(PUNTEN_BREED, PUNTEN_HOOG)
    blad.setStyleSheet(merk.pagina(op_donker=True))
    buiten = QVBoxLayout(blad)
    buiten.setContentsMargins(merk.RUIMTE_KANTLIJN, merk.RUIMTE_KANTLIJN,
                              merk.RUIMTE_KANTLIJN, merk.RUIMTE_KANTLIJN)
    buiten.setSpacing(merk.RUIMTE_RUIM)

    buiten.addWidget(_label("MyBoothBox — de bouwstenen van de bediening",
                            merk.TEKST_KOP, merk.OP_DONKER, vet=True, kop=True))
    buiten.addWidget(_label(
        f"Letter: {merk.LOPEND_LETTER} voor tekst, {merk.KOP_LETTER} voor koppen. "
        f"Afrondingen {merk.RONDING_KNOP}/{merk.RONDING_KAART}/{merk.RONDING_VLAK}. "
        f"Knophoogtes {merk.KNOP_HOOG}/{merk.KNOP_NORMAAL}/{merk.KNOP_MIN}.",
        merk.TEKST_KLEIN, merk.OP_DONKER_ZACHT))

    # ── knoppen en hun toestanden ────────────────────────────────────────────
    raster = QGridLayout()
    raster.setSpacing(merk.RUIMTE)
    kolommen = [
        ("Hoofdactie", merk.knop_hoofd(), merk.KNOP_HOOG),
        ("Tweede keus", merk.knop_tweede(op_donker=True), merk.KNOP_NORMAAL),
        ("Stil", merk.knop_stil(op_donker=True), merk.KNOP_NORMAAL),
        ("Gevaar", merk.knop_gevaar(), merk.KNOP_NORMAAL),
    ]
    for kol, (naam, stijl, hoogte) in enumerate(kolommen):
        raster.addWidget(_label(naam, merk.TEKST_FIJN, merk.OP_DONKER_FIJN), 0, kol)
        raster.addWidget(_knop("Printen", stijl, hoogte), 1, kol)
        uit = _knop("Uitgeschakeld", stijl, hoogte)
        uit.setEnabled(False)
        raster.addWidget(uit, 2, kol)
    buiten.addLayout(raster)

    # ── de tekstschaal ───────────────────────────────────────────────────────
    schaal = QHBoxLayout()
    schaal.setSpacing(merk.RUIMTE_RUIM)
    for naam, punten, kop in [("Kop", merk.TEKST_KOP, True),
                              ("Tussenkop", merk.TEKST_SUBKOP, True),
                              ("Knop", merk.TEKST_KNOP, False),
                              ("Lopend", merk.TEKST_LOPEND, False),
                              ("Klein", merk.TEKST_KLEIN, False),
                              ("Fijn", merk.TEKST_FIJN, False)]:
        kolom = QVBoxLayout()
        kolom.addWidget(_label(f"Aa {punten}", punten, merk.OP_DONKER,
                               vet=kop, kop=kop))
        kolom.addWidget(_label(naam, merk.TEKST_FIJN, merk.OP_DONKER_FIJN))
        schaal.addLayout(kolom)
    schaal.addStretch()
    buiten.addLayout(schaal)

    # ── kaart, veld en balk ──────────────────────────────────────────────────
    onder = QHBoxLayout()
    onder.setSpacing(merk.RUIMTE_RUIM)

    kaartje = QWidget()
    kaartje.setStyleSheet(f"QWidget {{ {merk.kaart(op_donker=True)} }}")
    kl = QVBoxLayout(kaartje)
    kl.setContentsMargins(merk.RUIMTE_RUIM, merk.RUIMTE_RUIM,
                          merk.RUIMTE_RUIM, merk.RUIMTE_RUIM)
    kl.addWidget(_label("Een kaart", merk.TEKST_SUBKOP, merk.OP_DONKER,
                        vet=True, kop=True))
    kl.addWidget(_label("Met een rand, niet met een schaduw — Qt kent box-shadow "
                        "niet, en het merk vraagt hier toch al om de rand.",
                        merk.TEKST_KLEIN, merk.OP_DONKER_ZACHT))
    onder.addWidget(kaartje, 1)

    velden = QVBoxLayout()
    velden.setSpacing(merk.RUIMTE)
    veld = QLineEdit("Naam van de gast")
    veld.setFont(merk.letter(merk.TEKST_LOPEND))
    veld.setMinimumHeight(merk.KNOP_MIN)
    veld.setStyleSheet(merk.invoerveld(op_donker=True))
    velden.addWidget(veld)
    balk = QProgressBar()
    balk.setRange(0, 100)
    balk.setValue(62)
    balk.setTextVisible(False)
    balk.setFixedHeight(6)
    balk.setStyleSheet(merk.voortgangsbalk(op_donker=True))
    velden.addWidget(balk)
    velden.addStretch()
    onder.addLayout(velden, 1)
    buiten.addLayout(onder)

    buiten.addStretch()
    return blad


def bouw_deelscherm():
    """Het paneel van het deelscherm, met dezelfde merk-aanroepen als het echt."""
    blad = QWidget()
    blad.setFixedSize(PUNTEN_BREED, PUNTEN_HOOG)
    blad.setStyleSheet(merk.pagina(op_donker=True))
    buiten = QHBoxLayout(blad)
    buiten.setContentsMargins(0, 0, 0, 0)
    buiten.setSpacing(0)

    # links: waar de fotostrip staat
    links = QWidget()
    links.setStyleSheet("background: transparent;")
    ll = QVBoxLayout(links)
    ll.setContentsMargins(merk.RUIMTE_KANTLIJN, merk.RUIMTE_KANTLIJN,
                          merk.RUIMTE_KANTLIJN, merk.RUIMTE_KANTLIJN)
    strip = QLabel("de fotostrip")
    strip.setAlignment(Qt.AlignCenter)
    strip.setFont(merk.letter(merk.TEKST_KLEIN))
    strip.setStyleSheet(
        f"color: {merk.OP_DONKER_FIJN}; background: {merk.INKT_VLAK};"
        f" border: 1px solid {merk.INKT_RAND};"
        f" border-radius: {merk.RONDING_KAART}px;")
    ll.addWidget(strip)
    buiten.addWidget(links, 1)

    # rechts: het paneel met de acties — precies zoals photobooth.py het zet
    paneel = QWidget()
    paneel.setFixedWidth(420)
    paneel.setStyleSheet(f"QWidget {{ background: {merk.INKT_VLAK}; }}")
    pl = QVBoxLayout(paneel)
    pl.setContentsMargins(merk.RUIMTE_KANTLIJN, merk.RUIMTE_RUIM,
                          merk.RUIMTE_KANTLIJN, merk.RUIMTE_RUIM)
    pl.setSpacing(merk.RUIMTE)
    pl.addStretch()

    pl.addWidget(_label("Klaar om te printen", merk.TEKST_KLEIN,
                        merk.OP_DONKER_ZACHT))
    pl.addWidget(_knop("Printen", merk.knop_hoofd(), merk.KNOP_HOOG))
    pl.addWidget(_knop("E-mail", merk.knop_tweede(op_donker=True),
                       merk.KNOP_NORMAAL))
    pl.addWidget(_label("Nog 8 prints over", merk.TEKST_KLEIN,
                        merk.OP_DONKER_FIJN))

    qr = QWidget()
    qr.setStyleSheet(f"QWidget {{ background: {merk.WIT};"
                     f" border-radius: {merk.RONDING_VLAK}px; }}")
    ql = QVBoxLayout(qr)
    ql.setContentsMargins(merk.RUIMTE, merk.RUIMTE, merk.RUIMTE, merk.RUIMTE)
    vak = QLabel()
    vak.setFixedSize(180, 180)
    vak.setStyleSheet(f"background: {merk.INKT};")
    ql.addWidget(vak, alignment=Qt.AlignCenter)
    prompt = _label("Download je foto's op je telefoon", merk.TEKST_KLEIN, merk.INKT,
                    vet=True)
    prompt.setAlignment(Qt.AlignCenter)
    ql.addWidget(prompt)
    pl.addWidget(qr)

    pl.addWidget(_knop("Klaar", merk.knop_tweede(op_donker=True),
                       merk.KNOP_NORMAAL))
    pl.addStretch()
    buiten.addWidget(paneel, 0)
    return blad


def veilig(naam, maker):
    """Maak één blad; klapt het om, meld dat en ga door met de rest.

    Zonder dit stopte het hele script bij het eerste probleem en kwam er één
    plaatje uit een reeks van tien — terwijl de stap in de bouwstraat
    "geslaagd" meldde, want hij mag de bouw niet tegenhouden. Nu staat er in
    het logboek wat er misging en komt de rest er gewoon uit.
    """
    try:
        return maker()
    except Exception as exc:
        print(f"[AFDRUK] MISLUKT: {naam} — {type(exc).__name__}: {exc}", flush=True)
        return None


def schrijf(widget, pad):
    """Teken het scherm op de echte resolutie van de tablet en sla het op.

    Het doek volgt de maat van de widget zelf en niet een vaste liggende maat:
    het startscherm wordt ook staand getekend, en dan is 2736 x 1824 juist de
    verkeerde kant op. Stond hier eerst wel vast, en dan viel het logo buiten
    beeld en bleef er een derde van het doek leeg.
    """
    widget.show()  # nodig zodat Qt de indeling uitrekent
    QApplication.processEvents()
    b = widget.width() or PUNTEN_BREED
    h = widget.height() or PUNTEN_HOOG
    # De schaal van de widget zelf, niet een vast getal: dan staat er op de
    # afdruk precies wat de tablet te zien krijgt, inclusief eventuele waas.
    schaal = float(widget.devicePixelRatioF() or VERGROTING)
    doek = QPixmap(int(b * schaal), int(h * schaal))
    doek.setDevicePixelRatio(schaal)
    doek.fill(Qt.transparent)
    widget.render(doek)
    doek.save(pad, "PNG")
    print(f"[AFDRUK] {pad}  ({doek.width()} x {doek.height()} px, "
          f"{b} x {h} punten @ {schaal:g}x)", flush=True)


def main():
    map_naam = "schermafdrukken"
    if "--map" in sys.argv:
        map_naam = sys.argv[sys.argv.index("--map") + 1]
    os.makedirs(map_naam, exist_ok=True)

    app = QApplication(sys.argv)
    verslag = lettertype.laad_merkletters()
    if not verslag["families"]:
        print("[AFDRUK] LET OP: er zijn geen merkletters geladen. De afdrukken "
              "tonen dan de systeemletter, niet het merk.", flush=True)

    schrijf(bouw_proefblad(), os.path.join(map_naam, "proefblad.png"))
    schrijf(bouw_deelscherm(), os.path.join(map_naam, "deelscherm.png"))

    # Het startscherm in zijn vier toestanden, liggend en staand. Met een
    # nagemaakt event, want op een bouwserver staat er geen.
    raw = _nepevent(map_naam)
    alle = startscherm.fotos_van_event(raw)
    print(f"[AFDRUK] {len(alle)} foto's uit {raw}", flush=True)

    bewaar = []
    for stand, (b, h) in (("liggend", (PUNTEN_BREED, PUNTEN_HOOG)),
                          ("staand", (PUNTEN_HOOG, PUNTEN_BREED))):
        vorm = startscherm.Layout(b, h)
        achtergrond = _collage_achtergrond(int(b * VERGROTING))
        for naam, aantal in (("leeg", 0),
                             ("1-rij", vorm.kolommen),
                             ("2-rijen", vorm.kolommen * 2),
                             ("vol", vorm.n)):
            def maak(b=b, h=h, aantal=aantal, achtergrond=achtergrond):
                # PRECIES de volgorde van _build_idle_page(): aanmaken,
                # meteen vullen, en pas daarna in de stapel — en dán krijgt
                # hij zijn maat. Wie hem hier eerst op maat zet, tekent een
                # situatie die op de booth niet bestaat; dat is de reden dat
                # de afdrukken van beta.5 goed leken.
                blad = startscherm.Collage(achtergrond)
                # Zonder beeldscherm meldt Qt een scherm van 800 x 600, en
                # daar zou de indeling dan op uitkomen. Hier wordt op de maat
                # van de tablet getekend, dus die maat wordt opgegeven.
                blad.zet_zichtbaar_vlak(QRect(0, 0, b, h))
                blad.zet_fotos(alle[:aantal])
                stapel = QStackedWidget()
                stapel.addWidget(blad)
                stapel.setFixedSize(b, h)
                stapel.show()
                QApplication.processEvents()
                bewaar.append(stapel)      # anders ruimt Python hem meteen op
                return blad
            blad = veilig(f"startscherm-{stand}-{naam}", maak)
            if blad is not None:
                schrijf(blad, os.path.join(map_naam,
                                           f"startscherm-{stand}-{naam}.png"))
    app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
