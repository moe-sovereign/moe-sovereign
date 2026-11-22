#!/usr/bin/env python3
"""Score answers with the generative verifier (pre-registration D3): greedy rationale, then P(CORRECT) at the verdict token.

Step 1 generates the rationale for `prompt_of(text)`; the final `VERDICT:` line is cut off. Step 2 feeds prompt + rationale + "VERDICT:" and reads the
probabilities of the next token being CORRECT or INCORRECT: score = p_CORRECT / (p_CORRECT + p_INCORRECT). This gives a continuous score for AUROC
and best-of-N, not just a binary verdict. Rows: {"id", "text"} (the same rows as score_answers.py); output {"id", "score", "verdict"}.

  score_genverifier.py --model-dir <merged> --rows rows.jsonl --out scored.jsonl [--tp 1]
"""
import argparse
import json
import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # hf_overrides
from gen_rationales import prompt_of, verdict_of  # noqa: E402

CUT = re.compile(r"\n?\s*VERDICT:\s*(?:CORRECT|INCORRECT)\s*$", re.IGNORECASE)


def rationale_prefix(rationale: str) -> str:
    """The rationale without its final verdict line, ending in a newline."""
    return CUT.sub("", rationale.rstrip()).rstrip() + "\n"


def score_from_top(top: dict):
    """top: {token string: logprob} of the next token after 'VERDICT:'. None when neither verdict word is among them."""
    p = {"CORRECT": 0.0, "INCORRECT": 0.0}
    for tok, lp in top.items():
        w = tok.strip().upper()
        # tokenizers may split INCORRECT into IN + CORRECT: only the full word and its first piece are counted
        if w in p:
            p[w] += math.exp(lp)
        elif w in ("IN", "INC"):
            p["INCORRECT"] += math.exp(lp)
    total = p["CORRECT"] + p["INCORRECT"]
    return None if total <= 0 else p["CORRECT"] / total


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tp", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    from transformers import AutoTokenizer
    from vllm import LLM, SamplingParams  # LUMI only
    tok = AutoTokenizer.from_pretrained(a.model_dir)
    rows = [json.loads(l) for l in open(a.rows, encoding="utf-8")]
    rows = rows[:a.limit] if a.limit else rows
    from hf_overrides import llm_kwargs
    llm = LLM(model=a.model_dir, dtype="bfloat16", tensor_parallel_size=a.tp, max_model_len=8192, gpu_memory_utilization=0.9, trust_remote_code=True, **llm_kwargs(a.model_dir))
    chat = [tok.apply_chat_template([{"role": "user", "content": prompt_of(r["text"])}], tokenize=False, add_generation_prompt=True, enable_thinking=False) for r in rows]
    g = llm.generate(chat, SamplingParams(temperature=0.0, max_tokens=450))
    rationales = [o.outputs[0].text for o in g]
    probes = [c + rationale_prefix(t) + "VERDICT:" for c, t in zip(chat, rationales)]
    p = llm.generate(probes, SamplingParams(temperature=0.0, max_tokens=1, logprobs=20))
    with open(a.out, "w", encoding="utf-8") as f:
        for r, t, o in zip(rows, rationales, p):
            top = {v.decoded_token: v.logprob for v in (o.outputs[0].logprobs[0] or {}).values()} if o.outputs[0].logprobs else {}
            s = score_from_top(top)
            if s is None:  # fall back to the generated verdict
                v = verdict_of(t)
                s = 0.5 if v is None else float(v)
            f.write(json.dumps({"id": r["id"], "score": round(s, 5), "verdict": verdict_of(t)}) + "\n")
    print(json.dumps({"scored": len(rows), "out": a.out}))


if __name__ == "__main__":
    main()
