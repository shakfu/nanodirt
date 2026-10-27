"""The superdough oracle renders, repeatably, at the levels docs/dev/superdough-mapping.md states.

Opt-in: ``NANODIRT_TEST_SUPERDOUGH=1`` after
``python scripts/superdough_oracle/superdough_oracle.py setup``, run with
``uv run --with playwright pytest``.
"""

from __future__ import annotations

import math
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "superdough_oracle"))
import superdough_oracle  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (os.environ.get("NANODIRT_TEST_SUPERDOUGH") and superdough_oracle.available()),
    reason="set NANODIRT_TEST_SUPERDOUGH=1 after superdough_oracle.py setup",
)
pytest.importorskip("playwright")

RATE = 48000


@pytest.fixture(scope="module")
def sd() -> Iterator[Any]:
    with superdough_oracle.Superdough() as s:
        yield s


def _rms(frames: list[tuple[float, float]], a: float, b: float) -> float:
    seg = [left for left, _ in frames[int(a * RATE) : int(b * RATE)]]
    return math.sqrt(sum(v * v for v in seg) / len(seg))


def test_sawtooth_level_and_repeatability(sd: Any) -> None:
    ev = [{"value": {"s": "sawtooth", "note": 57}, "t": 0.0, "duration": 0.5}]
    a = sd.render(ev, 1.0)
    assert a == sd.render(ev, 1.0)
    # 0.3 source * 0.6 sustain * 0.8 gain, saw RMS ~0.49 after peak normalization
    assert _rms(a, 0.15, 0.45) == pytest.approx(0.0703, rel=0.02)
    assert _rms(a, 0.6, 1.0) == 0.0  # released by end + release + 0.01


def test_random_sources_repeat(sd: Any) -> None:
    for s in ("supersaw", "white"):
        ev = [{"value": {"s": s, "note": 57}, "t": 0.0, "duration": 0.3}]
        assert sd.render(ev, 0.5) == sd.render(ev, 0.5), s


def test_reverb_has_a_tail(sd: Any) -> None:
    ev = [
        {"value": {"s": "sine", "note": 69, "room": 0.8, "roomsize": 1}, "t": 0.0, "duration": 0.1}
    ]
    assert _rms(sd.render(ev, 1.0), 0.5, 1.0) > 1e-5
