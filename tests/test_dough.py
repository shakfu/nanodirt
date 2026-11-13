"""superdough's voice (nanodirt.dough) against the spec and the superdough oracle.

The oracle tests are opt-in, as tests/test_superdough_oracle.py.
"""

from __future__ import annotations

import math
import os
import random
import shutil
import struct
import sys
import tempfile
import wave
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from nanosynth.osc import OscMessage
from nanosynth.score import Score
from nanosynth.scsynth import Options
from parity import _helper_defs, write_test_wav
from soundfont_presets import write_presets

from nanodirt import dough, dough_orbit
from nanodirt.library import Sample, read_header
from nanodirt.soundfonts import Soundfonts, Zone

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "superdough_oracle"))
import superdough_oracle  # noqa: E402

RATE = 44100
NUM_CHANNELS = 2
DEFS = {d.effective_name: d for d in [*dough.synthdefs(NUM_CHANNELS), *dough_orbit.synthdefs()]}
DEFS["parity_seed"] = _helper_defs(1)[2]  # RandSeed: the same random draws on every run
DELAY_BUS, ROOM_BUS = 66, 68
FM_BUS = 70
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
    # one full-scale sample, a broadband source, after the 2 ms attack and decay
    click = root / "click0.wav"
    with wave.open(str(click), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(RATE)
        w.writeframes(bytes(2 * 200) + struct.pack("<h", 32767) + bytes(2 * 199))
    out["click"] = [click, click]
    return out


def nrt(
    event: dict[str, Any],
    seconds: float,
    banks: dict[str, list[Path]] | None = None,
    fonts: Soundfonts | None = None,
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

    def find_font(name: str, index: int, midi: float) -> tuple[Sample, Zone] | None:
        found = fonts.get(name, index, midi) if fonts is not None else None
        if found is None:
            return None
        sample, zone = found
        return Sample(
            sample.path, sample.header, samples.setdefault(sample.path, len(samples))
        ), zone

    v = dough.voice(event, NUM_CHANNELS, find, find_font, RATE)
    assert v is not None and v is not dough.SILENT
    score = Score()
    for d in DEFS.values():
        score.add_synthdef(0.0, d)
    for path, buf in samples.items():
        score.add(0.0, OscMessage("/b_allocRead", buf, str(path), 0, -1))
    source = {"out": SYNTH_BUS, **v.controls}
    if v.sample is not None:
        source["buf"] = v.sample.buffer
    gate = {"in": SYNTH_BUS, "out": 0, **v.gate, "delayBus": DELAY_BUS, "roomBus": ROOM_BUS}
    after: list[OscMessage] = []  # the orbit's effects, added once the voice's group exists
    delay = dough_orbit.delay_settings(event, RATE)
    if delay is not None:  # the orbit's effects, after the voice's group
        send, time_, feedback = delay
        gate["delaySend"] = send
        after.append(OscMessage(
            "/s_new", "dough_delay", 2000, 3, 900, "in", DELAY_BUS, "out", 0,
            *dough_orbit.delay_controls(time_, feedback, RATE),
        ))  # fmt: skip
    reverb = dough_orbit.reverb_settings(event)
    if reverb is not None:
        gate["roomSend"] = reverb[0]
        ir = Path(tempfile.mkdtemp()) / "ir.wav"
        frames = dough_orbit.prepare_ir(reverb[1], RATE, 64, random.Random(1), ir)
        load, prepare = dough_orbit.ir_messages(ir, frames, (10, 11), (12, 13))
        after += load + prepare
        after.append(OscMessage(
            "/s_new", "dough_reverb", 2001, 3, 900, "in", ROOM_BUS, "out", 0, "left", 12, "right", 13,
        ))  # fmt: skip
    flat = lambda d: [x for kv in d.items() for x in kv]  # noqa: E731
    score.add(0.0, OscMessage("/s_new", "parity_seed", 999, 0, 0))  # repeatable noise
    score.add(0.0, OscMessage("/g_new", 900, 1, 0))
    if v.modulators:
        source |= {"fm": 1.0, "fmbus": FM_BUS}
    score.add(0.0, OscMessage("/s_new", v.source, 1000, 0, 900, *flat(source)))
    for k, (name, c) in enumerate(v.modulators):  # each to the head: the last runs first
        score.add(0.0, OscMessage("/s_new", name, 1100 + k, 0, 900, *flat({"fmbus": FM_BUS, **c})))
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
    for m in after:
        score.add(0.0, m)
    score.add(seconds, OscMessage("/c_set", 0, 0))
    path = Path(tempfile.mkdtemp()) / "voice.wav"
    score.render(
        path,
        sample_format="float",
        output_channels=2,
        sample_rate=RATE,
        options=Options(verbosity=-1, wire_buffer_count=2048),  # as cli.py and offline.py
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
        assert dough.voice({"note": 60}, 2).source == "dough_triangle"  # type: ignore[union-attr]

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
    write_presets(superdough_oracle.WWW / "nanodirt-sf")
    with superdough_oracle.Superdough() as s:
        s.add_samples({name: [f"nanodirt-test/{p.name}" for p in ps] for name, ps in banks.items()})
        s.use_soundfonts("nanodirt-sf")
        yield s


def reference(sd: Any, value: dict[str, Any], duration: float, seconds: float) -> Frames:
    # superdough takes cps as an argument; Strudel's OSC output puts it in the event.
    event = {"value": value, "t": 0.0, "duration": duration, "cps": value.get("cps", 0.5)}
    return sd.render([event], seconds, RATE)  # type: ignore[no-any-return]


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
    # loops: play from begin to the loop end, then wrap, as Web Audio does
    {"s": "mono", "loop": 1, "loopBegin": 0.25, "loopEnd": 0.75},
    {"s": "mono", "loop": 1, "begin": 0.1, "loopBegin": 0.4, "loopEnd": 0.6, "speed": 1.3},
    {"s": "mono", "loop": 1, "loopBegin": 0.2, "loopEnd": 0.7, "speed": -1},
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


# -- distort: helpers.mjs's curves, ported line by line ---------------------------------


def _squash(x: float) -> float:
    return x / (1 + x)


def _soft(x: float, k: float) -> float:
    return math.tanh(x * (1 + k))


def _fold(x: float, k: float) -> float:
    y = (1 + 0.5 * k) * x
    window = ((y + 1) % 4 + 4) % 4
    return 1 - abs(window - 2)


def _diode(x: float, k: float, asym: bool = False) -> float:
    g = 1 + 2 * k
    t = _squash(math.log1p(k))
    bias = 0.07 * t
    pos = _soft(x + bias, 2 * k)
    neg = _soft(bias if asym else -x + bias, 2 * k)
    y = pos - neg
    sech = 1 / math.cosh(g * bias)
    denom = max(1e-8, (1 if asym else 2) * g * sech * sech)
    return _soft(y / denom, k)


def _chebyshev(x: float, k: float) -> float:
    kl = 10 * math.log1p(k)
    tnm1, tnm2, y = 1.0, x, 0.0
    for i in range(1, 64):
        if i < 2:
            y += tnm1 if i == 0 else tnm2
            continue
        tn = 2 * x * tnm1 - tnm2
        tnm2, tnm1 = tnm1, tn
        if i % 2 == 0:
            y += min((1.3 * kl) / i, 2) * tn
    return _soft(y, kl / 20)


CURVES = {
    "scurve": lambda x, k: ((1 + k) * x) / (1 + k * abs(x)),
    "soft": _soft,
    "hard": lambda x, k: min(max((1 + k) * x, -1), 1),
    "cubic": lambda x, k: _soft(
        (x - (_squash(math.log1p(k)) / 3) * x**3) / (1 - _squash(math.log1p(k)) / 3), k
    ),
    "diode": _diode,
    "asym": lambda x, k: _diode(x, k, True),
    "fold": _fold,
    "sinefold": lambda x, k: math.sin(math.pi / 2 * _fold(x, k)),
    "chebyshev": _chebyshev,
}


class TestDistortRules:
    def test_types(self) -> None:
        assert list(CURVES) == dough.DISTORTIONS
        assert dough.distortion({}) is None
        assert dough.distortion({"distort": 0})[0] == "distort_scurve"  # type: ignore[index]
        for algo, name in [(2, "hard"), (9, "scurve"), (17, "chebyshev"), ("fold", "fold")]:
            got = dough.distortion({"distort": 1, "distorttype": algo})
            assert got is not None and got[0] == f"distort_{name}"
        assert dough.distortion({"distort": 1, "distorttype": "nonesuch"})[0] == "distort_scurve"  # type: ignore[index]
        for bad in (-1, 1.5):  # the worklet throws: superdough plays nothing
            assert dough.voice({"s": "sine", "distort": 1, "distorttype": bad}, 2) is dough.SILENT

    def test_volume_and_gain(self) -> None:
        def post(vol: float) -> float:
            return dough.distortion({"distort": 1, "distortvol": vol})[1]["post"]  # type: ignore[index]

        assert (post(0.5), post(3), post(0)) == (0.5, 1.0, 0.001)
        v = dough.voice({"s": "sine", "gain": 0.5, "postgain": 2, "distort": 1}, 2)
        assert v is not None and v.filters[-1][1]["gain"] == 0.5  # gain before the curve
        assert v.gate["level"] == 2  # postgain after it
        assert dough.distortion({"distort": 1000})[1]["k"] == math.expm1(dough.MAX_DISTORT)  # type: ignore[index]


@pytest.mark.parametrize("name", list(CURVES))
@pytest.mark.parametrize("amount", [0.5, 3.0])
def test_distort_curve_offline(name: str, amount: float) -> None:
    """Each curve on nanodirt's own undistorted sine, against the JavaScript curve."""
    sine = {"s": "sine", "note": 57, "delta": 0.3, "gain": 1}
    clean = nrt(sine, 0.4)
    gain, vol = 4.0, 0.7  # 4 * 0.3 * 0.6: the sine peaks at 0.72 then 1.2, past the knees
    got = nrt(
        {**sine, "gain": gain, "distort": amount, "distorttype": name, "distortvol": vol}, 0.4
    )
    k = math.expm1(amount)
    want = [vol * CURVES[name](gain * left, k) for left, _ in clean]
    peak = max(abs(w) for w in want)
    assert peak > 0.1
    assert max(abs(w - left) for w, (left, _) in zip(want, got)) <= 2e-4 * max(peak, 1.0)


@oracle
@pytest.mark.parametrize("name", list(CURVES))
def test_distort_curve_in_chrome(sd: Any, name: str) -> None:
    """superdough's curves are the port's: test_distort_curve_offline then covers nanodirt."""
    sine = {"s": "sine", "note": 57, "gain": 1}
    clean = reference(sd, sine, 0.2, 0.3)
    got = reference(
        sd, {**sine, "gain": 4, "distort": 2, "distorttype": name, "distortvol": 0.7}, 0.2, 0.3
    )
    want = [0.7 * CURVES[name](4 * left, math.expm1(2)) for left, _ in clean]
    assert max(abs(w - left) for w, (left, _) in zip(want, got)) < 1e-5


# The chain around the curve: gain, filters, distortvol, pan, postgain. A curve multiplies
# the input's error (the envelope's 0.23% at a breakpoint) by its slope, so these use gentle
# ones; chebyshev's slope reaches 25, asym's at 0 is about 400.
DISTORTED: list[dict[str, Any]] = [
    {"s": "sine", "note": 57, "distort": 2},
    {"s": "sine", "note": 45, "distort": 1.5, "distorttype": "diode", "gain": 1.5},
    {"s": "mono", "n": 1, "distort": 1, "distorttype": "soft", "cutoff": 800, "pan": 0.3},
    {"s": "stereo", "distort": 1, "distorttype": 5 + 9, "postgain": 0.5},  # wraps to asym
    {"s": "mono", "distort": 2, "distorttype": "sinefold", "velocity": 0.5, "distortvol": 0.6},
]


@oracle
@pytest.mark.parametrize(
    "value", DISTORTED, ids=lambda e: ",".join(f"{k}={v}" for k, v in e.items())
)
def test_distort_matches_sample_by_sample(
    sd: Any, banks: dict[str, list[Path]], value: dict[str, Any]
) -> None:
    ref = reference(sd, value, 0.2, 0.4)
    got = nrt({**value, "delta": 0.2}, 0.4, banks)
    assert max(abs(left) for left, _ in ref) > 0.05, "silent reference"
    # After a sample runs out, Chrome's filter tail goes on (test_filter_matches_sample_by_sample).
    bank = banks.get(value["s"])
    end = read_header(bank[int(value.get("n", 0))]).frames if bank else len(ref)
    assert matches(ref, got, skip=(end, len(ref)))


# -- vib and penv: the source's detune ---------------------------------------------------

MS = 1000.0  # a sample rate that puts the default breakpoints on whole samples


def detune(p: dict[str, float], n: int, rate: float = MS, prefix: str = "p") -> float:
    """EnvGen's output at sample *n* for exact_envelope()'s controls (EnvGen_initSegment)."""
    level, start, residual = p[f"{prefix}0"], 0, 0.0
    for i in range(1, dough.SEGMENTS + 1):
        span = p[f"{prefix}t{i}"] * rate + residual
        whole, spread = max(1, int(span)), max(1.0, span)
        residual = span - whole
        target, exp = p[f"{prefix}{i}"], p[f"{prefix}s{i}"] == 2.0

        def at(k: int) -> float:
            if exp:
                return level * (target / level) ** (k / spread)  # type: ignore[no-any-return]
            return level + (target - level) * k / spread

        if n < start + whole:
            return at(n - start)
        level, start = at(whole), start + whole
    return p[f"{prefix}{dough.SEGMENTS}"]  # where EnvGen stays when it ends


class TestPitchRules:
    def test_vib(self) -> None:
        assert dough.pitch({}, 0.5, MS) == {}
        assert dough.pitch({"vib": 0, "vibmod": 2}, 0.5, MS) == {}  # superdough needs vib > 0
        assert dough.pitch({"vib": 4}, 0.5, MS) == {"vib": 4.0, "vibmod": 50.0}  # cents
        assert dough.pitch({"vib": 4, "vibmod": 12}, 0.5, MS)["vibmod"] == 1200.0

    def test_penv_defaults(self) -> None:
        # attack 0.2, decay and release 0.001, sustain 1; panchor is the sustain
        p = dough.pitch({"penv": 12}, 0.5, MS)
        assert [detune(p, n) for n in (0, 100, 200, 400, 500)] == pytest.approx(
            [-1200, -600, 0, 0, 0]
        )
        assert detune(p, 501) == detune(p, 900) == pytest.approx(-1200)  # released to the start
        # any pitch control turns the envelope on, penv 1 semitone by default
        assert dough.pitch({"pattack": 0.1}, 0.5, MS)["p0"] == -100.0
        p = dough.pitch({"penv": 12, "panchor": 0, "pdecay": 0.1, "psustain": 0.5}, 0.5, MS)
        assert [detune(p, n) for n in (0, 1, 51, 101, 450)] == pytest.approx(
            [0, 1200, 900, 600, 600]  # attack 0.001
        )

    def test_breakpoint_between_samples(self) -> None:
        # attack ends at sample 2.5: samples 2 and 3 take the attack's and the decay's values
        # (sustain 0.001, decay 10 samples)
        p = dough.pitch({"penv": 1, "panchor": 0, "pattack": 0.0025, "pdecay": 0.01}, 0.5, MS)
        assert [detune(p, n) for n in (1, 2, 3, 8)] == pytest.approx([40, 80, 95.005, 45.055])

    def test_exponential(self) -> None:
        # opposite signs hold the start level, as exponentialRampToValueAtTime does
        p = dough.pitch({"penv": 12, "pcurve": 1}, 0.5, MS)
        assert detune(p, 199) == -1200.0 and detune(p, 200) == pytest.approx(0.001)  # 0 is 0.001
        assert detune(p, 500) == pytest.approx(0.001) and detune(p, 501) == -1200.0
        p = dough.pitch({"penv": -12, "pcurve": 1}, 0.5, MS)
        assert detune(p, 100) == pytest.approx(1200 * (0.001 / 1200) ** 0.5)
        assert detune(dough.pitch({"penv": 1, "pcurve": "0"}, 0.5, MS), 100) == pytest.approx(-50)
        p = dough.pitch({"penv": 1, "pcurve": 2}, 0.5, MS)  # curves[2] is undefined: exponential
        assert detune(p, 100) == -100.0

    def test_voices(self, banks: dict[str, list[Path]]) -> None:
        def find(name: str, index: int, midi: float) -> tuple[Sample, float | None] | None:
            path = banks[name][index % 2]
            return Sample(path, read_header(path), 0), None

        assert "vib" in dough.voice({"s": "saw", "vib": 2}, 2).controls  # type: ignore[union-attr]
        assert "vib" not in dough.voice({"s": "pink", "vib": 2}, 2).controls  # type: ignore[union-attr]
        v = dough.voice({"s": "mono", "penv": 12, "delta": 0.1}, 2, find, sample_rate=MS)
        assert v is not None
        end = round(read_header(banks["mono"][0]).duration * MS)  # holdEnd is the sample's
        assert detune(v.controls, end) == pytest.approx(0) and detune(v.controls, end + 1) == -1200


def frequencies(x: Frames, a: float, b: float) -> float:
    """Mean frequency from zero crossings between *a* and *b* seconds."""
    seg = [f[0] for f in x[int(a * RATE) : int(b * RATE)]]
    ups = [i for i in range(1, len(seg)) if seg[i - 1] < 0 <= seg[i]]
    return (len(ups) - 1) * RATE / (ups[-1] - ups[0])


def test_pitch_offline() -> None:
    # up an octave from 220 Hz over 0.2 s, held, then back down in the release
    x = nrt({"s": "sine", "note": 57, "penv": 12, "panchor": 0, "delta": 0.5}, 0.55)
    assert frequencies(x, 0.25, 0.45) == pytest.approx(440.0, rel=1e-3)
    assert frequencies(x, 0.02, 0.06) < 260
    # vibrato: the mean pitch stays, a quarter period of 5 Hz is sharp
    x = nrt({"s": "sine", "note": 69, "vib": 5, "vibmod": 1, "delta": 1.0}, 1.0)
    assert frequencies(x, 0.2, 0.8) == pytest.approx(440.0, rel=2e-3)
    assert frequencies(x, 0.0, 0.1) > 440 * 2 ** (0.5 / 12)


def test_slow_sample_plays_to_its_end_offline(banks: dict[str, list[Path]]) -> None:
    # Half speed: mono0 lasts 0.5 s, not 0.25 s. The voice is freed when the buffer ends.
    value = {"s": "mono", "penv": -12, "panchor": 0, "pattack": 0.001, "release": 1}
    x = nrt({**value, "delta": 0.8}, 0.8, banks)
    assert rms(x, 0.3, 0.48) > 0.05
    assert rms(x, 0.52, 0.8) == 0.0


PITCHED: list[dict[str, Any]] = [
    {"s": "sine", "note": 69, "vib": 5},
    {"s": "sine", "note": 57, "vib": 7.5, "vibmod": 3},
    {"s": "sine", "note": 57, "penv": 12},
    {"s": "sine", "note": 69, "penv": -7, "pattack": 0.05, "pdecay": 0.1, "psustain": 0.3},
    {"s": "sine", "note": 57, "penv": 5, "panchor": 0.5, "prelease": 0.1, "pattack": 0.5},
    {"s": "sine", "note": 69, "penv": -12, "pcurve": 1},  # exponential, one sign
    {"s": "sine", "note": 57, "penv": 12, "pcurve": 1},  # exponential, signs differ: held
    {"s": "sine", "note": 45, "vib": 3, "penv": 2},
    # Cut before the buffer ends: past its last frame, Chrome's output is not known exactly
    # (docs/dev/superdough-mapping.md).
    {"s": "mono", "n": 1, "vib": 6, "vibmod": 2, "clip": 1},
    {"s": "mono", "n": 1, "penv": 12, "pattack": 0.1, "speed": -1},
    {"s": "stereo", "penv": -5, "pdecay": 0.05, "psustain": 0},
    {"s": "gm_piano", "note": 57, "vib": 4},
    {"s": "gm_piano", "note": 60, "penv": -3},
]


@oracle
@pytest.mark.parametrize("value", PITCHED, ids=lambda e: ",".join(f"{k}={v}" for k, v in e.items()))
def test_pitch_matches_sample_by_sample(
    sd: Any, banks: dict[str, list[Path]], fonts: Soundfonts, value: dict[str, Any]
) -> None:
    ref = reference(sd, value, 0.3, 0.5)
    got = nrt({**value, "delta": 0.3}, 0.5, banks, fonts)
    assert max(abs(left) for left, _ in ref) > 0.05, "silent reference"
    assert matches(ref, got)


# -- FM: applyFM's modulator chain ------------------------------------------------------


class TestFMRules:
    def test_chain(self) -> None:
        assert dough.modulators({}, 220.0, 0.5, MS) == []
        assert dough.modulators({"fmi": 0, "fmh": 2}, 220.0, 0.5, MS) == []  # 0 is off
        # amount is fmi times the modulator's frequency, carrier freq times fmh (default 1)
        assert dough.modulators({"fmi": 2}, 220.0, 0.5, MS) == [
            ("dough_fm_sine", {"freq": 220.0, "amount": 440.0})
        ]
        mods = dough.modulators({"fmi": 2, "fmi2": 3, "fmh2": 0.5, "fmi4": 1}, 220.0, 0.5, MS)
        assert [(m[1]["freq"], m[1]["amount"]) for m in mods] == [(220.0, 440.0), (110.0, 330.0)]
        assert mods[0][1]["fm"] == 1.0 and "fm" not in mods[1][1]  # fmi4 does not reach fmi2
        # fm2 alone modulates a modulator that does not reach the carrier
        assert dough.modulators({"fmi2": 3}, 220.0, 0.5, MS) == []

    def test_waves(self) -> None:
        def wave(w: str) -> str:
            return dough.modulators({"fmi": 1, "fmwave": w}, 220.0, 0.5, MS)[0][0]

        assert wave("square") == "dough_fm_square" and wave("pink") == "dough_fm_pink"
        assert wave("saw") == "dough_fm_sine"  # OscillatorNode.type ignores names it lacks
        crackle = dough.modulators({"fmi": 1, "fmwave": "crackle"}, 220.0, 0.5, MS)[0][1]
        assert crackle["density"] == 2.0

    def test_envelope(self) -> None:
        # no envelope unless one of its controls is set: the SynthDef's default is constant 1
        assert "e0" not in dough.modulators({"fmi": 1, "fmenv": "lin"}, 220.0, 0.5, MS)[0][1]
        exp = dough.modulators({"fmi": 1, "fmdecay": 0.1}, 220.0, 0.5, MS)[0][1]
        # attack 0.001, sustain 0.001 of the way from 0.001 to 1: exponential up and down
        env = [detune(exp, n, prefix="e") for n in (0, 1, 51)]
        assert env == pytest.approx([0.001, 1, 0.001999**0.5], rel=1e-3)
        lin = dough.modulators({"fmi": 1, "fmdecay": 0.1, "fmenv": "lin"}, 220.0, 0.5, MS)[0][1]
        env = [detune(lin, n, prefix="e") for n in (0, 51)]
        assert env == pytest.approx([0, 0.5], abs=1e-2)

    def test_voices(self, banks: dict[str, list[Path]]) -> None:
        def find(name: str, index: int, midi: float) -> tuple[Sample, float | None] | None:
            path = banks[name][index % 2]
            return Sample(path, read_header(path), 0), None

        v = dough.voice({"s": "sawtooth", "note": 69, "octave": 1, "fmi": 1}, 2)
        assert v is not None and v.modulators[0][1]["freq"] == 880.0  # the carrier's frequency
        assert dough.voice({"s": "pink", "fmi": 1}, 2).modulators == []  # type: ignore[union-attr]
        assert dough.voice({"s": "mono", "fmi": 1}, 2, find).modulators == []  # type: ignore[union-attr]


def test_fm_offline() -> None:
    # 440 Hz modulated at 4.4 Hz (fmh 0.01) with index 10: 44 Hz either way
    x = nrt({"s": "sine", "note": 69, "fmi": 10, "fmh": 0.01, "delta": 1.0}, 1.0)
    swing = [frequencies(x, 0.02 * k, 0.02 * k + 0.02) for k in range(1, 45)]
    assert min(swing) == pytest.approx(396, abs=3) and max(swing) == pytest.approx(484, abs=3)


def test_oscillator_fades_below_nyquist_offline() -> None:
    # Chrome's tables drop the fundamental over the top third of an octave below the Nyquist
    # frequency: at 44.1 kHz, superdough's sine measured 0.42 at 20 kHz, 0.0098 at 22 kHz
    full = rms(nrt({"s": "sine", "freq": 1000, "delta": 0.3}, 0.3), 0.1, 0.25)
    for freq, level in ((16000, 1.0), (20000, 0.4223), (-20000, 0.4223), (22000, 0.0098)):
        got = rms(nrt({"s": "sine", "freq": freq, "delta": 0.3}, 0.3), 0.1, 0.25)
        assert got == pytest.approx(full * level, abs=2e-3 * full), freq


FM: list[dict[str, Any]] = [
    {"s": "sine", "note": 57, "fmi": 2},
    {"s": "sine", "note": 45, "fmi": 4, "fmh": 1.5},
    {
        "s": "sine",
        "note": 57,
        "fmi": 3,
        "fmh": 2,
        "fmattack": 0.05,
        "fmdecay": 0.1,
        "fmsustain": 0.3,
    },
    {"s": "sine", "note": 57, "fmi": 3, "fmdecay": 0.2, "fmenv": "lin", "fmrelease": 0.1},
    # A chain amplifies each engine's float32 rounding: at fmi2 3 both leave a double-precision
    # model by 1-2 samples of phase in 0.3 s, differently. Gentler indices check the wiring.
    {"s": "sine", "note": 57, "fmi": 1, "fmi2": 1, "fmh2": 0.5},
    {"s": "sine", "note": 57, "fmi": 2, "fmwave": "triangle"},
    {"s": "sine", "note": 57, "fmi": 1, "vib": 4, "penv": 5},  # with detune
    {"s": "sine", "note": 81, "fmi": 32},  # past the Nyquist frequency and below 0 Hz
]


def test_triangle_below_zero_hz_offline() -> None:
    # Index 3 at 1:1 sends the carrier to -440 Hz; the triangle must stay within its level
    x = nrt({"note": 57, "fmi": 3, "delta": 0.5}, 0.5)  # no s: superdough's triangle
    assert 0.2 < max(abs(left) for left, _ in x) <= 0.3 * 0.8 + 1e-6


@oracle
@pytest.mark.parametrize("value", FM, ids=lambda e: ",".join(f"{k}={v}" for k, v in e.items()))
def test_fm_matches_sample_by_sample(sd: Any, value: dict[str, Any]) -> None:
    ref = reference(sd, value, 0.3, 0.5)
    got = nrt({**value, "delta": 0.3}, 0.5)
    assert max(abs(left) for left, _ in ref) > 0.05, "silent reference"
    assert matches(ref, got)


@oracle
@pytest.mark.parametrize("value", [
    {"s": "sawtooth", "note": 45, "fmi": 2},  # band-limited in Chrome: compared by level
    {"s": "sine", "note": 57, "fmi": 2, "fmwave": "square"},
    {"s": "sine", "note": 57, "fmi": 1, "fmh": 0.5, "fmwave": "sawtooth"},
], ids=lambda e: ",".join(f"{k}={v}" for k, v in e.items()))  # fmt: skip
def test_fm_level(sd: Any, value: dict[str, Any]) -> None:
    ref = reference(sd, value, 0.6, 0.8)
    got = nrt({**value, "delta": 0.6}, 0.8)
    assert rms(got, 0.1, 0.55) == pytest.approx(rms(ref, 0.1, 0.55), rel=0.03)


class TestOrbitRules:
    def test_delay_settings(self) -> None:
        assert dough_orbit.delay_settings({}, 48000) is None
        assert dough_orbit.delay_settings({"delay": 0.5, "cps": 0.5}, 48000) == (0.5, 0.375, 0.5)
        assert dough_orbit.delay_settings({"delay": 0.5, "delayfeedback": 0}, 48000) is None
        send, time_, feedback = dough_orbit.delay_settings(  # type: ignore[misc]
            {"delay": 1, "delaytime": 3, "delayfeedback": 2}, 48000
        )
        assert (time_, feedback) == (1.0, 0.98)  # DelayNode's maximum; clamped feedback
        assert (
            dough_orbit.delay_settings({"delay": 1, "delaytime": 0.0001}, 48000)[1] == 128 / 48000
        )  # type: ignore[index]

    def test_comb_coefficient(self) -> None:
        assert 0.001 ** (0.2 / dough_orbit.comb_decay(0.2, 0.6)) == pytest.approx(0.6)

    def test_reverb_ir(self) -> None:
        ir = dough_orbit.reverb_ir(0.5, 0.1, 0, 0, 8000, random.Random(0))  # no lowpass
        assert len(ir) == 2 and len(ir[0]) == 6000  # 1.5 * roomsize
        assert ir[0][0] == 0.0 and ir[0] != ir[1]  # faded in; independent channels
        late = max(abs(x) for x in ir[0][3990:4010])
        assert late < 0.0012  # -60 dB at roomsize
        scale = dough_orbit.normalization_scale(ir, 8000)
        power = math.sqrt(sum(x * x for ch in ir for x in ch) / 12000)
        assert scale == pytest.approx(0.00125 / power * 44100 / 8000)


@oracle
@pytest.mark.parametrize(
    "extra",
    [
        {"delay": 0.5, "delaytime": 0.1, "delayfeedback": 0.6},
        {"delay": 0.8, "cps": 2.0, "pan": 0.2},
    ],
    ids=["explicit", "delaysync"],
)
def test_delay_matches_sample_by_sample(sd: Any, extra: dict[str, Any]) -> None:
    value = {"s": "sine", "note": 69, **extra}
    ref = reference(sd, value, 0.05, 1.0)
    got = nrt({**value, "delta": 0.05}, 1.0)
    assert matches(ref, got)


def energy_decay(x: Frames, start: float) -> tuple[float, list[float]]:
    """Tail energy after *start*, and its Schroeder decay curve in dB every 50 ms."""
    e = [left * left + right * right for left, right in x[int(start * RATE) :]]
    total = remaining = sum(e)
    curve = []
    for k, v in enumerate(e):
        if k % (RATE // 20) == 0:
            curve.append(10 * math.log10(max(remaining, 1e-30) / total))
        remaining -= v
    return total, curve


@oracle
@pytest.mark.parametrize("size", [0.5, 1.0, 2.0])
def test_reverb_energy_and_decay(sd: Any, banks: dict[str, list[Path]], size: float) -> None:
    # Both IRs are random, so samples cannot match: compare the tail's energy and its
    # decay curve. A click is broadband, so the tail is the IR itself.
    value = {"s": "click", "room": 0.6, "roomsize": size}
    ref_total, ref_curve = energy_decay(reference(sd, value, 0.05, size * 1.6), 0.01)
    got_total, got_curve = energy_decay(nrt({**value, "delta": 0.05}, size * 1.6, banks), 0.01)
    assert got_total == pytest.approx(ref_total, rel=0.35)
    n = int(size * 20)  # down to about -60 dB
    assert max(abs(a - b) for a, b in zip(ref_curve[:n], got_curve[:n])) < 2.0, (
        ref_curve,
        got_curve,
    )


@pytest.fixture(scope="module")
def fonts(tmp_path_factory: pytest.TempPathFactory) -> Soundfonts:
    served = write_presets(tmp_path_factory.mktemp("presets"))
    return Soundfonts(tmp_path_factory.mktemp("fonts"), blocking=True, url=served.as_uri())


FONTS: list[dict[str, Any]] = [
    {"s": "gm_piano", "note": 57},  # rate 1, held past the buffer: loops
    {"s": "gm_piano"},  # c3 by default: transposed down
    {"s": "gm_piano", "note": "f#2", "attack": 0.05, "release": 0.1},
    {"s": "gm_piano", "note": 58.5, "gain": 0.6, "pan": 0.3},  # fractional key
    {"s": "gm_piano", "note": 64},  # stereo zone, tuned, no loop
    {"s": "gm_piano", "note": 84, "pan": 0.8},  # faster than the buffer lasts
    # Loop points at another sample rate. Not 330 Hz: rate 1.5 lands exactly on the loop end,
    # which Chrome's double-precision loop end, 3000 / 22050 * 44100, passes by 1e-12.
    {"s": "gm_piano", "n": 1, "freq": 325},
    {"s": "gm_piano", "note": 57, "cutoff": 800, "resonance": 4},
]


@oracle
@pytest.mark.parametrize("value", FONTS, ids=lambda e: ",".join(f"{k}={v}" for k, v in e.items()))
def test_soundfont_matches_sample_by_sample(
    sd: Any, fonts: Soundfonts, value: dict[str, Any]
) -> None:
    ref = reference(sd, value, 0.6, 0.9)
    got = nrt({**value, "delta": 0.6}, 0.9, fonts=fonts)
    assert max(abs(left) for left, _ in ref) > 0.02, "silent reference"
    v = dough.voice({**value, "delta": 0.6}, NUM_CHANNELS, None, fonts.get)
    assert v is not None and v.sample is not None and v.sample.header is not None
    # fontloader decodes the MP3 from a buffer sized by its base64 length, so Chrome keeps
    # LAME's end padding (641 frames here, -43 dB) after a zone that does not loop.
    end = len(ref) if v.controls["loop"] else int(v.sample.header.frames / v.controls["rate"])
    assert matches(ref, got, skip=(end, len(ref)))
