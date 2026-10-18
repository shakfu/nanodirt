"""SuperDirt's core and global SynthDefs (core-synths.scd, core-synths-global.scd).

Each function returns the SynthDef that SuperDirt `650a1d6` builds for
*num_channels* output channels, under the same name and control names.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from nanosynth.envelopes import Envelope, EnvGen
from nanosynth.synthdef import SynthDef, SynthDefBuilder, control
from nanosynth.ugens import (
    BPF,
    DC,
    HPF,
    RHPF,
    RLPF,
    AllpassL,
    AllpassN,
    Balance2,
    BufFrames,
    BufRateScale,
    BufRd,
    BufSampleRate,
    CheckBadValues,
    DelayC,
    DelayN,
    Impulse,
    In,
    Lag,
    Latch,
    LeakDC,
    LFNoise1,
    LFPar,
    LFSaw,
    Limiter,
    Line,
    LocalIn,
    LocalOut,
    OffsetOut,
    OnePole,
    Out,
    PanAz,
    PitchShift,
    PlayBuf,
    Rand,
    ReplaceOut,
    SampleRate,
    Select,
    SendPeakRMS,
    Silent,
    SinOsc,
    XFade2,
)
from nanosynth.ugens.sc3 import SwitchDelay

from .ugens import dirt_gate_cut_group, dirt_pan, dirt_pause

MAX_SAMPLE_CHANNELS = 2  # SuperDirt.maxSampleNumChannels
MIDDLE_C = 261.6255653005986  # 60.midicps
Controls = list[tuple[str, Any, str]]


@contextmanager
def _builder(controls: Controls) -> Iterator[SynthDefBuilder]:
    with SynthDefBuilder(**{n: control(v, r) for n, v, r in controls}) as b:
        yield b


def _pan_controls(num_channels: int) -> Controls:
    """Named controls DirtPan creates inline, by output channel count."""
    if num_channels == 2:
        return [("span", 1, "ir")]
    if num_channels > 2:
        return [
            ("span", 1, "ir"),
            ("splay", 1, "ir"),
            ("panwidth", 2, "ir"),
            ("orientation", 0, "ir"),
        ]
    return []


def _pan_args(b: SynthDefBuilder, num_channels: int) -> dict[str, Any]:
    return {name: b[name] for name, _, _ in _pan_controls(num_channels)}


SAMPLE_CONTROLS: Controls = [
    ("out", 0, "ir"),
    ("bufnum", 0, "ir"),
    ("sustain", 1, "ir"),
    ("begin", 0, "ir"),
    ("end", 1, "ir"),
    ("speed", 1, "ir"),
    ("endSpeed", 1, "ir"),
    ("freq", 440, "ir"),
    ("pan", 0, "kr"),
]


def sample(sample_channels: int, num_channels: int) -> SynthDef:
    """``dirt_sample_<s>_<n>``: play a buffer segment at a rate set by speed and freq."""
    with _builder(SAMPLE_CONTROLS + _pan_controls(num_channels)) as b:
        bufnum, begin, end, speed = b["bufnum"], b["begin"], b["end"], b["speed"]
        rate = Line.kr(start=speed, stop=b["endSpeed"], duration=b["sustain"]) * (
            b["freq"] / MIDDLE_C
        )
        num_frames = BufFrames.ir(buffer_id=bufnum).max_(1)
        sawrate = rate * BufSampleRate.ir(buffer_id=bufnum) / (begin.absdif(end) * num_frames)
        phase = (speed.sign() * LFSaw.ar(frequency=sawrate, initial_phase=1)).range(
            begin, end
        ) * num_frames
        sound = BufRd.ar(
            channel_count=sample_channels,
            buffer_id=bufnum,
            phase=phase,
            loop=0,
            interpolation=4,
        )
        sound = dirt_pan(sound, num_channels, b["pan"], _pan_args(b, num_channels))
        Out.ar(bus=b["out"], source=sound)
    return b.build(name=f"dirt_sample_{sample_channels}_{num_channels}")


def sample_long(sample_channels: int, num_channels: int) -> SynthDef:
    """``dirt_sample_long_<s>_<n>``: ``PlayBuf`` for files beyond 2^24 frames."""
    with _builder(SAMPLE_CONTROLS + _pan_controls(num_channels)) as b:
        bufnum, begin, sustain = b["bufnum"], b["begin"], b["sustain"]
        rate = Line.kr(start=b["speed"], stop=b["endSpeed"], duration=sustain) * (
            b["freq"] / MIDDLE_C
        )
        rate = rate * BufRateScale.ir(buffer_id=bufnum)
        num_frames = BufFrames.ir(buffer_id=bufnum).max_(1)
        # sclang if(cond, a, b) on UGens: cond * (a - b) + b
        cond = (rate < 0) * (begin <= 0)
        start = num_frames * begin
        start_pos = cond * (num_frames - start) + start
        sound = PlayBuf.ar(
            channel_count=sample_channels,
            buffer_id=bufnum,
            rate=rate,
            start_position=start_pos,
            loop=0,
        )
        env = EnvGen.kr(
            envelope=Envelope.linen(attack_time=0, sustain_time=sustain, release_time=0)
        )
        sound = dirt_pan(sound * env, num_channels, b["pan"], _pan_args(b, num_channels))
        Out.ar(bus=b["out"], source=sound)
    return b.build(name=f"dirt_sample_long_{sample_channels}_{num_channels}")


def stretch_sample(sample_channels: int, num_channels: int) -> SynthDef:
    """``dirt_stretchsample_<s>_<n>``: overlap-add time stretch with a Gaussian window."""
    controls = SAMPLE_CONTROLS + [("timescale", 1, "ir"), ("timescalewin", 1, "ir")]
    with _builder(controls + _pan_controls(num_channels)) as b:
        bufnum, begin, end, speed = b["bufnum"], b["begin"], b["end"], b["speed"]
        timescale = b["timescale"]
        rate = Line.kr(start=speed, stop=b["endSpeed"], duration=b["sustain"]) * (
            b["freq"] / MIDDLE_C
        )
        num_frames = BufFrames.ir(buffer_id=bufnum)
        bsr = BufSampleRate.ir(buffer_id=bufnum)
        window_size = timescale.clip(0.1, 2) * 0.05 * bsr * b["timescalewin"]
        step = window_size / timescale
        sawrate = rate * bsr / (begin.absdif(end) * timescale * num_frames)
        phase = (
            (speed.sign() * LFSaw.ar(frequency=sawrate, initial_phase=1)).range(begin, end)
            * num_frames
            * timescale
        )
        steps = [phase // step - 1.0, phase // step - 0.0]
        index = [s * (step / timescale - step) + phase for s in steps]
        window_index = [phase - s * step for s in steps]
        window = [(-50 * (w / window_size - 0.5).squared()).exp_() for w in window_index]
        sounds = [
            w
            * BufRd.ar(
                channel_count=sample_channels,
                buffer_id=bufnum,
                phase=i,
                loop=0,
                interpolation=4,
            )
            for w, i in zip(window, index)
        ]
        sound = (sounds[0] + sounds[1]) / (window[0] + window[1])
        sound = dirt_pan(sound, num_channels, b["pan"], _pan_args(b, num_channels))
        Out.ar(bus=b["out"], source=sound)
    return b.build(name=f"dirt_stretchsample_{sample_channels}_{num_channels}")


def gate(num_channels: int) -> SynthDef:
    """``dirt_gate<n>``: envelope, level, cut group; mix to the dry bus and clear the input."""
    controls: Controls = [
        ("out", 0, "ir"),
        ("in", 0, "ir"),
        ("sustain", 1, "ir"),
        ("fadeInTime", 0.001, "ir"),
        ("fadeTime", 0.001, "ir"),
        ("amp", 1, "kr"),
        ("gain", 1, "ir"),
        ("overgain", 0, "ir"),
        ("cut_gate", 1, "kr"),
    ]
    with _builder(controls) as b:
        signal = In.ar(bus=b["in"], channel_count=num_channels)
        env = EnvGen.ar(
            envelope=Envelope(
                amplitudes=[0, 1, 1, 0],
                durations=[b["fadeInTime"], b["sustain"], b["fadeTime"]],
                curves=["sine"],
            ),
            done_action=14,
        )
        amp = b["amp"] * (b["gain"].min_(2) + b["overgain"]) ** 4
        cut = dirt_gate_cut_group(b["cut_gate"], b["fadeTime"], done_action=14)
        signal = signal * env * amp * cut
        OffsetOut.ar(bus=b["out"], source=signal)
        ReplaceOut.ar(bus=b["in"], source=Silent.ar(num_channels))
    return b.build(name=f"dirt_gate{num_channels}")


def out(num_channels: int) -> SynthDef:
    """``dirt_out<n>``: copy the event to a routing bus (``to``)."""
    controls: Controls = [
        ("out", 0, "ir"),
        ("to", 0, "ir"),
        ("sustain", 1, "ir"),
        ("fadeInTime", 0.001, "ir"),
        ("fadeTime", 0.001, "ir"),
        ("through", 0, "ir"),
        ("amp", 1, "kr"),
    ]
    with _builder(controls) as b:
        signal = In.ar(bus=b["out"], channel_count=num_channels)
        env = EnvGen.ar(
            envelope=Envelope(
                amplitudes=[0, 1, 1, 0],
                durations=[b["fadeInTime"], b["sustain"], b["fadeTime"]],
                curves=["sine"],
            )
        )
        Out.ar(bus=b["to"], source=signal * env * b["amp"])
        ReplaceOut.ar(bus=b["out"], source=signal * b["through"])
    return b.build(name=f"dirt_out{num_channels}")


def _effect(
    name: str,
    num_channels: int,
    controls: Controls,
    body: Callable[[SynthDefBuilder, Any], Any],
) -> SynthDef:
    """An in-place effect: read ``out``, process, ``ReplaceOut`` to ``out``."""
    with _builder([("out", 0, "ir"), *controls]) as b:
        signal = In.ar(bus=b["out"], channel_count=num_channels)
        ReplaceOut.ar(bus=b["out"], source=body(b, signal))
    return b.build(name=f"{name}{num_channels}")


def vowel(num_channels: int) -> SynthDef:
    def body(b: SynthDefBuilder, signal: Any) -> Any:
        resonance = b["resonance"]
        freqs = b["vowelFreqs"] * b["cutoff"].linexp(0, 1, 1, 2)
        amps = b["vowelAmps"] * resonance.linlin(0, 1, 50, 200)
        rqs = b["vowelRqs"] / resonance.linlin(0, 1, 1 / 10, 1 / 2)
        freqs = freqs.min_(SampleRate.ir() * 0.5)  # minNyquist
        # BPF.ar([signal], freqs, rqs, amps).sum: one BPF per formant, each
        # over all channels; then sum the formants.
        formants = [
            BPF.ar(source=signal, frequency=f, reciprocal_of_q=q) * a
            for f, q, a in zip(freqs, rqs, amps)
        ]
        voweled = formants[0]
        for f in formants[1:]:
            voweled = voweled + f
        return XFade2.ar(in_a=signal, in_b=voweled, pan=resonance.linlin(0, 1, -0.5, 1), level=0.25)

    return _effect(
        "dirt_vowel",
        num_channels,
        [
            ("resonance", 0.5, "ir"),
            ("cutoff", 0, "ir"),
            ("vowelFreqs", [1000.0] * 5, "ir"),
            ("vowelAmps", [0.0] * 5, "ir"),
            ("vowelRqs", [0.0] * 5, "ir"),
        ],
        body,
    )


def crush(num_channels: int) -> SynthDef:
    return _effect(
        "dirt_crush",
        num_channels,
        [("crush", 4, "kr")],
        lambda b, s: s.round_(0.5 ** (b["crush"] - 1)),
    )


def coarse(num_channels: int) -> SynthDef:
    return _effect(
        "dirt_coarse",
        num_channels,
        [("coarse", 0, "kr"), ("bandq", 10, "kr")],
        lambda b, s: Latch.ar(
            source=s, trigger=Impulse.ar(frequency=SampleRate.ir() / b["coarse"])
        ),
    )


def hpf(num_channels: int) -> SynthDef:
    return _effect(
        "dirt_hpf",
        num_channels,
        [("hcutoff", 440, "kr"), ("hresonance", 0, "kr")],
        lambda b, s: RHPF.ar(
            source=s,
            frequency=abs(b["hcutoff"]).clip(20, SampleRate.ir() / 2),
            reciprocal_of_q=b["hresonance"].linexp(0, 1, 1, 0.001),
        ),
    )


def bpf(num_channels: int) -> SynthDef:
    def body(b: SynthDefBuilder, signal: Any) -> Any:
        bandq = b["bandq"].max_(1)
        freq = b["bandqf"].clip(20, SampleRate.ir() / 2)
        return BPF.ar(source=signal, frequency=freq, reciprocal_of_q=1 / bandq) * (bandq.max_(1.0))

    return _effect("dirt_bpf", num_channels, [("bandqf", 440, "kr"), ("bandq", 10, "kr")], body)


def shape(num_channels: int) -> SynthDef:
    def body(b: SynthDefBuilder, signal: Any) -> Any:
        k = b["shape"].min_(1.0 - 4e-10)
        k = (2.0 * k) / (1.0 - k)
        return (1 + k) * signal / (1 + (k * abs(signal))) * 1

    return _effect("dirt_shape", num_channels, [("shape", 0, "kr")], body)


def lpf(num_channels: int) -> SynthDef:
    return _effect(
        "dirt_lpf",
        num_channels,
        [("cutoff", 440, "kr"), ("resonance", 0, "kr")],
        lambda b, s: RLPF.ar(
            source=s,
            frequency=abs(b["cutoff"]).clip(20, SampleRate.ir() / 2),
            reciprocal_of_q=b["resonance"].linexp(0, 1, 1, 0.001),
        ),
    )


def envelope(num_channels: int) -> SynthDef:
    return _effect(
        "dirt_envelope",
        num_channels,
        [
            ("attack", 0, "ir"),
            ("hold", 0, "ir"),
            ("release", math.inf, "ir"),
            ("curve", -3, "ir"),
        ],
        lambda b, s: (
            s
            * EnvGen.ar(
                envelope=Envelope.linen(
                    attack_time=b["attack"],
                    sustain_time=b["hold"],
                    release_time=b["release"],
                    level=1,
                    curve=b["curve"],
                )
            )
        ),
    )


def grain_envelope(num_channels: int) -> SynthDef:
    def body(b: SynthDefBuilder, signal: Any) -> Any:
        p = b["plat"].clip(0, 1)
        c = b["tilt"].clip(0, 1)
        rise_and_fall = 1 - p
        env = EnvGen.ar(
            envelope=Envelope.linen(
                attack_time=c * rise_and_fall,
                sustain_time=p,
                release_time=(1 - c) * rise_and_fall,
                level=1,
                curve=b["curve"],
            ),
            time_scale=b["sustain"],
        )
        return signal * env

    return _effect(
        "dirt_grain_envelope",
        num_channels,
        [
            ("sustain", 1, "ir"),
            ("tilt", 0.5, "ir"),
            ("plat", 0, "ir"),
            ("curve", -3, "ir"),
        ],
        body,
    )


def pitch_shift(num_channels: int) -> SynthDef:
    return _effect(
        "dirt_pitch_shift",
        num_channels,
        [("psrate", 1, "ir"), ("sustain", 1, "ir"), ("psdisp", 0, "ir")],
        lambda b, s: PitchShift.ar(
            source=s,
            window_size=b["sustain"].linlin(0.01, 0.3, 0.01, 0.18),
            pitch_ratio=b["psrate"],
            pitch_dispersion=0,
            time_dispersion=b["psdisp"],
        ),
    )


def tremolo(num_channels: int) -> SynthDef:
    def body(b: SynthDefBuilder, signal: Any) -> Any:
        depth = b["tremolodepth"].clip(0, 1) * 0.5
        wave = (1 - depth) + SinOsc.ar(frequency=b["tremolorate"], phase=0.5 * math.pi) * depth
        return signal * wave

    return _effect(
        "dirt_tremolo",
        num_channels,
        [("tremolorate", 1.0, "kr"), ("tremolodepth", 0.5, "kr")],
        body,
    )


def phaser(num_channels: int) -> SynthDef:
    def body(b: SynthDefBuilder, signal: Any) -> Any:
        phase = LFPar.kr(frequency=b["phaserrate"]).range(0.0088, 0.01)
        depth = b["phaserdepth"].clip(0, 1)
        wave = AllpassL.ar(
            source=signal,
            maximum_delay_time=b["sustain"],
            delay_time=phase,
            decay_time=0,
        )
        return signal + wave * depth

    return _effect(
        "dirt_phaser",
        num_channels,
        [("sustain", 4, "ir"), ("phaserrate", 1.0, "kr"), ("phaserdepth", 0.5, "kr")],
        body,
    )


# -- Global effects (core-synths-global.scd) ------------------------------------

GLOBAL_BUSES: Controls = [("dryBus", 0, "ir"), ("effectBus", 0, "ir")]


def _asr(gate: Any) -> Any:
    return EnvGen.kr(
        envelope=Envelope.asr(attack_time=0.01, sustain=1, release_time=1, curve=-4),
        gate=gate,
        done_action=2,
    )


def _jitter_lag(signal: Any) -> Any:
    """``x.lag(LFNoise1.kr(1).range(0.01, 0.02))``."""
    return signal.lag(LFNoise1.kr(frequency=1).range(0.01, 0.02))


def monitor(num_channels: int) -> SynthDef:
    controls: Controls = [
        ("dryBus", 0, "ir"),
        ("effectBus", 0, "ir"),
        ("outBus", 0, "kr"),
        ("gate", 1, "kr"),
        ("limitertype", 1, "kr"),
        ("dirt_resumed", 0, "tr"),
    ]
    with _builder(controls) as b:
        dry = In.ar(bus=b["dryBus"], channel_count=num_channels)
        wet = In.ar(bus=b["effectBus"], channel_count=num_channels)
        # Per channel: nanosynth's Select flattens a vector inside ``sources``
        # where sclang expands over it.
        silence = DC.ar(source=0)
        signal = []
        for x in wet + dry:
            bad = CheckBadValues.ar(source=x, ugen_id=0, post_mode=0) > 0
            x = Select.ar(selector=bad, sources=[x, silence])
            signal.append(
                Select.ar(
                    selector=b["limitertype"],
                    sources=[x, Limiter.ar(source=x), (x * 0.5).softclip() * 2],
                )
            )
        dirt_pause(signal, b["dirt_resumed"], grace_time=4)
        env = _asr(b["gate"])
        Out.ar(bus=b["outBus"], source=[x * env for x in signal])
    return b.build(name=f"dirt_monitor{num_channels}")


def rms(num_channels: int) -> SynthDef:
    controls: Controls = [
        ("gate", 1, "kr"),
        ("dryBus", 0, "ir"),
        ("effectBus", 0, "ir"),
        ("rmsReplyRate", 8, "kr"),
        ("rmsPeakLag", 3, "kr"),
        ("orbitIndex", 0, "kr"),
        ("dirt_resumed", 0, "tr"),
    ]
    with _builder(controls) as b:
        signal = In.ar(bus=b["effectBus"], channel_count=num_channels) + In.ar(
            bus=b["dryBus"], channel_count=num_channels
        )
        dirt_pause(signal, b["dirt_resumed"], grace_time=4)
        _asr(b["gate"])
        SendPeakRMS.kr(
            source=signal,
            reply_rate=b["rmsReplyRate"],
            peak_lag=b["rmsPeakLag"],
            command_name="/rms",
            reply_id=b["orbitIndex"],
        )
    return b.build(name=f"dirt_rms{num_channels}")


def delay(num_channels: int) -> SynthDef:
    """``dirt_delay<n>`` with ``SwitchDelay``, which ships in the nanosynth wheel."""
    controls: Controls = GLOBAL_BUSES + [
        ("gate", 1, "kr"),
        ("delaytime", 0, "kr"),
        ("delayfeedback", 0, "kr"),
        ("delaySend", 1, "kr"),
        ("delayAmp", 1, "kr"),
        ("lock", 0, "kr"),
        ("cps", 1, "kr"),
        ("dirt_resumed", 0, "tr"),
    ]
    with _builder(controls) as b:
        max_delay = 4
        signal = In.ar(bus=b["dryBus"], channel_count=num_channels)
        signal = signal * _jitter_lag(b["delaySend"])
        feedback = b["delayfeedback"].max_(0)
        lock = b["lock"]
        # if(lock, reciprocal(cps), 1) on UGens: lock * (1/cps - 1) + 1
        time = b["delaytime"] * (lock * (b["cps"].reciprocal() - 1) + 1)
        time = time.clip(0, max_delay)
        signal = SwitchDelay.ar(
            source=signal,
            drylevel=1,
            wetlevel=1,
            delaytime=time,
            delayfactor=feedback,
            maxdelaytime=max_delay,
        )
        signal = signal * _asr(b["gate"])
        signal = signal * b["delayAmp"].lag(0.01)
        dirt_pause(signal, b["dirt_resumed"], grace_time=4)
        Out.ar(bus=b["effectBus"], source=signal)
    return b.build(name=f"dirt_delay{num_channels}")


def reverb(num_channels: int) -> SynthDef:
    controls: Controls = GLOBAL_BUSES + [
        ("gate", 1, "kr"),
        ("room", 0, "kr"),
        ("size", 0.1, "kr"),
        ("dry", 0, "kr"),
        ("dirt_resumed", 0, "tr"),
    ]
    with _builder(controls) as b:
        channels = list(In.ar(bus=b["dryBus"], channel_count=num_channels))
        sig = channels[0]
        for c in channels[1:]:
            sig = sig + c
        sig = sig * _jitter_lag(b["room"])
        for _ in range(4):
            sig = AllpassN.ar(
                source=sig,
                maximum_delay_time=0.03,
                delay_time=[Rand.ir(minimum=0.005, maximum=0.02) for _ in range(num_channels)],
                decay_time=1,
            )
        depth = b["size"].lag(0.02).linexp(0, 1, 0.01, 0.98)
        loop = LocalIn.ar(channel_count=num_channels) * [
            depth + Rand.ir(minimum=0, maximum=0.05) for _ in range(num_channels)
        ]
        loop = OnePole.ar(source=loop, coefficient=0.5)
        loop = AllpassN.ar(
            source=loop,
            maximum_delay_time=0.05,
            delay_time=[Rand.ir(minimum=0.01, maximum=0.05) for _ in range(num_channels)],
            decay_time=2,
        )
        loop = DelayN.ar(
            source=loop,
            maximum_delay_time=0.3,
            delay_time=[t + Rand.ir(minimum=-0.003, maximum=0.003) for t in (0.19, 0.26)],
        )
        loop = AllpassN.ar(
            source=loop,
            maximum_delay_time=0.05,
            delay_time=[Rand.ir(minimum=0.03, maximum=0.15) for _ in range(num_channels)],
            decay_time=2,
        )
        loop = LeakDC.ar(source=loop + sig)
        LocalOut.ar(source=loop)
        snd = loop * _jitter_lag(1 - b["dry"])
        dirt_pause(snd, b["dirt_resumed"], grace_time=4)
        Out.ar(bus=b["effectBus"], source=snd * _asr(b["gate"]))
    return b.build(name=f"dirt_reverb{num_channels}")


def leslie(num_channels: int) -> SynthDef:
    controls: Controls = GLOBAL_BUSES + [
        ("gate", 1, "kr"),
        ("leslie", 0.5, "kr"),
        ("lrate", 6.7, "kr"),
        ("lsize", 0.3, "kr"),
        ("dirt_resumed", 0, "tr"),
    ]
    with _builder(controls) as b:
        amount, lrate, lsize = b["leslie"], b["lrate"], b["lsize"]
        sig = In.ar(bus=b["dryBus"], channel_count=num_channels)
        ReplaceOut.ar(bus=b["dryBus"], source=sig * (1 - amount))
        chans = list(sig)
        distance = SinOsc.ar(frequency=Lag.kr(source=lrate, lag_time=10), phase=0).range(0, lsize)
        throb = SinOsc.ar(frequency=Lag.kr(source=lrate * 0.8, lag_time=20), phase=0).range(
            0, lsize * 1.3
        )
        bal1 = SinOsc.kr(frequency=Lag.kr(source=lrate * 0.8, lag_time=4), phase=math.pi / 2).range(
            -0.2, 0.2
        )
        bal2 = SinOsc.kr(frequency=Lag.kr(source=lrate, lag_time=2), phase=math.pi / 2).range(
            -0.4, 0.4
        )

        def rotor(left: Any, right: Any) -> Any:
            """One stereo rotor; a pair of signals in, two channels out."""
            high = [HPF.ar(source=x, frequency=800) for x in (left, right)]
            low = [x - h for x, h in zip((left, right), high)]
            low = [amount * (1.0 - throb) * x for x in low]
            s1 = Balance2.ar(left=low[0], right=low[1], position=bal1)
            high = [
                amount
                * (1.0 - distance)
                * DelayC.ar(source=h, maximum_delay_time=1, delay_time=distance / 343)
                for h in high
            ]
            s2 = Balance2.ar(left=high[0], right=high[1], position=bal2)
            return 1.2 * (s1 + s2)

        if num_channels == 2:
            # sclang's in.dup gives [[l, r], [l, r]], so Balance2 expands per
            # input channel.
            rotors = [rotor(c, c) for c in chans]
        elif num_channels == 1:
            rotors = [rotor(chans[0], chans[0])]
        else:
            # in.clump(2).sum, with sclang's wrap-around for unequal sizes.
            acc = chans[0:2]
            for i in range(2, num_channels, 2):
                part = chans[i : i + 2]
                size = max(len(acc), len(part))
                acc = [acc[k % len(acc)] + part[k % len(part)] for k in range(size)]
            rotors = [rotor(acc[0], acc[1 % len(acc)])]
        dirt_pause(
            [list(r) for r in rotors] if len(rotors) > 1 else rotors[0],
            b["dirt_resumed"],
            grace_time=4,
        )
        env = _asr(b["gate"])
        rotors = [r * env for r in rotors]
        if num_channels == 1:
            snd = rotors[0][0] + rotors[0][1]
        elif num_channels > 2:
            saw = LFSaw.kr(frequency=lrate)
            snd = PanAz.ar(
                channel_count=num_channels, source=rotors[0][0], position=saw
            ) + PanAz.ar(channel_count=num_channels, source=rotors[0][1], position=saw + 1)
        if num_channels == 2:
            # Out.ar(bus, [[p0, p1], [q0, q1]]) expands to Out(bus, p0, q0) and
            # Out(bus, p1, q1): each rotor's two outputs sum into one channel.
            for k in range(2):
                Out.ar(bus=b["effectBus"], source=[r[k] for r in rotors])
        else:
            Out.ar(bus=b["effectBus"], source=snd)
    return b.build(name=f"dirt_leslie{num_channels}")


MODULE_DEFS: list[Callable[[int], SynthDef]] = [
    gate,
    out,
    vowel,
    crush,
    coarse,
    hpf,
    bpf,
    shape,
    lpf,
    envelope,
    grain_envelope,
    pitch_shift,
    tremolo,
    phaser,
]
GLOBAL_DEFS: list[Callable[[int], SynthDef]] = [monitor, rms, delay, reverb, leslie]


def core_synthdefs(num_channels: int) -> list[SynthDef]:
    """Every core and global SynthDef SuperDirt builds for *num_channels*."""
    defs = [
        f(s, num_channels)
        for f in (sample, sample_long, stretch_sample)
        for s in range(1, MAX_SAMPLE_CHANNELS + 1)
    ]
    defs += [f(num_channels) for f in MODULE_DEFS + GLOBAL_DEFS]
    return defs
