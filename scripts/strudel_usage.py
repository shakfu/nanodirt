"""Which superdough sources and controls Strudel code uses, and which nanodirt lacks.

usage: uv run python scripts/strudel_usage.py <strudel checkout> <github dir>

<strudel checkout> is Strudel at `a3302cf`; <github dir> is the output of
`strudel_usage_fetch.py`. Prints the tables in docs/dev/strudel-usage.md.
All inputs are read as text; nothing from them is executed.
"""

from __future__ import annotations

import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

from nanodirt.effects import effect_synthdefs
from nanodirt.instruments import default_instruments
from nanodirt.synthdefs import core_synthdefs

NANODIRT = Path(__file__).resolve().parent.parent / "src" / "nanodirt"
NON_AUDIO_TAGS = {"tonal", "midi", "external_io", "visualization"}
NON_AUDIO = {"color", "label", "activeLabel", "analyze", "fft"}
OSC = {"sawtooth", "sine", "square", "triangle", "tri", "saw", "sqr", "sin", "pulse", "supersaw"}
OSC |= {"z_" + x for x in ("sawtooth", "sine", "square", "triangle")}
# Repo-name filters for the sensitivity check.
AI = re.compile(r"agent|\bai\b|-ai|ai-|gpt|llm|vibe|harness|mcp|claude|copilot", re.I)
TOOL = re.compile(
    r"vim|vscode|zed|tui|cli|lsp|\bls\b|-ls|server|docker|node|plugin|editor|extension", re.I
)
SHARED = [
    "gain",
    "cutoff",
    "room",
    "delay",
    "release",
    "attack",
    "decay",
    "sustain",
    "resonance",
    "distort",
]
CALL = re.compile(r"(?<![\w$])\.?([A-Za-z_]\w*)\s*\(")

Repos = dict[str, list[set[str]]]


def nanodirt_vocab() -> set[str]:
    """Event fields nanodirt reads, plus every nanodirt SynthDef control name."""
    names = {"orbit", "clip", "duration", "channels", "cps", "cycle", "delta"}
    names.add("roomsize")  # Strudel's osc.mjs sends it as size
    for f in ["dirt.py", "strudel.py", "effects.py"]:
        text = (NANODIRT / f).read_text()
        names |= set(re.findall(r'e\["([A-Za-z0-9_]+)"\]', text))
        names |= set(re.findall(r'"([a-z][A-Za-z0-9]*)": _', text))
        for args in re.findall(r"_has\(([^)]*)\)", text):
            names |= set(re.findall(r'"([A-Za-z0-9_]+)"', args))
    for d in core_synthdefs(2) + default_instruments(2) + effect_synthdefs(2):
        names |= set(d.parameters)
    return names


def strudel_controls(root: Path) -> tuple[dict[str, str], set[str]]:
    """(alias -> main name, audio main names) from packages/core/controls.mjs."""
    src = (root / "packages/core/controls.mjs").read_text()
    tags: dict[str, set[str]] = {}
    for block in re.findall(r"/\*\*.*?\*/", src, flags=re.S):
        name, tag = re.search(r"@name\s+(\w+)", block), re.search(r"@tags\s+([^\n]*)", block)
        if name and tag:
            tags[name.group(1)] = {t.strip() for t in tag.group(1).split(",")}
    src = re.sub(r"/\*\*.*?\*/", "", src, flags=re.S)  # jsdoc @examples are not uses
    alias: dict[str, str] = {}

    def strs(s: str) -> list[str]:
        return re.findall(r"'([^']*)'", s)

    def reg(names: list[str], aliases: list[str]) -> None:
        m = re.fullmatch(r"(fm[a-z]*?)(\d+)", names[0])  # fold fmi3, fmenv2, fmi12, ...
        main = m.group(1) + "N" if m else names[0]
        for a in [names[0], *aliases]:
            alias[a] = main

    for m in re.finditer(
        r"registerControl\(\s*(\[[^\]]*\]|'[^']*')\s*((?:,\s*'[^']*'\s*)*)\)", src
    ):
        reg(strs(m.group(1)), strs(m.group(2)))
    multi = r"registerMultiControl\(\s*(\[[^\]]*\]|'[^']*')\s*,\s*(\d+)\s*((?:,\s*'[^']*'\s*)*)\)"
    for m in re.finditer(multi, src):  # as registerMultiControl expands them
        names, n, aliases = strs(m.group(1)), int(m.group(2)), strs(m.group(3))
        reg(names, aliases + [a + "1" for a in aliases] + [x + "1" for x in names])
        for i in range(2, n + 1):
            reg([x + str(i) for x in names], [a + str(i) for a in aliases])
    for i in range(9):  # the FM matrix loop
        for j in range(9):
            reg([f"fmi{i}{j}"], [f"fm{i}{j}"])
    audio = {
        m for m in alias.values() if not (tags.get(m, set()) & NON_AUDIO_TAGS or m in NON_AUDIO)
    }
    return alias, audio


def source_kind(tok: str) -> str:
    if tok in OSC:
        return "oscillator"
    if tok in {"white", "pink", "brown", "crackle"}:
        return "noise"
    if tok.startswith("gm_"):
        return "gm_soundfont"
    if tok.startswith("wt_"):
        return "wt_wavetable"
    if re.fullmatch(r"super\w+|sos\w+", tok):
        return "superdirt_synth"
    return "sample_or_other"


def features(text: str, alias: dict[str, str]) -> set[str]:
    """Controls (`ctl:`) and sound sources (`src:`) one unit of code uses."""
    text = re.sub(r"//[^\n]*", "", text)
    used = {"ctl:" + alias[n] for n in CALL.findall(text) if n in alias}
    for m in re.finditer(r"""\b(?:s|sound)\s*\(\s*(["'`])(.*?)\1""", text, flags=re.S):
        used |= {"src:" + source_kind(t) for t in re.findall(r"[A-Za-z_]\w*", m.group(2))}
    return used


def strudel_units(root: Path) -> dict[str, list[str]]:
    """Tunes and teaching snippets from the Strudel checkout."""
    tunes: dict[str, str] = {}
    for f in ["test/testtunes.mjs", "website/src/repl/tunes.mjs"]:
        tunes |= dict(
            re.findall(r"export const (\w+)\s*=\s*`(.*?)`;", (root / f).read_text(), flags=re.S)
        )
    pages = root / "website/src/pages"
    teaching = []
    for d in ["workshop", "recipes", "understand", "intro"]:
        for p in sorted((pages / d).glob("*.mdx")):
            t = p.read_text()
            teaching += re.findall(r"tune=\{`(.*?)`\}", t, flags=re.S)
            teaching += re.findall(r"```\w*\n(.*?)```", t, flags=re.S)
    return {"tunes": list(dict.fromkeys(tunes.values())), "teaching": teaching}


def github_repos(gh: Path, alias: dict[str, str]) -> Repos:
    """Feature sets per repo, one per distinct file containing pattern calls."""
    index = json.loads((gh / "index.json").read_text())
    repos: Repos = defaultdict(list)
    seen: set[str] = set()
    for row in index["rows"]:
        if "file" not in row or row["sha"] in seen:
            continue
        seen.add(row["sha"])
        text = (gh / row["file"]).read_text(errors="replace")
        if re.search(r"\b(?:s|sound|note|n|stack|samples)\s*\(", text):
            repos[row["repo"]].append(features(text, alias))
    return dict(repos)


def weighted(repos: Repos, feat: str) -> float:
    """Mean over repos of the fraction of the repo's files using *feat*."""
    xs = [sum(feat in f for f in fs) / len(fs) for fs in repos.values()]
    return sum(xs) / len(xs)


def interval(repos: Repos, feat: str, n: int = 2000) -> tuple[float, float]:
    """95% bootstrap interval for `weighted`, resampling repos."""
    rng = random.Random(0)
    xs = [sum(feat in f for f in fs) / len(fs) for fs in repos.values()]
    means = sorted(sum(rng.choice(xs) for _ in xs) / len(xs) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n)]


def pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def main(root: Path, gh: Path) -> None:
    vocab = nanodirt_vocab()
    alias, audio = strudel_controls(root)
    repos = github_repos(gh, alias)
    units = {k: [features(u, alias) for u in v] for k, v in strudel_units(root).items()}
    no_ai = {r: fs for r, fs in repos.items() if not AI.search(r)}
    no_ai_tool = {r: fs for r, fs in no_ai.items() if not TOOL.search(r)}
    feats = sorted({x for fs in repos.values() for f in fs for x in f})

    def cell(feat: str) -> str:
        lo, hi = interval(repos, feat)
        return f"{pct(weighted(repos, feat))} ({100 * lo:.0f}-{100 * hi:.0f}%)"

    files = sorted((len(fs), r) for r, fs in repos.items())
    print(
        f"files {sum(n for n, _ in files)}, repos {len(repos)}, controls {len(set(alias.values()))}, audio {len(audio)}"
    )
    print("largest repos:", ", ".join(f"{r} {n}" for n, r in files[::-1][:5]))
    print(f"repos with one file: {sum(n == 1 for n, _ in files)}")
    print(
        f"sensitivity: without AI-named {len(no_ai)} repos, also without tool-named {len(no_ai_tool)}"
    )
    shift = max(
        abs(weighted(g, f) - weighted(repos, f)) for g in (no_ai, no_ai_tool) for f in feats
        if weighted(repos, f) >= 0.03
    )  # fmt: skip
    print(f"largest shift for features at 3% or more: {100 * shift:.1f} points\n")

    print("| Source | Repo-weighted (95% CI) |\n|-|-|")
    for f in sorted((f for f in feats if f.startswith("src:")), key=lambda f: -weighted(repos, f)):
        print(f"| {f[4:]} | {cell(f)} |")

    unhandled = [f for f in feats if f.startswith("ctl:") and f[4:] in audio and f[4:] not in vocab]
    in_two = sum(sum(any(f in x for x in fs) for fs in repos.values()) >= 2 for f in unhandled)
    print(f"\nunhandled audio controls used: {len(unhandled)}; in two or more repos: {in_two}\n")
    print("| Control | Repo-weighted (95% CI) | Repos using | Tunes | Teaching |\n|-|-|-|-|-|")
    for f in sorted(unhandled, key=lambda f: -weighted(repos, f)):
        if weighted(repos, f) < 0.03:
            break
        using = sum(any(f in x for x in fs) for fs in repos.values())
        t, w = (sum(f in u for u in units[k]) for k in ("tunes", "teaching"))
        print(f"| `{f[4:]}` | {cell(f)} | {using} | {t} | {w} |")

    print("\nshared:", ", ".join(f"`{c}` {cell('ctl:' + c)}" for c in SHARED))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
