"""Translate captions on the PC with Meta's NLLB-200 model (CTranslate2), keeping their timing.

Captions are usually fragments of sentences, and translating fragments on their own reads badly, so
whole sentences are translated and the result is shared back out over the captions they came from.
"""

from __future__ import annotations

import glob
import os
import threading
from typing import Callable

from .engine import Cancelled, ctranslate2  # importing engine registers the GPU DLLs first
from .i18n import t
from .subtitles import SENTENCE_END

REPO = "JustFrederik/nllb-200-distilled-600M-ct2-int8"
REVISION = "302d78f00e6fdb50a1064059df7c392b735e9d05"
FILES = ["config.json", "model.bin", "shared_vocabulary.txt", "tokenizer.json"]
DOWNLOAD_SIZE = 640_000_000  # bytes, for display

# Whisper language code -> NLLB language code. The first block matches SPOKEN_LANGUAGES (translation
# targets); the rest are other languages Whisper may detect, so they can be translated from.
NLLB_CODES = {
    "en": "eng_Latn", "es": "spa_Latn", "fr": "fra_Latn", "de": "deu_Latn", "it": "ita_Latn",
    "pt": "por_Latn", "nl": "nld_Latn", "pl": "pol_Latn", "ru": "rus_Cyrl", "uk": "ukr_Cyrl",
    "tr": "tur_Latn", "ar": "arb_Arab", "hi": "hin_Deva", "ur": "urd_Arab", "bn": "ben_Beng",
    "ja": "jpn_Jpan", "ko": "kor_Hang", "zh": "zho_Hans", "vi": "vie_Latn", "th": "tha_Thai",
    "id": "ind_Latn", "ms": "zsm_Latn", "tl": "tgl_Latn", "sv": "swe_Latn", "no": "nob_Latn",
    "da": "dan_Latn", "fi": "fin_Latn", "el": "ell_Grek", "cs": "ces_Latn", "ro": "ron_Latn",
    "hu": "hun_Latn", "he": "heb_Hebr", "fa": "pes_Arab", "sw": "swh_Latn", "cy": "cym_Latn",

    "ca": "cat_Latn", "hr": "hrv_Latn", "sk": "slk_Latn", "sr": "srp_Cyrl", "bg": "bul_Cyrl",
    "lt": "lit_Latn", "lv": "lvs_Latn", "et": "est_Latn", "sl": "slv_Latn", "bs": "bos_Latn",
    "mk": "mkd_Cyrl", "be": "bel_Cyrl", "sq": "als_Latn", "gl": "glg_Latn", "eu": "eus_Latn",
    "is": "isl_Latn", "af": "afr_Latn", "az": "azj_Latn", "kk": "kaz_Cyrl", "hy": "hye_Armn",
    "ka": "kat_Geor", "ta": "tam_Taml", "te": "tel_Telu", "mr": "mar_Deva", "gu": "guj_Gujr",
    "kn": "kan_Knda", "ml": "mal_Mlym", "pa": "pan_Guru", "ne": "npi_Deva", "si": "sin_Sinh",
    "km": "khm_Khmr", "lo": "lao_Laoo", "my": "mya_Mymr", "am": "amh_Ethi", "so": "som_Latn",
    "yo": "yor_Latn", "yue": "yue_Hant",
}
# Written without spaces between words.
NO_SPACES = {"ja", "zh", "yue", "th", "lo", "km", "my"}
MAX_SENTENCE = 300  # characters; longer runs without a full stop are translated in pieces
BATCH = 16


def can_translate(source: str, target: str) -> bool:
    return source in NLLB_CODES and target in NLLB_CODES


def joiner(language: str) -> str:
    return "" if language in NO_SPACES else " "


class Translator:
    def __init__(self) -> None:
        self._model = None
        self._tokenizer = None
        self._key: tuple[str, str] | None = None
        self.device = "cpu"

    def load(self, models_dir: str, device: str, on_status: Callable[[str], None]) -> None:
        if self._key == (models_dir, device):
            return
        from huggingface_hub import snapshot_download
        from tokenizers import Tokenizer

        self._model = None
        os.makedirs(models_dir, exist_ok=True)
        local = glob.glob(os.path.join(models_dir, f"models--{REPO.replace('/', '--')}", "snapshots", REVISION,
                                       "model.bin"))
        on_status(t("loading_translator" if local else "downloading_translator",
                    size=f"{DOWNLOAD_SIZE / 1e6:.0f} MB"))
        path = snapshot_download(REPO, revision=REVISION, cache_dir=models_dir, allow_patterns=FILES)
        self._tokenizer = Tokenizer.from_file(os.path.join(path, "tokenizer.json"))
        try:
            self._model = ctranslate2.Translator(path, device=device,
                                                 compute_type="int8_float16" if device == "cuda" else "int8")
            self.device = device
        except Exception:
            if device != "cuda":
                raise
            self._model = ctranslate2.Translator(path, device="cpu", compute_type="int8")
            self.device = "cpu"
        self._key = (models_dir, device)

    def translate(self, texts: list[str], source: str, target: str,
                  on_progress: Callable[[float], None], cancel: threading.Event) -> list[str]:
        """Translate caption texts from one language to another; returns one text per caption
        (occasionally empty, when a translation has fewer words than there are captions)."""
        assert self._model is not None, "call load() first"
        groups = _sentences(texts)
        sentences = [joiner(source).join(texts[i] for i in g).strip() for g in groups]
        translated = self._translate_sentences(sentences, NLLB_CODES[source], NLLB_CODES[target],
                                               on_progress, cancel)
        out = [""] * len(texts)
        for g, text in zip(groups, translated):
            for i, part in zip(g, _share(text, [len(texts[i]) for i in g], target)):
                out[i] = part
        return out

    def _translate_sentences(self, sentences: list[str], src: str, tgt: str,
                             on_progress: Callable[[float], None], cancel: threading.Event) -> list[str]:
        out = [""] * len(sentences)
        todo = [i for i, s in enumerate(sentences) if s]
        for n in range(0, len(todo), BATCH):
            if cancel.is_set():
                raise Cancelled()
            batch = todo[n:n + BATCH]
            tokens = [[src, *self._tokenizer.encode(sentences[i], add_special_tokens=False).tokens, "</s>"]
                      for i in batch]
            results = self._model.translate_batch(tokens, target_prefix=[[tgt]] * len(batch), beam_size=4,
                                                  max_decoding_length=512, repetition_penalty=1.1)
            for i, result in zip(batch, results):
                hyp = result.hypotheses[0]
                if hyp and hyp[0] == tgt:
                    hyp = hyp[1:]
                ids = [self._tokenizer.token_to_id(tok) for tok in hyp]
                out[i] = self._tokenizer.decode([x for x in ids if x is not None]).strip()
            on_progress(min(1.0, (n + len(batch)) / len(todo)))
        return out


def _sentences(texts: list[str]) -> list[list[int]]:
    """Group consecutive captions into sentences (index lists)."""
    groups: list[list[int]] = []
    current: list[int] = []
    length = 0
    for i, text in enumerate(texts):
        if current and length + len(text) > MAX_SENTENCE:
            groups.append(current)
            current, length = [], 0
        current.append(i)
        length += len(text) + 1
        if text.rstrip().endswith(SENTENCE_END):
            groups.append(current)
            current, length = [], 0
    if current:
        groups.append(current)
    return groups


def _share(text: str, weights: list[int], language: str) -> list[str]:
    """Split text into len(weights) consecutive parts sized in proportion to weights, between words."""
    if len(weights) == 1:
        return [text]
    spaced = language not in NO_SPACES and " " in text
    tokens = text.split() if spaced else list(text.replace(" ", ""))
    sizes = [len(tok) + 1 for tok in tokens]
    total, total_weight = sum(sizes), sum(weights) or 1
    parts: list[list[str]] = []
    i = done = weight = 0
    for k, w in enumerate(weights[:-1]):
        weight += w
        goal = total * weight / total_weight
        start = i
        keep = len(weights) - 1 - k  # tokens to leave so each later caption gets one, when possible
        while i < len(tokens) and (i == start or (i < len(tokens) - keep
                                                  and abs(done + sizes[i] - goal) <= abs(done - goal))):
            done += sizes[i]
            i += 1
        parts.append(tokens[start:i])
    parts.append(tokens[i:])
    glue = " " if spaced else ""
    return [glue.join(p) for p in parts]
