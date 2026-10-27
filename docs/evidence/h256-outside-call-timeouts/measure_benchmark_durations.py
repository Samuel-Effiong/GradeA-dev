"""H-256: the per-call durations in the live benchmark records, by kind.

    python -I measure_benchmark_durations.py ai_processor/benchmark/answers/reports

Reads every report's records: `extraction.calls` and `calls` are answer
readings, `grading.calls` are grading calls; each call carries `seconds`.
Prints n, p50, p95, p99 (linear interpolation) and max per kind.
"""

import glob
import json
import os
import sys


def q(xs, p):
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    f = int(k)
    c = min(f + 1, len(xs) - 1)
    return round(xs[f] + (xs[c] - xs[f]) * (k - f), 1)


def main(folder):
    kinds = {"reading": [], "grading": []}
    for path in sorted(glob.glob(os.path.join(folder, "*.json"))):
        with open(path) as f:
            report = json.load(f)
        records = report.get("records") if isinstance(report, dict) else None
        for record in records or []:
            if "extraction" in record:
                kinds["reading"] += [
                    c["seconds"] for c in record["extraction"].get("calls", [])
                ]
            if "grading" in record:
                kinds["grading"] += [
                    c["seconds"] for c in record["grading"].get("calls", [])
                ]
            if "calls" in record:
                kinds["reading"] += [c["seconds"] for c in record["calls"]]
    for kind, xs in kinds.items():
        print(
            kind,
            len(xs),
            "p50",
            q(xs, 0.5),
            "p95",
            q(xs, 0.95),
            "p99",
            q(xs, 0.99),
            "max",
            max(xs),
        )


if __name__ == "__main__":
    main(sys.argv[1])
