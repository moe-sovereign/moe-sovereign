#!/usr/bin/env python3
"""Evaluation of the 2026-11-20 runs: base vs fine-tuned (factorial), sampling yield, router switches.

All correctness is gate-free (`effective_correct`: answer if one left the pipeline, else the withheld draft); pairing by task id with a
bootstrap over tasks (compare_runs.compare). A direct record counts as its answer. Run from the directory that holds wave6/ and the
task files:   analyze_wave6.py --root /opt/tmp/lumi-rollouts > wave6_results.json
"""
import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze import effective_correct  # noqa: E402
from compare_runs import bootstrap, compare, per_task  # noqa: E402


def rows(path) -> list:
    return [json.loads(l) for l in open(path, encoding="utf-8")]


def mean_acc(recs, tasks) -> dict:
    pt = per_task(recs, tasks)
    return {i: sum(v["eff"]) / len(v["eff"]) for i, v in pt.items()}


def summarize(name, recs, tasks) -> dict:
    m = mean_acc(recs, tasks)
    return {"run": name, "tasks": len(m), "samples": len(recs), "acc": round(sum(m.values()) / len(m), 3)}


def passk(recs, tasks) -> dict:
    pt = per_task(recs, tasks)
    ks = {}
    for k in (1, 2, 4, 6, 8):
        vals = []
        for v in pt.values():
            n, c = len(v["eff"]), sum(v["eff"])
            if n < k:
                continue
            # unbiased pass@k = 1 - C(n-c, k) / C(n, k)
            from math import comb
            vals.append(1 - comb(n - c, k) / comb(n, k))
        if vals:
            ks[k] = round(sum(vals) / len(vals), 3)
    return ks


def cmp(a, b, tasks) -> dict:
    r = compare(a, b, tasks)["groups"]["ALL"]
    return {"n_tasks": r["tasks"], "a": r["a"], "b": r["b"], "diff": r["diff"], "ci95": r["ci95"]}


def by_family(a, b, tasks) -> dict:
    g = compare(a, b, tasks, by="family")["groups"]
    return {k: {"a": v["a"], "b": v["b"], "diff": v["diff"], "ci95": v["ci95"], "n": v["tasks"]} for k, v in g.items() if k != "ALL"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True)
    root = Path(ap.parse_args().root)
    w = root / "wave6"
    ho = {t["id"]: t for t in rows(root / "orch_tasks_heldout2.jsonl")}
    ho1 = {t["id"]: t for t in rows(root / "orch_tasks_heldout.jsonl")}
    look = {t["id"]: t for t in rows(root / "lookup_simple.jsonl")}
    ext_tasks = Path(root / "ext_tasks.jsonl")
    ext = {t["id"]: t for t in rows(ext_tasks)} if ext_tasks.exists() else {}
    R = lambda j: rows(w / str(j) / "rollouts.jsonl")  # noqa: E731
    out: dict = {}

    # --- 1. sampling yield on the held-out set ---------------------------------------------------------------------------------
    direct_ft = R(23857467)                                   # judge fine-tuned 32B alone, 8 samples/task
    wf = R(23857468) + R(23857469) + R(23857470)              # full workflow, 6 samples/task (heldout2 = every task twice)
    out["sampling"] = {
        "direct_ft_8": summarize("direct fine-tuned judge, 8 samples", direct_ft, ho1),
        "workflow_6": summarize("workflow, 6 samples", wf, ho),
        "direct_ft_passk": passk(direct_ft, ho1),
        "workflow_passk": passk(wf, ho),
        "workflow_vs_direct_same_tasks": cmp(direct_ft, wf, {**ho1, **ho}),
    }

    # --- 2. base vs fine-tuned, factorial --------------------------------------------------------------------------------------
    # A = base planner + fine-tuned judge; B = fine-tuned planner + base judge; C = both base; FT = both fine-tuned (rep1-3 / ext/wf_on)
    hof = {"A": R(23858648), "B": R(23858649), "C": R(23858650), "FT": wf}
    out["factorial_heldout"] = {k: summarize(k, v, ho) for k, v in hof.items()}
    out["factorial_heldout"]["direct_base32b"] = summarize("direct base 32B (3 samples)", R(23858647), ho1)
    out["factorial_heldout"]["direct_ft32b"] = summarize("direct fine-tuned 32B (8 samples)", direct_ft, ho1)
    out["factorial_heldout"]["contrasts"] = {
        "FT vs C (both fine-tuned vs both base)": cmp(hof["C"], hof["FT"], ho),
        "planner effect: A(base planner) -> FT": cmp(hof["A"], hof["FT"], ho),
        "judge effect: B(base judge) -> FT": cmp(hof["B"], hof["FT"], ho),
        "direct: base32b -> fine-tuned 32b": cmp(R(23858647), direct_ft, {**ho1}),
        "C(workflow base) vs direct base32b": cmp(R(23858647), hof["C"], {**ho1, **ho}),
    }
    if ext:
        ex = {"A": R(23858652), "B": R(23858653), "C": R(23858654)}
        ext_dir_base = R(23858651)
        wf_off = root / "ext" / "wf_on.jsonl"  # check on = default, like A-C (wf_off had the calculate check disabled)
        if wf_off.exists():
            ex["FT"] = rows(wf_off)
        ext_dir_ft = root / "ext" / "direct.jsonl"
        out["factorial_ext"] = {k: summarize(k, v, ext) for k, v in ex.items()}
        out["factorial_ext"]["direct_base32b"] = summarize("direct base 32B", ext_dir_base, ext)
        if ext_dir_ft.exists():
            out["factorial_ext"]["direct_ft32b"] = summarize("direct fine-tuned 32B", rows(ext_dir_ft), ext)
            out["factorial_ext"]["contrasts"] = {"direct: base -> fine-tuned": cmp(ext_dir_base, rows(ext_dir_ft), ext)}
        if "FT" in ex:
            out["factorial_ext"].setdefault("contrasts", {})["workflow FT vs C"] = cmp(ex["C"], ex["FT"], ext)
            out["factorial_ext"]["contrasts"]["planner effect A->FT"] = cmp(ex["A"], ex["FT"], ext)
            out["factorial_ext"]["contrasts"]["judge effect B->FT"] = cmp(ex["B"], ex["FT"], ext)

    # --- 3. router switches ----------------------------------------------------------------------------------------------------
    e1, e2, e3 = R(23875180), R(23857472), R(23857473)
    out["router"] = {
        "E2_force_graph_heldout": {"base_wf6": summarize("workflow", wf, ho), "E2": summarize("forced graph", e2, ho), "vs_baseline": cmp(wf, e2, ho)},
        "E3_extra_categories_heldout": {"E3": summarize("extra categories", e3, ho), "vs_baseline": cmp(wf, e3, ho)},
    }
    prod, forced = root / "graphrag_prod.jsonl", root / "graphrag_forced.jsonl"
    if prod.exists():
        for name, p in (("graphrag_prod (router default)", prod), ("graphrag_forced", forced)):
            if p.exists():
                out["router"].setdefault("E1_lookup", {})[name] = summarize(name, rows(p), look)
        out["router"].setdefault("E1_lookup", {})["E1 extra categories"] = summarize("E1", e1, look)
        out["router"]["E1_lookup"]["vs_default"] = cmp(rows(prod), e1, look)
        if forced.exists():
            out["router"]["E1_lookup"]["vs_forced"] = cmp(rows(forced), e1, look)
        retr = sum(1 for r in e1 if (r.get("captured") or {}).get("graph_context"))
        out["router"]["E1_lookup"]["graph_context_share"] = round(retr / len(e1), 3)
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
