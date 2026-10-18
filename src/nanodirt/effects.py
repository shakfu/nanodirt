"""SuperDirt's extra effect modules (library/default-effects-extra.scd).

Each module adds one SynthDef to an event when its parameter is present; they
run after the core modules, in this order, before ``out_to`` and ``map_from``.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from nanosynth.osc import OscMessage
from nanosynth.synthdef import SynthDef, SynthDefBuilder
from nanosynth.ugens import (
    BPF,
    DC,
    FFT,
    HPF,
    IFFT,
    LPF,
    RHPF,
    RLPF,
    Amplitude,
    Clip,
    DelayC,
    DelayN,
    DynKlank,
    FreqShift,
    In,
    LeakDC,
    LocalBuf,
    PV_BinScramble,
    PV_BinShift,
    PV_BrickWall,
    PV_ConformalMap,
    PV_MagSmear,
    PV_RectComb,
    ReplaceOut,
    Select,
    SelectX,
    SinOsc,
    ToggleFF,
    XLine,
)
from nanosynth.ugens.sc3 import CrossoverDistortion, PV_Freeze, PV_SpectralEnhance, Squiz, WaveLoss

from .instruments import _builder

if TYPE_CHECKING:
    from .dirt import Dirt, Event, Module


def _effect(
    name: str,
    n: int,
    controls: list[tuple[str, Any]],
    body: Callable[[SynthDefBuilder, list[Any]], Any],
) -> SynthDef:
    """``|out, ...|`` with rates ``[\\ir]``: ``out`` at scalar rate, the rest control rate."""
    with _builder([("out", 0, "ir"), *[(c, v, "kr") for c, v in controls]]) as b:
        signal = In.ar(bus=b["out"], channel_count=n)
        channels = list(signal) if n > 1 else [signal]
        out = body(b, channels)
        ReplaceOut.ar(bus=b["out"], source=out)
    return b.build(name=f"{name}{n}")


def _spectral(
    chains_op: Callable[[Any], Any], tanh: bool = False
) -> Callable[[SynthDefBuilder, list[Any]], Any]:
    """``signal.asArray.collect { FFT(LocalBuf(2048), x) }``, a PV op, then IFFT."""

    def body(b: SynthDefBuilder, channels: list[Any]) -> Any:
        outs = []
        for x in channels:
            chain = FFT.kr(buffer_id=LocalBuf.ir(channel_count=1, frame_count=2048), source=x)
            y = IFFT.ar(pv_chain=chains_op(chain))
            outs.append(y.tanh_() if tanh else y)
        return outs

    return body


def waveloss(n: int) -> SynthDef:
    return _effect("waveloss", n, [("drop", 1)],
                   lambda b, ch: [WaveLoss.ar(source=x, drop=b["drop"], outof=100, mode=2) for x in ch])  # fmt: skip


def squiz(n: int) -> SynthDef:
    return _effect("squiz", n, [("pitchratio", 1)],
                   lambda b, ch: [Squiz.ar(source=x, pitchratio=b["pitchratio"]) for x in ch])  # fmt: skip


def fshift(n: int) -> SynthDef:
    def body(b: SynthDefBuilder, ch: list[Any]) -> Any:
        shift = b["freq"] * b["fshiftnote"] + b["fshift"]
        return [FreqShift.ar(source=x, frequency=shift, phase=b["fshiftphase"]) for x in ch]

    return _effect(
        "dirt_fshift", n, [("fshift", 0), ("fshiftphase", 0), ("fshiftnote", 0), ("freq", 0)], body
    )


def triode(n: int) -> SynthDef:
    def body(b: SynthDefBuilder, ch: list[Any]) -> Any:
        sc = b["triode"] * 10 + 1e-3
        return [LeakDC.ar(source=(x * (x > 0)) + ((x * sc).tanh_() / sc * (x < 0))) for x in ch]

    return _effect("dirt_triode", n, [("triode", 0)], body)


def krush(n: int) -> SynthDef:
    def body(b: SynthDefBuilder, ch: list[Any]) -> Any:
        k = b["krush"]
        freq = Select.kr(selector=b["kcutoff"] > 0, sources=[DC.kr(source=4000), b["kcutoff"]])
        outs = []
        for orig in ch:
            sig = (orig.squared() + (k * orig)) / (orig.squared() + (abs(orig) * (k - 1.0)) + 1.0)
            sig = RLPF.ar(source=sig, frequency=freq.clip(20, 10000), reciprocal_of_q=1)
            outs.append(SelectX.ar(which=k * 2.0, sources=[orig, sig]))
        return outs

    return _effect("dirt_krush", n, [("krush", 0), ("kcutoff", 0)], body)


def octer(n: int) -> SynthDef:
    def body(b: SynthDefBuilder, ch: list[Any]) -> Any:
        o1, o2, o3 = b["octer"], b["octersub"], b["octersubsub"]
        outs = []
        for x in ch:
            oct1 = 2.0 * LeakDC.ar(source=abs(x))
            sub = LPF.ar(source=x, frequency=440)
            oct2 = ToggleFF.ar(trigger=sub)
            oct3 = ToggleFF.ar(trigger=oct2)
            y = SelectX.ar(which=o1, sources=[x, o1 * oct1, DC.ar(source=0)])
            outs.append(y + (o2 * oct2 * sub) + (o3 * oct3 * sub))
        return outs

    return _effect("dirt_octer", n, [("octer", 0), ("octersub", 0), ("octersubsub", 0)], body)


def ring(n: int) -> SynthDef:
    def body(b: SynthDefBuilder, ch: list[Any]) -> Any:
        ringf = b["ringf"]
        sweep = XLine.kr(start=ringf, stop=ringf + b["ringdf"], duration=1)
        mod = b["ring"] * SinOsc.ar(frequency=Clip.kr(source=sweep, minimum=20, maximum=20000))
        return [x.ring1(mod) for x in ch]

    return _effect("dirt_ring", n, [("ring", 0), ("ringf", 0), ("ringdf", 0)], body)


def distort(n: int) -> SynthDef:
    def body(b: SynthDefBuilder, ch: list[Any]) -> Any:
        d = b["distort"]
        mods = [CrossoverDistortion.ar(source=x, amp=0.2, smooth=0.01) for x in ch]
        # DynKlank expands over the channels: one resonator bank each.
        freqs = [60, 61, 240, 3000 + SinOsc.ar(frequency=62) * 100]
        klanks = [
            DynKlank.ar(source=x, frequencies=freqs, decay_times=[0.1, 0.1, 0.05, 0.01]) for x in ch
        ]
        mods = [m + (0.1 * d * k) for m, k in zip(mods, klanks)]
        mods = [(m.cubed() * 8).softclip() * 0.5 for m in mods]
        return [SelectX.ar(which=d, sources=[x, m]) for x, m in zip(ch, mods)]

    return _effect("dirt_distort", n, [("distort", 0)], body)


def spectral_delay(n: int) -> SynthDef:
    size, max_delay = 16, 0.2

    def body(b: SynthDefBuilder, ch: list[Any]) -> Any:
        delay_time = b["tsdelay"] * max_delay
        outs = []
        for x in ch:
            filtered = None
            for i in range(1, size + 1):
                freq = 40 * (17000 / 40) ** ((i - 1) / (size - 1))  # i.linexp(1, size, 40, 17000)
                band = BPF.ar(source=x, frequency=freq, reciprocal_of_q=0.005)
                t = (i & b["xsdelay"]) * (1 / size) * delay_time
                d = DelayN.ar(source=band, maximum_delay_time=max_delay, delay_time=t)
                filtered = d if filtered is None else filtered + d
            outs.append(x * 0.2 + (filtered * 4))
        return outs

    return _effect("spectral-delay", n, [("tsdelay", 0.5), ("xsdelay", 0.5)], body)


def spectral_freeze(n: int) -> SynthDef:
    return _effect("spectral-freeze", n, [("freeze", 0)], lambda b, ch: _spectral(
        lambda c: PV_Freeze.kr(buffer=c, freeze=b["freeze"]))(b, ch))  # fmt: skip


def spectral_comb(n: int) -> SynthDef:
    return _effect("spectral-comb", n, [("comb", 0)], lambda b, ch: _spectral(
        lambda c: PV_RectComb.kr(pv_chain=c, num_teeth=256 * b["comb"], width=1 - b["comb"]))(b, ch))  # fmt: skip


def spectral_smear(n: int) -> SynthDef:
    return _effect("spectral-smear", n, [("smear", 0)], lambda b, ch: _spectral(
        lambda c: PV_MagSmear.kr(pv_chain=c, bins=b["smear"].linexp(0.0, 1.0, 1, 64)))(b, ch))  # fmt: skip


def spectral_scram(n: int) -> SynthDef:
    return _effect("spectral-scram", n, [("scram", 0)], lambda b, ch: _spectral(
        lambda c: PV_BinScramble.kr(pv_chain=c, wipe=b["scram"], width=b["scram"]))(b, ch))  # fmt: skip


def spectral_binshift(n: int) -> SynthDef:
    return _effect("spectral-binshift", n, [("binshift", 0)], lambda b, ch: _spectral(
        lambda c: PV_BinShift.kr(pv_chain=c, stretch=b["binshift"].linlin(0.0, 1.0, 0.01, 4.0),
                                 shift=b["binshift"] * 10, interpolate=1))(b, ch))  # fmt: skip


def spectral_hbrick(n: int) -> SynthDef:
    return _effect("spectral-hbrick", n, [("hbrick", 0)], lambda b, ch: _spectral(
        lambda c: PV_BrickWall.kr(pv_chain=c, wipe=b["hbrick"] * 0.6))(b, ch))  # fmt: skip


def spectral_lbrick(n: int) -> SynthDef:
    return _effect("spectral-lbrick", n, [("lbrick", 0)], lambda b, ch: _spectral(
        lambda c: PV_BrickWall.kr(pv_chain=c, wipe=b["lbrick"].linlin(0.0, 1.0, 0.0, -1.0)))(b, ch))  # fmt: skip


def spectral_conformer(n: int) -> SynthDef:
    return _effect("spectral-conformer", n, [("real", 0.5), ("imag", 0.5)], lambda b, ch: _spectral(
        lambda c: PV_ConformalMap.kr(pv_chain=c, areal=b["real"].linlin(0.0, 1.0, 0.01, 2.0),
                                     aimag=b["imag"].linlin(0.0, 1.0, 0.01, 10.0)), tanh=True)(b, ch))  # fmt: skip


def spectral_enhance(n: int) -> SynthDef:
    return _effect("spectral-enhance", n, [("enhance", 0.5)], lambda b, ch: _spectral(
        lambda c: PV_SpectralEnhance.kr(
            buffer=c, num_partials=b["enhance"].linlin(0.0, 1.0, 1, 16),
            ratio=b["enhance"].linlin(0.0, 1.0, 1.0, 5.0), strength=b["enhance"].linlin(0.0, 1.0, 0.0, 0.99),
        ), tanh=True)(b, ch))  # fmt: skip


def dj_filter(n: int) -> SynthDef:
    def body(b: SynthDefBuilder, ch: list[Any]) -> Any:
        lpf = b["djf"].linexp(0, 0.5, 20, 10000)
        hpf = b["djf"].linexp(0.5, 1, 20, 10000)
        return [RHPF.ar(source=RLPF.ar(source=x, frequency=lpf), frequency=hpf) for x in ch]

    return _effect("dj-filter", n, [("djf", 0)], body)


def compressor(n: int) -> SynthDef:
    def body(b: SynthDefBuilder, ch: list[Any]) -> Any:
        thresh, ratio, attack, release = b["cThresh"], b["cRatio"], b["cAttack"], b["cRelease"]
        ins = [x * b["cTrim"].dbamp() for x in ch]
        e = ins[0]
        for x in ins[1:]:
            e = e + x
        e = e / len(ins)  # .mean
        e = HPF.ar(source=e, frequency=b["cHpf"])
        e = Amplitude.ar(source=e, attack_time=attack, release_time=release)
        o = e.ampdb().excess(thresh.ampdb())
        reduction = o.max_(0.0) * (ratio.reciprocal() - 1)
        kneelag = attack * b["cKnee"]
        reduction = reduction.lag(kneelag).dbamp()
        delay = (attack + release + kneelag).lag(0.4)
        makeup = (-(thresh.ampdb() * (1 - (1 / ratio)))).dbamp()
        outs = []
        for x in ins:
            look = DelayC.ar(source=x, maximum_delay_time=0.5, delay_time=delay)
            y = (look * reduction * makeup).softclip() * b["cGain"]
            outs.append(LeakDC.ar(source=y))
        return outs

    controls = [("cThresh", 0), ("cRatio", 4), ("cAttack", 0.01), ("cRelease", 0.1), ("cTrim", 0), ("cHpf", 50),
                ("cKnee", 0), ("cGain", 0)]  # fmt: skip
    return _effect("compressor", n, controls, body)


EFFECT_DEFS: list[Callable[[int], SynthDef]] = [
    waveloss, squiz, fshift, triode, krush, octer, ring, distort, spectral_delay, spectral_freeze,
    spectral_comb, spectral_smear, spectral_scram, spectral_binshift, spectral_hbrick, spectral_lbrick,
    spectral_conformer, spectral_enhance, dj_filter, compressor,
]  # fmt: skip


def effect_synthdefs(num_channels: int) -> list[SynthDef]:
    return [f(num_channels) for f in EFFECT_DEFS]


# -- Modules ----------------------------------------------------------------------


def _send(name: str, *keys: tuple[str, str]) -> Callable[["Dirt", "Event", int], list[OscMessage]]:
    def module(dirt: Dirt, e: Event, group: int) -> list[OscMessage]:
        args: list[Any] = []
        for control, key in keys:
            args += [control, e[key]]
        args += ["out", e["out"]]
        return dirt.synth_messages(f"{name}{dirt.num_channels}", args, group)

    return module


def _compressor(dirt: Dirt, e: Event, group: int) -> list[OscMessage]:
    def get(key: str, default: Any) -> Any:
        v = e[key]
        return default if v is None else v

    args = [
        "cThresh", e["cThresh"], "cRatio", get("cRatio", 4), "cAttack", get("cAttack", 0.01),
        "cRelease", get("cRelease", 0.1), "cTrim", get("cTrim", 0), "cHpf", get("cHpf", 50),
        "cKnee", get("cKnee", 0), "cGain", get("cGain", 1), "out", e["out"],
    ]  # fmt: skip
    return dirt.synth_messages(f"compressor{dirt.num_channels}", args, group)


def _has(*keys: str) -> Callable[[Event], bool]:
    return lambda e: any(e[k] is not None for k in keys)


EXTRA_MODULES: list[Module] = [
    ("waveloss", _has("waveloss"), _send("waveloss", ("drop", "waveloss"))),
    ("squiz", _has("squiz"), _send("squiz", ("pitchratio", "squiz"))),
    ("fshift", _has("fshift"), _send("dirt_fshift", ("fshift", "fshift"), ("fshiftphase", "fshiftphase"),
                                     ("fshiftnote", "fshiftnote"), ("freq", "freq"))),
    ("triode", _has("triode"), _send("dirt_triode", ("triode", "triode"))),
    ("krush", _has("krush"), _send("dirt_krush", ("krush", "krush"), ("kcutoff", "kcutoff"))),
    ("octer", _has("octer", "octersub", "octersubsub"),
     _send("dirt_octer", ("octer", "octer"), ("octersub", "octersub"), ("octersubsub", "octersubsub"))),
    ("ring", _has("ring"), _send("dirt_ring", ("ring", "ring"), ("ringf", "ringf"), ("ringdf", "ringdf"))),
    ("distort", _has("distort"), _send("dirt_distort", ("distort", "distort"))),
    ("spectral-delay", _has("tsdelay", "xsdelay"),
     _send("spectral-delay", ("xsdelay", "xsdelay"), ("tsdelay", "tsdelay"))),
    ("spectral-freeze", _has("freeze"), _send("spectral-freeze", ("freeze", "freeze"))),
    ("spectral-comb", _has("comb"), _send("spectral-comb", ("comb", "comb"))),
    ("spectral-smear", _has("smear"), _send("spectral-smear", ("smear", "smear"))),
    ("spectral-scram", _has("scram"), _send("spectral-scram", ("scram", "scram"))),
    ("spectral-binshift", _has("binshift"), _send("spectral-binshift", ("binshift", "binshift"))),
    ("spectral-hbrick", _has("hbrick"), _send("spectral-hbrick", ("hbrick", "hbrick"))),
    ("spectral-lbrick", _has("lbrick"), _send("spectral-lbrick", ("lbrick", "lbrick"))),
    ("spectral-conformer", _has("real", "imag"), _send("spectral-conformer", ("real", "real"), ("imag", "imag"))),
    ("spectral-enhance", _has("enhance"), _send("spectral-enhance", ("enhance", "enhance"))),
    ("dj-filter", _has("djf"), _send("dj-filter", ("djf", "djf"))),
    ("compressor", _has("cThresh"), _compressor),
]  # fmt: skip

__all__ = ["EFFECT_DEFS", "EXTRA_MODULES", "effect_synthdefs", "math"]
