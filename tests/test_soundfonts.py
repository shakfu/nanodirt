"""``gm_`` soundfonts: MP3 decoding, preset parsing, loading, and the voice superdough plays."""

from __future__ import annotations

import logging
import math
import struct
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from soundfont_presets import FIXTURES, PRESETS, TONE_FRAMES, preset_text, write_presets, zone

from nanodirt import _mp3, dough
from nanodirt.library import Sample, read_header
from nanodirt.soundfonts import GM, PresetError, Soundfonts, Zone, find_zone, parse_preset


def frames(pcm: bytes, channels: int, channel: int = 0) -> list[float]:
    x = struct.unpack(f"{len(pcm) // 4}f", pcm)
    return list(x[channel::channels])


def crossings(x: list[float]) -> int:
    return sum(1 for a, b in zip(x, x[1:]) if a < 0 <= b)


# -- decoding --------------------------------------------------------------------------


@pytest.mark.parametrize("channels", [1, 2])
def test_decode_drops_encoder_delay_and_padding(channels: int) -> None:
    name = "tone_mono.mp3" if channels == 1 else "tone_stereo.mp3"
    pcm, got_channels, rate = _mp3.decode((FIXTURES / name).read_bytes())
    assert (got_channels, rate) == (channels, 44100)
    assert len(pcm) == TONE_FRAMES * channels * 4  # exactly the encoded frames
    for c, hz in list(enumerate((220, 330)))[:channels]:
        x = frames(pcm, channels, c)
        assert crossings(x) == pytest.approx(hz * TONE_FRAMES / 44100, abs=1)
        assert 0.6 < max(map(abs, x)) < 0.9  # 0.5 + 0.2 + 0.1 at most


@pytest.mark.parametrize("data", [b"", b"not mp3", b"\xff\xfb" + bytes(100), b"ID3\x04" + bytes(6)])
def test_decode_rejects_data_without_frames(data: bytes) -> None:
    with pytest.raises(ValueError):
        _mp3.decode(data)


def test_decode_rejects_a_non_bytes_argument() -> None:
    with pytest.raises(TypeError):
        _mp3.decode("ID3")  # type: ignore[arg-type]


def test_decode_runs_on_threads() -> None:
    data = (FIXTURES / "tone_stereo.mp3").read_bytes()
    want = _mp3.decode(data)
    got: list[Any] = []
    threads = [threading.Thread(target=lambda: got.append(_mp3.decode(data))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert got == [want] * 8


# -- presets ---------------------------------------------------------------------------


def test_parse_preset_reads_zones_as_data() -> None:
    zones = parse_preset(preset_text("x", PRESETS["0000_JCLive_sf2_file"]))
    assert len(zones) == 2
    z = zones[1]
    assert (z.key_low, z.key_high, z.original_pitch, z.coarse_tune, z.fine_tune) == (
        60, 127, 6400, 1, 30,
    )  # fmt: skip
    assert zones[0].loops and not z.loops


def test_parse_preset_tokens() -> None:
    text = """var _tone_x={ /* a block
    comment */ zones:[{midi:0,originalPitch:6.0e3,keyRangeLow:0,keyRangeHigh:127,
    loopStart:-0,loopEnd:.5,coarseTune:-1,fineTune:-2.5,sampleRate:44100,
    ahdsr:false,"file":'a//b+/==',extra:[1,{a:"x"}],}, ]};"""
    (z,) = parse_preset(text)
    assert z.file == "a//b+/=="  # // in a string is not a comment
    assert (z.original_pitch, z.loop_end, z.coarse_tune, z.fine_tune) == (6000, 0.5, -1, -2.5)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "var x = 1;",
        "var x={zones:[{file:'a'}]};",  # numbers missing
        "var x={zones:[]};",
        "var x={zones:[alert(1)]};",  # code, not data
        "var x={zones:[{file:'a', keyRangeLow: Infinity}]};",
        "var x={zones:[{file:'a'",  # truncated
        "var x={zones:[{file:'a\\'b'}]};",  # escapes are not in the format
    ],
)
def test_parse_preset_rejects(text: str) -> None:
    with pytest.raises(PresetError):
        parse_preset(text)


def test_parse_preset_rejects_a_bad_sample_rate() -> None:
    with pytest.raises(PresetError, match="sampleRate"):
        parse_preset(preset_text("x", [zone(0, 127, 6000, sampleRate=0)]))


def test_find_zone_includes_the_next_key() -> None:
    zones = parse_preset(preset_text("x", PRESETS["0000_JCLive_sf2_file"]))
    assert [find_zone(zones, m) for m in (0, 59, 59.5, 60, 60.5, 127, 128, 128.5)] == [
        0, 0, 0, 0, 1, 1, 1, None,
    ]  # fmt: skip


def test_playback_rate() -> None:
    z = Zone(0, 127, 6000, 0, 0, 0, 0, 44100, "")
    assert z.playback_rate(60) == 1.0
    assert z.playback_rate(72) == pytest.approx(2.0)
    tuned = Zone(0, 127, 6000, 1, 50, 0, 0, 44100, "")  # 150 cents sharp
    assert tuned.playback_rate(60) == pytest.approx(2 ** (150 / 1200))


def test_gm_map_from_strudel() -> None:
    assert len(GM) == 125
    assert GM["gm_piano"][:2] == ["0000_JCLive_sf2_file", "0000_FluidR3_GM_sf2_file"]


# -- loading ---------------------------------------------------------------------------


@pytest.fixture
def served(tmp_path: Path) -> str:
    return write_presets(tmp_path / "served").as_uri()


def test_blocking_load_decodes_one_zone(tmp_path: Path, served: str) -> None:
    fonts = Soundfonts(tmp_path / "cache", blocking=True, url=served)
    found = fonts.get("gm_piano", 0, 48)
    assert found is not None
    sample, z = found
    assert sample.header is not None and sample.header.frames == TONE_FRAMES
    assert sample.header.channels == 1 and z.loop_start == 4410
    assert fonts.get("gm_piano", 0, 50) == found  # same zone: the same Sample
    assert fonts.get("gm_piano", 0, 70)[0].header.channels == 2  # type: ignore[index,union-attr]
    assert sorted(p.name for p in (tmp_path / "cache" / "0000_JCLive_sf2_file").iterdir()) == [
        "0.wav", "1.wav",
    ]  # fmt: skip
    assert (tmp_path / "cache" / "0000_JCLive_sf2_file.js").exists()


def test_cached_presets_load_without_fetching(tmp_path: Path, served: str) -> None:
    Soundfonts(tmp_path / "cache", blocking=True, url=served).get("gm_piano", 0, 48)
    fonts = Soundfonts(tmp_path / "cache", fetch=False, blocking=True, url="http://invalid")
    assert fonts.get("gm_piano", 0, 48) is not None


def test_load_in_the_background(tmp_path: Path, served: str) -> None:
    fonts = Soundfonts(tmp_path / "cache", url=served)
    assert fonts.get("gm_piano", 1, 60) is None  # queued
    deadline = time.monotonic() + 10
    while (found := fonts.get("gm_piano", 1, 60)) is None:
        assert time.monotonic() < deadline, "not loaded"
        time.sleep(0.01)
    assert found[1].sample_rate == 22050


def test_prefetch_waits_for_the_zones(tmp_path: Path, served: str) -> None:
    fonts = Soundfonts(tmp_path / "cache", url=served)
    keys = [("gm_piano", 0, 48), ("gm_piano", 0, 70), ("gm_piano", 1, 60), ("gm_nonesuch", 0, 60)]
    assert fonts.prefetch(keys, timeout=10)
    assert all(fonts.get(*k) is not None for k in keys[:3])  # ready for the first note
    assert fonts.prefetch([("gm_piano", 0, 200)], timeout=0)  # no zone: nothing to wait for


def test_prefetch_gives_up_at_the_timeout(
    tmp_path: Path, served: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import urllib.request

    release = threading.Event()
    urlopen = urllib.request.urlopen

    def slow(*args: Any, **kwargs: Any) -> Any:
        release.wait(10)
        return urlopen(*args, **kwargs)

    monkeypatch.setattr(urllib.request, "urlopen", slow)
    fonts = Soundfonts(tmp_path / "cache", url=served)
    start = time.monotonic()
    assert not fonts.prefetch([("gm_piano", 0, 48)], timeout=0.2)
    assert 0.2 <= time.monotonic() - start < 2
    release.set()  # the load goes on in the background
    assert fonts.prefetch([("gm_piano", 0, 48)], timeout=10)


def test_a_failed_preset_ends_the_wait(tmp_path: Path) -> None:
    fonts = Soundfonts(tmp_path / "cache", url=(tmp_path / "nothing").as_uri())
    start = time.monotonic()
    assert fonts.prefetch([("gm_piano", 0, 48)], timeout=10)  # settled: it cannot load
    assert time.monotonic() - start < 5


def test_dirt_prepares_soundfonts_and_reads_their_buffers(tmp_path: Path, served: str) -> None:
    from oracle import FakeServer

    from nanodirt.dirt import Dirt
    from nanodirt.strudel import StrudelSamples

    reads: list[str] = []
    server = FakeServer()
    server.read_buffer = lambda path: reads.append(path) or len(reads)  # type: ignore[attr-defined]
    dirt = Dirt(server, sample_rate=44100.0)
    assert dirt.prepare_strudel([{"s": "gm_piano"}])  # no Strudel samples: nothing to do
    dirt.remote = StrudelSamples(tmp_path / "strudel", fetch=False)
    dirt.remote.soundfonts = Soundfonts(tmp_path / "fonts", url=served)
    events = [{"s": "gm_piano", "note": 48}, {"s": "gm_piano", "note": 50}, {"s": "bd"}]
    assert dirt.prepare_strudel(events)
    assert [Path(r).name for r in reads] == ["0.wav"]  # one zone, read before the first note
    v = dough.voice(events[0], 2, None, dirt._find_font)
    assert v is not None and v is not dough.SILENT and v.sample is not None
    assert v.sample.buffer == 1


def test_unknown_names_and_keys(tmp_path: Path, served: str) -> None:
    fonts = Soundfonts(tmp_path / "cache", blocking=True, url=served)
    assert fonts.get("gm_nonesuch", 0, 60) is None
    assert fonts.get("gm_piano", 0, 129) is None  # no zone holds the key


def test_a_bad_preset_is_not_cached_or_retried(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    served = tmp_path / "served"
    served.mkdir()
    (served / "0000_JCLive_sf2_file.js").write_text("var x={zones:[alert(1)]};")
    fonts = Soundfonts(tmp_path / "cache", blocking=True, url=served.as_uri())
    with caplog.at_level(logging.WARNING):
        assert fonts.get("gm_piano", 0, 60) is None
        assert fonts.get("gm_piano", 0, 62) is None
    (record,) = caplog.records  # one attempt
    assert record.message.startswith("cannot load soundfont 0000_JCLive_sf2_file: unexpected")
    assert not (tmp_path / "cache" / "0000_JCLive_sf2_file.js").exists()


def test_a_bad_zone_file_fails_the_preset(tmp_path: Path) -> None:
    served = tmp_path / "served"
    served.mkdir()
    text = preset_text("x", [zone(0, 127, 6000, file="bm90IG1wMw==")])  # "not mp3"
    (served / "0000_JCLive_sf2_file.js").write_text(text)
    fonts = Soundfonts(tmp_path / "cache", blocking=True, url=served.as_uri())
    assert fonts.get("gm_piano", 0, 60) is None
    assert "0000_JCLive_sf2_file" in fonts.failed


# -- the voice -------------------------------------------------------------------------


def font_finder(fonts: Soundfonts, seen: list[tuple[str, int, float]] | None = None) -> Any:
    def find(name: str, index: int, midi: float) -> tuple[Sample, Zone] | None:
        if seen is not None:
            seen.append((name, index, midi))
        return fonts.get(name, index, midi)

    return find


def test_font_voice_rules(tmp_path: Path, served: str) -> None:
    fonts = Soundfonts(tmp_path / "cache", blocking=True, url=served)
    seen: list[tuple[str, int, float]] = []
    v = dough.voice({"s": "gm_piano", "delta": 0.5}, 2, None, font_finder(fonts, seen))
    assert seen == [("gm_piano", 0, 48)]  # note c3 by default
    assert v is not None and v.source == "dough_sample1"
    assert v.controls["rate"] == pytest.approx(2 ** (-9 / 12))  # c3 from a3
    assert (v.controls["loop"], v.controls["loopStart"], v.controls["loopEnd"]) == (1, 4410, 8820)
    assert max(v.controls[f"l{i}"] for i in range(1, 5)) == pytest.approx(0.3)
    assert v.gate["life"] == pytest.approx(0.5 + 0.01 + 0.01)  # not cut at the buffer's end

    assert (
        dough.voice({"s": "gm_piano", "n": 2.5}, 2, None, font_finder(fonts, seen)) is dough.SILENT
    )
    assert seen[-1] == ("gm_piano", 3, 48)  # Math.round; that preset is not served
    v = dough.voice({"s": "gm_piano", "note": 64}, 2, None, font_finder(fonts))
    assert v is not None and v.channels == 2 and v.controls["loop"] == 0
    assert v.controls["rate"] == pytest.approx(2 ** ((6400 - 6400 + 100 + 30) / 1200))

    v = dough.voice({"s": "gm_piano", "n": 1, "freq": 440}, 2, None, font_finder(fonts))
    assert v is not None and v.controls["rate"] == pytest.approx(2 ** (12 / 12))  # a4 from a3
    assert (v.controls["loopStart"], v.controls["loopEnd"]) == (2000, 6000)  # 22.05 -> 44.1 kHz


def test_font_voice_is_silent_until_loaded(tmp_path: Path, served: str) -> None:
    fonts = Soundfonts(tmp_path / "cache", url=served)
    assert dough.voice({"s": "gm_piano"}, 2, None, font_finder(fonts)) is dough.SILENT
    assert dough.voice({"s": "gm_piano"}, 2) is None  # no soundfonts: SuperDirt's rules


def test_font_level_offline(tmp_path: Path, served: str) -> None:
    from test_dough import nrt, rms

    fonts = Soundfonts(tmp_path / "cache", blocking=True, url=served)
    # A held note loops after the 0.3 s buffer; one that does not loop ends with it.
    looped = nrt({"s": "gm_piano", "note": 57, "delta": 0.8}, 1.0, fonts=fonts)
    assert rms(looped, 0.5, 0.75) == pytest.approx(rms(looped, 0.05, 0.25), rel=0.05)
    pcm, _, _ = _mp3.decode((FIXTURES / "tone_mono.mp3").read_bytes())
    peak = 0.3 * 0.8 * max(map(abs, frames(pcm, 1)))  # level * gain * the tone's peak
    assert max(abs(f[0]) for f in looped) == pytest.approx(peak, rel=0.02)
    plain = nrt({"s": "gm_piano", "note": 76, "delta": 0.8}, 1.0, fonts=fonts)
    rate = 2 ** ((7600 - 6270) / 1200)
    assert rms(plain, 0.05, 0.25) > 0.01
    assert rms(plain, 0.3 / rate + 0.01, 0.8) == 0.0
    assert not math.isnan(rms(plain, 0.0, 1.0))
    assert read_header(fonts.get("gm_piano", 0, 76)[0].path).channels == 2  # type: ignore[index]
