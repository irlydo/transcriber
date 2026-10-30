# PyInstaller spec for the Windows build. Run via tools/build.py, which also writes version_info.txt.
import os
import re

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

ROOT = os.path.dirname(SPECPATH)

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    datas=[(os.path.join(ROOT, "assets", "icon.ico"), "assets")]
    + collect_data_files("customtkinter")
    + collect_data_files("faster_whisper")  # the Silero VAD model
    + copy_metadata("hf_xet")  # huggingface_hub checks for it by version before using fast downloads
    # the tkdnd Tcl extension, Windows x64 builds only
    + [d for d in collect_data_files("tkinterdnd2") if "tkdnd\\" not in d[1] or "win-x64" in d[1]],
    excludes=["nvidia", "torch", "tensorflow", "matplotlib", "IPython", "pytest"],
)

# The CUDA libraries are downloaded on demand (see transcriber/gpu_pack.py), so never bundle
# them, not even copies PyInstaller finds on PATH from a local CUDA Toolkit install.
CUDA_DLL = re.compile(r"(cublas|cudnn|nvrtc|cudart|cufft|curand|cusparse|nvjitlink)[^\\/]*\.dll$", re.I)
a.binaries = [b for b in a.binaries if not CUDA_DLL.search(b[0])]

# sounddevice ships PortAudio for every platform; the 64-bit Windows build only loads this one.
PORTAUDIO = re.compile(r"libportaudio[^\\/]*\.dll$", re.I)
a.binaries = [b for b in a.binaries if not PORTAUDIO.search(b[0]) or b[0].lower().endswith("libportaudio64bit.dll")]
a.datas = [d for d in a.datas if not PORTAUDIO.search(d[0]) or d[0].lower().endswith("libportaudio64bit.dll")]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Transcriber",
    icon=os.path.join(ROOT, "assets", "icon.ico"),
    version=os.path.join(ROOT, "build", "version_info.txt"),
    console=False,
    upx=False,
)

coll = COLLECT(exe, a.binaries, a.datas, name="Transcriber", upx=False)
