"""WebSocket adapter: Strudel's OSC output (packages/osc/osc.mjs), without the Node bridge.

Each frame is JSON, as ``server.js`` reads it::

    {"address": "/dirt/play", "args": ["s", "bd", ...], "timestamp": <Unix ms>}
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

from .dirt import Dirt
from .strudel import normalize
from .udp import bind_error, pairs_to_dict

logger = logging.getLogger(__name__)

DEFAULT_PORT = 8080  # hardcoded in Strudel (osc.mjs)


class WebSocketAdapter:
    def __init__(self, dirt: Dirt, port: int = DEFAULT_PORT, host: str = "localhost") -> None:
        self.dirt = dirt
        self.port = port
        self.host = host
        self.connections = 0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Future[None] | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None

    def handle_frame(self, raw: str | bytes) -> None:
        data = json.loads(raw)
        if data.get("address") != "/dirt/play":
            return
        timestamp = data.get("timestamp")
        event = normalize(pairs_to_dict(list(data.get("args", []))))
        self.dirt.play(event, timestamp / 1000.0 if timestamp else None)

    async def _handler(self, ws: Any) -> None:
        self.connections += 1
        request = getattr(ws, "request", None)
        origin = request.headers.get("Origin") if request is not None else None
        logger.info("Strudel connected (origin %s)", origin)
        async for raw in ws:
            try:
                self.handle_frame(raw)
            except Exception:  # noqa: BLE001 -- one bad frame must not close the socket
                logger.exception("bad frame from Strudel")

    async def _serve(self) -> None:
        import websockets

        self._stop = asyncio.get_running_loop().create_future()
        try:
            server = await websockets.serve(self._handler, self.host, self.port)
        except OSError as exc:
            self._error = exc
            self._ready.set()
            return
        if self.port == 0:
            self.port = next(iter(server.sockets)).getsockname()[1]
        self._ready.set()
        async with server:
            await self._stop

    def start(self) -> None:
        def run() -> None:
            self._loop = asyncio.new_event_loop()
            self._loop.run_until_complete(self._serve())

        self._thread = threading.Thread(target=run, name="nanodirt-ws", daemon=True)
        self._thread.start()
        self._ready.wait(10)
        if self._error is not None:
            raise bind_error(
                self.port,
                "tcp",
                "Strudel needs this port; stop the other server or use the Node bridge.",
            ) from self._error

    def stop(self) -> None:
        if self._loop is not None and self._stop is not None and not self._stop.done():
            self._loop.call_soon_threadsafe(self._stop.set_result, None)
        if self._thread is not None:
            self._thread.join(5)
