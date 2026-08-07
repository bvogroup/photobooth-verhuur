"""Toetst dat de QR-code zonder internet te bouwen is, en altijd naar
MyBoothBox wijst.

Waarom dit bestaat
------------------
De QR hing aan de upload: lukte die niet, dan kreeg de gast geen code. Dat was
te verdedigen zolang de enige terugval het adres van de booth op het
plaatselijke netwerk was — dat werkt op een feest nooit. Maar de goede URL is
lokaal te maken: de sjabloon-URL plus het sessie-id, en dat id maakt de booth
zelf voordat er iets geüpload is.

Wat er dus nooit meer mag gebeuren:
  * een QR die naar 192.168.x.x of een ander lokaal adres wijst;
  * geen QR omdat er geen internet was.

Draait zonder netwerk en zonder beeldscherm:

    python test_qr.py
"""

import os
import sys

APP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Het logboek van de bouwserver is Windows-cp1252, en dat kent lang niet alle
# tekens die hier gebruikt worden — een pijltje in een boodschap liet de hele
# toets omvallen met een UnicodeEncodeError, en daarmee de bouw. De uitvoer
# gaat daarom expliciet in UTF-8, en wat er dan nog niet in kan wordt vervangen
# in plaats van dat het de boel tegenhoudt. Een toets hoort om te vallen over
# wat hij toetst, niet over hoe hij dat opschrijft.
for _stroom in (sys.stdout, sys.stderr):
    try:
        _stroom.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


fouten = []


def eis(voorwaarde, boodschap):
    if voorwaarde:
        print(f"  ok    {boodschap}", flush=True)
    else:
        print(f"  FOUT  {boodschap}", flush=True)
        fouten.append(boodschap)


def toets_url():
    print("\nDe URL", flush=True)
    # Zonder omgevingsvariabele, zodat het sjabloon uit config.py geldt —
    # dat is wat er in de installer meegaat.
    os.environ.pop("BOOTHAROO_GALLERY_URL", None)
    import config
    from cloud_storage import gallery_url_for

    sessie = "20260807_115009_506be0da"
    url = gallery_url_for(sessie)
    print(f"        {url}", flush=True)

    eis(url.startswith("https://"), "de URL is https")
    eis("myboothbox.nl" in url,
        f"de URL wijst naar MyBoothBox ({url})")
    eis(sessie in url, "het sessie-id staat erin")
    eis("192.168." not in url and "127.0.0.1" not in url
        and "localhost" not in url and ":8080" not in url,
        "geen lokaal netwerkadres")
    eis("{session_id}" not in url, "de plaatshouder is ingevuld")
    eis(config.CLOUD_GALLERY_URL_TEMPLATE.startswith("https://myboothbox.nl/"),
        "het meegeleverde sjabloon wijst naar MyBoothBox")

    # Een leeg sessie-id levert geen half adres op waar de gast niets aan heeft.
    eis(gallery_url_for("") != url, "een leeg sessie-id geeft een andere URL")

    # En de code moet er echt van te maken zijn, zonder netwerk.
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from qr_generator import generate_qr_pixmap
    pm = generate_qr_pixmap(url, size=360)
    eis(not pm.isNull() and pm.width() == 360,
        f"er komt een QR-plaatje uit ({pm.width()}x{pm.height()})")


def toets_bedrading():
    """De QR mag nergens meer op de upload wachten."""
    print("\nDe bedrading in photobooth.py", flush=True)
    bron = open(os.path.join(APP, "photobooth.py"), encoding="utf-8").read()

    eis("def _galerij_url(self)" in bron, "_galerij_url() bestaat")
    eis("gallery_url_for" in bron,
        "de URL komt uit gallery_url_for(), niet uit de upload")
    eis("def _poll_qr_cloud_url" not in bron,
        "er wordt niet meer op een cloud-URL gepolld")
    eis("generate_qr_pixmap(cloud_url" not in bron,
        "de QR wordt nergens meer uit de upload-URL gemaakt")
    eis("get_local_ip" not in bron,
        "er is geen lokaal netwerkadres meer in beeld")
    eis("Upload MISLUKT" in bron,
        "een mislukte upload wordt wel eerlijk in het logboek gemeld")


def main():
    for naam, doen in (("de URL", toets_url), ("de bedrading", toets_bedrading)):
        try:
            doen()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            print(f"  FOUT  {naam} klapte om: {type(exc).__name__}: {exc}",
                  flush=True)
            fouten.append(f"{naam} klapte om: {exc}")

    print("", flush=True)
    if fouten:
        print(f"QR: {len(fouten)} fout(en)", flush=True)
        for f in fouten:
            print(f"  - {f}", flush=True)
        return 1
    print("QR: alles klopt", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
