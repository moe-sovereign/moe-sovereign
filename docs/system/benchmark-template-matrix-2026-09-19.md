# Benchmark template matrix (2026-09-19)

**Status:** validated against the live database and the Ollama endpoints at generation time (`python3 scripts/benchmark_template_matrix.py`). Regenerate after every template change.

Design per track: 3 pre-finetune templates, 3 fine-tuned templates and 1 native baseline (7 conditions). Comparability criteria checked per fine-tuned template against its pre-finetune counterpart:

- **C1** identical structure (categories, flags, context windows, endpoints, number of models per category); only model names differ
- **C2** identical planner, judge and expert system prompts
- **C3** every referenced model is present on its endpoint
- **C4** `local_only`, cache off, web research off

## Spur 1 (open source)

Native baseline (single dense LLM without orchestration, default of `run_spur1_and_spur2.sh`): `olmo31-32b-instruct-base-fixed:latest` on N04-RTX (**to be confirmed**).

| Role | Template (id) | Planner | Judge | GraphRAG | Debate | Prompt hashes planner / judge | C1 | C2 | C3 | C4 | Verdict |
|---|---|---|---|:-:|:-:|---|:-:|:-:|:-:|:-:|---|
| Pre-Finetune, GraphRAG | `LUMI-G Base (Pre-Finetune)` (`tmpl-7be691d7`) | `olmo3-7b-instruct-base-fixed:latest` @N04-RGTX ctx 65536 | `olmo31-32b-instruct-base-fixed:latest` @N04-RTX ctx 65536 | on | off | a9300b51 / 627b2c29 | - | - | yes | yes | pre-finetune |
| Pre-Finetune, no GraphRAG | `LUMI-G Base (Pre-Finetune) - No-GraphRAG` (`tmpl-37d31274`) | `olmo3-7b-instruct-base-fixed:latest` @N04-RGTX ctx 65536 | `olmo31-32b-instruct-base-fixed:latest` @N04-RTX ctx 65536 | off | off | a9300b51 / 627b2c29 | - | - | yes | yes | pre-finetune |
| Pre-Finetune, GraphRAG + debate | `LUMI-G Base (Pre-Finetune) - Deliberation` (`tmpl-a026a606`) | `olmo3-7b-instruct-base-fixed:latest` @N04-RGTX ctx 65536 | `olmo31-32b-instruct-base-fixed:latest` @N04-RTX ctx 65536 | on | on | a9300b51 / 627b2c29 | - | - | yes | yes | pre-finetune |
| Fine-tuned, GraphRAG | `LUMI-G OLMo + SmolLM3 Sovereign Ensemble` (`tmpl-11f532fc`) | `moe-sovereign-planner-olmo3-7b:Q4_K_M` @N04-RGTX ctx 65536 | `sovereign-judge-olmo31-32b:Q4_K_M` @N04-RTX ctx 65536 | on | off | a9300b51 / 627b2c29 | yes | yes | yes | yes | sound |
| Fine-tuned, no GraphRAG | `LUMI-G OLMo + SmolLM3 Sovereign Ensemble - No-GraphRAG` (`tmpl-smollm3-nograph`) | `moe-sovereign-planner-olmo3-7b:Q4_K_M` @N04-RGTX ctx 65536 | `sovereign-judge-olmo31-32b:Q4_K_M` @N04-RTX ctx 65536 | off | off | a9300b51 / 627b2c29 | yes | yes | yes | yes | sound |
| Fine-tuned, GraphRAG + debate | `LUMI-G OLMo + SmolLM3 Sovereign Ensemble - Deliberation` (`tmpl-smollm3-delib`) | `moe-sovereign-planner-olmo3-7b:Q4_K_M` @N04-RGTX ctx 65536 | `sovereign-judge-olmo31-32b:Q4_K_M` @N04-RTX ctx 65536 | on | on | a9300b51 / 627b2c29 | yes | yes | yes | yes | sound |

### Spur 1 (open source): expert assignment (category -> model @ endpoint)

| Category | Training role prompt | Pre-Finetune reference | Fine-tuned |
|---|---|---|---|
| `general` | d675e123 | `HuggingFaceTB_SmolLM3-3B-GGUF:Q4_K_M` @N02-M60-02 | `HuggingFaceTB_SmolLM3-3B-GGUF:Q4_K_M` @N02-M60-02 |
| `security` | 0e981d9d | `HuggingFaceTB_SmolLM3-3B-GGUF:Q4_K_M` @N02-M60-03 | `HuggingFaceTB_SmolLM3-3B-GGUF:Q4_K_M` @N02-M60-03 |
| `research` | 85289ded | `HuggingFaceTB_SmolLM3-3B-GGUF:Q4_K_M` @N02-M60-04 | `HuggingFaceTB_SmolLM3-3B-GGUF:Q4_K_M` @N02-M60-04 |
| `governance` | 8ff953dd | `HuggingFaceTB_SmolLM3-3B-GGUF:Q4_K_M` @N02-M60-05 | `HuggingFaceTB_SmolLM3-3B-GGUF:Q4_K_M` @N02-M60-05 |
| `compounding_knowledge` | 2dcc7cc8 | `HuggingFaceTB_SmolLM3-3B-GGUF:Q4_K_M` @N02-M60-06 | `HuggingFaceTB_SmolLM3-3B-GGUF:Q4_K_M` @N02-M60-06 |
| `precision_tools` | 915b98f7 | `HuggingFaceTB_SmolLM3-3B-GGUF:Q4_K_M` @N02-M60-07 | `HuggingFaceTB_SmolLM3-3B-GGUF:Q4_K_M` @N02-M60-07 |
| `data_analyst` | 64567ed0 | `HuggingFaceTB_SmolLM3-3B-GGUF:Q4_K_M` @N02-M60-08 | `HuggingFaceTB_SmolLM3-3B-GGUF:Q4_K_M` @N02-M60-08 |
| `code_reviewer` | 1c1872c1 | `HuggingFaceTB_SmolLM3-3B-GGUF:Q4_K_M` @N02-M60-09 | `HuggingFaceTB_SmolLM3-3B-GGUF:Q4_K_M` @N02-M60-09 |

## Spur 2 (open weight)

Native baseline (single dense LLM without orchestration, default of `run_spur1_and_spur2.sh`): `qwen3.6:27b` on N04-RTX (**to be confirmed**).

| Role | Template (id) | Planner | Judge | GraphRAG | Debate | Prompt hashes planner / judge | C1 | C2 | C3 | C4 | Verdict |
|---|---|---|---|:-:|:-:|---|:-:|:-:|:-:|:-:|---|
| Pre-Finetune, GraphRAG | `Open-Weight Base (Pre-Finetune)` (`tmpl-95dbac05`) | `qwen3.5:9b` @N04-RGTX ctx 32768 | `qwen3.8:27b` @N04-RTX ctx 262144 | on | off | 43275a9b / 627b2c29 | - | - | yes | yes | pre-finetune |
| Pre-Finetune, no GraphRAG | `Open-Weight Base (Pre-Finetune) - No-GraphRAG` (`tmpl-db16e336`) | `qwen3.5:9b` @N04-RGTX ctx 32768 | `qwen3.8:27b` @N04-RTX ctx 262144 | off | off | 43275a9b / 627b2c29 | - | - | yes | yes | pre-finetune |
| Pre-Finetune, GraphRAG + debate | `Open-Weight Base (Pre-Finetune) - Deliberation` (`tmpl-d3b31c32`) | `qwen3.5:9b` @N04-RGTX ctx 32768 | `qwen3.8:27b` @N04-RTX ctx 262144 | on | on | 43275a9b / 627b2c29 | - | - | yes | yes | pre-finetune |
| Fine-tuned, GraphRAG | `Open-Weight Finetuned Ensemble` (`tmpl-ow-ft`) | `moe-sovereign-planner-9b:Q4_K_M` @N04-RGTX ctx 32768 | `sovereign-judge-27b:Q4_K_M` @N04-RTX ctx 262144 | on | off | 43275a9b / 627b2c29 | yes | yes | yes | yes | sound |
| Fine-tuned, no GraphRAG | `Open-Weight Finetuned Ensemble - No-GraphRAG` (`tmpl-ow-ft-nograph`) | `moe-sovereign-planner-9b:Q4_K_M` @N04-RGTX ctx 32768 | `sovereign-judge-27b:Q4_K_M` @N04-RTX ctx 262144 | off | off | 43275a9b / 627b2c29 | yes | yes | yes | yes | sound |
| Fine-tuned, GraphRAG + debate | `Open-Weight Finetuned Ensemble - Deliberation` (`tmpl-ow-ft-delib`) | `moe-sovereign-planner-9b:Q4_K_M` @N04-RGTX ctx 32768 | `sovereign-judge-27b:Q4_K_M` @N04-RTX ctx 262144 | on | on | 43275a9b / 627b2c29 | yes | yes | yes | yes | sound |

### Spur 2 (open weight): expert assignment (category -> model @ endpoint)

| Category | Training role prompt | Pre-Finetune reference | Fine-tuned |
|---|---|---|---|
| `code_reviewer` | 1c1872c1 | `qwen3.5:4b` @N04-TM10-01 | `qwen3.5:4b` @N04-TM10-01 |
| `systems_programming` | 1c1872c1 | `qwen3.5:4b` @N04-TM10-01 | `qwen3.5:4b` @N04-TM10-01 |
| `research` | 85289ded | `qwen3.5:4b` @N04-TM10-01 | `qwen3.5:4b` @N04-TM10-01 |
| `web_researcher` | 85289ded | `qwen3.5:4b` @N04-TM10-01 | `qwen3.5:4b` @N04-TM10-01 |
| `general` | d675e123 | `qwen3.5:4b` @N04-TM10-01 | `qwen3.5:4b` @N04-TM10-01 |
| `reasoning` | d675e123 | `qwen3.5:4b` @N04-TM10-01 | `qwen3.5:4b` @N04-TM10-01 |
| `math` | 915b98f7 | `qwen3.5:4b` @N04-TM10-02 | `qwen3.5:4b` @N04-TM10-02 |
| `data_analyst` | 64567ed0 | `qwen3.5:4b` @N04-TM10-02 | `qwen3.5:4b` @N04-TM10-02 |
| `tool_expert` | 915b98f7 | `qwen3.5:4b` @N04-TM10-02 | `qwen3.5:4b` @N04-TM10-02 |
| `technical_support` | 64567ed0 | `qwen3.5:4b` @N04-TM10-03 | `qwen3.5:4b` @N04-TM10-03 |
| `dynamic` | 64567ed0 | `qwen3.5:4b` @N04-TM10-03 | `qwen3.5:4b` @N04-TM10-03 |
| `security` | 0e981d9d | `qwen3.5:4b` @N04-TM10-01 | `qwen3.5:4b` @N04-TM10-01 |
| `governance` | 8ff953dd | `qwen3.5:4b` @N04-TM10-01 | `qwen3.5:4b` @N04-TM10-01 |
| `science` | 2dcc7cc8 | `qwen3.5:4b` @N04-TM10-01 | `qwen3.5:4b` @N04-TM10-01 |
| `graphrag` | 2dcc7cc8 | `qwen3.5:4b` @N04-TM10-01 | `qwen3.5:4b` @N04-TM10-01 |

## Findings and open points

1. **Spur 1 fine-tuned variants were not comparable and have been corrected (2026-09-19).** In all three variants
   (`tmpl-11f532fc`, `tmpl-smollm3-nograph`, `tmpl-smollm3-delib`) the category `code_reviewer` had a second, forced
   security model (`role: always`) and `review_lenses: ["security"]`; the reference has one model. With
   `MOE_REVIEW_WAVE_ENABLED` (default on in the deployed orchestrator) only the fine-tuned arm would have run extra
   review calls. Both additions were removed (backup `benchmarks/results/runbook/spur1_finetuned_before_cleanup_20260919.json`);
   all six pairs now pass C1 to C4. They are not part of the benchmark design and can re-appear if another agent edits
   the templates again: re-run this script before every benchmark start. `review_lenses` still exist in the Review and
   Review NoSC arms (intended) and in the three `LUMI-G Ensemble` hybrids (outside the matrix).
2. **Spur 2 is comparable.** All six templates pass C1 to C4.
3. **System prompts are aligned (C2)** by `scripts/align_benchmark_template_prompts.py`: each expert category gets the
   training role prompt of its assigned domain expert, the judge gets the training judge prompt, and the planner gets the
   training preamble plus a category block in the training format and the guard against empty plans. The full
   12.8k-character training planner prompt is deliberately not used because `graph/planner.py` appends the routing
   rules itself and they would appear twice. The same category has the same prompt hash in both tracks.
4. **Model assignment aligned across tracks:** `data_analyst` was served by the precision expert in Spur 2 and by the
   data-infrastructure expert in Spur 1; Spur 2 now follows Spur 1. Questionable but unchanged: Spur 2 `science` and
   `dynamic` are served by the GraphRAG and data-infrastructure experts.
5. **Differences between the tracks that are inherent, not defects:** category sets (8 vs 15), planner context
   (65536 vs 32768), judge context (65536 vs 262144) and different judges. Absolute scores must not be compared across
   tracks; compare each track with its own baseline.
6. **Planner taxonomy (unverified effect):** the planners were trained on a fixed taxonomy; `security`, `governance`,
   `compounding_knowledge` (Spur 1) and `systems_programming`, `web_researcher`, `tool_expert`, `graphrag` (Spur 2) are
   not part of it. The planner must generalise to them; this is not measured.
7. **Native baseline:** called through the orchestrator route `model@N04-RTX` with the user prompt only (no system
   prompt) at temperature 0.2. The model defaults above follow the operator's earlier statement (Qwen3.6-27B for
   Open Weight, OLMo 3.1 for Open Source) and are not yet confirmed.
8. **Design per track: 7 conditions** = 3 pre-finetune templates + 3 fine-tuned templates (GraphRAG, no GraphRAG,
   GraphRAG + debate) + 1 native baseline. Each pre-finetune template is compared with its fine-tuned counterpart.
9. **Not part of the matrix:** the Review and Review NoSC variants (review-wave arms), the `LUMI-G Ensemble` hybrids and
   the `moe-frontier-*` templates.
