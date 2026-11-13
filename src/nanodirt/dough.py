"""superdough's voice for Strudel events (docs/dev/superdough-mapping.md).

A voice is a group in the orbit, as in SuperDirt: a source synth writes the
enveloped source to the orbit's synth bus; ``dough_gate`` applies gain, pan
and postgain, adds the result to the dry bus and clears the synth bus. The
envelope's breakpoints depend on the event's duration, so they are computed
here per event and sent as controls.
"""

from __future__ import annotations

import math
import struct
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from nanosynth.enums import EnvelopeShape
from nanosynth.envelopes import Envelope, EnvGen
from nanosynth.synthdef import SynthDef, SynthDefBuilder, control
from nanosynth.ugens import (
    A2K,
    DC,
    SOS,
    BlockSize,
    BufRd,
    BufSampleRate,
    Delay1,
    Delay2,
    Dust2,
    In,
    Latch,
    LFSaw,
    OffsetOut,
    OnePole,
    Out,
    Phasor,
    ReplaceOut,
    SampleRate,
    Select,
    SinOsc,
    Sweep,
    WhiteNoise,
)

from .library import Sample
from .soundfonts import GM, Zone
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
# measured RMS 1.68-1.69 times that of scsynth's Saw and Pulse, which peak at 0.5. The sawtooth
# and square here peak at 1: RMS 1/sqrt(3) and 1 (tests/test_dough.py).
WAVE_LEVEL = {"triangle": 1.0, "square": 0.845, "sawtooth": 0.843}

# (name, index, midi) -> (sample, root note of a pitched bank or None)
FindSample = Callable[[str, int, float], "tuple[Sample, float | None] | None"]
# (gm_ instrument, preset index, midi) -> (decoded zone, zone), None until loaded
FindFont = Callable[[str, int, float], "tuple[Sample, Zone] | None"]
FONT_LEVEL = 0.3  # fontloader.mjs: getParamADSR(node.gain, ..., 0, 0.3, ...)
FONT_NOTE = 48  # fontloader.mjs: note = 'c3'


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
    # FM modulators, SynthDef and controls, from the one modulating the source; the player adds
    # fmbus to them and fm=1, fmbus to the source
    modulators: list[tuple[str, dict[str, float]]] = field(default_factory=list)


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
    e: Mapping[str, Any],
    defaults: tuple[float, float, float, float],
    duration: float,
    peak: float = 1.0,
) -> tuple[dict[str, float], float]:
    adsr = adsr_values(
        tuple(_number(e.get(k)) for k in ("attack", "decay", "sustain", "release")), defaults
    )
    levels, times = envelope_points(adsr, duration, 0.0, peak)
    controls = {f"l{i + 1}": v for i, v in enumerate(levels)}
    controls |= {f"t{i + 1}": v for i, v in enumerate(times)}
    return controls, duration + adsr[3] + 0.01  # the source stops at end + release + 0.01


def _gain(e: Mapping[str, Any]) -> float:
    """superdough's gain stage: ``gain * velocity``."""
    gain, velocity = _number(e.get("gain")), _number(e.get("velocity"))
    return (DEFAULT_GAIN if gain is None else gain) * (1.0 if velocity is None else velocity)


def _gate(e: Mapping[str, Any], life: float) -> dict[str, float]:
    """``superdough()``'s voice chain after the source: gain, pan, postgain."""
    postgain, pan, dry = (_number(e.get(k)) for k in ("postgain", "pan", "dry"))
    return {
        "level": _gain(e) * (1.0 if postgain is None else postgain),
        "pan": 0.5 if pan is None else pan,
        "panned": 0.0 if pan is None else 1.0,  # superdough adds a panner only when pan is set
        "life": life,
        "dry": 1.0 if dry is None else dry,  # scales the direct output, not the sends
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


PITCH_ENVELOPE = (0.2, 0.001, 1.0, 0.001)  # getPitchEnvelope's defaults
PITCH_KEYS = ("pattack", "pdecay", "psustain", "prelease")
SEGMENTS = 8  # of an exact envelope: 4 stages, each breakpoint bridged by a 1-sample segment
Stage = tuple[float, float, float, float, str]  # start, end (s), from, to, curve


def param_adsr(
    adsr: tuple[float, float, float, float], duration: float, lo: float, hi: float, linear: bool
) -> list[Stage]:
    """``getParamADSR``'s ramps from *lo* to *hi*, ending *duration* after the onset."""
    if not linear:  # exponential ramps cannot reach 0
        lo, hi = lo or 0.001, hi or 0.001
    levels, times = envelope_points(adsr, duration, lo, hi)
    if not linear:
        levels = [v or 0.001 for v in levels]
    stages, begin = [], 0.0
    for v0, v1, t in zip([lo, *levels], levels, times):
        # exponentialRampToValueAtTime between levels of opposite sign holds the start level
        curve = "linear" if linear else "exponential" if v0 * v1 > 0 else "hold"
        stages.append((begin, begin + t, v0, v1, curve))
        begin += t
    return stages


def exact_envelope(stages: list[Stage], sample_rate: float, prefix: str) -> dict[str, float]:
    """EnvGen controls that output *stages*' value at every sample, as Web Audio evaluates it.

    EnvGen starts each segment on a whole sample, ramps over the fractional length and carries
    the remainder into the next. A breakpoint between samples would shift the curve; a pitch
    curve's integral, the phase or playback position, would drift by up to a few frames.
    """
    final = stages[-1][3]

    def stage(n: float) -> Stage:
        return next((s for s in stages if n / sample_rate < s[1]), (0.0, 1.0, final, final, "hold"))

    def value(n: int) -> float:
        start, end, v0, v1, curve = stage(n)
        x = (n / sample_rate - start) / (end - start)
        if curve == "hold":
            return v0
        return v0 + (v1 - v0) * x if curve == "linear" else v0 * (v1 / v0) ** x

    breaks = {0}
    for _, end, *_ in stages:
        last = math.ceil(end * sample_rate) - 1  # the last sample before the breakpoint
        breaks |= {last, last + 1} if last >= 0 else set()
    points = sorted(breaks)
    out = {f"{prefix}0": (level := _f32(value(0)))}
    segments = list(zip(points, points[1:]))
    residual = 0.0  # EnvGen's: the fraction of a sample the last segment did not play
    for i in range(SEGMENTS):
        if i < len(segments):
            a, b = segments[i]
            exp = b - a > 1 and stage((a + b) / 2)[4] == "exponential"
            target, samples = value(b), b - a
        else:
            exp, target, samples = False, level, 0
        seconds = _f32((samples - residual + 0.5) / sample_rate)
        span = seconds * sample_rate + residual
        whole = max(1, int(span))
        spread, residual = max(1.0, span) / whole, span - whole
        sent = level * (target / level) ** spread if exp else level + (target - level) * spread
        if i == SEGMENTS - 1:
            sent = target  # where EnvGen stays when it ends
        out[f"{prefix}{i + 1}"] = sent
        out[f"{prefix}t{i + 1}"] = seconds
        out[f"{prefix}s{i + 1}"] = float(EnvelopeShape.EXPONENTIAL if exp else EnvelopeShape.LINEAR)
        level = target
    return out


def pitch(e: Mapping[str, Any], duration: float, sample_rate: float) -> dict[str, float]:
    """``getVibratoOscillator`` and ``getPitchEnvelope``: the source's detune, in cents."""
    out: dict[str, float] = {}
    vib = _number(e.get("vib"))
    if vib is not None and vib > 0:
        vibmod = _number(e.get("vibmod"))
        out |= {"vib": vib, "vibmod": 100 * (0.5 if vibmod is None else vibmod)}
    if all(e.get(k) is None for k in (*PITCH_KEYS, "penv")):
        return out
    penv = _number(e.get("penv"))
    cents = 100 * (1.0 if penv is None else penv)
    pcurve = e.get("pcurve")
    linear = pcurve is None or pcurve == 0 or pcurve == "0"  # curves[pcurve]; others exponential
    adsr = adsr_values(tuple(_number(e.get(k)) for k in PITCH_KEYS), PITCH_ENVELOPE)
    anchor = _number(e.get("panchor"))
    anchor = adsr[2] if anchor is None else anchor
    stages = param_adsr(adsr, duration, -cents * anchor, cents - cents * anchor, linear)
    return out | exact_envelope(stages, sample_rate, "p")


FM_WAVES = ["sine", "square", "sawtooth", "triangle"]  # OscillatorNode types
FM_NOISE_DENSITY = 2.0  # mod(): getNoiseBuffer(type, 2) passes 2 as the density
MAX_FM = 8


def modulators(
    e: Mapping[str, Any], freq: float, duration: float, sample_rate: float
) -> list[tuple[str, dict[str, float]]]:
    """``applyFM``'s chain: modulator *k* adds to modulator *k - 1*'s frequency, 1 to the carrier's.

    Returns each modulator's SynthDef and controls, from 1. The matrix controls (``fmi13``) have
    no control function in Strudel and are not read.
    """
    out: list[tuple[str, dict[str, float]]] = []
    for k in range(1, MAX_FM + 1):
        suffix = "" if k == 1 else str(k)
        amount = _number(e.get(f"fmi{suffix}"))
        if not amount:  # a modulator not reaching the carrier changes nothing
            break
        ratio = _number(e.get(f"fmh{suffix}"))
        wave = e.get(f"fmwave{suffix}")
        if wave not in NOISES:  # the oscillator type setter ignores other names: sine
            wave = wave if wave in FM_WAVES else "sine"
        mod_freq = freq * (1.0 if ratio is None else ratio)
        controls = {"freq": mod_freq, "amount": amount * mod_freq}
        adsr = tuple(
            _number(e.get(f"fm{s}{suffix}")) for s in ("attack", "decay", "sustain", "release")
        )
        if any(v is not None for v in adsr):
            linear = (e.get(f"fmenv{suffix}") or "exp") != "exp"
            stages = param_adsr(adsr_values(adsr, SAMPLE_ENVELOPE), duration, 0.0, 1.0, linear)
            controls |= exact_envelope(stages, sample_rate, "e")
        if wave == "crackle":
            controls["density"] = FM_NOISE_DENSITY
        if out:
            out[-1][1]["fm"] = 1.0  # this one modulates the last
        out.append((f"dough_fm_{wave}", controls))
    return out


def _f32(x: float) -> float:
    """*x* as a control value: OSC sends float32."""
    return float(struct.unpack("<f", struct.pack("<f", x))[0])


def sound_name(e: Mapping[str, Any]) -> str | None:
    """The sound superdough looks up: ``bank_s``, else ``s`` lowercased, oscillator aliases resolved.

    ``s`` is ``triangle`` when unset (``defaultDefaultValues``).
    """
    s = e.get("s", "triangle")
    if not isinstance(s, str):
        return None
    bank = e.get("bank")
    return OSCILLATORS.get(s.lower(), s.lower()) if not bank else f"{bank}_{s}"


DISTORTIONS = [  # helpers.mjs distortionAlgorithms, in order: distorttype indexes them
    "scurve", "soft", "hard", "cubic", "diode", "asym", "fold", "sinefold", "chebyshev",
]  # fmt: skip
MAX_DISTORT = 30.0  # expm1(30) = 1.1e13; above, the shapes are saturated and float32 overflows


def distortion(e: Mapping[str, Any]) -> tuple[str, dict[str, float]] | None:
    """``getDistortion``: the waveshaper's SynthDef kind and controls, or None without ``distort``.

    Raises ValueError for a ``distorttype`` superdough cannot resolve, a negative or
    fractional number: its worklet then throws and the voice is silent.
    """
    amount = _number(e.get("distort"))
    if amount is None:
        return None
    algo = e.get("distorttype")
    if isinstance(algo, str):
        name = algo if algo in DISTORTIONS else DISTORTIONS[0]  # superdough logs and uses scurve
    else:
        index = 0.0 if algo is None else _number(algo)
        if index is None or index < 0 or index != math.floor(index):
            raise ValueError(f"no distorttype {algo!r}")
        name = DISTORTIONS[int(index) % len(DISTORTIONS)]
    vol = _number(e.get("distortvol"))
    k = math.expm1(min(amount, MAX_DISTORT))  # the worklet's shape
    controls = {"post": min(max(1.0 if vol is None else vol, 0.001), 1.0), "k": k}
    lk = math.log1p(k)  # __squash(log1p(k)); JavaScript divides -1 by 0 to -Infinity
    t = (lk / (1 + lk) if lk != -1 else -math.inf) if name in ("cubic", "diode", "asym") else 0.0
    if name == "cubic":
        controls["t"] = t
    elif name in ("diode", "asym"):
        g, bias = 1 + 2 * k, 0.07 * t
        sech = 0.0 if abs(g * bias) > 700 else 1 / math.cosh(g * bias)
        controls |= {
            "bias": bias,
            "norm": 1 / max(1e-8, (1 if name == "asym" else 2) * g * sech * sech),
        }
    elif name == "chebyshev":
        controls["kl"] = 10 * math.log1p(k)
    if not all(math.isfinite(v) for v in controls.values()):
        raise ValueError(f"distort {amount} has no finite shape")
    return f"distort_{name}", controls


def voice(
    e: Mapping[str, Any],
    num_channels: int,
    find_sample: FindSample | None = None,
    find_font: FindFont | None = None,
    sample_rate: float = 48000.0,
) -> Voice | None:
    """superdough's voice for a Strudel event, or None if superdough has no such sound."""
    v = _voice(e, find_sample, find_font, sample_rate)
    if v is None or v is SILENT:
        return v
    try:
        shaper = distortion(e)
    except ValueError:
        return SILENT
    if shaper is not None:  # after the gain stage and filters, before pan and postgain
        kind, controls = shaper
        v.filters.append((kind, {"gain": _gain(e), **controls}))
        postgain = _number(e.get("postgain"))
        v.gate["level"] = 1.0 if postgain is None else postgain
    return v


def _voice(
    e: Mapping[str, Any],
    find_sample: FindSample | None,
    find_font: FindFont | None,
    sample_rate: float,
) -> Voice | None:
    name = sound_name(e)
    if name is None:
        return None
    duration = float(e.get("delta") or 0.0)
    if name in OSCILLATORS.values() or name in NOISES:
        env, life = _envelope(e, SOURCE_ENVELOPE, duration)
        controls = {"amp": SOURCE_LEVEL, "freq": frequency(e), **env}
        fm: list[tuple[str, dict[str, float]]] = []
        if name in OSCILLATORS.values():
            controls |= pitch(e, duration, sample_rate)
            fm = modulators(e, controls["freq"], duration, sample_rate)
        if name == "crackle":
            controls["density"] = _number(e.get("density")) or CRACKLE_DENSITY
        return Voice(
            f"dough_{name}",
            controls,
            1,
            _gate(e, life),
            filters=filters(e, duration),
            modulators=fm,
        )
    if find_font is not None and name in GM:
        return _font_voice(e, duration, find_font, sample_rate)
    if find_sample is None:
        return None
    return _sample_voice(e, duration, find_sample, sample_rate)


def sample_key(e: Mapping[str, Any]) -> tuple[str, int, float] | None:
    """The sound, index and key a sample event looks up, or None if it plays no sample."""
    name = sound_name(e)
    if name is None or name in OSCILLATORS.values() or name in NOISES or name in GM:
        return None
    n = _number(e.get("n")) or 0.0
    return name, math.floor(n + 0.5), midi(e)  # getSoundIndex: Math.round, then mod


def _sample_voice(
    e: Mapping[str, Any], duration: float, find: FindSample, sample_rate: float
) -> Voice | None:
    """``onTriggerSample`` and ``getSampleBufferSource`` (sampler.mjs)."""
    key = sample_key(e)
    assert key is not None
    target = key[2]
    found = find(*key)
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
        **pitch(e, duration, sample_rate),
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


def font_key(e: Mapping[str, Any]) -> tuple[str, int, float] | None:
    """The ``gm_`` instrument, preset index and key an event plays, or None if not a soundfont."""
    name = sound_name(e)
    if name not in GM:
        return None
    n = _number(e.get("n")) or 0.0
    return name, math.floor(n + 0.5), midi(e, FONT_NOTE)  # getSoundIndex: Math.round


def _font_voice(e: Mapping[str, Any], duration: float, find: FindFont, sample_rate: float) -> Voice:
    """``registerSoundfonts`` and ``getFontBufferSource`` (fontloader.mjs).

    Silent until the zone is loaded.
    """
    key = font_key(e)
    assert key is not None
    target = key[2]
    found = find(*key)
    if found is None:
        return SILENT
    sample, zone = found
    header = sample.header
    if header is None or header.channels > 2:
        return SILENT
    env, life = _envelope(e, SAMPLE_ENVELOPE, duration, FONT_LEVEL)
    scale = header.sample_rate / zone.sample_rate  # loop points are in the zone's frames
    controls = {
        "rate": zone.playback_rate(target),
        "start": 0.0,
        "loop": float(zone.loops),
        "loopStart": zone.loop_start * scale,
        "loopEnd": zone.loop_end * scale,
        "frames": header.frames,
        **env,
        **pitch(e, duration, sample_rate),
    }
    return Voice(
        f"dough_sample{header.channels}",
        controls,
        header.channels,
        _gate(e, life),
        sample=sample,
        filters=filters(e, duration),
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


def _dpw(phase: Any, freq: Any, before: float) -> Any:
    """A sawtooth from its phase in [-1, 1), differentiated parabolic wave: less aliasing.

    *before* is the phase's square one sample before the start.
    """
    step = freq * (4 / SampleRate.ir())  # d(phase^2) per sample is 2 phase * 2 freq / rate
    square = phase * phase
    slope = (square - Delay1.ar(source=square, x1=before)) / step
    return Select.ar(selector=abs(step) > 1e-4, sources=[phase, slope])


def _fade(freq: Any) -> Any:
    """Chrome's oscillator table at *freq*: the fundamental fades out over the third of an
    octave below the Nyquist frequency, as tables 400 cents apart drop it. Measured."""
    return ((SampleRate.ir() * 0.5 / abs(freq)).log2_() * 3).clip(0, 1)


def _source(name: str, freq: Any, density: Any) -> Any:
    """A superdough source, frequency read every sample."""
    if name == "sine":
        return SinOsc.ar(frequency=freq) * _fade(freq)
    if name == "triangle":  # not LFTri: its phase runs away below 0 Hz
        triangle = (LFSaw.ar(frequency=freq) * 2).fold2(1)
        return triangle * (WAVE_LEVEL["triangle"] * _fade(freq))
    if name in ("square", "sawtooth"):  # not Saw and Pulse: they read freq once per block
        phase = LFSaw.ar(frequency=freq)
        saw = _dpw(phase, freq, 0.0)
        if name == "square":
            shifted = phase + 1 - 2 * (phase >= 0)  # half a period on, starting at -1
            saw = saw - _dpw(shifted, freq, 1.0)
        return saw * (WAVE_LEVEL[name] * _fade(freq))
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


def _exact_controls(prefix: str, level: float) -> dict[str, Any]:
    """exact_envelope()'s controls, constant at *level* by default."""
    controls = {f"{prefix}0": control(level, "ir")}
    for i in range(1, SEGMENTS + 1):
        controls |= {f"{prefix}{i}": control(level, "ir"), f"{prefix}t{i}": control(0.0, "ir")}
        controls[f"{prefix}s{i}"] = control(float(EnvelopeShape.LINEAR), "ir")
    return controls


def _exact_env(b: SynthDefBuilder, prefix: str) -> Any:
    envelope: list[Any] = [b[f"{prefix}0"], SEGMENTS, -99, -99]
    for i in range(1, SEGMENTS + 1):
        envelope += [b[f"{prefix}{i}"], b[f"{prefix}t{i}"], b[f"{prefix}s{i}"], 0]
    return EnvGen.ar(envelope=envelope)


def _pitch_controls() -> dict[str, Any]:
    return {"vib": control(0.0, "ir"), "vibmod": control(0.0, "ir"), **_exact_controls("p", 0.0)}


def _detune(b: SynthDefBuilder, quantum: bool = False) -> Any:
    """The frequency ratio from ``vib`` and the pitch envelope.

    *quantum* holds each value for 128 frames, as an ``AudioBufferSourceNode``'s k-rate
    detune, at control rate: Sweep steps in double precision at control rate, float32 at
    audio rate.
    """
    cents = SinOsc.ar(frequency=b["vib"]) * b["vibmod"] + _exact_env(b, "p")
    if quantum:  # a voice starts on a control block, so blocks and render quanta align
        first = Phasor.kr(stop=128 / BlockSize.ir()) < 0.5
        cents = Latch.kr(source=A2K.kr(source=cents), trigger=first)
    return (cents * (math.log(2) / 1200)).exp_()


def _sample(channels: int) -> SynthDef:
    """An ``AudioBufferSourceNode``: linear interpolation, silent outside the buffer.

    A looping source plays from ``start`` to the loop end before it wraps, as Web Audio does;
    a negative rate plays the buffer backwards with mirrored loop points. A source that is not
    looping frees the voice when it leaves the buffer: ``onended`` releases superdough's chain,
    cutting filter tails.
    """
    controls = {
        "out": control(0, "ir"), "buf": control(0, "ir"), "rate": control(1.0, "ir"),
        "start": control(0.0, "ir"), "loop": control(0.0, "ir"), "loopStart": control(0.0, "ir"),
        "loopEnd": control(0.0, "ir"), "frames": control(1.0, "ir"), **_envelope_controls(),
        **_pitch_controls(),
    }  # fmt: skip
    with SynthDefBuilder(**controls) as b:
        # u: the position in the direction of play, increasing from sign(rate) * start
        sign = b["rate"].sign()
        speed = abs(b["rate"]) * _detune(b, quantum=True) * BufSampleRate.ir(buffer_id=b["buf"])
        u = b["start"] * sign + Sweep.ar(rate=speed)
        lo = (b["loopStart"] * sign).min_(b["loopEnd"] * sign)
        hi = (b["loopStart"] * sign).max_(b["loopEnd"] * sign)
        wrapped = lo + (u - lo) % (hi - lo)
        phase = Select.ar(selector=(u >= hi) * b["loop"], sources=[u, wrapped]) * sign
        # Past the last frame, Chrome interpolates toward 0 until the position reaches the
        # buffer's length; BufRd holds the last frame. Reversed, the first frame is the last.
        edge = (b["frames"] - phase).min_(phase + 1).clip(0, 1).max_(b["loop"])
        inside = edge > 0
        EnvGen.ar(  # frees the voice's group when *inside* falls
            envelope=Envelope(amplitudes=[1.0, 1.0, 1.0], durations=[0.0, 0.0], release_node=1),
            gate=inside,
            done_action=14,
        )
        sig = BufRd.ar(
            channel_count=channels, buffer_id=b["buf"], phase=phase, loop=0, interpolation=2
        )
        env = _env(b) * edge
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


def _shape(name: str, x: Any, b: Any) -> Any:
    """One sample through a distortion curve (helpers.mjs), *b* holding its controls."""
    k = b["k"]
    if name == "scurve":
        return (1 + k) * x / (1 + k * abs(x))
    if name == "soft":
        return (x * (1 + k)).tanh_()
    if name == "hard":
        return (x * (1 + k)).clip(-1, 1)
    if name in ("fold", "sinefold"):
        y = 1 - abs((x * (1 + 0.5 * k) + 1) % 4 - 2)  # sc's % is a floored modulo, as _mod
        return (y * (math.pi / 2)).sin_() if name == "sinefold" else y
    if name == "cubic":
        third = b["t"] / 3
        return ((x - third * x.cubed()) / (1 - third) * (1 + k)).tanh_()
    if name in ("diode", "asym"):
        g, bias = 1 + 2 * k, b["bias"]
        neg = ((bias if name == "asym" else bias - x) * g).tanh_()
        return ((((x + bias) * g).tanh_() - neg) * b["norm"] * (1 + k)).tanh_()
    # chebyshev: the worklet's recurrence starts from (x, 1), so step i holds T(i - 1); its
    # even steps add the odd polynomials. Past |x| = 2 the sum is past tanh's range in double
    # precision, and T61 overflows float32 past about 4.
    xc = x.clip(-2, 2)
    two_x = 2 * xc
    y, older, old = x, xc, 1.0
    for i in range(2, 64):
        older, old = old, two_x * old - older
        if i % 2 == 0:
            y = y + b["c"][i] * old
    return (y * (1 + b["kl"] / 20)).tanh_()


def _distort(name: str, channels: int) -> SynthDef:
    """``distort-processor``: the gain stage, then a curve, then ``distortvol``, in place."""
    controls = {"out": control(0, "ir"), "gain": control(DEFAULT_GAIN, "ir")}
    controls |= {c: control(0.0, "ir") for c in ("k", "t", "bias", "norm", "kl")}
    controls["post"] = control(1.0, "ir")
    with SynthDefBuilder(**controls) as b:
        shape = {c: b[c] for c in controls}
        if name == "chebyshev":  # the worklet's weights of the even steps, computed once
            shape["c"] = {i: (b["kl"] * (1.3 / i)).min_(2) for i in range(2, 64, 2)}
        x = In.ar(bus=b["out"], channel_count=channels)
        xs = list(x) if channels > 1 else [x]
        y = [_shape(name, c * b["gain"], shape) * b["post"] for c in xs]
        ReplaceOut.ar(bus=b["out"], source=y if channels > 1 else y[0])
    return b.build(name=f"dough_distort_{name}{channels}")


def _gate_def(channels: int, num_channels: int) -> SynthDef:
    """``StereoPannerNode`` (Web Audio's equal-power rules for mono and stereo input), postgain."""
    controls = {
        "in": control(0, "ir"), "out": control(0, "ir"), "level": control(DEFAULT_GAIN),
        "pan": control(0.5), "panned": control(0.0, "ir"), "life": control(1.0, "ir"),
        "cut_gate": control(1.0), "dry": control(1.0), "delayBus": control(0, "ir"),
        "roomBus": control(0, "ir"), "delaySend": control(0.0), "roomSend": control(0.0),
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
        direct = [x * b["dry"] for x in stereo]
        OffsetOut.ar(bus=b["out"], source=direct if num_channels > 1 else direct[0])
        # superdough sends to the orbit's delay and reverb from after postgain
        OffsetOut.ar(bus=b["delayBus"], source=[x * b["delaySend"] for x in stereo])
        OffsetOut.ar(bus=b["roomBus"], source=[x * b["roomSend"] for x in stereo])
        ReplaceOut.ar(bus=b["in"], source=[DC.ar(source=0)] * channels)  # clear for the next voice
    return b.build(name=f"dough_gate{channels}_{num_channels}")


def _modulated(b: SynthDefBuilder, freq: Any, ratio: Any = 1.0) -> Any:
    """(*freq* plus the FM bus when ``fm`` is set) times *ratio*, clamped to the Nyquist as Web
    Audio clamps an oscillator's computed frequency."""
    fm = Select.ar(selector=b["fm"], sources=[DC.ar(source=0), In.ar(bus=b["fmbus"])])
    nyquist = SampleRate.ir() * 0.5
    return ((freq + fm) * ratio).clip(-nyquist, nyquist)


def _modulator(wave: str) -> SynthDef:
    """``applyFM``'s modulator: *wave* at ``freq`` plus its input, times its envelope and amount.

    It reads the next modulator's output from the FM bus, if any, and replaces it with its own.
    """
    controls = {
        "fmbus": control(0, "ir"), "freq": control(440.0, "ir"), "amount": control(0.0, "ir"),
        "fm": control(0.0, "ir"), "density": control(FM_NOISE_DENSITY, "ir"),
        **_exact_controls("e", 1.0),
    }  # fmt: skip
    with SynthDefBuilder(**controls) as b:
        x = _source(wave, _modulated(b, b["freq"]), b["density"])
        ReplaceOut.ar(bus=b["fmbus"], source=x * _exact_env(b, "e") * b["amount"])
    return b.build(name=f"dough_fm_{wave}")


def synthdefs(num_channels: int) -> list[SynthDef]:
    defs = []
    for name in [*dict.fromkeys(OSCILLATORS.values()), *sorted(NOISES)]:
        controls = {"out": control(0, "ir"), "amp": control(SOURCE_LEVEL), "freq": control(440.0)}
        controls |= _envelope_controls()
        controls["density"] = control(CRACKLE_DENSITY, "ir")
        pitched = name not in NOISES  # superdough's noise has no detune or FM
        if pitched:
            controls |= {"fmbus": control(0, "ir"), "fm": control(0.0, "ir"), **_pitch_controls()}
        with SynthDefBuilder(**controls) as b:
            freq = _modulated(b, b["freq"], _detune(b)) if pitched else b["freq"]
            Out.ar(bus=b["out"], source=_source(name, freq, b["density"]) * _env(b) * b["amp"])
        defs.append(b.build(name=f"dough_{name}"))
    defs += [_modulator(wave) for wave in [*FM_WAVES, *sorted(NOISES)]]
    defs += [_sample(1), _sample(2), _gate_def(1, num_channels), _gate_def(2, num_channels)]
    defs += [_filter(kind, c) for kind, *_ in FILTERS for c in (1, 2)]
    defs += [_distort(name, c) for name in DISTORTIONS for c in (1, 2)]
    return defs


__all__ = [
    "NOISES",
    "OSCILLATORS",
    "SILENT",
    "Voice",
    "DISTORTIONS",
    "adsr_values",
    "distortion",
    "envelope_points",
    "filter_type",
    "filters",
    "font_key",
    "pitch",
    "frequency",
    "midi",
    "synthdefs",
    "voice",
]
