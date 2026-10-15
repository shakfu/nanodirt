# Spike results

Date: 2026-10-15. Code: `spike/` (throwaway, not nanodirt). Engine: nanosynth 0.4.1 from PyPI, macOS arm64, 48 kHz. Samples: Dirt-Samples in `~/.cache/nanodirt/Dirt-Samples`. Each run was recorded with `Server.record` and checked for peaks at the expected times.

## Results

| Front end | Path | Result | Early by (timetag - receive) |
|-|-|-|-|
| Tidal 1.10.3, stock `BootTidal.hs` | UDP 57120 | plays; Tidal prints "Connected to SuperDirt." | 0.25-0.29 s (16 events) |
| Tidal bus params (`cutoffbus 1 ...`) | UDP 57120 + `/c_set` to 57110 | `/dirt/play` carries `cutoff: "c1"`, index 1 of the handshake list | - |
| vortex (git `aefea84`) via `vortex_dsl()` with `spike/vortex_boot.py` as `vortex.boot` | in-process | plays on the 0.5 s grid; `vortex.stream` and `pyliblo3` never imported | 0.206-0.212 s (8 events) |
| Strudel frames from Node (format of `osc.mjs:79-87`) | WS 8080 | plays; ms timestamps convert correctly | 0.1 s + pattern offset |
| Strudel on strudel.cc, Chrome | WS 8080 | plays, after Chrome's Local Network Access permission prompt (2026-10-15, manual) | median 0.285 s, steady state 0.23-0.34 s (197 events); one outlier 2.97 s late |
| Strudel on strudel.cc, Safari | WS 8080 | no connection reached the spike (no ESTABLISHED socket on 8080); console error not yet captured | - |

## Findings for nanodirt

- **Sample loading must leave the receive path.** The spike reads a buffer synchronously on first use, and each first hit delays the next event by about 30 ms. nanodirt should preload, or read asynchronously and drop or delay only the event that needs the new buffer.

- **Latency budgets** are 0.25 s (Tidal), 0.2 s (vortex) and 0.1 s (Strudel). Strudel's is the tightest.

- **The `vortex.boot` replacement works through vortex's own `vortex_dsl()`.** The GUI and REPL use the same entry point. Neither was driven interactively.

- **Port 8080 was taken on the first real run** by a llama.cpp server (`llama serve --port 8080`), the default port of llama.cpp and many dev servers. Strudel hardcodes the port, so the only fix on the user's side is to stop the other server. nanodirt must name the process holding the port when a bind fails.

- **Tidal refuses some bus params on its own side.** For example, `speedbus` raises "Control parameter 'speed' can't be sent to a bus" (`Params.hs:3309`). This needs no handling in nanodirt.

- **Strudel timestamps are correct.** Receive time plus skew lands on an exact 0.5 s grid (for example 5.311, 5.811, 6.312, 6.812 s). The suspected clock offset (`clockbridge.mjs`) is not present: it would be seconds, not tenths. Strudel's events arrive about 0.19 s before the 0.1 s latency budget implies, probably the scheduler's lookahead (inference). The arrival jitter (0.23-0.34 s) comes from batching per scheduler tick and does not affect playback, because the engine schedules by timetag.

- **Late events need a policy.** One Chrome event arrived 2.97 s after its timetag. Probably it was queued while the permission prompt was open (inference). The engine plays a past timetag immediately, which is audible as a stray hit. nanodirt should drop events later than a threshold (for example 50 ms) and log a count.

## Still to verify: Strudel in a browser

This needs a person at a browser. It checks two things: Local Network Access and Safari policy for `ws://localhost` from an https page, and the suspected early-timestamp offset.

```bash
cd ~/projects/personal/nanodirt/spike
uv run --with nanosynth==0.4.1 --with websockets python spike.py
```

Then:

1. Open https://strudel.cc in Safari, and in Chrome and Firefox if available.

2. Settings: set the audio engine target to OSC.

3. Evaluate `s("bd*2 [sn hh]")`.

In the spike log, look for:

- `ws connected, origin=https://strudel.cc`. If it is absent, the browser blocked the connection. Note any permission prompt.

- The `skew ws:` line on Ctrl-C. About 0.1 s early is correct. Much larger or negative values confirm the clock-offset suspicion (`superdough/clockbridge.mjs:21-44`).
