# Strudel without a browser

Scope, 2026-10-27. Committed 2026-11-11, independent of rustel (`rustel.md`).

Status 2026-11-12: done. `nanodirt strudel song.strudel` plays a file and re-evaluates it on save; `nanodirt render` renders offline; `nanodirt.headless` exposes both to Python. All 46 tunes give the same events in QuickJS as in Node (`tests/test_headless.py`); 39 evaluate, 7 fail in both on functions outside the bundled packages (Csound, Tone.js, note-name globals). Block evaluation is out of scope for the file runner (Design). Rebuild the bundle with `python scripts/strudel_bundle/build.py` (needs git and Node). Goal: run Strudel code in-process, so it plays through nanodirt or renders offline with no browser and no Node.

## Uses

| Use | For |
|-|-|
| `nanodirt strudel song.strudel`: play a file, re-evaluate on save | Editor users (nvim, VS Code, Zed and TUI integrations appear in the usage sample) |
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

Measured 2026-11-12 on macOS arm64, quickjs-ng 0.18.0, by `scripts/strudel_eval_timing.py`; Node 22.19 by `scripts/strudel_bundle/node_timing.mjs`. 39 tunes evaluate; each figure is a tune's median of 3 runs, in ms. Query windows are 50 ms, over 4 cycles from the given cycle.

| ms | load | warm evaluate | query from cycle 0 | query from cycle 1000 |
|-|-|-|-|-|
| p50 | 95 | 5.5 | 3.9 | 4.2 |
| p90 | 99 | 20 | 14 | 12 |
| max | 112 | 79 | 19 | 19 |

- `load` is a new runtime with the bundle; `warm` re-evaluates in the same runtime. A first evaluate costs the same as a warm one.

- The slowest evaluate is `swimming` (79 ms). Node's median evaluate is about half QuickJS's.

- fraction.js 5.2.1 converts a float to a fraction by walking the Stern-Brocot tree one mediant per step, up to denominator 10^7. A float one ulp from a short fraction takes up to 10^7 steps. Such floats come from query bounds (`0.5 + 2^-52`) and from values >= 1, which `parse` divides by a power of ten first. With fraction.js's loop, in QuickJS: `caverave` evaluated in 1.0 s, a query from `0.5000000000000001` took 320 ms, and `festivalOfFingers3`'s queries rose from 15 to 95 ms between cycles 10 and 100. In Node's profiler `parse` was 48-69% of the time in all three. fraction.js 4.3.7 uses the same walk and gains 10-20%.

- The bundle replaces the loop (`scripts/strudel_bundle/farey.js`): it takes each run of same-direction steps at once, by binary search on the loop's own float comparisons, so it returns the same fractions. Checks: `tests/test_headless.py` compares it with the loop; Node's events for the 46 tunes are byte-identical with and without it. After: `caverave` 14 ms, the off-grid query 1.5 ms, `festivalOfFingers3` 16-18 ms at every cycle.

## Design

Decided 2026-10-27: no third-party runtime dependencies. Decided 2026-11-11: everything Strudel-specific lives in nanodirt, including QuickJS; nanosynth is unchanged.

- **`nanodirt._quickjs`** (MIT code inside nanodirt): quickjs-ng vendored, engine sources only (`quickjs.c`, `libregexp.c`, `libunicode.c`, `dtoa.c`, `cutils`), not `quickjs-libc`. API: context with memory limit; `eval` with a time limit through the interrupt handler, so a runaway loop in live code can be stopped; run pending jobs; register Python callbacks; JS exceptions as Python exceptions with the JS stack; a mutex, since a runtime is single-threaded; release the GIL during eval. Values cross as JSON strings. nanodirt becomes a compiled package. Decided 2026-11-11: [genotrance/quickjs-ng](https://github.com/genotrance/quickjs-ng)'s `module.c` (MIT) is vendored in `csrc/`, built with setuptools as abi3. Its time limit used process CPU `clock()`, which counts scsynth's audio thread; it now counts the evaluating thread's CPU time, and Python callbacks may run under a limit. Each context has a recursive lock, so threads can share one.

- **Strudel bundle** (AGPL-3.0-or-later, inside nanodirt): built from a pinned revision. GPL-3 and AGPL-3 combine in one package (GPL-3 and AGPL-3, section 13). nanodirt then declares `GPL-3.0-or-later AND AGPL-3.0-or-later`, ships both texts, marks the AGPL files, and ships or links the Strudel revision and build script as the bundle's source.

- **JS harness:** evaluates through Strudel's own `repl` with the scheduler stubbed (`setInterval`, `getTime`), and answers `query(begin, end)` with events as JSON.

- **Python scheduler** (`Scheduler`): cyclist.mjs in a thread, with its windows, latency (0.1 s) and `cps` rules, so events land where strudel.cc puts them. Each event's fields are those Strudel's OSC output sends (`parseControlsFromHap`: `cps`, `cycle`, `delta`, `midinote`, ...); they go to `Dirt.play_strudel`. Each evaluation runs in a fresh runtime, loaded in the background after the previous one (95 ms), and replaces the playing runtime only if it succeeds. Queries never wait for an evaluation; with one shared runtime, a 0.6 s evaluate skipped 10 ticks (`test_slow_evaluation_drops_no_events`). Decided 2026-11-12 over one runtime, which holds only while every evaluate stays under about 0.1 s. The harness reports each `setcps`/`setcpm` call, and the scheduler applies them as cyclist.mjs's `setCps`; sample maps live in Python and carry over. Before the swap, the scheduler queries the new pattern 4 cycles ahead and passes the events to `prepare`, which loads the samples and `gm_` soundfonts they use (`soundfonts.md`). Unlike strudel.cc, state that code leaves in the runtime (globals, `register`) does not carry into the next evaluation.

- **Runner** (`nanodirt strudel FILE`): evaluates the file once its size and mtime hold for one 0.1 s poll, so a half-written save is not evaluated. A failed evaluation is reported and the previous pattern plays on. `samples()` loads maps in a background thread, so a download does not hold up the evaluation. The engine opens no audio input unless `--inputs` asks for it, so macOS does not ask for the microphone.

- **Block evaluation** (`evaluateBlock`): out of scope for the file runner. strudel.cc's editor sends the block under the cursor; its labelled patterns replace their old versions, and other blocks keep what was last evaluated. A saved file has no cursor and is the whole source, and patterns depend only on time, so evaluating the file gives what evaluating every block gives. Evaluating one block needs an editor plugin that sends it.

- **Host functions:** `samples()` to nanodirt's Strudel sample maps; `setcps`, `setcpm` to the scheduler; `hush()` silences through Strudel's own `repl`; visual methods (`pianoroll`, `punchcard`, `color`, ...) pass the pattern through.

Out of scope: MIDI output, hydra, csound, Kabelsalat, drawing.

## Work items

| Item | Effort |
|-|-|
| `nanodirt._quickjs`: vendor, wrap; switch nanodirt to a compiled build with wheels for macOS arm64, Linux x86_64 and Windows AMD64 | Medium |
| Bundle from a pinned revision; CI check that QuickJS and Node give the same events for all tunes | Low |
| Event to OSC fields | Low |
| Host functions, then the remaining tune failures | Medium |
| Scheduler: windows, latency, `cps` changes, as cyclist.mjs | Medium |
| CLI runner with error reporting | Medium |
| Offline render: query N cycles into a nanosynth `Score` | Low, after the above |

## Order

After the shared superdough voice chain (`superdough-mapping.md`, "Order of work", step 2): headless Strudel is worth less until the Strudel path sounds as strudel.cc does. The offline render is the earliest low-risk piece.

## Risks

- Strudel changes quickly. The pinned revision and the tune comparison catch breakage; each upgrade costs work.

- Terse QuickJS errors cost live coders time. Source maps would help.

- The fraction.js patch is tied to 5.2.1's source text; the build fails if the loop changes.

## Alternatives considered

- Node subprocess: best fidelity and errors, but requires Node.

- Status quo: strudel.cc with nanodirt over WebSocket already works; headless adds editor use and offline rendering.

- A Python port in the style of vortex: Strudel users write JavaScript, so it must run in a JavaScript engine.
