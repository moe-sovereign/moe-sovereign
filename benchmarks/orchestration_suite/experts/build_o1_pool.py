#!/usr/bin/env python3
"""Answer pool of strand O1: the direct judge (judgeA = the 8 samples of wave 6, judgeB = the 16 new ones) and the eight experts under their fine-tuning system prompt (exp_<role>).

  build_o1_pool.py --tasks orch_tasks_heldout.jsonl --judge-a wave6/23857467/rollouts.jsonl --judge-b wave7/<id>/rollouts.jsonl --expert-dir wave7/<stack id> --out pool_o1.jsonl [--variant sys]

The rows are scored with prm/score_answers.py (verifier v2) and analysed with experts/analyze_o1.py.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "prm"))
import build_answer_pool as B  # noqa: E402

ROLES = ("omni", "research", "security", "governance", "datainfra", "coder", "precision", "graphrag")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--judge-a", required=True)
    ap.add_argument("--judge-b", required=True)
    ap.add_argument("--expert-dir", required=True)
    ap.add_argument("--variant", default="sys", choices=["sys", "plain"])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    tasks = {t["id"]: t for t in map(json.loads, open(a.tasks, encoding="utf-8"))}
    runs = {"judgeA": a.judge_a, "judgeB": a.judge_b}
    for r in ROLES:
        p = Path(a.expert_dir) / f"expert_{r}_{a.variant}.jsonl"
        if p.exists():
            runs[f"exp_{r}"] = str(p)
    rows = B.build(tasks, runs)
    with open(a.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps({"rows": len(rows), "sources": sorted(runs), "tasks": len({r["task_id"] for r in rows})}))


if __name__ == "__main__":
    main()
