# Status

Paused 2026-10-15. Read this first when resuming.

## Goal

nanodirt replaces SuperCollider + SuperDirt for users of TidalCycles, Strudel and vortex. It reimplements SuperDirt's event model on nanosynth's embedded scsynth. It is a GPL-3 package that depends on MIT nanosynth.

## Documents

| File | Content |
|-|-|
| `docs/dev/integration.md` | Design: core plus per-front-end adapters, ports, CLI, order of work |
| `docs/dev/superdirt-parameter-mapping.md` | Reference spec: SuperDirt event pipeline, fields, node/bus layout |
| `docs/dev/spike-results.md` | What the spike proved, measured timings, findings |
| nanosynth `docs/dev/livecoding.md` | Options considered (SuperDirt on nanosynth, nanodirt, uzu/dirt) and why nanodirt |
| nanosynth `docs/dev/sc3-plugins-build.md` | sc3-plugins in the nanosynth wheel |
| nanosynth `docs/dev/dirt-parameter-mapping.md` | Classic Dirt; superseded as reference, kept as a record |

## Decisions

- SuperDirt is the reference semantics. Classic Dirt (uzu/dirt) is not a target.

- nanodirt is a separate GPL-3 repo.

- Strudel's superdough-only controls (167 of 293) are in scope, after SuperDirt parity.

- sc3-plugins ship inside the nanosynth wheel, without HOA, NovaDiskIO and LADSPA. PitchDetection is built on macOS only.

- vortex: nanodirt ships a replacement `vortex.boot` module (Link clock, `p`, `d`, `hush`) and registers it before starting vortex's own GUI or REPL. vortex is not bundled or forked.

- Strudel: nanodirt serves the WebSocket on 8080 itself; no Node bridge.

## Done

- **nanosynth** (committed, `7a212da`): sc3-plugins vendored in `thirdparty/sc3-plugins/`, 75 plugins per server, `FM7` and `PulseDPW` wrappers, render tests. `make test`: 2182 passed, 34 skipped on macOS arm64.

- **Spike** (`spike/`, uncommitted, throwaway). Played audio, recorded and checked:
  - Tidal 1.10.3 with stock `BootTidal.hs`: handshake ("Connected to SuperDirt."), bus params as `"cN"`, events 0.25-0.29 s early.
  - vortex in-process via the `vortex.boot` replacement: on time, pyliblo3 never loaded.
  - Strudel on strudel.cc in Chrome: works after the Local Network Access prompt; timestamps exact; events about 0.29 s early.
  - Strudel in Safari: no connection reached the spike.

## Not done / unverified

- **Linux and Windows builds of sc3-plugins.** They have not run. Check the CI run of `7a212da`; the Windows flags in `cmake/sc3_plugins.cmake` are untested.

- **Safari.** The error was not captured. Develop > Show JavaScript Console while evaluating `s("bd").osc()` on strudel.cc.

- **Firefox.** Untested.

- `CLAUDE.md` in nanosynth does not mention `thirdparty/sc3-plugins` under Vendored Dependencies.

- `docs/` and `spike/` in this repo are uncommitted.

## Next

1. **Package setup:** `pyproject.toml` (GPL-3, depends on nanosynth with bundled sc3-plugins; `websockets` as a dependency), Makefile with `make test`/`lint`/`typecheck`, CI. Python 3.10-3.14 to match nanosynth. The vortex path needs 3.11-3.13.

2. **Phase 1 core,** per `integration.md` "Order of work" and `superdirt-parameter-mapping.md`: sample library, event pipeline (defaults, `splitName`, `calcTimeSpan`, `finaliseParameters`), `dirt_sample_*`, `dirt_gate`, cut groups, `lpf`/`hpf`/`bpf`. Make the core a pure function from event to OSC bundles, so most tests need no audio. Use nanosynth `Score` for offline render tests.

3. **UDP adapter:** `/dirt/play`, `/dirt/handshake` with 128 control buses, `/dirt/hello`.

4. **Requirements from the spike:**
   - load samples in the background, off the receive path (each first hit cost about 30 ms);
   - drop events later than a threshold (about 50 ms) and log a count (Strudel sent one 2.97 s late);
   - on a failed bind, name the process holding the port (port 8080 was taken by a llama.cpp server).

5. Then: WebSocket adapter and Strudel sample maps; `nanodirt vortex`; orbits and global effects; remaining modules and `super*` synths; superdough controls.

## Open questions

- Strudel `sustain` over UDP (desktop app, Node bridge): a `--strudel` flag, or detect Strudel from its fields?

- Upstream PRs: a configurable WebSocket port in Strudel (8080 is hardcoded in `osc.mjs:15`); a DSL-module hook in vortex.

## Environment

| Item | Location | Note |
|-|-|-|
| Dirt-Samples | `~/.cache/nanodirt/Dirt-Samples` | shallow clone of github.com/tidalcycles/Dirt-Samples |
| Tidal 1.10.3 | `~/.cabal/store/ghc-9.6.7` | built for the spike. The package environment was in a session scratchpad and is gone. Recreate: `cabal install tidal-1.10.3 --lib --package-env <dir>/tidal.env`, then `ghci -package-env <dir>/tidal.env` with `:script BootTidal.hs` |
| vortex | not installed | `uv venv -p 3.12 vx && uv pip install git+https://codeberg.org/uzu/vortex` |
| Spike | `spike/` | `uv run --with nanosynth==0.4.1 --with websockets python spike.py`. Needs ports 57120, 57110 and 8080 free (stop SuperCollider and any llama server). `probe_udp.py` and `probe_ws.mjs` send Tidal- and Strudel-shaped test events. `spike_vortex.py --auto 4` runs vortex headless. vortex in `spike_vortex.py` needs the vx venv plus nanosynth and websockets |

Upstream revisions read: SuperDirt `650a1d6`, Tidal `5ae6552` (codeberg.org/uzu/tidal), Strudel `a3302cf`, vortex `aefea84`, uzu/dirt `123b399`, sc3-plugins `Version-3.14.1`.
