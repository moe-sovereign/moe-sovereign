#!/usr/bin/env python3
"""Paired comparison of two rollout runs of the same tasks (e.g. knowledge broker off vs on), without the trust gate.

Correctness is `effective_correct`: the answer if one left the pipeline, else the withheld draft (a blocked episode still has
one). Pairing is by task id; the interval is a bootstrap over tasks. Latency is `base.seconds`.

  compare_runs.py compare --a 'off/*/rollouts.jsonl' --b 'on/*/rollouts.jsonl' --tasks lookup_conflicts.jsonl
  compare_runs.py gate --lookup lookup.json --heldout heldout.json      # the two JSON outputs of `compare`

Gate P0 of the knowledge-broker plan (all three must hold):
  lookup tasks   accuracy gain       >= +0.30 on the graph and noisy arms (forced retrieval alone gave +0.60 earlier)
  held-out tasks accuracy change     >= -0.02
  p95 latency    increase            <= 3 s (held-out)
"""
import argparse
import glob
import json
import random
from collections import Counter, defaultdict

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze import effective_correct, pctl  # noqa: E402

GATE = {"lookup_gain_min": 0.30, "heldout_change_min": -0.02, "p95_increase_max_s": 3.0}


def load(pattern: str) -> list[dict]:
    return [json.loads(line) for f in sorted(glob.glob(pattern)) for line in open(f, encoding="utf-8")]


def per_task(recs: list[dict], tasks: dict) -> dict:
    """task id -> {correct: mean effective correctness over repeats, sec: [...], outcome: Counter, family, arm}."""
    out: dict = defaultdict(lambda: {"eff": [], "sec": [], "outcome": Counter()})
    for r in recs:
        t, b = tasks.get(r["task_id"]), r.get("base")
        if not t or not b:
            continue
        e = out[r["task_id"]]
        e["family"], e["arm"] = t.get("family", "?"), t.get("arm", "")
        e["eff"].append(effective_correct(b))
        e["sec"].append(float(b.get("seconds") or 0.0))
        e["outcome"][b["outcome"].split(":")[0]] += 1
    return out


def bootstrap(diffs: list, boot: int = 4000, seed: int = 0) -> tuple:
    rnd = random.Random(seed)
    means = sorted(sum(rnd.choice(diffs) for _ in diffs) / len(diffs) for _ in range(boot))
    return round(means[int(0.025 * boot)], 3), round(means[int(0.975 * boot)], 3)


def compare(a: list[dict], b: list[dict], tasks: dict, by: str = "arm") -> dict:
    pa, pb = per_task(a, tasks), per_task(b, tasks)
    ids = sorted(set(pa) & set(pb))
    groups: dict = defaultdict(list)
    for i in ids:
        groups["ALL"].append(i)
        groups[pa[i].get(by) or pa[i]["family"]].append(i)
    out: dict = {"tasks": len(ids), "groups": {}}
    for g, members in sorted(groups.items()):
        diffs = [sum(pb[i]["eff"]) / len(pb[i]["eff"]) - sum(pa[i]["eff"]) / len(pa[i]["eff"]) for i in members]
        out["groups"][g] = {
            "tasks": len(members),
            "a": round(sum(sum(pa[i]["eff"]) / len(pa[i]["eff"]) for i in members) / len(members), 3),
            "b": round(sum(sum(pb[i]["eff"]) / len(pb[i]["eff"]) for i in members) / len(members), 3),
            "diff": round(sum(diffs) / len(diffs), 3), "ci95": bootstrap(diffs),
        }
    sa, sb = [s for i in ids for s in pa[i]["sec"]], [s for i in ids for s in pb[i]["sec"]]
    out["seconds"] = {"a_p50": pctl(sa, 0.5), "a_p95": pctl(sa, 0.95), "b_p50": pctl(sb, 0.5), "b_p95": pctl(sb, 0.95)}
    out["outcomes"] = {"a": dict(sum((pa[i]["outcome"] for i in ids), Counter())), "b": dict(sum((pb[i]["outcome"] for i in ids), Counter()))}
    return out


def p0_gate(lookup: dict, heldout: dict) -> dict:
    """Verdict of the three P0 criteria from two `compare` results."""
    # the lookup gain is measured where the graph holds the answer (graph and noisy arms); inline/empty arms dilute it
    arms = [lookup["groups"][g]["diff"] for g in ("graph", "noisy") if g in lookup["groups"]]
    gain = round(sum(arms) / len(arms), 3) if arms else lookup["groups"]["ALL"]["diff"]
    change = heldout["groups"]["ALL"]["diff"]
    inc = (heldout["seconds"]["b_p95"] or 0) - (heldout["seconds"]["a_p95"] or 0)
    checks = {"lookup_gain": (gain, gain >= GATE["lookup_gain_min"]),
              "heldout_change": (change, change >= GATE["heldout_change_min"]),
              "p95_increase_s": (round(inc, 1), inc <= GATE["p95_increase_max_s"])}
    return {"checks": {k: {"value": v, "pass": bool(ok)} for k, (v, ok) in checks.items()}, "pass": all(ok for _, ok in checks.values())}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    c = sp.add_parser("compare")
    c.add_argument("--a", required=True, help="glob of the baseline rollouts (e.g. broker off)")
    c.add_argument("--b", required=True, help="glob of the variant rollouts (e.g. broker on)")
    c.add_argument("--tasks", nargs="+", required=True)
    g = sp.add_parser("gate")
    g.add_argument("--lookup", required=True)
    g.add_argument("--heldout", required=True)
    a = ap.parse_args()
    if a.cmd == "compare":
        tasks = {t["id"]: t for f in a.tasks for t in map(json.loads, open(f, encoding="utf-8"))}
        print(json.dumps(compare(load(a.a), load(a.b), tasks), indent=1))
    else:
        print(json.dumps(p0_gate(json.load(open(a.lookup)), json.load(open(a.heldout))), indent=1))
