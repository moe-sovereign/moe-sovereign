#!/usr/bin/env python3
"""Verifier as a release rule against the trust gate (pre-registration A, docs/system/lumi_preregistration_2026-11.md).

Inputs: the answer pool (build_answer_pool.py), the verifier scores (score_answers.py) and the rollouts of the workflow runs (for the
episodes that produced no answer and for the trust gate's verdict and score). Tasks are split by sha256(task_id) mod 2 into dev (0) and
test (1), fixed in advance; thresholds are chosen on dev and reported on test.

A-H1  coverage-matched accuracy of released workflow answers: the trust gate releases an episode when its outcome is `answer` or `hitl`
      (an answer left the pipeline; `hitl` is auto-approved in the harness); the verifier releases the same *number* of episodes, the
      highest-scored ones. Coverage is released / all episodes (an episode without an answer cannot be released by either rule).
A-H2  escalation: per workflow episode, use its answer if the verifier score >= t, otherwise the direct model's answer (expected accuracy =
      the direct model's mean accuracy on that task, since a re-ask is one more sample). Compared with the direct model alone, paired by task.
Also: AUROC (verifier score and trust score against correctness) and ECE of the verifier.

  selective_eval.py --pool pool.jsonl --scores scored.jsonl --tasks tasks.jsonl --wf wf1=run1.jsonl wf2=run2.jsonl --direct direct_ft > out.json
"""
import argparse
import collections
import hashlib
import json
import random
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verifier_bestofn import auroc  # noqa: E402

GRID = [round(0.05 * i, 2) for i in range(1, 20)]


def split_of(task_id: str) -> int:
    """0 = dev, 1 = test; fixed by the task id alone."""
    return int(hashlib.sha256(task_id.encode()).hexdigest()[:8], 16) % 2


def released_by_trust(outcome: str) -> bool:
    return str(outcome).split(":")[0] in ("answer", "hitl")


def episodes(runs: dict, pool_ids: dict) -> list:
    """All episodes of the workflow runs (also those without an answer). `pool_ids` maps pool row id -> (score, q)."""
    out, counter = [], collections.Counter()
    for name, path in runs.items():
        for line in open(path, encoding="utf-8"):
            r = json.loads(line)
            b = r.get("base")
            ep = {"task_id": r["task_id"], "src": name, "answered": False, "score": None, "q": 0.0, "released": False, "trust": None,
                  "family": r.get("family", "")}
            if b:
                ep["released"] = released_by_trust(b.get("outcome", ""))
                ep["trust"] = (b.get("trust") or {}).get("score")
                if b.get("answer") or b.get("draft"):
                    k = counter[(name, r["task_id"])]
                    counter[(name, r["task_id"])] += 1
                    hit = pool_ids.get(f"{r['task_id']}#{name}{k}")
                    if hit is not None:
                        ep.update(answered=True, score=hit[0], q=hit[1])
            out.append(ep)
    return out


def acc(items: list) -> float:
    return sum(i["q"] for i in items) / len(items) if items else float("nan")


def release_curve(eps: list) -> dict:
    """Accuracy of the top-m answered episodes by verifier score, as a function of m (prefix means)."""
    ranked = sorted((e for e in eps if e["answered"]), key=lambda e: -e["score"])
    pref, tot = [], 0.0
    for i, e in enumerate(ranked, 1):
        tot += e["q"]
        pref.append(tot / i)
    return {"ranked": ranked, "prefix_acc": pref}


def coverage_matched(eps: list) -> dict:
    """Trust gate vs verifier at the trust gate's coverage on one set of episodes."""
    n = len(eps)
    released = [e for e in eps if e["released"] and e["answered"]]
    m = len(released)
    if not n or not m:
        return {"n": n, "coverage": 0.0}
    curve = release_curve(eps)
    m_v = min(m, len(curve["ranked"]))
    return {"n": n, "coverage": round(m / n, 3), "trust_acc": round(acc(released), 3), "verifier_acc": round(curve["prefix_acc"][m_v - 1], 3),
            "all_answered_acc": round(acc([e for e in eps if e["answered"]]), 3)}


def by_task(eps: list) -> dict:
    d: dict = collections.defaultdict(list)
    for e in eps:
        d[e["task_id"]].append(e)
    return d


def bootstrap_diff(eps: list, fn, boot: int = 2000, seed: int = 0) -> tuple:
    """95 % interval of fn(sample) over tasks; fn returns a float."""
    groups = list(by_task(eps).values())
    rnd = random.Random(seed)
    vals = []
    for _ in range(boot):
        sample = [e for g in (rnd.choice(groups) for _ in groups) for e in g]
        v = fn(sample)
        if v == v:  # not NaN
            vals.append(v)
    vals.sort()
    return (round(vals[int(0.025 * len(vals))], 3), round(vals[int(0.975 * len(vals))], 3)) if vals else (float("nan"), float("nan"))


def h1_diff(sample: list) -> float:
    r = coverage_matched(sample)
    return r["verifier_acc"] - r["trust_acc"] if "trust_acc" in r else float("nan")


def escalation(eps: list, direct_acc: dict, t: float) -> dict:
    """Per-task accuracy when a workflow answer scoring >= t is used and the direct model answers otherwise."""
    per_task, esc, total = {}, 0, 0
    for tid, g in by_task(eps).items():
        vals = []
        for e in g:
            total += 1
            if e["answered"] and e["score"] >= t:
                vals.append(e["q"])
            else:
                vals.append(direct_acc.get(tid, 0.0))
                esc += 1
        per_task[tid] = st.mean(vals)
    return {"per_task": per_task, "escalation_rate": esc / total if total else float("nan")}


def choose_t(dev: list, direct_acc: dict) -> float:
    best, best_t = -1.0, GRID[0]
    for t in GRID:
        a = st.mean(escalation(dev, direct_acc, t)["per_task"].values())
        if a > best + 1e-12:
            best, best_t = a, t
    return best_t


def ece(scores: list, labels: list, bins: int = 10) -> float:
    n, total = len(scores), 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, s in enumerate(scores) if (lo <= s < hi) or (b == bins - 1 and s == 1.0)]
        if idx:
            total += len(idx) / n * abs(st.mean(scores[i] for i in idx) - st.mean(labels[i] for i in idx))
    return round(total, 3)


def evaluate(eps: list, direct_acc: dict, boot: int = 2000) -> dict:
    dev = [e for e in eps if split_of(e["task_id"]) == 0]
    test = [e for e in eps if split_of(e["task_id"]) == 1]
    out: dict = {"episodes": {"dev": len(dev), "test": len(test)},
                 "tasks": {"dev": len(by_task(dev)), "test": len(by_task(test))}}
    for name, part in (("dev", dev), ("test", test)):
        ans = [e for e in part if e["answered"]]
        out[name] = {"coverage_matched": coverage_matched(part),
                     "auroc_verifier": round(auroc([e["score"] for e in ans], [e["q"] >= 0.5 for e in ans]), 3) if ans else None,
                     "auroc_trust": round(auroc([e["trust"] or 0.0 for e in ans], [e["q"] >= 0.5 for e in ans]), 3) if ans else None,
                     "auroc_trust_release": round(auroc([1.0 if e["released"] else 0.0 for e in ans], [e["q"] >= 0.5 for e in ans]), 3) if ans else None,
                     "ece_verifier": ece([e["score"] for e in ans], [e["q"] for e in ans]) if ans else None}
    out["test"]["h1_diff_ci95"] = bootstrap_diff(test, h1_diff, boot)
    t = choose_t(dev, direct_acc)
    esc = escalation(test, direct_acc, t)
    tids = sorted(esc["per_task"])
    diffs = [esc["per_task"][i] - direct_acc.get(i, 0.0) for i in tids]
    rnd = random.Random(1)
    means = sorted(st.mean(rnd.choice(diffs) for _ in diffs) for _ in range(boot))
    out["h2"] = {"t_chosen_on_dev": t, "test_tasks": len(tids), "escalation_rate": round(esc["escalation_rate"], 3),
                 "accuracy": round(st.mean(esc["per_task"].values()), 3), "direct_alone": round(st.mean(direct_acc.get(i, 0.0) for i in tids), 3),
                 "diff": round(st.mean(diffs), 3), "ci95": (round(means[int(0.025 * boot)], 3), round(means[int(0.975 * boot)], 3)),
                 "dev_curve": {str(g): round(st.mean(escalation(dev, direct_acc, g)["per_task"].values()), 3) for g in GRID}}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool", required=True)
    ap.add_argument("--scores", required=True)
    ap.add_argument("--direct", default="direct_ft", help="pool source of the direct model")
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--wf", nargs="+", required=True, help="name=rollouts.jsonl (names equal the pool `src`)")
    a = ap.parse_args()
    scores = {r["id"]: r["score"] for r in map(json.loads, open(a.scores, encoding="utf-8"))}
    pool = [json.loads(l) for l in open(a.pool, encoding="utf-8")]
    pool_ids = {r["id"]: (scores[r["id"]], r["q"]) for r in pool if r["id"] in scores}
    d: dict = collections.defaultdict(list)
    for r in pool:
        if r["src"] == a.direct:
            d[r["task_id"]].append(r["q"])
    direct_acc = {k: st.mean(v) for k, v in d.items()}
    eps = [e for e in episodes(dict(x.split("=", 1) for x in a.wf), pool_ids) if e["task_id"] in direct_acc]
    print(json.dumps(evaluate(eps, direct_acc, a.boot), indent=1))


if __name__ == "__main__":
    main()
