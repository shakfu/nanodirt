# Strudel usage sample

Measured 2026-10-27, to scope M5 (superdough-only controls). Question: which superdough sound sources and controls does Strudel code use, and which does nanodirt lack?

## Data

| Corpus | Units | Source |
|-|-|-|
| GitHub | 2,626 files in 136 repos | Every `.strudel` file GitHub code search returned, deduplicated by blob SHA |
| Tunes | 53 | `website/src/repl/tunes.mjs` and `test/testtunes.mjs` at Strudel `a3302cf`, deduplicated |
| Teaching | 242 | Code in `website/src/pages/{workshop,recipes,understand,intro}/*.mdx` at `a3302cf` |

GitHub collection:

- Code search returns at most 1,000 results per query, so queries were split by file size into 7 buckets, each under the cap.

- 2,751 paths were collected and downloaded; 2,688 have distinct contents; 2,626 contain pattern calls.

- The search API's `total_count` is approximate: the buckets sum to 2,990, while the unsplit query reports 2,560. Coverage of the index cannot be stated exactly.

- Code search indexes default branches only and skips most forks.

Files are concentrated in a few repos:

| Repo | Files | Content |
|-|-|-|
| `tzfm/rustel` | 1,446 | Test scores, one per Strudel jsdoc example |
| `nukleas/cycletron` | 185 | Pattern corpus |
| `calv-io-n/strudel-studio` | 124 | Song sets |
| `jan-oboth/strudel` | 59 | Songs |
| `MrityunjayBhardwaj/Stave` | 57 | Parser test corpus |

Counts are distinct files with pattern calls. 54 repos hold one file. Per-file shares therefore describe `tzfm/rustel`, so the unit of analysis is the repo.

## Method

- **Repo-weighted share:** for each repo, the fraction of its files that use a feature, averaged over repos. Each repo weighs the same.

- **Interval:** 95% bootstrap over repos (2,000 resamples). It covers sampling noise only, not selection bias.

- **Sensitivity:** recomputed without repos whose names suggest generated code (`agent`, `gpt`, `vibe`, `harness`, ...; 5 repos) and without editor or tool repos (`vim`, `vscode`, `cli`, `server`, ...; 11 more). No share of 3% or more moves by more than 2.5 points.

- **Controls:** every `registerControl` and `registerMultiControl` call in `packages/core/controls.mjs`, aliases resolved, FM numbered variants folded. 247 of the 277 are audio controls; the rest are tagged `tonal`, `midi`, `external_io` or `visualization`, or are display controls (`color`, `label`, ...).

- **A use** is a call `name(` or `.name(` outside a `//` comment. Regex over text, not parsed JavaScript.

- **Sound sources:** tokens inside the string passed to `s(...)` or `sound(...)`.

- **Handled by nanodirt:** the name is an event field `dirt.py`, `strudel.py` or `effects.py` reads, or a control of a nanodirt SynthDef. A known name; the semantics may still differ from superdough.

Limits:

- Public GitHub is a small, self-selected part of Strudel use. Most users write on strudel.cc and commit nothing.

- The reference docs (`learn/*.mdx`) are excluded from the Teaching corpus: they show every control by design. `tzfm/rustel` contains the same material but counts as one repo.

## Reproducing

```bash
python scripts/strudel_usage_fetch.py <github dir>        # needs an authenticated gh; about 10 minutes
uv run python scripts/strudel_usage.py <strudel checkout> <github dir>
```

`<strudel checkout>` is Strudel at `a3302cf`. The GitHub results change as files are added or removed, so a rerun gives different counts.

## Results

### Sound sources

| Source | Repo-weighted (95% CI) | nanodirt |
|-|-|-|
| Sample banks (`bd`, `hh`, `piano`, ...) | 79% (74-85%) | Plays |
| superdough oscillators (`sawtooth`, `sine`, `triangle`, `square`, `saw`, `pulse`, `supersaw`) | 61% (54-68%) | Silent: unknown sound |
| `gm_` soundfonts | 28% (22-35%) | Silent: unknown sound |
| superdough noise (`white`, `pink`, `brown`, `crackle`) | 11% (7-16%) | Silent: unknown sound |
| `wt_` wavetables | 3% (1-6%) | Silent: unknown sound |
| SuperDirt synths (`super*`, `sos*`) | 0% (0-0%) | Plays |

`supersaw` plays on nanodirt, but as SuperDirt's synth, which differs from superdough's.

### Unhandled audio controls

107 unhandled audio controls appear in some file; 58 in at least two repos. Those at 3% or more:

| Control | Repo-weighted (95% CI) | Repos using | Tunes | Teaching |
|-|-|-|-|-|
| `lpenv` | 12% (8-17%) | 34 | 7 | 4 |
| `fmi` (`fm`) | 12% (8-17%) | 31 | 0 | 0 |
| `duckorbit` | 10% (6-15%) | 23 | 0 | 0 |
| `postgain` | 8% (5-13%) | 22 | 0 | 0 |
| `duckdepth` | 7% (3-11%) | 17 | 0 | 0 |
| `lpdecay` | 7% (3-11%) | 20 | 0 | 1 |
| `lpattack` | 6% (3-10%) | 17 | 7 | 3 |
| `fmh` | 6% (3-10%) | 17 | 0 | 0 |
| `duckattack` | 6% (3-10%) | 16 | 0 | 0 |
| `lpsustain` | 5% (2-9%) | 15 | 0 | 0 |
| `vib` | 4% (2-8%) | 14 | 1 | 1 |
| `penv` | 4% (1-7%) | 14 | 0 | 0 |
| `pdecay` | 4% (1-7%) | 14 | 0 | 0 |
| `compressor` | 4% (1-7%) | 9 | 0 | 0 |
| `unison` | 4% (1-7%) | 12 | 0 | 0 |

### Shared controls

Handled names whose superdough and SuperDirt semantics differ (TODO M5), repo-weighted: `gain` 77% (71-83%), `cutoff` 66% (60-73%), `room` 58% (51-66%), `delay` 42% (35-49%), `release` 33%, `attack` 32%, `decay` 27%, `sustain` 23% (dropped by `normalize`), `resonance` 23%, `distort` 12%.

## Implications

- Sound sources matter more than controls. superdough oscillators appear in 61% of a typical repo's files and are silent on nanodirt. The largest control gap, `lpenv`, is at 12%.

- superdough oscillators have no SuperDirt counterpart, so their ADSR (`attack`, `decay`, `sustain`, `release`) must follow superdough. For these sources, the M5 semantics decision is already made.

- 15 controls, in five groups (filter envelope, FM, ducking, pitch envelope, `postgain`/`vib`/`compressor`/`unison`), cover every unhandled control at 3% or more. Specifying all 167 is not needed for the first version.

- `gm_` soundfonts come from a separate package (`packages/soundfonts`), not from the sample maps nanodirt loads. Their data source and licence need checking before planning.
