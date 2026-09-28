#!/usr/bin/env python3
"""One iteration of the expert-template optimisation loop on a single benchmark task.

Runs the orchestrated template on one task exactly as the scientific benchmark does (``run_single_test_condition``: same
request, same deterministic + judge scoring), repeats it, and writes everything needed to decide the next template change:
scores, planner plan, MCP tool calls, every expert prompt and answer (from ``ai_io_audit_log``), the merger call and the
orchestrator log lines of the request.

Guard: before every run the models, endpoints and planner/judge assignments of the template must equal the frozen
snapshot (``--freeze`` writes it). A model swap or a pinning change aborts the iteration.

  python3 benchmarks/optimize_expert_template.py --template "<name>" --freeze
  python3 benchmarks/optimize_expert_template.py --template "<name>" --task sci-precision-01-vlsm-subnetting --label iter0 --repeats 2
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
SNAPSHOT_DIR = BASE / "results" / "opt"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)


def psql_json(sql: str):
    done = subprocess.run(["docker", "exec", "terra_checkpoints", "psql", "-U", "moe_admin", "-d", "moe_userdb", "-At", "-c", sql],
                          capture_output=True, text=True, check=True)
    return json.loads(done.stdout.strip() or "null")


def load_template(name: str) -> dict:
    row = psql_json("select row_to_json(t) from (select id, config_json from admin_expert_templates where name='%s') t" % name.replace("'", "''"))
    if not row:
        raise SystemExit(f"template not found: {name}")
    return json.loads(row["config_json"])


def pinned(cfg: dict) -> dict:
    """What must never change during the optimisation: models, endpoints, roles of every slot, planner and judge model@endpoint."""
    return {
        "planner_model": cfg["planner_model"], "judge_model": cfg["judge_model"],
        "experts": {c: [(m["model"], m["endpoint"], m.get("role"), bool(m.get("forced"))) for m in ec["models"]] for c, ec in cfg["experts"].items()},
    }


def snapshot_path(template: str) -> Path:
    return SNAPSHOT_DIR / ("frozen_" + re.sub(r"[^A-Za-z0-9]+", "_", template) + ".json")


def refresh_template_cache(api: str, key: str) -> None:
    """Templates are cached for 30 s stale-while-revalidate; the first request after an edit still sees the old one."""
    import urllib.request
    body = json.dumps({"model": "refresh-trigger", "messages": [{"role": "user", "content": "x"}]}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(api + "/v1/chat/completions", data=body,
                               headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}), timeout=60).read()
    except Exception:
        pass
    time.sleep(40)


def audit_rows(chatcmpl_id: str) -> list:
    return psql_json(
        "select json_agg(t) from (select stage, model, endpoint, status, started_at, completed_at, request_body, response_body "
        "from ai_io_audit_log where request_id='%s' order by started_at) t" % chatcmpl_id) or []


def answer(row: dict) -> str:
    body = row.get("response_body") or {}
    return ((body.get("message") or {}).get("content") or (((body.get("choices") or [{}])[0]).get("message") or {}).get("content") or "").strip()


def log_lines(chatcmpl_id: str, since: str) -> list:
    short = chatcmpl_id.replace("chatcmpl-", "")[:8]
    out = subprocess.run(["docker", "logs", "--since", since, "langgraph-orchestrator"], capture_output=True, text=True)
    pat = re.compile(r"PLANNER RAW|MCP|mcp|precision|Precision|trust|Trust|quality|Quality|Plan \(|CONTRACT|critic|Critic|self.critique|verdict|blocked|withheld|ERROR|WARNING.*(expert|planner)", re.I)
    return [re.sub(r"^\d{4}-\d\d-\d\d ", "", l)[:400] for l in (out.stdout + out.stderr).splitlines() if short in l and pat.search(l)]


async def run(args) -> None:
    os.environ.setdefault("MOE_JUDGE_MODEL", args.judge)
    sys.path.insert(0, str(BASE))
    import run_scientific_benchmark as rsb

    cfg = load_template(args.template)
    snap = snapshot_path(args.template)
    if args.freeze:
        snap.write_text(json.dumps(pinned(cfg), indent=1))
        (SNAPSHOT_DIR / ("backup_original_" + snap.name)).write_text(json.dumps(cfg, indent=1))
        print(f"frozen: {snap}")
        return
    if json.loads(snap.read_text()) != json.loads(json.dumps(pinned(cfg))):
        raise SystemExit("ABORT: models/endpoints/planner/judge of the template differ from the frozen snapshot")
    (SNAPSHOT_DIR / f"{args.label}_template.json").write_text(json.dumps(cfg, indent=1, ensure_ascii=False))
    refresh_template_cache(rsb.ORCHESTRATOR_URL, rsb.API_KEY)
    cases = {c["id"]: c for c in json.loads(rsb.DATASET_PATH.read_text())["test_cases"]}
    case = cases[args.task]
    results = []
    async with rsb.httpx.AsyncClient(timeout=rsb.httpx.Timeout(connect=60.0, read=18000.0, write=60.0, pool=60.0)) as client:
        for r in range(1, args.repeats + 1):
            run_id = f"opt-{args.label}-r{r}"
            since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 5))
            res = await rsb.run_single_test_condition(client, case, "compound_ai", args.template, r, run_id)
            side = [json.loads(l) for l in (rsb.RESULTS_DIR / f"sidecar_{run_id}.jsonl").read_text().splitlines()] if (rsb.RESULTS_DIR / f"sidecar_{run_id}.jsonl").exists() else []
            cid = side[-1]["chatcmpl_id"] if side else None
            rows = audit_rows(cid) if cid else []
            diag = {
                "chatcmpl_id": cid,
                "planner": [answer(x)[:1500] for x in rows if x["stage"] == "planner"],
                "experts": [{"category": (x.get("request_body") or {}).get("expert_category"), "model": x["model"], "endpoint": x["endpoint"],
                             "subtask": (x.get("request_body") or {}).get("messages", [{}])[-1].get("content", "")[:600],
                             "system_prompt_head": ((x.get("request_body") or {}).get("messages", [{}])[0].get("content", ""))[:400],
                             "answer": answer(x)} for x in rows if x["stage"] == "expert"],
                "merger": [{"stage": x["stage"], "model": x["model"], "answer": answer(x)[:3000]} for x in rows if x["stage"] in ("judge", "judge_background")],
                "log": log_lines(cid, "40m") if cid else [],
            }
            results.append({"repeat": r, "result": {k: res.get(k) for k in ("score", "deterministic_score", "judge_score", "judge_verdict", "judge_reasoning", "trust_score", "trust_verdict", "total_time_s", "total_tokens")},
                            "final_response": (res.get("turns") or [{}])[-1].get("response", ""), "diagnostics": diag})
            print(f"[{args.label} r{r}] score={res.get('score')} det={res.get('deterministic_score')} judge={res.get('judge_score')} "
                  f"verdict={res.get('judge_verdict')} trust={res.get('trust_verdict')} time={res.get('total_time_s')}s", flush=True)
    out = SNAPSHOT_DIR / f"{args.label}.json"
    out.write_text(json.dumps({"template": args.template, "task": args.task, "results": results}, indent=1, ensure_ascii=False))
    print(f"written: {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--template", required=True)
    ap.add_argument("--task", default="sci-precision-01-vlsm-subnetting")
    ap.add_argument("--label", default="iter0")
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--freeze", action="store_true")
    ap.add_argument("--judge", default="hf.co/h3rb3rn/sovereign-judge-olmo31-32b:Q4_K_M")
    asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    main()
