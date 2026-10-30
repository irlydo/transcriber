"""Optional NVIDIA GPU support: cuBLAS / cuDNN DLLs, downloaded on demand from NVIDIA's PyPI packages.

They're ~2 GB unpacked, so the installer leaves them out and only users with an NVIDIA card fetch them.
"""

from __future__ import annotations

import ctypes
import glob
import hashlib
import json
import os
import shutil
import site
import sys
import threading
import urllib.request
import zipfile
from typing import Callable

from .paths import DATA_DIR

GPU_DIR = os.path.join(DATA_DIR, "gpu")

# Versions tested with the bundled CTranslate2 (CUDA 12, cuDNN 9).
PACKAGES = [
    ("nvidia-cuda-nvrtc-cu12", "12.9.86"),
    ("nvidia-cublas-cu12", "12.9.2.10"),
    ("nvidia-cudnn-cu12", "9.27.0.42"),
]
DOWNLOAD_SIZE = 1_372_000_000  # bytes, for display before the download starts


class Cancelled(Exception):
    pass


def _roots() -> list[str]:
    roots = [GPU_DIR, sys.prefix, getattr(sys, "_MEIPASS", "")]
    try:
        roots += list(site.getsitepackages()) + [site.getusersitepackages()]
    except AttributeError:  # a packaged app's stripped-down site module
        pass
    return [r for r in roots if r]


def _bin_dirs() -> list[str]:
    return [d for root in _roots() for d in glob.glob(os.path.join(root, "nvidia", "*", "bin"))]


def installed() -> bool:
    """cuDNN 9 is present, either downloaded by the app or pip-installed in a dev environment."""
    return any(glob.glob(os.path.join(d, "cudnn*64_9.dll")) for d in _bin_dirs())


def register_dlls() -> bool:
    """Make the CUDA libraries visible to CTranslate2. Must run before it is imported."""
    for bin_dir in _bin_dirs():
        os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
        try:
            os.add_dll_directory(bin_dir)
        except (OSError, AttributeError):
            pass
    return installed()


def has_nvidia_gpu() -> bool:
    """An NVIDIA driver with at least one CUDA device, whether or not the CUDA libraries are installed."""
    try:
        cuda = ctypes.WinDLL("nvcuda.dll")
        count = ctypes.c_int(0)
        return cuda.cuInit(0) == 0 and cuda.cuDeviceGetCount(ctypes.byref(count)) == 0 and count.value > 0
    except (OSError, AttributeError):
        return False


def _wheel_info(name: str, version: str) -> dict:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as r:
        files = json.load(r)["urls"]
    return next(f for f in files if f["filename"].endswith("win_amd64.whl"))


def install(on_progress: Callable[[int, int], None], cancel: threading.Event) -> None:
    """Download the wheels, verify them, and unpack only their DLLs into GPU_DIR/nvidia/<lib>/bin."""
    os.makedirs(GPU_DIR, exist_ok=True)
    staging = os.path.join(GPU_DIR, "staging")
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging)
    try:
        wheels = [_wheel_info(name, version) for name, version in PACKAGES]
        total = sum(w["size"] for w in wheels)
        done = 0
        for wheel in wheels:
            path = os.path.join(staging, wheel["filename"])
            sha = hashlib.sha256()
            with urllib.request.urlopen(wheel["url"], timeout=60) as r, open(path, "wb") as f:
                while chunk := r.read(1 << 20):
                    if cancel.is_set():
                        raise Cancelled()
                    f.write(chunk)
                    sha.update(chunk)
                    done += len(chunk)
                    on_progress(done, total)
            if sha.hexdigest() != wheel["digests"]["sha256"]:
                raise RuntimeError(f"{wheel['filename']} is corrupted (checksum mismatch)")
            with zipfile.ZipFile(path) as z:
                for member in z.namelist():
                    parts = member.split("/")
                    if len(parts) == 4 and parts[0] == "nvidia" and parts[2] == "bin" and member.endswith(".dll"):
                        z.extract(member, staging)
            os.remove(path)
        final = os.path.join(GPU_DIR, "nvidia")
        shutil.rmtree(final, ignore_errors=True)
        os.replace(os.path.join(staging, "nvidia"), final)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def remove() -> None:
    shutil.rmtree(os.path.join(GPU_DIR, "nvidia"), ignore_errors=True)
