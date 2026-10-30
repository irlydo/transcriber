"""Speech-to-text using faster-whisper, running on the GPU when available."""

from __future__ import annotations

import glob
import os
import threading
from dataclasses import dataclass, field
from typing import Callable

from . import gpu_pack
from .i18n import t
from .subtitles import CaptionStyle, Cue, Word, build_cues

_CUDNN_AVAILABLE = gpu_pack.register_dlls()

import ctranslate2  # noqa: E402  (must come after DLL registration)
from faster_whisper import WhisperModel, decode_audio  # noqa: E402

PUNCTUATION_PROMPTS = {
    "en": "Hello, and welcome. Here is the transcript, with proper punctuation!",
    "es": "Hola, y bienvenidos. Aquí está la transcripción, con la puntuación correcta.",
    "fr": "Bonjour, et bienvenue. Voici la transcription, avec la ponctuation correcte.",
    "de": "Hallo, und willkommen. Hier ist das Transkript, mit korrekter Zeichensetzung.",
    "it": "Ciao, e benvenuti. Ecco la trascrizione, con la punteggiatura corretta.",
    "pt": "Olá, e bem-vindos. Aqui está a transcrição, com a pontuação correta.",
    "nl": "Hallo, en welkom. Hier is het transcript, met de juiste interpunctie.",
}


def gpu_available() -> bool:
    try:
        return _CUDNN_AVAILABLE and ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


@dataclass
class Result:
    cues: list[Cue]
    text: str
    language: str
    duration: float
    words: list[Word] = field(default_factory=list)


class Cancelled(Exception):
    pass


class Transcriber:
    def __init__(self, models_dir: str, use_gpu: bool = True) -> None:
        self._model: WhisperModel | None = None
        self._model_key: tuple[str, str, str] | None = None
        self.models_dir = models_dir
        self.device = "cuda" if use_gpu and gpu_available() else "cpu"

    def configure(self, models_dir: str, use_gpu: bool) -> None:
        """Takes effect the next time a model is loaded."""
        self.models_dir = models_dir
        self.device = "cuda" if use_gpu and gpu_available() else "cpu"

    def load(self, model_name: str, on_status: Callable[[str], None]) -> None:
        key = (model_name, self.device, self.models_dir)
        if self._model_key == key:
            return
        self._model = None
        os.makedirs(self.models_dir, exist_ok=True)
        local = glob.glob(os.path.join(self.models_dir, f"models--*--faster-whisper-{model_name}"))
        on_status(t("loading_model" if local else "downloading_model", model=model_name))
        compute = "float16" if self.device == "cuda" else "int8"
        try:
            self._model = WhisperModel(model_name, device=self.device, compute_type=compute,
                                       download_root=self.models_dir)
        except Exception:
            if self.device != "cuda":
                raise
            on_status(t("gpu_fallback"))
            self.device = "cpu"
            self._model = WhisperModel(model_name, device="cpu", compute_type="int8",
                                       download_root=self.models_dir)
        self._model_key = (model_name, self.device, self.models_dir)

    def transcribe(
        self,
        path: str,
        style: CaptionStyle,
        language: str | None,
        translate: bool,
        on_progress: Callable[[float, str], None],
        cancel: threading.Event,
    ) -> Result:
        assert self._model is not None, "call load() first"
        on_progress(0.0, "")
        audio = decode_audio(path)
        if cancel.is_set():
            raise Cancelled()
        if language is None:
            language, _, _ = self._model.detect_language(audio, vad_filter=True, language_detection_segments=3)
        # Without a punctuated example, Whisper sometimes writes everything lowercase with no punctuation.
        prompt = PUNCTUATION_PROMPTS.get("en" if translate else language)
        segments, info = self._model.transcribe(
            audio,
            language=language,
            initial_prompt=prompt,
            task="translate" if translate else "transcribe",
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            beam_size=5,
            condition_on_previous_text=False,  # avoids repeated-phrase hallucinations
        )

        words: list[Word] = []
        texts: list[str] = []
        for seg in segments:
            if cancel.is_set():
                raise Cancelled()
            for w in seg.words or []:
                words.append(Word(w.start, w.end, w.word))
            texts.append(seg.text.strip())
            pct = min(1.0, seg.end / info.duration) if info.duration else 0.0
            on_progress(pct, seg.text.strip())

        return Result(build_cues(words, style), "\n".join(texts), info.language, info.duration, words)
