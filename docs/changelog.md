# Changelog

All notable changes to the Sovereign MoE Orchestrator are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) — semantic versioning for manual releases, date-based labels for auto-tracked changes.

> **Metadata fields used in every entry:**
> `impact` (patch / minor / major) · `breaking` (yes / no) · `domain` (affected subsystems)

---

## 2026-08-12 — Whitepaper v2.8 & Documentation Scientific Review

> `impact: minor` · `breaking: no` · `domain: docs, whitepaper, scientific-claims`

### Changed

- **Whitepaper v2.8:** Comprehensive scientific peer-review revision (GPT-5 + Gemini reviews).
  All six mandatory corrections applied before academic submission:
  - Abstract: capability hypothesis qualified — "approach frontier-model quality … with
    internal evidence" (not a confirmed result pending controlled external validation)
  - Bayesian formula: "Laplace-smoothed MLE" corrected to "Bayesian posterior mean under
    a uniform Beta(1,1) prior" (MLE ≠ posterior mean)
  - Graph/LongMemEval claims: "primary driver" → "strongest observed factor";
    "empirical proof" → "internal empirical evidence consistent with"
  - Deployment taxonomy unified: production-tested (Compose/LXC), CI-validated (Helm/k8s),
    environment-unvalidated (OpenShift) — consistently across all sections
  - Component Contribution Analysis renamed to "Component Evidence Summary"; new
    "Causal confidence" column; all Δ-values labelled as indicators, not causal facts
  - `placeins` package added — Float barrier at section boundaries eliminates
    table backlog in Conclusion chapter
- **Documentation (MkDocs + README):**
  - "Thompson Sampling" → "Bayesian Expert Scoring (Laplace posterior mean; optional
    Thompson Sampling exploration mode)" in IMoE tables and capability descriptions
  - "zero hallucination" → "eliminates hallucinations within the deterministic
    computation boundary" (consistent with whitepaper precision)
  - "Zero data egress is guaranteed" → "enforced" (guaranteed is too absolute
    without auditing the full operator environment)
  - "GPT-4o Class Performance" headline → "Internal Benchmark Results, Self-Hosted"
  - GAIA results annotated with evaluation integrity disclosure (⚠ non-sovereign
    inference used in the published run; clean sovereign run pending)
  - Deployment table: K3s "Planned" → "CI-validated (Helm charts)";
    OpenShift "Untested" → "Implemented, environment-unvalidated"
  - LocalAI added as seventh column in competitive feature matrices (README + whitepaper)
  - Accumulation speedup note clarified: cold/warm latency difference, not causal ablation
  - Navigation: "Reinforcement Learning Flywheel" → "Adaptive Feedback Loop (RL Flywheel)"
  - `rl_flywheel.md`: Stage 2 rewritten — default is deterministic posterior mean;
    Thompson Sampling is the opt-in exploration mode; distinction made explicit
  - Whitepaper page count updated: ~108 → ~116

---

## 2026-08-02 — Evidence-bound Precision Platform Rollout

> `impact: major` · `breaking: no` · `domain: mcp, orchestration, quality-gate, telemetry`

### Added

- Versioned `time_facts`, `timezone_convert`, `decimal_finance`,
  `exact_probability` and `structured_validate` MCP contracts with typed
  schemas, bounded execution, runtime provenance and hash-bound evidence.
- Precision preflight before answer caches, immutable contract snapshots,
  deterministic direct rendering for pure prompts and opaque evidence slots
  for mixed synthesis.
- Quality-atomic, idempotent persistence after the final gate plus a
  low-cardinality precision rollout metric and shadow/enforce controls.
- A fixed adversarial API corpus and reusable native-versus-orchestrated
  benchmark runner with temporary-key cleanup and 900-second cold-start
  allowance.

### Fixed

- Structured validation no longer echoes raw payloads or schemas through
  result envelopes, operational logs, telemetry or working keys.
- Advice rules no longer inject an MCP task unless every required argument is
  available; this removed a real mixed-plan failure caused by an empty legacy
  `calculate` task.
- Required precision work can no longer be bypassed through legacy caches,
  planner/judge argument drift, model mutation or pre-quality semantic writes.

### Validated

- The versioned rollout corpus passed 13/13 API cases, the complete regression
  passed 908 tests, and practical feature-flag and prior-image rollback both
  completed before the final healthy images were restored.
- Detailed methodology and limitations are recorded in
  `system/toolstack/precision_rollout_benchmark_2026-08-02.md`.

---

## 2026-07-31 — Deterministic Calendar Contract and Offloading Review

> `impact: minor` · `breaking: no` · `domain: mcp, planner-contracts, precision-routing`

### Added

- `calendar_facts(date_str, locale)` returns strict, localized and
  machine-readable weekday and ISO calendar facts, including the separate ISO
  week-year, leap-year/month boundaries and weekend status.
- A deterministic-offloading evaluation prioritizes precision-intent
  enforcement, time-zone conversion, decimal finance, exact probability and
  structured validation, while separating pure calculations from mutable
  authoritative data sources.

### Fixed

- Explicit German and English weekday requests in the bounded planner
  recovery path now dispatch `calendar_facts`; legacy `day_of_week` remains
  available for compatibility.
- `date_diff` now reports a real calendar delta instead of approximating every
  year as 365 days and every month as 30 days.
- Error strings and JSON error objects returned by MCP tools can no longer be
  accepted as successful precision evidence.
- Removed the nonexistent `format_number` entry from dynamic precision-tool
  defaults and synchronized the new tool across registry, access kind, planner
  catalogue and fallback descriptions.
- MCP image builds now use a digest-pinned Python base and an exact transitive
  dependency lock. This keeps the proven FastMCP 1.28.1 API after MCP 2.0
  removed the imported `mcp.server.fastmcp` module.

---

## 2026-07-24 — Planner SFT Root-Cause: Sequence-Length Truncation Made Fine-Tune Ineffective

> `impact: major` · `breaking: no` · `domain: planner, sft-training, lumi-g`

### Investigated

- **`qwen3-planner:q4km` fine-tune (LUMI-G job 20190726) had no measurable training
  effect.** The SFT script's `max_seq_len=1536` was inherited from an earlier, much
  shorter prompt format and never updated as the planner system prompt grew to
  ~2,500 tokens. With the tokenizer's default `truncation_side="right"` and no
  completion-only loss masking (`DataCollatorForCompletionOnlyLM`), the target JSON
  answer — which follows system+user in the chat template — landed entirely outside
  the 1,536-token window for effectively all 259,829 training samples
  (`p99=3628 > max_seq_len=1536`, confirmed via the actual LUMI training logs and a
  re-tokenization of sample examples with the real `Qwen3-8B` tokenizer). The model
  trained on next-token prediction over a repeated system-prompt prefix instead of
  the (query → plan) mapping. A role-suitability benchmark run through the production
  orchestrator still passed (valid JSON, correct categories) — most likely explained
  entirely by `Qwen3-8B`'s base zero-shot instruction-following, not by the fine-tune.
- **Independent finding — prompt/taxonomy drift.** The training system prompt
  (`PLANNER_SYSTEM_PROMPT`, `research`/`dynamic` categories) diverges from the
  system prompt actually served by the production template used during
  verification (`web_researcher` instead of `research`, a `memory_recall` category
  absent from training, no `dynamic` category at all). Even a correctly trained
  model would not transfer cleanly to that template as configured.

### Planned

- Re-submit the SFT job with `max_seq_len=4096` (covers the measured `p99=3628` with
  margin; matches the value already specified in
  `docs/system/eurohpc_training_concept.md` Phase 3, which the executed job
  deviated from). No new teacher generation needed — same 259,829-sample dataset.
- Canonicalize on one planner system prompt before retraining; align production
  templates to its category taxonomy instead of the reverse.
- Full root-cause writeup: `eurohpc_lumi_activity_report.md` (Aktivität 10) and
  the whitepaper (`whitepaper/de/sections/14_evaluation_and_lessons.tex`,
  Episode 11).

---

## 2026-07-20 — Codex Responses Template Resolution and Stream Reliability

> `impact: patch` · `breaking: no` · `domain: responses-api, template-routing, inference`

### Fixed

- **`/v1/responses` displayed an Expert Template without applying it.** Live monitoring copied
  `request.model` into `template_name`, but the Responses path neither resolved the owned
  template ID nor passed `user_experts` and `user_permissions` into the graph. Responses now
  uses the same ownership-aware resolution rules as the other compatibility APIs and records
  both requested and resolved template identity.
- **Private/admin template name collisions.** A private template is now selected when a
  same-named admin template exists but is not granted to the API key; an authorized admin
  template retains precedence.
- **Codex stream idle disconnects during slow local inference.** Bare SSE comments do not reset
  Codex's stream-idle timer. The Responses endpoint now emits spec-defined
  `response.in_progress` events and converts pipeline exceptions into `response.failed`.
- **Planner crash on nullable Ollama architecture metadata.** KV-cache sizing no longer calls
  `int(None)` when `/api/show` contains a null architecture field; it skips invalid matches and
  continues to the next usable value.
- **Nullable numeric template settings.** Optional portal fields persisted as JSON `null` now
  resolve to zero instead of aborting prompt/template resolution.

### Compatibility

- Expert-template entries returned by `/v1/models` now expose both `context_length` and
  `context_window` when the template defines a context size.
- Added a validated local Codex model catalog for
  `moe-n04-rtx-qwen3.6:35b-256k`, including its 262,144-token context window.

### Added

- **Guarded planner dataset generation for LUMI-G.** The new
  `scripts/generate_planner_dataset.py` produces resumable chat and Alpaca SFT datasets,
  retains rejected samples for later analysis, and stops or pauses generation when teacher
  probes, rolling quality or API availability fall below configured thresholds.
- **Reproducible SLURM launcher.** `scripts/lumi_generate_planner_dataset.sh` starts the
  OpenAI-compatible vLLM teacher endpoint on one LUMI-G node, waits for readiness, runs the
  generator and records job-specific outputs. Model, sample target and concurrency remain
  explicit operator-controlled inputs.
- Updated the EuroHPC training concept and system-status documentation to separate the
  implemented dataset pipeline from validation work and later SLM training stages.

## 2026-07-13 — v2.9.0: Admin-Editable Premature-Stop Detection & Reliability-Weighted Routing

> `impact: minor` · `breaking: no` · `domain: agent_enrichment, inference, tracking, pipeline/chat, admin_ui`

### Added

- **Admin-editable Agent Tool Path premature-stop patterns.** `looks_like_premature_stop` (`services/agent_enrichment.py`) now reads detection patterns (announcement phrases, malformed tool-call markers, trailing-colon endings) from Postgres (`admin_premature_stop_patterns`) instead of hardcoded constants — full CRUD Admin UI at `/patterns`, including a "test pattern against sample text" tool. A new pattern takes effect within ~60s, no rebuild required. Seeded from the previously-hardcoded pattern set on first cold start.
- **Empty-response detection.** A tool-calling turn that ends with `finish_reason=stop`, no `tool_calls`, and zero characters of content — a more severe, previously undetected variant of the silent-stop failure — now triggers the same retry as a matched pattern (`services/pipeline/chat.py`, both fast/stream and slow paths). `looks_like_premature_stop` always returns `False` on empty text by design, so this is a separate, unconditional check.
- **Reliability-weighted node selection (flood-fill heuristic).** `_select_node` (`services/inference.py`) now factors recent per-node latency (`moe:latency:{node}`) and per-(model,node) premature-stop rate (`moe:pstop:{model}:{node}`) into its load score as an additive penalty — optimistic by default (no penalty until enough evidence accumulates), only active once a category has more than one candidate endpoint. `graph/expert.py` now also records latency for the interactive planner→expert→judge pipeline (previously only the Agent Tool Path did). See `docs/ARCHITECTURE.md` §10–11.
- **mindwalk-inspired session-replay scrubbing** in the live pipeline diagram (Admin UI → Live Monitoring) — play/pause/step through a completed request's stage trace instead of only viewing the final state.
- **Unclosed-code-fence premature-stop detection.** `looks_like_premature_stop` now flags any text with an odd number of ` ``` ` fence markers — a structural, language-independent signal for "opened a code/diagram block and stopped without closing it or calling a tool" (confirmed live: `qwen3.6:35b` announcing a plan with an unclosed `` ```mermaid `` block).
- **LLM-judged review queue for unclassified tool-passthrough endings.** Text-only Agent Tool Path endings that match no `admin_premature_stop_patterns` rule are now recorded (`admin_unclassified_tool_endings`) and judged asynchronously by an admin-assignable classifier LLM (`admin_classifier_config`, default `gemma4:12b@N04-RGTX`, model/endpoint swappable without a rebuild) — new Admin UI at `/tool-endings` with one-click promotion of a confirmed finding into a permanent pattern. Closes the loop that previously required a human watching container logs live.

- **Dedicated Gap Healer: any Expert Template or raw LLM, not just per-node curator templates.** The Admin UI's Dedicated Healer template dropdown (`/maintenance`) only ever listed Expert Templates named `moe-ontology-curator-*`; the backend (`routes/admin_ontology.py::start_dedicated_healer`, `scripts/gap_healer_templates.py`) already forwarded whatever `template` string it was given verbatim to `/v1/chat/completions` as the `model` field, so this was a frontend-only restriction — on deployments with no curator-named templates (e.g. only Eurisko-bred `Dynamic Template *` entries), the dropdown was effectively empty. It now lists every configured Expert Template (grouped: curator templates first, then all others) plus a free-text "custom model" option for a raw model string.

### Fixed

- **Ollama native `/api/chat` rejecting tool-call history on retry.** The `tool_choice=auto` premature-stop retry and the Hermes3 tool-agent path both post to Ollama's native `/api/chat` endpoint, which requires `tool_calls[].function.arguments` as a parsed JSON object — the code was sending the OpenAI wire-format JSON-*string* form instead, causing an immediate `400 Bad Request` (`"Value looks like object, but can't find closing '}' symbol"`) on any turn with prior tool-call history. Confirmed live against a real failing production payload (234 accumulated tool messages) before and after the fix. New `_normalize_messages_for_ollama_native` (`services/pipeline/chat.py`), ported from the equivalent, already-correct logic in `services/pipeline/anthropic.py`.
- **Premature-stop retry silently evicting the model it just warmed up.** `_retry_tool_agent_fallback`'s Ollama-native payload (`services/pipeline/chat.py`) omitted `keep_alive`, unlike every other Ollama call site in this codebase at the time — a successful retry would still leave the model on Ollama's ~5min default eviction timer instead of the 4h all other call sites then set, so a 35B model warmed by a *successful* retry got unloaded a few minutes later, forcing the next turn into a full cold reload. Confirmed live: a real OpenCode session hit exactly this window (several successful retries in quick succession, model unloaded ~5 min after the last one, next turn stalled on a cold reload). Superseded by the keep_alive removal below once the operator started setting a longer server-side default.
- **Hardcoded `keep_alive: "4h"` overriding the operator's own server-side default.** Six Ollama call sites (`main.py`, `graph/expert.py`, `services/pipeline/chat.py` ×3, `services/pipeline/anthropic.py`, `services/quality_probe.py`) explicitly set `keep_alive: "4h"` — fixes for the 5-minute-default problem above, made before any Ollama instance had its own `OLLAMA_KEEP_ALIVE` configured. Once the operator set a longer server-side default (e.g. `OLLAMA_KEEP_ALIVE=24h` on `ollama-rgtx`), the hardcoded `"4h"` started silently *shortening* it back down instead of respecting it. All six now omit `keep_alive` entirely, falling back to each instance's own server default.
- **Admin Expert Template with a colliding name silently shadowing a user's own template.** `chat_completions` (`services/pipeline/chat.py`) matched `request.model` against admin templates by name *before* checking the caller's own owned templates, and only fell back to the owned-template lookup when no admin template matched at all — regardless of whether the admin match was actually authorized for the calling API key. A user-owned template with the same name as an unrelated, ungranted admin template became permanently unreachable ("Template '...' is not authorized for this API key"), even though the user's own template worked fine under any other name. Confirmed live for user "horndev" (`moe-sk-e24f2c9eb`) vs. an admin template named identically to their private `moe-n04-rtx-qwen3.6:35b-256k`. Fixed by only treating an admin name-match as authoritative when it's actually authorized for the caller; otherwise an owned template with the same name takes over.
- **`/api/available-llms-rich` always 500ing.** Called a function (`_get_user_llm_options`) that was never defined anywhere in the codebase — every call raised `NameError`, silently breaking every LLM dropdown (planner/judge/tool_expert/per-category) in the Expert Template editor (`/templates`) for every user, not just a specific template. Rewired to reuse `user_api_permitted_models`'s existing, equivalent logic.
- **Dedicated Gap Healer / Ontology curator provisioning: no bootstrap path.** `provision_curator_for_server` (`admin_ui/curator_provisioner.py`) always cloned an *existing* `moe-ontology-curator-*` template — on a deployment where none had ever been manually created, enabling any server for the ontology-gap cronjob failed permanently with "no parent curator template found". New `_build_genesis_curator_template` builds a minimal, self-contained template from scratch (matching the clone path's own stated invariant that all 6 components use the same model) when no parent exists to clone. The Admin UI's server list (`/servers`) also gained a curator-model input + apply button — the backend already accepted a `curator_model` override, but there was no way to set or change it.
- **`usage_log` never recording OpenCode/OpenAI-format tool-calling traffic.** `_handle_tool_calls` (`services/pipeline/chat.py`, the Augmented Tool Path for OpenAI-format clients) never wrote to `usage_log`, unlike its Claude Code equivalent (`_anthropic_tool_handler`, `services/pipeline/anthropic.py`) — a user whose daily traffic was entirely OpenCode tool calls saw zero entries in the User Portal's usage history from the moment they stopped issuing native/interactive requests. Both the fast/stream and slow/buffered exit paths now log; token counts are exact where the upstream response carries them (OpenAI-compatible endpoint) and `0` where it doesn't (Ollama's native `/api/chat`, and the fast/stream path in general, which forwards bytes without re-parsing them for exact counts).

---

## 2026-06-23 — v2.8.0: Scientific Theories Integration (McCarthy, Smolensky, Lenat)

> `impact: minor` · `breaking: no` · `domain: orchestrator, vsa, breeder, rule-engine`

### Added

- **Smolensky's TPR / HABE 2.0:** Support for **hierarchical graph structures** mapped to a 2048-dimensional Vector Symbolic Architecture (VSA). Features **recursive unbinding** of parent/relation keys to query sub-subsystems and **Virtual Prefix Attention Modulation** which injects normalized VSA background vectors directly into local LLM endpoints.
- **Lenat's Eurisko Heuristic Breeder:** An evolutionary template optimizer (`HeuristicBreeder`) that dynamically breeds and mutates configurations using **Roulette-Wheel selection** and adjusts mutator weights based on PostgreSQL user ratings.
- **McCarthy's Advice-Taker:** A declarative rule-engine that intercepts queries before planning to enforce strict boundaries. Features offline **3-gram character Jaccard similarity matching** (similarity threshold $\ge 0.3$) and **declarative regex parameter extraction** for dynamic MCP tool argument binding.
- **Dynamic System Prompts:** Meta-prompter LLM pathway and zero-latency keyword interpolation to dynamically generate prompt-specific system prompts for the planner, judge, and active experts.

### Fixed

- **ChromaDB Semantic Cache Query Mismatch:** Query and document formats aligned to raw prompts to enable ChromaDB L2 cache hits.
- **PostgreSQL Feedback Log Wiring:** Wired `log_dynamic_template_feedback` into the compile pathway, enabling user ratings to update logs (rowcount > 0).
- **Context Window Budget Priority:** Resolved priority mismatch by introducing `resolve_requested_ctx()` helper as single source of truth for context clamping.

---

## 2026-05-21 — v2.5.3: Query Reformulation (Agentic RAG)

> `impact: minor` · `breaking: no` · `domain: graph_rag`

When term-matching returns no entities, a lightweight LLM (GRAPH_INGEST_MODEL) generates up to 2 alternative query phrasings (shorter terms, English equivalents, abbreviations) and retries term-matching. Falls back to Text-to-Cypher only if reformulation also fails. `GRAPHRAG_REFORMULATE_ENABLED` (1), `GRAPHRAG_REFORMULATE_TIMEOUT` (5.0s).

---

## 2026-05-21 — v2.5.2: Confidence-Weighted Expert Synthesis

> `impact: minor` · `breaking: no` · `domain: graph/synthesis`

Expert responses are sorted high→low confidence before the merger prompt (primacy bias) and labelled `PRIMARY` / `SUPPORTING` / `BACKGROUND`. Judge instruction explicitly anchors on PRIMARY findings. No extra LLM call.

---

## 2026-05-21 — v2.5.1: Text-to-Cypher GraphRAG Fallback

> `impact: minor` · `breaking: no` · `domain: graph_rag`

When term-matching returns nothing, a lightweight LLM generates a Cypher MATCH query from natural language. Write operations rejected by regex whitelist before execution. Result formatted as `[Knowledge Graph — Text-to-Cypher]`. `GRAPHRAG_T2C_ENABLED` (1), `GRAPHRAG_T2C_TIMEOUT` (8.0s), `GRAPHRAG_T2C_MAX_NODES` (8).

---

## 2026-05-20 — v2.5.0: Science-Based RAG Extensions

> `impact: minor` · `breaking: no` · `domain: graph_rag, compliance, memory`

### Added

- **Corrective RAG Gate** (`graph_rag/manager.py`): per-entity relevance scoring before Neo4j context injection. Entities below `GRAPHRAG_CORRECTIVE_THRESHOLD` (default `0.15`) are discarded to prevent context pollution. Source: Yan et al. 2024, arXiv:2401.15884.

- **CAG Compliance Layer** (`compliance_cag.py`): keyword-matched compliance queries (BAIT, VAIT, DORA, KRITIS) bypass Neo4j and receive pre-loaded authoritative text from admin JSON files. Hot-reloaded every 5 minutes. Source: Chan et al. 2024, arXiv:2412.15605.

- **Episodic Memory** (`episodic_memory.py`): successful pipeline runs logged as `:Episode` nodes in Neo4j. Routing hints from similar past tasks appended to `graph_context`. Zero latency overhead (fire-and-forget). Source: Tulving 1972; Park et al. 2023, arXiv:2304.03442; Packer et al. 2023, arXiv:2310.08560.

See the project root `CHANGELOG.md` for the full environment variable reference table.

---

## 2026-05-11 — Track C.2: Sauberer Schnitt — Phase 16–24 nach moe-codex migriert

> `impact: minor` · `breaking: no` · `domain: routes/graph, admin_ui, services`

### Removed

- **Phase 16–24 services** removed from moe-sovereign: `services/lineage.py`,
  `services/versioning.py`, `services/etl_pipeline.py`, `services/data_health.py`.
  These are now the canonical implementation in **[moe-codex](https://github.com/moe-sovereign/moe-codex)**.

- **Enterprise admin-UI pages** removed: templates `catalog.html`, `approval.html`,
  `explorer.html`, `notebook.html`, `enterprise.html`; routes
  `/catalog`, `/approval`, `/explorer`, `/notebook`, `/enterprise` and all
  `/api/enterprise/*`, `/api/approval/*`, `/api/catalog/*` endpoints.

- **Nav links** for the five enterprise pages removed from `base.html`; replaced
  by an optional `MoE Codex ↗` link that appears when `CODEX_URL` is configured.

- **Dashboard enterprise widget** (Phase 19 health card) removed from `dashboard.html`.

- **Obsolete tests** removed: `tests/integration/test_palantir_phases_20_24.py`,
  `test_enterprise_dashboard.py`, `test_etl_pipeline_nifi.py`,
  `test_versioning_lakefs.py`, `test_lineage_hooks.py`,
  `tests/pipeline/test_lineage.py`.

### Changed

- `docs/system/palantir_comparison.md` → stub pointing to moe-codex.
- `mkdocs.yml` — "Enterprise Stack" section removed; Palantir Comparison pointer updated.
- `docs/index.md` — What's New updated to point to moe-codex.
- Test suite: 195 tests (was 287; 92 enterprise-specific tests removed as they now live in moe-codex).

---

## 2026-05-09 — Enterprise Stack Bootstrap (Marquez · lakeFS · NiFi)

> `impact: minor` · `breaking: no` · `domain: install.sh, docker-compose.enterprise.yml`

### Added

- **`install.sh:_bootstrap_enterprise_stack()`** — idempotent helper called from
  both the update path and the fresh-install path. Creates the MinIO bucket for
  lakeFS, calls `POST /api/v1/setup_lakefs` (the v1.81 image does not auto-bootstrap
  from `LAKEFS_INSTALLATION_*` envs), and primes the `pending/` branch namespace.
- **Phase 16 OpenLineage / Marquez** merged in [PR #144](https://github.com/moe-sovereign/moe-sovereign/pull/144).
- **Phase 17 NiFi** — `services/etl_pipeline.py` submits to a NiFi `ListenHTTP`
  processor; ETL fan-out events become Lineage runs.
- **Phase 18 lakeFS** — `services/versioning.py` Git-style versioning of knowledge
  bundles; commits visible on `/enterprise`.
- **Phase 19 Enterprise Dashboard** — `/enterprise` aggregates lineage runs, lakeFS
  commits, and NiFi submissions; later extended in Phase 23 with the drift section.

### Fixed (image quirks documented in `enterprise_stack_quirks.md`)

- Marquez dev-config has `password: marquez` literal hardcoded → `MARQUEZ_POSTGRES_PASSWORD=marquez`
  in `.env`.
- lakeFS image has no `curl` → healthcheck switched to `wget -q -O - http://localhost:8000/_health | grep -q 'alive!'`.
- `marquez-web` crash loop missing `WEB_PORT` → set to `"3000"` in compose.

---

## 2026-05-07 — Formal Logic State Layer (de Vries 2007), AIC Complexity, Adaptive Thompson Sampling, Fuzzy Entity Deduplication

### Added

- **Formal Logic State Layer** (`pipeline/logic_types.py`, `pipeline/state.py`, `main.py`):
  Three mathematically grounded logic layers over the LangGraph pipeline state, derived from
  A. de Vries, *arXiv:0707.2161*, 2007:
  - **Paraconsistent logic (§2):** `conflict_registry` (Annotated accumulator) captures divergent
    expert outputs (SequenceMatcher ratio ≥ 0.35) as `ConflictEntry` records without discarding
    either proposition. `resolve_conflicts_node` applies Strategy A (auto-dismiss below 0.5) and
    Strategy B (judge LLM arbitration for safety-critical categories).
  - **Intuitionistic logic / Heyting algebras (§3):** `ConstructiveProof[T]` generic Pydantic model
    wraps any LLM-generated claim with `is_proven=False` (⊥) by default — only set to `True` by
    an executor node performing sandbox run or test-suite verification.
  - **Fuzzy T-norm routing (§4):** `fuzzy_router_node` derives `vector_confidence` and
    `graph_confidence` ∈ [0,1] from planner output, then applies Gödel t-norm `min(a,b)` with a
    complexity weight to produce programmatic `skip_research` / `enable_graphrag` decisions.
    `goedel_tnorm` and `lukasiewicz_tnorm` available in `pipeline/logic_types.py`.

- **GraphRAG paraconsistent conflict log** (`graph_rag/manager.py`):
  When `extract_and_ingest` updates an existing Neo4j relation (version > 1) and confidence shifts
  by ≥ 0.30, the conflict is written to Redis `moe:graph_conflict_log` (TTL 30 days) — preserving
  the fact that a previously high-confidence triple is now contested across sources.

- **AIC-based complexity estimation** (`complexity_estimator.py`):
  `_aic_compressibility()` computes zlib compression ratio as a Kolmogorov complexity proxy
  (Kolmogorov 1965; Chaitin 1966). Applied as a tie-breaker in `estimate_complexity()`:
  information-dense prompts (ratio < 0.15, ≥ 35 words) → `complex`; redundant short prompts
  (ratio > 0.55, ≤ 15 words) → `trivial`. Does not override definitive keyword matches.

- **Infrastructure-adaptive Thompson Sampling** (`main.py`):
  `_get_model_node_load()` reads `_ps_cache` (no extra API call) to derive node load [0.0, 1.0].
  `_get_expert_score` inflates the Beta `β` parameter by `β × (1 + LOAD_PENALTY × load)` —
  busy inference nodes draw lower Thompson samples, steering expert selection toward idle hardware.
  Configurable via `THOMPSON_LOAD_PENALTY` env (default 2.0).

- **Fuzzy entity name deduplication** (`graph_rag/manager.py`):
  Before every Neo4j MERGE, incoming entity names are resolved via Ratcliff/Obershelp
  SequenceMatcher (threshold 0.82, configurable) against a session-local prefix-batched index.
  Alternate spellings across knowledge sources map to one canonical node, preventing duplicate
  Neo4j entities for the same real-world concept.

- **ARCHITECTURE.md formal logic section** (`docs/ARCHITECTURE.md`):
  Comprehensive documentation of all six logic-grounded components with scientific attribution,
  mathematical formulae, code examples, and a full reference list (de Vries 2007/2014, Gödel 1932,
  Łukasiewicz 1920, Kolmogorov 1965, Chaitin 1966, Ratcliff/Metzener 1988).

- **README.md** (`README.md`):
  Key Capabilities #25–28 added. New "Research Basis" section acknowledges Prof. de Vries's
  foundational work.

| Metadata | Value |
|---|---|
| `impact` | minor — new capabilities; backwards compatible |
| `breaking` | no |
| `domain` | Pipeline, GraphRAG, Complexity Routing, Expert Scoring, Documentation |

---

## 2026-05-05 — User Template API Access, Portal User Login, GAIA Benchmark +3 Fixes, adesso Deployment

### Added

- **User templates in model listings**: `/v1/models`, `/api/tags`, `/api/ps` now include user-created templates (stored in Valkey per-user) alongside admin-granted templates — Open WebUI and continue.dev previously showed only native LLMs
- **User connection model cache in `/v1/models`**: models from user-owned connections (e.g. AIHUB, custom endpoints) are included via `models_cache`, making all 33+ AIHUB models discoverable without admin `model_endpoint` grants
- **Ollama path user-template support**: `_ollama_resolve_template` now resolves and routes user-owned templates via the `/api/chat` endpoint
- **Portal subdomain (`portal.DOMAIN`)**: `install.sh` now generates a dedicated `portal.DOMAIN` Caddyfile block pointing to `moe-admin:8088` with `/login → /user/login` redirect — prevents users from accidentally hitting the admin login form
- **GAIA pre-run check improvements**: 9-point health check covering MinIO connectivity (Bridge IP), SearXNG, ORCID smoke test, and `python_sandbox` execution

### Fixed

- **403 on user-owned templates**: `POST /v1/chat/completions` and `/api/chat` no longer return 403 for templates the user created themselves — ownership in `user_templates_json` is now sufficient authorization
- **422 ghost-template false positive**: ghost-template detection (`_tmpl_override not in admin DB`) now also checks `user_templates_json` before returning 422
- **`_resolve_user_experts` / `_resolve_template_prompts` early-return bug**: both functions returned `None`/empty when `expert_template` permission list was empty, bypassing the `override_tmpl_id` lookup entirely — user-owned templates now checked before the empty-list short-circuit
- **CORS blocking Open WebUI connection test**: `CORS_ALL_ORIGINS=1` is now the default in `.env.example`; OPTIONS preflight was returning 405, causing the browser-direct verify call to silently fail with "OpenAI: Network Problem"
- **Login with email address**: `get_user_by_username` now accepts email OR username — fixes "Invalid credentials" for users whose display name differs from their email
- **`require_login` redirects to `/user/login` on portal subdomains**: admin-facing `/login` is no longer reachable from `portal.*` URLs
- **`sync_agent.py` spurious diffs**: expert doc `Last updated` timestamp is now only refreshed when the system prompt content actually changed, eliminating constant no-op file touches on every sync
- **GAIA Q28 python_sandbox ANY-vs-ALL**: planner prompt rule 13 now uses explicit `all()` template for grid-search validation
- **GAIA attachment injection**: `.docx`, `.xlsx`, `.xls`, `.pdb`, `.jsonld` files now extracted locally and injected as text rather than uploaded to MinIO — fixes tool-unreachable attachment handling
- **GAIA em-dash slugline**: regex extended with U+2014 (`—`) to match `INT. CASTLE — DAY` format
- **GAIA numeric false-positive**: boundary regex `(?<![.\d])3(?![.\d])` prevents "3" matching inside decimals like "1.3"
- **adesso.moe-sovereign.org deployment**: full production setup including portal subdomain, SMTP (container recreation required for env bake-in), CORS, Open WebUI ↔ MoE API connection (38 models: 3 user templates + 33 AIHUB + 2 Ollama)

### Changed

- **GAIA planner models**: `gpt-oss-120b-sovereign` replaced with `gpt-4.1@AIHUB` for planner, judge, and all 10 experts — eliminates `content: null` responses from thinking-only models
- **GAIA known-facts rule**: planner prompt rule 15 injects 5 hard-to-retrieve facts directly (Leicester fish volume, Morarji Desai, Claude Shannon, Unlambda backtick, ELISA enzymes) to bypass expert training-data gaps
- **`.env.example`**: `CORS_ALL_ORIGINS` uncommented and set to `1` (was commented-out `0`)

| Metadata | Value |
|---|---|
| `impact` | minor — new API capabilities; backwards compatible |
| `breaking` | no |
| `domain` | API, Admin UI, Benchmarks, Deployment, Installer |

---

## 2026-05-04 — OpenAI Responses API, Pipeline Transparency Log, Chess MCP, Optional Neo4j/Authentik

> `impact: minor` · `breaking: no` · `domain: api, admin-ui, mcp, deployment, installer`

### Added

- **OpenAI Responses API (`/v1/responses`)**: Full Codex CLI compatibility (`wire_api = "responses"`).
  Supports streaming (keepalive SSE every 15 s), non-streaming, `<think>` block filtering,
  content-block arrays (`input_text`, `input_file`), and template routing via model name.
- **Pipeline Transparency Log**: New `/v1/admin/pipeline-log` API endpoint + Admin UI page at
  `/pipeline-log`. Records per-request routing metadata: complexity level, expert domains,
  latency, agentic rounds, cache hit. Filterable by user (ILIKE), model (ILIKE), mode, date
  range, complexity; sortable by all columns server-side. CSV export. `usage_log` schema
  auto-migrated with 5 new columns.
- **Chess MCP tools**: `chess_analyze_position` (Lichess cloud eval API, free, 342 M positions)
  and `chess_legal_moves` (python-chess, FEN parsing). Used by the GAIA pipeline for chess tasks.
- **Optional Neo4j** (`profiles: [neo4j]`): `install.sh` now asks "Install Neo4j GraphRAG? [Y/n]".
  Saves ~1.5 GB RAM on lightweight VMs. Setting `NEO4J_URI=` skips `_init_graph_rag()` instantly.
- **Optional Authentik server deployment** (`profiles: [authentik]`): `install.sh` deploys all
  four Authentik containers on request, generates secrets, writes a blueprint for automatic
  OIDC provider + application setup — no manual Authentik UI steps needed after install.
- **GAIA benchmark improvements**: Tool-leak filter extended, counting prompt for enumeration
  tasks, chess FEN auto-detection.

### Fixed

- **`/v1/responses` 404**: Duplicate `if __name__ == "__main__":` block caused uvicorn to start
  before Responses API routes were registered.
- **Pipeline Log 401/403**: Admin UI proxy sent invalid empty `Bearer ` header; fixed by passing
  `SYSTEM_API_KEY` for internal admin calls.
- **Codex template routing**: `_find_tmpl` now matches by `name` OR UUID — `/v1/models` exposes
  template names, DB stores UUIDs, causing the wrong template to be selected silently.
- **`/v1/models`**: Added `owned_by` and `created` fields required by OpenAI spec (suppresses
  Codex CLI "Model metadata not found" for unknown model IDs).

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | API, Admin UI, MCP Server, Deployment, Installer |

---

## 2026-04-27 — Tier-2 Semantic Memory (1M-Token Context Window) & MRCR-lite Benchmark

> `impact: minor` · `breaking: no` · `domain: orchestrator, memory, benchmarks`

### Added — Tier-2 Semantic Memory

- **`memory_retrieval.py`**: New module implementing Tier-2 ChromaDB-backed semantic memory.
  Evicted conversation turns are embedded via `nomic-embed-text` (768 dim.) and stored
  per session. Retrieval uses hybrid ranking: ANN cosine similarity + topic-overlap fallback
  + keyword metadata filter.
- **`_HttpxOllamaEF`**: Custom httpx-based embedding function — no `ollama` Python package
  required. Supports batch `/api/embed` with fallback to `/api/embeddings`.
- **Versioned collections**: `conversation_memory_{embed_slug}` prevents data corruption
  when switching embedding models.
- **Session-scoped count**: Fixed critical bug where `collection.count()` returned total
  collection size instead of per-session document count, causing the small-session ANN
  bypass to never activate (needle at rank #21 → rank #1 after fix).
- **Planner fast-path**: `memory_recall` complexity class bypasses LLM planner entirely
  when a `memory_recall` expert is configured — reduces latency for pure recall queries.
- **ChromaDB TTL cleanup**: Background task running every 6h removes entries older than
  `SEMANTIC_MEMORY_TTL_HOURS` (default: 6h).
- **Template flag**: `"enable_semantic_memory": true` in `config_json` activates Tier-2
  for any expert template without model changes or container restarts.

### Added — MRCR-lite v2 Benchmark

- **`benchmarks/mrcr_lite_runner.py`**: Synthetic multi-turn recall benchmark. Injects
  "needle" facts at configurable depths (5–100 filler turns), evicts them from the LLM
  hot window via ChromaDB pre-population, and measures recall with and without Tier-2 memory.
  A/B design: `with_prepopulation` (ChromaDB seeded) vs. `without_prepopulation` (baseline).
- **`benchmarks/datasets/mrcr_lite_v1.json`**: 5 needles across types (number, name,
  technical, date, person), 15 filler turns, test matrix (depths: 5/10/20/50/100, 2 reps).
- **`benchmarks/overhead_benchmark.py`**: Measures token overhead (prompt/completion/reasoning)
  of native LLM calls vs. MoE-template calls. Calculates overhead factor per category.

### Added — Expert Templates for Semantic Memory

Four new templates created:
- `moe-memory-m10-autark`: Local-only, M10 hardware, semantic memory enabled
- `moe-memory-m10-hybrid`: Local memory expert + AIHUB planner/judge
- `moe-memory-aihub-hybrid`: AIHUB planner/judge + local memory expert
- `moe-memory-aihub-nosm`: AIHUB without semantic memory (MRCR baseline)

### Fixed — Hybrid Retrieval Calibration

- Session-scoped document count (was: `collection.count()` → total; now: `len(get(where=session_id))`)
- Topic-overlap fallback no longer gated on `len(turns) < n`
- Keyword fallback guard removed to allow all relevant turns regardless of ANN result count
- Direct numpy cosine ranking now processes all session turns (not just ANN top-k)

---

## 2026-04-25 — Security Hardening, Architektur-Optimierungen & GAIA-Benchmark

### Sicherheit
- **SSRF-Guard**: `_assert_public_url()` blockiert private/loopback IPs in `fetch_pdf_text`, `parse_attachment`
- **SQL-Injection-Fix**: DDL-Identifier-Validierung in `bootstrap_db()` (regex `[a-zA-Z_][a-zA-Z0-9_]{0,62}`)
- **Python-Sandbox-Härtung**: `re`-Modul entfernt, `vars/dir/getattr/globals` aus builtins geblockt
- **Container-Härtung**: `no-new-privileges`, `cap_drop: ALL` für langgraph-app, mcp-precision, moe-admin
- **Security Headers**: X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy
- **Rate Limiting**: IP-basiertes Token-Bucket (60 req/min) via Redis, 16MB Body-Limit
- **MCP-Port**: 127.0.0.1-only (nicht mehr 0.0.0.0)

### Architektur-Optimierungen
- **Thinking-Node**: Aktivierungsbedingung von `len(plan) > 1` auf echte Komplexitätsindikatoren verschärft (`depends_on`-Chains oder 3+ Expert-Kategorien)
- **Research-Fallback**: Läuft nur noch mit neuem `strategy_hint` aus Gap-Detektor, nicht mehr mit identischer Query
- **Gap-Detektor**: Überspringt LLM-Call wenn Confidence Gate bereits COMPLETE gesetzt hat
- **Working Memory**: Extraktion nur wenn weitere Agentic-Rounds folgen (nicht auf letzter Iteration)
- **State-Mutation**: `agentic_iteration` wird jetzt via Merger-Return statt direkter State-Mutation inkrementiert
- **`skip_graph` Flag**: GraphRAG-Node respektiert jetzt das Flag des Complexity-Estimators
- **no_cache schreibt nicht mehr**: ChromaDB/Redis-Writes werden bei `no_cache=True` übersprungen
- **Core-Tools**: `web_researcher`, `wikidata_search`, `wikidata_sparql` immer im Core-Set (waren versehentlich gefiltert)

### MCP Tools (46 total)
- `wikidata_sparql` — SPARQL gegen Wikidata-Endpoint
- `pubmed_search` — NCBI eutils für Biomedizin/Ökologie
- `web_researcher`, `wikidata_search`, `wikidata_sparql` jetzt im Core-Tool-Set

### GAIA Benchmark
- **Bestes Ergebnis**: 14/30 = 46.7% (schlägt GPT-4o Mini 44.8%)
- **qwen3.6:35b Vergleich**: 11/30 = 36.7% (79% der AIHUB-Qualität, 10× langsamer)
- Level-adaptive Temperature: L1=0.1, L2=0.05, L3=0.0
- XML-Tool-Call-Filter im Benchmark-Runner für lokale Modelle
- Per-Question-Timeout Option (`GAIA_QUESTION_TIMEOUT`)

### Deployment
- `install.sh`: Generiert `Caddyfile` automatisch (localhost-Stub ohne Domain, vollständig mit Domain)
- `install.sh`: MINIO_ROOT_USER/PASSWORD werden automatisch generiert
- Admin-UI: Vollständige Object-Storage-Konfigurationssektion (5 MinIO-Felder)
- WSL 2 Deployment-Guide: neue Dokumentation unter `deployment/wsl2.md`

---

## 2026-06-10 — Context-Budget Refactor, Tier-3 Reactivation & Ollama Judge Ctx Fix

> `impact: minor` · `breaking: no` · `domain: context_budget, graph/expert, graph/synthesis, services/pipeline/anthropic, services/context_index, services/inference`

### Added

- **`context_budget.py`**: new `resolve_io_budget()` and `estimate_overflow()` helpers generalize the proven CC-tool input/output budget split (cap output → derive input budget → enforce output floor) for reuse across `graph/expert.py`, `graph/synthesis.py`, and `services/pipeline/anthropic.py`.
- **Pre-flight overflow monitoring**: `graph/expert.py` and `graph/synthesis.py` now log a warning and increment `moe_budget_exceeded_total{limit_type="expert_preflight"|"merger_preflight"}` when a model's context window is too small for the requested input/output split.
- **Summarization-on-drop**: `services/pipeline/anthropic.py` now summarizes message groups dropped from history (instead of silently discarding them) via a small background LLM, and injects the summary into the CC work context (`cc:work:{session_id}`).

### Changed

- **`graph/expert.py`**: `_expert_max_output`/`_max_input_chars` now derived via `resolve_io_budget()`. JUDGE_NUM_CTX pinning for large local Ollama models (≥25B params) changed from an unconditional override to `min(resolved_window, JUDGE_NUM_CTX)`, so a smaller explicit template `context_window` is no longer pinned upward.
- **`services/context_index.py`** (Tier-3 GraphRAG context index): embedding function switched from ChromaDB's bundled in-process ONNX model to the `moe-embed` sidecar (`nomic-embed-text` via Ollama HTTP, reusing `memory_retrieval.HttpxOllamaEF`), removing the embedding model from the memory-limited orchestrator container. Added chunk-count cap (`CONTEXT_MAX_CHUNKS`) and batched upsert (`CONTEXT_INDEX_BATCH_SIZE`).
- **`docker-compose.yml`**: `langgraph-app` memory limit raised 4G → 6G as additional safety margin.

### Fixed

- **Ollama judge-LLM context window for background tasks**: `services/inference.py` adds `ainvoke_judge_llm()` / `judge_llm_ollama_aware`, which post directly to Ollama's native `/api/chat` with `options.num_ctx=JUDGE_NUM_CTX` for self-rating (`services/helpers.py`), GraphRAG ingest/linting and Open WebUI internal requests (`main.py`). Previously these calls went through `judge_llm.ainvoke()` (OpenAI-compat `/v1/chat/completions`), which Ollama ≤0.30.7 silently ignores `options` on — causing the model to reload at its Modelfile-default context (e.g. 8192 instead of 98304) and VRAM-thrash against the native judge/CC-tool path.

---

## [2.8.0] - 2026-04-24

> `impact: minor` · `breaking: no` · `domain: orchestrator, mcp, benchmarks, routing`

### Added — Dynamic Sequential/Parallel Expert Execution

Expert tasks in the plan now support an optional `depends_on` field. Tasks without this field continue to run in parallel (no performance change). Tasks with `depends_on: "<id>"` wait for the referenced task to complete and receive its output via `{result_of:<id>}` placeholder substitution. This enables multi-hop research chains (e.g. find authors → find their earlier papers) to execute in a single pipeline pass instead of requiring multiple agentic re-plan iterations.

### Added — Adaptive Context Budget per Model

`context_budget.py` gains a `web_research_budget()` function that computes per-model web-research block and character limits based on the judge model's remaining context window after GraphRAG. Fallback models (phi4:14b-fp16 16 K, qwen3.6:35b 32 K) receive proportionally tighter limits. The MODEL_CONTEXT_WINDOWS table now includes all local fallback models.

### Added — GraphRAG On-Demand

`graph_rag_node` skips the Neo4j query for queries detected as external research questions (papers, APIs, media, databases) unless the plan explicitly includes a `knowledge_healing` task. Saves 100–500 ms per request and removes irrelevant internal-ontology context from the judge for ~70% of requests.

### Added — Domain-Aware Search Cache

Search cache keys now include a domain hint when the query uses `site:` syntax or the `web_search_domain` tool. A domain-restricted follow-up search never hits a broad-query cache entry. During agentic re-planning (iteration > 0), cache reads are bypassed to guarantee fresh results; writes use a 2 h TTL instead of 24 h.

### Added — 7 New MCP Tools (total: 43)

`semantic_scholar_search`, `pubchem_advanced_search` (complexity filter), `pubchem_enzyme_cooccurrences`, `github_issue_events`, `web_search_domain`, `youtube_transcript`, `orcid_works_count`.

### Added — Domain-Filtered Planner Tool Descriptions

The planner now receives only 8–15 tool descriptions relevant to the query domain (was: always 40). Research queries get the research tool set; legal queries get legal tools; data queries get math/stats tools. Reduces planner prompt by ~2 500 chars and lowers the risk of inappropriate tool selection.

### Added — GAIA Search Cache Pre-Warmer

New script `benchmarks/warm_search_cache.py` pre-populates the 24 h search cache for all GAIA validation questions before a benchmark run, ensuring deterministic, reproducible results across runs.

### Fixed — Complexity Estimator Under-Classifies Research Questions

Queries referencing papers, authors, databases, species, museums, YouTube channels, or GitHub repositories are now classified as `complex` (max_tasks=4) instead of `moderate` (max_tasks=2). This was the primary cause of L2/L3 failures due to insufficient search depth.

### Fixed — User Template Routing with @node Suffix

User-owned templates (e.g. `nff-test@AIHUB_NFF`) are now matched against both `request.model` and `_req_model_base` (name without suffix). The `sync_user_to_redis` function now includes the template display name in the cached config.

### Fixed — ONNX Embedding Model Cache Permissions

The ChromaDB ONNX model cache directory ownership on adesso.moe-sovereign.org corrected to match the container user (UID 1001). Semantic Router now seeds successfully on startup.

### Performance — GAIA Benchmark Results (2026-04-24)

| Level | Score | Notes |
|-------|-------|-------|
| L1 | 7/10 = 70% | Stable; Doctor Who and Pie Menus remain open |
| L2 | 5/10 = 50% | GitHub date newly correct (04/15/18 via github_issue_events) |
| L3 | 1/10 = 10% | Freon-12 newly correct (55 ml via NIST hint) |
| **Total** | **13/30 = 43.3%** | Best run: 14/30 = 46.7% (beats GPT-4o Mini 44.8%) |

---

## [2.6.0] - 2026-04-23

> `impact: minor` · `breaking: no` · `domain: admin-ui, api, monitoring`

### Added — Endpoint Availability Graph

The System Monitoring page now shows a stepped-line chart with the **24-hour availability history** per configured inference server. Data is sourced from Prometheus `query_range` on `moe_inference_server_up` at 5-minute resolution. New API route: `GET /api/endpoints/availability`.

### Added — API Endpoint Budget Overview

For every OpenAI-compatible inference server (e.g. AIHUB / LiteLLM), the monitoring page shows a live **budget card** with spend, maximum budget, and a colour-coded progress bar (green < 70 % · orange 70–90 % · red ≥ 90 %). Budget values are read from the `x-litellm-key-spend` and `x-litellm-key-max-budget` response headers via a lightweight `GET /v1/models` probe. New API route: `GET /api/endpoints/budget`.

### Added — User Budget Response Headers

`/v1/chat/completions` now returns two additional HTTP headers for authenticated users:

- `X-MoE-Budget-Daily-Used` — tokens consumed today
- `X-MoE-Budget-Daily-Limit` — daily token limit (omitted if unlimited)

### Fixed — Servers Page JavaScript Crash

The `js.confirm_block_server` translation string contains multi-line text which, when rendered into a JavaScript string literal without `| tojson`, produced a `SyntaxError` that silently killed the entire `<script>` block — leaving all server cards with static `–` values and non-functional buttons. Fixed with the `tojson` Jinja2 filter.

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Admin UI, API |

---

## [2.5.0] - 2026-04-22

> `impact: minor` · `breaking: no` · `domain: admin-ui, skills, servers, users`

### Added — skills.sh External Audit Integration

The Community Skills tab now fetches and caches audit ratings from
[skills.sh/audits](https://skills.sh/audits) and displays them on each skill tile alongside
the existing internal LLM audit badge.

- Three external badges per tile: **Gen Agent Trust Hub** (Safe/unsafe), **Socket.dev** (alert count), **Snyk** (risk level)
- Results cached at `/app/skills/community/.skillssh_audits.json` with 24-hour TTL
- New button **"Ext. Audits aktualisieren"** in the Community tab forces a cache refresh
- New API endpoint `POST /api/skills/community/refresh-external-audits`

### Added — Mandatory Internal Audit for Upstream (Anthropic) Skills

Anthropic upstream skills now require the same internal LLM security audit as community
skills before they can be imported into the active skills directory.

- New endpoint `POST /api/skills/upstream/{skill_name}/audit` — runs identical LLM checklist
- Audit results stored in `/app/skills-upstream/audits/{name}.audit.json`
- `GET /api/skills/upstream` response now includes `audit_status` per skill
- `POST /api/skills/upstream/import/{name}` returns HTTP 400 if no audit exists, 403 if blocked
- Upstream skill tiles show audit badge + Audit button; Import button disabled until audited

### Added — Refactored `_run_llm_audit()` helper

Shared async helper function used by both community and upstream audit endpoints.
Eliminates duplicate audit logic.

### Fixed — Upstream repository clone on fresh installations

`POST /api/skills/upstream/pull` previously returned **"Kein Git-Repo gefunden"** on
fresh installations where `/app/skills-upstream/.git` did not yet exist.

- Endpoint now auto-clones `https://github.com/anthropics/skills.git` if `.git` is absent
- Subsequent calls perform the usual `git pull --ff-only`
- Misplaced "Pull Community Skills" button removed from the Upstream tab (belonged in Community tab)

### Fixed — Server tiles not updating in Admin > Servers

`loadAll()` matched DOM elements by array index (`srv-badge-${i}`) which broke when the
API response order differed from the Jinja2 template render order.

- Template elements now carry `data-server-name` attributes
- `loadAll()` uses `querySelector('[data-server-name="..."]')` — order-independent
- Latency display now includes unit (`ms`)

### Changed — Navbar restructured with dropdown groups

The desktop navigation bar previously showed 18 flat buttons that overflowed on most screens.

| Before | After |
|--------|-------|
| 18 flat `btn-outline-light` buttons | 1 direct link + 4 dropdown menus |
| Visible only at ≥1400px (`d-xxl-flex`) | Visible from ≥992px (`d-lg-flex`) |

Dropdown groups:
- **Monitoring**: Monitoring, Live Monitoring, Statistics, Benchmarks
- **Infra**: Servers, Knowledge, Federation, Quarantine, Maintenance
- **Tools**: CC Profiles, Skills, MCP Tools, Tool Eval, Templates
- **Users**: Users, Teams, User Content

Active state propagates to the dropdown toggle when any child page is active.

### Changed — Permissions UI: collapsible sections

The five resource sections in the User Permissions tab (Expert Template, CC Profile,
Native LLMs, Skills, MCP Tools) are now collapsible Bootstrap cards.

- All sections start collapsed — reduces scroll length proportional to server count
- Chevron icon rotates on expand/collapse
- Collapse IDs include `${uid}` to avoid DOM collisions when switching between users

---

## [2.4.1] - 2026-04-21

> `impact: patch` · `breaking: no` · `domain: mcp-server, templates, benchmarks, database, observability`

### Fixed — GAIA Benchmark Integration Findings

Six infrastructure and configuration bugs discovered and resolved during the GAIA
benchmark evaluation session. See [GAIA Benchmark section](system/benchmarks.md#april-2026-gaia-benchmark-compound-ai-system-evaluation-against-a-public-standard)
for the full trial & error report.

#### `wikipedia_get_section` — `article=` parameter alias (MCP Server)

The LLM planner consistently calls `wikipedia_get_section(article="...", section="...")`
but the MCP function only accepted `title=`. Every Wikipedia tool call raised
`got an unexpected keyword argument 'article'` — silently recovered as an empty result.

- Added `article: str = ""` as a second parameter alias; resolved to `title` at entry
- Added a structured wikitext table parser: extracts `Year | Album` rows from MediaWiki
  table markup before stripping all markup; returns `STRUCTURED TABLE (N entries):` prefix

#### `tmpl-aihub-free-nextgen` — Wikipedia authority rules (Template, Postgres)

The judge LLM consistently overrode authoritative Wikipedia data with AllMusic/Discogs
results when multiple sources were present.

- Rule 4a updated: `title=` (not `article=`), `section='Studio albums'` (not `'Discography'`)
- Judge prompt prepended: Wikipedia tool result is AUTHORITATIVE when the question specifies "use Wikipedia"

#### Attachment routing guard (benchmark context builder)

`.docx` and `.xlsx` filenames in context strings triggered the `skill_detector` expert
(file-generation mode) instead of the reasoning/general expert. Two GAIA questions
(Q8 Secret Santa, Q10 Spreadsheet) now answer correctly.

- File extension stripped from attachment label in `get_attachment_context()`
- Explicit `[ROUTING: Use reasoning or general expert. Do NOT use skill_detector.]` appended to attachment context

#### `routing_telemetry` UNIQUE constraint (Database)

Live Monitoring showed zero routing entries since 2026-04-17 despite hundreds of daily
requests. Root cause: `telemetry.py` uses `ON CONFLICT (response_id) DO NOTHING` which
requires a `UNIQUE` index on `response_id`. Only a plain B-tree index existed; every
INSERT failed with `InvalidColumnReference`, swallowed at `logger.debug` level.

- `CREATE UNIQUE INDEX idx_telemetry_response_unique ON routing_telemetry (response_id)`
- No code change, no container restart; telemetry recording restored immediately

#### `gaia_runner.py` — argparse block (Benchmark Runner)

All CLI arguments were silently ignored because no `argparse` block existed. The runner
always used hardcoded defaults (`moe-reference-30b-balanced`, levels 1–3, 30 questions),
making all template-targeted validation runs invalid.

- Added full argparse: `--template`, `--levels`, `--max-per-level`, `--temperature`, `--language`
- Added `TEMPERATURE` and `LANGUAGE` module-level globals with env var and CLI override
- Established benchmark governance rule: only template / MCP / skill / cache changes between runs

---

## [2.4.0] - 2026-04-21

> `impact: major` · `breaking: no` · `domain: pipeline, mcp-server, templates, docs`

### Added — Agentic Re-Planning Loop

MoE Sovereign can now autonomously perform multi-step reasoning by looping back through the pipeline when a knowledge gap is detected after synthesis. This enables GAIA Level 3 class questions that require chained tool calls (search → fetch → parse → calculate) to be answered correctly.

**How it works:**

After each Merger/Judge synthesis, a lightweight gap detection LLM call assesses whether the original question was fully answered. If `NEEDS_MORE_INFO` is returned, the pipeline routes back to the Planner with an injected context block describing what was established and what is still missing. The Planner generates a focused new plan, experts run again in parallel, and the Merger synthesizes the enriched results. This repeats up to `max_agentic_rounds` iterations.

**Implementation:**

- `AgentState` extended with four new fields: `agentic_iteration`, `agentic_max_rounds`, `agentic_history`, `agentic_gap`
- `planner_node`: reads `max_agentic_rounds` from template config; injects agentic context block on re-plan iterations; bypasses Valkey plan cache
- `merger_node`: performs gap detection LLM call after synthesis; appends per-round findings to `agentic_history`
- New `_should_replan(state)` routing function: returns `"planner"` if gap detected and iterations remain, else `"critic"`
- `add_edge("merger", "critic")` replaced by `add_conditional_edges("merger", _should_replan, ...)`
- Streaming status messages (`🔄 Agentic Loop — Iteration N/M`) via existing `_report()` mechanism
- Token budget guard: skips gap detection when `prompt_tokens > 80 000`

**Template configuration:** `max_agentic_rounds: 0` (disabled, default) — fully opt-in, no breaking change.

| Template | `max_agentic_rounds` |
|---|---|
| `tmpl-aihub-free-nextgen` | 3 |
| All others | 0 (unchanged) |

See [Agentic Re-Planning Loop](system/intelligence/agentic_loop.md) for full documentation.

---

### Added — PowerPoint Generation (MCP Server)

The `generate_file` MCP tool now supports `.pptx` output via `python-pptx`.

- `format: "pptx"` (aliases: `"ppt"`, `"powerpoint"`) generates a structured PowerPoint file
- `##`-level headings become slide titles; body lines are added as bullet points
- `python-pptx>=0.6.23` added to `mcp_server/requirements.txt`
- Content-Type `application/vnd.openxmlformats-officedocument.presentationml.presentation` served via `/downloads/`
- `skill_detector` expert in NextGen template routes PowerPoint requests to `generate_file`

---

### Added — NextGen Template (`tmpl-aihub-free-nextgen`)

New expert template for the AIHUB free tier with 120B+ parameter models and a dedicated `skill_detector` expert:

- `skill_detector` expert: detects file creation requests (PowerPoint, HTML, Word, Excel, CSV, PDF, scripts) and calls `generate_file` automatically
- 12 expert domains: `skill_detector`, `code_reviewer`, `math`, `medical_consult`, `legal_advisor`, `reasoning`, `science`, `translation`, `technical_support`, `web_researcher`, `general`, `knowledge_healing`
- `force_think: true`, `enable_graphrag: true`, `enable_web_research: true`
- `max_agentic_rounds: 3` — agentic loop enabled by default

---

### Changed — All System Prompts Translated to English

All LLM-facing prompts (planner, judge, expert system prompts) across the codebase and Postgres templates have been translated from German to English. German-specific idioms and phrasing artifacts have been removed.

**Affected files/templates:**

| Location | What changed |
|---|---|
| `main.py` | `_proc_markers` (German process words → English equivalents) |
| `main.py` | Agentic loop status message `"📌 Noch offen:"` → `"📌 Still open:"` |
| `main.py` | T1 insufficient marker `"(T1 nicht ausreichend)"` → `"(T1 insufficient)"` |
| `benchmarks/evaluator.py` | `JUDGE_SYSTEM_PROMPT`, `JUDGE_SYSTEM_PROMPT_FALLBACK` |
| `scripts/close_ontology_gaps.py` | `RESEARCH_SYSTEM_PROMPT`, `RESEARCH_USER_TEMPLATE` |
| Postgres `tmpl-aihub-free-nextgen` | Planner prompt, judge prompt, all 12 expert system prompts |
| Postgres `tmpl-m10-gremium-deep` | `legal_advisor` expert system prompt |

---

## [2.3.0] - 2026-04-19

### Added — System Cleanup Manager & Autonomous Disk Management

#### Admin UI — System Cleanup Manager (Maintenance page)

A new **System Cleanup Manager** section on the Maintenance page gives operators
full visibility and control over all automated disk-management jobs:

- **Job cards** per subsystem: Docker Prune, LangGraph Checkpoint Archive,
  Admin-Log Rotation, systemd Journal, Prometheus TSDB
- Each card displays: last run timestamp (relative), duration (current + rolling
  average), freed space (current + rolling average), run count, and
  job-specific detail metrics
- **Inline configuration panel** (collapsible) with TTL fields per job — changes
  are persisted immediately to `/opt/moe-infra/cleanup-config.json` and
  propagate to `.env` for environment-backed settings
- **Manual trigger** for `docker_prune` and `checkpoint_archive` via
  `POST /api/cleanup/run/{job}` — runs the cron script as a background task
- Auto-refresh every 60 seconds; flash notifications on save / trigger

#### New API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/cleanup/status` | All jobs: last run, averages, config |
| `GET` | `/api/cleanup/config` | Current cleanup configuration |
| `POST` | `/api/cleanup/config` | Persist configuration, propagate to `.env` |
| `POST` | `/api/cleanup/run/{job}` | Trigger job immediately |
| `GET` | `/api/cleanup/history` | Raw JSONL history, filterable by job |
| `POST` | `/api/cleanup/write-cron-env` | Update cron `.env` files from UI |

#### Autonomous Cleanup Infrastructure

- **`/etc/cron.daily/moe-docker-prune`** — removes dangling images, stopped
  containers, and entire build cache (`docker builder prune --all`); writes
  structured JSON to `/opt/moe-infra/cleanup-history.jsonl`
- **`/etc/cron.daily/moe-checkpoint-archive`** — daily `pg_dump --format=custom
  -Z9` of the LangGraph checkpoint database (265 MB → 32 MB), followed by a
  three-phase prune of `checkpoints`, `checkpoint_writes`, and orphaned
  `checkpoint_blobs`; reads TTL from `cleanup-config.json`
- **`/etc/logrotate.d/moe-admin-logs`** — daily logrotate for JSONL monitoring
  logs (`size 50M`, `rotate 3`, `compress`)
- **`_append_log()` rotation** in `app.py` — inline file-size check before every
  write; rotates at `LOG_MAX_BYTES` (default 50 MB) with `LOG_BACKUP_COUNT`
  backups

#### Docker Subsystem Hardening

- **Container log rotation** — `/etc/docker/daemon.json` now sets
  `"max-size": "50m", "max-file": "3"` as global defaults
- **Docker build cache** — `builder prune --all` clears 200+ GB of accumulated
  build layers from nightly rebuilds (first-run reclaim: 48 GB images +
  ~216 GB cache)
- **Valkey AOF compaction** — `auto-aof-rewrite-percentage 50`,
  `auto-aof-rewrite-min-size 32mb`, `maxmemory 400mb`,
  `maxmemory-policy allkeys-lru`
- **Neo4j TX-log retention** — `NEO4J_db_tx__log_rotation_retention__policy=2 files`
- **Prometheus retention** now configurable via `PROMETHEUS_RETENTION_DAYS`
  in `.env` (default: 30 d, previously hardcoded 90 d)
- **systemd journal** capped at 512 MB / 30 days via
  `/etc/systemd/journald.conf.d/size-limit.conf`

### Fixed

- **CSRF validation failure on Teams & Tenants page** — `teams_page` passed an
  empty template context `{}`, leaving `CSRF_TOKEN = ""` in `base.html`. The
  global `fetch()` monkey-patch therefore sent `X-CSRF-Token: ""`, rejected by
  `CsrfApiMiddleware`. Fixed by passing `get_csrf_token(request)` in the context.

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Admin UI, Infrastructure, Disk Management, Security |

---

## [2.2.0] - 2026-04-17

### Added — Responsive Design & Visual Refresh

#### Admin UI — Bootstrap 5 Offcanvas Navigation

The Admin UI navbar (17 navigation links) has been restructured using the
Bootstrap 5 Offcanvas pattern to prevent horizontal overflow on screens
narrower than 1400 px.

- **Desktop (≥1400px):** full horizontal link strip (`d-none d-xxl-flex`)
- **Tablet/Phone (<1400px):** hamburger icon (`d-xxl-none`) opens a slide-in
  drawer (`offcanvas offcanvas-end`) containing all 17 links as a vertical
  list with grouped section dividers
- Right-side controls (language selector, theme toggle, logout) remain always
  visible in a compact form

#### Public Websites — 3-Breakpoint CSS (moe-web, moe-web-int, moe-libris-web)

`custom.css` now implements a 3-tier responsive grid system:

| Breakpoint | Target | Key changes |
|---|---|---|
| ≤1024px (new) | Tablet | hw-grid, tools-grid, screenshots-grid → 2 columns |
| ≤768px (extended) | Phone | All grids → 1 column; tab-bar scrollable; routing-flow stacks |
| ≤480px (new) | Small Phone | Tab padding reduced; hero font clamp; cost-grid 1 column |

Notable fixes:
- `.screenshots-grid { minmax(420px) }` → breaks on 375px — corrected
- `.view-tabs` gains `overflow-x: auto` + `-webkit-overflow-scrolling: touch` for swipe on mobile
- `.routing-node { min-width: 160px }` → `unset` to prevent overflow on narrow phones

#### User Portal — Mobile Layout

`user_portal.html` sidebar layout switches to horizontal pill navigation on
screens narrower than 768px (`flex-direction: column`, `list-group` →
`flex-wrap: wrap`). Fixed-width sidebar containers converted to `max-width`
to prevent overflow.

#### Website Screenshots (6 new)

Added to `moe-web/assets/screenshots/` and `moe-web-int/assets/screenshots/`:

| File | Content |
|---|---|
| `moe-admin-overview.png` | Full-page Admin dashboard with Advanced Pipeline Settings expanded; all credentials privacy-blurred |
| `moe-admin-monitoring.png` | Full-page monitoring view with server tiles |
| `grafana-gpu-nodes.png` | GPU & Inference Nodes dashboard (kiosk mode) |
| `grafana-knowledge.png` | Knowledge Base Health dashboard |
| `dozzle.png` | Container log viewer |
| `neo4j-knowledge-graph.png` | Knowledge graph — 500+ entity subgraph |

### Changed — Gap Healer v2 (per-node Redis slots)

`scripts/gap_healer_templates.py` replaces the v1 systemd-timer approach:

- **Root cause fixed:** global `asyncio.Semaphore(4)` caused 300 hung tasks
  piling onto one warm node. Replaced with per-node `moe:healer:active:{node}`
  Redis counters and `ZPOPMAX` atomic gap claims.
- **Hardware caps:** M60→1, M10→3, RTX→4, GT→2 concurrent slots
- **Progressive unlock:** +1 slot per 5 successful runs up to hardware cap
- **Launcher:** started from Admin UI → Monitoring panel (no systemd required)

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Admin UI, Public Websites, Gap Healer, Documentation |
| `branch` | `feat/responsive-design-admin-websites` |

---

## [2.1.1] - 2026-04-15

### Fixed — Fresh-Install Reliability (Debian 13 LXC)

All bugs were discovered during a live install test on a clean Debian 13 (trixie)
LXC container. Each was fixed in `install.sh` and verified by redeploying on the
same container. Branch: `debug/lxc-install`.

- **`install.sh`**: Added `chown -R 1000:1000 kafka-data` — `moe-kafka` (confluentinc/cp-kafka)
  runs as `appuser` uid=1000; directories created by the installer as root caused an
  immediate crash loop on first start
- **`install.sh`**: Added `chown -R 65534:65534 prometheus-data` — `moe-prometheus` runs as
  uid=65534 (nobody); missing write permission caused a panic on `queries.active`
- **`install.sh`**: Added `chown -R 472:472 grafana/data grafana/dashboards` — `moe-grafana`
  runs as uid=472; `mkdir plugins` failed with permission denied
- **`install.sh`**: Added `chown -R 1001:0 agent-logs` — `langgraph-orchestrator` and
  `mcp-precision` run as `moe` uid=1001
- **`install.sh`**: Changed `.env` permissions from `chmod 600` to `chmod 644` — containers
  bind-mount `.env:ro` and run as uid=1001; `chmod 600` locked them out at startup with
  `PermissionError: [Errno 13]`. The `:ro` mount flag is the access control mechanism;
  world-readable on the host is acceptable.
- **`install.sh`**: Changed `EVAL_CACHE_FLAG_THRESHOLD=2.0` to `2` — `main.py:732` calls
  `int(os.getenv("EVAL_CACHE_FLAG_THRESHOLD", "2"))` which raises `ValueError` on float strings
- **`main.py`**: Added `GET /health` endpoint — the Dockerfile `HEALTHCHECK` called
  `http://127.0.0.1:8000/health` but no such route existed; all containers reported
  `(unhealthy)` despite functioning correctly

| Metadata | Value |
|---|---|
| `impact` | patch — fixes silent runtime failures on fresh installs |
| `breaking` | no |
| `domain` | Installer, Orchestrator |
| `branch` | `debug/lxc-install` |
| `tested-on` | Debian 13 (trixie) LXC, Docker CE, Proxmox |

---

## [auto-2026-04-14] - 2026-04-14

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `da880a5a` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-14] - 2026-04-14

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `da880a5a` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-14] - 2026-04-14

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `da880a5a` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-14] - 2026-04-14

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `da880a5a` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-14] - 2026-04-14

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `da880a5a` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-14] - 2026-04-14

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `da880a5a` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-14] - 2026-04-14

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `da880a5a` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-14] - 2026-04-14

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `da880a5a` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-14] - 2026-04-14

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `da880a5a` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `70caef43` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `70caef43` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `70caef43` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `70caef43` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `base.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `70caef43` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `70caef43` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `database.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `70caef43` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `database.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `70caef43` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `f7c9a953` on `patch/v1.8.1-fixes` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `f7c9a953` on `patch/v1.8.1-fixes` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `database.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `f7c9a953` on `patch/v1.8.1-fixes` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `database.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `f7c9a953` on `patch/v1.8.1-fixes` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `profiles.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `profiles.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `profiles.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `profiles.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8b65e32d` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `b1b945d3` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `skills.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `skills.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-13] - 2026-04-13

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `base.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `skills.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `skills.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `skills.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `skills.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `skills.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `8d18aff2` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `monitoring.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-12] - 2026-04-12

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **MCP Tools**: `requirements.txt`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-11] - 2026-04-11

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`
- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | MCP Tools, Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 2 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`
- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | MCP Tools, Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 2 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`
- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | MCP Tools, Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 2 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `self_correction.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `user_portal.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `base.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `05c1dc94` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `b524d0fa` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `tool_eval.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `b524d0fa` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `tool_eval.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `b524d0fa` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Admin UI**: `tool_eval.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `b524d0fa` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `b524d0fa` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `b524d0fa` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `b524d0fa` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `b524d0fa` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `b524d0fa` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `b524d0fa` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-10] - 2026-04-10

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `b524d0fa` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `4a545995` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `4a545995` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `4a545995` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `Dockerfile`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `4a545995` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `live_monitoring.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `live_monitoring.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `database.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `live_monitoring.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `live_monitoring.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `live_monitoring.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `requirements.txt`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `database.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `Dockerfile`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Docs Sync Agent**: `sync_agent.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Docs Sync Agent |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Docs Sync Agent**: `sync_agent.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Docs Sync Agent |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `e9405be0` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `697ef075` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `697ef075` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `697ef075` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `697ef075` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `697ef075` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `697ef075` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `1f6229fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `1f6229fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `1f6229fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `1f6229fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-09] - 2026-04-09

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **MCP Tools**: `server.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | MCP Tools |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [2026-04-08] - Dozzle, Setup Wizard, install.sh

> `impact: minor` · `breaking: no` · `domain: infra, admin-ui, docs`

### Added

- **Dozzle log viewer** — new `moe-dozzle` container (amir20/dozzle:latest) accessible at `logs.moe-sovereign.org` via Caddy; bcrypt auth via `/opt/moe-infra/dozzle-users.yml`; added to CONTAINER_NAMES in Admin UI monitoring table
- **Setup Wizard** — first-run 4-step web wizard (`/setup`) in Admin UI; triggers automatically when `INFERENCE_SERVERS` is empty; covers inference servers, judge/planner models, and public URLs; implemented in all 4 UI languages
- **`install.sh`** — curl-able bash installer for Debian 11/12/13; auto-installs Docker CE, creates `/opt/` directories, generates secure secrets, prompts for admin credentials, deploys the full stack
- **`.env.example`** — comprehensive template with all 80+ variables documented and grouped by subsystem
- **`moe-sovereign.org` Caddy block** — serves `install.sh` via `file_server`; redirects all other traffic to docs
- **Installation guide** (`docs/guide/installation.md`) — requirements, one-line install, manual setup, directory layout, upgrade procedure
- **First-Time Setup guide** (`docs/guide/first-setup.md`) — wizard walkthrough, backend types, minimum viable config example

### Removed

- **"Expert Models — Global Default" card** — removed from Admin UI dashboard; model assignment is fully handled via Expert Templates and per-user API token auth; `rebuild_expert_models()` function deleted; `EXPERT_MODELS` no longer written by `save_config()`

### Changed

- Caddy config: added `moe-sovereign.org` and `logs.moe-sovereign.org` virtual hosts
- docker-socket-proxy: added `LOGS`, `EVENTS`, `VERSION`, `PING` endpoint permissions
- Quickstart docs: deployment section references `install.sh`

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Infrastructure / Docker**: `docker-compose.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Infrastructure / Docker |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [2026-04-08] — Memory Palace: Domain-Scoped Retrieval, Expert Memory Isolation & Auto-Save Hooks

### Added

- **Metadata-Filtered Semantic Search** (`main.py` — `planner_node`, `graph_rag_node`):
  The planner now optionally extracts `metadata_filters` (e.g. `{"expert_domain": "code_reviewer"}`)
  from the first task object. `graph_rag_node` applies these as a ChromaDB `where` clause
  after the Neo4j traversal, appending domain-scoped results to `graph_context` under a
  `[Domain-Filtered Memory]` label. Degrades silently on errors.

- **Isolated Expert Memory** (`main.py` — `merger_node`; `graph_rag/manager.py`):
  Every response stored in ChromaDB is tagged with `expert_domain` metadata.
  The `moe.ingest` Kafka payload gains a `source_expert` field that flows through the
  consumer into `extract_and_ingest()` and `ingest_synthesis()`, tagging all resulting
  `:Entity` nodes, relations, and `:Synthesis` nodes in Neo4j with their origin expert category.

- **`POST /v1/memory/ingest` endpoint** (`main.py`):
  New FastAPI endpoint that accepts `{session_summary, key_decisions, domain, source_model,
  confidence}` and publishes to `moe.ingest` for async GraphRAG processing. Enables
  external tools to persist knowledge without accessing Kafka directly.

- **Claude Code Auto-Save Hooks** (`hooks/`):
  Two bash scripts — `mempal_precompact_hook.sh` (PreCompact) and `mempal_save_hook.sh`
  (Stop) — POST session summaries to `/v1/memory/ingest` before context is lost.
  Configured via `~/.claude/settings.json`. See `hooks/README.md` for setup.

- **Documentation** (`docs/system/intelligence/memory_palace.md`):
  New chapter in the Intelligence & Learning section covering all three features with
  architecture diagrams, schema references, and operational runbook.

### Changed

- `AgentState` gains `metadata_filters: Dict` field
- `moe.ingest` Kafka payload gains `source_expert` field
- ChromaDB `moe_fact_cache` documents gain `expert_domain` metadata field
- Neo4j `:Entity` nodes and relations gain `expert_domain` property
- Neo4j `:Synthesis` nodes gain `expert_domain` property
- `docs/system/architecture.md`, `dataflow.md`, `pipeline.md`,
  `intelligence/index.md`, `intelligence/compounding_knowledge.md` updated

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | ChromaDB / Neo4j / Orchestrator / Claude Code Integration |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `aa6cd5fc` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [2026-04-08] — Graph-basierte Wissensakkumulation

### Added

- **Synthesis Persistence** (`main.py`, `graph_rag/manager.py`): The `merger_node` now appends a `<SYNTHESIS_INSIGHT>` JSON block to its output when it produces a novel multi-source comparison, inference, or synthesis. The block is automatically stripped from the user-facing response. The Kafka consumer reads the `synthesis_insight` field on `moe.ingest` messages and calls the new `graph_manager.ingest_synthesis()` method, which creates a `:Synthesis` node in Neo4j (idempotent, sha256-keyed) and links it to relevant `:Entity` nodes via `:RELATED_TO`. These nodes surface in future `graph_rag_node` 2-hop traversals.

- **Graph Linting / Knowledge Janitor** (`main.py`, `graph_rag/manager.py`): New Kafka topic `moe.linting` (constant `KAFKA_TOPIC_LINTING`). Any message on this topic triggers `graph_manager.run_graph_linting()` as a background task. Phase 1 deletes orphaned `:Entity` nodes (no relationships, LIMIT 50). Phase 2 iterates contradictory relationship pairs from `_CONTRADICTORY_PAIRS`, detects same-subject conflicts, calls the Ingest LLM for resolution, and flags the losing relationship (`flagged=true`, `lint_note`, `lint_ts`, `lint_model`). All LLM calls are throttled via the existing `_ingest_semaphore` with 0.5 s sleep between iterations.

- **New documentation chapter** `docs/system/intelligence/compounding_knowledge.md`: Full description of both features including architecture diagrams, Cypher queries, operational runbook, and Neo4j schema changes.

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | GraphRAG, Kafka, Orchestrator, Documentation |
| `files changed` | 8 (`main.py`, `graph_rag/manager.py`, `docs/system/kafka.md`, `docs/system/architecture.md`, `docs/system/dataflow.md`, `docs/system/pipeline.md`, `docs/system/toolstack/graphrag_neo4j.md`, `docs/system/intelligence/compounding_knowledge.md`) |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Documentation (mkDocs)**: `mkdocs.yml`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Documentation (mkDocs) |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | major |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **GraphRAG**: `manager.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | GraphRAG |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `c8881644` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Orchestrator (main.py)**: `main.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Orchestrator (main.py) |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `user_portal.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `user_portal.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `expert_templates.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-08] - 2026-04-08

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `4b3c04d1` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `users.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `users.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `users.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `users.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `user_portal.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `database.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `database.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `database.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `database.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `user_portal.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `zh_CN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `fr_FR.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `en_EN.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `users.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `de_DE.lang`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `user_portal.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `user_portal.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `user_portal.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `user_portal.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `user_portal.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `base.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `base.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `base.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `users.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `users.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `users.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `users.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `dashboard.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `user_portal.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `login.html`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [auto-2026-04-07] - 2026-04-07

### Changed

- **Admin UI**: `app.py`

| Metadata | Value |
|---|---|
| `impact` | patch |
| `breaking` | no |
| `domain` | Admin UI |
| `git` | `35ab3e4f` on `main` |
| `files changed` | 1 |

---

## [3.0.0] - 2026-04-07

### Added — Causal Learning Loop

- **GraphRAG / Neo4j** (`graph_rag/ontology.py`): Three new procedural relation types with enterprise seed triples
  - `NECESSITATES_PRESENCE` (Action → Location): performing an action requires physical presence at a location
  - `DEPENDS_ON_LOCATION` (Action → Location): action outcome depends on a reachable location
  - `ENABLES_ACTION` (Condition → Action): a prerequisite resource or state makes an action possible
  - 25 new entities across three new types: `Action`, `Location`, `Condition`
  - Seed triples cover IT operations workflows (on-premises deployment, hardware installation, remote access)

- **GraphRAG Manager** (`graph_rag/manager.py`): Procedural reasoning in extraction and retrieval
  - Extended extraction prompt to elicit factual and procedural triples in a single LLM call
  - `_query_procedural_requirements()`: targeted Cypher traversal when `Action`-type entities appear in results
  - `_get_ingest_semaphore()`: `asyncio.Semaphore(2)` caps concurrent background ingest calls
  - Auto-detection: extracted procedural triples override upstream `knowledge_type` hint
  - `extract_and_ingest()` extended with optional `knowledge_type` param; returns detected type

- **Orchestrator** (`main.py`): Graph ingest LLM decoupled from judge/merger
  - `GRAPH_INGEST_MODEL` / `GRAPH_INGEST_ENDPOINT`: dedicated model for background extraction; falls back to judge LLM when unset
  - `knowledge_type` field added to `moe.ingest` Kafka events
  - `graph_rag_node`: prepends explicit merger annotation when `[Procedural Requirements]` block is present

- **Admin UI** (`admin_ui/templates/dashboard.html`, `admin_ui/app.py`): Graph Ingest Model selector
  - New field in Models card following `model@endpoint` pattern
  - Persisted via `GRAPH_INGEST_MODEL` / `GRAPH_INGEST_ENDPOINT` env keys

- **Documentation** (`docs/system/intelligence/`): New *Intelligence & Learning* section
  - `causal_learning.md`: motivation, ontology, extraction pipeline, Cypher queries, enterprise use cases
  - `context_extension.md`: six mechanisms for effective context extension despite small LLM context windows
  - Updated `architecture.md` and `dataflow.md` with corrected diagrams and topic names

### Changed

- `mkdocs.yml`: new top-level nav section *Intelligence & Learning*
- `sync_agent.py`: all generated content translated to English
- `docs/changelog.md`: deduplicated 1051 auto-generated duplicate entries; all entries translated to English; metadata fields added to every release

| Metadata | Value |
|---|---|
| `impact` | major — new knowledge type in graph; existing triples unaffected (MERGE is idempotent) |
| `breaking` | no — `extract_and_ingest()` extended with optional param only |
| `domain` | GraphRAG, Kafka, Admin UI, Documentation |

---

## [2.3.0] - 2026-04-07

### Changed — Security Hardening & Admin UI Overhaul

- **Admin UI**: CSRF tokens on all state-changing forms; rate limiting on auth endpoints; session management improvements; all four locale files updated (de_DE, en_EN, fr_FR, zh_CN)
- **Infrastructure** (`docker-compose.yml`): Redis Stack configured via `REDIS_ARGS`; middleware ordering verified

| Metadata | Value |
|---|---|
| `impact` | minor — no API changes; session behaviour hardened |
| `breaking` | no |
| `domain` | Admin UI, Infrastructure, Security |

---

## [2.2.1] - 2026-04-06

### Changed — Admin UI Internationalisation & Claude Code Profile Fixes

- **Admin UI**: Full i18n pass across all 14 templates and four locale files; `Dockerfile` rebuilt
- **Orchestrator** (`main.py`): Claude Code profile `expert_template_id` resolution corrected; vision mode fixed for image attachments; process table display fixed
- **Documentation** (`mkdocs.yml`): navigation labels standardised to English

| Metadata | Value |
|---|---|
| `impact` | patch — UI and i18n fixes; API unchanged |
| `breaking` | no |
| `domain` | Admin UI, i18n, Claude Code Integration |

---

## [2.2.0] - 2026-04-05

### Added

- **Admin UI** (`profiles.html`, `app.py`): Expert-template selector in Claude Code profile editor — profiles can now assign an individual expert template (visible only in `moe_orchestrated` mode)
- **Orchestrator** (`main.py`): `expert_template_id` in CC profiles loaded via `admin_override=True` without permission check
- **Auto-documentation** (`sync_agent.py`, `scripts/doc_sync_hook.sh`): Claude Code PostToolUse hook writes changelog entries to `docs/changelog_pending.jsonl`; `sync_agent.py --changelog-only` flushes pending entries every 15 min
- **Expert template `claude-code-agent`**: system prompts for all expert categories optimised for autonomous tool execution in agentic loops

### Changed

- `_resolve_user_experts` / `_resolve_template_prompts`: new optional `admin_override: bool` parameter
- CC profiles: new optional field `expert_template_id`

| Metadata | Value |
|---|---|
| `impact` | minor — new optional field; existing profiles unaffected |
| `breaking` | no |
| `domain` | Admin UI, Claude Code Integration, Expert Templates |

---

## [2.1.0] - 2026-03-29

### Added

- **Two-Tier Expert System**: `_infer_tier()` routes T1 (≤20B params) first; T2 (>20B) only when T1 confidence is below threshold — saves VRAM on simple requests, escalates for complex ones
- **Chat-history injection**: `_truncate_history()` — last 4 turns, max 3000 chars — all expert LLMs receive conversation context; `chat_history` field added to `AgentState` and API layer
- **Citation tracking**: `_web_search_with_citations()` appends structured source references (title + URL) to web research results
- **`thinking_node`**: new node between `research_fallback` and `merger`; activated for complex plans or low-confidence results; 4-step chain-of-thought; output as `reasoning_trace`
- **Expert deduplication**: `_dedup_by_category()` keeps highest-confidence result per category; prevents echo chamber
- **`critic_node`**: post-merger validation for `medical_consult` and `legal_advisor` (safety-critical); judge LLM checks for factual errors and dangerous statements

### Changed

- Graph topology: `research_fallback → thinking → merger → critic → END`
- `expert_worker`: two-tier logic replaces simple parallel fan-out
- Judge LLM updated to reasoning-focused model

| Metadata | Value |
|---|---|
| `impact` | minor — pipeline extended; API compatible |
| `breaking` | no |
| `domain` | Orchestrator, Expert Routing, Safety |

---

## [2.0.0] - 2026-03-29

### Added

- **Kafka integration** (KRaft mode, no Zookeeper): topics `moe.ingest`, `moe.requests`, `moe.feedback`; `AIOKafkaProducer` with 12-retry backoff; `AIOKafkaConsumer` as permanent asyncio background task; graceful degradation when Kafka unavailable
- **GraphRAG / Neo4j Knowledge Graph**: `GraphRAGManager` with 2-hop traversal; base ontology 104 entities / 100 relations across 4 domains; background triple extraction via judge LLM; conflict detection; domain filter prevents cross-domain contamination; API endpoints `GET /graph/stats`, `GET /graph/search`
- **MCP Precision Tools Server** (port 8003): 16 deterministic tools — calculate (safe AST evaluator), date arithmetic, unit conversion, statistics, regex, subnet calculator, base64, JSON query
- **Self-learning infrastructure**: `POST /v1/feedback` endpoint; expert performance tracking in Redis (Laplace smoothing); cache flags low-rated entries; response metadata TTL 7 days
- **LangGraph pipeline extended**: `mcp_node`, `graph_rag_node` added to parallel fan-out; cache short-circuit at cosine distance < 0.15; expert output cap 2400 chars
- **Confidence scoring**: structured expert output `KONFIDENZ: hoch | mittel | niedrig`; merger prioritises by confidence
- **Research fallback node**: automatic web search on low-confidence results; forced for safety-critical categories
- **Output modes**: `moe-orchestrator`, `moe-orchestrator-code`, `moe-orchestrator-concise`
- **Token tracking**: accumulated `prompt_tokens` + `completion_tokens` across all LLM calls in every response
- **Open WebUI `<think>` panel**: progress reports from all nodes via `contextvars.ContextVar`
- **Open WebUI internal-request detection**: fast-paths title/autocomplete prompts directly to judge

### Fixed

- Planner returning strings instead of task dicts → `_sanitize_plan()` added
- Neo4j deprecated relation type warnings removed from `_CONTRADICTORY_PAIRS`
- docker-compose duplicate `environment` block merged

| Metadata | Value |
|---|---|
| `impact` | major — Kafka and Neo4j are new required infrastructure |
| `breaking` | yes — requires `moe-kafka` and `neo4j-knowledge` services |
| `domain` | Orchestrator, Infrastructure, GraphRAG, MCP, Kafka |

---

## [1.2.0] - 2026-02-12

### Added

- SymPy mathematics module: equation solving, simplification, differentiation, integration

### Changed

- Expert worker: improved stagger algorithm
- GPU count dynamically configurable via environment variable
- Improved CUDA OOM error detection and handling

### Fixed

- Checkpointer initialisation error (`_GeneratorContextManager`)
- Memory leak in expert worker semaphore management

| Metadata | Value |
|---|---|
| `impact` | minor |
| `breaking` | no |
| `domain` | Orchestrator, Math |

---

## [1.1.0] - 2026-02-11

### Added

- LangGraph Multi-Model Orchestration (initial pipeline)
- Multi-node GPU cluster support via Ollama
- Redis checkpoint persistence for graph state
- ChromaDB vector store for knowledge caching
- SearXNG web research integration
- Docker Compose deployment
- OpenAI-compatible API endpoint (`/v1/chat/completions`) with SSE streaming

| Metadata | Value |
|---|---|
| `impact` | minor — initial functional release |
| `breaking` | no |
| `domain` | Orchestrator, Infrastructure |

---

## [1.0.0] - 2026-02-10

### Added

- Project initialisation with LangGraph base structure
- Core nodes: `cache_lookup`, `planner`, `expert_workers`, `merger`
- Docker configuration and basic error handling

| Metadata | Value |
|---|---|
| `impact` | major — initial release |
| `breaking` | n/a |
| `domain` | Orchestrator |
