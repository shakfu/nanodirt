"""Headless Strudel: the QuickJS binding, the bundle against Node, and offline rendering.

``tests/fixtures/strudel_tunes.json`` holds Node's events for Strudel's tunes, recorded
by ``scripts/strudel_bundle/build.py`` from the same bundle.
"""

from __future__ import annotations

import json
import math
import struct
import subprocess
import sys
import textwrap
import threading
import time
import wave
from pathlib import Path
from typing import Any

import pytest

from nanodirt import _quickjs
from nanodirt.cli import main
from nanodirt.headless import Strudel, StrudelError, render, timeline
from nanodirt.strudel import StrudelSamples

FIXTURE = Path(__file__).parent / "fixtures" / "strudel_tunes.json"
CYCLES = 4  # as build.py records


def read_wav(path: Path) -> tuple[int, list[float]]:
    """(channels, interleaved float samples) of a float WAV."""
    raw = path.read_bytes()
    channels = struct.unpack("<H", raw[22:24])[0]
    i = raw.find(b"data")
    n = struct.unpack("<I", raw[i + 4 : i + 8])[0]
    return channels, list(struct.unpack(f"<{n // 4}f", raw[i + 8 : i + 8 + n - n % 4]))


def rms(x: list[float]) -> float:
    return math.sqrt(sum(v * v for v in x) / len(x)) if x else 0.0


# -- the binding ----------------------------------------------------------------


def test_eval_and_callbacks_with_a_time_limit() -> None:
    ctx = _quickjs.Context()
    ctx.set_time_limit(1.0)  # the vendored binding allows callbacks under a limit
    ctx.add_callable("twice", lambda x: x * 2)
    assert ctx.eval("twice(21) + 1") == 43


def test_time_limit_stops_a_runaway_loop() -> None:
    ctx = _quickjs.Context()
    ctx.set_time_limit(0.2)
    start = time.monotonic()
    with pytest.raises(_quickjs.JSException, match="interrupted"):
        ctx.eval("while (true) {}")
    assert time.monotonic() - start < 2.0


def test_time_limit_survives_a_nested_eval() -> None:
    """A callback's own eval must not clear the outer call's limit."""
    ctx = _quickjs.Context()
    ctx.set_time_limit(0.2)
    ctx.add_callable("inner", lambda: ctx.eval("1"))
    start = time.monotonic()
    with pytest.raises(_quickjs.JSException, match="interrupted"):
        ctx.eval("inner(); const t = Date.now(); while (Date.now() - t < 3000) {}")
    assert time.monotonic() - start < 2.0


def test_time_limit_counts_this_threads_cpu_only() -> None:
    """A callback that sleeps, or another busy thread, does not use up the limit."""
    stop = threading.Event()
    busy = threading.Thread(target=lambda: [None for _ in iter(stop.is_set, True)])
    busy.start()
    try:
        ctx = _quickjs.Context()
        ctx.set_time_limit(0.2)
        ctx.add_callable("nap", lambda: time.sleep(0.5))
        ctx.eval("nap(); 1")
    finally:
        stop.set()
        busy.join()


_SHARED_CONTEXT = textwrap.dedent("""
    import threading
    from nanodirt import _quickjs

    ctx = _quickjs.Context()
    ctx.eval("var n = 0; function work() { let s = 0; for (let i = 0; i < 20000; i++) s += i; n++; return s; }")
    work = ctx.get("work")

    def hammer(k):
        for i in range(200):
            assert ctx.eval("work()") == 199990000
            assert work() == 199990000
            ctx.set(f"g{k}", i)
            ctx.get("n")
            ctx.eval("({a: [1, 2, {b: 3}]})")  # dropped at once: Object dealloc
            ctx.parse_json('{"x": [1, 2]}')

    ts = [threading.Thread(target=hammer, args=(k,)) for k in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert ctx.get("n") == 1600, ctx.get("n")
""")


def test_one_context_shared_between_threads() -> None:
    """Without the context's lock this crashed, or raised a false StackOverflow."""
    out = subprocess.run(
        [sys.executable, "-c", _SHARED_CONTEXT], capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stderr == "", out.stderr[-2000:]  # a thread that raised prints here


def test_callback_reenters_the_context() -> None:
    ctx = _quickjs.Context()
    ctx.eval("var base = 40")
    ctx.add_callable("inner", lambda: ctx.eval("base + 1") + ctx.get("base"))
    assert ctx.eval("inner() + 1") == 82


def test_memory_limit() -> None:
    ctx = _quickjs.Context()
    ctx.set_memory_limit(4 * 1024 * 1024)
    with pytest.raises(_quickjs.JSException):
        ctx.eval("let a = []; while (true) a.push(new Array(1000).fill(1));")


# -- Strudel in QuickJS ---------------------------------------------------------


@pytest.fixture(scope="module")
def strudel() -> Strudel:
    return Strudel()


def test_evaluate_and_query(strudel: Strudel) -> None:
    strudel.evaluate('setcps(1)\n$: s("bd*2 sd").gain(0.8)\n$: note("c e g").s("sawtooth")')
    assert strudel.cps == 1.0
    events = strudel.query(0, 1)
    drums = [e for e in events if e.controls["s"] in ("bd", "sd")]
    assert [(e.begin, e.end, e.controls["s"]) for e in drums] == [
        (0, 0.25, "bd"),
        (0.25, 0.5, "bd"),
        (0.5, 1, "sd"),
    ]
    assert drums[0].controls == {"cps": 1, "cycle": 0, "delta": 0.25, "s": "bd", "gain": 0.8}
    notes = [e.controls for e in events if e.controls["s"] == "sawtooth"]
    assert [c["midinote"] for c in notes] == [48, 52, 55]  # osc.mjs: note names to midinote


def test_errors_raise(strudel: Strudel) -> None:
    with pytest.raises(StrudelError, match="Unexpected token"):
        strudel.evaluate("s(")
    with pytest.raises(StrudelError, match="nope is not defined"):
        strudel.evaluate('nope("bd")')


def test_hush(strudel: Strudel) -> None:
    strudel.evaluate('s("bd")')
    assert strudel.query(0, 1)
    strudel.hush()
    assert strudel.query(0, 1) == []


def test_samples_calls_the_host() -> None:
    calls: list[tuple[Any, str]] = []
    s = Strudel(on_samples=lambda spec, base: calls.append((spec, base)))
    s.evaluate("samples('github:tidalcycles/dirt-samples')\nsamples({kick: ['k.wav']}, 'http://x/')\n"
               's("kick")')  # fmt: skip
    assert calls == [("github:tidalcycles/dirt-samples", ""), ({"kick": ["k.wav"]}, "http://x/")]


def test_drawing_methods_pass_through(strudel: Strudel) -> None:
    strudel.evaluate('note("c e").s("piano").pianoroll().punchcard()._scope()')
    assert len(strudel.query(0, 1)) == 2


def test_values_without_fields_are_skipped(strudel: Strudel) -> None:
    strudel.evaluate('stack(s("bd"), "c3 e3")')
    before = strudel.skipped
    assert [e.controls["s"] for e in strudel.query(0, 1)] == ["bd"]
    assert strudel.skipped == before + 2


def _close(a: Any, b: Any) -> bool:
    """Equal, with floats to 1e-9: V8 and QuickJS may differ in the last bits of libm."""
    if isinstance(a, float | int) and isinstance(b, float | int) and not isinstance(a, bool):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_close(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b, strict=True))
    return bool(a == b)


TUNES: dict[str, Any] = json.loads(FIXTURE.read_text())


@pytest.mark.parametrize("name", sorted(TUNES))
def test_tunes_match_node(name: str) -> None:
    tune = TUNES[name]
    strudel = Strudel()  # fresh, as Node's run: the REPL keeps cps across evaluations
    if "error" in tune:
        with pytest.raises(StrudelError):
            strudel.evaluate(tune["code"])
        return
    strudel.evaluate(tune["code"])
    assert strudel.cps == tune["cps"]
    got = [
        {"begin": e.begin, "end": e.end, "controls": e.controls}
        for e in strudel.query(0, CYCLES, tune["cps"])
    ]
    assert len(got) == len(tune["events"])
    for g, want in zip(got, tune["events"], strict=True):
        assert _close(g, want), (g, want)


def test_most_tunes_evaluate() -> None:
    ok = [t for t in TUNES.values() if "events" in t]
    assert len(TUNES) == 46 and len(ok) == 39


# -- timing ---------------------------------------------------------------------


def test_timeline_follows_cps_changes() -> None:
    """As cyclist.mjs: a cps carried by an event applies from the next window."""
    s = Strudel()
    s.evaluate('setcps(1)\ns("bd*4").cps("<1 2>")')
    times = [(round(t, 6), e.begin) for t, e in timeline(s, 2, window=0.5)]
    # cycle 0 at cps 1; the window from 0.5 is still at 1 until an event carries 2
    assert times[:4] == [(0.0, 0), (0.25, 0.25), (0.5, 0.5), (0.75, 0.75)]
    # events of cycle 1 carry cps 2: the first window of cycle 1 is at cps 1, then 2
    assert times[4:] == [(1.0, 1), (1.25, 1.25), (1.5, 1.5), (1.625, 1.75)]


# -- offline rendering ----------------------------------------------------------


def test_render_an_oscillator(tmp_path: Path) -> None:
    out = tmp_path / "saw.wav"
    code = 'setcps(1)\nnote("c3 e3").s("sawtooth")'  # two notes of 0.5 s
    n = render(code, out, cycles=1, strudel_maps=False, tail=0.5)
    assert n == 2
    channels, x = read_wav(out)
    assert channels == 2
    left = x[0::2]
    # the last note ends at 1 s plus the 0.1 s latency, then the tail
    assert len(left) == pytest.approx(48000 * (1.1 + 0.5), rel=0.01)
    playing, after = left[int(0.1 * 48000) : int(1.1 * 48000)], left[int(1.2 * 48000) :]
    assert rms(playing) > 0.01
    assert rms(after) < 1e-4  # silent after the release


def test_render_samples_and_reverb(tmp_path: Path) -> None:
    """A local bank, and the orbit reverb, built on the calling thread for the score."""
    bank = tmp_path / "samples" / "click"
    bank.mkdir(parents=True)
    with wave.open(str(bank / "click.wav"), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(48000)
        w.writeframes(bytes(2 * 200) + struct.pack("<h", 32767) + bytes(2 * 99))  # after the attack
    dry, wet = tmp_path / "dry.wav", tmp_path / "wet.wav"
    for code, path in (('s("click")', dry), ('s("click").room(0.8)', wet)):
        render(code, path, cycles=1, samples=[str(tmp_path / "samples")], strudel_maps=False)
    tail = slice(int(0.3 * 48000) * 2, int(1.0 * 48000) * 2)
    assert max(map(abs, read_wav(dry)[1])) > 0.5
    assert rms(read_wav(dry)[1][tail]) < 1e-5
    assert rms(read_wav(wet)[1][tail]) > 1e-4  # the reverb's tail


def test_cli_render(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    song = tmp_path / "song.strudel"
    song.write_text('setcps(2)\nnote("c3*2").s("square")')
    assert main(["--no-strudel-maps", "render", str(song), "--cycles", "2", "--tail", "0"]) == 0
    assert "4 events" in capsys.readouterr().out
    assert read_wav(song.with_suffix(".wav"))[1]
    song.write_text("s(")
    assert main(["--no-strudel-maps", "render", str(song)]) == 1


# -- blocking downloads ---------------------------------------------------------


def test_blocking_samples_download_on_first_use(tmp_path: Path) -> None:
    src = tmp_path / "remote" / "x.wav"
    src.parent.mkdir()
    with wave.open(str(src), "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(48000)
        w.writeframes(bytes(200))
    data = {"x": ["x.wav"]}
    base = src.parent.as_uri() + "/"
    lazy = StrudelSamples(tmp_path / "lazy")
    lazy.load_map(data, base)
    eager = StrudelSamples(tmp_path / "eager", blocking=True)
    eager.load_map(data, base)
    found = eager.get("x", 0, None)
    assert found is not None and found[0].path.exists()
    assert lazy.get("x", 0, None) is None  # queued for the background worker
