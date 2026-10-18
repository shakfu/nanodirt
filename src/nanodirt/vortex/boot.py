"""Replacement for ``vortex.boot``: the same names, playing through nanodirt in-process.

vortex's GUI and REPL take their namespace from ``vortex.boot`` (``vortex_dsl``).
This module stands in for it, so patterns call ``Dirt.play`` directly: no OSC,
and neither ``vortex.stream`` nor pyliblo3 is imported. Timestamps come from an
Ableton Link clock, so sessions stay in sync with other Link apps.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from fractions import Fraction
from typing import Any

import link
from vortex import *  # noqa: F403  (the pattern language, as vortex.boot does)
from vortex import TimeSpan, silence

log = logging.getLogger("nanodirt.vortex")
dirt: Any = None  # the Dirt to play through; set by nanodirt.vortex.run


class LinkClock:
    def __init__(self, bpm: float = 120, bpc: int = 4, rate: float = 1 / 20) -> None:
        self._link = link.Link(bpm)
        self.bpc = bpc
        self.rate = rate
        self._subs: list[Stream] = []
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None

    def subscribe(self, s: Stream) -> None:
        with self._lock:
            self._subs.append(s)

    def unsubscribe(self, s: Stream) -> None:
        with self._lock:
            self._subs.remove(s)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join()

    def _run(self) -> None:
        self._link.enabled = True
        clock = self._link.clock()
        start = clock.micros()
        frame = self.rate * 1e6
        ticks = 0
        while self._running:
            ticks += 1
            t0 = math.floor(start + ticks * frame)
            t1 = math.floor(start + (ticks + 1) * frame)
            wait = (t0 - clock.micros()) / 1e6
            if wait > 0:
                time.sleep(wait)
            st = self._link.captureSessionState()
            cps = st.tempo() / self.bpc / 60
            span = (st.beatAtTime(t0, 0) / self.bpc, st.beatAtTime(t1, 0) / self.bpc)
            # Link micros -> Unix seconds, measured once per tick
            offset = time.time() - clock.micros() / 1e6
            with self._lock:
                subs = list(self._subs)
            for s in subs:
                try:
                    s.tick(span, st, cps, self.bpc, offset)
                except Exception:  # noqa: BLE001 -- vortex drops these silently; log them
                    log.exception("stream %s", s.name)
        self._link.enabled = False


class Stream:
    latency = 0.2

    def __init__(self, name: str) -> None:
        self.name = name
        self.pattern: Any = None

    def tick(self, span: tuple[float, float], st: Any, cps: float, bpc: int, offset: float) -> None:
        if self.pattern is None:
            return
        for e in self.pattern.onsets_only().query(TimeSpan(*span)):
            on = st.timeAtBeat(e.whole.begin * bpc, 0)
            off = st.timeAtBeat(e.whole.end * bpc, 0)
            params = {k: float(v) if isinstance(v, Fraction) else v for k, v in e.value.items()}
            params.update(cps=float(cps), cycle=float(e.whole.begin), delta=(off - on) / 1e6)
            when = on / 1e6 + offset + self.latency + float(params.get("nudge", 0))
            dirt.play(params, when)


_default_clock = LinkClock(bpm=120)
__streams: dict[Any, Stream] = {}


def p(key: Any, pattern: Any = None) -> Stream:
    if key not in __streams:
        __streams[key] = Stream(name=str(key))
        _default_clock.subscribe(__streams[key])
    if pattern:
        __streams[key].pattern = pattern
    return __streams[key]


def hush() -> None:
    for s in __streams.values():
        s.pattern = silence
        _default_clock.unsubscribe(s)
    __streams.clear()


class __Streams:
    def __setitem__(self, key: Any, value: Any) -> None:
        p(key, value)


d = __Streams()
