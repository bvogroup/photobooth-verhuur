"""De foto's van de booth af halen zodra ze aantoonbaar in de cloud staan.

Waarvoor dit bestaat
--------------------
De schijf van de booth loopt vol. Zodra een event is afgerond en alle foto's
op de server staan, hoeven ze er lokaal niet meer te staan — en mógen ze er
ook niet meer staan: het zijn foto's van gasten. Wissen gaat daarom met
os.remove en NIET naar de prullenbak; anders staan ze er nog steeds.

Waarom dit een eigen bestand is
-------------------------------
Dit is de enige plek in de applicatie die foto's van een gast onherroepelijk
weggooit. Dat hoort toetsbaar te zijn zonder het hele photobooth-venster op
te tuigen, en het hoort op één plek te staan zodat de voorwaarde om te wissen
niet per ongeluk op twee plekken uit elkaar gaat lopen. Zie test_opruimen.py.

De regel
--------
Er wordt ALLEEN gewist wat aantoonbaar geüpload is. "Aantoonbaar" betekent:
de bestandsnaam staat in queue.json van deze boeking met toestand 'uploaded'.
Staat er ook maar één bestand op pending, uploading, failed of missing, dan
gaat er niets weg — ook de bestanden die wél klaar zijn niet. Half opruimen
tijdens een lopende upload is hoe je foto's kwijtraakt.

Bestanden in de fotomap die niet in de wachtrij voorkomen (foto's van vóór
de koppeling, handmatig neergezette bestanden) blijven staan en worden
geteld, niet gewist. Van die bestanden is niet te bewijzen dat ze ergens
anders bestaan.
"""

import os

import config

# Toestanden waarbij een foto nog niet veilig in de cloud staat. Eén hiervan
# in de wachtrij is genoeg om alles te laten staan.
BLOKKEERT = ("pending", "uploading", "failed", "missing")


def leesbaar(bytes_aantal) -> str:
    """1536000 -> '1,5 MB'. Voor op het scherm."""
    n = float(bytes_aantal or 0)
    for eenheid in ("B", "KB", "MB", "GB"):
        if n < 1024 or eenheid == "GB":
            if eenheid == "B":
                return f"{int(n)} B"
            return f"{n:.1f}".replace(".", ",") + f" {eenheid}"
        n /= 1024
    return f"{int(n)} B"


def mag_wissen(status: dict, overgeslagen: bool = False):
    """Mag er gewist worden? Returns (ja_of_nee, reden_als_nee).

    status is de dict van cloud_uploader.get_status().
    overgeslagen=True betekent: de verhuurder heeft het uploaden overgeslagen.
    Dan gaat er per definitie niets weg.
    """
    if overgeslagen:
        return False, "Het uploaden is overgeslagen, dus er is niets gewist."
    status = status or {}
    totaal = int(status.get("total", 0) or 0)
    if totaal <= 0:
        return False, "Er staat niets in de wachtrij van dit event."
    open_staand = {naam: int(status.get(naam, 0) or 0) for naam in BLOKKEERT}
    resterend = sum(open_staand.values())
    if resterend:
        delen = [f"{n} {naam}" for naam, n in open_staand.items() if n]
        return False, ("Nog niet alles staat in de cloud (" + ", ".join(delen)
                       + ") — er is niets gewist.")
    if int(status.get("uploaded", 0) or 0) != totaal:
        return False, ("De telling van de wachtrij klopt niet — "
                       "er is voor de zekerheid niets gewist.")
    return True, ""


def _binnen(pad: str, wortel: str) -> bool:
    """Ligt pad écht binnen wortel? Vangnet tegen een verkeerd doorgegeven map."""
    try:
        pad = os.path.realpath(pad)
        wortel = os.path.realpath(wortel)
    except Exception:
        return False
    if pad == wortel:
        return False          # de hele fotomap wissen doen we nooit
    return os.path.commonpath([pad, wortel]) == wortel


def wis_bestanden(fotomap: str, namen, wortel: str) -> dict:
    """Wis in fotomap alles waarvan de bestandsnaam in namen staat.

    Onherroepelijk (os.remove). Loopt de map recursief af, dus raw/, strips/
    en gif/ gaan in één keer mee. Lege submappen worden daarna opgeruimd;
    de eventmap zelf blijft bestaan.

    wortel is de map waar fotomap binnen MOET liggen (config.PHOTO_DIR).
    Klopt dat niet, dan gebeurt er niets — dat is een programmeerfout en geen
    reden om iets weg te gooien.

    Returns {"gewist": n, "bytes": n, "blijven": n, "fouten": [...]}.
    """
    uit = {"gewist": 0, "bytes": 0, "blijven": 0, "fouten": []}
    namen = set(namen or ())
    if not fotomap or not os.path.isdir(fotomap):
        return uit
    if not _binnen(fotomap, wortel):
        uit["fouten"].append(
            f"{fotomap} ligt niet in {wortel} — niets gewist")
        return uit
    if not namen:
        # Niets aantoonbaar geüpload → niets wissen, maar wel tellen wat
        # er blijft staan zodat het scherm eerlijk kan zijn.
        for _map, _submappen, bestanden in os.walk(fotomap):
            uit["blijven"] += len(bestanden)
        return uit

    for huidige, _submappen, bestanden in os.walk(fotomap, topdown=False):
        for naam in bestanden:
            pad = os.path.join(huidige, naam)
            if naam not in namen:
                uit["blijven"] += 1
                continue
            try:
                maat = os.path.getsize(pad)
            except OSError:
                maat = 0
            try:
                os.remove(pad)          # onherroepelijk, niet naar de prullenbak
                uit["gewist"] += 1
                uit["bytes"] += maat
            except Exception as e:
                uit["fouten"].append(f"{naam}: {e}")
                uit["blijven"] += 1
        # Lege submap weghalen; de eventmap zelf laten we staan.
        if huidige != fotomap:
            try:
                os.rmdir(huidige)
            except OSError:
                pass
    return uit


def ruim_op(booking_id: str, fotomap: str, overgeslagen: bool = False,
            wortel: str = None) -> dict:
    """De hele opruiming van één afgerond event.

    1. poortwachter: staat alles aantoonbaar in de cloud?
    2. zo ja: de geüploade foto's uit photos/<event>/ wissen
    3. en de wachtrij (pending/ + uploaded/ + manifest) van deze boeking

    Zo nee: er gebeurt NIETS en de reden komt terug in "reden".

    Returns {"gewist", "bytes", "blijven", "wachtrij", "reden", "fouten"}.
    """
    from cloud_uploader import get_status, uploaded_filenames, purge_queue

    uit = {"gewist": 0, "bytes": 0, "blijven": 0, "wachtrij": 0,
           "reden": "", "fouten": []}
    if not booking_id:
        uit["reden"] = "Er is geen gekoppeld event, dus er is niets gewist."
        return uit

    status = get_status(booking_id)
    ok, reden = mag_wissen(status, overgeslagen=overgeslagen)
    if not ok:
        uit["reden"] = reden
        print(f"[OPRUIMEN] {booking_id}: niets gewist — {reden}")
        return uit

    namen = uploaded_filenames(booking_id)
    if wortel is None:
        wortel = config.PHOTO_DIR
    res = wis_bestanden(fotomap, namen, wortel)
    uit.update({k: res[k] for k in ("gewist", "bytes", "blijven")})
    uit["fouten"].extend(res["fouten"])

    q = purge_queue(booking_id)
    uit["wachtrij"] = q["bestanden"]
    uit["bytes"] += q["bytes"]
    uit["fouten"].extend(q["fouten"])

    print(f"[OPRUIMEN] {booking_id}: {uit['gewist']} foto('s) + "
          f"{uit['wachtrij']} wachtrijbestand(en) gewist, "
          f"{leesbaar(uit['bytes'])} vrij, {uit['blijven']} blijven staan")
    return uit
