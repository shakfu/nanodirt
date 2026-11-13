"""How many Strudel tunes and teaching snippets touch a gap in docs/dev/gaps.md.

usage: uv run python scripts/strudel_gap_coverage.py <strudel checkout>

<strudel checkout> is Strudel at `a3302cf`. Prints the coverage table in
docs/dev/gaps.md. Code is read as text with strudel_usage.py's regexes; a
control named at runtime is missed. Update GAPS when gaps.md changes.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from strudel_usage import features, strudel_controls, strudel_units  # noqa: E402

GAPS = {  # gap: controls (main names) that use it
    "duck": r"duck.*",
    "compressor": r"compressor.*",
    "unison": r"unison|detune|spread",
    "noise mix": r"noise",
    "shape/crush/coarse": r"shape|crush|coarse",
    "vowel": r"vowel",
    "tremolo/phaser": r"tremolo.*|phaser.*",
    "ir": r"ir|iresponse",
    "djf": r"djf",
    "filter lfo": r"lp?(rate|depth|shape|sync|dc)|lfo.*",
}


def gaps(code: str, alias: dict[str, str]) -> set[str]:
    used = features(code, alias)
    controls = {u[4:] for u in used if u.startswith("ctl:")}
    out = {g for g, rx in GAPS.items() if any(re.fullmatch(rx, c) for c in controls)}
    if "src:wt_wavetable" in used:
        out.add("wt_")
    if re.search(r"""\b(?:s|sound)\(\s*["'`][^"'`]*\b(pulse|supersaw)\b""", code):
        out.add("pulse/supersaw")
    if re.search(r"""\bftype\(\s*["'`]?(1|ladder)""", code):
        out.add("ladder")
    return out


def main(root: Path) -> None:
    alias, _ = strudel_controls(root)
    for kind, codes in strudel_units(root).items():
        hits: Counter[str] = Counter()
        clean = 0
        for code in codes:
            found = gaps(code, alias)
            hits.update(found)
            clean += not found
        print(f"{kind}: {len(codes)} units, {clean} ({100 * clean / len(codes):.0f}%) touch no gap")
        for gap, n in hits.most_common():
            print(f"  {gap:20} {n}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
