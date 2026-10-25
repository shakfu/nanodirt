# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0]

### Added

- First release. SuperDirt's event model, SynthDefs, modules and instruments on nanosynth, translated from SuperDirt `650a1d6` and checked against it. Needs no SuperCollider install.

- Front ends: Tidal over UDP (port 57120), Strudel over WebSocket (`ws://localhost:8080`), and vortex in one process (`nanodirt vortex`).

- `nanodirt samples fetch` downloads Dirt-Samples or Strudel sample maps into `~/.cache/nanodirt`.

- Requires `nanosynth>=0.5.0`.
