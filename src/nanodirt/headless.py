"""Strudel code without a browser: Strudel's own pattern engine, run in QuickJS.

``js/strudel.js`` bundles Strudel's pattern packages with a harness
(``scripts/strudel_bundle``, AGPL-3.0-or-later). Design and scope:
docs/dev/strudel-headless.md.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from . import _quickjs

logger = logging.getLogger(__name__)

TIME_LIMIT = 5.0  # seconds of CPU time per evaluation step; stops a runaway loop
MEMORY_LIMIT = 512 * 1024 * 1024
LATENCY = 0.1  # Strudel's scheduler latency (cyclist.mjs)


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
        self._ctx.set_time_limit(TIME_LIMIT)
        self._api = {
            n: self._ctx.get(f"__nanodirt_{n}") for n in ("evaluate", "result", "query", "hush")
        }

    def _log(self, level: str, message: str) -> None:
        level_no = logging.DEBUG if self._loading else logging.getLevelName(level.upper())
        logger.log(level_no, "strudel: %s", message)

    def _samples(self, args: str) -> None:
        spec, base = json.loads(args)
        if self.on_samples is not None:
            self.on_samples(spec, base)

    def evaluate(self, code: str) -> None:
        """Evaluate *code* as Strudel's REPL does; raise StrudelError if it fails."""
        with self._lock:
            try:
                self._call("evaluate", code)
                while self._ctx.execute_pending_job():
                    pass
            except _quickjs.JSException as exc:  # e.g. interrupted at the time limit
                message, _, stack = str(exc).partition("\n")
                raise StrudelError(message, stack) from exc
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

        def load(spec: Any, base: str) -> None:
            if isinstance(spec, dict):
                remote.load_map(spec, base)
            else:
                remote.load_map_file(str(spec), base)

        strudel = Strudel(on_samples=load)
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
