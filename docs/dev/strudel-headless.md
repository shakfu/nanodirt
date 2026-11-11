# Strudel without a browser

Scope, 2026-10-27. Committed 2026-11-11, independent of rustel (`rustel.md`).

Status 2026-11-11: done up to offline rendering (`nanodirt render`, `nanodirt.headless`). All 46 tunes give the same events in QuickJS as in Node (`tests/test_headless.py`); 39 evaluate, 7 fail in both on functions outside the bundled packages (Csound, Tone.js, note-name globals). Not done: the realtime scheduler and the `nanodirt strudel` runner. Rebuild the bundle with `python scripts/strudel_bundle/build.py` (needs git and Node). Goal: run Strudel code in-process, so it plays through nanodirt or renders offline with no browser and no Node.

## Uses

| Use | For |
|-|-|
| `nanodirt strudel song.js`: play a file, re-evaluate on save | Editor users (nvim, VS Code, Zed and TUI integrations appear in the usage sample) |
| `Strudel(dirt).evaluate(code)` | Scripting from Python |
| Render Strudel code to WAV via nanosynth NRT | Tests, CI, sharing |

## Feasibility probe

Strudel's pattern packages at `a3302cf` (`core`, `mini`, `transpiler`, `tonal`) are plain JavaScript. Their browser APIs (mouse, keyboard, `performance.now`, one `setTimeout`) are only touched when called. Bundled with esbuild and run in QuickJS (`quickjs-ng` 0.17, through its PyPI binding, for the probe only):

- Bundle 1.0 MB; load 53 ms, 4.5 MiB.

- Six snippets (mini-notation, `scale`, `$:` blocks, signals, `sometimesBy`, `degradeBy`) give events identical to Node's.

- Of 46 bundled tunes, 17 give identical events; 29 fail in Node and QuickJS alike, on functions outside these packages (`samples()` in 12, `.piano()`, `.pianoroll()`). None fail only in QuickJS.

- Evaluating a tune and querying 4 cycles: median 5 ms, maximum 1.07 s. Querying 1/8 cycle: 0.23 ms.

- QuickJS errors are terser than V8's: "not a function" without the call chain.

## Timing

Measured 2026-11-11 on macOS arm64, quickjs-ng 0.18.0, by `scripts/strudel_eval_timing.py`. 39 tunes evaluate; each figure is a tune's median of 3 runs, in ms. Query windows are 50 ms, over 4 cycles.

| ms | load | cold evaluate | warm evaluate | slowest query |
|-|-|-|-|-|
| p50 | 90 | 5.6 | 5.1 | 3.7 |
| p90 | 91 | 27 | 26 | 12 |
| max | 94 | 1018 | 1026 | 17 |

- `load` is a new runtime with the bundle; `warm` re-evaluates in the same runtime.

- 38 of 39 tunes evaluate in under 100 ms. `caverave` takes 1.0 s.

- Query bounds must be exact short fractions. A bound one ulp off one, as `begin += step` gives, took up to 320 ms against 1.3 ms. Inference: fraction.js 5.2.1 holds fractions as `BigInt`, and such a bound gets a denominator near 2^53. The script uses a 1/1024-cycle grid.

- Query cost grows with the cycle number. From cycle 1000 (one run), the slowest query took 84 ms (`festivalOfFingers3`), against 17 ms from cycle 0. Evaluate times did not change. Node is not measured.

## Design

Decided 2026-10-27: no third-party runtime dependencies. Decided 2026-11-11: everything Strudel-specific lives in nanodirt, including QuickJS; nanosynth is unchanged.

- **`nanodirt._quickjs`** (MIT code inside nanodirt): quickjs-ng vendored, engine sources only (`quickjs.c`, `libregexp.c`, `libunicode.c`, `dtoa.c`, `cutils`), not `quickjs-libc`. API: context with memory limit; `eval` with a time limit through the interrupt handler, so a runaway loop in live code can be stopped; run pending jobs; register Python callbacks; JS exceptions as Python exceptions with the JS stack; a mutex, since a runtime is single-threaded; release the GIL during eval. Values cross as JSON strings. nanodirt becomes a compiled package. Decided 2026-11-11: [genotrance/quickjs-ng](https://github.com/genotrance/quickjs-ng)'s `module.c` (MIT) is vendored in `csrc/`, built with setuptools as abi3. Its time limit used process CPU `clock()`, which counts scsynth's audio thread; it now counts the evaluating thread's CPU time, and Python callbacks may run under a limit. Each context has a recursive lock, so threads can share one.

- **Strudel bundle** (AGPL-3.0-or-later, inside nanodirt): built from a pinned revision. GPL-3 and AGPL-3 combine in one package (GPL-3 and AGPL-3, section 13). nanodirt then declares `GPL-3.0-or-later AND AGPL-3.0-or-later`, ships both texts, marks the AGPL files, and ships or links the Strudel revision and build script as the bundle's source.

- **JS harness:** evaluates through Strudel's own `repl` with the scheduler stubbed (`setInterval`, `getTime`), and answers `query(begin, end)` with events as JSON.

- **Python scheduler:** queries the next window every ~50 ms, with bounds on an exact grid (see Timing), converts each event to the fields Strudel's OSC output sends (`parseControlsFromHap`: `cps`, `cycle`, `delta`, `midinote`, ...), and calls `Dirt.play_strudel`. Open question: how a slow re-evaluation avoids dropping events. Evaluating on another thread is not enough: `Strudel` serializes `evaluate` and `query` on one runtime, so a 1.0 s evaluate blocks queries for 1.0 s. Options:
  - Two runtimes: evaluate in a spare, swap on success, preload the next spare (90 ms). `cps` and loaded sample maps must carry over.
  - One runtime with a longer lookahead: covers 38 of 39 tunes at 100 ms (Timing), but every edit and `hush` waits that long.

- **Host functions:** `samples()` to nanodirt's Strudel sample maps; `setcps`, `setcpm`, `hush` to the scheduler; visual methods (`pianoroll`, `punchcard`, `color`, ...) pass the pattern through.

Out of scope: MIDI output, hydra, csound, Kabelsalat, drawing.

## Work items

| Item | Effort |
|-|-|
| `nanodirt._quickjs`: vendor, wrap; switch nanodirt to a compiled build with wheels for macOS arm64 and Linux x86_64 | Medium |
| Bundle from a pinned revision; CI check that QuickJS and Node give the same events for all tunes | Low |
| Event to OSC fields | Low |
| Host functions, then the remaining tune failures | Medium |
| Scheduler: windows, latency, `cps` changes; reuse the vortex clock's design | Medium |
| CLI runner with block evaluation (`evaluateBlock`) and error reporting | Medium |
| Offline render: query N cycles into a nanosynth `Score` | Low, after the above |

## Order

After the shared superdough voice chain (`superdough-mapping.md`, "Order of work", step 2): headless Strudel is worth less until the Strudel path sounds as strudel.cc does. The offline render is the earliest low-risk piece.

## Risks

- Strudel changes quickly. The pinned revision and the tune comparison catch breakage; each upgrade costs work.

- Terse QuickJS errors cost live coders time. Source maps would help.

- Heavy patterns: one tune takes 1.0 s to evaluate, which blocks queries in the same runtime.

- Query cost grows with the cycle number, beyond a 50 ms tick by cycle 1000 for one tune (Timing).

## Alternatives considered

- Node subprocess: best fidelity and errors, but requires Node.

- Status quo: strudel.cc with nanodirt over WebSocket already works; headless adds editor use and offline rendering.

- A Python port in the style of vortex: Strudel users write JavaScript, so it must run in a JavaScript engine.
