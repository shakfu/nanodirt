# SuperDirt parameter mapping

Maps SuperDirt's event handling and core effects to nanosynth, as the reference for nanodirt. Read against SuperDirt `650a1d6` (2025-12-06). Files are in that repo:

- `classes/DirtEvent.sc`, `DirtOrbit.sc`, `DirtSoundLibrary.sc`, `GlobalDirtEffect.sc`, `SuperDirtUGens.sc`

- `synths/core-synths.scd`, `core-modules.scd`, `core-synths-global.scd`, `zzzzz-core-modules-that-come-last.scd`

The earlier classic-Dirt mapping (nanosynth `docs/dev/dirt-parameter-mapping.md`) is kept as a record. Where Dirt and SuperDirt differ, this document wins.

## Event pipeline

`DirtEvent.play`, in order:

1. **Defaults.** The event inherits the orbit's parent event (`DirtOrbit.makeDefaultParentEvent`): `cps 1`, `begin 0`, `end 1`, `speed 1`, `pan 0.5`, `gain 1`, `overgain 0`, `cut 0`, `unit r`, `octave 5`, `loop 1`, `dry 0`, `lock 0`, `amp 0.4`, `fadeTime 0.001`. Derived: `midinote = note ? n + octave*12`, `freq = midicps(midinote)`.

2. **`splitName`.** `s` may be `name:index`. With `bank`, `s` becomes `<bank>_<s>`. `s = "\"` (a backslash) means do nothing.

3. **`mergeSoundEvent`.** Looks up `s` and `n` in the sound library. A sample folder gives `wrapAt(n.asInteger)` over the sorted files. A sample event sets `note: 0`, so `n` selects the file and does not change pitch. If no folder exists and `s` is a SynthDef name, the event plays that synth, with `n` as the note.

4. **`orderTimeSpan`.** If `speed < 0`, swap `begin` and `end`. `length = abs(end - begin)`.

5. **`calcTimeSpan`.** Sets `sustain`, `speed`, `endSpeed`, `fadeTime` and `fadeInTime`:
   - With `unit c`: `speed *= cps`, and also by `unitDuration` when the event has one.
   - `endSpeed = speed * (1 + accelerate)`.
   - `unitDuration` for samples is `bufDuration * midicps(60) / (freq * metatune ratio)`. Then:
     - `unit r`: `unitDuration * length / avgSpeed`;
     - `unit c`: the same;
     - `unit s`: `length`.
   - `sustain` is the explicit `sustain`, else `delta * legato`, else `unitDuration` (or `delta`) times `abs(loop)`. For samples it is capped at `unitDuration`.
   - `fadeTime = min(fadeTime, sustain * 0.19098)`. `fadeInTime = fadeTime` if `begin != 0`, else 0.
   - `timescale` multiplies `sustain`. Finally `sustain -= fadeTime + fadeInTime`.
   - Events with `sustain < 8 / sampleRate` are dropped (`minSustain`).

6. **`finaliseParameters`.**
   - `pan += channel / numChannels`, then `pan = pan*2 - 1` (bipolar).
   - `latency += lag + offset * abs(speed)`.

7. **`playSynths`.** One bundle per event, at the event time, in this order:
   - `/n_set cut_gate 0` to cut-group victims, in a bundle of its own;
   - global-effect parameter updates;
   - `/g_new` for the event group;
   - one `/s_new` per active module, each `addToTail` of the event group;
   - `dirt_gate` last.

## Node and bus layout

Per orbit (`DirtOrbit.init`):

- `synthBus` (numChannels): each event's source and effects run on it in place, using `In` / `ReplaceOut`.

- `dryBus`: `dirt_gate` mixes the gated event into it.

- `globalEffectBus`: the delay, reverb and leslie outputs.

- One orbit group inside SuperDirt's group. The global effects are placed after it, in the order delay, reverb, leslie, rms, monitor (`initNodeTree`, `reverseDo` with `\addAfter`). `dirt_monitor` sums the dry and effect buses into the output.

Each orbit reuses one `synthBus` for all of its events. This works because each event's chain ends with `dirt_gate`, which reads the bus, writes to `dryBus`, then clears the bus with `ReplaceOut(Silent)`. Within an event group the nodes run in order: source, then effects, then gate. Event groups are added to the tail of the orbit group (`/g_new` action 1), so they all run before the global effects.

nanodirt keeps this layout. It needs no SynthDef codegen: every module is a fixed SynthDef.

## Fields

`p` = parameter as received. `N` = output channels (`numChannels`).

| Field(s) | SuperDirt | nanosynth mapping |
|-|-|-|
| `s`, `n`, `bank` | see pipeline steps 2-3 | Python lookup; `n` truncates toward zero (`asInteger`), then wraps. Negative `n` wraps from the end |
| `speed`, `accelerate` | `rate = Line.kr(speed, endSpeed, sustain) * freq/midicps(60)` | `Line.kr` x `freq / 261.6256` |
| `begin`, `end` | phase `LFSaw.ar(sawrate, 1)` ranged to `[begin, end] * frames`, signed by `speed` | `LFSaw` with `.range` written as `lo + (x+1)/2*(hi-lo)`; nanosynth has no `range` helper |
| `note`, `octave`, `midinote`, `freq` | pitch model in step 1; samples are transposed through `freq` | Python |
| `unit`, `cps`, `legato`, `sustain`, `loop`, `delta` | step 5 | Python |
| `timescale`, `timescalewin` | `dirt_stretchsample_*`, overlap-add with a Gaussian window | port as-is |
| `gain`, `overgain`, `amp` | `amp * (min(gain,2) + overgain)^4` in `dirt_gate` | same |
| `pan`, `channel`, `span`, `splay`, `panwidth`, `orientation` | `DirtPan`: N=1 sum; N=2 `Pan2` (mono) or `Balance2` (stereo), pan folded to [-1,1]; N>2 `DirtSplayAz` (`PanAz` per channel) | `PseudoUGen` replicating `DirtPan` |
| `cut` | `dirt_gate` carries `cut_gate`; same `abs(cut)` group in the same orbit gets `/n_set cut_gate 0`, 0.02 s cutoff env (`DirtGateCutGroup`). `cut > 0`: any sample; `cut < 0`: same sample only | Python keeps a table of live gates per cut group (SuperDirt's `flotsam`), freed on `/n_end` |
| `fadeTime`, `fadeInTime` | `dirt_gate` env `[0,1,1,0]`, times `[fadeIn, sustain, fade]`, `\sin` curve | `Envelope` with sine shape |
| `vowel`, `resonance` | `dirt_vowel`: 5 `BPF`s from the `Vowel` quark tables (counterTenor), `XFade2` with the dry signal | Vowel formant tables as data; same graph |
| `cutoff`, `resonance` | `dirt_lpf`: `RLPF(sig, clip(abs(cutoff), 20, SR/2), linexp(resonance, 0,1, 1,0.001))` | same |
| `hcutoff`, `hresonance` | `dirt_hpf`: `RHPF`, same mapping | same |
| `bandf`, `bandq` | `dirt_bpf`: `BPF(sig, clip(bandf,20,SR/2), 1/max(1,bandq)) * max(bandq,1)` | same |
| `crush` | `dirt_crush`: `sig.round(0.5^(crush-1))` | `round_` binary op |
| `coarse` | `dirt_coarse`, only if `coarse > 1`: `Latch(sig, Impulse(SR/coarse))` | same |
| `shape` | `dirt_shape`: `k = 2s/(1-s)`, `(1+k)x/(1+k|x|)`, `s < 1` | same |
| `attack`, `hold`, `release`, `curve` | `dirt_envelope` if `attack` or `release` is set: `Env.linen(attack, hold, release, 1, curve)` | `Envelope.linen`; check the curve encoding (`compatibility-position.md`: `linen` default curve) |
| `tilt`, `plat` | `dirt_grain_envelope` | same |
| `psrate`, `psdisp` | `dirt_pitch_shift`: `PitchShift`, window from sustain | same |
| `tremolorate`, `tremolodepth` | `dirt_tremolo` | same |
| `phaserrate`, `phaserdepth` | `dirt_phaser`: `AllpassL` modulated by `LFPar` | same |
| `to`, `from`, `toArg` | routing buses (16), `dirt_out` and `/n_mapan` | later phase |
| `delay`, `delaytime`, `delayfeedback`, `lock` | per-orbit `dirt_delay`. `SwitchDelay` when sc3-plugins is present, else `CombL` with decay from feedback | `SwitchDelay` (sc3-plugins in the wheel) |
| `room`, `size`, `dry` | per-orbit `dirt_reverb`: 4 allpasses into a `LocalIn` loop; `size` sets loop depth | same |
| `leslie`, `lrate`, `lsize` | per-orbit `dirt_leslie` | same |
| `lag`, `offset` | added to latency (step 6) | Python |
| `cps`, `cycle`, `delta`, `_id_`, `nudge` | `cps` and `delta` are used; the others are ignored | Python |

Global effects (`GlobalDirtEffect.set`):

- Each event writes its values for the effect's parameters into the effect's state, and the synth is set when values change.

- An event that omits a parameter leaves the last value in place. Values persist across events.

- Effects pause themselves after 4 s of silence (`DirtPause`), and resume when an event sets one of their parameters.

## Differences from classic Dirt that change sound

| | Dirt | SuperDirt |
|-|-|-|
| Level at `gain=1` | 0.0625 | 0.4 |
| `cutoff` without `resonance` | no effect | lowpass active |
| Filters | 4-pole ladder | `RLPF` / `RHPF`, 2-pole |
| Interpolation | linear | cubic (`BufRd` interpolation 4) |
| `crush` | truncation | rounding |
| Effect order | fixed chain | module order (`core-modules.scd`): vowel, shape, hpf, bpf, crush, coarse, lpf, pshift, envelope, grenvelo, tremolo, phaser |
| Delay | one global line | per orbit |
| Reverb, leslie | none | per orbit |

## nanosynth coverage

All UGens in the core SynthDefs have nanosynth classes, except `IEnvGen`, which only `DirtEnvGen` uses; no core SynthDef uses `DirtEnvGen`. SuperDirt's pseudo-UGens (`DirtPan`, `DirtSplayAz`, `DirtPanBalance2`, `DirtGateCutGroup`, `DirtPause`, `DirtFreqScale`) become nanodirt `PseudoUGen`s. sclang conveniences used in the SynthDefs (`linexp`, `linlin`, `range`, `minNyquist`) need small helpers.

## Strudel extensions

Strudel-only controls (superdough) are in scope (decided 2026-10-15). They are specified separately, against superdough, not here.
