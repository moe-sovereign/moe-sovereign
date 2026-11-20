#!/usr/bin/env python3
"""Teacher traces for the planner interview: a large model answers the exact interview prompt the small planner will see.

Accepted trace (rule by task kind, no judgement by a model):
  public / hop   2 of 2 samples parse, 1..max questions, the first question shares a content word with the request (is on topic)
  infra / inline 2 of 2 samples parse and ask nothing (the facts or the data in the request are enough; private data is never asked)
The accepted completion is the first sample; the rejection reasons are counted, so the yield is a reported result.

  generate_traces.py --tasks train.jsonl --model Qwen/Qwen3-Next-80B-A3B-Instruct --out traces.jsonl [--limit N]
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
# The LUMI container has no psycopg etc.; services/pipeline/__init__ imports the whole request pipeline. interview.py and contracts.py
# are pure, so register the package without running its __init__ (job 23875194 died on `import psycopg`).
import types  # noqa: E402
if "services.pipeline" not in sys.modules:
    _pkg = types.ModuleType("services.pipeline")
    _pkg.__path__ = [str(ROOT / "services" / "pipeline")]
    sys.modules["services.pipeline"] = _pkg
from services.pipeline.interview import build_interview_prompt, completion_for, parse_questions  # noqa: E402

WORD = re.compile(r"[A-Za-zÄÖÜäöüß0-9]{4,}")


def on_topic(q: str, request: str) -> bool:
    return bool({w.lower() for w in WORD.findall(q)} & {w.lower() for w in WORD.findall(request)})


def judge(task: dict, samples: list, max_q: int):
    """(accepted completion questions or None, reason)."""
    parsed = [parse_questions(s, max_q) for s in samples]
    if any(p is None for p in parsed):
        return None, "unparseable"
    if task["needs_knowledge"]:
        if any(not p for p in parsed):
            return None, "asked_nothing"
        if not on_topic(parsed[0][0], task["request"]):
            return None, "off_topic"
    elif any(p for p in parsed):
        return None, "asked_when_not_needed"
    return parsed[0], "accepted"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-questions", type=int, default=3)
    ap.add_argument("--tp", type=int, default=8)
    ap.add_argument("--max-model-len", type=int, default=8192)
    ap.add_argument("--gpu-mem", type=float, default=0.9)
    a = ap.parse_args()
    tasks = [json.loads(l) for l in open(a.tasks, encoding="utf-8")]
    tasks = tasks[:a.limit] if a.limit else tasks
    prompts = [build_interview_prompt(t["request"], t["known_facts"], t["gaps"], a.max_questions) for t in tasks]
    from vllm import LLM, SamplingParams  # LUMI only
    llm = LLM(model=a.model, dtype="auto", tensor_parallel_size=a.tp, trust_remote_code=True, max_model_len=a.max_model_len,
              gpu_memory_utilization=a.gpu_mem)
    msgs = [[{"role": "user", "content": p}] for p in prompts]
    outs = llm.chat(msgs, SamplingParams(temperature=0.7, top_p=0.95, max_tokens=300, n=2))
    reasons, kept = Counter(), Counter()
    with open(a.out, "w", encoding="utf-8") as f:
        for t, p, o in zip(tasks, prompts, outs):
            qs, why = judge(t, [c.text for c in o.outputs], a.max_questions)
            reasons[(t["kind"], why)] += 1
            if qs is not None:
                kept[t["kind"]] += 1
                f.write(json.dumps({"id": t["id"], "kind": t["kind"], "prompt": p, "completion": completion_for(qs), "model": a.model},
                                   ensure_ascii=False) + "\n")
    print(json.dumps({"model": a.model, "tasks": len(tasks), "kept": dict(kept), "reasons": {f"{k}:{w}": n for (k, w), n in sorted(reasons.items())}}))


if __name__ == "__main__":
    main()
