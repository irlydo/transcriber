"""Where the app's files live, both when run from source and when packaged as an .exe."""

import os
import sys

APP_NAME = "Transcriber"
FROZEN = getattr(sys, "frozen", False)

# Read-only files shipped with the app (icon etc.). PyInstaller unpacks them to sys._MEIPASS.
RESOURCE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ICON = os.path.join(RESOURCE_DIR, "assets", "icon.ico")
FONT_DIR = os.path.join(RESOURCE_DIR, "assets", "fonts")

# Per-user, writable locations. An installed app can't write next to itself (e.g. Program Files).
SETTINGS_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), APP_NAME)
DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or SETTINGS_DIR, APP_NAME)


def default_models_dir() -> str:
    # Keep using models/ in a source checkout that already downloaded some.
    legacy = os.path.join(RESOURCE_DIR, "models")
    if not FROZEN and os.path.isdir(legacy):
        return legacy
    return os.path.join(DATA_DIR, "models")


def restart_command() -> list[str]:
    if FROZEN:
        return [sys.executable]
    return [sys.executable, os.path.join(RESOURCE_DIR, "main.py")]
