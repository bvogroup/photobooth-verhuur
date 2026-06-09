"""
Visuele uitleg voor DNP QW410 foutcodes.

Per foutcode een lijst van (image_filename, NL_tekst)-tuples. Elke stap heeft
één illustratie uit de DNP handleiding (bron: DNP QW410 User Guide v1.0.1)
plus een korte Nederlandse instructie in eigen bewoording.

Doel: klanten op locatie laten zien WAT ze moeten doen, zodat ze niet onnodig
media vervangen of de printer beschadigen.
"""

from __future__ import annotations
import os
import config


def _img(filename: str) -> str:
    """Absoluut pad naar image. Werkt in dev + PyInstaller."""
    base = getattr(config, "BUNDLE_DIR", config.BASE_DIR)
    return os.path.join(base, "resources", "dnp_help", filename)


# Per code → titel + lijst van stappen.
# Elke stap is (image_filename_of_None, "NL instructie").
DNP_HELP: dict[int, dict] = {

    # ── 1000: Klep open ────────────────────────────────────────────
    1000: {
        "title": "Klep van de printer is open",
        "steps": [
            ("step_1000_1.png",
             "Druk de bovenklep stevig dicht tot je een klik hoort."),
            (None,
             "⚠️  Let op: zorg dat je hand niet bekneld raakt tussen de klep "
             "en de printer. Raak de printkop binnenin niet aan — die blijft "
             "na het printen nog even heet."),
        ],
    },

    # ── 1010: Geen opvangbak ──────────────────────────────────────
    1010: {
        "title": "Opvangbak ontbreekt",
        "steps": [
            ("step_1010_1.png",
             "Plaats de opvangbak terug onderin de printer. "
             "Druk hem stevig aan — er mag geen ruimte tussen de bak en de "
             "printer zitten, anders werkt 'ie niet."),
            ("step_1010_2.png",
             "Tip: maak de opvangbak regelmatig leeg. Hierin worden de "
             "papier-snippers verzameld die ontstaan bij het snijden."),
            (None,
             "⚠️  Het mes bovenin de bak-opening is scherp — niet aanraken."),
        ],
    },

    # ── 1100: Papier op / Papier-fout (rol niet leeg) ─────────────
    1100: {
        "title": "Papier vervangen (alleen als de rol écht leeg is)",
        "steps": [
            ("step_1100_1.png",
             "Stap 1 — Trek de hendel naar je toe om de bovenklep te openen. "
             "Haal eerst het lint eruit (apart neerleggen)."),
            ("step_1100_2.png",
             "Stap 2 — Haal de papierhouder uit de printer. "
             "Leg 'm op een schone ondergrond, niet direct op de werkplek."),
            ("step_1100_3.png",
             "Stap 3 — Haal de oude rol van de houder en plaats de nieuwe rol. "
             "Zorg dat er géén ruimte tussen de rol en de houder zit en dat "
             "het papier niet voorbij de zijkanten uitsteekt."),
            ("step_1100_4.png",
             "Stap 4 — Plaats de papierhouder terug in de printer. "
             "Zorg dat het zegel (→PULL) in de juiste richting wijst."),
            ("step_1100_5.png",
             "Stap 5 — Verwijder het zegel, leg je hand op het papier en "
             "draai de houder rond tot je een piep hoort. Het ERROR-LED gaat "
             "van knipperend rood/oranje naar alleen rood knipperend."),
            ("step_1100_6.png",
             "Stap 6 — Plaats de opvangbak terug en sluit de klep. "
             "Drie blanco vellen komen er uit (papier-initialisatie) — dat is normaal."),
            (None,
             "⚠️  BELANGRIJK — vervang ALTIJD papier én lint samen. "
             "Stop de oude rol terug in de doos.\n"
             "Meng géén verschillende breedtes (4-inch ↔ 4,5-inch) op één "
             "printer — dat geeft strepen op de prints."),
        ],
    },

    # ── 1200: Lint op / Lint-fout (rol niet leeg) ─────────────────
    1200: {
        "title": "Lint (ribbon) vervangen (alleen als 't écht op is)",
        "steps": [
            ("step_1200_1.png",
             "Stap 1 — Trek de hendel naar je toe om de bovenklep te openen "
             "en haal het oude lint eruit."),
            ("step_1200_2.png",
             "Stap 2 — Bekijk de toevoerkant en opwindkant van het nieuwe lint. "
             "Draai de kern in de richting van de pijl om de slag eruit te halen."),
            ("step_1200_3.png",
             "Stap 3 — Plaats het lint in de printer: lijn de linkerkant uit "
             "met de groef (1), laat dan de rechterkant zakken (2). "
             "Sluit de bovenklep."),
            (None,
             "⚠️  BELANGRIJK — vervang ALTIJD lint én papier samen. "
             "Stop het oude lint terug in de doos.\n"
             "Gebruik alleen origineel DNP lint — er zit een chip in die "
             "wordt herkend door de printer."),
        ],
    },

    # ── 1300: Paper jam ───────────────────────────────────────────
    1300: {
        "title": "Papier vastgelopen",
        "steps": [
            (None,
             "Eerst voorbereiden:\n"
             "1. Open de bovenklep van de printer\n"
             "2. Haal het lint eruit"),
            ("step_1300_1.png",
             "Stap 1 — Trek het vastgelopen papier voorzichtig naar je toe "
             "(richting papierhouder). Draai de houder met je hand mee in de "
             "opwind-richting om de trekkracht te gebruiken."),
            ("step_1300_2.png",
             "Stap 2 — Trek het papiereinde naar je toe. Doe dit LANGZAAM — "
             "bij te hard trekken kan de printer beschadigen."),
            ("step_1300_3.png",
             "Stap 3 — Knip met een schaar het beschadigde stuk eraf "
             "(gekreukt of half-geprint). Het papier MOET recht zijn afgesneden, "
             "anders werkt de printer niet correct."),
            ("step_1300_4.png",
             "Lukt het papier niet naar je toe te trekken? Haal de papierhouder "
             "naar voren, knip het vastzittende deel af, en trek met een "
             "handschoen aan voorzichtig de voorkant van het papier eruit."),
            (None,
             "⚠️  Gaat het niet zonder veel kracht? STOP en bel ons.\n"
             "Geforceerd trekken kan de printer onherstelbaar beschadigen — "
             "en die kosten zijn niet onder garantie."),
        ],
    },

    # ── 1400: Lint-fout (gescheurd / slecht gespannen) ────────────
    1400: {
        "title": "Lint-fout — controleer of niet vervangen",
        "steps": [
            ("step_1400_1.png",
             "Stap 1 — Als het lint is gescheurd: open de bovenklep, trek het "
             "lint eruit en knip het door. Haal voorzichtig alle stukjes lint "
             "die nog binnenin de printer zitten weg."),
            ("step_1400_2.png",
             "Stap 2 — Plak het lint weer aan elkaar met plakband. "
             "Draai de opwindrol tot het tape-stuk niet meer zichtbaar is."),
            ("step_1400_3.png",
             "Stap 3 — Plaats het lint terug: linkerkant in de groef uitlijnen, "
             "rechterkant laten zakken, draai om de slack eruit te halen. "
             "Sluit de klep."),
            (None,
             "❌  Vervang het lint ALLEEN als het écht onherstelbaar is "
             "en er nog maar weinig prints over zijn.\n"
             "📞  Twijfel? Bel ons — vervangen kost geld."),
        ],
    },

    # ── 1500: Media size error ───────────────────────────────────
    1500: {
        "title": "Verkeerd papierformaat",
        "steps": [
            (None,
             "Het geladen papierformaat past niet bij het ontwerp.\n\n"
             "QW410 ondersteunt twee formaten:\n"
             "• 4-inch (102 mm) breed\n"
             "• 4,5-inch (114 mm) breed\n\n"
             "Controleer of:\n"
             "1. Het juiste formaat papier is geladen\n"
             "2. Het ontwerp/template overeenkomt met dit formaat\n\n"
             "Bij twijfel: bel ons."),
        ],
    },

    # ── 2500 / 2600: Head over heat ──────────────────────────────
    2500: {
        "title": "Printerkop te heet",
        "steps": [
            (None,
             "De thermische printkop is te warm geworden door intensief printen.\n\n"
             "✓ Doe niets — wacht 1 minuut.\n"
             "✓ De printer gaat vanzelf weer aan zodra de kop is afgekoeld.\n\n"
             "💡 Tip: zorg dat de printer wat ruimte heeft voor luchtcirculatie. "
             "Niet ingesloten in een kast of tegen een muur aan."),
        ],
    },

    # ── 9999: Communication error ───────────────────────────────
    9999: {
        "title": "Communicatie-fout (printer niet bereikbaar)",
        "steps": [
            (None,
             "De tablet kan niet meer met de printer praten.\n\n"
             "Probeer dit op volgorde:\n\n"
             "1. Controleer de USB-kabel — zit hij goed in beide kanten?\n"
             "2. Probeer een andere USB-poort op de tablet\n"
             "3. Zet de printer uit, wacht 10 seconden, zet hem weer aan\n"
             "4. Herstart als laatste de tablet\n\n"
             "📞  Lukt het nog steeds niet? Bel ons."),
        ],
    },
}

# Alias-codes die dezelfde uitleg krijgen
DNP_HELP[2600] = DNP_HELP[2500]


def get_help(code: int | None) -> dict | None:
    """Return help-data voor code, of None als geen uitleg beschikbaar."""
    if code is None:
        return None
    return DNP_HELP.get(code)


def steps_with_existing_images(code: int | None) -> list[tuple[str | None, str]]:
    """Return alleen stappen waarvan het image-bestand ook echt bestaat
    (of None == tekst-only stap)."""
    h = get_help(code)
    if not h:
        return []
    out = []
    for img, txt in h["steps"]:
        if img is None:
            out.append((None, txt))
            continue
        full = _img(img)
        if os.path.isfile(full):
            out.append((full, txt))
        else:
            # Image mist (build-fout) → toon alleen tekst
            out.append((None, txt))
    return out
