# Strudel without a browser

Scope, 2026-10-27. Not started. Goal: run Strudel code in-process, so it plays through nanodirt or renders offline with no browser and no Node.

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

## Design

Decided 2026-10-27: no third-party runtime dependencies. QuickJS is vendored and wrapped in nanosynth.

- **`nanosynth._quickjs`** (MIT): quickjs-ng vendored in `thirdparty/`, behind `NANOSYNTH_EMBED_QUICKJS`, as `_midi` is. Only the engine sources (`quickjs.c`, `libregexp.c`, `libunicode.c`, `dtoa.c`, `cutils`); not `quickjs-libc`. API: context with memory limit; `eval` with a time limit through the interrupt handler, so a runaway loop in live code can be stopped; run pending jobs; register Python callbacks; JS exceptions as Python exceptions with the JS stack; a mutex, since a runtime is single-threaded. Values cross as JSON strings.

- **`nanodirt-strudel`** (AGPL-3.0, pure Python): the Strudel bundle, built from a pinned revision, and the harness. Strudel's AGPL code stays out of MIT nanosynth and GPL nanodirt.

- **JS harness:** evaluates through Strudel's own `repl` with the scheduler stubbed (`setInterval`, `getTime`), and answers `query(begin, end)` with events as JSON.

- **Python scheduler:** queries the next window every ~50 ms, converts each event to the fields Strudel's OSC output sends (`parseControlsFromHap`: `cps`, `cycle`, `delta`, `midinote`, ...), and calls `Dirt.play_strudel`. Evaluation runs off the scheduling thread, so a slow re-evaluation does not drop events.

- **Host functions:** `samples()` to nanodirt's Strudel sample maps; `setcps`, `setcpm`, `hush` to the scheduler; visual methods (`pianoroll`, `punchcard`, `color`, ...) pass the pattern through.

Out of scope: MIDI output, hydra, csound, Kabelsalat, drawing.

## Work items

| Item | Effort |
|-|-|
| `nanosynth._quickjs`: vendor, wrap, test on the wheel platforms | Medium |
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

- Heavy patterns: one tune took 1.07 s to evaluate.

## Alternatives considered

- Node subprocess: best fidelity and errors, but requires Node.

- Status quo: strudel.cc with nanodirt over WebSocket already works; headless adds editor use and offline rendering.

- A Python port in the style of vortex: Strudel users write JavaScript, so it must run in a JavaScript engine.
