"""Compare nanodirt's bundles with stock SuperDirt's, event by event.

SuperDirt runs on nanosynth's engine through nanosynth's
``scripts/superdirt_oracle.py`` (headless sclang), which logs every message it
sends (``ORACLE_LOG_OSC``). nanodirt runs against a recording fake server.
Engine-specific numbers (node IDs, buses, buffers, hashes) are replaced by
their role before comparing.
"""

from __future__ import annotations

import ast
import itertools
import math
import os
import re
import socket
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

NANOSYNTH = Path(os.environ.get("NANOSYNTH_SRC", Path(__file__).resolve().parents[2] / "nanosynth"))
sys.path.insert(0, str(NANOSYNTH / "scripts"))

try:
    import superdirt_oracle  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    superdirt_oracle = None

SAMPLES = (
    Path(os.environ.get("NANODIRT_CACHE", Path.home() / ".cache" / "nanodirt")) / "Dirt-Samples"
)
COMMANDS = {12: "n_run", 15: "n_set", 9: "s_new", 21: "g_new"}
SPACING = 0.12  # seconds between events sent to SuperDirt


def available() -> bool:
    return (
        bool(os.environ.get("NANODIRT_TEST_ORACLE"))
        and superdirt_oracle is not None
        and superdirt_oracle.SCLANG.exists()
        and SAMPLES.is_dir()
    )


# -- Running SuperDirt ------------------------------------------------------------


def _parse_message(text: str) -> list[Any]:
    text = re.sub(r"Int8Array\[[^\]]*\]", "None", text)
    text = re.sub(r"\bnil\b", "None", text)
    text = text.replace("$[,", "[").replace(",$]", "]")  # OSC array markers
    text = re.sub(r"(?<![\w.])inf\b", "1e999", text)
    return list(ast.literal_eval("[" + text + "]"))


Sent = list[tuple[str, list[list[Any]], float | None]]  # kind, messages, latency
LEAD = 0.3  # how far ahead events are timestamped


def parse_log(path: Path) -> Sent:
    """Each logged send as (kind, messages, latency in seconds or None)."""
    out: Sent = []
    for line in path.read_text().splitlines():
        kind, latency, _time, rest = line.split(" ", 3)
        msgs = [_parse_message(m) for m in rest.split(" | ")]
        out.append((kind, msgs, None if latency == "nil" else float(latency)))
    return out


def run_superdirt(events: list[dict[str, Any]], banks: list[str]) -> Sent:
    from nanosynth import Server
    from nanosynth.osc import OscBundle, OscMessage

    log = Path(tempfile.mkdtemp()) / "osc.log"
    os.environ["ORACLE_LOG_OSC"] = str(log)
    server = Server(superdirt_oracle.oracle_options())
    server.boot()
    try:
        with superdirt_oracle.superdirt(server, [str(SAMPLES / b) for b in banks]):
            time.sleep(0.5)
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                for i, params in enumerate(events):
                    args = [x for kv in params.items() for x in kv]
                    msg = OscMessage("/dirt/play", *args)
                    when = time.time() + LEAD
                    sock.sendto(
                        OscBundle(timestamp=when, contents=[msg]).to_datagram(),
                        ("127.0.0.1", 57120),
                    )
                    time.sleep(SPACING)
            time.sleep(0.5)
    finally:
        os.environ.pop("ORACLE_LOG_OSC", None)
        server.quit()
    return [x for x in parse_log(log) if not (x[0] == "msg" and x[1][0][0] == "/status")]


# -- Running nanodirt -------------------------------------------------------------


class FakeServer:
    """Records bundles; allocates IDs the way nanosynth would."""

    default_group_id = 1

    def __init__(self) -> None:
        self.sent: Sent = []
        self.event_time = 0.0
        self._nodes = itertools.count(1000)
        self._audio = itertools.count(4)
        self._control = itertools.count(0)
        self._buffers = itertools.count(0)

    def send_bundle(self, contents: Any, timestamp: float | None = None) -> None:
        latency = None if timestamp is None else timestamp - self.event_time + LEAD
        self.sent.append(("bundle", [[m.address, *m.contents] for m in contents], latency))

    def send_msg(self, address: str, *args: Any) -> None:
        self.sent.append(("msg", [[address, *args]], None))

    def send_synthdef(self, synthdef: Any) -> None:
        pass

    def next_node_id(self) -> int:
        return next(self._nodes)

    def audio_bus(self, n: int = 1) -> int:
        base = next(self._audio)
        for _ in range(n - 1):
            next(self._audio)
        return base

    def control_bus(self, n: int = 1) -> int:
        return next(self._control)

    def read_buffer(self, path: str) -> int:
        return next(self._buffers)


def run_nanodirt(events: list[dict[str, Any]], banks: list[str]) -> Sent:
    from nanodirt.dirt import Dirt

    server = FakeServer()
    dirt = Dirt(server, sample_rate=44100.0, late_threshold=None)
    for b in banks:
        dirt.library.add_folder(SAMPLES / b)
    dirt.start()
    t0 = time.time() + 10
    for i, params in enumerate(events):
        server.event_time = t0 + i * SPACING
        dirt.play(params, server.event_time)
    return server.sent


# -- Normalizing ------------------------------------------------------------------


class Roles:
    """Map engine numbers to roles learned from the node tree and the gates."""

    def __init__(self) -> None:
        self.names: dict[int, str] = {}
        self.groups = itertools.count(1)

    def learn_tree(self, sent: Sent) -> None:
        orbit_of_group: dict[int, int] = {}
        for _, msgs, _ in sent:
            for m in msgs:
                cmd = _cmd(m[0])
                if (
                    cmd == "g_new"
                    and len(m) == 4
                    and m[2] == 0
                    and m[3] != 1
                    and m[3] in self.names
                ):
                    orbit = len(orbit_of_group)
                    orbit_of_group[m[1]] = orbit
                    self.names[m[1]] = f"OG{orbit}"
                elif cmd == "g_new" and len(m) == 4 and m[2] == 0 and m[3] == 1:
                    self.names.setdefault(m[1], "DIRT")
                elif cmd == "s_new" and m[3] == 3 and m[4] in orbit_of_group:
                    orbit = orbit_of_group[m[4]]
                    self.names[m[2]] = f"FX{orbit}:{str(m[1]).rstrip('0123456789')}"
                    args = dict(zip(m[5::2], m[6::2]))
                    self.names.setdefault(args["dryBus"], f"DRY{orbit}")
                    self.names.setdefault(args["effectBus"], f"EFX{orbit}")
                elif cmd == "s_new" and str(m[1]).startswith("dirt_gate"):
                    args = dict(zip(m[5::2], m[6::2]))
                    orbit = int(self.names.get(args["out"], "DRY?")[3:] or -1)
                    self.names.setdefault(args["in"], f"SYN{orbit}")

    def node(self, n: Any) -> Any:
        if n not in self.names:
            self.names[n] = f"G{next(self.groups)}"
        return self.names[n]


def _cmd(c: Any) -> str:
    return COMMANDS.get(c, c) if isinstance(c, int) else str(c).lstrip("/")


BUS_KEYS = {"out", "in", "to", "dryBus", "effectBus", "outBus"}


def normalize(sent: Sent) -> list[tuple[list[tuple[Any, ...]], float | None]]:
    """Event bundles with their latency, engine numbers replaced by roles."""
    roles = Roles()
    roles.learn_tree(sent)
    # An event sends a main bundle, which creates its group (add action 1),
    # and with a cut group first a bundle starting with /error.
    events = [
        (msgs, latency)
        for kind, msgs, latency in sent
        if kind == "bundle"
        and (_cmd(msgs[0][0]) == "error" or any(_cmd(m[0]) == "g_new" and m[2] == 1 for m in msgs))
    ]
    out = []
    for msgs, latency in events:
        norm = []
        for m in msgs:
            cmd = _cmd(m[0])
            args = list(m[1:])
            if cmd in ("n_run", "n_set") and args:
                args[0] = roles.node(args[0])
            elif cmd == "g_new":
                args = [roles.node(args[0]), args[1], roles.node(args[2])]
            elif cmd == "s_new":
                name, _id, action, target, *pairs = args
                pairs = list(pairs)
                for i in range(0, len(pairs) - 1, 2):
                    key = pairs[i]
                    if key in BUS_KEYS:
                        value = pairs[i + 1]
                        if value in roles.names:
                            pairs[i + 1] = roles.names[value]
                        else:
                            # 16 routing buses are allocated just before the orbits' buses.
                            orbit_buses = [
                                n for n, r in roles.names.items() if r[:3] in ("SYN", "DRY", "EFX")
                            ]
                            base = min(orbit_buses) - 16 * 2
                            pairs[i + 1] = f"ROUTE{(value - base) // 2}"
                    elif key in ("bufnum", "sample"):
                        pairs[i + 1] = key.upper()
                # scsynth ignores the order of control pairs; compare them sorted.
                ordered = sorted(zip(pairs[::2], pairs[1::2]), key=lambda kv: str(kv[0]))
                args = [str(name), action, roles.node(target), *(x for kv in ordered for x in kv)]
            # sclang's tagged OSC encoder sends nil as int 0.
            norm.append((cmd, *(0 if a is None else a for a in args)))
        out.append((norm, latency))
    return out


def same(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        if math.isinf(a) or math.isinf(b):
            return a == b
        return math.isclose(a, b, rel_tol=1e-5, abs_tol=1e-7)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return bool(a == b)
