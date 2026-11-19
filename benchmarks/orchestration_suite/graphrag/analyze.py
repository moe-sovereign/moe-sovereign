#!/usr/bin/env python3
"""Per-arm results of the GraphRAG ablation from rollout_driver output (run with --forks 0 --capture graph_context).

For every arm: episodes, correct share (Wilson 95 %), outcome mix, share of episodes whose retrieved graph context named
the task's entities, and accuracy given retrieval. Paired differences use the base task (same facts, same question);
bootstrap over base tasks.

  analyze.py --rollouts 'runs/*/rollouts.jsonl' --tasks lookup_tasks.jsonl
"""
import argparse
import glob
import json
import math
import random
from collections import Counter, defaultdict


def wilson(k: int, n: int, z: float = 1.96):
    if not n:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (round(c - h, 3), round(c + h, 3))


def effective_correct(base: dict) -> bool:
    """Correctness without the trust gate: the answer if one left the pipeline (answer / approved draft), else the withheld draft."""
    if base["outcome"].split(":")[0] in ("answer", "hitl"):
        return bool(base["correct"])
    return bool(base.get("draft_correct"))


def pctl(xs: list, q: float):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1) if xs else None


def retrieved(rec: dict, entities: list[str]) -> bool:
    ctx = (rec.get("captured") or {}).get("graph_context") or ""
    return bool(ctx) and any(e in ctx for e in entities)


def holds_answer(rec: dict, task: dict) -> bool:
    """Arithmetic-free tasks only: the stored number itself is in the retrieved context (checks the channel, not the model)."""
    if task["family"] not in ("lookup_val", "lookup_hop1") or "ref" not in task["final"]:
        return False
    ctx = (rec.get("captured") or {}).get("graph_context") or ""
    return f"{int(task['final']['ref'])} " in ctx or f"{int(task['final']['ref'])}\n" in ctx or ctx.endswith(str(int(task["final"]["ref"])))


def analyze(recs: list[dict], tasks: dict, boot: int = 2000, seed: int = 0) -> dict:
    by_arm = defaultdict(list)
    extra = defaultdict(lambda: {"eff": [], "conflicts": [], "rules": Counter(), "sec": [], "bundle": 0})
    per_base = defaultdict(lambda: defaultdict(list))
    for r in recs:
        t = tasks.get(r["task_id"])
        if not t or not r.get("base"):
            continue
        ok = bool(r["base"]["correct"])
        by_arm[t["arm"]].append((ok, r["base"]["outcome"].split(":")[0], retrieved(r, t["entities"]), bool(r["base"].get("draft_correct")), holds_answer(r, t)))
        per_base[t["base"]][t["arm"]].append(ok)
        e = extra[t["arm"]]
        e["eff"].append(effective_correct(r["base"]))
        e["sec"].append(float(r["base"].get("seconds") or 0.0))
        bundle = (r.get("captured") or {}).get("knowledge_bundle") or {}
        if bundle:
            e["bundle"] += 1
        e["conflicts"].append(len(bundle.get("conflicts") or []))
        e["rules"].update(c.get("rule", "?") for c in bundle.get("conflicts") or [])
    out = {"arms": {}, "paired": {}}
    for arm, rows in sorted(by_arm.items()):
        n, k = len(rows), sum(x[0] for x in rows)
        got = [x for x in rows if x[2]]
        out["arms"][arm] = {"episodes": n, "correct": round(k / n, 3), "ci95": wilson(k, n), "outcomes": dict(Counter(x[1] for x in rows)), "draft_correct": round(sum(x[3] for x in rows) / n, 3),
                            "retrieved_share": round(len(got) / n, 3), "answer_in_context": round(sum(x[4] for x in rows) / n, 3),
                            "correct_given_retrieved": round(sum(x[0] for x in got) / len(got), 3) if got else None,
                            "correct_given_not_retrieved": round(sum(x[0] for x in rows if not x[2]) / max(1, n - len(got)), 3)}
        e = extra[arm]
        out["arms"][arm].update({
            "effective_correct": round(sum(e["eff"]) / n, 3),  # gate-free: the number that counts for the knowledge experiments
            "broker_bundle_share": round(e["bundle"] / n, 3),
            "conflicts_per_episode": round(sum(e["conflicts"]) / n, 3), "conflict_rules": dict(e["rules"]),
            "seconds_p50": pctl(e["sec"], 0.5), "seconds_p95": pctl(e["sec"], 0.95)})
    rnd = random.Random(seed)
    bases = list(per_base)
    for a, b in (("graph", "empty"), ("graph", "inline"), ("noisy", "graph"), ("noisy", "empty")):
        diffs = []
        for bid in bases:
            x, y = per_base[bid].get(a), per_base[bid].get(b)
            if x and y:
                diffs.append(sum(x) / len(x) - sum(y) / len(y))
        if not diffs:
            continue
        means = sorted(sum(rnd.choice(diffs) for _ in diffs) / len(diffs) for _ in range(boot))
        out["paired"][f"{a}-{b}"] = {"tasks": len(diffs), "mean_diff": round(sum(diffs) / len(diffs), 3),
                                     "ci95": (round(means[int(0.025 * boot)], 3), round(means[int(0.975 * boot)], 3))}
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rollouts", required=True)
    ap.add_argument("--tasks", required=True)
    a = ap.parse_args()
    tasks = {t["id"]: t for t in map(json.loads, open(a.tasks, encoding="utf-8"))}
    recs = [json.loads(l) for f in glob.glob(a.rollouts) for l in open(f, encoding="utf-8")]
    print(json.dumps(analyze(recs, tasks), indent=1))
