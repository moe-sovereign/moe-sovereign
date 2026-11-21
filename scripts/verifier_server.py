#!/usr/bin/env python3
"""Scoring server for the answer verifier (scalar-head model trained by benchmarks/orchestration_suite/prm/train_prm.py).

  verifier_server.py --model-dir <run>/final [--host 127.0.0.1 --port 8120 --batch 4 --max-len 5120]

  POST /score  {"items": [{"prompt": "...", "answer": "..."}]}  ->  {"scores": [p, ...]}   p = P(answer is correct), same order
  GET  /health ->  {"status": "ok", "model": "<dir>"}

The text format is `[TASK]\\n<prompt>\\n[ANSWER]\\n<answer>`, identical to prm/score_answers.py and services/answer_verifier.text_of
(a test keeps the two in step). One GCD is enough for the 7B model in bf16 (~15 GB). Binds to loopback by default: the verifier is
part of the deployment, not a public service. Torch, transformers and FastAPI are imported lazily so that the module can be
imported (and tested) without them.
"""
import argparse
import math


def text_of(prompt: str, answer: str) -> str:
    return f"[TASK]\n{prompt}\n[ANSWER]\n{answer}"


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x)) if x >= 0 else math.exp(x) / (1.0 + math.exp(x))


def length_order(texts: list) -> list:
    """Indices longest first: padding stays small and an out-of-memory batch fails on the first call."""
    return sorted(range(len(texts)), key=lambda i: len(texts[i]), reverse=True)


def build_app(score_fn, model_name: str):
    from fastapi import FastAPI
    from pydantic import BaseModel

    class Item(BaseModel):
        prompt: str
        answer: str

    class Req(BaseModel):
        items: list[Item]

    app = FastAPI(title="answer verifier")

    @app.get("/health")
    def health():
        return {"status": "ok", "model": model_name}

    @app.post("/score")
    def score(req: Req):
        return {"scores": score_fn([text_of(i.prompt, i.answer) for i in req.items])}

    return app


def load_scorer(model_dir: str, batch: int, max_len: int):
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model_dir)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    tok.padding_side = "right"
    model = AutoModelForSequenceClassification.from_pretrained(model_dir, torch_dtype=torch.bfloat16).cuda().eval()
    model.config.pad_token_id = tok.pad_token_id

    def score(texts: list) -> list:
        out = [0.0] * len(texts)
        order = length_order(texts)
        with torch.no_grad():
            for s in range(0, len(order), batch):
                idx = order[s:s + batch]
                enc = tok([texts[i] for i in idx], truncation=True, max_length=max_len, padding=True, return_tensors="pt").to("cuda")
                logits = model(**enc).logits.squeeze(-1).float().cpu().tolist()
                logits = [logits] if isinstance(logits, float) else logits
                for i, lg in zip(idx, logits):
                    out[i] = round(sigmoid(lg), 5)
        return out

    return score


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8120)
    ap.add_argument("--batch", type=int, default=4)
    ap.add_argument("--max-len", type=int, default=5120)
    a = ap.parse_args()
    import uvicorn
    uvicorn.run(build_app(load_scorer(a.model_dir, a.batch, a.max_len), a.model_dir), host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
