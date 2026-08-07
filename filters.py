"""Fotobooth-filters — PIL-gebaseerd, snel genoeg voor live preview + strip.

apply_filter(pil_img, filter_id) geeft een NIEUWE afbeelding terug; de
originele wordt nooit gemuteerd. 'origineel' geeft een kopie.

De volgorde van FILTERS bepaalt de volgorde op het filterscherm; 'origineel'
staat altijd vooraan als startkeuze.

Onderaan staat de KLEURSTAAL: het kleine monster waarop het filterscherm laat
zien wat een filter doet. Zie de uitleg daar waarom dat geen foto meer is.
"""

from PIL import Image, ImageDraw, ImageEnhance, ImageOps, ImageFilter

# (id, label) — getoond als knoppen op het filterscherm
FILTERS = [
    ("origineel", "Origineel"),
    ("zwartwit",  "Zwart-wit"),
    ("sepia",     "Sepia"),
    ("noir",      "Noir"),
    ("warm",      "Warm"),
    ("koel",      "Koel"),
    ("vintage",   "Vintage"),
    ("helder",    "Helder"),
    ("levendig",  "Levendig"),
    ("zacht",     "Zacht"),
    ("contrast",  "Contrast"),
    ("pastel",    "Pastel"),
    ("goud",      "Goud"),
    ("koud",      "Koud"),
    ("frisser",   "Frisser"),
    ("negatief",  "Negatief"),
]

FILTER_IDS = [f[0] for f in FILTERS]
FILTER_LABEL = dict(FILTERS)


def _tint(img, r_mul, g_mul, b_mul):
    """Vermenigvuldig de RGB-kanalen (snelle point-operatie)."""
    r, g, b = img.split()
    r = r.point(lambda v: min(255, int(v * r_mul)))
    g = g.point(lambda v: min(255, int(v * g_mul)))
    b = b.point(lambda v: min(255, int(v * b_mul)))
    return Image.merge("RGB", (r, g, b))


def _sepia(gray):
    return Image.merge("RGB", (
        gray.point(lambda v: min(255, int(v * 1.07))),
        gray.point(lambda v: min(255, int(v * 0.74))),
        gray.point(lambda v: min(255, int(v * 0.43))),
    ))


def apply_filter(img, fid):
    """Pas filter `fid` toe op een PIL-afbeelding. Onbekende id → origineel."""
    img = img.convert("RGB")
    if fid in (None, "", "origineel"):
        return img.copy()
    if fid == "zwartwit":
        return ImageOps.grayscale(img).convert("RGB")
    if fid == "sepia":
        return _sepia(ImageOps.grayscale(img))
    if fid == "noir":
        g = ImageOps.autocontrast(ImageOps.grayscale(img)).convert("RGB")
        g = ImageEnhance.Contrast(g).enhance(1.45)
        return ImageEnhance.Brightness(g).enhance(0.96)
    if fid == "warm":
        return _tint(img, 1.12, 1.03, 0.86)
    if fid == "koel":
        return _tint(img, 0.90, 1.00, 1.16)
    if fid == "vintage":
        out = ImageEnhance.Contrast(img).enhance(0.82)
        out = ImageEnhance.Color(out).enhance(0.70)
        out = _tint(out, 1.08, 1.00, 0.84)
        return ImageEnhance.Brightness(out).enhance(1.05)
    if fid == "helder":
        out = ImageEnhance.Brightness(img).enhance(1.18)
        return ImageEnhance.Color(out).enhance(1.10)
    if fid == "levendig":
        out = ImageEnhance.Color(img).enhance(1.65)
        return ImageEnhance.Contrast(out).enhance(1.08)
    if fid == "zacht":
        out = ImageEnhance.Contrast(img).enhance(0.88)
        out = ImageEnhance.Brightness(out).enhance(1.06)
        return out.filter(ImageFilter.GaussianBlur(1.2))
    if fid == "contrast":
        return ImageEnhance.Contrast(img).enhance(1.45)
    if fid == "pastel":
        out = ImageEnhance.Color(img).enhance(0.60)
        out = ImageEnhance.Brightness(out).enhance(1.12)
        return _tint(out, 1.05, 1.00, 1.04)
    if fid == "goud":
        out = _tint(img, 1.16, 1.05, 0.80)
        return ImageEnhance.Brightness(out).enhance(1.04)
    if fid == "koud":
        out = ImageEnhance.Color(img).enhance(0.85)
        return _tint(out, 0.82, 0.95, 1.20)
    if fid == "frisser":
        out = ImageEnhance.Color(img).enhance(1.25)
        out = ImageEnhance.Contrast(out).enhance(1.12)
        return ImageEnhance.Brightness(out).enhance(1.04)
    if fid == "negatief":
        return ImageOps.invert(img)
    return img.copy()


# ══════════════════════════════════════════════════════════════════════════
#  DE KLEURSTAAL
# ══════════════════════════════════════════════════════════════════════════
#
# Tot beta.6 kreeg elk filter op het filterscherm een eigen voorbeeldFOTO: de
# zojuist gemaakte opname, zestien keer verkleind en zestien keer bewerkt. Dat
# kostte twee dingen.
#
#   Ruimte. Zestien fotootjes van 150 x 122 punten passen alleen in twee rijen,
#   en die twee rijen aten de onderkant van het scherm op — precies de plek
#   waar de bediening hoort te staan.
#
#   Tijd. Het moest per gemaakte foto opnieuw, want de foto is elke keer een
#   andere. Zestien keer een bewerking plus zestien keer een hoekmasker, op de
#   Python-thread die de GIL deelt met de bediening, op het moment dat de gast
#   net op de knop heeft gedrukt.
#
# Een kleurstaal is geen foto en verandert dus niet mee. Hij wordt één keer
# gerekend en daarna hergebruikt: dezelfde foto, dezelfde sessie, de hele
# avond, elke avond. Wat de gast eraan afleest is precies wat een filter doet
# — warmer, koeler, valer, harder, omgekeerd — en dat is waar hij op kiest.
#
# Waarom er huid in zit: dit is een fotobooth. Het verschil tussen "warm" en
# "goud" zie je niet op een grijsverloop maar wel op een gezicht. Het monster
# bevat daarom een huidverloop van licht naar donker (de bovenste twee derde),
# een grijsramp van zwart naar wit (de onderste derde, waar contrast- en
# helderheidsfilters op afleesbaar zijn) en één verzadigde rode stip (waar
# "levendig" en "pastel" uit elkaar lopen).

_STAAL_CACHE = {}


def kleurstaal(breedte, hoogte):
    """Het onbewerkte monster waar de filters overheen gaan.

    Maten in ECHTE pixels, niet in punten: op een tablet op 200% is een tegel
    van 72 x 48 punten 144 x 96 pixels, en wie hem op 72 x 48 rekent en laat
    oprekken krijgt hem wazig terug. Dat was een van de drie fouten van
    beta.5.
    """
    breedte = max(8, int(breedte))
    hoogte = max(8, int(hoogte))
    im = Image.new("RGB", (breedte, hoogte))
    px = im.load()

    grens = max(2, int(hoogte * 0.62))
    for y in range(grens):
        t = y / max(1, grens - 1)
        for x in range(breedte):
            u = x / max(1, breedte - 1)
            px[x, y] = (int(246 - 96 * u - 40 * t),
                        int(206 - 96 * u - 44 * t),
                        int(178 - 92 * u - 42 * t))

    d = ImageDraw.Draw(im)
    for x in range(breedte):
        v = int(16 + 223 * (x / max(1, breedte - 1)))
        d.line([(x, grens), (x, hoogte)], fill=(v, v, v))

    r = max(2, int(min(breedte, hoogte) * 0.20))
    cx, cy = int(breedte * 0.74), int(grens * 0.42)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(190, 48, 44))
    return im


def _afgerond(im, straal):
    """Ronde hoeken via een alfamasker; Qt kan een pixmap niet zelf afronden."""
    im = im.convert("RGBA")
    b, h = im.size
    masker = Image.new("L", (b, h), 0)
    ImageDraw.Draw(masker).rounded_rectangle([0, 0, b - 1, h - 1],
                                             radius=straal, fill=255)
    im.putalpha(masker)
    return im


def stalen(breedte, hoogte, straal=11):
    """Alle filters als kleurstaal — één keer gerekend, daarna uit de kast.

    Geeft [(id, label, PIL-afbeelding in RGBA)] in dezelfde volgorde als
    FILTERS. De uitkomst wordt op maat bewaard, dus een tweede aanroep met
    dezelfde maat kost niets meer. Er staan er hoogstens een paar in de kast
    (één per schermmaat), dus dit groeit niet.
    """
    sleutel = (int(breedte), int(hoogte), int(straal))
    if sleutel in _STAAL_CACHE:
        return _STAAL_CACHE[sleutel]
    monster = kleurstaal(breedte, hoogte)
    uit = [(fid, label, _afgerond(apply_filter(monster, fid), straal))
           for fid, label in FILTERS]
    _STAAL_CACHE[sleutel] = uit
    return uit
