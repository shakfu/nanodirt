"""nanodirt's extra effects against SuperDirt's, by rendered audio (tests/parity.py)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from parity import FIXTURES, SOURCE_BUS, compare, render

from nanodirt import effects as fx

N = 2
TOLERANCE = 1e-4
CASES: dict[str, dict[str, Any]] = {
    "waveloss": {"drop": 30},
    "squiz": {"pitchratio": 2},
    "dirt_fshift": {"fshift": 100, "fshiftnote": 0.5, "freq": 220, "fshiftphase": 0.3},
    "dirt_triode": {"triode": 0.6},
    "dirt_krush": {"krush": 0.4, "kcutoff": 3000},
    "dirt_octer": {"octer": 0.5, "octersub": 0.4, "octersubsub": 0.3},
    "dirt_ring": {"ring": 0.7, "ringf": 300, "ringdf": 200},
    "dirt_distort": {"distort": 0.6},
    "spectral-delay": {"tsdelay": 0.6, "xsdelay": 5},
    "spectral-freeze": {"freeze": 0},
    "spectral-comb": {"comb": 0.3},
    "spectral-smear": {"smear": 0.4},
    "spectral-scram": {"scram": 0.3},
    "spectral-binshift": {"binshift": 0.3},
    "spectral-hbrick": {"hbrick": 0.4},
    "spectral-lbrick": {"lbrick": 0.4},
    "spectral-conformer": {"real": 0.3, "imag": 0.6},
    "spectral-enhance": {"enhance": 0.4},
    "dj-filter": {"djf": 0.3},
    "compressor": {
        "cThresh": 0.1,
        "cRatio": 4,
        "cAttack": 0.01,
        "cRelease": 0.1,
        "cGain": 1,
        "cHpf": 50,
    },
}
FACTORIES = {f(N).effective_name[: -len(str(N))]: f for f in fx.EFFECT_DEFS}
RANDOM = {"spectral-scram", "waveloss"}  # random draws in graph order differ


def _rms(xs: list[float]) -> float:
    return (sum(x * x for x in xs if x == x) / len(xs)) ** 0.5


@pytest.mark.parametrize("name", sorted(CASES))
def test_matches_superdirt(name: str, tmp_path: Path) -> None:
    ours = FACTORIES[name](N)
    controls = {"out": SOURCE_BUS, **CASES[name]}
    fixture = FIXTURES / f"{ours.effective_name}.scsyndef"
    ref = render(fixture.read_bytes(), ours.effective_name, controls, tmp_path / "ref.wav")
    got = render(ours.compile(), ours.effective_name, controls, tmp_path / "got.wav")
    diff, peak = compare(ref, got)
    assert peak > 0.001
    if name in RANDOM:
        assert abs(_rms(ref) - _rms(got)) < 0.3 * _rms(ref)
    else:
        assert diff <= TOLERANCE * max(1.0, peak), f"max diff {diff:.3g}, peak {peak:.3g}"


def test_every_module_has_a_synthdef() -> None:
    assert len(fx.EXTRA_MODULES) == len(fx.EFFECT_DEFS) == len(CASES)
