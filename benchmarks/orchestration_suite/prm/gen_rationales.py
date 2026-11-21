#!/usr/bin/env python3
"""Teacher rationales for the generative verifier (pre-registration D): a large model checks an answer step by step and gives a verdict.

Accepted rationale: the first of n samples whose final line is `VERDICT: CORRECT|INCORRECT` and matches the label (rejection sampling;
the teacher never sees the label). A rationale can be right for the wrong reason; that limit is documented, the yield per family is reported.
Output rows: {"id", "family", "source", "label", "prompt", "completion"} where `prompt` is the verifier prompt and `completion` the rationale.

  gen_rationales.py --inputs gen_inputs.jsonl --model Qwen/Qwen3-Next-80B-A3B-Instruct --out rationales.jsonl [--limit N --n 2 --tp 8]
"""
import argparse
import collections
import json
import re

INSTRUCTION = (
    "You are a strict verifier. Below are a task and a proposed answer. Check the answer step by step: recompute every number from the data "
    "in the task, check that every part of the task is answered, and check the format. Keep the check short (at most 250 words). "
    "Finish with exactly one last line: `VERDICT: CORRECT` or `VERDICT: INCORRECT`.\n\n"
)
VERDICT = re.compile(r"VERDICT:\s*(CORRECT|INCORRECT)\s*$", re.IGNORECASE)


def prompt_of(text: str) -> str:
    return INSTRUCTION + text


def verdict_of(rationale: str):
    """1 for CORRECT, 0 for INCORRECT, None when the last line carries no verdict."""
    m = VERDICT.search(rationale.strip())
    return None if not m else int(m.group(1).upper() == "CORRECT")


def accept(samples: list, label: int):
    """The first sample whose verdict equals the label, or None."""
    for s in samples:
        if verdict_of(s) == label:
            return s.strip()
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inputs", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--n", type=int, default=2)
    ap.add_argument("--tp", type=int, default=8)
    ap.add_argument("--max-model-len", type=int, default=12288)
    ap.add_argument("--max-tokens", type=int, default=450)
    ap.add_argument("--gpu-mem", type=float, default=0.9)
    ap.add_argument("--smoke-n", type=int, default=300, help="first rows generated alone; stop if their yield is below --min-yield")
    ap.add_argument("--min-yield", type=float, default=0.3)
    a = ap.parse_args()
    rows = [json.loads(l) for l in open(a.inputs, encoding="utf-8")]
    rows = rows[:a.limit] if a.limit else rows
    from vllm import LLM, SamplingParams  # LUMI only
    llm = LLM(model=a.model, dtype="auto", tensor_parallel_size=a.tp, trust_remote_code=True, max_model_len=a.max_model_len,
              gpu_memory_utilization=a.gpu_mem)
    sampling = SamplingParams(temperature=0.7, top_p=0.95, max_tokens=a.max_tokens, n=a.n)
    kept, seen = collections.Counter(), collections.Counter()

    def run(batch: list, f) -> int:
        outs = llm.chat([[{"role": "user", "content": prompt_of(r["text"])}] for r in batch], sampling)
        n_ok = 0
        for r, o in zip(batch, outs):
            key = f"{r['family']}:{r['label']}"
            seen[key] += 1
            rationale = accept([c.text for c in o.outputs], r["label"])
            if rationale is None:
                continue
            kept[key] += 1
            n_ok += 1
            f.write(json.dumps({"id": r["id"], "family": r["family"], "source": r["source"], "label": r["label"],
                                "prompt": prompt_of(r["text"]), "completion": rationale}, ensure_ascii=False) + "\n")
        f.flush()
        return n_ok

    with open(a.out, "w", encoding="utf-8") as f:
        smoke = rows[:a.smoke_n] if a.smoke_n else []
        if smoke:  # abort rule of the pre-registration: yield below the minimum on the first rows stops the job early
            ok = run(smoke, f)
            print(json.dumps({"smoke_rows": len(smoke), "smoke_yield": round(ok / len(smoke), 3)}), flush=True)
            if ok / len(smoke) < a.min_yield:
                raise SystemExit(f"yield {ok / len(smoke):.2f} below {a.min_yield}: stopping (pre-registered abort)")
        rest = rows[len(smoke):]
        for i in range(0, len(rest), 4000):  # chunks: a time-limit kill keeps what is written
            run(rest[i:i + 4000], f)
    print(json.dumps({"model": a.model, "inputs": len(rows), "kept": sum(kept.values()),
                      "yield": {k: round(kept[k] / seen[k], 3) for k in sorted(seen)}}))


if __name__ == "__main__":
    main()
