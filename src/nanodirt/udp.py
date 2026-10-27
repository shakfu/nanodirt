"""UDP adapter: SuperDirt's OSC API on port 57120 (SuperDirt.sc, ``connect``).

Tidal, Strudel's desktop app and its Node bridge, and vortex's
``SuperDirtStream`` all send here.
"""

from __future__ import annotations

import logging
import shutil
import socket
import subprocess
import threading
from collections.abc import Mapping
from typing import Any

from nanosynth.exceptions import OscError
from nanosynth.osc import BUNDLE_PREFIX, OscBundle, OscMessage

from .dirt import Dirt

logger = logging.getLogger(__name__)

DEFAULT_PORT = 57120


def port_owner(port: int, proto: str = "udp") -> str | None:
    """The process bound to *port*, if ``lsof`` can tell."""
    lsof = shutil.which("lsof")
    if lsof is None:
        return None
    flag = f"-i{proto.upper()}:{port}"
    try:
        out = subprocess.run(
            [lsof, "-nP", flag], capture_output=True, text=True, timeout=5
        ).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return None
    if len(out) < 2:
        return None
    fields = out[1].split()
    return f"{fields[0]} (pid {fields[1]})"


def bind_error(port: int, proto: str, hint: str) -> OSError:
    owner = port_owner(port, proto)
    who = f" by {owner}" if owner else ""
    return OSError(f"{proto.upper()} port {port} is in use{who}. {hint}")


def pairs_to_dict(args: tuple[Any, ...] | list[Any]) -> dict[str, Any]:
    """``/dirt/play`` arguments as a dict; arguments past the last pair are ignored."""
    return {str(k): v for k, v in zip(args[::2], args[1::2])}


class UdpAdapter:
    """Receive SuperDirt's OSC messages and pass events to a Dirt."""

    def __init__(
        self,
        dirt: Dirt,
        port: int = DEFAULT_PORT,
        host: str = "127.0.0.1",
        strudel: bool = False,
    ) -> None:
        self.dirt = dirt
        self.port = port
        self.host = host
        self.strudel = strudel  # events come from Strudel's OSC output
        self.sock: socket.socket | None = None
        self.reply_address: tuple[str, int] | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((self.host, self.port))
        except OSError as exc:
            sock.close()
            raise bind_error(
                self.port, "udp", "Is sclang or SuperDirt running? Use --port to choose another."
            ) from exc
        self.sock = sock
        self._running = True
        self._thread = threading.Thread(target=self._run, name="nanodirt-udp", daemon=True)
        self._thread.start()
        logger.info("listening for /dirt/play on UDP %s:%d", self.host, self.port)

    def stop(self) -> None:
        self._running = False
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def _run(self) -> None:
        assert self.sock is not None
        while self._running:
            try:
                data, address = self.sock.recvfrom(65536)
            except OSError:
                return
            try:
                self.handle(data, address)
            except Exception:  # noqa: BLE001 -- one bad packet must not stop the server
                logger.exception("error handling packet from %s", address)

    def handle(self, data: bytes, address: tuple[str, int]) -> None:
        try:
            if data.startswith(BUNDLE_PREFIX):
                self._bundle(OscBundle.from_datagram(data), address)
            else:
                self._message(OscMessage.from_datagram(data), None, address)
        except OscError:
            logger.debug("malformed OSC from %s", address)

    def _bundle(self, bundle: OscBundle, address: tuple[str, int]) -> None:
        for item in bundle.contents:
            if isinstance(item, OscBundle):
                self._bundle(item, address)
            else:
                self._message(item, bundle.timestamp, address)

    def _message(self, msg: OscMessage, timestamp: float | None, address: tuple[str, int]) -> None:
        path = msg.address
        args = msg.contents
        if path in ("/dirt/play", "/play2"):
            self.reply_address = address
            event = pairs_to_dict(args)
            if self.strudel:
                self.dirt.play_strudel(event, timestamp)
            else:
                self.dirt.play(event, timestamp)
        elif path == "/dirt/handshake":
            self.reply(address, "/dirt/handshake/reply", *self.handshake_reply_data())
        elif path == "/dirt/hello":
            self.reply_address = address
            self.reply(address, "/dirt/hello/reply")
        elif path in ("dirt/setControlBus", "/dirt/setControlBus"):
            self.dirt.set_control_bus(*(float(a) for a in args if isinstance(a, (int, float))))
        elif path == "/dirt/synth-info" and args:
            name = str(args[0])
            sd = self.dirt.synthdefs.get(name)
            controls = sorted(sd.parameters.items(), key=lambda kv: kv[1][1]) if sd else []
            self.reply(
                address, "/dirt/synth-info/reply", " ".join([name, *(n for n, _ in controls)])
            )

    def handshake_reply_data(self) -> list[Any]:
        options = getattr(self.dirt.server, "_options", None)
        engine_port = getattr(options, "port", 57110)
        return [
            "&serverHostname",
            "127.0.0.1",
            "&serverPort",
            engine_port,
            "&controlBusIndices",
            *self.dirt.control_buses,
        ]

    def reply(self, address: tuple[str, int], path: str, *args: Any) -> None:
        if self.sock is not None:
            self.sock.sendto(OscMessage(path, *args).to_datagram(), address)

    def hello(self, address: tuple[str, int]) -> None:
        """Ask a running Tidal to handshake again, e.g. after a restart."""
        self.reply(address, "/dirt/hello")


__all__ = ["DEFAULT_PORT", "Mapping", "UdpAdapter", "pairs_to_dict", "port_owner"]
