#!/usr/bin/env python3
"""Strand O1 of docs/system/lumi_preregistration_2026-11.md: each expert alone, and what routing or selection could gain at best.

Input: an answer pool (prm/build_answer_pool.py) with the sources `judge*` (the direct fine-tuned 32B judge, 24 samples per task in all) and `exp_<role>` (the eight experts,
3 samples per task, answering under their fine-tuning system prompt), and the verifier scores of its rows (prm/score_answers.py). Task split: sha256(task_id) mod 2 (0 = dev, 1 = test).

H1  some (expert, family) pair beats the judge on that family by >= +0.05 (paired bootstrap over tasks, CI excluding 0)
H2  router: per family the best of {judge, experts} on dev, evaluated on test, beats the judge on the test tasks by >= +0.02
H3  verifier selection from a matched 24 candidates: 12 judge + 12 expert answers against 24 judge answers (random subsets, DRAWS per task), >= +0.02
Reported: 24 expert answers, the curve over the number of candidates, accuracy tables.

  analyze_o1.py --pool pool_o1.jsonl --scores scores.jsonl > o1_result.json
"""
import argparse
import collections
import hashlib
import json
import random
import statistics as st


def split_of(task_id: str) -> int:
    return int(hashlib.sha256(task_id.encode()).hexdigest()[:8], 16) % 2


def boot_ci(diffs: list, boot: int = 2000, seed: int = 0) -> tuple:
    rnd = random.Random(seed)
    means = sorted(st.mean(rnd.choice(diffs) for _ in diffs) for _ in range(boot))
    return round(means[int(0.025 * boot)], 3), round(means[int(0.975 * boot)], 3)


def group(pool: list) -> dict:
    """{task: {source: [q, ...]}} with the source family prefix `judge*` merged into `judge`."""
    out: dict = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in pool:
        src = "judge" if r["src"].startswith("judge") else r["src"]
        out[r["task_id"]][src].append(r["q"])
    return out


def family_of(pool: list) -> dict:
    return {r["task_id"]: r["family"] for r in pool}


def h1(g: dict, fam: dict, boot: int) -> dict:
    experts = sorted({s for t in g.values() for s in t if s.startswith("exp_")})
    rows, best = [], None
    for f in sorted(set(fam.values())):
        tasks = [t for t in g if fam[t] == f and "judge" in g[t]]
        for e in experts:
            d = [st.mean(g[t][e]) - st.mean(g[t]["judge"]) for t in tasks if e in g[t]]
            if not d:
                continue
            ci = boot_ci(d, boot)
            rec = {"expert": e, "family": f, "tasks": len(d), "expert_acc": round(st.mean(st.mean(g[t][e]) for t in tasks if e in g[t]), 3),
                   "judge_acc": round(st.mean(st.mean(g[t]["judge"]) for t in tasks if e in g[t]), 3), "diff": round(st.mean(d), 3), "ci95": ci}
            rows.append(rec)
            if best is None or rec["diff"] > best["diff"]:
                best = rec
    met = any(r["diff"] >= 0.05 and r["ci95"][0] > 0 for r in rows)
    return {"met": met, "best": best, "table": rows}


def h2(g: dict, fam: dict, boot: int) -> dict:
    dev = [t for t in g if split_of(t) == 0 and "judge" in g[t]]
    test = [t for t in g if split_of(t) == 1 and "judge" in g[t]]
    choice = {}
    for f in sorted(set(fam.values())):
        cands = {"judge": [st.mean(g[t]["judge"]) for t in dev if fam[t] == f]}
        for s in {s for t in g.values() for s in t if s.startswith("exp_")}:
            cands[s] = [st.mean(g[t][s]) for t in dev if fam[t] == f and s in g[t]]
        scored = {s: st.mean(v) for s, v in cands.items() if v}
        choice[f] = max(scored, key=scored.get)
    d = []
    for t in test:
        c = choice[fam[t]]
        d.append((st.mean(g[t][c]) if c in g[t] else st.mean(g[t]["judge"])) - st.mean(g[t]["judge"]))
    ci = boot_ci(d, boot)
    return {"met": st.mean(d) >= 0.02 and ci[0] > 0, "choice_on_dev": choice, "test_tasks": len(test), "diff": round(st.mean(d), 3), "ci95": ci,
            "router_acc": round(st.mean(st.mean(g[t][choice[fam[t]]]) if choice[fam[t]] in g[t] else st.mean(g[t]["judge"]) for t in test), 3),
            "judge_acc": round(st.mean(st.mean(g[t]["judge"]) for t in test), 3)}


def select(cands: list) -> float:
    """cands: [(score, q)]: q of the highest-scored candidate (ties by first)."""
    return max(cands, key=lambda c: c[0])[1]


def h3(pool: list, scores: dict, boot: int, draws: int, n: int = 24, seed: int = 0) -> dict:
    by: dict = collections.defaultdict(lambda: {"judge": [], "exp": []})
    for r in pool:
        if r["id"] not in scores:
            continue
        kind = "judge" if r["src"].startswith("judge") else "exp" if r["src"].startswith("exp_") else None
        if kind:
            by[r["task_id"]][kind].append((scores[r["id"]], r["q"]))
    rnd = random.Random(seed)
    per_task = {"judge": {}, "mix": {}, "exp": {}}
    half = n // 2
    for t, c in by.items():
        if len(c["judge"]) < n or len(c["exp"]) < half:
            continue
        per_task["judge"][t] = st.mean(select(rnd.sample(c["judge"], n)) for _ in range(draws))
        per_task["mix"][t] = st.mean(select(rnd.sample(c["judge"], half) + rnd.sample(c["exp"], half)) for _ in range(draws))
        if len(c["exp"]) >= n:
            per_task["exp"][t] = st.mean(select(rnd.sample(c["exp"], n)) for _ in range(draws))
    tasks = sorted(per_task["mix"])
    d = [per_task["mix"][t] - per_task["judge"][t] for t in tasks]
    ci = boot_ci(d, boot)
    curve = {}
    for k in (1, 2, 4, 8, 12, 24):
        cj = [st.mean(select(rnd.sample(by[t]["judge"], k)) for _ in range(max(20, draws // 5))) for t in tasks if len(by[t]["judge"]) >= k]
        curve[k] = round(st.mean(cj), 3) if cj else None
    return {"met": bool(d) and st.mean(d) >= 0.02 and ci[0] > 0, "tasks": len(tasks), "judge24": round(st.mean(per_task["judge"][t] for t in tasks), 3),
            "mix12_12": round(st.mean(per_task["mix"][t] for t in tasks), 3),
            "experts24": round(st.mean(per_task["exp"][t] for t in tasks if t in per_task["exp"]), 3) if per_task["exp"] else None,
            "mix_minus_judge": {"diff": round(st.mean(d), 3), "ci95": ci} if d else None, "judge_selection_curve": curve}


def accuracy_tables(g: dict, fam: dict) -> dict:
    out: dict = {}
    for f in sorted(set(fam.values())) + ["ALL"]:
        tasks = [t for t in g if (f == "ALL" or fam[t] == f)]
        out[f] = {s: round(st.mean(st.mean(g[t][s]) for t in tasks if s in g[t]), 3) for s in sorted({s for t in g.values() for s in t})}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool", required=True)
    ap.add_argument("--scores", required=True)
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--draws", type=int, default=200)
    a = ap.parse_args()
    pool = [json.loads(l) for l in open(a.pool, encoding="utf-8")]
    scores = {r["id"]: r["score"] for r in map(json.loads, open(a.scores, encoding="utf-8"))}
    g, fam = group(pool), family_of(pool)
    print(json.dumps({"accuracy": accuracy_tables(g, fam), "H1": h1(g, fam, a.boot), "H2": h2(g, fam, a.boot), "H3": h3(pool, scores, a.boot, a.draws)}, indent=1))


if __name__ == "__main__":
    main()
