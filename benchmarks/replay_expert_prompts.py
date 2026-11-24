#!/usr/bin/env python3
"""Controlled expert comparison: the SAME recorded expert prompt goes to the Open Source and the Open Weight experts.

In the benchmark the planner decomposes every question separately per track, so the two expert sets answer different
sub-tasks (see ``score_expert_answers.py``). This replay removes that confound: it takes the expert prompts recorded in
``ai_io_audit_log`` (system role prompt + sub-task, verbatim, collected by ``score_expert_answers.py``), sends every prompt
to the expert of the same category in all four expert sets

    spur1 / pre-finetune   (SmolLM3-3B base)         spur1 / fine-tuned   (smollm3-expert-*-3b)
    spur2 / pre-finetune   (Qwen3.5-4B base)         spur2 / fine-tuned   (moe-expert-*-4b)

and grades all answers with the benchmark judge(s), blind to the model. Because every set answers the identical prompt,
differences are paired per prompt (mean difference, standard error, win/tie/loss, exact sign test).

Everything goes through the MoE API ``model@node`` route like every other harness call. Run it after the benchmark:
the expert and judge models share the GPUs with the benchmark.

  python3 benchmarks/replay_expert_prompts.py --archive spur1=benchmarks/results/expert_outputs_spur1.jsonl \
        --archive spur2=benchmarks/results/expert_outputs_spur2.jsonl [--per-category 8] [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import hashlib
import json
import math
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import run_scientific_benchmark as rsb  # noqa: E402
import score_expert_answers as sea  # noqa: E402

# expert set -> (track label used in reports, stage, admin template that defines the models, one variant is enough:
# all three variants of a stage share the expert models)
EXPERT_SETS = {
    "spur1/pre-finetune": ("spur1", "pre-finetune", "LUMI-G Base (Pre-Finetune) - No-GraphRAG"),
    "spur1/fine-tuned": ("spur1", "fine-tuned", "LUMI-G OLMo + SmolLM3 Sovereign Ensemble - No-GraphRAG"),
    "spur2/pre-finetune": ("spur2", "pre-finetune", "Open-Weight Base (Pre-Finetune) - No-GraphRAG"),
    "spur2/fine-tuned": ("spur2", "fine-tuned", "Open-Weight Finetuned Ensemble - No-GraphRAG"),
}
COMPARISONS = [
    ("Open Source vs Open Weight, fine-tuned experts", "spur2/fine-tuned", "spur1/fine-tuned"),
    ("Open Source vs Open Weight, pre-finetune experts", "spur2/pre-finetune", "spur1/pre-finetune"),
    ("Fine-tuning effect, Open Source", "spur1/fine-tuned", "spur1/pre-finetune"),
    ("Fine-tuning effect, Open Weight", "spur2/fine-tuned", "spur2/pre-finetune"),
]


def prompt_id(item: Dict[str, Any]) -> str:
    body = json.dumps([item["category"], item["request_messages"]], sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(body.encode()).hexdigest()[:12]


def build_pool(archives: List[List[Dict[str, Any]]], per_category: int) -> List[Dict[str, Any]]:
    """Unique recorded expert prompts, capped per category and spread over the benchmark questions (round-robin)."""
    seen: Dict[str, Dict[str, Any]] = {}
    for rows in archives:
        for r in rows:
            if not r.get("request_messages") or not r.get("category") or r["category"] == "unknown":
                continue
            item = {k: r[k] for k in ("category", "task_id", "turn", "subtask", "request_messages", "request_options")
                    if k in r}
            item["prompt_id"] = prompt_id(item)
            seen.setdefault(item["prompt_id"], item)
    by_cat: Dict[str, Dict[str, List[Dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for item in seen.values():
        by_cat[item["category"]][item["task_id"]].append(item)
    pool: List[Dict[str, Any]] = []
    for cat in sorted(by_cat):
        queues = [sorted(v, key=lambda i: i["prompt_id"]) for _, v in sorted(by_cat[cat].items())]
        picked: List[Dict[str, Any]] = []
        while len(picked) < per_category and any(queues):
            for q in queues:
                if q and len(picked) < per_category:
                    picked.append(q.pop(0))
        pool += picked
    return pool


def model_sets(templates: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Tuple[str, str]]]:
    """expert set -> category -> (model, endpoint), from the admin templates (first primary slot per category)."""
    out: Dict[str, Dict[str, Tuple[str, str]]] = {}
    for label, (_, _, tname) in EXPERT_SETS.items():
        cfg = templates[tname]
        out[label] = {}
        for cat, ec in cfg["experts"].items():
            slot = next((m for m in ec["models"] if m.get("role", "primary") != "always"), ec["models"][0])
            out[label][cat] = (slot["model"], slot["endpoint"])
    return out


def load_templates() -> Dict[str, Dict[str, Any]]:
    names = ", ".join("'" + t[2].replace("'", "''") + "'" for t in EXPERT_SETS.values())
    rows = sea.psql_json(f"select json_agg(t) from (select name, config_json from admin_expert_templates where name in ({names})) t")
    return {r["name"]: json.loads(r["config_json"]) for r in rows}


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


async def generate(pool: List[Dict[str, Any]], sets: Dict[str, Dict[str, Tuple[str, str]]], cache_path: Path) -> List[Dict[str, Any]]:
    """Every pool prompt to every expert set; per endpoint one call at a time, grouped by model to avoid reloads."""
    cache = {(r["prompt_id"], r["expert_set"]): r for r in load_jsonl(cache_path)}
    jobs: Dict[str, List[Tuple[Dict[str, Any], str, str, str]]] = defaultdict(list)
    for item in pool:
        for label in EXPERT_SETS:
            if (item["prompt_id"], label) in cache:
                continue
            model_ep = sets[label].get(item["category"])
            if not model_ep:
                continue
            jobs[model_ep[1]].append((item, label, model_ep[0], model_ep[1]))
    total = sum(len(v) for v in jobs.values())
    print(f"[generate] {total} answers to generate on {len(jobs)} endpoints ({len(cache)} cached)", flush=True)
    limits = rsb.httpx.Limits(max_keepalive_connections=5, max_connections=20, keepalive_expiry=30.0)
    timeout_cfg = rsb.httpx.Timeout(connect=60.0, read=18000.0, write=60.0, pool=60.0)
    async with rsb.httpx.AsyncClient(timeout=timeout_cfg, limits=limits) as client:
        async def worker(endpoint: str, todo: List[Tuple[Dict[str, Any], str, str, str]]) -> None:
            for item, label, model, ep in sorted(todo, key=lambda j: (j[2], j[0]["prompt_id"])):
                opts = item.get("request_options") or {}
                res = await rsb.query_moe_orchestrator(client, f"{model}@{ep}", item["request_messages"],
                                                       temperature=opts.get("temperature"),
                                                       max_tokens=min(int(opts.get("num_predict") or 4096), 4096))
                rec = {"prompt_id": item["prompt_id"], "expert_set": label, "model": model, "endpoint": ep,
                       "ok": bool(res.get("ok")), "answer": sea_clean(res.get("content", "")), "error": res.get("error", ""),
                       "seconds": res.get("wall_clock_s"), "completion_tokens": res.get("completion_tokens")}
                with cache_path.open("a") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                cache[(item["prompt_id"], label)] = rec

        await asyncio.gather(*(worker(ep, todo) for ep, todo in jobs.items()))
    return list(cache.values())


def sea_clean(text: str) -> str:
    return sea.answer_of({"message": {"content": text}})


def to_scoring_rows(pool: List[Dict[str, Any]], generated: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_id = {i["prompt_id"]: i for i in pool}
    rows = []
    for g in generated:
        item = by_id.get(g["prompt_id"])
        if not item or not g.get("ok") or not g.get("answer"):
            continue
        track, stage, _ = EXPERT_SETS[g["expert_set"]]
        rows.append({"track": track, "stage_of_template": stage, "expert_set": g["expert_set"], "category": item["category"],
                     "task_id": item["task_id"], "turn": item.get("turn"), "subtask": item["subtask"], "answer": g["answer"],
                     "model": g["model"], "endpoint": g["endpoint"], "prompt_id": g["prompt_id"],
                     "audit_id": f"replay-{g['prompt_id']}-{g['expert_set']}"})
    return rows


def sign_test_p(wins: int, losses: int) -> float:
    n = wins + losses
    if n == 0:
        return 1.0
    k = min(wins, losses)
    p = sum(math.comb(n, i) for i in range(0, k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def paired(scores: Dict[str, Dict[str, float]], a: str, b: str, ids: List[str]) -> Dict[str, Any]:
    diffs = [scores[i][a] - scores[i][b] for i in ids if a in scores[i] and b in scores[i]]
    wins, losses = sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)
    m, se = sea.mean_se(diffs)
    return {"n": len(diffs), "mean": m, "se": se, "wins": wins, "ties": len(diffs) - wins - losses, "losses": losses,
            "p": sign_test_p(wins, losses)}


def cell(r: Dict[str, Any]) -> str:
    if r["n"] == 0:
        return "-"
    flag = "" if r["n"] >= sea.MIN_N else f" (n<{sea.MIN_N})"
    se = f" ± {r['se']:.2f}" if not math.isnan(r["se"]) else ""
    return f"{r['mean']:+.2f}{se} (n={r['n']}, W/T/L {r['wins']}/{r['ties']}/{r['losses']}, p={r['p']:.2f}){flag}"


def report(pool: List[Dict[str, Any]], scoring_rows: List[Dict[str, Any]], cache: Dict[str, Dict[str, Any]],
           judges: List[str], out: Path) -> None:
    cat_of = {i["prompt_id"]: i["category"] for i in pool}
    scores: Dict[str, Dict[str, float]] = defaultdict(dict)
    for r in scoring_rows:
        vals = []
        for j in judges:
            rec = cache.get(f"{r['audit_id']}|{j}")
            if rec and isinstance(rec.get("score"), (int, float)) and rec.get("verdict") != "UNSCORED_FALLBACK":
                vals.append(float(rec["score"]))
        if vals:
            scores[r["prompt_id"]][r["expert_set"]] = statistics.mean(vals)
    ids = sorted(scores)
    cats = sorted({cat_of[i] for i in ids})
    md = ["# Expert replay: identical prompts for Open Source and Open Weight experts", "",
          f"Generated {datetime.datetime.now(datetime.timezone.utc):%Y-%m-%d %H:%M UTC}. {len(pool)} recorded expert prompts "
          f"({len(ids)} with at least one graded answer), each sent verbatim to the expert of its category in all four expert sets. "
          f"Judges (cross-judging, mean of judge scores, 0-10): {', '.join(judges)}. Judges see the answer without the model name.", "",
          "**How to read:** each cell is the paired difference first minus second set over the same prompts (mean ± standard error, "
          "W/T/L = prompts where the first set scored higher / equal / lower, p = exact two-sided sign test). "
          f"Cells with n<{sea.MIN_N} are marked and not a basis for a conclusion. No correction for multiple comparisons.", "",
          "**Limits:** the prompts were recorded from benchmark runs (the planner of each condition decided the sub-tasks), so the "
          "prompt pool reflects what these planners ask; scores are LLM-judge opinions against the task reference; small n.", "",
          "## Mean score per expert set", "", "| Expert set | n | mean score |", "|---|---:|---:|"]
    for label in EXPERT_SETS:
        vals = [scores[i][label] for i in ids if label in scores[i]]
        m, se = sea.mean_se(vals)
        md.append(f"| {label} | {len(vals)} | {m:.2f} ± {se:.2f} |" if vals else f"| {label} | 0 | - |")
    for title, a, b in COMPARISONS:
        md += ["", f"## {title} ({a} - {b})", "", "| Category | paired difference |", "|---|---|"]
        for cat in cats:
            md.append(f"| `{cat}` | {cell(paired(scores, a, b, [i for i in ids if cat_of[i] == cat]))} |")
        md.append(f"| **all categories** | {cell(paired(scores, a, b, ids))} |")
    out.write_text("\n".join(md) + "\n")
    out.with_suffix(".json").write_text(json.dumps({"scores": scores, "categories": cat_of}, indent=1))
    print(f"[report] {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive", action="append", required=True, metavar="LABEL=PATH", help="expert_outputs_<label>.jsonl from score_expert_answers.py")
    ap.add_argument("--per-category", type=int, default=8)
    ap.add_argument("--judges", default=",".join(sea.DEFAULT_JUDGES))
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true", help="print pool size and effort estimate, call no model")
    args = ap.parse_args()

    archives = [load_jsonl(Path(spec.partition("=")[2])) for spec in args.archive]
    pool = build_pool(archives, args.per_category)
    judges = [j.strip() for j in args.judges.split(",") if j.strip()]
    per_cat = defaultdict(int)
    for i in pool:
        per_cat[i["category"]] += 1
    answers = len(pool) * len(EXPERT_SETS)
    print(f"[pool] {len(pool)} prompts {dict(per_cat)} -> {answers} answers, {answers * len(judges)} judge calls "
          f"(~{answers * len(judges) * 90 / 3600 / max(1, args.concurrency):.1f} h at ~90 s per judge call, concurrency {args.concurrency})")
    if args.dry_run or not pool:
        return
    sets = model_sets(load_templates())
    generated = asyncio.run(generate(pool, sets, rsb.RESULTS_DIR / "replay_answers.jsonl"))
    rows = to_scoring_rows(pool, generated)
    cases = {c["id"]: c for c in json.loads(rsb.DATASET_PATH.read_text())["test_cases"]}
    cache = asyncio.run(sea.score_all(rows, judges, args.concurrency, rsb.RESULTS_DIR / "replay_scores_cache.jsonl", cases))
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report(pool, rows, cache, judges, rsb.RESULTS_DIR / f"replay_scores_{stamp}.md")


if __name__ == "__main__":
    main()
