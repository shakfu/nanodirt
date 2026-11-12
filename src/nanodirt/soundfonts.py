"""Strudel's ``gm_`` soundfonts (packages/soundfonts, fontloader.mjs).

A ``gm_`` instrument is a list of webaudiofont presets; ``n`` picks one. A
preset is a JavaScript file of zones, each a key range with tuning, loop
points and an MP3 file in base64. nanodirt fetches a preset into its cache
the first time an event plays it, parses it as data, and decodes the zone an
event needs to a WAV file. Until then the event is silent, as superdough
skips a note whose sound is still loading. Licences: docs/dev/soundfonts.md.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import math
import queue
import re
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
from array import array
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .library import Sample, read_header
from .retry import BLOCKING_TRIES, ERRORS, Backoff, attempt, transient

logger = logging.getLogger(__name__)

SOUNDFONT_URL = "https://felixroos.github.io/webaudiofontdata/sound"
GM: dict[str, list[str]] = json.loads(
    (Path(__file__).parent / "js" / "gm.json").read_text(encoding="utf-8")
)
_PRESET_NAME = re.compile(r"^[0-9A-Za-z_]+$")  # names become cache paths
MAX_PRESET_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class Zone:
    """One zone of a preset; loop points are frames at ``sample_rate``."""

    key_low: float
    key_high: float
    original_pitch: float
    coarse_tune: float
    fine_tune: float
    loop_start: float
    loop_end: float
    sample_rate: float
    file: str  # base64 MP3

    def playback_rate(self, midi: float) -> float:
        """``getFontBufferSource``: the rate that sounds *midi* from this zone."""
        base_detune = self.original_pitch - 100.0 * self.coarse_tune - self.fine_tune
        return float(2 ** ((100.0 * midi - base_detune) / 1200.0))

    @property
    def loops(self) -> bool:
        return 1 < self.loop_start < self.loop_end


# -- preset files ------------------------------------------------------------------------

_TOKEN = re.compile(
    r"""(?P<space>\s+|//[^\n]*|/\*.*?\*/)
    |(?P<punct>[{}\[\],:;])
    |(?P<number>-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
    |'(?P<sq>[^'\\\n]*)'|"(?P<dq>[^"\\\n]*)"
    |(?P<name>[A-Za-z_$][\w$]*)""",
    re.VERBOSE | re.DOTALL,
)


class PresetError(ValueError):
    pass


def _tokens(text: str, pos: int) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if m is None:
            raise PresetError(f"unexpected {text[pos : pos + 20]!r} at {pos}")
        pos = m.end()
        kind, token = m.lastgroup, m.group(m.lastgroup or 0)
        if kind == "space":
            continue
        if token == ";" and kind == "punct":
            break  # the end of the preset's statement
        if kind in ("sq", "dq"):
            out.append(("str", token))
        elif kind == "number":
            out.append(("num", float(token)))
        elif token in ("true", "false"):
            out.append(("lit", token == "true"))
        else:
            out.append((kind or "", token))  # name or punct
    return out


def _value(tokens: list[tuple[str, Any]], i: int) -> tuple[Any, int]:
    """A JavaScript literal of objects, arrays, numbers, strings and booleans."""
    if i >= len(tokens):
        raise PresetError("unexpected end of preset")
    kind, v = tokens[i]
    if kind in ("num", "str", "lit"):
        return v, i + 1
    if (kind, v) == ("punct", "["):
        items: list[Any] = []
        i += 1
        while tokens[i] != ("punct", "]"):
            item, i = _value(tokens, i)
            items.append(item)
            if tokens[i] == ("punct", ","):
                i += 1
        return items, i + 1
    if (kind, v) == ("punct", "{"):
        obj: dict[str, Any] = {}
        i += 1
        while tokens[i] != ("punct", "}"):
            if tokens[i][0] not in ("name", "str") or tokens[i + 1] != ("punct", ":"):
                raise PresetError(f"expected a key at token {i}")
            key = tokens[i][1]
            obj[key], i = _value(tokens, i + 2)
            if tokens[i] == ("punct", ","):
                i += 1
        return obj, i + 1
    raise PresetError(f"unexpected {v!r} at token {i}")


def parse_preset(text: str) -> list[Zone]:
    """The zones of a preset file (``var _tone_<name>={zones:[...]};``), read as data."""
    start = text.find("={")
    if start < 0:
        raise PresetError("no preset object")
    try:
        preset, _ = _value(_tokens(text, start + 1), 0)
    except IndexError:
        raise PresetError("unexpected end of preset") from None
    zones = preset.get("zones") if isinstance(preset, dict) else None
    if not isinstance(zones, list):
        raise PresetError("no zones")
    out = []
    for z in zones:
        if not isinstance(z, dict) or not isinstance(z.get("file"), str):
            continue  # fontloader's raw ``sample`` zones are untested there; none are in gm.json
        nums = {}
        for key in ("keyRangeLow", "keyRangeHigh", "originalPitch", "coarseTune", "fineTune",
                    "loopStart", "loopEnd", "sampleRate"):  # fmt: skip
            v = z.get(key)
            if isinstance(v, bool) or not isinstance(v, float) or not math.isfinite(v):
                raise PresetError(f"zone {key} is {v!r}")
            nums[key] = v
        if nums["sampleRate"] <= 0:
            raise PresetError(f"zone sampleRate is {nums['sampleRate']}")
        out.append(Zone(
            nums["keyRangeLow"], nums["keyRangeHigh"], nums["originalPitch"], nums["coarseTune"],
            nums["fineTune"], nums["loopStart"], nums["loopEnd"], nums["sampleRate"], z["file"],
        ))  # fmt: skip
    if not out:
        raise PresetError("no playable zones")
    return out


def find_zone(zones: list[Zone], midi: float) -> int | None:
    """``findZone``: the first zone whose key range, widened by one key, holds *midi*."""
    for i, z in enumerate(zones):
        if z.key_low <= midi <= z.key_high + 1:
            return i
    return None


def write_wav(path: Path, pcm: bytes, channels: int, sample_rate: int) -> None:
    """32-bit float WAV, written to a temporary file and renamed into place."""
    if sys.byteorder != "little":
        a = array("f", pcm)
        a.byteswap()
        pcm = a.tobytes()
    fmt = struct.pack("<HHIIHHH", 3, channels, sample_rate, sample_rate * channels * 4,
                      channels * 4, 32, 0)  # fmt: skip
    frames = len(pcm) // (4 * channels)
    body = (b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
            + b"fact" + struct.pack("<II", 4, frames)
            + b"data" + struct.pack("<I", len(pcm)) + pcm)  # fmt: skip
    tmp = path.with_suffix(".part")
    tmp.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    tmp.replace(path)


def decode_zone(zone: Zone, path: Path) -> None:
    from ._mp3 import decode

    try:
        data = base64.b64decode(zone.file, validate=True)
    except binascii.Error as exc:
        raise PresetError(f"zone file is not base64: {exc}") from None
    pcm, channels, rate = decode(data)
    write_wav(path, pcm, channels, rate)


# -- loading -----------------------------------------------------------------------------


class Soundfonts:
    """``gm_`` instruments, each preset fetched and each zone decoded on first use."""

    def __init__(
        self, cache: Path, fetch: bool = True, blocking: bool = False, url: str = SOUNDFONT_URL
    ) -> None:
        self.cache = cache
        self.fetch = fetch
        self.blocking = blocking  # load on first use, before returning (offline rendering)
        self.url = url
        self.presets: dict[str, list[Zone]] = {}
        self.failed: set[str] = set()  # presets that cannot load: malformed, or HTTP 4xx
        self._retry = Backoff()  # presets that failed to download, by preset
        self.max_tries = BLOCKING_TRIES if blocking else 1  # a render cannot wait for a backoff
        self.tries = self.max_tries  # 1 after a transient failure, until a download succeeds
        self.samples: dict[tuple[str, int], Sample] = {}
        self._pending: set[tuple[str, float]] = set()
        self._queue: queue.Queue[tuple[str, float]] = queue.Queue()
        self._lock = threading.Lock()
        self._loaded = threading.Condition(self._lock)  # notified after each load
        self._worker: threading.Thread | None = None

    def prefetch(self, keys: Iterable[tuple[str, int, float]], timeout: float) -> bool:
        """Load the zones for *keys* (instrument, preset index, midi), waiting up to *timeout*.

        Returns False if some were still loading at the timeout; they load on in the background.
        """
        keys = [k for k in set(keys) if k[0] in GM and self.get(*k) is None]
        deadline = time.monotonic() + timeout
        with self._loaded:
            while not all(self._settled(*k) for k in keys):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._loaded.wait(remaining)
        return True

    def _settled(self, name: str, index: int, midi: float) -> bool:  # with self._lock held
        fonts = GM[name]
        preset = fonts[index % len(fonts)]
        if preset in self.failed or self._retry.waiting(preset):
            return True  # cannot load, or not before its retry
        zones = self.presets.get(preset)
        if zones is None:
            return False
        i = find_zone(zones, midi)
        return i is None or (preset, i) in self.samples

    def get(self, name: str, index: int, midi: float) -> tuple[Sample, Zone] | None:
        """The decoded zone of instrument *name*, preset *index*, for *midi*, if ready."""
        fonts = GM.get(name)
        if not fonts:
            return None
        preset = fonts[index % len(fonts)]
        if self.blocking:
            self._load(preset, midi)
        with self._lock:
            zones = self.presets.get(preset)
            if zones is not None:
                i = find_zone(zones, midi)
                if i is None:
                    return None
                sample = self.samples.get((preset, i))
                if sample is not None:
                    return sample, zones[i]
            if preset in self.failed or self._retry.waiting(preset) or self.blocking:
                return None
            if (preset, midi) not in self._pending:
                self._pending.add((preset, midi))
                self._queue.put((preset, midi))
                self._start_worker()
        return None

    def _zone_path(self, preset: str, i: int) -> Path:
        return self.cache / preset / f"{i}.wav"

    def _load(self, preset: str, midi: float) -> None:
        """Fetch and parse *preset* if needed, then decode the zone for *midi*."""
        with self._lock:
            if preset in self.failed or self._retry.waiting(preset):
                return
            zones = self.presets.get(preset)
        try:
            if zones is None:
                zones = attempt(lambda: self._read_preset(preset), self.tries)
                with self._lock:
                    self.presets[preset] = zones
            i = find_zone(zones, midi)
            with self._lock:
                if i is None or (preset, i) in self.samples:
                    return
            path = self._zone_path(preset, i)
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                decode_zone(zones[i], path)
            sample = Sample(path, read_header(path))
            if sample.header is None or sample.header.frames == 0:
                raise PresetError(f"cannot read {path}")
            with self._lock:
                self.samples[(preset, i)] = sample
                self._retry.succeeded(preset)
        except ERRORS as exc:  # PresetError and the decoder's ValueError are not transient
            with self._lock:
                if transient(exc):
                    self.tries = 1  # the network may be down: later presets skip the waits
                    delay = self._retry.failed(preset)
                    logger.warning(
                        "cannot load soundfont %s: %s; retrying in %.0f s", preset, exc, delay
                    )
                else:
                    if isinstance(exc, urllib.error.HTTPError):
                        self.tries = self.max_tries  # the server answered
                    self.failed.add(preset)
                    logger.warning("cannot load soundfont %s: %s", preset, exc)

    def _read_preset(self, preset: str) -> list[Zone]:
        if not _PRESET_NAME.match(preset):
            raise PresetError(f"bad preset name {preset!r}")
        path = self.cache / f"{preset}.js"
        if path.exists():
            return parse_preset(path.read_text(encoding="utf-8"))
        if not self.fetch:
            raise PresetError("not in the cache, and fetching is off")
        with urllib.request.urlopen(f"{self.url}/{preset}.js", timeout=30) as r:
            data = r.read(MAX_PRESET_BYTES + 1)
        self.tries = self.max_tries  # the network is up
        if len(data) > MAX_PRESET_BYTES:
            raise PresetError(f"larger than {MAX_PRESET_BYTES} bytes")
        text = data.decode("utf-8")
        zones = parse_preset(text)  # cached only if it parses
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
        return zones

    def _start_worker(self) -> None:  # with self._lock held
        if self._worker is None:
            self._worker = threading.Thread(
                target=self._run, name="nanodirt-soundfonts", daemon=True
            )
            self._worker.start()

    def _run(self) -> None:
        while True:
            try:
                job = self._queue.get(timeout=5)
            except queue.Empty:
                with self._lock:  # a job queued after the timeout finds no worker and starts one
                    if self._queue.empty():
                        self._worker = None
                        return
                continue
            try:
                self._load(*job)
            finally:
                with self._lock:
                    self._pending.discard(job)
                    self._loaded.notify_all()


__all__ = ["GM", "SOUNDFONT_URL", "PresetError", "Soundfonts", "Zone", "find_zone", "parse_preset"]
