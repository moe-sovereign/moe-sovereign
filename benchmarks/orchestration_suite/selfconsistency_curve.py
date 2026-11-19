#!/usr/bin/env python3
"""Accuracy of a plurality vote over N sampled answers per task, for N = 1..K (expected value over random subsets).

Each input is a jsonl of per-sample records; candidates are grouped by task. Direct-model files
(direct_baseline.py) hold one answer per record (`base.answer`, `repeat`), rollout files hold the base answer and, with
--forks, the drafts of the forks at one decision node.

  selfconsistency_curve.py --tasks tasks.jsonl --label direct heldout/direct.jsonl --label workflow heldout/main.jsonl
  selfconsistency_curve.py --tasks tasks.jsonl --forks-node planner --label forks clean_all/rollouts.jsonl
"""
import argparse
import collections
import importlib.util
import json
import random
from pathlib import Path

_spec = importlib.util.spec_from_file_location("rollout_driver", Path(__file__).resolve().parent / "rollout_driver.py")
drv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(drv)


def vote(values, ref, tol, rnd):
    """1.0 if the plurality value is correct; ties are broken uniformly at random (expected value)."""
    vals = [round(v, 2) for v in values if v is not None]
    if not vals:
        return 0.0
    c = collections.Counter(vals)
    top = max(c.values())
    win = [v for v, n in c.items() if n == top]
    return sum(abs(v - ref) <= max(tol, 1e-9) + 1e-9 for v in win) / len(win)


def candidates(path, forks_node):
    out = collections.defaultdict(list)
    for line in open(path, encoding="utf-8"):
        r = json.loads(line)
        texts = []
        b = r.get("base")
        if b and (b.get("answer") or b.get("draft")) and not forks_node:
            texts.append(b.get("answer") or b.get("draft"))
        for p in r.get("decision_points", []):
            if forks_node and forks_node in p["next"]:
                texts += [f.get("draft") or f.get("answer") for f in p["forks"] if f.get("draft") or f.get("answer")]
        if texts:
            out[r["task_id"]] += [(t, r["lang"]) for t in texts]
    return out


def curve(cands, tasks, k, draws, seed):
    rnd = random.Random(seed)
    res = collections.defaultdict(lambda: collections.defaultdict(list))
    for tid, items in cands.items():
        if tid not in tasks or len(items) < 1:
            continue
        fin = tasks[tid]["final"]
        if fin.get("kind") == "text":
            continue
        nums = [drv.final_number(t, lang) for t, lang in items]
        for n in range(1, min(k, len(items)) + 1):
            vals = [vote(rnd.sample(nums, n), fin["ref"], fin["tol"], rnd) for _ in range(draws if n < len(items) else 1)]
            res[tasks[tid]["family"]][n].append(sum(vals) / len(vals))
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--label", action="append", required=True)
    ap.add_argument("--forks-node", default="")
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--draws", type=int, default=40)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("files", nargs="+")
    a = ap.parse_args()
    tasks = {json.loads(l)["id"]: json.loads(l) for l in open(a.tasks, encoding="utf-8")}
    for label, path in zip(a.label, a.files):
        res = curve(candidates(path, a.forks_node), tasks, a.k, a.draws, a.seed)
        for fam, by_n in sorted(res.items()):
            print(f"{label:10s} {fam:16s} " + " ".join(f"N={n}:{sum(v) / len(v):.3f}" for n, v in sorted(by_n.items())) + f"  (tasks {len(by_n[1])})")


if __name__ == "__main__":
    main()
