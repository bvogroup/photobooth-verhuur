"""Meet wat een continu schuivende collage werkelijk kost.

De opdrachtgever wil dat de rijen van het startscherm doorschuiven. Het
ontwerpverslag waarschuwt daarvoor: dit draait op een fanless Surface Pro 7
naast een camera, een printer en een live camerabeeld, en drie schuivende rijen
betekent dat je het hele collagegebied per beeldje opnieuw aanraakt.

Dit script meet dat, in plaats van erover te gissen. Vier manieren om hetzelfde
beeld te maken, van duur naar goedkoop:

  1  naief          — elke tegel per beeldje opnieuw geschaald uit de bron
  2  tegels gecacht — elke tegel als kant-en-klare pixmap, 15 blits
  3  rijen gecacht  — elke rij één brede pixmap, 2 blits per rij (het voorstel)
  4  alles stil     — één blit van de hele collage (de stilstaande versie)

En ter ijking de achtergrond van de lege toestand: één geschaalde blit van
131 kpx naar het volle scherm, want dat is wat het ontwerp al had begroot.

Draaien:  python meet_startscherm.py

Let op bij het lezen: dit meet op de machine waar het draait. Een bouwserver is
geen Surface Pro 7. Daarom staat er niet alleen een uitkomst in milliseconden
maar ook hoeveel beeldjes per seconde er nog in passen, en hoeveel ruimte er
overblijft. Ruimte is wat je nodig hebt om een uitspraak te kunnen doen over
andere hardware.
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt, QRect                                  # noqa: E402
from PyQt5.QtGui import QPixmap, QPainter, QColor                   # noqa: E402
from PyQt5.QtWidgets import QApplication                            # noqa: E402

# ── de maatvoering, overgenomen uit docs/startscherm/render/ontwerp.py ──
# Niet opnieuw uitgerekend: Layout daar is de enige bron.
REF = 1824
M_V, GAP = 64, 26
TILE_W, TILE_H = 456, 285
SCHERM_B, SCHERM_H = 2736, 1824          # Surface Pro 7, liggend
KOLOMMEN, RIJEN = 5, 3

RASTER_B = KOLOMMEN * TILE_W + (KOLOMMEN - 1) * GAP     # 2384
RASTER_H = RIJEN * TILE_H + (RIJEN - 1) * GAP           # 907
RASTER_X = (SCHERM_B - RASTER_B) // 2
RASTER_Y = M_V


def _bron_tegels(n=15):
    """Nepfoto's op de maat waarop de camera ze aanlevert (3:2, 1800x1200)."""
    uit = []
    for i in range(n):
        p = QPixmap(1800, 1200)
        p.fill(QColor((37 * i) % 256, (91 * i) % 256, (149 * i) % 256))
        uit.append(p)
    return uit


def _miniaturen(bron):
    """Eén keer op tegelmaat brengen — wat het ontwerp voorschrijft."""
    return [b.scaled(TILE_W, TILE_H, Qt.KeepAspectRatioByExpanding,
                     Qt.SmoothTransformation) for b in bron]


def _rijstroken(mini):
    """Elke rij als één brede pixmap, één periode lang.

    Dit is de kern van het voorstel: schuiven wordt dan het tekenen van een
    stuk van een bestaande pixmap, en niet het opnieuw samenstellen van de rij.
    """
    stroken = []
    for r in range(RIJEN):
        strook = QPixmap(RASTER_B + GAP, TILE_H)
        strook.fill(Qt.transparent)
        p = QPainter(strook)
        for c in range(KOLOMMEN):
            p.drawPixmap(c * (TILE_W + GAP), 0, mini[(r * KOLOMMEN + c) % len(mini)])
        p.end()
        stroken.append(strook)
    return stroken


def _hele_collage(mini):
    doek = QPixmap(RASTER_B, RASTER_H)
    doek.fill(Qt.transparent)
    p = QPainter(doek)
    for i in range(KOLOMMEN * RIJEN):
        c, r = i % KOLOMMEN, i // KOLOMMEN
        p.drawPixmap(c * (TILE_W + GAP), r * (TILE_H + GAP), mini[i])
    p.end()
    return doek


def meet(naam, tekenen, doel, beeldjes=60):
    """Teken `beeldjes` keer en geef de mediaan per beeldje in milliseconden."""
    tekenen(doel, 0)                      # eerste keer niet meetellen (opwarmen)
    tijden = []
    for k in range(beeldjes):
        t0 = time.perf_counter()
        tekenen(doel, k)
        tijden.append((time.perf_counter() - t0) * 1000.0)
    tijden.sort()
    mediaan = tijden[len(tijden) // 2]
    slechtste = tijden[int(len(tijden) * 0.95)]
    return naam, mediaan, slechtste


def main():
    app = QApplication(sys.argv[:1])      # noqa: F841
    scherm = QPixmap(SCHERM_B, SCHERM_H)

    bron = _bron_tegels()
    mini = _miniaturen(bron)
    stroken = _rijstroken(mini)
    collage = _hele_collage(mini)

    # de achtergrond van de lege toestand: 1/8 maat, 1,3x overmaats
    veld = QPixmap(int(SCHERM_B / 8 * 1.3), int(SCHERM_H / 8 * 1.3))
    veld.fill(QColor(30, 40, 60))

    def naief(doel, k):
        p = QPainter(doel)
        dx = -(k * 2) % (TILE_W + GAP)
        for i in range(KOLOMMEN * RIJEN):
            c, r = i % KOLOMMEN, i // KOLOMMEN
            tegel = bron[i].scaled(TILE_W, TILE_H, Qt.KeepAspectRatioByExpanding,
                                   Qt.SmoothTransformation)
            p.drawPixmap(RASTER_X + c * (TILE_W + GAP) + dx,
                         RASTER_Y + r * (TILE_H + GAP), tegel)
        p.end()

    def tegels_gecacht(doel, k):
        p = QPainter(doel)
        dx = -(k * 2) % (TILE_W + GAP)
        for i in range(KOLOMMEN * RIJEN):
            c, r = i % KOLOMMEN, i // KOLOMMEN
            p.drawPixmap(RASTER_X + c * (TILE_W + GAP) + dx,
                         RASTER_Y + r * (TILE_H + GAP), mini[i])
        p.end()

    def rijen_gecacht(doel, k):
        p = QPainter(doel)
        breedte = RASTER_B + GAP
        for r in range(RIJEN):
            dx = -(k * 2 + r * 37) % breedte
            y = RASTER_Y + r * (TILE_H + GAP)
            p.setClipRect(QRect(RASTER_X, y, RASTER_B, TILE_H))
            p.drawPixmap(RASTER_X + dx, y, stroken[r])
            p.drawPixmap(RASTER_X + dx - breedte, y, stroken[r])
            p.setClipping(False)
        p.end()

    def stilstaand(doel, k):
        p = QPainter(doel)
        p.drawPixmap(RASTER_X, RASTER_Y, collage)
        p.end()

    def achtergrond(doel, k):
        p = QPainter(doel)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        dx = int((k % 60) * 0.4)
        p.drawPixmap(QRect(0, 0, SCHERM_B, SCHERM_H), veld,
                     QRect(dx, dx // 2, veld.width() - 100, veld.height() - 60))
        p.end()

    proeven = [
        ("1  naief — elke tegel per beeldje herschaald", naief),
        ("2  tegels gecacht — 15 blits per beeldje", tegels_gecacht),
        ("3  rijen gecacht — 6 blits per beeldje  <= HET VOORSTEL", rijen_gecacht),
        ("4  stilstaand — 1 blit per beeldje", stilstaand),
        ("   ijking: achtergrond lege toestand, 1 geschaalde blit", achtergrond),
    ]

    print(f"Scherm {SCHERM_B}x{SCHERM_H}, raster {RASTER_B}x{RASTER_H} "
          f"({RASTER_B * RASTER_H / 1e6:.2f} Mpx), {KOLOMMEN}x{RIJEN} tegels van "
          f"{TILE_W}x{TILE_H}")
    print(f"Qt-platform: {os.environ.get('QT_QPA_PLATFORM')}   "
          f"Python {sys.version.split()[0]}")
    print()
    print(f"{'manier':52} {'mediaan':>9} {'95%':>8} {'max b/s':>9} "
          f"{'ruimte bij 30 b/s':>18}")
    print("-" * 100)
    uitkomsten = {}
    for naam, fn in proeven:
        n, med, p95 = meet(naam, fn, scherm)
        uitkomsten[naam[:1]] = med
        bs = 1000.0 / med if med > 0 else float('inf')
        ruimte = 33.3 / med if med > 0 else float('inf')
        print(f"{n:52} {med:7.2f}ms {p95:6.2f}ms {bs:8.0f} {ruimte:15.1f}x")

    print()
    print("Wat dit betekent")
    print("-" * 100)
    if "3" in uitkomsten and uitkomsten["3"] > 0:
        m3 = uitkomsten["3"]
        print(f"De voorgestelde manier kost {m3:.2f} ms per beeldje. Bij 30 beeldjes")
        print(f"per seconde is het beeldjesbudget 33,3 ms, dus dit gebruikt "
              f"{m3 / 33.3 * 100:.1f}% daarvan.")
        print(f"Er is {33.3 / m3:.0f}x ruimte. Een toestel dat drie keer trager is dan")
        print(f"deze machine haalt het dus nog steeds ruim.")
    if "1" in uitkomsten and "3" in uitkomsten and uitkomsten["3"] > 0:
        print(f"De naieve manier is {uitkomsten['1'] / uitkomsten['3']:.0f}x zo duur. "
              f"Dat is het verschil tussen")
        print("wel en niet vooraf samenstellen — niet tussen wel en niet bewegen.")
    print()
    print("LET OP: dit is gemeten op de machine waar het nu draait, en dat is geen")
    print("Surface Pro 7. De verhouding tussen de vier manieren geldt overal; het")
    print("getal in milliseconden moet op de booth zelf bevestigd worden.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
