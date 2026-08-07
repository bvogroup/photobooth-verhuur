"""Belichtingskalibratie voor de webcam-opstelling.

Waarom dit bestaat
==================

De booth staat elke keer ergens anders. Een zaal met veel daglicht, een
donkere feesttent, een bruiloft waar de gordijnen halverwege de avond
dichtgaan: de automatische belichting van een webcam kiest daar niet altijd
goed op, en dan zijn de gezichten te donker of juist uitgebeten. De gast ziet
dat pas op de afdruk.

Deze module doet drie dingen, in deze volgorde:

1. METEN WAT DIT APPARAAT KAN.
   Op Windows accepteert OpenCV bijna elke cap.set() zonder klagen, ook als de
   driver er niets mee doet. Je kunt dus niet uit de code afleiden of een
   instelling werkt — dat moet je op het echte apparaat proberen. probe() zet
   een waarde, leest hem terug, kijkt of het BEELD ook echt verandert, en zet
   alles daarna terug. Wat er niet op reageert, laten we met rust.

2. GEZICHTSGEWOGEN METEN.
   Het beeldgemiddelde is de verkeerde maat. Een witte feesttent of een donkere
   achtergrond trekt dat gemiddelde alle kanten op terwijl het gezicht van de
   gast — het enige wat telt — precies goed staat. We wegen daarom de pixels op
   de gevonden gezichten veel zwaarder. Vindt de gezichtsherkenning niets, dan
   vallen we terug op het midden van het beeld, waar in een photobooth vrijwel
   altijd iemand staat.

3. GEDEMPT CORRIGEREN.
   Nooit in één klap naar de berekende waarde springen. Een meting is een
   momentopname: iemand kan net een lichte jas voor de lens houden. We zetten
   telkens een deel van de afstand (DEMPING) en begrenzen de stap. Zit de
   meting binnen de dode zone, dan doen we niets — liever niets dan onrustig
   heen en weer regelen.

De uitkomst wordt per event onthouden, zodat de tweede sessie op dezelfde
locatie meteen goed staat in plaats van weer van voren af aan te beginnen.

VEILIGHEID
==========
Alles hier is optioneel gereedschap. Elke functie vangt zijn eigen fouten af
en levert bij twijfel "niets doen" op. Een booth die midden op een bruiloft
niet meer opstart is een veel groter probleem dan een foto die een tik te
donker is, dus deze module mag nooit een sessie tegenhouden.
"""

import threading

try:
    import cv2
    import numpy as np
    _CV2 = True
except Exception:  # pragma: no cover - alleen op machines zonder cv2
    _CV2 = False


# ── Instelbare grenzen ───────────────────────────────────────────────────

# Streefwaarde voor de helderheid van een gezicht, op een schaal van 0-255.
# 128 is het neutrale midden. Iets lager dan wat "mooi" oogt op een scherm,
# omdat een print altijd donkerder uitvalt dan een beeldscherm.
DOELWAARDE = 128.0

# Dode zone: hierbinnen laten we het met rust. Zonder dode zone zou de booth
# bij elke sessie een piepklein beetje bijregelen, en dat is precies het
# nerveuze gedrag dat we niet willen.
DODE_ZONE = 12.0

# Hoeveel van de berekende afstand we per sessie daadwerkelijk zetten.
# 0.5 = de helft. Twee sessies brengen je dus op driekwart van de weg.
DEMPING = 0.5

# Harde begrenzing van één stap, in eenheden van de camera-instelling. Ook als
# de meting iets extreems oplevert, verschuift het beeld nooit meer dan dit.
MAX_STAP = 2.0

# Volgorde waarin we instellingen proberen. Belichting eerst: die verandert de
# sluitertijd en geeft de schoonste correctie. Gain verhoogt de ruis en komt
# daarom als laatste.
KANDIDAAT_INSTELLINGEN = ("exposure", "brightness", "gain")


def _prop_id(naam: str):
    """OpenCV-nummer van een instelling, of None als deze build hem niet kent."""
    tabel = {
        "exposure": "CAP_PROP_EXPOSURE",
        "brightness": "CAP_PROP_BRIGHTNESS",
        "gain": "CAP_PROP_GAIN",
        "auto_exposure": "CAP_PROP_AUTO_EXPOSURE",
    }
    if not _CV2 or naam not in tabel:
        return None
    return getattr(cv2, tabel[naam], None)


# ── Meten ────────────────────────────────────────────────────────────────

_gezichtsvinder = None
_gezichtsvinder_geprobeerd = False
_gezichtsvinder_lock = threading.Lock()


def _haal_gezichtsvinder():
    """Laad de gezichtsherkenning één keer; bij twijfel None.

    Het cascade-bestand komt uit de cv2-installatie. In een ingepakte build
    (PyInstaller) kan dat bestand ontbreken. Dat is geen ramp: zonder
    gezichtsvinder meten we het midden van het beeld, en dat is in een
    photobooth een prima benadering. Daarom wordt hier nooit een fout
    doorgegeven.
    """
    global _gezichtsvinder, _gezichtsvinder_geprobeerd
    with _gezichtsvinder_lock:
        if _gezichtsvinder_geprobeerd:
            return _gezichtsvinder
        _gezichtsvinder_geprobeerd = True
        if not _CV2:
            return None
        try:
            pad = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            vinder = cv2.CascadeClassifier(pad)
            if vinder.empty():
                print("[BELICHTING] Gezichtsherkenning niet geladen — "
                      "midden van het beeld wordt gemeten")
                return None
            _gezichtsvinder = vinder
            print("[BELICHTING] Gezichtsherkenning geladen")
        except Exception as e:
            print(f"[BELICHTING] Gezichtsherkenning niet beschikbaar ({e}) — "
                  f"midden van het beeld wordt gemeten")
        return _gezichtsvinder


def _luma(frame):
    """Helderheidskanaal van een BGR-beeld, als grijswaarden 0-255."""
    return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)


def meet_gezichtsgewogen(frame):
    """Gemiddelde helderheid, met de gezichten zwaar meegewogen.

    Levert (waarde, bron) waarbij bron 'gezicht', 'midden' of 'leeg' is.
    Bij een lege of onleesbare frame komt er (None, 'leeg') uit.

    Waarom een weging en geen harde uitsnede: alleen naar het gezicht kijken
    maakt de meting heel gevoelig voor een verkeerd gevonden vakje. Door de
    rest van het beeld licht mee te laten tellen blijft er een redelijke
    uitkomst staan als de gezichtsherkenning er een keer naast zit.
    """
    if not _CV2 or frame is None or getattr(frame, "size", 0) == 0:
        return None, "leeg"
    try:
        grijs = _luma(frame)
        h, b = grijs.shape[:2]

        gewichten = np.full(grijs.shape, 0.15, dtype=np.float32)
        bron = "midden"

        vakjes = []
        vinder = _haal_gezichtsvinder()
        if vinder is not None:
            try:
                # Verkleinen vóór de detectie: op een 4K-frame duurt de zoektocht
                # anders honderden milliseconden, en dit draait tijdens het
                # introscherm terwijl de gast staat te wachten.
                schaal = 640.0 / max(b, 1)
                if schaal < 1.0:
                    klein = cv2.resize(grijs, (int(b * schaal), int(h * schaal)),
                                       interpolation=cv2.INTER_AREA)
                else:
                    schaal, klein = 1.0, grijs
                gevonden = vinder.detectMultiScale(
                    klein, scaleFactor=1.15, minNeighbors=5, minSize=(40, 40))
                for (x, y, bw, bh) in gevonden:
                    vakjes.append((int(x / schaal), int(y / schaal),
                                   int(bw / schaal), int(bh / schaal)))
            except Exception as e:
                print(f"[BELICHTING] Gezichtsdetectie mislukt ({e}) — midden gemeten")

        if vakjes:
            bron = "gezicht"
            for (x, y, bw, bh) in vakjes:
                # Iets ruimer dan het vakje zelf: het voorhoofd en de kin
                # vallen bij deze cascade vaak net buiten de rechthoek.
                x0, y0 = max(0, x - bw // 8), max(0, y - bh // 6)
                x1, y1 = min(b, x + bw + bw // 8), min(h, y + bh + bh // 6)
                gewichten[y0:y1, x0:x1] = 1.0
        else:
            # Geen gezicht gevonden: het middenblok zwaarder wegen. In een
            # photobooth staat daar vrijwel altijd degene om wie het gaat.
            y0, y1 = int(h * 0.15), int(h * 0.80)
            x0, x1 = int(b * 0.25), int(b * 0.75)
            gewichten[y0:y1, x0:x1] = 1.0

        totaal = float(gewichten.sum())
        if totaal <= 0:
            return float(grijs.mean()), bron
        waarde = float((grijs.astype(np.float32) * gewichten).sum() / totaal)
        return waarde, bron
    except Exception as e:
        print(f"[BELICHTING] Meten mislukt: {e}")
        return None, "leeg"


# ── Reageert dit apparaat ergens op? ─────────────────────────────────────

def probeer_instellingen(lees_frame, zet_prop, lees_prop):
    """Zoek uit welke camera-instelling op dit apparaat echt iets doet.

    Argumenten zijn functies, zodat deze module niets hoeft te weten van de
    draad waarop de camera gelezen wordt:
        lees_frame()          -> laatste BGR-frame (of None)
        zet_prop(id, waarde)  -> instelling zetten
        lees_prop(id)         -> instelling teruglezen

    Levert een dict:
        {"instelling": "exposure"|"brightness"|"gain"|"none",
         "gevoeligheid": float,   # helderheidsverandering per eenheid
         "basis": float}          # de oorspronkelijke waarde

    De test is streng met opzet: een instelling telt pas als werkend wanneer
    de teruggelezen waarde MEEBEWEEGT ÉN het beeld meetbaar verandert. Alleen
    controleren of cap.set() True teruggeeft is zinloos — dat doet Windows
    ook als de driver de instelling negeert, en dan zouden we vervolgens
    urenlang aan een knop draaien die nergens op is aangesloten.
    """
    resultaat = {"instelling": "none", "gevoeligheid": 0.0, "basis": 0.0}
    if not _CV2:
        return resultaat

    for naam in KANDIDAAT_INSTELLINGEN:
        pid = _prop_id(naam)
        if pid is None:
            continue
        try:
            basis = float(lees_prop(pid))
            begin, _ = meet_gezichtsgewogen(lees_frame())
            if begin is None:
                continue

            # Een stap omhoog en een stap omlaag proberen. Sommige webcams
            # zitten al aan één kant van hun bereik vast; dan geeft de andere
            # richting alsnog uitsluitsel.
            for stap in (1.0, -1.0):
                doel = basis + stap
                zet_prop(pid, doel)
                gelezen = float(lees_prop(pid))
                na, _ = meet_gezichtsgewogen(lees_frame())
                zet_prop(pid, basis)  # altijd terugzetten

                if abs(gelezen - basis) < 1e-6:
                    continue  # driver negeert de instelling
                if na is None:
                    continue
                verschil = na - begin
                if abs(verschil) < 1.0:
                    continue  # waarde beweegt wel mee, beeld niet

                gevoeligheid = verschil / (gelezen - basis)
                print(f"[BELICHTING] '{naam}' reageert: {gevoeligheid:+.1f} "
                      f"helderheid per eenheid (basis {basis:g})")
                return {"instelling": naam,
                        "gevoeligheid": float(gevoeligheid),
                        "basis": basis}
        except Exception as e:
            print(f"[BELICHTING] Test van '{naam}' mislukt: {e}")
            continue

    print("[BELICHTING] Geen enkele instelling reageert op dit apparaat — "
          "belichting blijft ongemoeid")
    return resultaat


# ── Corrigeren ───────────────────────────────────────────────────────────

def bereken_correctie(gemeten, huidige_waarde, gevoeligheid):
    """Bereken de nieuwe instelwaarde, gedempt en begrensd.

    Levert (nieuwe_waarde, reden). Is er niets te doen, dan is nieuwe_waarde
    gelijk aan huidige_waarde en legt reden uit waarom.
    """
    if gemeten is None:
        return huidige_waarde, "niets gemeten"
    afwijking = DOELWAARDE - gemeten
    if abs(afwijking) <= DODE_ZONE:
        return huidige_waarde, (f"binnen de dode zone "
                                f"(gemeten {gemeten:.0f}, doel {DOELWAARDE:.0f})")
    if not gevoeligheid:
        return huidige_waarde, "geen werkende instelling bekend"

    ruwe_stap = afwijking / gevoeligheid
    stap = ruwe_stap * DEMPING
    # Begrenzen. Zonder deze rem zou één misgelopen meting — iemand die vlak
    # voor de lens langsloopt — de camera in één keer helemaal dichtdraaien.
    stap = max(-MAX_STAP, min(MAX_STAP, stap))
    nieuw = huidige_waarde + stap
    return nieuw, (f"gemeten {gemeten:.0f}, doel {DOELWAARDE:.0f} → "
                   f"{stap:+.2f} (ongedempt {ruwe_stap:+.2f})")
