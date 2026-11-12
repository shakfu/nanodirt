"""Headless Strudel: the QuickJS binding, the bundle against Node, and offline rendering.

``tests/fixtures/strudel_tunes.json`` holds Node's events for Strudel's tunes, recorded
by ``scripts/strudel_bundle/build.py`` from the same bundle.
"""

from __future__ import annotations

import json
import logging
import math
import struct
import subprocess
import sys
import textwrap
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from nanodirt import _quickjs
from nanodirt.cli import main
from nanodirt.headless import (
    LATENCY,
    PREPARE_CYCLES,
    Scheduler,
    Strudel,
    StrudelError,
    play_file,
    render,
    timeline,
)
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


def test_python_ints_keep_their_value() -> None:
    """Past int32 an int was truncated (2**40 became 0); past a double it crashed."""
    ctx = _quickjs.Context()
    ctx.eval("var same = (x) => x")
    same = ctx.get("same")
    for v in (2**31 - 1, -(2**31), 2**31, -(2**31) - 1, 2**40, 2**53):
        assert same(v) == v
    assert same(10**400) == math.inf and same(-(10**400)) == -math.inf


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


TUNES: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))


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


# fraction.js 5.2.1's loop in parse, as a function; the bundle replaces it (farey.js).
_FAREY_REFERENCE = """
function slowFarey(p1, N) {
  let n = 0, d = 1, A = 0, B = 1, C = 1, D = 1;
  while (B <= N && D <= N) {
    let M = (A + C) / (B + D);
    if (p1 === M) {
      if (B + D <= N) { n = A + C; d = B + D; } else if (D > B) { n = C; d = D; } else { n = A; d = B; }
      break;
    } else {
      if (p1 > M) { A += C; B += D; } else { C += A; D += B; }
      if (B > N) { n = C; d = D; } else { n = A; d = B; }
    }
  }
  return [n, d];
}
function ulps(x, k) {
  const f = new Float64Array([x]), u = new BigUint64Array(f.buffer);
  u[0] += BigInt(k);
  return f[0];
}
function differ(xs, N) {
  return xs.filter((x) => {
    const [a, b] = [slowFarey(x, N), __nanodirt_farey(x, N)];
    return a[0] !== b[0] || a[1] !== b[1];
  });
}
"""


def test_fast_farey_matches_fraction_js() -> None:
    """The bundle's float-to-fraction gives fraction.js's result, also next to short fractions."""
    s = Strudel()
    s._ctx.set_time_limit(-1)  # the reference loop is slow by design: over 5 s with MSVC
    s._ctx.eval(_FAREY_REFERENCE)
    cases = """
      const xs = [];
      for (let q = 1; q <= 60; q++) for (let p = 1; p < q; p++) for (let k = -3; k <= 3; k++) xs.push(ulps(p / q, k));
      for (let c = 1; c < 5000; c += 3) for (const z of [10, 100, 1000, 1e4]) xs.push(c / 1024 / z);
      let s = 1;
      for (let i = 0; i < 3000; i++) xs.push((s = (s * 48271) % 2147483647) / 2147483647);
    """
    for n in (1e2, 1e4):
        out = s._ctx.eval(
            f"(() => {{ {cases} return JSON.stringify([xs.length, differ(xs, {n})]); }})()"
        )
        count, bad = json.loads(out)
        assert count > 20000 and bad == [], (n, bad[:5])
    # fraction.js's own bound, on its slowest inputs: one ulp from 1/2, 1/3, 3/4
    out = s._ctx.eval("JSON.stringify(differ([ulps(1/2, 1), ulps(1/3, -1), ulps(3/4, 1)], 1e7))")
    assert json.loads(out) == []


def test_off_grid_query_is_fast() -> None:
    """A bound one ulp from 1/2 took 265 ms per query with fraction.js's own loop, 1.5 ms now."""
    s = Strudel()
    s.evaluate('note("c3 e3 g3").s("sawtooth")')
    s.query(0, 0.1)
    start = time.perf_counter()
    s.query(0.5000000000000001, 0.525)
    assert time.perf_counter() - start < 0.05


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


# -- realtime scheduler ---------------------------------------------------------


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def drive(
    code: str, seconds: float, clock: FakeClock | None = None
) -> tuple[Scheduler, list[tuple[float, dict[str, Any]]]]:
    """Run a Scheduler on a fake clock, one tick per INTERVAL; (scheduler, [(time, controls)])."""
    clock = clock or FakeClock()
    played: list[tuple[float, dict[str, Any]]] = []
    sched = Scheduler(Strudel, lambda c, t: played.append((t, c)), clock)
    sched.evaluate(code)
    end = clock.t + seconds
    while clock.t < end:
        sched.tick()
        clock.t += 0.1
    return sched, played


def test_scheduler_times_events_as_cyclist() -> None:
    """First tick at now + 10 ms; an event plays at its cycle / cps after that, plus latency."""
    _, played = drive('setcps(1)\ns("bd*4")', 2.0)
    first = 1000.0 + 0.01 + LATENCY
    assert [round(t, 9) for t, _ in played] == [
        round(first + k * 0.25, 9) for k in range(len(played))
    ]
    assert len(played) >= 8 and all(c["s"] == "bd" and c["cps"] == 1 for _, c in played)


def test_scheduler_plays_the_offline_timeline() -> None:
    """Over a whole tune, the same events at the same times as timeline(), offset by the start."""
    code = TUNES["festivalOfFingers3"]["code"]
    sched, played = drive(code, 8.0)
    cycles = sched._last_end
    want = [(t, e.controls) for t, e in timeline(Strudel_with(code), cycles)]
    got = [(t - (1000.0 + 0.01 + LATENCY), c) for t, c in played]
    assert len(got) == len(want) > 50

    def order(x: list[tuple[float, dict[str, Any]]]) -> list[tuple[float, dict[str, Any]]]:
        # the windows differ, so does Strudel's order within one
        return sorted(x, key=lambda e: (round(e[0], 6), json.dumps(e[1], sort_keys=True)))

    for (tg, cg), (tw, cw) in zip(order(got), order(want), strict=True):
        assert tg == pytest.approx(tw, abs=1e-9) and _close(cg, cw)


def Strudel_with(code: str) -> Strudel:
    s = Strudel()
    s.evaluate(code)
    return s


def test_scheduler_follows_setcps_on_reevaluation() -> None:
    clock = FakeClock()
    sched, played = drive('setcps(1)\ns("bd")', 1.0, clock)
    sched.evaluate('setcps(2)\ns("bd")')
    assert sched.cps == 2
    n = len(played)
    for _ in range(20):
        sched.tick()
        clock.t += 0.1
    times = [t for t, _ in played[n:]]
    assert len(times) >= 3 and all(b - a == pytest.approx(0.5) for a, b in zip(times, times[1:]))


def test_setcps_again_undoes_an_event_cps_change() -> None:
    """As cyclist.mjs setCps: each setcps call applies, even with the value set before."""
    clock = FakeClock()
    sched, _ = drive('setcps(1)\ns("bd").cps(2)', 3.0, clock)
    assert sched.cps == 2  # the events changed it
    sched.evaluate('setcps(1)\ns("bd")')
    assert sched.cps == 1


def test_code_without_setcps_keeps_the_cps() -> None:
    """A fresh runtime starts at cps 0.5; that is not a setcps call."""
    sched, _ = drive('setcps(2)\ns("bd")', 0.5)
    sched.evaluate('s("hh")')
    assert sched.cps == 2


def test_slow_evaluation_drops_no_events() -> None:
    """Evaluation runs in a spare runtime, so the playing one keeps answering queries."""
    played: list[float] = []
    sched = Scheduler(Strudel, lambda c, t: played.append(t))
    sched.evaluate('setcps(1)\ns("hh*8")')
    sched.start()
    try:
        time.sleep(0.3)
        start = time.perf_counter()
        sched.evaluate(
            'setcps(1)\nconst t0 = Date.now(); while (Date.now() - t0 < 600) {}\ns("hh*8")'
        )
        assert time.perf_counter() - start > 0.6
        time.sleep(0.3)
    finally:
        sched.stop()
    assert sched.skipped_ticks == 0
    gaps = [b - a for a, b in zip(played, played[1:])]
    assert len(played) > 10 and max(gaps) == pytest.approx(0.125, abs=1e-6)


def test_evaluation_prepares_the_next_cycles_before_they_play() -> None:
    clock = FakeClock()
    prepared: list[tuple[list[str], list[str]]] = []  # (to prepare, playing meanwhile)

    def prepare(events: list[dict[str, Any]]) -> None:
        playing = sorted(e.controls["s"] for e in sched.strudel.query(0, 1))
        prepared.append((sorted(e["s"] for e in events), playing))

    sched = Scheduler(Strudel, lambda c, t: None, clock, prepare=prepare)
    sched.evaluate('setcps(1)\ns("bd sd")')
    assert prepared == [(sorted(["bd", "sd"] * PREPARE_CYCLES), [])]
    for _ in range(10):  # 1 s at cps 1
        sched.tick()
        clock.t += 0.1
    sched.evaluate('setcps(1)\ns("<hh cp sn>")')
    # onsets from where the new pattern starts (cycle 1.1): cycles 2-5, not 0-3 (hh cp sn hh)
    assert sched._last_end == pytest.approx(1.1)
    assert prepared[1] == (["cp", "hh", "sn", "sn"], ["bd", "sd"])


def test_a_failing_prepare_does_not_fail_the_evaluation(caplog: pytest.LogCaptureFixture) -> None:
    def prepare(events: list[dict[str, Any]]) -> None:
        raise RuntimeError("no network")

    sched = Scheduler(Strudel, lambda c, t: None, FakeClock(), prepare=prepare)
    with caplog.at_level(logging.ERROR):
        sched.evaluate('s("bd")')
    assert "preparing sounds failed" in caplog.text
    assert [e.controls["s"] for e in sched.strudel.query(0, 1)] == ["bd"]


def test_scheduler_skips_ticks_in_the_past() -> None:
    clock = FakeClock()
    sched, played = drive('s("hh*8")', 0.5, clock)
    clock.t += 2.0  # the process stalled
    n = len(played)
    sched.tick()
    assert sched.skipped_ticks > 0
    assert all(t > clock.t for t, _ in played[n:])


def test_evaluation_error_is_raised_not_also_logged(caplog: pytest.LogCaptureFixture) -> None:
    s = Strudel()
    with caplog.at_level(logging.WARNING, logger="nanodirt.headless"):
        with pytest.raises(StrudelError, match="not a function"):
            s.evaluate('s("bd").nosuchmethod()')
    assert caplog.records == []


def test_failed_reevaluation_keeps_the_pattern() -> None:
    clock = FakeClock()
    sched, played = drive('s("bd*2")', 0.5, clock)
    with pytest.raises(StrudelError):
        sched.evaluate('s("bd*2").nosuchmethod()')
    n = len(played)
    for _ in range(10):
        sched.tick()
        clock.t += 0.1
    assert len(played) > n and all(c["s"] == "bd" for _, c in played[n:])


def test_scheduler_thread_plays_and_stops() -> None:
    played: list[float] = []
    sched = Scheduler(Strudel, lambda c, t: played.append(t))
    sched.evaluate('setcps(1)\ns("hh*8")')
    sched.start()
    time.sleep(0.4)
    sched.evaluate('setcps(1)\ns("hh*4")')  # from another thread, while it plays
    time.sleep(0.2)
    sched.stop()
    n = len(played)
    time.sleep(0.25)
    assert n >= 4 and len(played) == n
    assert sched._last_end == 0  # stopped: the next start is at cycle 0


def wait_for(cond: Callable[[], bool], what: object = None, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < deadline, what
        time.sleep(0.02)


def test_play_file_reevaluates_on_save(tmp_path: Path) -> None:
    song = tmp_path / "song.strudel"
    song.write_text('setcps(1)\ns("bd*4")')
    played: list[dict[str, Any]] = []
    reports: list[str] = []
    sched = Scheduler(Strudel, lambda c, t: played.append(c))
    stop = threading.Event()
    thread = threading.Thread(target=play_file, args=(song, sched, stop, reports.append, 0.02))
    thread.start()

    try:
        wait_for(lambda: any(c["s"] == "bd" for c in played), reports)
        song.write_text('setcps(1)\ns("hh*8")')
        wait_for(lambda: played[-1]["s"] == "hh")
        song.write_text('setcps(1)\ns("hh*8").nosuchmethod()')  # fails: hh plays on
        wait_for(lambda: len(reports) == 3)
        n = len(played)
        wait_for(lambda: len(played) > n + 4)
        assert all(c["s"] == "hh" for c in played[n:])
    finally:
        stop.set()
        thread.join(5)
        sched.stop()
    assert reports[:2] == ["nanodirt: evaluated song.strudel"] * 2
    assert reports[2].startswith("nanodirt: song.strudel: ")


def test_play_file_waits_for_a_save_to_finish(tmp_path: Path) -> None:
    """A file still being written (truncated, partly written) is not evaluated."""
    song = tmp_path / "song.strudel"
    song.write_text('s("hh")')
    reports: list[str] = []
    sched = Scheduler(Strudel, lambda c, t: None)
    stop = threading.Event()
    # a long poll: a stalled writer must not leave a half-written file stable for one
    thread = threading.Thread(target=play_file, args=(song, sched, stop, reports.append, 0.2))
    try:
        thread.start()
        wait_for(lambda: len(reports) == 1, reports)
        code = 's("bd*4")'
        for i in range(60):  # changes every 5-16 ms (Windows' timer), faster than a poll
            song.write_text(code[: i % len(code) + 1] if i % 2 else "x" * (i + 2))
            time.sleep(0.005)
        assert reports == ["nanodirt: evaluated song.strudel"]
        song.write_text(code)
        wait_for(lambda: len(reports) == 2, reports)
        time.sleep(0.5)  # and no more
    finally:
        stop.set()
        thread.join(5)
        sched.stop()
    assert reports == ["nanodirt: evaluated song.strudel"] * 2


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


def test_cli_reads_code_as_utf8(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Windows' locale encoding is not UTF-8; a non-ASCII comment must not garble the code."""
    song = tmp_path / "song.strudel"
    song.write_text(
        '// \u00fcber \u266a \u30c9\nsetcps(2)\nnote("c3*2").s("square")', encoding="utf-8"
    )
    assert main(["--no-strudel-maps", "render", str(song), "--cycles", "1", "--tail", "0"]) == 0
    assert "2 events" in capsys.readouterr().out


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
