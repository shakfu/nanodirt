"""superdough's voice for Strudel events (docs/dev/superdough-mapping.md).

A voice is a group in the orbit, as in SuperDirt: a source synth writes the
enveloped source to the orbit's synth bus; ``dough_gate`` applies gain, pan
and postgain, adds the result to the dry bus and clears the synth bus. The
envelope's breakpoints depend on the event's duration, so they are computed
here per event and sent as controls.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from nanosynth.envelopes import Envelope, EnvGen
from nanosynth.synthdef import SynthDef, SynthDefBuilder, control
from nanosynth.ugens import (
    DC,
    SOS,
    BufRateScale,
    BufRd,
    BufSampleRate,
    Delay1,
    Delay2,
    Dust2,
    Impulse,
    In,
    LFTri,
    OffsetOut,
    OnePole,
    Out,
    Phasor,
    Pulse,
    ReplaceOut,
    SampleRate,
    Saw,
    Select,
    SinOsc,
    Sweep,
    WhiteNoise,
)

from .library import Sample
from .strudel import note_to_midi

OSCILLATORS = {
    "sine": "sine", "sin": "sine", "triangle": "triangle", "tri": "triangle",
    "square": "square", "sqr": "square", "sawtooth": "sawtooth", "saw": "sawtooth",
}  # fmt: skip
NOISES = {"white", "pink", "brown", "crackle"}
SOURCE_LEVEL = 0.3  # synth.mjs: "turn down"
SOURCE_ENVELOPE = (0.001, 0.05, 0.6, 0.01)  # synth.mjs defaults for oscillators and noise
SAMPLE_ENVELOPE = (0.001, 0.001, 1.0, 0.01)  # getADSRValues with no defaults
DEFAULT_GAIN = 0.8
CRACKLE_DENSITY = 0.02  # getNoiseOscillator's default; synth.mjs reads value.density directly
# Web Audio normalizes band-limited waves to peak 1, Gibbs overshoot (~1.18) included;
# Saw and Pulse peak at 0.5. Measured RMS ratio 1.68-1.69 (tests/test_dough.py).
WAVE_LEVEL = {"triangle": 1.0, "square": 1.69, "sawtooth": 1.69}

# (name, index, midi) -> (sample, root note of a pitched bank or None)
FindSample = Callable[[str, int, float], "tuple[Sample, float | None] | None"]


@dataclass
class Voice:
    source: str  # SynthDef name
    controls: dict[str, float]  # source controls; the player adds out and buf
    channels: int  # channels the source writes: 1 or 2
    gate: dict[str, float]
    sample: Sample | None = None
    cut: int | None = None
    nudge: float = 0.0
    filters: list[tuple[str, dict[str, float]]] = field(default_factory=list)


SILENT = Voice("", {}, 1, {})  # a superdough source that plays nothing (speed 0)


def adsr_values(
    params: tuple[float | None, ...], defaults: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    """``getADSRValues`` (helpers.mjs): fill unset stages the way superdough does."""
    a, d, s, r = params
    if a is None and d is None and s is None and r is None:
        return defaults
    if s is not None:
        sustain = s
    elif (a is not None and d is None) or (a is None and d is None):
        sustain = 1.0
    else:
        sustain = 0.001
    return max(a or 0.0, 0.001), max(d or 0.0, 0.001), min(sustain, 1.0), max(r or 0.0, 0.01)


def envelope_points(
    adsr: tuple[float, float, float, float], duration: float, lo: float = 0.0, hi: float = 1.0
) -> tuple[list[float], list[float]]:
    """``getParamADSR`` from *lo* to *hi*: target levels and times of 4 segments.

    A note shorter than attack or attack + decay stops its ramp where the full
    envelope would be at its end. Empty stages are dropped and the release is
    followed by padding at level 0: EnvGen spends one sample on each
    zero-length segment, which would delay the release.
    """
    attack, decay, sustain, release = adsr

    def at(time: float) -> float:
        if attack > time:
            return time / attack
        return 1.0 + (time - attack) * (sustain - 1.0) / decay

    if attack > duration:
        stages = [(at(duration), duration)]
    elif attack + decay > duration:
        stages = [(1.0, attack), (at(duration), duration - attack)]
    else:
        stages = [(1.0, attack), (sustain, decay), (sustain, duration - attack - decay)]
    stages = [(lo + lv * (hi - lo), t) for lv, t in stages if t > 0] + [(lo, release)]
    stages += [(lo, 0.0)] * (4 - len(stages))
    return [lv for lv, _ in stages], [t for _, t in stages]


def midi(e: Mapping[str, Any], default: float = 36) -> float:
    """``valueToMidi``: from ``freq``, else ``note`` (number or name), else *default*."""
    freq, note = e.get("freq"), e.get("note")
    if isinstance(freq, (int, float)) and not isinstance(freq, bool):
        return 69 + 12 * math.log2(freq / 440)
    if isinstance(note, str):
        return float(note_to_midi(note))
    if isinstance(note, (int, float)) and not isinstance(note, bool):
        return float(note)
    return default


def frequency(e: Mapping[str, Any]) -> float:
    """``getFrequencyFromValue``: ``freq``, else ``note`` (default 36), times ``2^octave``."""
    freq = e.get("freq")
    if not freq:
        note = e.get("note") or 36
        if isinstance(note, str):
            note = note_to_midi(note)
        freq = 440.0 * 2 ** ((float(note) - 69) / 12)
    return float(freq) * 2 ** float(e.get("octave") or 0)


def _number(x: Any) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _envelope(
    e: Mapping[str, Any], defaults: tuple[float, float, float, float], duration: float
) -> tuple[dict[str, float], float]:
    adsr = adsr_values(
        tuple(_number(e.get(k)) for k in ("attack", "decay", "sustain", "release")), defaults
    )
    levels, times = envelope_points(adsr, duration)
    controls = {f"l{i + 1}": v for i, v in enumerate(levels)}
    controls |= {f"t{i + 1}": v for i, v in enumerate(times)}
    return controls, duration + adsr[3] + 0.01  # the source stops at end + release + 0.01


def _gate(e: Mapping[str, Any], life: float) -> dict[str, float]:
    """``superdough()``'s voice chain after the source: gain, pan, postgain."""
    gain, velocity, postgain, pan = (
        _number(e.get(k)) for k in ("gain", "velocity", "postgain", "pan")
    )
    level = (DEFAULT_GAIN if gain is None else gain) * (1.0 if velocity is None else velocity)
    return {
        "level": level * (1.0 if postgain is None else postgain),
        "pan": 0.5 if pan is None else pan,
        "panned": 0.0 if pan is None else 1.0,  # superdough adds a panner only when pan is set
        "life": life,
    }


FILTERS = [  # superdough.mjs: lowpass, highpass, bandpass, in that order
    ("lpf", "cutoff", "resonance", "lp"),
    ("hpf", "hcutoff", "hresonance", "hp"),
    ("bpf", "bandf", "bandq", "bp"),
]
FILTER_ENVELOPE = (0.005, 0.14, 0.0, 0.1)  # createFilter's defaults
MAX_FILTER_FREQ = 20000.0


def filter_type(ftype: Any) -> str:
    """``getFilterType``: 0 ``12db`` (default), 1 ``ladder``, 2 ``24db``, or the name."""
    if ftype is None:
        return "12db"
    if isinstance(ftype, (int, float)) and not isinstance(ftype, bool):
        return ["12db", "ladder", "24db"][math.floor(ftype) % 3]
    return str(ftype)


def filters(e: Mapping[str, Any], duration: float) -> list[tuple[str, dict[str, float]]]:
    """``createFilter`` for each filter the event sets: SynthDef kind and controls.

    The cutoff envelope runs from the event's onset to ``onset + duration``. The
    ladder model (``ftype`` 1) is not implemented; it plays as ``12db``.
    """
    out = []
    stages = 2.0 if filter_type(e.get("ftype")) == "24db" else 1.0
    for kind, key, q_key, p in FILTERS:
        freq = _number(e.get(key))
        if freq is None:
            continue
        q = _number(e.get(q_key))
        controls = {"q": 1.0 if q is None else q, "stages": stages}
        values = tuple(_number(e.get(p + k)) for k in ("attack", "decay", "sustain", "release"))
        env = _number(e.get(p + "env"))
        if env is None and all(v is None for v in values):
            levels, times, start = [freq] * 4, [0.0] * 4, freq
        else:
            env = 1.0 if env is None else env
            anchor = _number(e.get("fanchor")) or 0.0
            offset = abs(env) * anchor
            lo = min(max(2**-offset * freq, 0.0), MAX_FILTER_FREQ)
            hi = min(max(2 ** (abs(env) - offset) * freq, 0.0), MAX_FILTER_FREQ)
            if env < 0:
                lo, hi = hi, lo
            lo, hi = lo or 0.001, hi or 0.001  # exponential ramps cannot reach 0
            levels, times = envelope_points(adsr_values(values, FILTER_ENVELOPE), duration, lo, hi)
            start = lo
        controls["f0"] = start
        controls |= {f"f{i + 1}": v for i, v in enumerate(levels)}
        controls |= {f"ft{i + 1}": v for i, v in enumerate(times)}
        out.append((kind, controls))
    return out


def voice(
    e: Mapping[str, Any], num_channels: int, find_sample: FindSample | None = None
) -> Voice | None:
    """superdough's voice for a Strudel event, or None if superdough has no such sound."""
    s = e.get("s")
    if not isinstance(s, str):
        return None
    bank = e.get("bank")
    name = OSCILLATORS.get(s.lower(), s.lower()) if not bank else f"{bank}_{s}"
    duration = float(e.get("delta") or 0.0)
    if name in OSCILLATORS.values() or name in NOISES:
        env, life = _envelope(e, SOURCE_ENVELOPE, duration)
        controls = {"amp": SOURCE_LEVEL, "freq": frequency(e), **env}
        if name == "crackle":
            controls["density"] = _number(e.get("density")) or CRACKLE_DENSITY
        return Voice(f"dough_{name}", controls, 1, _gate(e, life), filters=filters(e, duration))
    if find_sample is None:
        return None
    return _sample_voice(e, name, duration, find_sample)


def _sample_voice(
    e: Mapping[str, Any], name: str, duration: float, find: FindSample
) -> Voice | None:
    """``onTriggerSample`` and ``getSampleBufferSource`` (sampler.mjs)."""
    target = midi(e)
    n = _number(e.get("n")) or 0.0
    found = find(name, math.floor(n + 0.5), target)  # getSoundIndex: Math.round, then mod
    if found is None:
        return None
    sample, root = found
    header = sample.header
    if header is None or header.channels > 2:
        return None
    speed = _number(e.get("speed"))
    speed = 1.0 if speed is None else speed
    if e.get("unit") == "c":  # Strudel's OSC output divides speed by cps; superdough does not
        speed *= _number(e.get("cps")) or 0.5
    if speed == 0:
        return SILENT
    transpose = target - (36 if root is None else root)
    rate = abs(speed) * 2 ** (transpose / 12)
    if e.get("unit") == "c":
        rate *= header.duration
    begin, end = (_number(e.get(k)) for k in ("begin", "end"))
    begin, end = 0.0 if begin is None else begin, 1.0 if end is None else end
    loop = bool(e.get("loop"))
    if e.get("clip") is None and e.get("loop") is None and e.get("release") is None:
        duration = (end - begin) * header.duration / rate  # the slice
    filter_controls = filters(e, float(e.get("delta") or 0.0))  # from onset to the hap's end
    env, life = _envelope(e, SAMPLE_ENVELOPE, duration)
    frames = header.frames
    offset = begin * frames
    if not loop:
        # When the buffer runs out, onended releases the whole chain, cutting filter tails.
        life = min(life, (frames - offset) / (rate * header.sample_rate))
    loop_begin, loop_end = (_number(e.get(k)) for k in ("loopBegin", "loopEnd"))
    loop_begin, loop_end = (
        (0.0 if loop_begin is None else loop_begin),
        (1.0 if loop_end is None else loop_end),
    )
    if speed < 0:  # superdough plays a reversed copy of the buffer
        start = frames - 1 - offset
        loop_begin, loop_end = 1 - loop_end, 1 - loop_begin
        rate = -rate
    else:
        start = offset
    controls = {
        "rate": rate,
        "start": start,
        "loop": float(loop),
        "loopStart": loop_begin * frames,
        "loopEnd": loop_end * frames,
        "frames": frames,
        **env,
    }
    cut = _number(e.get("cut"))
    return Voice(
        f"dough_sample{header.channels}",
        controls,
        header.channels,
        _gate(e, life),
        sample=sample,
        cut=None if cut is None else int(cut),
        nudge=_number(e.get("nudge")) or 0.0,
        filters=filter_controls,
    )


# -- SynthDefs --------------------------------------------------------------------------


def _pink() -> Any:
    """Paul Kellet's filter as superdough applies it (noise.mjs), as one-pole sections."""
    w = WhiteNoise.ar()
    total: Any = w * 0.5362 + Delay1.ar(source=w) * 0.115926
    for coef, gain in [
        (0.99886, 0.0555179), (0.99332, 0.0750759), (0.969, 0.153852),
        (0.8665, 0.3104856), (0.55, 0.5329522), (-0.7616, -0.016898),
    ]:  # fmt: skip
        # OnePole: y = (1 - |c|) x + c y[-1]; superdough's stage is y = c y[-1] + g w.
        total = total + OnePole.ar(source=w * (gain / (1 - abs(coef))), coefficient=coef)
    return total * 0.11


def _source(name: str, freq: Any, density: Any) -> Any:
    if name == "sine":
        return SinOsc.ar(frequency=freq)
    if name == "triangle":
        return LFTri.ar(frequency=freq) * WAVE_LEVEL["triangle"]
    if name == "square":
        return Pulse.ar(frequency=freq, width=0.5) * WAVE_LEVEL["square"]
    if name == "sawtooth":
        return Saw.ar(frequency=freq) * WAVE_LEVEL["sawtooth"]
    if name == "white":
        return WhiteNoise.ar()
    if name == "brown":
        return OnePole.ar(source=WhiteNoise.ar(), coefficient=1 / 1.02)
    if name == "pink":
        return _pink()
    return Dust2.ar(density=density * 0.01 * SampleRate.ir())  # crackle


def _envelope_controls() -> dict[str, Any]:
    controls = {f"l{i}": control(0.0, "ir") for i in (1, 2, 3, 4)}
    controls |= {f"t{i}": control(0.0, "ir") for i in (1, 2, 3, 4)}
    return controls


def _env(b: SynthDefBuilder) -> Any:
    return EnvGen.ar(
        envelope=Envelope(
            amplitudes=[0.0, b["l1"], b["l2"], b["l3"], b["l4"]],
            durations=[b["t1"], b["t2"], b["t3"], b["t4"]],
            curves=["linear"],
        ),
        done_action=2,
    )


def _sample(channels: int) -> SynthDef:
    """An ``AudioBufferSourceNode``: linear interpolation, silent outside the buffer."""
    controls = {
        "out": control(0, "ir"), "buf": control(0, "ir"), "rate": control(1.0, "ir"),
        "start": control(0.0, "ir"), "loop": control(0.0, "ir"), "loopStart": control(0.0, "ir"),
        "loopEnd": control(0.0, "ir"), "frames": control(1.0, "ir"), **_envelope_controls(),
    }  # fmt: skip
    with SynthDefBuilder(**controls) as b:
        straight = b["start"] + Sweep.ar(rate=b["rate"] * BufSampleRate.ir(buffer_id=b["buf"]))
        looped = Phasor.ar(
            trigger=Impulse.ar(frequency=0),
            rate=b["rate"] * BufRateScale.ir(buffer_id=b["buf"]),
            start=b["loopStart"],
            stop=b["loopEnd"],
            reset_pos=b["start"],
        )
        phase = Select.ar(selector=b["loop"], sources=[straight, looped])
        inside = ((phase >= 0) * (phase <= b["frames"] - 1)).max_(b["loop"])
        sig = BufRd.ar(
            channel_count=channels, buffer_id=b["buf"], phase=phase, loop=0, interpolation=2
        )
        env = _env(b) * inside
        Out.ar(bus=b["out"], source=[x * env for x in sig] if channels > 1 else sig * env)
    return b.build(name=f"dough_sample{channels}")


def _biquad(kind: str, x: Any, freq: Any, q: Any) -> Any:
    """``BiquadFilterNode`` (Web Audio's coefficients), recomputed every sample.

    Q is in dB for lowpass and highpass, linear for bandpass.
    """
    w0 = 2 * math.pi * freq.clip(0, SampleRate.ir() * 0.5) / SampleRate.ir()
    cos, sin = w0.cos_(), w0.sin_()
    alpha = sin / (2 * (q if kind == "bpf" else 10 ** (q / 20)))
    if kind == "lpf":
        b0, b1, b2 = (1 - cos) / 2, 1 - cos, (1 - cos) / 2
    elif kind == "hpf":
        b0, b1, b2 = (1 + cos) / 2, -(1 + cos), (1 + cos) / 2
    else:
        b0, b1, b2 = alpha, alpha * 0, -alpha
    a0 = 1 + alpha
    # Direct form I, as Chrome's Biquad: y = b0 x + b1 x[-1] + b2 x[-2] - a1 y[-1] - a2 y[-2].
    # The structures agree for fixed coefficients and diverge while the cutoff moves.
    ff = (b0 * x + b1 * Delay1.ar(source=x) + b2 * Delay2.ar(source=x)) / a0
    one, zero = DC.ar(source=1), DC.ar(source=0)  # all coefficients audio rate: SOS_next_a
    return SOS.ar(source=ff, a_0=one, a_1=zero, a_2=zero, b_1=2 * cos / a0, b_2=(alpha - 1) / a0)


def _filter(kind: str, channels: int) -> SynthDef:
    """A filter in a voice's chain, rewriting the synth bus in place; ``24db`` is two in series."""
    controls = {"out": control(0, "ir"), "q": control(1.0, "ir"), "stages": control(1.0, "ir")}
    controls |= {f"f{i}": control(1000.0, "ir") for i in range(5)}
    controls |= {f"ft{i}": control(0.0, "ir") for i in (1, 2, 3, 4)}
    with SynthDefBuilder(**controls) as b:
        freq = EnvGen.ar(
            envelope=Envelope(
                amplitudes=[b[f"f{i}"] for i in range(5)],
                durations=[b[f"ft{i}"] for i in (1, 2, 3, 4)],
                curves=["exponential"],
            )
        )
        x = In.ar(bus=b["out"], channel_count=channels)
        xs = list(x) if channels > 1 else [x]
        once = [_biquad(kind, c, freq, b["q"]) for c in xs]
        twice = [_biquad(kind, c, freq, b["q"]) for c in once]
        y = [Select.ar(selector=b["stages"] > 1, sources=[o, t]) for o, t in zip(once, twice)]
        ReplaceOut.ar(bus=b["out"], source=y if channels > 1 else y[0])
    return b.build(name=f"dough_{kind}{channels}")


def _gate_def(channels: int, num_channels: int) -> SynthDef:
    """``StereoPannerNode`` (Web Audio's equal-power rules for mono and stereo input), postgain."""
    controls = {
        "in": control(0, "ir"), "out": control(0, "ir"), "level": control(DEFAULT_GAIN),
        "pan": control(0.5), "panned": control(0.0, "ir"), "life": control(1.0, "ir"),
        "cut_gate": control(1.0),
    }  # fmt: skip
    with SynthDefBuilder(**controls) as b:
        half_pi = math.pi / 2
        if channels == 1:
            x = In.ar(bus=b["in"], channel_count=1)
            angle = b["pan"].clip(0, 1) * half_pi
            left = x * (b["panned"] * angle.cos_() + (1 - b["panned"]))
            right = x * (b["panned"] * angle.sin_() + (1 - b["panned"]))
        else:
            in_l, in_r = In.ar(bus=b["in"], channel_count=2)
            p = (b["pan"] * 2 - 1).clip(-1, 1)
            low = (p + 1) * half_pi  # p <= 0: the right channel moves left
            high = p * half_pi  # p > 0: the left channel moves right
            left = Select.ar(selector=p > 0, sources=[in_l + in_r * low.cos_(), in_l * high.cos_()])
            right = Select.ar(
                selector=p > 0, sources=[in_r * low.sin_(), in_r + in_l * high.sin_()]
            )
        life = EnvGen.ar(
            envelope=Envelope(amplitudes=[1.0, 1.0], durations=[b["life"]]), done_action=14
        )
        cut = EnvGen.ar(  # a cut group fades the previous sound over 10 ms (sampler.mjs)
            envelope=Envelope(amplitudes=[1.0, 1.0, 0.0], durations=[0.0, 0.01], release_node=1),
            gate=b["cut_gate"],
            done_action=14,
        )
        level = b["level"] * life * cut
        stereo = [left * level, right * level]
        OffsetOut.ar(bus=b["out"], source=stereo if num_channels > 1 else left * level)
        ReplaceOut.ar(bus=b["in"], source=[DC.ar(source=0)] * channels)  # clear for the next voice
    return b.build(name=f"dough_gate{channels}_{num_channels}")


def synthdefs(num_channels: int) -> list[SynthDef]:
    defs = []
    for name in [*dict.fromkeys(OSCILLATORS.values()), *sorted(NOISES)]:
        controls = {"out": control(0, "ir"), "amp": control(SOURCE_LEVEL), "freq": control(440.0)}
        controls |= _envelope_controls()
        controls["density"] = control(CRACKLE_DENSITY, "ir")
        with SynthDefBuilder(**controls) as b:
            Out.ar(bus=b["out"], source=_source(name, b["freq"], b["density"]) * _env(b) * b["amp"])
        defs.append(b.build(name=f"dough_{name}"))
    defs += [_sample(1), _sample(2), _gate_def(1, num_channels), _gate_def(2, num_channels)]
    defs += [_filter(kind, c) for kind, *_ in FILTERS for c in (1, 2)]
    return defs


__all__ = [
    "NOISES",
    "OSCILLATORS",
    "SILENT",
    "Voice",
    "adsr_values",
    "envelope_points",
    "filter_type",
    "filters",
    "frequency",
    "midi",
    "synthdefs",
    "voice",
]
