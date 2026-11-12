# Gaps

Where nanodirt plays differently from strudel.cc, or from Tidal with stock SuperDirt. As of 2026-11-11. Usage figures are repo-weighted shares from `strudel-usage.md`. Status and priorities are in nanosynth `TODO.md`, section "Dirt-protocol backend". Gaps in nanosynth itself: nanosynth `docs/dev/gaps.md`. rustel, a native Strudel port, names most of the Strudel gaps in its source: `rustel.md`.

## Strudel

Reference: superdough at Strudel `a3302cf` (`superdough-mapping.md`).

### Silent

| Gap | Use |
|-|-|
| `gm_` soundfonts | 28% (22-35%). Plan: `soundfonts.md` |
| `wt_` wavetables | 3% (1-6%) |
| Maps loaded in code with `samples('github:...')` | Not measured |

`ws.py` accepts only `/dirt/play`, so a `samples(...)` call in the browser never reaches nanodirt; maps can only be added with `--samples`. Inferred from the code, not tested live. Headless Strudel (`nanodirt render`) does load them.

### Plays, but sounds different

| Gap | Use |
|-|-|
| FM (`fmi`, `fmh`) | 12% |
| `distort` (shared control; may play with SuperDirt's semantics) | 12% |
| Ducking (`duckorbit`, `duckdepth`, `duckattack`) | 10% |
| `vib`, `penv` | 4% each |
| `compressor` | 4% |
| `unison`; `supersaw` plays SuperDirt's synth | 4% |

Under 3%:

- `pulse`, and `n` partials on oscillators.

- The `noise` mix.

- The ladder filter (`ftype` 1, plays as `12db`).

- Filter LFOs, `djf`.

- `shape`, `crush`, `coarse`, `vowel`.

- `tremolo`, phaser.

- `ir` (a sample as the reverb IR).

Not checked against the oracle: `loop`. Reverb matches only statistically (tail energy within 35%, decay within 2 dB), because both implementations use random IRs.

### Connection and workflow

- Safari does not connect; the error was not captured.

- Firefox, the Strudel desktop app and the Node bridge are untested. The desktop app and bridge need `--strudel` (the `sustain` clash).

- Port 8080 is hardcoded in Strudel and often taken. Only an upstream change fixes it.

- Scope and analyser visuals read Web Audio data in the browser and cannot work in OSC mode. This holds for any SuperDirt setup (inference).

- A clock offset in Strudel's OSC mode is suspected, not verified (`integration.md`, "Possible clock offset").

- Without a browser (`nanodirt strudel`), a file is evaluated whole on save. Evaluating one block, as strudel.cc's editor does, needs an editor plugin (`strudel-headless.md`).

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

Strudel gaps are many controls, each used in a small share of repos. Tidal gaps are workflows around a core that matches. A Tidal user with stock sounds is nearly served; one with a custom `startup.scd` cannot switch without porting SynthDefs.
