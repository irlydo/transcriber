# Third-party software

Transcriber is built on the open-source projects below. Each is used under its own license; see the
linked project for the full license text.

## Bundled with the app

| Component | License | Project |
|---|---|---|
| Python | PSF-2.0 | https://www.python.org |
| Tcl/Tk | Tcl/Tk license (BSD-style) | https://www.tcl.tk |
| faster-whisper | MIT | https://github.com/SYSTRAN/faster-whisper |
| CTranslate2 | MIT | https://github.com/OpenNMT/CTranslate2 |
| Silero VAD (via faster-whisper) | MIT | https://github.com/snakers4/silero-vad |
| PyAV | BSD-3-Clause | https://github.com/PyAV-Org/PyAV |
| FFmpeg (via PyAV, dynamically linked DLLs) | LGPL-3.0-or-later | https://ffmpeg.org |
| x264, x265 (linked by PyAV's FFmpeg build) | GPL-2.0-or-later | https://www.videolan.org/developers/x264.html, https://www.videolan.org/developers/x265.html |
| Other codecs in PyAV's FFmpeg build: libaom, dav1d, SVT-AV1, libvpx, libwebp, Opus, Vorbis, Speex, LAME, TwoLAME, OpenCORE AMR, OpenH264, SRT, GMP, libiconv, zlib, OpenSSL, MinGW runtime | BSD / LGPL / Apache-2.0 and similar | see https://github.com/PyAV-Org/pyav-ffmpeg |
| ONNX Runtime | MIT | https://onnxruntime.ai |
| DM Sans font | SIL Open Font License 1.1 | https://github.com/googlefonts/dm-fonts |
| DM Mono font | SIL Open Font License 1.1 | https://github.com/googlefonts/dm-mono |
| Source Serif 4 font | SIL Open Font License 1.1 | https://github.com/adobe-fonts/source-serif |
| CustomTkinter | CC0-1.0 | https://github.com/TomSchimansky/CustomTkinter |
| darkdetect | BSD-3-Clause | https://github.com/albertosottile/darkdetect |
| tkinterdnd2 / tkdnd | MIT / BSD-style | https://github.com/Eliav2/tkinterdnd2 |
| Hugging Face Hub, hf-xet, tokenizers | Apache-2.0 | https://github.com/huggingface |
| NumPy | BSD-3-Clause and others | https://numpy.org |
| Pillow | MIT-CMU | https://python-pillow.org |
| python-sounddevice | MIT | https://github.com/spatialaudio/python-sounddevice |
| PortAudio (via sounddevice) | MIT | https://www.portaudio.com |
| CFFI, pycparser | MIT-0, BSD-3-Clause | https://cffi.readthedocs.io, https://github.com/eliben/pycparser |
| protobuf | BSD-3-Clause | https://github.com/protocolbuffers/protobuf |
| flatbuffers | Apache-2.0 | https://github.com/google/flatbuffers |
| tqdm | MPL-2.0 and MIT | https://github.com/tqdm/tqdm |
| httpx2, httpcore2, h11, anyio, idna, filelock, fsspec, PyYAML, click, colorama, truststore, typing_extensions | MIT / BSD-3-Clause / PSF-2.0 | see PyPI |

## Downloaded on first use

| Component | License | Source |
|---|---|---|
| Whisper speech models (converted for CTranslate2) | MIT | https://github.com/openai/whisper, https://huggingface.co/Systran |
| Whisper large-v3-turbo (converted) | MIT | https://huggingface.co/mobiuslabsgmbh/faster-whisper-large-v3-turbo |
| NLLB-200 distilled 600M translation model (converted for CTranslate2) | CC-BY-NC-4.0 (non-commercial use) | https://huggingface.co/facebook/nllb-200-distilled-600M, https://huggingface.co/JustFrederik/nllb-200-distilled-600M-ct2-int8 |
| NVIDIA cuBLAS, cuDNN, NVRTC (optional GPU support) | NVIDIA Software License Agreement | https://pypi.org/project/nvidia-cudnn-cu12/ |
