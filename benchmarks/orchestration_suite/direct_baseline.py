#!/usr/bin/env python3
"""Single-model baseline for the orchestration tasks: the same prompts answered by one dense model, no planner, no tools,
no experts, no gate. It is the control for the workflow runs on the same tasks (same weights as the workflow's judge).

  direct_baseline.py --tasks tasks.jsonl --urls http://127.0.0.1:8101,http://127.0.0.1:8111 --model <served name> \
      --offset 7200 --n 491 --repeats 3 --out direct.jsonl

Records match the driver's shape closely enough for the same analysis: {task_id, family, arm, lang, base:{answer, draft,
correct, draft_correct}}; there is no gate, so draft_correct == correct.
"""
import argparse
import asyncio
import importlib.util
import itertools
import json
import time
from pathlib import Path

import httpx

_spec = importlib.util.spec_from_file_location("rollout_driver", Path(__file__).resolve().parent / "rollout_driver.py")
drv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(drv)


async def main(a):
    tasks = [json.loads(l) for l in open(a.tasks, encoding="utf-8")][a.offset:a.offset + a.n if a.n else None]
    urls = itertools.cycle(a.urls.split(","))
    sem = asyncio.Semaphore(a.concurrency)
    out = open(a.out, "w", encoding="utf-8")
    done = {"n": 0, "correct": 0, "errors": 0}

    async def one(http, task, rep):
        async with sem:
            lang = task["lang"]
            prompt = (task.get("prompts_inline") or task["prompts"])[lang]
            rec = {"task_id": task["id"], "family": task["family"], "arm": f"direct:{a.label}", "lang": lang, "repeat": rep,
                   "started": time.time(), "base": None, "errors": []}
            try:
                r = await http.post(f"{next(urls)}/v1/chat/completions", json={
                    "model": a.model, "messages": [{"role": "user", "content": prompt}],
                    "temperature": a.temperature, "max_tokens": a.max_tokens})
                r.raise_for_status()
                body = r.json()
                text = body["choices"][0]["message"].get("content") or ""
                ok = bool(text) and drv.score(text, task["final"], lang)
                rec["base"] = {"answer": text, "draft": text, "correct": ok, "draft_correct": ok, "outcome": "answer",
                               "usage": body.get("usage")}
                done["correct"] += ok
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                rec["errors"].append(f"{type(exc).__name__}: {exc}"[:300])
                done["errors"] += 1
            rec["finished"] = time.time()
            done["n"] += 1
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()

    async with httpx.AsyncClient(timeout=a.timeout) as http:
        await asyncio.gather(*(one(http, t, r) for r in range(a.repeats) for t in tasks))
    print(json.dumps({"tasks": len(tasks), "repeats": a.repeats, **done, "accuracy": round(done["correct"] / max(done["n"], 1), 4)}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--urls", required=True, help="comma list of OpenAI-compatible base URLs (replicas are used round-robin)")
    ap.add_argument("--model", required=True)
    ap.add_argument("--label", default="judge32b")
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--n", type=int, default=0)
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--temperature", type=float, default=0.7)
    ap.add_argument("--max-tokens", type=int, default=3000)
    ap.add_argument("--concurrency", type=int, default=16)
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--out", required=True)
    asyncio.run(main(ap.parse_args()))
