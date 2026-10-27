"""Render Strudel events through superdough, as the reference for nanodirt's Strudel path.

usage: python scripts/superdough_oracle/superdough_oracle.py setup

`setup` clones Strudel at `REVISION` into the cache, installs the bundler there
and bundles superdough from source. `Superdough` then renders events in
headless Chromium through an `OfflineAudioContext`; it needs Playwright
(`uv run --with playwright ...`). Strudel is AGPL-3.0; nothing of it ships
with nanodirt. Method and findings: docs/dev/superdough-mapping.md,
"Verification".
"""

from __future__ import annotations

import base64
import functools
import http.server
import os
import shutil
import struct
import subprocess
import sys
import threading
from pathlib import Path
from types import TracebackType
from typing import Any

REVISION = "a3302cf"
REPO = "https://codeberg.org/uzu/strudel.git"
HERE = Path(__file__).resolve().parent
CACHE = Path(os.environ.get("NANODIRT_CACHE", Path.home() / ".cache" / "nanodirt")) / "superdough"
STRUDEL = CACHE / "strudel"
BUILD = CACHE / "build"
WWW = CACHE / "www"

Event = dict[str, Any]  # {"value": {...}, "t": s, "duration": s, "cps": .., "cycle": ..}


def setup() -> None:
    if not (STRUDEL / ".git").is_dir():
        subprocess.run(
            ["git", "clone", "--filter=blob:none", "--no-checkout", REPO, str(STRUDEL)], check=True
        )
    subprocess.run(["git", "-C", str(STRUDEL), "checkout", "-q", REVISION], check=True)
    BUILD.mkdir(parents=True, exist_ok=True)
    for f in ("package.json", "build.mjs"):
        shutil.copy(HERE / f, BUILD / f)
    subprocess.run(
        ["npm", "install", "--no-audit", "--no-fund", "--ignore-scripts"], cwd=BUILD, check=True
    )
    WWW.mkdir(parents=True, exist_ok=True)
    subprocess.run(["node", "build.mjs", str(STRUDEL), str(WWW)], cwd=BUILD, check=True)
    shutil.copy(HERE / "index.html", WWW / "index.html")
    print(f"superdough {REVISION} built in {WWW}")


def available() -> bool:
    return (WWW / "superdough.js").is_file()


def _chromium(pw: Any) -> Any:
    try:
        return pw.chromium.launch()
    except Exception:  # noqa: BLE001 -- this Playwright's browser build is not installed
        cache = Path.home() / "Library/Caches/ms-playwright"
        shells = sorted(
            cache.glob("chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell")
        )
        if not shells:
            raise
        return pw.chromium.launch(executable_path=str(shells[-1]))


class Superdough:
    """Headless superdough. Use as a context manager; `render` returns stereo frames."""

    def __enter__(self) -> Superdough:
        from playwright.sync_api import sync_playwright

        shutil.copy(HERE / "index.html", WWW / "index.html")  # keep the page in step with the repo

        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(WWW))
        handler.log_message = lambda *a: None  # type: ignore[attr-defined]
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        self._pw = sync_playwright().start()
        self._browser = _chromium(self._pw)
        self._page = self._browser.new_page()
        self._page.on("pageerror", lambda e: print("[superdough]", e, file=sys.stderr))
        self._page.goto(f"http://127.0.0.1:{self._httpd.server_port}/index.html")
        self._page.wait_for_function("window.ready === true")
        return self

    def add_samples(self, banks: dict[str, list[str]]) -> None:
        """Register files under ``WWW`` as superdough sample banks, by path relative to it."""
        self._page.evaluate("(m) => window.addSamples(m)", banks)

    def render(
        self, events: list[Event], seconds: float, rate: int = 48000, seed: int = 1
    ) -> list[tuple[float, float]]:
        b64 = self._page.evaluate(
            "([e, s, r, k]) => window.render(e, s, r, k)", [events, seconds, rate, seed]
        )
        raw = base64.b64decode(b64)
        x = struct.unpack(f"<{len(raw) // 4}f", raw)
        return list(zip(x[0::2], x[1::2]))

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._browser.close()
        self._pw.stop()
        self._httpd.shutdown()


if __name__ == "__main__":
    if sys.argv[1:] != ["setup"]:
        sys.exit(__doc__)
    setup()
