"""De booth kiest zelf de printer die eraan hangt.

Waarom dit bestaat
------------------
Op de booth zit één USB-poort, dus er kan één printer in. Toch moest er tot nu
toe iemand in Instellingen → Printen de juiste queue aanklikken, en wie dat
vergat miste zijn prints. Dit bestand kijkt zelf welke printers Windows kent,
bij het opstarten en daarna periodiek, en kiest er één.

Wat hier NIET gebeurt
---------------------
Dit bestand raakt het printen zelf niet aan. `printer.py` is vergrendeld (zie
CLAUDE.md) en blijft ongewijzigd; de automaat staat er náást en zet hooguit
`config.PRINTER_NAME`. Staat de schakelaar uit, dan draait hier niets — geen
draad, geen periodieke bevraging, geen enkele Windows-aanroep. De software
gedraagt zich dan exact zoals voor deze wijziging.

De drie regels die dit veilig houden
------------------------------------
1. **Nooit naar een virtuele bestemming.** "Microsoft Print to PDF" is voor
   Windows een printer als elke andere. Kiest de booth die, dan verdwijnen de
   prints van de gasten in een bestand dat niemand opent — op een feest, zonder
   dat iemand het merkt. We sluiten daarom uit wat aantoonbaar geen fysiek
   apparaat is, en kiezen verder alles wat overblijft. Bewust géén lijst met
   goedgekeurde modellen: een nieuw of geleend apparaat dat daar niet op staat
   zou dan alsnog handwerk kosten, en dat is precies wat dit moest afschaffen.
2. **Nooit wisselen tijdens een print.** Een wissel halverwege een vel is een
   mislukte print op papier dat geld kost.
3. **Nooit wisselen bij één hik.** Een printer die een seconde niet antwoordt
   omdat hij papier pakt, is niet weg. Er zijn `drempel` mislukte controles op
   rij nodig voordat er opnieuw gezocht wordt.

Hoe snel er gekeken wordt
-------------------------
De opdrachtgever wil "zo kort mogelijk". Niet elke controle kost hetzelfde, dus
er zijn er drie, met elk hun eigen tempo:

* **Namenlijst (goedkoop, elke veeg).** `EnumPrinters` met alléén
  PRINTER_ENUM_LOCAL, informatieniveau 4: namen en kenmerken uit de lokale
  registry, geen netwerkverkeer. Dat is het tegenovergestelde van
  `printer.get_available_printers()`, die er PRINTER_ENUM_CONNECTIONS bij doet
  en daarmee elke printserver bevraagt — photobooth.py noemt die aanroep in
  `_update_status` met zoveel woorden "de traagste aanroep in deze software",
  goed voor seconden. Die versie gebruiken we hier dus niet.
* **Levenscontrole (goedkoop, elke veeg, alleen de gekozen printer).**
  `OpenPrinter` + `GetPrinter` niveau 2 op één queue. Dezelfde aanroep die
  `printer.check_printer_status()` vóór elke print doet — die zit al in het
  printpad, dus hij is aantoonbaar goedkoop genoeg.
* **Volledige lijst (duurder, alleen bij verandering).** `EnumPrinters` niveau
  2 geeft ook poort en stuurprogramma, en bouwt per queue een DEVMODE op. Dat
  hebben we alleen nodig als er iets ingeplugd of uitgetrokken is, dus draait
  het alleen als de namenlijst veranderde.

Wat we NIET doen is de echte printer bevragen. `dnp_status.read_via_ui_automation`
opent de dialoog van de driver en heeft een time-out van 8 seconden; de
`StatusPoller` houdt die dialoog open en komt dan nog op ~1 seconde per poll
(gemeten op echte hardware, zie dnp_status.py). Dat kan niet elke seconde, en
het hoeft ook niet: die poller draait al op 4 seconden en beantwoordt een andere
vraag ("wat is er mis met de printer"), niet de onze ("is er nog een printer").

De veeg draait op een eigen draad, net als `StatusPoller`. De hoofddraad krijgt
alleen een terugmelding.
"""

from __future__ import annotations

import glob
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple


# ── Instellingssleutels in settings.json ────────────────────────────────
SLEUTEL_AUTO = "printer_auto_keuze"          # bool — staat de automaat aan?
SLEUTEL_HANDMATIG = "printer_keuze_handmatig"  # str  — zelf gekozen queue-naam

# Standaard-tempo. Drie mislukte controles op 3 seconden = ~9 seconden voordat
# er opnieuw gezocht wordt. Kort genoeg om onopgemerkt te blijven, lang genoeg
# om een printer die even bezig is niet af te schrijven.
VEEG_INTERVAL_SEC = 3.0
MISSER_DREMPEL = 3


# ── Windows-vlaggen ─────────────────────────────────────────────────────
# Uitgeschreven in plaats van via win32print geïmporteerd, zodat dit bestand
# ook buiten Windows te lezen en te toetsen is.
KENMERK_WACHTRIJ_OFFLINE = 0x00000400   # PRINTER_ATTRIBUTE_WORK_OFFLINE
KENMERK_NETWERK = 0x00000010            # PRINTER_ATTRIBUTE_NETWORK
KENMERK_LOKAAL = 0x00000040             # PRINTER_ATTRIBUTE_LOCAL

STATUS_WORDT_VERWIJDERD = 0x00000004    # PRINTER_STATUS_PENDING_DELETION
STATUS_OFFLINE = 0x00000080             # PRINTER_STATUS_OFFLINE
STATUS_BEZIG_PRINTEN = 0x00000400       # PRINTER_STATUS_PRINTING
STATUS_NIET_BESCHIKBAAR = 0x00001000    # PRINTER_STATUS_NOT_AVAILABLE
STATUS_SERVER_ONBEKEND = 0x00800000     # PRINTER_STATUS_SERVER_UNKNOWN

# Alleen déze vlaggen betekenen "er zit niets meer aan die kant". Papier op,
# klep open of lint op zijn géén reden om van printer te wisselen — dat is een
# levende printer met een probleem dat de verhuurder oplost. Wisselen zou de
# prints naar een ander apparaat sturen terwijl er hier alleen papier in moet.
STATUS_DOOD = (STATUS_WORDT_VERWIJDERD | STATUS_OFFLINE
               | STATUS_NIET_BESCHIKBAAR | STATUS_SERVER_ONBEKEND)


# ── Wat aantoonbaar geen fysieke printer is ─────────────────────────────
#
# Deze lijst is kort en verandert niet mee met nieuwe hardware: het zijn de
# virtuele bestemmingen die Windows en veel bureau-software meeleveren. Alles
# wat hier NIET op staat wordt gewoon gekozen, ook een model dat wij nooit
# gezien hebben.
VIRTUELE_NAAMDELEN = (
    "microsoft print to pdf",
    "microsoft xps document writer",
    "xps document writer",
    "onenote",
    "fax",
    "adobe pdf",
    "print to file",
    "afdrukken naar bestand",
    "send to ",
    "snagit",
    "webex",
    "kindle",
    "evernote",
    "anydesk",
    "teamviewer",
    "(redirected",
    "(omgeleid",
    "root print queue",
    # Generiek: geen enkele fotoprinter heet naar een bestandsformaat, en
    # elke PDF-schrijver (CutePDF, PDFCreator, doPDF, Bullzip, PrimoPDF,
    # Foxit, Nitro, novaPDF, ...) draagt het in zijn naam. We hoeven die
    # merken dus niet één voor één op te sommen.
    "pdf",
    "xps",
)

VIRTUELE_POORTEN = (
    "portprompt:",   # Microsoft Print to PDF
    "xpsport:",      # XPS Document Writer
    "shrfax:",       # Microsoft Shared Fax
    "nul:",
    "file:",
    "onenote",
    "pdf",
    "xps",
)

VIRTUELE_STUURPROGRAMMADELEN = (
    "microsoft print to pdf",
    "microsoft xps document writer",
    "microsoft shared fax",
    "send to microsoft onenote",
    "local port",
    "pdf",
    "xps",
)


@dataclass(frozen=True)
class Kandidaat:
    """Eén printer-queue zoals Windows hem kent."""
    naam: str
    poort: str = ""
    stuurprogramma: str = ""
    kenmerken: int = 0

    @property
    def is_netwerk(self) -> bool:
        return bool(self.kenmerken & KENMERK_NETWERK)


@dataclass
class Besluit:
    """De uitkomst van één beoordeling."""
    printer: Optional[str]          # wat er vanaf nu gebruikt wordt (None = niets)
    gewisseld: bool                 # is dit een verandering t.o.v. daarvoor?
    reden: str                      # machine-leesbaar, voor logs en toetsen
    melding: str = ""               # wat er op het scherm hoort te staan
    vorige: Optional[str] = None    # wat het was (voor DEVMODE-overname)
    missers: int = 0                # mislukte controles op rij
    kandidaten: Tuple[str, ...] = ()  # de echte printers die we zagen
    # Het stuurprogramma van beide queues, door de wacht ingevuld. De
    # hoofddraad heeft dit nodig om te bepalen of het papierprofiel mee mag
    # verhuizen, en mag daarvoor zelf geen Windows-aanroep doen.
    vorig_stuurprogramma: str = ""
    nieuw_stuurprogramma: str = ""

    @property
    def zelfde_stuurprogramma(self) -> bool:
        return bool(self.vorig_stuurprogramma
                    and self.vorig_stuurprogramma == self.nieuw_stuurprogramma)


def is_virtueel(kandidaat: Kandidaat) -> Tuple[bool, str]:
    """Is dit aantoonbaar geen fysiek apparaat? Geeft (ja/nee, reden)."""
    naam = (kandidaat.naam or "").lower()
    for deel in VIRTUELE_NAAMDELEN:
        if deel in naam:
            return True, f"naam bevat {deel!r}"
    poort = (kandidaat.poort or "").lower()
    for deel in VIRTUELE_POORTEN:
        if poort.startswith(deel) or deel in poort:
            return True, f"poort {kandidaat.poort!r}"
    stuur = (kandidaat.stuurprogramma or "").lower()
    for deel in VIRTUELE_STUURPROGRAMMADELEN:
        if deel in stuur:
            return True, f"stuurprogramma {kandidaat.stuurprogramma!r}"
    return False, ""


def echte_printers(kandidaten: Iterable[Kandidaat]) -> List[Kandidaat]:
    """Alles wat overblijft nadat de virtuele bestemmingen eruit zijn."""
    overgebleven = []
    for k in kandidaten:
        if not (k.naam or "").strip():
            continue
        virtueel, _ = is_virtueel(k)
        if not virtueel:
            overgebleven.append(k)
    return overgebleven


def _poortrang(kandidaat: Kandidaat) -> int:
    """0 = aan deze machine via USB, 1 = anderszins lokaal, 2 = over het net."""
    poort = (kandidaat.poort or "").lower()
    if poort.startswith("usb") or poort.startswith("dot4"):
        return 0
    if kandidaat.is_netwerk:
        return 2
    if poort.startswith(("wsd", "ip_", "tcp", "\\\\", "http")):
        return 2
    return 1


def rangschik(kandidaten: Sequence[Kandidaat],
              levend: Optional[Dict[str, bool]] = None) -> List[Kandidaat]:
    """Zet de kandidaten op volgorde van voorkeur; de eerste wint.

    De poort telt zwaarder dan of hij antwoordt. Dat is met opzet: de booth
    heeft één USB-poort, en een printer die daar in zit is de printer waar de
    gast bij staat. Zou "antwoordt" bovenaan staan, dan kon een USB-printer die
    even niet reageert de booth naar de kantoorprinter verderop sturen — de
    prints komen dan ergens uit waar niemand ze ophaalt.

    Binnen dezelfde poortsoort wint wél wie antwoordt. Dat is het geval dat in
    de praktijk voorkomt: Windows laat na een unit-swap twee queues achter
    ("DP-QW410" en "DP-QW410 (Kopie 1)") waarvan er maar één echt bediend wordt.
    """
    levend = levend or {}
    return sorted(
        kandidaten,
        key=lambda k: (
            _poortrang(k),
            0 if levend.get(k.naam, True) else 1,
            (k.naam or "").lower(),
        ),
    )


class Keuzeautomaat:
    """De keuzelogica. Kent geen Windows en geen scherm — puur beslissen.

    Hierdoor is elk geval te toetsen met een nagebootste printerlijst: alleen
    een PDF-printer, een echte printer erbij, een printer die verdwijnt, een
    handmatige keuze, een lopende print.
    """

    def __init__(self, drempel: int = MISSER_DREMPEL,
                 gekozen: Optional[str] = None,
                 handmatig_naam: Optional[str] = None):
        self.drempel = max(1, int(drempel))
        self.gekozen = gekozen or None
        # De naam die de verhuurder zelf aanklikte. Zolang die printer bestaat
        # is hij de keuze — de automaat overrulet dat nooit.
        self.handmatig_naam = handmatig_naam or None
        self.missers = 0
        # Wordt gezet door terug_naar_automatisch(): de eerstvolgende
        # beoordeling kiest opnieuw in plaats van de huidige keuze te bevestigen.
        self._herzie = False

    # ── de enige methode die iets beslist ───────────────────────────────
    def beoordeel(self, kandidaten: Iterable[Kandidaat],
                  print_bezig: bool = False,
                  levend: Optional[Dict[str, bool]] = None) -> Besluit:
        """Weeg één momentopname en zeg wat de printer vanaf nu is."""
        levend = dict(levend or {})
        echt = echte_printers(kandidaten)
        namen = {k.naam for k in echt}
        lijst = tuple(sorted(namen))

        # 1. Loopt er een print? Dan gebeurt er niets. Ook de misser-teller
        #    blijft staan: een printer die aan het printen is antwoordt soms
        #    niet op een statusvraag, en dat is geen aanwijzing dat hij weg is.
        if print_bezig:
            return self._besluit(self.gekozen, False, "print-bezig",
                                 "Printer wordt niet gewisseld tijdens een print.",
                                 lijst)

        # 2. Een handmatige keuze wint zolang die printer bestaat.
        if self.handmatig_naam:
            if self.handmatig_naam in namen:
                self.missers = 0
                if self.gekozen != self.handmatig_naam:
                    vorige, self.gekozen = self.gekozen, self.handmatig_naam
                    return self._besluit(
                        self.gekozen, True, "handmatig-hersteld",
                        f"Terug naar de zelf gekozen printer: {self.gekozen}",
                        lijst, vorige)
                return self._besluit(self.gekozen, False, "handmatig",
                                     f"Zelf gekozen: {self.gekozen}", lijst)
            # De zelf gekozen printer bestaat niet meer. Dat is geen keuze die
            # we overrulen maar een keuze die nergens meer naar wijst — er valt
            # niets te printen zolang we eraan vasthouden. Na de drempel kiest
            # de automaat weer, maar de naam blijft bewaard: komt de printer
            # terug, dan springen we er meteen op terug (tak hierboven).
            self.missers += 1
            if self.missers < self.drempel:
                return self._besluit(self.gekozen, False, "hik",
                                     "", lijst)

        # 3. Automatisch: voldoet de huidige keuze nog?
        elif self.gekozen is not None and not self._herzie:
            in_lijst = self.gekozen in namen
            antwoordt = levend.get(self.gekozen, True)
            if in_lijst and antwoordt:
                self.missers = 0
                return self._besluit(self.gekozen, False, "ongewijzigd",
                                     "", lijst)
            self.missers += 1
            if self.missers < self.drempel:
                # Eén of twee keer niets horen is geen storing.
                return self._besluit(self.gekozen, False, "hik", "", lijst)

        # 4. Opnieuw kiezen.
        self._herzie = False
        if not echt:
            vorige, self.gekozen = self.gekozen, None
            return self._besluit(
                None, vorige is not None, "niets-gevonden",
                "Geen printer gevonden. Sluit de printer aan en zet hem aan; "
                "er wordt niets geprint zolang er geen printer is.",
                lijst, vorige)

        keus = rangschik(echt, levend)[0].naam
        if keus == self.gekozen:
            # De enige printer die er is, is de printer die we al hadden.
            # Niet wisselen dus, en de teller op de drempel laten staan zodat
            # hij niet eindeloos oploopt.
            self.missers = self.drempel
            return self._besluit(self.gekozen, False, "geen-alternatief",
                                 "", lijst)

        vorige, self.gekozen = self.gekozen, keus
        self.missers = 0
        if vorige is None:
            return self._besluit(keus, True, "eerste-keuze",
                                 f"Printer gevonden: {keus}", lijst, vorige)
        return self._besluit(keus, True, "gewisseld",
                             f"Overgestapt op {keus} (was {vorige})",
                             lijst, vorige)

    def _besluit(self, printer, gewisseld, reden, melding, lijst, vorige=None):
        return Besluit(printer=printer, gewisseld=gewisseld, reden=reden,
                       melding=melding, vorige=vorige, missers=self.missers,
                       kandidaten=lijst)

    # ── handmatig / automatisch omzetten ────────────────────────────────
    def zet_handmatig(self, naam: str) -> None:
        """De verhuurder klikte zelf een printer aan."""
        self.handmatig_naam = naam or None
        self.gekozen = naam or None
        self.missers = 0

    def terug_naar_automatisch(self) -> None:
        """De handmatige keuze wordt losgelaten; de automaat kiest weer.

        De huidige keuze blijft staan tot de eerstvolgende beoordeling, zodat
        er geen moment is waarop de booth zonder printer zit. Die beoordeling
        kiest wél opnieuw in plaats van de bestaande keuze te bevestigen —
        anders zou "terug naar automatisch" zichtbaar niets doen.
        """
        self.handmatig_naam = None
        self.missers = 0
        self._herzie = True


# ── Windows-kant ────────────────────────────────────────────────────────
# Alles hieronder praat met win32print. Op een machine zonder Windows geven de
# functies een lege lijst terug in plaats van te ontploffen, zodat de rest van
# dit bestand overal te draaien en te toetsen is.

def _win32print():
    try:
        import win32print
        return win32print
    except Exception:
        return None


def snelle_namen() -> Optional[List[str]]:
    """Alleen de namen van de lokale queues. De goedkoopste vraag die er is.

    Informatieniveau 4 geeft naam, server en kenmerken en verder niets — geen
    DEVMODE, geen beveiligingsbeschrijving, geen netwerkverkeer. Valt terug op
    niveau 1 als de pywin32-versie niveau 4 niet aankan.

    Geeft None als de vraag zelf niet gesteld kon worden. Dat is iets anders
    dan een lege lijst: "ik weet het niet" mag nooit als "er is geen printer"
    gelezen worden, want dan zou één hik in de spooler een werkende printer
    kunnen wegnemen.
    """
    wp = _win32print()
    if wp is None:
        return None
    for niveau in (4, 1):
        try:
            rijen = wp.EnumPrinters(wp.PRINTER_ENUM_LOCAL, None, niveau)
        except Exception:
            continue
        namen = []
        for rij in rijen:
            if isinstance(rij, dict):
                naam = rij.get("pPrinterName") or rij.get("pName") or ""
            elif isinstance(rij, (tuple, list)) and len(rij) >= 3:
                naam = rij[2]  # niveau 1: (vlaggen, omschrijving, naam, notitie)
            else:
                naam = str(rij)
            if naam:
                namen.append(naam)
        return namen
    return None


def lees_kandidaten() -> List[Kandidaat]:
    """De volledige lijst met poort en stuurprogramma. Alleen lokale queues.

    Duurder dan `snelle_namen()` omdat Windows per queue een DEVMODE opbouwt.
    Wordt daarom alleen aangeroepen als de namenlijst veranderde.
    """
    wp = _win32print()
    if wp is None:
        return []
    try:
        rijen = wp.EnumPrinters(wp.PRINTER_ENUM_LOCAL, None, 2)
    except Exception as e:
        print(f"[PRINTERKEUZE] Lijst opvragen mislukt: {e}")
        return []
    kandidaten = []
    for rij in rijen:
        try:
            if isinstance(rij, dict):
                kandidaten.append(Kandidaat(
                    naam=rij.get("pPrinterName") or "",
                    poort=rij.get("pPortName") or "",
                    stuurprogramma=rij.get("pDriverName") or "",
                    kenmerken=int(rij.get("Attributes") or 0),
                ))
            elif isinstance(rij, (tuple, list)) and len(rij) >= 3:
                kandidaten.append(Kandidaat(naam=rij[2]))
        except Exception:
            pass
    return kandidaten


def printer_leeft(naam: str) -> Optional[bool]:
    """Antwoordt deze queue nog? None = niet vast te stellen.

    Papier op, klep open of lint op tellen bewust NIET als dood: dat is een
    levende printer met een probleem, en daarvoor moet je niet van printer
    wisselen maar papier bijvullen.
    """
    wp = _win32print()
    if wp is None or not naam:
        return None
    try:
        h = wp.OpenPrinter(naam)
    except Exception:
        # De queue bestaat niet (meer) of is niet te openen.
        return False
    try:
        info = wp.GetPrinter(h, 2)
    except Exception:
        return None
    finally:
        try:
            wp.ClosePrinter(h)
        except Exception:
            pass
    try:
        status = int(info.get("Status", 0) or 0)
        kenmerken = int(info.get("Attributes", 0) or 0)
    except Exception:
        return None
    if kenmerken & KENMERK_WACHTRIJ_OFFLINE:
        return False
    if status & STATUS_DOOD:
        return False
    return True


def printer_is_bezig(naam: str) -> bool:
    """Staat er op dit moment een vel in deze printer? Extra vangnet bovenop
    de vlag die de app zelf zet rond een print."""
    wp = _win32print()
    if wp is None or not naam:
        return False
    try:
        h = wp.OpenPrinter(naam)
    except Exception:
        return False
    try:
        info = wp.GetPrinter(h, 2)
        if int(info.get("Status", 0) or 0) & STATUS_BEZIG_PRINTEN:
            return True
        return int(info.get("cJobs", 0) or 0) > 0
    except Exception:
        return False
    finally:
        try:
            wp.ClosePrinter(h)
        except Exception:
            pass


# ── DEVMODE meeverhuizen ────────────────────────────────────────────────
#
# Het opgeslagen papierprofiel hangt aan de printernáám: printer._devmode_path
# maakt van de queue-naam een bestandsnaam. Wisselt de naam, dan vindt
# printer.load_saved_devmode niets meer. Wat er dan gebeurt verschilt per pad,
# en het gevaarlijke geval is het stille:
#
#   * DNP met profiel-sleutel  → print_photo werpt een PrinterError ("profiel
#     niet vastgelegd"). Luid, dus zichtbaar.
#   * HiTi / legacy (geen sleutel) → valt terug op de driver-standaard. De
#     print komt er wél uit, maar zonder het vastgelegde papierformaat en
#     zonder split/cut. Niemand ziet het tot het vel eruit rolt.
#
# Daarom nemen we bij een wissel de blobs mee, maar alleen als beide queues
# hetzelfde stuurprogramma gebruiken. Dat is precies het juiste criterium: een
# DEVMODE-blob hoort bij een driver, niet bij een model. printer.load_saved_devmode
# zet de printernaam in de blob zelf al goed (_patch_devmode_device_name), dus
# een kale bestandskopie volstaat.

def _standaard_devmode_pad(naam: str, sleutel: Optional[str] = None) -> str:
    from printer import _devmode_path
    return _devmode_path(naam, sleutel)


def _standaard_profielsleutels() -> Tuple[Optional[str], ...]:
    try:
        from printer import DNP_PROFILE_KEYS
        return (None,) + tuple(DNP_PROFILE_KEYS)
    except Exception:
        return (None, "4x6_nocut", "4x6_cut", "4x3")


def neem_devmode_over(vorige: Optional[str], nieuwe: Optional[str],
                      zelfde_stuurprogramma: bool,
                      pad_functie: Optional[Callable] = None,
                      sleutels: Optional[Sequence] = None
                      ) -> Tuple[List[str], List[str]]:
    """Verhuis de opgeslagen papierprofielen mee naar de nieuwe queue.

    Geeft (overgenomen, ontbreekt) terug: welke profielen zijn meegekopieerd,
    en welke heeft de nieuwe printer daarna nog steeds niet.
    """
    pad = pad_functie or _standaard_devmode_pad
    sleutels = sleutels if sleutels is not None else _standaard_profielsleutels()
    overgenomen: List[str] = []
    ontbreekt: List[str] = []
    if not nieuwe:
        return overgenomen, ontbreekt
    for sleutel in sleutels:
        naam = sleutel or "legacy"
        try:
            doel = pad(nieuwe, sleutel)
            if os.path.isfile(doel):
                continue
            if not (vorige and vorige != nieuwe and zelfde_stuurprogramma):
                ontbreekt.append(naam)
                continue
            bron = pad(vorige, sleutel)
            if not os.path.isfile(bron):
                ontbreekt.append(naam)
                continue
            os.makedirs(os.path.dirname(doel), exist_ok=True)
            with open(bron, "rb") as f:
                blob = f.read()
            with open(doel, "wb") as f:
                f.write(blob)
            overgenomen.append(naam)
        except Exception as e:
            print(f"[PRINTERKEUZE] Profiel {naam} overnemen mislukt: {e}")
            ontbreekt.append(naam)
    return overgenomen, ontbreekt


# ── Standaardstand van de schakelaar ────────────────────────────────────

def _map_heeft_inhoud(pad: str) -> Optional[bool]:
    """True/False, of None als het niet vast te stellen is."""
    try:
        if not os.path.isdir(pad):
            return False
        return any(os.scandir(pad))
    except Exception:
        return None


def standaard_aan(data_dir: str) -> bool:
    """Moet de automaat aan staan op een machine waar niets is ingesteld?

    AAN alleen bij een aantoonbaar verse installatie. Op alles wat er ook maar
    naar riekt dat de software hier al gedraaid heeft: UIT.

    Waarom die scheefheid: er staan booths in het veld die zichzelf bijwerken.
    Gaat de automaat daar ten onrechte aan, dan kan er midden op een feest van
    printer gewisseld worden. Staat hij op een verse installatie ten onrechte
    uit, dan kost dat één tik in de instellingen. Die twee fouten zijn niet
    even erg, dus bij twijfel: uit.

    Let op: `config.py` maakt bij import photos/, backgrounds/, templates/ en
    events/ aan. Het bestaan van DATA_DIR of van die mappen zegt dus niets —
    er wordt op inhoud gekeken.
    """
    try:
        if not data_dir:
            return False
        # settings.json is het duidelijkste teken van "hier is al gewerkt".
        # Bestaat hij maar is hij onleesbaar of stuk, dan is dat twijfel, en
        # twijfel is uit.
        instellingen = os.path.join(data_dir, "settings.json")
        if os.path.exists(instellingen):
            return False
        if os.path.exists(os.path.join(data_dir, "booth_settings.json")):
            return False
        if not os.path.isdir(data_dir):
            return True  # map bestaat nog niet eens → vers
        # Een eerder vastgelegd papierprofiel betekent dat er een printer is
        # ingesteld, ook als settings.json ontbreekt.
        try:
            if glob.glob(os.path.join(data_dir, "printer_devmode_*.bin")):
                return False
        except Exception:
            return False
        # Events of foto's: er is hier gedraaid.
        for submap in ("events", "photos"):
            inhoud = _map_heeft_inhoud(os.path.join(data_dir, submap))
            if inhoud is None:
                return False  # niet te lezen → twijfel → uit
            if inhoud:
                return False
        # Losse bestanden in DATA_DIR die niet van config.py's makedirs komen.
        try:
            bekend = {"photos", "backgrounds", "templates", "events"}
            for item in os.scandir(data_dir):
                if item.name in bekend and item.is_dir():
                    continue
                return False
        except Exception:
            return False
        return True
    except Exception:
        return False


def bepaal_automaat_aan(lees: Callable, schrijf: Callable, data_dir: str) -> bool:
    """Staat de automaat aan? Bepaalt de standaard één keer en onthoudt die.

    `lees(sleutel, standaard)` en `schrijf(sleutel, waarde)` zijn de bestaande
    settings.json-helpers van de app. Zodra de waarde er staat is dit puur een
    uitlezing; de standaardbepaling draait dus maar één keer per machine.

    Dat onthouden is belangrijk: `standaard_aan()` kijkt of DATA_DIR leeg is,
    en zodra de app voor het eerst iets opslaat is die aanwijzing weg. Zonder
    onthouden zou een verse installatie bij de tweede start ineens als upgrade
    gelden en de schakelaar vanzelf omgaan.
    """
    huidig = lees(SLEUTEL_AUTO, None)
    if isinstance(huidig, bool):
        return huidig
    standaard = standaard_aan(data_dir)
    try:
        schrijf(SLEUTEL_AUTO, standaard)
    except Exception:
        pass
    print(f"[PRINTERKEUZE] Automatische printerkeuze standaard "
          f"{'AAN (verse installatie)' if standaard else 'UIT (bestaande installatie)'}")
    return standaard


# ── De wacht ────────────────────────────────────────────────────────────

@dataclass
class Stand:
    """Wat de instellingen op het scherm mogen laten zien."""
    printer: Optional[str] = None
    handmatig: bool = False
    laatst_gekeken: float = 0.0
    antwoordde: Optional[bool] = None
    reden: str = ""
    melding: str = ""
    kandidaten: Tuple[str, ...] = ()
    vegen: int = 0
    veegtijd_ms: Tuple[float, ...] = ()   # laatste metingen, voor verantwoording


class Printerwacht:
    """Kijkt op een eigen draad welke printers er zijn. Nooit op de hoofddraad.

    De terugmelding wordt vanaf de wachtdraad aangeroepen; de aanroeper hoort
    daar een Qt-signaal van te maken zodat het scherm op de hoofddraad bijwerkt
    (zie `StatusPoller` in dnp_status.py, dat doet het net zo).
    """

    def __init__(self,
                 terugmelding: Callable[[Besluit, "Stand"], None],
                 interval_sec: float = VEEG_INTERVAL_SEC,
                 drempel: int = MISSER_DREMPEL,
                 gekozen: Optional[str] = None,
                 handmatig_naam: Optional[str] = None,
                 print_bezig: Optional[Callable[[], bool]] = None,
                 lees_lijst: Optional[Callable[[], List[Kandidaat]]] = None,
                 lees_namen: Optional[Callable[[], List[str]]] = None,
                 leest_leven: Optional[Callable[[str], Optional[bool]]] = None):
        self.automaat = Keuzeautomaat(drempel=drempel, gekozen=gekozen,
                                      handmatig_naam=handmatig_naam)
        self._interval = max(0.5, float(interval_sec))
        self._terugmelding = terugmelding
        self._print_bezig = print_bezig or (lambda: False)
        self._lees_lijst = lees_lijst or lees_kandidaten
        self._lees_namen = lees_namen or snelle_namen
        self._leest_leven = leest_leven or printer_leeft
        self._stop = threading.Event()
        self._draad: Optional[threading.Thread] = None
        self._slot = threading.Lock()
        self._stand = Stand(printer=gekozen, handmatig=bool(handmatig_naam))
        self._vorige_namen: Optional[frozenset] = None
        self._kandidaten: List[Kandidaat] = []
        self._tijden: List[float] = []
        # Welk stuurprogramma hoorde bij welke queue-naam. Blijft bewaard nadat
        # een queue verdwijnt, want juist dán moet de hoofddraad nog kunnen
        # zien of het papierprofiel van de oude naar de nieuwe mag.
        self._stuurprogrammas: Dict[str, str] = {}

    # ── besturing ───────────────────────────────────────────────────────
    def start(self) -> None:
        if self._draad and self._draad.is_alive():
            return
        self._stop.clear()
        self._draad = threading.Thread(target=self._lus, daemon=True,
                                       name="printerwacht")
        self._draad.start()

    def stop(self, wacht_sec: float = 2.0) -> None:
        self._stop.set()
        if self._draad:
            self._draad.join(timeout=wacht_sec)
        self._draad = None

    def stand(self) -> Stand:
        with self._slot:
            return self._stand

    def zet_handmatig(self, naam: str) -> None:
        self.automaat.zet_handmatig(naam)
        with self._slot:
            self._stand.printer = naam or None
            self._stand.handmatig = bool(naam)

    def terug_naar_automatisch(self) -> None:
        self.automaat.terug_naar_automatisch()
        with self._slot:
            self._stand.handmatig = False

    # ── één veeg ────────────────────────────────────────────────────────
    def veeg(self) -> Besluit:
        """Eén controle. Los aanroepbaar, zodat een toets 'm kan stappen."""
        t0 = time.monotonic()

        bezig = False
        try:
            bezig = bool(self._print_bezig())
        except Exception:
            bezig = False

        rauwe_namen = self._lees_namen()
        if rauwe_namen is None:
            # De vraag kon niet gesteld worden. Geen gegevens is geen besluit:
            # we laten alles staan en tellen dit niet als misser.
            return self._stille_veeg(t0)

        namen = frozenset(rauwe_namen)
        if namen != self._vorige_namen:
            # Alleen bij verandering de duurdere lijst met poort en driver.
            self._kandidaten = self._lees_lijst() or []
            self._vorige_namen = namen
            for k in self._kandidaten:
                if k.naam:
                    self._stuurprogrammas[k.naam] = k.stuurprogramma or ""
        # Verdwenen queues mogen niet blijven hangen in de gecachte lijst.
        kandidaten = [k for k in self._kandidaten if k.naam in namen]
        # Kende de goedkope lijst namen die de dure lijst niet opleverde (die
        # aanroep kan op zichzelf mislukken), val dan terug op de naam alleen.
        # Zonder poort en stuurprogramma is de uitsluiting minder scherp, maar
        # een lege lijst zou een werkende printer wegnemen — en dat is erger.
        gezien = {k.naam for k in kandidaten}
        for naam in namen - gezien:
            kandidaten.append(Kandidaat(naam=naam))

        levend: Dict[str, bool] = {}
        huidig = self.automaat.gekozen
        if huidig and not bezig:
            uitkomst = self._leest_leven(huidig)
            if uitkomst is not None:
                levend[huidig] = bool(uitkomst)

        besluit = self.automaat.beoordeel(kandidaten, print_bezig=bezig,
                                          levend=levend)

        # Is er zojuist een andere printer gekozen, dan hebben we die nog niet
        # bevraagd. Eén extra goedkope controle, alleen op de veeg waarin er
        # iets veranderde — anders zou het scherm "antwoord onbekend" tonen bij
        # een printer die gewoon werkt.
        if besluit.printer and not bezig and besluit.printer not in levend:
            uitkomst = self._leest_leven(besluit.printer)
            if uitkomst is not None:
                levend[besluit.printer] = bool(uitkomst)

        besluit.vorig_stuurprogramma = self._stuurprogrammas.get(
            besluit.vorige or "", "")
        besluit.nieuw_stuurprogramma = self._stuurprogrammas.get(
            besluit.printer or "", "")

        duur_ms = (time.monotonic() - t0) * 1000.0
        self._tijden.append(duur_ms)
        if len(self._tijden) > 200:
            del self._tijden[:-200]

        with self._slot:
            self._stand = Stand(
                printer=besluit.printer,
                handmatig=bool(self.automaat.handmatig_naam),
                laatst_gekeken=time.time(),
                antwoordde=levend.get(besluit.printer) if besluit.printer else None,
                reden=besluit.reden,
                melding=besluit.melding,
                kandidaten=besluit.kandidaten,
                vegen=self._stand.vegen + 1,
                veegtijd_ms=tuple(self._tijden[-20:]),
            )
            stand = self._stand

        if besluit.gewisseld:
            print(f"[PRINTERKEUZE] {besluit.reden}: {besluit.vorige!r} -> "
                  f"{besluit.printer!r} (gezien: {', '.join(besluit.kandidaten) or 'niets'})")
        if stand.vegen % 100 == 0 and self._tijden:
            geordend = sorted(self._tijden)
            midden = geordend[len(geordend) // 2]
            print(f"[PRINTERKEUZE] {stand.vegen} vegen — mediaan {midden:.1f} ms, "
                  f"traagste {geordend[-1]:.1f} ms")

        try:
            self._terugmelding(besluit, stand)
        except Exception as e:
            print(f"[PRINTERKEUZE] Terugmelding-fout: {e}")
        return besluit

    def _stille_veeg(self, t0: float) -> Besluit:
        """De lijst was niet op te vragen. Niets veranderen, niets tellen."""
        with self._slot:
            self._stand = Stand(
                printer=self.automaat.gekozen,
                handmatig=bool(self.automaat.handmatig_naam),
                laatst_gekeken=self._stand.laatst_gekeken,
                antwoordde=self._stand.antwoordde,
                reden="geen-gegevens",
                melding=self._stand.melding,
                kandidaten=self._stand.kandidaten,
                vegen=self._stand.vegen + 1,
                veegtijd_ms=self._stand.veegtijd_ms,
            )
        return Besluit(printer=self.automaat.gekozen, gewisseld=False,
                       reden="geen-gegevens", missers=self.automaat.missers)

    def _lus(self) -> None:
        while not self._stop.is_set():
            try:
                self.veeg()
            except Exception as e:
                print(f"[PRINTERKEUZE] Veeg mislukt: {e}")
            self._stop.wait(self._interval)


# ── Leesbare tekst voor het instellingenscherm ──────────────────────────

def stand_tekst(stand: Optional[Stand], aan: bool,
                handmatige_naam: Optional[str] = None,
                nu: Optional[float] = None) -> str:
    """Eén regel voor Instellingen → Printen.

    Staat de automaat uit, dan hoort hier gewoon de handmatig gekozen printer
    te staan — zonder iets te suggereren over controles die niet draaien.
    """
    if not aan:
        if handmatige_naam:
            return f"{handmatige_naam} — handmatig gekozen"
        return "Geen printer gekozen — kies er zelf een met 'Wijzig'."

    if stand is None or not stand.laatst_gekeken:
        return "Automatisch — nog niet gecontroleerd."

    nu = nu if nu is not None else time.time()
    geleden = max(0, int(nu - stand.laatst_gekeken))
    if geleden < 60:
        wanneer = f"{geleden} sec geleden"
    else:
        wanneer = f"{geleden // 60} min geleden"

    if not stand.printer:
        return (f"Geen printer gevonden ({wanneer} gekeken). "
                f"Er wordt niets geprint zolang er geen printer is.")

    hoe = "handmatig gekozen" if stand.handmatig else "automatisch gekozen"
    if stand.antwoordde is True:
        antwoord = "antwoordde"
    elif stand.antwoordde is False:
        antwoord = "antwoordde NIET"
    else:
        antwoord = "antwoord onbekend"
    return f"{stand.printer} — {hoe} · {wanneer} gecontroleerd, {antwoord}"
