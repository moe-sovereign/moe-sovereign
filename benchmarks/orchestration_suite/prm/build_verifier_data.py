#!/usr/bin/env python3
"""Answer-verifier rows (outcome reward model): one row per drafted answer, label = was it correct.

Input is the same rollout files as the PRM dataset: every fork carries `draft` (the answer before the quality gate, also for
blocked forks) and `draft_correct`; base episodes carry `answer`/`draft` and `correct`. The verifier sees the task prompt and the
draft, nothing of the pipeline state, so it can be used on any generator's answers (workflow, direct model, best-of-N).

  build_verifier_data.py --rollouts 'wave4/*/rollouts.jsonl' 'wave5/*/rollouts.jsonl' --tasks orch_tasks_w3.jsonl \
      --heldout 'heldout/*.jsonl' --skip-ids skip.txt --out-dir verifier_data

Splits: train / val / test_iid by task hash as in build_dataset.py; `test_ood` = answers to the held-out tasks (never used for
collection) from the direct model and the workflow variants, i.e. another generator and unseen tasks.
"""
import argparse
import glob
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_dataset import bucket, load_tasks  # noqa: E402


def row(task, lang, draft, ok, rid, group, split, src, weight=1.0):
    prompt = (task.get("prompts_inline") or task["prompts"])[lang]
    return {"id": rid, "group": group, "split": split, "family": task["family"], "node": "answer", "lang": lang, "source": src,
            "text": f"[TASK]\n{prompt}\n[ANSWER]\n{draft}", "q": float(bool(ok)), "weight": weight}


def split_of(task_id):
    b = bucket(task_id)
    return "val" if b == 0 else "test_iid" if b == 1 else "train"


def build(rollouts, tasks, heldout, skip, per_task=24, seed=1, min_chars=1, max_chars=3000):
    rnd = random.Random(seed)
    by_task = {}
    for rec in rollouts:
        tid = rec["task_id"]
        if tid not in tasks or tid in skip:
            continue
        cands = by_task.setdefault(tid, [])
        b = rec.get("base")
        if b and (b.get("draft") or b.get("answer")):
            cands.append((b.get("draft") or b.get("answer"), b.get("draft_correct") if b.get("outcome", "").startswith("blocked") else b.get("correct"), rec["lang"]))
        for p in rec["decision_points"]:
            for f in p["forks"]:
                if f.get("draft"):
                    cands.append((f["draft"], f["draft_correct"], rec["lang"]))
    rows = []
    for tid, cands in by_task.items():
        cands = [c for c in cands if min_chars <= len(c[0]) <= max_chars]
        rnd.shuffle(cands)
        for i, (d, ok, lang) in enumerate(cands[:per_task]):  # a few answers per task so that no task dominates
            rows.append(row(tasks[tid], lang, d, ok, f"{tid}#{i}", tid, split_of(tid), "fork"))
    for path in heldout:
        src = os.path.basename(path).split(".")[0]
        for line in open(path, encoding="utf-8"):
            rec = json.loads(line)
            b = rec.get("base")
            if not b or rec["task_id"] not in tasks:
                continue
            text = b.get("draft") or b.get("answer")
            if not text or len(text) > max_chars:
                continue
            ok = b.get("draft_correct") if b.get("outcome", "").startswith("blocked") else b.get("correct")
            rows.append(row(tasks[rec["task_id"]], rec["lang"], text, ok, f"{rec['task_id']}#{src}{rec.get('repeat', len(rows))}", rec["task_id"], "test_ood", src))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rollouts", nargs="+", required=True)
    ap.add_argument("--tasks", nargs="+", required=True)
    ap.add_argument("--heldout", nargs="*", default=[])
    ap.add_argument("--skip-ids", default="", help="file with one task id per line to leave out (e.g. episodes run with a dead planner)")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--per-task", type=int, default=24)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    recs = [json.loads(line) for pat in a.rollouts for f in sorted(glob.glob(pat)) for line in open(f)]
    skip = set(open(a.skip_ids).read().split()) if a.skip_ids else set()
    held = [f for pat in a.heldout for f in sorted(glob.glob(pat))]
    rows = build(recs, load_tasks(a.tasks), held, skip, a.per_task, a.seed)
    os.makedirs(a.out_dir, exist_ok=True)
    for split in ("train", "val", "test_iid", "test_ood"):
        part = [r for r in rows if r["split"] == split]
        with open(os.path.join(a.out_dir, f"{split}.jsonl"), "w", encoding="utf-8") as f:
            for r in part:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(split, len(part), "positive share", round(sum(r["q"] for r in part) / max(len(part), 1), 3))


if __name__ == "__main__":
    main()
