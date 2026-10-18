"""nanodirt's SynthDefs against SuperDirt's, by rendered audio (tests/parity.py)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from parity import FIXTURES, SOURCE_BUS, compare, render, write_test_wav

from nanodirt import synthdefs as sd

N = 2
TOLERANCE = 1e-4
EFFECT = {"source": True, "tap": True}
SAMPLE = {"source": False, "tap": False}

# name -> (factory, controls, render options)
CASES: dict[str, tuple[Any, dict[str, Any], dict[str, Any]]] = {
    "dirt_gate2": (
        sd.gate,
        {
            "in": SOURCE_BUS,
            "out": 0,
            "sustain": 0.2,
            "fadeInTime": 0.01,
            "fadeTime": 0.05,
            "amp": 0.4,
            "gain": 1.2,
            "overgain": 0.1,
        },
        {"source": True, "tap": False},
    ),
    "dirt_out2": (
        sd.out,
        {"out": SOURCE_BUS, "to": 0, "sustain": 0.2, "through": 0.5, "amp": 0.7},
        EFFECT,
    ),
    "dirt_vowel2": (
        sd.vowel,
        {
            "out": SOURCE_BUS,
            "resonance": 0.3,
            "cutoff": 0.2,
            "vowelFreqs": [660, 1120, 2750, 3000, 3350],
            "vowelAmps": [1.0, 0.5, 0.07, 0.06, 0.012],
            "vowelRqs": [1 / 80, 1 / 90, 1 / 120, 1 / 130, 1 / 140],
        },
        EFFECT,
    ),
    "dirt_crush2": (sd.crush, {"out": SOURCE_BUS, "crush": 3}, EFFECT),
    "dirt_coarse2": (sd.coarse, {"out": SOURCE_BUS, "coarse": 7}, EFFECT),
    "dirt_hpf2": (sd.hpf, {"out": SOURCE_BUS, "hcutoff": 900, "hresonance": 0.3}, EFFECT),
    "dirt_bpf2": (sd.bpf, {"out": SOURCE_BUS, "bandqf": 1200, "bandq": 4}, EFFECT),
    "dirt_shape2": (sd.shape, {"out": SOURCE_BUS, "shape": 0.6}, EFFECT),
    "dirt_lpf2": (sd.lpf, {"out": SOURCE_BUS, "cutoff": 700, "resonance": 0.4}, EFFECT),
    "dirt_envelope2": (
        sd.envelope,
        {"out": SOURCE_BUS, "attack": 0.05, "hold": 0.1, "release": 0.2, "curve": -2},
        EFFECT,
    ),
    "dirt_grain_envelope2": (
        sd.grain_envelope,
        {"out": SOURCE_BUS, "sustain": 0.4, "tilt": 0.3, "plat": 0.2, "curve": -3},
        EFFECT,
    ),
    "dirt_pitch_shift2": (
        sd.pitch_shift,
        {"out": SOURCE_BUS, "psrate": 1.5, "sustain": 0.2, "psdisp": 0},
        EFFECT,
    ),
    "dirt_tremolo2": (
        sd.tremolo,
        {"out": SOURCE_BUS, "tremolorate": 7, "tremolodepth": 0.8},
        EFFECT,
    ),
    "dirt_phaser2": (sd.phaser, {"out": SOURCE_BUS, "phaserrate": 3, "phaserdepth": 0.7}, EFFECT),
    "dirt_monitor2": (
        sd.monitor,
        {"dryBus": SOURCE_BUS, "effectBus": 40, "outBus": 0, "limitertype": 2},
        {"source": True, "tap": False},
    ),
    "dirt_delay2": (
        sd.delay,
        {
            "dryBus": SOURCE_BUS,
            "effectBus": 0,
            "delaytime": 0.05,
            "delayfeedback": 0.5,
            "delayAmp": 0.8,
            "cps": 0.5,
        },
        {"source": True, "tap": False},
    ),
    "dirt_leslie2": (
        sd.leslie,
        {"dryBus": SOURCE_BUS, "effectBus": 0, "leslie": 0.7, "lrate": 3, "lsize": 0.4},
        {"source": True, "tap": False},
    ),
    "dirt_reverb2": (
        sd.reverb,
        {"dryBus": SOURCE_BUS, "effectBus": 0, "room": 0.5, "size": 0.6, "dry": 0.2},
        {"source": True, "tap": False},
    ),
}

for ch in (1, 2):
    sample_controls = {
        "out": 0,
        "bufnum": 0,
        "sustain": 0.25,
        "begin": 0.1,
        "end": 0.9,
        "speed": 1.3,
        "endSpeed": 0.8,
        "freq": 330,
        "pan": 0.3,
    }
    CASES[f"dirt_sample_{ch}_2"] = (
        lambda n, ch=ch: sd.sample(ch, n),
        sample_controls,
        {**SAMPLE, "buffer_channels": ch},
    )
    CASES[f"dirt_sample_long_{ch}_2"] = (
        lambda n, ch=ch: sd.sample_long(ch, n),
        sample_controls,
        {**SAMPLE, "buffer_channels": ch},
    )
    CASES[f"dirt_stretchsample_{ch}_2"] = (
        lambda n, ch=ch: sd.stretch_sample(ch, n),
        {**sample_controls, "timescale": 1.7, "timescalewin": 1.2},
        {**SAMPLE, "buffer_channels": ch},
    )

# Rand UGens draw in graph order, which differs between the two compilers.
LOOSE = {"dirt_reverb2"}


@pytest.mark.parametrize("name", sorted(CASES))
def test_matches_superdirt(name: str, tmp_path: Path) -> None:
    factory, controls, opts = CASES[name]
    opts = dict(opts)
    buffer = None
    if "buffer_channels" in opts:
        buffer = tmp_path / "buf.wav"
        write_test_wav(buffer, opts.pop("buffer_channels"))
    ours = factory(N)
    assert ours.effective_name == name
    ref = render(
        (FIXTURES / f"{name}.scsyndef").read_bytes(),
        name,
        controls,
        tmp_path / "ref.wav",
        buffer=buffer,
        **opts,
    )
    got = render(ours.compile(), name, controls, tmp_path / "got.wav", buffer=buffer, **opts)
    diff, peak = compare(ref, got)
    assert peak > 0.01, "silent reference: the comparison proves nothing"
    if name in LOOSE:
        rms = lambda xs: (sum(x * x for x in xs) / len(xs)) ** 0.5  # noqa: E731
        assert abs(rms(ref) - rms(got)) < 0.25 * rms(ref)
    else:
        assert diff <= TOLERANCE, f"max diff {diff:.3g}, peak {peak:.3g}"


def test_every_core_def_is_covered() -> None:
    names = {d.effective_name for d in sd.core_synthdefs(N)}
    assert names - set(CASES) == {"dirt_rms2"}  # sends replies, renders silence
