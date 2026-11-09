#!/usr/bin/env python3
"""Train the orchestration PRM: a scalar value head on an OLMo-3 backbone, all parameters, DeepSpeed ZeRO-3.

Target is q in [0,1], the Monte-Carlo success rate from the forked continuations (see build_dataset.py); the loss is
binary cross-entropy against that soft label, weighted per row (rows thinned out of the uniform majority carry a
weight > 1). Launch with torchrun / srun on LUMI-G (see lumi/lumig_prm_train.slurm).
"""
import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from metrics import evaluate  # noqa: E402


def read_rows(path, limit=0):
    rows = [json.loads(line) for line in open(path)]
    return rows[:limit] if limit else rows


def to_example(row, tok, max_len):
    ids = tok(row["text"], truncation=True, max_length=max_len)["input_ids"]
    return {"input_ids": ids, "labels": float(row["q"]), "weights": float(row.get("weight", 1.0))}


def bce(logit, q):
    """Numerically stable binary cross-entropy of sigmoid(logit) against the soft label q (reference for the torch loss)."""
    return max(logit, 0.0) - logit * q + math.log1p(math.exp(-abs(logit)))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-model", required=True)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--deepspeed", required=True)
    ap.add_argument("--max-len", type=int, default=5120)
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--lr", type=float, default=5e-6)
    ap.add_argument("--head-lr-mult", type=float, default=20.0, help="the freshly initialised score head learns faster")
    ap.add_argument("--micro-bs", type=int, default=1)
    ap.add_argument("--grad-accum", type=int, default=8)
    ap.add_argument("--max-steps", type=int, default=-1)
    ap.add_argument("--max-train-rows", type=int, default=0)
    ap.add_argument("--max-eval-rows", type=int, default=0)
    ap.add_argument("--save-steps", type=int, default=200)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()

    import torch
    from torch.utils.data import Dataset
    from transformers import AutoModelForSequenceClassification, AutoTokenizer, Trainer, TrainingArguments

    tok = AutoTokenizer.from_pretrained(a.base_model)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    tok.padding_side = "right"

    class Rows(Dataset):
        def __init__(self, rows):
            self.rows = rows

        def __len__(self):
            return len(self.rows)

        def __getitem__(self, i):
            return to_example(self.rows[i], tok, a.max_len)

    def collate(batch):
        n = max(len(b["input_ids"]) for b in batch)
        ids = torch.full((len(batch), n), tok.pad_token_id, dtype=torch.long)
        mask = torch.zeros((len(batch), n), dtype=torch.long)
        for i, b in enumerate(batch):
            ids[i, :len(b["input_ids"])] = torch.tensor(b["input_ids"])
            mask[i, :len(b["input_ids"])] = 1
        return {"input_ids": ids, "attention_mask": mask,
                "labels": torch.tensor([b["labels"] for b in batch], dtype=torch.float32),
                "weights": torch.tensor([b["weights"] for b in batch], dtype=torch.float32)}

    class PRMTrainer(Trainer):
        def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
            labels, weights = inputs.pop("labels"), inputs.pop("weights")
            out = model(**inputs)
            logit = out.logits.squeeze(-1).float()
            loss = (torch.nn.functional.binary_cross_entropy_with_logits(logit, labels, reduction="none") * weights).sum() / weights.sum()
            return (loss, out) if return_outputs else loss

        def create_optimizer(self):
            if self.optimizer is None:  # the DeepSpeed config carries no optimizer, so this one is handed to it
                head = [p for n, p in self.model.named_parameters() if n.startswith("score")]
                rest = [p for n, p in self.model.named_parameters() if not n.startswith("score")]
                self.optimizer = torch.optim.AdamW(
                    [{"params": rest, "lr": a.lr}, {"params": head, "lr": a.lr * a.head_lr_mult}], weight_decay=0.0)
            return super().create_optimizer()

    train_rows = read_rows(os.path.join(a.data_dir, "train.jsonl"), a.max_train_rows)
    model = AutoModelForSequenceClassification.from_pretrained(a.base_model, num_labels=1, torch_dtype=torch.bfloat16)
    model.config.pad_token_id = tok.pad_token_id
    model.config.use_cache = False

    args = TrainingArguments(
        output_dir=a.output_dir, per_device_train_batch_size=a.micro_bs, per_device_eval_batch_size=1,
        gradient_accumulation_steps=a.grad_accum, num_train_epochs=a.epochs, max_steps=a.max_steps,
        learning_rate=a.lr, lr_scheduler_type="cosine", warmup_steps=0.03, bf16=True, gradient_checkpointing=True,
        deepspeed=a.deepspeed, logging_steps=5, save_strategy="steps", save_steps=a.save_steps, save_total_limit=2,
        save_only_model=True, report_to=[], remove_unused_columns=False, dataloader_num_workers=2, seed=a.seed,
        prediction_loss_only=False)
    trainer = PRMTrainer(model=model, args=args, train_dataset=Rows(train_rows), data_collator=collate)
    trainer.train()
    trainer.save_model(os.path.join(a.output_dir, "final"))
    tok.save_pretrained(os.path.join(a.output_dir, "final"))

    report = {}
    for split in ("val", "test_iid", "test_ood"):
        path = os.path.join(a.data_dir, f"{split}.jsonl")
        if not os.path.exists(path):
            continue
        rows = read_rows(path, a.max_eval_rows)
        if not rows:
            continue
        pred = trainer.predict(Rows(rows)).predictions
        prob = [1.0 / (1.0 + math.exp(-float(x))) for x in pred.reshape(-1)]
        report[split] = evaluate(rows, prob)
        if trainer.is_world_process_zero():
            with open(os.path.join(a.output_dir, f"pred_{split}.jsonl"), "w") as f:
                for r, p in zip(rows, prob):
                    f.write(json.dumps({"id": r["id"], "q": r["q"], "pred": round(p, 5), "node": r["node"], "family": r["family"]}) + "\n")
    if trainer.is_world_process_zero():
        json.dump(report, open(os.path.join(a.output_dir, "eval.json"), "w"), indent=1)
        print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
