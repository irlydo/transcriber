"""Transcriber desktop app: drop in audio/video, get SRT subtitles."""

from __future__ import annotations

import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
from dataclasses import dataclass
from tkinter import filedialog

import customtkinter as ctk
from tkinterdnd2 import DND_FILES, TkinterDnD

from . import __version__, gpu_pack
from .engine_info import MODELS
from .i18n import SPOKEN_LANGUAGES, UI_LANGUAGES, current_language, set_language, system_language, t
from .paths import APP_NAME, DATA_DIR, ICON, SETTINGS_DIR, default_models_dir, restart_command
from .subtitles import PRESETS, CaptionStyle, write_srt

SETTINGS_PATH = os.path.join(SETTINGS_DIR, "settings.json")

VIDEO_EXTS = {
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mxf", ".wmv", ".mts", ".m2ts", ".mpg", ".mpeg",
}
MEDIA_EXTS = VIDEO_EXTS | {
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".aif", ".aiff",
}

# preset name -> i18n keys of its label and hint
PRESET_TEXT = {
    "Standard": ("preset_standard", "hint_standard"),
    "One line": ("preset_one_line", "hint_one_line"),
    "Social": ("preset_social", "hint_social"),
}
APPEARANCES = {"system": "appearance_system", "light": "appearance_light", "dark": "appearance_dark"}

ACCENT = "#7C5CFF"
ACCENT_HOVER = "#6846F5"
OK = "#3DDC97"
ERR = "#FF6B6B"
MUTED = ("gray40", "gray60")
CARD = ("gray92", "#1E1E24")
SURFACE = ("gray97", "#16161A")

DEFAULT_SETTINGS = {
    "model": "large-v3-turbo",
    "language": "auto",
    "translate": False,
    "preset": "Standard",
    "max_chars": 42,
    "max_lines": 2,
    "save_txt": False,
    "output_dir": "",
    "ui_language": "system",
    "appearance": "system",
    "use_gpu": True,
    "models_dir": "",
    "caption_look": {},
    "studio_safe_zone": True,
}

MENU_KW = dict(fg_color=SURFACE, button_color=ACCENT, button_hover_color=ACCENT_HOVER,
               dropdown_hover_color=ACCENT, dynamic_resizing=False)
OUTLINE_BTN_KW = dict(height=28, fg_color="transparent", border_width=1, border_color=("gray70", "gray30"),
                      text_color=("gray20", "gray80"), hover_color=("gray85", "gray25"))


def load_settings() -> dict:
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            settings = {**DEFAULT_SETTINGS, **json.load(f)}
    except (OSError, ValueError):
        settings = dict(DEFAULT_SETTINGS)
    if settings["language"] not in SPOKEN_LANGUAGES:
        settings["language"] = "auto"
    return settings


def save_settings(settings: dict) -> None:
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except OSError:
        pass


def unique_path(path: str) -> str:
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(path):
        path = f"{base} ({n}){ext}"
        n += 1
    return path


def show_in_explorer(path: str) -> None:
    if os.path.isfile(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    elif os.path.isdir(path):
        os.startfile(path)


def format_duration(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m}:{s:02}"


def format_size(n: float) -> str:
    text = f"{n / 1e9:.1f} GB"
    return text.replace(".", ",") if current_language() in ("de", "es", "fr", "it", "nl", "pl", "pt") else text


def section(parent, text: str) -> None:
    ctk.CTkLabel(parent, text=text.upper(), text_color=MUTED,
                 font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w", padx=12, pady=(12, 2))


def label(parent, text: str) -> None:
    ctk.CTkLabel(parent, text=text, font=ctk.CTkFont(size=13)).pack(anchor="w", padx=12, pady=(4, 0))


def hint(parent, text: str = "", wraplength: int = 270) -> ctk.CTkLabel:
    w = ctk.CTkLabel(parent, text=text, text_color=MUTED, font=ctk.CTkFont(size=11),
                     justify="left", wraplength=wraplength, anchor="w")
    w.pack(anchor="w", padx=12, pady=(4, 0))
    return w


@dataclass
class Job:
    id: int
    path: str
    status: str = "waiting"  # waiting | running | done | error | cancelled
    output: str = ""


class QueueRow(ctk.CTkFrame):
    def __init__(self, master, job: Job, on_remove) -> None:
        super().__init__(master, fg_color=CARD, corner_radius=10)
        self.job = job
        self.grid_columnconfigure(1, weight=1)

        is_video = os.path.splitext(job.path)[1].lower() in VIDEO_EXTS
        ctk.CTkLabel(self, text="🎬" if is_video else "🎵", font=ctk.CTkFont(size=20), width=36) \
            .grid(row=0, column=0, rowspan=2, padx=(10, 4), pady=8)

        ctk.CTkLabel(self, text=os.path.basename(job.path), anchor="w",
                     font=ctk.CTkFont(size=13, weight="bold")) \
            .grid(row=0, column=1, sticky="ew", pady=(8, 0))
        self.status = ctk.CTkLabel(self, text=t("waiting"), anchor="w", text_color=MUTED,
                                   font=ctk.CTkFont(size=12))
        self.status.grid(row=1, column=1, sticky="ew", pady=(0, 6))
        # Shown once work starts; an empty bar still draws a stray sliver.
        self.bar = ctk.CTkProgressBar(self, height=4, progress_color=ACCENT)
        self.bar.set(0)

        self.action = ctk.CTkButton(self, text="✕", width=30, height=30, fg_color="transparent",
                                    hover_color=("gray80", "gray25"), text_color=MUTED,
                                    command=lambda: on_remove(self))
        self.action.grid(row=0, column=2, rowspan=2, padx=8)

    def _show_bar(self) -> None:
        self.bar.grid(row=2, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 8))

    def set_progress(self, pct: float, text: str) -> None:
        if pct > 0:
            self._show_bar()
            self.bar.set(pct)
        self.status.configure(text=text, text_color=MUTED)

    def set_done(self, output: str, summary: str) -> None:
        self._show_bar()
        self.bar.set(1)
        self.bar.configure(progress_color=OK)
        self.status.configure(text=f"✓ {summary}", text_color=OK)
        self.action.configure(text=t("show"), width=60, border_width=1, border_color=("gray70", "gray35"),
                              text_color=("gray20", "gray85"), command=lambda: show_in_explorer(output))

    def set_error(self, message: str) -> None:
        self.bar.grid_remove()
        self.status.configure(text=f"✕ {message}", text_color=ERR)


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.app = app
        self.title(t("settings"))
        # Room for the GPU download box when it's on offer.
        self.offer_gpu = app.has_gpu and not gpu_pack.installed() or app.gpu_dl["state"] != "idle"
        self.geometry("460x690" if self.offer_gpu else "460x580")
        self.resizable(False, False)
        self.configure(fg_color=SURFACE)
        self.transient(app)
        if os.path.exists(ICON):
            self.after(250, lambda: self.iconbitmap(ICON))  # CTkToplevel sets its own icon at ~200 ms
        settings = app.settings

        body = ctk.CTkFrame(self, fg_color=CARD, corner_radius=14)
        body.pack(fill="both", expand=True, padx=20, pady=(20, 10))

        section(body, t("section_general"))
        label(body, t("interface_language"))
        self.ui_langs = {t("system_default", name=UI_LANGUAGES[system_language()]): "system",
                         **{name: code for code, name in UI_LANGUAGES.items()}}
        chosen = next((k for k, v in self.ui_langs.items() if v == settings["ui_language"]),
                      next(iter(self.ui_langs)))
        ctk.CTkOptionMenu(body, values=list(self.ui_langs), variable=ctk.StringVar(value=chosen),
                          command=self._on_ui_language, **MENU_KW).pack(fill="x", padx=12)
        self.restart_box = ctk.CTkFrame(body, fg_color="transparent")
        hint(self.restart_box, t("restart_hint"), wraplength=380)
        self.restart_btn = ctk.CTkButton(self.restart_box, text=t("restart_now"), height=28,
                                         fg_color=ACCENT, hover_color=ACCENT_HOVER, command=app.restart)
        self.restart_btn.pack(anchor="w", padx=12, pady=(6, 0))
        self.lang_anchor = ctk.CTkFrame(body, fg_color="transparent", height=0)
        self.lang_anchor.pack(fill="x")

        label(body, t("appearance"))
        self.appearances = {t(key): mode for mode, key in APPEARANCES.items()}
        ctk.CTkSegmentedButton(body, values=list(self.appearances), selected_color=ACCENT,
                               selected_hover_color=ACCENT_HOVER, command=self._on_appearance,
                               variable=ctk.StringVar(value=t(APPEARANCES.get(settings["appearance"],
                                                                              "appearance_system"))),
                               dynamic_resizing=False).pack(fill="x", padx=12)

        section(body, t("section_performance"))
        self.gpu_var = ctk.BooleanVar(value=settings["use_gpu"])
        ctk.CTkSwitch(body, text=t("use_gpu"), variable=self.gpu_var, progress_color=ACCENT,
                      command=self._on_gpu).pack(anchor="w", padx=12, pady=(4, 0))
        hint(body, t("use_gpu_hint"), wraplength=380)
        if self.offer_gpu:
            self._build_gpu_pack(body)

        label(body, t("models_folder"))
        self.models_label = ctk.CTkLabel(body, text=app.models_dir(), font=ctk.CTkFont(size=12),
                                         justify="left", wraplength=380, anchor="w")
        self.models_label.pack(anchor="w", padx=12)
        btns = ctk.CTkFrame(body, fg_color="transparent")
        btns.pack(fill="x", padx=12, pady=(6, 0))
        ctk.CTkButton(btns, text=t("change"), command=self._change_models_dir, **OUTLINE_BTN_KW) \
            .pack(side="left", expand=True, fill="x", padx=(0, 4))
        ctk.CTkButton(btns, text=t("open"), command=self._open_models_dir, **OUTLINE_BTN_KW) \
            .pack(side="left", expand=True, fill="x", padx=(4, 0))
        hint(body, t("models_hint"), wraplength=380)

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.pack(fill="x", padx=20, pady=(0, 16))
        ctk.CTkLabel(footer, text=f"{APP_NAME}  ·  {t('version', version=__version__)}", text_color=MUTED,
                     font=ctk.CTkFont(size=12)).pack(side="left")
        ctk.CTkButton(footer, text=t("done"), width=100, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=self.destroy).pack(side="right")

        self.after(100, self._take_focus)

    def _build_gpu_pack(self, body) -> None:
        box = ctk.CTkFrame(body, fg_color=SURFACE, corner_radius=10)
        box.pack(fill="x", padx=12, pady=(10, 0))
        ctk.CTkLabel(box, text=t("gpu_pack_title"), font=ctk.CTkFont(size=13, weight="bold")) \
            .pack(anchor="w", padx=12, pady=(10, 0))
        self.gpu_text = ctk.CTkLabel(box, text="", font=ctk.CTkFont(size=12), justify="left",
                                     wraplength=360, anchor="w")
        self.gpu_text.pack(anchor="w", padx=12, pady=(2, 0))
        self.gpu_bar = ctk.CTkProgressBar(box, height=6, progress_color=ACCENT)
        self.gpu_btn = ctk.CTkButton(box, text="", height=30, fg_color=ACCENT, hover_color=ACCENT_HOVER)
        self.gpu_btn.pack(anchor="w", padx=12, pady=(8, 12))
        self.refresh_gpu()

    def refresh_gpu(self) -> None:
        """Show the GPU download box for the app's current download state."""
        if not self.offer_gpu:
            return
        dl = self.app.gpu_dl
        state = dl["state"]
        if state == "downloading":
            self.gpu_text.configure(text=t("gpu_pack_downloading", done=format_size(dl["done"]),
                                           total=format_size(dl["total"])), text_color=("gray10", "gray90"))
            self.gpu_bar.pack(fill="x", padx=12, pady=(8, 0), before=self.gpu_btn)
            self.gpu_bar.set(dl["done"] / dl["total"] if dl["total"] else 0)
            self.gpu_btn.configure(text=t("cancel"), command=self.app.cancel_gpu_pack, fg_color="transparent",
                                   border_width=1, border_color=ERR, text_color=ERR, state="normal")
            return
        self.gpu_bar.pack_forget()
        accent = dict(fg_color=ACCENT, border_width=0, text_color=("gray98", "gray98"))
        if state == "done":
            self.gpu_text.configure(text=t("gpu_pack_done"), text_color=OK)
            self.gpu_btn.configure(text=t("restart_now"), command=self.app.restart,
                                   state="disabled" if self.app.busy() else "normal", **accent)
        else:
            text = t("gpu_pack_offer", size=format_size(gpu_pack.DOWNLOAD_SIZE))
            if state == "error":
                text += "\n\n" + t("gpu_pack_failed", error=dl["error"])
            self.gpu_text.configure(text=text, text_color=ERR if state == "error" else ("gray10", "gray90"))
            self.gpu_btn.configure(text=t("gpu_pack_download"), command=self.app.download_gpu_pack,
                                   state="normal", **accent)

    def _take_focus(self) -> None:
        self.lift()
        self.focus_force()
        self.grab_set()

    def _on_ui_language(self, choice: str) -> None:
        code = self.ui_langs[choice]
        self.app.settings["ui_language"] = code
        save_settings(self.app.settings)
        effective = system_language() if code == "system" else code
        if effective != current_language():
            self.restart_btn.configure(state="disabled" if self.app.busy() else "normal")
            self.restart_box.pack(fill="x", before=self.lang_anchor)
        else:
            self.restart_box.pack_forget()

    def _on_appearance(self, choice: str) -> None:
        mode = self.appearances[choice]
        self.app.settings["appearance"] = mode
        save_settings(self.app.settings)
        ctk.set_appearance_mode(mode)

    def _on_gpu(self) -> None:
        self.app.settings["use_gpu"] = self.gpu_var.get()
        save_settings(self.app.settings)
        self.app.apply_engine_settings()

    def _change_models_dir(self) -> None:
        folder = filedialog.askdirectory(parent=self, title=t("models_folder"),
                                         initialdir=self.app.models_dir())
        if folder:
            self.app.settings["models_dir"] = os.path.normpath(folder)
            save_settings(self.app.settings)
            self.models_label.configure(text=self.app.models_dir())
            self.app.apply_engine_settings()

    def _open_models_dir(self) -> None:
        os.makedirs(self.app.models_dir(), exist_ok=True)
        os.startfile(self.app.models_dir())


class App(ctk.CTk, TkinterDnD.DnDWrapper):
    def __init__(self, settings: dict) -> None:
        super().__init__()
        self.TkdndVersion = TkinterDnD._require(self)

        self.settings = settings
        self.jobs: list[Job] = []
        self.rows: dict[int, QueueRow] = {}
        self._next_id = 1
        self.messages: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None
        self.transcriber = None  # created once the engine has loaded
        self.settings_dialog: SettingsDialog | None = None
        self.studio = None  # the Caption Studio window, while open
        self.has_gpu = False  # an NVIDIA card is present (known once the engine thread checks)
        self.gpu_dl = {"state": "idle", "done": 0, "total": 0, "error": ""}  # idle|downloading|done|error
        self.gpu_cancel = threading.Event()

        self.title(APP_NAME)
        self.geometry("1100x780")
        self.minsize(900, 640)
        self.configure(fg_color=SURFACE)
        if os.path.exists(ICON):
            self.iconbitmap(ICON)
            self.after(300, lambda: self.iconbitmap(ICON))  # CTk resets the icon on startup

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_main()
        self._build_sidebar()

        self.drop_target_register(DND_FILES)
        self.dnd_bind("<<Drop>>", self._on_drop)
        self.dnd_bind("<<DropEnter>>", lambda e: self._highlight_drop(True))
        self.dnd_bind("<<DropLeave>>", lambda e: self._highlight_drop(False))

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)
        threading.Thread(target=self._load_engine, daemon=True).start()

    # ---------- layout ----------

    def _build_main(self) -> None:
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=0, sticky="nsew", padx=(20, 10), pady=20)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(2, weight=3)
        main.grid_rowconfigure(4, weight=2)

        header = ctk.CTkFrame(main, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text=APP_NAME, font=ctk.CTkFont(size=26, weight="bold")) \
            .grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(header, text=t("tagline"), text_color=MUTED, font=ctk.CTkFont(size=13)) \
            .grid(row=1, column=0, sticky="w")
        ctk.CTkButton(header, text=f"🎬  {t('caption_studio')}", width=150, height=32, command=self.open_studio,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER) \
            .grid(row=0, column=1, rowspan=2, sticky="e", padx=(0, 8))
        ctk.CTkButton(header, text=f"⚙  {t('settings')}", width=110, command=self._open_settings,
                      **{**OUTLINE_BTN_KW, "height": 32}) \
            .grid(row=0, column=2, rowspan=2, sticky="e")

        self.drop = ctk.CTkFrame(main, height=150, corner_radius=14, border_width=2,
                                 border_color=("gray75", "gray30"), fg_color=CARD, cursor="hand2")
        self.drop.grid(row=1, column=0, sticky="ew")
        self.drop.grid_propagate(False)
        self.drop.grid_columnconfigure(0, weight=1)
        self.drop.grid_rowconfigure((0, 4), weight=1)
        widgets = [
            ctk.CTkLabel(self.drop, text="⬆", font=ctk.CTkFont(size=30), text_color=ACCENT),
            ctk.CTkLabel(self.drop, text=t("drop_title"), font=ctk.CTkFont(size=16, weight="bold")),
            ctk.CTkLabel(self.drop, text=t("drop_hint"), text_color=MUTED, font=ctk.CTkFont(size=12)),
        ]
        for i, w in enumerate(widgets, 1):
            w.grid(row=i, column=0)
        for w in [self.drop, *widgets]:
            w.bind("<Button-1>", lambda e: self._browse())

        qhead = ctk.CTkFrame(main, fg_color="transparent")
        qhead.grid(row=2, column=0, sticky="new", pady=(18, 0))
        ctk.CTkLabel(qhead, text=t("queue"), font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        ctk.CTkButton(qhead, text=t("clear_finished"), width=110, height=26, fg_color="transparent",
                      border_width=1, border_color=("gray70", "gray30"), text_color=("gray20", "gray80"),
                      hover_color=("gray85", "gray20"), command=self._clear_finished).pack(side="right")

        self.queue_frame = ctk.CTkScrollableFrame(main, fg_color="transparent")
        self.queue_frame.grid(row=2, column=0, sticky="nsew", pady=(50, 0))
        self.queue_frame.grid_columnconfigure(0, weight=1)
        self.empty_label = ctk.CTkLabel(self.queue_frame, text=t("no_files"), text_color=MUTED)
        self.empty_label.grid(row=0, column=0, pady=30)

        ctk.CTkLabel(main, text=t("live_transcript"), font=ctk.CTkFont(size=15, weight="bold")) \
            .grid(row=3, column=0, sticky="w", pady=(14, 6))
        self.preview = ctk.CTkTextbox(main, fg_color=CARD, corner_radius=10, wrap="word",
                                      font=ctk.CTkFont(size=13))
        self.preview.grid(row=4, column=0, sticky="nsew")
        self.preview.configure(state="disabled")

    def _build_sidebar(self) -> None:
        side = ctk.CTkFrame(self, width=320, fg_color=CARD, corner_radius=14)
        side.grid(row=0, column=1, sticky="ns", padx=(10, 20), pady=20)
        side.grid_propagate(False)
        side.grid_columnconfigure(0, weight=1)

        body = ctk.CTkScrollableFrame(side, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=4, pady=(8, 0))
        side.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)

        section(body, t("section_transcription"))
        label(body, t("model"))
        self.model_labels = {t(key): name for name, key in MODELS.items()}
        model = self.settings["model"] if self.settings["model"] in MODELS else "large-v3-turbo"
        self.model_var = ctk.StringVar(value=t(MODELS[model]))
        ctk.CTkOptionMenu(body, variable=self.model_var, values=list(self.model_labels),
                          command=lambda _: self._save(), **MENU_KW).pack(fill="x", padx=12)

        label(body, t("spoken_language"))
        self.lang_labels = {t("auto_detect"): "auto", **{name: code for code, name in SPOKEN_LANGUAGES.items()}}
        lang = self.settings["language"]
        self.lang_var = ctk.StringVar(value=SPOKEN_LANGUAGES.get(lang, t("auto_detect")))
        ctk.CTkOptionMenu(body, variable=self.lang_var, values=list(self.lang_labels),
                          command=lambda _: self._save(), **MENU_KW).pack(fill="x", padx=12)

        self.translate_var = ctk.BooleanVar(value=self.settings["translate"])
        ctk.CTkSwitch(body, text=t("translate_to_english"), variable=self.translate_var,
                      progress_color=ACCENT, command=self._save).pack(anchor="w", padx=12, pady=(12, 0))

        section(body, t("section_style"))
        self.preset_labels = {t(label_key): name for name, (label_key, _) in PRESET_TEXT.items()}
        preset = self.settings["preset"]
        self.preset_var = ctk.StringVar(value=t(PRESET_TEXT[preset][0]) if preset in PRESET_TEXT
                                        else "" if preset == "" else t("preset_standard"))
        ctk.CTkSegmentedButton(body, values=list(self.preset_labels),
                               variable=self.preset_var, selected_color=ACCENT,
                               selected_hover_color=ACCENT_HOVER, command=self._apply_preset,
                               dynamic_resizing=False).pack(fill="x", padx=12)
        self.preset_hint = hint(body)

        self.chars_label = ctk.CTkLabel(body, text="", font=ctk.CTkFont(size=13))
        self.chars_label.pack(anchor="w", padx=12, pady=(10, 2))
        self.chars_var = ctk.IntVar(value=self.settings["max_chars"])
        ctk.CTkSlider(body, from_=12, to=60, number_of_steps=48, variable=self.chars_var,
                      button_color=ACCENT, button_hover_color=ACCENT_HOVER, progress_color=ACCENT,
                      command=lambda _: self._on_style_change()).pack(fill="x", padx=12)

        label(body, t("lines_per_subtitle"))
        self.lines_var = ctk.StringVar(value=str(self.settings["max_lines"]))
        ctk.CTkSegmentedButton(body, values=["1", "2"], variable=self.lines_var, selected_color=ACCENT,
                               selected_hover_color=ACCENT_HOVER,
                               command=lambda _: self._on_style_change()).pack(fill="x", padx=12)

        section(body, t("section_output"))
        self.txt_var = ctk.BooleanVar(value=self.settings["save_txt"])
        ctk.CTkCheckBox(body, text=t("save_txt"), variable=self.txt_var,
                        fg_color=ACCENT, hover_color=ACCENT_HOVER, command=self._save) \
            .pack(anchor="w", padx=12, pady=(4, 8))
        self.out_label = ctk.CTkLabel(body, text="", text_color=MUTED, font=ctk.CTkFont(size=12),
                                      justify="left", wraplength=270, anchor="w")
        self.out_label.pack(anchor="w", padx=12)
        out_btns = ctk.CTkFrame(body, fg_color="transparent")
        out_btns.pack(fill="x", padx=12, pady=(6, 0))
        ctk.CTkButton(out_btns, text=t("choose_folder"), command=self._choose_output, **OUTLINE_BTN_KW) \
            .pack(side="left", expand=True, fill="x", padx=(0, 4))
        ctk.CTkButton(out_btns, text=t("next_to_source"), command=self._reset_output, **OUTLINE_BTN_KW) \
            .pack(side="left", expand=True, fill="x", padx=(4, 0))

        footer = ctk.CTkFrame(side, fg_color="transparent")
        footer.grid(row=1, column=0, sticky="ew", padx=16, pady=16)
        footer.grid_columnconfigure(0, weight=1)
        self.device_label = ctk.CTkLabel(footer, text=t("starting_engine"), text_color=MUTED,
                                         font=ctk.CTkFont(size=12))
        self.device_label.grid(row=0, column=0, sticky="w")
        self.gpu_link = ctk.CTkButton(footer, text=t("enable_gpu"), height=20, width=0, fg_color="transparent",
                                      hover_color=("gray85", "gray25"), text_color=ACCENT,
                                      font=ctk.CTkFont(size=12, underline=True), command=self._open_settings)
        self.status_label = ctk.CTkLabel(footer, text=t("get_started"), anchor="w",
                                         justify="left", wraplength=280, font=ctk.CTkFont(size=12))
        self.status_label.grid(row=2, column=0, sticky="ew", pady=(2, 8))
        self.start_btn = ctk.CTkButton(footer, text=t("generate"), height=44, corner_radius=10,
                                       fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                       font=ctk.CTkFont(size=15, weight="bold"), command=self._start)
        self.start_btn.grid(row=3, column=0, sticky="ew")
        self.cancel_btn = ctk.CTkButton(footer, text=t("cancel"), height=32, fg_color="transparent",
                                        border_width=1, border_color=ERR, text_color=ERR,
                                        hover_color=("gray85", "gray20"), command=self._cancel)

        self._update_output_label()
        self._on_style_change(save=False)

    # ---------- settings ----------

    def _preset(self) -> str:
        """The selected preset's name, or "" for a custom style."""
        return self.preset_labels.get(self.preset_var.get(), "")

    def _apply_preset(self, choice: str) -> None:
        style = PRESETS[self.preset_labels[choice]]
        self.chars_var.set(style.max_chars)
        self.lines_var.set(str(style.max_lines))
        self._on_style_change(keep_preset=True)

    def _on_style_change(self, keep_preset: bool = False, save: bool = True) -> None:
        chars = int(self.chars_var.get())
        lines = int(self.lines_var.get())
        self.chars_label.configure(text=t("chars_per_line", n=chars))
        current = PRESETS.get(self._preset())
        if not keep_preset and current and (current.max_chars, current.max_lines) != (chars, lines):
            self.preset_var.set("")  # user tweaked the sliders: now a custom style
        preset = self._preset()
        self.preset_hint.configure(text=t(PRESET_TEXT[preset][1]) if preset else t("hint_custom"))
        if save:
            self._save()

    def _caption_style(self) -> CaptionStyle:
        preset = self._preset()
        base = PRESETS.get(preset, PRESETS["Standard"])
        lines = int(self.lines_var.get())
        return CaptionStyle(
            max_chars=int(self.chars_var.get()),
            max_lines=lines,
            max_duration=base.max_duration if preset else (6.0 if lines == 2 else 4.0),
            min_duration=base.min_duration,
            gap_split=base.gap_split,
        )

    def _save(self) -> None:
        self.settings.update(
            model=self.model_labels[self.model_var.get()],
            language=self.lang_labels[self.lang_var.get()],
            translate=self.translate_var.get(),
            preset=self._preset(),
            max_chars=int(self.chars_var.get()),
            max_lines=int(self.lines_var.get()),
            save_txt=self.txt_var.get(),
        )
        save_settings(self.settings)

    def _choose_output(self) -> None:
        folder = filedialog.askdirectory(title=t("dialog_save_to"))
        if folder:
            self.settings["output_dir"] = folder
            save_settings(self.settings)
            self._update_output_label()

    def _reset_output(self) -> None:
        self.settings["output_dir"] = ""
        save_settings(self.settings)
        self._update_output_label()

    def _update_output_label(self) -> None:
        folder = self.settings["output_dir"]
        self.out_label.configure(text=t("saving_to", folder=folder) if folder else t("saving_next_to_source"))

    def _open_settings(self) -> None:
        if self.settings_dialog is not None and self.settings_dialog.winfo_exists():
            self.settings_dialog.focus_force()
        else:
            self.settings_dialog = SettingsDialog(self)

    def open_studio(self) -> None:
        if self.studio is not None and self.studio.winfo_exists():
            self.studio.deiconify()
            self.studio.lift()
            self.studio.focus_force()
            return
        from .studio import StudioWindow
        self.studio = StudioWindow(self)

    def models_dir(self) -> str:
        return self.settings["models_dir"] or default_models_dir()

    def apply_engine_settings(self) -> None:
        if self.transcriber is not None:
            self.transcriber.configure(self.models_dir(), self.settings["use_gpu"])
            if not self.busy():
                self._show_device(self.transcriber.device)

    def restart(self) -> None:
        if self.busy() or self.gpu_dl["state"] == "downloading":
            return
        subprocess.Popen(restart_command())
        self._on_close()

    def download_gpu_pack(self) -> None:
        if self.gpu_dl["state"] == "downloading":
            return
        self.gpu_dl.update(state="downloading", done=0, total=gpu_pack.DOWNLOAD_SIZE, error="")
        self.gpu_cancel.clear()
        self._refresh_gpu_dialog()
        threading.Thread(target=self._run_gpu_download, daemon=True).start()

    def cancel_gpu_pack(self) -> None:
        self.gpu_cancel.set()

    def _run_gpu_download(self) -> None:
        last = 0

        def progress(done: int, total: int) -> None:
            nonlocal last
            if done - last >= total / 400 or done == total:
                last = done
                self.messages.put(("gpu_progress", (done, total)))

        try:
            gpu_pack.install(progress, self.gpu_cancel)
            self.messages.put(("gpu_done", None))
        except gpu_pack.Cancelled:
            self.messages.put(("gpu_error", None))
        except Exception as e:  # noqa: BLE001
            self.messages.put(("gpu_error", str(e) or e.__class__.__name__))

    def _refresh_gpu_dialog(self) -> None:
        if self.settings_dialog is not None and self.settings_dialog.winfo_exists():
            self.settings_dialog.refresh_gpu()

    # ---------- files ----------

    def _browse(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(MEDIA_EXTS))
        paths = filedialog.askopenfilenames(title=t("dialog_choose_files"),
                                            filetypes=[(t("filetype_media"), exts), (t("filetype_all"), "*.*")])
        self._add_files(paths)

    def _on_drop(self, event) -> None:
        self._highlight_drop(False)
        paths = []
        for p in self.tk.splitlist(event.data):
            if os.path.isdir(p):
                for root, _, files in os.walk(p):
                    paths += [os.path.join(root, f) for f in sorted(files)]
            else:
                paths.append(p)
        self._add_files(paths)

    def _highlight_drop(self, on: bool) -> None:
        self.drop.configure(border_color=ACCENT if on else ("gray75", "gray30"))

    def _add_files(self, paths) -> None:
        existing = {j.path for j in self.jobs if j.status in ("waiting", "running")}
        skipped = 0
        for p in paths:
            p = os.path.normpath(p)
            if os.path.splitext(p)[1].lower() not in MEDIA_EXTS:
                skipped += 1
                continue
            if p in existing:
                continue
            job = Job(self._next_id, p)
            self._next_id += 1
            self.jobs.append(job)
            row = QueueRow(self.queue_frame, job, self._remove_row)
            row.grid(row=len(self.rows) + 1, column=0, sticky="ew", pady=4)
            self.rows[job.id] = row
        self.empty_label.grid_remove() if self.rows else self.empty_label.grid()
        if skipped:
            self._set_status(t("skipped_files", n=skipped))
        elif self.rows and not self.busy():
            waiting = sum(j.status == "waiting" for j in self.jobs)
            self._set_status(t("files_ready", n=waiting))

    def _remove_row(self, row: QueueRow) -> None:
        if row.job.status == "running":
            return
        if row.job.status == "waiting" and self.busy():
            row.job.status = "cancelled"
        self.jobs = [j for j in self.jobs if j.id != row.job.id]
        self.rows.pop(row.job.id, None)
        row.destroy()
        if not self.rows:
            self.empty_label.grid()

    def _clear_finished(self) -> None:
        for row in list(self.rows.values()):
            if row.job.status in ("done", "error", "cancelled"):
                self._remove_row(row)

    # ---------- work ----------

    def _load_engine(self) -> None:
        self.messages.put(("gpu_info", gpu_pack.has_nvidia_gpu()))
        try:
            from .engine import Transcriber
            engine = Transcriber(self.models_dir(), self.settings["use_gpu"])
            self.messages.put(("engine", engine))
        except Exception as e:  # noqa: BLE001
            self.messages.put(("engine_error", str(e)))

    def busy(self) -> bool:
        """Transcribing, either the queue or in Caption Studio."""
        studio = self.studio is not None and self.studio.engine_busy()
        return studio or (self.worker is not None and self.worker.is_alive())

    def _start(self) -> None:
        if self.studio is not None and self.studio.engine_busy():
            self._set_status(t("studio_running"))
            return
        if self.busy():
            return
        if self.transcriber is None:
            self._set_status(t("engine_not_ready"))
            return
        pending = [j for j in self.jobs if j.status == "waiting"]
        if not pending:
            self._set_status(t("add_files_first"))
            return
        self._save()
        self.cancel.clear()
        self.start_btn.configure(state="disabled", text=t("working"))
        self.cancel_btn.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        lang = self.lang_labels[self.lang_var.get()]
        config = dict(
            model=self.model_labels[self.model_var.get()],
            language=None if lang == "auto" else lang,
            translate=self.translate_var.get(),
            style=self._caption_style(),
            save_txt=self.txt_var.get(),
            output_dir=self.settings["output_dir"],
        )
        self.worker = threading.Thread(target=self._run, args=(config,), daemon=True)
        self.worker.start()

    def _cancel(self) -> None:
        self.cancel.set()
        self._set_status(t("cancelling"))

    def _run(self, cfg: dict) -> None:
        from .engine import Cancelled

        post = self.messages.put
        try:
            self.transcriber.load(cfg["model"], lambda s: post(("status", s)))
        except Exception as e:  # noqa: BLE001
            post(("status", t("model_load_failed", error=e)))
            post(("finished", None))
            return
        post(("device", self.transcriber.device))

        done = 0
        while not self.cancel.is_set():
            job = next((j for j in self.jobs if j.status == "waiting"), None)
            if job is None:
                break
            job.status = "running"
            post(("clear_preview", None))
            post(("status", t("transcribing", name=os.path.basename(job.path))))
            try:
                result = self.transcriber.transcribe(
                    job.path, cfg["style"], cfg["language"], cfg["translate"],
                    lambda pct, text, jid=job.id: post(("progress", (jid, pct, text))),
                    self.cancel,
                )
                if not result.cues:
                    raise RuntimeError(t("no_speech"))
                folder = cfg["output_dir"] or os.path.dirname(job.path)
                stem = os.path.splitext(os.path.basename(job.path))[0]
                srt_path = unique_path(os.path.join(folder, stem + ".srt"))
                write_srt(srt_path, result.cues)
                if cfg["save_txt"]:
                    with open(unique_path(os.path.join(folder, stem + ".txt")), "w", encoding="utf-8") as f:
                        f.write(result.text + "\n")
                job.status, job.output = "done", srt_path
                summary = (f"{os.path.basename(srt_path)}  ·  {t('subtitle_count', n=len(result.cues))}  ·  "
                           f"{format_duration(result.duration)}  ·  {result.language.upper()}")
                post(("done", (job.id, srt_path, summary)))
                done += 1
            except Cancelled:
                job.status = "waiting"
                post(("job_cancelled", job.id))
            except Exception as e:  # noqa: BLE001
                job.status = "error"
                post(("error", (job.id, str(e) or e.__class__.__name__)))

        post(("status", t("all_cancelled") if self.cancel.is_set() else t("all_done", n=done)))
        post(("finished", None))

    def _poll(self) -> None:
        try:
            while True:
                kind, data = self.messages.get_nowait()
                self._handle(kind, data)
        except queue.Empty:
            pass
        self.after(80, self._poll)

    def _handle(self, kind: str, data) -> None:
        if kind == "engine":
            self.transcriber = data
            self.apply_engine_settings()  # in case they were changed while the engine loaded
            self._show_device(data.device)
        elif kind == "gpu_info":
            self.has_gpu = data
            if self.transcriber is not None:
                self._show_device(self.transcriber.device)
        elif kind == "gpu_progress":
            self.gpu_dl["done"], self.gpu_dl["total"] = data
            self._refresh_gpu_dialog()
        elif kind == "gpu_done":
            self.gpu_dl["state"] = "done"
            self.gpu_link.grid_remove()
            self._refresh_gpu_dialog()
        elif kind == "gpu_error":
            # None means the user cancelled
            self.gpu_dl.update(state="idle" if data is None else "error", error=data or "")
            self._refresh_gpu_dialog()
        elif kind == "engine_error":
            self.device_label.configure(text=t("engine_failed"), text_color=ERR)
            self._set_status(data)
        elif kind == "device":
            self._show_device(data)
        elif kind == "status":
            self._set_status(data)
        elif kind == "clear_preview":
            self.preview.configure(state="normal")
            self.preview.delete("1.0", "end")
            self.preview.configure(state="disabled")
        elif kind == "progress":
            jid, pct, text = data
            if jid in self.rows:
                self.rows[jid].set_progress(pct, f"{int(pct * 100)}%" if pct or text else t("preparing_audio"))
            if text:
                self.preview.configure(state="normal")
                self.preview.insert("end", text + " ")
                self.preview.see("end")
                self.preview.configure(state="disabled")
        elif kind == "job_cancelled":
            if data in self.rows:
                self.rows[data].set_progress(0, t("cancelled"))
        elif kind == "done":
            jid, path, summary = data
            if jid in self.rows:
                self.rows[jid].set_done(path, summary)
        elif kind == "error":
            jid, msg = data
            if jid in self.rows:
                self.rows[jid].set_error(msg)
        elif kind == "finished":
            self.start_btn.configure(state="normal", text=t("generate"))
            self.cancel_btn.grid_remove()

    def _show_device(self, device: str) -> None:
        if device == "cuda":
            self.device_label.configure(text=t("gpu_on"), text_color=OK)
        else:
            self.device_label.configure(text=t("cpu_mode"), text_color=MUTED)
        offer = (device != "cuda" and self.has_gpu and self.settings["use_gpu"]
                 and not gpu_pack.installed() and self.gpu_dl["state"] != "done")
        if offer:
            self.gpu_link.grid(row=1, column=0, sticky="w")
        else:
            self.gpu_link.grid_remove()

    def _set_status(self, text: str) -> None:
        self.status_label.configure(text=text)

    def _on_close(self) -> None:
        self.cancel.set()
        if self.studio is not None:
            self.studio.cancel.set()
            if self.studio.player is not None:
                self.studio.player.close()
        self.destroy()
        os._exit(0)  # don't wait on a model download / transcription thread


def _redirect_output() -> None:
    """Without a console (pythonw / the packaged .exe) writes to stdout/stderr would fail; log them instead."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        log = open(os.path.join(DATA_DIR, "transcriber.log"), "w", encoding="utf-8", buffering=1)
    except OSError:
        log = open(os.devnull, "w")
    sys.stdout = sys.stdout or log
    sys.stderr = sys.stderr or log


def _selftest(path: str, model: str) -> int:
    """Transcriber.exe --selftest <audio> [model]: one headless transcription, results in the log."""
    from .engine import Transcriber

    settings = load_settings()
    engine = Transcriber(settings["models_dir"] or default_models_dir(), settings["use_gpu"])
    from .player import sounddevice
    print(f"Transcriber {__version__}  device={engine.device}  gpu_pack={gpu_pack.installed()}"
          f"  nvidia_gpu={gpu_pack.has_nvidia_gpu()}  audio_out={sounddevice is not None}", flush=True)
    try:
        engine.load(model, print)
        result = engine.transcribe(path, PRESETS["Standard"], None, False, lambda pct, text: None,
                                   threading.Event())
    except Exception as e:  # noqa: BLE001
        print(f"SELFTEST FAILED: {e!r}", flush=True)
        return 1
    print(f"device={engine.device}  language={result.language}  subtitles={len(result.cues)}", flush=True)
    print(result.text, flush=True)
    print("SELFTEST OK", flush=True)
    return 0


def main() -> None:
    _redirect_output()
    if len(sys.argv) >= 3 and sys.argv[1] == "--selftest":
        sys.exit(_selftest(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "large-v3-turbo"))
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Transcriber.App")
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        pass
    settings = load_settings()
    set_language(settings["ui_language"])
    ctk.set_appearance_mode(settings["appearance"])
    App(settings).mainloop()
