"""Toetst dat alles wat een gast aanraakt onderin het midden staat.

Waarom dit bestaat
------------------
De booth staat op een luidsprekerstatief. Een tik rechts op het scherm draait
hem weg: hij staat scheef, de volgende foto klopt niet meer en iemand moet hem
rechtzetten. De eis is daarom meetbaar en niet smaakgevoelig — de hoofdknop
hoort op de horizontale hartlijn van het scherm te staan, en laag.

Hoe er getoetst wordt
---------------------
Niet op een nagebouwd scherm. Beta.5 kwam door de bouwstraat met afdrukken die
er goed uitzagen en was op de booth onbruikbaar, omdat de proef iets anders
tekende dan de applicatie doet. Daarom worden hier de ECHTE bouwmethodes uit
photobooth.py aangeroepen — `_build_review_confirm_panel`,
`_build_review_print_question_panel`, `_build_filter_page` — op een venster van
de werkelijke maat, met de werkelijke schermschaal. Wat hier gemeten wordt, is
wat er op het glas staat.

photobooth.py trekt bij het importeren de camera, de printer en de cloud mee.
Die worden hieronder vervangen door lege modules; er wordt niets van ze
gebruikt, alleen de vensteropbouw.

Draait zonder beeldscherm:

    QT_SCALE_FACTOR=2 python test_bediening.py
"""

import os
import sys
import types

APP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# De bouwstraat draait dit op een Windows-console met codepagina 1252. Een
# teken dat daar niet in past — een pijl, een liggend streepje uit de verkeerde
# hoek — laat de hele toets omvallen met een UnicodeEncodeError, en dan lijkt
# het alsof de bediening stuk is terwijl er alleen iets niet te printen viel.
# Dat is precies één keer gebeurd; vandaar dit.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Het logboek van de bouwserver is Windows-cp1252, en dat kent lang niet alle
# tekens die hier gebruikt worden — een pijltje in een boodschap liet de hele
# toets omvallen met een UnicodeEncodeError, en daarmee de bouw. De uitvoer
# gaat daarom expliciet in UTF-8, en wat er dan nog niet in kan wordt vervangen
# in plaats van dat het de boel tegenhoudt. Een toets hoort om te vallen over
# wat hij toetst, niet over hoe hij dat opschrijft.
for _stroom in (sys.stdout, sys.stderr):
    try:
        _stroom.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


from PyQt5.QtCore import Qt                                     # noqa: E402
from PyQt5.QtWidgets import QApplication, QPushButton           # noqa: E402

import merk                                                     # noqa: E402
import proefvenster                                             # noqa: E402
import bediening                                                # noqa: E402
import filters as _filters                                      # noqa: E402

# De Surface Pro 7: 2736 x 1824 fysiek. Op 200% rekent Qt in 1368 x 912 punten.
FYSIEK_B, FYSIEK_H = 2736, 1824

fouten = []


def eis(voorwaarde, boodschap):
    if voorwaarde:
        print(f"  ok    {boodschap}", flush=True)
    else:
        print(f"  FOUT  {boodschap}", flush=True)
        fouten.append(boodschap)


def onderdeel(naam, doen, *args):
    try:
        return doen(*args)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"  FOUT  {naam} klapte om: {type(exc).__name__}: {exc}", flush=True)
        fouten.append(f"{naam} klapte om: {type(exc).__name__}: {exc}")
        return None


def _gastknoppen(wortel):
    """Alle QPushButtons die zichtbaar in `wortel` staan."""
    return [k for k in wortel.findChildren(QPushButton)
            if k.isVisibleTo(wortel) and k.width() > 4 and k.height() > 4]


def _hartlijn(scherm_breed, knop, wortel):
    """Hoeveel punten staat het midden van `knop` naast het midden van het scherm?"""
    midden = knop.mapTo(wortel, knop.rect().center()).x()
    return abs(midden - scherm_breed / 2.0)


# ── 1. de balk zelf ────────────────────────────────────────────────────────
def toets_balk(breedte):
    print("\nDe gastbalk", flush=True)

    hoofd = bediening.zet_hoofdknop(QPushButton("Volgende foto maken"))
    links = bediening.zet_zijknop(QPushButton("Foto opnieuw nemen"))
    rechts = bediening.zet_zijknop(QPushButton("Stoppen"))
    balk = bediening.gastbalk(hoofd=hoofd, links=links, rechts=rechts)
    balk.setFixedWidth(breedte)
    balk.show()
    QApplication.processEvents()

    eis(bediening.hartlijn_afwijking(balk, hoofd) <= 1,
        f"de hoofdknop staat op de hartlijn "
        f"({bediening.hartlijn_afwijking(balk, hoofd):.0f} punten ernaast)")
    eis(balk.height() == bediening.BALK_HOOG,
        f"de balk is {balk.height()} punten hoog (ontwerp: {bediening.BALK_HOOG})")

    # En nu de proef die ertoe doet: dezelfde balk met ANDERE knoppen ernaast.
    # Bij een QHBoxLayout met stretches zou de hoofdknop hier meeschuiven.
    for beschrijving, l, r in (
            ("zonder knoppen ernaast", None, None),
            ("alleen links", bediening.zet_zijknop(QPushButton("Nee, begin opnieuw")), None),
            ("een heel lange tekst links",
             bediening.zet_zijknop(QPushButton("Nee, begin helemaal opnieuw alsjeblieft")),
             None),
            ("alleen rechts", None, bediening.zet_zijknop(QPushButton("E-mail")))):
        h = bediening.zet_hoofdknop(QPushButton("Klaar"))
        b = bediening.gastbalk(hoofd=h, links=l, rechts=r)
        b.setFixedWidth(breedte)
        b.show()
        QApplication.processEvents()
        eis(bediening.hartlijn_afwijking(b, h) <= 1,
            f"de hoofdknop blijft op de hartlijn — {beschrijving}")


# ── 2. de echte schermen ───────────────────────────────────────────────────
def toets_tussenruimte():
    """De afstand die "Sessie stoppen" van "Ja" weghoudt.

    Dit was de STILLE KNOPSTIJL: geen rand, gedempte letter, zodat niemand die
    knop voor de bevestiging zou aanzien. Die stijl is eraf — de twee buitenste
    knoppen van een gastscherm horen er hetzelfde uit te zien — en daarmee is
    deze afstand het enige wat de verwisseling nog tegenhoudt. Vandaar dat hij
    hier apart wordt vastgelegd en niet alleen indirect uit de plaatsing volgt.

    Veertig punten is op de Surface Pro 7 zeven komma zes millimeter tussen de
    randen van de twee knoppen. Bij de gebruikelijke 24 was het vier komma zes.
    """
    print("\nDe afstand rond de hoofdknop", flush=True)
    mm = bediening.TUSSEN * bediening.PUNT_MM
    print(f"        {bediening.TUSSEN} punten = {mm:.1f} mm op het glas",
          flush=True)
    eis(bediening.TUSSEN >= 40,
        f"de tussenruimte staat op {bediening.TUSSEN} punten (ondergrens 40)")
    eis(mm >= 7.0,
        f"dat is {mm:.1f} mm tussen de knopranden (ondergrens 7,0)")
    eis(bediening.TUSSEN > merk.RUIMTE_RUIM,
        f"ruimer dan de gewone tussenruimte ({merk.RUIMTE_RUIM}) — het is een "
        f"bewuste afstand en niet de standaard")


def toets_schermen(pb, breedte, hoogte, dpr):
    print(f"\nDe gastschermen op {breedte}x{hoogte} punten bij {dpr:g}x", flush=True)
    import tempfile
    sessie = proefvenster.nepsessie(tempfile.mkdtemp(prefix="deelscherm-"))
    schermen = proefvenster.gastschermen(pb, breedte, hoogte, sessie=sessie)

    hoogtes = {}
    for naam, venster, widget, hoofdnaam in schermen:
        print(f"\n  — {naam}", flush=True)
        knoppen = _gastknoppen(widget)
        eis(len(knoppen) >= 2, f"er staan {len(knoppen)} gastknoppen op")

        hoofd = getattr(venster, hoofdnaam)
        afwijking = _hartlijn(breedte, hoofd, widget)
        eis(afwijking <= 1,
            f"de hoofdknop “{hoofd.text()}” staat op de hartlijn "
            f"({afwijking:.0f} punten ernaast)")

        # Geen enkele gastknop mag ver van het midden liggen: hoe verder van de
        # staander, hoe groter de hefboom waarmee de booth wegdraait. Gemeten
        # in millimeters op het glas, niet in procenten van de breedte — de
        # hefboom is een afstand, en een staande booth is smaller.
        for k in knoppen:
            mid = k.mapTo(widget, k.rect().center()).x()
            mm = abs(mid - breedte / 2.0) * bediening.PUNT_MM
            eis(mm <= bediening.MAX_UIT_MIDDEN_MM,
                f"“{k.text()}” staat {mm:.0f} mm uit het midden "
                f"(grens {bediening.MAX_UIT_MIDDEN_MM} mm)")

        # De aanraaknorm: 48 punten is 9 millimeter op dit scherm.
        for k in knoppen:
            eis(k.height() >= merk.KNOP_MIN and k.width() >= merk.KNOP_MIN,
                f"“{k.text()}” is {k.width()}x{k.height()} punten "
                f"(ondergrens {merk.KNOP_MIN})")

        # Tussen de hoofdknop en zijn buren moet genoeg lucht zitten. Rechts
        # van "Ja" staat "Sessie stoppen", en dat is de enige gastknop die iets
        # weggooit wat de gast wilde — de print. Naast een knop die je juist wél
        # wil raken is dat de gevaarlijke verwisseling.
        for k in knoppen:
            if k is hoofd:
                continue
            hl = hoofd.mapTo(widget, hoofd.rect().topLeft()).x()
            hr = hl + hoofd.width()
            kl = k.mapTo(widget, k.rect().topLeft()).x()
            kr = kl + k.width()
            gat = kl - hr if kl > hr else hl - kr
            eis(gat * bediening.PUNT_MM >= 7.0,
                f"tussen “{hoofd.text()}” en “{k.text()}” zit "
                f"{gat * bediening.PUNT_MM:.1f} mm (ondergrens 7,0)")

        # DE TWEE BUITENSTE KNOPPEN ZIEN ER HETZELFDE UIT.
        #
        # "Sessie stoppen" stond op de stilste knopstijl — geen rand, gedempte
        # letter — zodat niemand hem voor "Ja" zou aanzien. Naast een omlijnde
        # knop las dat als een knop die half uitgeschakeld was, en de klacht was
        # dat de rechterkant er raar uitzag. De bescherming zit nu in de
        # AFSTAND (zie hieronder); alleen de middelste knop mag eruit springen.
        zij = [k for k in knoppen if k is not hoofd]
        if len(zij) == 2:
            eis(zij[0].styleSheet() == zij[1].styleSheet(),
                f"“{zij[0].text()}” en “{zij[1].text()}” hebben dezelfde "
                f"omlijning")
            eis(zij[0].height() == zij[1].height(),
                f"en dezelfde hoogte ({zij[0].height()} punten)")
            eis(hoofd.styleSheet() != zij[0].styleSheet(),
                f"terwijl “{hoofd.text()}” er wél uit springt")

        # En laag. De duim komt van onderen.
        onder = hoofd.mapTo(venster, hoofd.rect().bottomLeft()).y()
        hoogtes[naam] = hoofd.mapTo(venster, hoofd.rect().center()).y()
        eis(onder >= hoogte * 0.8,
            f"de hoofdknop eindigt op {onder / hoogte * 100:.0f}% van de hoogte "
            f"(moet voorbij 80%)")

    # De vier schermen komen na elkaar. De hoofdknop hoort op alle vier op
    # dezelfde hoogte te staan, anders moet de duim tussendoor verhuizen.
    spreiding = max(hoogtes.values()) - min(hoogtes.values())
    eis(spreiding <= 2,
        f"de hoofdknop staat op alle schermen op dezelfde hoogte "
        f"({spreiding} punten verschil)")

    # De rij kleurstalen: zestien tegels die samen binnen het scherm passen en
    # elk boven de aanraaknorm blijven. Ze zijn hierboven gebouwd terwijl het
    # filterscherm nog nooit vooraan had gestaan — de toestand van de eerste
    # foto van de eerste sessie. Werd de maat aan de balk gevraagd in plaats van
    # aan het scherm, dan kwam daar 640 punten uit en werden het acht tegels van
    # 65 in plaats van zestien van 74. Dat is dezelfde fout als de miniaturen
    # van beta.5 en de collage van beta.8, en hij hoort niet nog een keer te
    # gebeuren.
    filterv = [s for s in schermen if s[0] == "filterscherm"][0][1]
    tegels = list(filterv._filter_thumb_btns.values())
    eis(len(tegels) == len(_filters.FILTERS),
        f"er staat een tegel per filter ({len(tegels)})")
    if tegels:
        # De maat die de tegels WERKELIJK hebben gekregen, niet een die hier
        # opnieuw uitgerekend wordt — dat laatste zou de fout juist verbergen,
        # want op het moment van deze regel staat de pagina wél vooraan en komt
        # er dus een goed getal uit.
        gebouwd_b = max(t.width() for t in tegels)
        raster = filterv._filter_thumbs_layout
        kolommen = max(raster.getItemPosition(i)[1] for i in range(raster.count())) + 1
        rijen = max(raster.getItemPosition(i)[0] for i in range(raster.count())) + 1
        bedoeld_k, bedoeld_b, _sb, _sh = filterv._filterstaal_maat()

        eis(gebouwd_b == bedoeld_b and kolommen == bedoeld_k,
            f"de tegels zijn op de SCHERMbreedte gebouwd en niet op de maat die "
            f"een nooit-getoonde pagina toevallig heeft "
            f"({rijen}x{kolommen} van {gebouwd_b} punten; hoort "
            f"{(len(tegels) + bedoeld_k - 1) // bedoeld_k}x{bedoeld_k} van "
            f"{bedoeld_b} te zijn)")

        nodig = kolommen * gebouwd_b + (kolommen - 1) * merk.RUIMTE_KRAP \
            + 2 * merk.RUIMTE_KANTLIJN
        eis(nodig <= breedte,
            f"de rij past op het scherm ({nodig} van {breedte} punten)")
        eis(min(t.width() for t in tegels) >= merk.KNOP_MIN
            and min(t.height() for t in tegels) >= merk.KNOP_MIN,
            f"elke tegel haalt de aanraaknorm "
            f"({min(t.width() for t in tegels)}x"
            f"{min(t.height() for t in tegels)} punten, ondergrens {merk.KNOP_MIN})")
        eis(rijen <= 2, f"het past in hoogstens twee rijen ({rijen})")

    # ── Het deelscherm: het beeld is het onderwerp ─────────────────────────
    #
    # De klacht op de echte booth was dat dit scherm de administratie toonde in
    # plaats van het resultaat: een klein strookje, een grote witte QR-kaart en
    # een statusmelding. Dat is hier meetbaar gemaakt.
    print("\n  — het beeld is het onderwerp", flush=True)
    deel = [s for s in schermen if s[0] == "deelscherm"][0][1]
    band = deel._review_panel_stack.height()
    foto = deel._review_photo_container.height()
    print(f"        de band is {band} punten hoog, het beeld houdt er {foto} over",
          flush=True)
    eis(foto > 2 * band,
        f"het beeld krijgt meer dan het dubbele van de band ({foto} tegen {band})")

    strook = deel.review_strip_label.pixmap()
    eis(strook is not None and not strook.isNull(), "de fotostrook staat er")
    if strook is not None and not strook.isNull():
        vlak_strook = strook.width() * strook.height()
        vlak_qr = deel._inline_qr_box.width() * deel._inline_qr_box.height()
        print(f"        strook {strook.width()}x{strook.height()}, "
              f"QR-kaartje {deel._inline_qr_box.width()}x"
              f"{deel._inline_qr_box.height()}", flush=True)
        vlak_boem = (deel._boomerang_kaart.width()
                     * deel._boomerang_kaart.height())
        eis(strook.height() >= hoogte * 0.55,
            f"de strook vult minstens 55% van de schermhoogte "
            f"({strook.height() / hoogte * 100:.0f}%)")
        eis(vlak_strook > 2 * vlak_boem and vlak_strook > 4 * vlak_qr,
            f"de strook is het grootste ding op het scherm — strook "
            f"{vlak_strook // 1000}k, boemerang {vlak_boem // 1000}k, "
            f"QR {vlak_qr // 1000}k punten")
        eis(strook.width() > deel._boomerang_kaart.width(),
            f"en breder dan de boemerang ({strook.width()} tegen "
            f"{deel._boomerang_kaart.width()})")

    # De boemerang beweegt echt, en hij zit in de kolom naast de strook.
    film = getattr(deel, '_boomerang_movie', None)
    eis(film is not None and film.state() == film.Running,
        "de boemerang speelt af" + ("" if film is not None else " (geen QMovie)"))
    eis(not deel._boomerang_kaart.isHidden(), "de boemerang staat in beeld")
    if film is not None:
        eis(film.frameCount() > 2,
            f"het is een bewegend beeldje en geen stilstaand ({film.frameCount()} beeldjes)")

    # De QR hoort pas op het deelscherm. Op "zijn de foto's goed gelukt?" kan de
    # gast nog opnieuw beginnen; een code naar foto's die hij misschien weggooit
    # hoort daar niet.
    for naam, venster, _w, _h in schermen:
        if naam in ("zijn-de-fotos-goed-gelukt", "wil-je-ze-geprint"):
            eis(venster._review_qr_groep.isHidden(),
                f"de QR staat niet op “{naam}”")
    eis(not deel._review_qr_groep.isHidden(), "de QR staat wél op het deelscherm")

    # ── De balk verspringt niet als de printer aan het afkoelen is ─────────
    print("\n  — de balk tijdens het printen", flush=True)
    voor = deel._sharing_done_btn.mapTo(deel, deel._sharing_done_btn.rect().center())
    voor_maat = (deel._sharing_done_btn.width(), deel._sharing_done_btn.height())
    deel._zet_printstand(True)
    QApplication.processEvents()
    na = deel._sharing_done_btn.mapTo(deel, deel._sharing_done_btn.rect().center())
    na_maat = (deel._sharing_done_btn.width(), deel._sharing_done_btn.height())
    zichtbaar = deel._sharing_links_vak.currentWidget().text()
    eis(voor == na and voor_maat == na_maat,
        f"“Klaar” blijft staan waar hij staat als de printknoppen wisselen "
        f"({voor.x()}x{voor.y()} en daarna {na.x()}x{na.y()})")
    eis("nnuleer" in zichtbaar,
        f"links staat nu “{zichtbaar}” in plaats van “Printen”")
    deel._zet_printstand(False)
    QApplication.processEvents()
    eis(deel._sharing_links_vak.currentWidget() is deel._sharing_print_btn,
        "en daarna weer “Printen”")
    return schermen


# ── 3. de kleurstalen ──────────────────────────────────────────────────────
def toets_stalen():
    print("\nDe kleurstalen", flush=True)
    b, h = 128, 85
    eerste = _filters.stalen(b, h)
    eis(len(eerste) == len(_filters.FILTERS),
        f"er is een staal per filter ({len(eerste)} van {len(_filters.FILTERS)})")
    eis(all(im.size == (b, h) for _f, _l, im in eerste),
        "elk staal heeft de gevraagde maat")
    eis(all(im.mode == "RGBA" for _f, _l, im in eerste),
        "elk staal heeft ronde hoeken (RGBA met masker)")

    tweede = _filters.stalen(b, h)
    eis(eerste is tweede,
        "een tweede aanroep komt uit de kast en rekent niets opnieuw")
    eis(_filters.stalen(b + 1, h) is not eerste,
        "een andere maat levert een ander stel op (en geen verkeerde uit de kast)")

    # Waar het om gaat: de gast moet ze uit elkaar kunnen houden. Twee stalen
    # die op minder dan een handvol punten verschillen zijn twee dezelfde
    # plaatjes met een ander bijschrift.
    def kern(im):
        klein = im.convert("RGB").resize((6, 4))
        return list(klein.getdata())

    kernen = {fid: kern(im) for fid, _l, im in eerste}
    gelijk = []
    for i, (a, ka) in enumerate(kernen.items()):
        for bfid, kb in list(kernen.items())[i + 1:]:
            afstand = max(max(abs(p - q) for p, q in zip(pa, pb))
                          for pa, pb in zip(ka, kb))
            if afstand < 8:
                gelijk.append(f"{a}/{bfid} ({afstand})")
    eis(not gelijk,
        f"alle zestien stalen zijn van elkaar te onderscheiden"
        + (f" — te dicht bij elkaar: {', '.join(gelijk)}" if gelijk else ""))


# ── 4. wat het kost ────────────────────────────────────────────────────────
def toets_kosten():
    """Meten, niet gissen. De opdrachtgever ziet dat het maken van de foto
    trager wordt van al die voorbeeldplaatjes."""
    print("\nWat het filterscherm kost", flush=True)
    import time
    from PIL import Image, ImageDraw, ImageOps

    # Een opname zoals de camera hem levert: 20 megapixel, als JPEG op schijf,
    # want juist het uitpakken daarvan is het dure deel.
    import tempfile
    bron = Image.new("RGB", (5472, 3648))
    d = ImageDraw.Draw(bron)
    for i in range(0, 3648, 24):
        d.rectangle([0, i, 5472, i + 24],
                    fill=((i * 7) % 256, (i * 13) % 256, (i * 29) % 256))
    pad = os.path.join(tempfile.mkdtemp(prefix="filtermeting-"), "opname.jpg")
    bron.save(pad, "JPEG", quality=92)

    def meet(fn, n=5):
        fn()
        t = []
        for _ in range(n):
            t0 = time.perf_counter()
            fn()
            t.append((time.perf_counter() - t0) * 1000)
        t.sort()
        return t[len(t) // 2]

    def _rond(im, straal):
        im = im.convert("RGBA")
        w, h = im.size
        masker = Image.new("L", (w, h), 0)
        ImageDraw.Draw(masker).rounded_rectangle([0, 0, w - 1, h - 1],
                                                 radius=straal, fill=255)
        im.putalpha(masker)
        return im

    def oud():
        """Zoals het tot beta.6 ging: alles uit de foto, per foto opnieuw."""
        with Image.open(pad) as raw:
            img = ImageOps.exif_transpose(raw).convert("RGB")
        basis = img
        basis.thumbnail((900, 900), Image.LANCZOS)
        _filters.apply_filter(basis, "origineel")
        tbox = ImageOps.fit(basis, (128, 80), Image.LANCZOS)
        for fid, _label in _filters.FILTERS:
            _rond(_filters.apply_filter(tbox, fid), 11)

    def nieuw():
        """Zoals het nu gaat: alleen de grote voorbeeldfoto, met draft()."""
        with Image.open(pad) as raw:
            raw.draft("RGB", (1800, 1800))
            img = ImageOps.exif_transpose(raw).convert("RGB")
        basis = img
        basis.thumbnail((900, 900), Image.LANCZOS)
        _filters.apply_filter(basis, "origineel")

    def stalen_koud():
        _filters._STAAL_CACHE.clear()
        _filters.stalen(240, 160)

    def stalen_warm():
        _filters.stalen(240, 160)

    o, n = meet(oud), meet(nieuw)
    koud, warm = meet(stalen_koud), meet(stalen_warm, 50)
    print(f"        per foto, zoals het was:      {o:7.1f} ms", flush=True)
    print(f"        per foto, zoals het nu is:    {n:7.1f} ms", flush=True)
    print(f"        de zestien stalen, eerste keer:{koud:6.1f} ms (eenmalig)", flush=True)
    print(f"        de zestien stalen, daarna:    {warm:7.3f} ms", flush=True)
    eis(n < o,
        f"het maken van een foto kost minder dan het kostte "
        f"({n:.0f} tegen {o:.0f} ms, {o / max(n, 0.001):.1f}x)")
    eis(warm < 0.5,
        f"een tweede sessie betaalt niets meer voor de stalen ({warm:.3f} ms)")


# ── 5. de erfenis van het draaiende scherm ─────────────────────────────────
def toets_erfenis():
    """De software rekende nog met een scherm dat 90 graden kon draaien.

    Dat is waarom de knoppen rechts stonden: in een gedraaide stand ís rechts
    onderin. Deze toets bewaakt dat de vertakking op het deelscherm weg blijft.
    """
    print("\nDe erfenis van het draaiende scherm", flush=True)
    bron = open(os.path.join(APP, "photobooth.py"), encoding="utf-8").read()

    def blok(naam):
        start = bron.index(f"    def {naam}(self)")
        eind = bron.index("\n    def ", start + 10)
        return bron[start:eind]

    for naam in ("_adapt_review_layout", "_position_qr_overlay"):
        eis("_is_portrait" not in blok(naam),
            f"{naam} vraagt niet meer of het scherm staat of ligt")
    eis("LeftToRight" not in blok("_adapt_review_layout"),
        "de band met de bediening kan niet meer naar de zijkant klappen")
    eis("_review_is_portrait" not in bron,
        "de dode vlag _review_is_portrait is weg")
    eis("912, 1368" not in bron,
        "de staande terugvalmaat 912x1368 staat er niet meer in")

    # En de bedrading van de nieuwe balk.
    eis("import bediening" in bron, "photobooth.py gebruikt bediening.py")
    for naam in ("_filter_next_btn", "_sharing_done_btn",
                 "_review_confirm_yes_btn", "_review_print_yes_btn"):
        eis(f"bediening.zet_hoofdknop({naam})" in bron
            or f"bediening.zet_hoofdknop(yes_btn)" in bron,
            f"{naam} of zijn bouwer gaat door bediening.zet_hoofdknop")
    eis("setFixedWidth(340)" not in bron,
        "de kolom van 340 punten aan de rechterkant van het filterscherm is weg")


def main():
    app = QApplication(sys.argv)
    try:
        import lettertype
        lettertype.laad_merkletters()
    except Exception as e:
        print(f"  let op: merkletters niet geladen ({e})", flush=True)

    dpr = float(os.environ.get("QT_SCALE_FACTOR", "1") or 1)
    breedte, hoogte = int(FYSIEK_B / dpr), int(FYSIEK_H / dpr)

    onderdeel("de erfenis", toets_erfenis)
    onderdeel("de balk", toets_balk, breedte)
    onderdeel("de tussenruimte", toets_tussenruimte)
    onderdeel("de stalen", toets_stalen)

    pb = onderdeel("photobooth importeren", proefvenster.leen_photobooth)
    if pb is not None:
        onderdeel("de schermen", toets_schermen, pb, breedte, hoogte, dpr)

    onderdeel("de kosten", toets_kosten)

    # En de staande stand. Er is geen instelling voor en geen booth waarvan we
    # het weten, maar de software leidt de stand af uit de venstermaat, dus een
    # staand gemonteerde tablet krijgt gewoon een staand scherm. Dan moet het
    # er niet uit vallen.
    if pb is not None:
        print(f"\nStaand gemonteerd ({hoogte}x{breedte})", flush=True)
        onderdeel("de schermen staand", toets_schermen, pb, hoogte, breedte, dpr)

    print("", flush=True)
    if fouten:
        print(f"BEDIENING: {len(fouten)} fout(en)", flush=True)
        for f in fouten:
            print(f"  - {f}", flush=True)
        return 1
    print("BEDIENING: alles klopt", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
