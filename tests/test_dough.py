"""superdough's voice (nanodirt.dough) against the spec and the superdough oracle.

The oracle tests are opt-in, as tests/test_superdough_oracle.py.
"""

from __future__ import annotations

import math
import os
import shutil
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
from parity import write_test_wav

from nanodirt import dough
from nanodirt.library import Sample, read_header

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "superdough_oracle"))
import superdough_oracle  # noqa: E402

RATE = 44100
NUM_CHANNELS = 2
DEFS = {d.effective_name: d for d in dough.synthdefs(NUM_CHANNELS)}
SYNTH_BUS = 64  # private in NRT
Frames = list[tuple[float, float]]


@pytest.fixture(scope="module")
def banks(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[Path]]:
    """A mono and a stereo bank, written at the render rate so neither side resamples."""
    root = tmp_path_factory.mktemp("banks")
    out: dict[str, list[Path]] = {}
    for name, channels in (("mono", 1), ("stereo", 2)):
        out[name] = []
        for i in range(2):
            path = root / f"{name}{i}.wav"
            write_test_wav(path, channels, seconds=0.25 + 0.1 * i, rate=RATE)
            out[name].append(path)
    return out


def nrt(
    event: dict[str, Any], seconds: float, banks: dict[str, list[Path]] | None = None
) -> Frames:
    """nanodirt's voice for *event*, rendered offline: source and gate in one group."""
    samples: dict[Path, int] = {}

    def find(name: str, index: int, midi: float) -> tuple[Sample, float | None] | None:
        bank = (banks or {}).get(name)
        if not bank:
            return None
        path = bank[index % len(bank)]
        buf = samples.setdefault(path, len(samples))
        return Sample(path, read_header(path), buf), None

    v = dough.voice(event, NUM_CHANNELS, find)
    assert v is not None and v is not dough.SILENT
    score = Score()
    for d in DEFS.values():
        score.add_synthdef(0.0, d)
    for path, buf in samples.items():
        score.add(0.0, OscMessage("/b_allocRead", buf, str(path), 0, -1))
    source = {"out": SYNTH_BUS, **v.controls}
    if v.sample is not None:
        source["buf"] = v.sample.buffer
    gate = {"in": SYNTH_BUS, "out": 0, **v.gate}
    flat = lambda d: [x for kv in d.items() for x in kv]  # noqa: E731
    score.add(0.0, OscMessage("/g_new", 900, 0, 0))
    score.add(0.0, OscMessage("/s_new", v.source, 1000, 0, 900, *flat(source)))
    for k, (kind, c) in enumerate(v.filters):
        score.add(
            0.0,
            OscMessage(
                "/s_new",
                f"dough_{kind}{v.channels}",
                1002 + k,
                1,
                900,
                *flat({"out": SYNTH_BUS, **c}),
            ),
        )
    score.add(
        0.0,
        OscMessage("/s_new", f"dough_gate{v.channels}_{NUM_CHANNELS}", 1001, 1, 900, *flat(gate)),
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
    x = struct.unpack(f"<{n // 4}f", raw[i + 8 : i + 8 + n - n % 4])
    return list(zip(x[0::2], x[1::2]))


def rms(x: Frames, a: float, b: float, channel: int = 0) -> float:
    seg = [f[channel] for f in x[int(a * RATE) : int(b * RATE)]]
    return math.sqrt(sum(v * v for v in seg) / len(seg))


def max_diff(a: Frames, b: Frames) -> float:
    return max(max(abs(p[0] - q[0]), abs(p[1] - q[1])) for p, q in zip(a, b))


def matches(
    ref: Frames, got: Frames, tolerance: float = 0.003, skip: tuple[int, int] = (0, 0)
) -> bool:
    """Equal within *tolerance* of the peak, ignoring frames in *skip*.

    Web Audio places envelope breakpoints in continuous time; EnvGen moves them to
    whole samples, which costs up to 0.23% next to a breakpoint.
    """
    peak = max(max(abs(p[0]), abs(p[1])) for p in ref)
    pairs = [(p, q) for k, (p, q) in enumerate(zip(ref, got)) if not skip[0] <= k < skip[1]]
    return max_diff([p for p, _ in pairs], [q for _, q in pairs]) <= tolerance * peak


class TestRules:
    def test_adsr_defaults_and_fill(self) -> None:
        d = dough.SOURCE_ENVELOPE
        assert dough.adsr_values((None, None, None, None), d) == d
        assert dough.adsr_values((0.1, None, None, None), d) == (0.1, 0.001, 1.0, 0.01)
        assert dough.adsr_values((None, 0.2, None, None), d) == (0.001, 0.2, 0.001, 0.01)
        assert dough.adsr_values((None, None, 2.0, 0.0), d) == (0.001, 0.001, 1.0, 0.01)

    def test_envelope_points(self) -> None:
        assert dough.envelope_points((0.1, 0.2, 0.5, 0.3), 1.0) == (
            [1.0, 0.5, 0.5, 0.0],
            [0.1, 0.2, pytest.approx(0.7), 0.3],
        )
        # ends during decay: stops where the full envelope would be at the end
        levels, times = dough.envelope_points((0.1, 0.2, 0.5, 0.3), 0.2)
        assert levels == [1.0, pytest.approx(0.75), 0.0, 0.0]
        assert times == [0.1, pytest.approx(0.1), 0.3, 0.0]  # padding after the release
        # ends during attack
        assert dough.envelope_points((0.4, 0.2, 0.5, 0.3), 0.1) == (
            [0.25, 0.0, 0.0, 0.0],
            [0.1, 0.3, 0.0, 0.0],
        )

    def test_frequency_and_midi(self) -> None:
        assert dough.frequency({}) == pytest.approx(65.406, rel=1e-4)  # note 36
        assert dough.frequency({"note": 69}) == 440.0
        assert dough.frequency({"note": "a3"}) == pytest.approx(220.0)  # c3 is 48
        assert dough.frequency({"note": 69, "octave": -1}) == 220.0
        assert dough.frequency({"freq": 100, "note": 69}) == 100.0
        assert dough.midi({}) == 36 and dough.midi({"freq": 220}) == pytest.approx(57)

    def test_sources(self) -> None:
        assert dough.voice({"s": "saw"}, 2).source == "dough_sawtooth"  # type: ignore[union-attr]
        assert dough.voice({"s": "Sine"}, 2).source == "dough_sine"  # type: ignore[union-attr]
        assert dough.voice({"s": "bd"}, 2) is None  # no sample finder
        assert dough.voice({"s": "sine", "bank": "x"}, 2) is None  # superdough plays x_sine
        assert dough.voice({"s": "crackle"}, 2).controls["density"] == 0.02  # type: ignore[union-attr]

    def test_gate(self) -> None:
        v = dough.voice({"s": "sine", "gain": 0.5, "velocity": 0.5, "postgain": 2}, 2)
        assert v is not None and v.gate["level"] == pytest.approx(0.5 * 0.5 * 2)
        assert v.gate["panned"] == 0.0
        assert dough.voice({"s": "sine", "pan": 0.2}, 2).gate["panned"] == 1.0  # type: ignore[union-attr]

    def test_sample_rules(self, banks: dict[str, list[Path]]) -> None:
        def find(name: str, index: int, midi: float) -> tuple[Sample, float | None] | None:
            path = banks[name][index % 2]
            return Sample(path, read_header(path), 0), None

        v = dough.voice({"s": "mono", "n": 2.6, "delta": 0.1, "speed": -2}, 2, find)
        assert (
            v is not None and v.sample is not None and v.sample.path.name == "mono1.wav"
        )  # round(2.6) % 2
        assert v.controls["rate"] == pytest.approx(-2.0)
        slice_ = read_header(banks["mono"][1]).duration / 2  # the whole slice, not delta
        assert v.controls["t3"] == pytest.approx(slice_ - 0.002)
        assert dough.voice({"s": "mono", "speed": 0}, 2, find) is dough.SILENT


def test_sine_level_offline() -> None:
    # 0.3 source * 0.8 gain * 0.6 sustain = 0.144 amplitude, on both channels unpanned
    x = nrt({"s": "sine", "note": 69, "delta": 0.5}, 0.7)
    assert rms(x, 0.1, 0.45) == pytest.approx(0.144 / math.sqrt(2), rel=1e-3)
    assert rms(x, 0.1, 0.45, 1) == rms(x, 0.1, 0.45)
    assert rms(x, 0.53, 0.7) == 0.0


# -- against superdough ----------------------------------------------------------------

oracle = pytest.mark.skipif(
    not (os.environ.get("NANODIRT_TEST_SUPERDOUGH") and superdough_oracle.available()),
    reason="set NANODIRT_TEST_SUPERDOUGH=1 after superdough_oracle.py setup",
)


@pytest.fixture(scope="module")
def sd(banks: dict[str, list[Path]]) -> Iterator[Any]:
    pytest.importorskip("playwright")
    served = superdough_oracle.WWW / "nanodirt-test"
    served.mkdir(exist_ok=True)
    for paths in banks.values():
        for p in paths:
            shutil.copy(p, served / p.name)
    with superdough_oracle.Superdough() as s:
        s.add_samples({name: [f"nanodirt-test/{p.name}" for p in ps] for name, ps in banks.items()})
        yield s


def reference(sd: Any, value: dict[str, Any], duration: float, seconds: float) -> Frames:
    return sd.render([{"value": value, "t": 0.0, "duration": duration}], seconds, RATE)  # type: ignore[no-any-return]


ENVELOPES: list[dict[str, Any]] = [
    {},
    {"attack": 0.05, "release": 0.1},
    {"decay": 0.1},
    {"attack": 0.2},
    {"attack": 0.1, "decay": 0.2, "sustain": 0.3, "release": 0.2},
    {"attack": 0.4},  # longer than the note
    {"gain": 0.5, "velocity": 0.7, "postgain": 1.5},
    {"note": "e4", "octave": 1},
    {"pan": 0.2},
    {"pan": 1},
]


@oracle
@pytest.mark.parametrize(
    "extra", ENVELOPES, ids=lambda e: ",".join(f"{k}={v}" for k, v in e.items()) or "default"
)
def test_sine_matches_sample_by_sample(sd: Any, extra: dict[str, Any]) -> None:
    value = {"s": "sine", "note": 69, **extra}
    assert matches(reference(sd, value, 0.3, 0.8), nrt({**value, "delta": 0.3}, 0.8))


SAMPLES: list[dict[str, Any]] = [
    {"s": "mono"},
    {"s": "mono", "n": 1, "gain": 1.2, "pan": 0.3},
    {"s": "mono", "note": 48},  # transposed: rate 2, linear interpolation
    {"s": "mono", "speed": 0.75, "begin": 0.2, "end": 0.6},
    {"s": "mono", "speed": -1},
    {"s": "mono", "clip": 1, "attack": 0.02, "release": 0.05},  # cut at the event's end
    {"s": "stereo"},
    {"s": "stereo", "pan": 0.2},
    {"s": "stereo", "n": 1, "pan": 0.85, "postgain": 0.5},
]


@oracle
@pytest.mark.parametrize("value", SAMPLES, ids=lambda e: ",".join(f"{k}={v}" for k, v in e.items()))
def test_sample_matches_sample_by_sample(
    sd: Any, banks: dict[str, list[Path]], value: dict[str, Any]
) -> None:
    ref = reference(sd, value, 0.15, 0.6)
    got = nrt({**value, "delta": 0.15}, 0.6, banks)
    assert max(abs(left) for left, _ in ref) > 0.05, "silent reference"
    assert matches(ref, got)


@oracle
@pytest.mark.parametrize("s", ["triangle", "square", "sawtooth"])
@pytest.mark.parametrize("note", [33, 57, 81])
def test_waveform_level(sd: Any, s: str, note: int) -> None:
    ref = reference(sd, {"s": s, "note": note}, 0.6, 0.8)
    got = nrt({"s": s, "note": note, "delta": 0.6}, 0.8)
    assert rms(got, 0.1, 0.55) == pytest.approx(rms(ref, 0.1, 0.55), rel=0.03)
    assert rms(got, 0.7, 0.8) == rms(ref, 0.7, 0.8) == 0.0


def test_crackle_density_offline() -> None:
    # superdough sets a sample to uniform(-1, 1) with probability density * 0.01
    # (noise.mjs): mean square p/3. ~170 impulses in 20 s: relative sd ~5%.
    p = dough.CRACKLE_DENSITY * 0.01
    x = nrt({"s": "crackle", "delta": 20.0}, 20.1)
    expected = 0.3 * 0.8 * 0.6 * math.sqrt(p / 3)
    assert rms(x, 0.1, 19.9) == pytest.approx(expected, rel=0.2)


@oracle
@pytest.mark.parametrize("s", ["white", "pink", "brown"])  # crackle: too sparse to compare
def test_noise_level(sd: Any, s: str) -> None:
    ref = reference(sd, {"s": s}, 1.8, 2.0)
    got = nrt({"s": s, "delta": 1.8}, 2.0)
    tolerance = {"white": 0.02, "pink": 0.15, "brown": 0.3}[s]
    assert rms(got, 0.1, 1.75) == pytest.approx(rms(ref, 0.1, 1.75), rel=tolerance)


# On samples: they play identically in both engines, so the filter is compared alone.
FILTERED: list[dict[str, Any]] = [
    {"cutoff": 800},
    {"cutoff": 800, "resonance": 12},  # Q in dB
    {"hcutoff": 1500, "hresonance": 6},
    {"bandf": 1000, "bandq": 4},  # linear Q
    {"cutoff": 600, "ftype": 2},  # 24 dB: two in series
    {"cutoff": 300, "lpenv": 4},
    {"cutoff": 300, "lpenv": 3, "lpattack": 0.05, "lpdecay": 0.1, "lpsustain": 0.4},
    {"cutoff": 2000, "lpenv": -3, "lpdecay": 0.2},
    {"cutoff": 800, "lpenv": 2, "fanchor": 0.5, "lprelease": 0.2},
    {"cutoff": 300, "lpenv": 4, "lpattack": 0.5},  # longer than the note
    {"hcutoff": 200, "hpenv": 3, "hpdecay": 0.1},
    {"s": "stereo", "cutoff": 1200, "hcutoff": 300, "resonance": 5},
]


@oracle
@pytest.mark.parametrize(
    "extra", FILTERED, ids=lambda e: ",".join(f"{k}={v}" for k, v in e.items())
)
def test_filter_matches_sample_by_sample(
    sd: Any, banks: dict[str, list[Path]], extra: dict[str, Any]
) -> None:
    value = {"s": "mono", "n": 1, **extra}
    ref = reference(sd, value, 0.2, 0.5)
    got = nrt({**value, "delta": 0.2}, 0.5, banks)
    assert max(abs(left) for left, _ in ref) > 0.02, "silent reference"
    # After the buffer runs out, the tail depends on engine timing: superdough releases the
    # chain when onended runs (a few ms in realtime, after the offline render ends), and Chrome
    # stops a silent biquad after a tail time of its own. Compare up to the buffer's end.
    end = read_header(banks[value["s"]][1]).frames
    swept = any(k.endswith(("env", "attack", "decay", "sustain", "release")) for k in extra)
    # A moving cutoff: 32-bit coefficients near the unit circle (Chrome's are 64-bit).
    assert matches(ref, got, 0.005 if swept else 0.003, skip=(end, len(ref)))
