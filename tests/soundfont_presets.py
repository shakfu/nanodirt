"""Synthetic webaudiofont presets for tests, in the format of webaudiofontdata's files.

The zones wrap tests/fixtures/tone_{mono,stereo}.mp3: 13230 frames (0.3 s at 44.1 kHz) of
220 Hz (left) and 330 Hz (right) with two overtones, encoded by LAME at 64 kb/s. No
soundfont data is used. The preset files take the names Strudel's gm.mjs gives to
``gm_piano``'s first two presets, so superdough's ``registerSoundfonts`` loads them.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"
TONE_FRAMES = 13230


def tone(channels: int) -> str:
    name = "tone_mono.mp3" if channels == 1 else "tone_stereo.mp3"
    return base64.b64encode((FIXTURES / name).read_bytes()).decode()


def _js(v: Any) -> str:
    return (
        ("true" if v else "false")
        if isinstance(v, bool)
        else f"'{v}'"
        if isinstance(v, str)
        else repr(v)
    )


def preset_text(name: str, zones: list[dict[str, Any]]) -> str:
    """A preset file as webaudiofontdata writes it: unquoted keys, a comment per zone."""
    body = ",".join(
        "\n\t\t{\n" + "".join(f"\t\t\t,{k}:{_js(v)}\n" for k, v in z.items()).replace(",", "", 1)
        + "\t\t\t//_tone.synthetic\n\t\t}"
        for z in zones
    )  # fmt: skip
    return f"console.log('load _tone_{name}');\nvar _tone_{name}={{\n\tzones:[{body}\n\t]\n}};\n"


def zone(low: int, high: int, pitch: int, channels: int = 1, **kw: Any) -> dict[str, Any]:
    z = {
        "midi": 0, "originalPitch": pitch, "keyRangeLow": low, "keyRangeHigh": high,
        "loopStart": 0, "loopEnd": 0, "coarseTune": 0, "fineTune": 0, "sampleRate": 44100,
        "ahdsr": True, "file": tone(channels), "anchor": 0.0,
    }  # fmt: skip
    z.update(kw)
    return z


# gm_piano n=0: a looping mono zone below c4 (a3 at the original pitch), a stereo zone
# with coarse and fine tuning above, no loop. n=1: loop points at another sample rate.
PRESETS = {
    "0000_JCLive_sf2_file": [
        zone(0, 59, 5700, loopStart=4410, loopEnd=8820),
        zone(60, 127, 6400, channels=2, coarseTune=1, fineTune=30),
    ],
    "0000_FluidR3_GM_sf2_file": [
        zone(0, 127, 5700, loopStart=1000, loopEnd=3000, sampleRate=22050)
    ],
}


def write_presets(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name, zones in PRESETS.items():
        (directory / f"{name}.js").write_text(preset_text(name, zones), encoding="utf-8")
    return directory
