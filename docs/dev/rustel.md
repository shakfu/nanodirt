# rustel

Comparison with [tzfm/rustel](https://github.com/tzfm/rustel) at `88156f8` (2026-11-11). Read from its docs and source; not built or run.

## Scope

rustel replaces Strudel's browser runtime: a Rust pattern engine, QuickJS for score evaluation, Rust DSP and a terminal studio. nanodirt replaces SuperDirt and keeps the front ends (Strudel web, Tidal, vortex).

| | rustel | nanodirt + nanosynth |
|-|-|-|
| Replaces | Strudel's pattern engine, superdough, editor | SuperDirt (sclang + scsynth) |
| Front ends | Strudel only: its studio, or any editor with `--watch` | Strudel web, Tidal, vortex |
| Pattern engine | Rewritten in Rust; QuickJS evaluates the score | Unchanged, in the front end |
| DSP | rustel's own Rust code | scsynth; SynthDefs translated from SuperDirt and superdough |
| Sound target | strudel.cc, Firefox as reference | superdough in headless Chrome (Strudel); stock SuperDirt (Tidal) |
| Parity tests | Goldens against rustel's own earlier output; browser comparison done separately by maintainers (`CONTRIBUTING.md`, "What the goldens prove") | Automated against the superdough oracle and stock SuperDirt, often sample by sample |
| `gm_` soundfonts | Fetched from the same `webaudiofontdata` URL | The same (`soundfonts.md`) |
| MP3 | symphonia (pure Rust) | minimp3 (C, vendored) |
| Extras | VST3, Csound, Hydra, scope and piano roll, MIDI, OSC, serial, Ableton Link, WAV/MP3 export, session tapes | MIDI and NRT rendering (nanosynth) |
| Licence | AGPL-3.0-or-later | GPL-3.0-or-later (nanodirt), MIT (nanosynth) |
| Distribution | `rustelup` installer; crates not on crates.io | PyPI |
| Size | About 715k lines of Rust; created 2026-09-17; 2 contributors; v0.1.6 | About 5.2k lines (nanodirt) |

## Strudel coverage

rustel's source mentions most items in `gaps.md`, "Strudel": FM, ducking, `supersaw`, `pulse`, `vib`, `penv`, the ladder filter, `distorttype`, `coarse`, `vowel`, `compressor`, `tremolo`, `wt_` and `gm_`. This was found by grep; it shows the names are handled, not that they match superdough.

rustel's own list of limits (`docs/compatibility.md`):

- No audio effect: `chorus`, `gate`, `overshape`, `sustainpedal`, the pan spread controls.

- Errors: `getDuration`.

- Unsupported: the `mondo` and `tidal` template tags, MQTT, the browser DOM, ES module imports.

Its docs say sample-rate conversion and pitch playback "still need validation" against the browser (`docs/overview.md`, "Sound fidelity").

## Where nanodirt differs

- Tidal and vortex. rustel supports neither.

- Strudel users who keep the strudel.cc editor.

- A SuperDirt receiver without sclang. rustel's `.osc()` sends `/dirt/play` to `127.0.0.1:57120`, and its docs tell users to install SuperCollider and SuperDirt for it (`docs/hardware.md`). nanodirt can take that role. Untested; it probably needs `--strudel`, since rustel sends Strudel field names over UDP (inference).

- Parity checked in the test suite against independent references.

## Open questions

Undecided unless marked. Each lists the case for and against.

### Headless Strudel (`strudel-headless.md`)

Decided 2026-11-11: nanodirt implements headless Strudel, QuickJS included (`strudel-headless.md`); rustel does not change that. The plan runs upstream Strudel's JavaScript in QuickJS, so semantics follow upstream rather than a port.

### Scope of the Strudel path

- For narrowing to strudel.cc users: users who want no browser have rustel.

- Against: strudel.cc users still write the rare controls. The work is already ordered by usage, so narrowing changes little.

### rustel as a client

- For testing rustel `.osc()` against nanodirt: it replaces SuperCollider + SuperDirt in rustel's documented OSC setup.

- Against: rustel plays Strudel sounds natively. Its users need SuperDirt only for SuperDirt synths, which 0% of sampled Strudel repos use (`strudel-usage.md`). Demand is unknown.

### Reference browser

- For Chrome (current oracle): likely the browser most strudel.cc users run (inference, no usage data).

- For Firefox: rustel's reference, so the two ports could be compared directly.

- The browsers differ at least in measured detail: Chrome's feedback delay adds one 128-frame quantum per echo. Firefox's behaviour there is not measured.

## Notes

- Licence for reuse. nanodirt (GPL-3) can combine with AGPL-3 code, but the result carries AGPL's network-use terms. nanosynth (MIT) cannot take rustel code. Facts are free to use either way, such as the corrected bass filenames in `crates/runtime/assets/gm-fonts.json`.

- rustel also fetches soundfonts on demand and decodes MP3 natively, as nanodirt does.
