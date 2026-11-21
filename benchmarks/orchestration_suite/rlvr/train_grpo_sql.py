#!/usr/bin/env python3
"""GRPO with a binary SQL-execution reward (pre-registration E), LoRA, TRL; adapted from the P200 GRPO pilot (same hyper-parameters).

  torchrun --standalone --nproc_per_node=8 train_grpo_sql.py --base-model <dir> --data <dir with grpo.jsonl> --output-dir <dir>
  python3 train_grpo_sql.py --merge-only --base-model <dir> --output-dir <dir>

Needs a PostgreSQL reachable through PGSOCK (lumig_rlvr_sql.slurm starts an embedded one). Rows: {"system","user","setup","ref","tol"}.
Pilot rule: the mean reward of the last 10 logged steps must exceed that of the first 10; the job prints REWARD_TREND for the check.
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-model", required=True)
    ap.add_argument("--data", default="")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--merge-only", action="store_true")
    ap.add_argument("--steps", type=int, default=300)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--lora-r", type=int, default=64)
    ap.add_argument("--per-device", type=int, default=8)
    ap.add_argument("--grad-accum", type=int, default=1)
    ap.add_argument("--max-completion", type=int, default=400)
    ap.add_argument("--seed", type=int, default=200)
    ap.add_argument("--deepspeed", default="")
    a = ap.parse_args()
    out = Path(a.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer
    if a.merge_only:
        from peft import PeftModel
        m = AutoModelForCausalLM.from_pretrained(a.base_model, dtype=torch.bfloat16)
        m = PeftModel.from_pretrained(m, str(out / "adapter")).merge_and_unload()
        m.save_pretrained(str(out / "merged"), safe_serialization=True)
        AutoTokenizer.from_pretrained(a.base_model).save_pretrained(str(out / "merged"))
        return
    from datasets import Dataset
    from peft import LoraConfig
    from trl import GRPOConfig, GRPOTrainer
    import sql_exec_reward as SR
    tok = AutoTokenizer.from_pretrained(a.base_model)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    rows = [json.loads(l) for l in open(Path(a.data) / "grpo.jsonl", encoding="utf-8")]

    def chat(r):
        return tok.apply_chat_template([{"role": "system", "content": r["system"]}, {"role": "user", "content": r["user"]}],
                                       tokenize=False, add_generation_prompt=True, enable_thinking=False)

    ds = Dataset.from_list([{"prompt": chat(r), "setup": r["setup"], "ref": float(r["ref"]), "tol": float(r["tol"])} for r in rows])
    seen = []

    def reward(prompts, completions, setup, ref, tol, **kw):
        out_r = SR.grpo_reward(prompts, completions, setup, ref, tol)
        seen.append(sum(out_r) / max(len(out_r), 1))
        print("REWARD_STEP", json.dumps({"n": len(out_r), "mean": round(seen[-1], 4)}), flush=True)
        return out_r

    reward.__name__ = "sql_exec_exact"
    model = AutoModelForCausalLM.from_pretrained(a.base_model, dtype=torch.bfloat16)
    cfg = GRPOConfig(output_dir=str(out / "trainer"), max_steps=a.steps, per_device_train_batch_size=a.per_device, gradient_accumulation_steps=a.grad_accum,
                     num_generations=8, max_completion_length=a.max_completion, learning_rate=a.lr, lr_scheduler_type="cosine", warmup_steps=0.05, beta=0.0,
                     temperature=0.7, top_p=0.8, top_k=20, logging_steps=2, save_strategy="no", bf16=True, gradient_checkpointing=True,
                     deepspeed=a.deepspeed or None, report_to="none", seed=a.seed)
    tr = GRPOTrainer(model=model, reward_funcs=[reward], args=cfg, train_dataset=ds, processing_class=tok,
                     peft_config=LoraConfig(r=a.lora_r, lora_alpha=2 * a.lora_r, lora_dropout=0.0, target_modules=TARGETS, task_type="CAUSAL_LM"))
    tr.train()
    if tr.is_world_process_zero():
        if len(seen) >= 20:
            print("REWARD_TREND", json.dumps({"first10": round(sum(seen[:10]) / 10, 4), "last10": round(sum(seen[-10:]) / 10, 4)}), flush=True)
        tr.model.save_pretrained(str(out / "adapter"))


if __name__ == "__main__":
    main()
