# LUMI-G Training Paper

**Fine-Tuning LLMs on AMD MI250X: A Practitioner's Report**
*EuroHPC Grant EHPC-DEV-2026D06-XXX · July–August 2026*

[Download PDF (23 pages)](../assets/papers/moe-sovereign-lumi-paper.pdf){ .md-button .md-button--primary }

---

## Overview

This paper documents the complete fine-tuning campaign for the MoE Sovereign
Judge and Planner models on the [LUMI-G](https://lumi-supercomputer.eu/)
supercomputer (AMD MI250X, 64 GiB HBM2e per GCD, ROCm stack).
It covers two model types — dense (Qwen3-8B) and sparse MoE
(Qwen3.6-35B-A3B) — two training roles (Judge and Planner), and
20+ SLURM job iterations spanning failure, diagnosis, and resolution.

The paper is written from a practitioner's perspective: every configuration
choice is explained, every OOM error is root-caused, and every discovered
bug is documented with a minimal reproducible fix.
It is intended as a reference for teams planning similar campaigns on
EuroHPC or comparable AMD ROCm infrastructure.

---

## Key Findings

| # | Finding | Implication |
|---|---------|-------------|
| 1 | **64 GiB per GCD is tight for dense models under ZeRO-2 with eager attention.** A dense 8B model consumes ≈30 GiB in static tensors, leaving only 34 GiB for activations. | Gradient checkpointing is mandatory. |
| 2 | **A sparse MoE model with lower `d_model` can be cheaper than a dense model with fewer parameters.** Qwen3.6-35B-A3B (3B active) has smaller attention matrices than Qwen3-8B (all active). | Compare *active* dimensions, not total parameter counts. |
| 3 | **Memory fragmentation and memory pressure are distinct failure modes.** Reserved-but-unallocated > 2 GiB → fragmentation; fix via `expandable_segments`. | Check the OOM message's reserved figure before adjusting batch size. |
| 4 | **Flash Attention availability on ROCm must be verified, not assumed.** Without it, eager attention at T > 2048 dominates training cost. | Set `max_seq_len` to p99 of tokenised dataset. |
| 5 | **Measure throughput at step 25 and compute the ETA before trusting a job.** The step-time signal is stable within the first 25 steps. | Cancel and reconfigure if ETA > SLURM time limit. |
| 6 | **Reducing `max_seq_len` only helps when combined with `--packing`.** With `--no-packing`, micro-batches pad to the longest sample, not to `max_seq_len`. The measured speedup from 8192 → 4096 was only **1.2×** (not the expected 4×). | Analyse packing behaviour before predicting speedup. |
| 7 | **`expandable_segments` may silently be inactive.** The LUMI-G PyTorch/ROCm build (July 2026) emits a per-rank warning and leaves fragmentation protection inactive. | Inspect job logs for `expandable_segments not supported`. |

---

## Campaign Summary

### Phase 1 — Judge Model

The Judge model (`sovereign-judge:35b-q4km`, base: Qwen3.6-35B-A3B) was
trained on a paraconsistent dataset of 90,000 samples generated asynchronously
on LUMI-G.
Ground-truth quality was secured via a GPT-4 advocate / Mixtral-8x22B
adversary / Qwen2.5-32B teacher synthesis pipeline.
The Judge completed training in July 2026 and is **deployed in production
as of 2026-07-19**.

### Phase 2 — Planner v4

The Planner SFT campaign (Qwen3-8B, 6 060 steps, ~125 s/step,
ETA ≈210 h) was submitted as a **six-job SLURM dependency chain**
(Jobs 20469441–20470181, combined budget 228 h) after discovering that
a single 38-hour job cannot cover the required training duration.

Three resume bugs were identified and fixed:

| Bug | Cause | Fix |
|-----|-------|-----|
| Restart from step 0 | `OUTPUT_DIR` contained `$SLURM_JOB_ID` | Fixed run name across all jobs in a chain |
| No checkpoint produced | `save_steps=500` > job step budget | Reduced to `save_steps=100` (~3.5 h at 125 s/step) |
| Immediate OOM | Gradient checkpointing disabled by a speed experiment | Locked setting in SLURM script |

### Phase 3 — Teacher-Student Distillation

A distillation campaign targets a smaller 4B student model to enable
on-device inference on `N04-RTX` (24 GiB VRAM):

| Step | Configuration |
|------|--------------|
| Teacher | Qwen3.5-35B-A3B (ZeRO-3, no-4bit, `max_seq_len=4096`) |
| Student SFT | Qwen3.5-4B, ZeRO-2, lr=2×10⁻⁴, LoRA r=16/α=32, no-packing |
| DPO Alignment | planned: lr=1×10⁻⁵, base = SFT checkpoint; **no usable preference dataset yet** |

Remaining steps: verifier-scored preference pairs, DPO, LoRA merge, GGUF quantisation, on-device validation.
The earlier `moe_rule_based_rl_dpo.jsonl` (from `scripts/compile_rule_based_rl_dataset.py`) consists of
templated placeholder pairs without any executed verifier and must not be used for training; no DPO
checkpoint was produced from it.

---

## Practical Recommendations (Highlights)

Full checklists and decision trees are in the paper (§7).

```bash
# Must-have environment variables for ROCm training on LUMI-G
export PYTORCH_HIP_ALLOC_CONF=expandable_segments:True
export PYTORCH_ALLOC_CONF=expandable_segments:True
export NCCL_SOCKET_IFNAME=hsn0
export NCCL_NET_GDR_LEVEL=3
export HF_HOME=/scratch/$PROJECT/$USER/hf_cache   # home quota too small
```

**ZeRO stage selection:**

| Scenario | ZeRO stage |
|----------|-----------|
| Dense ≤ 13B, 8 GCDs, 64 GiB | ZeRO-2 |
| Dense > 13B, 8 GCDs, 64 GiB | ZeRO-3 |
| Sparse MoE, total > 30B | ZeRO-3 |
| Sparse MoE, total ≤ 30B | ZeRO-2 |

**OOM decision tree:**

1. Reserved-but-unallocated > 2 GiB → fragmentation → enable `expandable_segments`
2. Low unallocated, OOM still occurs → memory pressure → verify gradient checkpointing; reduce `max_seq_len` or batch size
3. OOM during checkpoint resume → reduce `max_seq_len` or set `per_device_eval_batch_size=1`

---

## Citation

If you use this paper or the techniques described herein, please cite:

```
Horn, P. (2026). Fine-Tuning LLMs on AMD MI250X: A Practitioner's Report
on the MoE Sovereign LUMI-G Training Campaign.
EuroHPC Grant EHPC-DEV-2026D06-XXX.
Available as part of the MoE Sovereign project.
```

---

## Related Pages

- [EuroHPC Training Concept](eurohpc_training_concept.md) — the original grant proposal and distillation plan
- [Hardware](hardware.md) — local GPU cluster (`N04-RTX`, `ollama-rgtx`)
- [Intelligence & Learning](intelligence/index.md) — RL Flywheel, Agentic Re-Planning Loop, Causal Learning
- [Whitepaper (EN)](../assets/papers/moe-sovereign-whitepaper-en-v2.8.pdf) — full system whitepaper (v2.8, PDF)
