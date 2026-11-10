# LUMI-G Final Report (draft, prepared 2026-11-10)

**Status:** draft. Sections marked **to complete** are filled in after the allocation expires on **2026-12-05**; everything
else is taken from the [activity log](lumi_activity_log.md) and carries its status labels. Nothing in a *to complete* section
may be written from expectation: it needs a job, a file or a command output as its source.

**Audience:** the operator, the funding body and the public documentation (website and whitepaper take only the labelled
excerpts of sections 3–5).

---

## 1. Summary (to complete)

Write last, five sentences at most: what the 18,000 card-hours produced, which results are measured, which are negative,
what remains open, where the artefacts are. No claim without a section below that carries its evidence.

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

### 4.2 Orchestration process-reward model
| Item | Value |
|---|---|
| Baseline to beat (train mean per family and node, `test_iid`) | AUROC 0.70, within-task pairwise 0.49 |
| 7B smoke (1 node), 32B smoke (4 nodes) | trainer works end to end; 32B ≈ 2.4 samples/s on 32 ranks |
| 32B runs `prm32b_ood` (23603688), `prm32b_all` (23603689) | **to complete:** final metrics from `eval.json` (AUROC, Brier, Spearman, within-task pairwise on `test_iid` and `test_ood`), seeds, wall time, card-hours |
| Verdict | **to complete:** does the PRM beat the family/node baseline within-task and on the held-out domain? State the answer even if it is no |

If the within-task pairwise accuracy does not exceed the baseline by more than the spread across seeds, the report says that
the PRM did not demonstrate decision-level value, and phase 3 is documented as not started for that reason.

### 4.3 GraphRAG ablation
| Finding | Status |
|---|---|
| Waves 2/3: GraphRAG on in ~90 % of episodes but against an empty `:Entity` graph (task graphs used other labels) | verified |
| Fuzzy router switched GraphRAG off for lookup plans (0.30 < 0.35); log line "template toggle" misleading | verified |
| With forced GraphRAG the context holds the right values (10/10), yet nothing is answered: the planner sends arithmetic to MCP `calculate` with invented expressions, the trust gate blocks, the merger returns empty text | validated (negative) |
| Arithmetic-free tasks (smoke 3, 23603690) | **to complete:** `answer_in_context`, retrieved share, correct and draft-correct per arm |
| Mini wave, external datasets (TAT-QA, FinQA, GSM8K, MuSiQue, HotpotQA, CypherBench) | **to complete:** run or state "not run" with the reason |

### 4.4 Policy pilot (phase 3)
**To complete:** run (CSO-DPO against SFT v4) with metrics, or "not started" with the reason and the budget that stayed
unused.

## 5. Negative results and dead ends

Collected from the log so that they are read as findings, not hidden: ZeRO-3 + PEFT on multimodal checkpoints; rule-based DPO;
the August expert campaign; GRPO `sequence_mask` importance sampling; the 2-node hang without Slingshot bindings; GraphRAG
against an empty schema; arithmetic tasks that measure the planner. **To complete:** add whatever the last weeks add.

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

What cannot continue without LUMI (larger waves, seeds, phase 3), what can run locally on N04, and which questions the
results leave: **to complete**.
