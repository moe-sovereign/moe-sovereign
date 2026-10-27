#!/usr/bin/env python3
"""Compare A/B arms of run_scientific_benchmark.py (valid results only).

Usage:
    python3 benchmarks/compare_arms.py results/run_scientific_benchmark_<ts1>.json [...]
"""
from __future__ import annotations

import json
import math
import sys

VALID = {"EXCELLENT", "PASS", "DEFICIENT", "FAIL"}


def _stats(values):
    n = len(values)
    if n == 0:
        return 0.0, 0.0, (0.0, 0.0)
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1)) if n > 1 else 0.0
    sem = sd / math.sqrt(n) if n > 1 else 0.0
    return mean, sd, (mean - 1.96 * sem, mean + 1.96 * sem)


def main() -> None:
    rows = []
    for path in sys.argv[1:]:
        data = json.load(open(path))
        arm = data.get("arm") or path
        by_cond = {}
        for r in data.get("detailed_results", []):
            by_cond.setdefault(r["condition"], []).append(r)
        for cond, items in by_cond.items():
            valid = [r for r in items if r.get("judge_verdict") in VALID]
            judge = [float(r["judge_score"]) for r in valid]
            total = [float(r["score"]) for r in valid]
            det = [float(r["deterministic_score"]) for r in valid]
            secs = [float(r["total_time_s"]) for r in valid if r.get("total_time_s")]
            jm, jsd, jci = _stats(judge)
            tm, _, tci = _stats(total)
            mean_min = (sum(secs) / len(secs) / 60.0) if secs else 0.0
            rows.append({
                "arm": arm, "condition": cond, "n_valid": len(valid), "n_all": len(items),
                "fallback_rate": round(1 - len(valid) / len(items), 3) if items else 0.0,
                "judge_mean": round(jm, 2), "judge_ci95": [round(jci[0], 2), round(jci[1], 2)],
                "score_mean": round(tm, 2), "score_ci95": [round(tci[0], 2), round(tci[1], 2)],
                "det_mean": round(sum(det) / len(det), 2) if det else 0.0,
                "latency_min": round(mean_min, 1),
                "score_per_minute": round(tm / mean_min, 3) if mean_min else 0.0,
                "self_critique_rounds_mean": round(
                    sum(int(r.get("self_critique_round") or 0) for r in valid) / len(valid), 2
                ) if valid else 0.0,
            })
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
