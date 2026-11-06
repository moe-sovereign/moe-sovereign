# LUMI-G Training Paper

**Fine-Tuning LLMs on AMD MI250X: A Practitioner's Report**
*EuroHPC Grant EHPC-DEV-2026D06-XXX · July–August 2026 · status update 2026-11-06*

The PDF covers the campaign up to August 2026. Later phases, dead ends and corrections are summarised in
[Later Phases](#later-phases-august-november-2026) below; every job of the grant is listed in the
[LUMI-G Activity Log](lumi_activity_log.md).

[Download PDF (25 pages)](../assets/papers/moe-sovereign-lumi-paper.pdf){ .md-button .md-button--primary }

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
| 8 | **Very high token accuracy on SFT data signals memorisation, not success.** All August 2026 expert adapters ended above 98 % token accuracy on template-generated data and were rejected. | Read real samples and check diversity before training; judge models only on held-out, execution-checked tasks. |
| 9 | **Execution-checked GRPO gave the first clear held-out gains.** SmolLM3-3B precision 5/7 → 26/27 of 36 (P200, October 2026). | Prefer verifier rewards over further SFT rounds for skills with a checkable answer. |
| 10 | **TRL's default vLLM importance-sampling mode can silently stop learning.** `sequence_mask` drove the ratio to ≈ 0.005 over ~380 tokens. | Use `vllm_importance_sampling_mode=token_truncate` and log `sampling/importance_sampling_ratio`. |
| 11 | **Checkpoints saved with transformers 5 are not loadable by vLLM 0.20.1** (nested `rope_parameters` per layer type, OLMo-3). | Record library versions with every checkpoint; test serving before relying on a merge. |

---

## Campaign Summary

### Phase 1 — Judge Model

The Judge model (`sovereign-judge:35b-q4km`, base: Qwen3.6-35B-A3B) was
trained on a paraconsistent dataset of 90,000 samples generated asynchronously
on LUMI-G.
Ground-truth quality was secured via a GPT-4 advocate / Mixtral-8x22B
adversary / Qwen2.5-32B teacher synthesis pipeline (as described in the PDF; not re-verified in the
2026-11 reconciliation).
The Judge completed training in July 2026 and was **deployed in production
on 2026-07-19**. It has since been superseded: by a Qwen3.8-27B judge (August 2026, re-trained
in September 2026 after the August data was rejected) and, in the open-source track, by an
OLMo-3.1-32B judge (September 2026).

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

The v4 chain reached `checkpoint-6060` and was exported to GGUF (job 20933544, 2026-08-10).
The preceding run (jobs 20188267/20190726) had been **ineffective**: `max_seq_len=1536` against a
tokenised p99 of 3,628 truncated every sample. An earlier Phi-4 planner attempt (jobs
20053764–20081793) was abandoned after API incompatibilities and an ETA above 100 h.
Since 2026-09-19 the default planner is the OLMo-3-7B planner of the open-source track.

### Phase 3 — Teacher-Student Distillation

A distillation campaign targets a smaller 4B student model to enable
on-device inference on `N04-RTX` (24 GiB VRAM):

| Step | Configuration |
|------|--------------|
| Teacher | Qwen3.5-35B-A3B (ZeRO-3, no-4bit, `max_seq_len=4096`) |
| Student SFT | Qwen3.5-4B, ZeRO-2, lr=2×10⁻⁴, LoRA r=16/α=32, no-packing — done: `moe_qwen35_4b_distilled` (2026-08-13) |
| DPO Alignment | planned: lr=1×10⁻⁵, base = SFT checkpoint; **no usable preference dataset yet** |

Remaining steps: verifier-scored preference pairs, DPO, LoRA merge, GGUF quantisation, on-device validation.
The earlier `moe_rule_based_rl_dpo.jsonl` (from `scripts/compile_rule_based_rl_dataset.py`) consists of
templated placeholder pairs without any executed verifier and must not be used for training; no DPO
checkpoint was produced from it.

---

## Later Phases (August – November 2026)

Summary of what happened after the PDF; details, job IDs and sources are in the
[LUMI-G Activity Log](lumi_activity_log.md).

| Period | Activity | Outcome | Status |
|---|---|---|---|
| 2026-08-15 – 08-17 | Expert campaign (`moe_expert_pipe`, planner + 8 experts + 27B judge) | Trained, but **rejected on 2026-09-10**: data came from an LLM-free template generator (e.g. coder: 3 templates, 150,000 rows), token accuracy > 98 % = memorisation | dead end, documented |
| 2026-09-01 – 09-11 | Teacher smoke tests (5 open-weight teachers + OLMo-3.1-32B), curated role datasets, training smoke tests | All teachers verified after fixes; OLMo-3 needs `--enforce-eager`; OLMo-3.1-32B needs ZeRO-3 | validated |
| 2026-09-11 – 09-12 | Re-training of two tracks: open weight (Qwen3.5-4B experts, 9B planner, Qwen3.8-27B judge) and open source (SmolLM3-3B experts, OLMo-3-7B planner, OLMo-3.1-32B judge) | Healthy training curves; 20 models published | verified |
| 2026-09-19 – 09-22 | Local benchmark of the fine-tunes | Open-weight track: fine-tuned compound 6.91 vs pre-finetune 7.20 vs native baseline 8.16 (n = 8 tasks, 1 round): **no measured gain from SFT**. Open-source run incomplete | validated (open weight), open (open source) |
| 2026-10-25 – 10-31 | P200: GRPO + LoRA with execution rewards (PostgreSQL/Neo4j in the job) | First clear held-out gains (precision, graphrag); DPO alone, full fine-tuning, weight merging, OSRM: no gain | validated, see [P200 snapshot](../experiments/2026-11-05-lumi-p200-snapshot.md) |
| 2026-11-05 – | End-game: vLLM-accelerated GRPO (3.85× faster rollouts) and a full-stack rollout environment for an orchestration value model | IS-mode trap fixed; stack launch blocked by the transformers-5 RoPE config | in progress; phases 1–3 planned until 2026-12-05 |

Meter on 2026-11-06: 2,944 of 18,000 GPU-hours used; the grant expires on 2026-12-05.

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
