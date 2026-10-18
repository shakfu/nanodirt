"""SuperDirt's instruments (default-synths.scd, library/default-synths-extra.scd).

Each function returns the SynthDef SuperDirt `650a1d6` builds for
*num_channels*, under the same name and control names. sclang arguments
without a default are control-rate controls defaulting to 0.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from nanosynth.envelopes import Envelope, EnvGen
from nanosynth.synthdef import SynthDef, SynthDefBuilder, control
from nanosynth.ugens import (
    BPF,
    BRF,
    DC,
    HPF,
    LPF,
    RLPF,
    AmpCompA,
    BAllPass,
    Blip,
    BPeakEQ,
    BrownNoise,
    Clip,
    CombC,
    CombL,
    ControlDur,
    ControlRate,
    Crackle,
    Decay,
    DelayC,
    DelayN,
    Dseq,
    Dust,
    Duty,
    ExpRand,
    GVerb,
    Impulse,
    In,
    InFeedback,
    Latch,
    LeakDC,
    LFDNoise0,
    LFGauss,
    LFNoise2,
    LFPar,
    LFPulse,
    LFSaw,
    LFTri,
    Limiter,
    Line,
    LocalIn,
    LocalOut,
    Mix,
    MoogFF,
    MouseX,
    MouseY,
    NumOutputBuses,
    Out,
    Phasor,
    PitchShift,
    PMOsc,
    Poll,
    Pulse,
    Rand,
    Resonz,
    Sanitize,
    Saw,
    SelectX,
    SinOsc,
    SinOscFB,
    Splay,
    SplayAz,
    Sweep,
    TIRand,
    VarSaw,
    WhiteNoise,
    XFade2,
    XLine,
)
from nanosynth.ugens.sc3 import AY, BMoog, MdaPiano, MembraneHexagon, SawDPW
from nanosynth.ugens.sc3_custom import FM7, PulseDPW

from .ugens import dirt_freq_scale, dirt_pan

TWO_PI = 2 * math.pi
Controls = list[tuple[str, Any, str]]


# -- sclang conveniences -----------------------------------------------------------


def env_linen(
    a: Any = 0.01, s: Any = 1.0, r: Any = 1.0, level: Any = 1.0, curve: Any = "linear"
) -> Envelope:
    return Envelope(amplitudes=[0, level, level, 0], durations=[a, s, r], curves=[curve])


def env_perc(
    attack: Any = 0.01, release: Any = 1.0, level: Any = 1.0, curve: Any = -4.0
) -> Envelope:
    return Envelope(amplitudes=[0, level, 0], durations=[attack, release], curves=[curve])


def env_pairs(pairs: Sequence[tuple[float, Any]], curve: Any) -> Envelope:
    times = [b[0] - a[0] for a, b in zip(pairs, pairs[1:])]
    return Envelope(amplitudes=[p[1] for p in pairs], durations=times, curves=[curve])


def env_sine(dur: Any = 1.0, level: Any = 1.0) -> Envelope:
    return Envelope(amplitudes=[0, level, 0], durations=[dur * 0.5, dur * 0.5], curves=["sine"])


def env_new(levels: Sequence[Any], times: Sequence[Any], curve: Any = "linear") -> Envelope:
    return Envelope(amplitudes=list(levels), durations=list(times), curves=[curve])


def env_ar(env: Envelope, done_action: int = 0, **kwargs: Any) -> Any:
    return EnvGen.ar(envelope=env, done_action=done_action, **kwargs)


def env_kr(env: Envelope, done_action: int = 0, **kwargs: Any) -> Any:
    return EnvGen.kr(envelope=env, done_action=done_action, **kwargs)


def vsum(xs: Sequence[Any]) -> Any:
    """sclang ``Array.sum``: a left-to-right chain of ``+``."""
    total = xs[0]
    for x in xs[1:]:
        total = total + x
    return total


def selectx_kr(which: Any, table: Sequence[Any]) -> Any:
    """``SelectX.kr(which, [alternative, ...])`` with alternatives of any nesting.

    nanosynth's Select flattens nested sources, so expand per leaf, as sclang's
    multichannel expansion does.
    """
    first = table[0]
    if isinstance(first, (list, tuple)):
        return [selectx_kr(which, [alt[i] for alt in table]) for i in range(len(first))]
    return SelectX.kr(which=which, sources=list(table))


@contextmanager
def _builder(controls: Controls) -> Iterator[SynthDefBuilder]:
    with SynthDefBuilder(**{n: control(v, r) for n, v, r in controls}) as b:
        yield b


def _kr(*names: str, **defaults: Any) -> Controls:
    """sclang function arguments: control rate, 0 unless given a default."""
    return [(n, defaults.get(n, 0), "kr") for n in names]


def _pan_controls(num_channels: int) -> Controls:
    from .synthdefs import _pan_controls as pan_controls

    return pan_controls(num_channels)


def _pan(b: SynthDefBuilder, n: int, sound: Any, mul: Any = 1.0) -> Any:
    args = {name: b[name] for name, _, _ in _pan_controls(n)}
    return dirt_pan(sound, n, b["pan"], args, mul)


def _fs(b: SynthDefBuilder, speed: Any, accelerate: Any, sustain: Any) -> Any:
    return dirt_freq_scale(speed, accelerate, sustain, b["speedFreq"])


SPEED_FREQ: Controls = [("speedFreq", 1, "ir")]  # DirtFreqScale's \speedFreq.ir(1)


def _def(
    name: str,
    num_channels: int,
    controls: Controls,
    body: Callable[[SynthDefBuilder], Any],
    speed_freq: bool = False,
) -> SynthDef:
    extra = SPEED_FREQ if speed_freq else []
    with _builder(controls + extra + _pan_controls(num_channels)) as b:
        body(b)
    return b.build(name=name)


# -- default-synths.scd ------------------------------------------------------------


def imp(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        sustain, begin = b["sustain"], b["begin"]
        env = env_ar(env_perc(0.01, 0.99, 1, -1), done_action=2, time_scale=sustain)
        phase = Line.kr(start=begin, stop=b["end"], duration=sustain)
        rate = (begin + 1) * (b["speed"] + Sweep.kr(trigger=1, rate=b["accelerate"]))
        f = rate.linexp(0, 1, 1, b["freq"])
        harmonics = ExpRand.ir(minimum=80, maximum=118) * phase
        sound = vsum(
            [Blip.ar(frequency=f * k, harmonic_count=harmonics) for k in (1, 1.25, 1.51, 1.42)]
        )
        Out.ar(bus=b["out"], source=_pan(b, n, sound, env))

    return _def(
        "imp", n,
        _kr("out", "sustain", "freq", "speed", "begin", "end", "pan", "accelerate", "offset",
            sustain=1, freq=440, speed=1, end=1),
        body,
    )  # fmt: skip


def _psin_body(b: SynthDefBuilder, n: int, freq: Any, modfreq: Any) -> None:
    sustain = b["sustain"]
    AmpCompA.kr(frequency=freq)  # computed but unused, as in SuperDirt
    env = env_ar(env_perc(0.002, sustain, 1, -1), done_action=2)
    phase = Line.ar(start=b["begin"], stop=b["end"], duration=sustain)
    b["speed"] + Sweep.kr(trigger=1, rate=b["accelerate"])  # unused, as in SuperDirt
    mods = [
        SinOsc.ar(frequency=modfreq * k, phase=0) * ((1 - phase) * 10 * env / k) + 0.5 * math.pi
        for k in range(1, 6)
    ]
    sound = vsum([SinOsc.ar(frequency=freq, phase=m) for m in mods])
    Out.ar(bus=b["out"], source=_pan(b, n, sound, env))


def psin(n: int) -> SynthDef:
    return _def(
        "psin", n,
        _kr("out", "sustain", "freq", "speed", "begin", "end", "pan", "accelerate", "offset", "modfreq",
            sustain=1, freq=440, speed=1, end=1, modfreq=40),
        lambda b: _psin_body(b, n, b["freq"], b["modfreq"]),
    )  # fmt: skip


def pmsin(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        freq = MouseX.kr(minimum=30, maximum=80).round_(1) + b["freq"]
        modfreq = MouseY.kr(minimum=1, maximum=200, warp=1)
        _psin_body(b, n, freq, modfreq)

    return _def(
        "pmsin", n,
        _kr("out", "sustain", "freq", "speed", "begin", "end", "pan", "accelerate", "offset",
            sustain=1, freq=440, speed=1, end=1),
        body,
    )  # fmt: skip


def _fade_env(b: SynthDefBuilder) -> Any:
    fade, sustain = b["inputFadeTime"], b["sustain"]
    return env_kr(env_linen(fade, sustain - 2 * fade, fade))


def sound_in(n: int, stereo: bool) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        env = _fade_env(b)
        offset = NumOutputBuses.ir()
        if stereo:  # SoundIn.ar([0, 1] + soundin): one In.ar per bus
            sound: Any = [
                In.ar(bus=offset + (k + b["soundin"]), channel_count=1) * env for k in (0, 1)
            ]
        else:
            sound = In.ar(bus=offset + b["soundin"], channel_count=1) * env
        Out.ar(bus=b["out"], source=_pan(b, n, sound))

    return _def(
        "in" if stereo else "in1", n,
        _kr("out", "soundin", "sustain", "pan", "inputFadeTime", sustain=1, inputFadeTime=0.03),
        body,
    )  # fmt: skip


def dirt_from(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        env = _fade_env(b)
        sound = InFeedback.ar(bus=b["in"], channel_count=n)
        sound = [s * env for s in sound] if n > 1 else sound * env
        Out.ar(bus=b["out"], source=_pan(b, n, sound))

    return _def(
        "dirt_from",
        n,
        _kr("out", "sustain", "in", "pan", "inputFadeTime", sustain=1, inputFadeTime=0.03),
        body,
    )


def inr(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        env = _fade_env(b)
        Line.ar(start=b["begin"], stop=b["end"], duration=b["sustain"])  # unused, as in SuperDirt
        rate = (
            (b["begin"] + 1)
            * (b["speed"] + Sweep.kr(trigger=1, rate=b["accelerate"]))
            * b["freq"]
            / 440
        )
        offset = NumOutputBuses.ir()
        sound = [In.ar(bus=offset + k, channel_count=1) * env for k in (0, 1)]
        sound = [
            PitchShift.ar(
                source=s,
                window_size=0.1,
                pitch_ratio=rate,
                pitch_dispersion=0.02,
                time_dispersion=0.01,
            )
            for s in sound
        ]
        Out.ar(bus=b["out"], source=_pan(b, n, sound))

    return _def(
        "inr", n,
        _kr("out", "sustain", "freq", "speed", "begin", "end", "pan", "accelerate", "offset", "inputFadeTime",
            sustain=1, freq=440, speed=1, end=1, inputFadeTime=0.03),
        body,
    )  # fmt: skip


def gabor(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        sound = SinOsc.ar(frequency=b["freq"], phase=0.5 * math.pi)
        env = LFGauss.ar(duration=b["sustain"], width=b["width"], loop=0).range(0, 1)
        Out.ar(bus=b["out"], source=_pan(b, n, env * sound))

    return _def(
        "gabor",
        n,
        _kr("out", "sustain", "freq", "pan", "width", sustain=0.03, freq=440, width=0.3),
        body,
    )


def dirac(n: int) -> SynthDef:
    return _def(
        "dirac", n, _kr("out", "sustain", "pan", sustain=0.03),
        lambda b: Out.ar(bus=b["out"], source=_pan(b, n, Impulse.ar(frequency=0))),
    )  # fmt: skip


def cyclo(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        sustain, begin, speed = b["sustain"], b["begin"], b["speed"]
        env = env_ar(env_perc(0.01, 0.99, 1, -1), done_action=2, time_scale=sustain)
        phase = Line.kr(start=begin, stop=1 - b["end"], duration=sustain)
        rate = (begin + 1) * (speed + Sweep.kr(trigger=1, rate=b["accelerate"]))
        # (cycle * speed) % ((1..16) * ratio + phase) + 1 * freq * rate, left to right
        freqs = [
            ((b["cycle"] * speed) % (k * b["ratio"] + phase) + 1) * b["freq"] * rate
            for k in range(1, 17)
        ]
        sound = vsum([SinOsc.ar(frequency=f) for f in freqs]) / 16  # .mean
        Out.ar(bus=b["out"], source=_pan(b, n, sound, env))

    return _def(
        "cyclo", n,
        _kr("out", "freq", "sustain", "speed", "begin", "end", "pan", "accelerate", "cycle", "ratio",
            sustain=1, speed=1, end=1, ratio=1),
        body,
    )  # fmt: skip


def debug(n: int) -> SynthDef:
    defaults = {"begin": -1, "end": -1, "delta": -1, "sustain": -1, "unitDuration": -1, "cycle": -1,
                "speed": 1, "accelerate": 0}  # fmt: skip
    with _builder([(k, v, "kr") for k, v in defaults.items()]) as b:
        trig = Impulse.kr(frequency=0)
        for name in [
            "cycle",
            "begin",
            "end",
            "delta",
            "sustain",
            "unitDuration",
            "speed",
            "accelerate",
        ]:
            Poll.kr(trigger=trig, source=b[name], label=name)
        Poll.kr(trigger=trig, source=DC.kr(source=0), label="\n\n")
    return b.build(name="debug")


# -- default-synths-extra.scd ------------------------------------------------------


def supermandolin(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        sustain, freq, detune = b["sustain"], b["freq"], b["detune"]
        env = env_ar(env_linen(0.002, 0.996, 0.002, 1, -3), done_action=2, time_scale=sustain)
        sound = Decay.ar(
            source=Impulse.ar(frequency=0, phase=0) * 0.1, decay_time=0.1 * freq.cpsmidi() / 69
        )
        sound = sound * WhiteNoise.ar()
        pitch = freq * _fs(b, b["speed"], b["accelerate"], sustain)
        sound = CombL.ar(
            source=sound, maximum_delay_time=0.05, delay_time=pitch.reciprocal() * (1 - detune / 100),
            decay_time=sustain,
        ) + CombL.ar(
            source=sound, maximum_delay_time=0.05, delay_time=pitch.reciprocal() * (1 + detune / 100),
            decay_time=sustain,
        )  # fmt: skip
        Out.ar(bus=b["out"], source=_pan(b, n, sound, env))

    return _def(
        "supermandolin", n,
        _kr("out", "sustain", "pan", "accelerate", "freq", "detune", "speed", sustain=1, detune=0.2, speed=1),
        body, speed_freq=True,
    )  # fmt: skip


GONG = [1.000, 2.081, 3.414, 3.893, 4.995, 5.954, 6.819, 8.280, 8.722, 8.882, 10.868, 11.180, 11.754,
        13.710, 13.715, 15.057, 15.484, 16.469, 16.817, 18.628]  # fmt: skip


def supergong(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        sustain, freq = b["sustain"], b["freq"]
        tscale = [100.0 / freq / (f ** (2 - b["decay"].clip(0, 2))) for f in GONG]
        ascale = [f ** b["voice"].clip(0, 4) for f in GONG]
        fs = _fs(b, b["speed"], b["accelerate"], sustain)
        parts = [
            env_ar(
                env_perc(0.01 * tscale[i], 0.5 * tscale[i], 0.2 * ascale[i]), time_scale=sustain * 5
            )
            * SinOsc.ar(frequency=freq * GONG[i] * fs)
            for i in range(15)
        ]
        Out.ar(bus=b["out"], source=_pan(b, n, Mix.new(parts)))

    return _def(
        "supergong", n,
        _kr("out", "sustain", "pan", "accelerate", "freq", "voice", "decay", "speed", sustain=1, decay=1, speed=1),
        body, speed_freq=True,
    )  # fmt: skip


def superpiano(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        sustain, velocity = b["sustain"], b["velocity"]
        env = env_ar(env_linen(0.002, 0.996, 0.002, 1, -3), done_action=2, time_scale=sustain)
        sound = MdaPiano.ar(
            freq=b["freq"] * _fs(b, b["speed"], b["accelerate"], sustain) + 0.01,
            vel=velocity * 100, hard=0.8 * velocity, decay=0.1 * sustain, tune=0.5, random=0.05,
            stretch=b["detune"], muffle=0.8 * b["muffle"], stereo=b["stereo"],
        )  # fmt: skip
        Out.ar(bus=b["out"], source=_pan(b, n, sound, env))

    return _def(
        "superpiano", n,
        _kr("out", "sustain", "pan", "velocity", "detune", "muffle", "stereo", "freq", "accelerate", "speed",
            sustain=1, velocity=1, detune=0.1, muffle=1, stereo=0.2, freq=440, speed=1),
        body, speed_freq=True,
    )  # fmt: skip


def superhex(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        sustain, freq = b["sustain"], b["freq"]
        env = env_ar(env_linen(0.02, 0.96, 0.02, 1, -3), done_action=2, time_scale=sustain)
        tension = 0.05 * freq / 400 * _fs(b, b["speed"], b["accelerate"], sustain)
        loss = 1.0 - (0.01 * b["rate"] / freq)
        excitation = Decay.ar(source=Impulse.ar(frequency=0, phase=0), decay_time=0.01)
        sound = MembraneHexagon.ar(excitation=excitation, tension=tension, loss=loss)
        Out.ar(bus=b["out"], source=_pan(b, n, sound, env))

    return _def(
        "superhex", n,
        _kr("out", "rate", "sustain", "pan", "freq", "accelerate", "speed", rate=1, sustain=1, speed=1),
        body, speed_freq=True,
    )  # fmt: skip


def superkick(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        sustain = b["sustain"]
        env = env_ar(env_linen(0.01, 0, 0.5, 1, -3), done_action=2, time_scale=sustain)
        sound = SinOsc.ar(frequency=(b["n"] - 25.5).midicps())
        clickdur = 0.02 * sustain * b["decay"]
        fs = _fs(b, b["speed"], b["accelerate"], clickdur)
        click = LPF.ar(source=WhiteNoise.ar(), frequency=1500 * b["pitch1"] * fs)
        sound = sound + click * Line.ar(start=1, stop=0, duration=clickdur)
        Out.ar(bus=b["out"], source=_pan(b, n, sound, env))

    return _def(
        "superkick", n,
        _kr("out", "sustain", "pan", "accelerate", "n", "pitch1", "decay", "speed",
            sustain=1, pitch1=1, decay=1, speed=1),
        body, speed_freq=True,
    )  # fmt: skip


def super808(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        sustain = b["sustain"]
        note = ((b["n"] > 0) * b["n"]) + ((b["n"] < 1) * 3)
        freq = (note * 10).midicps() * _fs(b, b["speed"], b["accelerate"], sustain)
        env = env_ar(env_linen(0.01, 0, 1, 1, -3), done_action=2, time_scale=sustain)
        sweep = XLine.ar(
            start=freq.expexp(10, 2000, 1000, 8000), stop=freq, duration=0.025 / b["rate"]
        )
        sound = LPF.ar(source=SinOscFB.ar(frequency=sweep, feedback=b["voice"]), frequency=9000)
        Out.ar(bus=b["out"], source=_pan(b, n, sound, env))

    return _def(
        "super808", n,
        _kr("out", "rate", "sustain", "pan", "voice", "n", "speed", "accelerate", rate=1, sustain=1, speed=1),
        body, speed_freq=True,
    )  # fmt: skip


def superhat(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        sustain = b["sustain"]
        env = env_ar(env_linen(0.01, 0, 0.3, 1, -3), done_action=2, time_scale=sustain)
        freq = 2000 * _fs(b, b["speed"], b["accelerate"], sustain) * (b["n"] / 5 + 1).wrap(0.5, 2)
        sound = HPF.ar(source=LPF.ar(source=WhiteNoise.ar(), frequency=3 * freq), frequency=freq)
        Out.ar(bus=b["out"], source=_pan(b, n, sound, env))

    return _def(
        "superhat", n, _kr("out", "sustain", "pan", "accelerate", "n", "speed", sustain=1, speed=1),
        body, speed_freq=True,
    )  # fmt: skip


def supersnare(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        sustain = b["sustain"]
        env = env_ar(env_linen(0.01, 0, 0.6, 1, -3), done_action=2, time_scale=sustain)
        freq = 100 * _fs(b, b["speed"], b["accelerate"], sustain) * (b["n"] / 5 + 1).wrap(0.5, 2)
        tone = LPF.ar(
            source=Pulse.ar(frequency=freq),
            frequency=Line.ar(start=1030, stop=30, duration=0.2 * sustain),
        )
        noise = BPF.ar(source=HPF.ar(source=WhiteNoise.ar(), frequency=500), frequency=1500)
        sound = tone + noise * Line.ar(start=1, stop=0, duration=0.2 * b["decay"])
        Out.ar(bus=b["out"], source=_pan(b, n, sound, env))

    return _def(
        "supersnare", n,
        _kr("out", "sustain", "pan", "accelerate", "n", "decay", "speed", sustain=1, decay=1, speed=1),
        body, speed_freq=True,
    )  # fmt: skip


def superclap(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        sustain = b["sustain"]
        spr = 0.005 * b["delay"]
        env = env_ar(env_linen(0.01, 0, 0.6, 1, -3), done_action=2, time_scale=sustain)
        sound = BPF.ar(
            source=LPF.ar(source=WhiteNoise.ar(), frequency=7500 * b["pitch1"]),
            frequency=1500 * b["pitch1"],
        )
        parts = [
            sound
            * 0.5
            * env_ar(
                env_new([0, 0, 1, 0], [spr * (i ** (b["n"].clip(0, 5) + 1)), 0, 0.04 / b["rate"]])
            )
            for i in range(4)
        ]
        Out.ar(bus=b["out"], source=_pan(b, n, Mix.new(parts), env))

    return _def(
        "superclap", n,
        _kr("out", "rate", "sustain", "pan", "n", "delay", "pitch1", rate=1, sustain=1, delay=1, pitch1=1),
        body,
    )  # fmt: skip


def supersiren(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        sustain = b["sustain"]
        freq = b["freq"] * _fs(b, b["speed"], b["accelerate"], sustain)
        env = env_ar(env_linen(0.05, 0.9, 0.05, 1, -2), done_action=2, time_scale=sustain)
        glide = env_kr(env_linen(0.25, 0.5, 0.25, 3, 0), done_action=2, time_scale=sustain)
        sound = VarSaw.ar(
            frequency=freq * (1.0 + glide),
            initial_phase=0,
            width=Line.kr(start=0.05, stop=1, duration=sustain),
        )
        Out.ar(bus=b["out"], source=_pan(b, n, sound, env))

    return _def(
        "supersiren", n, _kr("out", "sustain", "pan", "freq", "accelerate", "speed", sustain=1, speed=1),
        body, speed_freq=True,
    )  # fmt: skip


MOOG_CONTROLS = (
    "out", "rate", "decay", "sustain", "pan", "accelerate", "freq",
    "voice", "semitone", "resonance", "lfo", "pitch1", "speed",
)  # fmt: skip
MOOG_DEFAULTS = {
    "rate": 1,
    "sustain": 1,
    "voice": 0.5,
    "semitone": 12,
    "resonance": 0.2,
    "lfo": 1,
    "pitch1": 1,
    "speed": 1,
}


def _moog_env(b: SynthDefBuilder, attack: float = 0.05, curve: float = -3) -> Any:
    d = b["decay"]
    pairs = [(0, 0), (attack, 1), (0.2, 1 - d), (0.95, 1 - d), (1, 0)]
    return env_ar(env_pairs(pairs, curve), done_action=2, time_scale=b["sustain"])


def _lfo_freqs(b: SynthDefBuilder, basefreq: Any) -> tuple[Any, Any]:
    lfof1 = (basefreq * 10 * b["pitch1"]).min_(22000)
    lfof2 = (lfof1 * (b["lfo"] + 1)).min_(22000)
    return lfof1, lfof2


def supersquare(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        env = _moog_env(b)
        basefreq = b["freq"] * _fs(b, b["speed"], b["accelerate"], b["sustain"])
        basefreq2 = basefreq / (2 ** (b["semitone"] / 12))
        lfof1, lfof2 = _lfo_freqs(b, basefreq)
        voice = b["voice"]
        sound = 0.7 * Pulse.ar(frequency=basefreq, width=voice) + 0.3 * Pulse.ar(
            frequency=basefreq2, width=voice
        )
        sound = MoogFF.ar(
            source=sound,
            frequency=SinOsc.ar(frequency=basefreq / 64 * b["rate"], phase=0).range(lfof1, lfof2),
            gain=b["resonance"] * 4,
        )
        Out.ar(bus=b["out"], source=_pan(b, n, sound.tanh_() * 2, env))

    return _def("supersquare", n, _kr(*MOOG_CONTROLS, **MOOG_DEFAULTS), body, speed_freq=True)


def supersaw(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        env = _moog_env(b)
        basefreq = b["freq"] * _fs(b, b["speed"], b["accelerate"], b["sustain"])
        basefreq2 = basefreq * (2 ** (b["semitone"] / 12))
        lfof1, lfof2 = _lfo_freqs(b, basefreq)
        voice = b["voice"]
        saws = Mix.new(
            [SawDPW.ar(freq=basefreq * ((i - 1) * voice / 50 + 1), iphase=0) for i in range(3)]
        )
        sound = MoogFF.ar(
            source=0.5 * saws + 0.5 * LFTri.ar(frequency=basefreq2, initial_phase=voice),
            frequency=LFTri.ar(frequency=basefreq / 64 * b["rate"], initial_phase=0.5).range(
                lfof1, lfof2
            ),
            gain=b["resonance"] * 4,
        )
        Out.ar(bus=b["out"], source=_pan(b, n, sound.tanh_() * 2, env))

    return _def("supersaw", n, _kr(*MOOG_CONTROLS, **MOOG_DEFAULTS), body, speed_freq=True)


def superpwm(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        sustain, voice = b["sustain"], b["voice"]
        env = _moog_env(b)
        env2 = env_ar(
            env_pairs([(0, 0.1), (0.1, 1), (0.4, 0.5), (0.9, 0.2), (1, 0.2)], -3),
            time_scale=sustain / b["rate"],
        )
        basefreq = b["freq"] * _fs(b, b["speed"], b["accelerate"], sustain)
        basefreq2 = basefreq / (2 ** (b["semitone"] / 12))
        lfof1, lfof2 = _lfo_freqs(b, basefreq)

        def pdpw(f: Any) -> Any:
            return PulseDPW.ar(freq=f)

        sound = 0.7 * pdpw(basefreq) * DelayC.ar(
            source=pdpw(basefreq), maximum_delay_time=0.2,
            delay_time=Line.kr(start=0, stop=voice, duration=sustain) / basefreq,
        )  # fmt: skip
        sound = 0.3 * pdpw(basefreq2) * DelayC.ar(
            source=pdpw(basefreq2), maximum_delay_time=0.2,
            delay_time=Line.kr(start=0.1, stop=0.1 + voice, duration=sustain) / basefreq,
        ) + sound  # fmt: skip
        sound = MoogFF.ar(
            source=sound,
            frequency=SinOsc.ar(frequency=basefreq / 32 * b["rate"], phase=0).range(lfof1, lfof2),
            gain=b["resonance"] * 4,
        )
        sound = MoogFF.ar(source=sound, frequency=(env2 * lfof2 * 1.1).min_(22000), gain=3)
        Out.ar(bus=b["out"], source=_pan(b, n, sound.tanh_() * 5, env))

    return _def("superpwm", n, _kr(*MOOG_CONTROLS, **MOOG_DEFAULTS), body, speed_freq=True)


COMPARATOR = [1.000, 2.188, 5.091, 8.529, 8.950, 9.305, 13.746, 14.653, 19.462, 22.003, 24.888, 25.991,
              26.085, 30.509, 33.608, 35.081, 40.125, 42.023, 46.527, 49.481]  # fmt: skip


def supercomparator(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        env = _moog_env(b)
        basefreq = b["freq"] * _fs(b, b["speed"], b["accelerate"], b["sustain"])
        lfo, voice = b["lfo"], b["voice"]
        sound = VarSaw.ar(
            frequency=basefreq,
            initial_phase=0,
            width=Line.ar(start=0, stop=1, duration=b["sustain"]),
        )
        freqlist = [f ** (voice / 5) for f in COMPARATOR]
        sound = Splay.ar(
            source=[sound > LFTri.ar(frequency=freqlist[i]) for i in range(16)], spread=1
        )
        cutoff = (
            b["pitch1"] * 4 * basefreq
            + SinOsc.ar(frequency=basefreq / 64 * b["rate"], phase=0) * (lfo * basefreq / 2)
            + LFNoise2.ar(frequency=1) * (lfo * basefreq)
        )
        gain = LFNoise2.ar(frequency=0) * 0.1 + 4 * b["resonance"]
        sound = MoogFF.ar(source=sound, frequency=cutoff, gain=gain)
        Out.ar(bus=b["out"], source=_pan(b, n, 0.5 * sound, env))

    names = tuple(c for c in MOOG_CONTROLS if c != "semitone")
    return _def(
        "supercomparator",
        n,
        _kr(*names, **{**MOOG_DEFAULTS, "resonance": 0.5}),
        body,
        speed_freq=True,
    )


def superchip(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        sustain, voice, slide = b["sustain"], b["voice"], b["slide"]
        env = env_ar(env_linen(0.01, 0.98, 0.01, 1, -1), done_action=2, time_scale=sustain)
        sweep = Line.kr(start=-1, stop=1 + (2 * b["rate"] - 2), duration=sustain)
        basefreq = b["freq"] + (slide * 100 * sweep).wrap2(slide * 100)
        basefreq = basefreq * _fs(b, b["speed"], b["accelerate"], sustain)
        va = (voice < 0.5) * 15
        vb = (((2 * voice) % 1) < 0.5) * 15
        vc = (((4 * voice) % 1) < 0.5) * 15

        def tone(f: Any) -> Any:  # AY.freqtotone
            return 110300 / (f - 0.5)

        sound = AY.ar(
            tonea=tone(basefreq), toneb=tone(b["pitch2"] * basefreq), tonec=tone(b["pitch3"] * basefreq),
            vola=va, volb=vb, volc=vc,
        ) / 2  # fmt: skip
        Out.ar(bus=b["out"], source=_pan(b, n, sound.tanh_() * 2, env))

    return _def(
        "superchip", n,
        _kr("out", "sustain", "pan", "freq", "rate", "slide", "pitch2", "pitch3", "accelerate", "voice", "speed",
            sustain=1, rate=1, pitch2=2, pitch3=3, speed=1),
        body, speed_freq=True,
    )  # fmt: skip


def supernoise(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        sustain, freq = b["sustain"], b["freq"]
        env = env_ar(env_linen(0.01, 0.98, 0.01, 1, -1), done_action=2, time_scale=sustain)
        acc = b["accelerate"] * freq * 4
        basefreq = freq * 8 + (
            acc * Line.kr(start=-1, stop=1 + (2 * b["rate"] - 2), duration=sustain)
        ).wrap2(acc)
        ffreq = basefreq * 5 * b["pitch1"] * Line.kr(start=1, stop=1 + b["slide"], duration=sustain)
        ffreq = ffreq.clip(60, 20000)
        sound = XFade2.ar(
            in_a=LFDNoise0.ar(frequency=basefreq.min_(22000)) * 0.5,
            in_b=XFade2.ar(
                in_a=BrownNoise.ar() * 0.5, in_b=WhiteNoise.ar() * 0.5, pan=basefreq.cpsmidi() / 127
            ),
            pan=2 * b["voice"] - 1,
        )
        sound = HPF.ar(
            source=BMoog.ar(source=sound, freq=ffreq, q=b["resonance"], mode=3), frequency=20
        )
        sound = sound.clip(-1, 1) * 0.3
        Out.ar(bus=b["out"], source=_pan(b, n, sound, env))

    return _def(
        "supernoise", n,
        _kr("out", "sustain", "pan", "freq", "accelerate", "slide", "pitch1", "rate", "resonance", "voice",
            sustain=1, pitch1=1, rate=1),
        body,
    )  # fmt: skip


def superfork(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        sustain = b["sustain"]
        freq = b["freq"] * _fs(b, b["speed"], b["accelerate"], sustain)
        env = env_ar(env_linen(0.001, 0.998, 0.001), done_action=2, time_scale=sustain)
        partials = [
            0.2 * SinOsc.ar(frequency=k * freq) * XLine.ar(start=a, stop=z, duration=sustain * d)
            for k, a, z, d in zip(
                [1.0, 6.1, 16.7, 20.1],
                [0.3, 1, 1.3, 2],
                [1e-4, 1e-7, 1e-6, 1e-7],
                [4, 4, 0.5, 0.333],
            )
        ]
        Out.ar(bus=b["out"], source=_pan(b, n, partials, env))

    return _def(
        "superfork", n, _kr("out", "sustain", "pan", "freq", "accelerate", "speed", sustain=1, speed=1),
        body, speed_freq=True,
    )  # fmt: skip


HAMMOND = [
    [1, 4, 5, 4, 3, 1, 0, 0, 0], [0, 0, 8, 0, 4, 0, 0, 0, 0], [0, 7, 8, 1, 2, 0, 0, 0, 0],
    [0, 0, 7, 8, 8, 8, 8, 7, 2], [4, 0, 5, 5, 4, 5, 3, 3, 6], [8, 8, 5, 3, 2, 4, 5, 8, 8],
    [8, 8, 8, 0, 0, 0, 0, 0, 3], [8, 8, 8, 8, 8, 8, 8, 8, 8], [8, 0, 0, 0, 0, 0, 8, 8, 8],
    [0, 0, 8, 8, 0, 0, 0, 0, 0],
]  # fmt: skip


def superhammond(n: int, rng: random.Random | None = None) -> SynthDef:
    """The B3 organ. Its oscillator phases are drawn when the SynthDef is built
    (``(2pi ! 9).rand``); nanodirt draws them from a fixed seed."""
    rng = rng or random.Random(650)

    def body(b: SynthDefBuilder) -> None:
        sustain = b["sustain"]
        attrel = (sustain / 2).clip(0, 0.005)
        freq = b["freq"] * _fs(b, b["speed"], b["accelerate"], sustain)
        env = env_ar(env_linen(attrel, sustain - 2 * attrel, attrel), done_action=2)
        freqs = [k * freq for k in (0.5, 1.4988, 1, 2, 2.9976, 4, 5.0409, 5.9953, 8)]
        freqs = [2 ** (f < 63) * f for f in freqs]
        freqs = [f / (2 ** (f > 6000)) for f in freqs]
        freqs = [f / (2 ** (f > 6000)) for f in freqs]
        amps = selectx_kr(b["voice"], HAMMOND)
        amps = [a - d for a, d in zip(amps, [0, 0, 0, 0, 0, 0, 0.5, 1, 1.5])]
        amps = [2 ** (a - 8) for a in amps]
        total = vsum(amps)
        click = WhiteNoise.ar() * XLine.ar(start=1, stop=1e-6, duration=0.1)
        phases = [rng.uniform(0, TWO_PI) for _ in range(9)]
        sound = 0.5 * Mix.new(
            [
                SinOsc.ar(frequency=f * (2 * click + 1), phase=p) * (a / total)
                for f, p, a in zip(freqs, phases, amps)
            ]
        )
        perc = (
            b["perc"] * 0.5 * SinOsc.ar(frequency=freq * b["percf"], phase=rng.uniform(0, TWO_PI))
        )
        sound = perc * XLine.ar(start=1, stop=1e-6, duration=2 * b["decay"] + 1) + sound
        vib = LFTri.kr(frequency=b["vrate"], initial_phase=0) * (b["vibrato"] / 2) + 1
        sound = sound + BAllPass.ar(source=sound, frequency=1000 * vib)
        Out.ar(bus=b["out"], source=_pan(b, n, sound, env))

    return _def(
        "superhammond", n,
        _kr("out", "sustain", "decay", "pan", "freq", "vibrato", "vrate", "voice", "perc", "percf", "accelerate",
            "speed", sustain=1, vibrato=0.5, vrate=7, percf=2, speed=1),
        body, speed_freq=True,
    )  # fmt: skip


def supervibe(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        sustain, velocity, decay = b["sustain"], b["velocity"], b["decay"]
        env = env_ar(env_linen(0.001, 0.998, 0.001), done_action=2, time_scale=sustain)
        freq = b["freq"] * _fs(b, b["speed"], b["accelerate"], sustain)
        freqs = [freq * k for k in (1, 4, 10)] + [freq * (13.75 + b["detune"])]
        hifreqs = [freq * k for k in (19.2, 20, 21.2)]
        mod_osc = SinOsc.kr(frequency=b["modfreq"], phase=Rand.ir(minimum=0, maximum=TWO_PI))
        mods = [mod_osc.range(1 - (d * b["modamp"]), 1) for d in (0.5, 0.3, 0.3, 0.5)]
        levels = [1, 1.1 * velocity, 0.6 * velocity * velocity, 0.5 * velocity]
        sound = [0.3 * lv * SinOsc.ar(frequency=f) for lv, f in zip(levels, freqs)]
        fade = (-1.0 * decay).exp_()
        decays = [1, 0.7 * fade, 0.5 * fade, 0.4 * fade]
        sound = [
            XLine.ar(start=1e-6, stop=1, duration=1 / f)
            * XLine.ar(start=1, stop=1e-6, duration=d * 4 * sustain)
            * s
            * m
            for f, d, s, m in zip(freqs, decays, sound, mods)
        ]
        onset = XLine.ar(start=0.3 * (velocity + 1e-6), stop=1e-6, duration=0.02)
        sound = vsum([onset * SinOsc.ar(frequency=f) for f in hifreqs]) + vsum(sound)
        Out.ar(bus=b["out"], source=_pan(b, n, sound, env))

    return _def(
        "supervibe", n,
        _kr("out", "sustain", "decay", "pan", "freq", "accelerate", "velocity", "modamp", "modfreq", "detune",
            "speed", sustain=1, velocity=1, modamp=1, modfreq=7.0, speed=1),
        body, speed_freq=True,
    )  # fmt: skip


def superhoover(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        sustain, d = b["sustain"], b["decay"]
        env = env_ar(
            env_pairs([(0, 0), (0.03, 1), (0.2, 1 - d), (0.95, 1 - d), (1, 0)], -1),
            done_action=2,
            time_scale=sustain,
        )
        fglide = (
            env_ar(env_perc(0.05, 0.3, 1, "linear"), time_scale=sustain) * (-1 * b["slide"] / 4) + 1
        )
        freq = b["freq"] * fglide * _fs(b, b["speed"], b["accelerate"], sustain)
        freqs = [
            SinOsc.kr(frequency=f, phase=p).exprange(0.995, 1.005) * freq
            for f, p in zip((2.94, 3.0, 3.07), (3.0, 5.5, 1.2))
        ]
        pwm = [SinOsc.kr(frequency=f).range(1 / 8, 7 / 8) for f in (1.3, 2.9, 4.4)]
        parts = [
            g
            * LFSaw.ar(frequency=f * r, initial_phase=ph).range(0, 1)
            * (1 - LFPulse.ar(frequency=f * r2, initial_phase=0, width=w))
            for g, f, r, ph, r2, w in zip(
                (1, 0.9, 1.1), freqs, (0.25, 0.5, 1), (1, 1.01, 1.02), (0.5, 1, 2), pwm
            )
        ]
        mix = vsum(parts) * 0.15
        mix = mix + vsum([LFPar.ar(frequency=f * 0.25, initial_phase=0) * 0.1 for f in freqs])
        mix = BPeakEQ.ar(source=mix, frequency=6000, reciprocal_of_q=1, gain=3)
        mix = BPeakEQ.ar(source=mix, frequency=3500, reciprocal_of_q=1, gain=6)
        delays = [
            SinOsc.kr(frequency=3, phase=p).range(1 / 300, 1 / 200)
            for p in (0.5 * math.pi, 1.5 * math.pi)
        ]
        mix = [
            mix + CombC.ar(source=mix, maximum_delay_time=1 / 200, delay_time=t, decay_time=0)
            for t in delays
        ]
        Out.ar(bus=b["out"], source=_pan(b, n, [1.4 * m for m in mix], env))

    return _def(
        "superhoover", n,
        _kr("out", "sustain", "decay", "pan", "freq", "accelerate", "slide", "speed", sustain=1, speed=1),
        body, speed_freq=True,
    )  # fmt: skip


def superzow(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        sustain, d, slide, detune = b["sustain"], b["decay"], b["slide"], b["detune"]
        env = env_ar(
            env_pairs([(0, 0), (0.07, 1), (0.2, 1 - d), (0.95, 1 - d), (1, 0)], -1),
            done_action=2,
            time_scale=sustain,
        )
        basefreq = b["freq"] * _fs(b, b["speed"], b["accelerate"], sustain)

        def saw(f: Any) -> Any:
            return VarSaw.ar(
                frequency=f,
                initial_phase=0,
                width=Line.ar(start=0, stop=0.5, duration=sustain * 20 / slide),
            )

        sound = saw(basefreq)
        sound2 = saw(basefreq * (detune / 100 + 1))
        sound3 = saw(basefreq * (-1 * detune / 100 + 1))
        sound = (
            sound
            - DelayN.ar(source=sound2, maximum_delay_time=0.2,
                        delay_time=Line.ar(start=0, stop=1, duration=5 * sustain / slide) / basefreq)
            + DelayN.ar(source=sound3, maximum_delay_time=0.2,
                        delay_time=Line.ar(start=0, stop=1, duration=20 * sustain / slide) / basefreq)
        )  # fmt: skip
        Out.ar(bus=b["out"], source=_pan(b, n, sound / 2, env))

    return _def(
        "superzow", n,
        _kr("out", "sustain", "pan", "accelerate", "freq", "decay", "slide", "detune", "speed",
            sustain=1, slide=1, detune=1, speed=1),
        body, speed_freq=True,
    )  # fmt: skip


def superstatic(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        sustain = b["sustain"]
        env = env_ar(env_sine(1, 0.5), done_action=2, time_scale=sustain)
        freq = b["freq"] * _fs(b, b["speed"], b["accelerate"], sustain)
        sound = Dust.ar(density=freq * 40) > 0.5
        sound = Pulse.ar(frequency=freq * sound * 4, width=0.5) * 0.5
        Out.ar(bus=b["out"], source=_pan(b, n, sound, env))

    return _def(
        "superstatic", n, _kr("out", "sustain", "pan", "freq", "accelerate", "speed", sustain=1, speed=1),
        body, speed_freq=True,
    )  # fmt: skip


def supergrind(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        sustain, detune = b["sustain"], b["detune"]
        freq = (b["freq"] * _fs(b, b["speed"], b["accelerate"], sustain)) / 2
        env = env_ar(env_linen(0.1, 0.8, 0.1), done_action=2, time_scale=sustain)
        duty = Duty.kr(
            duration=1, reset=0, level=Dseq.dr(repeats=math.inf, sequence=[0.25, 0.25, 0.25, 2])
        )
        trig = Impulse.kr(frequency=b["rate"] * duty)
        loop = LocalIn.ar(channel_count=1)
        loop = loop + Impulse.ar(
            frequency=SinOsc.ar(frequency=1 / 4, phase=0) * (freq / 2) + (b["voice"] + 1.0) * freq
        )
        delays = [1 / (2 * freq + x) - ControlDur.ir() for x in (detune, -1.0 * detune)]
        loop = (
            Mix.new(
                [DelayC.ar(source=loop + trig, maximum_delay_time=1, delay_time=t) for t in delays]
            )
            / 2.0
        )
        LocalOut.ar(source=LeakDC.ar(source=loop).tanh_())
        loop = loop + DelayC.ar(
            source=loop,
            maximum_delay_time=0.2,
            delay_time=SinOsc.ar(frequency=1, phase=0) * 1e-3 + 0.1,
        )
        verb = GVerb.ar(source=((loop + (8 * trig)) * 4).sin_())
        loop = [v.sin_() + loop for v in verb]
        Out.ar(bus=b["out"], source=_pan(b, n, [0.3 * x for x in loop], env))

    return _def(
        "supergrind", n,
        _kr("out", "pan", "freq", "sustain", "accelerate", "detune", "voice", "rate", "speed", rate=1, speed=1),
        body, speed_freq=True,
    )  # fmt: skip


PRIMES = [2, 3, 5, 7, 11, 13, 17, 19, 23]


def superprimes(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        sustain, detune, voice = b["sustain"], b["detune"], b["voice"]
        freq = b["freq"] * _fs(b, b["speed"], b["accelerate"], sustain)
        env = env_ar(env_linen(0.1, 0.6, 0.3), done_action=2, time_scale=sustain)
        trig = [Impulse.ar(frequency=30.0 / p / sustain * b["rate"]) for p in PRIMES]
        phase = [
            Phasor.kr(trigger=t, rate=1.0 / p / ControlRate.ir()) for t, p in zip(trig, PRIMES)
        ]
        ratios = [1 + (detune / 12), 1 - (detune / 12)]
        # freq * (1..9) * [a, b] / 2: the 2-element array wraps over the 9.
        sines = [
            SinOsc.ar(frequency=freq * (k + 1) * ratios[k % 2] / 2.0, phase=phase[k])
            for k in range(9)
        ]
        cutoff = voice.linexp(0, 2, 30, 18000)
        decays = [
            LPF.ar(
                source=Decay.ar(source=trig[k], decay_time=(PRIMES[k] + 3) * sustain / 10),
                frequency=cutoff,
            )
            for k in range(9)
        ]
        sound = [s * e for s, e in zip(sines, decays)]
        verbs = [
            GVerb.ar(
                source=s.softclip(), room_size=10 * voice.linexp(0, 5, 1, 0.01), reverb_time=10
            )
            for s in sound
        ]
        flat = [x.tanh_() for v in verbs for x in v]
        center = LFNoise2.kr(frequency=sustain).range(0, len(PRIMES) - 1)
        sound = SplayAz.ar(channel_count=len(PRIMES), source=flat, center=center)
        Out.ar(bus=b["out"], source=_pan(b, n, sound, env))

    return _def(
        "superprimes", n,
        _kr("out", "pan", "freq", "sustain", "accelerate", "rate", "detune", "voice", "speed", rate=1, speed=1),
        body, speed_freq=True,
    )  # fmt: skip


def superwavemechanics(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        sustain = b["sustain"]
        freq = b["freq"] * _fs(b, b["speed"], b["accelerate"], sustain)
        env = env_ar(env_sine(1.0), done_action=2, time_scale=sustain)
        tick = Impulse.kr(frequency=sustain / 8)  # .dup(8): one Impulse, 8 times
        i = [TIRand.kr(minimum=0, maximum=3, trigger=tick) for _ in range(8)]
        r = [3 / 8, 5 / 8, 8 / 8, 9 / 8]
        detune = b["detune"].min_(1.5)
        d = [Rand.ir(minimum=1.6 * x - detune, maximum=1.7 * x + detune).round_(0.25) for x in i]
        rq = 0.01 + b["voice"].linexp(0, 1, 1e-3, 1)
        noise = WhiteNoise.ar() * 0.5  # one noise source for all 8 filters
        res = [
            Resonz.ar(source=noise, frequency=freq * r[k % 4] * d[k], bandwidth_ratio=rq)
            for k in range(8)
        ]
        size = b["resonance"].linexp(0, 1, 99, 0.1)
        sound = [
            Sanitize.ar(source=HPF.ar(source=Limiter.ar(source=55 * x), frequency=30))
            for s in res
            for x in GVerb.ar(source=s, room_size=size, reverb_time=10)
        ]
        Out.ar(bus=b["out"], source=_pan(b, n, sound, env))

    return _def(
        "superwavemechanics", n,
        _kr("out", "pan", "freq", "sustain", "accelerate", "detune", "voice", "resonance", "speed", speed=1),
        body, speed_freq=True,
    )  # fmt: skip


def supertron(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        sustain, detune = b["sustain"], b["detune"]
        aenv = env_ar(env_linen(0.05, 0.85, 0.1, 1, "linear"), done_action=2, time_scale=sustain)
        freq = b["freq"] * _fs(b, b["speed"], b["accelerate"], sustain)
        fb = LocalIn.ar(channel_count=1)
        width = RLPF.ar(source=fb, frequency=freq / 6.1, reciprocal_of_q=1.5).range(
            0, 1 - (b["voice"] / 1.5)
        )
        sound = Mix.new(
            [Pulse.ar(frequency=freq + x, width=width) for x in (1 + detune, -1 - detune)]
        )
        sound = LeakDC.ar(source=sound)
        LocalOut.ar(source=sound)
        Out.ar(bus=b["out"], source=_pan(b, n, sound, aenv))

    return _def(
        "supertron", n, _kr("out", "pan", "freq", "sustain", "voice", "detune", "accelerate", "speed", speed=1),
        body, speed_freq=True,
    )  # fmt: skip


def superreese(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        sustain, detune, voice = b["sustain"], b["detune"], b["voice"]
        env = env_ar(env_linen(0.05, 0.8, 0.15), done_action=2, time_scale=sustain)
        q1 = voice.linlin(0, 2, 3.0, 0.5)
        q2 = voice.linlin(0, 2, 2.0, 7.0)
        freq = b["freq"] * _fs(b, b["speed"], b["accelerate"], sustain)
        saws = [
            VarSaw.ar(frequency=freq * m, initial_phase=p, width=w)
            for m, p, w in zip(
                (-1 * detune / 100 + 0.99, detune / 100 + 1.01), (0.7, 0.5), (0.01, 0.02)
            )
        ]
        sound = Splay.ar(source=saws, spread=0.2, level=1, normalize=False)
        sound = RLPF.ar(source=sound, frequency=freq * 10, reciprocal_of_q=1.0 / q1)
        sound = sound.clip2(1.0 / 5.0) * 5.0
        sound = 0.35 * RLPF.ar(source=sound, frequency=freq * 20, reciprocal_of_q=1.0 / q2)
        Out.ar(bus=b["out"], source=_pan(b, n, sound, env))

    return _def(
        "superreese", n, _kr("out", "pan", "freq", "sustain", "accelerate", "detune", "voice", "speed", speed=1),
        body, speed_freq=True,
    )  # fmt: skip


def superfm(n: int, rng: random.Random | None = None) -> SynthDef:
    """The DX7-like 6-op FM synth. Its 5 random presets and its ``detune``
    defaults are drawn when the SynthDef is built: SuperDirt draws new ones on
    every load. nanodirt draws them from a fixed seed, so voices 1-5 are stable.
    """
    rng = rng or random.Random(7)
    ops = range(1, 7)
    controls: Controls = [
        ("sustain", 1, "kr"), ("lfofreq", 1, "kr"), ("lfodepth", 0, "kr"), ("freq", 440, "kr"),
        ("out", 0, "kr"), ("pan", 0, "kr"), ("voice", 0, "kr"),
    ]  # fmt: skip
    controls += [(f"amp{i}", 1, "kr") for i in ops]
    controls += [(f"ratio{i}", 1, "kr") for i in ops]
    controls += [(f"detune{i}", rng.uniform(-0.1, 0.1), "kr") for i in ops]
    controls += [("feedback", 0.0, "kr")]
    controls += [(f"eglevel{i}{k}", 1, "kr") for i in ops for k in range(1, 5)]
    controls += [
        (f"egrate{i}{k}", d, "ir") for i in ops for k, d in zip(range(1, 5), (10, 0.3, 0.1, 0.1))
    ]
    controls += [(f"mod{r}{c}", 0, "kr") for r in ops for c in ops]
    weights = [1, 2, 8, 4, 3, 0.5, 0.5, 0.5, 0.5, 0.25, 0.25, 0.25]

    def body(b: SynthDefBuilder) -> None:
        sustain, feedback = b["sustain"], b["feedback"]
        tremolo = 1 + LFTri.kr(frequency=b["lfofreq"]) * b["lfodepth"]
        env = env_ar(env_linen(0.01, 0.98, 0.01, 1, -3), done_action=2, time_scale=sustain)
        amps = [b[f"amp{i}"] for i in ops]
        user = [
            [b[f"ratio{i}"] for i in ops],
            [b[f"detune{i}"] for i in ops],
            amps,
            [[b[f"eglevel{i}{k}"] for k in range(1, 5)] for i in ops],
            [[(0.1 / b[f"egrate{i}{k}"]).max_(0.001) for k in range(1, 5)] for i in ops],
            [[b[f"mod{r}{c}"] * (feedback if r == c else 1) for c in ops] for r in ops],
        ]

        def ratio() -> float:
            pool = [
                0.25,
                0.5,
                1,
                2,
                3,
                4,
                5,
                6,
                7,
                rng.randint(1, 11),
                rng.randint(1, 13),
                rng.randint(1, 15),
            ]
            return float(rng.choices(pool, weights)[0])

        def preset() -> list[Any]:
            return [
                [ratio() for _ in ops],
                [rng.uniform(-7, 7) for _ in ops],
                [rng.random() * (rng.random() < 0.5) for _ in ops],
                [[rng.random() for _ in range(4)] for _ in ops],
                [[rng.random() for _ in range(4)] for _ in ops],
                [
                    [
                        rng.random() * (rng.random() < 0.25) * (feedback if r == c else 1)
                        for c in ops
                    ]
                    for r in ops
                ],
            ]

        presets = selectx_kr(b["voice"], [user] + [preset() for _ in range(5)])
        envs = [
            env_kr(env_new([0] + [presets[3][i][k] for k in range(4)], [presets[4][i][k] for k in range(4)]),
                   time_scale=sustain)
            for i in range(6)
        ]  # fmt: skip
        ctls = [[b["freq"] * tremolo * presets[0][i] + presets[1][i], 0, envs[i]] for i in range(6)]
        sound = FM7.ar(ctl_matrix=ctls, mod_matrix=presets[5])
        sound = Mix.new([s * a for s, a in zip(sound, amps)]) * (10 ** (-15 / 20))
        Out.ar(bus=b["out"], source=_pan(b, n, sound, env))

    return _def("superfm", n, controls, body)


def soskick(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        freq = b["freq"]
        env = env_ar(env_perc(0.005, b["sustain"]), done_action=2)
        source = Line.ar(start=freq * 2, stop=freq, duration=0.02)
        source = PMOsc.ar(
            carrier_frequency=source, modulator_frequency=b["pitch1"], pm_index=b["voice"]
        )
        source = LPF.ar(source=source, frequency=1000)
        beater = WhiteNoise.ar() * b["pitch2"].clip(0, 1)
        beater = HPF.ar(source=beater, frequency=500)
        beater = LPF.ar(
            source=beater,
            frequency=XLine.ar(start=6000, stop=500, duration=b["speed"].clip(0, 1) * 0.1),
        )
        beater = beater * env_ar(env_perc())
        Out.ar(bus=b["out"], source=_pan(b, n, (source + beater) * (10 ** (3 / 20)), env))

    return _def(
        "soskick", n,
        _kr("out", "pan", "freq", "pitch1", "speed", "voice", "sustain", "pitch2",
            freq=65, speed=0.3, voice=1, sustain=1),
        body,
    )  # fmt: skip


def soshats(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        freq, sustain = b["freq"], b["sustain"]
        pulse = Pulse.ar(frequency=freq)
        source = Mix.new([
            PMOsc.ar(carrier_frequency=pulse, modulator_frequency=freq * m, pm_index=b["pitch1"] * k)
            for m, k in zip((1.34, 2.405, 3.09, 1.309), (1, 0.22, 0.014, 0.0038))
        ])  # fmt: skip
        bpf = BPF.ar(
            source=source,
            frequency=XLine.kr(start=15000, stop=9000, duration=sustain),
            reciprocal_of_q=Clip.ir(source=b["resonance"], minimum=0, maximum=1),
        ) * env_ar(env_perc(0.005, sustain, 1, -4))
        hpf = HPF.ar(
            source=source, frequency=XLine.kr(start=9000, stop=12000, duration=sustain)
        ) * env_ar(env_perc(0.005, sustain, 1, -4))
        envelope = env_ar(env_perc(0.005, sustain), done_action=2)
        Out.ar(bus=b["out"], source=_pan(b, n, (bpf + hpf) * (10 ** (-5 / 20)), envelope))

    return _def(
        "soshats", n,
        _kr("out", "pan", "freq", "pitch1", "resonance", "sustain", freq=220, pitch1=238.5, resonance=1, sustain=0.5),
        body,
    )  # fmt: skip


def sostoms(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        freq = b["freq"]
        source = PMOsc.ar(
            carrier_frequency=Saw.ar(frequency=freq * 0.9),
            modulator_frequency=freq * 0.85,
            pm_index=b["voice"],
        ) * (10 ** (6 / 20))
        source = Mix.new([source + SinOsc.ar(frequency=f) for f in (freq, freq * 0.8)])
        source = LeakDC.ar(source=source + Crackle.ar(chaos_parameter=2)) * (10 ** (-3 / 20))
        envelope = env_ar(env_perc(0.005, b["sustain"], 10 ** (-4 / 20), -6), done_action=2)
        Out.ar(bus=b["out"], source=_pan(b, n, source, envelope))

    return _def(
        "sostoms",
        n,
        _kr("out", "pan", "sustain", "freq", "voice", sustain=0.5, freq=261.626, voice=0.5),
        body,
    )


def sossnare(n: int) -> SynthDef:
    def body(b: SynthDefBuilder) -> None:
        freq, pitch1, resonance, sustain = b["freq"], b["pitch1"], b["resonance"], b["sustain"]
        source = PMOsc.ar(
            carrier_frequency=Saw.ar(frequency=freq * 0.85),
            modulator_frequency=freq * b["semitone"],
            pm_index=b["voice"],
        )
        noise = Latch.ar(source=WhiteNoise.ar() * 0.1, trigger=Impulse.ar(frequency=1700 * 2))
        for k in (4, 2.5, 1.8):
            noise = BRF.ar(source=noise, frequency=k * pitch1, reciprocal_of_q=resonance) * 0.5
        # Env.perc(0.005, sustain, -4): the third argument is the level.
        noise = BRF.ar(source=noise, frequency=pitch1, reciprocal_of_q=resonance) * env_ar(
            env_perc(0.005, sustain, -4)
        )
        noise = Resonz.ar(source=noise, frequency=pitch1, bandwidth_ratio=1) * 40
        envelope = env_ar(env_perc(0.005, sustain, 0.5, -4), done_action=2)
        Out.ar(bus=b["out"], source=_pan(b, n, (source + noise) * (10 ** (4 / 20)), envelope))

    return _def(
        "sossnare", n,
        _kr("out", "pan", "freq", "voice", "semitone", "pitch1", "resonance", "sustain",
            freq=405, voice=0.385, semitone=0.452, pitch1=2000, resonance=0.1, sustain=0.5),
        body,
    )  # fmt: skip


DEFAULT_INSTRUMENTS: list[Callable[[int], SynthDef]] = [
    imp, psin, pmsin, lambda n: sound_in(n, True), lambda n: sound_in(n, False), dirt_from, inr, gabor, dirac,
    cyclo, debug,
]  # fmt: skip
EXTRA_INSTRUMENTS: list[Callable[[int], SynthDef]] = [
    supermandolin, supergong, superpiano, superhex, superkick, super808, superhat, supersnare, superclap,
    supersiren, supersquare, supersaw, superpwm, supercomparator, superchip, supernoise, superfork,
    superhammond, supervibe, superhoover, superzow, superstatic, supergrind, superprimes, superwavemechanics,
    supertron, superreese, superfm, soskick, soshats, sostoms, sossnare,
]  # fmt: skip


def default_instruments(num_channels: int) -> list[SynthDef]:
    """Every instrument SynthDef, for *num_channels* output channels."""
    return [f(num_channels) for f in DEFAULT_INSTRUMENTS + EXTRA_INSTRUMENTS]
