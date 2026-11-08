#!/usr/bin/env python3
"""Rollouts of the counterfactual campaign -> training rows for the orchestration PRM.

One row per decision point: what a value model sees (task, the node about to run, the pipeline state at that
checkpoint) and what it should predict, q = share of the K forked continuations that end in a correct answer.

Splits are by task (never by decision point, the points of one episode share almost all of their state):
  test_ood   all tasks of one scenario domain (default "energy") - checks transfer to unseen data schemas
  val / test_iid  hash(task_id) buckets of the remaining tasks (5 % each)
  train      the rest
Uniform decision points (every fork right or every fork wrong) carry no ranking signal; `--uniform-keep` thins them
out of the TRAIN split only, and the kept rows get a `weight` that restores their share of the loss.

Usage: build_dataset.py --rollouts 'runs/*/rollouts.jsonl' --tasks tasks.jsonl [--tasks more.jsonl] --out-dir prm_data
"""
import argparse
import glob
import hashlib
import json
import os
import random
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from metrics import evaluate  # noqa: E402

STR_LIMIT = 700       # characters kept per string value in the state
STATE_LIMIT = 14000   # characters of state JSON per row (~4k tokens)


def compact(value, limit=STR_LIMIT):
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f"...[+{len(value) - limit} chars]"
    if isinstance(value, dict):
        return {k: compact(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [compact(v, limit) for v in value[:30]]
    return value


def render(prompt, nodes, state):
    body = json.dumps(compact(state), ensure_ascii=False, separators=(",", ":"))
    if len(body) > STATE_LIMIT:
        body = body[:STATE_LIMIT] + "...[truncated]"
    return f"[TASK]\n{prompt}\n[NEXT NODE]\n{', '.join(nodes)}\n[STATE]\n{body}"


def bucket(task_id):
    return int(hashlib.sha256(task_id.encode()).hexdigest()[:8], 16) % 20


def split_of(task_id, domain, ood_domain):
    if ood_domain and domain == ood_domain:
        return "test_ood"
    b = bucket(task_id)
    return "val" if b == 0 else "test_iid" if b == 1 else "train"


def load_tasks(paths):
    tasks = {}
    for p in paths:
        for line in open(p):
            t = json.loads(line)
            tasks.setdefault(t["id"], t)
    return tasks


def build(rollouts, tasks, ood_domain="energy", uniform_keep=0.25, seed=1, min_forks=2):
    rnd = random.Random(seed)
    best = {}  # a task may appear twice (resubmitted waves): keep the record with the most forks
    for rec in rollouts:
        n = sum(len(p["forks"]) for p in rec["decision_points"])
        if rec["task_id"] in tasks and (rec["task_id"] not in best or n > best[rec["task_id"]][0]):
            best[rec["task_id"]] = (n, rec)
    rows = []
    for _, rec in best.values():
        t = tasks[rec["task_id"]]
        prompt = (t.get("prompts_inline") or t["prompts"])[rec["lang"]]
        domain = rec["task_id"].split("-")[1]
        split = split_of(rec["task_id"], domain, ood_domain)
        prev = None
        for i, p in enumerate(rec["decision_points"]):
            ok = [f["correct"] for f in p["forks"]]
            if len(ok) < min_forks:
                continue
            q = sum(ok) / len(ok)
            uniform = q in (0.0, 1.0)
            weight = 1.0
            if split == "train" and uniform and uniform_keep < 1.0:
                if rnd.random() >= uniform_keep:
                    prev = q
                    continue
                weight = 1.0 / uniform_keep
            rows.append({
                "id": f"{rec['task_id']}#{i}", "group": rec["task_id"], "split": split, "family": rec["family"],
                "domain": domain, "lang": rec["lang"], "index": i, "node": p["next"][0] if p["next"] else "?",
                "text": render(prompt, p["next"], p["state"]), "q": q, "k": sum(ok), "n": len(ok), "weight": weight,
                "dq": None if prev is None else q - prev,
                "base_correct": bool(rec["base"] and rec["base"]["correct"]),
            })
            prev = q
    return rows


def baseline(rows):
    """Predict the training mean of q per (family, node): what a PRM has to beat to claim it looks at the state."""
    acc = defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        if r["split"] == "train":
            a = acc[(r["family"], r["node"])]
            a[0] += r["q"] * r["weight"]
            a[1] += r["weight"]
    glob_mean = sum(a[0] for a in acc.values()) / max(sum(a[1] for a in acc.values()), 1e-9)
    table = {k: v[0] / v[1] for k, v in acc.items() if v[1]}
    out = {}
    for split in ("val", "test_iid", "test_ood"):
        part = [r for r in rows if r["split"] == split]
        if part:
            out[split] = evaluate(part, [table.get((r["family"], r["node"]), glob_mean) for r in part])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rollouts", nargs="+", required=True, help="glob patterns")
    ap.add_argument("--tasks", nargs="+", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--ood-domain", default="energy", help="scenario held out as test_ood; '' keeps all domains in play (final run)")
    ap.add_argument("--uniform-keep", type=float, default=0.25)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    recs = [json.loads(line) for pat in a.rollouts for f in sorted(glob.glob(pat)) for line in open(f)]
    rows = build(recs, load_tasks(a.tasks), a.ood_domain, a.uniform_keep, a.seed)
    os.makedirs(a.out_dir, exist_ok=True)
    for split in ("train", "val", "test_iid", "test_ood"):
        with open(os.path.join(a.out_dir, f"{split}.jsonl"), "w") as f:
            for r in rows:
                if r["split"] == split:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
    report = {"records": len(recs), "rows": Counter(r["split"] for r in rows),
              "signal_share": {s: round(sum(0 < r["q"] < 1 for r in rows if r["split"] == s) / max(sum(r["split"] == s for r in rows), 1), 3)
                               for s in ("train", "val", "test_iid", "test_ood")},
              "by_family": Counter(r["family"] for r in rows), "by_node": Counter(r["node"] for r in rows),
              "text_chars_mean": round(sum(len(r["text"]) for r in rows) / max(len(rows), 1)),
              "baseline_family_node_mean": baseline(rows)}
    json.dump(report, open(os.path.join(a.out_dir, "report.json"), "w"), indent=1, default=dict)
    print(json.dumps(report, indent=1, default=dict))


if __name__ == "__main__":
    main()
