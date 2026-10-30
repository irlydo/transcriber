"""Styled, animated captions for burning into video. The same renderer draws the live preview and
the exported frames, so what you see while editing is what you get."""

from __future__ import annotations

import bisect
import math
import os
from collections import OrderedDict
from dataclasses import asdict, dataclass, field, fields
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .subtitles import CaptionStyle, Word, build_cues

_FONT_DIRS = [
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts"),  # per-user installs
]
# Bold Windows fonts that suit captions: name -> (file, variation)
FONTS = {
    "Arial Black": ("ariblk.ttf", None),
    "Impact": ("impact.ttf", None),
    "Segoe UI Black": ("seguibl.ttf", None),
    "Segoe UI Bold": ("segoeuib.ttf", None),
    "Segoe UI Semibold": ("seguisb.ttf", None),
    "Arial Bold": ("arialbd.ttf", None),
    "Verdana Bold": ("verdanab.ttf", None),
    "Trebuchet Bold": ("trebucbd.ttf", None),
    "Bahnschrift Bold": ("bahnschrift.ttf", "Bold"),
    "Comic Sans Bold": ("comicbd.ttf", None),
    # wide character coverage for Japanese, Korean and Chinese
    "Yu Gothic Bold": ("YuGothB.ttc", None),
    "Malgun Gothic Bold": ("malgunbd.ttf", None),
    "Microsoft YaHei Bold": ("msyhbd.ttc", None),
}

# How many words share the screen: grouping -> line-breaking rules (None = one word at a time)
GROUPINGS: dict[str, CaptionStyle | None] = {
    "word": None,
    "short": CaptionStyle(14, 1, 1.6, min_duration=0.3, gap_split=0.4),
    "line": CaptionStyle(24, 1, 2.5, min_duration=0.4, gap_split=0.5),
    "two": CaptionStyle(20, 2, 3.5, min_duration=0.5, gap_split=0.6),
}
ANIMATIONS = ("none", "pop", "bounce", "fade", "slide")
WORD_EFFECTS = ("none", "color", "box", "pop", "reveal")
HOLD_GAP = 0.5  # a caption stays up through a pause this short, so the screen doesn't flicker


def _font_path(file: str) -> str | None:
    for folder in _FONT_DIRS:
        path = os.path.join(folder, file)
        if os.path.isfile(path):
            return path
    return None


def available_fonts() -> list[str]:
    return [name for name, (file, _) in FONTS.items() if _font_path(file)]


@lru_cache(maxsize=96)
def _load_font(font: str, size: int) -> ImageFont.FreeTypeFont:
    """font is a name from FONTS or a path to a font file."""
    file, variation = FONTS.get(font, (font, None))
    path = _font_path(file) if font in FONTS else font if os.path.isfile(font) else None
    if path is None:
        path = next((p for f, _ in FONTS.values() if (p := _font_path(f))), None)
    if path is None:
        return ImageFont.load_default(size)
    f = ImageFont.truetype(path, size)
    if variation:
        try:
            f.set_variation_by_name(variation)
        except (OSError, ValueError):
            pass
    return f


@dataclass
class Look:
    font: str = "Arial Black"
    size: float = 7.0              # font size, % of the video width
    uppercase: bool = False
    grouping: str = "short"        # see GROUPINGS
    animation: str = "pop"         # how each caption appears, see ANIMATIONS
    word_effect: str = "color"     # what the word being spoken does, see WORD_EFFECTS
    text_color: str = "#FFFFFF"
    highlight_color: str = "#FFE14D"
    outline_color: str = "#000000"
    outline: float = 0.12          # outline thickness, fraction of the font size
    shadow: float = 0.6            # drop shadow strength, 0 to 1
    background: bool = False       # rounded box behind each line
    background_color: str = "#000000"
    background_opacity: float = 0.7
    position: float = 0.68         # vertical centre, fraction of the video height

    @classmethod
    def from_dict(cls, data: dict) -> Look:
        names = {f.name for f in fields(cls)}
        look = cls(**{k: v for k, v in (data or {}).items() if k in names})
        if look.grouping not in GROUPINGS:
            look.grouping = "short"
        if look.animation not in ANIMATIONS:
            look.animation = "pop"
        if look.word_effect not in WORD_EFFECTS:
            look.word_effect = "color"
        return look

    def to_dict(self) -> dict:
        return asdict(self)


# Starting points; every setting can still be changed afterwards. name -> i18n key, look
LOOKS: dict[str, tuple[str, Look]] = {
    "pop": ("look_pop", Look()),
    "highlight": ("look_highlight", Look(font="Segoe UI Black", size=7.5, uppercase=True, word_effect="box",
                                         highlight_color="#7C5CFF", outline=0.10, shadow=0.5)),
    "clean": ("look_clean", Look(font="Segoe UI Bold", size=5.6, grouping="two", animation="fade",
                                 word_effect="none", outline=0.0, shadow=0.0, background=True,
                                 background_opacity=0.75)),
    "karaoke": ("look_karaoke", Look(size=6.4, grouping="line", animation="slide", word_effect="pop",
                                     highlight_color="#3DDCFF")),
    "bold": ("look_bold", Look(font="Impact", size=11.0, uppercase=True, grouping="word", animation="bounce",
                               word_effect="none", text_color="#FFE14D", outline=0.09, shadow=0.7)),
    "typewriter": ("look_typewriter", Look(font="Segoe UI Black", size=6.6, grouping="two", animation="none",
                                           word_effect="reveal", outline=0.11)),
}


@dataclass
class Caption:
    start: float
    end: float
    words: list[Word] = field(default_factory=list)  # Whisper style: text keeps its leading space

    @property
    def text(self) -> str:
        return "".join(w.text for w in self.words).strip()


def group_words(words: list[Word], grouping: str) -> list[Caption]:
    words = [w for w in words if w.text.strip()]
    style = GROUPINGS.get(grouping)
    if style is None:
        captions = [Caption(w.start, max(w.end, w.start + 0.25), [w]) for w in words]
    else:
        captions = [Caption(c.start, c.end, list(c.words)) for c in build_cues(words, style)]
    hold(captions)
    return captions


def hold(captions: list[Caption]) -> None:
    """Keep each caption up until the next one if the pause between them is short."""
    for cur, nxt in zip(captions, captions[1:]):
        if nxt.start - cur.end < HOLD_GAP:
            cur.end = nxt.start


def all_words(captions: list[Caption]) -> list[Word]:
    return [w for c in captions for w in c.words]


def retext(caption: Caption, text: str, original: list[Word]) -> None:
    """Replace a caption's words with the edited text. Where the word count is unchanged each word keeps
    its timing; otherwise the new words share the caption's time in proportion to their length."""
    tokens = text.split()
    if len(tokens) == len(original):
        caption.words = [Word(w.start, w.end, (" " if w.text.startswith(" ") else "") + tok)
                         for w, tok in zip(original, tokens)]
        return
    start = original[0].start if original else caption.start
    end = original[-1].end if original else caption.end
    weights = [len(tok) + 1 for tok in tokens]
    total, t, words = sum(weights) or 1, start, []
    for tok, wgt in zip(tokens, weights):
        d = (end - start) * wgt / total
        words.append(Word(t, t + d, " " + tok))
        t += d
    caption.words = words


def retime(caption: Caption, start: float, end: float) -> None:
    """Move a caption to a new time span, stretching its words to fit."""
    span = caption.end - caption.start

    def move(t: float) -> float:
        p = (t - caption.start) / span if span > 0 else 0.0
        return start + min(1.0, max(0.0, p)) * (end - start)

    caption.words = [Word(move(w.start), move(w.end), w.text) for w in caption.words]
    caption.start, caption.end = start, end


def caption_at(captions: list[Caption], t: float, starts: list[float] | None = None) -> int:
    """Index of the caption on screen at time t, or -1."""
    starts = starts if starts is not None else [c.start for c in captions]
    i = bisect.bisect_right(starts, t) - 1
    return i if i >= 0 and t < captions[i].end else -1


# ---------- animation curves ----------

def _ease_out_back(p: float, k: float = 1.70158) -> float:
    p -= 1
    return 1 + (k + 1) * p ** 3 + k * p ** 2


def _ease_out_cubic(p: float) -> float:
    return 1 - (1 - p) ** 3


ENTRANCE_SECONDS = {"pop": 0.18, "bounce": 0.45, "fade": 0.15, "slide": 0.22}
WORD_POP_SECONDS = 0.16
STEPS = 30  # animation progress is quantised so rendered captions can be cached


def _entrance(animation: str, p: float) -> tuple[float, float, float]:
    """(scale, opacity, drop as a fraction of the line height) at progress p of the entrance."""
    if animation == "pop":
        return 0.7 + 0.3 * _ease_out_back(p), min(1.0, p * 3), 0.0
    if animation == "bounce":
        return 1 - 0.6 * math.exp(-6 * p) * math.cos(3 * math.pi * p), min(1.0, p * 4), 0.0
    if animation == "fade":
        return 1.0, p, 0.0
    if animation == "slide":
        e = _ease_out_cubic(p)
        return 1.0, e, 0.6 * (1 - e)
    return 1.0, 1.0, 0.0


def _hex(color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    c = color.lstrip("#")
    try:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), alpha
    except (ValueError, IndexError):
        return 255, 255, 255, alpha


# ---------- rendering ----------

@dataclass
class Overlay:
    image: Image.Image  # RGBA
    x: int
    y: int
    _yuv: tuple | None = None

    def yuv(self, matrix: tuple[float, float], full_range: bool):
        """The overlay as Y, premultiplied U and V, alpha and chroma alpha for 4:2:0 blending, aligned to
        even coordinates. Returns (x, y, Y, A, U, V, Ac) with float32 planes."""
        if self._yuv is None or self._yuv[0] != (matrix, full_range):
            self._yuv = ((matrix, full_range), _to_yuv420(self, matrix, full_range))
        return self._yuv[1]


def _to_yuv420(ov: Overlay, matrix: tuple[float, float], full_range: bool):
    rgba = np.asarray(ov.image, dtype=np.float32) / 255.0
    x, y = ov.x, ov.y
    # Align to the chroma grid (even x, y, width and height).
    left, top = x & 1, y & 1
    h, w = rgba.shape[:2]
    ph, pw = (top + h + 1) // 2 * 2, (left + w + 1) // 2 * 2
    padded = np.zeros((ph, pw, 4), np.float32)
    padded[top:top + h, left:left + w] = rgba
    x, y = x - left, y - top

    kr, kb = matrix
    r, g, b, a = padded[..., 0], padded[..., 1], padded[..., 2], padded[..., 3]
    luma = kr * r + (1 - kr - kb) * g + kb * b
    cb = (b - luma) / (2 * (1 - kb))
    cr = (r - luma) / (2 * (1 - kr))
    if full_range:
        Y, U, V = luma * 255, 128 + cb * 255, 128 + cr * 255
    else:
        Y, U, V = 16 + luma * 219, 128 + cb * 224, 128 + cr * 224

    def down(p):
        return p.reshape(ph // 2, 2, pw // 2, 2).mean(axis=(1, 3))

    ac = down(a)
    return x, y, Y * a, a, down(U * a), down(V * a), ac


def blend_yuv420(y_plane: np.ndarray, u_plane: np.ndarray, v_plane: np.ndarray, ov: Overlay,
                 matrix: tuple[float, float], full_range: bool) -> None:
    """Alpha-blend the overlay into a frame's planes in place, leaving every other pixel untouched."""
    x, y, Yp, A, Up, Vp, Ac = ov.yuv(matrix, full_range)
    H, W = y_plane.shape
    h, w = A.shape
    # Crop to the frame, keeping even offsets so luma and chroma stay aligned.
    x0, y0 = max(0, -x), max(0, -y)
    x1, y1 = min(w, W - x), min(h, H - y)
    x0, y0 = x0 + (x0 & 1), y0 + (y0 & 1)
    x1, y1 = x1 - (x1 & 1), y1 - (y1 & 1)
    if x1 <= x0 or y1 <= y0:
        return
    fy = slice(y + y0, y + y1)
    fx = slice(x + x0, x + x1)
    a = A[y0:y1, x0:x1]
    region = y_plane[fy, fx].astype(np.float32)
    y_plane[fy, fx] = np.clip(region * (1 - a) + Yp[y0:y1, x0:x1] + 0.5, 0, 255).astype(np.uint8)
    cy = slice((y + y0) // 2, (y + y1) // 2)
    cx = slice((x + x0) // 2, (x + x1) // 2)
    ac = Ac[y0 // 2:y1 // 2, x0 // 2:x1 // 2]
    for plane, premult in ((u_plane, Up), (v_plane, Vp)):
        region = plane[cy, cx].astype(np.float32)
        plane[cy, cx] = np.clip(region * (1 - ac) + premult[y0 // 2:y1 // 2, x0 // 2:x1 // 2] + 0.5,
                                0, 255).astype(np.uint8)


class Renderer:
    """Draws the caption on screen at a given time. Rendered states are cached, so a caption is only
    redrawn while it animates or when the spoken word changes."""

    def __init__(self, captions: list[Caption] | None = None) -> None:
        self._cache: OrderedDict = OrderedDict()
        self.set_captions(captions or [])

    def set_captions(self, captions: list[Caption]) -> None:
        """Call after captions are added, removed or retimed."""
        self.captions = captions
        self._starts = [c.start for c in captions]

    def overlay(self, look: Look, t: float, width: int, height: int) -> Overlay | None:
        i = caption_at(self.captions, t, self._starts)
        if i < 0 or not self.captions[i].words:
            return None
        cap = self.captions[i]
        words = cap.words

        # Which word is being spoken (the last one that has started) and how far each animation has got.
        active = max(0, bisect.bisect_right([w.start for w in words], t) - 1)
        if look.word_effect == "none":
            active = -1
        dur = ENTRANCE_SECONDS.get(look.animation)
        entrance = min(STEPS, int((t - cap.start) / dur * STEPS)) if dur else STEPS
        pop = STEPS
        if look.word_effect == "pop" and active >= 0:
            pop = min(STEPS, max(0, int((t - words[active].start) / WORD_POP_SECONDS * STEPS)))

        key = (id(cap), cap.text, len(words), tuple(asdict(look).values()), width, height, active, entrance, pop)
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
            return hit
        result = self._draw(cap, look, width, height, active, entrance / STEPS, pop / STEPS)
        self._cache[key] = result
        if len(self._cache) > 160:
            self._cache.popitem(last=False)
        return result

    def _draw(self, cap: Caption, look: Look, W: int, H: int, active: int, entrance: float,
              pop: float) -> Overlay | None:
        tokens = [w.text.strip() for w in cap.words]
        if look.uppercase:
            tokens = [tok.upper() for tok in tokens]
        spaced = [i > 0 and w.text.startswith(" ") for i, w in enumerate(cap.words)]
        base_px = max(6, round(look.size / 100 * W))
        lines = _break_lines(tokens, spaced, _load_font(look.font, base_px), W * 0.86)

        scale, opacity, drop = _entrance(look.animation, entrance)
        px = max(4, round(base_px * scale))
        font = _load_font(look.font, px)
        ascent, descent = font.getmetrics()
        line_h = round((ascent + descent) * 1.04)
        stroke = round(look.outline * px)
        space = font.getlength(" ") + (px * 0.14 if look.word_effect == "pop" else 0)  # room to grow
        box_px, box_py = round(px * 0.28), round(px * 0.08)
        shadow_off = round(px * 0.05) if look.shadow > 0 else 0
        blur = max(1, round(px * 0.05)) if look.shadow > 0 else 0
        word_pop = 1 + 0.12 * _ease_out_back(pop, 3.0) if look.word_effect == "pop" else 1.0

        widths = [font.getlength(tok) for tok in tokens]
        line_ws = [sum(widths[i] for i in ln) + space * sum(spaced[i] for i in ln[1:]) for ln in lines]
        # Room around the text for the outline, shadow, boxes and a popped word.
        pad = stroke + shadow_off + blur * 3 + box_px + round(px * (word_pop - 1) * 2) + 4
        cw = int(max(line_ws) + 2 * pad)
        ch = int(line_h * len(lines) + 2 * pad)

        # Lay words out: (index, x, baseline)
        slots = []
        for li, ln in enumerate(lines):
            x = pad + (max(line_ws) - line_ws[li]) / 2
            baseline = pad + li * line_h + ascent
            for n, i in enumerate(ln):
                if n and spaced[i]:
                    x += space
                slots.append((i, x, baseline))
                x += widths[i]
        visible = {i for i, _, _ in slots if look.word_effect != "reveal" or i <= max(active, 0)}

        img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        if look.background:
            fill = _hex(look.background_color, round(255 * look.background_opacity))
            for li, ln in enumerate(lines):
                shown = [s for s in slots if s[0] in ln and s[0] in visible]
                if not shown:
                    continue
                x0 = shown[0][1]
                x1 = shown[-1][1] + widths[shown[-1][0]]
                top = pad + li * line_h
                draw.rounded_rectangle((x0 - box_px, top - box_py, x1 + box_px, top + line_h + box_py),
                                       radius=round(px * 0.25), fill=fill)

        def word_font(i):
            return _load_font(look.font, max(4, round(px * word_pop))) if i == active else font

        def place(i, x, baseline):
            """Position and anchor for a word; a popped word grows around its centre."""
            if i == active and word_pop != 1.0:
                return (x + widths[i] / 2, baseline - (ascent - descent) / 2), "mm"
            return (x, baseline), "ls"

        if look.shadow > 0:
            shadow = Image.new("L", (cw, ch), 0)
            sdraw = ImageDraw.Draw(shadow)
            for i, x, baseline in slots:
                if i in visible:
                    xy, anchor = place(i, x + shadow_off, baseline + shadow_off)
                    sdraw.text(xy, tokens[i], font=word_font(i), fill=255, anchor=anchor,
                               stroke_width=stroke, stroke_fill=255)
            shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
            shade = Image.new("RGBA", (cw, ch), (0, 0, 0, 255))
            shade.putalpha(shadow.point(lambda v: int(v * min(1.0, look.shadow) * 0.85)))
            img.alpha_composite(shade)
            draw = ImageDraw.Draw(img)

        if look.word_effect == "box" and active in visible:
            i, x, baseline = next(s for s in slots if s[0] == active)
            top = baseline - ascent
            draw.rounded_rectangle((x - box_px * 0.6, top - box_py, x + widths[i] + box_px * 0.6,
                                    top + ascent + descent + box_py),
                                   radius=round(px * 0.2), fill=_hex(look.highlight_color))

        text_fill = _hex(look.text_color)
        hi_fill = _hex(look.highlight_color)
        outline = _hex(look.outline_color)
        for i, x, baseline in slots:
            if i not in visible:
                continue
            fill = hi_fill if i == active and look.word_effect in ("color", "pop") else text_fill
            xy, anchor = place(i, x, baseline)
            draw.text(xy, tokens[i], font=word_font(i), fill=fill, anchor=anchor,
                      stroke_width=stroke, stroke_fill=outline)

        if opacity < 1:
            alpha = img.getchannel("A").point(lambda v: int(v * opacity))
            img.putalpha(alpha)

        cx = W / 2
        cy = look.position * H + drop * line_h
        return Overlay(img, round(cx - cw / 2), round(cy - ch / 2))


def _break_lines(tokens: list[str], spaced: list[bool], font, max_width: float) -> list[list[int]]:
    """Wrap words to fit the width, evening out the lines when the caption needs two."""
    widths = [font.getlength(tok) for tok in tokens]
    space = font.getlength(" ")

    def width(idx: list[int]) -> float:
        return sum(widths[i] for i in idx) + space * sum(spaced[i] for i in idx[1:])

    every = list(range(len(tokens)))
    if width(every) <= max_width or len(tokens) == 1:
        return [every]
    lines, cur = [], []
    for i in every:
        if cur and width(cur + [i]) > max_width:
            lines.append(cur)
            cur = []
        cur.append(i)
    lines.append(cur)
    if len(lines) == 2:
        best = min(range(1, len(tokens)), key=lambda k: max(width(every[:k]), width(every[k:])))
        lines = [every[:best], every[best:]]
    return lines
