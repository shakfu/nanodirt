"""Dirt rendered offline: a nanosynth ``Score`` stands in for the realtime server."""

from __future__ import annotations

import itertools
import tempfile
from pathlib import Path
from typing import Any

from nanosynth.osc import OscMessage
from nanosynth.score import Score
from nanosynth.scsynth import Options
from nanosynth.synthdef import SynthDef

INPUT_CHANNELS = 2


class OfflineEngine:
    """The server API Dirt uses, recording into a Score at the engine's current time.

    Dirt stamps events with ``now`` plus their latency; set ``now`` to each event's
    time before playing it.
    """

    default_group_id = 1
    synchronous = True  # Dirt builds reverb IRs on the calling thread

    def __init__(self, num_channels: int = 2, sample_rate: int = 48000) -> None:
        self.num_channels = num_channels
        self.sample_rate = sample_rate
        self.options = Options(
            output_bus_channel_count=num_channels,
            input_bus_channel_count=INPUT_CHANNELS,
            buffer_count=1024 * 16,
            memory_size=8192 * 32,
            maximum_node_count=1024 * 32,
            wire_buffer_count=2048,
            verbosity=-1,
        )
        self.score = Score()
        self.now = 0.0
        self.end = 0.0  # the last bundle's time; a caller may extend it to the events' ends
        self._tmp = tempfile.TemporaryDirectory(prefix="nanodirt-offline-")
        self.workdir = Path(self._tmp.name)  # files the score reads when it renders
        self._nodes = itertools.count(1000)
        self._buffers = itertools.count(0)
        self._controls = itertools.count(0)
        self._audio = num_channels + INPUT_CHANNELS  # first private bus
        self.score.add(0.0, OscMessage("/g_new", self.default_group_id, 0, 0))

    # -- the server API

    def send_bundle(self, contents: Any, timestamp: float | None = None) -> None:
        when = self.now if timestamp is None else max(0.0, timestamp)
        self.end = max(self.end, when)
        self.score.add(when, list(contents))

    def send_msg(self, address: str, *args: Any) -> None:
        self.score.add(self.now, OscMessage(address, *args))

    def send_synthdef(self, synthdef: SynthDef) -> None:
        self.score.add_synthdef(0.0, synthdef)

    def sync(self) -> None:
        pass  # NRT runs asynchronous commands to completion in order

    def next_node_id(self) -> int:
        return next(self._nodes)

    def next_buffer_id(self) -> int:
        return next(self._buffers)

    def audio_bus(self, n: int = 1) -> int:
        bus = self._audio
        self._audio += n
        return bus

    def control_bus(self, n: int = 1) -> int:
        base = next(self._controls)
        for _ in range(n - 1):
            next(self._controls)
        return base

    def read_buffer(self, path: str) -> int:
        buffer = self.next_buffer_id()
        self.score.add(0.0, OscMessage("/b_allocRead", buffer, path, 0, -1))
        return buffer

    # -- rendering

    def render(self, path: str | Path, tail: float = 2.0) -> None:
        """Render to a float WAV file, *tail* seconds past ``end``."""
        self.score.add(self.end + tail, OscMessage("/c_set", 0, 0))
        self.score.render(
            path,
            sample_rate=self.sample_rate,
            sample_format="float",
            output_channels=self.num_channels,
            options=self.options,
        )

    def close(self) -> None:
        self._tmp.cleanup()
