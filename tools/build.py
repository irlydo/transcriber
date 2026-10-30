"""Build the Windows release: dist/Transcriber/ (the app), the installer and a portable zip.

    .venv\\Scripts\\python tools\\build.py            app + installer + zip
    .venv\\Scripts\\python tools\\build.py --no-installer

Needs PyInstaller (pip install -r requirements-dev.txt) and Inno Setup 6 (https://jrsoftware.org/isinfo.php).
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
    args = parser.parse_args()

    version = app_version()
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
    print(f"\nDone. See {DIST}")


if __name__ == "__main__":
    main()
