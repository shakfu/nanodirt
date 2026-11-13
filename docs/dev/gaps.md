# Gaps

Where nanodirt plays differently from strudel.cc, or from Tidal with stock SuperDirt. As of 2026-11-12. Usage figures are repo-weighted shares from `strudel-usage.md`. Status and priorities are in nanosynth `TODO.md`, section "Dirt-protocol backend". Gaps in nanosynth itself: nanosynth `docs/dev/gaps.md`. rustel, a native Strudel port, names most of the Strudel gaps in its source: `rustel.md`.

## Strudel

Reference: superdough at Strudel `a3302cf` (`superdough-mapping.md`).

Both paths run: headless (`nanodirt strudel`, `nanodirt render`) and the browser over the WebSocket. Sample maps, oscillators, noise and `gm_` soundfonts play. What remains is breadth: individual sources and effects.

### Coverage

Units whose code touches no gap below, by `scripts/strudel_gap_coverage.py`:

| Corpus | Units | Touch no gap | Gaps hit (units) |
|-|-|-|-|
| Tunes (`tunes.mjs`, `testtunes.mjs`) | 53 | 47 (89%) | `noise` mix 3, `shape`/`crush`/`coarse` 3 |
| Teaching (workshop, recipes, understand, intro) | 242 | 232 (96%) | `vowel` 5, `wt_` 3, `shape`/`crush`/`coarse` 2 |

- In the GitHub sample (136 repos), ducking appears in 10% of repos and in no tune. The repo shares of the gaps sum to about 50%, an upper bound on repos touching one; their overlap was not measured.

- "Touches no gap" means no missing feature is used, not that the sound matches.

- The regexes read code as text; a control named at runtime is missed.

### Silent

| Gap | Use | Size |
|-|-|-|
| `wt_` wavetables | 3% (1-6%) | Medium: wavetable maps and position scanning |
| Maps loaded in browser code with `samples('github:...')` | Not measured | Needs the map URL, which only the browser knows, or a Strudel change |

`ws.py` accepts only `/dirt/play`, so a `samples(...)` call in the browser never reaches nanodirt; maps can only be added with `--samples`. Inferred from the code, not tested live. Headless Strudel loads them.

### Plays, but sounds different

| Gap | Use | Size |
|-|-|-|
| Ducking (`duckorbit`, `duckdepth`, `duckattack`) | 10% | Medium: a sidechain across orbits |
| `compressor` | 4% | Hard to match: Chrome's `DynamicsCompressorNode` has its own algorithm |
| `unison`; `supersaw` plays SuperDirt's synth | 4% | Medium |

Sizes are estimates. Under 3%:

- `pulse`, `n` partials on oscillators, the `noise` mix (medium, together).

- `shape`, `crush`, `coarse`, `vowel` (small each; superdough worklets).

- The ladder filter (`ftype` 1, plays as `12db`), filter LFOs, `djf`, `tremolo`, phaser (medium, together).

- `ir` (a sample as the reverb IR; small, reuses `PartConv`).

Known differences in features that play:

- After a soundfont zone that does not loop, superdough plays up to 38 ms of MP3 end padding; nanodirt stops at the encoded length (`soundfonts.md`).

- Past a sample's last frame, Chrome plays about 0.97 of the interpolation toward zero that nanodirt plays, measured at 2 points; the cause is unknown (`superdough-mapping.md`). One frame per sample that runs out.

- `triangle`, `square` and `sawtooth` are not band-limited as Chrome's are: they match by level (within 3%), not sample by sample. FM through a long chain (`fm2` 3 and up) amplifies each engine's float32 rounding: the phase drifts apart by 1-2 samples in 0.3 s.

- Reverb matches only statistically (tail energy within 35%, decay within 2 dB), because both implementations use random IRs.

### Connection and workflow

- Over the WebSocket, nothing loads ahead: the first note of each new sample or soundfont zone is silent. strudel.cc sends events about 0.1 s ahead, so loading on first request would still miss that note. Headless Strudel loads 4 cycles ahead at evaluation.

- Safari does not connect; the error was not captured.

- Firefox, the Strudel desktop app and the Node bridge are untested. The desktop app and bridge need `--strudel` (the `sustain` clash).

- Port 8080 is hardcoded in Strudel and often taken. Only an upstream change fixes it.

- Scope and analyser visuals read Web Audio data in the browser and cannot work in OSC mode. This holds for any SuperDirt setup (inference).

- A clock offset in Strudel's OSC mode is suspected, not verified (`integration.md`, "Possible clock offset").

- Headless, 7 of 46 tunes do not evaluate: they need Csound or Tone.js, out of scope (`strudel-headless.md`).

- Headless, a file is evaluated whole on save. Evaluating one block, as strudel.cc's editor does, needs an editor plugin (`strudel-headless.md`).

### Order

For strudel.cc users switching: ducking, against the oracle. Estimate: about 4 days (inference). Done 2026-11-12: `distort`; 2026-11-13: `vib`, `penv`, FM. For the teaching material alone: `vowel` and `wt_`.

## Tidal

Reference: SuperDirt `650a1d6` (`superdirt-parameter-mapping.md`). The event path and all 198 SuperDirt SynthDefs match stock SuperDirt (M1-M4, `status.md`).

- **Custom SynthDefs in sclang cannot run.** nanodirt has no sclang; each user SynthDef from `startup.scd` must be rewritten in the nanosynth DSL. Likely the largest gap for long-time users (inference, no usage data).

- **UGen libraries beyond sc3-plugins** (e.g. mi-UGens) are not shipped. sc3-plugins ship without HOA.

- **MIDI output** (`s "midi"` with SuperDirt `addMIDI` targets) is not implemented. nanosynth already has RtMidi.

- **Audio setup.** The CLI has `--channels`, `--orbits` and `--inputs`, but no device, sample rate or block size options; the system default device is always used.

- **Random SynthDef constants.** `superhammond` and `superfm` redraw constants on every SuperDirt load, so no fixed draw matches a given session.

- **`pmsin`** is not loaded: it reads the mouse through `MouseX`/`MouseY`, whose plugin (UIUGens) nanosynth does not ship. `s "pmsin"` plays nothing.

- **Smaller features:** the WAV `smpl` chunk (`metatune`), `dirt_rms` replies for editor visualisations, `/dirt/synth-info` beyond control names, `IEnvGen` (only the unused `DirtEnvGen` needs it).

- **Editors.** vim-tidal starts nanodirt itself (`g:tidal_sc_boot_cmd`). VSCode, Pulsar and Emacs start only ghci; nanodirt runs in a separate terminal.

## Summary

Strudel gaps are many controls, each used in a small share of repos; 87% of Strudel's tunes and 95% of its teaching snippets touch none. Tidal gaps are workflows around a core that matches. A Tidal user with stock sounds is nearly served; one with a custom `startup.scd` cannot switch without porting SynthDefs.
