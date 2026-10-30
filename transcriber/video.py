"""Reading video details and rendering a copy with captions burned in, matching the source's resolution,
frame rate and frame timing. Audio is copied untouched when the output container allows it."""

from __future__ import annotations

import copy
import os
import threading
from dataclasses import dataclass
from fractions import Fraction
from typing import Callable

import av
import numpy as np

from .captions import Caption, Look, Renderer, blend_yuv420

# Audio codecs an .mp4 can carry as they are; anything else is converted to AAC.
MP4_AUDIO = {"aac", "mp3", "alac", "ac3", "eac3", "opus", "flac"}


class Cancelled(Exception):
    pass


@dataclass
class VideoInfo:
    path: str
    width: int           # as displayed, after any rotation
    height: int
    fps: Fraction
    duration: float
    rotation: int        # quarter turns counter-clockwise needed to display the frames upright
    start: float         # the media time that caption time 0 corresponds to
    has_audio: bool
    codec: str
    bit_rate: int


def probe(path: str) -> VideoInfo:
    with av.open(path) as c:
        if not c.streams.video:
            raise ValueError("no video stream")
        vs = c.streams.video[0]
        frame = next(c.decode(vs), None)
        if frame is None:
            raise ValueError("no video frames")
        turns = round((frame.rotation or 0) / 90) % 4
        w, h = (frame.height, frame.width) if turns % 2 else (frame.width, frame.height)
        audio = c.streams.audio[0] if c.streams.audio else None
        # Transcription times are relative to the first audio sample.
        ref = audio if audio is not None else vs
        start = float(ref.start_time * ref.time_base) if ref.start_time is not None else 0.0
        if vs.duration:
            duration = float(vs.duration * vs.time_base)
        elif c.duration:
            duration = c.duration / av.time_base
        else:
            duration = 0.0
        fps = vs.average_rate or vs.guessed_rate or Fraction(30)
        return VideoInfo(path, w, h, Fraction(fps), duration, turns, start, audio is not None,
                         vs.codec_context.name, vs.bit_rate or c.bit_rate or 0)


def matrix_for(colorspace: int, height: int) -> tuple[float, float]:
    """Luma coefficients (Kr, Kb) of a frame's colour matrix."""
    if colorspace in (5, 6):  # BT.601 (bt470bg, smpte170m)
        return 0.299, 0.114
    if colorspace in (9, 10):  # BT.2020
        return 0.2627, 0.0593
    if colorspace == 1 or height >= 720:  # BT.709, also the usual assumption for untagged HD video
        return 0.2126, 0.0722
    return 0.299, 0.114


def _planes(frame: av.VideoFrame, turns: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Copies of a yuv420p frame's planes, turned upright."""
    w, h = frame.width, frame.height
    packed = frame.to_ndarray()
    y = packed[:h]
    u = packed[h:h + h // 4].reshape(h // 2, w // 2)
    v = packed[h + h // 4:].reshape(h // 2, w // 2)
    if turns:
        y, u, v = (np.rot90(p, turns) for p in (y, u, v))
    return np.ascontiguousarray(y), np.ascontiguousarray(u), np.ascontiguousarray(v)


def _pack(y: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    h, w = y.shape
    return np.concatenate([y, u.reshape(h // 4, w), v.reshape(h // 4, w)])


def _open_encoder(out, info: VideoInfo, vin, use_gpu: bool):
    """The H.264 stream for the output: NVENC on an NVIDIA card, otherwise x264. Both at near-lossless
    quality, since the platforms re-compress what you upload anyway."""
    rate = info.fps.limit_denominator(1001 * 1000)
    candidates = (["h264_nvenc"] if use_gpu else []) + ["libx264"]
    last_error: Exception | None = None
    for name in candidates:
        try:
            stream = out.add_stream(name, rate=rate)
        except Exception as e:  # noqa: BLE001
            last_error = e
            continue
        stream.width, stream.height = info.width, info.height
        stream.pix_fmt = "yuv420p"
        stream.time_base = vin.time_base
        stream.codec_context.time_base = vin.time_base
        cc_in = vin.codec_context
        for attr in ("color_primaries", "color_trc", "colorspace", "color_range"):
            try:
                setattr(stream.codec_context, attr, getattr(cc_in, attr))
            except (AttributeError, ValueError, TypeError):
                pass
        if name == "h264_nvenc":
            stream.codec_context.options = {"preset": "p6", "tune": "hq", "rc": "vbr", "cq": "19", "b": "0",
                              "profile": "high", "spatial-aq": "1"}
        else:
            stream.codec_context.options = {"preset": "medium", "crf": "18", "profile": "high"}
        try:
            stream.codec_context.open()
        except Exception as e:  # noqa: BLE001
            # NVENC fails to open without a recent driver; the stream can't be removed, so start over.
            last_error = e
            return None, name, e
        return stream, name, None
    return None, None, last_error


def render(info: VideoInfo, dst: str, captions: list[Caption], look: Look,
           on_progress: Callable[[float], None], cancel: threading.Event, use_gpu: bool = False) -> str:
    """Write a copy of the video with the captions burned in. Returns the encoder that was used."""
    captions = copy.deepcopy(captions)
    look = copy.deepcopy(look)
    try:
        return _render(info, dst, captions, look, on_progress, cancel, use_gpu)
    except Exception as e:
        if use_gpu and getattr(e, "_nvenc", False):
            return _render(info, dst, captions, look, on_progress, cancel, False)
        raise


def _render(info, dst, captions, look, on_progress, cancel, use_gpu) -> str:
    renderer = Renderer(captions)
    inp = av.open(info.path)
    out = av.open(dst, "w", options={"movflags": "+faststart"})
    finished = False
    try:
        vin = inp.streams.video[0]
        vin.thread_type = "AUTO"
        ain = inp.streams.audio[0] if inp.streams.audio else None

        vout, encoder, error = _open_encoder(out, info, vin, use_gpu)
        if vout is None:
            if encoder == "h264_nvenc":
                error._nvenc = True  # type: ignore[attr-defined]
            raise error or RuntimeError("no H.264 encoder")

        aout = resampler = None
        if ain is not None:
            if ain.codec_context.name in MP4_AUDIO:
                aout = out.add_stream_from_template(ain)
            else:
                aout = out.add_stream("aac", rate=ain.codec_context.sample_rate or 48000)
                aout.bit_rate = 256_000
                resampler = av.AudioResampler("fltp", ain.codec_context.layout or "stereo", aout.rate)

        full_range = vin.codec_context.color_range == 2 or (vin.codec_context.pix_fmt or "").startswith("yuvj")
        matrix = matrix_for(vin.codec_context.colorspace, min(info.width, info.height))
        duration = info.duration or 1.0
        count = 0

        streams = [vin] + ([ain] if ain is not None else [])
        for packet in inp.demux(*streams):
            if cancel.is_set():
                raise Cancelled()
            if packet.stream is vin:
                for frame in packet.decode():
                    _write_frame(frame, out, vout, renderer, look, info, matrix, full_range)
                    count += 1
                    if count % 10 == 0 and frame.time is not None:
                        on_progress(min(1.0, (frame.time - info.start) / duration))
            elif resampler is None:
                if packet.dts is None:
                    continue
                packet.stream = aout
                out.mux(packet)
            else:
                for frame in packet.decode():
                    for f in resampler.resample(frame):
                        out.mux(aout.encode(f))

        out.mux(vout.encode(None))
        if resampler is not None:
            for f in resampler.resample(None):
                out.mux(aout.encode(f))
            out.mux(aout.encode(None))
        finished = True
        on_progress(1.0)
        return encoder
    finally:
        inp.close()
        out.close()
        if not finished:
            try:
                os.remove(dst)
            except OSError:
                pass


def _plane_views(frame: av.VideoFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Writable views straight into a yuv420p frame's planes (rows are padded to line_size)."""
    views = []
    for plane in frame.planes:
        rows = np.frombuffer(plane, np.uint8).reshape(-1, plane.line_size)
        views.append(rows[:plane.height, :plane.width])
    return views[0], views[1], views[2]


def _write_frame(frame, out, vout, renderer: Renderer, look: Look, info: VideoInfo,
                 matrix, full_range: bool) -> None:
    if frame.format.name not in ("yuv420p", "yuvj420p"):
        frame = frame.reformat(format="yuv420p")
    t = (frame.time - info.start) if frame.time is not None else 0.0
    ov = renderer.overlay(look, t, info.width, info.height)
    if info.rotation:
        y, u, v = _planes(frame, info.rotation)
        if ov is not None:
            blend_yuv420(y, u, v, ov, matrix, full_range)
        new = av.VideoFrame.from_ndarray(_pack(y, u, v), format="yuv420p")
        new.pts, new.time_base = frame.pts, frame.time_base
        frame = new
    else:
        # Draw into the decoded frame itself; untouched pixels go to the encoder exactly as decoded.
        if ov is not None:
            frame.make_writable()
            blend_yuv420(*_plane_views(frame), ov, matrix, full_range)
        frame.pict_type = 0  # let the encoder place keyframes rather than copying the source's
    out.mux(vout.encode(frame))
