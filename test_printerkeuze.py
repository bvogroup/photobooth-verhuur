"""Toetst de automatische printerkeuze met een nagebootste printerlijst.

Waarom dit bestaat
------------------
Deze toetsen draaien zonder Windows en zonder printer. Dat kan omdat de
keuzelogica (`Keuzeautomaat`) niets van Windows weet: hij krijgt een lijst
`Kandidaat`-objecten en zegt wat de printer wordt. Alle Windows-aanroepen
zitten in aparte functies die de wacht via parameters binnenkrijgt, zodat een
toets ze kan vervangen.

De gevallen die er echt toe doen staan bovenaan:

* alleen een PDF-printer aanwezig     → kies NIETS (prints mogen nooit stil in
                                        een bestand verdwijnen)
* een onbekende maar echte printer    → kies hem gewoon
* een printer verdwijnt               → pas na meerdere missers opnieuw zoeken
* de verhuurder koos zelf             → niet overrulen
* er loopt een print                  → niet wisselen
* de schakelaar staat uit             → er verandert helemaal niets

Draaien: pytest test_printerkeuze.py, of gewoon python test_printerkeuze.py
"""

import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import printerkeuze as pk
from printerkeuze import Kandidaat, Keuzeautomaat, Printerwacht


# ── Handige nabootsingen ────────────────────────────────────────────────

USB = 0x40  # PRINTER_ATTRIBUTE_LOCAL
NET = 0x10  # PRINTER_ATTRIBUTE_NETWORK

PDF = Kandidaat("Microsoft Print to PDF", poort="PORTPROMPT:",
                stuurprogramma="Microsoft Print To PDF", kenmerken=USB)
XPS = Kandidaat("Microsoft XPS Document Writer", poort="XPSPort:",
                stuurprogramma="Microsoft XPS Document Writer v4", kenmerken=USB)
ONENOTE = Kandidaat("OneNote (Desktop)", poort="nul:",
                    stuurprogramma="Send to Microsoft OneNote 16 Driver",
                    kenmerken=USB)
FAX = Kandidaat("Fax", poort="SHRFAX:",
                stuurprogramma="Microsoft Shared Fax Driver", kenmerken=USB)

DNP = Kandidaat("DP-QW410", poort="USB001",
                stuurprogramma="DP-QW410", kenmerken=USB)
DNP_KOPIE = Kandidaat("DP-QW410 (Kopie 1)", poort="USB002",
                      stuurprogramma="DP-QW410", kenmerken=USB)
HITI = Kandidaat("HiTi P525L", poort="USB003",
                 stuurprogramma="HiTi P525L", kenmerken=USB)
# Een model dat wij nooit gezien hebben. Moet gewoon gekozen worden.
ONBEKEND = Kandidaat("Vreemdmerk FotoJet 900", poort="USB004",
                     stuurprogramma="Vreemdmerk FJ900", kenmerken=USB)
KANTOOR = Kandidaat("HP LaserJet M404 (kantoor)", poort="WSD-88f1e2",
                    stuurprogramma="HP LaserJet M404", kenmerken=NET)


# ── 1. Alleen virtuele bestemmingen → kies NIETS ────────────────────────

def test_alleen_pdf_printer_kiest_niets():
    a = Keuzeautomaat()
    b = a.beoordeel([PDF, XPS, ONENOTE, FAX])
    assert b.printer is None, f"koos {b.printer!r} terwijl er geen echte printer is"
    assert b.reden == "niets-gevonden"
    assert "Geen printer gevonden" in b.melding
    assert a.gekozen is None


def test_lege_printerlijst_kiest_niets():
    a = Keuzeautomaat()
    b = a.beoordeel([])
    assert b.printer is None
    assert b.reden == "niets-gevonden"


def test_virtuele_bestemmingen_worden_herkend():
    for k in (PDF, XPS, ONENOTE, FAX,
              Kandidaat("CutePDF Writer", poort="CPW2:"),
              Kandidaat("Bureau (redirected 2)", poort="TS002"),
              Kandidaat("Adobe PDF", poort="Documents\\*.pdf")):
        virtueel, reden = pk.is_virtueel(k)
        assert virtueel, f"{k.naam!r} werd niet als virtueel herkend"
        assert reden


# ── 2. Een echte printer erbij → die kiezen ─────────────────────────────

def test_dnp_ertussen_wordt_gekozen():
    a = Keuzeautomaat()
    b = a.beoordeel([PDF, XPS, DNP, FAX])
    assert b.printer == "DP-QW410"
    assert b.gewisseld is True
    assert b.reden == "eerste-keuze"


def test_onbekende_echte_printer_wordt_gewoon_gekozen():
    """De fout die we NIET willen maken: een goedgekeurde-modellenlijst.

    Hangt de verhuurder een nieuw, vervangend of geleend apparaat aan, dan
    staat dat op geen enkele lijst. Dat mag geen reden zijn om er niets mee te
    doen — dan moet er alsnog iemand in de instellingen, en dat is precies wat
    deze opdracht moest afschaffen.
    """
    a = Keuzeautomaat()
    b = a.beoordeel([PDF, ONENOTE, ONBEKEND])
    assert b.printer == "Vreemdmerk FotoJet 900", \
        "een onbekend maar echt apparaat moet gewoon gekozen worden"
    assert b.gewisseld is True


def test_usb_wint_van_netwerkprinter():
    """De booth heeft één USB-poort. Wat daarin zit is de printer waar de gast
    bij staat; de kantoorprinter verderop levert prints die niemand ophaalt."""
    a = Keuzeautomaat()
    b = a.beoordeel([KANTOOR, ONBEKEND])
    assert b.printer == "Vreemdmerk FotoJet 900"


def test_levende_kopie_wint_van_dode_kopie():
    """Na een unit-swap laat Windows twee queues achter met dezelfde naam-stam;
    er wordt er maar één echt bediend."""
    a = Keuzeautomaat()
    b = a.beoordeel([DNP, DNP_KOPIE],
                    levend={"DP-QW410": False, "DP-QW410 (Kopie 1)": True})
    assert b.printer == "DP-QW410 (Kopie 1)"


# ── 3. Printer verdwijnt → pas na meerdere missers opnieuw zoeken ───────

def test_verdwenen_printer_pas_na_drempel_vervangen():
    a = Keuzeautomaat(drempel=3)
    a.beoordeel([DNP, HITI])
    assert a.gekozen == "DP-QW410"

    # De DNP valt weg. Eerste twee vegen: nog niets doen.
    for veeg in (1, 2):
        b = a.beoordeel([HITI])
        assert b.printer == "DP-QW410", f"veeg {veeg} wisselde al"
        assert b.gewisseld is False
        assert b.reden == "hik"
        assert b.missers == veeg

    # Derde misser: nu pas opnieuw zoeken.
    b = a.beoordeel([HITI])
    assert b.printer == "HiTi P525L"
    assert b.gewisseld is True
    assert b.reden == "gewisseld"
    assert b.vorige == "DP-QW410"


def test_een_hik_reset_de_teller_weer():
    """Papier pakken duurt even. Antwoordt hij daarna weer, dan is er niets
    aan de hand en begint het tellen opnieuw."""
    a = Keuzeautomaat(drempel=3)
    a.beoordeel([DNP])
    a.beoordeel([DNP], levend={"DP-QW410": False})
    a.beoordeel([DNP], levend={"DP-QW410": False})
    assert a.missers == 2
    b = a.beoordeel([DNP], levend={"DP-QW410": True})
    assert b.missers == 0
    assert b.gewisseld is False


def test_papier_op_is_geen_reden_om_te_wisselen():
    """`printer_leeft` geeft alleen False bij offline/verwijderd. Papier op
    hoort True te blijven, anders stapt de booth over op een ander apparaat
    terwijl er hier alleen papier in moet."""
    assert pk.STATUS_DOOD & pk.STATUS_OFFLINE
    for benigne in (0x00000010,   # PAPER_OUT
                    0x00400000,   # DOOR_OPEN
                    0x00000008,   # PAPER_JAM
                    0x00000200):  # BUSY
        assert not (pk.STATUS_DOOD & benigne), f"{benigne:#x} telt onterecht als dood"


def test_dode_printer_zonder_alternatief_blijft_staan():
    a = Keuzeautomaat(drempel=2)
    a.beoordeel([DNP])
    a.beoordeel([DNP], levend={"DP-QW410": False})
    b = a.beoordeel([DNP], levend={"DP-QW410": False})
    assert b.printer == "DP-QW410"
    assert b.gewisseld is False
    assert b.reden == "geen-alternatief"


def test_laatste_printer_weg_meldt_dat_duidelijk():
    a = Keuzeautomaat(drempel=2)
    a.beoordeel([DNP])
    a.beoordeel([PDF])
    b = a.beoordeel([PDF])
    assert b.printer is None
    assert b.gewisseld is True
    assert "Geen printer gevonden" in b.melding


# ── 4. Handmatige keuze → niet overrulen ────────────────────────────────

def test_handmatige_keuze_wordt_niet_overruled():
    a = Keuzeautomaat(drempel=2)
    a.zet_handmatig("HiTi P525L")
    # Er hangt óók een DNP, die de automaat anders zou verkiezen.
    for _ in range(5):
        b = a.beoordeel([DNP, HITI])
        assert b.printer == "HiTi P525L"
        assert b.gewisseld is False


def test_handmatige_keuze_die_dood_is_blijft_toch_staan():
    """Hij staat er nog, hij antwoordt alleen niet. Dat is de printer van de
    verhuurder met een probleem — niet een verkeerde keuze."""
    a = Keuzeautomaat(drempel=2)
    a.zet_handmatig("HiTi P525L")
    for _ in range(5):
        b = a.beoordeel([DNP, HITI], levend={"HiTi P525L": False})
        assert b.printer == "HiTi P525L"
        assert b.gewisseld is False


def test_handmatige_keuze_die_helemaal_weg_is_valt_terug_en_komt_terug():
    """Wijst de zelf gekozen naam nergens meer naar, dan valt de automaat na de
    drempel in. De naam blijft bewaard: komt de printer terug, dan springt de
    keuze er meteen op terug. De keuze gaat dus nooit verloren."""
    a = Keuzeautomaat(drempel=2)
    a.zet_handmatig("HiTi P525L")
    b = a.beoordeel([DNP])
    assert b.printer == "HiTi P525L" and b.reden == "hik"
    b = a.beoordeel([DNP])
    assert b.printer == "DP-QW410" and b.gewisseld is True

    # HiTi weer aangesloten → terug naar de eigen keuze.
    b = a.beoordeel([DNP, HITI])
    assert b.printer == "HiTi P525L"
    assert b.reden == "handmatig-hersteld"


def test_terug_naar_automatisch():
    a = Keuzeautomaat(drempel=2)
    a.zet_handmatig("HiTi P525L")
    assert a.beoordeel([DNP, HITI]).printer == "HiTi P525L"
    a.terug_naar_automatisch()
    b = a.beoordeel([DNP, HITI])
    assert b.printer == "DP-QW410"


# ── 5. Print bezig → niet wisselen ──────────────────────────────────────

def test_niet_wisselen_tijdens_een_print():
    a = Keuzeautomaat(drempel=1)
    a.beoordeel([DNP])
    # De DNP is uit de lijst verdwenen én er loopt een print: niets doen.
    b = a.beoordeel([HITI], print_bezig=True)
    assert b.printer == "DP-QW410"
    assert b.gewisseld is False
    assert b.reden == "print-bezig"


def test_print_bezig_telt_geen_missers():
    """Een printer die aan het printen is antwoordt soms niet op een
    statusvraag. Dat mag geen misser worden, anders wisselt de booth vlak na
    de print alsnog."""
    a = Keuzeautomaat(drempel=2)
    a.beoordeel([DNP])
    for _ in range(6):
        a.beoordeel([HITI], print_bezig=True)
    assert a.missers == 0
    assert a.gekozen == "DP-QW410"


# ── 6. Schakelaar uit → er verandert helemaal niets ─────────────────────

class _Spion:
    """Telt hoe vaak er iets aan Windows gevraagd wordt."""
    def __init__(self, kandidaten):
        self.kandidaten = list(kandidaten)
        self.namen_gevraagd = 0
        self.lijst_gevraagd = 0
        self.leven_gevraagd = 0

    def namen(self):
        self.namen_gevraagd += 1
        return [k.naam for k in self.kandidaten]

    def lijst(self):
        self.lijst_gevraagd += 1
        return list(self.kandidaten)

    def leeft(self, naam):
        self.leven_gevraagd += 1
        return True

    @property
    def totaal(self):
        return self.namen_gevraagd + self.lijst_gevraagd + self.leven_gevraagd


def _nep_app(instellingen, data_dir, spion):
    """Bootst na wat photobooth.py bij het opstarten doet.

    Dit is de belangrijkste toets van het hele bestand: staat de schakelaar
    uit, dan hoort er geen enkele draad te draaien en geen enkele vraag aan
    Windows gesteld te worden. Er staan 25 booths in het veld die zichzelf
    bijwerken; die mogen door een update geen ander printgedrag krijgen.
    """
    def lees(sleutel, standaard=None):
        return instellingen.get(sleutel, standaard)

    def schrijf(sleutel, waarde):
        instellingen[sleutel] = waarde

    aan = pk.bepaal_automaat_aan(lees, schrijf, data_dir)
    if not aan:
        return None  # precies zoals nu: geen wacht, geen controles
    wacht = Printerwacht(
        terugmelding=lambda besluit, stand: None,
        gekozen=lees("printer_name", "") or None,
        handmatig_naam=lees(pk.SLEUTEL_HANDMATIG, "") or None,
        lees_namen=spion.namen,
        lees_lijst=spion.lijst,
        leest_leven=spion.leeft,
    )
    return wacht


def test_schakelaar_uit_vraagt_windows_helemaal_niets(tmp_pad=None):
    map_ = tmp_pad or tempfile.mkdtemp(prefix="pk-uit-")
    try:
        spion = _Spion([DNP, PDF])
        instellingen = {pk.SLEUTEL_AUTO: False,
                        "printer_name": "HiTi P525"}
        wacht = _nep_app(instellingen, map_, spion)
        assert wacht is None, "er werd een wacht gebouwd terwijl de schakelaar uit staat"
        assert spion.totaal == 0, \
            f"met de schakelaar uit werden er {spion.totaal} vragen aan Windows gesteld"
        # En de printernaam blijft precies wat er in de instellingen stond.
        assert instellingen["printer_name"] == "HiTi P525"
    finally:
        if tmp_pad is None:
            shutil.rmtree(map_, ignore_errors=True)


def test_schakelaar_uit_toont_gewoon_de_handmatige_printer():
    tekst = pk.stand_tekst(None, aan=False, handmatige_naam="HiTi P525L")
    assert "HiTi P525L" in tekst
    assert "handmatig" in tekst.lower()
    # Geen suggestie van controles die niet draaien.
    assert "gecontroleerd" not in tekst


def test_schakelaar_aan_gaat_wel_kijken():
    map_ = tempfile.mkdtemp(prefix="pk-aan-")
    try:
        spion = _Spion([DNP, PDF])
        instellingen = {pk.SLEUTEL_AUTO: True}
        wacht = _nep_app(instellingen, map_, spion)
        assert wacht is not None
        assert spion.totaal == 0, "de wacht mag pas kijken als hij draait"
        besluit = wacht.veeg()
        assert besluit.printer == "DP-QW410"
        assert spion.namen_gevraagd == 1
        assert spion.lijst_gevraagd == 1
    finally:
        shutil.rmtree(map_, ignore_errors=True)


def test_wacht_vraagt_de_dure_lijst_alleen_bij_verandering():
    """De namenlijst is goedkoop, de volledige lijst met poort en driver niet.
    Zolang er niets in- of uitgeplugd wordt hoeft die tweede niet."""
    spion = _Spion([DNP, PDF])
    wacht = Printerwacht(terugmelding=lambda b, s: None,
                         lees_namen=spion.namen, lees_lijst=spion.lijst,
                         leest_leven=spion.leeft)
    for _ in range(5):
        wacht.veeg()
    assert spion.namen_gevraagd == 5
    assert spion.lijst_gevraagd == 1, \
        f"de dure lijst werd {spion.lijst_gevraagd}x opgevraagd zonder verandering"

    spion.kandidaten.append(HITI)
    wacht.veeg()
    assert spion.lijst_gevraagd == 2


def test_wacht_meldt_de_stand_voor_het_scherm():
    spion = _Spion([DNP])
    wacht = Printerwacht(terugmelding=lambda b, s: None,
                         lees_namen=spion.namen, lees_lijst=spion.lijst,
                         leest_leven=spion.leeft)
    wacht.veeg()
    stand = wacht.stand()
    assert stand.printer == "DP-QW410"
    assert stand.antwoordde is True
    assert stand.laatst_gekeken > 0
    tekst = pk.stand_tekst(stand, aan=True)
    assert "DP-QW410" in tekst
    assert "automatisch" in tekst
    assert "antwoordde" in tekst


def test_onbereikbare_printerlijst_verandert_niets():
    """`snelle_namen` geeft None als de vraag niet gesteld kon worden. Dat is
    iets anders dan 'er is geen printer' — één hik in de spooler mag een
    werkende printer niet wegnemen."""
    spion = _Spion([DNP])
    wacht = Printerwacht(terugmelding=lambda b, s: None,
                         lees_namen=spion.namen, lees_lijst=spion.lijst,
                         leest_leven=spion.leeft)
    wacht.veeg()
    assert wacht.automaat.gekozen == "DP-QW410"

    wacht._lees_namen = lambda: None  # spooler antwoordt niet
    for _ in range(10):
        besluit = wacht.veeg()
        assert besluit.reden == "geen-gegevens"
        assert besluit.printer == "DP-QW410"
    assert wacht.automaat.missers == 0
    assert wacht.stand().printer == "DP-QW410"


def test_mislukte_dure_lijst_neemt_de_printer_niet_weg():
    """De namenlijst lukt, de lijst met poort en driver niet. Dan kiezen we op
    naam alleen — een lege lijst zou een werkende printer wegnemen."""
    spion = _Spion([DNP, PDF])
    wacht = Printerwacht(terugmelding=lambda b, s: None, drempel=1,
                         lees_namen=spion.namen, lees_lijst=lambda: [],
                         leest_leven=spion.leeft)
    besluit = wacht.veeg()
    assert besluit.printer == "DP-QW410"
    # En de PDF-printer wordt nog steeds op naam uitgesloten.
    assert "Microsoft Print to PDF" not in (besluit.printer or "")


def test_wacht_onthoudt_het_stuurprogramma_van_een_verdwenen_queue():
    """Het geval waarvoor dit nodig is: Windows maakt na een unit-swap een
    nieuwe queue ('(Kopie 1)') en gooit de oude weg. De hoofddraad moet dan
    nog kunnen zien dat het dezelfde driver is, zonder zelf Windows te
    bevragen — anders verhuist het papierprofiel niet mee."""
    spion = _Spion([DNP])
    wacht = Printerwacht(terugmelding=lambda b, s: None, drempel=1,
                         lees_namen=spion.namen, lees_lijst=spion.lijst,
                         leest_leven=spion.leeft)
    wacht.veeg()
    spion.kandidaten = [DNP_KOPIE]          # oude queue weg, nieuwe erbij
    besluit = wacht.veeg()
    assert besluit.printer == "DP-QW410 (Kopie 1)"
    assert besluit.vorige == "DP-QW410"
    assert besluit.zelfde_stuurprogramma is True, \
        "het papierprofiel zou nu niet meeverhuizen"


def test_ander_merk_ertussen_verhuist_het_profiel_niet():
    spion = _Spion([HITI])
    wacht = Printerwacht(terugmelding=lambda b, s: None, drempel=1,
                         lees_namen=spion.namen, lees_lijst=spion.lijst,
                         leest_leven=spion.leeft)
    wacht.veeg()
    spion.kandidaten = [DNP]
    besluit = wacht.veeg()
    assert besluit.printer == "DP-QW410"
    assert besluit.zelfde_stuurprogramma is False


def test_wacht_vraagt_niets_terwijl_er_geprint_wordt():
    spion = _Spion([DNP])
    wacht = Printerwacht(terugmelding=lambda b, s: None,
                         print_bezig=lambda: True,
                         lees_namen=spion.namen, lees_lijst=spion.lijst,
                         leest_leven=spion.leeft)
    wacht.veeg()
    assert spion.leven_gevraagd == 0, \
        "de printer werd bevraagd terwijl hij aan het printen was"


# ── 7. Standaardstand van de schakelaar ─────────────────────────────────

def test_verse_installatie_zet_de_schakelaar_aan():
    map_ = tempfile.mkdtemp(prefix="pk-vers-")
    try:
        # Precies wat config.py bij import aanmaakt: lege mappen, verder niets.
        for sub in ("photos", "backgrounds", "templates", "events"):
            os.makedirs(os.path.join(map_, sub), exist_ok=True)
        assert pk.standaard_aan(map_) is True
    finally:
        shutil.rmtree(map_, ignore_errors=True)


def test_map_bestaat_nog_niet_is_ook_vers():
    map_ = os.path.join(tempfile.mkdtemp(prefix="pk-leeg-"), "Bootharoo")
    assert pk.standaard_aan(map_) is True


def test_bestaande_installatie_laat_de_schakelaar_uit():
    map_ = tempfile.mkdtemp(prefix="pk-bestaand-")
    try:
        with open(os.path.join(map_, "settings.json"), "w") as f:
            json.dump({"printer_name": "DP-QW410 (Kopie 1)"}, f)
        assert pk.standaard_aan(map_) is False
    finally:
        shutil.rmtree(map_, ignore_errors=True)


def test_stukke_instellingen_laten_de_schakelaar_uit():
    """Het geval waar zoiets in het echt op stukgaat: settings.json bestaat
    maar is halverwege afgebroken. Twijfel is uit."""
    map_ = tempfile.mkdtemp(prefix="pk-stuk-")
    try:
        with open(os.path.join(map_, "settings.json"), "w") as f:
            f.write('{"printer_name": "DP-QW4')
        assert pk.standaard_aan(map_) is False
    finally:
        shutil.rmtree(map_, ignore_errors=True)


def test_onleesbare_map_laat_de_schakelaar_uit():
    map_ = tempfile.mkdtemp(prefix="pk-dicht-")
    events = os.path.join(map_, "events")
    os.makedirs(events)
    try:
        os.chmod(events, 0o000)
        if os.access(events, os.R_OK):
            return  # draait als root; deze toets zegt dan niets
        assert pk.standaard_aan(map_) is False
    finally:
        os.chmod(events, 0o700)
        shutil.rmtree(map_, ignore_errors=True)


def test_alleen_een_vastgelegd_papierprofiel_is_al_een_bestaande_booth():
    map_ = tempfile.mkdtemp(prefix="pk-devmode-")
    try:
        with open(os.path.join(map_, "printer_devmode_DP_QW410.bin"), "wb") as f:
            f.write(b"\x00" * 64)
        assert pk.standaard_aan(map_) is False
    finally:
        shutil.rmtree(map_, ignore_errors=True)


def test_standaard_wordt_onthouden():
    map_ = tempfile.mkdtemp(prefix="pk-onthoud-")
    try:
        instellingen = {}
        aan = pk.bepaal_automaat_aan(
            lambda s, d=None: instellingen.get(s, d),
            lambda s, w: instellingen.__setitem__(s, w),
            map_)
        assert aan is True
        assert instellingen[pk.SLEUTEL_AUTO] is True
        # Tweede start: DATA_DIR is inmiddels niet leeg meer, maar de keuze
        # staat vast en mag niet vanzelf omslaan.
        with open(os.path.join(map_, "settings.json"), "w") as f:
            json.dump(instellingen, f)
        opnieuw = pk.bepaal_automaat_aan(
            lambda s, d=None: instellingen.get(s, d),
            lambda s, w: instellingen.__setitem__(s, w),
            map_)
        assert opnieuw is True
    finally:
        shutil.rmtree(map_, ignore_errors=True)


def test_opgeslagen_keuze_wint_van_de_standaard():
    map_ = tempfile.mkdtemp(prefix="pk-keuze-")
    try:
        instellingen = {pk.SLEUTEL_AUTO: True}
        with open(os.path.join(map_, "settings.json"), "w") as f:
            json.dump(instellingen, f)
        aan = pk.bepaal_automaat_aan(
            lambda s, d=None: instellingen.get(s, d),
            lambda s, w: instellingen.__setitem__(s, w),
            map_)
        assert aan is True, "de eigen keuze van de verhuurder werd overruled"
    finally:
        shutil.rmtree(map_, ignore_errors=True)


# ── 8. Het papierprofiel verhuist mee ───────────────────────────────────

def test_devmode_verhuist_mee_bij_zelfde_stuurprogramma():
    map_ = tempfile.mkdtemp(prefix="pk-dm-")
    try:
        def pad(naam, sleutel=None):
            veilig = "".join(c if c.isalnum() else "_" for c in naam)
            staart = f"_{sleutel}" if sleutel else ""
            return os.path.join(map_, f"printer_devmode_{veilig}{staart}.bin")

        with open(pad("DP-QW410", "4x6_cut"), "wb") as f:
            f.write(b"BLOB-CUT")
        with open(pad("DP-QW410", None), "wb") as f:
            f.write(b"BLOB-LEGACY")

        overgenomen, ontbreekt = pk.neem_devmode_over(
            "DP-QW410", "DP-QW410 (Kopie 1)", zelfde_stuurprogramma=True,
            pad_functie=pad, sleutels=(None, "4x6_nocut", "4x6_cut", "4x3"))

        assert "4x6_cut" in overgenomen
        assert "legacy" in overgenomen
        assert set(ontbreekt) == {"4x6_nocut", "4x3"}
        with open(pad("DP-QW410 (Kopie 1)", "4x6_cut"), "rb") as f:
            assert f.read() == b"BLOB-CUT"
    finally:
        shutil.rmtree(map_, ignore_errors=True)


def test_devmode_verhuist_niet_naar_een_ander_stuurprogramma():
    """Een DEVMODE-blob hoort bij een driver. Hem naar een ander merk kopiëren
    levert een blob die de driver niet begrijpt."""
    map_ = tempfile.mkdtemp(prefix="pk-dm2-")
    try:
        def pad(naam, sleutel=None):
            veilig = "".join(c if c.isalnum() else "_" for c in naam)
            staart = f"_{sleutel}" if sleutel else ""
            return os.path.join(map_, f"printer_devmode_{veilig}{staart}.bin")

        with open(pad("HiTi P525L", None), "wb") as f:
            f.write(b"HITI-BLOB")
        overgenomen, ontbreekt = pk.neem_devmode_over(
            "HiTi P525L", "DP-QW410", zelfde_stuurprogramma=False,
            pad_functie=pad, sleutels=(None,))
        assert overgenomen == []
        assert ontbreekt == ["legacy"]
        assert not os.path.exists(pad("DP-QW410", None))
    finally:
        shutil.rmtree(map_, ignore_errors=True)


def test_devmode_overschrijft_nooit_een_bestaand_profiel():
    map_ = tempfile.mkdtemp(prefix="pk-dm3-")
    try:
        def pad(naam, sleutel=None):
            veilig = "".join(c if c.isalnum() else "_" for c in naam)
            staart = f"_{sleutel}" if sleutel else ""
            return os.path.join(map_, f"printer_devmode_{veilig}{staart}.bin")

        with open(pad("A", None), "wb") as f:
            f.write(b"OUD")
        with open(pad("B", None), "wb") as f:
            f.write(b"AL-VASTGELEGD")
        overgenomen, _ = pk.neem_devmode_over("A", "B", True,
                                              pad_functie=pad, sleutels=(None,))
        assert overgenomen == []
        with open(pad("B", None), "rb") as f:
            assert f.read() == b"AL-VASTGELEGD"
    finally:
        shutil.rmtree(map_, ignore_errors=True)


# ── 9. De regel op het scherm ───────────────────────────────────────────

def test_stand_tekst_zonder_printer_is_duidelijk():
    stand = pk.Stand(printer=None, laatst_gekeken=time.time())
    tekst = pk.stand_tekst(stand, aan=True)
    assert "Geen printer gevonden" in tekst
    assert "niets geprint" in tekst


def test_stand_tekst_meldt_dat_hij_niet_antwoordde():
    stand = pk.Stand(printer="DP-QW410", laatst_gekeken=time.time() - 8,
                     antwoordde=False)
    tekst = pk.stand_tekst(stand, aan=True, nu=time.time())
    assert "antwoordde NIET" in tekst
    assert "8 sec geleden" in tekst


def test_stand_tekst_zonder_controle_belooft_niets():
    assert pk.stand_tekst(None, aan=True) == "Automatisch — nog niet gecontroleerd."


# ── 10. Hoe duur is het beslissen zelf? ─────────────────────────────────

def test_beslissen_kost_vrijwel_niets():
    """De veeg mag niet de nieuwe traagheid worden. Het beslissen zelf is puur
    rekenwerk; de tijd van een veeg zit volledig in de Windows-aanroepen."""
    lijst = [PDF, XPS, ONENOTE, FAX, DNP, DNP_KOPIE, HITI, ONBEKEND, KANTOOR]
    a = Keuzeautomaat()
    a.beoordeel(lijst)
    t0 = time.perf_counter()
    ronden = 2000
    for _ in range(ronden):
        a.beoordeel(lijst)
    per_keer_us = (time.perf_counter() - t0) / ronden * 1e6
    print(f"    beslissen: {per_keer_us:.1f} µs per veeg ({len(lijst)} printers)")
    assert per_keer_us < 500, f"{per_keer_us:.0f} µs is te duur voor elke 3 seconden"


if __name__ == "__main__":
    toetsen = [(k, v) for k, v in sorted(globals().items())
               if k.startswith("test_") and callable(v)]
    print(f"{len(toetsen)} toetsen\n")
    fouten = []
    for naam, fn in toetsen:
        try:
            fn()
            print(f"  OK    {naam}")
        except AssertionError as e:
            print(f"  FOUT  {naam}: {e}")
            fouten.append(naam)
        except Exception as e:
            print(f"  STUK  {naam}: {type(e).__name__}: {e}")
            fouten.append(naam)
    print("")
    if fouten:
        print(f"PRINTERKEUZE: {len(fouten)} fout(en)")
        sys.exit(1)
    print("PRINTERKEUZE: alles klopt")
