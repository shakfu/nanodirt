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

## Plan

- Fetch presets on demand into `~/.cache/nanodirt`, from the URL Strudel uses, as for the Strudel sample maps. Every `n` then sounds as on strudel.cc. nanodirt redistributes nothing; the user takes JCLive's terms, as on strudel.cc.

- Never ship preset data in the wheel or an sdist. nanodirt would then distribute JCLive's non-commercial and E-MU material.

- Optional: a flag (e.g. `--free-soundfonts`) that remaps each instrument to its FluidR3 or GeneralUser GS presets. This changes the `n=0` sound for 103 instruments. Bundling those presets requires shipping FluidR3's notice and `COPYING`.

- Decoding needs MP3, which nanosynth's libsndfile lacks (nanosynth `TODO.md`). Options: ffmpeg, as for the `piano` map; an MP3 decoder vendored in nanodirt (e.g. minimp3), writing WAV to the cache; or MPEG enabled in nanosynth's libsndfile (mpg123), which lets scsynth read MP3 files itself. The presets hold MP3 in memory, so they need decoding in nanodirt unless written out as `.mp3` files first.
