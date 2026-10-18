"""nanodirt's bundles against stock SuperDirt's for the same events (tests/oracle.py).

Opt-in: ``NANODIRT_TEST_ORACLE=1``, with nanosynth's oracle built
(``python scripts/superdirt_oracle.py setup`` in nanosynth).
"""

from __future__ import annotations

from typing import Any

import oracle
import pytest

pytestmark = pytest.mark.skipif(not oracle.available(), reason="set NANODIRT_TEST_ORACLE=1")

BANKS = ["bd", "hh", "sn", "arpy"]
T = {"cps": 0.5625, "cycle": 0.0, "delta": 0.4444}

EVENTS: list[dict[str, Any]] = [
    {"s": "bd", "n": 0.0, **T},
    {"s": "hh", "n": 2.0, "speed": -1.0, "begin": 0.2, "end": 0.8, **T},
    {
        "s": "sn",
        "n": 1.0,
        "cutoff": 800.0,
        "resonance": 0.2,
        "room": 0.4,
        "size": 0.7,
        "orbit": 1,
        **T,
    },
    {"s": "bd", "n": 1.0, "unit": "c", "speed": 1.0, **T},
    {"s": "arpy", "n": 3.0, "note": 7.0, "legato": 0.5, **T},
    {"s": "bd", "n": 0.0, "cut": 1, **T},
    {"s": "bd", "n": 3.0, "cut": 1, **T},
    {"s": "hh", "n": 0.0, "accelerate": -1.0, "loop": 2.0, **T},
    {
        "s": "sn",
        "n": 0.0,
        "vowel": "a",
        "shape": 0.5,
        "crush": 4.0,
        "coarse": 4.0,
        "hcutoff": 300.0,
        "hresonance": 0.1,
        "bandf": 1000.0,
        "bandq": 3.0,
        "orbit": 2,
        **T,
    },
    {"s": "arpy", "n": 0.0, "delay": 0.5, "delaytime": 0.25, "delayfeedback": 0.5, "lock": 1, **T},
    {
        "s": "hh",
        "n": 1.0,
        "attack": 0.01,
        "hold": 0.05,
        "release": 0.1,
        "tilt": 0.3,
        "plat": 0.2,
        "psrate": 1.5,
        "psdisp": 0.1,
        "tremolorate": 4.0,
        "tremolodepth": 0.5,
        "phaserrate": 2.0,
        "phaserdepth": 0.4,
        **T,
    },
    {
        "s": "bd",
        "n": 2.0,
        "pan": 0.2,
        "channel": 1.0,
        "gain": 1.2,
        "overgain": 0.1,
        "amp": 0.3,
        **T,
    },
    {"s": "bd", "n": 0.0, "timescale": 1.5, **T},
    {"s": "bd", "n": 0.0, "leslie": 0.5, "lrate": 6.0, "lsize": 0.3, "orbit": 3, **T},
    {"s": "", **T},
    {"s": "nosuchsound", **T},
    {"s": "bd", "n": 1.0, "sustain": 0.05, "lag": 0.01, "offset": 0.02, **T},
    {"s": "bd", "n": -1.0, "bank": "x", **T},
    {"s": "sn", "n": 2.0, "to": 1.0, **T},
    {"s": "arpy", "n": 1.0, "cut": -2, **T},
    {"s": "arpy", "n": 1.0, "cut": -2, "orbit": 1, **T},
    {"s": "arpy", "n": 2.0, "cut": -2, **T},
    {"s": "bd", "n": 0.0, "orbit": 14, "room": 0.1, **T},
    # Instruments: arguments come from the SynthDef's controls (msgFunc).
    {"s": "superpiano", "n": 7.0, "velocity": 0.7, "legato": 1.0, **T},
    {"s": "supersaw", "note": 5.0, "voice": 0.3, "accelerate": 0.2, **T},
    {"s": "superkick", "n": 2.0, "pitch1": 1.2, **T},
    {"s": "superfm", "n": 0.0, "voice": 0, "amp1": 1, "mod12": 0.5, **T},
    {"s": "superpiano", **T},  # no n: midinote stays the symbol \none
    {"s": "imp", "n": 3.0, "begin": 0.2, "speed": 1.5, **T},
    {"s": "from", "from": 2.0, **T},
    # Extra effect modules.
    {"s": "sn", "n": 0.0, "djf": 0.3, "squiz": 2.0, "ring": 0.5, "ringf": 200.0, **T},
    {"s": "hh", "n": 0.0, "octer": 0.4, "triode": 0.5, "krush": 0.6, "distort": 0.3, **T},
    {"s": "arpy", "n": 0.0, "cThresh": 0.2, "waveloss": 20.0, "fshift": 100.0, "comb": 0.2, **T},
    {
        "s": "bd",
        "n": 0.0,
        "real": 0.4,
        "binshift": 0.2,
        "lbrick": 0.3,
        "enhance": 0.5,
        "tsdelay": 0.4,
        **T,
    },
]


@pytest.fixture(scope="module")
def both() -> tuple[list[Any], list[Any]]:
    theirs = oracle.normalize(oracle.run_superdirt(EVENTS, BANKS))
    ours = oracle.normalize(oracle.run_nanodirt(EVENTS, BANKS))
    return theirs, ours


def test_same_number_of_bundles(both: tuple[list[Any], list[Any]]) -> None:
    theirs, ours = both
    assert len(ours) == len(theirs)


def test_bundles_match(both: tuple[list[Any], list[Any]]) -> None:
    theirs, ours = both
    for i, ((a, _), (b, _)) in enumerate(zip(theirs, ours)):
        assert oracle.same(a, b), f"bundle {i}\nSuperDirt: {a}\nnanodirt:  {b}"


def test_bundle_times_match(both: tuple[list[Any], list[Any]]) -> None:
    """lag and offset shift the bundle; SuperDirt measures latency on receipt (ms jitter)."""
    theirs, ours = both
    for i, ((_, a), (_, b)) in enumerate(zip(theirs, ours)):
        assert abs(a - b) < 0.005, f"bundle {i}: SuperDirt {a:.4f} s, nanodirt {b:.4f} s"
