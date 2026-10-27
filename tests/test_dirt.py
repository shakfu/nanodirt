"""Event model and adapters against a recording fake server (no engine, no sclang)."""

from __future__ import annotations

import socket
import struct
import time
from pathlib import Path
from typing import Any

import pytest
from nanosynth.osc import OscBundle, OscMessage
from oracle import FakeServer
from parity import write_test_wav

from nanodirt.dirt import Dirt
from nanodirt.library import read_header
from nanodirt.udp import UdpAdapter, pairs_to_dict


@pytest.fixture()
def samples(tmp_path: Path) -> Path:
    for bank, channels in (("bd", 1), ("pad", 2)):
        (tmp_path / bank).mkdir()
        for i in range(3):
            write_test_wav(tmp_path / bank / f"{bank}{i}.wav", channels, seconds=0.2 + 0.1 * i)
    return tmp_path


@pytest.fixture()
def dirt(samples: Path) -> Dirt:
    d = Dirt(FakeServer(), sample_rate=44100.0, late_threshold=0.05)
    d.add_samples([samples])
    d.start()
    d.server.sent.clear()
    return d


def bundles(d: Dirt) -> list[list[list[Any]]]:
    return [m for kind, m, _ in d.server.sent if kind == "bundle"]


def s_new(bundle: list[list[Any]], name_prefix: str) -> dict[str, Any]:
    (m,) = [m for m in bundle if m[0] == "/s_new" and str(m[1]).startswith(name_prefix)]
    return dict(zip(m[5::2], m[6::2]))


class TestHeaders:
    def test_wav(self, tmp_path: Path) -> None:
        write_test_wav(tmp_path / "a.wav", 2, seconds=0.5)
        h = read_header(tmp_path / "a.wav")
        assert h is not None and (h.frames, h.channels, h.sample_rate) == (22050, 2, 44100.0)

    def test_aiff(self, tmp_path: Path) -> None:
        # COMM: channels 1, frames 1000, 16 bit, 48000 Hz as 80-bit extended.
        rate = bytes.fromhex("400EBB80000000000000")
        comm = struct.pack(">hIh", 1, 1000, 16) + rate
        body = b"AIFF" + b"COMM" + struct.pack(">I", len(comm)) + comm
        (tmp_path / "a.aif").write_bytes(b"FORM" + struct.pack(">I", len(body)) + body)
        h = read_header(tmp_path / "a.aif")
        assert h is not None and (h.frames, h.channels, h.sample_rate) == (1000, 1, 48000.0)

    def test_unreadable(self, tmp_path: Path) -> None:
        (tmp_path / "x.wav").write_bytes(b"nonsense")
        assert read_header(tmp_path / "x.wav") is None


class TestEvents:
    def test_sample_event(self, dirt: Dirt) -> None:
        assert dirt.play({"s": "bd", "n": 1.0, "cps": 0.5, "delta": 0.5}, time.time() + 0.2)
        (bundle,) = bundles(dirt)
        args = s_new(bundle, "dirt_sample_1_2")
        # unitDuration = file duration at middle C; minus the fade time.
        assert args["sustain"] == pytest.approx(0.3 - 0.001)
        assert args["pan"] == 0.0
        gate = s_new(bundle, "dirt_gate2")
        assert gate["amp"] == 0.4 and gate["sustain"] == args["sustain"]

    def test_stereo_sample_and_n_wraps(self, dirt: Dirt) -> None:
        dirt.play({"s": "pad", "n": 4.0}, time.time() + 0.2)
        assert s_new(bundles(dirt)[0], "dirt_sample_2_2")["sustain"] == pytest.approx(0.3 - 0.001)

    def test_name_colon_index(self, dirt: Dirt) -> None:
        dirt.play({"sound": "bd:2"}, time.time() + 0.2)
        assert s_new(bundles(dirt)[0], "dirt_sample_1_2")["sustain"] == pytest.approx(0.4 - 0.001)

    def test_empty_name_does_nothing(self, dirt: Dirt) -> None:
        assert not dirt.play({"s": ""}, time.time() + 0.2)
        assert bundles(dirt) == []

    def test_unknown_sound_still_gates(self, dirt: Dirt) -> None:
        dirt.play({"s": "nosuch", "delta": 0.5}, time.time() + 0.2)
        names = [m[1] for m in bundles(dirt)[0] if m[0] == "/s_new"]
        assert names == ["dirt_gate2"]

    def test_absent_value_is_sent_as_zero(self, dirt: Dirt) -> None:
        dirt.play({"s": "bd", "vowel": "a"}, time.time() + 0.2)
        assert s_new(bundles(dirt)[0], "dirt_vowel2")["resonance"] == 0

    def test_late_events_dropped(self, dirt: Dirt) -> None:
        assert not dirt.play({"s": "bd"}, time.time() - 1.0)
        assert dirt.late_dropped == 1

    def test_lag_and_offset_shift_the_bundle(self, dirt: Dirt) -> None:
        t = time.time() + 0.2
        dirt.play({"s": "bd", "lag": 0.01, "offset": 0.02, "speed": 2.0}, t)
        assert dirt.server.sent[-1][2] is not None

    def test_cut_group(self, dirt: Dirt) -> None:
        t = time.time() + 0.2
        dirt.play({"s": "bd", "cut": 1}, t)
        group = next(m[1] for m in bundles(dirt)[-1] if m[0] == "/g_new")
        dirt.play({"s": "bd", "n": 1.0, "cut": 1}, t + 0.1)
        cut_bundle = bundles(dirt)[-2]
        assert ["/n_set", group, "cut_gate", 0] in cut_bundle
        # A freed group leaves the cut table.
        dirt._on_node(type("E", (), {"action": "end", "node_id": group})())
        assert group not in dirt.flotsam

    def test_orbit_wraps(self, dirt: Dirt) -> None:
        dirt.play({"s": "bd", "orbit": 13}, time.time() + 0.2)
        g_new = next(m for m in bundles(dirt)[0] if m[0] == "/g_new")
        assert g_new[3] == dirt.orbits[1].group

    def test_global_effect_sends_only_changes(self, dirt: Dirt) -> None:
        t = time.time() + 0.2
        dirt.play({"s": "bd", "room": 0.3}, t)
        dirt.play({"s": "bd", "room": 0.3}, t + 0.1)
        reverb = dirt.orbits[0].global_effects[1].node_id
        sets = [m for m in bundles(dirt)[1] if m[0] == "/n_set" and m[1] == reverb and "room" in m]
        assert sets == []


class TestUdp:
    @pytest.fixture()
    def adapter(self, dirt: Dirt) -> Any:
        a = UdpAdapter(dirt, port=0)
        a.start()
        assert a.sock is not None
        a.port = a.sock.getsockname()[1]
        yield a
        a.stop()

    def _send(self, adapter: UdpAdapter, packet: bytes, reply: bool = False) -> Any:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(2)
            s.sendto(packet, ("127.0.0.1", adapter.port))
            if reply:
                return OscMessage.from_datagram(s.recvfrom(65536)[0])
        time.sleep(0.1)
        return None

    def test_handshake(self, adapter: UdpAdapter) -> None:
        reply = self._send(adapter, OscMessage("/dirt/handshake").to_datagram(), reply=True)
        assert reply.address == "/dirt/handshake/reply"
        args = list(reply.contents)
        assert args[:5] == [
            "&serverHostname",
            "127.0.0.1",
            "&serverPort",
            57110,
            "&controlBusIndices",
        ]
        assert len(args[5:]) == 128

    def test_hello(self, adapter: UdpAdapter) -> None:
        assert (
            self._send(adapter, OscMessage("/dirt/hello").to_datagram(), reply=True).address
            == "/dirt/hello/reply"
        )

    def test_play_bundle(self, adapter: UdpAdapter, dirt: Dirt) -> None:
        msg = OscMessage("/dirt/play", "s", "bd", "n", 0.0, "cps", 0.5, "delta", 0.5)
        self._send(adapter, OscBundle(timestamp=time.time() + 0.2, contents=[msg]).to_datagram())
        assert any(m[0] == "/g_new" for b in bundles(dirt) for m in b)

    def test_set_control_bus(self, adapter: UdpAdapter, dirt: Dirt) -> None:
        self._send(adapter, OscMessage("dirt/setControlBus", 3, 0.5).to_datagram())
        assert ("msg", [["/c_set", dirt.control_buses[3], 0.5]], None) in dirt.server.sent

    def test_bind_conflict_names_the_problem(self, adapter: UdpAdapter, dirt: Dirt) -> None:
        with pytest.raises(OSError, match=f"UDP port {adapter.port} is in use"):
            UdpAdapter(dirt, port=adapter.port).start()


def test_pairs_to_dict_ignores_odd_tail() -> None:
    assert pairs_to_dict(["s", "bd", "n"]) == {"s": "bd"}


class TestStrudel:
    def test_field_rules(self) -> None:
        from nanodirt.strudel import normalize

        e = normalize({"s": "bd", "sustain": 0.5, "clip": 0.8, "duration": 1, "channels": "[1,2]"})
        assert e == {"s": "bd", "strudelSustain": 0.5, "legato": 1.0}

    def test_note_names(self) -> None:
        from nanodirt.strudel import note_to_midi

        assert [note_to_midi(n) for n in ("c3", "A0", "Ds1", "fs2", "eb4", "c")] == [
            48,
            21,
            27,
            42,
            63,
            48,
        ]

    def test_map_and_alias(self, tmp_path: Path, samples: Path) -> None:
        from nanodirt.strudel import StrudelSamples

        remote = StrudelSamples(tmp_path, fetch=False)
        remote.load_map({"_base": "https://x/", "RolandTR909_bd": ["a.wav", "b.wav"]})
        remote.aliases = {"tr909": "RolandTR909"}
        url = "https://x/b.wav"
        path = remote._cache_path(url)
        path.parent.mkdir(parents=True)
        path.write_bytes((samples / "bd" / "bd0.wav").read_bytes())
        sample, root = remote.get("tr909_bd", 1, None)  # type: ignore[misc]
        assert sample.path == path and root is None
        assert remote.get("tr909_bd", 0, None) is None  # not downloaded

    def test_pitched_bank_picks_nearest_note(self, tmp_path: Path, samples: Path) -> None:
        from nanodirt.strudel import StrudelSamples

        remote = StrudelSamples(tmp_path, fetch=False)
        remote.load_map({"_base": "https://p/", "piano": {"C3": "c3.wav", "C4": "c4.wav"}})
        for name in ("c3.wav", "c4.wav"):
            p = remote._cache_path("https://p/" + name)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes((samples / "bd" / "bd0.wav").read_bytes())
        assert remote.get("piano", 0, 58.0)[1] == 60.0  # type: ignore[index]
        assert remote.get("piano", 0, 52.0)[1] == 48.0  # type: ignore[index]

    def test_pitched_bank_repitches(self, dirt: Dirt, tmp_path: Path, samples: Path) -> None:
        from nanodirt.strudel import StrudelSamples

        remote = StrudelSamples(tmp_path, fetch=False)
        remote.load_map({"_base": "https://p/", "piano": {"C4": "c4.wav"}})
        p = remote._cache_path("https://p/c4.wav")
        p.parent.mkdir(parents=True)
        p.write_bytes((samples / "bd" / "bd0.wav").read_bytes())
        dirt.remote = remote
        dirt.play({"s": "piano", "midinote": 72.0}, time.time() + 0.2)
        args = s_new(bundles(dirt)[0], "dirt_sample_1_2")
        assert args["freq"] == pytest.approx(261.6255653005986 * 2)  # an octave above the root

    def test_websocket_frames(self, dirt: Dirt) -> None:
        import json as _json

        from websockets.sync.client import connect

        from nanodirt.ws import WebSocketAdapter

        ws = WebSocketAdapter(dirt, port=0)
        ws.start()
        try:
            with connect(f"ws://localhost:{ws.port}") as c:
                frame = {
                    "address": "/dirt/play",
                    "args": ["s", "bd", "n", 1, "clip", 0.5, "delta", 0.2],
                    "timestamp": (time.time() + 0.2) * 1000,
                }
                c.send(_json.dumps(frame))
                time.sleep(0.2)
        finally:
            ws.stop()
        source = s_new(bundles(dirt)[0], "dough_sample1")
        assert source["t3"] == pytest.approx(0.2 - 0.002)  # clip: cut at delta, which includes it

    def test_superdough_voice_layout(self, dirt: Dirt) -> None:
        dirt.play_strudel(
            {"s": "sawtooth", "note": 57, "delta": 0.5, "orbit": 1}, time.time() + 0.2
        )
        (bundle,) = bundles(dirt)
        orbit = dirt.orbits[1]
        monitor = orbit.global_effects[-1].node_id
        assert ["/n_run", monitor, 1] in bundle  # the sleeping monitor is resumed
        g_new, source, gate = bundle[-3:]
        group = g_new[1]
        assert g_new == ["/g_new", group, 1, orbit.group]
        assert source[:5] == ["/s_new", "dough_sawtooth", -1, 0, group]
        assert gate[:5] == ["/s_new", "dough_gate1_2", -1, 1, group]
        src, gt = dict(zip(source[5::2], source[6::2])), dict(zip(gate[5::2], gate[6::2]))
        assert src["out"] == gt["in"] == orbit.synth_bus and gt["out"] == orbit.dry_bus
        assert src["freq"] == pytest.approx(220.0) and gt["level"] == pytest.approx(0.8)

    def test_strudel_filters_sit_between_source_and_gate(self, dirt: Dirt) -> None:
        dirt.play_strudel(
            {"s": "pad", "cutoff": 800, "hcutoff": 200, "delta": 0.2}, time.time() + 0.2
        )
        names = [m[1] for m in bundles(dirt)[0] if m[0] == "/s_new"]
        assert names == ["dough_sample2", "dough_lpf2", "dough_hpf2", "dough_gate2_2"]

    def test_strudel_orbit_delay(self, dirt: Dirt) -> None:
        dirt.play_strudel({"s": "sine", "delay": 0.5, "cps": 1.0, "delta": 0.2}, time.time() + 0.2)
        dirt.play_strudel(
            {"s": "sine", "delay": 0.3, "delaytime": 0.1, "delta": 0.2}, time.time() + 0.3
        )
        first, second = bundles(dirt)
        fx = dirt.dough_orbits[0]
        (create,) = [m for m in first if m[:2] == ["/s_new", "dough_delay"]]
        assert create[2:5] == [fx.delay_node, 3, dirt.orbits[0].group]  # after the voices
        assert dict(zip(create[5::2], create[6::2]))["delaytime"] == pytest.approx(3 / 16)
        (update,) = [m for m in second if m[0] == "/n_set" and m[1] == fx.delay_node]
        assert dict(zip(update[2::2], update[3::2]))["delaytime"] == pytest.approx(0.1)
        gate = s_new(second, "dough_gate")
        assert gate["delaySend"] == 0.3 and gate["delayBus"] == fx.delay_bus

    def test_strudel_sample_plays_its_slice(self, dirt: Dirt) -> None:
        dirt.play_strudel({"s": "bd", "n": 1, "delta": 0.05, "note": 48}, time.time() + 0.2)
        source = s_new(bundles(dirt)[0], "dough_sample1")
        assert source["rate"] == pytest.approx(2.0)  # note 48 is 12 semitones over 36
        assert source["t3"] == pytest.approx(0.3 / 2 - 0.002)  # whole slice, not delta

    def test_strudel_cut_group(self, dirt: Dirt) -> None:
        dirt.play_strudel({"s": "pad", "cut": 1, "delta": 0.2}, time.time() + 0.2)
        first = bundles(dirt)[0][-3][1]
        dirt.play_strudel({"s": "bd", "cut": 1, "delta": 0.2}, time.time() + 0.3)
        assert ["/n_set", first, "cut_gate", 0] in bundles(dirt)[1]

    def test_strudel_superdirt_synth_and_tidal_names(self, dirt: Dirt) -> None:
        dirt.play_strudel({"s": "superpiano", "delta": 0.2}, time.time() + 0.2)
        s_new(bundles(dirt)[0], "dirt_gate2")  # superdough has no superpiano: SuperDirt plays it
        dirt.server.sent.clear()
        dirt.play({"s": "sawtooth"}, time.time() + 0.2)  # Tidal: not a superdough source
        assert not [m for m in bundles(dirt)[0] if str(m[1]).startswith("dough_")]
