#!/usr/bin/env python3
"""Offline evaluation of an interview planner on the held-out interview tasks (pre-registration G1), and of the rule-based baseline it has to beat.

Per task kind (public, hop need knowledge; infra, inline do not):
  parse_rate        the answer is a parseable interview answer
  ask_rate          share of tasks with at least one question
  needless_rate     share of tasks that need no knowledge but got a question   (G-H1: below 10 % for infra and inline)
  missed_rate       share of tasks that need knowledge but got no question
  on_topic_rate     share of asked tasks whose first question shares a content word with the request (the acceptance rule of the teacher traces)
Baseline = the broker's rule: one gap question per term of the request that the infrastructure does not know (`gaps` of the task), at most 3.

  eval_interview.py --tasks interview_data/test.jsonl --baseline                      # rule-based baseline, no model
  eval_interview.py --tasks interview_data/test.jsonl --model <merged dir> --out r.json
"""
import argparse
import collections
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[2]))
import types  # noqa: E402
if "services.pipeline" not in sys.modules:  # the container has no psycopg: register the package without its __init__ (as generate_traces.py)
    _pkg = types.ModuleType("services.pipeline")
    _pkg.__path__ = [str(HERE.parents[2] / "services" / "pipeline")]
    sys.modules["services.pipeline"] = _pkg
from generate_traces import on_topic  # noqa: E402
from services.pipeline.interview import build_interview_prompt, parse_questions  # noqa: E402


def baseline_questions(task: dict, max_q: int = 3) -> list:
    return [f"What is {g}? Answer in one or two sentences." for g in task["gaps"]][:max_q]


def metrics(tasks: list, answers: list) -> dict:
    """answers[i]: list of questions, or None when the model's answer was not parseable."""
    by: dict = collections.defaultdict(list)
    for t, a in zip(tasks, answers):
        by[t["kind"]].append((t, a))
    out = {}
    for kind, items in sorted(by.items()):
        n = len(items)
        parsed = [(t, a) for t, a in items if a is not None]
        asked = [(t, a) for t, a in parsed if a]
        need = items[0][0]["needs_knowledge"]
        out[kind] = {"n": n, "parse_rate": round(len(parsed) / n, 3), "ask_rate": round(len(asked) / n, 3),
                     "needless_rate": None if need else round(len(asked) / n, 3),
                     "missed_rate": round(sum(1 for t, a in parsed if not a) / n, 3) if need else None,
                     "on_topic_rate": round(sum(on_topic(a[0], t["request"]) for t, a in asked) / len(asked), 3) if asked else None}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tasks", required=True)
    ap.add_argument("--model", default="")
    ap.add_argument("--baseline", action="store_true")
    ap.add_argument("--max-questions", type=int, default=3)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    tasks = [json.loads(l) for l in open(a.tasks, encoding="utf-8")]
    if a.baseline:
        answers = [baseline_questions(t, a.max_questions) for t in tasks]
    else:
        from transformers import AutoTokenizer
        from vllm import LLM, SamplingParams  # LUMI only
        tok = AutoTokenizer.from_pretrained(a.model)
        llm = LLM(model=a.model, dtype="bfloat16", max_model_len=8192, gpu_memory_utilization=0.9, trust_remote_code=True)
        chats = [tok.apply_chat_template([{"role": "user", "content": build_interview_prompt(t["request"], t["known_facts"], t["gaps"], a.max_questions)}],
                                         tokenize=False, add_generation_prompt=True, enable_thinking=False) for t in tasks]
        outs = llm.generate(chats, SamplingParams(temperature=0.0, max_tokens=300))
        answers = [parse_questions(o.outputs[0].text, a.max_questions) for o in outs]
    result = {"model": "rule_baseline" if a.baseline else a.model, "kinds": metrics(tasks, answers)}
    print(json.dumps(result, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
