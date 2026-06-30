"""Fotobooth-filters — PIL-gebaseerd, snel genoeg voor live preview + strip.

apply_filter(pil_img, filter_id) geeft een NIEUWE afbeelding terug; de
originele wordt nooit gemuteerd. 'origineel' geeft een kopie.

De volgorde van FILTERS bepaalt de volgorde op het filterscherm; 'origineel'
staat altijd vooraan als startkeuze.
"""

from PIL import Image, ImageEnhance, ImageOps, ImageFilter

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
