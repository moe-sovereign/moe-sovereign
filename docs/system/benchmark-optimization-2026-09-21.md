# Template optimisation on an MCP task (2026-09-21)

**Status:** validated measurements of 2026-09-20/21 on one benchmark task, one template, models and instance pinning unchanged.
**Task:** `sci-precision-02-ast-financial-arithmetic` (multi-step energy/cost/emission arithmetic through the MCP tool `calculate`,
deterministic numeric scoring with 0.5 % tolerance). **Template:** `LUMI-G OLMo + SmolLM3 Sovereign Ensemble - No-GraphRAG`
(planner `moe-sovereign-planner-olmo3-7b`, experts SmolLM3-3B fine-tunes on N02-M60, judge `sovereign-judge-olmo31-32b`).
**Method:** `benchmarks/optimize_expert_template.py` runs the template like the scientific benchmark (same request, same scoring,
same judge), stores planner attempts, MCP results, expert prompts/answers and log lines per request; a guard aborts a run if any
model, endpoint or planner/judge assignment differs from the frozen snapshot. 17 iterations, 91 runs. Failure analysis by the
fine-tuned Open Source judge (`benchmarks/debug_with_judge.py`) plus manual verification against the raw data.
**Limits:** one task, small n per iteration (6-10), scores are noisy: a configuration with mean 7.2 (n=6) measured 4.0 (n=10) in a
validation run. The deterministic score is judge-independent; the LLM-judge score is lenient on numbers.

## Result

| | Before the code fixes (18 runs) | After (73 runs) |
|---|---:|---:|
| Mean score (0-10) | 2.7 | 5.2 |
| Pipeline failures (HTTP 422/500) | 33 % | 14 % |
| All 6 figures correct | 11 % | 29 % |
| Median request time | 222 s | 114 s (70-110 s without foreign load on N04-RTX) |

## Defects found and fixed (all deployed)

| Defect | Fix (commit) |
|---|---|
| Expert prompts reached Ollama as the repr of message dicts inside user turns (100 % of recorded calls since July) | `_ollama_chat_messages` (073ec790) |
| A malformed planner array was silently cut to its first task; the gate then measured completeness against one task | repair truncated/bracket-slipped arrays, reject the rest (0d5104cc, ee812901) |
| The compact retry prompt asked only for task+category, so every retry lost `mcp_tool` | rule in the compact prompt (93a085f2) |
| Judge reload of 111 s per call: native calls without `num_ctx` forced the server default; the OpenAI-compatible `/v1` endpoint ignores `options.num_ctx` | reuse the loaded context, native `/api/chat` for plain chat (1de953c8, 13f315f8) |
| Judge reload next to a second large model could hang for hours (no VRAM freeing on the judge path) | evict only the missing VRAM before a larger reload (1de953c8) |
| `calculate` failed on one missing or surplus closing parenthesis and on `^` | tolerant repair, AST whitelist unchanged (76c6a52e, b297ecc8) |
| Plan limit of 8 counted deterministic MCP calls (one per requested figure) | limit counts model tasks, MCP calls get 3x ceiling (0577d7e4) |
| One planner generation ran to the 16384 token cap (776 s) | `MAX_PLANNER_TOKENS=4096` (.env) |
| Category `dynamic` bypassed pinned rosters | sanitizer (a0523d8c) |

## Prompt-level levers (kept in the optimised template, no robust effect measured)

Planner prompt block inserted before the `dynamic` sentence:

```
PRECISION PLANNING RULES (calculations):
- Every precision_tools task MUST contain "mcp_tool": "calculate" and "mcp_args": {"expression": "<expression>"}: exactly one string, no other fields, no placeholders.
- Each expression runs on its own: numbers and operators only, no variable names, no reference to other tasks or earlier results. Write the whole chain inline, keep it flat, and count your parentheses: every "(" needs its ")".
- Formula sheet: kW * hours = kWh; kWh / 1000 = MWh; cost in EUR = kWh * (EUR per kWh), never MWh * (EUR per kWh); tonnes of CO2 = kWh * (grams per kWh) / 1000000. Keep given numbers in the unit they are stated in and put the conversion into the divisor.
- Dimension check before you write an expression: energy is kWh = kW*hours*days. An expression for a cost in EUR contains no "/1000" (that would turn the energy into MWh and make the cost 1000 times too small); only MWh figures and gram-to-tonne conversions divide.
- Yearly increases: year 2 = base * f2, year 3 = base * f2 * f3 (keep every earlier factor). A multi-year total adds each year with its own factors inside one expression; it is never a single year multiplied by the number of years unless the yearly value is constant.
- Example with other numbers (120 kW, 24 h/day, 365 days, 0.25 EUR/kWh, 300 g CO2/kWh, +3% then +2%): energy in MWh = 120*24*365/1000; cost in EUR = 120*24*365*0.25; year 3 cost = 120*24*365*0.25*1.03*1.02; 3-year cost = 120*24*365*0.25*(1+1.03+1.03*1.02); CO2 in tonnes over 3 years with a constant yearly load = 120*24*365*3*300/1000000.
- One calculate task per requested figure, at most 6 tasks. No rounding, conversion or summation tasks; the answer rounds. Never add tasks that were not requested and never copy example tasks. The JSON array must be valid and never empty.
```

Judge (merger) prompt tail:

```
SYNTHESIS RULES: (1) Numbers: use only values that appear in the tool results or verified evidence; copy them exactly and never recompute or "correct" them yourself; if a value is missing, say so. (2) Answer the request directly: one numbered item per requested figure with its value and a one-line formula; no meta commentary, no self-doubt, no alternative solution attempts. (3) Be concise: at most 250 words plus a short results table. (4) If your own arithmetic differs from a tool value, the tool value is correct.
```

Tried and rejected: merger plausibility check with power-of-ten correction (mean 3.9, it "corrected" right values), an advice rule
with unit relations (10-run validation 4.0), added planning limits (mean 4.1). Original prompts: `benchmarks/results/opt/backup_original_*.json`.

## What remains

The 7B planner writes dimension errors in about three quarters of the runs (cost = MWh x price per kWh, gram/tonne factors).
Levers that need a decision: a dedicated parameter-based MCP tool for energy/cost/emissions, a planner self-consistency vote,
dimension-consistent calculation decompositions in the next planner training data, a re-plan when magnitudes are implausible.

## Same task on the Open Weight template (2026-09-21)

`Open-Weight Finetuned Ensemble - No-GraphRAG` (planner `moe-sovereign-planner-9b`, experts Qwen3.5-4B fine-tunes on N02-M60, judge
`sovereign-judge-27b`) with the same planner/judge prompt blocks and the same code/infrastructure fixes, 10 runs
(`benchmarks/results/opt/sp2_run1.json`). All 10 runs: all six figures correct (deterministic score 10), no pipeline failure, mean
score 9.3 (LLM-judge component graded by the Spur 2 judge, which is not comparable to the Spur 1 judge), median request time 108 s;
9 of 10 runs needed a planner retry (first attempt structurally invalid), the retry then produced a correct plan.
Comparison, same code: Spur 1 mean 5.0 (10 of 48 runs with all figures correct, 4 of 48 pipeline failures).
Before the fixes only Spur 1 was measured (mean 2.7); there is no Spur 2 measurement without the fixes.
