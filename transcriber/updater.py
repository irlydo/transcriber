"""Automatic updates from GitHub Releases: when a newer release is published, its installer is downloaded
in the background and run (silently) the next time the user restarts the app.

Only the installed app updates itself; a source checkout or the portable zip never does.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request

from . import __version__
from .paths import DATA_DIR, FROZEN

REPO = "irlydo/transcriber"
LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
UPDATE_DIR = os.path.join(DATA_DIR, "updates")
ASSET = re.compile(r"Transcriber-Setup-([\d.]+)\.exe$")


def version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", text)[:4])


def enabled() -> bool:
    """Installed by the setup program (it leaves an uninstaller next to the app)."""
    return FROZEN and bool(glob.glob(os.path.join(os.path.dirname(sys.executable), "unins*.exe")))


def _get(url: str, timeout: float = 30):
    req = urllib.request.Request(url, headers={"User-Agent": f"Transcriber/{__version__}",
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def check_and_download() -> tuple[str, str] | None:
    """(version, installer path) of a newer release, downloaded and verified; None when up to date."""
    with _get(LATEST) as r:
        release = json.load(r)
    version = release.get("tag_name", "").lstrip("v")
    if not version or version_tuple(version) <= version_tuple(__version__):
        _clean(keep=None)
        return None
    asset = next((a for a in release.get("assets", []) if ASSET.search(a.get("name", ""))), None)
    if asset is None:
        return None

    os.makedirs(UPDATE_DIR, exist_ok=True)
    path = os.path.join(UPDATE_DIR, asset["name"])
    _clean(keep=path)
    digest = (asset.get("digest") or "").removeprefix("sha256:").lower()
    if os.path.isfile(path) and os.path.getsize(path) == asset["size"] and (not digest or _sha256(path) == digest):
        return version, path

    part = path + ".part"
    h = hashlib.sha256()
    with _get(asset["browser_download_url"], timeout=60) as r, open(part, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
            h.update(chunk)
    if os.path.getsize(part) != asset["size"] or (digest and h.hexdigest() != digest):
        os.remove(part)
        raise RuntimeError("update download didn't match the release")
    os.replace(part, path)
    return version, path


def install(path: str) -> None:
    """Run the installer silently; it closes this app if needed, upgrades it and starts the new version."""
    subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
                     close_fds=True)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _clean(keep: str | None) -> None:
    """Remove installers left from earlier updates."""
    for old in glob.glob(os.path.join(UPDATE_DIR, "*")):
        if old != keep:
            try:
                os.remove(old)
            except OSError:
                pass
