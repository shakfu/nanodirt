"""superdough's synth sources for Strudel events (docs/dev/superdough-mapping.md).

Oscillators and noise, with superdough's envelope, gain and pitch rules. The
envelope's breakpoints depend on the event's duration, so they are computed
here per event and sent as controls.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nanosynth.envelopes import Envelope, EnvGen
from nanosynth.synthdef import SynthDef, SynthDefBuilder, control
from nanosynth.ugens import (
    Delay1,
    Dust2,
    LFTri,
    OnePole,
    Out,
    Pulse,
    SampleRate,
    Saw,
    SinOsc,
    WhiteNoise,
)

from .strudel import note_to_midi

OSCILLATORS = {
    "sine": "sine", "sin": "sine", "triangle": "triangle", "tri": "triangle",
    "square": "square", "sqr": "square", "sawtooth": "sawtooth", "saw": "sawtooth",
}  # fmt: skip
NOISES = {"white", "pink", "brown", "crackle"}
SOURCE_LEVEL = 0.3  # synth.mjs: "turn down"
SOURCE_ENVELOPE = (0.001, 0.05, 0.6, 0.01)  # synth.mjs defaults for oscillators and noise
DEFAULT_GAIN = 0.8
CRACKLE_DENSITY = 0.02  # getNoiseOscillator's default; synth.mjs reads value.density directly
# Web Audio normalizes band-limited waves to peak 1, Gibbs overshoot (~1.18) included;
# Saw and Pulse peak at 0.5. Measured RMS ratio 1.68-1.69 (tests/test_dough.py).
WAVE_LEVEL = {"triangle": 1.0, "square": 1.69, "sawtooth": 1.69}


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
    adsr: tuple[float, float, float, float], duration: float
) -> tuple[list[float], list[float]]:
    """``getParamADSR`` from 0 to 1, linear: levels and times of 4 segments.

    The fourth segment is the release, to 0. A note shorter than attack or
    attack + decay stops its ramp where the full envelope would be at its end.
    """
    attack, decay, sustain, release = adsr

    def at(time: float) -> float:
        if attack > time:
            return time / attack
        return 1.0 + (time - attack) * (sustain - 1.0) / decay

    if attack > duration:
        v = at(duration)
        return [v, v, v], [duration, 0.0, 0.0, release]
    if attack + decay > duration:
        v = at(duration)
        return [1.0, v, v], [attack, duration - attack, 0.0, release]
    return [1.0, sustain, sustain], [attack, decay, duration - attack - decay, release]


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


def voice(e: Mapping[str, Any], num_channels: int) -> tuple[str, dict[str, float]] | None:
    """The SynthDef and controls for a Strudel event with a superdough source, else None."""
    s = e.get("s")
    if not isinstance(s, str) or e.get("bank"):
        return None
    name = OSCILLATORS.get(s.lower(), s.lower())
    if name not in OSCILLATORS.values() and name not in NOISES:
        return None
    adsr = adsr_values(
        tuple(_number(e.get(k)) for k in ("attack", "decay", "sustain", "release")),
        SOURCE_ENVELOPE,
    )
    levels, times = envelope_points(adsr, float(e.get("delta") or 0.0))
    gain, velocity, postgain = (_number(e.get(k)) for k in ("gain", "velocity", "postgain"))
    amp = SOURCE_LEVEL * (DEFAULT_GAIN if gain is None else gain)
    amp *= (1.0 if velocity is None else velocity) * (1.0 if postgain is None else postgain)
    controls = {"amp": amp, "freq": frequency(e)}
    controls |= {f"l{i + 1}": v for i, v in enumerate(levels)}
    controls |= {f"t{i + 1}": v for i, v in enumerate(times)}
    if name == "crackle":
        controls["density"] = _number(e.get("density")) or CRACKLE_DENSITY
    return f"dough_{name}{num_channels}", controls


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


def synthdefs(num_channels: int) -> list[SynthDef]:
    defs = []
    for name in [*dict.fromkeys(OSCILLATORS.values()), *sorted(NOISES)]:
        controls = {"out": control(0, "ir"), "amp": control(0.0), "freq": control(440.0)}
        controls |= {f"l{i}": control(0.0, "ir") for i in (1, 2, 3)}
        controls |= {f"t{i}": control(0.0, "ir") for i in (1, 2, 3, 4)}
        controls["density"] = control(CRACKLE_DENSITY, "ir")
        with SynthDefBuilder(**controls) as b:
            env = Envelope(
                amplitudes=[0.0, b["l1"], b["l2"], b["l3"], 0.0],
                durations=[b["t1"], b["t2"], b["t3"], b["t4"]],
                curves=["linear"],
            )
            gate = EnvGen.ar(envelope=env, done_action=2)
            sig = _source(name, b["freq"], b["density"]) * gate * b["amp"]
            Out.ar(bus=b["out"], source=[sig] * min(num_channels, 2))
        defs.append(b.build(name=f"dough_{name}{num_channels}"))
    return defs


__all__ = [
    "NOISES",
    "OSCILLATORS",
    "adsr_values",
    "envelope_points",
    "frequency",
    "synthdefs",
    "voice",
]
