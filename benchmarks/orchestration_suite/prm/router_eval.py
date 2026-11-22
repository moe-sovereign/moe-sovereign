#!/usr/bin/env python3
"""Prospective router rules (pre-registration F1) on the new held-out set (seed 777, 491 tasks): direct judge x 3 and workflow x 2.

R1  workflow only for the family `cypher_sql_calc`, otherwise the direct judge.
R2  per workflow episode: the workflow answer if its verifier score exceeds the mean verifier score of the direct answers of that task, otherwise the direct judge;
    an episode without an answer uses the direct judge. The direct judge's accuracy is its mean over its samples (expected value of one more answer).
Reported (gate-free draft accuracy, paired by task, bootstrap over tasks): direct, workflow, R1, R2, and R1/R2 minus direct. F1-H1: both beat direct.

  router_eval.py --pool pool_h3.jsonl --scores h3_v2.jsonl --tasks orch_tasks_heldout3.jsonl --wf wf=rollouts.jsonl
"""
import argparse
import collections
import json
import random
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import selective_eval as se  # noqa: E402


def per_task(pool: list, scores: dict, eps: list, family: dict) -> dict:
    d_acc, d_score = collections.defaultdict(list), collections.defaultdict(list)
    for r in pool:
        if r["src"] == "direct":
            d_acc[r["task_id"]].append(r["q"])
            d_score[r["task_id"]].append(scores[r["id"]])
    wf = collections.defaultdict(list)
    for e in eps:
        wf[e["task_id"]].append(e)
    out = {}
    for tid in d_acc:
        if tid not in wf:
            continue
        direct = st.mean(d_acc[tid])
        dscore = st.mean(d_score[tid])
        wf_acc = st.mean(e["q"] for e in wf[tid])
        r1 = wf_acc if family[tid] == "cypher_sql_calc" else direct
        r2 = st.mean((e["q"] if (e["answered"] and e["score"] > dscore) else direct) for e in wf[tid])
        out[tid] = {"direct": direct, "workflow": wf_acc, "R1": r1, "R2": r2}
    return out


def summarize(pt: dict, boot: int = 4000, seed: int = 0) -> dict:
    ids = sorted(pt)
    res = {k: round(st.mean(pt[i][k] for i in ids), 3) for k in ("direct", "workflow", "R1", "R2")}
    rnd = random.Random(seed)
    for rule in ("workflow", "R1", "R2"):
        diffs = [pt[i][rule] - pt[i]["direct"] for i in ids]
        means = sorted(st.mean(rnd.choice(diffs) for _ in diffs) for _ in range(boot))
        res[f"{rule}_minus_direct"] = {"diff": round(st.mean(diffs), 3), "ci95": (round(means[int(0.025 * boot)], 3), round(means[int(0.975 * boot)], 3))}
    res["tasks"] = len(ids)
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool", required=True)
    ap.add_argument("--scores", required=True)
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--wf", required=True, help="name=rollouts.jsonl; the name equals the pool `src` of the workflow answers")
    a = ap.parse_args()
    scores = {r["id"]: r["score"] for r in map(json.loads, open(a.scores, encoding="utf-8"))}
    pool = [json.loads(l) for l in open(a.pool, encoding="utf-8")]
    family = {t["id"]: t["family"] for t in map(json.loads, open(a.tasks, encoding="utf-8"))}
    pid = {r["id"]: (scores[r["id"]], r["q"]) for r in pool if r["id"] in scores}
    name, path = a.wf.split("=", 1)
    eps = se.episodes({name: path}, pid)
    print(json.dumps(summarize(per_task(pool, scores, eps, family)), indent=1))


if __name__ == "__main__":
    main()
