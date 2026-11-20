# LUMI-G research findings, November 2026 (consolidated)

**Status:** 2026-11-20, allocation expires 2026-12-05. This page keeps the *results* of phase J (verifier-guided orchestrator
learning) in one place, with the evidence and the limits, so that they can be reused without reading the whole
[activity log](lumi_activity_log.md). Each finding names its jobs; numbers carry a 95 % interval where one was computed. The
[final report](lumi_final_report.md) holds the allocation accounting and the run-by-run assessment. Anything marked *pending*
has a job in the queue and is not a result yet.

## 1. Setup in brief

- **Stack.** The full MoE Sovereign orchestrator in one Slurm job (PostgreSQL, Valkey, Chroma, Kafka, Neo4j, embeddings, MCP
  precision tools, ten vLLM servers): fine-tuned OLMo-3 7B planner, fine-tuned OLMo-3.1 32B judge (two replicas), eight
  fine-tuned SmolLM3 3B experts. Research routes fork an episode from any LangGraph checkpoint.
- **Tasks.** Synthetic families `sql_calc`, `cypher_calc`, `cypher_sql_calc` with the data inline in the prompt (a held-out set of
  491 tasks was never used for collection or training); synthetic single-value lookups for GraphRAG; external sets (770 tasks:
  TAT-QA tables, GSM8K, HotpotQA/MuSiQue, 20 graph questions).
- **Metric.** *Draft accuracy*: the answer if one left the pipeline, else the answer the quality gate withheld. Comparisons are
  paired per task with a bootstrap interval. The **direct baseline is the same fine-tuned 32B judge answering alone**, not a base
  model.
- **Limits that apply to everything below.** One stack catalog without a SQL tool; synthetic task families; exact-number scoring;
  mostly one repeat per condition; the fine-tuned weights are the September models.

## 2. Findings

| # | Finding | Evidence | Limits | Consequence |
|---|---|---|---|---|
| F1 | **The trust gate decides most labels and says little about correctness.** About half of all forks were blocked; correctness and gate verdict correlate 0.10; trust score → correctness AUROC 0.60 over 8,365 episodes; verdict PROCEED (the deterministic-tool case) was correct in 14 % (n = 59). | waves 2–5, `eval` of the base episodes | synthetic tasks; PROCEED has a small n | Labels for learning must be recorded without the gate (`quality_blocked_draft`); the gate is a candidate for replacement by a learned verifier (F10). Gate unchanged. |
| F2 | **A label confounded by the gate carries no learnable decision signal.** Text baseline (TF-IDF + ridge), within-task pairwise accuracy 0.60 on all forks, **0.50–0.53 on gate-passed forks only**. | rebuilt from the raw wave 2+3 rollouts | one baseline family | Waves 2/3 are not usable as PRM data; the negative result stays as a finding. |
| F3 | **GraphRAG helps when it is reached, and the product does not reach it.** Product router: retrieved share 0.00 in all four arms (480 lookup tasks). Router forced on: draft correct graph 0.61, noisy 0.61, inline 0.75, empty 0.00; graph − empty +0.60 (CI +0.50 … +0.68), graph − inline −0.14 (−0.23 … −0.04), stale duplicates cost nothing measurable. | 23795184, 23814342, 23642846 | synthetic single-value lookups; one model set | Cause: the graph confidence of a lookup plan is a constant 0.30 against the threshold 0.35 and the planner category `compounding_knowledge` does not count as graph-heavy. Entity hits now route to the graph (knowledge-broker branch). |
| F4 | **The planner server died in 8 of 11 long jobs** (vLLM 0.20.1, hybrid KV cache manager, `Cannot get N free blocks from the pool`); the orchestrator then silently replaced every plan by a one-task fallback. 49 % of the task records of waves 4 and the planner wave were unusable (≈ 350 card-h). | 23662477–87, 23667179; reruns 23791554–61, 23814340 | — | Fix: no hybrid KV manager and capped concurrency for the planner server, restart only when the process has exited, `PLANNER_RUNTIME_FALLBACK=0` in research stacks. Verified: 0 crashes in six long reruns, 0 of 1,201 planner points with a single plan. |
| F5 | **On this suite the workflow does not beat the single model it contains.** Held-out tasks: direct judge 0.485, workflow 0.352 (paired −0.133, CI −0.172 … −0.094). External data (n = 150 / 295 / 287): GSM8K 0.944 vs 0.707 (−0.24), TAT-QA 0.675 vs 0.356 (−0.32), text QA 0.597 vs 0.512 (−0.085). No-GraphRAG, review wave and review-without-self-correction do not differ from the workflow beyond noise; deliberation is slightly worse (0.323, −0.030, CI −0.064 … +0.003). | 23795177–82, 23814341 → 23843015, 23843014 | Hypothesis, not tested: the 3B experts answer and the judge merges, which loses accuracy against the 32B alone. One repeat for the workflow on external data. | No claim that the orchestrated system is more accurate than a single large model may rest on these tasks. See F11 for where it could be. |
| F6 | **The `calculate` expression check (added 2026-11-10) costs accuracy on the synthetic tasks and helps on external data.** 153 of 212 workflow failures were contract errors from the check. Clean rerun with the check off: held-out 0.410 vs 0.352 (paired +0.055, CI +0.022 … +0.089), failed episodes 4.8 % vs 21.6 %. External data, check off − on: −0.061 / −0.059 / −0.031 (intervals include 0 except borderline TAT-QA). | 23795178, 23843017, 23843014, 23843016 | The first off-run was invalid (watchdog fault, below). | Failing hard after three retries is the wrong end state; degrading (drop the unusable tool task, let the experts answer) is the proposal and has not been run. Default unchanged. |
| F7 | **Planner preference pairs (DPO) are not supported by the data.** Outcome noise after the planner is as large as the planner effect (mixed outcomes 47 % at the planner, 45 % at `workers`/`thinking` where the plan is already fixed); of the `calculate` plans 69 % are not evaluable (mostly SQL text), 28.5 % evaluable but wrong, 2.2 % equal to the reference, yet draft correctness is 0.41 / 0.61 / 0.77 for the three groups, i.e. the experts answer from the inline data. Ceiling: a perfect planner would reach ≈ 0.45 against 0.49 for the single model. | 23618242, 23791558–61, 23843018 | — | No DPO set was built. |
| F8 | **A 7B process-reward model beats the baselines, but only slightly at the step level.** Test_ood, three seeds: AUROC 0.873 (sd 0.003), within-task pairwise 0.564 (sd 0.007) against 0.537 (node mean) and 0.525 (text); Brier 0.078. Learning curve ≈ +0.01 AUROC per doubling of the data. The "planner 0.611" reported on 2026-11-17 was an artefact (pairs of different nodes of the same task) and is withdrawn. | 23791608, 23814361–64 | a 32B run was not justified by this signal | More waves would add little; the PRM is not the lever. |
| F9 | **Voting over samples is a strong lever for the workflow and a weak one for the single model.** Planner forks, N = 1 → 8 (plurality vote): `cypher_calc` 0.73 → 0.92, `sql_calc` 0.31 → 0.43, `cypher_sql_calc` 0.16 → 0.12 (voting hurts where most samples are wrong). The direct judge at temperature 0.7 gives nearly identical samples (N = 1 → 3: 0.71 → 0.76 and 0.67 → 0.72). | offline on waves 4/5, 23795177 | workflow curves count only episodes that produced an answer | Diversity of plans, not of tokens, is what makes sampling pay. |
| F10 | **An answer verifier trained on drafts selects correct answers far better than the gate or a vote.** 7B, one epoch on 112,057 (task, draft, correct) rows from the clean forks; test = 4,247 answers of the direct judge and four workflow variants to the 491 held-out tasks. AUROC **0.952** (cypher_calc 0.951, cypher_sql_calc 0.948, sql_calc 0.889; direct answers 0.915). Best-of-N accuracy of the chosen answer: direct judge N ≈ 3: single 0.531, vote 0.567, **verifier 0.606**, oracle 0.687; workflow N ≈ 6: 0.411 / 0.511 / **0.577** / 0.684; all pooled N ≈ 8.6: 0.440 / 0.589 / **0.635** / 0.776. | 23842900 | same synthetic families in training and test (unseen tasks, not unseen families); exact-number labels. **Transfer to the external data: pending (23874318).** | Largest effect found. Next: the pipeline (selection of N candidate answers, replacement or complement of the trust score). |
| F11 | **A single model collapses on large tables.** Direct judge, `sql_calc`: 0.66 at ≈ 19 rows, 0.42 at ≈ 57, 0.16 at ≈ 190, 0.05 at ≈ 570; the 100× prompts (≈ 48,000 characters) mostly errored. | 23842892 (806 of 1,000 episodes, 4 h limit) | one model, one family | This is the range where tools or retrieval have to carry the load; a fair suite for orchestration lives here. |
| F12 | **Fine-tuning has no demonstrated effect at pipeline level.** September benchmark (Qwen track, 8 tasks, one round): fine-tuned 6.91 vs pre-finetune 7.20 over all runs, 7.77 vs 7.20 over valid runs only; OLMo/SmolLM3 track: interrupted, −0.28 ± 1.15 (n = 12). GRPO on verifiable single-step tasks does help small models (SmolLM3-3B precision 5/7 → 26/27 of 36). Component-level (planner only, judge only) was never measured. | `eval_scientific_benchmark_20260922-140103.json`, P200 | tiny n in September | Factorial base-vs-fine-tuned runs submitted (23858647–54), *pending*. |
| F13 | **Knowledge broker, reconciliation and expert injection are implemented** (branch `feat/knowledge-broker`, 23 commits, all new behaviour behind switches, off by default). Gate P0 on LUMI (conflict arms, lookup, held-out, broker off/on) submitted as 23874159–64, *pending*. | repository | no accuracy claim yet | See the plan in the repository history of this branch. |

## 3. Dead ends and our own errors

Kept here because they cost compute and teach something:

- **Premature 32B PRM submissions** before a baseline existed (cancelled while pending, no cost).
- **Smokes without a look at the planner logs**; the crash of F4 would have been visible after the first smoke.
- **My watchdog** killed a healthy, busy planner on a `/v1/models` timeout; every episode in the restart window failed
  (invalidated the first deliberation and check-off runs, 23814341 and 23814831). It now restarts only when the process is gone.
- **The `calculate` check fails hard** after three retries (F6).
- **The driver dropped the draft of approved (HITL) base episodes**, so some base drafts were scored wrong until 2026-11-17;
  forks were not affected.
- **The "planner 0.611" PRM figure** was an artefact and is withdrawn (F8).
- **Unseen mistakes found by the tests of the knowledge-broker branch** (product code, fixed there): correction memory never ran its
  similarity query (a Neo4j keyword collision swallowed by an `except`), streaming requests ignored template toggles and tenant
  scoping, the fallback category depended on the hash seed.
- **Earlier phases** (see the log): rule-based DPO, the August expert campaign (template-generated data, token accuracy above
  98 %), ZeRO-3 + PEFT on a multimodal checkpoint, GRPO with `sequence_mask` importance sampling.

## 4. What follows from it

| Result | Use |
|---|---|
| F10 | Build the verifier pipeline: score N candidate answers, return the best; compare with the trust gate. First the external transfer (23874318). |
| F3, F13 | Entity hits route to the graph; the broker puts infrastructure facts before the experts and the judge; gate P0 decides. |
| F6 | Degrade instead of fail for unusable `calculate` tasks; test on held-out and external data. |
| F5, F11 | Do not claim an accuracy advantage of the workflow; build a table-size suite in which tools are needed. |
| F7, F8 | No planner DPO; no further waves; no 32B PRM. |
| F12 | Factorial base/fine-tuned runs decide what the fine-tunes are worth. |

## 5. Reproduction

- **Branches** (origin and github): `feat/orchestration-prm-stack` (research routes, stack, tooling), `docs/lumi-activity-reconciliation`
  (this documentation), `feat/knowledge-broker` (product changes from `origin/main`), `eval/knowledge-broker` (their merge with the
  evaluation tooling).
- **Stack job:** `benchmarks/orchestration_suite/lumi/lumig_moe_stack.slurm` (examples in `submit_prepared.sh`,
  `submit_knowledge_gate.sh`); direct baseline `DIRECT_ONLY=1`; base models via `JUDGE_CKPT`/`PLANNER_CKPT`.
- **Analysis:** `graphrag/analyze.py`, `graphrag/compare_runs.py`, `prm/verifier_bestofn.py`, `selfconsistency_curve.py`; data builders
  `prm/build_dataset.py`, `prm/build_verifier_data.py`, `prm/build_answer_pool.py`.
- **Data:** rollouts under `$SCRATCH/moe-stack/runs/<job>/rollouts.jsonl` on LUMI, copied to `/opt/tmp/lumi-rollouts/` locally
  (waves, held-out, external, verifier data); checkpoints under `$SCRATCH/prm/runs/`.
- Task files: `orch_tasks_w3.jsonl` (held-out = lines 7200–7690), `ext_tasks.jsonl`, `lookup_simple.jsonl`, `orch_tasks_scale.jsonl`,
  `lookup_conflicts.jsonl`.

## 6. Open questions

1. Does the verifier transfer to external data and to a different generator (pending, 23874318)?
2. What do the fine-tuned planner, judge and experts add over their base models inside the pipeline (pending, 23858647–54)?
3. Does the knowledge broker pass gate P0, and does reconciliation pick the expected value on the conflict arms (pending)?
4. Where does an orchestrated system beat the single model: a suite with tables of 60 rows and more and a tool that can aggregate.
5. Does degrading the `calculate` check recover the accuracy the check costs without losing its benefit on external data?
