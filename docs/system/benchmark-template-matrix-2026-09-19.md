# Benchmark template matrix (2026-09-22)

**Status:** validated against the live database and the Ollama endpoints at generation time (`python3 scripts/benchmark_template_matrix.py`). Regenerate after every template change.

Design per track: 3 pre-finetune templates, 3 fine-tuned templates and 1 native baseline (7 conditions). Comparability criteria checked per fine-tuned template against its pre-finetune counterpart:

- **C1** identical structure (categories, flags, context windows, endpoints, number of models per category); only model names differ
- **C2** identical planner, judge and expert system prompts
- **C3** every referenced model is present on its endpoint
- **C4** `local_only`, cache off, web research off
- **C5** exactly eight experts

## Spur 1 (open source)

Native baseline (single dense LLM without orchestration, default of `run_spur1_and_spur2.sh`): `olmo31-32b-instruct-base-fixed:latest` on N04-RTX (confirmed by the operator on 2026-09-19).

| Role | Template (id) | Planner | Judge | GraphRAG | Debate | Prompt hashes planner / judge | C1 | C2 | C3 | C4 | C5 | Verdict |
|---|---|---|---|:-:|:-:|---|:-:|:-:|:-:|:-:|:-:|---|
| Pre-Finetune, GraphRAG | `LUMI-G Base (Pre-Finetune)` (`tmpl-7be691d7`) | `olmo3-7b-instruct-base-fixed:latest` @N04-RGTX ctx 65536 | `olmo31-32b-instruct-base-fixed:latest` @N04-RTX ctx 65536 | on | off | dafd04de / ebfbd7b0 | - | - | yes | yes | yes | pre-finetune |
| Pre-Finetune, no GraphRAG | `LUMI-G Base (Pre-Finetune) - No-GraphRAG` (`tmpl-37d31274`) | `olmo3-7b-instruct-base-fixed:latest` @N04-RGTX ctx 65536 | `olmo31-32b-instruct-base-fixed:latest` @N04-RTX ctx 65536 | off | off | dafd04de / ebfbd7b0 | - | - | yes | yes | yes | pre-finetune |
| Pre-Finetune, GraphRAG + debate | `LUMI-G Base (Pre-Finetune) - Deliberation` (`tmpl-a026a606`) | `olmo3-7b-instruct-base-fixed:latest` @N04-RGTX ctx 65536 | `olmo31-32b-instruct-base-fixed:latest` @N04-RTX ctx 65536 | on | on | dafd04de / ebfbd7b0 | - | - | yes | yes | yes | pre-finetune |
| Fine-tuned, GraphRAG | `LUMI-G OLMo + SmolLM3 Sovereign Ensemble` (`tmpl-11f532fc`) | `moe-sovereign-planner-olmo3-7b:Q4_K_M` @N04-RGTX ctx 65536 | `sovereign-judge-olmo31-32b:Q4_K_M` @N04-RTX ctx 65536 | on | off | dafd04de / ebfbd7b0 | yes | yes | yes | yes | yes | sound |
| Fine-tuned, no GraphRAG | `LUMI-G OLMo + SmolLM3 Sovereign Ensemble - No-GraphRAG` (`tmpl-smollm3-nograph`) | `moe-sovereign-planner-olmo3-7b:Q4_K_M` @N04-RGTX ctx 65536 | `sovereign-judge-olmo31-32b:Q4_K_M` @N04-RTX ctx 65536 | off | off | dafd04de / ebfbd7b0 | yes | yes | yes | yes | yes | sound |
| Fine-tuned, GraphRAG + debate | `LUMI-G OLMo + SmolLM3 Sovereign Ensemble - Deliberation` (`tmpl-smollm3-delib`) | `moe-sovereign-planner-olmo3-7b:Q4_K_M` @N04-RGTX ctx 65536 | `sovereign-judge-olmo31-32b:Q4_K_M` @N04-RTX ctx 65536 | on | on | dafd04de / ebfbd7b0 | yes | yes | yes | yes | yes | sound |

### Spur 1 (open source): expert assignment (category -> model @ endpoint)

| Category | Training role prompt | Pre-Finetune reference | Fine-tuned |
|---|---|---|---|
| `general` | d675e123 | `HuggingFaceTB_SmolLM3-3B-GGUF:Q4_K_M` @N02-M60-02 | `smollm3-expert-omni-3b:Q4_K_M` @N02-M60-02 |
| `security` | 0e981d9d | `HuggingFaceTB_SmolLM3-3B-GGUF:Q4_K_M` @N02-M60-03 | `smollm3-expert-security-3b:Q4_K_M` @N02-M60-03 |
| `research` | 85289ded | `HuggingFaceTB_SmolLM3-3B-GGUF:Q4_K_M` @N02-M60-04 | `smollm3-expert-research-3b:Q4_K_M` @N02-M60-04 |
| `governance` | 8ff953dd | `HuggingFaceTB_SmolLM3-3B-GGUF:Q4_K_M` @N02-M60-05 | `smollm3-expert-governance-3b:Q4_K_M` @N02-M60-05 |
| `compounding_knowledge` | 2dcc7cc8 | `HuggingFaceTB_SmolLM3-3B-GGUF:Q4_K_M` @N02-M60-06 | `smollm3-expert-graphrag-3b:Q4_K_M` @N02-M60-06 |
| `precision_tools` | 915b98f7 | `HuggingFaceTB_SmolLM3-3B-GGUF:Q4_K_M` @N02-M60-07 | `smollm3-expert-precision-3b:Q4_K_M` @N02-M60-07 |
| `data_analyst` | 64567ed0 | `HuggingFaceTB_SmolLM3-3B-GGUF:Q4_K_M` @N02-M60-08 | `smollm3-expert-datainfra-3b:Q4_K_M` @N02-M60-08 |
| `code_reviewer` | 1c1872c1 | `HuggingFaceTB_SmolLM3-3B-GGUF:Q4_K_M` @N02-M60-09 | `smollm3-expert-coder-3b:Q4_K_M` @N02-M60-09 |

## Spur 2 (open weight)

Native baseline (single dense LLM without orchestration, default of `run_spur1_and_spur2.sh`): `qwen3.8:27b` on N04-RTX (confirmed by the operator on 2026-09-19).

| Role | Template (id) | Planner | Judge | GraphRAG | Debate | Prompt hashes planner / judge | C1 | C2 | C3 | C4 | C5 | Verdict |
|---|---|---|---|:-:|:-:|---|:-:|:-:|:-:|:-:|:-:|---|
| Pre-Finetune, GraphRAG | `Open-Weight Base (Pre-Finetune)` (`tmpl-95dbac05`) | `qwen3.5:9b` @N04-RGTX ctx 131072 | `qwen3.8:27b` @N04-RTX ctx 262144 | on | off | dafd04de / ebfbd7b0 | - | - | yes | yes | yes | pre-finetune |
| Pre-Finetune, no GraphRAG | `Open-Weight Base (Pre-Finetune) - No-GraphRAG` (`tmpl-db16e336`) | `qwen3.5:9b` @N04-RGTX ctx 131072 | `qwen3.8:27b` @N04-RTX ctx 262144 | off | off | dafd04de / ebfbd7b0 | - | - | yes | yes | yes | pre-finetune |
| Pre-Finetune, GraphRAG + debate | `Open-Weight Base (Pre-Finetune) - Deliberation` (`tmpl-d3b31c32`) | `qwen3.5:9b` @N04-RGTX ctx 131072 | `qwen3.8:27b` @N04-RTX ctx 262144 | on | on | dafd04de / ebfbd7b0 | - | - | yes | yes | yes | pre-finetune |
| Fine-tuned, GraphRAG | `Open-Weight Finetuned Ensemble` (`tmpl-ow-ft`) | `moe-sovereign-planner-9b:Q4_K_M` @N04-RGTX ctx 131072 | `sovereign-judge-27b:Q4_K_M` @N04-RTX ctx 262144 | on | off | dafd04de / ebfbd7b0 | yes | yes | yes | yes | yes | sound |
| Fine-tuned, no GraphRAG | `Open-Weight Finetuned Ensemble - No-GraphRAG` (`tmpl-ow-ft-nograph`) | `moe-sovereign-planner-9b:Q4_K_M` @N04-RGTX ctx 131072 | `sovereign-judge-27b:Q4_K_M` @N04-RTX ctx 262144 | off | off | dafd04de / ebfbd7b0 | yes | yes | yes | yes | yes | sound |
| Fine-tuned, GraphRAG + debate | `Open-Weight Finetuned Ensemble - Deliberation` (`tmpl-ow-ft-delib`) | `moe-sovereign-planner-9b:Q4_K_M` @N04-RGTX ctx 131072 | `sovereign-judge-27b:Q4_K_M` @N04-RTX ctx 262144 | on | on | dafd04de / ebfbd7b0 | yes | yes | yes | yes | yes | sound |

### Spur 2 (open weight): expert assignment (category -> model @ endpoint)

| Category | Training role prompt | Pre-Finetune reference | Fine-tuned |
|---|---|---|---|
| `general` | d675e123 | `qwen3.5:4b` @N02-M60-02 | `moe-expert-omni-4b:Q4_K_M` @N02-M60-02 |
| `research` | 85289ded | `qwen3.5:4b` @N02-M60-04 | `moe-expert-research-4b:Q4_K_M` @N02-M60-04 |
| `security` | 0e981d9d | `qwen3.5:4b` @N02-M60-03 | `moe-expert-security-4b:Q4_K_M` @N02-M60-03 |
| `governance` | 8ff953dd | `qwen3.5:4b` @N02-M60-05 | `moe-expert-governance-4b:Q4_K_M` @N02-M60-05 |
| `data_analyst` | 64567ed0 | `qwen3.5:4b` @N02-M60-08 | `moe-expert-datainfra-4b:Q4_K_M` @N02-M60-08 |
| `code_reviewer` | 1c1872c1 | `qwen3.5:4b` @N02-M60-09 | `moe-expert-coder-4b:Q4_K_M` @N02-M60-09 |
| `precision_tools` | 915b98f7 | `qwen3.5:4b` @N02-M60-07 | `moe-expert-precision-4b:Q4_K_M` @N02-M60-07 |
| `compounding_knowledge` | 2dcc7cc8 | `qwen3.5:4b` @N02-M60-06 | `moe-expert-graphrag-4b:Q4_K_M` @N02-M60-06 |

## Cross-track parity (Open Source vs Open Weight)

Everything except the model names and the per-model context windows must be identical between the tracks: categories, expert/planner/judge instances, flags, planner/judge prompts and the effective expert prompt (template text plus tool hint block). Context windows follow the rule "planner and judge = maximum of the model, experts = largest context that fits the 8 GB GPU" and are listed per track.

| Condition | Differences | Context planner / judge / expert (Spur 1 vs Spur 2) |
|---|---|---|
| Pre-Finetune, GraphRAG | none | 65536 / 65536 / 65536 vs 131072 / 262144 / 98304 |
| Pre-Finetune, no GraphRAG | none | 65536 / 65536 / 65536 vs 131072 / 262144 / 98304 |
| Pre-Finetune, GraphRAG + debate | none | 65536 / 65536 / 65536 vs 131072 / 262144 / 98304 |
| Fine-tuned, GraphRAG | none | 65536 / 65536 / 65536 vs 131072 / 262144 / 98304 |
| Fine-tuned, no GraphRAG | none | 65536 / 65536 / 65536 vs 131072 / 262144 / 98304 |
| Fine-tuned, GraphRAG + debate | none | 65536 / 65536 / 65536 vs 131072 / 262144 / 98304 |

## Findings and open points

1. **Spur 1 fine-tuned variants were not comparable and have been corrected (2026-09-19).** In all three variants
   (`tmpl-11f532fc`, `tmpl-smollm3-nograph`, `tmpl-smollm3-delib`) the category `code_reviewer` had a second, forced
   security model (`role: always`) and `review_lenses: ["security"]`; the reference has one model. With
   `MOE_REVIEW_WAVE_ENABLED` (default on in the deployed orchestrator) only the fine-tuned arm would have run extra
   review calls. Both additions were removed (backup `benchmarks/results/runbook/spur1_finetuned_before_cleanup_20260919.json`);
   all six pairs now pass C1 to C4. They are not part of the benchmark design and can re-appear if another agent edits
   the templates again: re-run this script before every benchmark start. `review_lenses` still exist in the Review and
   Review NoSC arms (intended) and in the three `LUMI-G Ensemble` hybrids (outside the matrix).
2. **Spur 2 is comparable.** All six templates pass C1 to C5.
3. **System prompts are aligned (C2)** by `scripts/align_benchmark_template_prompts.py`. Experts: each category gets the
   training role prompt of its assigned domain expert; judge: the training judge prompt. **Planner: not the training
   prompt.** An A/B test (9 planner calls per variant, 2026-09-19) showed that the training preamble with a
   training-format category block never routed a GDPR question to `governance` (0/6) and once returned an empty plan,
   while the original list format routed it 3/3 and never returned an empty plan. The canonical planner prompt is
   therefore the original descriptive list ("- category: description" plus the empty-plan guard) for each template's own
   categories in a fixed order; for Spur 1 it is byte-identical to the validated original. The same category has the
   same expert prompt hash in both tracks. Expert-prompt alignment itself has no A/B evidence yet.
4. **Every template has exactly eight experts (C5, operator requirement 2026-09-19).** Spur 2 carried 15 categories; it
   now has the eight Spur 1 categories. `precision_tools` keeps the slot of the former `tool_expert` and
   `compounding_knowledge` the slot of the former `graphrag` (models, endpoints, MCP tool lists unchanged); the categories
   `systems_programming`, `web_researcher`, `reasoning`, `math`, `technical_support`, `dynamic` and `science` were removed.
   Both planners therefore list the same eight experts with the same text (prompt hash 7c72ab83 in both tracks). Backup:
   `benchmarks/results/runbook/template_prompts_backup_20260919T130053Z.json`. Spur 2 keeps its MCP tool lists per expert;
   the Spur 1 experts have none (not a C1 criterion, but a structural difference).
5. **Empty-plan guard in the planner prompt.** The sentence "You MUST always produce at least one task. NEVER return an
   empty JSON array." is not part of the planner training prompt (`generate_planner_dataset.py` rejects empty plans as a
   training sample). It was already in the Spur 1 fine-tuned template before the alignment (byte-identical) and its
   wording matches the runtime fallback prompts added on 2026-08-08 (commit `fb7b5378`, for a planner that returned `[]`);
   the date it entered the template cannot be established from Git because templates live in the database. The runtime
   also creates a fallback task for an empty plan (`graph/planner.py`), so the guard is redundant for pipeline
   correctness. Whether removing it changes routing was not measured (A/B blocked, see the status log).
6. **Instance placement and expert prompts are identical in both tracks (operator, 2026-09-20):** one expert per instance N02-M60-02..09 in
   ascending port order, judge N04-RTX (:11434), planner N04-RGTX (:11435, same host); expert `mcp_tools` are empty in both tracks. `mcp_tools`
   only selects the "Available Tools" text block appended to the expert system prompt (`services/helpers.py`), the tools themselves are run by
   the planner path; with an empty list the block depends only on the category, so equal categories give equal effective prompts. The
   "Cross-track parity" table above checks this on the live templates.
   **Context windows follow one rule (operator, 2026-09-20): planner and judge = maximum of the model, experts = largest context that fits the VRAM of
   their GPU.** Native maxima (Ollama `/api/show`): OLMo 3 7B and OLMo 3.1 32B 65536, Qwen3.5-9B and Qwen3.8-27B 262144, SmolLM3-3B 65536, Qwen3.5-4B 262144.
   Expert VRAM measurement on idle 8 GB Maxwell GPUs (f16 KV cache, 2026-09-20): Qwen3.5-4B fine-tune 65536 = 5.0 GB / 12.1 tok/s, 98304 = 6.1 GB / 11.9 tok/s,
   114688 = 6.7 GB / 6.8 tok/s, 131072 = 7.3 GB / 0.3 tok/s, 196608 = 33 % CPU offload; SmolLM3-3B fine-tune 48128 = 5.7 GB / 8.7 tok/s, 65536 = 6.9 GB / 8.7 tok/s.
   Chosen: Qwen experts 98304 (last size without throughput loss), SmolLM3 experts 65536 (native maximum, fits). Spur 1 templates still carry 48128 for
   the experts because the templates must not change during the running benchmark: apply `align_benchmark_template_prompts.py --apply --tracks spur1`
   after the run. Not verified on the target GPU: the Qwen3.5-9B planner at its maximum of 262144 needs about 16 GB (9B weights plus 34 KB KV per token) on
   N04-RGTX (18 GB); a load test on an idle multi-GPU instance placed only 7 GB on the GPU (56 % CPU offload). The Spur 2 planner is therefore provisionally
   capped at 131072 (about 11 GB by the same formula, operator agreed 2026-09-20); after Spur 1 finishes, test 262144 on N04-RGTX (fully in VRAM, no throughput loss) and raise
   `CONTEXT["spur2"]["planner"]` if it fits. The templates only
   set the cap: the orchestrator requests a context adapted to the prompt size, so short prompts do not allocate the full window.
   **Remaining inherent differences:** the models, the judges (each track is judged by its own fine-tuned judge) and the context windows above. Absolute scores must not
   be compared across tracks; compare each track with its own baseline.
7. **Planner taxonomy (unverified effect):** the planners were trained on a fixed taxonomy (`legal_advisor`, `agentic_coder`, ...);
   `security`, `governance` and `compounding_knowledge` are not part of it. The planner must generalise to them; this is not measured.
8. **Native baseline:** called through the orchestrator route `model@N04-RTX` with the user prompt only (no system
   prompt) at temperature 0.2. Confirmed by the operator on 2026-09-19: Spur 1 `olmo31-32b-instruct-base-fixed:latest`,
   Spur 2 `qwen3.8:27b` (the base model of the Spur 2 judge).
9. **Design per track: 7 conditions** = 3 pre-finetune templates + 3 fine-tuned templates (GraphRAG, no GraphRAG,
   GraphRAG + debate) + 1 native baseline. Each pre-finetune template is compared with its fine-tuned counterpart.
10. **Not part of the matrix:** the Review and Review NoSC variants (review-wave arms), the `LUMI-G Ensemble` hybrids and
   the `moe-frontier-*` templates.
