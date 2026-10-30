"""Caption Studio: transcribe a short video, check the captions while it plays, style and animate them,
then render a copy of the video with the captions burned in. A page (tab) of the main window."""

from __future__ import annotations

import json
import os
import queue
import threading
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox

import customtkinter as ctk
from PIL import ImageTk

from .app import (ACCENT, ACCENT_HOVER, ACCENT_TEXT, CARD, ERR, MENU_KW, MONO_FONT, MUTED, OK, OUTLINE_BTN_KW,
                  SURFACE, TEXT_FONT, TITLE_FONT, VIDEO_EXTS, hint, save_settings, section,
                  show_in_explorer, unique_path)
from .captions import (ANIMATIONS, LOOKS, WORD_EFFECTS, Caption, Look, Renderer, all_words, available_fonts,
                       caption_at, group_words, retext, retime, spread)
from .i18n import SPOKEN_LANGUAGES, t
from .paths import DATA_DIR
from .subtitles import PRESETS, Cue, Word, wrap, write_srt

SESSION_PATH = os.path.join(DATA_DIR, "studio_session.json")
GROUPING_TEXT = {"word": "words_one", "short": "words_few", "line": "words_line", "two": "words_two"}
ANIMATION_TEXT = {"none": "anim_none", "pop": "anim_pop", "bounce": "anim_bounce", "fade": "anim_fade",
                  "slide": "anim_slide"}
EFFECT_TEXT = {"none": "effect_none", "color": "effect_color", "box": "effect_box", "pop": "effect_pop",
               "reveal": "effect_reveal"}
# Roughly where TikTok, Reels and Shorts put their buttons and captions: (left, top, right, bottom) margins
SAFE_ZONE = (0.06, 0.10, 0.14, 0.22)
ROW = ("gray95", "#1C1C1C")
ROW_NOW = ("#F6DADA", "#3A1214")


def fmt_time(seconds: float) -> str:
    m, s = divmod(max(0.0, seconds), 60)
    return f"{int(m)}:{s:05.2f}"


def parse_time(text: str) -> float | None:
    try:
        parts = text.strip().replace(",", ".").split(":")
        seconds = float(parts[-1])
        if len(parts) == 2:
            seconds += 60 * int(parts[0])
        elif len(parts) > 2:
            return None
        return seconds if seconds >= 0 else None
    except ValueError:
        return None


class CaptionRow(ctk.CTkFrame):
    def __init__(self, master, on_click) -> None:
        super().__init__(master, fg_color=ROW, corner_radius=8, border_width=2, border_color=ROW[1])
        self.index = -1
        self.grid_columnconfigure(1, weight=1)
        self.time = ctk.CTkLabel(self, text="", text_color=MUTED, width=64, anchor="w",
                                 font=ctk.CTkFont(size=11, family=MONO_FONT))
        self.time.grid(row=0, column=0, padx=(10, 6), pady=6, sticky="nw")
        self.text = ctk.CTkLabel(self, text="", anchor="w", justify="left", wraplength=300,
                                 font=ctk.CTkFont(family=TEXT_FONT, size=14))
        self.text.grid(row=0, column=1, padx=(0, 10), pady=6, sticky="ew")
        for w in (self, self.time, self.text):
            w.bind("<Button-1>", lambda e: on_click(self.index))

    def show(self, index: int, cap: Caption) -> None:
        self.index = index
        self.time.configure(text=fmt_time(cap.start))
        self.text.configure(text=cap.text)

    def mark(self, selected: bool, now: bool) -> None:
        state = (selected, now)
        if state != getattr(self, "_state", None):
            self._state = state
            fill = ROW_NOW if now else ROW
            self.configure(fg_color=fill, border_color=ACCENT if selected else fill)


class StudioPage(ctk.CTkFrame):
    def __init__(self, master, app) -> None:
        super().__init__(master, fg_color="transparent")
        self.app = app

        self.info = None
        self.player = None
        self.captions: list[Caption] = []
        self.caption_lang = ""         # language the captions are in, once known
        self.renderer = Renderer()
        self.look = Look.from_dict(app.settings.get("caption_look") or {})
        self.selected = -1
        self.current = -1
        self.rows: list[CaptionRow] = []
        self._edit_base: list[Word] = []
        self.messages: queue.Queue = queue.Queue()
        self.cancel = threading.Event()
        self.task: threading.Thread | None = None
        self.task_kind = ""
        self.output = ""
        self._photo = None
        self._drawn = None
        self._display = (0, 0, 1, 1)  # x, y, width, height of the picture on the canvas
        self._caption_box = None       # where the caption is drawn on the canvas
        self._drag = None
        self._scrubbing = False
        self._save_job = None
        self._session_job = None
        self._shown = {}               # last values put in the transport bar

        self.grid_columnconfigure(0, weight=1, uniform="cols")
        self.grid_columnconfigure(1, weight=1, uniform="cols")
        self.grid_columnconfigure(2, weight=0, minsize=350)
        self.grid_rowconfigure(1, weight=1)
        self._build_header()
        self._build_player()
        self._build_captions()
        self._build_style()
        self._sync_controls()

        # Files dropped on the window come in through App._on_drop.
        for key, action in (("<space>", lambda: self._toggle_play()),
                            ("<Left>", lambda: self._nudge(-1.0)), ("<Right>", lambda: self._nudge(1.0)),
                            ("<Shift-Left>", lambda: self._nudge(-0.1)), ("<Shift-Right>", lambda: self._nudge(0.1)),
                            ("<Up>", lambda: self._step_caption(-1)), ("<Down>", lambda: self._step_caption(1))):
            self.winfo_toplevel().bind(key, lambda e, a=action: self._shortcut(e, a), add="+")
        self.after(15, self._tick)

    # ---------- layout ----------

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=3, sticky="ew", padx=20, pady=(0, 10))
        header.grid_columnconfigure(1, weight=1)
        self.file_label = ctk.CTkLabel(header, text=t("studio_tagline"), text_color=MUTED, anchor="w",
                                       font=ctk.CTkFont(size=13))
        self.file_label.grid(row=0, column=1, sticky="ew")
        ctk.CTkButton(header, text=t("open_video"), width=130, command=self._browse,
                      **{**OUTLINE_BTN_KW, "height": 32}).grid(row=0, column=2, sticky="e")

    def _build_player(self) -> None:
        box = ctk.CTkFrame(self, fg_color=CARD, corner_radius=14)
        box.grid(row=1, column=0, sticky="nsew", padx=(20, 8), pady=(0, 20))
        box.grid_columnconfigure(0, weight=1)
        box.grid_rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(box, bg="#000000", highlightthickness=0, bd=0)
        self.canvas.grid(row=0, column=0, sticky="nsew", padx=10, pady=(10, 6))
        self.canvas.bind("<Configure>", lambda e: self._layout_canvas())
        self.canvas.bind("<ButtonPress-1>", self._canvas_press)
        self.canvas.bind("<B1-Motion>", self._canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._canvas_release)
        self.canvas.bind("<Motion>", self._canvas_hover)

        bar = ctk.CTkFrame(box, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        bar.grid_columnconfigure(2, weight=1)
        self.play_btn = ctk.CTkButton(bar, text="▶", width=40, height=32, fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                      font=ctk.CTkFont(size=15), command=self._toggle_play, state="disabled")
        self.play_btn.grid(row=0, column=0)
        self.time_label = ctk.CTkLabel(bar, text="0:00.00 / 0:00.00", width=130,
                                       font=ctk.CTkFont(size=12, family=MONO_FONT))
        self.time_label.grid(row=0, column=1, padx=8)
        self.scrub = ctk.CTkSlider(bar, from_=0, to=1, button_color=ACCENT, button_hover_color=ACCENT_HOVER,
                                   progress_color=ACCENT, command=self._on_scrub, state="disabled")
        self.scrub.set(0)
        self.scrub.grid(row=0, column=2, sticky="ew")
        self.scrub.bind("<ButtonPress-1>", lambda e: setattr(self, "_scrubbing", True), add="+")
        self.scrub.bind("<ButtonRelease-1>", lambda e: setattr(self, "_scrubbing", False), add="+")

    def _build_captions(self) -> None:
        box = ctk.CTkFrame(self, fg_color=CARD, corner_radius=14)
        box.grid(row=1, column=1, sticky="nsew", padx=8, pady=(0, 20))
        box.grid_columnconfigure(0, weight=1)
        box.grid_rowconfigure(4, weight=1)

        top = ctk.CTkFrame(box, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 0))
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top, text=t("captions"), font=ctk.CTkFont(family=TITLE_FONT, size=18)) \
            .grid(row=0, column=0, sticky="w")
        self.lang_labels = {t("auto_detect"): "auto", **{name: code for code, name in SPOKEN_LANGUAGES.items()}}
        lang = self.app.settings["language"]
        self.lang_var = ctk.StringVar(value=SPOKEN_LANGUAGES.get(lang, t("auto_detect")))
        ctk.CTkOptionMenu(top, variable=self.lang_var, values=list(self.lang_labels), width=150,
                          command=self._on_language, **MENU_KW).grid(row=0, column=1, padx=(8, 8))
        self.transcribe_btn = ctk.CTkButton(top, text=t("transcribe"), width=120, fg_color=ACCENT,
                                            hover_color=ACCENT_HOVER, command=self._transcribe, state="disabled")
        self.transcribe_btn.grid(row=0, column=2)

        status = ctk.CTkFrame(box, fg_color="transparent")
        status.grid(row=1, column=0, sticky="ew", padx=12, pady=(6, 0))
        status.grid_columnconfigure(0, weight=1)
        self.status = ctk.CTkLabel(status, text=t("studio_open_hint"), text_color=MUTED, anchor="w",
                                   justify="left", wraplength=420, font=ctk.CTkFont(size=12))
        self.status.grid(row=0, column=0, sticky="ew")
        self.task_bar = ctk.CTkProgressBar(status, height=5, progress_color=ACCENT)
        self.task_bar.set(0)

        self.editor = ctk.CTkFrame(box, fg_color=SURFACE, corner_radius=10)
        self.editor.grid_columnconfigure(0, weight=1)
        self.edit_text = ctk.CTkTextbox(self.editor, height=58, wrap="word",
                                        font=ctk.CTkFont(family=TEXT_FONT, size=16),
                                        fg_color=CARD, border_width=1, border_color=("gray75", "gray30"))
        self.edit_text.grid(row=0, column=0, columnspan=2, sticky="ew", padx=10, pady=(10, 6))
        self.edit_text.bind("<KeyRelease>", lambda e: self._on_text_edit())
        self.edit_text.bind("<Return>", lambda e: (self.canvas.focus_set(), "break")[1])
        self.edit_text.bind("<Escape>", lambda e: self.canvas.focus_set())

        times = ctk.CTkFrame(self.editor, fg_color="transparent")
        times.grid(row=1, column=0, columnspan=2, sticky="ew", padx=10)
        self.start_var, self.end_var = tk.StringVar(), tk.StringVar()
        for col, (key, var, which) in enumerate((("start", self.start_var, "start"), ("end", self.end_var, "end"))):
            ctk.CTkLabel(times, text=t(key), font=ctk.CTkFont(size=12)).grid(row=0, column=col * 3, padx=(0 if col == 0 else 14, 4))
            entry = ctk.CTkEntry(times, textvariable=var, width=78, font=ctk.CTkFont(size=12, family=MONO_FONT))
            entry.grid(row=0, column=col * 3 + 1)
            entry.bind("<Return>", lambda e: self._apply_times())
            entry.bind("<FocusOut>", lambda e: self._apply_times())
            ctk.CTkButton(times, text="⌖", width=28, height=28, command=lambda w=which: self._time_to_playhead(w),
                          **{k: v for k, v in OUTLINE_BTN_KW.items() if k != "height"}) \
                .grid(row=0, column=col * 3 + 2, padx=(4, 0))
        self.playhead_hint = ctk.CTkLabel(times, text=t("set_to_playhead"), text_color=MUTED, font=ctk.CTkFont(size=11))
        self.playhead_hint.grid(row=0, column=6, padx=(10, 0))

        actions = ctk.CTkFrame(self.editor, fg_color="transparent")
        actions.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=(8, 10))
        for col, (key, command) in enumerate((("split_here", self._split), ("merge_next", self._merge),
                                              ("delete", self._delete))):
            kw = dict(OUTLINE_BTN_KW)
            if key == "delete":
                kw.update(border_color=ERR, text_color=ERR)
            ctk.CTkButton(actions, text=t(key), command=command, **kw) \
                .grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 6, 0))
            actions.grid_columnconfigure(col, weight=1)

        self.list_head = ctk.CTkFrame(box, fg_color="transparent")
        self.list_head.grid(row=3, column=0, sticky="ew", padx=12, pady=(10, 0))
        self.list_head.grid_columnconfigure(0, weight=1)
        self.count_label = ctk.CTkLabel(self.list_head, text="", text_color=MUTED, font=ctk.CTkFont(size=12))
        self.count_label.grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))
        self.target_labels = {name: code for code, name in SPOKEN_LANGUAGES.items()}
        self.translate_menu = ctk.CTkOptionMenu(self.list_head, values=list(self.target_labels), width=130,
                                                height=28, variable=ctk.StringVar(value=t("translate_menu")),
                                                command=self._translate, state="disabled", **MENU_KW)
        self.translate_menu.grid(row=0, column=1, padx=(0, 6))
        self.add_btn = ctk.CTkButton(self.list_head, text=t("add_caption"), width=0, command=self._add,
                                     state="disabled", **OUTLINE_BTN_KW)
        self.add_btn.grid(row=0, column=2)

        self.list = ctk.CTkScrollableFrame(box, fg_color="transparent")
        self.list.grid(row=4, column=0, sticky="nsew", padx=6, pady=(4, 10))
        self.list.grid_columnconfigure(0, weight=1)
        self.empty = ctk.CTkLabel(self.list, text=t("no_captions"), text_color=MUTED, justify="center",
                                  wraplength=360)
        self.empty.grid(row=0, column=0, pady=40)

    def _build_style(self) -> None:
        side = ctk.CTkFrame(self, fg_color=CARD, corner_radius=14, width=350)
        side.grid(row=1, column=2, sticky="nsew", padx=(8, 20), pady=(0, 20))
        side.grid_propagate(False)
        side.grid_columnconfigure(0, weight=1)
        side.grid_rowconfigure(0, weight=1)
        body = ctk.CTkScrollableFrame(side, fg_color="transparent")
        body.grid(row=0, column=0, sticky="nsew", padx=4, pady=(8, 0))
        body.grid_columnconfigure(0, weight=1)
        self.controls: dict = {}

        section(body, t("section_looks"))
        grid = ctk.CTkFrame(body, fg_color="transparent")
        grid.pack(fill="x", padx=12)
        for i, (name, (key, _)) in enumerate(LOOKS.items()):
            ctk.CTkButton(grid, text=t(key), height=30, command=lambda n=name: self._apply_look(n),
                          **{k: v for k, v in OUTLINE_BTN_KW.items() if k != "height"}) \
                .grid(row=i // 2, column=i % 2, sticky="ew", padx=2, pady=2)
            grid.grid_columnconfigure(i % 2, weight=1, uniform="looks")

        section(body, t("section_text"))
        font_row = ctk.CTkFrame(body, fg_color="transparent")
        font_row.pack(fill="x", padx=12, pady=(4, 0))
        font_row.grid_columnconfigure(0, weight=1)
        self.fonts = available_fonts()
        self.font_var = ctk.StringVar()
        self.font_menu = ctk.CTkOptionMenu(font_row, variable=self.font_var, values=self.fonts or ["Arial"],
                                           command=self._on_font, **MENU_KW)
        self.font_menu.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(font_row, text="…", width=34, command=self._choose_font_file,
                      **{k: v for k, v in OUTLINE_BTN_KW.items() if k != "height"}).grid(row=0, column=1, padx=(6, 0))

        self._slider(body, "size", t("text_size"), 3.0, 14.0, 110, lambda v: f"{v:.1f}")
        self.upper_var = ctk.BooleanVar()
        ctk.CTkSwitch(body, text=t("all_caps"), variable=self.upper_var, progress_color=ACCENT,
                      command=lambda: self._set(uppercase=self.upper_var.get())).pack(anchor="w", padx=12, pady=(10, 0))

        ctk.CTkLabel(body, text=t("words_on_screen"), font=ctk.CTkFont(size=13)).pack(anchor="w", padx=12, pady=(10, 0))
        self.grouping_labels = {t(key): name for name, key in GROUPING_TEXT.items()}
        self.grouping_var = ctk.StringVar()
        ctk.CTkSegmentedButton(body, values=list(self.grouping_labels), variable=self.grouping_var,
                               selected_color=ACCENT, selected_hover_color=ACCENT_HOVER, dynamic_resizing=False,
                               command=lambda v: self._on_grouping(self.grouping_labels[v])).pack(fill="x", padx=12)

        section(body, t("section_animation"))
        ctk.CTkLabel(body, text=t("caption_entrance"), font=ctk.CTkFont(size=13)).pack(anchor="w", padx=12)
        self.anim_labels = {t(ANIMATION_TEXT[a]): a for a in ANIMATIONS}
        self.anim_var = ctk.StringVar()
        ctk.CTkOptionMenu(body, variable=self.anim_var, values=list(self.anim_labels),
                          command=lambda v: self._set(animation=self.anim_labels[v]), **MENU_KW).pack(fill="x", padx=12)
        ctk.CTkLabel(body, text=t("spoken_word"), font=ctk.CTkFont(size=13)).pack(anchor="w", padx=12, pady=(8, 0))
        self.effect_labels = {t(EFFECT_TEXT[e]): e for e in WORD_EFFECTS}
        self.effect_var = ctk.StringVar()
        ctk.CTkOptionMenu(body, variable=self.effect_var, values=list(self.effect_labels),
                          command=lambda v: self._set(word_effect=self.effect_labels[v]), **MENU_KW) \
            .pack(fill="x", padx=12)

        section(body, t("section_colours"))
        swatches = ctk.CTkFrame(body, fg_color="transparent")
        swatches.pack(fill="x", padx=12)
        self.swatches = {}
        for col, (field, key) in enumerate((("text_color", "colour_text"), ("highlight_color", "colour_highlight"),
                                            ("outline_color", "colour_outline"))):
            swatches.grid_columnconfigure(col, weight=1)
            ctk.CTkLabel(swatches, text=t(key), font=ctk.CTkFont(size=12)).grid(row=0, column=col)
            btn = ctk.CTkButton(swatches, text="", width=56, height=28, border_width=1,
                                border_color=("gray60", "gray40"), command=lambda f=field: self._pick_colour(f))
            btn.grid(row=1, column=col, pady=(2, 0))
            self.swatches[field] = btn
        self._slider(body, "outline", t("outline"), 0.0, 0.25, 25, lambda v: f"{round(v * 100)}%")
        self._slider(body, "shadow", t("shadow"), 0.0, 1.0, 20, lambda v: f"{round(v * 100)}%")

        section(body, t("section_background"))
        bg_row = ctk.CTkFrame(body, fg_color="transparent")
        bg_row.pack(fill="x", padx=12)
        bg_row.grid_columnconfigure(0, weight=1)
        self.bg_var = ctk.BooleanVar()
        ctk.CTkSwitch(bg_row, text=t("background_box"), variable=self.bg_var, progress_color=ACCENT,
                      command=lambda: self._set(background=self.bg_var.get())).grid(row=0, column=0, sticky="w")
        btn = ctk.CTkButton(bg_row, text="", width=56, height=28, border_width=1, border_color=("gray60", "gray40"),
                            command=lambda: self._pick_colour("background_color"))
        btn.grid(row=0, column=1)
        self.swatches["background_color"] = btn
        self._slider(body, "background_opacity", t("opacity"), 0.1, 1.0, 18, lambda v: f"{round(v * 100)}%")

        section(body, t("section_position"))
        self._slider(body, "position", t("height_on_screen"), 0.08, 0.92, 84, lambda v: f"{round(v * 100)}%")
        hint(body, t("drag_hint"), wraplength=300)
        self.safe_var = ctk.BooleanVar(value=self.app.settings.get("studio_safe_zone", True))
        ctk.CTkSwitch(body, text=t("safe_zone"), variable=self.safe_var, progress_color=ACCENT,
                      command=self._on_safe_zone).pack(anchor="w", padx=12, pady=(10, 0))
        hint(body, t("safe_zone_hint"), wraplength=300)
        ctk.CTkFrame(body, fg_color="transparent", height=10).pack()

        footer = ctk.CTkFrame(side, fg_color="transparent")
        footer.grid(row=1, column=0, sticky="ew", padx=16, pady=14)
        footer.grid_columnconfigure((0, 1), weight=1)
        self.render_btn = ctk.CTkButton(footer, text=t("render_video"), height=44, corner_radius=10,
                                        fg_color=ACCENT, hover_color=ACCENT_HOVER, state="disabled",
                                        font=ctk.CTkFont(size=15, weight="bold"), command=self._render)
        self.render_btn.grid(row=0, column=0, columnspan=2, sticky="ew")
        self.srt_btn = ctk.CTkButton(footer, text=t("save_srt"), command=self._save_srt, state="disabled",
                                     **OUTLINE_BTN_KW)
        self.srt_btn.grid(row=1, column=0, sticky="ew", pady=(8, 0), padx=(0, 4))
        self.show_btn = ctk.CTkButton(footer, text=t("show"), command=lambda: show_in_explorer(self.output),
                                      state="disabled", **OUTLINE_BTN_KW)
        self.show_btn.grid(row=1, column=1, sticky="ew", pady=(8, 0), padx=(4, 0))
        self.cancel_btn = ctk.CTkButton(footer, text=t("cancel"), height=32, fg_color="transparent",
                                        border_width=1, border_color=ERR, text_color=ERR,
                                        hover_color=("gray85", "gray20"), command=self.cancel.set)

    def _slider(self, parent, field: str, text: str, lo: float, hi: float, steps: int, show) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=(10, 0))
        row.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(row, text=text, font=ctk.CTkFont(size=13)).grid(row=0, column=0, sticky="w")
        value = ctk.CTkLabel(row, text="", text_color=MUTED, font=ctk.CTkFont(size=12))
        value.grid(row=0, column=1, sticky="e")
        var = ctk.DoubleVar()

        def changed(v):
            value.configure(text=show(float(v)))
            self._set(**{field: float(v)})

        ctk.CTkSlider(parent, from_=lo, to=hi, number_of_steps=steps, variable=var, button_color=ACCENT,
                      button_hover_color=ACCENT_HOVER, progress_color=ACCENT, command=changed) \
            .pack(fill="x", padx=12)
        self.controls[field] = (var, value, show)

    # ---------- style ----------

    def _sync_controls(self) -> None:
        look = self.look
        for field, (var, value, show) in self.controls.items():
            var.set(getattr(look, field))
            value.configure(text=show(getattr(look, field)))
        name = look.font if look.font in self.fonts else os.path.basename(look.font)
        values = self.fonts + ([name] if name not in self.fonts else [])
        self.font_menu.configure(values=values)
        self.font_var.set(name)
        self.upper_var.set(look.uppercase)
        self.bg_var.set(look.background)
        self.grouping_var.set(t(GROUPING_TEXT[look.grouping]))
        self.anim_var.set(t(ANIMATION_TEXT[look.animation]))
        self.effect_var.set(t(EFFECT_TEXT[look.word_effect]))
        for field, btn in self.swatches.items():
            colour = getattr(look, field)
            btn.configure(fg_color=colour, hover_color=colour)

    def _set(self, **changes) -> None:
        for k, v in changes.items():
            setattr(self.look, k, v)
        self._save_look()

    def _save_look(self) -> None:
        if self._save_job is not None:
            self.after_cancel(self._save_job)

        def save():
            self._save_job = None
            self.app.settings["caption_look"] = self.look.to_dict()
            save_settings(self.app.settings)

        self._save_job = self.after(500, save)

    def _apply_look(self, name: str) -> None:
        preset = Look.from_dict(LOOKS[name][1].to_dict())
        preset.position = self.look.position  # keep wherever the user placed the captions
        regroup = preset.grouping != self.look.grouping
        self.look = preset
        self._sync_controls()
        self._save_look()
        if regroup:
            self._regroup()

    def _on_font(self, choice: str) -> None:
        if choice in self.fonts:
            self._set(font=choice)

    def _choose_font_file(self) -> None:
        path = filedialog.askopenfilename(parent=self, title=t("choose_font"),
                                          initialdir=os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
                                          filetypes=[(t("filetype_fonts"), "*.ttf *.otf *.ttc"), (t("filetype_all"), "*.*")])
        if path:
            self._set(font=os.path.normpath(path))
            self._sync_controls()

    def _pick_colour(self, field: str) -> None:
        _, colour = colorchooser.askcolor(color=getattr(self.look, field), parent=self, title=t("choose_colour"))
        if colour:
            self._set(**{field: colour.upper()})
            self.swatches[field].configure(fg_color=colour, hover_color=colour)

    def _on_grouping(self, grouping: str) -> None:
        if grouping != self.look.grouping:
            self._set(grouping=grouping)
            self._regroup()

    def _on_safe_zone(self) -> None:
        self.app.settings["studio_safe_zone"] = self.safe_var.get()
        save_settings(self.app.settings)
        self._drawn = None

    def _on_language(self, choice: str) -> None:
        self.app.settings["language"] = self.lang_labels[choice]
        save_settings(self.app.settings)
        if choice in getattr(self.app, "lang_labels", {}):
            self.app.lang_var.set(choice)

    # ---------- opening a video ----------

    def _browse(self) -> None:
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTS))
        path = filedialog.askopenfilename(parent=self, title=t("open_video"),
                                          filetypes=[(t("filetype_video"), exts), (t("filetype_all"), "*.*")])
        if path:
            self.open(path)

    def _on_drop(self, event) -> None:
        paths = [p for p in self.tk.splitlist(event.data) if os.path.splitext(p)[1].lower() in VIDEO_EXTS]
        if paths:
            self.open(paths[0])
        else:
            self._status(t("not_a_video"), ERR)

    def open(self, path: str) -> None:
        if self.task is not None and self.task.is_alive():
            self._status(t("studio_wait"))
            return
        from .video import probe
        path = os.path.normpath(path)
        try:
            info = probe(path)
        except Exception as e:  # noqa: BLE001
            self._status(t("open_failed", error=str(e) or e.__class__.__name__), ERR)
            return
        if self.player is not None:
            self.player.close()
        from .player import Player
        self.info = info
        self.player = Player(info)
        self.output = ""
        self.show_btn.configure(state="disabled")
        fps = float(info.fps)
        fps_text = f"{fps:.3f}".rstrip("0").rstrip(".")
        self.file_label.configure(text=f"{os.path.basename(path)}   ·   {info.width}×{info.height}   ·   "
                                       f"{fps_text} fps   ·   {fmt_time(info.duration)[:-3]}",
                                  text_color=("gray10", "gray90"))
        self.scrub.configure(to=max(info.duration, 0.01), state="normal")
        self.scrub.set(0)
        for w in (self.play_btn, self.transcribe_btn, self.add_btn):
            w.configure(state="normal")
        self._layout_canvas()

        self.caption_lang = ""
        restored = self._restore_session()
        if restored:
            self._set_captions(restored)
            self._status(t("restored_captions"), OK)
        else:
            self._set_captions([])
            if info.has_audio:
                self._status(t("ready_to_transcribe"))
                if self.app.transcriber is not None and not self.app.busy():
                    self._transcribe()
            else:
                self._status(t("no_audio"), ERR)

    # ---------- captions ----------

    def _set_captions(self, captions: list[Caption], keep_selection: bool = False) -> None:
        self.captions = captions
        self.renderer.set_captions(captions)
        if not keep_selection or self.selected >= len(captions):
            self._select(-1, seek=False)
        self._refresh_rows()
        state = "normal" if captions else "disabled"
        self.render_btn.configure(state=state if not self._task_running() else "disabled")
        self.srt_btn.configure(state=state)
        self.translate_menu.configure(state=state if not self._task_running() else "disabled")
        self.transcribe_btn.configure(text=t("retranscribe") if captions else t("transcribe"))
        self._changed()

    def _changed(self) -> None:
        """Captions were edited: redraw and remember them in case the window is closed."""
        self.renderer.set_captions(self.captions)
        self._drawn = None
        if self._session_job is not None:
            self.after_cancel(self._session_job)
        self._session_job = self.after(800, self._save_session)

    def _refresh_rows(self) -> None:
        while len(self.rows) > len(self.captions):
            self.rows.pop().destroy()
        for i, cap in enumerate(self.captions):
            if i == len(self.rows):
                row = CaptionRow(self.list, self._on_row_click)
                row.grid(row=i + 1, column=0, sticky="ew", pady=2, padx=(0, 4))
                self.rows.append(row)
            self.rows[i].show(i, cap)
            self.rows[i].mark(i == self.selected, i == self.current)
        self.empty.grid_remove() if self.captions else self.empty.grid()
        self.count_label.configure(text=t("caption_count", n=len(self.captions)) if self.captions else "")

    def _mark_rows(self) -> None:
        for i, row in enumerate(self.rows):
            row.mark(i == self.selected, i == self.current)

    def _on_row_click(self, index: int) -> None:
        self._select(index)

    def _select(self, index: int, seek: bool = True) -> None:
        self.selected = index if 0 <= index < len(self.captions) else -1
        if self.selected < 0:
            self.editor.grid_remove()
            self._mark_rows()
            return
        cap = self.captions[self.selected]
        self._edit_base = [Word(w.start, w.end, w.text) for w in cap.words]
        self.edit_text.delete("1.0", "end")
        self.edit_text.insert("1.0", cap.text)
        self.start_var.set(fmt_time(cap.start))
        self.end_var.set(fmt_time(cap.end))
        self.editor.grid(row=2, column=0, sticky="ew", padx=12, pady=(8, 0))
        self._mark_rows()
        if seek and self.player is not None:
            self.player.seek(cap.start + 0.001)

    def _on_text_edit(self) -> None:
        if self.selected < 0:
            return
        cap = self.captions[self.selected]
        text = " ".join(self.edit_text.get("1.0", "end").split())
        if text and text != cap.text:
            retext(cap, text, self._edit_base)
            self.rows[self.selected].show(self.selected, cap)
            self._changed()

    def _apply_times(self) -> None:
        if self.selected < 0:
            return
        cap = self.captions[self.selected]
        start, end = parse_time(self.start_var.get()), parse_time(self.end_var.get())
        if start is None or end is None:
            self.start_var.set(fmt_time(cap.start))
            self.end_var.set(fmt_time(cap.end))
            return
        self._retime(self.selected, start, end)

    def _retime(self, index: int, start: float, end: float) -> None:
        cap = self.captions[index]
        prev = self.captions[index - 1] if index > 0 else None
        nxt = self.captions[index + 1] if index + 1 < len(self.captions) else None
        limit = self.info.duration if self.info and self.info.duration else end
        start = max(prev.start + 0.1 if prev else 0.0, min(start, limit - 0.1))
        end = max(start + 0.1, min(end, nxt.end - 0.1 if nxt else limit))
        if (start, end) == (cap.start, cap.end):
            return
        retime(cap, start, end)
        # Neighbours give way rather than overlap.
        if prev and prev.end > start:
            retime(prev, prev.start, start)
        if nxt and nxt.start < end:
            retime(nxt, end, nxt.end)
        self._edit_base = [Word(w.start, w.end, w.text) for w in cap.words]
        self.start_var.set(fmt_time(cap.start))
        self.end_var.set(fmt_time(cap.end))
        self._refresh_rows()
        self._changed()

    def _time_to_playhead(self, which: str) -> None:
        if self.selected < 0 or self.player is None:
            return
        cap = self.captions[self.selected]
        now = self.player.position()
        if which == "start":
            self._retime(self.selected, now, max(cap.end, now + 0.1))
        else:
            self._retime(self.selected, min(cap.start, now - 0.1), now)

    def _split(self) -> None:
        if self.selected < 0 or self.player is None:
            return
        cap = self.captions[self.selected]
        if len(cap.words) < 2:
            self._status(t("split_needs_words"))
            return
        now = self.player.position()
        k = sum(w.start < now for w in cap.words) if cap.start < now < cap.end else len(cap.words) // 2
        k = min(max(k, 1), len(cap.words) - 1)
        mid = cap.words[k].start
        left = Caption(cap.start, mid, cap.words[:k])
        right = Caption(mid, cap.end, cap.words[k:])
        self.captions[self.selected:self.selected + 1] = [left, right]
        index = self.selected
        self._set_captions(self.captions, keep_selection=True)
        self._select(index, seek=False)

    def _merge(self) -> None:
        i = self.selected
        if i < 0 or i + 1 >= len(self.captions):
            return
        a, b = self.captions[i], self.captions[i + 1]
        self.captions[i:i + 2] = [Caption(a.start, b.end, a.words + b.words)]
        self._set_captions(self.captions, keep_selection=True)
        self._select(i, seek=False)

    def _delete(self) -> None:
        i = self.selected
        if i < 0:
            return
        del self.captions[i]
        self._set_captions(self.captions)
        if self.captions:
            self._select(min(i, len(self.captions) - 1), seek=False)

    def _add(self) -> None:
        if self.player is None:
            return
        now = self.player.position()
        i = sum(c.start <= now for c in self.captions)
        if i > 0 and self.captions[i - 1].end > now:  # inside a caption: add after it instead
            now = self.captions[i - 1].end
        limit = self.captions[i].start if i < len(self.captions) else (self.info.duration or now + 1.5)
        end = min(now + 1.5, limit)
        if end - now < 0.2:
            self._status(t("no_room"))
            return
        text = t("new_caption")
        self.captions.insert(i, Caption(now, end, [Word(now, end, " " + text)]))
        self._set_captions(self.captions)
        self._select(i)
        self.edit_text.focus_set()
        self.edit_text.tag_add("sel", "1.0", "end-1c")

    def _regroup(self) -> None:
        if not self.captions:
            return
        selected_time = self.captions[self.selected].start if self.selected >= 0 else None
        self._set_captions(group_words(all_words(self.captions), self.look.grouping))
        if selected_time is not None:
            self._select(caption_at(self.captions, selected_time + 0.001), seek=False)

    def _step_caption(self, step: int) -> None:
        if not self.captions or self.player is None:
            return
        base = self.selected if self.selected >= 0 else max(self.current, 0) - (1 if step > 0 else 0)
        self._select(min(max(base + step, 0), len(self.captions) - 1))

    # ---------- session (so closing the window doesn't lose edits) ----------

    def _save_session(self) -> None:
        self._session_job = None
        if self.info is None:
            return
        try:
            data = {"path": self.info.path, "size": os.path.getsize(self.info.path),
                    "language": self.caption_lang,
                    "captions": [{"start": c.start, "end": c.end,
                                  "words": [[w.start, w.end, w.text] for w in c.words]} for c in self.captions]}
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(SESSION_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except OSError:
            pass

    def _restore_session(self) -> list[Caption] | None:
        try:
            with open(SESSION_PATH, encoding="utf-8") as f:
                data = json.load(f)
            if data["path"] != self.info.path or data["size"] != os.path.getsize(self.info.path):
                return None
            captions = [Caption(c["start"], c["end"], [Word(*w) for w in c["words"]]) for c in data["captions"]]
            self.caption_lang = data.get("language", "") if captions else ""
            return captions or None
        except (OSError, ValueError, KeyError, TypeError):
            return None

    # ---------- transcribe & render ----------

    def _task_running(self) -> bool:
        return self.task is not None and self.task.is_alive()

    def engine_busy(self) -> bool:
        return self._task_running() and self.task_kind in ("transcribe", "translate")

    def _transcribe(self) -> None:
        if self.info is None or self._task_running():
            return
        if self.app.transcriber is None:
            self._status(t("engine_not_ready"))
            return
        if self.app.worker is not None and self.app.worker.is_alive():
            self._status(t("queue_running"))
            return
        if self.captions and not messagebox.askyesno(t("retranscribe"), t("retranscribe_confirm"), parent=self):
            return
        if self.player is not None:
            self.player.pause()
        lang = self.lang_labels[self.lang_var.get()]
        config = dict(model=self.app.settings["model"], language=None if lang == "auto" else lang,
                      path=self.info.path)
        self._begin("transcribe")
        self.task = threading.Thread(target=self._run_transcribe, args=(config,), daemon=True)
        self.task.start()

    def _run_transcribe(self, cfg: dict) -> None:
        from .engine import Cancelled
        post = self.messages.put
        engine = self.app.transcriber
        try:
            engine.load(cfg["model"], lambda s: post(("status", s)))
            post(("status", t("transcribing", name=os.path.basename(cfg["path"]))))
            result = engine.transcribe(cfg["path"], PRESETS["Social"], cfg["language"], False,
                                       lambda pct, text: post(("progress", pct)), self.cancel)
            post(("words", (result.words, result.language)))
        except Cancelled:
            post(("cancelled", None))
        except Exception as e:  # noqa: BLE001
            post(("failed", t("transcribe_failed", error=str(e) or e.__class__.__name__)))

    def _translate(self, choice: str) -> None:
        self.translate_menu.set(t("translate_menu"))
        target = self.target_labels[choice]
        if not self.captions or self._task_running():
            return
        if self.app.transcriber is None:
            self._status(t("engine_not_ready"))
            return
        if self.app.worker is not None and self.app.worker.is_alive():
            self._status(t("queue_running"))
            return
        # Captions from before languages were remembered: go by the language menu.
        source = self.caption_lang or self.lang_labels[self.lang_var.get()]
        if source == target:
            self._status(t("already_in_language", language=choice))
            return
        from .translate import can_translate
        if source == "auto" or not can_translate(source, target):
            self._status(t("cant_translate", language=SPOKEN_LANGUAGES.get(source, source)), ERR)
            return
        if not messagebox.askyesno(t("translate_menu"), t("translate_confirm", language=choice),
                                   parent=self):
            return
        if self.player is not None:
            self.player.pause()
        self._begin("translate")
        texts = [c.text for c in self.captions]
        self.task = threading.Thread(target=self._run_translate, args=(texts, source, target), daemon=True)
        self.task.start()

    def _run_translate(self, texts: list[str], source: str, target: str) -> None:
        from .engine import Cancelled
        post = self.messages.put
        try:
            translator = self.app.load_translator(lambda s: post(("status", s)))
            post(("status", t("translating_pct", pct=0)))
            out = translator.translate(texts, source, target, lambda pct: post(("translate_progress", pct)),
                                       self.cancel)
            post(("translated", (out, target)))
        except Cancelled:
            post(("cancelled", None))
        except Exception as e:  # noqa: BLE001
            post(("failed", t("translate_failed", error=str(e) or e.__class__.__name__)))

    def _apply_translation(self, texts: list[str], language: str) -> None:
        from .translate import joiner
        space = joiner(language)
        words: list[Word] = []
        for cap, text in zip(self.captions, texts):
            tokens = text.split() if space else [ch for ch in text if not ch.isspace()]
            start = cap.words[0].start if cap.words else cap.start
            end = cap.words[-1].end if cap.words else cap.end
            words += spread(tokens, start, end, space)
        self.caption_lang = language
        # Translations run longer or shorter than the original, so share them out afresh.
        self._set_captions(group_words(words, self.look.grouping))

    def _render(self) -> None:
        if self.info is None or not self.captions or self._task_running():
            return
        folder, name = os.path.split(self.info.path)
        stem = os.path.splitext(name)[0]
        default = os.path.basename(unique_path(os.path.join(folder, f"{stem} ({t('captioned')}).mp4")))
        path = filedialog.asksaveasfilename(parent=self, title=t("render_video"), initialdir=folder,
                                            initialfile=default, defaultextension=".mp4",
                                            filetypes=[(t("filetype_mp4"), "*.mp4")])
        if not path:
            return
        path = os.path.normpath(path)
        if os.path.normcase(path) == os.path.normcase(self.info.path):
            self._status(t("would_overwrite"), ERR)
            return
        if self.player is not None:
            self.player.pause()
        use_gpu = bool(self.app.has_gpu and self.app.settings.get("use_gpu", True))
        self._begin("render")
        self.task = threading.Thread(target=self._run_render, args=(path, use_gpu), daemon=True)
        self.task.start()

    def _run_render(self, path: str, use_gpu: bool) -> None:
        from .video import Cancelled, render
        post = self.messages.put
        try:
            post(("status", t("rendering", pct=0)))
            render(self.info, path, self.captions, self.look, lambda pct: post(("render_progress", pct)),
                   self.cancel, use_gpu)
            post(("rendered", path))
        except Cancelled:
            post(("cancelled", None))
        except Exception as e:  # noqa: BLE001
            post(("failed", t("render_failed", error=str(e) or e.__class__.__name__)))

    def _begin(self, kind: str) -> None:
        self.task_kind = kind
        self.cancel.clear()
        for w in (self.transcribe_btn, self.render_btn, self.translate_menu):
            w.configure(state="disabled")
        self.task_bar.set(0)
        self.task_bar.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.cancel_btn.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))

    def _end(self) -> None:
        self.task_bar.grid_remove()
        self.cancel_btn.grid_remove()
        self.transcribe_btn.configure(state="normal" if self.info else "disabled")
        self.render_btn.configure(state="normal" if self.captions else "disabled")
        self.translate_menu.configure(state="normal" if self.captions else "disabled")

    def _handle(self, kind: str, data) -> None:
        if kind == "status":
            self._status(data)
        elif kind == "progress":
            self.task_bar.set(data)
            self._status(t("transcribing_pct", pct=int(data * 100)))
        elif kind == "words":
            self._end()
            words, self.caption_lang = data
            captions = group_words(words, self.look.grouping)
            self._set_captions(captions)
            self._status(t("transcribed", n=len(captions)) if captions else t("no_speech"),
                         OK if captions else ERR)
        elif kind == "translate_progress":
            self.task_bar.set(data)
            self._status(t("translating_pct", pct=int(data * 100)))
        elif kind == "translated":
            self._end()
            texts, language = data
            self._apply_translation(texts, language)
            self._status(t("translated", language=SPOKEN_LANGUAGES.get(language, language), n=len(self.captions)),
                         OK)
        elif kind == "render_progress":
            self.task_bar.set(data)
            self._status(t("rendering", pct=int(data * 100)))
        elif kind == "rendered":
            self._end()
            self.output = data
            self.show_btn.configure(state="normal")
            self._status(t("rendered", name=os.path.basename(data)), OK)
        elif kind == "cancelled":
            self._end()
            self._status(t("cancelled"))
        elif kind == "failed":
            self._end()
            self._status(data, ERR)

    def _status(self, text: str, color=None) -> None:
        self.status.configure(text=text, text_color=color or MUTED)

    def _save_srt(self) -> None:
        if self.info is None or not self.captions:
            return
        folder, name = os.path.split(self.info.path)
        default = os.path.basename(unique_path(os.path.join(folder, os.path.splitext(name)[0] + ".srt")))
        path = filedialog.asksaveasfilename(parent=self, initialdir=folder, initialfile=default,
                                            defaultextension=".srt", filetypes=[("SRT", "*.srt")])
        if path:
            write_srt(path, [Cue(c.start, c.end, wrap(c.text, 42, 2)) for c in self.captions])
            self._status(t("saved_srt", name=os.path.basename(path)), OK)

    # ---------- playback ----------

    def _toggle_play(self) -> None:
        if self.player is not None:
            self.player.toggle()

    def _nudge(self, seconds: float) -> None:
        if self.player is not None:
            self.player.seek(self.player.position() + seconds)

    def _shortcut(self, event, action) -> None:
        # Only while this tab is showing, and leave keys alone while typing.
        if not self.winfo_ismapped() or isinstance(event.widget, (tk.Entry, tk.Text)):
            return
        action()

    def _on_scrub(self, value: float) -> None:
        if self.player is not None:
            self.player.seek(float(value))

    def _layout_canvas(self) -> None:
        if self.info is None:
            self._draw_empty()
            return
        cw, ch = max(10, self.canvas.winfo_width()), max(10, self.canvas.winfo_height())
        scale = min(cw / self.info.width, ch / self.info.height)
        w, h = max(2, int(self.info.width * scale) // 2 * 2), max(2, int(self.info.height * scale) // 2 * 2)
        self._display = ((cw - w) // 2, (ch - h) // 2, w, h)
        if self.player is not None:
            self.player.size = (w, h)
        self._drawn = None

    def _draw_empty(self) -> None:
        self.canvas.delete("all")
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        self.canvas.create_text(cw / 2, ch / 2 - 16, text="🎬", font=("Segoe UI Emoji", 30), fill=ACCENT_TEXT[1])
        self.canvas.create_text(cw / 2, ch / 2 + 26, text=t("studio_drop"), fill="#9A9AA5",
                                font=(TITLE_FONT, 15), width=max(100, cw - 40), justify="center")

    def _tick(self) -> None:
        try:
            while True:
                self._handle(*self.messages.get_nowait())
        except queue.Empty:
            pass
        if self.player is not None:
            self._update_picture()
        self.after(15, self._tick)

    def _update_picture(self) -> None:
        player = self.player
        now = player.position()
        frame = player.frame
        x0, y0, w, h = self._display
        overlay = self.renderer.overlay(self.look, now, w, h) if frame else None
        key = (frame[0] if frame else None, id(overlay), self._display, self.safe_var.get())
        if key != self._drawn and frame is not None:
            self._drawn = key
            img = frame[1]
            if img.size != (w, h):
                img = img.resize((w, h))
            else:
                img = img.copy()
            if overlay is not None:
                img.paste(overlay.image, (overlay.x, overlay.y), overlay.image)
                self._caption_box = (x0 + overlay.x, y0 + overlay.y,
                                     x0 + overlay.x + overlay.image.width, y0 + overlay.y + overlay.image.height)
            else:
                self._caption_box = None
            self._photo = ImageTk.PhotoImage(img)
            self.canvas.delete("all")
            self.canvas.create_image(x0, y0, image=self._photo, anchor="nw")
            if self.safe_var.get():
                left, top, right, bottom = SAFE_ZONE
                self.canvas.create_rectangle(x0 + w * left, y0 + h * top, x0 + w * (1 - right), y0 + h * (1 - bottom),
                                             outline="#FFFFFF", dash=(4, 4), width=1)

        # Only touch widgets whose value changed; redrawing CTk widgets every tick is slow.
        shown = {"play": "❚❚" if player.playing else "▶", "time": f"{fmt_time(now)} / {fmt_time(self.info.duration)}"}
        if shown["play"] != self._shown.get("play"):
            self.play_btn.configure(text=shown["play"])
        if shown["time"] != self._shown.get("time"):
            self.time_label.configure(text=shown["time"])
            if not self._scrubbing:
                self.scrub.set(now)
        self._shown = shown

        current = caption_at(self.captions, now) if self.captions else -1
        if current != self.current:
            self.current = current
            self._mark_rows()
            if player.playing and current >= 0:
                self._scroll_to(current)
                if self.selected >= 0 and self.focus_get() is not self.edit_text._textbox:
                    self._select(current, seek=False)

    def _scroll_to(self, index: int) -> None:
        canvas = self.list._parent_canvas
        row = self.rows[index]
        total = max(1, self.list.winfo_height())
        top, bottom = canvas.yview()
        y = row.winfo_y() / total
        if not top <= y <= bottom - row.winfo_height() / total:
            canvas.yview_moveto(max(0.0, y - (bottom - top) / 3))

    # ---------- dragging the captions ----------

    def _over_caption(self, x: int, y: int) -> bool:
        box = self._caption_box
        return box is not None and box[0] <= x <= box[2] and box[1] <= y <= box[3]

    def _canvas_hover(self, event) -> None:
        self.canvas.configure(cursor="sb_v_double_arrow" if self._over_caption(event.x, event.y) else "")

    def _canvas_press(self, event) -> None:
        self.canvas.focus_set()
        if self.info is None:
            self._browse()
            return
        self._drag = {"y": event.y, "moved": False, "caption": self._over_caption(event.x, event.y),
                      "position": self.look.position}

    def _canvas_drag(self, event) -> None:
        drag = self._drag
        if not drag or not drag["caption"]:
            return
        dy = event.y - drag["y"]
        if abs(dy) > 3:
            drag["moved"] = True
        if drag["moved"]:
            h = self._display[3]
            position = min(0.92, max(0.08, drag["position"] + dy / h))
            self.look.position = position
            var, value, show = self.controls["position"]
            var.set(position)
            value.configure(text=show(position))
            self._save_look()

    def _canvas_release(self, event) -> None:
        drag, self._drag = self._drag, None
        if drag and not drag["moved"]:
            self._toggle_play()

    # ---------- closing ----------

    def hidden(self) -> None:
        """Another tab was chosen."""
        if self.player is not None:
            self.player.pause()

    def close(self) -> bool:
        """The app is closing: stop work and keep the edits. False if the user wants to keep rendering."""
        if self._task_running() and self.task_kind == "render":
            if not messagebox.askyesno(t("render_video"), t("stop_render_confirm"), parent=self):
                return False
        self.cancel.set()
        if self._session_job is not None:
            self.after_cancel(self._session_job)
            self._save_session()
        if self.player is not None:
            self.player.close()
            self.player = None
        return True
