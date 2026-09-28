# Expert template audit (2026-09-19)

**Status:** validated for the measured facts (database, Ollama and log state on 2026-09-19, i.e. before the removals
listed in section 4; the inventory CSV shows that earlier state); *planned* for every recommendation in section 6 that
section 4 does not list as applied.
**Method:** all rows of `admin_expert_templates` (58) and `user_expert_templates` (52) were read from the database,
every referenced `model@endpoint` was checked against the endpoint's `/api/tags` (or the owner's private API
connections), usage was taken from `routing_telemetry` and `usage_log`, and every planner, judge and expert system
prompt was compared with the prompts used to train the fine-tuned models.
**Limits:** `routing_telemetry` wrote no rows between 2026-09-14 and 2026-09-18 (defect fixed on 2026-09-18), so "never
used" is a lower bound for that window; `usage_log` was used as a second source for dynamic templates. Model
availability is only verifiable for Ollama endpoints; templates that use cloud endpoints through a user's private
connection are reported as "ok" if the connection exists and is active. Other users' template owners are anonymised
in `expert-template-inventory-2026-09-19.csv`.

## 1. Inventory

| Family | Count | Health | Note |
|---|---:|---|---|
| Dynamic templates (`moe-dyn-*`, auto-generated) | 34 | all broken | created 2026-09-03..11, never used; planner `qwen3-planner:q4km` exists on no node |
| Spur 1 fine-tuned (`LUMI-G OLMo + SmolLM3 Sovereign Ensemble` + Deliberation, No-GraphRAG, Review, Review NoSC) | 5 | ok | benchmark family |
| Spur 1 pre-finetune (`LUMI-G Base (Pre-Finetune)` + 2 variants) | 3 | ok | reference family |
| `LUMI-G Ensemble` + 2 variants | 3 | ok | hybrid: SmolLM3 experts, planner-9B, OLMo judge; no clear purpose |
| Spur 2 pre-finetune (`Open-Weight Base (Pre-Finetune)` + 2 variants) | 3 | ok | planner now base Qwen3.5-9B |
| Spur 2 fine-tuned (`Open-Weight Finetuned Ensemble` + 2 variants) | 3 | ok | created 2026-09-19 |
| `moe-frontier-*` | 3 | broken | 15 expert names do not exist on any node; duplicates of the Spur 2 fine-tuned family |
| `MoE Sovereign ...` (old Qwen benchmark, seeded by the admin service) | 4 | broken | planner `moe-sovereign-student:4b` without endpoint |
| User templates (5 owners) | 52 | 36 broken, 16 ok | see the CSV; 39 belong to the operator, 13 to other users; 49 still use `moe-sovereign-student:4b` |

Only 17 of 58 admin templates and 16 of 52 user templates are usable today. No user template has been used since
2026-08-29 other than the old benchmark copies.

## 2. Why nobody can see through them

1. **Auto-generated clutter.** The dynamic router writes a new `moe-dyn-<uuid>` template per unmatched request pattern;
   29 of the 30 IDs found in `usage_log` are already gone and the 34 that remain were never reused.
2. **Templates re-created on every start.** `seed_default_admin_templates()` (`admin_ui/database.py`, called from the
   admin lifespan) upserts the four `MoE Sovereign ...` templates with `moe-sovereign-student:4b`, so deleting or
   editing them does not persist.
3. **Family sprawl without a naming rule.** Base, fine-tuned, hybrid, frontier and review variants coexist and only
   the name distinguishes them.
4. **Dead references.** `qwen3-planner` appears in 37 templates and in the global default `PLANNER_MODEL`, although
   the model is present on no node; `moe-sovereign-student:4b` appears in 53 templates; `gpt-oss` (10), `gemma` (11),
   `Nemotron` (6) and `H200` (3) appear in user templates for cloud connections.
5. **Shared model stores.** Ollama instances on one host share a single model store, so a model tag cannot be
   removed "per instance" (found while removing `sovereign-judge:27b`).

## 3. System prompt audit

| Check | Result |
|---|---|
| Distinct planner / judge / expert prompts over 110 templates | 34 / 29 / 192 (122 expert prompts are shared by more than one slot) |
| Templates without planner or judge prompt | 7 (they fall back to the code defaults in `prompts.py`) |
| Expert slots without system prompt | 0 of 779 |
| Planner prompt names a category the template does not have | 1 template |
| Models carry a baked-in system prompt | no (`/api/show` returns an empty `system` for planner-9B, judge-27B, coder-4B and security-3B) |

### Training/serving mismatch (fine-tuned families)

The fine-tuned models were trained with fixed, long role prompts (`CHATML_SYSTEM_PROMPTS` in
`scripts/generate_expert_ensemble_datasets.py`, `PLANNER_SYSTEM_PROMPT` in `scripts/generate_planner_dataset.py`). The
templates send unrelated short prompts instead:

| Prompt | Runtime (template) | Training | Text similarity |
|---|---|---|---:|
| Expert (Spur 1 fine-tuned, 8 categories) | mean 107 chars, e.g. "You are a senior principal systems engineer and code reviewer." | mean 341 chars, e.g. "You are the MoE Sovereign Expert for Systems Programming, Low-Level Concurrency, and Kernel Architecture. ..." | 0.06 |
| Expert (Spur 2, 15 categories) | mean 62 chars | mean 338 chars | 0.08 |
| Planner (Spur 1 / Spur 2 / hybrid) | 2491 / 531 / 1303 chars | 12,789 chars (rendered with a category block and a `MANDATORY:` suffix) | 0.02 |
| Judge | 215 chars ("You are a specialized synthesis judge ...") | 304 chars ("You are the MoE Sovereign Paraconsistent Quality Gate & Judge ...") | 0.11 |

Consequences: the fine-tuned models run off-distribution relative to their training prompt, which understates any
measured fine-tuning effect. The runtime additionally caps the planner role at `PLANNER_ROLE_MAX_CHARS` (8000), below
the 12,789-character training prompt, and appends its own blocks (task budget, tool catalog, JSON instructions) that
the training data does not contain. The pre-finetune reference templates use the same generic prompts, so a
weights-only comparison is fair only if both families use the same prompts.

## 4. Applied so far

| Change | Status |
|---|---|
| 34 dynamic + 3 user templates repointed from the removed `sovereign-judge:27b` to `hf.co/h3rb3rn/sovereign-judge-27b` | applied |
| `qwen3.5:4b` planner replaced (Open-Weight base: base `qwen3.5:9b`; frontier: `moe-sovereign-planner-9b`) | applied |
| Spur 2 fine-tuned template family created, `qwen3.5:9b` and `moe-expert-coder-4b:Q4_K_M` pulled to N04 | applied |
| 34 dynamic templates and their 34 permission rows deleted (never used, all broken; backup `benchmarks/results/runbook/dynamic_templates_backup_20260919.json`) | applied (2026-09-19, operator approval) |
| Seed `seed_default_admin_templates()` removed from `admin_ui/database.py` and `admin_ui/app.py`; the four seeded `MoE Sovereign ...` templates (planner `moe-sovereign-student:4b`) and their 16 permission rows deleted (backup `moe_sovereign_seeded_templates_backup_20260919.json`) | applied; takes effect in the running admin process at its next restart |
| Harness defaults for the three template variables now point at the Spur 1 fine-tuned templates | applied |
| `moe-sovereign-student:4b` removed everywhere it was referenced functionally (operator, 2026-09-19): `planner_model` of all 49 user templates (5 owners, incl. 13 of other users; the field was the only reference) repointed to `hf.co/h3rb3rn/moe-sovereign-planner-olmo3-7b:Q4_K_M@N04-RGTX` instead of deleting the templates; entries removed from `context_budget.py`, `configs/model_capabilities.yaml`, the model registration/download/upload scripts; `install.sh` and `scripts/ingest_pdf_knowledge.py` now use the OLMo planner. Kept as history: the model card, training/SLURM comments, earlier docs and 915 rows of `ai_io_audit_log` / `dynamic_template_feedback_log`. Backup of the 49 rows: `benchmarks/results/runbook/student4b_and_orphans_backup_20260919.json` (git-ignored) | applied |
| 47 orphaned `permissions` rows (expert_template rows pointing at no existing template, 11 users) deleted | applied (same backup) |
| Global default planner `PLANNER_MODEL` changed from `qwen3-planner:q4km` (on no node) to `hf.co/h3rb3rn/moe-sovereign-planner-olmo3-7b:Q4_K_M` in `.env`; orchestrator recreated from the unchanged image (`PLANNER_NUM_CTX` stays 40960); startup log confirms the model | applied |
| Spur 2 templates reduced from 15 to the eight Spur 1 categories (operator requirement: every template has 8 experts); backup `benchmarks/results/runbook/template_prompts_backup_20260919T130053Z.json` | applied (2026-09-19) |
| Prompts of 14 benchmark templates aligned by `scripts/align_benchmark_template_prompts.py`: experts and judge use the training role prompts, the planner uses the original descriptive category list (not the training prompt, see below); backup of the pre-alignment rows in `benchmarks/results/runbook/template_prompts_backup_original_pre_alignment_20260919.json` | applied (2026-09-19, operator approval) |

## 5. Not verified

- Whether the global default planner `qwen3-planner:q4km` is silently replaced by a fallback at runtime.
- Whether the planner trained on the full category set behaves correctly with the 8- or 15-category subsets in the
  templates (`VALID_CATEGORIES` in the training script versus template categories).
- The effect of aligned prompts on scores; this must be measured (see section 6, item 2).

## 6. Recommendations (planned)

1. Stop the dynamic router from persisting templates whose planner does not exist (the 34 dead ones are deleted).
2. **Align prompts with training** in the fine-tuned and pre-finetune benchmark templates: *done for experts and judge*
   (training role prompts, effect not yet measured). *Planner: deliberately not aligned.* A/B test on 2026-09-19 (9
   planner calls per variant, one Spur 1 template, GDPR probe question; n is small, so this is a routing sanity check,
   not a score measurement):

   | Planner prompt | empty plans | GDPR routed to `governance` |
   |---|---:|---:|
   | Original list format (kept) | 0 / 9 | 3 / 3 |
   | Training preamble + category list (v1) | 1 / 9 | 0 / 3 |
   | Training preamble, variant 2 | 0 / 9 | 0 / 3 |
   | Rule-based prompt from the parallelisation plan | 0 / 9 | 1 / 3 |
3. Replace the global default planner (`PLANNER_MODEL`, currently `qwen3-planner:q4km`, present on no node) with an
   existing fine-tuned planner; requires an orchestrator restart. The 49 user templates that use
   `moe-sovereign-student:4b` belong to their owners.
4. Delete or repair the three `moe-frontier-*` templates and decide on the `LUMI-G Ensemble` hybrids.
5. Adopt a naming rule (`<track>-<stage>-<variant>`) and add a `description` to every admin template; user templates of
   other users are theirs to decide.
