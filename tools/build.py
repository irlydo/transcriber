"""Build the Windows release: dist/Transcriber/ (the app), the installer and a portable zip.

    .venv\\Scripts\\python tools\\build.py            app + installer + zip
    .venv\\Scripts\\python tools\\build.py --no-installer
    .venv\\Scripts\\python tools\\build.py --publish   also release it on GitHub: installed apps update to it

Needs PyInstaller (pip install -r requirements-dev.txt) and Inno Setup 6 (https://jrsoftware.org/isinfo.php).
Publishing needs the GitHub CLI (https://cli.github.com), logged in, and a new version in transcriber/__init__.py.
"""

import argparse
import glob
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "build")
DIST = os.path.join(ROOT, "dist")

VERSION_INFO = """VSVersionInfo(
  ffi=FixedFileInfo(filevers={tup}, prodvers={tup}),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Transcriber'),
      StringStruct('FileDescription', 'Transcriber'),
      StringStruct('FileVersion', '{ver}'),
      StringStruct('InternalName', 'Transcriber'),
      StringStruct('OriginalFilename', 'Transcriber.exe'),
      StringStruct('ProductName', 'Transcriber'),
      StringStruct('ProductVersion', '{ver}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def app_version() -> str:
    with open(os.path.join(ROOT, "transcriber", "__init__.py"), encoding="utf-8") as f:
        return re.search(r'__version__ = "([^"]+)"', f.read()).group(1)


def update_repo() -> str:
    with open(os.path.join(ROOT, "transcriber", "updater.py"), encoding="utf-8") as f:
        return re.search(r'REPO = "([^"]+)"', f.read()).group(1)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def find_gh() -> str:
    candidates = [os.path.join(base, "GitHub CLI", "gh.exe")
                  for base in (os.environ.get("ProgramFiles", ""), os.environ.get("LOCALAPPDATA", ""))]
    found = shutil.which("gh") or next((c for c in candidates if os.path.isfile(c)), None)
    if not found:
        sys.exit("GitHub CLI not found. Install it (winget install GitHub.cli) and run: gh auth login")
    return found


def check_publishable(gh: str, version: str) -> None:
    """Fail before the slow build rather than after it."""
    if git("status", "--porcelain", "--untracked-files=no"):
        sys.exit("Commit your changes before publishing.")
    existing = subprocess.run([gh, "release", "view", f"v{version}", "--repo", update_repo()],
                              capture_output=True, text=True)
    if existing.returncode == 0:
        sys.exit(f"v{version} is already released. Raise __version__ in transcriber/__init__.py first.")


def publish(gh: str, version: str) -> None:
    print(f"== Publishing v{version} to {update_repo()}")
    git("push", "origin", "HEAD")
    subprocess.run([gh, "release", "create", f"v{version}", "--repo", update_repo(), "--target", git("rev-parse", "HEAD"),
                    "--title", f"Transcriber {version}", "--generate-notes",
                    os.path.join(DIST, f"Transcriber-Setup-{version}.exe"),
                    os.path.join(DIST, f"Transcriber-{version}-portable.zip")], check=True)


def find_iscc() -> str:
    candidates = [os.environ.get("ISCC", "")] + [
        os.path.join(base, "Inno Setup 6", "ISCC.exe")
        for base in (os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
                     os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""))
    ]
    found = next((c for c in candidates if c and os.path.isfile(c)), None) or shutil.which("ISCC")
    if not found:
        sys.exit("Inno Setup 6 not found. Install it (winget install JRSoftware.InnoSetup) or set ISCC.")
    return found


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-installer", action="store_true", help="only build dist/Transcriber")
    parser.add_argument("--publish", action="store_true", help="also publish a GitHub release")
    args = parser.parse_args()
    if args.publish and args.no_installer:
        sys.exit("--publish needs the installer.")

    version = app_version()
    gh = find_gh() if args.publish else ""
    if args.publish:
        check_publishable(gh, version)
    nums = (version.split(".") + ["0"] * 4)[:4]
    os.makedirs(BUILD, exist_ok=True)
    with open(os.path.join(BUILD, "version_info.txt"), "w", encoding="utf-8") as f:
        f.write(VERSION_INFO.format(tup=tuple(int(n) for n in nums), ver=version))

    print(f"== Building Transcriber {version}")
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--distpath", DIST, "--workpath", os.path.join(BUILD, "pyinstaller"),
                    os.path.join(ROOT, "packaging", "transcriber.spec")], check=True)

    leaked = glob.glob(os.path.join(DIST, "Transcriber", "**", "cublas*.dll"), recursive=True)
    if leaked:
        sys.exit(f"CUDA libraries ended up in the build (they're downloaded on demand): {leaked}")
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        if os.path.exists(os.path.join(ROOT, name)):
            shutil.copy(os.path.join(ROOT, name), os.path.join(DIST, "Transcriber", name))

    if args.no_installer:
        return
    print("== Building installer")
    subprocess.run([find_iscc(), f"/DAppVersion={version}", os.path.join(ROOT, "packaging", "installer.iss")],
                   check=True)

    print("== Building portable zip")
    shutil.make_archive(os.path.join(DIST, f"Transcriber-{version}-portable"), "zip", DIST, "Transcriber")
    if args.publish:
        publish(gh, version)
    print(f"\nDone. See {DIST}")


if __name__ == "__main__":
    main()
