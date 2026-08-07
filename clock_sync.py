"""Klokafwijking opvangen, zodat uploads ook slagen bij een scheve systeemklok.

Waarom dit bestaat
==================

Op een booth in het veld liep de systeemklok ruim acht uur achter. Elke
ondertekende R2-aanvraag draagt een tijdstempel; wijkt die meer dan ongeveer
een kwartier af van de servertijd, dan weigert Cloudflare hem:

    An error occurred (RequestTimeTooSkewed) when calling the PutObject
    operation: The difference between the request time and the server's time
    is too large.

Gevolg: geen enkele foto kwam in de cloud.


WAAROM WE DE KLOK NIET VERZETTEN
================================

De voor de hand liggende reactie is `w32tm /resync`. Dat is hier de
verkeerde weg:

- Het vraagt beheerdersrechten. Draait de booth niet verhoogd, dan mislukt
  het stil en denk je dat het is opgelost.
- Een applicatie die bij elke start de systeemklok wil verzetten is precies
  het gedrag waar virusscanners op aanslaan.
- Het repareert alleen deze machine op dit moment. Loopt de klok tijdens een
  bruiloft alsnog weg, dan sta je er weer.

Wat we in plaats daarvan doen: de afwijking METEN en er bij het ondertekenen
voor CORRIGEREN. Cloudflare stuurt in élk antwoord een `Date`-kop met zijn
eigen tijd — ook in het antwoord waarin hij je afwijst. Daar lezen we de
servertijd uit, berekenen het verschil met de machineklok, en laten boto3
ondertekenen met de gecorrigeerde tijd.

Dat werkt ongeacht hoe scheef de klok staat, heeft geen rechten nodig en
raakt Windows niet aan.


HOE DE CORRECTIE IN boto3 KOMT
==============================

Nagezocht in de geinstalleerde botocore (niet uit documentatie overgenomen):
alle ondertekenaars halen hun tijd bij één functie op.

    botocore/auth.py:430   datetime_now = get_current_datetime()
    botocore/auth.py:568   datetime_now = get_current_datetime()
    botocore/auth.py:828   datetime_now = get_current_datetime()

en die functie komt via `from botocore.compat import (..., get_current_datetime, ...)`
in de naamruimte van botocore.auth terecht. Door daar één vervanger neer te
zetten, ondertekent alles — PutObject, presigned URL's, S3Express — met de
gecorrigeerde tijd.

We vervangen bewust NIET `botocore.compat.get_current_datetime` zelf. Die
wordt ook gebruikt voor het berekenen van time-outs (botocore/endpoint.py);
daar hoort de echte machineklok te gelden, want dat zijn tijdsduren en geen
tijdstempels.
"""

import datetime
import threading
import time
from email.utils import parsedate_to_datetime

# Vanaf deze afwijking is er echt iets mis met de machine: R2 weigert rond een
# kwartier, en tijdstempels in logboek, bestandsnamen en eventadministratie
# staan dan ook scheef. Boven deze grens tonen we het aan de verhuurder.
GRENS_MELDEN_SEC = 15 * 60

# Onder deze afwijking doen we niets. Een paar seconden verschil is normaal en
# elke correctie zou alleen maar ruis toevoegen.
GRENS_CORRIGEREN_SEC = 30

_slot = threading.Lock()
_afwijking_sec = 0.0          # servertijd - machinetijd, in seconden
_afwijking_bekend = False
_patch_actief = False


# ── De machineklok, apart zodat hij te testen is ─────────────────────────

def _machinetijd() -> datetime.datetime:
    """Huidige UTC-tijd volgens deze machine, zonder tijdzone-informatie.

    Staat apart zodat een test een scheve klok kan nabootsen zonder de echte
    systeemklok te verzetten.
    """
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


_klokbron = _machinetijd


def zet_klokbron(functie):
    """Vervang de machineklok. Uitsluitend bedoeld om te testen."""
    global _klokbron
    _klokbron = functie or _machinetijd


# ── Meten ───────────────────────────────────────────────────────────────

def lees_servertijd(url: str, timeout: float = 8.0):
    """Haal de tijd van een server op uit de `Date`-kop van zijn antwoord.

    Levert (servertijd_utc, heen-en-weertijd_in_seconden) of (None, None).

    Er is geen geldig verzoek voor nodig: ook een 400 of 403 draagt een
    `Date`-kop. Dat is juist het punt — dit werkt óók als onze ondertekende
    aanvragen al worden geweigerd.
    """
    try:
        import requests
    except Exception:
        return None, None

    voor = time.time()
    try:
        antwoord = requests.get(url, timeout=timeout)
    except Exception as e:
        print(f"[KLOK] Servertijd ophalen mislukt bij {url}: {e}")
        return None, None
    na = time.time()

    kop = antwoord.headers.get("Date")
    if not kop:
        print(f"[KLOK] Antwoord van {url} bevat geen Date-kop")
        return None, None
    try:
        servertijd = parsedate_to_datetime(kop)
        if servertijd.tzinfo is not None:
            servertijd = servertijd.astimezone(datetime.timezone.utc)
            servertijd = servertijd.replace(tzinfo=None)
    except Exception as e:
        print(f"[KLOK] Date-kop onleesbaar ({kop!r}): {e}")
        return None, None
    return servertijd, (na - voor)


def meet_afwijking(url: str = "", timeout: float = 8.0):
    """Meet hoeveel de machineklok afwijkt van de server.

    Levert de afwijking in seconden (positief = de machine loopt ACHTER) of
    None als het niet lukte.

    De heen-en-weertijd wordt half meegerekend: de `Date`-kop beschrijft het
    moment waarop de server antwoordde, en dat ligt ruwweg halverwege de
    aanvraag. Zonder die correctie zou een trage verbinding als klokafwijking
    worden gelezen. Op de schaal waar het hier om gaat — uren — maakt het
    niets uit, maar het voorkomt dat we bij een gezonde klok gaan corrigeren
    op wat in werkelijkheid netwerkvertraging is.
    """
    if not url:
        url = _standaard_url()

    servertijd, rondgang = lees_servertijd(url, timeout=timeout)
    if servertijd is None:
        return None

    lokaal = _klokbron()
    afwijking = (servertijd - lokaal).total_seconds() - (rondgang or 0.0) / 2.0
    print(f"[KLOK] Servertijd {servertijd:%Y-%m-%d %H:%M:%S} UTC, "
          f"machine {lokaal:%Y-%m-%d %H:%M:%S} UTC, "
          f"afwijking {afwijking:+.0f}s (heen-en-weer {(rondgang or 0)*1000:.0f}ms)")
    return afwijking


def _standaard_url() -> str:
    """R2-endpoint uit config; dat is de server waarvan de tijd ertoe doet."""
    try:
        import config
        url = (getattr(config, "R2_ENDPOINT_URL", "") or "").strip()
        if url:
            return url
        account = (getattr(config, "R2_ACCOUNT_ID", "") or "").strip()
        if account:
            return f"https://{account}.r2.cloudflarestorage.com"
    except Exception:
        pass
    return "https://cloudflare.com"


# ── Toepassen ───────────────────────────────────────────────────────────

def _gecorrigeerde_tijd(remove_tzinfo=True):
    """Vervanger voor botocore's tijdbron bij het ondertekenen."""
    nu = _klokbron() + datetime.timedelta(seconds=_afwijking_sec)
    if not remove_tzinfo:
        return nu.replace(tzinfo=datetime.timezone.utc)
    return nu


def _zet_patch():
    """Zet de vervanger in botocore.auth. Idempotent."""
    global _patch_actief
    if _patch_actief:
        return True
    try:
        import botocore.auth
        botocore.auth.get_current_datetime = _gecorrigeerde_tijd
        _patch_actief = True
        print("[KLOK] Ondertekening van R2-aanvragen loopt nu via de "
              "gecorrigeerde tijd")
        return True
    except Exception as e:
        print(f"[KLOK] Kon de tijdcorrectie niet in botocore zetten: {e}")
        return False


def zet_afwijking(afwijking_sec: float, bewaren: bool = True):
    """Leg een gemeten afwijking vast en activeer de correctie."""
    global _afwijking_sec, _afwijking_bekend
    with _slot:
        _afwijking_sec = float(afwijking_sec or 0.0)
        _afwijking_bekend = True
    if abs(_afwijking_sec) >= GRENS_CORRIGEREN_SEC:
        _zet_patch()
    if bewaren:
        _bewaar(_afwijking_sec)


def huidige_afwijking() -> float:
    with _slot:
        return _afwijking_sec


def is_bekend() -> bool:
    with _slot:
        return _afwijking_bekend


# ── Onthouden tussen starts ─────────────────────────────────────────────
#
# Zodat de eerste upload van een sessie niet eerst hoeft te mislukken voordat
# hij goed gaat. Een booth waarvan de klok structureel scheef staat, begint
# na een herstart meteen met de laatst gemeten correctie.

def _bewaar(afwijking_sec: float):
    try:
        from booth_settings import BoothSettings
        bs = BoothSettings.load() if BoothSettings.exists() else BoothSettings()
        bs.clock_offset_seconds = float(afwijking_sec)
        bs.clock_offset_measured_at = datetime.datetime.now().isoformat(timespec="seconds")
        bs.save()
    except Exception as e:
        print(f"[KLOK] Afwijking bewaren mislukt: {e}")


_bewaarde_geprobeerd = False


def laad_bewaarde_afwijking():
    """Zet de laatst bekende afwijking alvast actief, zonder te meten.

    Bedoeld om heel vroeg bij het opstarten aan te roepen: dan is de eerste
    upload al gedekt, ook voordat de meting over het netwerk rond is.
    """
    global _bewaarde_geprobeerd
    if _bewaarde_geprobeerd:
        return None
    _bewaarde_geprobeerd = True
    try:
        from booth_settings import BoothSettings
        if not BoothSettings.exists():
            return None
        bewaard = float(getattr(BoothSettings.load(), "clock_offset_seconds", 0.0) or 0.0)
    except Exception as e:
        print(f"[KLOK] Bewaarde afwijking lezen mislukt: {e}")
        return None
    if abs(bewaard) >= GRENS_CORRIGEREN_SEC:
        global _afwijking_sec
        with _slot:
            _afwijking_sec = bewaard
        _zet_patch()
        print(f"[KLOK] Bewaarde afwijking toegepast: {bewaard:+.0f}s "
              f"(wordt zo opnieuw gemeten)")
    return bewaard


# ── Alles-in-een voor bij het opstarten ─────────────────────────────────

def synchroniseer(url: str = "", timeout: float = 8.0):
    """Meet de afwijking en activeer de correctie. Levert een verslag-dict:

        {"gemeten": bool, "afwijking_sec": float, "melden": bool,
         "tekst": str}

    'melden' is True wanneer de afwijking groot genoeg is om aan de
    verhuurder te tonen.
    """
    afwijking = meet_afwijking(url=url, timeout=timeout)
    if afwijking is None:
        bewaard = huidige_afwijking()
        return {"gemeten": False, "afwijking_sec": bewaard, "melden": False,
                "tekst": "Kon de tijd van de server niet ophalen."}

    zet_afwijking(afwijking)
    melden = abs(afwijking) >= GRENS_MELDEN_SEC
    return {"gemeten": True, "afwijking_sec": afwijking, "melden": melden,
            "tekst": omschrijf(afwijking)}


def omschrijf(afwijking_sec: float) -> str:
    """Leesbare omschrijving voor logboek en scherm."""
    seconden = abs(float(afwijking_sec))
    if seconden < GRENS_CORRIGEREN_SEC:
        return "De klok van deze booth loopt gelijk."
    richting = "achter" if afwijking_sec > 0 else "voor"
    if seconden >= 3600:
        hoeveel = f"{seconden / 3600:.1f} uur"
    elif seconden >= 60:
        hoeveel = f"{seconden / 60:.0f} minuten"
    else:
        hoeveel = f"{seconden:.0f} seconden"
    return (f"De klok van deze booth loopt {hoeveel} {richting}. "
            f"Het delen van foto's is opgevangen, maar tijden in het logboek "
            f"en in de bestandsnamen kloppen niet. Zet de tijd van Windows "
            f"goed (automatisch instellen aanzetten).")


def hermeet_na_afwijzing() -> bool:
    """Meet opnieuw nadat een upload op RequestTimeTooSkewed strandde.

    Vangt het geval op dat de klok tijdens een event wegloopt, of dat de
    bewaarde correctie niet meer klopt. Levert True als er daarna een
    correctie actief is.
    """
    print("[KLOK] Upload geweigerd op tijdsverschil — afwijking opnieuw meten")
    afwijking = meet_afwijking()
    if afwijking is None:
        return False
    zet_afwijking(afwijking)
    return abs(afwijking) >= GRENS_CORRIGEREN_SEC
