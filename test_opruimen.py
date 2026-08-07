"""Toetst het wissen van foto's na een geslaagde upload.

Waarom dit bestaat
------------------
Dit is de enige plek in de applicatie die foto's van gasten onherroepelijk
weggooit. Er is geen prullenbak om iets uit terug te halen en er is geen
tweede kopie op de booth. Eén verkeerde voorwaarde en een event is weg.

Daarom wordt hier niet getoetst dát er gewist wordt, maar vooral dat er NIET
gewist wordt zolang het niet mag: bij een lopende upload, bij een mislukte
upload, bij een verdwenen bronbestand, als de verhuurder het uploaden heeft
overgeslagen, en bij bestanden die nooit in de wachtrij hebben gestaan.

Draait zonder beeldscherm en zonder booth:

    python test_opruimen.py
"""

import json
import os
import shutil
import sys
import tempfile

APP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Zelfde reden als in test_startscherm.py: het logboek van de bouwserver is
# cp1252 en kan niet alles wat hier op het scherm komt.
for _stroom in (sys.stdout, sys.stderr):
    try:
        _stroom.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import config                                                   # noqa: E402

fouten = []


def eis(voorwaarde, boodschap):
    if voorwaarde:
        print(f"  ok    {boodschap}", flush=True)
    else:
        print(f"  FOUT  {boodschap}", flush=True)
        fouten.append(boodschap)


def onderdeel(naam, doen, *args):
    print(f"\n{naam}", flush=True)
    try:
        return doen(*args)
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"  FOUT  {naam} viel om: {e}", flush=True)
        fouten.append(f"{naam}: {e}")
        return None


# ── Een booth op schijf nabouwen ──────────────────────────────────────
#
# config.DATA_DIR wordt door cloud_uploader bij ELKE aanroep opnieuw gelezen
# (_data_root doet getattr op config), dus het volstaat om hem hier om te
# zetten naar een tijdelijke map. Er wordt niets aan de echte Documents-map
# van de gebruiker aangeraakt.

class Booth:
    """Een tijdelijke Documents/Bootharoo met een event en een wachtrij erin."""

    def __init__(self, booking_id="boeking-1", eventnaam="Testfeest"):
        self.wortel = tempfile.mkdtemp(prefix="opruimtoets_")
        self.booking_id = booking_id
        self.oude_data = config.DATA_DIR
        self.oude_fotos = config.PHOTO_DIR
        self.oude_events = config.EVENTS_DIR
        config.DATA_DIR = self.wortel
        config.PHOTO_DIR = os.path.join(self.wortel, "photos")
        config.EVENTS_DIR = os.path.join(self.wortel, "events")
        os.makedirs(config.EVENTS_DIR, exist_ok=True)
        self.fotomap = os.path.join(config.PHOTO_DIR, eventnaam)
        for sub in ("raw", "strips", "gif"):
            os.makedirs(os.path.join(self.fotomap, sub), exist_ok=True)
        self.manifest = {"files": {}}

    def sluit(self):
        config.DATA_DIR = self.oude_data
        config.PHOTO_DIR = self.oude_fotos
        config.EVENTS_DIR = self.oude_events
        shutil.rmtree(self.wortel, ignore_errors=True)

    # ── bouwstenen ────────────────────────────────────────────────────

    def foto(self, sub, naam, bytes_aantal=2048):
        """Een bestand in photos/<event>/<sub>/ zetten."""
        pad = os.path.join(self.fotomap, sub, naam)
        with open(pad, "wb") as f:
            f.write(b"\xff" * bytes_aantal)
        return pad

    def in_wachtrij(self, naam, toestand, bytes_aantal=2048, fout=""):
        """Dezelfde naam ook in de wachtrij zetten, in de gegeven toestand."""
        from cloud_uploader import queue_dir
        qd = queue_dir(self.booking_id)
        sub = "uploaded" if toestand == "uploaded" else "pending"
        pad = os.path.join(qd, sub, naam)
        with open(pad, "wb") as f:
            f.write(b"\xff" * bytes_aantal)
        self.manifest["files"][naam] = {
            "state": toestand,
            "attempts": 0,
            "last_error": fout,
            "next_retry_at": 0.0,
            "size_bytes": bytes_aantal,
            "object_key": "",
        }
        self.schrijf_manifest()
        return pad

    def schrijf_manifest(self):
        from cloud_uploader import queue_dir
        pad = os.path.join(queue_dir(self.booking_id), "queue.json")
        with open(pad, "w", encoding="utf-8") as f:
            json.dump(self.manifest, f)

    def bestanden(self):
        """Alle bestandsnamen die nog in de fotomap staan."""
        uit = []
        for _m, _s, bestanden in os.walk(self.fotomap):
            uit.extend(bestanden)
        return sorted(uit)

    def wachtrijbestanden(self):
        from cloud_uploader import queue_dir
        qd = queue_dir(self.booking_id)
        uit = []
        for sub in ("pending", "uploaded"):
            d = os.path.join(qd, sub)
            if os.path.isdir(d):
                uit.extend(os.listdir(d))
        return sorted(uit)


# ── De poortwachter ───────────────────────────────────────────────────

def toets_poortwachter():
    import opruimen

    ja, _ = opruimen.mag_wissen(
        {"total": 3, "uploaded": 3, "pending": 0, "uploading": 0,
         "failed": 0, "missing": 0})
    eis(ja, "alles geüpload → wissen mag")

    for toestand in ("pending", "uploading", "failed", "missing"):
        status = {"total": 3, "uploaded": 2, "pending": 0, "uploading": 0,
                  "failed": 0, "missing": 0}
        status[toestand] = 1
        ja, reden = opruimen.mag_wissen(status)
        eis(not ja, f"één bestand op '{toestand}' → wissen mag niet")
        eis(toestand in reden or "cloud" in reden,
            f"de reden bij '{toestand}' benoemt wat er nog openstaat")

    ja, reden = opruimen.mag_wissen(
        {"total": 3, "uploaded": 3, "pending": 0, "uploading": 0,
         "failed": 0, "missing": 0}, overgeslagen=True)
    eis(not ja, "overgeslagen → wissen mag niet, ook al is alles binnen")

    ja, _ = opruimen.mag_wissen({"total": 0, "uploaded": 0})
    eis(not ja, "lege wachtrij → niets te wissen")

    ja, _ = opruimen.mag_wissen({"total": 3, "uploaded": 2, "pending": 0,
                                 "uploading": 0, "failed": 0, "missing": 0})
    eis(not ja, "telling klopt niet (2 van 3, niets openstaand) → niet wissen")


# ── Alles geüpload: de booth gaat leeg ────────────────────────────────

def toets_alles_geuploaded():
    import opruimen

    b = Booth()
    try:
        b.foto("raw", "07-08-2026_20.10.01_1.jpg", 4096)
        b.foto("raw", "07-08-2026_20.10.01_2.jpg", 4096)
        b.foto("strips", "07-08-2026_20.10.05_strip.jpg", 8192)
        b.foto("gif", "07-08-2026_20.10.09.gif", 16384)
        for naam, maat in (("07-08-2026_20.10.01_1.jpg", 4096),
                           ("07-08-2026_20.10.01_2.jpg", 4096),
                           ("07-08-2026_20.10.05_strip.jpg", 8192),
                           ("07-08-2026_20.10.09.gif", 16384)):
            b.in_wachtrij(naam, "uploaded", maat)

        res = opruimen.ruim_op(b.booking_id, b.fotomap)
        eis(res["gewist"] == 4, f"alle vier de foto's gewist (was {res['gewist']})")
        eis(res["blijven"] == 0, "er blijft niets liggen")
        eis(b.bestanden() == [], f"fotomap is leeg (nog: {b.bestanden()})")
        eis(res["wachtrij"] == 4,
            f"de wachtrij is ook geleegd (was {res['wachtrij']})")
        eis(b.wachtrijbestanden() == [],
            f"pending/ en uploaded/ zijn leeg (nog: {b.wachtrijbestanden()})")
        eis(res["bytes"] >= 32768 * 2 - 4096,
            f"de vrijgekomen ruimte wordt geteld ({res['bytes']} bytes)")
        eis(not res["reden"], "geen reden om het te laten — er is echt gewist")

        from cloud_uploader import get_status
        eis(get_status(b.booking_id)["total"] == 0,
            "het manifest is leeg na het opruimen")
        eis(os.path.isdir(b.fotomap), "de eventmap zelf blijft bestaan")
    finally:
        b.sluit()


# ── Niet alles geüpload: er gaat NIETS weg ────────────────────────────

def toets_niet_alles_geuploaded():
    import opruimen

    for toestand in ("pending", "uploading", "failed", "missing"):
        b = Booth()
        try:
            b.foto("raw", "een.jpg")
            b.foto("raw", "twee.jpg")
            b.foto("strips", "drie.jpg")
            b.in_wachtrij("een.jpg", "uploaded")
            b.in_wachtrij("twee.jpg", "uploaded")
            b.in_wachtrij("drie.jpg", toestand)

            res = opruimen.ruim_op(b.booking_id, b.fotomap)
            eis(res["gewist"] == 0,
                f"'{toestand}' openstaand → geen enkele foto gewist")
            eis(res["wachtrij"] == 0,
                f"'{toestand}' openstaand → de wachtrij blijft ook staan")
            eis(len(b.bestanden()) == 3,
                f"'{toestand}': alle drie de bestanden staan er nog "
                f"({b.bestanden()})")
            eis(bool(res["reden"]),
                f"'{toestand}': er komt een reden terug voor het scherm")
        finally:
            b.sluit()


def toets_overgeslagen():
    import opruimen

    b = Booth()
    try:
        b.foto("raw", "een.jpg")
        b.in_wachtrij("een.jpg", "uploaded")
        res = opruimen.ruim_op(b.booking_id, b.fotomap, overgeslagen=True)
        eis(res["gewist"] == 0, "overgeslagen → geen foto gewist")
        eis(res["wachtrij"] == 0, "overgeslagen → de wachtrij blijft staan")
        eis(b.bestanden() == ["een.jpg"], "het bestand staat er nog")
        eis("overgeslagen" in res["reden"].lower(),
            "de reden benoemt dat het overgeslagen is")
    finally:
        b.sluit()


# ── Bestanden die nooit in de wachtrij stonden ────────────────────────

def toets_vreemde_bestanden():
    """Foto's van vóór de koppeling zijn nergens te bewijzen — die blijven."""
    import opruimen

    b = Booth()
    try:
        b.foto("raw", "gekoppeld.jpg")
        b.foto("raw", "van-voor-de-koppeling.jpg")
        b.foto("strips", "handmatig-neergezet.png")
        b.in_wachtrij("gekoppeld.jpg", "uploaded")

        res = opruimen.ruim_op(b.booking_id, b.fotomap)
        eis(res["gewist"] == 1, f"alleen de geüploade foto weg ({res['gewist']})")
        eis(res["blijven"] == 2, f"de andere twee blijven ({res['blijven']})")
        eis(b.bestanden() == ["handmatig-neergezet.png",
                              "van-voor-de-koppeling.jpg"],
            f"precies die twee staan er nog: {b.bestanden()}")
    finally:
        b.sluit()


def toets_andere_events_blijven():
    """De fotomap van een ANDER event mag niet meegaan."""
    import opruimen

    b = Booth()
    try:
        ander = os.path.join(config.PHOTO_DIR, "Bruiloft ergens anders", "raw")
        os.makedirs(ander, exist_ok=True)
        # Zelfde bestandsnaam als een geüploade foto van het eigen event:
        # ook dán mag hij niet weg, want hij ligt in een andere eventmap.
        with open(os.path.join(ander, "gedeeld.jpg"), "wb") as f:
            f.write(b"\x00" * 512)

        b.foto("raw", "gedeeld.jpg")
        b.in_wachtrij("gedeeld.jpg", "uploaded")
        opruimen.ruim_op(b.booking_id, b.fotomap)

        eis(os.path.isfile(os.path.join(ander, "gedeeld.jpg")),
            "de foto van het andere event staat er nog")
        eis(b.bestanden() == [], "de eigen foto is wel weg")
    finally:
        b.sluit()


def toets_wachtrij_van_ander_event_blijft():
    import opruimen
    from cloud_uploader import queue_dir, get_status

    b = Booth()
    try:
        qd = queue_dir("een-oudere-boeking")
        with open(os.path.join(qd, "pending", "oud.jpg"), "wb") as f:
            f.write(b"\x00" * 256)
        with open(os.path.join(qd, "queue.json"), "w", encoding="utf-8") as f:
            json.dump({"files": {"oud.jpg": {"state": "pending",
                                             "size_bytes": 256}}}, f)

        b.foto("raw", "nieuw.jpg")
        b.in_wachtrij("nieuw.jpg", "uploaded")
        opruimen.ruim_op(b.booking_id, b.fotomap)

        eis(os.path.isfile(os.path.join(qd, "pending", "oud.jpg")),
            "de wachtrij van het oudere event blijft onaangeroerd")
        eis(get_status("een-oudere-boeking")["total"] == 1,
            "het manifest van het oudere event blijft staan")
    finally:
        b.sluit()


# ── Vangnetten ────────────────────────────────────────────────────────

def toets_vangnetten():
    import opruimen

    b = Booth()
    try:
        b.foto("raw", "een.jpg")

        res = opruimen.wis_bestanden(config.PHOTO_DIR, {"een.jpg"},
                                     config.PHOTO_DIR)
        eis(res["gewist"] == 0 and res["fouten"],
            "de fotomap-wortel zelf wordt nooit leeggehaald")
        eis(os.path.isfile(os.path.join(b.fotomap, "raw", "een.jpg")),
            "en het bestand staat er dus nog")

        buiten = tempfile.mkdtemp(prefix="buitenom_")
        try:
            with open(os.path.join(buiten, "een.jpg"), "wb") as f:
                f.write(b"\x00")
            res = opruimen.wis_bestanden(buiten, {"een.jpg"}, config.PHOTO_DIR)
            eis(res["gewist"] == 0 and res["fouten"],
                "een map buiten photos/ wordt geweigerd")
            eis(os.path.isfile(os.path.join(buiten, "een.jpg")),
                "en dat bestand staat er nog")
        finally:
            shutil.rmtree(buiten, ignore_errors=True)

        res = opruimen.wis_bestanden(b.fotomap, set(), config.PHOTO_DIR)
        eis(res["gewist"] == 0 and res["blijven"] == 1,
            "lege namenlijst → niets wissen, wel tellen")

        res = opruimen.ruim_op("", b.fotomap)
        eis(res["gewist"] == 0 and res["reden"],
            "zonder booking gebeurt er niets")
    finally:
        b.sluit()


def toets_leesbaar():
    import opruimen
    eis(opruimen.leesbaar(0) == "0 B", "0 bytes")
    eis(opruimen.leesbaar(1536) == "1,5 KB", f"1536 → {opruimen.leesbaar(1536)}")
    eis(opruimen.leesbaar(1536 * 1024) == "1,5 MB",
        f"1,5 MB → {opruimen.leesbaar(1536 * 1024)}")
    eis(opruimen.leesbaar(3 * 1024 ** 3).endswith(" GB"), "gigabytes")


# ── De uploader-kant ──────────────────────────────────────────────────

def toets_uploader_helpers():
    from cloud_uploader import uploaded_filenames, purge_queue, last_errors, get_status

    b = Booth()
    try:
        b.in_wachtrij("klaar.jpg", "uploaded")
        b.in_wachtrij("wacht.jpg", "pending", fout="Verbinding verbroken")
        eis(uploaded_filenames(b.booking_id) == {"klaar.jpg"},
            "alleen 'uploaded' telt als bewijs")
        eis(last_errors(b.booking_id) == ["Verbinding verbroken"],
            "de laatste fout komt terug voor op het scherm")

        res = purge_queue(b.booking_id)
        eis(res["bestanden"] == 2, f"beide wachtrijbestanden weg ({res['bestanden']})")
        eis(b.wachtrijbestanden() == [], "pending/ en uploaded/ zijn leeg")
        eis(get_status(b.booking_id)["total"] == 0, "het manifest is leeg")
    finally:
        b.sluit()


def main():
    print("OPRUIMEN", flush=True)
    onderdeel("de poortwachter", toets_poortwachter)
    onderdeel("alles geüpload", toets_alles_geuploaded)
    onderdeel("niet alles geüpload", toets_niet_alles_geuploaded)
    onderdeel("uploaden overgeslagen", toets_overgeslagen)
    onderdeel("bestanden buiten de wachtrij", toets_vreemde_bestanden)
    onderdeel("andere events", toets_andere_events_blijven)
    onderdeel("wachtrij van een ander event", toets_wachtrij_van_ander_event_blijft)
    onderdeel("de vangnetten", toets_vangnetten)
    onderdeel("de maatweergave", toets_leesbaar)
    onderdeel("de uploader-hulpjes", toets_uploader_helpers)

    print("", flush=True)
    if fouten:
        print(f"OPRUIMEN: {len(fouten)} fout(en)", flush=True)
        for f in fouten:
            print(f"  - {f}", flush=True)
        return 1
    print("OPRUIMEN: alles klopt", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
