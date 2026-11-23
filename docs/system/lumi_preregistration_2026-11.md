# LUMI-G end-game: pre-registration of six experiments (2026-11-21)

**Status:** written and committed on 2026-11-21, before any result of the experiments below exists. Hypotheses, metrics, thresholds,
splits and abort rules are fixed here; deviations are reported as deviations in the [findings](lumi_findings_2026-11.md) and the
[final report](lumi_final_report.md). Allocation: project_465003058, expires 2026-12-05. Planned use ≈ 920–1,725 of ≈ 12,700 remaining
card-hours; the rest is left unused on purpose.

**Rules for all experiments.** Correctness is the gate-free draft accuracy (`effective_correct`); comparisons are paired by task with a
bootstrap over tasks (95 % interval); a claim needs the interval to exclude zero *and* the stated minimum effect. Infrastructure faults are
not results: analyse, fix, resubmit. Tasks used to choose a threshold are never used to report it.

## A. Verifier as a calibrated release rule

*Background.* The trust score separates correct from incorrect answers with AUROC 0.60; the 7B verifier reaches 0.95 on unseen tasks of the
same families (F1, F10). Related work: calibrated deferral from a small to a large model (ReDAct, ICML 2026 guardrail cascades),
prover-verifier selective prediction.
- **A-H1.** At equal coverage (share of answers released), the accuracy of released workflow answers is higher with the verifier than with
  the trust gate by ≥ +0.10.
- **A-H2.** "Release the workflow answer if the verifier score ≥ t, otherwise use the direct 32B answer" beats the direct 32B alone by ≥ +0.02.
- **Data.** Held-out workflow and direct answers of 2026-11-20 (491 tasks). Split: `sha256(task_id) mod 2` into dev and test, fixed now;
  t is chosen on dev, reported on test. External data: verifier v1 only (v2 saw external answers in training).
- **Metrics.** Coverage-accuracy curves, AUROC, ECE (10 bins), escalation rate against accuracy.
- **Abort.** A-H1 not met → no in-loop run; the result is reported.

## B. Where does the workflow lose? Synthesis ablation at equal compute

*Background.* LLM teams often fall below their best member because they average expert and non-expert views (Pappu et al., ICML 2026).
- **B-H1.** The loss arises in the merge step: `MERGER_MODE=evidence` beats `default` by ≥ +0.05 on the held-out tasks; `judge_solo` equals the
  direct 32B within 0.03 (otherwise the loss sits in the pipeline shell).
- **B-H2.** At equal token budget the direct 32B with a plurality vote reaches at least the workflow's accuracy.
- **Arms.** `judge_solo`, `evidence`, `best_member` against the existing `default` (3 × 2 repeats) and direct (8 repeats) runs. B-H2 requires
  token counts per episode; without them only B-H1 is tested.

## C. Fine-tuned experts

- **C-H1.** Replacing the fine-tuned 3B experts with the SmolLM3-3B base changes pipeline accuracy by less than ±0.03 (held-out and external).
  A larger change is a finding about the experts; none is the null result already found for planner and judge (F12).

## D. Generative verifier

*Background.* Verifiers that reason before judging outperform discriminative ones; small tool-augmented verifiers rival much larger reward models.
- **D-H1.** A 7B verifier trained on teacher rationales (Qwen3-Next-80B, filtered to those whose verdict matches the label) reaches AUROC ≥ 0.70
  on external text QA (the discriminative v1: 0.59–0.61) with an in-domain loss ≤ 0.02 against v2.
- **Abort.** Rationale yield < 30 % or no loss decrease in the smoke run. No second run after a miss.

## E. Reinforcement learning with execution rewards on large tables

*Background.* Execution-reward GRPO gives small models strong text-to-SQL results; simple rewards are preferred over fine-grained ones;
RLVR is bounded by the base model's distribution.
- **E-H1.** A 3B or 7B model with a SQL tool, trained with GRPO on an execution reward, beats the direct 32B (table inline) by ≥ +0.20
  at ≥ 3× the base table size (≈ 57 rows).
- **Mandatory control.** The 32B *with* the same SQL tool and no training; if it matches the trained small model, the finding is "tool, not training".
- **Also reported.** pass@k, k = 1…64, before and after training. **Abort.** No reward increase in the 2 h pilot.

## F. Prospective router and memory ablation

- **F1-H1.** Two rules fixed now, evaluated on a new held-out set (new seed, 491 tasks, known ids excluded): R1 workflow only for
  `cypher_sql_calc`, otherwise direct; R2 workflow answer if its verifier score exceeds the direct answer's, otherwise direct. Both beat direct
  (post hoc on the old set R1 gave +0.017, 95 % CI +0.007 … +0.028; per-task oracle 0.555).
- **F2-H1.** With Correction Memory and graph ingest on, accuracy on a repeated task template rises from first to second occurrence by more than
  with the memory off. Precondition (smoke): corrections or facts are actually written during the benchmark; if not, F2 is dropped and that is the result.

## Running and unchanged

Gate P0 of the knowledge broker (jobs 23900909–14) and the planner-interview pilot keep their own criteria (see the knowledge-broker plan).

## Addendum 2026-11-22 (written before any result of G–J)

**G. Planner with externalised knowledge (decision recorded).** The planner that decides what to ask a knowledge model is developed in steps, and only the first
step spends compute before 2026-12-05. *G1* (running): a LoRA-SFT OLMo-3-7B interview planner from teacher traces (Qwen3-Next-80B; accepted by
rule per task kind), evaluated offline on 369 held-out interview tasks (public facts, two-hop QA, answer already known, private inline data) and in the stack on the conflict
arms of gate P0. **G-H1:** it beats the current planner (rule-based gap questions) on the conflict arms of gate P0 and keeps the share of needless questions on
known-answer and inline tasks below 10 %; **abort:** no better on the conflict arms. *G2* (a small planner trained from scratch with knowledge kept out of the weights, or
continued pre-training on ecosystem data) is described in the [concept note](lumi_planner_concept.md) and receives **no compute before 2026-12-05 unless G1 passes**; reasons: a perfect
planner would reach only ≈ 0.45 against 0.49 for the single model on these tasks (F7), and fine-tuning planner and judge has no measurable end-to-end effect (F12).

**H. Unseen task families.** The verifier was trained on `sql_calc`, `cypher_calc`, `cypher_sql_calc` and external sets. New tasks of the families `tool_chain`
(multi-step calculation with tools) and `direct` (single-step arithmetic), seed 888, ids unseen (250 + 250 tasks, `orch_tasks_heldout4.jsonl`), direct judge × 3 and workflow × 2.
**H-H1:** AUROC of verifier v1 and v2 ≥ 0.80 on each unseen family. **H-H2:** the release rule of A-H2 with the thresholds fixed *now* from the seen families (t = 0.80 for v2, 0.85 for v1)
beats the direct judge alone by ≥ +0.02 (CI excluding 0) on the unseen families. A miss is reported as a limit of the verifier, not retried.

**I. Seed repeat of the verifier.** A second training run of verifier v2 (seed 2, same data and settings). **I-H1:** AUROC on `test_iid`, `test_ood`, `test_ext` within ±0.01 of seed 1, and the
A-H2 effect of seed 2 inside the confidence interval of seed 1. Both seeds are reported.

**J. Release rule on external data.** Verifier **v1 only** (v2 saw external answers in training) scores the external answers of the base-vs-fine-tuned runs (7,490 answers to 770 tasks). Reported per external family
(math, tables, text QA): AUROC, ECE and coverage-accuracy curve against the trust score. **J-H1 (descriptive):** AUROC ≥ 0.80 on math and tables and < 0.65 on text QA, as in the earlier transfer measurement.

**Evaluation rule.** Each strand is evaluated once all of its jobs have finished with exit code 0, with the script named in its section; infrastructure failures are fixed and resubmitted and are not results.

## Addendum 2026-11-23 (written before any result of A5)

**A5. Fallback to the direct judge on pipeline failure.** F26 showed that the verifier gate with escalation raises accuracy on the episodes that produce an answer (0.594 against 0.500
for the direct judge on the same tasks) but that 23 % of the episodes fail before the gate and count as wrong. The product switch `PIPELINE_FALLBACK_DIRECT=1` answers such a request
(pipeline error or time limit) with one direct judge call (temperature 0, the original request only) and marks the response. Held-out tasks (`orch_tasks_heldout2.jsonl`, 491 tasks × 2),
two arms, same stack as the A4 run otherwise: **A5a** fallback only (verifier off), **A5b** fallback with `VERIFIER_MODE=gate`, `VERIFIER_ESCALATE=1`, `VERIFIER_PASS=0.8`.
- **A5-H1.** A5b beats the direct judge (mean of 8 samples, 0.489) by ≥ +0.02 with a confidence interval that excludes 0 (draft accuracy, paired by task). The counterfactual of F26 predicts about +0.07.
- **A5-H2 (decomposition).** A5a beats the workflow default (0.331) by ≥ +0.05, and A5b beats A5a by ≥ +0.02.
- **Descriptive.** Share of episodes answered by the fallback; accuracy of the fallback answers against the direct judge's mean on the same tasks; accuracy by family; tokens per episode.
- **Abort.** A smoke run of 30 tasks must show fallback answers (otherwise the hook is not reached) before the two full runs are submitted.

## Addendum 2026-11-23 (2) (written before any result of O1-O3): is the MoE logic wrong, or was it never tested?

*Background.* F30: the planner sends 74 % of the subtasks to the `precision_tools` category and 56 % of the requests use no expert other than that tool path; every pipeline failure
(F26) is a planner contract error. The comparison "workflow against the direct judge" therefore did not test "specialist experts against a generalist". Three claims are separated: (a) the experts are
good on their topic; (b) correct routing would let them help; (c) their answers can be selected well. Tasks: the 491 held-out tasks (`orch_tasks_heldout.jsonl`); comparator: the direct fine-tuned 32B judge
(8 samples exist, 16 more are added, 24 in total); the experts are the eight fine-tuned SmolLM3-3B experts. The comparator is an open 32B model, not a frontier model.

**O1. Each expert alone, and what routing or selection could gain at best.** Every expert answers every task 3 times, with the system prompt it was fine-tuned with (primary) and without it (descriptive),
no planner, no judge, no gate; the judge answers 16 more times. Split of the tasks by `sha256(task_id) mod 2` (dev / test), fixed now.
- **O1-H1 (specialists on their topic).** At least one (expert, family) pair beats the direct judge on that family by ≥ +0.05 (mean accuracy per task, paired bootstrap, CI excluding 0).
- **O1-H2 (realistic routing).** A router that picks, per family, the best of {judge, the eight experts} on dev and is evaluated on test beats the judge on the test tasks by ≥ +0.02 (CI excluding 0).
- **O1-H3 (selection among answers).** With the verifier v2 choosing one answer from a matched number of 24 candidates per task, a pool of 12 judge + 12 expert answers beats a pool of 24 judge answers by ≥ +0.02 (CI excluding 0):
  the experts add something beyond the gain of choosing among more answers. Reported with it: 24 expert answers, 24 judge answers, and the curve over the number of candidates. Random subsets are drawn 200 times per task.
- **Decision rule.** If H2 or H3 is met, the MoE logic is justified for these tasks and the next step is a learned router or selector. If none of H1-H3 is met, experts of this quality add nothing on these tasks, and the
  test moves to knowledge-heavy tasks with real specialist models and a stronger generalist.

**O2. Forced routing instead of the LLM planner.** `PLANNER_MODE=rule` replaces the planner by rules: one task per request, category by keywords (data tables, SQL, database → `data_analyst`; graph, nodes, relationships, Cypher
→ `compounding_knowledge`; calculation of quantities → `precision_tools`; otherwise `general`), no planner call, no contract errors. Held-out tasks `orch_tasks_heldout2.jsonl`, the judge as in the default run.
- **O2-H1.** The workflow with rule routing beats the default workflow (0.331) by ≥ +0.05 (so the planner was an obstacle).
- **O2-H2.** It reaches the direct judge (0.489) within 0.03 or beats it (so routing was the main obstacle).
- Descriptive: share of failed episodes (expected 0), accuracy by family, tokens per episode, and the same with `MERGER_MODE=best_member`.

**O3. Routing audit.** From the plans of the default run: for each family, the categories the planner chose, compared with the expert that O1 finds best on that family; share of requests whose plan contains the best expert. Descriptive.

## Addendum 2026-11-23 (3) (written before any result of O4–O6): Do the experts help once their training prompt is gone and the route is chosen by competence?

Reason (F32–F34): the experts lose half their accuracy under the prompt they were fine-tuned with (F33); topic-named routing sends table tasks to the weakest expert (F32); the only configuration that beats the direct judge is the one with a verifier gate (F34). Three strands separate the remaining explanations. Tasks `orch_tasks_heldout2.jsonl` (491 tasks × 2) unless stated; paired bootstrap over tasks (2000 draws); run settings as in O2/A5.

**O4. Workflow without the experts' training prompt.** New switch `EXPERT_SYSTEM_PROMPT=plain` (the expert's own system prompt is left out; the mode suffix stays). O4a = `PLANNER_MODE=rule`, `MERGER_MODE=best_member`, plain prompt, no gate (identical to O2b except for the prompt). O4b = O4a + `PIPELINE_FALLBACK_DIRECT=1` + verifier gate with escalation (identical to A5b except for routing, merger and prompt).
- **O4-H1.** O4a beats O2b (0.457) by ≥ +0.04, CI excluding 0 (the prompt was an obstacle inside the workflow, too).
- **O4-H2.** O4b beats A5b (0.584) by ≥ +0.02, CI excluding 0.
- Reading: if O4-H1 fails, the template prompt of the pipeline does not harm the way the training prompt did, and the expert side is not the limit.

**O5. Base model against role fine-tunes.** The eight experts answer the 491 held-out tasks (3 samples) served from the SmolLM3-3B base checkpoint instead of the role fine-tunes (`EXPERT_CKPT`), with and without the role prompt, otherwise as in O1.
- **O5-H1.** The fine-tuned experts without prompt (mean over the eight roles 0.428, O1) beat the base model without prompt by ≥ +0.03 (paired over task × role, CI excluding 0): the role fine-tuning added competence.
- **O5-H2.** For at least one family the best fine-tuned expert beats the best base-model role by ≥ +0.03 (CI excluding 0).
- Reading: if neither holds, the role fine-tuning has no measurable benefit on these tasks and one base model would replace eight experts.

**O6. Competence router.** `RULE_ROUTER_MATRIX_FILE` maps the task family (detected by keywords: tables only, graph only, both) to the expert that is best on the **dev half** of the tasks (sha256(task_id) mod 2 = 0) in O1's plain variant (`experts/build_competence_matrix.py`; ties go to the alphabetically first role). The matrix is fixed before the run: `cypher_calc` → general, `cypher_sql_calc` → governance, `sql_calc` → code_reviewer. O6a = O4a with this routing; O6b = O4b with it. **Evaluation on the test half only** (mod 2 = 1).
- **O6-H1.** O6a beats O4a by ≥ +0.03, CI excluding 0 (routing by competence beats routing by topic).
- **O6-H2.** O6b beats O4b by ≥ +0.02, CI excluding 0.

**Decision rule.** If O4-H2 or O6-H2 is met, the best configuration is the gated workflow with plain experts (and competence routing), and the next step is the verifier-versus-strong-baselines comparison with a cost metric. If none of O4-H1, O4-H2, O6-H1, O6-H2 is met, the expert side does not limit the workflow and the gain of F34 is selection by the verifier alone. Reported in every case: failed share, accuracy by family, tokens per episode, and the share of episodes the gate sent to the direct fallback.

**Limits stated in advance.** The family detection by keywords is exact on this synthetic generator (491 of 491) and says nothing about free text; one run per arm; the matrix is chosen on 50 % of the tasks and evaluated on the other 50 %, so O6 has about half the power of O4; the verifier v2 comes from the same task generator; `precision_tools` stays an MCP category and is not a model in the pipeline (in O1 it was a fine-tuned model), so it is never selected by the matrix.
