# Front-end integration

Status: design, 2026-10-15. Nothing is implemented. Claims about external code cite the revision read; "inference" marks claims that were not checked. Background and the options considered: nanosynth `docs/dev/livecoding.md`.

Revisions read:

- Tidal `5ae6552` (codeberg.org/uzu/tidal, 2026-07-02; Hackage 1.10.3). The GitHub repo is stale.

- Strudel `a3302cf` (codeberg.org/uzu/strudel, 2026-10-13)

- vortex `aefea84` (codeberg.org/uzu/vortex, 2025-12-26)

- SuperDirt `650a1d6` (2025-12-06)

## Goal

One install, one command, and each front end plays with no changes on its side:

```bash
pip install nanodirt
nanodirt                 # Tidal and Strudel: start the server, then use the editor as usual
nanodirt vortex          # vortex: one process, no OSC
```

nanodirt replaces SuperCollider, sclang, SuperDirt, its quarks and sc3-plugins. It does not replace GHC for Tidal or a browser for Strudel.

## Architecture

```
Tidal (ghci)   --UDP 57120 /dirt/play, /dirt/handshake-->  +-----------+
               --UDP 57110 /c_set------------------------> |  engine   |
Strudel (web)  --WS 8080 JSON--------------------------->  |  nanodirt |-- nanosynth
Strudel (app)  --UDP 57120------------------------------>  |   core    |   embedded
vortex         --Python call (in-process)--------------->  +-----------+   scsynth
```

- **Core:** `Dirt.play(params: dict, time: float)`, where `time` is Unix seconds. It holds the SuperDirt event model, orbits, modules, global effects, the sample library and the SynthDefs. It does not know which front end sent the event.

- **Adapters:** each converts one transport to `Dirt.play` calls and applies its front end's field rules (see "Field normalisation").

- **Engine:** nanosynth's embedded scsynth on UDP 57110. Tidal's `/c_set` reaches it directly. sc3-plugins come in the nanosynth wheel (nanosynth `docs/dev/sc3-plugins-build.md`).

Time conversion: OSC timetags are NTP seconds (epoch 1900). nanosynth bundles take Unix seconds (`osc.py`, `NTP_DELTA = 2208988800`). Each adapter converts once.

## Tidal

### What Tidal does

- **Target:** `superdirtTarget` sends to `127.0.0.1:57120`, with bus messages to port 57110 (`Stream/Target.hs:142-153`).

- **Bundles:** one `/dirt/play` per bundle. Arguments are name/value pairs sorted by key (`Process.hs:251-253`). Types: `f`, `i`, `s`, and `b` for VX (`Process.hs:260-267`). Events without `s` are dropped.

- **Timing:** events are computed 0.3 s ahead (`Clock.hs:73`). `mkTidal` sets `oLatency = 0.05` (`Boot.hs:146`). Bundles therefore arrive about 0.25 s before their timetag (inference).

- **Extra fields:** `cps`, `delta` (seconds), `cycle`, `_id_` (stream name as a string), `nudge` (`Process.hs:198-205,345`). SuperDirt ignores `_id_` and `nudge`.

- **Handshake:** `/dirt/handshake`, no arguments. Tidal resends it every 2 s until a reply arrives. It reads only the integers after `"&controlBusIndices"` (`Target.hs:66-98`). SuperDirt replies with 128 control-bus indices (`SuperDirt.sc:40,271-279`). `/dirt/hello` from the server makes Tidal handshake again (`Target.hs:88`). Playback does not wait for the handshake.

- **Bus params** (`^name`): `/dirt/play` carries `name: "c<idx>"`. A separate `/c_set [idx, value]` bundle goes to 57110 (`Process.hs:194,217-231`). scsynth maps `"cN"` strings in `/s_new` to buses itself (`SC_MiscCmds.cpp:430`).

- **Nothing else:** `hush`, mute and solo are local to Tidal. `/dirt/synth-info`, `dirt/setControlBus` and `/play2` are SuperDirt endpoints that Tidal never calls.

- **Params:** `Params.hs` defines 197 names. Tidal resolves its 57 aliases itself (`lpf` to `cutoff`, `sz` to `size`, `clip` to `legato`, `midinote` to `note`, ...). nanodirt never sees an alias.

### What nanodirt needs

1. Bind UDP 57120 exclusively (no `SO_REUSEPORT`). On failure, report that sclang or SuperDirt is probably running.

2. Answer `/dirt/handshake` to the sender with `"&serverHostname", "127.0.0.1", "&serverPort", 57110, "&controlBusIndices", i0..i127`. Allocate the 128 control buses from nanosynth's allocator.

3. Send `/dirt/hello` to the last handshake sender on startup, so a running Tidal resyncs its bus indices after a nanodirt restart (inference: SuperDirt does not do this).

4. Pass `"cN"` string values through to `/s_new` unchanged.

5. Ignore `_id_` and `nudge`.

### Integration

Zero-config. With `nanodirt` running, the stock `BootTidal.hs` and every editor plugin work unchanged.

Non-default ports need `mkTidalWith [(superdirtTarget {oPort = .., oBusPort = Just ..}, [superdirtShape])]` in BootTidal (the commented example at `BootTidal.hs:11-13`). nanodirt prints this snippet when started with `--port`.

Starting nanodirt from the editor:

- vim-tidal already starts a sound server: `let g:tidal_sc_boot_cmd = "nanodirt"` (`plugin/tidal.vim:95-98`).

- VSCode, Pulsar and Emacs start only ghci. A `nanodirt-ghci` wrapper set as `ghciPath` could start both. Deferred: it adds process-lifecycle work (ghci restarts must not orphan the engine), and running `nanodirt` in a terminal already works.

## Strudel

### What Strudel does

- **Web REPL:** OSC mode is the `audioEngineTarget = osc` setting; webaudio is the default (`website/src/repl/useReplContext.jsx:70-72`, `settings.mjs:65`). Each event is one WebSocket JSON frame to the hardcoded `ws://localhost:8080` (`packages/osc/osc.mjs:15,79-87`):

  ```json
  {"address": "/dirt/play", "args": ["s", "bd", "n", 0, "cps", 0.5], "timestamp": 1760500000123.4, "host": "...", "port": 57120}
  ```

  `timestamp` is Unix milliseconds. `host` and `port` are optional, taken from the `oschost`/`oscport` controls.

- **Node bridge** (`packages/osc/server.js`): turns each frame into a UDP bundle to `127.0.0.1:57120`. JS numbers become float32 (inference, from osc.js type inference).

- **Desktop app** (Tauri): sends UDP to the hardcoded `127.0.0.1:57120` from port 57122 natively, with no bridge (`src-tauri/src/oscbridge.rs:58-60,132-155`).

- **Latency:** fixed at 0.1 s (`packages/core/cyclist.mjs:18`).

- **Possible clock offset (inference, untested):** in OSC mode, `targetTime` is already in performance seconds, but `getPerformanceTime()` treats it as AudioContext seconds (`superdough/clockbridge.mjs:21-44`). Timestamps may run ahead. nanodirt logs early/late skew per adapter, which will show whether this happens.

- **Controls:** 293 main names, plus numbered `fm1..fm8` variants; aliases are resolved client-side (`packages/core/controls.mjs`). `parseControlsFromHap` (`osc.mjs:35-56`):
  - adds `cps`, `cycle`, `delta`;
  - sets `midinote` from `note` (octave default 3);
  - sets `size` from `roomsize`;
  - with `unit=c`, divides `speed` by `cps`;
  - sends `channels` as a JSON string;
  - sends `bank` and `s` separately.

### Differences from Tidal

| Field | Strudel | SuperDirt / Tidal | nanodirt rule (WS adapter) |
|-|-|-|-|
| `sustain` | ADSR sustain level, 0..1 (`superdough/helpers.mjs:59-79`) | event duration in seconds (`DirtEvent.sc:110-128`) | treat as level; duration from `delta` and `clip` |
| `clip` | main name; `legato` is its alias | Tidal sends `legato` | `clip` -> `legato` |
| `bank` | superdough plays `<bank>_<s>` (`superdough.mjs:560`) | ignored | `s = bank + "_" + s` |
| `duration` | main name; `dur` is its alias | unused | ignore |
| `midinote` | computed with octave 3 | `note ? n + octave*12`, octave 5 | an explicit `midinote` wins, as in SuperDirt |

Strudel-only controls: 167 of the 293 main names appear nowhere in SuperDirt's sources (lexical check, heuristic). They are superdough features: filter envelopes (`lpenv`, `lpattack`, ...), FM (`fmh`, `fmi`, `fmenv`), pitch envelope (`penv`), `vib`, `pw`, `unison`, wavetables, `distorttype`, `roomlp`/`roomdim`, `duck*`, `compressor*`, `postgain`. On SuperDirt they do nothing. nanodirt implements them (decided 2026-10-15) as modules in the same event model, so Strudel code sounds as it does on strudel.cc.

### Samples

Strudel loads samples by URL into the browser (`samples('github:...')`, `strudel.json` maps; `superdough/sampler.mjs:152-226,274-288`). Default banks come from CDN JSON maps (`website/src/repl/prebake.mjs:25-41`). The OSC path sends only `s`, `n` and `bank`, not URLs. So code that plays on strudel.cc can be silent on SuperDirt: `RolandTR909_bd` has no local folder.

nanodirt fixes this on its side:

- It loads the same default maps that `prebake.mjs` loads, and resolves `s`/`bank` through them.

- It downloads each sample on first use into an on-disk cache.

- `nanodirt --samples github:user/repo` (or a `strudel.json` URL or path) adds more maps, with the same resolution rules as `sampler.mjs:152-196`.

- Local SuperDirt-layout folders (Dirt-Samples) still work.

The first play of an uncached sample is late (download time). Preload with `nanodirt samples fetch <map>`.

### Integration

Primary: nanodirt serves the WebSocket on 8080 itself, with the same JSON as `server.js:43-53`. No Node bridge and no Strudel change: the user picks OSC output in strudel.cc settings and runs `nanodirt`.

- Needs a WebSocket library; the stdlib has none. `websockets` is pure Python and BSD-licensed. Make it a default dependency, since Strudel users are a main target.

- Port 8080 is hardcoded in Strudel and often taken. On bind failure, say so and fall back to telling the user to run the Node bridge.

- Browser policy (inference, needs a live test): Chrome and Firefox treat `ws://localhost` from https as trustworthy, and `@strudel/sampler` on `localhost:5432` relies on that. Chrome's Local Network Access prompt and Safari may block or prompt. Test all three before release.

The desktop app and the Node bridge reach the UDP adapter with no extra work. Neither carries a front-end marker, so the UDP adapter cannot tell Strudel from Tidal. It applies Tidal rules; the `sustain` clash then remains for UDP Strudel users. A `--strudel` flag switches the UDP adapter to Strudel rules.

Upstream option, later: a small Strudel output module that also sends resolved sample URLs. It removes the map duplication but needs upstream acceptance.

## vortex

### What vortex does

- **Install:** PyPI has only `tidalvortex 0.1.0a0` (2022), for Python below 3.11. The repo needs Python 3.11 to 3.13 (`pyproject.toml`). Users install from git.

- **Dependencies:** `pyliblo3 >=0.16.4,<0.17` (wheels for cp310-313 on macOS arm64, manylinux x86_64, Windows; no cp314 under that pin), `linkpython-extern`, PyQt6, QScintilla, IPython.

- **Streams:** `boot.py` creates one `LinkClock` at import (`:4`). `p(key, pattern)` builds `SuperDirtStream(name=key)` lazily, with the class and port fixed (`boot.py:8-14`). `p()` takes no port argument.

- **Output hook:** `BaseStream.notify_event(event, timestamp, cps, cycle, delta)` (`stream.py`). `notify_tick` reads `self.latency`, which only `SuperDirtStream` sets.

- **Timing:** a 20 Hz tick thread (`stream.py:77-130`). Timestamps are `liblo.time()` seconds (NTP epoch), with latency 0.2 and `nudge` added (`stream.py:170-173`).

- **Errors:** the tick loop wraps all subscriber calls in a bare `except: pass` (`stream.py:118-119`). An exception drops that tick for all streams, silently.

- **Controls:** 160 (`control.py:7-329`); the only alias is `sound = s`. Mini-notation `"bd:3"` becomes `s("bd") << n(3)`.

### Integration

nanodirt does not bundle or fork vortex. vortex stays a separate install, and nanodirt imports it.

vortex's GUI and REPL get their namespace from one module. `vortex_dsl()` imports `vortex.boot` and calls `_default_clock.start()` and `.stop()` (`__init__.py:20-43`). The GUI then runs each block with `exec(code, vars(module))` (`gui.py:152`); the REPL embeds IPython in the same module (`repl.py:7-11`). `vortex.boot` contains only `from vortex import *`, the clock, `p`, `hush` and `d` (30 lines).

nanodirt ships `nanodirt.vortex.boot`, a replacement for that module:

- `from vortex import *`: the pattern language, controls and mini-notation. None of these import liblo or Link.

- `_default_clock`: nanodirt's clock, with `start()` and `stop()`. It uses Ableton Link (`link`, already a vortex dependency), so sessions stay in sync with other Link apps.

- `p`, `d`, `hush`: the same behaviour as vortex's, creating nanodirt streams.

- Streams query the pattern each tick and call `Dirt.play` with Unix-time timestamps. No OSC.

`nanodirt vortex [--cli]` registers it as `sys.modules["vortex.boot"]`, then calls vortex's own `start_gui()` or `start_repl()`. The user gets vortex's editor or REPL unchanged.

Compared with replacing only the `SuperDirtStream` class inside `vortex.boot`:

- `vortex.stream` is never imported, so pyliblo3 is not loaded and `liblo.time()` is not used. Timestamps come from the Link clock in Unix time.

- Errors are logged. vortex's tick loop drops them silently (`stream.py:118-119`).

- The contract is a module with 4 names, instead of a class name inside another module.

- nanodirt can add names to the namespace (`samples(...)`, orbit helpers) without touching vortex.

Cost: if vortex adds names to `boot.py`, nanodirt must add them too. A nanodirt test compares the public names of both modules against the installed vortex, so this shows up as a failing test.

Thread safety: streams call `Dirt.play` from the clock thread. nanosynth's `world_send_packet` releases the GIL around `World_SendPacket` (`_scsynth.cpp:494-515`), and scsynth serialises producers with `mDriverLock` (`SC_CoreAudio.cpp:184-194`). The GIL is shared with Qt or IPython. The 0.2 s latency absorbs pauses; a longer pause makes events late, not early.

Plain `vortex` with `nanodirt` running also works over UDP 57120, with no launcher.

Upstream PR to vortex, optional: a documented way to supply the DSL module (for example an entry point or a `vortex --dsl nanodirt.vortex.boot` flag). With it, nanodirt stops writing to `sys.modules`.

Packaging: `nanodirt[vortex]` cannot depend on a git URL on PyPI. Document `pip install git+https://codeberg.org/uzu/vortex` until vortex publishes a current release. The vortex path supports Python 3.11 to 3.13 only.

## Ports

| Port | Owner | Conflict |
|-|-|-|
| UDP 57120 | nanodirt (Tidal, Strudel UDP) | sclang's default `langPort` |
| UDP 57110 | nanosynth engine | any running scsynth |
| TCP 8080 | nanodirt WebSocket (Strudel) | common dev-server port |

All three binds are exclusive. A failure names the process holding the port (as `lsof -iTCP:<port>` does) and the flag to change it. Port 8080 conflicted on the first real test (llama.cpp, see `spike-results.md`). Because Strudel hardcodes `ws://localhost:8080` (`osc.mjs:15`), propose an upstream Strudel change: read the port from a setting or an `oscport`-style control.

## Command line

```
nanodirt [--port 57120] [--engine-port 57110] [--ws-port 8080] [--no-ws]
         [--samples <path|url|github:user/repo>]... [--strudel] [--orbits 12] [--channels 2]
nanodirt vortex [--cli]
nanodirt samples fetch <map|Dirt-Samples>
```

## Order of work

1. Core phase 1 (samples, event model, basic fields, `lpf`/`hpf`/`bpf`), plus the UDP adapter with the handshake. Tidal works.

2. The WebSocket adapter and the Strudel sample maps. Strudel works with default banks.

3. `nanodirt vortex` with `nanodirt.vortex.boot`.

4. Orbits and global effects, then the remaining modules and the `super*` synths. The synths need sc3-plugins in the nanosynth wheel.

5. superdough-only controls (in scope, decided 2026-10-15). Specify them against `packages/superdough` first, as `superdirt-parameter-mapping.md` does for SuperDirt.

## Open questions

- `sustain` over UDP: is the `--strudel` flag enough, or should the UDP adapter detect Strudel from its fields (for example the presence of `clip` or `bank`)?

