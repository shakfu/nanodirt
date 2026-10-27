"""Download every `.strudel` file GitHub code search returns, for `strudel_usage.py`.

usage: python scripts/strudel_usage_fetch.py <out dir>

Needs an authenticated `gh`. Code search returns at most 1,000 results per
query, so queries are split by file size into buckets under that cap. Writes
one `NNNNN.txt` per path and `index.json` (repo, path, blob SHA, file).
Method and results: docs/dev/strudel-usage.md.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

BUCKETS = ["<150", "150..299", "300..499", "500..800", "801..2000", "2001..5000", ">5000"]
PAUSE = 7.0  # code search allows 10 requests per minute


def search(bucket: str, page: int) -> dict[str, Any]:
    cmd = ["gh", "api", "-X", "GET", "search/code", "-f", f"q=extension:strudel size:{bucket}",
           "-f", "per_page=100", "-f", f"page={page}"]  # fmt: skip
    for _ in range(4):
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            return json.loads(r.stdout)  # type: ignore[no-any-return]
        print(f"retry {bucket} page {page}: {r.stderr[:120]}", file=sys.stderr)
        time.sleep(60)
    raise RuntimeError(f"search failed: {bucket} page {page}")


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    items: dict[tuple[str, str], str] = {}
    totals: dict[str, int] = {}
    for bucket in BUCKETS:
        for page in range(1, 11):
            d = search(bucket, page)
            totals[bucket] = d["total_count"]
            for it in d["items"]:
                items[(it["repository"]["full_name"], it["path"])] = it["sha"]
            time.sleep(PAUSE)
            if len(d["items"]) < 100:
                break
        print(f"{bucket}: total_count {totals[bucket]}, collected {len(items)}")

    rows: list[dict[str, str]] = [{"repo": r, "path": p, "sha": s} for (r, p), s in items.items()]

    def download(i: int, row: dict[str, str]) -> None:
        url = f"https://raw.githubusercontent.com/{row['repo']}/HEAD/{urllib.parse.quote(row['path'])}"
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                (out / f"{i:05d}.txt").write_bytes(resp.read(400_000))
            row["file"] = f"{i:05d}.txt"
        except OSError as exc:
            row["error"] = str(exc)[:80]

    with ThreadPoolExecutor(8) as ex:
        list(ex.map(download, range(len(rows)), rows))
    (out / "index.json").write_text(json.dumps({"totals": totals, "rows": rows}, indent=0))
    print(f"paths {len(rows)}, downloaded {sum('file' in r for r in rows)}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
