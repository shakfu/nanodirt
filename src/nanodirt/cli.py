"""``nanodirt``: start the engine and the front-end adapters."""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

DEFAULT_SAMPLES = (
    Path(os.environ.get("NANODIRT_CACHE", Path.home() / ".cache" / "nanodirt")) / "Dirt-Samples"
)
PREPARE_TIMEOUT = 10.0  # seconds an evaluation waits for its sounds; the old pattern plays on


def engine_options(args: argparse.Namespace, inputs: int = 2):  # type: ignore[no-untyped-def]
    """Engine options as SuperDirt's startup file sets them.

    *inputs* opens the audio input device, which on macOS asks for microphone access.
    """
    from nanosynth.scsynth import Options

    return Options(
        port=args.engine_port,
        buffer_count=1024 * 256,
        memory_size=8192 * 32,
        maximum_node_count=1024 * 32,
        maximum_synthdef_count=4096,
        wire_buffer_count=2048,
        input_bus_channel_count=inputs,
        output_bus_channel_count=args.channels,
        verbosity=-1,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nanodirt", description=__doc__)
    p.add_argument("--port", type=int, default=57120, help="UDP port for /dirt/play (Tidal)")
    p.add_argument(
        "--engine-port", type=int, default=57110, help="engine UDP port (Tidal's /c_set)"
    )
    p.add_argument("--ws-port", type=int, default=8080, help="WebSocket port (Strudel)")
    p.add_argument("--no-ws", action="store_true", help="do not serve Strudel's WebSocket")
    p.add_argument("--strudel", action="store_true", help="apply Strudel's field rules on UDP too")
    p.add_argument("--samples", action="append", default=[], help="sample folder (repeatable)")
    p.add_argument("--orbits", type=int, default=12)
    p.add_argument("--channels", type=int, default=2)
    p.add_argument(
        "--inputs",
        type=int,
        help="audio input channels, for SuperDirt's `in` and `inr` (default 2; 0 for strudel)."
        " Opening input asks for microphone access on macOS",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command")
    sp = sub.add_parser("samples", help="manage samples")
    sp.add_argument("action", choices=["fetch"])
    sp.add_argument(
        "what",
        nargs="?",
        default="Dirt-Samples",
        help="Dirt-Samples (default), strudel (every default Strudel map), or a map: "
        "github:user/repo, shabda:..., a strudel.json URL or path",
    )
    p.add_argument(
        "--no-strudel-maps", action="store_true", help="do not load Strudel's default sample maps"
    )
    r = sub.add_parser("render", help="render a Strudel file to WAV, without a browser")
    r.add_argument("file", type=Path, help="Strudel code")
    r.add_argument("-o", "--output", type=Path, help="WAV file (default: FILE with .wav)")
    r.add_argument("--cycles", type=float, default=8.0)
    r.add_argument("--sample-rate", type=int, default=48000)
    r.add_argument("--tail", type=float, default=2.0, help="seconds after the last event")
    s = sub.add_parser("strudel", help="play a Strudel file, re-evaluated on each save")
    s.add_argument("file", type=Path, help="Strudel code")
    v = sub.add_parser("vortex", help="run vortex with nanodirt as its output, in one process")
    v.add_argument("--cli", action="store_true", help="vortex's REPL instead of its editor")
    return p


CACHE = DEFAULT_SAMPLES.parent


def is_map(spec: str) -> bool:
    return spec.startswith(("github:", "shabda:", "http://", "https://", "bubo:")) or spec.endswith(
        ".json"
    )


def fetch_samples(what: str = "Dirt-Samples") -> int:
    import subprocess

    if what != "Dirt-Samples":
        from .strudel import StrudelSamples

        remote = StrudelSamples(CACHE / "strudel")
        if what == "strudel":
            remote.load_defaults()
        else:
            remote.load_map_file(what)
        remote.prefetch()
        print(f"fetched {len(remote.banks)} banks into {CACHE / 'strudel'}")
        return 0
    if DEFAULT_SAMPLES.exists():
        print(f"exists: {DEFAULT_SAMPLES}")
        return 0
    DEFAULT_SAMPLES.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.call(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "https://github.com/tidalcycles/Dirt-Samples.git",
            str(DEFAULT_SAMPLES),
        ]
    )


def render_file(args: argparse.Namespace) -> int:
    from .headless import StrudelError, render

    out = args.output or args.file.with_suffix(".wav")
    local = [x for x in args.samples if not is_map(x)]
    if not local and DEFAULT_SAMPLES.exists():
        local = [str(DEFAULT_SAMPLES)]
    code = args.file.read_text(encoding="utf-8")
    maps = "".join(f"samples({json.dumps(m)});\n" for m in args.samples if is_map(m))
    try:
        n = render(
            maps + code,
            out,
            cycles=args.cycles,
            samples=local,
            strudel_maps=not args.no_strudel_maps,
            num_channels=args.channels,
            sample_rate=args.sample_rate,
            tail=args.tail,
        )
    except StrudelError as exc:
        print(f"nanodirt: {args.file}: {exc}", file=sys.stderr)
        return 1
    print(f"nanodirt: {n} events, {args.cycles:g} cycles in {out}")
    return 0


def play_strudel_file(args: argparse.Namespace) -> int:
    from .headless import Scheduler, Strudel, load_sample_map, play_file
    from .strudel import StrudelSamples

    if not args.file.exists():
        print(f"nanodirt: no such file: {args.file}", file=sys.stderr)
        return 1
    server, dirt = start(args, inputs=0)  # Strudel code reads no audio input
    if dirt.remote is None:
        dirt.remote = StrudelSamples(CACHE / "strudel")
    remote = dirt.remote

    def on_samples(spec: Any, base: str) -> None:
        # In the background: a download inside evaluate would hold up the scheduler's queries.
        def load() -> None:
            try:
                load_sample_map(remote, spec, base)
            except OSError as exc:
                print(f"nanodirt: cannot load samples {spec}: {exc}", file=sys.stderr)

        thread = threading.Thread(target=load, name="nanodirt-maps", daemon=True)
        thread.start()
        maps.append(thread)

    maps: list[threading.Thread] = []

    def prepare(events: list[dict[str, Any]]) -> None:
        deadline = time.monotonic() + PREPARE_TIMEOUT
        for thread in maps:  # the maps samples() loads name the banks to prepare
            thread.join(max(0.0, deadline - time.monotonic()))
        maps[:] = [t for t in maps if t.is_alive()]
        if maps or not dirt.prepare_strudel(events, max(0.0, deadline - time.monotonic())):
            print("nanodirt: sounds still loading; their first notes may be silent", flush=True)

    scheduler = Scheduler(
        lambda: Strudel(on_samples=on_samples), dirt.play_strudel, prepare=prepare
    )
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    print(f"nanodirt: playing {args.file}; save to re-evaluate, Ctrl-C to stop", flush=True)
    try:
        # flushed: an editor running this as a job reads the reports from a pipe
        play_file(args.file, scheduler, stop, lambda m: print(m, flush=True))
    finally:
        scheduler.stop()
        if dirt.late_dropped:
            print(f"nanodirt: dropped {dirt.late_dropped} late events")
        server.quit()
    return 0


def start(args: argparse.Namespace, inputs: int = 2):  # type: ignore[no-untyped-def]
    """Boot the engine and Dirt; return (server, dirt)."""
    from nanosynth.server import Server

    from .dirt import Dirt

    server = Server(engine_options(args, inputs if args.inputs is None else args.inputs))
    server.boot()
    rate = 48000.0
    try:
        status = server.status()
        rate = float(status.nominal_sample_rate or rate)
    except Exception:  # noqa: BLE001 -- the status query is a nicety
        pass
    dirt = Dirt(server, num_channels=args.channels, num_orbits=args.orbits, sample_rate=rate)
    local = [x for x in args.samples if not is_map(x)]
    maps = [x for x in args.samples if is_map(x)]
    if not local and DEFAULT_SAMPLES.exists():
        local = [str(DEFAULT_SAMPLES)]
    dirt.add_samples(local)
    if not args.no_strudel_maps or maps:
        from .strudel import StrudelSamples

        remote = StrudelSamples(CACHE / "strudel")
        dirt.remote = remote

        def load() -> None:
            if not args.no_strudel_maps:
                remote.load_defaults()
            for spec in maps:
                try:
                    remote.load_map_file(spec)
                except OSError as exc:
                    print(f"nanodirt: cannot load {spec}: {exc}", file=sys.stderr)

        threading.Thread(target=load, name="nanodirt-maps", daemon=True).start()
    dirt.start()
    server.sync()
    print(f"nanodirt: {len(dirt.library.banks)} sample banks, {len(dirt.synthdefs)} SynthDefs")
    return server, dirt


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s"
    )
    if args.command == "samples":
        return fetch_samples(args.what)
    if args.command == "render":
        return render_file(args)
    if args.command == "strudel":
        return play_strudel_file(args)
    if args.command == "vortex":
        from .vortex import run

        return run(args)

    from .udp import UdpAdapter

    server, dirt = start(args)
    adapters: list[Any] = []
    try:
        udp = UdpAdapter(dirt, port=args.port)
        udp.strudel = args.strudel
        udp.start()
        adapters.append(udp)
        print(f"nanodirt: /dirt/play on UDP {args.port}")
        if not args.no_ws:
            from .ws import WebSocketAdapter

            ws = WebSocketAdapter(dirt, port=args.ws_port)
            try:
                ws.start()
                adapters.append(ws)
                print(f"nanodirt: Strudel WebSocket on ws://localhost:{args.ws_port}")
            except OSError as exc:
                print(
                    f"nanodirt: {exc}\n  Strudel can still reach UDP {args.port} through `npx @strudel/osc`.",
                    file=sys.stderr,
                )
        stop = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        while not stop.wait(0.5):  # with a timeout: on Windows Ctrl-C may not end a bare wait
            pass
    except OSError as exc:
        print(f"nanodirt: {exc}", file=sys.stderr)
        return 1
    finally:
        for a in adapters:
            a.stop()
        if dirt.late_dropped:
            print(f"nanodirt: dropped {dirt.late_dropped} late events")
        server.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
