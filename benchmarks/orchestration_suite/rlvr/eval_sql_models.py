#!/usr/bin/env python3
"""Evaluate a model that writes SQL for a table it cannot see (pre-registration E): n samples per task, executed on PostgreSQL, pass@k per table scale.

  eval_sql_models.py --model <dir> --tests test_s1.jsonl test_s3.jsonl ... --n 64 --out result.json [--tp 1]

Used for the base and the GRPO-trained small models and for the untrained 32B with the same tool (the mandatory control). The prompt is the
tool prompt of build_sql_tasks.py (schema and question, no rows). Needs PGSOCK like the training job.
"""
import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))  # hf_overrides
import build_sql_tasks as B  # noqa: E402
import sql_exec_reward as SR  # noqa: E402


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased pass@k of Chen et al. for c correct out of n samples."""
    if n - c < k:
        return 1.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def summarize(per_task: list, ks: list) -> dict:
    """per_task: list of (n, c). Mean accuracy of one sample and pass@k for each k <= n."""
    out = {"tasks": len(per_task), "mean_sample_acc": round(sum(c / n for n, c in per_task) / len(per_task), 4)}
    for k in ks:
        vals = [pass_at_k(n, c, k) for n, c in per_task if n >= k]
        if vals:
            out[f"pass@{k}"] = round(sum(vals) / len(vals), 4)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True)
    ap.add_argument("--tests", nargs="+", required=True)
    ap.add_argument("--n", type=int, default=64)
    ap.add_argument("--tp", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--max-tokens", type=int, default=400)
    ap.add_argument("--max-model-len", type=int, default=4096)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    from transformers import AutoTokenizer
    from vllm import LLM, SamplingParams  # LUMI only
    tok = AutoTokenizer.from_pretrained(a.model)
    from hf_overrides import llm_kwargs
    llm = LLM(model=a.model, dtype="bfloat16", tensor_parallel_size=a.tp, trust_remote_code=True, max_model_len=a.max_model_len, gpu_memory_utilization=0.9, **llm_kwargs(a.model))
    sp = SamplingParams(temperature=0.7, top_p=0.8, top_k=20, max_tokens=a.max_tokens, n=a.n)
    ks = [k for k in (1, 2, 4, 8, 16, 32, 64) if k <= a.n]
    result = {"model": a.model, "n": a.n, "scales": {}}
    for path in a.tests:
        tasks = [json.loads(l) for l in open(path, encoding="utf-8")]
        tasks = tasks[:a.limit] if a.limit else tasks
        rows = [B.tool_row(t) for t in tasks]
        prompts = [tok.apply_chat_template([{"role": "system", "content": r["system"]}, {"role": "user", "content": r["user"]}], tokenize=False,
                                           add_generation_prompt=True, enable_thinking=False) for r in rows]
        outs = llm.generate(prompts, sp)
        per_task = []
        for r, o in zip(rows, outs):
            c = sum(SR.reward(x.text, r["setup"], r["ref"], r["tol"], SR.run_pg) for x in o.outputs)
            per_task.append((len(o.outputs), int(c)))
        result["scales"][Path(path).stem] = summarize(per_task, ks)
        print(json.dumps({Path(path).stem: result["scales"][Path(path).stem]}), flush=True)
    Path(a.out).write_text(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
