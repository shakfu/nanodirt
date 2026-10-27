"""nanodirt's instruments against SuperDirt's, by rendered audio (tests/parity.py)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from parity import FIXTURES, SOURCE_BUS, compare, render

from nanodirt import instruments as ins

N = 2
TOLERANCE = 1e-4
BASE = {
    "out": 0,
    "freq": 330.0,
    "sustain": 0.4,
    "pan": 0.3,
    "n": 2.0,
    "speed": 1.0,
    "accelerate": 0.1,
}

# name -> extra controls
CASES: dict[str, dict[str, Any]] = {
    "imp": {"begin": 0.2, "end": 0.8},
    "psin": {"modfreq": 30, "begin": 0.1},
    "gabor": {"width": 0.2, "sustain": 0.05},
    "dirac": {},
    "cyclo": {"cycle": 3.5, "ratio": 1.2, "begin": 0.1, "end": 0.7},
    "dirt_from": {"in": SOURCE_BUS},
    "supermandolin": {"detune": 0.4},
    "supergong": {"voice": 0.5, "decay": 0.8},
    "superpiano": {"velocity": 0.8, "muffle": 0.7},
    "superhex": {"rate": 2},
    "superkick": {"pitch1": 1.5, "decay": 0.8},
    "super808": {"rate": 1.3, "voice": 0.2},
    "superhat": {},
    "supersnare": {"decay": 0.7},
    "superclap": {"delay": 1.2, "pitch1": 0.9, "rate": 1.1},
    "supersiren": {},
    "supersquare": {"voice": 0.3, "decay": 0.2, "resonance": 0.3, "lfo": 0.8},
    "supersaw": {"voice": 0.3, "decay": 0.2},
    "superpwm": {"voice": 0.4, "decay": 0.1},
    "supercomparator": {"voice": 0.7},
    "superchip": {"slide": 0.3, "voice": 0.6, "pitch2": 1.5},
    "supernoise": {"voice": 0.4, "slide": 0.2, "resonance": 0.3},
    "superfork": {},
    "superhammond": {"voice": 3.4, "perc": 0.5, "decay": 0.3},
    "supervibe": {"decay": 0.4, "velocity": 0.8, "detune": 0.3},
    "superhoover": {"slide": 1, "decay": 0.2},
    "superzow": {"slide": 1.5, "detune": 2, "decay": 0.1},
    "superstatic": {"freq": 60.0},
    "supergrind": {"detune": 3, "voice": 0.5, "rate": 2},
    "superprimes": {"detune": 0.3, "voice": 0.1},  # GVerb room size must stay valid
    "superwavemechanics": {"detune": 0.5, "voice": 0.3, "resonance": 0.4},
    "supertron": {"voice": 0.3, "detune": 1},
    "superreese": {"detune": 2, "voice": 0.5},
    "superfm": {
        "voice": 0,
        "amp1": 1,
        "amp2": 0,
        "ratio2": 2,
        "mod12": 0.6,
        **{f"detune{i}": 0.0 for i in range(1, 7)},
    },
    "soskick": {"pitch1": 40, "pitch2": 0.3, "speed": 0.5},
    "soshats": {"resonance": 0.5},
    "sostoms": {"voice": 0.7},
    "sossnare": {},
}
FACTORIES = {getattr(f, "__name__", ""): f for f in ins.EXTRA_INSTRUMENTS + ins.DEFAULT_INSTRUMENTS}

# Random draws: noise and Rand UGens consume the seeded generator in graph
# order, which differs between the compilers; build-time random constants
# (superhammond's phases) differ per SuperDirt load. Compared by level only.
RANDOM = {
    "imp", "supermandolin", "superkick", "superhat", "supersnare", "superclap", "supernoise",
    "superhammond", "supervibe", "superstatic", "superwavemechanics", "superprimes",
    "supercomparator", "soskick", "sossnare", "supergrind", "sostoms",
}  # fmt: skip


def _rms(xs: list[float]) -> float:
    return (sum(x * x for x in xs if x == x) / len(xs)) ** 0.5


@pytest.mark.parametrize("name", sorted(CASES))
def test_matches_superdirt(name: str, tmp_path: Path) -> None:
    ours = FACTORIES[name](N)
    assert ours.effective_name == name
    controls = {**BASE, **CASES[name]}
    opts = {"source": name == "dirt_from", "tap": False}
    ref = render(
        (FIXTURES / f"{name}.scsyndef").read_bytes(), name, controls, tmp_path / "ref.wav", **opts
    )
    got = render(ours.compile(), name, controls, tmp_path / "got.wav", **opts)
    diff, peak = compare(ref, got)
    assert peak > 0.001, "silent reference: the comparison proves nothing"
    if name in RANDOM:
        assert abs(_rms(ref) - _rms(got)) < 0.3 * _rms(ref), (_rms(ref), _rms(got))
    else:
        assert diff <= TOLERANCE * max(1.0, peak), f"max diff {diff:.3g}, peak {peak:.3g}"
