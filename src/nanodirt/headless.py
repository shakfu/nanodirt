"""Strudel code without a browser: Strudel's own pattern engine, run in QuickJS.

``js/strudel.js`` bundles Strudel's pattern packages with a harness
(``scripts/strudel_bundle``, AGPL-3.0-or-later). Design and scope:
docs/dev/strudel-headless.md.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import _quickjs

if TYPE_CHECKING:
    from .strudel import StrudelSamples

logger = logging.getLogger(__name__)

TIME_LIMIT = 5.0  # seconds of CPU time per evaluation step; stops a runaway loop
MEMORY_LIMIT = 512 * 1024 * 1024
LATENCY = 0.1  # Strudel's scheduler latency (cyclist.mjs)
# zyklus.mjs, as cyclist.mjs uses it: a tick covers DURATION seconds; every INTERVAL the clock
# runs each tick due before INTERVAL + OVERLAP from now.
DURATION = 0.05
INTERVAL = 0.1
OVERLAP = 0.1
MIN_LATENCY = 0.01


class StrudelError(Exception):
    """Strudel code failed to evaluate; ``stack`` holds the JavaScript stack."""

    def __init__(self, message: str, stack: str = "") -> None:
        super().__init__(message)
        self.stack = stack


@dataclass(frozen=True)
class StrudelEvent:
    """An onset: its whole's span in cycles, and the fields Strudel's OSC output sends."""

    begin: float
    end: float
    controls: dict[str, Any]


def _bundle() -> str:
    return resources.files("nanodirt").joinpath("js/strudel.js").read_text(encoding="utf-8")


class Strudel:
    """One Strudel runtime. Thread-safe: calls are serialized, as QuickJS is single-threaded.

    *on_samples* receives each ``samples(map, base)`` call from Strudel code: *map*
    is a URL, a ``github:`` spec or a map object.
    """

    def __init__(self, on_samples: Callable[[Any, str], None] | None = None) -> None:
        self.on_samples = on_samples
        self.skipped = 0  # haps without OSC fields, as Strudel's scheduler skips them
        self._lock = threading.Lock()
        self._ctx = _quickjs.Context()
        self._ctx.set_memory_limit(MEMORY_LIMIT)
        self._loading = True  # Strudel's load-time notices ("not in browser?") log at debug
        self._ctx.add_callable("__nanodirt_host_log", self._log)
        self._ctx.add_callable("__nanodirt_host_samples", self._samples)
        self._ctx.eval(_bundle())
        self._loading = False
        self._evaluating = False
        self._ctx.set_time_limit(TIME_LIMIT)
        self._api = {
            n: self._ctx.get(f"__nanodirt_{n}") for n in ("evaluate", "result", "query", "hush")
        }

    def _log(self, level: str, message: str) -> None:
        # An evaluation error is raised as StrudelError; Strudel's own log of it would repeat it.
        quiet = self._loading or (self._evaluating and level == "error")
        level_no = logging.DEBUG if quiet else logging.getLevelName(level.upper())
        logger.log(level_no, "strudel: %s", message)

    def _samples(self, args: str) -> None:
        spec, base = json.loads(args)
        if self.on_samples is not None:
            self.on_samples(spec, base)

    def evaluate(self, code: str) -> None:
        """Evaluate *code* as Strudel's REPL does; raise StrudelError if it fails."""
        with self._lock:
            self._evaluating = True
            try:
                self._call("evaluate", code)
                while self._ctx.execute_pending_job():
                    pass
            except _quickjs.JSException as exc:  # e.g. interrupted at the time limit
                message, _, stack = str(exc).partition("\n")
                raise StrudelError(message, stack) from exc
            finally:
                self._evaluating = False
            result = json.loads(self._call("result"))
        if result["state"] == "pending":
            raise StrudelError(
                "evaluation did not finish: it waits on something that never resolves"
            )
        if result["state"] == "error":
            raise StrudelError(result["message"], result.get("stack", ""))

    @property
    def cps(self) -> float:
        """The tempo the code set (``setcps``, ``setcpm``); 0.5 by default."""
        with self._lock:
            return float(json.loads(self._call("result"))["cps"])

    @property
    def cps_calls(self) -> list[float]:
        """The last evaluation's ``setcps``/``setcpm`` calls, in order, as cps."""
        with self._lock:
            calls = json.loads(self._call("result"))["cpsCalls"]
        return [0.5 if c is None else float(c) for c in calls]  # setCps(cps = 0.5)

    def query(self, begin: float, end: float, cps: float | None = None) -> list[StrudelEvent]:
        """Onset events in cycles [*begin*, *end*), in Strudel's order."""
        with self._lock:
            rate = cps if cps is not None else float(json.loads(self._call("result"))["cps"])
            out = json.loads(self._call("query", begin, end, rate))
        if out["skipped"]:
            if not self.skipped:
                logger.warning("strudel: %s", out["error"])
            self.skipped += out["skipped"]
        return [StrudelEvent(e["begin"], e["end"], e["controls"]) for e in out["events"]]

    def hush(self) -> None:
        with self._lock:
            self._call("hush")

    def _call(self, name: str, *args: Any) -> Any:
        return self._api[name](*args)


def timeline(
    strudel: Strudel, cycles: float, window: float = 0.25
) -> Iterator[tuple[float, StrudelEvent]]:
    """(seconds, event) for cycles [0, *cycles*), following ``cps`` changes as Strudel does.

    As cyclist.mjs: an event carrying ``cps`` changes the tempo from the next window on.
    """
    cps = strudel.cps
    begin = 0.0
    cycles_at_change, seconds_at_change = 0.0, 0.0
    while begin < cycles:
        end = min(begin + window, cycles)
        change = None
        for e in strudel.query(begin, end, cps):
            yield (e.begin - cycles_at_change) / cps + seconds_at_change, e
            new = e.controls.get("cps")
            if isinstance(new, (int, float)) and new > 0 and new != cps:
                change = float(new)
        if change is not None:
            seconds_at_change += (end - cycles_at_change) / cps
            cycles_at_change, cps = end, change
        begin = end


class Scheduler:
    """cyclist.mjs in a thread: plays Strudel code's events as ``play(controls, time)``.

    Each evaluation runs in a fresh runtime from *new_strudel*, loaded in advance; it replaces
    the playing one only if it succeeds. Queries never wait for an evaluation, however slow.
    *clock* gives seconds on *play*'s timeline (Dirt's is Unix time). Window bounds and event
    times follow cyclist.mjs exactly, so events land where strudel.cc puts them.
    """

    def __init__(
        self,
        new_strudel: Callable[[], Strudel],
        play: Callable[[dict[str, Any], float], object],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.new_strudel = new_strudel
        self.play = play
        self.clock = clock
        self.strudel = new_strudel()  # the playing runtime; silent until an evaluation
        self.cps = self.strudel.cps
        self.skipped_ticks = 0  # ticks already past when the clock reached them
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._spare: Strudel | None = None
        self._evaluating = threading.Lock()  # one evaluation at a time: one spare
        self._loader: threading.Thread | None = None
        self._load_spare()
        self._reset()

    def _reset(self) -> None:
        self._phase = 0.0
        self._ticks_since_change = 0
        self._cycles_at_change = 0.0
        self._seconds_at_change = 0.0
        self._last_end = 0.0

    def _load_spare(self) -> None:
        def load() -> None:
            self._spare = self.new_strudel()

        self._loader = threading.Thread(target=load, name="nanodirt-strudel-spare", daemon=True)
        self._loader.start()

    def _take_spare(self) -> Strudel:
        if self._loader is not None:
            self._loader.join()
        spare, self._spare = self._spare, None
        return spare if spare is not None else self.new_strudel()  # the loader failed

    def evaluate(self, code: str) -> None:
        """Evaluate *code*; the old pattern plays on if it fails (raises StrudelError)."""
        with self._evaluating:
            runtime = self._take_spare()
            try:
                runtime.evaluate(code)  # a failed runtime is dropped: its state is unknown
                calls = runtime.cps_calls
            finally:
                self._load_spare()
        with self._lock:
            self.strudel = runtime
            for cps in calls:  # cyclist.mjs setCps
                if cps != self.cps:
                    self.cps = cps
                    self._ticks_since_change = 0

    def tick(self) -> None:
        """One clock callback (zyklus.mjs onTick): each tick due within the lookahead."""
        with self._lock:
            t = self.clock()
            lookahead = t + INTERVAL + OVERLAP
            if self._phase == 0:
                self._phase = t + MIN_LATENCY
            while self._phase < lookahead:
                self._phase = math.floor(self._phase * 1e4 + 0.5) / 1e4  # JS Math.round
                self._query(self._phase, t)
                self._phase += DURATION

    def _query(self, phase: float, now: float) -> None:
        if self._ticks_since_change == 0:
            self._cycles_at_change = self._last_end
            self._seconds_at_change = phase
        self._ticks_since_change += 1
        begin = self._last_end
        end = self._cycles_at_change + self._ticks_since_change * DURATION * self.cps
        self._last_end = end
        if phase < now:
            self.skipped_ticks += 1
            return
        try:
            events = self.strudel.query(begin, end, self.cps)
        except Exception:  # noqa: BLE001 -- as cyclist.mjs: log, keep the clock running
            logger.exception("strudel: query failed")
            return
        for e in events:
            when = (e.begin - self._cycles_at_change) / self.cps + self._seconds_at_change + LATENCY
            self.play(e.controls, when)
            new = e.controls.get("cps")
            if isinstance(new, (int, float)) and new > 0 and new != self.cps:
                self.cps = float(new)
                self._ticks_since_change = 0

    @property
    def running(self) -> bool:
        return self._thread is not None

    def start(self) -> None:
        """Start the clock thread, from cycle 0."""
        self.stop()
        self._stop.clear()
        self.tick()

        def run() -> None:
            while not self._stop.wait(INTERVAL):
                try:
                    self.tick()
                except Exception:  # noqa: BLE001 -- a bad tick must not stop the clock
                    logger.exception("strudel: tick failed")

        self._thread = threading.Thread(target=run, name="nanodirt-strudel", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the clock; the next start begins at cycle 0, as cyclist.mjs's stop."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(5)
            self._thread = None
        with self._lock:
            self._reset()


def load_sample_map(remote: StrudelSamples, spec: Any, base: str) -> None:
    """A ``samples(spec, base)`` call: a map object, a URL, a ``github:`` spec or a path."""
    if isinstance(spec, dict):
        remote.load_map(spec, base)
    else:
        remote.load_map_file(str(spec), base)


def play_file(
    path: Path,
    scheduler: Scheduler,
    stop: threading.Event,
    report: Callable[[str], object] = print,
    poll: float = 0.1,
) -> None:
    """Evaluate *path*, start *scheduler*, and re-evaluate on each save until *stop* is set.

    A failed evaluation is reported and the previous pattern plays on, as on strudel.cc.
    """

    def mark() -> tuple[int, int] | None:
        try:
            st = path.stat()
        except FileNotFoundError:  # an editor replacing the file
            return None
        return st.st_mtime_ns, st.st_size

    seen = last = None
    while not stop.is_set():
        now = mark()
        # A save truncates, then writes: read only once the file has not changed for a poll.
        if now is not None and now == last and now != seen:
            try:
                code = path.read_text()
            except FileNotFoundError:
                code = None
            if code is not None and mark() == now:
                seen = now
                try:
                    scheduler.evaluate(code)
                    report(f"nanodirt: evaluated {path.name}")
                except StrudelError as exc:
                    report(f"nanodirt: {path.name}: {exc}")
                if not scheduler.running:
                    scheduler.start()
        last = now
        stop.wait(poll)


def render(
    code: str,
    path: str | Path,
    cycles: float,
    samples: list[str] | None = None,
    strudel_maps: bool = True,
    num_channels: int = 2,
    sample_rate: int = 48000,
    tail: float = 2.0,
) -> int:
    """Render *cycles* of Strudel *code* to a float WAV at *path*; return the event count.

    *samples* are local folders (Dirt-Samples layout). With *strudel_maps*, Strudel's
    default sample maps load, as on strudel.cc. A sample downloads when first played.
    """
    from .cli import CACHE
    from .dirt import Dirt
    from .offline import OfflineEngine
    from .strudel import StrudelSamples

    engine = OfflineEngine(num_channels, sample_rate)
    try:
        dirt = Dirt(engine, num_channels=num_channels, sample_rate=sample_rate, late_threshold=None)
        dirt.clock = lambda: engine.now
        dirt.add_samples(samples or [])
        remote = StrudelSamples(CACHE / "strudel", blocking=True)
        if strudel_maps:
            remote.load_defaults()
        dirt.remote = remote

        strudel = Strudel(on_samples=lambda spec, base: load_sample_map(remote, spec, base))
        strudel.evaluate(code)
        events = list(timeline(strudel, cycles))
        dirt.start()
        for seconds, e in events:
            engine.now = seconds
            dirt.play_strudel(e.controls, seconds + LATENCY)
            engine.end = max(engine.end, seconds + LATENCY + float(e.controls.get("delta", 0)))
        engine.render(path, tail=tail)
        return len(events)
    finally:
        engine.close()
