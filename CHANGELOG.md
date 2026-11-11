# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Strudel code without a browser. `nanodirt render song.strudel` renders it to WAV; `nanodirt.headless.Strudel` evaluates and queries it from Python. Strudel's own pattern packages at `a3302cf` run in QuickJS, so patterns follow upstream rather than a port. `samples()` in the code loads maps into nanodirt; drawing methods do nothing. Scope: `docs/dev/strudel-headless.md`.

- Rendering through Dirt offline: `nanodirt.offline.OfflineEngine` records Dirt's messages into a nanosynth `Score`.

### Changed

- nanodirt is now a compiled package: it vendors quickjs-ng 0.18.0 and genotrance's MIT binding, built with setuptools as one stable-ABI wheel per platform. The binding's time limit counts the evaluating thread's CPU time, not the process's, which includes the engine's audio thread. A context can be shared between threads: upstream crashed when two threads used one, and raised a false `StackOverflow` because QuickJS checked one thread's stack against another's. A JS call made from inside a Python callback no longer clears the outer call's time limit. The package licence is `GPL-3.0-or-later AND AGPL-3.0-or-later AND MIT`: it ships the AGPL Strudel bundle.

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
