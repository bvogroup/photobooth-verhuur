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
_VERVANGEN = (
    "edsdk_wrapper", "win32print", "win32ui", "win32con", "win32gui",
    "win32api", "pythoncom", "uiautomation", "serial", "serial.tools",
    "serial.tools.list_ports", "usb", "usb.core", "usb.util", "boto3",
    "botocore", "cv2", "qrcode", "requests", "truststore",
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
    )
    OVERGENOMEN = ("_FILTER_TEGEL_STIJL", "_REVIEW_QR_HOOG",
                   "_REVIEW_QR_KAART_HOOG", "_REVIEW_MELDING_BREED")

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

    # De handlers waar de knoppen aan hangen. Ze doen hier niets: het gaat om
    # waar de knop staat, niet om wat hij doet.
    def _niets(self, *a, **k):
        pass

    _on_review_photos_ok = _on_review_photos_redo = _niets
    _on_review_print_yes = _on_review_print_no = _niets
    _filter_next = _filter_retake = _filter_stop = _niets
    _go_done = _go_email_input = _sharing_do_print = _sharing_show_qr = _niets
    _on_inline_print_cancel = _on_inline_print_redo = _niets
    _on_sharing_countdown_tick = _display_review_strip = _niets


def _afronden(venster, breedte, hoogte):
    QApplication.processEvents()
    venster.resize(breedte, hoogte)
    QApplication.processEvents()


def gastschermen(pb, breedte, hoogte):
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
        _afronden(v, breedte, hoogte)
        uit.append((naam, v, v.stack.currentWidget(), hoofd))

    return uit
