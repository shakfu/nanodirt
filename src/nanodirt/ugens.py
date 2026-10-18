"""SuperDirt's pseudo-UGens (SuperDirtUGens.sc), as functions over nanosynth signals."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from nanosynth.envelopes import Envelope, EnvGen
from nanosynth.synthdef import UGenOperable, UGenScalar, UGenVector
from nanosynth.ugens import (
    Balance2,
    DetectSilence,
    Impulse,
    Line,
    Mix,
    Pan2,
    PanAz,
    PauseSelf,
    Splay,
    Trig1,
)

Signal = Any  # UGenOperable | float


def _channels(signal: Signal) -> list[Signal]:
    """A signal's channels: vectors and multi-output UGens are their outputs."""
    if isinstance(signal, (list, tuple)):
        return list(signal)
    if isinstance(signal, UGenOperable) and not isinstance(signal, UGenScalar):
        return list(signal)
    return [signal]


def dirt_pan(
    signal: Signal,
    num_channels: int,
    pan: Signal,
    controls: dict[str, Signal],
    mul: Signal = 1.0,
) -> Signal:
    """``DirtPan.defaultPanningFunction``: mono sum, stereo balance, or ``DirtSplayAz``.

    *controls* holds the named controls sclang creates inline: ``span`` for two
    channels, plus ``splay``, ``panwidth`` and ``orientation`` above two.
    """
    signals = _channels(signal)
    if num_channels == 1:
        total = signals[0]
        for s in signals[1:]:
            total = total + s
        return total * mul
    if num_channels == 2:
        return dirt_pan_balance2(signals, controls["span"], pan, mul)
    return dirt_splay_az(
        num_channels,
        signals,
        controls["span"],
        pan,
        mul,
        controls["splay"],
        controls["panwidth"],
        controls["orientation"],
    )


def dirt_pan_balance2(signals: Sequence[Signal], span: Signal, pan: Signal, mul: Signal) -> Signal:
    if len(signals) == 1:
        return Pan2.ar(source=signals[0], position=pan.fold(-1, 1), level=mul)
    if len(signals) > 2:
        signals = list(Splay.ar(source=list(signals), spread=span))
    return Balance2.ar(left=signals[0], right=signals[1], position=pan.fold(-1, 1), level=mul)


def dirt_splay_az(
    num_channels: int,
    signals: Sequence[Signal],
    span: Signal,
    pan: Signal,
    mul: Signal,  # unused, as in SuperDirt
    splay: Signal,
    width: Signal,
    orientation: Signal,
) -> Signal:
    n = len(signals)
    span = span * splay.linlin(0, 1, num_channels / n, 1)
    pan = pan + 1
    channels = [
        PanAz.ar(
            channel_count=num_channels,
            source=x,
            position=i / num_channels * 2 * span + pan,
            width=width,
            orientation=orientation,
        )
        for i, x in enumerate(signals)
    ]
    return Mix.new(channels)


def dirt_gate_cut_group(
    cut_gate: Signal, release_time: Signal = 0.02, done_action: int = 2
) -> Signal:
    """``DirtGateCutGroup``: an ``Env.cutoff`` released by the ``cut_gate`` control."""
    cutoff = Envelope(
        amplitudes=[1, 0], durations=[release_time], curves=["linear"], release_node=0
    )
    return EnvGen.kr(envelope=cutoff, gate=cut_gate, done_action=done_action)


def dirt_pause(
    signal: Signal,
    dirt_resumed: Signal,
    grace_time: Signal = 1,
    pause_immediately: Signal = 0,
) -> None:
    """``DirtPause``: pause the synth after *grace_time* of silence."""
    PauseSelf.kr(trigger=Impulse.kr(frequency=0) * pause_immediately)
    # signal.asArray.abs.sum: a nested array sums element-wise, leaving one
    # DetectSilence per element.
    parts = [_channels(x) for x in _channels(signal)]
    width = max(len(p) for p in parts)
    totals = []
    for k in range(width):
        total: Signal = None
        for p in parts:
            x = abs(p[k % len(p)])
            total = x if total is None else total + x
        totals.append(total)
    resumed = Trig1.ar(source=dirt_resumed, duration=grace_time)
    for total in totals:
        DetectSilence.ar(source=total + resumed, time=grace_time, done_action=1)


def dirt_freq_scale(
    speed: Signal, accelerate: Signal, sustain: Signal, speed_freq: Signal
) -> Signal:
    """``DirtFreqScale.kr``."""
    speed = abs(speed)
    term = Line.kr(start=speed, stop=speed * (accelerate + 1), duration=sustain)
    return speed_freq * (term - 1) + 1


__all__ = [
    "UGenVector",
    "dirt_freq_scale",
    "dirt_gate_cut_group",
    "dirt_pan",
    "dirt_pan_balance2",
    "dirt_pause",
    "dirt_splay_az",
]
