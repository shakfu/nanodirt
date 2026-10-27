# Status

As of 2026-10-18. Read this first when resuming.

## Goal

nanodirt replaces SuperCollider + SuperDirt for users of TidalCycles, Strudel and vortex. It reimplements SuperDirt's event model on nanosynth's embedded scsynth. It is GPL-3.0-or-later and depends on MIT nanosynth: a translation of SuperDirt (GPL-2.0-or-later) inherits the GPL. Licences and packaging: nanosynth `docs/dev/nanodirt-integration.md`.

## Documents

| File | Content |
|-|-|
| `docs/dev/integration.md` | Design: core plus per-front-end adapters, ports, CLI, order of work |
| `docs/dev/superdirt-parameter-mapping.md` | Reference spec: SuperDirt event pipeline, fields, node/bus layout |
| `docs/dev/superdough-mapping.md` | Reference spec for the Strudel path: superdough's sources, envelope, gain, filters, orbit |
| `docs/dev/strudel-headless.md` | Scope: Strudel code in-process via a QuickJS wrapper in nanosynth |
| `docs/dev/spike-results.md` | What the spike proved, measured timings, findings |
| `docs/dev/strudel-usage.md` | Which superdough sources and controls Strudel code uses; scope for M5 |
| nanosynth `docs/dev/roadmap.md` | Requirements, milestones and status across both packages |
| nanosynth `docs/dev/livecoding.md` | Options considered and why nanodirt |

## Decisions

- SuperDirt is the reference semantics. Classic Dirt (uzu/dirt) is not a target.

- nanodirt is a separate GPL-3.0-or-later repo.

- SynthDefs are translated into the nanosynth DSL, not shipped as sclang-compiled binaries; each is checked against sclang's binary by rendered audio.

- Strudel: nanodirt serves the WebSocket on 8080 itself; no Node bridge.

- vortex: nanodirt ships a replacement `vortex.boot` module and registers it before starting vortex's own GUI or REPL.

- Semantics per front end (2026-10-27): Strudel events (WebSocket, or UDP with `--strudel`) follow superdough for all sources and controls; Tidal and vortex follow SuperDirt. Spec: `docs/dev/superdough-mapping.md`.

## Done

| Milestone | What | Checked by |
|-|-|-|
| Package | `pyproject.toml`, Makefile (`make test`, `lint`, `typecheck`), ruff, mypy (strict outside the SynthDef modules) | - |
| M1 | Sample library (lazy header + async read), event pipeline, cut groups, global effects with sleep/resume, core modules, UDP adapter with handshake, hello, setControlBus | `tests/test_event_parity.py`: 34 events give the same bundles as stock SuperDirt, bundle times within 5 ms. `tests/test_audio_parity.py`: a pattern recorded through both, RMS envelope correlation 0.9997, level ratio 0.999 |
| M2 | WebSocket adapter, Strudel field rules (`sustain`, `clip`, `duration`, `channels`), default sample maps and bank aliases, background download, pitched banks, ffmpeg transcoding of MP3 | `tests/test_dirt.py` (unit); live smoke test downloading `tr909` from the CDN |
| M3 | `nanodirt.vortex.boot`, `nanodirt vortex [--cli]` | `tests/test_vortex.py`: headless playback through `vortex_dsl()`; vortex.stream and pyliblo3 not imported; public-name check against vortex's own `boot.py` |
| M4 | 25 core and global SynthDefs, 43 instruments, 20 extra effect modules | `tests/test_synthdef_parity.py`, `test_instrument_parity.py`, `test_effect_parity.py`: rendered against sclang's compiled SynthDefs (`tests/fixtures/superdirt_defs/`), 1e-4 tolerance, or by level for graphs with random draws |
| M5 (part) | superdough synth sources on the Strudel path (`dough.py`, `Dirt.play_strudel`); superdough oracle | `tests/test_dough.py`: rules, offline level; with `NANODIRT_TEST_SUPERDOUGH=1`, rendered against superdough |

Behaviour found by the oracle and reproduced:

- sclang sends an absent (`nil`) value as int 0. `vowel` without `resonance` gets 0, `tremolorate` without `tremolodepth` gets depth 0, and an event without `delaytime` after one with it resets the delay time to 0.

- An unknown sound still plays: SuperDirt resumes the effects and sends the gate, without a source.

- SuperDirt's "do nothing" is `s` equal to the empty symbol, not a backslash.

## Not done

Pending work is tracked in nanosynth's `TODO.md`, section "Dirt-protocol backend" (items marked "(nanodirt)").

## Testing

```bash
make test                                  # unit, SynthDef parity (NRT), adapters
NANODIRT_TEST_ORACLE=1 make test           # plus event and audio parity against SuperDirt
NANODIRT_TEST_SUPERDOUGH=1 uv run --with playwright pytest   # plus Strudel-path parity against superdough
uv run --with "git+https://codeberg.org/uzu/vortex" pytest tests/test_vortex.py
```

The oracle runs stock SuperDirt on nanosynth's engine through headless sclang: `python scripts/superdirt_oracle.py setup` in nanosynth builds it under `~/.cache/nanodirt`.

## Environment

| Item | Location |
|-|-|
| Dirt-Samples | `~/.cache/nanodirt/Dirt-Samples` (`nanodirt samples fetch`) |
| Strudel maps and samples | `~/.cache/nanodirt/strudel` |
| sclang 3.14.1, sc3-plugins classes, SuperDirt `650a1d6`, Vowel | `~/.cache/nanodirt/src`, `~/.cache/nanodirt/quarks` |

Upstream revisions read: SuperDirt `650a1d6`, Tidal `5ae6552`, Strudel `a3302cf`, vortex `aefea84`, sc3-plugins `Version-3.14.1`.
