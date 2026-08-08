"""Toetst het slotmenu: het scherm achter het slotje.

Waarom dit bestaat
------------------
Dit scherm was een QDialog — een LOS VENSTER dat op Windows over de booth
zweefde, dat je kon verslepen, en dat met zijn lichte kaart niets te maken had
met het scherm eronder. Het is nu een overlay ín het venster. Vier dingen
mogen daarbij niet stilletjes wegvallen, en dat zijn precies de dingen die je
pas op een event merkt:

  1. het mag geen apart venster meer zijn, en de achtergrond moet erdoorheen
     blijven kleuren — anders is het opnieuw een pop-up, alleen donkerder;
  2. de uploadstand moet blijven. Dat is waarom de verhuurder hier kijkt
     vóórdat hij loskoppelt: staat alles al in de cloud?
  3. de pincode moet blijven. Loskoppelen en Geavanceerde instellingen vragen
     hem nog steeds — dat is de drempel tussen een gast die op het slotje tikt
     en een booth die midden op een feest van zijn event af ligt;
  4. het menu moet weer dicht kunnen, en de verversing moet dan stoppen.

De schermen worden getekend zoals de booth ze tekent (offscreen, op de maat
van de Surface Pro) en de ECHTE methodes van PhotoboothWindow worden geleend,
niet nagebouwd.

    python test_slotmenu.py
"""

import os
import sys
import types

APP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_SCALE_FACTOR", "2")

for _stroom in (sys.stdout, sys.stderr):
    try:
        _stroom.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from PyQt5.QtCore import Qt                                      # noqa: E402
from PyQt5.QtGui import QPixmap                                  # noqa: E402
from PyQt5.QtWidgets import QApplication, QPushButton            # noqa: E402

import config                                                    # noqa: E402
import proefvenster                                              # noqa: E402

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
        doen(*args)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        print(f"  FOUT  {naam} klapte om: {type(exc).__name__}: {exc}", flush=True)
        fouten.append(f"{naam}: {exc}")


class Slotvenster(proefvenster.Startschermvenster):
    """De idle-pagina mét het slotmenu erop, met de echte methodes.

    De achtergrond is de echte idle-pagina en niet een leeg venster: de vraag
    of de achtergrond meekleurt is alleen te beantwoorden als er een
    achtergrond ís.
    """

    # Ook de drie acties zelf, want juist die moeten onveranderd blijven
    # werken: de pincode zit in _lock_action_unlink en _lock_action_advanced.
    GELEEND_SLOT = ("_slotmenu_open", "_slotmenu_sluit", "_slotmenu_plaats",
                    "_slotmenu_uploadregel", "_refresh_event_info_labels",
                    "_show_event_info_dialog", "_on_lock_clicked",
                    "_lock_action_refresh", "_lock_action_unlink",
                    "_lock_action_advanced")

    def __init__(self, pb, gekoppeld=True):
        super().__init__(pb, BREED, HOOG, raw_dir="")
        for naam in self.GELEEND_SLOT:
            setattr(self, naam, getattr(pb.PhotoboothWindow, naam).__get__(self))
        self._SLOTMENU_KAART_BREED = pb.PhotoboothWindow._SLOTMENU_KAART_BREED
        self.active_event.linked_booking_id = "test-booking-1" if gekoppeld else ""
        self.active_event.linked_booking_label = (
            "ron-debbygroet · 2026-06-05" if gekoppeld else "")
        self.active_event.pin_code = "1350"
        self.active_event.save = lambda *a, **k: None
        self._dnp_last_status = None
        self._advanced_unlocked = False

    def _niets(self, *a, **k):
        pass

    _on_refresh_event_clicked = _go_idle = _niets
    _update_linked_card_visibility = _go_settings_after_pin = _niets
    _start_handover_flow = _niets


def knoppen(overlay):
    """Alle knoppen op de kaart, op naam."""
    uit = {}
    for k in overlay.findChildren(QPushButton):
        uit[k.text()] = k
    return uit


def _uploadstand(uploaded, total, failed=0):
    import cloud_uploader
    cloud_uploader.get_status = lambda bid: {
        "total": total, "uploaded": uploaded, "pending": max(0, total - uploaded),
        "uploading": 0, "failed": failed, "missing": 0}


def _pixel(venster, x, y):
    """Eén beeldpunt van het getekende scherm."""
    doek = QPixmap(venster.width(), venster.height())
    doek.fill(Qt.transparent)
    venster.render(doek)
    return doek.toImage().pixelColor(x, y)


# ══════════════════════════════════════════════════════════════════════
#  1. Geen los venster meer
# ══════════════════════════════════════════════════════════════════════

def toets_ingebed(pb):
    v = Slotvenster(pb)
    v.bouw(melding=False)
    ov = v._slotmenu_open()
    QApplication.processEvents()

    eis(ov.parent() is v,
        "het slotmenu hangt aan het venster zelf")
    eis(not ov.isWindow(),
        "het is geen apart venster meer (niet los te schuiven)")
    eis(not ov.windowFlags() & Qt.Dialog,
        "en het is ook geen dialoog meer")
    eis((ov.width(), ov.height()) == (v.width(), v.height()),
        f"het bedekt het hele scherm ({ov.width()}x{ov.height()})")

    kaart = ov.kaart
    midden_x = kaart.x() + kaart.width() // 2
    midden_y = kaart.y() + kaart.height() // 2
    eis(abs(midden_x - v.width() // 2) <= 1 and abs(midden_y - v.height() // 2) <= 1,
        f"de kaart staat in het midden ({kaart.x()},{kaart.y()} "
        f"van {kaart.width()}x{kaart.height()})")
    eis(kaart.width() < v.width(),
        "en hij is smaller dan het scherm, dus de achtergrond blijft eromheen staan")
    v._slotmenu_sluit()


def toets_achtergrond_kleurt_mee(pb):
    """Het vlak is doorzichtig: wat eronder ligt is er nog, alleen gedempt."""
    v = Slotvenster(pb)
    v.bouw(melding=False)
    QApplication.processEvents()
    # Een punt linksboven, buiten de kaart: daar staat de collage.
    kaal = _pixel(v, 60, 60)
    v._slotmenu_open()
    QApplication.processEvents()
    met_menu = _pixel(v, 60, 60)

    eis(kaal != met_menu, "het menu dempt de achtergrond")
    # Meekleuren betekent: níét één egaal vlak. De tint van de collage moet er
    # nog in zitten, dus het punt mag niet gelijk zijn aan de kale merkinkt.
    import merk
    from PyQt5.QtGui import QColor
    inkt = QColor(merk.INKT)
    eis((met_menu.red(), met_menu.green(), met_menu.blue())
        != (inkt.red(), inkt.green(), inkt.blue()),
        f"maar dekt hem niet af — de achtergrond kleurt mee "
        f"(rgb {met_menu.red()},{met_menu.green()},{met_menu.blue()})")
    v._slotmenu_sluit()


# ══════════════════════════════════════════════════════════════════════
#  2. De uploadstand blijft
# ══════════════════════════════════════════════════════════════════════

def toets_uploadstand(pb):
    v = Slotvenster(pb)
    v.bouw(melding=False)

    _uploadstand(118, 132)
    v._slotmenu_open()
    QApplication.processEvents()
    bezig = v._evinfo_upload_lbl.text()
    eis("14" in bezig and "132" in bezig,
        f"onderweg staat er hoeveel er nog moet: “{bezig}”")
    eis(v._evinfo_upload_lbl.isVisible(), "en die regel staat er zichtbaar bij")

    _uploadstand(132, 132)
    v._refresh_event_info_labels()
    klaar = v._evinfo_upload_lbl.text()
    eis("cloud" in klaar.lower(),
        f"en als alles binnen is, staat dat er als antwoord: “{klaar}”")

    _uploadstand(120, 132, failed=3)
    v._refresh_event_info_labels()
    mislukt = v._evinfo_upload_lbl.text()
    eis("3" in mislukt and "mislukt" in mislukt,
        f"mislukte uploads blijven zichtbaar: “{mislukt}”")
    v._slotmenu_sluit()


def toets_kortere_regels(pb):
    """De naam van het event staat alleen; de rest hangt eronder."""
    v = Slotvenster(pb)
    v.bouw(melding=False)
    _uploadstand(118, 132)
    v._slotmenu_open()
    QApplication.processEvents()

    naamregel = v._evinfo_event_lbl.text()
    eis("ron-debbygroet" in naamregel, f"de naam staat er: “{naamregel}”")
    eis("2026-06-05" not in naamregel,
        "de datum niet meer in dezelfde regel")
    eis("132" not in naamregel and "%" not in naamregel,
        "en de uploadrekensom ook niet")
    eis(v._evinfo_datum_lbl.text() == "2026-06-05",
        f"de datum staat eronder: “{v._evinfo_datum_lbl.text()}”")
    v._slotmenu_sluit()


def toets_printerregel(pb):
    """De printerstand blijft, met de prints die er nog in zitten."""
    from dnp_status import StatusLevel
    v = Slotvenster(pb)
    v.bouw(melding=False)
    v._dnp_last_status = types.SimpleNamespace(
        level=StatusLevel.OK, connected=True, label="Klaar", code=None,
        prints_remaining=312, prints_total=700)
    _uploadstand(132, 132)
    v._slotmenu_open()
    QApplication.processEvents()
    regel = v._evinfo_printer_lbl.text()
    eis(config.PRINTER_NAME in regel, f"de printer staat erbij: “{regel}”")
    eis("312" in regel and "700" in regel, "met de prints die er nog in zitten")
    v._slotmenu_sluit()


# ══════════════════════════════════════════════════════════════════════
#  3. De pincode blijft — hier is met opzet niets aan veranderd
# ══════════════════════════════════════════════════════════════════════

def toets_pincode_blijft(pb):
    gevraagd = []

    def nep_pin(parent, titel=""):
        gevraagd.append(titel)
        return ("", False)          # de gast breekt af

    echt = pb.PinDialog.get_pin
    pb.PinDialog.get_pin = staticmethod(nep_pin)
    try:
        v = Slotvenster(pb)
        v.bouw(melding=False)
        _uploadstand(132, 132)
        ov = v._slotmenu_open()
        QApplication.processEvents()
        k = knoppen(ov)

        k["Loskoppelen"].click()
        QApplication.processEvents()
        eis(len(gevraagd) == 1, "Loskoppelen vraagt om de pincode")
        eis(v.active_event.linked_booking_id == "test-booking-1",
            "en zonder code blijft het event gewoon gekoppeld")
        eis(v._slotmenu_widget is not None,
            "het menu blijft open, je staat niet ineens ergens anders")

        geav = [t for t in k if "Geavanceerde" in t]
        k[geav[0]].click()
        QApplication.processEvents()
        eis(len(gevraagd) == 2, "Geavanceerde instellingen vraagt hem ook")
        v._slotmenu_sluit()
    finally:
        pb.PinDialog.get_pin = echt


# ══════════════════════════════════════════════════════════════════════
#  4. Rangorde en sluiten
# ══════════════════════════════════════════════════════════════════════

def toets_geavanceerd_is_stil(pb):
    v = Slotvenster(pb)
    v.bouw(melding=False)
    _uploadstand(132, 132)
    ov = v._slotmenu_open()
    QApplication.processEvents()
    k = knoppen(ov)
    geav = k[[t for t in k if "Geavanceerde" in t][0]]
    los = k["Loskoppelen"]

    eis(geav.font().pixelSize() < los.font().pixelSize(),
        f"Geavanceerd staat in kleinere letter ({geav.font().pixelSize()} "
        f"tegen {los.font().pixelSize()})")
    eis(geav.height() < los.height(),
        f"en de knop is lager ({geav.height()} tegen {los.height()})")
    eis("background: transparent" in geav.styleSheet(),
        "hij heeft geen eigen vlak — het is een knop voor uitzonderingen")
    v._slotmenu_sluit()


def toets_sluiten(pb):
    v = Slotvenster(pb)
    v.bouw(melding=False)
    _uploadstand(132, 132)
    ov = v._slotmenu_open()
    QApplication.processEvents()
    k = knoppen(ov)

    k["Sluiten"].click()
    QApplication.processEvents()
    eis(v._slotmenu_widget is None, "Sluiten doet het menu dicht")
    eis(getattr(v, "_evinfo_refresh_timer", None) is None,
        "en de verversing stopt (anders werkt hij labels bij die weg zijn)")

    ov = v._slotmenu_open()
    QApplication.processEvents()
    ov.wegtikvlak.click()
    QApplication.processEvents()
    eis(v._slotmenu_widget is None, "naast de kaart tikken sluit hem ook")

    # Twee keer openen mag geen tweede menu opleveren.
    a = v._slotmenu_open()
    b = v._slotmenu_open()
    eis(a is b, "twee keer op het slotje tikken stapelt geen menu's")
    v._slotmenu_sluit()


def main():
    print("SLOTMENU", flush=True)
    app = QApplication(sys.argv)                                # noqa: F841
    import lettertype
    lettertype.laad_merkletters()
    pb = proefvenster.leen_photobooth()

    onderdeel("ingebed in het scherm", toets_ingebed, pb)
    onderdeel("de achtergrond kleurt mee", toets_achtergrond_kleurt_mee, pb)
    onderdeel("de uploadstand", toets_uploadstand, pb)
    onderdeel("kortere regels", toets_kortere_regels, pb)
    onderdeel("de printerregel", toets_printerregel, pb)
    onderdeel("de pincode blijft", toets_pincode_blijft, pb)
    onderdeel("Geavanceerd is stil", toets_geavanceerd_is_stil, pb)
    onderdeel("sluiten", toets_sluiten, pb)

    print("", flush=True)
    if fouten:
        print(f"SLOTMENU: {len(fouten)} fout(en)", flush=True)
        for f in fouten:
            print(f"  - {f}", flush=True)
        return 1
    print("SLOTMENU: alles klopt", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
