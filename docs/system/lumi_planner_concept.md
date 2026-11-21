# Concept note: a planner that fetches knowledge it does not have

**Status: concept, 2026-11-22. Only option A has compute behind it (jobs in the queue); B and C are described so that they can be decided on evidence.**

**Goal.** A small model that is good at what the system needs from a planner: decide what to ask, ask a knowledge model only for what the infrastructure (graph, corrections,
episodes) does not hold, call tools with valid contracts, and stay inside the catalogue of the deployment. Knowledge stays outside the weights: it comes from the infrastructure
first and from a swappable knowledge LLM second; conflicts are reconciled by authority before recency (see the knowledge-broker design). A model that does this well is
useful beyond one deployment, because swapping the knowledge model or the knowledge base does not require retraining it.

## Three options

| | A: LoRA-SFT of OLMo-3-7B (running) | B: continued pre-training on ecosystem data | C: small model from scratch, knowledge externalised |
|---|---|---|---|
| Idea | Learn the interview format and the plan format from teacher traces | Show the base model catalogues, schemas, tool contracts and plans at scale | Pre-train a 0.3–1B model whose targets exclude retrievable facts, so it learns reasoning, planning and look-up behaviour only |
| Data | 1,095 task prompts, teacher Qwen3-Next-80B, accepted by rule per task kind | Synthetic catalogues and plans, a few billion tokens | Synthetic plans, tool-call traces and retrieval-annotated text; tens of billions of tokens |
| Compute | ≈ 100–150 card-h (traces, SFT, evaluation) | several hundred card-h plus data work | ≈ 150–400 card-h, depending on tokens |
| Main risk | Gains vanish at pipeline level (as with fine-tuned planner and judge, F12) | Unclear gain over A, hard to measure | Many unverified assumptions; capability floor of tiny models; data pipeline is the real cost |
| Measurable by | Interview accuracy, needless-question rate, conflict arms of gate P0 | The same, plus catalogue transfer | The same, plus swap of the knowledge model and of the catalogue without retraining |

## Why compute goes to A only
- A perfect planner reaches ≈ 0.45 against 0.49 for the single model on the synthetic tasks (F7): the planner is not the bottleneck there.
- Fine-tuning planner and judge has no measurable end-to-end effect (F12, ±0.03).
- The need "the model lacks knowledge" is already addressed without training by the knowledge broker (graph facts, corrections, optional knowledge model); its acceptance test (gate P0) has not run yet.
- The window until 2026-12-05 is short and the queue wait is one to two days: a from-scratch run cannot be iterated.

## What C would have to show before it gets compute
1. G1 passes (interview planner better than the current planner on the conflict arms; needless questions < 10 %).
2. A pre-registered, cheaper pilot: a 100–300M model trained for ≤ 20 card-h on synthetic interview and plan data beats the *untrained* base of the same size by a clear margin and approaches the A model on the offline interview test.
3. A pre-registered evaluation that varies the knowledge model (two different models) and the catalogue (renamed categories and tools) without retraining.

**Abort.** A pilot that does not beat the same-size untrained model, or an A model that does not beat the current planner, ends the line; the negative result is reported.

## Related work (read from abstracts, to be checked against the full texts)
Externalised knowledge in small models (language model with an external fact database, ICLR 2026; retrieval-augmented reasoning models that separate knowledge storage from reasoning), RL with execution
rewards for small tool-using models, learned small-to-large deferral. None of them studies a planner whose knowledge source is swappable and reconciled with an infrastructure of its own.
