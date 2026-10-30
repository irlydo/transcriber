"""Video playback for the caption editor: frames are decoded on a background thread at the size they're
shown, and the audio plays through the default output device (when one is available)."""

from __future__ import annotations

import threading
import time

import av
import numpy as np
from PIL import Image

from .video import VideoInfo

try:
    import sounddevice
except Exception:  # noqa: BLE001  (no PortAudio: play silently)
    sounddevice = None

SAMPLE_RATE = 48000


class Player:
    def __init__(self, info: VideoInfo) -> None:
        self.info = info
        self.size = (max(2, info.width // 4), max(2, info.height // 4))  # display size, set by the UI
        self.frame: tuple[int, Image.Image] | None = None  # (sequence number, picture) for the UI
        self.playing = False
        self._pos = 0.0          # playhead while paused, or where playback started
        self._wall = 0.0         # perf_counter() at which _pos was (or will be) heard
        self._closed = threading.Event()
        self._audio: np.ndarray | None = None
        self._sample = 0
        self._stream = None
        self._latency = 0.0
        self._lock = threading.Lock()
        threading.Thread(target=self._decode_loop, daemon=True).start()
        if info.has_audio and sounddevice is not None:
            threading.Thread(target=self._load_audio, daemon=True).start()

    # ---------- clock ----------

    def position(self) -> float:
        with self._lock:
            if not self.playing:
                return self._pos
            t = self._pos + max(0.0, time.perf_counter() - self._wall)
        if self.info.duration and t >= self.info.duration:
            self.pause()
            self.seek(self.info.duration)
            return self.info.duration
        return t

    def play(self) -> None:
        if self.playing:
            return
        if self.info.duration and self._pos >= self.info.duration - 0.05:
            self._pos = 0.0
        self._start_audio()
        with self._lock:
            self._sample = int(self._pos * SAMPLE_RATE)
            # Sound written now is heard after the output latency; hold the picture back to match.
            self._wall = time.perf_counter() + self._latency
            self.playing = True

    def pause(self) -> None:
        with self._lock:
            if self.playing:
                self._pos += max(0.0, time.perf_counter() - self._wall)
                self.playing = False

    def toggle(self) -> None:
        self.pause() if self.playing else self.play()

    def seek(self, t: float) -> None:
        t = max(0.0, min(t, self.info.duration or t))
        with self._lock:
            self._pos = t
            self._wall = time.perf_counter() + (self._latency if self.playing else 0.0)
            self._sample = int(t * SAMPLE_RATE)

    def close(self) -> None:
        self._closed.set()
        self.playing = False
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:  # noqa: BLE001
                pass

    # ---------- audio ----------

    def _load_audio(self) -> None:
        try:
            chunks = []
            with av.open(self.info.path) as c:
                resampler = av.AudioResampler("s16", "stereo", SAMPLE_RATE)
                for frame in c.decode(audio=0):
                    if self._closed.is_set():
                        return
                    for f in resampler.resample(frame):
                        chunks.append(f.to_ndarray().reshape(-1, 2))
            self._audio = np.concatenate(chunks) if chunks else None
        except Exception:  # noqa: BLE001  (play the picture without sound)
            self._audio = None

    def _start_audio(self) -> None:
        if self._stream is not None or sounddevice is None or not self.info.has_audio:
            return
        try:
            self._stream = sounddevice.OutputStream(samplerate=SAMPLE_RATE, channels=2, dtype="int16",
                                                    latency="low", callback=self._fill)
            self._stream.start()
            self._latency = float(self._stream.latency)
        except Exception:  # noqa: BLE001
            self._stream = None

    def _fill(self, out, frames, _time, _status) -> None:
        audio = self._audio
        if not self.playing or audio is None:
            out.fill(0)
            return
        with self._lock:
            start = self._sample
            self._sample += frames
        chunk = audio[start:start + frames] if start >= 0 else audio[:0]
        out[:len(chunk)] = chunk
        out[len(chunk):] = 0

    # ---------- pictures ----------

    def _decode_loop(self) -> None:
        try:
            container = av.open(self.info.path)
        except Exception:  # noqa: BLE001
            return
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        frame_dur = float(1 / self.info.fps) if self.info.fps else 1 / 30
        frames = None
        current = upcoming = None   # the frame on screen, and the next one decoded
        current_t = upcoming_t = -1.0
        shown = None                 # (frame, size) last published
        seq = 0
        eof = False
        sought = -10.0               # where we last seeked to

        def start_at(t: float):
            ts = int((t + self.info.start) / stream.time_base)
            container.seek(max(0, ts), stream=stream, backward=True)
            return container.decode(stream)

        while not self._closed.is_set():
            target = self.position()
            # Jumped backwards, or far ahead: seek rather than decode through. (A seek lands on the
            # keyframe before the target, which can itself be far behind; don't seek again for that.)
            if frames is None or (current is not None and (
                    target < current_t - 1e-3 or (target > current_t + 2.0 and abs(target - sought) > 2.0))):
                try:
                    sought = target
                    frames = start_at(target)
                except Exception:  # noqa: BLE001
                    time.sleep(0.05)
                    continue
                current = upcoming = None
                current_t = upcoming_t = -1.0
                eof = False

            if upcoming is None and not eof:
                try:
                    upcoming = next(frames)
                    upcoming_t = (upcoming.time - self.info.start) if upcoming.time is not None else current_t + frame_dur
                except StopIteration:
                    eof = True
                except Exception:  # noqa: BLE001  (a damaged frame: skip it)
                    continue

            if upcoming is not None and (upcoming_t <= target + 1e-3 or current is None):
                current, current_t = upcoming, upcoming_t
                upcoming = None
                continue  # keep catching up before drawing

            if current is not None and shown != (current, self.size):
                shown = (current, self.size)
                seq += 1
                self.frame = (seq, self._picture(current, self.size))
                continue
            time.sleep(0.004)

        container.close()

    def _picture(self, frame: av.VideoFrame, size: tuple[int, int]) -> Image.Image:
        w, h = size
        turns = self.info.rotation
        if turns % 2:
            w, h = h, w
        img = frame.reformat(width=w, height=h, format="rgb24").to_image()
        if turns:
            img = img.rotate(90 * turns, expand=True)
        return img
