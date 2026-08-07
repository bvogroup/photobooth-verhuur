"""Toetst de uploadpoort in de afsluit-/overdrachtflow (code 2718).

Waarom dit bestaat
------------------
Tussen "ziet de print er goed uit?" en de updatecheck is er een stap bij
gekomen: staan alle foto's van dit event in de cloud? Die stap mag drie
dingen niet doen, en dat zijn precies de dingen die je pas op een echt event
merkt:

  1. vastlopen. Als het uploaden niet lukt moet de verhuurder er altijd uit
     kunnen, en dat mag niet betekenen dat hij de booth moet herstarten.
  2. twee keer hetzelfde wifi-verhaal vertellen. De poort doet zijn eigen
     wifi-check; de losse check die er al stond mag hem niet herhalen.
  3. de overslaan-knop laten meedoen met de echte knoppen. Hij hoort in de
     hoek, klein en grijs — het moet gewoon goed komen.

De schermen worden hier getekend zoals de booth ze tekent (offscreen, op de
maat van de Surface Pro), en de routering wordt met de ECHTE methodes van
PhotoboothWindow gelopen, niet met een nabootsing ervan.

Het wissen zelf staat in test_opruimen.py — dat is de gevaarlijke helft.

    python test_overdracht.py
"""

import os
import sys
import time

APP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

for _stroom in (sys.stdout, sys.stderr):
    try:
        _stroom.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from PyQt5.QtWidgets import QApplication, QPushButton, QProgressBar  # noqa: E402

import config                                                   # noqa: E402
import proefvenster                                             # noqa: E402
from test_opruimen import Booth                                 # noqa: E402

# De Surface Pro 7 in punten, net als in schermafdrukken.py.
BREED, HOOG = 1368, 912

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


# ── Een booth zonder booth ────────────────────────────────────────────

class Nepsignaal:
    """Een pyqtSignal die niets verstuurt maar onthoudt wát er verstuurd is.

    De echte signalen brengen het antwoord van een achtergrondthread naar de
    hoofdthread. Hier wordt dat handmatig gedaan, zodat de toets bepaalt
    wanneer het antwoord binnenkomt in plaats van de klok.
    """

    def __init__(self):
        self.ontvangen = []

    def emit(self, waarde):
        self.ontvangen.append(waarde)

    def wacht(self, seconden=3.0):
        klok = time.time()
        while not self.ontvangen and time.time() - klok < seconden:
            QApplication.processEvents()
            time.sleep(0.01)
        return self.ontvangen.pop(0) if self.ontvangen else None


class Nepevent:
    def __init__(self, booking_id="", token=""):
        self.linked_booking_id = booking_id
        self.linked_token = token


class Nepbooth(proefvenster.Overdrachtvenster):
    """De uploadpoort met de ECHTE methodes erin, zonder camera of cloud.

    Alles wat de poort zelf doet (kiezen, tellen, tekenen) is echt. Alleen de
    stappen ernaast — de wifi-check die er al stond, de updatecheck, het
    wissen — worden vervangen door een aantekening, zodat te zien is waar de
    poort naartoe stuurt.
    """

    ROUTERING = (
        "_handover_check_uploads", "_handover_upload_context",
        "_handover_upload_check_net", "_on_handover_upload_net",
        "_handover_start_uploads", "_handover_start_upload_timer",
        "_handover_stop_upload_timer", "_handover_upload_tick",
        "_handover_upload_vastgelopen", "_handover_skip_uploads",
        "_handover_after_uploads", "_handover_toon_uploadscherm",
    )

    def __init__(self, pb, booth, internet=True, booking=True):
        super().__init__(pb, BREED, HOOG)
        for naam in self.ROUTERING:
            setattr(self, naam, getattr(pb.PhotoboothWindow, naam).__get__(self))
        # Geen methode maar een getal: hoe lang het uploaden mag stilstaan
        # voordat het scherm naar de wifi vraagt.
        self._HANDOVER_STIL_NA = pb.PhotoboothWindow._HANDOVER_STIL_NA
        self._booth = booth
        self._internet = internet
        self.active_event = Nepevent(booth.booking_id if booking else "",
                                     "nep-token" if booking else "")
        self.backend_brand = "hippe"
        self._handover_active = True
        self._handover_upload_timer = None
        self._handover_upload_net_signal = Nepsignaal()
        self._handover_wipe_signal = Nepsignaal()
        self.gelopen = []          # welke vervolgstappen zijn aangeroepen

    # De stappen náást de poort: alleen aantekenen waar hij heen stuurt.
    def _handover_check_wifi(self):
        self.gelopen.append("wifi-check")

    def _handover_check_updates(self):
        self.gelopen.append("updatecheck")

    def _handover_start_wipe(self):
        self.gelopen.append("opruimen")

    def _handover_has_internet(self):
        return self._internet

    def _handover_open_wifi(self):
        self.gelopen.append("wifi-instellingen")

    def _get_event_photo_dir(self):
        return self._booth.fotomap


def bouw(pb, booth, internet=True, booking=True):
    v = Nepbooth(pb, booth, internet=internet, booking=booking)
    QApplication.processEvents()
    return v


def knoppen(overlay):
    """De echte knoppen uit de rij (dus zonder het hoekknopje)."""
    hoek = getattr(overlay, 'hoekknop', None)
    return [b for b in overlay.findChildren(QPushButton) if b is not hoek]


# ── Het knopje linksonder ─────────────────────────────────────────────

def toets_hoekknop(pb):
    b = Booth()
    try:
        v = bouw(pb, b)
        ov = v._handover_overlay(
            "Foto's uploaden…", subtitle="3 van 10 foto's geüpload",
            progress=(3, 10),
            corner_button=("uploaden overslaan", lambda: None))
        QApplication.processEvents()
        hoek = getattr(ov, 'hoekknop', None)
        eis(hoek is not None, "er is een overslaan-knopje")
        if hoek is None:
            return

        eis(hoek.x() < 60,
            f"het staat helemaal links (x={hoek.x()} van {BREED})")
        eis(hoek.y() > HOOG * 0.85,
            f"en helemaal onderin (y={hoek.y()} van {HOOG})")
        eis(hoek.width() < BREED * 0.25,
            f"het is smal ({hoek.width()} van {BREED} punten breed)")
        eis(hoek.height() < 44,
            f"en laag ({hoek.height()} punten hoog)")

        echte = knoppen(ov)
        eis(all("overslaan" not in k.text().lower() for k in echte),
            "overslaan staat NIET tussen de gewone knoppen")
        for k in echte:
            eis(k.height() > hoek.height() * 1.8,
                f"de knop “{k.text()}” is fors groter dan het hoekknopje")

        stijl = hoek.styleSheet()
        eis("transparent" in stijl,
            "het hoekknopje heeft geen vlak — alleen tekst")
        eis("#43434f" in stijl,
            "de tekstkleur ligt vlak bij de achtergrond (#15151b)")
        eis(hoek.font().pointSize() <= 11,
            f"kleine letter ({hoek.font().pointSize()} punten)")

        # En zonder corner_button hoort er niets te staan.
        ov2 = v._handover_overlay("Klaar", buttons=[("Doorgaan", config.COLOR_SUCCESS,
                                                     lambda: None)])
        eis(getattr(ov2, 'hoekknop', None) is None,
            "zonder overslaan-knop staat er ook geen")
    finally:
        b.sluit()


def toets_voortgangsbalk(pb):
    b = Booth()
    try:
        v = bouw(pb, b)
        ov = v._handover_overlay("Foto's uploaden…", subtitle="…",
                                 progress=(84, 132))
        QApplication.processEvents()
        balk = ov.findChild(QProgressBar)
        eis(balk is not None, "er staat een voortgangsbalk")
        if balk is None:
            return
        eis(balk.maximum() == 132 and balk.value() == 84,
            f"hij staat op 84 van 132 (nu {balk.value()}/{balk.maximum()})")
        eis(ov.subtitel_label is not None,
            "de regel eronder is bereikbaar om bij te werken")

        tekst = v._handover_upload_tekst(
            {"total": 132, "uploaded": 84, "pending": 46, "uploading": 2,
             "failed": 0, "missing": 0})
        eis("84 van 132" in tekst, f"de regel noemt x van y — “{tekst}”")
        eis("48 te gaan" in tekst, "en hoeveel er nog te gaan zijn")

        tekst = v._handover_upload_tekst(
            {"total": 10, "uploaded": 7, "pending": 1, "uploading": 0,
             "failed": 2, "missing": 0, "_fout": "Verbinding verbroken"})
        eis("2 mislukt" in tekst, "mislukte uploads worden benoemd")
        eis("Verbinding verbroken" in tekst,
            "en er staat bij WAT er misging")
    finally:
        b.sluit()


# ── De routering ──────────────────────────────────────────────────────

def toets_geen_event(pb):
    """Booth staat los → geen uploadstap, gewoon door."""
    b = Booth()
    try:
        v = bouw(pb, b, booking=False)
        v._handover_check_uploads()
        eis(v.gelopen == ["wifi-check"],
            f"zonder gekoppeld event meteen door ({v.gelopen})")
        eis(v._handover_overlay_widget is None,
            "en er komt geen uploadscherm in beeld")
    finally:
        b.sluit()


def toets_geen_fotos(pb):
    b = Booth()
    try:
        v = bouw(pb, b)
        v._handover_check_uploads()
        eis(v.gelopen == ["wifi-check"],
            f"lege wachtrij → meteen door ({v.gelopen})")
    finally:
        b.sluit()


def toets_alles_al_geuploaded(pb):
    b = Booth()
    try:
        b.foto("raw", "een.jpg")
        b.in_wachtrij("een.jpg", "uploaded")
        v = bouw(pb, b)
        v._handover_check_uploads()
        eis(v.gelopen == ["opruimen"],
            f"alles al binnen → rechtstreeks opruimen ({v.gelopen})")
        eis(v._handover_booking_id == b.booking_id,
            "de booking is vastgelegd voor ná het loskoppelen")
        eis(v._handover_fotomap == b.fotomap,
            "en de fotomap ook")
    finally:
        b.sluit()


def toets_wel_wifi(pb):
    b = Booth()
    try:
        b.in_wachtrij("een.jpg", "pending")
        b.in_wachtrij("twee.jpg", "uploaded")
        v = bouw(pb, b, internet=True)
        gestart = []
        _met_neppe_uploader(gestart)
        try:
            v._handover_check_uploads()
            eis(v._handover_overlay_widget is not None,
                "er staat een scherm terwijl de verbinding gecheckt wordt")
            ok = v._handover_upload_net_signal.wacht()
            eis(ok is True, "de wifi-check meldt verbinding")
            v._on_handover_upload_net(ok)
            eis(gestart and gestart[0][0] == b.booking_id,
                f"de uploader is gestart voor deze booking ({gestart})")
            eis(("retry", b.booking_id) in gestart,
                "en alles wat op een wachttijd stond mag meteen opnieuw")
            titel = v._handover_overlay_widget.titel_label.text()
            eis("uploaden" in titel.lower(), f"het uploadscherm staat er ({titel})")
            eis(getattr(v._handover_overlay_widget, 'hoekknop', None) is not None,
                "met het overslaan-knopje erbij")
            eis(v._handover_upload_timer is not None,
                "en de voortgang wordt ververst")
        finally:
            v._handover_stop_upload_timer()
            _herstel_uploader()
    finally:
        b.sluit()


def toets_geen_wifi(pb):
    """Geen wifi terwijl er nog foto's openstaan → eerst dát verhaal."""
    b = Booth()
    try:
        b.in_wachtrij("een.jpg", "pending")
        b.in_wachtrij("twee.jpg", "pending")
        v = bouw(pb, b, internet=False)
        v._handover_check_uploads()
        ok = v._handover_upload_net_signal.wacht()
        eis(ok is False, "de wifi-check meldt geen verbinding")
        v._on_handover_upload_net(ok)

        ov = v._handover_overlay_widget
        titel = ov.titel_label.text()
        sub = ov.subtitel_label.text()
        eis("geüpload" in titel, f"het scherm zegt dat er nog foto's op staan ({titel})")
        eis("geen wifi" in sub.lower(), "én dat er geen wifi is")
        teksten = [k.text() for k in knoppen(ov)]
        eis("Wifi instellen" in teksten,
            f"er is een knop naar de wifi-instellingen ({teksten})")
        eis("Doorgaan" in teksten, "en een knop om verder te gaan")
        eis(getattr(ov, 'hoekknop', None) is not None,
            "het overslaan-knopje staat er ook, klein in de hoek")

        # "Doorgaan" moet de verbinding opnieuw checken en dán uploaden.
        v._internet = True
        gestart = []
        _met_neppe_uploader(gestart)
        try:
            for k in knoppen(ov):
                if k.text() == "Doorgaan":
                    k.click()
            ok = v._handover_upload_net_signal.wacht()
            eis(ok is True, "na Doorgaan wordt de verbinding opnieuw gecheckt")
            v._on_handover_upload_net(ok)
            eis(gestart and gestart[0][0] == b.booking_id,
                "en dan gaat hij uploaden")
        finally:
            v._handover_stop_upload_timer()
            _herstel_uploader()
    finally:
        b.sluit()


def toets_overslaan(pb):
    """Het knopje linksonder: door naar de updatecheck, zonder te wissen."""
    b = Booth()
    try:
        b.foto("raw", "een.jpg")
        b.in_wachtrij("een.jpg", "pending")
        v = bouw(pb, b, internet=False)
        v._handover_check_uploads()
        v._on_handover_upload_net(v._handover_upload_net_signal.wacht())

        ov = v._handover_overlay_widget
        ov.hoekknop.click()
        QApplication.processEvents()

        eis(v.gelopen[-1] == "updatecheck",
            f"overslaan gaat door naar de updatecheck ({v.gelopen})")
        eis("opruimen" not in v.gelopen, "en er wordt NIETS opgeruimd")
        eis(v._handover_upload_skipped is True,
            "het overslaan wordt onthouden")
        eis(v._handover_upload_timer is None, "de verversing staat stil")
        eis(os.path.isfile(os.path.join(b.fotomap, "raw", "een.jpg")),
            "de foto staat er gewoon nog")

        # En het opruimen weigert het ook zélf nog een keer.
        import opruimen
        res = opruimen.ruim_op(b.booking_id, b.fotomap, overgeslagen=True)
        eis(res["gewist"] == 0, "opruimen weigert na een overgeslagen upload")
    finally:
        b.sluit()


def toets_geen_dubbel_wifiverhaal(pb):
    """De poort neemt de wifi-vraag over van de losse check erna."""
    b = Booth()
    try:
        b.in_wachtrij("een.jpg", "pending")
        v = bouw(pb, b, internet=False)
        v._handover_check_uploads()
        v._on_handover_upload_net(v._handover_upload_net_signal.wacht())
        eis(v._handover_wifi_gedaan is True,
            "de poort heeft het wifi-verhaal verteld")
        v._handover_after_uploads()
        eis(v.gelopen[-1] == "updatecheck",
            f"dus daarna niet nóg eens de wifi-check ({v.gelopen})")
    finally:
        b.sluit()

    b = Booth()
    try:
        b.in_wachtrij("een.jpg", "uploaded")
        v = bouw(pb, b)
        v._handover_check_uploads()          # alles binnen, geen wifi-vraag
        v._handover_after_uploads()
        eis(v.gelopen[-1] == "wifi-check",
            f"is de vraag niet gesteld, dan gaat de losse check gewoon door "
            f"({v.gelopen})")
    finally:
        b.sluit()


# ── De verversing tijdens het uploaden ────────────────────────────────

def toets_tik_klaar(pb):
    b = Booth()
    try:
        b.in_wachtrij("een.jpg", "pending")
        v = bouw(pb, b)
        v._handover_booking_id = b.booking_id
        v._handover_upload_status = {"total": 1, "uploaded": 0, "pending": 1}
        v._handover_overlay("Foto's uploaden…", subtitle="0 van 1",
                            progress=(0, 1))
        v._handover_start_upload_timer()

        v._handover_upload_tick()
        eis(v.gelopen == [], "zolang er nog iets openstaat gebeurt er niets")
        sub = v._handover_overlay_widget.subtitel_label.text()
        eis("0 van 1" in sub, f"de regel is bijgewerkt ({sub!r})")

        # Nu komt de foto binnen.
        b.manifest["files"]["een.jpg"]["state"] = "uploaded"
        b.schrijf_manifest()
        v._handover_upload_tick()
        eis(v.gelopen == ["opruimen"],
            f"alles binnen → door naar het opruimen ({v.gelopen})")
        eis(v._handover_upload_timer is None, "en de verversing stopt")
    finally:
        v._handover_stop_upload_timer()
        b.sluit()


def toets_tik_vastgelopen(pb):
    """Niets meer te proberen, maar ook niet alles binnen."""
    b = Booth()
    try:
        b.in_wachtrij("een.jpg", "uploaded")
        b.in_wachtrij("weg.jpg", "missing", fout="bestand verdwenen")
        v = bouw(pb, b)
        v._handover_booking_id = b.booking_id
        v._handover_upload_status = {"total": 2, "uploaded": 1, "missing": 1}
        v._handover_overlay("Foto's uploaden…", subtitle="1 van 2")
        v._handover_start_upload_timer()

        v._handover_upload_tick()
        eis("opruimen" not in v.gelopen, "er wordt niet opgeruimd")
        eis(v._handover_upload_timer is None, "de verversing stopt")
        ov = v._handover_overlay_widget
        eis("lukt niet" in ov.titel_label.text().lower(),
            f"het scherm zegt dat het niet lukt ({ov.titel_label.text()})")
        sub = ov.subtitel_label.text()
        eis("niet meer op de booth" in sub,
            f"en waarom niet ({sub!r})")
        eis("niets gewist" in sub.lower(),
            "met de mededeling dat er dus niets gewist wordt")
        teksten = [k.text() for k in knoppen(ov)]
        eis("Opnieuw proberen" in teksten, f"opnieuw proberen kan ({teksten})")
        eis(getattr(ov, 'hoekknop', None) is not None,
            "en de nooduitgang staat er nog steeds")
    finally:
        v._handover_stop_upload_timer()
        b.sluit()


def toets_netwerk_valt_weg(pb):
    """Halverwege het uploaden de wifi kwijt: het scherm moet het zeggen."""
    b = Booth()
    try:
        b.in_wachtrij("een.jpg", "uploaded")
        b.in_wachtrij("twee.jpg", "pending", fout="Verbinding verbroken")
        b.in_wachtrij("drie.jpg", "pending", fout="Verbinding verbroken")
        v = bouw(pb, b)
        v._handover_booking_id = b.booking_id
        v._handover_token = "nep-token"
        v._handover_upload_status = {"total": 3, "uploaded": 1, "pending": 2}
        gestart = []
        _met_neppe_uploader(gestart)
        try:
            v._handover_start_uploads()
            v._handover_upload_tick()
            eis(v._handover_upload_stil is False,
                "meteen na de start staat er nog niets stil")

            # Doe alsof de teller al een minuut niet meer beweegt.
            v._handover_upload_stil_sinds -= (v._HANDOVER_STIL_NA + 5)
            v._handover_upload_tick()
            ov = v._handover_overlay_widget
            sub = ov.subtitel_label.text()
            eis(v._handover_upload_stil is True, "de stilstand wordt gezien")
            eis("wifi" in sub.lower(),
                f"het scherm vraagt naar de wifi ({sub!r})")
            teksten = [k.text() for k in knoppen(ov)]
            eis("Wifi instellen" in teksten,
                f"en er staat een knop naar de wifi-instellingen ({teksten})")
            eis(getattr(ov, 'hoekknop', None) is not None,
                "de nooduitgang blijft staan")
            eis("opruimen" not in v.gelopen, "er wordt niets opgeruimd")

            # Komt er weer een foto binnen, dan is het gewoon weer aan het werk.
            b.manifest["files"]["twee.jpg"]["state"] = "uploaded"
            b.schrijf_manifest()
            v._handover_upload_tick()
            eis(v._handover_upload_stil is False,
                "zodra de teller weer schuift is de melding weg")
        finally:
            v._handover_stop_upload_timer()
            _herstel_uploader()
    finally:
        b.sluit()


def toets_afbreken_stopt_de_tik(pb):
    """De flow verlaten mag geen timer laten doortikken."""
    b = Booth()
    try:
        b.in_wachtrij("een.jpg", "pending")
        v = bouw(pb, b)
        v._handover_booking_id = b.booking_id
        v._handover_start_upload_timer()
        eis(v._handover_upload_timer is not None, "de verversing loopt")
        v._handover_active = False
        v._handover_upload_tick()
        eis(v._handover_upload_timer is None,
            "flow verlaten → de verversing stopt zichzelf")
    finally:
        v._handover_stop_upload_timer()
        b.sluit()


# ── Uploader-vervangers ───────────────────────────────────────────────
#
# _handover_start_uploads haalt start_worker en force_retry_all bij aanroep op
# uit cloud_uploader, dus ze zijn daar te vervangen. Er wordt hier geen echte
# uploadthread gestart: die zou het netwerk op gaan.

_echt = {}


def _met_neppe_uploader(logboek):
    import cloud_uploader
    _echt["start"] = cloud_uploader.start_worker
    _echt["retry"] = cloud_uploader.force_retry_all

    def nep_start(booking_id, token, brand="hippe"):
        logboek.append((booking_id, token, brand))
        return None

    def nep_retry(booking_id):
        logboek.append(("retry", booking_id))
        return {"failed_reset": 0, "pending_reset": 0}

    cloud_uploader.start_worker = nep_start
    cloud_uploader.force_retry_all = nep_retry


def _herstel_uploader():
    import cloud_uploader
    if "start" in _echt:
        cloud_uploader.start_worker = _echt.pop("start")
    if "retry" in _echt:
        cloud_uploader.force_retry_all = _echt.pop("retry")


def main():
    print("OVERDRACHT", flush=True)
    app = QApplication(sys.argv)                                # noqa: F841
    pb = proefvenster.leen_photobooth()

    onderdeel("het overslaan-knopje", toets_hoekknop, pb)
    onderdeel("de voortgang", toets_voortgangsbalk, pb)
    onderdeel("geen gekoppeld event", toets_geen_event, pb)
    onderdeel("geen foto's", toets_geen_fotos, pb)
    onderdeel("alles al geüpload", toets_alles_al_geuploaded, pb)
    onderdeel("mét wifi", toets_wel_wifi, pb)
    onderdeel("zonder wifi", toets_geen_wifi, pb)
    onderdeel("overslaan", toets_overslaan, pb)
    onderdeel("geen dubbel wifi-verhaal", toets_geen_dubbel_wifiverhaal, pb)
    onderdeel("de verversing: klaar", toets_tik_klaar, pb)
    onderdeel("de verversing: vastgelopen", toets_tik_vastgelopen, pb)
    onderdeel("netwerk valt weg tijdens het uploaden", toets_netwerk_valt_weg, pb)
    onderdeel("de flow verlaten", toets_afbreken_stopt_de_tik, pb)

    print("", flush=True)
    if fouten:
        print(f"OVERDRACHT: {len(fouten)} fout(en)", flush=True)
        for f in fouten:
            print(f"  - {f}", flush=True)
        return 1
    print("OVERDRACHT: alles klopt", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
