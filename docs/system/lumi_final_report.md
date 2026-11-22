# LUMI-G Final Report (draft, updated 2026-11-20)

**Status:** draft. Sections marked **to complete** are filled in after the allocation expires on **2026-12-05**; everything
else is taken from the [activity log](lumi_activity_log.md) and carries its status labels. Nothing in a *to complete* section
may be written from expectation: it needs a job, a file or a command output as its source.

**Audience:** the operator, the funding body and the public documentation (website and whitepaper take only the labelled
excerpts of sections 3–5).

---

## 1. Summary (draft 2026-11-20, to be finalised after 2026-12-05)

Of 18,000 card-hours, 5,108 were booked by 2026-11-20. What they produced, all measured on a synthetic task suite with exact-number
scoring and a held-out set of 491 tasks plus 770 external tasks ([findings](lumi_findings_2026-11.md)): (1) the quality gate hides
most signal (correlation with correctness 0.10, AUROC 0.60), so the counterfactual dataset of waves 2/3 is not usable for a process-reward
model, and a 7B model trained on gate-free labels beats the baselines only slightly at the step level; (2) the product router never queried
the graph for lookups, although forcing it gives +0.60 accuracy; (3) the orchestrated workflow does **not** beat the single 32B model it
contains (−0.13 on held-out, −0.09 to −0.32 on external data), the largest single cost being a contract check and a crashing planner
server that were found and fixed; (4) a 7B **answer verifier** selects correct answers far better than the trust gate or a majority vote
(AUROC 0.95; best-of-N 0.64 against 0.44 for a single answer; transfer to numeric external data works, to free text it does not); (5) fine-tuning of planner and judge shows **no effect** at pipeline level (+0.001, CI −0.028 … +0.029), the workflow's deficit sits in one family (`sql_calc`), and extra router categories lift graph retrieval only partway (0 → 21 %). Open:
the knowledge-broker gate and the verifier pipeline. About 350 card-hours went into data that had to be discarded after a server fault,
which this report states as a mistake; the unused remainder is reported in section 2. Artefacts: section 7.

## 2. Allocation and use

| Quantity | Value | Source |
|---|---|---|
| Grant | 18,000 GPU-hours (MI250X cards; the meter counts cards, 4 per node, not GCDs) | operator, 2026-11-05; settled by the wave-2 accounting (74.1 node-h → 296 meter units) |
| Expiry | 2026-12-05 | operator |
| Used on 2026-11-10 | 3,858 (21.4 %) | `lumi-allocations` |
| Used at expiry | **to complete** | `lumi-allocations` on the last day, then once more after final booking |
| Jobs, states, partitions | **to complete** | `sacct -S 2026-06-01 -X -o JobID,JobName%24,Partition,AllocNodes,Elapsed,State` |
| Unused remainder and why | **to complete** | difference grant − used; state plainly if it lapses |

Booking lags: wave 3 appeared in the meter about a day after it ended (about 567 units at once). Read the final value after
the last job has been booked, not at the moment it ends.

## 3. What was done (phases A–J)

One paragraph per phase, taken from the activity log; each ends with its result status.

| Phase | Subject | Outcome in one line | Status |
|---|---|---|---|
| A | Router warm-up | ONNX router produced; lesson: containers must live on scratch | verified |
| B | Judge v1/v2 | many failures from ZeRO-3 + PEFT on a multimodal checkpoint; fixed | validated |
| C | Planner v1–v4 | see log | see log |
| D | Student and rule-based DPO | rule-based DPO was a data problem, no gain | see log |
| E | August expert campaign | largest loss of the grant; data problem, not compute | see log |
| F–G | Retraining, two model tracks | open-source track (Tier 1) and open-weight track (Tier 2) | verified |
| H | Local evaluation | informal quality gap between the tracks, not yet systematic | documented |
| I | P200: GRPO with execution rewards | first clear held-out gain of the grant; `token_truncate` importance sampling fixes the default `sequence_mask` collapse (precision 24/22, OOD 41/46 against HF baseline 26/27, 54/52; one seed) | validated |
| J | Verifier-guided orchestrator learning | see section 4 | partly open |

## 4. Verifier-guided orchestrator learning (phase J)

### 4.1 Counterfactual dataset (done)
Waves 2 and 3: counts of tasks, episodes, decision points and the signal share are in the log (wave 3: 6,963 tasks, 217,704
episodes, 52,769 decision points, 24.1 % with signal). **To complete:** final totals over both waves, the split sizes of
`data_ood` / `data_all`, and where the files are archived.

### 4.2 Orchestration process-reward model and answer verifier
| Item | Value |
|---|---|
| Baselines on the gate-free label (`test_ood`, within-task pairwise) | node mean 0.537, text (TF-IDF + ridge) 0.525; on the confounded label of waves 2/3 the text baseline reached 0.60 (gate signal) and 0.50–0.53 on gate-passed forks only |
| 7B smoke, 32B smoke (4 nodes) | trainer works end to end; 32B ≈ 2.4 samples/s on 32 ranks; the two 32B runs (23603688/89) were cancelled while pending |
| **7B PRM, three seeds** (23791608, 23814361–62), clean wave-4 rows | AUROC 0.873 (sd 0.003), Brier 0.078, within-task pairwise **0.564 (sd 0.007)**; learning curve +0.01 AUROC per doubling (25 % / 50 % / 100 % of the data: 0.848 / 0.862 / 0.874) |
| Verdict PRM | Better than both baselines by several seed spreads, but the step-level gain over the node mean is small (+0.027); no demonstrated decision-level value. A per-node figure for the planner is not defined (one planner point per task); the "planner 0.611" of 2026-11-17 was an artefact and is withdrawn. A 32B run was not justified |
| **7B answer verifier** (23842900): task + draft → correct, 112,057 rows, one epoch | test (4,247 answers of the direct judge and four workflow variants to the 491 held-out tasks): AUROC **0.952**, within-task pairwise 0.884, Brier 0.093; trust score AUROC 0.60. Best-of-N accuracy: direct N ≈ 3 single 0.531 / vote 0.567 / **verifier 0.606** / oracle 0.687; workflow N ≈ 6 0.411 / 0.511 / **0.577** / 0.684; pooled N ≈ 8.6 0.440 / 0.589 / **0.635** / 0.776 |
| Verdict verifier | The largest effect of the grant. Transfer to external data measured (23874318, 23874919: numeric yes, free text no). Open: unseen task families; the pipeline integration |

### 4.3 GraphRAG ablation
| Finding | Status |
|---|---|
| Waves 2/3: GraphRAG on in ~90 % of episodes but against an empty `:Entity` graph (task graphs used other labels) | verified |
| Fuzzy router switched GraphRAG off for lookup plans (graph confidence 0.30 < 0.35; the planner category `compounding_knowledge` is not graph-heavy) | verified |
| With forced GraphRAG the context holds the right values (10/10), but arithmetic went to MCP `calculate` with invented expressions and the trust gate blocked everything; arithmetic-free tasks and gate-free drafts fixed that | validated |
| Forced router, 480 tasks (23814342): graph 0.61, noisy 0.61, inline 0.75, empty 0.00; graph − empty +0.60 (CI +0.50 … +0.68), graph − inline −0.14 (−0.23 … −0.04); stale duplicates cost nothing measurable | validated |
| **Product router, 480 tasks (23795184): retrieved share 0.00 in every arm** | validated |
| External datasets: a functional check ran as the external-data comparison of workflow and direct model (770 tasks, [findings F5](lumi_findings_2026-11.md)); the graph arm of 20 graph questions was not meaningful without the graph | partly |
| Consequence in code: entity hits route to the graph, the knowledge broker supplies graph facts before the planner (branch `feat/knowledge-broker`); gate P0 (23874159–64) | pending |

### 4.4 Policy pilot (phase 3)
**Not started, with reasons.** A planner preference-pair set was collected and analysed (23618242, 23791558–61, 23843018): outcome noise after the
planner is as large as the planner effect, 69 % of the `calculate` plans are not evaluable and the experts answer from the inline data, and
a perfect planner would reach about 0.45 against 0.49 for the single model ([F7](lumi_findings_2026-11.md)). The budget that this phase
would have used stays unused; it is reported as such in section 2, not spent on a run without a question.

### 4.5 Run-by-run assessment (status 2026-11-19)

Costs are estimated from elapsed time × 4 cards per node and rounded. Ratings: achieved / partly / missed / invalid / open.

| # | Project | Jobs | Intended | Actual | Rating | Conclusion |
|---|---|---|---|---|---|---|
| 1 | Stack build and smokes | e.g. 23515523 ff. | The full MoE Sovereign stack runs in one Slurm job. | Runs (start ≈ 13 min); many porting pitfalls documented. | Achieved | Prerequisite met. About 20 % of job starts fail on single nodes (vLLM segfault, 8 cases, cost: minutes). |
| 2 | Waves 2 and 3 (counterfactual dataset) | 23522672–75, 23535809–14 | Gate-free orchestration labels for a PRM. | 217,000 episodes, 52,000 decision points. The label `q` is confounded by the gate: 50 % of forks blocked, text baseline 0.60 within a task, only 0.50–0.53 on gate-passed forks. Two jobs timed out. ≈ 950 card-h. | Partly | Unusable as PRM training data. The negative finding ("the gate dominates the label") was the main gain of this phase. |
| 3 | PRM early tests | 23549095/96, 23550288, 23556411, 23562153 | Check the training stack for 7B and 32B on several nodes. | Slingshot bindings, `transformers` 5 and shutdown hang fixed; 32B ≈ 2.4 samples/s; one failed start, two timeouts. | Achieved | Infrastructure works, cost per run known. A few card-h. |
| 4 | 32B PRM runs | 23603688/89 | Full 32B PRM (≈ 90 card-h each). | Cancelled before start, 0 consumed. The submission was premature; the text-baseline check should have come first. | Rightly stopped | Without signal in the label it would have been wasted compute. |
| 5 | GraphRAG smokes 1–4 | 23567371, 23569439✗/23572488, 23603690, 23642846 | Measure the GraphRAG effect. | Smoke 1: graph empty and router off. Smoke 2: 0 correct, the planner sends arithmetic to `calculate`. Smoke 3: everything blocked by the gate. Smoke 4: first effect (+0.53). | Partly | Each smoke exposed a real measurement or product defect; only the fourth measured something. ≈ 20 card-h. |
| 6 | GraphRAG ablation | 23795184, 23814342, 23795183✗ | Does the product router use the graph? Do stale facts cost accuracy? | Product router: retrieval 0.00. Forced: graph − empty +0.60, graph − inline −0.14 (CI −0.23 … −0.04), noisy ≈ graph. | Achieved | The effect exists but the product does not reach it; retrieval is worse than data in the prompt. Limit: synthetic single-value lookups. |
| 7 | Planner pair smoke | 23618242 | Check feasibility of planner pairs. | Six distinct plans per point, correctness independent of the gate. ≈ 5 card-h. | Achieved | Showed that the gate-free label works. |
| 8 | Wave 4 and planner wave | 23662477–87, 23667179 | PRM v2 labels and ≈ 7,700 planner pairs. | The planner server crashed in 8 of 11 jobs (vLLM KV-cache error), then only fallback plans. 49 % of the task records unusable, ≈ 350 card-h lost. | Partly | My failure: planner logs not checked after the smoke, no health check. Found via "1 instead of 6 distinct plans". |
| 9 | Reruns | 23791554–61, 23814340, 23814343✗/23843018 | Replace the unusable parts. | Planner fix holds: 0 crashes, 0 of 1,201 points with a single plan. Wave 5: 2,757 tasks, planner pairs 1,211 (808 running). ≈ 300 card-h. | Achieved | Clean data exists. The cost was a consequence of row 8. |
| 10 | PRM v2 (7B) | 23791608, 23814361–64 | Does a learned value model beat the baselines? | AUROC 0.873 (sd 0.003), within a task 0.564 (sd 0.007) against 0.537 (node mean) and 0.525 (text). Learning curve ≈ +0.01 AUROC per doubling of the data. An earlier "planner 0.611" was an artefact, corrected. ≈ 36 card-h. | Partly | The signal is real but small; a benefit for step choice or plan selection is not shown. 32B only as a clean scaling question. |
| 11 | Direct vs. workflow on the held-out tasks | 23795177–82, 23814341✗, 23814831✗ | Show that the workflow beats a single model, plus a stage ablation. | Direct 0.485, workflow 0.352 (−0.13, CI −0.17 … −0.09). The variants do not differ from the workflow beyond noise. 22 % of workflow episodes fail at the planner contract (153 of them at the `calculate` check I added). Equal on `cypher_calc` (0.72 vs. 0.71). Two runs invalid because of my watchdog. | Partly | A solid, **negative** result: on this suite the workflow shows no advantage. Claims to the contrary on the website and in the whitepaper are not covered. |
| 12 | External data and size curve | 23842891, 23842892, 23843014/16 | External validity, difficulty by size. | Direct judge on external data: math 0.94, tables 0.68, text QA 0.60. Workflow below it in all three families (−0.24 / −0.32 / −0.085); check off vs on mixed. Size curve of the direct judge: 0.66 / 0.42 / 0.16 / 0.05 at 19 / 57 / 190 / 570 rows. | Achieved | The single model is strong on small data and collapses above ≈ 60 rows; the workflow has no advantage on external data. |
| 13 | Answer verifier | 23842900 | A learnable checker against the trust score (AUROC 0.60). | AUROC 0.952 on answers to held-out tasks; best-of-N 0.635 against 0.440 for a single answer (pooled N ≈ 8.6), 0.606 against 0.531 for the direct judge at N ≈ 3. ≈ 20 card-h. | Achieved | The largest effect of the grant; transfer to external data pending (23874318), then the pipeline. |
| 14 | Control runs | 23843015/17/18 | Clean repeats of the invalid runs. | Deliberation 0.323 (−0.03 vs workflow); check off 0.410 vs 0.352 on held-out (+0.055), opposite sign on external data; planner pairs 808 collected. | Achieved | The check should degrade, not fail; deliberation has no benefit. |
| 15 | Knowledge broker (product code) | branch `feat/knowledge-broker` | Infrastructure knowledge before the planner, reconciliation, expert injection, entity-hit routing. | 23 commits, 1,452 tests, all new behaviour off by default; found and fixed correction-memory, streaming and tenant defects on the way. | Achieved (code) | Effect unmeasured until gate P0 (23874159–64). |
| 16 | Base vs. fine-tuned, factorial | 23858647–54, 23857467–70 | Do fine-tuned planner and judge change pipeline accuracy? | Held-out: both fine-tuned − both base **+0.001** (CI −0.028 … +0.029), planner −0.004, judge −0.020 (−0.045 … +0.007); single model 0.498 vs 0.489. External: +0.011 (−0.029 … +0.050). Experts fine-tuned in all runs. | Achieved (null result) | No effect above ≈ ±0.03 for planner and judge; experts unmeasured. See findings F12. |
| 17 | Sampling yield, workflow by family, router switches | 23857467–73, 23875180 | Does sampling close the workflow gap? Do extra router categories reach the graph? | pass@k direct 0.489 → 0.709 (k = 8), workflow 0.338 → 0.639 (k = 6); the workflow loses in `sql_calc` (−0.479) only and wins in `cypher_sql_calc` (+0.051). Extra categories: lookup 0.152 → 0.273 (forced 0.494), graph context 0 → 20.6 %. Held-out router runs are confounded by a loaded graph. | Achieved; router held-out part not interpretable | Sampling does not close the gap; the loss is one path. Control for the router held-out part is in gate P0. See F14–F16. |
| 18 | Verifier v2 | 23874919, 23874318 | Does external data in training widen the verifier? | In-domain unchanged (AUROC 0.963 / 0.952); `test_ext` AUROC 0.820. Transfer without external data: math 0.815–0.925, tables 0.72–0.80, text 0.59–0.61. | Achieved | No evidence that external answers close the free-text gap. See F10. |
| 19 | Knowledge-broker gate P0, fine-tuned experts, learning-loop pre-check | 23919612, 23924026–28, 23900911/13, 23924033/34, 23924041/42 | Does the broker pass the pre-registered gate? Do fine-tuned experts matter? Is the learning loop active in the benchmark? | Gate not passed: accuracy criteria met (lookup +0.672, held-out −0.012), p95 latency +253 s. Experts: held-out −0.020, external −0.043. Nothing was written to the graph in 60 episodes. | Achieved (mixed) | See F20–F22. The broker answers questions about the system's own knowledge at a cost in time; the learning loop was inactive in every earlier run. |
| 20 | Verifier as release rule: offline, prospective, in the loop, seed repeat, unseen families | 23914482/83, 23928260, 23926557/58, 23921905, 23924036 | Is the verifier a better release rule than the trust gate, and does it hold up? | Offline +0.060 over the direct judge; prospectively on a new set R2 +0.063 / +0.055, the family rule +0.004; in the loop 0.465 against 0.489 (23 % of episodes fail before the gate; +0.094 on answered ones); seed 2 within ±0.01; on unseen families AUROC 0.73–0.88 and the release rule does not carry over. | Achieved (mixed) | The strongest and best-confirmed result of the project, with two clear limits: it cannot rescue episodes that fail before the gate, and it is not shown to transfer to new families. See F17, F19, F23, F26, F28, F29. |
| 21 | Synthesis ablation and token budget | 23924030/31, 23926559, 23914438 | Does the loss of the workflow come from averaging the experts' answers? | Stopping the averaging gives +0.06 (judge solo, evidence, best member alike); the direct judge stays 0.09–0.10 ahead; the workflow uses 17 times the tokens. | Achieved | About 0.06 of the 0.158 loss is the merge; the rest sits in the pipeline shell. See F27. |
| 22 | SQL tool with an execution reward (GRPO) | 23914683, 23924058/59, 23926560, 23928221, 23924037–40 | Does a small tool-using model, trained on an execution reward, beat the single large model on large tables? | 32B inline 0.68 → 0.05 over table scales 1 → 30; 32B with the tool 0.81–0.86; GRPO-trained SmolLM3-3B / OLMo-3-7B 0.94–1.00 / 0.93–0.98. | Achieved | The tool carries most of the gain, training adds 0.12–0.14: the one place where a small model clearly beats the large one. See F25. |
| 23 | Planner with externalised knowledge (interview planner), generative verifier | 23900915, 23921943, 23926561, 23924043, 23928243, 23944192 | Can an SFT planner decide when to ask a knowledge model; does a generative verifier close the free-text gap? | Interview planner: no needless question on private or known-answer tasks (rule baseline 100 % on private data), no missed question; stack test not done. Rationales accepted 81 %; verifier fine-tuned, scoring pending. | Partly achieved | See F24; the generative verifier result is pending (D-H1). |
| 24 | Open at writing | 23944192 | Generative verifier on the three test sets. | Queued. | Open | See findings, section 6. |

Overall picture:

- **Used:** 5,108 of 18,000 card-h (28.4 %) as of 2026-11-19. About 350 went into data that had to be discarded and ≈ 15 into invalid runs. Roughly 11,000–12,000 card-h will probably stay unused; that is scientifically right if the reasons hold.
- **Solid findings:** the trust gate hides effects (correlation with correctness 0.10, AUROC 0.60); the product router does not use the graph; a single model beats the workflow on this suite; the PRM signal is small; plurality voting over samples is strong.
- **My mistakes:** two 32B runs submitted prematurely; smokes without checking the planner logs; the watchdog killed healthy servers; the `calculate` check fails hard after three retries; the driver dropped the draft of approved episodes; the PRM planner figure was an artefact.
- **Still missing for a result:** workflow vs. direct on external data, the clean control runs on the `calculate` check, and the verifier training. DPO, a 32B PRM and further waves stay deferred.

## 5. Negative results and dead ends

Collected so that they are read as findings: ZeRO-3 + PEFT on multimodal checkpoints; rule-based DPO; the August expert campaign; GRPO
`sequence_mask` importance sampling; the 2-node hang without Slingshot bindings; GraphRAG against an empty schema; arithmetic tasks that
measure the planner. From phase J: a label confounded by the quality gate (waves 2/3 unusable as PRM data); the planner preference-pair route
(noise as large as the effect); **the orchestrated workflow does not beat the single model on this suite or on the external sets**;
deliberation brings nothing; fine-tuning of planner and judge has no measurable pipeline-level effect (September benchmark and the factorial runs; the experts were not varied);
a PRM with a small step-level gain. Own mistakes that cost compute are listed in [findings, section 3](lumi_findings_2026-11.md): the server
fault that discarded ≈ 350 card-hours, a watchdog that killed a healthy server, a contract check that fails hard, a driver defect, a withdrawn figure.

## 6. Lessons (carry-over from the log, extend at the end)

Data before compute; training metrics are not capability; verifier rewards work where SFT stalled; library versions are part
of the experiment; fail fast; agent-submitted work belongs on small-g/standard-g. New in phase J: instrument the channel
(`answer_in_context`) before measuring the model; check that a switch really reaches the component (router overrode the
template toggle); estimate compute from a measured step time before budgeting (the 32B run needs about 90 card-hours, not
4,500).

## 7. Artefacts and export (deadline 2026-12-05)

| Artefact | Location on LUMI | Export | Status |
|---|---|---|---|
| Rollouts waves 2+3, PRM data | `$SCRATCH/moe-stack/runs`, `$SCRATCH/prm/data*` | local copy exists under `/opt/tmp/lumi-rollouts/` | **to complete:** final checksum list |
| 32B PRM checkpoints (about 60 GB each) | `$SCRATCH/prm/runs/prm32b_*` | **to complete:** copy or publish (HF), see policy below | open |
| Logs, job scripts, configs | `$SCRATCH/*/logs`, repository | repository holds scripts; logs **to complete** | open |
| Container images | `$SCRATCH/*.sif` | not exported (rebuildable) | decided |

Export policy (standing rules): no user data, logs of real users or memory contents leave the system; no secrets in the
repository; the private key is never transferred; datasets derived from third-party sets follow their licences (HotpotQA is
share-alike, SynthCypher non-commercial and excluded; FinQA conflicts between repository and Hugging Face card).

## 8. Data collection at the end (commands)

```bash
lumi-allocations
sacct -S 2026-06-01 -X -o JobID,JobName%24,Partition,AllocNodes,Elapsed,State,Start > sacct_all.tsv
cat $SCRATCH/prm/runs/prm32b_*/eval.json
du -sh $SCRATCH/prm/runs/* $SCRATCH/moe-stack/runs
sha256sum <export files> > SHA256SUMS
```

## 9. Open items after the allocation

Cannot continue without LUMI: larger sampling waves, seeds, a 32B model. Can continue locally or on Colab: the verifier pipeline
(a 7B scorer), the knowledge broker (product code, tests, N04 stack), the table-size suite, degrading the `calculate` check. Open
questions: [findings, section 6](lumi_findings_2026-11.md). Jobs still in the queue when this was written: base-vs-fine-tuned
(23858647–54), sampling yield and router (23857467–73), knowledge gate P0 (23874159–64), verifier transfer (23874318).
