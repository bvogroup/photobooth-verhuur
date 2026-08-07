"""De gastschermen uit photobooth.py los kunnen tekenen, zonder booth.

Waarvoor dit bestaat
--------------------
Tot beta.6 stond er in schermafdrukken.py: "Let op wat dit NIET is: het echte
scherm uit photobooth.py. Dat scherm hangt aan een camera, een printer en een
sessie, en is niet los te tekenen." Daardoor werden de afdrukken en de toetsen
gemaakt op NAGEBOUWDE schermen — en precies daarin zaten de drie fouten die
beta.5 op de booth onbruikbaar maakten.

Het blijkt wél te kunnen. photobooth.py trekt bij het importeren de camera, de
printer en de cloud mee; die worden hier vervangen door lege modules. Daarna
worden de bouwmethodes ONVERANDERD van PhotoboothWindow geleend en op een kaal
venster van de werkelijke maat aangeroepen. Wat eruit komt is dus niet een
nabootsing van het scherm maar het scherm zelf, met de echte lettertypen, de
echte stijlen en de echte maatvoering.

Wat er NIET is: een camera, dus geen livebeeld en geen gemaakte foto; een
printer, dus geen printstatus; een sessie, dus geen fotostrook. Die vakken
blijven leeg of tonen hun plaatshoudertekst. Alles wat met plaats, maat en
opmaak te maken heeft is echt.

Gebruikt door test_bediening.py en schermafdrukken.py.
"""

import os
import sys
import types

APP = os.path.dirname(os.path.abspath(__file__))
if APP not in sys.path:
    sys.path.insert(0, APP)

from PyQt5.QtCore import QRect                                   # noqa: E402
from PyQt5.QtWidgets import (QApplication, QMainWindow,        # noqa: E402
                             QStackedWidget, QWidget)

# Alles wat photobooth.py bij het importeren aan hardware en netwerk optuigt.
# Er wordt hieronder niets van gebruikt; het gaat om de vensteropbouw.
#
# qrcode staat hier BEWUST NIET tussen. Dat is geen hardware en geen netwerk
# maar een tekenaar, en er staat een QR-code op het startscherm. Werd hij
# vervangen, dan bleef die code onzichtbaar op de afdrukken terwijl hij op de
# booth wel stond — precies de soort blinde vlek waar beta.5 op stukliep. Wat
# een afdruk niet laat zien, wordt niet beoordeeld.
_VERVANGEN = (
    "edsdk_wrapper", "win32print", "win32ui", "win32con", "win32gui",
    "win32api", "pythoncom", "uiautomation", "serial", "serial.tools",
    "serial.tools.list_ports", "usb", "usb.core", "usb.util", "boto3",
    "botocore", "cv2", "requests", "truststore",
)


def leen_photobooth():
    """Importeer photobooth.py met de hardware eruit gehaald."""
    for naam in _VERVANGEN:
        if naam not in sys.modules:
            mod = types.ModuleType(naam)
            mod.__getattr__ = lambda _n: types.SimpleNamespace()
            sys.modules[naam] = mod
    import photobooth
    return photobooth


class Nepscherm:
    """Het scherm van de booth, in punten.

    De beeldschermloze Qt-uitvoering wendt een scherm van 800 x 600 voor.
    Verschillende schermen klemmen hun breedte op de SCHERMbreedte vast — een
    vangnet tegen een venstermaat die Windows bij DPI-schaling verkeerd opgeeft.
    Op de booth zijn scherm en venster hetzelfde ding, dus daar doet dat vangnet
    niets; zonder deze klasse zou het hier juist alles kleiner maken en zouden
    de afdrukken iets anders tonen dan het glas.
    """

    def __init__(self, breedte, hoogte):
        self._rect = QRect(0, 0, breedte, hoogte)

    def geometry(self):
        return self._rect

    availableGeometry = geometry


class Proefvenster(QMainWindow):
    """Een venster met de ECHTE bouwmethodes van PhotoboothWindow erin."""

    GELEEND = (
        "_build_review_confirm_panel", "_build_review_print_question_panel",
        "_build_filter_page", "_build_review_page", "_adapt_review_layout",
        "_pil_to_qpixmap", "_filterstaal_maat", "_zorg_filterstalen",
        "_bouw_filterstalen", "_clear_filter_thumbs",
        "_werk_zijkolom_bij", "_toon_boemerang", "_stop_boemerang",
        "_zijkolom_breed",
        "_zet_printstand", "_display_review_strip",
    )
    OVERGENOMEN = ("_FILTER_TEGEL_STIJL", "_REVIEW_QR_HOOG",
                   "_REVIEW_QR_KAART_HOOG", "_REVIEW_ZIJ_BREED",
                   "_REVIEW_MELDING_HOOG", "_REVIEW_BAND_TEKST")

    def __init__(self, pb, breedte, hoogte):
        super().__init__()
        self._pb = pb
        self._nepscherm = Nepscherm(breedte, hoogte)
        for naam in self.GELEEND:
            setattr(self, naam, getattr(pb.PhotoboothWindow, naam).__get__(self))
        for naam in self.OVERGENOMEN:
            setattr(self, naam, getattr(pb.PhotoboothWindow, naam))
        self.pages = {}
        self._filter_thumb_btns = {}
        self._filter_staal_maat = None
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.resize(breedte, hoogte)
        self.show()
        QApplication.processEvents()

    def screen(self):
        return self._nepscherm

    # Wat _display_review_strip van een echte sessie verwacht. Zonder sessie
    # zijn dit lege waarden; nepsessie() hieronder vult ze met echte bestanden.
    strip_path = ""
    display_strip_path = ""
    display_single_strip_path = ""
    active_event = None
    _cached_strip_path = ""

    # De handlers waar de knoppen aan hangen. Ze doen hier niets: het gaat om
    # waar de knop staat, niet om wat hij doet.
    def _niets(self, *a, **k):
        pass

    _on_review_photos_ok = _on_review_photos_redo = _niets
    _on_review_print_yes = _on_review_print_no = _on_review_stop = _niets
    _filter_next = _filter_retake = _filter_stop = _niets
    _go_done = _go_email_input = _sharing_do_print = _sharing_show_qr = _niets
    _on_inline_print_cancel = _on_inline_print_redo = _niets
    _on_sharing_countdown_tick = _niets


class Overdrachtvenster(QMainWindow):
    """De overdrachtsschermen (code 2718 bij Loskoppelen) los kunnen tekenen.

    Diezelfde truc als hierboven: _handover_overlay wordt ONVERANDERD van
    PhotoboothWindow geleend en op een kaal venster van de werkelijke maat
    aangeroepen. Wat eruit komt is dus het scherm dat de verhuurder na afloop
    van een event te zien krijgt, niet een nabootsing ervan.

    Gebruikt door test_overdracht.py en schermafdrukken.py.
    """

    GELEEND = ("_handover_overlay", "_handover_clear_overlay",
               "_handover_upload_tekst", "_handover_openstaand")
    # Een staticmethod hoort niet aan een instantie gebonden te worden: dan
    # zou self als eerste argument meegaan en klopt de aanroep niet meer.
    OVERGENOMEN = ("_handover_uploads_klaar",)

    def __init__(self, pb, breedte, hoogte):
        super().__init__()
        self._nepscherm = Nepscherm(breedte, hoogte)
        for naam in self.GELEEND:
            setattr(self, naam, getattr(pb.PhotoboothWindow, naam).__get__(self))
        for naam in self.OVERGENOMEN:
            setattr(self, naam, getattr(pb.PhotoboothWindow, naam))
        self._handover_overlay_widget = None
        self.setCentralWidget(QWidget())
        self.resize(breedte, hoogte)
        self.show()
        QApplication.processEvents()

    def screen(self):
        return self._nepscherm


def overdrachtschermen(pb, breedte, hoogte):
    """De schermen van de afsluitflow, vóór en ná de uploadstap.

    Geeft [(naam, venster, overlay)]. De vensters moeten door de aanroeper
    bewaard worden, anders ruimt Python ze meteen op.

    De "voor"-schermen zijn de flow zoals hij was: printgoedkeuring, wifi,
    update. Ze staan hier zodat er iets is om de nieuwe schermen naast te
    leggen — ze worden door dezelfde methode getekend, dus wat je ziet is
    precies wat er stond.
    """
    import config

    uit = []

    def scherm(naam, *args, **kwargs):
        v = Overdrachtvenster(pb, breedte, hoogte)
        ov = v._handover_overlay(*args, **kwargs)
        QApplication.processEvents()
        uit.append((naam, v, ov))
        return v, ov

    # ── Zoals het was ────────────────────────────────────────────────
    scherm("voor-print-goed", "Ziet de print er goed uit?",
           buttons=[("Ja", config.COLOR_SUCCESS, lambda: None),
                    ("Nee", config.COLOR_DANGER, lambda: None)])
    scherm("voor-geen-wifi", "Geen wifi",
           subtitle="Zet de photobooth terug op wifi en probeer opnieuw.",
           buttons=[("Wifi instellen", config.COLOR_PRIMARY, lambda: None),
                    ("Doorgaan", config.COLOR_SECONDARY, lambda: None),
                    ("Overslaan", config.COLOR_DANGER, lambda: None)])
    scherm("voor-up-to-date", "Je bent up-to-date",
           subtitle="Versie 1.99.149 (kanaal beta)",
           buttons=[("Doorgaan", config.COLOR_SUCCESS, lambda: None)])

    # ── Zoals het nu is: de uploadpoort ───────────────────────────────
    scherm("na-geen-wifi-uploaden", "Er moeten nog foto's geüpload worden",
           subtitle=("48 van 132 foto's staan nog op de booth en ik zie geen "
                     "wifi. Zet de booth op wifi en ga daarna verder."),
           buttons=[("Wifi instellen", config.COLOR_PRIMARY, lambda: None),
                    ("Doorgaan", config.COLOR_SUCCESS, lambda: None)],
           corner_button=("uploaden overslaan", lambda: None))

    v = Overdrachtvenster(pb, breedte, hoogte)
    ov = v._handover_overlay(
        "Foto's uploaden…",
        subtitle=v._handover_upload_tekst(
            {"total": 132, "uploaded": 84, "pending": 48, "uploading": 0,
             "failed": 0, "missing": 0}),
        progress=(84, 132),
        corner_button=("uploaden overslaan", lambda: None))
    QApplication.processEvents()
    uit.append(("na-uploaden", v, ov))

    scherm("na-uploaden-stilgevallen", "Foto's uploaden…",
           subtitle=("84 van 132 foto's geüpload  ·  nog 48 te gaan\n"
                     "Er komt al even niets binnen. Staat de booth nog op wifi?"),
           progress=(84, 132),
           buttons=[("Wifi instellen", config.COLOR_PRIMARY, lambda: None),
                    ("Opnieuw proberen", config.COLOR_SECONDARY, lambda: None)],
           corner_button=("uploaden overslaan", lambda: None))

    scherm("na-uploaden-vastgelopen", "Uploaden lukt niet",
           subtitle=("129 van 132 foto's geüpload  ·  3 niet meer op de booth\n"
                     "Die bestanden zijn van de booth verdwenen en komen er "
                     "niet meer bij. Er wordt niets gewist."),
           progress=(129, 132),
           buttons=[("Opnieuw proberen", config.COLOR_PRIMARY, lambda: None),
                    ("Wifi instellen", config.COLOR_SECONDARY, lambda: None)],
           corner_button=("uploaden overslaan", lambda: None))

    scherm("na-booth-leeg", "Booth is leeg",
           subtitle=("132 foto('s) en 132 wachtrijbestand(en) gewist  ·  "
                     "1,4 GB vrijgemaakt.\n4 bestand(en) blijven staan: die "
                     "zijn nooit geüpload, dus die gooit de booth niet weg."),
           buttons=[("Doorgaan", config.COLOR_SUCCESS, lambda: None)])

    scherm("na-niets-gewist", "Foto's blijven op de booth staan",
           subtitle=("Nog niet alles staat in de cloud (7 pending) — er is "
                     "niets gewist."),
           buttons=[("Doorgaan", config.COLOR_SUCCESS, lambda: None)])

    return uit


def nepsessie(map_naam):
    """Een fotostrook en een boemerang op schijf, zoals een sessie ze achterlaat.

    Echte bestanden, want ze gaan door de echte _display_review_strip en de
    echte QMovie. Wat erin staat is nagemaakt — er is geen camera — maar het
    formaat, de verhouding en de weg erheen zijn die van de booth: een staande
    strook van 1200 x 1800 en een bewegende GIF op de maat uit
    config.BOOMERANG_SIZE.
    """
    from PIL import Image, ImageDraw
    os.makedirs(map_naam, exist_ok=True)

    # De strook: vier liggende opnames onder elkaar met een rand eromheen,
    # zoals een 2x6-strook eruitziet.
    strook = Image.new("RGB", (1200, 1800), (250, 248, 244))
    d = ImageDraw.Draw(strook)
    tinten = [(198, 156, 130), (122, 148, 168), (176, 140, 168), (150, 170, 128)]
    marge, tussen = 60, 30
    vak_b = 1200 - 2 * marge
    vak_h = (1800 - 2 * marge - 3 * tussen - 150) // 4
    for i, tint in enumerate(tinten):
        y = marge + i * (vak_h + tussen)
        d.rectangle([marge, y, marge + vak_b, y + vak_h], fill=tint)
        # een gezicht-achtige vorm, zodat de verhouding herkenbaar is
        r = vak_h // 3
        cx, cy = marge + vak_b // 2, y + vak_h // 2
        d.ellipse([cx - r, cy - r, cx + r, cy + r],
                  fill=tuple(min(255, k + 40) for k in tint))
    d.text((marge, 1800 - 130), "MyBoothBox", fill=(22, 32, 45))
    strookpad = os.path.join(map_naam, "strook.jpg")
    strook.save(strookpad, "JPEG", quality=90)

    # De boemerang: acht beeldjes heen en terug, op 480 x 320.
    beeldjes = []
    for k in range(8):
        f = Image.new("RGB", (480, 320), (30, 40, 55))
        g = ImageDraw.Draw(f)
        x = 140 + k * 24
        g.ellipse([x, 90, x + 200, 290], fill=(214, 168, 138))
        g.rectangle([0, 300, 480, 320], fill=(148, 214, 10))
        beeldjes.append(f)
    heen_en_terug = beeldjes + beeldjes[-2:0:-1]
    gifpad = os.path.join(map_naam, "boemerang.gif")
    heen_en_terug[0].save(gifpad, save_all=True, append_images=heen_en_terug[1:],
                          duration=66, loop=0, optimize=True)
    return strookpad, gifpad


def _afronden(venster, breedte, hoogte):
    QApplication.processEvents()
    venster.resize(breedte, hoogte)
    QApplication.processEvents()


def gastschermen(pb, breedte, hoogte, sessie=None):
    """Bouw de schermen die een gast tijdens een sessie aanraakt.

    Geeft [(naam, venster, widget, hoofdknop-naam)]. De vensters moeten door de
    aanroeper bewaard worden, anders ruimt Python ze meteen op.

    De drie vragen na de sessie ("goed gelukt?", "geprint?", het deelscherm)
    zijn GEEN losse schermen: het zijn drie panelen in dezelfde band onderin
    dezelfde pagina, met daarboven de fotostrook. Ze worden hier dan ook in die
    pagina gebouwd en niet los. Een los paneel op schermformaat tekenen zou een
    plaatje opleveren van iets dat op de booth niet bestaat — dat is precies
    hoe beta.5 door de bouwstraat kwam.
    """
    uit = []

    v = Proefvenster(pb, breedte, hoogte)
    # Twee lege pagina's ervoor, want op de booth is het filterscherm pagina 18
    # en niet pagina 0. Een QStackedWidget geeft alleen de pagina die vooraan
    # staat een maat; wie het filterscherm hier als eerste toevoegt, bouwt hem
    # op een maat die hij op de booth pas ná de eerste foto krijgt.
    v.stack.addWidget(QWidget())
    v.stack.addWidget(QWidget())
    v._build_filter_page()
    # De stalen worden gebouwd terwijl de pagina nog NOOIT vooraan heeft
    # gestaan — precies zoals bij de eerste foto van de eerste sessie.
    v._zorg_filterstalen("sepia")
    v.stack.setCurrentWidget(v._filter_page)
    _afronden(v, breedte, hoogte)
    uit.append(("filterscherm", v, v._filter_page, "_filter_next_btn"))

    strookpad, gifpad = (sessie or (None, None))
    for naam, index, hoofd in (
            ("zijn-de-fotos-goed-gelukt", 0, "_review_confirm_yes_btn"),
            ("wil-je-ze-geprint", 1, "_review_print_yes_btn"),
            ("deelscherm", 2, "_sharing_done_btn")):
        v = Proefvenster(pb, breedte, hoogte)
        v.pages["review"] = v.stack.count()
        v._build_review_page()
        v.stack.setCurrentIndex(v.pages["review"])
        _afronden(v, breedte, hoogte)
        v._review_panel_stack.setCurrentIndex(index)
        v._adapt_review_layout()
        if strookpad:
            v.strip_path = v.display_strip_path = strookpad
            # Op het deelscherm hoort de QR erbij; die groep wordt normaal door
            # _update_inline_qr aangezet en dat pad hangt aan een echte upload.
            if index == 2:
                v._inline_qr_box.show()
            # De boemerang komt op de booth een paar seconden later binnen, via
            # het gif_complete-signaal. Hier wordt precies datzelfde pad
            # gelopen: dezelfde methode, met de GIF van schijf.
            v._toon_boemerang(gifpad)
            v._werk_zijkolom_bij()
            _afronden(v, breedte, hoogte)
            v._display_review_strip()
        _afronden(v, breedte, hoogte)
        uit.append((naam, v, v.stack.currentWidget(), hoofd))

    return uit
