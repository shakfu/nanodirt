"""superdough's synth sources (nanodirt.dough) against the spec and the superdough oracle.

The oracle tests are opt-in, as tests/test_superdough_oracle.py.
"""

from __future__ import annotations

import math
import os
import struct
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from nanosynth.osc import OscMessage
from nanosynth.score import Score
from nanosynth.scsynth import Options

from nanodirt import dough

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "superdough_oracle"))
import superdough_oracle  # noqa: E402

RATE = 44100
DEFS = {d.effective_name: d for d in dough.synthdefs(2)}


def nrt(event: dict[str, Any], seconds: float) -> list[float]:
    """Left channel of nanodirt's voice for *event*, rendered offline."""
    found = dough.voice(event, 2)
    assert found is not None
    name, controls = found
    score = Score()
    score.add_synthdef(0.0, DEFS[name])
    score.add(
        0.0, OscMessage("/s_new", name, 1000, 0, 0, *[x for kv in controls.items() for x in kv])
    )
    score.add(seconds, OscMessage("/c_set", 0, 0))
    path = Path(tempfile.mkdtemp()) / "voice.wav"
    score.render(
        path,
        sample_format="float",
        output_channels=2,
        sample_rate=RATE,
        options=Options(verbosity=-1),
    )
    raw = path.read_bytes()
    i = raw.find(b"data")
    n = struct.unpack("<I", raw[i + 4 : i + 8])[0]
    return list(struct.unpack(f"<{n // 4}f", raw[i + 8 : i + 8 + n - n % 4]))[0::2]


def rms(x: list[float], a: float, b: float) -> float:
    seg = x[int(a * RATE) : int(b * RATE)]
    return math.sqrt(sum(v * v for v in seg) / len(seg))


class TestRules:
    def test_adsr_defaults_and_fill(self) -> None:
        d = dough.SOURCE_ENVELOPE
        assert dough.adsr_values((None, None, None, None), d) == d
        assert dough.adsr_values((0.1, None, None, None), d) == (0.1, 0.001, 1.0, 0.01)
        assert dough.adsr_values((None, 0.2, None, None), d) == (0.001, 0.2, 0.001, 0.01)
        assert dough.adsr_values((None, None, 2.0, 0.0), d) == (0.001, 0.001, 1.0, 0.01)

    def test_envelope_points(self) -> None:
        assert dough.envelope_points((0.1, 0.2, 0.5, 0.3), 1.0) == (
            [1.0, 0.5, 0.5],
            [0.1, 0.2, pytest.approx(0.7), 0.3],
        )
        # ends during decay: stops where the full envelope would be at the end
        levels, times = dough.envelope_points((0.1, 0.2, 0.5, 0.3), 0.2)
        assert levels == [1.0, pytest.approx(0.75), pytest.approx(0.75)]
        assert times == [0.1, pytest.approx(0.1), 0.0, 0.3]
        # ends during attack
        assert dough.envelope_points((0.4, 0.2, 0.5, 0.3), 0.1) == (
            [0.25, 0.25, 0.25],
            [0.1, 0.0, 0.0, 0.3],
        )

    def test_frequency(self) -> None:
        assert dough.frequency({}) == pytest.approx(65.406, rel=1e-4)  # note 36
        assert dough.frequency({"note": 69}) == 440.0
        assert dough.frequency({"note": "a3"}) == pytest.approx(220.0)  # c3 is 48
        assert dough.frequency({"note": 69, "octave": -1}) == 220.0
        assert dough.frequency({"freq": 100, "note": 69}) == 100.0

    def test_sources(self) -> None:
        assert dough.voice({"s": "saw"}, 2)[0] == "dough_sawtooth2"  # type: ignore[index]
        assert dough.voice({"s": "Sine"}, 2)[0] == "dough_sine2"  # type: ignore[index]
        assert dough.voice({"s": "bd"}, 2) is None
        assert dough.voice({"s": "sine", "bank": "x"}, 2) is None  # superdough plays x_sine
        assert dough.voice({"s": "crackle"}, 2)[1]["density"] == 0.02  # type: ignore[index]

    def test_gain_chain(self) -> None:
        amp = dough.voice({"s": "sine", "gain": 0.5, "velocity": 0.5, "postgain": 2}, 2)
        assert amp is not None and amp[1]["amp"] == pytest.approx(0.3 * 0.5 * 0.5 * 2)


def test_sine_level_offline() -> None:
    # 0.3 source * 0.8 gain * 0.6 sustain = 0.144 amplitude
    x = nrt({"s": "sine", "note": 69, "delta": 0.5}, 0.7)
    assert rms(x, 0.1, 0.45) == pytest.approx(0.144 / math.sqrt(2), rel=1e-3)
    assert rms(x, 0.53, 0.7) == 0.0


# -- against superdough ----------------------------------------------------------------

oracle = pytest.mark.skipif(
    not (os.environ.get("NANODIRT_TEST_SUPERDOUGH") and superdough_oracle.available()),
    reason="set NANODIRT_TEST_SUPERDOUGH=1 after superdough_oracle.py setup",
)


@pytest.fixture(scope="module")
def sd() -> Iterator[Any]:
    pytest.importorskip("playwright")
    with superdough_oracle.Superdough() as s:
        yield s


def reference(sd: Any, value: dict[str, Any], duration: float, seconds: float) -> list[float]:
    frames = sd.render([{"value": value, "t": 0.0, "duration": duration}], seconds, RATE)
    return [left for left, _ in frames]


ENVELOPES: list[dict[str, Any]] = [
    {},
    {"attack": 0.05, "release": 0.1},
    {"decay": 0.1},
    {"attack": 0.2},
    {"attack": 0.1, "decay": 0.2, "sustain": 0.3, "release": 0.2},
    {"attack": 0.4},  # longer than the note
    {"gain": 0.5, "velocity": 0.7, "postgain": 1.5},
    {"note": "e4", "octave": 1},
]


@oracle
@pytest.mark.parametrize("extra", ENVELOPES, ids=lambda e: ",".join(e) or "default")
def test_sine_matches_sample_by_sample(sd: Any, extra: dict[str, Any]) -> None:
    value = {"s": "sine", "note": 69, **extra}
    ref = reference(sd, value, 0.3, 0.8)
    got = nrt({**value, "delta": 0.3}, 0.8)
    assert max(abs(a - b) for a, b in zip(ref, got)) < 1e-3


@oracle
@pytest.mark.parametrize("s", ["triangle", "square", "sawtooth"])
@pytest.mark.parametrize("note", [33, 57, 81])
def test_waveform_level(sd: Any, s: str, note: int) -> None:
    ref = reference(sd, {"s": s, "note": note}, 0.6, 0.8)
    got = nrt({"s": s, "note": note, "delta": 0.6}, 0.8)
    assert rms(got, 0.1, 0.55) == pytest.approx(rms(ref, 0.1, 0.55), rel=0.03)
    assert rms(got, 0.7, 0.8) == rms(ref, 0.7, 0.8) == 0.0


@oracle
@pytest.mark.parametrize("s", ["white", "pink", "brown", "crackle"])
def test_noise_level(sd: Any, s: str) -> None:
    ref = reference(sd, {"s": s}, 1.8, 2.0)
    got = nrt({"s": s, "delta": 1.8}, 2.0)
    tolerance = {"white": 0.02, "pink": 0.15, "brown": 0.3, "crackle": 0.3}[s]
    assert rms(got, 0.1, 1.75) == pytest.approx(rms(ref, 0.1, 1.75), rel=tolerance)
