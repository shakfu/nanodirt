"""Render a SuperDirt SynthDef and nanodirt's translation under the same inputs.

``tests/fixtures/superdirt_defs/`` holds sclang's compiled SynthDefs, dumped
from SuperDirt `650a1d6` by nanosynth's ``scripts/superdirt_oracle.py``
(``ORACLE_DUMP_DEFS``).
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path
from typing import Any

from nanosynth.osc import OscMessage
from nanosynth.score import Score
from nanosynth.scsynth import Options
from nanosynth.synthdef import SynthDef, SynthDefBuilder
from nanosynth.ugens import In, LFSaw, Out, RandSeed, SinOsc

FIXTURES = Path(__file__).parent / "fixtures" / "superdirt_defs"
SOURCE_BUS = 16  # first private bus with 8 in + 8 out
SECONDS = 0.5


def _helper_defs(channels: int) -> list[SynthDef]:
    with SynthDefBuilder() as b:
        Out.ar(
            bus=SOURCE_BUS,
            source=[
                SinOsc.ar(frequency=220 * (i + 1), phase=0) * 0.4
                + LFSaw.ar(frequency=1300 + 170 * i, initial_phase=0) * 0.2
                for i in range(channels)
            ],
        )
    source = b.build(name="parity_source")
    with SynthDefBuilder() as b:
        Out.ar(bus=0, source=In.ar(bus=SOURCE_BUS, channel_count=channels))
    tap = b.build(name="parity_tap")
    with SynthDefBuilder() as b:
        RandSeed.ir(trigger=1, seed=42)
    seed = b.build(name="parity_seed")
    return [source, tap, seed]


def write_test_wav(path: Path, channels: int, seconds: float = 0.3, rate: int = 44100) -> None:
    """A chirp, distinct per channel, as 16-bit PCM."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = bytearray()
        for n in range(int(seconds * rate)):
            t = n / rate
            for c in range(channels):
                x = 0.5 * math.sin(2 * math.pi * (200 * (c + 1) + 2000 * t) * t)
                frames += struct.pack("<h", int(x * 32767))
        w.writeframes(bytes(frames))


def render(
    def_bytes: bytes,
    name: str,
    controls: dict[str, Any],
    path: Path,
    channels: int = 2,
    source: bool = True,
    tap: bool = True,
    buffer: Path | None = None,
) -> list[float]:
    """Render *def_bytes* as synth *name*, between the source and the tap."""
    score = Score()
    for d in _helper_defs(channels):
        score.add_synthdef(0.0, d)
    score.add(0.0, OscMessage("/d_recv", def_bytes))
    if buffer is not None:
        score.add(0.0, OscMessage("/b_allocRead", 0, str(buffer), 0, -1))
    score.add(0.0, OscMessage("/s_new", "parity_seed", 999, 0, 0))
    if source:
        score.add(0.0, OscMessage("/s_new", "parity_source", 1000, 1, 0))
    args = [x for kv in controls.items() for x in kv]
    score.add(0.0, OscMessage("/s_new", name, 1001, 1, 0, *args))
    if tap:
        score.add(0.0, OscMessage("/s_new", "parity_tap", 1002, 1, 0))
    score.add(SECONDS, OscMessage("/c_set", 0, 0))
    score.render(
        path,
        sample_format="float",
        output_channels=channels,
        options=Options(verbosity=-1, memory_size=8192 * 32),  # SuperDirt's; GVerb needs it
    )
    raw = path.read_bytes()
    i = raw.find(b"data")
    n = struct.unpack("<I", raw[i + 4 : i + 8])[0]
    return list(struct.unpack(f"<{n // 4}f", raw[i + 8 : i + 8 + n - n % 4]))


def compare(ref: list[float], got: list[float]) -> tuple[float, float]:
    """(max abs difference, reference peak), ignoring NaN samples."""
    assert len(ref) == len(got)
    diff = max((abs(a - b) for a, b in zip(ref, got) if a == a and b == b), default=0.0)
    peak = max((abs(a) for a in ref if a == a), default=0.0)
    return diff, peak
