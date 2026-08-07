"""MyBoothBox — de merkwaarden van de bediening, op één plek.

Hier staan de kleuren, de maten, de afrondingen en de letters van MyBoothBox, plus
kant-en-klare stijlen voor knoppen, kaarten en invoervelden. Elk scherm haalt zijn
opmaak hiervandaan; nergens anders hoort nog een losse kleurcode of een los getal te
staan.

Waarom dit bestand bestaat
--------------------------
Vóór dit bestand stonden er in `photobooth.py` 110 losse kleurcodes, 19 verschillende
hoekafrondingen en 28 verschillende tekstgroottes. Elk scherm was los opgelost. Dat is
wat je ziet als iets nooit als geheel is ontworpen: het oogt niet fout, het oogt naar
niemand.

De bron is `docs/MERK.md` in het MyBoothBox-project. De waarden hieronder zijn daaruit
overgenomen, niet benaderd.

Hoe je dit gebruikt
-------------------
    import merk

    knop = QPushButton("Printen")
    knop.setStyleSheet(merk.knop_hoofd())
    knop.setFont(merk.letter(merk.TEKST_KNOP, vet=True))
    knop.setMinimumHeight(merk.KNOP_HOOG)

Let op één ding bij de letter: gebruik altijd `merk.letter(...)` en zet géén
`font-size` in het stijlblad. Als beide gezet zijn wint het stijlblad, en dan komt de
tekst kleiner uit dan bedoeld — dat gebeurde op 100 plekken in deze software.
`merk.letter()` werkt met beeldpunten (dezelfde eenheid als het stijlblad), zodat de
twee niet meer uit elkaar kunnen lopen.
"""

# Let op: dit bestand importeert PyQt5 BEWUST niet bovenaan. config.py haalt zijn
# kleuren hiervandaan, en config.py wordt ook geladen door print_worker.py — een
# los proces zonder vensters. Een Qt-import bovenaan zou dat proces onnodig zwaar
# maken en op een machine zonder beeldscherm zelfs kunnen laten vastlopen. De
# enige functie die Qt nodig heeft (letter) importeert het zelf, bij aanroep.


# ══════════════════════════════════════════════════════════════════════════════
#  KLEUR
# ══════════════════════════════════════════════════════════════════════════════
#
# Alle waarden komen uit docs/MERK.md van het MyBoothBox-project. De verhoudingen
# achter elke regel zijn gemeten volgens WCAG 2.1, niet geschat. De norm is 4,5:1
# voor tekst en 3:1 voor iconen en randen.

# ── De twee kleuren waar het merk om draait ──────────────────────────────────

INKT = "#16202D"
"""De inkt van het merk. Koppen, donkere schermen, tweede-rangs knoppen.

BESLUIT 7 augustus 2026 — dit is het donker van MyBoothBox, en niet #23261F.
De omslagbeelden op de socials staan op #23261F, een groenzwart; dat is nagemeten
in public/social/omslag/facebook-omslag-1640x624.png. Toch is #16202D gekozen,
omdat docs/MERK.md en de website dát als merkinkt vastleggen en de opdrachtgever
vroeg om "de MBB branding" — dan is de vastgelegde kleur de bron, niet een kleur
die uit een beeld is gemeten. Bedenkt hij zich, dan is dit één regel.
"""

GROEN = "#94D60A"
"""Gravity-groen. De hoofdknop, actieve toestanden, kleine accenten.

Dit is een VLAK, geen letter. Er komt donkere tekst op (9,29:1), nooit witte —
wit op dit groen haalt 1,77:1 en dat leest niemand. Zie GROEN_INKT voor groen
als tekst- of randkleur.

Groen op grote vlakken is afgekeurd: geen groen scherm, geen groene band. Een
knop is een klein vlak, een scherm niet.
"""

GROEN_INKT = "#4D7800"
"""Groen als tekst, rand of icoon op een lichte ondergrond — 4,95:1.

GROEN zelf haalt op licht maar 1,67:1 en is daar dus onbruikbaar, ook als
randje. Op een donker scherm mag GROEN wél als lijn en tekst (9,29:1).
"""

# ── Licht: de opbouw- en instelschermen ──────────────────────────────────────

PAPIER = "#FAF8F4"          # warm gebroken wit — de ondergrond
PAPIER_DIEPER = "#F4F1EA"   # een trapje dieper — kaarten die zich aftekenen
WIT = "#FFFFFF"             # vlak — invoervelden, kaarten
RAND = "#E1DED6"            # scheidingen en veldranden
HOVER_VLAK = "#EBE8E0"      # waar de vinger overheen gaat

TEKST = "#4D5157"           # lopende tekst op licht — 7,53:1
TEKST_GEDEMPT = "#6B7064"   # bijschriften en hulptekst — 4,80:1
                            # (stond op #A8A9AC = 2,16:1, dus onleesbaar)

# ── Donker: de gastschermen, waar de foto's op staan ─────────────────────────
#
# Een foto komt het beste uit op donker. Deze tonen zijn afgeleid uit INKT zelf —
# zelfde tint, zelfde verzadiging, alleen lichter — zodat een donker scherm uit
# hetzelfde systeem komt als de rest en niet als los grijs aanvoelt.

INKT_VLAK = "#242F3C"       # paneel of kaart op een donker scherm
INKT_HOOG = "#2E3947"       # wat een halve laag hoger ligt
INKT_RAND = "#333E4C"       # scheidingslijn op donker

OP_DONKER = "#FFFFFF"         # tekst op donker — 16,42:1
OP_DONKER_ZACHT = "#B1B8C1"   # bijschrift op donker — 8,21:1
OP_DONKER_FIJN = "#8C939B"    # het fijnste dat nog mag — 5,29:1

# ── Toestanden ───────────────────────────────────────────────────────────────

GROEN_AAN = "#AAE653"       # groene knop onder de vinger
GROEN_IN = "#83BF00"        # groene knop ingedrukt
INKT_AAN = "#26313F"        # inktknop onder de vinger
INKT_IN = "#0B1421"         # inktknop ingedrukt

GOED = "#4D7800"            # "klaar" — werkt als tekst (4,95:1) én als vlak (5,25:1)
FOUT = "#C0392B"            # "mis" — idem (5,13:1 en 5,44:1)
FOUT_OP_DONKER = "#F17166"  # dezelfde melding op een gastscherm — 5,70:1 op INKT
                            # en 4,72:1 op INKT_VLAK. FOUT zelf haalt op donker
                            # maar 3,4:1 en is daar dus te donker.
LEI = "#4B535E"             # neutrale tweede knop op licht — wit erop 7,78:1

UIT_VLAK = "#2E3947"        # uitgeschakelde knop op donker
UIT_TEKST = "#8C939B"       # ... met deze tekst erop — 2,7:1, bewust gedempt
                            # maar wel leesbaar (stond op 2,10:1 grijs-op-grijs)


# ══════════════════════════════════════════════════════════════════════════════
#  AFRONDING
# ══════════════════════════════════════════════════════════════════════════════
#
# Vier waarden, meer niet. Er stonden er negentien, van 3 tot 40, en op één paneel
# lagen knoppen van 16, 14 en 14 onder elkaar. Dat is wat "net niet uitlijnen"
# voelt: van dichtbij zie je het niet, van een meter af oogt het rommelig.
#
# De grondmaat is --radius = 0,75rem = 12 punten, dezelfde als op de website.

RONDING_KNOP = 10           # knoppen en invoervelden
RONDING_KAART = 12          # kaarten en blokken
RONDING_VLAK = 16           # grote vlakken, beelden, de QR-kaart
RONDING_ROND = 999          # pillen en badges — genoeg om altijd rond te zijn


# ══════════════════════════════════════════════════════════════════════════════
#  MAAT — dit is een aanraakscherm
# ══════════════════════════════════════════════════════════════════════════════
#
# De tablet is een Surface Pro 7: 2736 x 1824 op 12,3 inch, dus 267 punten per
# inch. Bij de gebruikelijke vergroting van 200% is één punt uit deze code op dat
# scherm 0,19 millimeter. De richtlijn voor aanraakschermen is 9 millimeter, en dat
# is hier dus 48 punten.
#
# Er stonden 27 van de 59 knophoogtes ónder die 48. Voor iemand met een glas in
# zijn hand, in een donkere zaal, is dat te klein. Grote knoppen winnen het hier
# van elegantie; dat is geen compromis maar de opdracht.

KNOP_HOOG = 88              # de hoofdactie op een gastscherm — 16,7 mm
KNOP_NORMAAL = 64           # een gewone knop — 12,2 mm
KNOP_MIN = 48               # de ondergrens voor alles wat een gast aanraakt — 9,1 mm
KNOP_BEDIENING = 44         # alleen voor de instelschermen: die worden met een
                            # vinger aan een tafel bediend, niet met een glas erbij

RUIMTE_KRAP = 8             # tussen dingen die bij elkaar horen
RUIMTE = 16                 # de standaard
RUIMTE_RUIM = 24            # tussen groepen
RUIMTE_KANTLIJN = 32        # van de schermrand af


# ══════════════════════════════════════════════════════════════════════════════
#  LETTER
# ══════════════════════════════════════════════════════════════════════════════
#
# Twee schreeflozen, precies als op de website: Plus Jakarta Sans voor koppen en
# DM Sans voor al het andere. Ze lijken op elkaar maar verschillen net genoeg dat
# een kop gewicht krijgt zonder te schreeuwen.
#
# Welke er werkelijk gebruikt wordt, bepaalt lettertype.py bij het opstarten. Staat
# het merklettertype er niet, dan valt hij terug op Segoe UI en ziet alles er
# generiek uit — daarom worden de bestanden meegeleverd.

KOP_LETTER = "Plus Jakarta Sans"
LOPEND_LETTER = "DM Sans"

# De schaal, in beeldpunten. Zeven maten in plaats van achtentwintig.
TEKST_REUS = 96             # het aftellen: 5 - 4 - 3 - 2 - 1
TEKST_KOP = 40              # de kop van een gastscherm
TEKST_SUBKOP = 28           # een tussenkop
TEKST_KNOP = 22             # knoptekst
TEKST_LOPEND = 18           # gewone tekst
TEKST_KLEIN = 15            # bijschrift
TEKST_FIJN = 13             # het kleinste dat nog mag


def letter(punten=TEKST_LOPEND, vet=False, kop=False, spatie=None):
    """Geef een QFont in de merkletter, op maat in beeldpunten.

    Gebruik dit in plaats van QFont("DM Sans", 18) — dat laatste rekent in
    typografische punten en botst dan met de `font-size: 18px` in het stijlblad.
    Het stijlblad wint zo'n botsing altijd, waardoor tekst tot 39% kleiner uitkwam
    dan er in de code stond. setPixelSize gebruikt dezelfde eenheid als het
    stijlblad, dus die twee kunnen niet meer uit elkaar lopen.

    Zet `kop=True` voor Plus Jakarta Sans; standaard is DM Sans.
    """
    from PyQt5.QtGui import QFont  # bewust hier: zie de notitie bovenaan

    f = QFont(KOP_LETTER if kop else LOPEND_LETTER)
    f.setPixelSize(int(punten))
    if vet:
        f.setWeight(QFont.Bold)
    # Het merk zet koppen op -0,02 em. Qt-stijlbladen kennen letter-spacing niet
    # — er staan zes van die regels in photobooth.py die stilzwijgend niets doen.
    # Via de letter zelf werkt het wel. Geef `spatie` mee (in procenten, 100 =
    # normaal) voor een label dat juist uit elkaar moet staan, zoals een klein
    # kopje in hoofdletters.
    if spatie is not None:
        f.setLetterSpacing(QFont.PercentageSpacing, float(spatie))
    elif kop:
        f.setLetterSpacing(QFont.PercentageSpacing, 98.0)
    return f


# ══════════════════════════════════════════════════════════════════════════════
#  KANT-EN-KLARE STIJLEN
# ══════════════════════════════════════════════════════════════════════════════
#
# Qt-stijlbladen kennen geen variabelen en geen calc(). Daarom zijn dit functies:
# de waarden hierboven worden in Python ingevuld en als tekst afgeleverd.
#
# Wat Qt óók niet kent, en waar deze stijlen dus omheen werken:
#   box-shadow      — diepte maken we met toon en rand, zoals het merk voorschrijft
#   letter-spacing  — zit in merk.letter()
#   transition      — op een aanraakscherm is directe reactie beter


def _knop(vlak, tekst, aan, in_, ronding=RONDING_KNOP, rand=None,
          uit_vlak=UIT_VLAK, uit_tekst=UIT_TEKST):
    """De gemeenschappelijke vorm van elke knop. Niet rechtstreeks gebruiken."""
    randregel = f"border: 2px solid {rand};" if rand else "border: none;"
    return (
        f"QPushButton {{"
        f" background: {vlak}; color: {tekst}; {randregel}"
        f" border-radius: {ronding}px; padding: 12px 24px; text-align: center; }}"
        f"QPushButton:hover {{ background: {aan}; }}"
        f"QPushButton:pressed {{ background: {in_}; }}"
        f"QPushButton:disabled {{ background: {uit_vlak}; color: {uit_tekst};"
        f" border: none; }}"
    )


def knop_hoofd():
    """De hoofdactie: groen vlak, donkere letters. 9,29:1.

    Er is er één per scherm. Als alles groen is valt niets meer op — al het
    andere is een tweede knop.
    """
    return _knop(GROEN, INKT, GROEN_AAN, GROEN_IN)


def knop_tweede(op_donker=True):
    """De tweede keus: omlijnd, geen vlak. Trekt geen aandacht weg van de hoofdknop."""
    if op_donker:
        return (
            f"QPushButton {{ background: transparent; color: {OP_DONKER};"
            f" border: 2px solid {INKT_RAND}; border-radius: {RONDING_KNOP}px;"
            f" padding: 12px 24px; }}"
            f"QPushButton:hover {{ background: {INKT_VLAK}; border-color: {GROEN}; }}"
            f"QPushButton:pressed {{ background: {INKT_HOOG}; }}"
            f"QPushButton:disabled {{ background: transparent; color: {UIT_TEKST};"
            f" border-color: {INKT_VLAK}; }}"
        )
    # Op licht mag de randkleur geen GROEN zijn: die haalt daar 1,67:1 en is dus
    # niet te zien. Daar hoort GROEN_INKT.
    return (
        f"QPushButton {{ background: {WIT}; color: {INKT};"
        f" border: 2px solid {RAND}; border-radius: {RONDING_KNOP}px;"
        f" padding: 12px 24px; }}"
        f"QPushButton:hover {{ background: {HOVER_VLAK}; border-color: {GROEN_INKT}; }}"
        f"QPushButton:pressed {{ background: {PAPIER_DIEPER}; }}"
        f"QPushButton:disabled {{ background: {PAPIER_DIEPER}; color: {TEKST_GEDEMPT};"
        f" border-color: {RAND}; }}"
    )


def knop_inkt():
    """Een volle donkere knop. Voor de opbouwschermen, waar geen foto's achter staan."""
    return _knop(INKT, WIT, INKT_AAN, INKT_IN,
                 uit_vlak=PAPIER_DIEPER, uit_tekst=TEKST_GEDEMPT)


def knop_gevaar():
    """Annuleren, wissen, afbreken. Wit op rood — 5,44:1."""
    return _knop(FOUT, WIT, "#D0503F", "#A02E22",
                 uit_vlak=PAPIER_DIEPER, uit_tekst=TEKST_GEDEMPT)


def knop_stil(op_donker=True):
    """Nauwelijks een knop. Voor "terug", "overslaan" en het slotje."""
    if op_donker:
        return (
            f"QPushButton {{ background: transparent; color: {OP_DONKER_ZACHT};"
            f" border: none; border-radius: {RONDING_KNOP}px; padding: 10px 18px; }}"
            f"QPushButton:hover {{ background: {INKT_VLAK}; color: {OP_DONKER}; }}"
            f"QPushButton:pressed {{ background: {INKT_HOOG}; }}"
        )
    return (
        f"QPushButton {{ background: transparent; color: {TEKST_GEDEMPT};"
        f" border: none; border-radius: {RONDING_KNOP}px; padding: 10px 18px; }}"
        f"QPushButton:hover {{ background: {HOVER_VLAK}; color: {INKT}; }}"
        f"QPushButton:pressed {{ background: {PAPIER_DIEPER}; }}"
    )


def pagina(op_donker=False):
    """De achtergrond van een heel scherm."""
    return f"background: {INKT if op_donker else PAPIER};"


def kaart(op_donker=False, ronding=RONDING_KAART):
    """Een vlak dat zich aftekent. Met een rand, niet met een schaduw.

    Het merk schrijft dat voor: twijfel je tussen een schaduw en een rand, kies de
    rand. Dat komt hier goed uit, want Qt kent box-shadow toch niet.
    """
    if op_donker:
        return (f"background: {INKT_VLAK}; border: 1px solid {INKT_RAND};"
                f" border-radius: {ronding}px;")
    return (f"background: {WIT}; border: 1px solid {RAND};"
            f" border-radius: {ronding}px;")


def invoerveld(op_donker=False):
    """Een tekstveld. De focusrand is groen — op licht in de donkere groentint."""
    if op_donker:
        return (
            f"QLineEdit {{ background: {INKT_VLAK}; color: {OP_DONKER};"
            f" border: 2px solid {INKT_RAND}; border-radius: {RONDING_KNOP}px;"
            f" padding: 12px 16px; selection-background-color: {GROEN};"
            f" selection-color: {INKT}; }}"
            f"QLineEdit:focus {{ border-color: {GROEN}; }}"
        )
    return (
        f"QLineEdit {{ background: {WIT}; color: {INKT};"
        f" border: 2px solid {RAND}; border-radius: {RONDING_KNOP}px;"
        f" padding: 12px 16px; selection-background-color: {GROEN};"
        f" selection-color: {INKT}; }}"
        f"QLineEdit:focus {{ border-color: {GROEN_INKT}; }}"
    )


def tekst(kleur=None, op_donker=False):
    """Een label. Altijd doorzichtig, zodat het niet zijn eigen vlak meebrengt."""
    if kleur is None:
        kleur = OP_DONKER if op_donker else TEKST
    return f"color: {kleur}; background: transparent;"


def voortgangsbalk(op_donker=True):
    """De aftelbalk boven aan het deelscherm."""
    spoor = INKT_VLAK if op_donker else RAND
    return (
        f"QProgressBar {{ background: {spoor}; border: none; border-radius: 3px; }}"
        f"QProgressBar::chunk {{ background: {GROEN}; border-radius: 3px; }}"
    )
