#!/usr/bin/env python3
"""Counterfactual rollouts against a full MoE Sovereign research stack.

For each task: run one base episode through /v1/chat/completions with a caller-chosen research
thread id, read the thread's checkpoint history, and fork K times from every decision checkpoint
(the checkpoint *before* a decision node runs). Every episode answer is scored against the task's
reference, so each decision checkpoint gets a Monte-Carlo value estimate:
    V(checkpoint) = mean(correct over its K forks)
which is the step-level label for the orchestration PRM.

  rollout_driver.py --api http://127.0.0.1:8000 --key-file research.key --tasks tasks.jsonl \
      --template "LUMI-G OLMo + SmolLM3 Sovereign Ensemble" --out rollouts.jsonl --forks 4

The admin key for the research routes is read from the SYSTEM_API_KEY environment variable.
"""
import argparse
import asyncio
import json
import os
import re
import time
import uuid

import httpx

DECISION_NODES = {"planner", "workers", "merger", "self_critique", "critic", "resolve_conflicts", "thinking"}
STATE_KEYS = "plan,expert_results,final_response"
_NUM = re.compile(r"-?\d[\d.,']*\d|-?\d")


def parse_number(token: str, lang: str):
    """Parse 1.234,56 / 1,234.56 / 370,76 / 370.76. A lone separator followed by exactly three digits
    is read as a thousands separator in the request language's convention, otherwise as decimal mark."""
    t = token.replace("'", "")
    seps = [c for c in t if c in ".,"]
    if not seps:
        dec = None
    elif "," in seps and "." in seps:
        dec = "," if t.rfind(",") > t.rfind(".") else "."
    else:
        sep = seps[0]
        tail = t.rpartition(sep)[2]
        thousands_sep = "." if lang == "de" else ","
        dec = None if len(seps) > 1 or (sep == thousands_sep and len(tail) == 3) else sep
    group = {",": ".", ".": ","}.get(dec, "") if dec else ""
    t = t.replace(group, "") if group else t.replace(",", "").replace(".", "") if dec is None else t
    t = t.replace(",", ".") if dec == "," else t
    try:
        return float(t)
    except ValueError:
        return None


# The self-critique prompt (graph/synthesis.py) lets the model append "[Correction-Note: ...]" at the very end;
# its numbers (dates, intermediate values) are commentary, not the answer.
_NOTE = re.compile(r"\[\s*Correction-Note\s*:", re.IGNORECASE)
_MARKER = re.compile(r"(?:\b(?:final (?:result|answer)|endergebnis)\b\s*(?:is|ist|lautet)?\s*[:=]?"
                     r"|\b(?:result|answer|ergebnis|antwort)\b\s*\**\s*[:=])\s*\**", re.IGNORECASE)


def final_number(answer: str, lang: str):
    """The answer's final number: the first number after the last result marker of the closing passage,
    otherwise the last number; a trailing Correction-Note is removed first."""
    text = answer or ""
    notes = list(_NOTE.finditer(text))
    if notes:
        text = text[:notes[-1].start()]
    tail = text[-600:]
    markers = list(_MARKER.finditer(tail))
    if markers:
        after = [n for n in (parse_number(m, lang) for m in _NUM.findall(tail[markers[-1].end():])) if n is not None]
        if after:
            return after[0]
    nums = [n for n in (parse_number(m, lang) for m in _NUM.findall(tail)) if n is not None]
    return nums[-1] if nums else None


def correct(answer: str, ref: float, tol: float, lang: str) -> bool:
    """True when the answer's final number matches the reference."""
    n = final_number(answer, lang)
    return n is not None and abs(n - ref) <= max(tol, 1e-9) + 1e-9


_WS = re.compile(r"\s+")


def _norm(t: str) -> str:
    t = re.sub(r"\b(a|an|the)\b", " ", (t or "").lower())
    return _WS.sub(" ", re.sub(r"[^\w\s]", " ", t)).strip()


def score(answer: str, final: dict, lang: str) -> bool:
    """Task-level scoring: numeric (default) or text (normalised containment of the gold answer or an alias;
    final['all'] requires every listed answer, for list answers)."""
    if final.get("kind") != "text":
        return correct(answer, final["ref"], final["tol"], lang)
    hay = _norm(answer)
    hit = [bool(a) and re.search(rf"(?<!\w){re.escape(_norm(a))}(?!\w)", hay) is not None for a in map(str, final["answers"])]
    return all(hit) if final.get("all") else any(hit)


def _err(exc: BaseException) -> str:
    # httpx timeouts stringify to "" (wave 2 logged 369 empty errors); keep the exception type
    return f"{type(exc).__name__}: {exc}"


class Client:
    def __init__(self, api: str, user_key: str, admin_key: str, timeout: float):
        self.api = api.rstrip("/")
        self.http = httpx.AsyncClient(timeout=timeout)
        self.user_h = {"Authorization": f"Bearer {user_key}"}
        self.admin_h = {"Authorization": f"Bearer {admin_key}"}

    async def approve(self, gate_id: str) -> str:
        """Release a HITL gate's draft. Nobody approves on LUMI; like run_scientific_benchmark.py the
        draft is auto-approved and the outcome is labelled 'hitl' so it can be filtered later."""
        r = await self.http.post(f"{self.api}/gates/{gate_id}/approve", headers=self.user_h)
        r.raise_for_status()
        return r.json().get("response_draft", "")

    async def episode(self, template: str, prompt: str, thread: str, temperature: float):
        """Returns (answer, outcome, usage). outcome: 'answer' | 'hitl' (auto-approved draft) |
        'blocked:<reason>' (quality gate withheld the response) | 'failed:<cause>' (pipeline raised);
        blocked and failed are scored as wrong."""
        body = {"model": template, "messages": [{"role": "user", "content": prompt}], "stream": False,
                "temperature": temperature, "no_cache": True}
        r = await self.http.post(f"{self.api}/v1/chat/completions", json=body, headers=self.user_h | {"X-MoE-Research-Thread": thread})
        if r.status_code == 422 and (r.json().get("error") or {}).get("type") == "quality_blocked":
            return "", f"blocked:{r.json()['error'].get('code', '')}", {}
        if r.status_code == 500 and (r.json().get("error") or {}).get("code") == "orchestration_failed":
            return "", "failed:orchestration_failed", {}  # pipeline raised (e.g. PlannerContractError); scored wrong
        r.raise_for_status()
        data = r.json()
        if r.status_code == 202 and data.get("gate_id"):
            return await self.approve(data["gate_id"]), "hitl", {}
        if data.get("moe_thread_id") != thread:
            raise RuntimeError("research thread id not honoured: is RESEARCH_ROUTES_ENABLED set on the stack?")
        return data["choices"][0]["message"]["content"], "answer", data.get("usage", {})

    async def history(self, thread: str, keys: str = STATE_KEYS):
        r = await self.http.get(f"{self.api}/v1/admin/research/threads/{thread}/history", params={"keys": keys}, headers=self.admin_h)
        r.raise_for_status()
        return r.json()["checkpoints"]

    async def fork(self, thread: str, checkpoint_id: str, temperature: float):
        r = await self.http.post(f"{self.api}/v1/admin/research/threads/{thread}/fork",
                                 json={"checkpoint_id": checkpoint_id, "overrides": {"query_temperature": temperature}}, headers=self.admin_h)
        r.raise_for_status()
        f = r.json()
        if f.get("pipeline_error"):
            return "", f"failed:{f['pipeline_error'].split(':')[0]}", f
        if f.get("hitl_gate_id"):
            return await self.approve(f["hitl_gate_id"]), "hitl", f
        if f.get("quality_blocked"):
            return "", f"blocked:{f.get('quality_block_reason') or ''}", f
        return f["final_response"], "answer", f


async def run_task(cli: Client, task: dict, a, sem: asyncio.Semaphore, out):
    async with sem:
        lang = task["lang"]
        # ablation tasks name their arm; for those the arm decides whether the data is in the prompt
        inline = task["arm"] == "inline" if "arm" in task else a.inline
        prompt = (task.get("prompts_inline") or task["prompts"])[lang] if inline else task["prompts"][lang]
        thread = f"research-{task['id'][-12:]}-{uuid.uuid4().hex[:8]}"
        final = task["final"]
        rec = {"task_id": task["id"], "family": task["family"], "arm": task.get("arm"), "lang": lang, "thread": thread, "template": a.template,
               "started": time.time(), "base": None, "decision_points": [], "errors": []}
        try:
            t0 = time.monotonic()
            answer, outcome, usage = await cli.episode(a.template, prompt, thread, a.temperature)
            rec["base"] = {"answer": answer, "outcome": outcome, "correct": score(answer, final, lang), "usage": usage,
                           "seconds": time.monotonic() - t0}
            extra = getattr(a, "capture", "")
            hist = await cli.history(thread, STATE_KEYS + ("," + extra if extra else ""))
            if hist:  # the draft before the quality gate: a blocked episode still has a final_response in its end state
                draft = hist[0]["values"].get("final_response") or ""
                rec["base"]["draft"] = draft
                rec["base"]["draft_correct"] = bool(draft) and score(draft, final, lang)
            if extra and hist:
                rec["captured"] = {k: hist[0]["values"].get(k) for k in extra.split(",")}  # newest checkpoint = end state
            nodes = set(a.fork_nodes.split(",")) if getattr(a, "fork_nodes", "") else DECISION_NODES  # a planner-only wave forks just the planning step
            base_cps = [c for c in hist if set(c["next"]) & nodes] if a.forks else []
            for cp in reversed(base_cps):  # chronological order
                point = {"checkpoint_id": cp["checkpoint_id"], "step": cp["step"], "next": cp["next"], "state": cp["values"], "forks": []}
                for _ in range(a.forks):
                    try:
                        answer, outcome, f = await cli.fork(thread, cp["checkpoint_id"], a.temperature)
                        point["forks"].append({"answer": answer, "outcome": outcome, "correct": score(answer, final, lang),
                                               "final_checkpoint_id": f["final_checkpoint_id"], "plan": f.get("plan")})
                    except (httpx.HTTPError, KeyError) as exc:
                        rec["errors"].append(f"fork {cp['checkpoint_id']}: {_err(exc)[:200]}")
                ok = [x["correct"] for x in point["forks"]]
                point["value"] = sum(ok) / len(ok) if ok else None
                rec["decision_points"].append(point)
        except (httpx.HTTPError, RuntimeError, KeyError) as exc:
            rec["errors"].append(f"episode: {_err(exc)[:300]}")
        rec["finished"] = time.time()
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out.flush()
        return rec


async def main(a):
    tasks = [json.loads(l) for l in open(a.tasks, encoding="utf-8")]
    if a.families:
        tasks = [t for t in tasks if t["family"] in a.families.split(",")]
    tasks = tasks[a.offset:a.offset + a.n] if a.n else tasks[a.offset:]
    tasks = tasks * a.repeats
    cli = Client(a.api, open(a.key_file).read().strip(), os.environ["SYSTEM_API_KEY"], a.timeout)
    sem = asyncio.Semaphore(a.concurrency)
    t0 = time.monotonic()
    with open(a.out, "a", encoding="utf-8") as out:
        recs = await asyncio.gather(*(run_task(cli, t, a, sem, out) for t in tasks))
    base = [r["base"]["correct"] for r in recs if r["base"]]
    forks = sum(len(p["forks"]) for r in recs for p in r["decision_points"])
    outcomes = {}
    for r in recs:
        for o in ([r["base"]["outcome"]] if r["base"] else []) + [f["outcome"] for p in r["decision_points"] for f in p["forks"]]:
            k = o.split(":")[0]
            outcomes[k] = outcomes.get(k, 0) + 1
    print(json.dumps({"tasks": len(recs), "base_ok": sum(base), "base_n": len(base), "forks": forks, "outcomes": outcomes,
                      "errors": sum(len(r["errors"]) for r in recs), "seconds": round(time.monotonic() - t0)}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", required=True)
    ap.add_argument("--key-file", required=True)
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--template", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--forks", type=int, default=4)
    ap.add_argument("--fork-nodes", default="", help="comma list of nodes to fork at (default: all decision nodes), e.g. planner")
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--concurrency", type=int, default=16)
    ap.add_argument("--timeout", type=float, default=1800)
    ap.add_argument("--n", type=int, default=0)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--families", default="")
    ap.add_argument("--repeats", type=int, default=1, help="run every task this many times (own thread each)")
    ap.add_argument("--capture", default="", help="extra state keys to keep from the end state, e.g. graph_context")
    ap.add_argument("--inline", action=argparse.BooleanOptionalAction, default=True)
    asyncio.run(main(ap.parse_args()))
