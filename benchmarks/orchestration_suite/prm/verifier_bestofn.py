#!/usr/bin/env python3
"""Best-of-N with an answer verifier against single answers, majority vote and the oracle, on a scored answer pool.

Pool rows come from build_answer_pool.py (`task_id`, `src`, `family`, `answer`, `q`), scores from score_answers.py
(`id`, `score`). A "selection set" is a group of sources whose answers compete per task (e.g. all three repeats of the direct
model, or every answer of every run). Reported per set and family: the mean accuracy of a single answer, of a plurality vote
over the final numbers (numeric families only; ties count fractionally), of the answer with the highest verifier score, and
of the oracle (any answer correct); plus the verifier's AUROC.

  verifier_bestofn.py --pool pool.jsonl --scores scored.jsonl --set direct=direct --set all=direct,wf_on,wf_off
"""
import argparse
import collections
import importlib.util
import json
import statistics as st
from pathlib import Path


def auroc(scores: list, labels: list) -> float:
    """Probability that a random positive outscores a random negative (ties count half)."""
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return float("nan")
    return sum((p > n) + 0.5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg))


def vote_accuracy(items: list, number_of) -> float:
    """Plurality vote over the final numbers of the answers; a tie is broken uniformly at random (expected value)."""
    vals = [v for v in (number_of(i["answer"]) for i in items) if v is not None]
    if not vals:
        return 0.0
    counts = collections.Counter(round(v, 2) for v in vals)
    top = max(counts.values())
    winners = [v for v, n in counts.items() if n == top]
    good = {round(v, 2) for i in items if i["q"] >= 0.5 for v in [number_of(i["answer"])] if v is not None}
    return sum(w in good for w in winners) / len(winners)


def evaluate(pool: list, scores: dict, sets: dict, number_of=None, min_answers: int = 2) -> dict:
    """sets: name -> list of sources. Returns {set: {family|ALL: {tasks, n, single, majority, verifier, oracle, auroc}}}."""
    by_task: dict = collections.defaultdict(list)
    for r in pool:
        if r["id"] in scores:
            by_task[r["task_id"]].append({**r, "score": scores[r["id"]]})
    out: dict = {}
    for name, sources in sets.items():
        groups: dict = collections.defaultdict(list)
        for items in by_task.values():
            chosen = [i for i in items if i["src"] in sources]
            if len(chosen) < min_answers:
                continue
            groups["ALL"].append(chosen)
            groups[chosen[0]["family"]].append(chosen)
        out[name] = {}
        for fam, tasks in sorted(groups.items()):
            numeric = number_of is not None and not fam.endswith("text_qa")
            flat = [i for t in tasks for i in t]
            out[name][fam] = {
                "tasks": len(tasks), "n_per_task": round(st.mean(len(t) for t in tasks), 1),
                "single": round(st.mean(st.mean(i["q"] >= 0.5 for i in t) for t in tasks), 3),
                "majority": round(st.mean(vote_accuracy(t, number_of) for t in tasks), 3) if numeric else None,
                "verifier": round(st.mean(max(t, key=lambda i: i["score"])["q"] >= 0.5 for t in tasks), 3),
                "oracle": round(st.mean(any(i["q"] >= 0.5 for i in t) for t in tasks), 3),
                "auroc": round(auroc([i["score"] for i in flat], [i["q"] >= 0.5 for i in flat]), 3)}
    return out


def _number_of():
    spec = importlib.util.spec_from_file_location("rollout_driver", Path(__file__).resolve().parents[1] / "rollout_driver.py")
    drv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(drv)
    return lambda text: drv.final_number(text, "en")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool", required=True)
    ap.add_argument("--scores", required=True)
    ap.add_argument("--set", action="append", required=True, help="name=src1,src2")
    a = ap.parse_args()
    pool = [json.loads(line) for line in open(a.pool, encoding="utf-8")]
    scores = {r["id"]: r["score"] for r in map(json.loads, open(a.scores, encoding="utf-8"))}
    sets = {s.split("=", 1)[0]: s.split("=", 1)[1].split(",") for s in a.set}
    print(json.dumps(evaluate(pool, scores, sets, _number_of()), indent=1))
