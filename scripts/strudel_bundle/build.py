"""Build nanodirt's Strudel bundle from a pinned Strudel revision.

usage: python scripts/strudel_bundle/build.py

Clones Strudel at `REVISION` into the cache (the checkout the superdough oracle
uses), installs the bundler, bundles the pattern packages with `harness.mjs`
into `src/nanodirt/js/strudel.js` with its third-party licences in `LICENSES/`, and records Node's events for Strudel's tunes
in `tests/fixtures/strudel_tunes.json`. Needs git and Node. Strudel and the
bundle are AGPL-3.0-or-later; this directory is the bundle's source.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REVISION = "a3302cf"
REPO = "https://codeberg.org/uzu/strudel.git"
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CACHE = Path(os.environ.get("NANODIRT_CACHE", Path.home() / ".cache" / "nanodirt"))
STRUDEL = CACHE / "superdough" / "strudel"
BUNDLE = ROOT / "src" / "nanodirt" / "js" / "strudel.js"
FIXTURE = ROOT / "tests" / "fixtures" / "strudel_tunes.json"
NOTICES = ROOT / "LICENSES" / "strudel-bundle-third-party.txt"
CYCLES = 4


def main() -> None:
    if not (STRUDEL / ".git").is_dir():
        subprocess.run(
            ["git", "clone", "--filter=blob:none", "--no-checkout", REPO, str(STRUDEL)], check=True
        )
    subprocess.run(["git", "-C", str(STRUDEL), "checkout", "-q", REVISION], check=True)
    subprocess.run(
        ["npm", "install", "--no-audit", "--no-fund", "--ignore-scripts"], cwd=HERE, check=True
    )
    BUNDLE.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["node", "build.mjs", str(STRUDEL), str(BUNDLE), str(NOTICES)], cwd=HERE, check=True
    )
    header = f"/* Strudel {REVISION} ({REPO}), built by scripts/strudel_bundle. AGPL-3.0-or-later. */\n"
    BUNDLE.write_text(header + BUNDLE.read_text())
    subprocess.run(
        ["node", "node_events.mjs", str(STRUDEL), str(BUNDLE), str(FIXTURE), str(CYCLES)],
        cwd=HERE,
        check=True,
    )


if __name__ == "__main__":
    main()
