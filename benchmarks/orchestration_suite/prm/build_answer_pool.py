#!/usr/bin/env python3
"""Pool the answers of several runs on the same tasks into verifier input rows, with the label for evaluation.

Each input is `name=path`: a jsonl of rollout_driver.py / direct_baseline.py records (`base.answer`/`base.draft`, `correct`,
`outcome`). The prompt is the one the run used (`prompts_inline` when present, like the driver's default, else `prompts`).
Row id: `<task id>#<name><k>` (k counts the records of that run for the task); `q` is 1.0/0.0 (answer correct, or the withheld
draft for a blocked episode).

  build_answer_pool.py --tasks ext_tasks.jsonl --out pool.jsonl direct=ext/direct.jsonl wf_on=ext/wf_on.jsonl wf_off=ext/wf_off.jsonl
"""
import argparse
import collections
import json


def effective(base: dict) -> bool:
    return bool(base["correct"]) if base["outcome"].split(":")[0] in ("answer", "hitl") else bool(base.get("draft_correct"))


def build(tasks: dict, runs: dict) -> list:
    rows, counter = [], collections.Counter()
    for name, path in runs.items():
        for line in open(path, encoding="utf-8"):
            r = json.loads(line)
            b, t = r.get("base"), tasks.get(r["task_id"])
            if not b or not t:
                continue
            answer = b.get("answer") or b.get("draft") or ""
            if not answer:
                continue
            k = counter[(name, r["task_id"])]
            counter[(name, r["task_id"])] += 1
            prompt = (t.get("prompts_inline") or t["prompts"])[t["lang"]]
            rows.append({"id": f"{r['task_id']}#{name}{k}", "task_id": r["task_id"], "src": name, "family": t["family"],
                         "prompt": prompt, "answer": answer, "q": 1.0 if effective(b) else 0.0})
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("runs", nargs="+", help="name=path")
    a = ap.parse_args()
    tasks = {t["id"]: t for t in map(json.loads, open(a.tasks, encoding="utf-8"))}
    rows = build(tasks, dict(r.split("=", 1) for r in a.runs))
    with open(a.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps({"rows": len(rows), "tasks": len({r["task_id"] for r in rows}), "positive": round(sum(r["q"] for r in rows) / max(len(rows), 1), 3)}))


if __name__ == "__main__":
    main()
