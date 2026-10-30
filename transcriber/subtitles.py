"""Turn word-level timestamps into well-formed SRT captions."""

from __future__ import annotations

from dataclasses import dataclass, field

SENTENCE_END = (".", "?", "!", "…", "。", "？", "！")
CLAUSE_END = (",", ";", ":", "、", "，", "；", "：")
# Words a caption line shouldn't end on (English); they read better carried to the next caption.
WEAK_WORDS = {
    "a", "an", "the", "and", "or", "but", "so", "to", "of", "in", "on", "at", "for", "with",
    "from", "by", "as", "if", "that", "than", "is", "are", "was", "were", "be", "my", "your",
    "our", "their", "his", "her", "its", "this", "these", "those", "into", "about", "without",
    "i", "we", "you", "they", "he", "she", "it's", "i'm", "we're", "you're", "they're",
}


@dataclass
class Word:
    start: float
    end: float
    text: str  # Whisper keeps the leading space, e.g. " hello"


@dataclass
class Cue:
    start: float
    end: float
    lines: list[str] = field(default_factory=list)
    words: list[Word] = field(default_factory=list)


@dataclass
class CaptionStyle:
    max_chars: int = 42        # characters per line
    max_lines: int = 2         # lines per caption
    max_duration: float = 6.0  # seconds a caption may stay on screen
    min_duration: float = 0.8  # short captions are extended up to this
    gap_split: float = 0.8     # a pause this long always starts a new caption


PRESETS: dict[str, CaptionStyle] = {
    "Standard": CaptionStyle(42, 2, 6.0),
    "One line": CaptionStyle(42, 1, 4.0),
    "Social": CaptionStyle(20, 1, 2.0, min_duration=0.4, gap_split=0.4),
}


def _join(words: list[Word]) -> str:
    return "".join(w.text for w in words).strip()


def _is_weak(word: Word) -> bool:
    return word.text.strip().lower() in WEAK_WORDS


def wrap(text: str, max_chars: int, max_lines: int) -> list[str]:
    """Split text into lines no longer than max_chars, balancing two-line captions."""
    if len(text) <= max_chars:
        return [text]

    if max_lines == 2 and " " in text:
        # Pick the space that makes both lines as even as possible,
        # nudging toward breaks after punctuation.
        best, best_score = None, None
        for i, ch in enumerate(text):
            if ch != " ":
                continue
            left, right = text[:i], text[i + 1:]
            if len(left) > max_chars or len(right) > max_chars:
                continue
            score = abs(len(left) - len(right))
            if left.endswith(SENTENCE_END + CLAUSE_END):
                score -= 8
            elif left.rsplit(" ", 1)[-1].lower() in WEAK_WORDS:
                score += 6
            if best_score is None or score < best_score:
                best, best_score = (left, right), score
        if best:
            return list(best)

    # Greedy fallback (also handles text without spaces, e.g. CJK).
    lines: list[str] = []
    current = ""
    for token in text.split(" ") if " " in text else list(text):
        sep = " " if current and " " in text else ""
        if len(current) + len(sep) + len(token) <= max_chars or not current:
            current += sep + token
        else:
            lines.append(current)
            current = token
    if current:
        lines.append(current)
    return lines


def build_cues(words: list[Word], style: CaptionStyle) -> list[Cue]:
    groups: list[list[Word]] = []
    current: list[Word] = []

    def flush(carry: bool = False) -> None:
        # With carry=True, trailing weak words move on to the next caption.
        kept = len(current)
        if carry:
            while kept > 1 and len(current) - kept < 3 and _is_weak(current[kept - 1]):
                kept -= 1
        if kept:
            groups.append(current[:kept])
        del current[:kept]

    capacity = style.max_chars * style.max_lines
    for word in words:
        if not word.text.strip():
            continue
        if current:
            candidate = _join(current + [word])
            too_long = len(wrap(candidate, style.max_chars, style.max_lines)) > style.max_lines
            too_slow = word.end - current[0].start > style.max_duration
            paused = word.start - current[-1].end > style.gap_split
            if paused:
                flush()
            elif too_long or too_slow:
                flush(carry=True)
        current.append(word)

        text = word.text.rstrip()
        if text.endswith(SENTENCE_END):
            flush()
        elif text.endswith(CLAUSE_END) and len(_join(current)) > capacity * 0.6:
            flush()
    flush()

    _rebalance_orphans(groups, style)
    cues = [Cue(g[0].start, g[-1].end, wrap(_join(g), style.max_chars, style.max_lines), g) for g in groups]
    _fix_timing(cues, style)
    return cues


def _fits(group: list[Word], style: CaptionStyle) -> bool:
    return (len(wrap(_join(group), style.max_chars, style.max_lines)) <= style.max_lines
            and group[-1].end - group[0].start <= style.max_duration)


def _rebalance_orphans(groups: list[list[Word]], style: CaptionStyle) -> None:
    """A caption of one or two words that finishes the previous caption's sentence looks
    stranded, so re-split the pair at the most even point that still fits."""
    for i in range(1, len(groups)):
        prev, cur = groups[i - 1], groups[i]
        if len(cur) > 2 or prev[-1].text.rstrip().endswith(SENTENCE_END):
            continue
        if cur[0].start - prev[-1].end > style.gap_split:
            continue
        combined = prev + cur
        best, best_score = None, None
        for k in range(1, len(combined)):
            left, right = combined[:k], combined[k:]
            if not (_fits(left, style) and _fits(right, style)):
                continue
            score = min(len(_join(left)), len(_join(right)))
            if left[-1].text.rstrip().endswith(CLAUSE_END):
                score += 10
            elif _is_weak(left[-1]):
                score -= 10
            if best_score is None or score > best_score:
                best, best_score = k, score
        if best is not None:
            groups[i - 1], groups[i] = combined[:best], combined[best:]


def _fix_timing(cues: list[Cue], style: CaptionStyle) -> None:
    for i, cue in enumerate(cues):
        next_start = cues[i + 1].start if i + 1 < len(cues) else None
        if cue.end - cue.start < style.min_duration:
            cue.end = cue.start + style.min_duration
        if next_start is not None and cue.end > next_start - 0.04:
            cue.end = max(cue.start + 0.1, next_start - 0.04)


def _timestamp(seconds: float) -> str:
    ms = max(0, int(round(seconds * 1000)))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def to_srt(cues: list[Cue]) -> str:
    blocks = []
    for i, cue in enumerate(cues, 1):
        blocks.append(f"{i}\n{_timestamp(cue.start)} --> {_timestamp(cue.end)}\n" + "\n".join(cue.lines))
    return "\n\n".join(blocks) + "\n"


def write_srt(path: str, cues: list[Cue]) -> None:
    text = to_srt(cues)
    # Some video editors only detect UTF-8 (accented / non-Latin characters) with a BOM.
    encoding = "utf-8" if text.isascii() else "utf-8-sig"
    with open(path, "w", encoding=encoding, newline="\r\n") as f:
        f.write(text)
