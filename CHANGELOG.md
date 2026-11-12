# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0]

### Added

- Strudel code without a browser. `nanodirt render song.strudel` renders it to WAV; `nanodirt.headless.Strudel` evaluates and queries it from Python. Strudel's own pattern packages at `a3302cf` run in QuickJS, so patterns follow upstream rather than a port. `samples()` in the code loads maps into nanodirt; drawing methods do nothing. The bundle patches fraction.js 5.2.1's float-to-fraction conversion (`scripts/strudel_bundle/farey.js`), which steps through the Stern-Brocot tree one mediant at a time: a float one ulp from a short fraction took up to 10^7 steps, so one tune evaluated in 1.0 s instead of 14 ms. The patch takes each run of steps at once and returns the same fractions. Scope: `docs/dev/strudel-headless.md`.

- `nanodirt strudel song.strudel` plays Strudel code live, without a browser, and re-evaluates it on each save. A failed evaluation is reported and the previous pattern plays on. Events are timed as Strudel's own scheduler (`cyclist.mjs`) times them; `nanodirt.headless.Scheduler` runs it from Python. Each save evaluates in a fresh runtime, swapped in on success, so a slow evaluation drops no events; state the code leaves in the runtime does not carry over, unlike on strudel.cc. It opens no audio input, so macOS does not ask for the microphone.

- `--inputs N` sets the engine's audio input channels: 2 by default, as SuperDirt, for its `in` and `inr` instruments; 0 for `nanodirt strudel`. On macOS, opening input asks for microphone access, so `nanodirt --inputs 0` avoids the prompt.

- Windows (AMD64) support: CI builds and tests a wheel. On Windows the QuickJS time limit counts the evaluating thread's CPU time (`GetThreadTimes`), as on Linux and macOS; `clock()` there counts the whole process. Strudel code, sample maps and fixtures are read as UTF-8, not in the locale's encoding.

- Rendering through Dirt offline: `nanodirt.offline.OfflineEngine` records Dirt's messages into a nanosynth `Score`.

### Changed

- nanodirt is now a compiled package: it vendors quickjs-ng 0.18.0 and genotrance's MIT binding, built with setuptools as one stable-ABI wheel per platform. The binding's time limit counts the evaluating thread's CPU time, not the process's, which includes the engine's audio thread. A Python int outside int32 reaches JavaScript as a number: the binding truncated it (2^40 became 0), and crashed on one beyond a double. A context can be shared between threads: upstream crashed when two threads used one, and raised a false `StackOverflow` because QuickJS checked one thread's stack against another's. A JS call made from inside a Python callback no longer clears the outer call's time limit. The package licence is `GPL-3.0-or-later AND AGPL-3.0-or-later AND MIT AND BSD-2-Clause AND BSD-3-Clause AND ISC`: it ships the AGPL Strudel bundle, which includes 40 MIT, BSD and ISC npm packages. Their notices are in `LICENSES/strudel-bundle-third-party.txt`, which the bundle build writes from what esbuild includes. The sdist carries the bundle's build scripts and the test suite with its fixtures.

### Fixed

- Every start printed `exception in GraphDef_Recv: UGen 'MouseX' not installed.`: `pmsin` needs UIUGens, which nanosynth does not ship. It is no longer sent, and `s "pmsin"` plays nothing, as before.

## [0.2.0]

### Added

- superdough's synth sources for Strudel events: `sine`, `triangle`, `square`, `sawtooth` (and `sin`, `tri`, `sqr`, `saw`), and `white`, `pink`, `brown`, `crackle` noise. They played nothing before. Envelope, gain and pitch follow superdough (`docs/dev/superdough-mapping.md`). Effects, `pan` and `n` (partials) do not apply to them yet. Tidal's `s "sine"` still plays the sample bank.

- A superdough oracle for the Strudel path: `scripts/superdough_oracle/` renders events through Strudel's own superdough in headless Chromium. Opt-in tests: `NANODIRT_TEST_SUPERDOUGH=1`.

### Changed

- Requires `nanosynth>=0.5.1` (`PartConv`, for the Strudel reverb).

- Strudel samples follow superdough, not SuperDirt: level `gain * velocity` (default 0.8, linear) instead of `0.4 * gain^4`, so they play about 2.7 times louder at default gain; `note` transposes a sample bank from note 36; a sample plays its whole slice unless `clip`, `loop` or `release` is set; `n` rounds instead of truncating; `cut` groups span all orbits. `pan` and `postgain` apply to every Strudel voice, and so do superdough's filters: `cutoff`, `hcutoff`, `bandf` with `resonance`/`hresonance`/`bandq` as the biquad's Q (in dB for lowpass and highpass), `ftype` 2 (24 dB), and the cutoff envelope (`lpenv`, `lpattack`, ... and `hp*`, `bp*`, `fanchor`). The ladder filter (`ftype` 1) plays as 12 dB. Each orbit gets superdough's delay and reverb on first use: `delay` sends to a feedback delay (`delaytime`, default `delaysync` 3/16 cycle; `delayfeedback` 0.5, at most 0.98), `room` to a convolution reverb whose impulse response is generated as superdough's (`roomsize`, `roomfade`, `roomlp`, `roomdim`). `dry` scales the direct sound. Needs nanosynth's `PartConv`. Strudel events naming a SuperDirt synth (`superpiano`, ...) still play through SuperDirt.

### Fixed

- Strudel's `clip` was applied twice: Strudel's `delta` already includes it, and nanodirt also used `clip` as `legato`, so `clip(0.5)` played a quarter of the slot. A `clip` now cuts the sound at `delta`, as superdough does.

## [0.1.0]

### Added

- First release. SuperDirt's event model, SynthDefs, modules and instruments on nanosynth, translated from SuperDirt `650a1d6` and checked against it. Needs no SuperCollider install.

- Front ends: Tidal over UDP (port 57120), Strudel over WebSocket (`ws://localhost:8080`), and vortex in one process (`nanodirt vortex`).

- `nanodirt samples fetch` downloads Dirt-Samples or Strudel sample maps into `~/.cache/nanodirt`.

- Requires `nanosynth>=0.5.0`.
