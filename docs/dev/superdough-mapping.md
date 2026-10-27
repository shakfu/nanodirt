# superdough mapping

Specifies how superdough turns a Strudel event into sound, as the reference for nanodirt's Strudel path. Read against Strudel `a3302cf`. Files are in `packages/superdough/` unless a path says otherwise.

Scope: decided 2026-10-27, Strudel events (WebSocket, or UDP with `--strudel`) follow superdough for all sources and controls; Tidal and vortex follow SuperDirt (`superdirt-parameter-mapping.md`). Priorities come from `strudel-usage.md`.

## What arrives

Strudel's OSC output sends `hap.value` plus `cps`, `cycle`, `delta` (`packages/osc/osc.mjs:35-56`):

- `delta = hap.duration / cps`. `hap.duration` already includes `clip` (`core/hap.mjs:35-46`). superdough's `duration` is the same value (`webaudio/webaudio.mjs:36`).

- `midinote` is added for SuperDirt and is ignored here: superdough computes pitch from `note`, `freq` and `octave`.

- `roomsize` is copied to `size`; superdough reads `roomsize`.

- `channels` arrives as a JSON string.

## Defaults

`defaultDefaultValues` (`superdough.mjs:185-207`): `s triangle`, `gain 0.8`, `postgain 1`, `velocity 1`, `release 0.01`, `delay 0`, `delayfeedback 0.5`, `delaysync 3/16` (cycles), `orbit 1`, `shapevol 1`, `distortvol 1`, `distorttype 0`, `phaserdepth 0.75`, `tremolodepth 1`, `density 0.03`.

The gain curve is the identity unless the user calls `setGainCurve`; it applies to `gain`, `velocity`, `postgain`, `delay`, `shapevol`, `distortvol`, `tremolodepth` (`superdough.mjs:66-86`).

## Voice chain

`superdough()` (`superdough.mjs:461-1050`), per event, in order:

1. Source (below), started at `t`, held to `t + duration`, released over `release`.

2. Gain: `gain * velocity`.

3. Lowpass if `cutoff` is set, highpass if `hcutoff`, bandpass if `bandf`.

4. `vowel`, `coarse`, `crush`, `shape`, `distort`, `tremolo`, `compressor`.

5. Pan, only if `pan` is set: `StereoPannerNode` at `2*pan - 1` (equal power).

6. Phaser, if `phaserrate` is set and `phaserdepth > 0`.

7. `FXrelease` fade, if longer than `release`.

8. `postgain`.

9. Sends to the orbit: delay with amount `delay`, reverb with amount `room`. The dry signal goes to the orbit output, scaled by `dry` if set.

An unknown `s` throws (`superdough.mjs:594`): the event is dropped, with no sound. `s` of `-`, `~` or `_` is a rest.

## Pitch

`getFrequencyFromValue` (`helpers.mjs:605-617`):

- `freq` wins if set. Else `note`, as a MIDI number or a name (`c3` is 48), default 36.

- The result is multiplied by `2^octave` (default 0). This differs from the OSC path, where `octave` is the default octave of a note name.

## Envelope

### Values

`getADSRValues([a, d, s, r], curve, defaults)` (`helpers.mjs:182-193`):

- All four unset: return `defaults`.

- Else `sustain = s` if set; else 1 if only `attack` is set or neither `attack` nor `decay`; else 0.001.

- Then `attack = max(a ?? 0, 0.001)`, `decay = max(d ?? 0, 0.001)`, `sustain = min(sustain, 1)`, `release = max(r ?? 0, 0.01)`.

So `.decay(0.2)` alone gives a decay-only envelope (sustain 0.001), and `.attack(0.1)` alone holds at full level.

### Shape

`getParamADSR(param, a, d, s, r, min, max, begin, end, curve)` (`helpers.mjs:55-114`), with `end = begin + duration`:

- Starts at `min`. Ramps to `max` over `attack`, then to `min + s*(max - min)` over `decay`, holds to `end`, then ramps to `min` over `release`.

- If the note ends during attack or decay, the ramp stops at the value the full envelope would have at `end`, and the release starts from there.

- Ramps are linear for amplitude, exponential for filter frequency (zero is replaced by 0.001).

The source stops at `end + release + 0.01`.

## Sources

### Oscillators

`sine`, `triangle`, `square`, `sawtooth`; aliases `sin`, `tri`, `sqr`, `saw` (`synth.mjs:23-82, 503-567`):

- A Web Audio `OscillatorNode`: band-limited, starting at phase 0. Web Audio normalizes the waveform so its peak, overshoot included, is 1. A sawtooth's RMS is therefore about 0.49, not 1/sqrt(3); measured in the oracle: sustain RMS 0.0703 against 0.0831 for an ideal saw at the same level.

- Fixed level 0.3, then the envelope (`min 0`, `max 1`, linear), defaults `[0.001, 0.05, 0.6, 0.01]`.

- `n` (or `partials`) other than for `sine`: a custom wave of that many harmonics, each `1/k` (saw, square odd only) or `1/k^2` (triangle, odd only). `phases` rotates them.

- `noise`: mixes in pink noise; `drywet` keeps the oscillator at full level until `noise > 0.5`.

- Pitch modulation: `vib`, `penv`, FM (below).

### `supersaw`

`synth.mjs:153-217`, worklet `worklets.mjs:466-577`:

- `unison` voices (default 5, clamp 1-100), each a polyBLEP saw with a random start phase.

- `detune` (default `n`, else 0.18) spreads the voices in semitones; `spread` (default 0.6) pans them, alternating left and right.

- Level `0.3 / sqrt(voices)`, envelope defaults as the oscillators.

### `pulse`

`synth.mjs:295-370`, worklet `worklets.mjs:751-850`:

- `pw` default 0.5. `pwrate` and `pwsweep` drive an LFO on the width (`pwsweep` default 0.3 when `pwrate` is set).

- No 0.3 level: it plays at full level, envelope defaults as the oscillators.

### Noise

`white`, `pink`, `brown`, `crackle` (`synth.mjs:407-452`, `noise.mjs`):

- A looped 2 s buffer from `Math.random`: brown is `(last + 0.02*w)/1.02`; pink is Paul Kellet's filter, times 0.11; crackle sets a sample to random with probability `density * 0.01`.

- Level 0.3, envelope defaults as the oscillators.

### Samples

`sampler.mjs:290-360`:

- Envelope defaults: `getADSRValues` with no defaults, so `[0.001, 0.001, 1, 0.01]` when none is set.

- Without `clip`, `loop` or `release`, the sample plays to the end of its slice; `duration` is ignored (`sampler.mjs:338-341`).

- Level 1.

### Pitch modulation

- `vib` (Hz) and `vibmod` (semitones, default 0.5): a sine on the oscillator's detune (`helpers.mjs:361-379`).

- `penv` (semitones, default 1 when any pitch envelope control is set), `pattack`, `pdecay`, `psustain`, `prelease`, `panchor` (default `psustain`), `pcurve` (0 linear, 1 exponential). Envelope defaults `[0.2, 0.001, 1, 0.001]`, on detune in cents (`helpers.mjs:341-359`).

- FM: `fmi` (index) and `fmh` (harmonicity ratio, default 1) add a modulator at `freq * fmh` with amplitude `fmi * freq * fmh` to the carrier frequency. `fmwave` (default sine), and `fmattack`... `fmrelease` with `fmenv` (`exp` default, or `lin`). Up to 8 operators, and a matrix `fmiIJ` (`helpers.mjs:429-507`).

## Gain

- Voice level: source level (0.3 for oscillators and noise, `0.3/sqrt(voices)` for `supersaw`, 1 for samples and `pulse`) times envelope times `gain * velocity` (default 0.8) times `postgain` (default 1).

- Linear. SuperDirt uses `0.4 * gain^4`.

## Filters

`createFilter` (`helpers.mjs:234-305`):

- `ftype` 0 (`12db`, default): one `BiquadFilterNode`. `ftype` 2 (`24db`): two in series. `ftype` 1 (`ladder`): a Moog-style worklet with `drive` (default 0.69) (`worklets.mjs:365-427`).

- `resonance` (`hresonance`, `bandq`) is the biquad Q, default 1. SuperDirt maps a 0-1 `resonance` to `rq` from 1 to 0.001.

- Envelope: active when any of `lpattack`, `lpdecay`, `lpsustain`, `lprelease`, `lpenv` is set (`hp*`, `bp*` likewise). `env` (octaves, default 1) and `fanchor` (default 0) give `min = 2^(-|env|*anchor) * f`, `max = 2^(|env| - |env|*anchor) * f`, both clamped to 20 kHz; negative `env` swaps them. Exponential, defaults `[0.005, 0.14, 0, 0.1]`.

- Filter LFO controls (`lprate`, `lpdepth`, ...) exist; none reaches 3% of repos.

## Effects

| Control | superdough | Source |
|-|-|-|
| `vowel` | Formant filter bank | `vowel.mjs` |
| `coarse` | Holds every `coarse`-th sample, counted within each 128-sample block | `worklets.mjs:193-223` |
| `crush` | `round(x * 2^(c-1)) / 2^(c-1)`, `c >= 1` | `worklets.mjs:225-257` |
| `shape` | `k = 2s/(1-s)`, `(1+k)x / (1+k|x|) * shapevol` | `worklets.mjs:259-295` |
| `distort` | One of 9 waveshapers (`distorttype`, default `scurve`) with drive `expm1(distort)`, times `distortvol` | `worklets.mjs:429-463`, `helpers.mjs:511-603` |
| `tremolo` | LFO on gain at `tremolo` Hz, or `cps * tremolosync` Hz; depth `tremolodepth` | `superdough.mjs:818-849` |
| `compressor` | `DynamicsCompressorNode`: threshold `compressor` (-3), ratio 10, knee 10, attack 0.005, release 0.05 | `helpers.mjs:163-176` |
| `phaserrate` | One notch filter at `phasercenter + 282` Hz, Q from `phaserdepth`, swept by an LFO of `phasersweep` cents | `superdough.mjs:352-372` |

## Orbit

`superdoughoutput.mjs`. One `Orbit` per `orbit` number, created on first use:

- **Delay:** one feedback delay per orbit. `delaytime` defaults to `delaysync / cps` seconds (3/16 cycle); `delayfeedback` default 0.5, clamped to 0.98. Send level `delay`. Time and feedback are set per event and apply to the whole orbit.

- **Reverb:** a convolver per orbit. The impulse response is generated noise: `1.5 * roomsize` seconds long, decaying by 60 dB at `roomsize` (default 2 s), faded in over `roomfade` (0.1 s), and lowpassed from `roomlp` (15 kHz) to `roomdim` (1 kHz) at `roomsize` (`reverb.mjs`, `reverbGen.mjs`). It is regenerated only when a parameter changes. Send level `room`. `ir` replaces the generated response with a sample.

- **Ducking:** `duckorbit` names target orbits. At the event time, each target's output gain ramps exponentially to `clamp(1 - sqrt(duckdepth), 0.01, current)` over `duckonset` (0), then back to 1 over `duckattack` (0.1 s, at least 0.002).

- **`djf`:** a DJ filter on the orbit output: below 0.49 a lowpass, above 0.51 a highpass, cutoff `(v*11)^4` with `v` the distance from 0.5 times 2 (`worklets.mjs:297-362`).

## Differences from SuperDirt that change sound

| Control | superdough | SuperDirt |
|-|-|-|
| Level | `0.8` default gain, linear; oscillators at 0.3 | `0.4 * gain^4` |
| `resonance` | Biquad Q, default 1; filter active whenever `cutoff` is set | 0-1, mapped to `rq` 1-0.001 |
| `attack`, `decay`, `sustain`, `release` | Per-voice ADSR; `sustain` is a level | `attack`/`release` module; `sustain` is a duration |
| `room`, `size` | Convolution with generated noise; `roomsize` in seconds | Allpass loop; `size` sets loop depth |
| `delay` | Feedback delay, default time 3/16 cycle | `SwitchDelay` |
| `coarse` | Hold counted per 128-sample block | `Latch` on an `Impulse` |
| `pan` | Applied only when set; equal power | Always; `Pan2`/`Balance2` |

## Verification

Strudel renders offline through an `OfflineAudioContext` (`renderPatternAudio`, `webaudio/webaudio.mjs:40-90`). The oracle spike (2026-10-27) does the same in headless Chromium:

- superdough is bundled from the pinned checkout with esbuild; the `?audioworklet` import becomes a separate module. npm's `superdough` 1.3.0 is older than `a3302cf` and is not used.

- A page renders a list of events with `superdough(value, t, duration, cps, cycle)` and returns stereo float32 samples to Python through Playwright.

- Checked: oscillators, `supersaw`, `pulse`, noise, lowpass with envelope, `shape`, orbit delay and reverb all render. Two renders of the same events are identical.

Running it:

```bash
python scripts/superdough_oracle/superdough_oracle.py setup      # clones Strudel, bundles superdough into ~/.cache/nanodirt/superdough
NANODIRT_TEST_SUPERDOUGH=1 uv run --with playwright pytest tests/test_superdough_oracle.py
```

Two fixes the oracle needs:

- Reverb impulse responses come from a nested offline render that finishes asynchronously. The page waits for every orbit's convolver buffer before rendering; without this, `room` is silent.

- `Math.random` is seeded in both scopes: the page (noise, reverb) and the AudioWorklet scope (`supersaw` phases), the latter at build time.

Comparison:

- Deterministic: oscillators (phase 0), `pulse`, envelopes, biquad filters, `shape`, `crush`, `coarse`. Compare sample by sample, within a tolerance for oscillator band-limiting: Web Audio uses wavetables, SC uses BLIT or polyBLEP.

- Random: `supersaw` phases, noise, the reverb impulse response. Their draws cannot match nanodirt's generator; compare by level and spectrum.

## Order of work

1. Oscillators and noise, with the envelope, gain and pitch rules above. Done 2026-10-27 (`nanodirt/dough.py`): sine matches superdough within 1e-3 sample by sample; `square` and `sawtooth` levels within 3% after scaling by 1.69; noise within 2-30% by type. Not yet: `n` (partials), `noise` mix, `vib`, `penv`, FM.

2. Gain, filters (12 dB biquad, envelope), pan, `postgain`, orbit delay and reverb, for all sources on the Strudel path. Gain, `pan`, `postgain` and samples done 2026-10-27: a voice is a group (source synth, then `dough_gate`), as in SuperDirt; samples, `pan` and envelopes match superdough within 0.3% of the peak, sample by sample. Not yet: filters, orbit delay and reverb, effects; `loop` is implemented but not checked against the oracle.

3. Controls by usage: filter envelope, FM, ducking, pitch envelope, `vib`, `compressor`, `supersaw` `unison`.

4. `gm_` soundfonts, after their data source and licence are checked.
