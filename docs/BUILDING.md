# Building Transcriber

## Running from source

Needs Python 3.10+ (64-bit). Double-click **`setup.bat`**: it creates `.venv`, installs the dependencies
(including the NVIDIA libraries, so GPU support works without the download) and adds a desktop shortcut.
Then run `Transcriber.bat`.

## Building a release

```
build.bat
```

This needs [Inno Setup 6](https://jrsoftware.org/isinfo.php) (`winget install JRSoftware.InnoSetup`).
It writes to `dist\`:

- `Transcriber\`: the app (PyInstaller, one folder)
- `Transcriber-Setup-<version>.exe`: the installer
- `Transcriber-<version>-portable.zip`: the same app, zipped

The version comes from `transcriber/__init__.py`. `build.bat --no-installer` builds only the app folder.

To check a build end to end, run `dist\Transcriber\Transcriber.exe --selftest some.wav base`: it
transcribes the file without opening the window and writes the result to
`%LOCALAPPDATA%\Transcriber\transcriber.log`. Add a language code to translate the result too, e.g.
`--selftest some.wav base es`.

## Publishing an update

Raise `__version__` in `transcriber/__init__.py`, commit, and run:

```
build.bat --publish
```

It builds, pushes, and creates the GitHub Release `v<version>` with the installer attached (also as
`Transcriber-Setup.exe`, which the README's download button links to). Installed copies find it on their
next start and offer **⟳ Restart to update**. Publishing needs the [GitHub CLI](https://cli.github.com),
logged in (`gh auth login`).

## Where things live

Downloaded models, GPU support, update downloads and the log file are in `%LOCALAPPDATA%\Transcriber`;
settings are in `%APPDATA%\Transcriber`. The uninstaller offers to remove them.

## Project layout

```
main.py                   entry point
transcriber/app.py        the desktop UI (CustomTkinter + drag & drop) and Settings window
transcriber/engine.py     Whisper transcription (GPU/CPU)
transcriber/subtitles.py  turns word timings into readable, well-timed SRT captions
transcriber/translate.py  translates captions into other languages (NLLB-200), keeping their timing
transcriber/i18n.py       interface translations and system language detection
transcriber/paths.py      app, settings and model locations (source checkout or packaged .exe)
transcriber/gpu_pack.py   on-demand download of NVIDIA GPU libraries
transcriber/updater.py    checks GitHub Releases and installs updates
transcriber/studio.py     Caption Studio window: review, edit and style burned-in captions
transcriber/captions.py   caption looks, animations and the renderer shared by preview and export
transcriber/player.py     video playback with synced audio for the Studio preview
transcriber/video.py      reads video details and renders the captioned copy
assets/                   icon, bundled fonts, README download button
packaging/                PyInstaller spec and Inno Setup installer script
tools/build.py            builds the app, installer and portable zip, and publishes releases
```
