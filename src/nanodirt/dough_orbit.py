"""superdough's orbit effects for Strudel events: feedback delay and convolution reverb.

``superdoughoutput.mjs`` gives each orbit one delay and one reverb, created on
first use; voices feed them from after ``postgain``. See
docs/dev/superdough-mapping.md, "Orbit".
"""

from __future__ import annotations

import math
import random
import struct
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nanosynth.osc import OscMessage
from nanosynth.synthdef import SynthDef, SynthDefBuilder, control
from nanosynth.ugens import CombL, DelayL, In, Out, PartConv

DELAY_SYNC = 3 / 16  # cycles: superdough's default delay time
MAX_DELAY = 1.0  # DelayNode's default maximum
MAX_FEEDBACK = 0.98
QUANTUM = 128  # Web Audio's render quantum: the shortest delay in a feedback loop
FFT_SIZE = 1024  # PartConv: latency FFT_SIZE / 2 - 2 * block size
REVERB_DEFAULTS = {"roomsize": 2.0, "roomfade": 0.1, "roomlp": 15000.0, "roomdim": 1000.0}


def _number(x: Any) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def delay_settings(e: Mapping[str, Any], sample_rate: float) -> tuple[float, float, float] | None:
    """(send, time, feedback), or None if the event does not use the delay."""
    send = _number(e.get("delay")) or 0.0
    cps = _number(e.get("cps")) or 0.5
    time_ = _number(e.get("delaytime"))
    if time_ is None:
        time_ = (_number(e.get("delaysync")) or DELAY_SYNC) / cps
    feedback = _number(e.get("delayfeedback"))
    feedback = 0.5 if feedback is None else feedback
    if not (send > 0 and time_ > 0 and feedback > 0):
        return None
    time_ = min(max(time_, QUANTUM / sample_rate), MAX_DELAY)
    return send, time_, min(max(feedback, 0.0), MAX_FEEDBACK)


def reverb_settings(e: Mapping[str, Any]) -> tuple[float, dict[str, float]] | None:
    """(send, the IR parameters the event gives), or None if ``room`` is not positive."""
    send = _number(e.get("room")) or 0.0
    if send <= 0:
        return None
    given = {k: v for k in REVERB_DEFAULTS if (v := _number(e.get(k))) is not None}
    return send, given


# -- impulse response (reverbGen.mjs, reverb.mjs) ----------------------------------------


def reverb_ir(
    size: float, fade: float, lp: float, dim: float, rate: float, rng: random.Random
) -> list[list[float]]:
    """``generateReverb``: two channels of decaying noise, faded in, gradually lowpassed."""
    total = round(size * 1.5 * rate)
    decay_frames = round(size * rate)
    fade_frames = round(fade * rate)
    base = (1 / 1000) ** (1 / decay_frames)
    channels = []
    for _ in range(2):
        ch = [(rng.random() * 2 - 1) * base**j for j in range(total)]
        for j in range(min(fade_frames, total)):
            ch[j] *= j / fade_frames
        channels.append(ch)
    if lp:
        _gradual_lowpass(channels, min(lp, rate / 2), min(dim, rate / 2), size, rate)
    return channels


def _gradual_lowpass(
    channels: list[list[float]], start: float, end: float, at: float, rate: float
) -> None:
    """``applyGradualLowpass``: a lowpass biquad (Q 0.0001 dB) whose cutoff ramps linearly."""
    alpha_q = 2 * 10 ** (0.0001 / 20)
    end_frame = at * rate
    states = [[0.0, 0.0, 0.0, 0.0] for _ in channels]  # x1, x2, y1, y2
    coefs = None
    for j in range(len(channels[0])):
        if coefs is None or j <= end_frame:
            f = start + (end - start) * min(j / end_frame, 1.0)
            w0 = 2 * math.pi * f / rate
            cos, alpha = math.cos(w0), math.sin(w0) / alpha_q
            a0 = 1 + alpha
            coefs = ((1 - cos) / 2 / a0, (1 - cos) / a0, -2 * cos / a0, (1 - alpha) / a0)
        b0, b1, a1, a2 = coefs
        for ch, st in zip(channels, states):
            x = ch[j]
            y = b0 * x + b1 * st[0] + b0 * st[1] - a1 * st[2] - a2 * st[3]
            st[1], st[0], st[3], st[2] = st[0], x, st[2], y
            ch[j] = y


def normalization_scale(channels: list[list[float]], rate: float) -> float:
    """``ConvolverNode``'s normalization (Web Audio spec, "calculateNormalizationScale")."""
    length = len(channels[0])
    power = math.sqrt(sum(x * x for ch in channels for x in ch) / (len(channels) * length))
    if not math.isfinite(power) or power < 0.000125:
        power = 0.000125
    return 1 / power * 0.00125 * 44100 / rate


def write_float_wav(path: Path, channels: list[list[float]], rate: float) -> None:
    """32-bit float WAV (format 3), interleaved."""
    n, frames = len(channels), len(channels[0])
    data = struct.pack(f"<{n * frames}f", *[ch[j] for j in range(frames) for ch in channels])
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(data), b"WAVE", b"fmt ", 16, 3, n,
        int(rate), int(rate) * 4 * n, 4 * n, 32, b"data", len(data),
    )  # fmt: skip
    path.write_bytes(header + data)


def partitions(frames: int) -> int:
    return math.ceil(frames / (FFT_SIZE // 2))


def ir_messages(
    path: Path, frames: int, ir: tuple[int, int], spec: tuple[int, int]
) -> tuple[list[OscMessage], list[OscMessage]]:
    """Load a stereo IR file into two mono buffers; then prepare PartConv spectra from them.

    A realtime engine must finish the loads (``/sync``) before the preparation reads them.
    """
    load, prepare = [], []
    for ch in range(2):
        load += [
            OscMessage("/b_allocReadChannel", ir[ch], str(path), 0, frames, ch),
            OscMessage("/b_alloc", spec[ch], FFT_SIZE * partitions(frames), 1),
        ]
        prepare.append(OscMessage("/b_gen", spec[ch], "PreparePartConv", ir[ch], FFT_SIZE))
    return load, prepare


def latency(block_size: int) -> int:
    """PartConv's output delay in samples, measured: FFT_SIZE / 2 - 2 * block size."""
    return FFT_SIZE // 2 - 2 * block_size


def prepare_ir(
    params: Mapping[str, float], rate: float, block_size: int, rng: random.Random, path: Path
) -> int:
    """Generate, normalize, align and write the IR; returns its length in frames.

    The first ``latency`` frames are dropped, so the reverb is not delayed by
    PartConv. superdough fades the IR in over ``roomfade``, so they carry little energy.
    """
    p = {**REVERB_DEFAULTS, **params}
    channels = reverb_ir(p["roomsize"], p["roomfade"], p["roomlp"], p["roomdim"], rate, rng)
    scale = normalization_scale(channels, rate)
    skip = latency(block_size)
    channels = [[x * scale for x in ch[skip:]] for ch in channels]
    write_float_wav(path, channels, rate)
    return len(channels[0])


# -- SynthDefs ---------------------------------------------------------------------------


def synthdefs() -> list[SynthDef]:
    controls = {
        "in": control(0, "ir"), "out": control(0, "ir"), "delaytime": control(0.375),
        "loop": control(0.378), "feedback": control(0.5), "decay": control(1.0),
    }  # fmt: skip
    with SynthDefBuilder(**controls) as b:
        x = In.ar(bus=b["in"], channel_count=2)
        # superdough's DelayNode loop, measured in Chrome: y = x[-T] + fb y[-T - 128], one
        # render quantum more per pass. As w + fb CombL(w) with w = x[-T]; CombL's loop
        # gain is 0.001^(loop / decay) = fb.
        y = []
        for c in x:
            w = DelayL.ar(source=c, maximum_delay_time=MAX_DELAY, delay_time=b["delaytime"])
            comb = CombL.ar(
                source=w,
                maximum_delay_time=MAX_DELAY + 0.02,
                delay_time=b["loop"],
                decay_time=b["decay"],
            )
            y.append(w + b["feedback"] * comb)
        Out.ar(bus=b["out"], source=y)
    delay = b.build(name="dough_delay")
    with SynthDefBuilder(
        **{"in": control(0, "ir"), "out": control(0, "ir"), "left": control(0), "right": control(0)}
    ) as b:
        x = In.ar(bus=b["in"], channel_count=2)
        y = [
            PartConv.ar(source=c, fft_size=FFT_SIZE, ir_buffer_id=b[k])
            for c, k in zip(x, ("left", "right"))
        ]
        Out.ar(bus=b["out"], source=y)
    return [delay, b.build(name="dough_reverb")]


def comb_decay(time_: float, feedback: float) -> float:
    """CombL's decay time for a feedback gain: c = 0.001^(T / decay)."""
    return math.log(0.001) * time_ / math.log(feedback)


def delay_controls(time_: float, feedback: float, sample_rate: float) -> list[Any]:
    """``dough_delay``'s controls for a delay time and feedback gain."""
    loop = time_ + QUANTUM / sample_rate
    return [
        "delaytime",
        time_,
        "loop",
        loop,
        "feedback",
        feedback,
        "decay",
        comb_decay(loop, feedback),
    ]


# -- per-orbit state ---------------------------------------------------------------------


@dataclass
class DoughOrbit:
    """One orbit's delay and reverb, created on first use as superdough does."""

    delay_bus: int
    room_bus: int
    delay_node: int | None = None
    reverb_node: int | None = None
    reverb_params: dict[str, float] = field(default_factory=dict)  # as generated, defaults filled
    reverb_pending: dict[str, float] | None = None
    reverb_buffers: list[int] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)


__all__ = [
    "DoughOrbit",
    "comb_decay",
    "delay_controls",
    "delay_settings",
    "ir_messages",
    "latency",
    "normalization_scale",
    "prepare_ir",
    "reverb_ir",
    "reverb_settings",
    "synthdefs",
]
