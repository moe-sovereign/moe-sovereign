#!/usr/bin/env python3
"""Per-request pipeline phase timings from a saved orchestrator log.

Correlates each sidecar record (benchmark start/end timestamps) with the
orchestrator request that ran inside that window and derives phase durations
from the ``--- [NODE] ...`` markers.

Usage:
    python3 benchmarks/analyze_phase_timings.py \
        --sidecar benchmarks/results/sidecar_<run_id>.jsonl \
        --log benchmarks/results/orchestrator_logs/<arm>.log \
        --out benchmarks/results/phases_<run_id>.json
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone

LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+)Z .*?\[(chatcmpl-[0-9a-f-]+)\] - (.*)$")
MARKERS = [
    ("planner", "PLANNER RAW OUTPUT"),
    ("experts", "--- [NODE] EXPERTS"),
    ("review_wave", "--- [NODE] REVIEW-WAVE"),
    ("thinking", "--- [NODE] THINKING"),
    ("merger", "--- [NODE] MERGER & INGEST"),
    ("self_critique", "--- [NODE] SELF-CRITIQUE"),
    ("resolve_conflicts", "resolve_conflicts_node"),
    ("critic", "--- [NODE] CRITIC"),
]


def _ts(value: str) -> float:
    value = value.rstrip("Z")
    if "." in value:
        head, frac = value.split(".", 1)
        value = f"{head}.{frac[:6]}"
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sidecar", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    events = defaultdict(list)  # request_id -> [(ts, text)]
    with open(args.log, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = LINE_RE.match(line)
            if match:
                events[match.group(2)].append((_ts(match.group(1)), match.group(3)))

    results = []
    with open(args.sidecar, encoding="utf-8") as handle:
        for raw in handle:
            rec = json.loads(raw)
            if not rec.get("start_ts_utc") or not rec.get("end_ts_utc"):
                continue
            start, end = _ts(rec["start_ts_utc"]), _ts(rec["end_ts_utc"])
            best_id, best_count = "", 0
            for request_id, items in events.items():
                inside = [item for item in items if start <= item[0] <= end]
                has_experts = any("--- [NODE] EXPERTS" in text for _, text in inside)
                if has_experts and len(inside) > best_count:
                    best_id, best_count = request_id, len(inside)
            phases = defaultdict(float)
            counts = defaultdict(int)
            queue_wait_ms = 0
            if best_id:
                marks = []
                for ts, text in events[best_id]:
                    if not start <= ts <= end:
                        continue
                    for name, needle in MARKERS:
                        if needle in text:
                            marks.append((ts, name))
                            counts[name] += 1
                            break
                    wait = re.search(r"Endpoint queue wait: \S+ (\d+) ms", text)
                    if wait:
                        queue_wait_ms += int(wait.group(1))
                marks.sort()
                boundaries = [(start, "pre_planner")] + marks + [(end, "end")]
                for (t0, name), (t1, _) in zip(boundaries, boundaries[1:]):
                    phases[name] += max(0.0, t1 - t0)
            results.append({
                "condition": rec.get("condition"),
                "task_id": rec.get("task_id"),
                "round": rec.get("round"),
                "turn": rec.get("turn"),
                "request_id": best_id,
                "wall_clock_s": rec.get("wall_clock_s"),
                "phase_seconds": {k: round(v, 1) for k, v in phases.items()},
                "phase_counts": dict(counts),
                "queue_wait_ms": queue_wait_ms,
            })

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2)
    print(f"{len(results)} records, {sum(1 for r in results if r['request_id'])} matched -> {args.out}")


if __name__ == "__main__":
    main()
