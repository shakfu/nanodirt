"""Throwaway spike: one sample from Tidal, Strudel and vortex through nanosynth.

Not nanodirt. Checks transport, handshake, timing and browser access only.
Run: python spike.py [--record out.wav] [--samples DIR]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import socket
import threading
import time
from pathlib import Path
from typing import Any

from nanosynth import Options, Server
from nanosynth.envelopes import EnvGen, Envelope
from nanosynth.osc import OscBundle, OscMessage
from nanosynth.synthdef import DoneAction, SynthDefBuilder
from nanosynth.ugens import Balance2, BufRateScale, Out, Pan2, PlayBuf

log = logging.getLogger("spike")
SAMPLES = Path.home() / ".cache/nanodirt/Dirt-Samples"
EXTS = {".wav", ".aif", ".aiff", ".aifc"}


def build_defs() -> list[Any]:
    defs = []
    for nch in (1, 2):
        with SynthDefBuilder(
            bufnum=0.0, rate=1.0, sustain=1.0, amp=0.4, pan=0.0, start=0.0
        ) as b:
            sig = PlayBuf.ar(
                channel_count=nch,
                buffer_id=b["bufnum"],
                rate=b["rate"] * BufRateScale.kr(buffer_id=b["bufnum"]),
                start_position=b["start"],
            )
            env = EnvGen.ar(
                envelope=Envelope.linen(
                    attack_time=0.0, sustain_time=b["sustain"], release_time=0.002
                ),
                done_action=DoneAction.FREE_SYNTH,
            )
            if nch == 1:
                out = Pan2.ar(source=sig * env * b["amp"], position=b["pan"])
            else:
                out = Balance2.ar(
                    left=sig[0] * env * b["amp"],
                    right=sig[1] * env * b["amp"],
                    position=b["pan"],
                )
            Out.ar(bus=0, source=out)
        defs.append(b.build(name=f"spike_sample_{nch}"))
    return defs


class Spike:
    def __init__(self, server: Server, root: Path) -> None:
        self.server = server
        self.root = root
        self.buffers: dict[Path, tuple[int, int, float, int]] = {}
        self.lock = threading.Lock()
        self.control_buses = [int(server.control_bus()) for _ in range(128)]
        self.skews: dict[str, list[float]] = {}

    def files(self, name: str) -> list[Path]:
        d = self.root / name
        if not d.is_dir():
            return []
        return sorted(p for p in d.iterdir() if p.suffix.lower() in EXTS)

    def buffer(self, path: Path) -> tuple[int, int, float, int]:
        with self.lock:
            if path not in self.buffers:
                bufnum = self.server.read_buffer(str(path))
                self.server.sync()
                frames, chans, sr = self.server.query_buffer(bufnum)
                self.buffers[path] = (bufnum, chans, frames / sr, frames)
            return self.buffers[path]

    def play(self, params: dict[str, Any], when: float | None, source: str) -> None:
        now = time.time()
        if when is not None:
            self.skews.setdefault(source, []).append(when - now)
        bused = {k: v for k, v in params.items() if isinstance(v, str) and v[:1] == "c" and v[1:].isdigit()}
        if bused:
            log.info("%s: bus-mapped %s", source, bused)
        s = str(params.get("s", ""))
        if ":" in s:
            s, idx = s.split(":", 1)
            params.setdefault("n", float(idx))
        if params.get("bank"):
            s = f"{params['bank']}_{s}"
        files = self.files(s)
        if not files:
            log.info("%s: no sample folder %r", source, s)
            return
        path = files[int(float(params.get("n", 0))) % len(files)]
        bufnum, chans, dur, frames = self.buffer(path)
        speed = float(params.get("speed", 1.0))
        gain = float(params.get("gain", 1.0))
        amp = 0.4 * min(gain, 2.0) ** 4
        pan = float(params.get("pan", 0.5)) * 2 - 1
        sustain = dur / max(abs(speed), 1e-3)
        start = 0.0 if speed >= 0 else float(frames - 1)  # reverse from the end
        msg = OscMessage(
            "/s_new",
            f"spike_sample_{min(chans, 2)}",
            -1,
            0,
            1,
            "bufnum",
            bufnum,
            "rate",
            speed,
            "sustain",
            sustain,
            "amp",
            amp,
            "pan",
            pan,
            "start",
            start,
        )
        self.server.send_bundle([msg], timestamp=when)
        log.info(
            "%s: %s:%s at %+.3fs",
            source,
            s,
            path.name,
            (when - now) if when else 0.0,
        )

    def report(self) -> None:
        for src, xs in self.skews.items():
            xs = sorted(xs)
            log.info(
                "skew %s: n=%d min=%+.3f median=%+.3f max=%+.3f (positive = early)",
                src,
                len(xs),
                xs[0],
                xs[len(xs) // 2],
                xs[-1],
            )


def pairs(args: tuple[Any, ...]) -> dict[str, Any]:
    return {str(args[i]): args[i + 1] for i in range(0, len(args) - 1, 2)}


def udp_loop(spike: Spike, port: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", port))  # exclusive: fails if sclang holds the port
    log.info("udp listening on %d", port)
    while True:
        data, addr = sock.recvfrom(65536)
        try:
            if data.startswith(b"#bundle"):
                bundle = OscBundle.from_datagram(data)
                items = [(m, bundle.timestamp) for m in bundle.contents]
            else:
                items = [(OscMessage.from_datagram(data), None)]
            for msg, ts in items:
                if not isinstance(msg, OscMessage):
                    continue
                if msg.address == "/dirt/play":
                    spike.play(pairs(msg.contents), ts, f"udp:{addr[1]}")
                elif msg.address == "/dirt/handshake":
                    reply = OscMessage(
                        "/dirt/handshake/reply",
                        "&serverHostname",
                        "127.0.0.1",
                        "&serverPort",
                        57110,
                        "&controlBusIndices",
                        *spike.control_buses,
                    )
                    sock.sendto(reply.to_datagram(), addr)
                    log.info("handshake from %s", addr)
                elif msg.address == "/dirt/hello":
                    sock.sendto(OscMessage("/dirt/hello/reply").to_datagram(), addr)
                else:
                    log.info("udp: unhandled %s", msg.address)
        except Exception:
            log.exception("udp: bad packet from %s", addr)


async def ws_main(spike: Spike, port: int) -> None:
    import websockets

    async def handler(ws: Any) -> None:
        origin = ws.request.headers.get("Origin") if ws.request else None
        log.info("ws connected, origin=%s", origin)
        async for raw in ws:
            try:
                data = json.loads(raw)
                if data.get("address") != "/dirt/play":
                    continue
                ts = data.get("timestamp")
                spike.play(pairs(tuple(data["args"])), ts / 1000.0 if ts else None, "ws")
            except Exception:
                log.exception("ws: bad frame")

    async with websockets.serve(handler, "localhost", port):
        log.info("ws listening on %d", port)
        await asyncio.Future()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--samples", type=Path, default=SAMPLES)
    ap.add_argument("--record", type=Path)
    ap.add_argument("--udp-port", type=int, default=57120)
    ap.add_argument("--ws-port", type=int, default=8080)
    ap.add_argument("--seconds", type=float, default=0, help="0 = run until Ctrl-C")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    server = Server(Options(output_bus_channel_count=2, input_bus_channel_count=2))
    server.boot()
    for d in build_defs():
        server.send_synthdef(d)
    if a.record:
        server.record(a.record, num_channels=2)
    spike = Spike(server, a.samples)
    threading.Thread(target=udp_loop, args=(spike, a.udp_port), daemon=True).start()
    threading.Thread(
        target=lambda: asyncio.run(ws_main(spike, a.ws_port)), daemon=True
    ).start()
    try:
        if a.seconds:
            time.sleep(a.seconds)
        else:
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        spike.report()
        if a.record:
            server.stop_recording()
        server.quit()


if __name__ == "__main__":
    main()
