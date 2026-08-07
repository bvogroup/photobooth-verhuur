"""Toetst dat Qt de merkletters echt laadt, en dat het vangnet blijft werken.

Draait in de bouwstraat op Windows, en is los te draaien:

    python test_lettertype.py

Drie vragen, want dit zijn de drie manieren waarop dit stuk stuk kan gaan:

  1. Worden DM Sans en Plus Jakarta Sans werkelijk geladen, mét hun beide snedes?
     Een lettertype dat wel in de map staat maar door Qt niet wordt herkend, is
     net zo nutteloos als geen lettertype.

  2. Valt de software netjes terug op Segoe UI als de map leeg is?

  3. Overleeft hij een stukgemaakt bestand?

Vraag 2 en 3 zijn belangrijker dan 1. Een photobooth staat op een feest: hij moet
altijd opkomen. Liever de verkeerde letter dan een zwart scherm.

Afsluitcode 0 = alles goed, 1 = er is iets mis.
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication            # noqa: E402
from PyQt5.QtGui import QFontDatabase, QFontInfo    # noqa: E402

import merk                                          # noqa: E402
import lettertype                                    # noqa: E402

_fouten = []
_geslaagd = 0


def eis(voorwaarde, omschrijving):
    global _geslaagd
    if voorwaarde:
        _geslaagd += 1
        print(f"  OK    {omschrijving}")
    else:
        _fouten.append(omschrijving)
        print(f"  FOUT  {omschrijving}")


def _herstel_merk(lopend, kop):
    """merk.py wordt door de lader aangepast; na elke proef terugzetten."""
    merk.LOPEND_LETTER, merk.KOP_LETTER = lopend, kop


def main():
    app = QApplication(sys.argv[:1])  # noqa: F841 — nodig voor QFontDatabase
    oorspronkelijk = (merk.LOPEND_LETTER, merk.KOP_LETTER)

    # ── 1. de echte map ──────────────────────────────────────────────────
    print("\n1. De meegeleverde lettertypen")
    verslag = lettertype.laad_merkletters(stil=True)
    print(f"     map: {verslag['map']}")
    print(f"     geladen: {verslag['geladen']}")
    print(f"     families: {verslag['families']}")

    eis(verslag["map"] is not None, "de map fonts/ is gevonden")
    eis("DM Sans" in verslag["families"], "Qt heeft de familie 'DM Sans' geregistreerd")
    eis("Plus Jakarta Sans" in verslag["families"],
        "Qt heeft de familie 'Plus Jakarta Sans' geregistreerd")

    db = QFontDatabase()
    for familie in ("DM Sans", "Plus Jakarta Sans"):
        if familie not in verslag["families"]:
            continue
        snedes = set(db.styles(familie))
        print(f"     snedes van {familie}: {sorted(snedes)}")
        eis("Regular" in snedes, f"{familie} heeft een Regular")
        eis(any("Bold" == s for s in snedes), f"{familie} heeft een echte Bold")

    # Vraagt merk.letter() ook werkelijk de merkletter aan, en krijgt hij die?
    for kop, verwacht in ((False, "DM Sans"), (True, "Plus Jakarta Sans")):
        f = merk.letter(merk.TEKST_KNOP, vet=True, kop=kop)
        gekregen = QFontInfo(f).family()
        eis(gekregen == verwacht,
            f"merk.letter(kop={kop}) levert {verwacht!r} (kreeg {gekregen!r})")
        eis(f.pixelSize() == merk.TEKST_KNOP,
            f"merk.letter meet in beeldpunten, niet in punten "
            f"(pixelSize={f.pixelSize()})")

    # ── 2. lege map: valt hij terug? ─────────────────────────────────────
    print("\n2. Het vangnet: een lege map")
    _herstel_merk(*oorspronkelijk)
    leeg = tempfile.mkdtemp(prefix="geen_letters_")
    try:
        origineel = lettertype._mogelijke_mappen
        lettertype._mogelijke_mappen = lambda: [os.path.join(leeg, "bestaat_niet")]
        v = lettertype.laad_merkletters(stil=True)
        eis(v["map"] is None, "een ontbrekende map wordt gemeld, niet doodgezwegen")
        eis(len(v["gemist"]) > 0, "er staat een waarschuwing in het verslag")
        # De terugval mag nooit een letter aanwijzen die er niet is.
        beschikbaar = set(QFontDatabase().families())
        eis(merk.LOPEND_LETTER in beschikbaar,
            f"na terugval bestaat de lopende letter echt ({merk.LOPEND_LETTER!r})")
        eis(merk.KOP_LETTER in beschikbaar,
            f"na terugval bestaat de kopletter echt ({merk.KOP_LETTER!r})")
    finally:
        lettertype._mogelijke_mappen = origineel
        shutil.rmtree(leeg, ignore_errors=True)

    # ── 3. stukgemaakt bestand ───────────────────────────────────────────
    print("\n3. Het vangnet: een stukgemaakt bestand")
    _herstel_merk(*oorspronkelijk)
    rommel = tempfile.mkdtemp(prefix="kapotte_letters_")
    try:
        with open(os.path.join(rommel, "DMSans-Regular.ttf"), "wb") as fh:
            fh.write(b"dit is geen lettertype, maar wel een .ttf")
        origineel = lettertype._mogelijke_mappen
        lettertype._mogelijke_mappen = lambda: [rommel]
        v = lettertype.laad_merkletters(stil=True)
        eis(True, "een kapot bestand laat de software niet vallen")
        eis(any("niet lezen" in r for r in v["gemist"]),
            "het kapotte bestand wordt bij name genoemd in het verslag")
        beschikbaar = set(QFontDatabase().families())
        eis(merk.LOPEND_LETTER in beschikbaar,
            f"ook dan bestaat de gekozen letter echt ({merk.LOPEND_LETTER!r})")
    except Exception as exc:
        eis(False, f"een kapot bestand liet de software vallen: {exc!r}")
    finally:
        lettertype._mogelijke_mappen = origineel
        shutil.rmtree(rommel, ignore_errors=True)

    _herstel_merk(*oorspronkelijk)

    print(f"\n{_geslaagd} goed, {len(_fouten)} fout")
    for f in _fouten:
        print(f"   FOUT: {f}")
    return 1 if _fouten else 0


if __name__ == "__main__":
    sys.exit(main())
