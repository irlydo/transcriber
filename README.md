# Transcriber

Drop in audio or video and get `.srt` subtitles for any video editor, player or platform. Everything runs
locally on your PC (no uploads, no subscription), using OpenAI's Whisper models via
[faster-whisper](https://github.com/SYSTRAN/faster-whisper). It's GPU-accelerated on NVIDIA cards.

## Install

Download **`Transcriber-Setup-<version>.exe`** from the Releases page and run it. It installs for your
user account (no admin rights needed) and works on 64-bit Windows 10 and 11. A portable `.zip` is
available too: unzip it anywhere and run `Transcriber.exe`.

The first time you transcribe, the chosen model is downloaded once (Turbo is ~1.6 GB). You can see or
change where models are stored in **Settings**.

### GPU acceleration

On a PC with an NVIDIA graphics card, Transcriber offers to download **GPU support** (NVIDIA's cuBLAS
and cuDNN libraries, a one-time ~1.4 GB download) from **⚙ Settings**. It makes transcription several
times faster. Only an up-to-date NVIDIA driver is needed, not the CUDA Toolkit. Without an NVIDIA card
the app runs on the CPU.

Downloaded models, GPU support and the log file live in `%LOCALAPPDATA%\Transcriber`; settings are in
`%APPDATA%\Transcriber`. The uninstaller offers to remove them.

## Use

1. Drag files (or whole folders) onto the window, or click the drop zone to browse.
   Supported: MP4, MOV, MKV, AVI, WEBM, MXF, MP3, WAV, M4A, FLAC, OGG and more.
2. Pick a subtitle style:
   - **Standard**: up to 2 lines × 42 characters (broadcast / YouTube)
   - **One line**: a single tidy line
   - **Social**: a few words at a time for TikTok / Reels / Shorts
3. Press **Generate subtitles**. The `.srt` is saved next to the source file (or a folder you choose).
   Existing files are never overwritten; you'll get `name (2).srt` instead.

## Caption Studio: burned-in captions for short videos

Click **🎬 Caption Studio** to put animated captions straight into a TikTok / Reels / Shorts video, so
one upload works on every platform.

1. Drop a video onto the Studio window (or click **Open video…**). It's transcribed automatically.
2. Play it back and check the captions. Click any caption to fix its text or timing, or to split, merge or
   delete it. **Space** plays and pauses, **← →** skip a second (**Shift** for a tenth), **↑ ↓** move
   between captions.
3. Pick a look (Pop, Highlight, Clean box, Karaoke, One word, Typewriter) and adjust it: font, size,
   ALL CAPS, how many words are on screen, the entrance animation (pop, bounce, fade, slide), what the
   word being spoken does (highlight colour, highlight box, pop, appear one by one), colours, outline,
   shadow, background box and height. Drag the captions on the video to move them. The dashed safe-zone
   guide shows roughly where the apps' buttons won't cover them.
4. Press **Render video**. You get an `.mp4` with the same resolution and frame rate (every frame keeps
   its original timing) as the source, H.264 at near-lossless quality, with the original audio copied
   as-is. On an NVIDIA card it encodes on the GPU. **Save .srt** exports the edited captions too.

Edits are remembered: reopen the same video and your captions come back.

## Using the subtitles

SRT is the most widely supported subtitle format: import the `.srt` into your video editor, upload it
alongside your video on YouTube and other platforms, or keep it next to the video so players like VLC
pick it up automatically.

## Settings

Click **⚙ Settings** for:

- **Interface language**: follows your Windows display language by default. Available in English,
  Deutsch, Español, Français, Italiano, Nederlands, Polski, Português, 日本語, 한국어 and 中文.
- **Appearance**: System, Light or Dark.
- **Use NVIDIA GPU**: turn off to always run on the CPU. If GPU support isn't downloaded yet, it's
  offered here.
- **Models folder**: where downloaded models are kept.

## Tips

- **Turbo** is the best all-rounder. Use **Large** for difficult audio (heavy accents, noise).
- Choosing the spoken language (instead of Auto-detect) helps on short or mixed-language clips.
- **Translate subtitles to English** creates English subtitles from any spoken language.
- Without an NVIDIA GPU the app runs on the CPU automatically; pick **Small** for speed there.

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
`%LOCALAPPDATA%\Transcriber\transcriber.log`.

## Project layout

```
main.py                   entry point
transcriber/app.py        the desktop UI (CustomTkinter + drag & drop) and Settings window
transcriber/engine.py     Whisper transcription (GPU/CPU)
transcriber/subtitles.py  turns word timings into readable, well-timed SRT captions
transcriber/i18n.py       interface translations and system language detection
transcriber/paths.py      app, settings and model locations (source checkout or packaged .exe)
transcriber/gpu_pack.py   on-demand download of NVIDIA GPU libraries
transcriber/studio.py     Caption Studio window: review, edit and style burned-in captions
transcriber/captions.py   caption looks, animations and the renderer shared by preview and export
transcriber/player.py     video playback with synced audio for the Studio preview
transcriber/video.py      reads video details and renders the captioned copy
packaging/                PyInstaller spec and Inno Setup installer script
tools/build.py            builds the app, installer and portable zip
```

Third-party components and their licenses are listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
