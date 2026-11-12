# `gm_` soundfonts

Data source, licences and the recommended plan for Strudel's `gm_` sounds. Checked 2026-11-11 against Strudel `a3302cf`. Not legal advice: the copyright fields below are what each font's author wrote into the file.

## Data source

- `packages/soundfonts/fontloader.mjs:14` fetches `https://felixroos.github.io/webaudiofontdata/sound/<preset>.js`. That repo is a fork of [surikov/webaudiofontdata](https://github.com/surikov/webaudiofontdata).

- `gm.mjs` maps 125 `gm_` instruments to lists of presets. `n` indexes the list; `n=0` plays the first preset.

- A preset is a JavaScript file. Each zone holds a key range, loop points, tuning and base64-encoded MP3 (`ID3` header). Strudel evaluates the file with `new Function`. nanodirt should parse it as data.

- The data repo has an MIT licence, and its README cites licences only for GeneralUser GS and FluidR3. The MIT licence covers the repo; it cannot relicense the samples.

- The player library [surikov/webaudiofont](https://github.com/surikov/webaudiofont) is GPL-3 since 2020-01-16 ([#57](https://github.com/surikov/webaudiofont/issues/57)). nanodirt does not use it.

## Licences of the source fonts

Read from the INFO chunk of each original file in `surikov/webaudiofontdata/sf2/`. Presets and defaults are counted in `gm.mjs`.

| Font | Presets | Default (`n=0`) for | INFO copyright and comment |
|-|-|-|-|
| JCLive 2.1 (Park Tae-seong) | 123 | 66 | Commercial use prohibited; "for listening to and enjoying MIDI" only. Copyright: Park, Lee Kwon-hyung (Aspirin), Jang Seung-geol (FantaGM32), E-MU |
| Aspirin 160 GM GS (1998) | 175 | 37 | "Public Domain"; JCLive's comment says Aspirin's author holds copyright |
| FluidR3 GM (Frank Wen) | 211 | 19 | [MIT](https://github.com/musescore/MuseScore/blob/master/share/sound/FluidR3Mono_License.md); copies must include the notice |
| Chaos V20 | 101 | 2 | "Public Domain"; author unknown |
| GeneralUser GS (S. Christian Collins) | 236 | 0 | [Licence v2.0](https://github.com/mrbumpy409/GeneralUser-GS): free use, including in software, modification allowed; sample origins not fully certain |
| Acoustic Guitar | 3 | 1 | No copyright field |
| LesPaul | 5 | 0 | "2000 Sonic Network, Inc." |
| Soul Ahhs | 3 | 0 | "E-mu Systems, Inc., 1998" |
| SBAWE32, Stratocaster, LK-AcousticSteel | 11 | 0 | "Copyright Information Not Present" |

JCLive's comment is Korean (CP949) in the file. 122 of 125 instruments have a FluidR3 or GeneralUser GS preset; `gm_fretless_bass`, `gm_bagpipe` and `gm_tinkle_bell` have none.

## Implementation

Done 2026-11-12 (`soundfonts.py`, `csrc/_mp3.c`).

- Presets are fetched on demand into `~/.cache/nanodirt/strudel/soundfonts`, from the URL Strudel uses. Every `n` sounds as on strudel.cc; the user takes JCLive's terms, as there. Only the zone a note needs is decoded, to a float WAV beside the preset.

- No preset data ships in the wheel or the sdist. Tests use synthetic presets (`tests/soundfont_presets.py`) around two 0.3 s tones nanodirt encoded itself.

- A preset is parsed as data: objects, arrays, numbers, strings and booleans. Anything else rejects the file, which is then neither cached nor retried.

- MP3 decodes with minimp3 (`thirdparty/minimp3`, CC0, pinned at `ea99364`), over ffmpeg, which many users lack, and over MPEG in nanosynth's libsndfile, which needs mpg123 and lame vendored. minimp3 trims the encoder delay and padding recorded in the LAME tag; its output equals ffmpeg's within 1.2e-6.

- Not done: `vib` and the pitch envelope (`gaps.md`); a flag remapping each instrument to its FluidR3 or GeneralUser GS presets. The remap would change the `n=0` sound for 103 instruments, and bundling those presets requires FluidR3's notice and `COPYING`.

## Differences from superdough

Checked against the superdough oracle with Strudel's own `fontloader.mjs` (`tests/test_dough.py`, `test_soundfont_matches_sample_by_sample`): loops, transposition, tuning, stereo zones, loop points at another sample rate, envelopes and filters match within 0.3% of the peak.

- `getBuffer` copies the decoded base64 into an `ArrayBuffer` sized by the base64 string, 4/3 of the MP3. Chrome's decoder then keeps LAME's end padding: 13871 frames, not 13230, for the test tone. After a zone that does not loop, superdough plays that padding, up to about 1680 frames (38 ms at 44.1 kHz) of encoder output; nanodirt stops at the encoded length.

- The first note of each zone is silent while nanodirt fetches, parses and decodes it, even when the preset is cached on disk. superdough awaits the load and skips the note only if it is then late.
