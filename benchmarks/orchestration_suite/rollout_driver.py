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


def correct(answer: str, ref: float, tol: float, lang: str) -> bool:
    """True when the last number in the closing passage matches the reference."""
    tail = (answer or "")[-600:]
    nums = [n for n in (parse_number(m, lang) for m in _NUM.findall(tail)) if n is not None]
    return bool(nums) and abs(nums[-1] - ref) <= max(tol, 1e-9) + 1e-9


class Client:
    def __init__(self, api: str, user_key: str, admin_key: str, timeout: float):
        self.api = api.rstrip("/")
        self.http = httpx.AsyncClient(timeout=timeout)
        self.user_h = {"Authorization": f"Bearer {user_key}"}
        self.admin_h = {"Authorization": f"Bearer {admin_key}"}

    async def episode(self, template: str, prompt: str, thread: str, temperature: float):
        body = {"model": template, "messages": [{"role": "user", "content": prompt}], "stream": False,
                "temperature": temperature, "no_cache": True}
        r = await self.http.post(f"{self.api}/v1/chat/completions", json=body, headers=self.user_h | {"X-MoE-Research-Thread": thread})
        r.raise_for_status()
        data = r.json()
        if data.get("moe_thread_id") != thread:
            raise RuntimeError("research thread id not honoured: is RESEARCH_ROUTES_ENABLED set on the stack?")
        return data["choices"][0]["message"]["content"], data.get("usage", {})

    async def history(self, thread: str):
        r = await self.http.get(f"{self.api}/v1/admin/research/threads/{thread}/history", params={"keys": STATE_KEYS}, headers=self.admin_h)
        r.raise_for_status()
        return r.json()["checkpoints"]

    async def fork(self, thread: str, checkpoint_id: str, temperature: float):
        r = await self.http.post(f"{self.api}/v1/admin/research/threads/{thread}/fork",
                                 json={"checkpoint_id": checkpoint_id, "overrides": {"query_temperature": temperature}}, headers=self.admin_h)
        r.raise_for_status()
        return r.json()


async def run_task(cli: Client, task: dict, a, sem: asyncio.Semaphore, out):
    async with sem:
        lang = task["lang"]
        prompt = (task.get("prompts_inline") or task["prompts"])[lang] if a.inline else task["prompts"][lang]
        thread = f"research-{task['id'][-12:]}-{uuid.uuid4().hex[:8]}"
        ref, tol = task["final"]["ref"], task["final"]["tol"]
        rec = {"task_id": task["id"], "family": task["family"], "lang": lang, "thread": thread, "template": a.template,
               "started": time.time(), "base": None, "decision_points": [], "errors": []}
        try:
            t0 = time.monotonic()
            answer, usage = await cli.episode(a.template, prompt, thread, a.temperature)
            rec["base"] = {"answer": answer, "correct": correct(answer, ref, tol, lang), "usage": usage, "seconds": time.monotonic() - t0}
            hist = await cli.history(thread)
            base_cps = [c for c in hist if set(c["next"]) & DECISION_NODES]
            for cp in reversed(base_cps):  # chronological order
                point = {"checkpoint_id": cp["checkpoint_id"], "step": cp["step"], "next": cp["next"], "state": cp["values"], "forks": []}
                for _ in range(a.forks):
                    try:
                        f = await cli.fork(thread, cp["checkpoint_id"], a.temperature)
                        point["forks"].append({"answer": f["final_response"], "correct": correct(f["final_response"], ref, tol, lang),
                                               "final_checkpoint_id": f["final_checkpoint_id"]})
                    except (httpx.HTTPError, KeyError) as exc:
                        rec["errors"].append(f"fork {cp['checkpoint_id']}: {str(exc)[:200]}")
                ok = [x["correct"] for x in point["forks"]]
                point["value"] = sum(ok) / len(ok) if ok else None
                rec["decision_points"].append(point)
        except (httpx.HTTPError, RuntimeError, KeyError) as exc:
            rec["errors"].append(f"episode: {str(exc)[:300]}")
        rec["finished"] = time.time()
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out.flush()
        return rec


async def main(a):
    tasks = [json.loads(l) for l in open(a.tasks, encoding="utf-8")]
    if a.families:
        tasks = [t for t in tasks if t["family"] in a.families.split(",")]
    tasks = tasks[a.offset:a.offset + a.n] if a.n else tasks[a.offset:]
    cli = Client(a.api, open(a.key_file).read().strip(), os.environ["SYSTEM_API_KEY"], a.timeout)
    sem = asyncio.Semaphore(a.concurrency)
    t0 = time.monotonic()
    with open(a.out, "a", encoding="utf-8") as out:
        recs = await asyncio.gather(*(run_task(cli, t, a, sem, out) for t in tasks))
    base = [r["base"]["correct"] for r in recs if r["base"]]
    forks = sum(len(p["forks"]) for r in recs for p in r["decision_points"])
    print(json.dumps({"tasks": len(recs), "base_ok": sum(base), "base_n": len(base), "forks": forks,
                      "errors": sum(len(r["errors"]) for r in recs), "seconds": round(time.monotonic() - t0)}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", required=True)
    ap.add_argument("--key-file", required=True)
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--template", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--forks", type=int, default=4)
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--concurrency", type=int, default=16)
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--n", type=int, default=0)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--families", default="")
    ap.add_argument("--inline", action=argparse.BooleanOptionalAction, default=True)
    asyncio.run(main(ap.parse_args()))
