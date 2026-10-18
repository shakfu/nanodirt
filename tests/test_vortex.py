"""nanodirt.vortex.boot as vortex's DSL module. Skipped unless vortex is installed:

uv run --with "git+https://codeberg.org/uzu/vortex" pytest tests/test_vortex.py
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("vortex")

from oracle import FakeServer  # noqa: E402
from parity import write_test_wav  # noqa: E402

from nanodirt.dirt import Dirt  # noqa: E402


def _original_boot():  # type: ignore[no-untyped-def]
    import vortex

    path = Path(vortex.__file__).parent / "boot.py"
    spec = importlib.util.spec_from_file_location("_vortex_boot_original", path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_defines_the_names_vortex_boot_defines() -> None:
    import vortex

    from nanodirt.vortex import boot

    original = _original_boot()
    own = {n for n in vars(original) if not n.startswith("__")} - set(dir(vortex))
    own -= {"SuperDirtStream", "LinkClock", "annotations"}  # replaced, not exposed API
    assert own <= set(vars(boot)), own - set(vars(boot))


def test_patterns_play_through_dirt(tmp_path: Path) -> None:
    from nanodirt.vortex import boot

    (tmp_path / "bd").mkdir()
    write_test_wav(tmp_path / "bd" / "bd.wav", 1)
    server = FakeServer()
    dirt = Dirt(server, sample_rate=44100.0, late_threshold=None)
    dirt.add_samples([tmp_path])
    dirt.start()
    server.sent.clear()
    boot.dirt = dirt
    already = {m for m in ("vortex.stream", "pyliblo3") if m in sys.modules}
    sys.modules["vortex.boot"] = boot
    from vortex import vortex_dsl

    with vortex_dsl() as mod:
        exec('d[1] = s("bd*4") >> speed("1 2")', vars(mod))
        time.sleep(2.2)
        exec("hush()", vars(mod))
    groups = [
        m for kind, msgs, _ in server.sent if kind == "bundle" for m in msgs if m[0] == "/g_new"
    ]
    assert len(groups) >= 4  # 120 bpm, 4 beats a cycle: bd*4 is 2 events per second
    # Playing imported neither vortex.stream nor pyliblo3.
    assert {m for m in ("vortex.stream", "pyliblo3") if m in sys.modules} <= already
