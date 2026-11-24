#!/usr/bin/env python3
"""Score the individual expert answers of the benchmark templates (Open Source vs Open Weight experts).

The benchmark harness writes one sidecar line per request (``chatcmpl_id``); the orchestrator persists every expert call in
``ai_io_audit_log`` (stage ``expert``, category in ``request_body.expert_category``, sub-task as last user message).
This script joins both after a run, archives the expert answers and grades each one with the benchmark judge(s):

  1. collect : sidecar -> audit log -> ``results/expert_outputs_<label>.jsonl`` (idempotent archive)
  2. score   : every answer is graded 0..10 for ITS sub-task against the task's reference answer and rubric, with the same
               prompt, scale and retry logic as the final-answer judge (``judge_evaluation``), once per judge
  3. report  : mean / standard error / n per track x stage (pre-finetune, fine-tuned) x category, plus the Open Source vs
               Open Weight comparison per category and stage

Scoring runs after the benchmark so it does not slow it down. By default both track judges grade every answer (cross-judging)
so that the family of the judge does not decide the comparison; judge models are loaded one after the other.

Usage:
  python3 benchmarks/score_expert_answers.py \
      --sidecar spur1=benchmarks/results/sidecar_<run>.jsonl --sidecar spur2=benchmarks/results/sidecar_<run>.jsonl
  add --collect-only to archive the answers without grading, --include-refinement to also grade debate refinements.

Limits (also written into the report): the planner decomposes each question separately per track, so the two tracks answer
different sub-tasks; with 8 benchmark questions per round the per-category n is small; judge scores are model opinions.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import datetime
import json
import math
import re
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import run_scientific_benchmark as rsb  # noqa: E402  (harness: judge prompt, scale, retry logic, API access)

DEFAULT_JUDGES = [
    "hf.co/h3rb3rn/sovereign-judge-olmo31-32b:Q4_K_M",
    "hf.co/h3rb3rn/sovereign-judge-27b:Q4_K_M",
]
MIN_N = 5  # below this a cell is reported but marked as too small for a comparison


def psql_json(sql: str) -> Any:
    done = subprocess.run(["docker", "exec", "terra_checkpoints", "psql", "-U", "moe_admin", "-d", "moe_userdb", "-At", "-c", sql],
                          capture_output=True, text=True, check=True)
    return json.loads(done.stdout.strip() or "null")


def answer_of(response_body: Any) -> str:
    """Text of an expert answer from an audit ``response_body`` (Ollama chat or OpenAI shape)."""
    if not isinstance(response_body, dict):
        return ""
    text = ((response_body.get("message") or {}).get("content")
            or (((response_body.get("choices") or [{}])[0]).get("message") or {}).get("content") or "")
    if "</think>" in text:
        text = text.split("</think>")[-1]
    return text.strip()


def subtask_of(request_body: Any) -> str:
    users = [m.get("content", "") for m in (request_body or {}).get("messages", []) if m.get("role") == "user"]
    text = users[-1] if users else ""
    if isinstance(text, str) and text.startswith("{'role'"):  # some audit rows store the message as a Python repr
        try:
            text = ast.literal_eval(text).get("content", text)
        except (ValueError, SyntaxError):
            pass
    return re.sub(r"^/no_think\s*", "", str(text)).strip()


def clean_messages(request_body: Any) -> List[Dict[str, str]]:
    """The recorded chat messages of an expert call, with message reprs unwrapped (used verbatim by the replay)."""
    out = []
    for m in (request_body or {}).get("messages", []):
        content = m.get("content", "")
        if isinstance(content, str) and content.startswith("{'role'"):
            try:
                content = ast.literal_eval(content).get("content", content)
            except (ValueError, SyntaxError):
                pass
        out.append({"role": m.get("role", "user"), "content": str(content)})
    return out


def load_sidecar(path: Path) -> List[Dict[str, Any]]:
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def collect(label: str, sidecar: Path, include_refinement: bool) -> List[Dict[str, Any]]:
    """Join the sidecar with the audit log and archive the expert answers."""
    rows = [r for r in load_sidecar(sidecar) if r.get("chatcmpl_id") and r.get("condition") != "native_baseline"]
    ids = sorted({r["chatcmpl_id"] for r in rows})
    if not ids:
        return []
    stages = "'expert','expert_refinement'" if include_refinement else "'expert'"
    quoted = ",".join("'" + i.replace("'", "''") + "'" for i in ids)
    audit = psql_json(
        "select json_agg(t) from (select audit_id, request_id, stage, model, endpoint, status, started_at, completed_at, "
        "prompt_tokens, completion_tokens, request_body, response_body from ai_io_audit_log "
        f"where request_id in ({quoted}) and stage in ({stages}) order by started_at) t") or []
    by_req: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for a in audit:
        by_req[a["request_id"]].append(a)
    out = []
    for r in rows:
        for a in by_req.get(r["chatcmpl_id"], []):
            out.append({
                "track": label,
                "condition": r["condition"],
                "stage_of_template": "pre-finetune" if str(r["condition"]).startswith("prefinetune") else "fine-tuned",
                "task_id": r["task_id"], "round": r.get("round"), "turn": r.get("turn"),
                "chatcmpl_id": r["chatcmpl_id"], "audit_id": a["audit_id"], "audit_stage": a["stage"],
                "category": (a.get("request_body") or {}).get("expert_category") or "unknown",
                "model": a["model"], "endpoint": a["endpoint"], "status": a["status"],
                "subtask": subtask_of(a.get("request_body")),
                "request_messages": clean_messages(a.get("request_body")),
                "request_options": (a.get("request_body") or {}).get("options") or {},
                "answer": answer_of(a.get("response_body")),
                "completion_tokens": a.get("completion_tokens"),
                "seconds": _seconds(a.get("started_at"), a.get("completed_at")),
            })
    archive = rsb.RESULTS_DIR / f"expert_outputs_{label}.jsonl"
    archive.write_text("".join(json.dumps(o, ensure_ascii=False) + "\n" for o in out))
    print(f"[collect] {label}: {len(rows)} orchestrated requests, {len(out)} expert answers -> {archive}")
    return out


def _seconds(a: Optional[str], b: Optional[str]) -> Optional[float]:
    try:
        f = lambda s: datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return round((f(b) - f(a)).total_seconds(), 1)
    except Exception:
        return None


def question_of(test_case: Dict[str, Any], turn: Optional[int]) -> str:
    if test_case.get("type") == "multi_turn":
        turns = test_case.get("turns", [])
        return "\n\n".join(f"[turn {t['turn']}] {t['prompt']}" for t in turns if turn is None or t["turn"] <= turn)
    return test_case.get("prompt", "")


def load_cache(path: Path) -> Dict[str, Dict[str, Any]]:
    cache: Dict[str, Dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                rec = json.loads(line)
                cache[f"{rec['audit_id']}|{rec['judge']}"] = rec
    return cache


async def score_all(answers: List[Dict[str, Any]], judges: List[str], concurrency: int, cache_path: Path,
                    cases: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    cache = load_cache(cache_path)
    limits = rsb.httpx.Limits(max_keepalive_connections=5, max_connections=10, keepalive_expiry=30.0)
    timeout_cfg = rsb.httpx.Timeout(connect=60.0, read=18000.0, write=60.0, pool=60.0)
    async with rsb.httpx.AsyncClient(timeout=timeout_cfg, limits=limits) as client:
        for judge in judges:  # one judge model at a time: the judge models do not fit the GPU together
            rsb.JUDGE_MODEL = judge
            todo = [a for a in answers if a["answer"] and f"{a['audit_id']}|{judge}" not in cache]
            print(f"[score] judge {judge}: {len(todo)} answers to grade ({len(answers) - len(todo)} cached/empty)", flush=True)
            sem = asyncio.Semaphore(concurrency)

            async def one(a: Dict[str, Any]) -> None:
                async with sem:
                    case = cases[a["task_id"]]
                    prompt = (
                        f"[ORIGINAL USER QUESTION]\n{question_of(case, a.get('turn'))}\n\n"
                        f"[SUB-TASK ASSIGNED TO THIS EXPERT (specialist role: {a['category']})]\n{a['subtask']}\n\n"
                        "Grade ONLY how correct, plausible and useful the expert answer is for ITS sub-task, measured against the "
                        "reference answer. Other experts cover the other parts of the question; do not penalise missing parts that "
                        "are outside this sub-task."
                    )
                    verdict = await rsb.judge_evaluation(client, case, prompt, a["answer"][:8000])
                    score = verdict.get("score", verdict.get("overall_score"))
                    rec = {"audit_id": a["audit_id"], "judge": judge, "score": score, "verdict": verdict.get("verdict"),
                           "reasoning": verdict.get("reasoning", "")}
                    with cache_path.open("a") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    cache[f"{a['audit_id']}|{judge}"] = rec

            await asyncio.gather(*(one(a) for a in todo))
    return cache


def mean_se(values: List[float]) -> tuple:
    if not values:
        return (float("nan"), float("nan"))
    m = statistics.mean(values)
    se = statistics.stdev(values) / math.sqrt(len(values)) if len(values) > 1 else float("nan")
    return (m, se)


def fmt(m: float, se: float, n: int) -> str:
    if n == 0:
        return "-"
    flag = "" if n >= MIN_N else " (n<%d)" % MIN_N
    return f"{m:.2f} ± {se:.2f} (n={n}){flag}" if not math.isnan(se) else f"{m:.2f} (n={n}){flag}"


def report(answers: List[Dict[str, Any]], cache: Dict[str, Dict[str, Any]], judges: List[str], out: Path) -> None:
    scored = []
    for a in answers:
        per_judge = {}
        for j in judges:
            rec = cache.get(f"{a['audit_id']}|{j}")
            if rec and isinstance(rec.get("score"), (int, float)) and rec.get("verdict") != "UNSCORED_FALLBACK":
                per_judge[j] = float(rec["score"])
        if per_judge:
            scored.append({**a, "per_judge": per_judge, "mean_score": statistics.mean(per_judge.values())})
    cells: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
    for s in scored:
        cells[(s["track"], s["stage_of_template"], s["category"])].append(s)
    tracks = sorted({s["track"] for s in scored})
    stages = ["pre-finetune", "fine-tuned"]
    cats = sorted({s["category"] for s in scored})
    md = ["# Expert answer scores", "",
          f"Generated {datetime.datetime.now(datetime.timezone.utc):%Y-%m-%d %H:%M UTC}. Scale 0-10 (harness judge scale). "
          f"Judges: {', '.join(judges)}; every answer is graded by all judges, the cell value is the mean of the judge means. "
          f"{len(scored)} of {len(answers)} answers scored.", "",
          "**Limits:** the planner decomposes each question separately per track and template, so tracks answer different sub-tasks; "
          f"cells with n<{MIN_N} are marked and not a basis for a comparison; scores are LLM-judge opinions against the task reference; "
          "one round of 8 benchmark questions.", ""]
    for stage in stages:
        md += [f"## {stage} templates: mean expert score per category", "",
               "| Category | " + " | ".join(tracks) + " | Δ (" + (tracks[-1] + " - " + tracks[0] if len(tracks) > 1 else "") + ") |",
               "|---|" + "---|" * (len(tracks) + 1)]
        for cat in cats:
            row, means = [], []
            for t in tracks:
                vals = [s["mean_score"] for s in cells.get((t, stage, cat), [])]
                m, se = mean_se(vals)
                row.append(fmt(m, se, len(vals)))
                means.append((m, len(vals)))
            delta = f"{means[-1][0] - means[0][0]:+.2f}" if len(means) > 1 and all(n >= MIN_N for _, n in means) else "n/a"
            md.append(f"| `{cat}` | " + " | ".join(row) + f" | {delta} |")
        allrow = []
        for t in tracks:
            vals = [s["mean_score"] for s in scored if s["track"] == t and s["stage_of_template"] == stage]
            m, se = mean_se(vals)
            allrow.append(fmt(m, se, len(vals)))
        md.append("| **all experts** | " + " | ".join(allrow) + " | |")
        md.append("")
    md += ["## Fine-tuning effect on the experts (fine-tuned minus pre-finetune, per track)", "",
           "| Category | " + " | ".join(tracks) + " |", "|---|" + "---|" * len(tracks)]
    for cat in cats + ["all experts"]:
        row = []
        for t in tracks:
            def vals(stage):
                return [s["mean_score"] for s in scored if s["track"] == t and s["stage_of_template"] == stage
                        and (cat == "all experts" or s["category"] == cat)]
            a, b = vals("fine-tuned"), vals("pre-finetune")
            row.append(f"{statistics.mean(a) - statistics.mean(b):+.2f} (n={len(a)}/{len(b)})" if len(a) >= MIN_N and len(b) >= MIN_N else f"n/a (n={len(a)}/{len(b)})")
        md.append(f"| `{cat}` | " + " | ".join(row) + " |")
    if len(judges) > 1:
        md += ["", "## Judge agreement", "", "| Track | Judge means (all answers) |", "|---|---|"]
        for t in tracks:
            parts = []
            for j in judges:
                vals = [s["per_judge"][j] for s in scored if s["track"] == t and j in s["per_judge"]]
                parts.append(f"{j.split('/')[-1]}: {statistics.mean(vals):.2f} (n={len(vals)})" if vals else f"{j.split('/')[-1]}: -")
            md.append(f"| {t} | " + "; ".join(parts) + " |")
    out.write_text("\n".join(md) + "\n")
    out.with_suffix(".json").write_text(json.dumps(scored, ensure_ascii=False, indent=1))
    print(f"[report] {out} ({len(scored)} scored answers)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sidecar", action="append", required=True, metavar="LABEL=PATH", help="e.g. spur1=results/sidecar_x.jsonl (repeatable)")
    ap.add_argument("--judges", default=",".join(DEFAULT_JUDGES))
    ap.add_argument("--concurrency", type=int, default=2)
    ap.add_argument("--collect-only", action="store_true")
    ap.add_argument("--include-refinement", action="store_true")
    args = ap.parse_args()

    dataset = json.loads(rsb.DATASET_PATH.read_text())
    cases = {c["id"]: c for c in dataset["test_cases"]}
    answers: List[Dict[str, Any]] = []
    for spec in args.sidecar:
        label, _, path = spec.partition("=")
        answers += collect(label, Path(path), args.include_refinement)
    if args.collect_only or not answers:
        return
    judges = [j.strip() for j in args.judges.split(",") if j.strip()]
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    cache = asyncio.run(score_all(answers, judges, args.concurrency, rsb.RESULTS_DIR / "expert_scores_cache.jsonl", cases))
    report(answers, cache, judges, rsb.RESULTS_DIR / f"expert_scores_{stamp}.md")


if __name__ == "__main__":
    main()
