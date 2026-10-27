"""SuperDirt's event model on nanosynth (SuperDirt.sc, DirtOrbit.sc, DirtEvent.sc).

``Dirt.play(params, timestamp)`` takes one ``/dirt/play`` event as a dict and
sends the bundles SuperDirt would send for it. Front-end adapters convert their
transport into this call.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from nanosynth.osc import OscMessage
from nanosynth.synthdef import SynthDef

from .library import Sample, SoundLibrary

logger = logging.getLogger(__name__)

MIDDLE_C = 261.6255653005986
MAX_LATENCY = 42.0  # SuperDirt.maxLatency
NUM_ROUTING_BUSES = 16
NUM_CONTROL_BUSES = 128

# Vowel quark, counterTenor register: freqs (Hz), amps (dB), widths (Hz).
VOWELS: dict[str, tuple[list[float], list[float], list[float]]] = {
    "a": ([660, 1120, 2750, 3000, 3350], [0, -6, -23, -24, -38], [80, 90, 120, 130, 140]),
    "e": ([440, 1800, 2700, 3000, 3300], [0, -14, -18, -20, -20], [70, 80, 100, 120, 120]),
    "i": ([270, 1850, 2900, 3350, 3590], [0, -24, -24, -36, -36], [40, 90, 100, 120, 120]),
    "o": ([430, 820, 2700, 3000, 3300], [0, -10, -26, -22, -34], [40, 80, 100, 120, 120]),
    "u": ([370, 630, 2750, 3000, 3400], [0, -20, -23, -30, -34], [40, 60, 100, 120, 120]),
}


def midicps(note: float) -> float:
    return float(440.0 * 2.0 ** ((note - 69.0) / 12.0))


class Engine(Protocol):
    """What Dirt needs from a nanosynth ``Server``."""

    def send_bundle(self, contents: Any, timestamp: float | None = None) -> None: ...
    def next_node_id(self) -> int: ...


# -- Event environment --------------------------------------------------------


class Event:
    """An event's environment: received values, then the sound's, then the orbit's.

    Mirrors an sclang Event with a proto (the sound event) and a parent (the
    orbit's defaults). Callable values are evaluated with this event, as
    sclang's ``.value`` does.
    """

    def __init__(
        self,
        received: dict[str, Any],
        parent: Mapping[str, Any],
        proto: Mapping[str, Any] | None = None,
    ) -> None:
        self.own = received
        self.parent = parent
        self.proto: Mapping[str, Any] = proto or {}

    def raw(self, key: str) -> Any:
        if key in self.own:
            return self.own[key]
        if key in self.proto:
            return self.proto[key]
        return self.parent.get(key)

    def __getitem__(self, key: str) -> Any:
        value = self.raw(key)
        return value(self) if callable(value) else value

    def get(self, key: str, default: Any = None) -> Any:
        value = self[key]
        return default if value is None else value

    def __setitem__(self, key: str, value: Any) -> None:
        self.own[key] = value

    def __contains__(self, key: str) -> bool:
        return self.raw(key) is not None


def _midinote(e: Event) -> Any:
    # ~note ? ~n + (~octave * 12), evaluated left to right: (note ? n) + octave*12.
    # n defaults to the symbol \none, for which sclang's arithmetic yields \none.
    base = e["note"] if e["note"] is not None else e["n"]
    if not isinstance(base, (int, float)):
        return "none"
    return base + e["octave"] * 12


def _freq(e: Event) -> Any:
    note = e["midinote"]
    return midicps(note) if isinstance(note, (int, float)) else note


# -- Global effects (GlobalDirtEffect.sc) ---------------------------------------


@dataclass
class GlobalEffect:
    name: str
    param_names: list[str]
    always_run: bool = False
    active: bool = True
    state: dict[str, Any] = field(default_factory=dict)
    node_id: int | None = None

    def play_messages(
        self, num_channels: int, group: int, out_bus: int, dry_bus: int, effect_bus: int, orbit: int
    ) -> list[OscMessage]:
        args: list[Any] = [
            "outBus",
            out_bus,
            "dryBus",
            dry_bus,
            "effectBus",
            effect_bus,
            "orbitIndex",
            orbit,
        ]
        for k, v in self.state.items():
            if v is not None:
                args += [k, v]
        assert self.node_id is not None
        return [
            OscMessage("/s_new", f"{self.name}{num_channels}", self.node_id, 3, group, *args),
            OscMessage("/n_run", self.node_id, 0),
        ]

    def set(self, event: Event) -> list[OscMessage]:
        """Resume if the event names any parameter; set the ones that changed."""
        if not self.active or self.node_id is None:
            return []
        changed: list[Any] = []
        any_given = self.always_run
        for key in self.param_names:
            value = event[key]
            if value is not None:
                any_given = True
            if self.state.get(key) != value:
                changed += [key, value]
                self.state[key] = value
        msgs: list[OscMessage] = []
        if any_given:
            msgs.append(OscMessage("/n_run", self.node_id, 1))
            msgs.append(
                OscMessage("/n_set", self.node_id, "pauseImmediately", 0, "dirt_resumed", 1)
            )
        if changed:
            msgs.append(OscMessage("/n_set", self.node_id, *_pairs(changed)))
        return msgs


def default_global_effects() -> list[GlobalEffect]:
    return [
        GlobalEffect(
            "dirt_delay", ["delaytime", "delayfeedback", "delaySend", "delayAmp", "lock", "cps"]
        ),
        GlobalEffect("dirt_reverb", ["size", "room", "dry"]),
        GlobalEffect("dirt_leslie", ["leslie", "lrate", "lsize"]),
        GlobalEffect("dirt_rms", ["rmsReplyRate", "rmsPeakLag"], always_run=True, active=False),
        GlobalEffect("dirt_monitor", ["limitertype"], always_run=True),
    ]


def _flat(d: Mapping[str, Any]) -> list[Any]:
    return [x for kv in d.items() for x in kv]


def _pairs(args: list[Any]) -> list[Any]:
    """Name/value pairs as sclang encodes them: an absent (nil) value is sent as 0."""
    out: list[Any] = []
    for name, value in zip(args[::2], args[1::2]):
        out += [name, _osc_value(value)]
    return out


def _osc_value(value: Any) -> Any:
    # sclang's tagged OSC encoder sends nil and false as int 0, true as int 1
    # (OSCData.cpp, addMsgSlotWithTags). So "vowel" without "resonance" sends
    # resonance 0, not the SynthDef default.
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_osc_value(v) for v in value]
    return float(value)


# -- Orbits -------------------------------------------------------------------


@dataclass
class Orbit:
    index: int
    group: int
    synth_bus: int
    dry_bus: int
    effect_bus: int
    out_bus: int
    defaults: dict[str, Any]
    global_effects: list[GlobalEffect] = field(default_factory=default_global_effects)


def orbit_defaults(orbit: Orbit, num_channels: int) -> dict[str, Any]:
    """``DirtOrbit.makeDefaultParentEvent``."""
    return {
        "cps": 1.0,
        "offset": 0.0,
        "begin": 0.0,
        "end": 1.0,
        "speed": 1.0,
        "pan": 0.5,
        "gain": 1.0,
        "overgain": 0.0,
        "cut": 0.0,
        "unit": "r",
        "n": "none",
        "octave": 5,
        "midinote": _midinote,
        "freq": _freq,
        "dur": 1.0,
        "delta": lambda e: e["dur"],
        "latency": 0.0,
        "lag": 0.0,
        "length": 1.0,
        "loop": 1.0,
        "dry": 0.0,
        "lock": 0,
        "amp": 0.4,
        "fadeTime": 0.001,
        "out": orbit.synth_bus,
        "dryBus": orbit.dry_bus,
        "effectBus": orbit.effect_bus,
        "numChannels": num_channels,
    }


@dataclass
class Flotsam:
    """A live event group in a cut group (Flotsam.sc)."""

    cut_group: float
    orbit: int
    hash: int
    timestamp: float


Module = tuple[str, Callable[[Event], bool], Callable[["Dirt", Event, int], list[OscMessage]]]


# -- Dirt ---------------------------------------------------------------------


class Dirt:
    """SuperDirt on a nanosynth server: orbits, modules, samples and synths."""

    def __init__(
        self,
        server: Any,
        num_channels: int = 2,
        num_orbits: int = 12,
        out_buses: Iterable[int] | None = None,
        sample_rate: float = 48000.0,
        late_threshold: float | None = 0.05,
    ) -> None:
        self.server = server
        self.num_channels = num_channels
        self.sample_rate = sample_rate
        self.late_threshold = late_threshold
        self.late_dropped = 0
        self.library = SoundLibrary(num_channels, read_buffer=getattr(server, "read_buffer", None))
        self.remote: Any = None  # StrudelSamples, consulted after local banks
        self.synthdefs: dict[str, SynthDef] = {}
        from .effects import EXTRA_MODULES

        self.modules: list[Module] = CORE_MODULES + EXTRA_MODULES + LAST_MODULES
        self.flotsam: dict[int, Flotsam] = {}
        self._flotsam_lock = threading.Lock()
        self.dough_cuts: dict[int, int] = {}  # superdough cut group -> event group
        self.orbits: list[Orbit] = []
        self.group = 0
        self.routing_buses: list[int] = []
        self.control_buses: list[int] = []
        self._num_orbits = num_orbits
        self._out_buses = list(out_buses) if out_buses is not None else [0] * num_orbits

    # -- setup

    def add_synthdefs(self, defs: Iterable[SynthDef]) -> None:
        for d in defs:
            self.synthdefs[d.effective_name] = d

    def start(self) -> None:
        """Send SynthDefs, allocate buses and groups, and start the orbits."""
        from .effects import effect_synthdefs
        from .instruments import default_instruments
        from .synthdefs import core_synthdefs

        if not self.synthdefs:
            from .dough import synthdefs as dough_synthdefs

            self.add_synthdefs(core_synthdefs(self.num_channels))
            self.add_synthdefs(dough_synthdefs(self.num_channels))
            self.add_synthdefs(default_instruments(self.num_channels))
            self.add_synthdefs(effect_synthdefs(self.num_channels))
        for d in self.synthdefs.values():
            self.server.send_synthdef(d)
        n = self.num_channels
        self.routing_buses = [int(self.server.audio_bus(n)) for _ in range(NUM_ROUTING_BUSES)]
        self.control_buses = [int(self.server.control_bus(1)) for _ in range(NUM_CONTROL_BUSES)]
        self.group = self.server.next_node_id()
        for i, out_bus in enumerate(self._out_buses[: self._num_orbits]):
            orbit = Orbit(
                index=i,
                group=self.server.next_node_id(),
                synth_bus=int(self.server.audio_bus(n)),
                dry_bus=int(self.server.audio_bus(n)),
                effect_bus=int(self.server.audio_bus(n)),
                out_bus=out_bus,
                defaults={},
            )
            orbit.defaults = orbit_defaults(orbit, n)
            for effect in orbit.global_effects:
                effect.node_id = self.server.next_node_id()
            self.orbits.append(orbit)
            self.server.send_bundle(self._node_tree(orbit))
        if hasattr(self.server, "on_node"):
            self.server.on_node(self._on_node)

    def _node_tree(self, orbit: Orbit) -> list[OscMessage]:
        default_group = getattr(self.server, "default_group_id", 1)
        msgs = [
            OscMessage("/g_new", self.group, 0, default_group),
            OscMessage("/g_new", orbit.group, 0, self.group),
        ]
        for effect in reversed(orbit.global_effects):
            msgs += effect.play_messages(
                self.num_channels,
                orbit.group,
                orbit.out_bus,
                orbit.dry_bus,
                orbit.effect_bus,
                orbit.index,
            )
        return msgs

    def _on_node(self, event: Any) -> None:
        if event.action == "end":
            with self._flotsam_lock:
                self.flotsam.pop(event.node_id, None)
                for cut, group in list(self.dough_cuts.items()):
                    if group == event.node_id:
                        del self.dough_cuts[cut]

    def add_samples(self, paths: Iterable[str]) -> None:
        self.library.add_folders(paths)

    def set_control_bus(self, *pairs: float) -> None:
        for index, value in zip(pairs[::2], pairs[1::2]):
            i = int(index)
            if 0 <= i < len(self.control_buses):
                self.server.send_msg("/c_set", self.control_buses[i], float(value))

    # -- events

    def _schedule(self, timestamp: float | None) -> float | None:
        """The time to play an event stamped *timestamp*, or None if it is too late."""
        now = time.time()
        if timestamp is None:
            timestamp = now
        latency = timestamp - now
        if latency > MAX_LATENCY:
            logger.warning("scheduling delay too long; network clocks may not be in sync")
            timestamp = now + 0.2
        if self.late_threshold is not None and latency < -self.late_threshold:
            self.late_dropped += 1
            return None
        return timestamp

    def play_strudel(self, params: Mapping[str, Any], timestamp: float | None = None) -> bool:
        """Play a Strudel event as superdough does; sounds it lacks (``super*``) as SuperDirt."""
        from .dough import SILENT, voice
        from .strudel import normalize

        v = voice(params, self.num_channels, self._find_sample)
        if v is None:
            return self.play(normalize(dict(params)), timestamp)
        when = self._schedule(timestamp)
        if when is None or not self.orbits or v is SILENT:
            return False
        orbit = self.orbits[int(params.get("orbit", 0) or 0) % len(self.orbits)]
        # Resume the orbit's monitor, which sleeps when idle; the other global effects
        # (delay, reverb) keep their state: superdough voices do not send to them yet.
        e = Event(dict(params), orbit.defaults)
        msgs = [m for fx in orbit.global_effects if fx.always_run for m in fx.set(e)]
        group = self.server.next_node_id()
        if v.cut is not None:  # superdough's cut groups span all orbits
            with self._flotsam_lock:
                previous = self.dough_cuts.get(v.cut)
                self.dough_cuts[v.cut] = group
            if previous is not None:
                msgs.append(OscMessage("/n_set", previous, "cut_gate", 0))
        source = {"out": orbit.synth_bus, **v.controls}
        if v.sample is not None:
            source["buf"] = v.sample.buffer or 0
        gate = {"in": orbit.synth_bus, "out": orbit.dry_bus, **v.gate}
        msgs += [
            OscMessage("/g_new", group, 1, orbit.group),
            OscMessage("/s_new", v.source, -1, 0, group, *_pairs(_flat(source))),
            *[
                OscMessage(
                    "/s_new",
                    f"dough_{kind}{v.channels}",
                    -1,
                    1,
                    group,
                    *_pairs(_flat({"out": orbit.synth_bus, **c})),
                )  # fmt: skip
                for kind, c in v.filters
            ],
            OscMessage(
                "/s_new",
                f"dough_gate{v.channels}_{self.num_channels}",
                -1,
                1,
                group,
                *_pairs(_flat(gate)),
            ),
        ]
        self.server.send_bundle(msgs, when + v.nudge)
        return True

    def _find_sample(
        self, name: str, index: int, midi: float
    ) -> tuple[Sample, float | None] | None:
        """A Strudel sound: local banks first, then the Strudel sample maps."""
        sample = self.library.get(name, index)
        if sample is not None:
            return sample, None
        if self.remote is None:
            return None
        found = self.remote.get(name, index, midi)
        if found is None:
            return None
        self.library._ensure_loaded(found[0])
        return found if found[0].header is not None else None

    def play(self, params: Mapping[str, Any], timestamp: float | None = None) -> bool:
        """Play one event at *timestamp* (Unix seconds). Returns False if dropped."""
        when = self._schedule(timestamp)
        if when is None or not self.orbits:
            return False
        timestamp = when
        received = dict(params)
        index = int(received.get("orbit", 0) or 0)
        orbit = self.orbits[index % len(self.orbits)]
        received["timeStamp"] = timestamp
        return self._play_event(orbit, Event(received, orbit.defaults), timestamp)

    def _play_event(self, orbit: Orbit, e: Event, timestamp: float) -> bool:
        _split_name(e)
        # SuperDirt's "do nothing" is ~s == \, the empty symbol.
        if e["s"] == "":
            return False
        if not self._merge_sound_event(e):
            # SuperDirt only posts this; the event still plays, without a source.
            logger.info("no synth or sample named '%s' could be found.", e["s"])
        play = e.raw("play")  # a sound event with its own play function
        if play is not None:
            play(self, e)
            return True
        _order_time_span(e)
        _calc_time_span(e)
        if e["sustain"] < 8.0 / self.sample_rate:
            return False
        _finalise_parameters(e, self.num_channels)
        when = timestamp + e["lag"] + e["offset"] * abs(e["speed"])
        if e["cut"] != 0:
            cuts = self._cut_all_cuts(orbit, e)
            self.server.send_bundle([OscMessage("/error", -1), *cuts], when)
        msgs: list[OscMessage] = []
        for effect in orbit.global_effects:
            msgs += effect.set(e)
        group = self.server.next_node_id()
        msgs.append(OscMessage("/g_new", group, 1, orbit.group))
        if e["cut"] != 0:
            with self._flotsam_lock:
                self.flotsam[group] = Flotsam(abs(e["cut"]), orbit.index, e["hash"], e["timeStamp"])
        for _, test, module in self.modules:
            if test(e):
                msgs += module(self, e, group)
        msgs.append(self._gate_message(orbit, e, group))
        self.server.send_bundle(msgs, when)
        return True

    def _merge_sound_event(self, e: Event) -> bool:
        name = str(e["s"])
        sample = self.library.get(name, _number(e["n"]))
        if sample is not None:
            e.proto = self._sample_event(sample)
            return True
        if self.remote is not None:
            pitched = "note" in e.own or "midinote" in e.own
            midi = e["midinote"] if pitched else None
            found = self.remote.get(
                name, _number(e["n"]), midi if isinstance(midi, (int, float)) else None
            )
            if found is not None:
                remote_sample, root = found
                self.library._ensure_loaded(remote_sample)
                if remote_sample.header is not None:
                    e.proto = self._sample_event(remote_sample, root, midi)
                    return True
        if name in LIBRARY_SYNTHS:
            e.proto = LIBRARY_SYNTHS[name](self)
            return True
        if name in self.synthdefs:
            e.proto = {"instrument": name, "hash": hash(name) & 0x7FFFFFFF}
            return True
        return False

    def free_synths(self, orbit: Orbit) -> None:
        """``DirtOrbit.freeSynths``: free the orbit's group and rebuild its node tree."""
        for effect in orbit.global_effects:
            effect.node_id = self.server.next_node_id()
        self.server.send_bundle([OscMessage("/n_free", orbit.group), *self._node_tree(orbit)])

    def _sample_event(
        self, sample: Sample, root: float | None = None, midi: Any = None
    ) -> dict[str, Any]:
        header = sample.header
        assert header is not None
        tune: Any = _meta_tune_ratio
        if root is not None:
            # A pitched Strudel bank: play the nearest sample at the event's pitch
            # (superdough's getCommonSampleInfo), whatever SuperDirt's freq is.
            target = midi if isinstance(midi, (int, float)) else 36
            tune = lambda e: midicps(target) / midicps(root) * MIDDLE_C / e["freq"]  # noqa: E731
        return {
            "buffer": sample.buffer,
            "instrument": self.library.instrument(sample),
            "stretchInstrument": self.library.stretch_instrument(sample),
            "bufNumFrames": header.frames,
            "bufNumChannels": header.channels,
            "baseFreqToMetaFreqRatio": 1.0,
            "metaDataTuneRatio": tune,
            "unitDuration": lambda e: (
                header.duration * MIDDLE_C / (e["freq"] * e["metaDataTuneRatio"])
            ),
            "hash": sample.hash,
            "note": 0,
        }

    def _cut_all_cuts(self, orbit: Orbit, e: Event) -> list[OscMessage]:
        cut = abs(e["cut"])
        all_samples = e["cut"] > 0
        all_orbits = bool(e["cutAll"])
        msgs = []
        with self._flotsam_lock:
            for node_id, f in self.flotsam.items():
                if (
                    f.cut_group == cut
                    and (all_orbits or (f.orbit == orbit.index and e["timeStamp"] != f.timestamp))
                    and (all_samples or e["hash"] == f.hash)
                ):
                    msgs.append(OscMessage("/n_set", node_id, "cut_gate", 0))
        return msgs

    def _gate_message(self, orbit: Orbit, e: Event, group: int) -> OscMessage:
        args = [
            "in", orbit.synth_bus,
            "out", orbit.dry_bus,
            "amp", e["amp"],
            "gain", e["gain"],
            "overgain", e["overgain"],
            "sample", e["hash"],
            "cut", abs(e["cut"]),
            "sustain", e["sustain"],
            "fadeInTime", e["fadeInTime"],
            "fadeTime", e["fadeTime"],
        ]  # fmt: skip
        return OscMessage("/s_new", f"dirt_gate{self.num_channels}", -1, 1, group, *_pairs(args))

    def synth_messages(self, instrument: str, args: list[Any], group: int) -> list[OscMessage]:
        """``DirtEvent.sendSynth``: one ``/s_new`` per element of any array argument."""
        pairs = _pairs(args)
        values = pairs[1::2]
        width = max((len(v) for v in values if isinstance(v, list)), default=0)
        if width == 0:
            return [OscMessage("/s_new", instrument, -1, 1, group, *pairs)]
        msgs = []
        for k in range(width):
            flat: list[Any] = []
            for name, v in zip(pairs[::2], values):
                flat += [name, v[k % len(v)] if isinstance(v, list) else v]
            msgs.append(OscMessage("/s_new", instrument, -1, 1, group, *flat))
        return msgs

    def msg_func_args(self, instrument: str, e: Event) -> list[Any]:
        """``SynthDesc.msgFunc``: every control the event has a value for, in control order."""
        sd = self.synthdefs.get(instrument)
        if sd is None:
            return []
        names = [name for name, _ in sorted(sd.parameters.items(), key=lambda kv: kv[1][1])]
        args: list[Any] = []
        for name in names:
            if name == "gate":
                continue
            key = name[2:] if len(name) > 1 and name[1] == "_" else name
            value = e[key]
            if value is not None:
                args += [name, value]
        return args


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _meta_tune_ratio(e: Event) -> float:
    metatune = e["metatune"]
    if metatune is None:
        return 1.0
    ratio = e["baseFreqToMetaFreqRatio"]
    # linexp(0, 1, 1, ratio), clipped as sclang's SimpleNumber.linexp
    m = min(max(float(metatune), 0.0), 1.0)
    return float(ratio**m)


# -- DirtEvent steps ------------------------------------------------------------


def _split_name(e: Event) -> None:
    note = None
    if e["s"] is None:
        sound = str(e["sound"])
        sound, _, rest = sound.partition(":")
        note = rest or None
    else:
        sound = str(e["s"])
    if e["bank"] is not None:
        sound = f"{e['bank']}_{sound}"
    e["s"] = sound
    if note is not None:
        e["n"] = float(note)


def _order_time_span(e: Event) -> None:
    if e["speed"] < 0:
        e["begin"], e["end"] = e["end"], e["begin"]
    e["length"] = abs(e["end"] - e["begin"])


def _calc_time_span(e: Event) -> None:
    speed = e["speed"]
    loop = e["loop"]
    accelerate = e["accelerate"]
    e["freq"] = e["freq"]
    unit_duration = e["unitDuration"]
    use_unit = unit_duration is not None
    unit = e["unit"]
    if unit == "c":
        speed = speed * e["cps"] * (unit_duration if use_unit else 1.0)
    if accelerate is None:
        end_speed = speed
        avg_speed = abs(speed)
    else:
        end_speed = speed * (1.0 + accelerate)
        avg_speed = (abs(speed) + abs(end_speed)) * 0.5
    if use_unit:
        if unit == "rate":
            unit = e["unit"] = "r"
        if unit in ("r", "c"):
            unit_duration = unit_duration * e["length"] / avg_speed
        elif unit == "s":
            unit_duration = e["length"]
        else:
            raise ValueError(f"this unit ('{unit}') is not defined")
    sustain = e["sustain"]
    if sustain is None:
        delta = e["delta"]
        if e["legato"] is not None:
            sustain = delta * e["legato"]
        else:
            unit_duration = unit_duration if unit_duration is not None else delta
            if loop is not None:
                unit_duration = unit_duration * abs(loop)
            sustain = unit_duration
    if use_unit:
        sustain = min(unit_duration, sustain)
    fade = min(e["fadeTime"], sustain * 0.19098)
    e["fadeTime"] = fade
    e["fadeInTime"] = fade if e["begin"] != 0 else 0.0
    if e["timescale"] is not None:
        sustain = sustain * e["timescale"]
    e["sustain"] = sustain - (fade + e["fadeInTime"])
    e["speed"] = speed
    e["endSpeed"] = end_speed


def _finalise_parameters(e: Event, num_channels: int) -> None:
    if e["channel"] is not None:
        e["pan"] = e["pan"] + e["channel"] / num_channels
    e["pan"] = e["pan"] * 2 - 1
    e["delayAmp"] = e["delay"] if e["delay"] is not None else 0.0


# -- Modules (core-modules.scd, zzzzz-core-modules-that-come-last.scd) -----------


def _sound(dirt: Dirt, e: Event, group: int) -> list[OscMessage]:
    if e["buffer"] is not None:
        args = [
            "bufnum", e["buffer"],
            "sustain", e["sustain"],
            "speed", e["speed"],
            "freq", e["freq"] * e["metaDataTuneRatio"],
            "endSpeed", e["endSpeed"],
            "begin", e["begin"],
            "end", e["end"],
            "pan", e["pan"],
            "out", e["out"],
        ]  # fmt: skip
        if e["timescale"] is not None:
            instrument = e["stretchInstrument"]
            args += ["timescale", e["timescale"], "timescalewin", e.get("timescalewin", 1)]
        else:
            instrument = e["instrument"]
        return dirt.synth_messages(instrument, args, group)
    instrument = e["instrument"]
    if instrument is None:
        return []
    return dirt.synth_messages(instrument, dirt.msg_func_args(instrument, e), group)


def _effect(name: str, *keys: tuple[str, str]) -> Callable[[Dirt, Event, int], list[OscMessage]]:
    def module(dirt: Dirt, e: Event, group: int) -> list[OscMessage]:
        args: list[Any] = []
        for control, key in keys:
            args += [control, e[key]]
        args += ["out", e["out"]]
        return dirt.synth_messages(f"{name}{dirt.num_channels}", args, group)

    return module


def _vowel(dirt: Dirt, e: Event, group: int) -> list[OscMessage]:
    vowel = VOWELS.get(str(e["vowel"]))
    if vowel is None:
        return []
    freqs, dbs, widths = vowel
    args = [
        "out", e["out"],
        "vowelFreqs", [list(map(float, freqs))],
        "vowelAmps", [[10 ** (d / 20) for d in dbs]],
        "vowelRqs", [[1 / w for w in widths]],
        "resonance", e["resonance"],
    ]  # fmt: skip
    return dirt.synth_messages(f"dirt_vowel{dirt.num_channels}", args, group)


def _out_to(dirt: Dirt, e: Event, group: int) -> list[OscMessage]:
    bus = dirt.routing_buses[int(e["to"]) % len(dirt.routing_buses)]
    return dirt.synth_messages(f"dirt_out{dirt.num_channels}", ["to", bus, "out", e["out"]], group)


def _map_from(dirt: Dirt, e: Event, group: int) -> list[OscMessage]:
    buses = dirt.routing_buses
    bus = buses[0] if e["from"] is None else buses[int(e["from"]) % len(buses)]
    return [OscMessage("/n_mapan", group, str(e["toArg"]), bus, dirt.num_channels)]


# Sounds that default-synths.scd adds to the sound library (soundLibrary.addSynth).
LIBRARY_SYNTHS: dict[str, Callable[[Dirt], dict[str, Any]]] = {
    "from": lambda dirt: {
        "instrument": "dirt_from",
        "hash": hash("from") & 0x7FFFFFFF,
        "in": lambda e: dirt.routing_buses[int(e["from"] or 0) % len(dirt.routing_buses)],
    },
    "superpanic": lambda dirt: {
        "hash": hash("superpanic") & 0x7FFFFFFF,
        "play": lambda dirt_, e: [dirt_.free_synths(o) for o in dirt_.orbits],
    },
}


def _has(key: str) -> Callable[[Event], bool]:
    return lambda e: e[key] is not None


CORE_MODULES: list[Module] = [
    ("sound", lambda e: True, _sound),
    ("vowel", _has("vowel"), _vowel),
    ("shape", _has("shape"), _effect("dirt_shape", ("shape", "shape"))),
    (
        "hpf",
        _has("hcutoff"),
        _effect("dirt_hpf", ("hcutoff", "hcutoff"), ("hresonance", "hresonance")),
    ),
    ("bpf", _has("bandf"), _effect("dirt_bpf", ("bandqf", "bandf"), ("bandq", "bandq"))),
    ("crush", _has("crush"), _effect("dirt_crush", ("crush", "crush"))),
    (
        "coarse",
        lambda e: e["coarse"] is not None and e["coarse"] > 1,
        _effect("dirt_coarse", ("coarse", "coarse")),
    ),
    ("lpf", _has("cutoff"), _effect("dirt_lpf", ("cutoff", "cutoff"), ("resonance", "resonance"))),
    (
        "pshift",
        _has("psrate"),
        _effect(
            "dirt_pitch_shift", ("psrate", "psrate"), ("psdisp", "psdisp"), ("sustain", "sustain")
        ),
    ),
    (
        "envelope",
        lambda e: e["attack"] is not None or e["release"] is not None,
        _effect(
            "dirt_envelope",
            ("attack", "attack"),
            ("hold", "hold"),
            ("release", "release"),
            ("curve", "curve"),
        ),
    ),
    (
        "grenvelo",
        _has("tilt"),
        _effect(
            "dirt_grain_envelope",
            ("sustain", "sustain"),
            ("tilt", "tilt"),
            ("plat", "plat"),
            ("curve", "curve"),
        ),
    ),
    (
        "tremolo",
        _has("tremolorate"),
        _effect("dirt_tremolo", ("tremolorate", "tremolorate"), ("tremolodepth", "tremolodepth")),
    ),
    (
        "phaser",
        _has("phaserrate"),
        _effect("dirt_phaser", ("phaserrate", "phaserrate"), ("phaserdepth", "phaserdepth")),
    ),
]
# Loaded after the extra effects (zzzzz-core-modules-that-come-last.scd).
LAST_MODULES: list[Module] = [
    ("out_to", _has("to"), _out_to),
    ("map_from", _has("toArg"), _map_from),
]

__all__ = ["Dirt", "Event", "GlobalEffect", "Orbit", "VOWELS", "math"]
