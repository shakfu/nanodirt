"""Time Strudel evaluation and queries in QuickJS, per tune in tests/fixtures/strudel_tunes.json.

Decides how a realtime runner must handle a slow re-evaluation (docs/dev/strudel-headless.md):
if every evaluate fits inside the scheduler's lookahead, one runtime suffices.

    uv run python scripts/strudel_eval_timing.py [--repeats 3] [--window 0.05] [--at-cycle 0] [--per-tune]

Wall-clock times, in ms; a tune's figure is the median of its repeats.

- load: a new runtime with the bundle loaded.
- cold: the first evaluate in a new runtime.
- warm: evaluating the same code again in that runtime, as on re-evaluation after a save.
- query: the slowest single query over 4 cycles from --at-cycle, in windows of --window seconds.

Query bounds lie on a 1/1024-cycle grid, so runs are comparable with bundles built before
the fraction.js patch (scripts/strudel_bundle/farey.js), where a bound one ulp off a short
fraction made a query up to 250 times slower.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from nanodirt.headless import Strudel, StrudelError

FIXTURE = Path(__file__).parent.parent / "tests" / "fixtures" / "strudel_tunes.json"
CYCLES = 4
GRID = 1024  # query bounds are multiples of 1/GRID cycle, exact as floats


def ms(start: float) -> float:
    return (time.perf_counter() - start) * 1e3


def time_tune(code: str, window: float, at_cycle: int) -> dict[str, float]:
    t = time.perf_counter()
    strudel = Strudel()  # samples() calls are ignored: no network
    load = ms(t)
    t = time.perf_counter()
    strudel.evaluate(code)
    cold = ms(t)
    t = time.perf_counter()
    strudel.evaluate(code)
    warm = ms(t)
    cps = strudel.cps
    step = max(1, round(window * cps * GRID))  # window in grid units
    query = 0.0
    for k in range(at_cycle * GRID, (at_cycle + CYCLES) * GRID, step):
        t = time.perf_counter()
        strudel.query(k / GRID, (k + step) / GRID, cps)
        query = max(query, ms(t))
    return {"load": load, "cold": cold, "warm": warm, "query": query}


def percentile(xs: list[float], p: float) -> float:
    """Nearest-rank percentile."""
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, round(p / 100 * len(s) + 0.5) - 1))]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--window", type=float, default=0.05, help="query window, seconds")
    ap.add_argument("--at-cycle", type=int, default=0, help="first queried cycle")
    ap.add_argument("--per-tune", action="store_true", help="print each tune's figures")
    ap.add_argument("--json", type=Path, help="write per-tune figures here")
    args = ap.parse_args()

    tunes = json.loads(FIXTURE.read_text())
    results: dict[str, dict[str, float]] = {}
    failed: list[str] = []
    for name in sorted(tunes):
        runs = []
        try:
            for _ in range(args.repeats):
                runs.append(time_tune(tunes[name]["code"], args.window, args.at_cycle))
        except StrudelError:
            failed.append(name)
            continue
        results[name] = {k: statistics.median(r[k] for r in runs) for k in runs[0]}

    keys = ("load", "cold", "warm", "query")
    if args.per_tune:
        print(f"{'tune':<24}" + "".join(f"{k:>10}" for k in keys))
        for name, r in sorted(results.items(), key=lambda kv: -kv[1]["warm"]):
            print(f"{name:<24}" + "".join(f"{r[k]:>10.2f}" for k in keys))
        print()

    n = len(results)
    print(
        f"{n} tunes evaluated, {len(failed)} failed, {args.repeats} repeats, "
        f"window {args.window} s, from cycle {args.at_cycle}"
    )
    print(f"{'ms':<8}" + "".join(f"{k:>10}" for k in keys))
    for label, p in (("p50", 50), ("p90", 90), ("p99", 99), ("max", 100)):
        row = [percentile([r[k] for r in results.values()], p) for k in keys]
        print(f"{label:<8}" + "".join(f"{v:>10.2f}" for v in row))
    if n < 100:
        print(f"p99 of {n} values is the maximum or next to it.")
    slowest = max(results, key=lambda k: results[k]["warm"])
    print(f"slowest warm evaluate: {slowest} ({results[slowest]['warm']:.1f} ms)")
    if failed:
        print("failed:", ", ".join(failed))
    if args.json:
        args.json.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
