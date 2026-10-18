"""The same pattern through SuperDirt and through nanodirt sounds the same.

Both run realtime on nanosynth's engine and are recorded; the recordings are
aligned at their first onset and compared as 10 ms RMS envelopes. Opt-in, as
tests/test_event_parity.py, plus an audio device.
"""

from __future__ import annotations

import socket
import time
import wave
from pathlib import Path
from typing import Any

import oracle
import pytest

pytestmark = pytest.mark.skipif(not oracle.available(), reason="set NANODIRT_TEST_ORACLE=1")
np = pytest.importorskip("numpy")

BANKS = ["bd", "sn", "hh", "arpy"]
STEP = 0.125
PATTERN: list[dict[str, Any]] = [
    {"s": "bd", "n": 0.0},
    {"s": "hh", "n": 1.0, "speed": 1.5, "gain": 0.9},
    {"s": "sn", "n": 2.0, "cutoff": 1200.0, "resonance": 0.3},
    {"s": "hh", "n": 0.0, "pan": 0.8},
    {"s": "arpy", "n": 3.0, "note": 5.0, "legato": 1.0, "vowel": "o", "resonance": 0.4},
    {"s": "bd", "n": 1.0, "shape": 0.5, "crush": 5.0},
    {"s": "sn", "n": 0.0, "hcutoff": 500.0, "speed": -1.0},
    {
        "s": "arpy",
        "n": 0.0,
        "accelerate": -0.5,
        "pan": 0.2,
        "tremolorate": 8.0,
        "tremolodepth": 0.6,
    },
]
TIMING = {"cps": 1.0, "delta": STEP, "orbit": 0}


def _send_pattern(port: int = 57120) -> None:
    from nanosynth.osc import OscBundle, OscMessage

    start = time.time() + 0.5
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for k, params in enumerate(PATTERN):
            args = [x for kv in {**params, **TIMING}.items() for x in kv]
            bundle = OscBundle(
                timestamp=start + k * STEP, contents=[OscMessage("/dirt/play", *args)]
            )
            sock.sendto(bundle.to_datagram(), ("127.0.0.1", port))


def _record(server: Any, path: Path) -> None:
    server.record(path, num_channels=2, sample_format="int16")
    time.sleep(0.2)
    _send_pattern()
    time.sleep(0.5 + len(PATTERN) * STEP + 1.0)
    server.stop_recording()


def _envelope(path: Path) -> Any:
    with wave.open(str(path)) as w:
        rate = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).reshape(-1, 2) / 32768
    onset = int(np.argmax(np.abs(x).max(axis=1) > 0.01))
    frame = rate // 100
    x = x[onset : onset + frame * 180]
    n = len(x) // frame
    return np.sqrt((x[: n * frame].reshape(n, frame, 2) ** 2).mean(axis=1))


def test_pattern_sounds_the_same(tmp_path: Path) -> None:
    from nanosynth import Server

    from nanodirt.dirt import Dirt
    from nanodirt.udp import UdpAdapter

    server = Server(oracle.superdirt_oracle.oracle_options())
    server.boot()
    try:
        with oracle.superdirt_oracle.superdirt(server, [str(oracle.SAMPLES / b) for b in BANKS]):
            time.sleep(0.5)
            _record(server, tmp_path / "superdirt.wav")
    finally:
        server.quit()

    server = Server(oracle.superdirt_oracle.oracle_options(client_id=None))
    server.boot()
    try:
        status = server.status()
        dirt = Dirt(server, sample_rate=status.nominal_sample_rate)
        for b in BANKS:
            dirt.library.add_folder(oracle.SAMPLES / b)
        dirt.start()
        server.sync()
        # Read the samples before playing, as SuperDirt did at startup.
        for bank in dirt.library.banks.values():
            for sample in bank:
                dirt.library._ensure_loaded(sample)
        server.sync()
        udp = UdpAdapter(dirt)
        udp.start()
        try:
            _record(server, tmp_path / "nanodirt.wav")
        finally:
            udp.stop()
    finally:
        server.quit()

    theirs, ours = _envelope(tmp_path / "superdirt.wav"), _envelope(tmp_path / "nanodirt.wav")
    n = min(len(theirs), len(ours))
    theirs, ours = theirs[:n], ours[:n]
    assert theirs.max() > 0.02
    corr = np.corrcoef(theirs.ravel(), ours.ravel())[0, 1]
    level = ours.sum() / theirs.sum()
    assert corr > 0.98, f"envelope correlation {corr:.4f}"
    assert 0.95 < level < 1.05, f"level ratio {level:.3f}"
