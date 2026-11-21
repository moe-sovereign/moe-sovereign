#!/usr/bin/env python3
"""Score (task, answer) rows with a trained answer verifier (train_prm.py checkpoint, scalar head on OLMo-3).

  score_answers.py --model-dir <run>/final --rows answers.jsonl --out scored.jsonl [--batch 4 --max-len 5120]

Input rows carry `id` and either `text` ("[TASK]\\n<prompt>\\n[ANSWER]\\n<answer>", the training format) or `prompt` + `answer`.
Output: one {"id", "score"} per row, score = P(answer correct). One GCD is enough for the 7B model (bf16, ~15 GB).
"""
import argparse
import json
import math
import sys


def text_of(row: dict) -> str:
    return row["text"] if "text" in row else f"[TASK]\n{row['prompt']}\n[ANSWER]\n{row['answer']}"


def batches_by_length(items: list, batch: int, key=lambda x: len(x[1])):
    """Longest first, so the first batch fails early on memory and padding stays small."""
    order = sorted(items, key=key, reverse=True)
    for i in range(0, len(order), batch):
        yield order[i:i + batch]


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x)) if x >= 0 else math.exp(x) / (1.0 + math.exp(x))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--rows", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--max-len", type=int, default=5120)
    a = ap.parse_args()

    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(a.model_dir)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    tok.padding_side = "right"
    model = AutoModelForSequenceClassification.from_pretrained(a.model_dir, torch_dtype=torch.bfloat16).cuda().eval()
    model.config.pad_token_id = tok.pad_token_id

    rows = [json.loads(line) for line in open(a.rows, encoding="utf-8")]
    items = [(r["id"], text_of(r)) for r in rows]
    scores = {}
    with torch.no_grad():
        for n, chunk in enumerate(batches_by_length(items, a.batch)):
            enc = tok([t for _, t in chunk], truncation=True, max_length=a.max_len, padding=True, return_tensors="pt").to("cuda")
            logits = model(**enc).logits.squeeze(-1).float().cpu().tolist()
            logits = [logits] if isinstance(logits, float) else logits
            for (rid, _), lg in zip(chunk, logits):
                scores[rid] = sigmoid(lg)
            if n % 50 == 0:
                print(f"scored {len(scores)}/{len(items)}", flush=True)
    with open(a.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"id": r["id"], "score": round(scores[r["id"]], 5)}) + "\n")
    print(json.dumps({"scored": len(scores), "out": a.out}))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
