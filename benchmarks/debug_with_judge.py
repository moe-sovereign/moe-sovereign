#!/usr/bin/env python3
"""Let the fine-tuned Open Source judge LLM analyse planner / MCP failures of an optimisation run.

Reads ``results/opt/<label>.json`` (written by optimize_expert_template.py), takes every repeat that scored below the
threshold, collects the evidence (user request, the template planner prompt, every planner attempt from
``ai_io_audit_log``, the MCP tool results from the orchestrator log, the final answer) and asks the judge model, and only
that model, for root causes and generic planner-prompt rules. Nothing task-specific is asked for or accepted.

  python3 benchmarks/debug_with_judge.py --label dbg1 [--below 9.0]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
JUDGE = "hf.co/h3rb3rn/sovereign-judge-olmo31-32b:Q4_K_M@N04-RTX"


def psql_json(sql: str):
    done = subprocess.run(["docker", "exec", "terra_checkpoints", "psql", "-U", "moe_admin", "-d", "moe_userdb", "-At", "-c", sql],
                          capture_output=True, text=True, check=True)
    return json.loads(done.stdout.strip() or "null")


def planner_attempts(chatcmpl_id: str) -> list:
    rows = psql_json("select json_agg(t) from (select response_body from ai_io_audit_log where request_id='%s' and stage='planner' order by started_at) t" % chatcmpl_id) or []
    return [((r.get("response_body") or {}).get("message") or {}).get("content", "") for r in rows]


def mcp_lines(chatcmpl_id: str, since: str = "3h") -> list:
    short = chatcmpl_id.replace("chatcmpl-", "")[:8]
    out = subprocess.run(["docker", "logs", "--since", since, "langgraph-orchestrator"], capture_output=True, text=True)
    keep = re.compile(r"MCP: \[|Trust-Score|Quality gate|structured failure|malformed|Plan \(")
    lines = []
    for l in (out.stdout + out.stderr).splitlines():
        if short in l and keep.search(l):
            lines.append(re.sub(r"^[0-9-]+ [0-9:,]+ - [A-Z]+ - \[chatcmpl-[a-z0-9-]+\] - ", "", l)[:260])
    return list(dict.fromkeys(lines))


def evidence(entry: dict, task: dict, planner_prompt: str) -> str:
    diag = entry["diagnostics"]
    cid = diag.get("chatcmpl_id")
    attempts = planner_attempts(cid) if cid else []
    parts = [
        f"REQUEST GIVEN TO THE SYSTEM:\n{task['prompt']}",
        f"SCORE: {entry['result']['score']} (deterministic {entry['result']['deterministic_score']}, verdict {entry['result']['judge_verdict']})",
        "PLANNER ATTEMPTS (raw output, in order):\n" + "\n".join(f"--- attempt {i + 1} ---\n{a[:2500]}" for i, a in enumerate(attempts)),
        "TOOL RESULTS AND PIPELINE EVENTS:\n" + "\n".join(mcp_lines(cid)) if cid else "TOOL RESULTS: none recorded",
        "FINAL ANSWER (start):\n" + (entry.get("final_response") or "")[:1200],
    ]
    return "\n\n".join(parts)


async def ask(prompt: str) -> str:
    import run_scientific_benchmark as rsb
    async with rsb.httpx.AsyncClient(timeout=rsb.httpx.Timeout(connect=60.0, read=1800.0, write=60.0, pool=60.0)) as client:
        res = await rsb.query_moe_orchestrator(client, JUDGE, [{"role": "user", "content": prompt}], temperature=0.2, max_tokens=2500)
    return res.get("content", "") if res.get("ok") else f"[judge call failed: {res.get('error')}]"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--below", type=float, default=9.0)
    ap.add_argument("--template", default="LUMI-G OLMo + SmolLM3 Sovereign Ensemble - No-GraphRAG")
    args = ap.parse_args()
    run = json.loads((BASE / "results" / "opt" / f"{args.label}.json").read_text())
    import run_scientific_benchmark as rsb
    task = next(c for c in json.loads(rsb.DATASET_PATH.read_text())["test_cases"] if c["id"] == run["task"])
    cfg = json.loads(psql_json("select row_to_json(t) from (select config_json from admin_expert_templates where name='%s') t" % args.template)["config_json"])
    bad = [r for r in run["results"] if (r["result"]["score"] or 0) < args.below]
    print(f"{len(bad)} of {len(run['results'])} repeats below {args.below}")
    if not bad:
        return
    cases = "\n\n=========== FAILURE CASE ===========\n".join(evidence(r, task, cfg["planner_prompt"]) for r in bad)
    prompt = (
        "You debug a small planner LLM inside a multi-expert pipeline. The planner must split a user request into JSON tasks; "
        "calculation tasks call the tool `calculate` with ONE self-contained arithmetic string expression (numbers and operators, "
        "parentheses allowed, no variables, no ^). Each task runs independently. A failed task blocks the whole answer; a wrong "
        "expression gives a wrong number.\n\n"
        f"THE PLANNER'S CURRENT PROMPT:\n{cfg['planner_prompt']}\n\n"
        f"{len(bad)} FAILURE CASE(S) FOLLOW (same request each time):\n\n=========== FAILURE CASE ===========\n{cases}\n\n"
        "TASK:\n1. For every failure case name the root cause in one sentence and quote the faulty expression or output.\n"
        "2. Group the causes and rank them by how often they occur.\n"
        "3. Propose at most 5 SHORT, GENERIC rules for the planner prompt that would prevent these causes. Do not use numbers, "
        "figures or wording from this specific request and do not state any answer.\n"
        "4. Name rules in the current prompt that are unclear, redundant or contradictory and should be removed.\n"
        "Be concrete and concise."
    )
    analysis = asyncio.run(ask(prompt))
    out = BASE / "results" / "opt" / f"{args.label}_judge_analysis.md"
    out.write_text(analysis)
    print(analysis)
    print(f"\n[written: {out}]")


if __name__ == "__main__":
    main()
