"""Auto-updater — checkt de nieuwste GitHub-release en installeert die.

De repo is publiek, dus de GitHub Releases-API en de installer-assets zijn
zonder token bereikbaar. Flow:
  1. check_for_update()  -> haalt de laatste release van het gekozen kanaal
                            op en vergelijkt de versie
  2. download_installer() -> downloadt de Bootharoo_Setup_*.exe naar temp
  3. run_installer()      -> start de installer (stil); die sluit de draaiende
                             app, update en herstart 'm (zie installer.iss)

De check wordt NOOIT vanzelf gestart. Er zijn precies twee aanroepers, allebei
met een mens erachter: de overdrachtsstroom bij het loskoppelen van een event
en de knop "Controleer op updates" in Geavanceerd. Bij het opstarten en tijdens
een sessie gebeurt er niets.


TWEE KANALEN — PRODUCTIE EN BETA
================================

Er zijn twee kanalen, in te stellen in Geavanceerd (booth_settings.json):

  "production" (standaard) — de nieuwste definitieve release
  "beta"                   — de nieuwste voorloopversie, óók als er een
                             nieuwere definitieve release bestaat

Hoe herkennen we een betaversie? Op twee manieren, en dat is bewust:

  1. het vinkje "prerelease" op de GitHub-release (leidend — dit is wat
     GitHub zelf als waarheid hanteert), en
  2. een achtervoegsel in de tag: v1.5.0-beta.2, -rc.1, -alpha.

Alleen op het vinkje vertrouwen gaat mis zodra iemand het vergeet aan te
zetten: die release komt dan in productie terecht en installeert zichzelf bij
de eerstvolgende overdracht op elke booth. Alleen op de tag vertrouwen gaat
mis zodra iemand een tag anders schrijft. Met allebei is één van de twee
genoeg om een release als beta te herkennen, en dat is de veilige kant: een
twijfelgeval belandt in beta, niet in productie.

Let ook op: het endpoint /releases/latest is hier NIET bruikbaar. Dat slaat
prereleases over, dus daarmee is het betakanaal onmogelijk te bedienen. We
halen daarom de releaselijst op en kiezen zelf.
"""

import os
import re
import subprocess
import tempfile

try:
    import requests
except Exception:
    requests = None

import config

# Lijst van releases, nieuwste eerst. Bewust NIET /releases/latest — dat
# endpoint verzwijgt prereleases en dan is het betakanaal niet te bedienen.
GITHUB_API_RELEASES = (
    "https://api.github.com/repos/bvogroup/photobooth-verhuur/releases"
    "?per_page=30"
)

# Achtervoegsels die een voorloopversie aanduiden: -beta.2, -rc1, -alpha
_PRERELEASE_RE = re.compile(r"-\s*(alpha|beta|rc)[._-]?(\d*)", re.IGNORECASE)

CHANNEL_PRODUCTION = "production"
CHANNEL_BETA = "beta"


def normalize_channel(channel: str) -> str:
    """Onbekende waarde → productie. Nooit per ongeluk op beta uitkomen."""
    return CHANNEL_BETA if str(channel or "").lower() == CHANNEL_BETA else CHANNEL_PRODUCTION


def _parse_version(s: str):
    """Zet een versiestring om in een tuple die je kunt vergelijken.

        'v1.99.146'      -> (1, 99, 146, 1, 0)
        'v1.5.0'         -> (1,  5,   0, 1, 0)
        'v1.5.0-beta.2'  -> (1,  5,   0, 0, 2)
        'v1.4.0'         -> (1,  4,   0, 1, 0)

    Het vierde getal is de rijpheid: 0 voor een voorloopversie, 1 voor een
    definitieve. Daardoor geldt vanzelf

        1.4.0  <  1.5.0-beta.2  <  1.5.0

    precies zoals semver het voorschrijft. Het vijfde getal is het volgnummer
    van de voorloopversie, zodat beta.2 boven beta.1 komt.

    Waarom dit ertoe doet: de oude versie pakte simpelweg de eerste drie
    getallen uit de string. Voor 'v1.5.0-beta.2' leverde dat (1, 5, 0) — net
    zoveel als de definitieve 1.5.0. Een betatester bleef dan hangen (beta.3
    telde als gelijk aan beta.2) of sprong ongemerkt heen en weer tussen een
    beta en een definitieve versie die als identiek werden gezien.
    """
    s = (s or "").strip()
    # Het achtervoegsel er eerst afhalen. Zou dat niet gebeuren, dan werd de
    # '2' uit 'beta.2' als derde getal (de patch) gelezen: 1.5.0-beta.2 werd
    # dan 1.5.2, en dus onterecht nieuwer dan de definitieve 1.5.0.
    m = _PRERELEASE_RE.search(s)
    is_final = 1
    pre_num = 0
    if m:
        is_final = 0
        pre_num = int(m.group(2)) if m.group(2) else 0
        s = s[:m.start()]
    nums = [int(n) for n in re.findall(r"\d+", s)[:3]]
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2], is_final, pre_num)


def is_prerelease_version(s: str) -> bool:
    """True als deze versiestring een voorloopversie (beta/rc/alpha) is."""
    return _parse_version(s)[3] == 0


def _release_is_beta(release: dict) -> bool:
    """Telt deze GitHub-release als betaversie?

    Het vinkje 'prerelease' óf een beta-achtervoegsel in de tag is genoeg.
    Zie de uitleg boven in dit bestand: bij twijfel liever beta dan productie.
    """
    if release.get("prerelease"):
        return True
    return is_prerelease_version(release.get("tag_name", ""))


def _installer_url(release: dict) -> str:
    """Zoek de installer (.exe) tussen de assets van een release."""
    for a in release.get("assets", []) or []:
        name = (a.get("name") or "").lower()
        if name.endswith(".exe"):
            return a.get("browser_download_url", "") or ""
    return ""


def _pick_release(releases: list, channel: str):
    """Kies de nieuwste release van het gevraagde kanaal.

    'Nieuwste' is hier het hoogste versienummer, niet de publicatiedatum. Een
    beta die ná een productierelease wordt gepubliceerd maar een lager
    versienummer heeft, wordt zo niet per ongeluk als de nieuwste beta gezien.
    """
    want_beta = (channel == CHANNEL_BETA)
    kandidaten = [
        r for r in releases
        if not r.get("draft") and _release_is_beta(r) == want_beta
    ]
    if not kandidaten:
        return None
    return max(kandidaten, key=lambda r: _parse_version(r.get("tag_name", "")))


def check_for_update(channel: str = CHANNEL_PRODUCTION) -> dict:
    """Kijk of er op dit kanaal een andere versie klaarstaat.

    Return bij succes:
        current      huidige versie (config.VERSION)
        latest       tag van de gekozen release ("" als het kanaal leeg is)
        channel      "production" of "beta"
        url          download-URL van de installer ("" als die ontbreekt)
        notes        release-notities
        available    True als de gevonden versie afwijkt van de huidige
        newer        True als de gevonden versie nieuwer is dan de huidige
        downgrade    True als de gevonden versie ouder is dan de huidige
        current_is_beta  True als er nu een betaversie draait

    Bij fout: {"error": str, "channel": str}

    WAAROM 'available' NAAST 'newer'
    --------------------------------
    Alleen naar 'nieuwer' kijken maakt terugschakelen onmogelijk. Wie op
    1.5.0-beta.3 zit en terug wil naar productie waar 1.4.0 de laatste is,
    zou dan "je hebt al de nieuwste versie" te zien krijgen en stilzwijgend
    op de beta blijven draaien — precies de verwarring die we willen
    voorkomen.

    De regel is daarom symmetrisch: elk kanaal heeft één doelversie, en
    wijkt de draaiende versie daarvan af, dan wordt die doelversie
    aangeboden. Is hij nieuwer, dan heet dat bijwerken; is hij ouder, dan
    heet dat terugzetten. Het scherm moet dat verschil laten zien — zie
    'downgrade'. Zo werkt heen én terug schakelen, en gebeurt er nooit stil
    niets.
    """
    channel = normalize_channel(channel)
    current = getattr(config, "VERSION", "")
    if requests is None:
        return {"error": "requests niet beschikbaar", "channel": channel}
    try:
        r = requests.get(
            GITHUB_API_RELEASES, timeout=15,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "MyBoothBox-Updater"},
        )
        if r.status_code != 200:
            return {"error": f"GitHub API {r.status_code}", "channel": channel}
        releases = r.json()
        if not isinstance(releases, list):
            return {"error": "onverwacht antwoord van GitHub", "channel": channel}

        release = _pick_release(releases, channel)
        current_v = _parse_version(current)

        if release is None:
            # Kanaal is (nog) leeg. Geen fout: op een verse repo bestaat er
            # simpelweg nog geen betaversie.
            return {
                "current": current,
                "latest": "",
                "channel": channel,
                "url": "",
                "notes": "",
                "available": False,
                "newer": False,
                "downgrade": False,
                "current_is_beta": current_v[3] == 0,
                "empty_channel": True,
            }

        tag = release.get("tag_name", "") or ""
        latest_v = _parse_version(tag)

        return {
            "current": current,
            "latest": tag,
            "channel": channel,
            "url": _installer_url(release),
            "notes": (release.get("body") or "")[:2000],
            "available": latest_v != current_v,
            "newer": latest_v > current_v,
            "downgrade": latest_v < current_v,
            "current_is_beta": current_v[3] == 0,
            "empty_channel": False,
        }
    except Exception as e:
        return {"error": str(e), "channel": channel}


def download_installer(url: str, progress_cb=None) -> str:
    """Download de installer naar %TEMP%\\MyBoothBox_Update.exe.
    progress_cb(pct:int) wordt aangeroepen tijdens het downloaden.
    Return: pad naar het bestand, of "" bij fout."""
    if requests is None or not url:
        return ""
    dest = os.path.join(tempfile.gettempdir(), "MyBoothBox_Update.exe")
    try:
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0) or 0)
            done = 0
            tmp = dest + ".part"
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=262144):
                    if not chunk:
                        continue
                    f.write(chunk)
                    done += len(chunk)
                    if progress_cb and total:
                        try:
                            progress_cb(int(100 * done / total))
                        except Exception:
                            pass
            if os.path.exists(dest):
                os.remove(dest)
            os.replace(tmp, dest)
        return dest
    except Exception as e:
        print(f"[UPDATER] Download fout: {e}")
        return ""


def run_installer(path: str) -> bool:
    """Start de installer stil en keer terug. De installer sluit de draaiende
    MyBoothBox, voert de update uit en start 'm opnieuw. /SILENT toont een
    voortgangsbalk; /SUPPRESSMSGBOXES onderdrukt vragen.

    De booth draait via de Taakplanner als admin (RunLevel Highest), dus de
    installer erft de rechten en krijgt geen UAC-prompt."""
    if not path or not os.path.isfile(path):
        return False
    try:
        subprocess.Popen(
            [path, "/SILENT", "/SUPPRESSMSGBOXES", "/NOCANCEL"],
            close_fds=True,
        )
        return True
    except Exception as e:
        print(f"[UPDATER] Installer start fout: {e}")
        return False
