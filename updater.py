"""Auto-updater — checkt de nieuwste GitHub-release en installeert die.

De repo is publiek, dus de GitHub Releases-API en de installer-assets zijn
zonder token bereikbaar. Flow:
  1. check_for_update()  -> haalt de laatste release op, vergelijkt de versie
  2. download_installer() -> downloadt de Bootharoo_Setup_*.exe naar temp
  3. run_installer()      -> start de installer (stil); die sluit de draaiende
                             app, update en herstart 'm (zie installer.iss)
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

GITHUB_API_LATEST = (
    "https://api.github.com/repos/bvogroup/photobooth-verhuur/releases/latest"
)


def _parse_version(s: str):
    """'v1.99.125 — titel' -> (1, 99, 125). Pakt de eerste 3 getallen."""
    nums = re.findall(r"\d+", s or "")
    nums = [int(n) for n in nums[:3]]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def check_for_update() -> dict:
    """Return dict met:
        {current, latest, newer (bool), url, notes}   bij succes
        {error: str}                                  bij fout
    """
    if requests is None:
        return {"error": "requests niet beschikbaar"}
    try:
        r = requests.get(
            GITHUB_API_LATEST, timeout=15,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "Bootharoo-Updater"},
        )
        if r.status_code != 200:
            return {"error": f"GitHub API {r.status_code}"}
        data = r.json()
        tag = data.get("tag_name", "") or ""
        latest_v = _parse_version(tag)
        current_v = _parse_version(getattr(config, "VERSION", ""))

        # Zoek de installer-asset (.exe)
        url = ""
        for a in data.get("assets", []) or []:
            name = (a.get("name") or "").lower()
            if name.endswith(".exe"):
                url = a.get("browser_download_url", "") or ""
                break

        return {
            "current": getattr(config, "VERSION", ""),
            "latest": tag,
            "newer": latest_v > current_v,
            "url": url,
            "notes": (data.get("body") or "")[:2000],
        }
    except Exception as e:
        return {"error": str(e)}


def download_installer(url: str, progress_cb=None) -> str:
    """Download de installer naar %TEMP%\\Bootharoo_Update.exe.
    progress_cb(pct:int) wordt aangeroepen tijdens het downloaden.
    Return: pad naar het bestand, of "" bij fout."""
    if requests is None or not url:
        return ""
    dest = os.path.join(tempfile.gettempdir(), "Bootharoo_Update.exe")
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
    Bootharoo, voert de update uit en start 'm opnieuw. /SILENT toont een
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
