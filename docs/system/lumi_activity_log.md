# LUMI-G Activity Log (June – November 2026)

**Status:** reconciliation of all LUMI-G activities under project `project_465003058` (EuroHPC grant EHPC-DEV-2026D06-XXX,
18,000 GPU-hours, AMD MI250X) as of **2026-11-06**. Every entry is labelled *validated* (measured result with a cited source),
*verified* (artefact or job state checked on LUMI or in Git), *documented only* (stated in an agent status log or memory note,
not re-checked), *open* or *planned*. Negative results and dead ends are recorded on purpose: they explain why later choices
were made and should prevent repeating them.

**Sources:** `sacct -S 2026-06-01 -X` on LUMI (694 jobs, read on 2026-11-06), checkpoint directories under
`/scratch/project_465003058/hornphil/`, `lumi-allocations`, Git history of `moe-infra` and of the whitepaper repository,
`agent_status/agy.md`, `agent_status/claude-code.md`, `docs/experiments/*`, `docs/system/benchmark-optimization-2026-09-21.md`,
`docs/experiments/2026-11-05-lumi-p200-snapshot.md`.

**Companion pages:** [LUMI-G Training Paper](lumi_training_paper.md) (methods and AMD lessons up to August 2026),
[P200 snapshot](../experiments/2026-11-05-lumi-p200-snapshot.md) (GRPO campaign October/November 2026),
[Antigravity post-mortem](../experiments/antigravity_frontier_pipeline_postmortem.md),
[teacher verification](../experiments/lumig_openrouter_teacher_verification.md).

---

## Budget

| Quantity | Value | Status |
|---|---|---|
| Grant | 18,000 GPU-hours (MI250X), expires **2026-12-05** | verified (operator, 2026-11-05) |
| `lumi-allocations` on 2026-11-06 | **2,944 / 18,000** (16.4 %) | verified |
| Jobs since 2026-06-01 | 694 (small-g 567, standard-g 58, dev-g 27, debug 3, CPU `small` 39) | verified |
| Job states | 463 COMPLETED, 173 FAILED, 39 CANCELLED, 18 TIMEOUT, 1 OUT_OF_MEMORY (incl. CPU jobs) | verified |

**Accounting caveat (open):** the meter increase between 2026-11-05 19:39 and 2026-11-06 (+11) matches about half of the
GCD-hours of the jobs in that window (25.4 GCD-h), i.e. the meter counts MI250X cards (2 GCDs). The `sacct` sum over all jobs
since June, however, also gives about 2,944 GCD-hours. Both cannot hold at the same time for the whole history; until CSC
clarifies the billing, plan with the meter value and treat 1 node-hour = 4 meter units.

---

## Phase A — Router warm-up (June 2026)

| Date | Jobs | Goal | Result | Status |
|---|---|---|---|---|
| 06-11 | 19166029, 19166081 (`moe-router-train`), 19239779 (`-onnx`) | First use of the grant: train the IMoE router classifier and export ONNX | `sovereign_router.onnx` produced by 19166081, copied to the orchestrator, MD5 verified in the container | verified (status log) |
| 06-11–06-29 | `hostname`, `rocm-smi`, `tmux`, `ls`, `df` on dev-g | Interactive exploration | 3 × `tmux` FAILED: batch-style interactive use does not work on dev-g | documented |
| 06-13 | 19220741–19221170 (`moe-dataset-gen`, 3 cancelled) | Dataset generation test | purpose not recorded | documented only |

**Lesson:** compute nodes do not mount `/pfs/lustref1`; the shared container under `/appl/local/laifs/` fails in batch jobs.
Copy `lumi-multitorch-latest.sif` to project scratch (found during the judge campaign, `agent_status/agy.md`).

## Phase B — Judge v1/v2 (June – July 2026)

| Date | Jobs | Goal / why | Result | Status |
|---|---|---|---|---|
| 06-25 | 19517008–19534326 (`sovereign-judge-lora`, 7 FAILED, 1 OOM) | Paraconsistent judge as QLoRA on Qwen2.5-32B | No usable run; OOM and setup failures | verified (sacct) |
| 06-26 | 19540774 (`merge`) | Merge v1 | 62 GB FP16 merged model, CPU, 22 min | documented |
| 06-26–07-03 | 19540836–19682381 (`judge-datagen`, 6 TIMEOUT, 3 FAILED) | 90,000 paraconsistent samples with a teacher ensemble | Generation finished after resume jobs; first attempts failed because compute nodes lacked `/pfs/lustref1` | documented |
| 07-03–07-12 | 19686758–19832821 (`judge-lora-large`, 14 FAILED, 2 TIMEOUT) | Judge on Qwen3.6-35B-A3B (MoE) with ZeRO-3 | Root cause of most failures: ZeRO-3 + PEFT on a multimodal checkpoint (`AssertionError: NOT_AVAILABLE` on empty vision-tower parameters). Fix: freeze vision tower before `get_peft_model()`, `stage3_param_persistence_threshold=1e7`. Checkpoint-704: loss 0.485, token accuracy 85.7 % | validated (training log, memory note) |
| 07-15–07-16 | 19926224, 19948455 (`judge-lora-resume`) | Finish training | COMPLETED | verified |
| 07-16–07-19 | merge 19951999–19964923 (3 FAILED), quant 19970104–19978657, GGUF 19987816–19996375 | Export | PEFT version mismatch during merge (whitepaper Episode 9), one failed GGUF re-conversion | verified (sacct) |
| 07-19 | – | Deployment | `sovereign-judge:35b-q4km` in production; superseded in August/September (see Phase E/G) | documented |

## Phase C — Planner v1–v4 (July – August 2026)

| Date | Jobs | Goal / why | Result | Status |
|---|---|---|---|---|
| 07-20–07-22 | 20018741–20140407 (`planner_dataset`) | Planner decomposition dataset | 2 COMPLETED, 2 FAILED, 1 CANCELLED | verified |
| 07-21 | 20053764–20081793 (`phi4_planner_sft`, 5 FAILED, 3 CANCELLED) | Phi-4 as planner | **Dead end.** Successive failures: `--local_rank` not accepted, TRL API change (`max_seq_length` no longer in `SFTConfig`), NCCL/TCPStore abort, data `zip()` length mismatch; the last run reached 63 s/step, ETA > 100 h, cancelled. Phi-4 is also open-weight only, which later excluded it from the open-source track | verified (job logs) |
| 07-23–07-24 | 20149876–20177964 (`planner_dynamic_topup`), 20177965–20190726 (`qwen3_sft_merged`) | Qwen3-8B planner SFT (259,829 examples) | **Ineffective fine-tune**: `max_seq_len=1536` against p99 3,628 tokens truncated 100 % of samples, so the training target was never seen (whitepaper Episode 11) | validated (training log) |
| 07-25–08-10 | 20242417–20734381 (`qwen3_planner_sft`, 6-job chain v4: 1 COMPLETED, 9 FAILED, 6 TIMEOUT, 3 CANCELLED) | Planner v4 with `max_seq_len=4096`, gradient checkpointing, `save_steps=100` | Three resume bugs fixed (run name with `$SLURM_JOB_ID`, `save_steps` above job budget, GC switched off by an experiment). `qwen3_planner_v4/checkpoint-6060` reached; GGUF 20933544 | verified (checkpoint on scratch) |
| – | – | Deployment | `qwen3-planner:q4km` used by dynamic templates; later default planner switched to the OLMo-3 planner (2026-09-19) | documented |

**Lesson:** `max_seq_len` must be checked against the tokenised p99 before every SFT run; with `--no-packing` shortening
`max_seq_len` barely speeds up training (measured 1.2× for 8192 → 4096).

## Phase D — Distillation student and "rule-based DPO" (August 2026)

| Date | Jobs | Goal / why | Result | Status |
|---|---|---|---|---|
| 08-11–08-12 | 21015892, 21017107 (`moe_synth_dataset`) | Synthetic SFT corpus | COMPLETED. A separate file `moe_sovereign_200k_synthetic.jsonl` (1.6 M rows) contains only placeholders ("Synthetic CoT prompt N", no assistant field); it was not used for the distillation | verified (status log 2026-09-10) |
| 08-11–08-13 | 21015893–21032430 (`moe_distill_student`), 21041838–21090807 (`moe_qwen35_4b_distill`) | 4B student for N04-RTX | Student model ID changed several times within one day (Qwen2.5-3B, Qwen3.5-3B, Qwen3.5-4B). Result `moe_qwen35_4b_distilled`: trained on `moe_system_knowledge_sft.jsonl` (37,875 rows, 1,338 unique prompts, 16.7 % one changelog template) — moderately diverse; the only August checkpoint judged reusable | verified (scratch, status log) |
| 08-12–08-13 | 21017109–21090808 (`moe_rl_dpo_alignment`, 5 COMPLETED) | "Rule-based RLVR/DPO" alignment | **Invalid.** `scripts/compile_rule_based_rl_dataset.py` never ran a verifier: 4 fixed prompts, constant chosen/rejected texts, hard-coded flags, missing flags counted as passed. `moe_3b_rl_dpo_aligned` contains only rank JSON files, `moe_qwen35_4b_dpo_aligned` is empty: no DPO model exists. Guarded since 2026-11-06 (commit "refuse placeholder rule-based DPO pairs") | verified |

## Phase E — August expert campaign `moe_expert_pipe` (2026-08-15 – 08-17)

| Date | Jobs | Goal / why | Result | Status |
|---|---|---|---|---|
| 08-15–08-16 | 81 FAILED, 1 TIMEOUT of `moe_expert_pipe` (21169247 …) | Train planner, 8 experts and judge in one pipeline (SFT → merge → GGUF) | Many pipeline iterations; the 9 role jobs 21189555–21190762 trained and merged but failed in the GGUF stage (fixed on 2026-09-03, job 21698556) | verified (sacct) |
| 08-17–08-18 | 21263413 | Judge on Qwen3.8-27B | COMPLETED (24.7 h). Exported as `sovereign-judge:27b` | verified |
| 09-10 | – | Audit of the August adapters | **All August SFT adapters rejected.** Training data came from an LLM-free template generator, not from the teacher ensembles planned by Antigravity (see post-mortem). Example coder: 150,000 rows from 3 templates, 72.5 % language errors; loss 3.11 → 0.0103, token accuracy 0.996. All roles incl. judge_27b ended above 98 % token accuracy = memorisation. Moved to `checkpoints/_rejected_aug_campaign/` | validated (status log 2026-09-10) |
| 08-18–08-19 | local | T1 benchmark after distillation | No usable result: template names in the runner had drifted; every request failed | documented (whitepaper) |

**Lesson:** a high token accuracy on the training split is a warning sign, not a success metric. Read real samples of every
dataset before training; check that the declared data source is the one actually used.

## Phase F — Re-training preparation (2026-09-01 – 09-11)

| Date | Jobs | Goal / why | Result | Status |
|---|---|---|---|---|
| 09-01–09-07 | 21676084–21799570 (`moe_enrichment_smoketest`), 21698556 (`stage3`) | Data enrichment and GGUF stage fix | Stage-3 fixed (COMPLETED, 3.5 min) | verified |
| 09-03–09-07 | 21699042, 21701726/21705930, 21701731/21705837, 21701732/21705838, 21701733 | Teacher smoke tests (Qwen3-Next-80B, GLM-4.5-Air, Qwen3-235B-A22B, DeepSeek-Coder-V2, Mistral-Large-2411) | All 5 verified after fixes: GLM-4.5-Air 24/24 after a parser fix, Mistral-Large 16/16, Qwen3-235B and DeepSeek-Coder-V2 14/16 after raising walltime to 4 h. New constraint: checkpoints above 90 % of node RAM disable vLLM's Lustre prefetch (100–228 s per shard instead of ~1 s) | validated (job logs) |
| 09-07 | 21794616–21798692 | `role_sft` generation smoke | First run 0/5 (JSON output format); switched to a delimiter format | documented |
| 09-08–09-09 | 21805142, 21814113–21829009, 21832981–21845727 | Role datasets for 9 roles + supplements | Topic collapse in the generic prompt found and fixed (coder 577 → 564 after filtering) | documented |
| 09-09 | 21848558 (FAILED), 21849323, 21855388–21855393 | OLMo-3.1-32B as additional teacher | `hipErrorCapturedEvent` during CUDA-graph capture for `Olmo3ForCausalLM`; works with `--enforce-eager` | validated |
| 09-09–09-10 | 21853583/4, 21856449, 21891384–21902215 | Open-source student candidates | OLMo-2-7B rejected before training (4k context). SmolLM3-3B retest 21/24; OLMo-3-7B first 25 % usable, v2 prompt regressed to 0/24, prompt v3 fixed it. Instella-MoE rejected | documented |
| 09-10 | – | Quality check Q1 of the role datasets | Dolci long-context files for planner/omni/research quarantined (wrong behaviour for the role) | documented |
| 09-10–09-11 | 21902216–21915742 (`phaseC_*_trainsmoke`) | Training smoke tests | SmolLM3, OLMo-3-7B, Qwen-4B pass; OLMo-3.1-32B OOM with ZeRO-2, passes with ZeRO-3 | validated |
| 09-11 | 21916644, 21916682 | Pilots: coder (Qwen-4B), judge (OLMo-3.1-32B) | Healthy curves: coder loss 1.70 → 1.26, accuracy 0.63 → 0.68; judge loss 2.52 → 0.97, accuracy 0.62 → 0.85 (45.7 % of judge targets are the single word "CONFIRMED") | validated (training log) |

## Phase G — Two model tracks (2026-09-11 – 09-12)

| Track | Models | Jobs | Status |
|---|---|---|---|
| Open weight (Qwen) | 8 Qwen3.5-4B experts, students 4B and 9B (planner `moe-sovereign-planner-9b`), judge Qwen3.8-27B | `moe_expert_pipe` 2026-09-11/12 (12 COMPLETED, 1 FAILED), text-tower extraction 21915558, 21965905 | verified (checkpoints dated 2026-09-12) |
| Open source (OLMo/SmolLM3) | 8 SmolLM3-3B experts, planner OLMo-3-7B, judge OLMo-3.1-32B | 21965771–21965871 (8 COMPLETED, 1 CANCELLED), 21965942, 21916682 | verified |

All 20 models were published on Hugging Face (account h3rb3rn: 2 judges, 2 planners, 8 + 8 experts; status log 2026-09-19).

**Naming caveat:** in the status log of 2026-09-11 "Spur 1" means the Qwen track and "Spur 2" the OLMo track; from
2026-09-19 on, the benchmark tooling uses "Spur 1 = open source" and "Spur 2 = open weight". This log therefore names the
tracks only as *open source* and *open weight*.

## Phase H — Evaluation of the September models (local inference, 2026-09-18 – 09-22)

| Date | What | Result | Status |
|---|---|---|---|
| 09-18 | Benchmark run, both tracks | Invalid: orchestrator restarted by another session after the first request | documented |
| 09-19–09-20 | Open-source track, 7 conditions × 8 tasks | Interrupted after 4 of 8 tasks. Interim paired difference fine-tuned minus pre-finetune −0.28 ± 1.15 (n = 12): no evidence for better or worse. A bug sent expert prompts to Ollama as dict reprs (100 % of expert calls since July); fixed afterwards | documented, partial |
| 09-21 | Template optimisation on one MCP task | Open-source template 2.7 → 5.2 mean score through code/infra fixes; open-weight template 10/10 runs correct (mean 9.3) | validated (`benchmark-optimization-2026-09-21.md`) |
| 09-22 | Open-weight track, 8 tasks × 7 conditions, 1 round | Native baseline 8.16 > every compound condition (6.09–7.20); fine-tuned compound (6.91) not better than pre-finetune (7.20) | validated (`eval_scientific_benchmark_20260922-140103.json`), n = 8 per condition |

**Interpretation:** the September SFT round produced working models but no measured improvement over the base models inside the
pipeline. No complete open-source run exists. This is the main motivation for verifier-based training (Phase I).

## Phase I — P200: GRPO with execution rewards (2026-10-25 – 10-31)

Full record: [P200 snapshot](../experiments/2026-11-05-lumi-p200-snapshot.md). About 306 card-hours, 270 jobs.

- **Success:** TRL GRPO + LoRA runs on gfx90a with rewards from real execution (numeric result, SQL against PostgreSQL in the job,
  Cypher against Neo4j in the job). SmolLM3-3B precision 5/7 → 26/27 (of 36), OOD 12/15 → 54/52 (of 70); Qwen3.5-4B precision
  36/36; Cypher GRPO raises graphrag for all three bases.
- **Dead ends:** DPO alone (worse than SFT-v3), full fine-tuning (more forgetting than LoRA), naive weight merging 0.5, OSRM
  initialisation, rejection-sampling SFT, GRPO on OLMo-3-7B without prior SFT (40 % truncated answers in the first run).
- **Parrondo hypothesis** (alternating task types beats single types): not testable in this setup because no single arm loses;
  side finding (seed 1 only): mixing all task types within one step (`batch`) drops OOD to 19/18 versus 39–54 for homogeneous
  steps.
- **Process issue:** 11 smoke jobs ran on dev-g/debug (2026-10-28/29), which is no longer allowed for agent-submitted jobs
  since 2026-11-05.

## Phase J — End-game: orchestration value model (2026-11-05 – ongoing)

| Date | Jobs | Goal / why | Result | Status |
|---|---|---|---|---|
| 11-05 | – | CSC rule | dev-g/debug: 1 running job per user and project, interactive human debugging only; agent jobs on small-g/standard-g. Enforced in `scripts/lumig_preflight_check.py` and a repository test (branch `feat/orchestration-prm-stack`) | verified |
| 11-05 | 23500330–23500432 (35 × `hardtest_gen`) | Evaluate the 7 Parrondo checkpoints | see Phase I | validated |
| 11-05 | 23500457 (HF), 23500458 (vLLM) | Rollout throughput | vLLM colocate 3.85× faster (192 s vs 738 s for 12 steps) | validated |
| 11-05 | 23501446 | 150 GRPO steps with vLLM | **Failure:** precision 10/10, OOD 17/23. TRL default `vllm_importance_sampling_mode=sequence_mask` multiplies a small per-token logprob gap (0.026) over ~380 tokens; importance ratio ≈ 0.005 suppresses the gradients | validated |
| 11-06 | 23503154 | Same with `token_truncate` | Reward 0.16 → 0.94, ratio 0.975, precision 24/22, OOD 41/46 (HF baseline 26/27, 54/52); training 37 min instead of 2.5–3 h. OOD gap to HF still open (1 seed) | validated |
| 11-06 | 23511683 | Full MoE Sovereign stack on one node (PostgreSQL, Valkey, Chroma, Kafka, Neo4j, embeddings, MCP, orchestrator, 10 vLLM servers) | FAILED after 14 s: `CREATE DATABASE` sent as one multi-statement `psql -c` runs in a transaction. Fixed with separate `-c` options | verified |
| 11-06 | 23511738 | Retry | All data services up; both vLLM servers rejected the merged OLMo checkpoints: `rope_parameters should have a 'rope_type' key`. The September merges were saved with transformers 5.16.0.dev0, which nests `rope_parameters` per layer type; the container's transformers 4.57 does not know this format (vLLM 0.20.1 itself is not the cause). Fixed by running the vLLM servers with the same `.user_site` overlay (transformers 5.16) as the vLLM-GRPO pilot, checked beforehand on the login node with vLLM's `ModelConfig` for planner, judge and expert. The launcher also waited 20 min on a dead server; `wait_http` now stops as soon as the process is gone | verified, fixed |
| 11-06 | 23512110 | Retry | FAILED after 3 min: `No HIP GPUs are available` for every server on a single GCD. Cause: `ROCR_VISIBLE_DEVICES=2` renumbers the visible GCD to HIP index 0; the additionally set `HIP_VISIBLE_DEVICES=2` then points at nothing. The judge on `0,1` was unaffected because those indices survive the renumbering. Lesson: on LUMI set only `ROCR_VISIBLE_DEVICES` and drop the HIP/CUDA variables. The new liveness check limited the loss to 3 instead of 20 min | verified, fixed |
| 11-06 | 23514199 | Retry | FAILED after 3 min: `KeyError: 'rope_theta'` in vLLM's OLMo model code (`olmo2.py`), which reads a flat `rope_parameters` and derives the sliding layers itself; transformers 5 nests it per layer type. Fixed with `--hf-overrides` (full-attention values, equal theta checked first) and `--enforce-eager` for OLMo-3 (known `hipErrorCapturedEvent`, job 21848558) | verified, fixed |
| 11-06 | 23514795 | Retry | First time all 10 vLLM servers ready (planner after ~3 min, experts ~1 min each in sequence, judge in parallel; ~12 min to "ready" overall). Aborted because the embedding server listened on 11434 instead of 21434: the image sets `OLLAMA_HOST` itself, which beats inherited variables. Fixed with `singularity --env` | verified, fixed |
| 11-06 | 23515108 | Retry, 20 tasks | Full stack up ("stack up" after 13 min) but all 7 finished episodes unusable; job cancelled. Three causes: (1) MCP died at import because `/app` is read-only in the SIF (`/app/generated`); the orchestrator started anyway and cleared its tool catalog. (2) Inline tables were labelled `shipments.csv`; `_detect_file_skill` took that for an attachment and prepended the `/xlsx` skill, so the planner planned Excel work. (3) The driver counted 422 (quality gate) and 202 (HITL gate) as errors instead of episode outcomes. Fixed: writable `MOE_GENERATED_DIR` and an MCP health check before the orchestrator; tables labelled "Table … (CSV)" with a test against the real detector; outcomes `blocked:<reason>` and `hitl` (draft auto-approved as in `run_scientific_benchmark.py`), fork route returns the gate fields | verified, fixed |
| 11-06 | 23515523 | Calibration run, 20 tasks, K=2, 8 concurrent | COMPLETED (1:25 h incl. 13 min start-up). 336 episodes in 1.19 h of driver time ≈ 281 episodes per node-hour; 4 infrastructure errors. Outcomes: 49 % blocked by the quality gate, 36 % HITL (auto-approved), 15 % direct answer. 44/336 correct, almost all from the 2 `direct` tasks; `tool_chain` 0 %. 27 of 160 decision points with differing outcomes, most often at the planner (7/20). Initially every value was 0 because the scorer read numbers from the model-appended `[Correction-Note: …]`; re-scored offline after the fix. Finding: the trust score does not count data supplied in the prompt as a source, so answers without the MCP path are mostly BLOCKed; deliberately unchanged because the gate thereby rewards delegating to `precision_tools` (confirmed by the project owner on 11-06) | verified |
| 11-06 | 23520649–23520652 | Phase 1, first wave: 4 × 24 h, one node each, K=4, 16 concurrent, families without `direct`, disjoint task ranges | Submitted. The first submission (23520639–42) was cancelled at once: `sbatch --export` splits on commas and would have cut the family list in `DRIVER_ARGS`; resubmitted with an exported shell variable. **Cancelled after ~1.5 h:** forks ended in HTTP 500 when the planner invented MCP tools (`sql_query`, `db_query`, `database_query`); `PlannerContractError` escaped the fork route, so the driver logged an infrastructure error instead of a wrong outcome and the decision point lost its value. Fixed: the fork route returns `pipeline_error`, the driver records `failed:<cause>` (base 500 `orchestration_failed` likewise), with tests. Invented tools are a real model error and belong in the dataset as outcome 0 | cancelled, fixed |
| 11-06 | 23522672–23522675 | Phase 1, second wave, same set-up | Interim after ~17 h (1527 tasks, 46,987 episodes): **~690 episodes per node-hour** (2.5× the calibration run). Fork outcomes: 55 % blocked, 38 % HITL, 7 % answer, 0.7 % failed. 17.6 % of 11,449 decision points carry signal (planner 35 %, merger 16 %, critic/conflict resolution 10 %). By family: `cypher_calc` 34 % correct forks / 27 % signal, `sql_calc` 10 % / 18 %, `cypher_sql_calc` 8 % / 17 %, `tool_chain` 0.8 % / 1.9 %. Half of all forks end as `incomplete_task_execution:task-1:failed`: the planner writes placeholders, SQL or `MAX(…)` straight into `calculate` instead of first computing the values. That is a genuine model error and stays in the data. 369 records with empty error strings were driver timeouts (900 s; httpx timeouts stringify to ""). Cause: one judge serving ~14 concurrent requests at ~9.5 tok/s each, while the 8 expert servers averaged 0.17 running requests | interim |
| 11-07 | – | Fixes for wave 3 | Second judge replica on GCDs 6,7; the shim balances replicas by fewest requests in flight; experts packed onto GCDs 3–5. Driver logs the exception type, timeout 1800 s. `tool_chain` dropped (hardly any signal). Second task set (seed 2), deduplicated against seed 1 on the inline prompt: 7691 open tasks from three families. Also, at the owner's request, `_detect_file_skill` now only reacts to real attachments, no longer to file names in the prompt text | verified (tests) |
| 11-07 | 23535809–23535814 | Phase 1, third wave: 6 × 24 h, K=4, 32 concurrent, 1200 tasks each | Started 11-08 09:55 after the closure. **Final:** 6963 tasks, 217,704 episodes, 52,769 decision points, 24.1 % with signal, 4.1 % of records with a driver error; fork outcomes 49 % blocked, 46 % HITL, 5 % answer, 0.7 % failed. Five jobs ran into the 24 h limit (TIMEOUT), one completed; the last tasks of the five were cut off, the finished records are complete. Together with wave 2: 8563 records | completed
| 11-09 | 23549095 | PRM smoke, 1 node, OLMo-3-7B | Failed after 2:50 min: `TrainingArguments.__init__() got an unexpected keyword argument 'warmup_ratio'` — transformers 5 renamed it; `warmup_steps` below 1 is a fraction now | fixed |
| 11-09 | 23550288 | PRM smoke, 1 node (resubmitted) | 12 steps in 16 min, evaluation and checkpoint written; the trainer works end to end. The metrics (800 rows, 12 steps) say nothing | verified |
| 11-09 | 23549096 | PRM smoke, 2 nodes | **Hung until the 1 h limit** after all 16 ranks had loaded their weights: no step, no error. Cause: the container had no access to the Slingshot libraries (cxi, libfabric, `/opt/cray`), RCCL could not set up its network. Dead end for the multi-node set-up, found only because the job ran to the time limit | failed |
| 11-09 | 23556411 | PRM smoke, 2 nodes with the bindings of the LUMI module `singularity-AI-bindings/24.03` (`/var/spool/slurmd`, `/opt/cray`, `libcxi`, `libjansson`) and CXI settings | Completed in 12 min over 16 ranks with the OFI plugin; only a harmless `fid_domain` warning at shutdown. Multi-node training works | verified |
| 11-09 | – | PRM data | Final rollouts (waves 2+3) built into two variants on LUMI: `data_ood` (domain `energy` held out as OOD test: train 16,363 / val 2,241 / test_iid 2,196 / test_ood 21,235) and `data_all` (train 24,392 / val 3,374 / test_iid 3,137, no OOD). Baseline (train mean per family and node) on test_iid: AUROC 0.70, within-task pairwise 0.49 — the number the PRM has to beat | built |
| 11-07 – 11-08 | – | LUMI closed | From 11-07 ~20:00 to 11-08 ~11:00 login refused: "System is currently closed to apply emergency security patches" (second closure after 17 Oct). Queued and running jobs survived; waves 2 and 3 lost nothing. Expired certificates also answer "Permission denied (publickey)" during a closure, so that message alone does not show that the system is open | verified |
| 11-08 | – | Meter question | Wave 2 ran 74.1 node-hours (4 jobs, 17.7–19.4 h each); the meter rose from 2962 to 3258 = **296 = 74.1 × 4**. The meter therefore counts **cards (4 per node), not GCDs (8 per node)**. A 6-node wave costs 576 card-hours per day. Settles the earlier doubt (sacct sum vs meter) | verified |

Planned until 2026-12-05 (see the end-game plan): counterfactual rollouts at orchestration decisions (phase 1), an
orchestration process-reward model on OLMo-3.1-32B (phase 2), a CSO-DPO/GRPO policy pilot (phase 3), export of datasets and
checkpoints. All *planned*.

---

## Cross-cutting lessons

1. **Data before compute.** The two largest losses of the grant (August expert campaign, rule-based DPO) were data problems that
   no amount of GPU time could fix. Inspect real samples; check uniqueness, language and role fit; never train on generator
   placeholders.
2. **Training metrics are not capability.** Token accuracy above ~95 % on SFT data indicates memorisation. Only held-out,
   execution-checked evaluations count (P200 hardtest, OOD set).
3. **Verifier rewards work where SFT stalled.** GRPO with execution checks produced the first clear held-out gains of the grant.
4. **Library versions are part of the experiment.** Pin and record TRL, transformers, vLLM and PEFT versions per job; the
   TRL `max_seq_length` rename, the PEFT merge mismatch, the transformers-5 RoPE config and the TRL IS default each cost runs.
5. **Fail fast.** Check step time and ETA within the first 25 steps; launchers must stop when a child process dies.
6. **Partition policy.** Agent-submitted work belongs on small-g/standard-g; dev-g is for interactive human debugging only.
