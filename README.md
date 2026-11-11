# nanodirt

SuperDirt's event model on [nanosynth](https://github.com/shakfu/nanosynth): a sound server for TidalCycles, Strudel and vortex, with no SuperCollider install.

```bash
pip install nanodirt
nanodirt samples fetch      # Dirt-Samples into ~/.cache/nanodirt
nanodirt                    # Tidal and Strudel: start this, then use the editor as usual
nanodirt vortex             # vortex in one process (needs vortex installed)
nanodirt render song.strudel --cycles 8   # Strudel code to WAV, without a browser
```

- **Tidal:** the stock `BootTidal.hs` works unchanged (UDP 57120, engine on 57110).

- **Strudel:** choose OSC output in strudel.cc's settings; nanodirt serves `ws://localhost:8080`. Strudel's default sample maps download on first use.

- **vortex:** `nanodirt vortex` replaces vortex's `boot` module, so patterns play in-process.

- **Strudel without a browser:** `nanodirt render` runs Strudel's own pattern engine in an embedded QuickJS and renders offline. From Python: `nanodirt.headless.Strudel` evaluates and queries code.

SynthDefs, modules and instruments are translated from SuperDirt `650a1d6` and checked against it; see `docs/dev/status.md`.

GPL-3.0-or-later. The bundled Strudel (`src/nanodirt/js/strudel.js`, built by `scripts/strudel_bundle/`) is AGPL-3.0-or-later; QuickJS and its binding are MIT. Texts in `LICENSES/`.
