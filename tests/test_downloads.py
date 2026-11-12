"""Downloads of Strudel samples and soundfonts: retries, failures, and waiting for them."""

from __future__ import annotations

import http.client
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from soundfont_presets import FIXTURES, write_presets

from nanodirt.retry import Backoff, transient
from nanodirt.soundfonts import Soundfonts
from nanodirt.strudel import StrudelSamples


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x/", code, "error", None, None)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("exc", "want"),
    [
        (http_error(404), False),
        (http_error(403), False),
        (http_error(429), True),
        (http_error(503), True),
        (urllib.error.URLError(TimeoutError("timed out")), True),
        (ConnectionResetError(), True),
        (http.client.IncompleteRead(b""), True),
        (ValueError("cannot decode"), False),
    ],
)
def test_transient(exc: BaseException, want: bool) -> None:
    assert transient(exc) is want


def test_backoff_doubles_to_its_cap_and_clears_on_success() -> None:
    clock = Clock()
    b = Backoff(first=5, cap=12, clock=clock)
    assert not b.waiting("k")
    assert [b.failed("k") for _ in range(3)] == [5, 10, 12]
    assert b.waiting("k") and not b.waiting("other")
    clock.t = 12
    assert not b.waiting("k")
    b.succeeded("k")
    assert b.failed("k") == 5


class Flaky:
    """``urlopen`` that raises *errors* in turn, then opens the URL."""

    def __init__(self, *errors: BaseException) -> None:
        self.errors = list(errors)
        self.calls = 0
        self.urlopen = urllib.request.urlopen

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return self.urlopen(*args, **kwargs)


# -- soundfonts ------------------------------------------------------------------------


def test_a_soundfont_retries_a_network_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    served = write_presets(tmp_path / "served").as_uri()
    flaky = Flaky(urllib.error.URLError(TimeoutError("timed out")), http_error(503))
    monkeypatch.setattr(urllib.request, "urlopen", flaky)
    fonts = Soundfonts(tmp_path / "cache", blocking=True, url=served)
    fonts._retry = Backoff(clock=(clock := Clock()))
    assert fonts.get("gm_piano", 0, 48) is None
    assert fonts.get("gm_piano", 0, 48) is None  # waiting: not tried again
    assert flaky.calls == 1
    assert fonts.prefetch([("gm_piano", 0, 48)], timeout=0)  # settled until the retry
    clock.t = 5
    assert fonts.get("gm_piano", 0, 48) is None  # 503
    clock.t = 14
    assert fonts.get("gm_piano", 0, 48) is None  # 5 s, then 10 s
    clock.t = 15
    assert fonts.get("gm_piano", 0, 48) is not None
    assert flaky.calls == 3 and not fonts.failed


def test_a_missing_soundfont_is_not_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    flaky = Flaky(http_error(404))
    monkeypatch.setattr(urllib.request, "urlopen", flaky)
    fonts = Soundfonts(tmp_path / "cache", blocking=True, url="http://x")
    fonts._retry = Backoff(clock=(clock := Clock()))
    assert fonts.get("gm_piano", 0, 48) is None
    clock.t = 1000
    assert fonts.get("gm_piano", 0, 48) is None
    assert flaky.calls == 1 and fonts.failed == {"0000_JCLive_sf2_file"}


def test_an_incomplete_read_does_not_stop_the_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    served = write_presets(tmp_path / "served").as_uri()
    monkeypatch.setattr(urllib.request, "urlopen", Flaky(http.client.IncompleteRead(b"")))
    fonts = Soundfonts(tmp_path / "cache", url=served)
    assert fonts.prefetch([("gm_piano", 0, 48)], timeout=10)  # failed, to be retried
    assert fonts.prefetch([("gm_piano", 1, 60)], timeout=10)  # another preset: the worker lives
    assert fonts.get("gm_piano", 1, 60) is not None


# -- sample maps -----------------------------------------------------------------------


@pytest.fixture
def served(tmp_path: Path) -> str:
    d = tmp_path / "served"
    d.mkdir()
    (d / "tone.mp3").write_bytes((FIXTURES / "tone_mono.mp3").read_bytes())
    (d / "bad.mp3").write_bytes(b"not mp3")
    return d.as_uri() + "/"


def samples(tmp_path: Path, served: str, **kwargs: Any) -> StrudelSamples:
    remote = StrudelSamples(tmp_path / "cache", **kwargs)
    remote.load_map({"_base": served, "tone": ["tone.mp3"], "bad": ["bad.mp3"]})
    return remote


def test_a_sample_retries_a_network_error(
    tmp_path: Path, served: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    flaky = Flaky(ConnectionResetError())
    monkeypatch.setattr(urllib.request, "urlopen", flaky)
    remote = samples(tmp_path, served, blocking=True)
    remote._retry = Backoff(clock=(clock := Clock()))
    assert remote.get("tone", 0, None) is None
    assert remote.get("tone", 0, None) is None  # waiting
    clock.t = 5
    assert remote.get("tone", 0, None) is not None
    assert flaky.calls == 2


def test_an_undecodable_sample_is_downloaded_once(
    tmp_path: Path, served: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    flaky = Flaky()
    monkeypatch.setattr(urllib.request, "urlopen", flaky)
    remote = samples(tmp_path, served)
    assert remote.prepare([("bad", 0, None)], timeout=10)  # settled: it cannot load
    assert remote.get("bad", 0, None) is None
    assert remote.prepare([("bad", 0, None)], timeout=10)
    assert flaky.calls == 1 and remote.failed == {served + "bad.mp3"}


def test_prepare_waits_for_the_samples(tmp_path: Path, served: str) -> None:
    remote = samples(tmp_path, served)
    assert remote.get("tone", 0, None) is None  # not downloaded yet
    keys = [("tone", 0, 60.0), ("tone", 1, None), ("nonesuch", 0, None)]
    assert remote.prepare(keys, timeout=10)
    found = remote.get("tone", 0, None)
    assert found is not None and found[0].path.exists()


def test_prepare_gives_up_at_the_timeout(
    tmp_path: Path, served: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = threading.Event()
    urlopen = urllib.request.urlopen

    def slow(*args: Any, **kwargs: Any) -> Any:
        release.wait(10)
        return urlopen(*args, **kwargs)

    monkeypatch.setattr(urllib.request, "urlopen", slow)
    remote = samples(tmp_path, served)
    start = time.monotonic()
    assert not remote.prepare([("tone", 0, None)], timeout=0.2)
    assert 0.2 <= time.monotonic() - start < 2
    release.set()  # the download goes on in the background
    assert remote.prepare([("tone", 0, None)], timeout=10)
    assert remote.get("tone", 0, None) is not None


def test_dirt_prepares_samples_and_reads_their_buffers(tmp_path: Path, served: str) -> None:
    from oracle import FakeServer

    from nanodirt import dough
    from nanodirt.dirt import Dirt

    reads: list[str] = []
    server = FakeServer()
    server.read_buffer = lambda path: reads.append(path) or len(reads)  # type: ignore[attr-defined]
    dirt = Dirt(server, sample_rate=44100.0)
    dirt.remote = samples(tmp_path, served)
    dirt.remote.soundfonts = Soundfonts(
        tmp_path / "fonts", url=write_presets(tmp_path / "sf").as_uri()
    )
    events = [{"s": "tone"}, {"s": "tone", "n": 2}, {"s": "gm_piano", "note": 48}, {"s": "sine"}]
    assert dirt.prepare_strudel(events)
    assert sorted(Path(r).suffix for r in reads) == [".wav", ".wav"]  # the sample and the zone
    assert len(reads) == 2  # n 2 of a one-file bank is the same file
    v = dough.voice(events[0], 2, dirt._find_sample, dirt._find_font)
    assert v is not None and v is not dough.SILENT and v.sample is not None
    assert v.sample.buffer is not None
