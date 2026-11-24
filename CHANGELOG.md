# Changelog

Alle wesentlichen Änderungen am Sovereign MoE Orchestrator werden hier dokumentiert.
Format basiert auf [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased] - 2026-11-24

### Fixed

- **Native inference calls (`model@node`) ran into `ReadTimeout` after 300 s.** Six call sites
  (`services/pipeline/chat.py`: global endpoints, user connections, both non-streaming httpx
  calls; `main.py`: `_stream_native_llm` read timeout) used a hardcoded `300` fallback, and
  slow-prefill nodes (N11 Tesla M10) had no `timeout` in `INFERENCE_SERVERS`. Large agentic
  prompts (OpenCode, ~10k+ tokens) then timed out during prefill. All sites now use the single
  setting `INFERENCE_DEFAULT_TIMEOUT` (`config.py`); a per-server `timeout` still wins.
  Code: commit `7c021c21`; tests: `tests/test_native_timeout_map.py`.

### Added

- **`INFERENCE_DEFAULT_TIMEOUT`** (seconds, env, code default `300`, `.env.example` suggests
  `1800`): fallback read timeout for servers and user connections without an explicit `timeout`.
- Documentation of the `INFERENCE_SERVERS` fields `timeout`, `vram_gb` and `gpu_count` and of the
  `model@node` routing rules in `docs/system/toolstack/ollama_cluster.md`.

### Documentation

- Measured per-node performance for `qwen3.6:35b` (prefill/decode, direct vs. gateway) and an open N11 cooling note in
  `docs/system/toolstack/ollama_cluster.md`; `model@node` silent-fallback example.

---

## [2.9.0] - 2026-07-10

### Added

- **Augmented Tool Path for Agentic Clients** (`services/agent_enrichment.py`): opt-in enrichment
  layer sitting between the tool-calling fast path (`_anthropic_tool_handler` in
  `services/pipeline/anthropic.py`, `_handle_tool_calls` in `services/pipeline/chat.py`) and the
  tool model, fully transparent to the client (streaming events, `stop_reason` semantics, and
  tool-use blocks preserved byte-for-byte). Adds three independently gated capabilities for
  Claude Code CLI / OpenCode / comparable function-calling clients, which previously bypassed the
  entire cache and GraphRAG infrastructure on every request:
  - **Agent cache** (`AGENT_CACHE_ENABLED`): dedicated L0 Valkey + L1 ChromaDB
    (`moe_agent_cache`) cache, scoped per `sha256(user_id|workspace)`. Only informational queries
    on a session's first turn are served, at confidence ≥ 0.85 and freshness ≤ 14 days. Tool-use
    decisions are never cached.
  - **Agent GraphRAG injection** (`AGENT_GRAPHRAG_ENABLED`): tenant-scoped Neo4j context injected
    into the tool model's system prompt once per session (cached in Redis for subsequent turns),
    fixing three pre-existing bugs in the former inline Tier-4 block (`tenant_ids=None`, no
    budget cap, cross-tenant cache-key leak).
  - **Agent write-back / ingestion** (`AGENT_INGEST_ENABLED`): clean-ending agentic sessions
    publish their Q/A pair to the same Kafka ingestion topic the interactive pipeline uses.
    Fresh answers start at confidence 0.6 (below the serve threshold) and are promoted to 0.9 by
    an async judge call, mirroring the trust-building pattern of the interactive L1 cache.
  - All three flags default `false` (opt-in per CC profile / Expert Template); with everything
    disabled, behaviour is bit-identical to the plain tool-calling fast path. Latency is
    hard-bounded (300 ms cache lookup, 2 s GraphRAG query) and fails open. New test profile
    `configs/cc_profiles/cc-agent-augmented.json`. Capability **#50**. See
    `docs/reference/expert-template-guide.md#augmented-tool-path-agentic-clients`.

### Fixed

- **Missing request deregistration in tool-calling passthrough** (`services/pipeline/chat.py`,
  `services/pipeline/anthropic.py`): streaming tool-call responses never called
  `_deregister_active_request` on stream end, leaving stale entries in the admin "Laufende
  API-Anfragen" table after the client had already disconnected. Fixed with a
  `_wrap_deregister_on_stream_end()` `try/finally` generator wrapper around both streaming paths.
- **Missing template name in Claude Code session monitoring**: `_register_active_request()` calls
  in the Anthropic tool handler did not pass `template_name`/`resolved_tmpl_id`, leaving the
  "Template" column blank for CC sessions in the admin monitoring table.

### User Portal

- **Selective template export**: checkbox-based multi-select export for own Expert Templates
  (previously all-or-nothing), mirroring the existing Admin-panel export pattern.
- **Copy/duplicate own template**: one-click duplication of an existing user template into a new
  editable draft.
- Cleaned up orphaned "ghost template" permission grants pointing at deleted admin templates.

## [2.8.0] - 2026-06-23

### Added

- **HABE 2.0 (Tensor Product Representations)** (`services/vsa_background.py`): support for **hierarchical graph structures** recursively bundled in 2048-dimensional Vector Symbolic Architecture (VSA). Enables **recursive unbinding** of parent/relation keys to query sub-subsystems and **Virtual Prefix Attention Modulation** via `habe_prefix_embedding` options in OpenAI-compatible and native Ollama endpoints.
- **Eurisko Heuristic Breeder** (`scripts/eurisko_template_optimizer.py`): self-referential evolutionary template optimizer. Features **Roulette-Wheel selection** for mutators, **crossover breeding** of top-performing heuristics (weights > 1.5), and automatic **weight adaptation** (+15% reward / -15% penalty) based on PostgreSQL ratings.
- **Advice-Taker Rule-Engine** (`services/advice_store.py`): John McCarthy-inspired rule-engine. Intercepts planning to inject priority tasks. Features **3-gram character Jaccard similarity semantic matching** (threshold &ge; 0.3) and **declarative regex parameter extraction** for dynamic MCP tool argument binding.
- **Dynamic System Prompts** (`services/dynamic_router.py`, `scripts/dataset_generator.py`): Meta-prompter LLM pathway and zero-latency keyword interpolation to dynamically generate prompt-specific system prompts for the planner, judge, and active experts.

### Fixed

- **ChromaDB Semantic Cache Query Mismatch** (`services/dynamic_router.py`): query and document formats aligned to raw prompts to enable ChromaDB L2 cache hits.
- **PostgreSQL Feedback Log Wiring** (`services/dynamic_router.py`): wired `log_dynamic_template_feedback` into the compile pathway, enabling user ratings to update logs (rowcount > 0).
- **Context Window Budget Priority** (`context_budget.py`, `services/inference.py`): resolved priority mismatch by introducing `resolve_requested_ctx()` helper as single source of truth for context clamping.

## [2.7.1] - 2026-06-13

### Research & Funding

- **EuroHPC LUMI-G Grant Awarded**: Proposal `EHPC-DEV-2026D06-XXX` approved under the
  EuroHPC Development Access programme (award notice received 2026-06-05). Grants
  **4,500 node-hours (18,000 GPU-hours)** on the **LUMI-G** partition (AMD MI250X,
  128GB HBM2e/node, ROCm stack, 2TB storage) over a 6-month period.
- Funds a 5-target SLM distillation programme: `planner_node` (primary lever,
  targeting Qwen2.5-1.5B / SmolLM2-1.7B as GGUF Q4_K_M, ≥90% of the 35B-teacher
  GAIA plan quality at ~1/20 cost), `complexity_estimator` (DeBERTa-v3-small, ONNX
  INT8), semantic router (multilingual MiniLM-L12-v2 + FAISS), RL routing policy
  (offline-RL MLP), and node ranker (XGBoost, ONNX).
- Rollout gated behind a new `PLANNER_MODE` flag (`llm` / `slm_local` / `hybrid`)
  with confidence-based fallback to the cloud teacher — same fail-open principle as
  expert routing. 18,000 GPU-h budgeted across synthetic data generation, encoder/
  reward-model training, planner SFT+DPO, offline RL, and a final RLHF pass.
- See whitepaper section "Funded SLM distillation programme (EuroHPC LUMI-G)" for
  the full phase plan.

## [2.7.0] - 2026-06-02

### Added

- **OpenAI Tool-Calling Passthrough** (`services/pipeline/chat.py` `_handle_tool_calls()`): when a
  request carries a `tools` array or messages with `role: "tool"`, the gateway skips the
  planner/experts/merger pipeline and forwards directly to the template's judge model. Supports
  streaming (SSE) and non-streaming responses; normalises `content: null` and strips the
  non-standard `reasoning` field for strict OpenAI-compatible clients (Hermes, Open-WebUI).
  Follow-up `role: "tool"` turns omit `tools` in the upstream payload to prevent model tool-call
  loops. Capability **#44**. (PR #226)

- **Two-Tier Escalation Telemetry** (`metrics.py`):
  - `moe_tier_escalation_total{category, decision}` — per-category counter for four outcomes:
    `t1_high_skip` (T2 saved), `t1_cost_kept` (deliberate saving on trivial tasks), `t1_only`
    (no T2 configured), `t2_escalated` (T1 insufficient).
  - `moe_cache_query_distance` histogram — nearest cosine distance per cache lookup with dense
    buckets around the decision boundaries (0.08 / 0.25 / 0.50) for threshold calibration.
  - Structured per-call expert log line: `📊 expert_call model=… node=… tier=… cat=… conf=…`
    in `graph/expert.py`. Capability **#45**. (PR #222)

- **`t1_cost_kept` Telemetry Label** — splits the former `t1_only` bucket into two distinct
  outcomes: `t1_cost_kept` (T2 was available but a good-enough medium T1 answer kept the cost
  saving) vs `t1_only` (no T2 tier configured at all). Pure function `_tier2_escalation_decision()`
  with deterministic unit-test coverage. (PR #224)

- **Constellation Benchmark Suite** (`benchmarks/datasets/quality_constellation_v1.json`,
  `run_overnight_constellation.sh`): curated 11-question quality dataset across math, code,
  reasoning, science, and four new expert categories (data_analysis, creative_writing, devops_sre,
  security_analysis). Overnight runner covers all three constellation templates with LLM-judge
  scoring (mistral-small:24b) + GAIA Level 1/2 sanity run.

- **Expert Templates**: `moe-quality-optimal` (best-per-category allrounder: qwen2.5-coder:32b code,
  solar-pro:22b creative, qwen3-coder:30b devops, deepseek-r1:32b security) and `hermes-sovereign`
  (hermes3:8b for tool-calling / MCP, qwen3.6:35b for content analysis). Both on N04-RTX.

- **`moe-mail-classify` Template** — lightweight single-expert classifier (qwen2.5:7b, no
  pipeline overhead) for batch email categorisation. Used by the Hermes mail-sort MCP tool.

### Fixed

- **`moe_requests_total` + `moe_response_duration_seconds` not incremented for `stream=false`
  clients**: the non-streaming path in `services/pipeline/chat.py` was missing the Prometheus
  counter increments that the streaming path had. Affected all load tests, Open-WebUI, and
  any client using `"stream": false`. (PR #222)

- **`_sanitize_plan()` silently dropped user-template expert categories**: categories defined
  only in a user template (e.g. `mail_classify`, `devops_sre`, `creative_writing`, `tool_agent`)
  were not in the global `EXPERTS` registry and were silently rewritten to `"general"`, bypassing
  the intended specialist model. The active template's category set is now passed as an additional
  valid set. (PR #225)

- **`CACHE_HIT_THRESHOLD=0.0` in `.env` disabled L1 semantic cache**: a misconfigured `.env`
  entry (`dist < 0.0` is never true) killed all L1 cache hits while the comment claimed it
  "raised for better hit rate". Corrected default in `.env.example` to `0.08` (near-verbatim
  matches only; broader semantic reuse remains quality-gated via bypass threshold 0.25).

### Changed

- **`_tier2_escalation_decision()` extracted as pure, unit-tested function** (`graph/expert.py`):
  the inline decision logic is now a module-level function with 14 deterministic test cases
  (`tests/pipeline/test_tier_escalation.py`), covering normal vs cost-tier paths, mixed ensembles,
  and the absence of a T2 tier. (PR #223/#224)

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `TRIVIAL_LOW_CONF_RESCUE_ENABLED` | `true` | Allow a single T2 rescue on trivial queries that return low-confidence T1 answers |
| `CACHE_HIT_THRESHOLD` | `0.08` | L1 semantic cache hit threshold (cosine distance); previously defaulted to 0.15 in config.py but was overridden to 0.0 in .env |

---

## [2.6.0] - 2026-05-22

### Added

- **User Conversation Audit Log** (`services/conversation_log.py`, `admin_ui/conversation_log.py`): every authenticated API request is appended as a JSONL entry to `${MOE_DATA_ROOT}/user-audit-logs/{user_id}.jsonl`. Stores full prompt text, full response, routing metadata (model, mode, expert domains, cache hit, latency, agentic rounds). Fire-and-forget async write with per-user `asyncio.Lock` to prevent line interleaving under concurrent requests.

- **User Portal Audit Log page** (`/user/audit-log`): users can browse their own conversation history with date-range filter, full-text search (prompt + response), click-to-expand modal with copy button, CSV/JSON export, and configurable retention setting.

- **Per-user retention** (`users.conversation_log_retention_days`): users set their own retention in days (0 = no retention, max configurable by admin). Admin sets global default (`CONVERSATION_LOG_RETENTION_DAYS_DEFAULT=90`) and ceiling (`CONVERSATION_LOG_RETENTION_MAX=365`). Daily cleanup loop in `moe-admin` deletes rotated files beyond each user's retention.

- **Logrotate integration** (`/etc/logrotate.d/moe-user-audit`): daily rotation with `dateext`, `copytruncate`, `compress`, `rotate 365`. Host path: `${MOE_DATA_ROOT}/user-audit-logs/`.

- **Shared volume** (`docker-compose.yml`): `${MOE_DATA_ROOT}/user-audit-logs` mounted as `/app/logs/users` in both `langgraph-app` (writer) and `moe-admin` (reader/cleanup).

- New environment variables: `CONVERSATION_LOG_ENABLED` (default `true`), `CONVERSATION_LOG_DIR`, `CONVERSATION_LOG_RETENTION_DAYS_DEFAULT`, `CONVERSATION_LOG_RETENTION_MAX`.

- New DB column: `users.conversation_log_retention_days` (nullable INTEGER, auto-migrated).

- New API routes: `GET /user/audit-log`, `GET /user/api/audit-log`, `GET /user/api/audit-log/{request_id}`, `PATCH /user/api/settings/log-retention`.

---

## [2.5.3] - 2026-05-20

### Added

- **Query Reformulation for Agentic RAG** (`graph_rag/manager.py`): implements the iterative retrieval pattern from Agentic RAG (`1757817150496.jpeg`). When term-matching returns nothing, a lightweight LLM (GRAPH_INGEST_MODEL) generates up to 2 alternative query phrasings (shorter terms, English equivalents, known abbreviations). Each alternative is retried through the full term-matching pipeline before falling back to Text-to-Cypher. Adds at most one LLM call (5s timeout) when term-matching fails.

  Refactored `query_context()` into 3 clear stages: term-matching → reformulation retry → T2C fallback.
  New private method `_match_terms_to_entities()` eliminates the previously duplicated Neo4j Cypher loop.

  New env vars: `GRAPHRAG_REFORMULATE_ENABLED` (default `1`), `GRAPHRAG_REFORMULATE_TIMEOUT` (default `5.0`s).

---

## [2.5.2] - 2026-05-20

### Changed

- **Confidence-Weighted Expert Synthesis** (`graph/synthesis.py`): the merger prompt now receives an explicit weight table before the expert content. Experts are sorted high → low confidence (primacy bias) and labelled `PRIMARY` / `SUPPORTING` / `BACKGROUND`. The judge's synthesis instruction anchors on PRIMARY findings, uses SUPPORTING to refine, and draws on BACKGROUND only for gaps. No extra LLM call — relies on the `CONFIDENCE: high/medium/low` fields already present in expert output.

---

## [2.5.1] - 2026-05-20

### Added

- **Text-to-Cypher GraphRAG Fallback** (`graph_rag/manager.py`): when term-matching returns no entities, a lightweight LLM (configured via `GRAPH_INGEST_ENDPOINT`/`GRAPH_INGEST_MODEL`) generates a targeted Cypher MATCH query from natural language. Validation enforces read-only access (write operations rejected by regex whitelist before execution). Result formatted as `[Knowledge Graph — Text-to-Cypher]` block appended to the same `graph_context` field. Zero latency impact when term-matching succeeds.

  New env vars: `GRAPHRAG_T2C_ENABLED` (default `1`), `GRAPHRAG_T2C_TIMEOUT` (default `8.0`s), `GRAPHRAG_T2C_MAX_NODES` (default `8`).

---

## [2.5.0] - 2026-05-20

### Added

- **Corrective RAG Gate** (`graph_rag/manager.py` — `_corrective_relevance_score`): retrieved Neo4j entities are now scored for query relevance before being injected into the judge prompt. Score = weighted term-overlap (entity-name hit counts 2×, relation-target hit counts 1×) + average relation confidence. Entities scoring below `GRAPHRAG_CORRECTIVE_THRESHOLD` (env var, default `0.15`) are discarded. Prevents context pollution from tangentially matched graph nodes that degrade judge output quality. Scientific basis: Yan et al. 2024, *Corrective Retrieval Augmented Generation* (arXiv:2401.15884).

- **CAG Compliance Layer** (`compliance_cag.py`, integrated into `graph/tool_nodes.py`): static regulatory knowledge domains (BAIT, VAIT, DORA, KRITIS, MaRisk) now bypass Neo4j retrieval entirely. Admin drops JSON files (`{"name", "keywords", "context"}`) into `$MOE_DATA_ROOT/cag/`; at query time the orchestrator detects keyword matches and injects the pre-loaded authoritative text directly — more reliable and lower latency than retrieval for stable regulatory content. Hot-reloaded every `CAG_RELOAD_INTERVAL_S` seconds (default 300). Opt-out via `GRAPHRAG_CAG_ENABLED=0`. Scientific basis: Chan et al. 2024, *Don't Do RAG: When Cache-Augmented Generation is All You Need* (arXiv:2412.15605). Ships with `bait.json` and `dora.json` example domain files.

- **Episodic Memory** (`episodic_memory.py`, integrated into `graph/synthesis.py` and `graph/tool_nodes.py`): every completed pipeline run is logged as a `:Episode` node in Neo4j (task type, routing path, tools used, confidence estimate, token cost, user, TTL 90 days, indexed by `hash`). On similar queries, `get_episode_hint()` retrieves relevant past episodes via Sørensen–Dice similarity (APOC, with recency fallback) and appends a `[Episode Hint]` block to `graph_context` — the judge can take proven routing strategies into account. Logging is fire-and-forget (zero latency overhead). Scientific basis: Tulving (1972) episodic/semantic memory distinction; Park et al. 2023, *Generative Agents* (Stanford); Packer et al. 2023, *MemGPT*. Opt-out via `EPISODIC_MEMORY_ENABLED=0`.

### Environment Variables Added

| Variable | Default | Description |
|---|---|---|
| `GRAPHRAG_CORRECTIVE_THRESHOLD` | `0.15` | Minimum relevance score for Neo4j entities before injection (0 = disabled) |
| `GRAPHRAG_CAG_ENABLED` | `1` | Set to `0` to disable the CAG compliance layer |
| `CAG_COMPLIANCE_DIR` | `$MOE_DATA_ROOT/cag` | Directory containing compliance domain JSON files |
| `CAG_RELOAD_INTERVAL_S` | `300` | Seconds between live-reloads of CAG domain files |
| `EPISODIC_MEMORY_ENABLED` | `1` | Set to `0` to disable episodic memory logging and recall |
| `EPISODIC_MAX_HINTS` | `2` | Maximum past episodes injected as routing hints per request |
| `EPISODIC_MIN_CONFIDENCE` | `0.6` | Minimum episode confidence required for recall |
| `EPISODIC_TTL_DAYS` | `90` | Days before Episode nodes expire in Neo4j |

---

## [2.4.0] - 2026-05-08

### Changed
- **Monolith decomposition (14 phases)**: `main.py` split from 11 190 → ~1 500 lines (−86 %). Every domain concern now lives in a focused module:
  - `config.py`, `state.py`, `prompts.py`, `metrics.py` for cross-cutting state and constants
  - `routes/` (11 modules) for FastAPI endpoints — all decorated handlers extracted from `main.py`
  - `services/` (12 modules incl. `services/pipeline/`) for business logic — auth, tracking, routing, healer, inference, helpers, skills, kafka, llm_instances, plus a 4-file pipeline subpackage (chat / anthropic / ollama / responses)
  - `graph/` (6 modules) for LangGraph nodes — router, tools, planner, expert, research, synthesis
- **Generic infrastructure naming**: removed setup-specific identifiers (`AIHUB_*`, `N04_*`) from variable names. Hardcoded retry constants moved to env vars (`ENDPOINT_RETRY_COUNT`, `ENDPOINT_RETRY_DELAY`, `ENDPOINT_DEGRADED_TTL`); URL-pattern matching now driven by `EXTERNAL_ENDPOINT_PATTERNS`. Legacy env var names (`AIHUB_FALLBACK_*`) kept as backward-compat aliases.
- All 195 tests remain green throughout the decomposition — no behavioural change.

### Removed
- ~600 lines of dead route handler duplicates that survived from the original monolith (`benchmark_unlock`, `get_knowledge_stats`, `get_planner_patterns`, `pipeline_log`, all 9 `ollama_*` handlers — already implemented in `routes/`).

### Fixed
- Variable shadowing bug in regex parsing: `for _m in re.finditer(...)` shadowed the `import main as _m` alias. Renamed loop variable to `_match`.
- Several lifespan-task references (`_auto_resume_dedicated_healer`, `_watchdog_dedicated_healer`) and pipeline references (`stream_response`, `_is_openwebui_internal`, `_handle_internal_direct`, `_stream_native_llm`) were called as bare names without imports — would have raised `NameError` at runtime. Added explicit imports / lazy imports.

---

## [2.3.0] - 2026-05-05

### Added
- **Claude Desktop & Cowork Gateway compatibility** (Anthropic Third-Party Inference spec):
  - `display_name` field in all `/v1/models` entries — Claude Desktop displays human-readable names (e.g. "Claude Sonnet 4.6 → MoE (Gateway)") in the model picker
  - `POST /v1/messages/count_tokens` endpoint — satisfies the Anthropic Gateway spec requirement; returns a character-based token estimate (chars / 3.5) used by Claude Code for context-budget warnings
  - `X-Claude-Code-Session-Id` header now recognised in `_extract_session_id` (highest priority) for accurate session tracking from Claude Desktop
  - `_model_display_name()` helper with curated pretty-names for all current Claude model IDs
  - `scripts/setup-claude-desktop.sh` — interactive setup wizard that auto-configures `claude_desktop_config.json` on macOS, Linux, and WSL; checks connectivity, creates backup, merges gateway block

### Changed
- `/v1/models` response: all model objects now include the `display_name` field (templates, MoE modes, CC aliases, native node models, user connections)

---

## [2.2.0] - 2026-04-25

### Added
- **8 new MCP tools** (51 total): `wikidata_sparql`, `pubmed_search`, `crossref_lookup`, `openalex_search`, `duckduckgo_search`, `web_browser` (Splash JS rendering), `wayback_fetch`, `openalex_search`
- **`github_search_issues` fuzzy label resolution**: auto-resolves `Regression` → `05/06 - Regression` by fetching repo labels; OR-semantics across all matching variants
- **`openalex_search` year_max parameter** for precise publication year filtering
- **`wayback_fetch` dual-strategy**: primary availability API + direct URL fallback for reliable archive.org access
- **Pre-run smoke test** (`benchmarks/pre_run_check.py`): 8-point health check before benchmark runs — container health, MCP tools, prompt length, Redis, AST syntax, E2E question
- **HEALTHCHECK for mcp-precision**: Docker healthcheck via `calculate('2+2')` in compose.yml
- **Domain-filtered tool descriptions**: `_TOOL_GROUP_CORE` and `_TOOL_GROUP_RESEARCH` control which tools the planner sees per query type
- **Level-adaptive benchmark timeouts**: L1=300s, L2=480s, L3=900s via `GAIA_TIMEOUT_L{1,2,3}` env vars
- **Security hardening**: SSRF guard, SQL identifier validation, container `cap_drop: ALL` + `no-new-privileges`

### Changed
- **GAIA Benchmark**: Best score 14/30 = 46.7% (exceeds GPT-4o Mini 44.8% reference)
- **Expert-leak detection** (`_EXPERT_LEAK_RE`): broader patterns — `attempt tool call`, `attempt to search/browse`, capability disclaimers in merger fallback
- **Planner prompt** compressed from 15K → 12K chars to prevent context overflow; consolidated deterministic source rules
- **Benchmark normalization**: slugline stripping (`INT. X – DAY` → `X`), `grave accent` → `backtick`, leak phrases in NO_ANSWER retry

### Fixed
- `wayback_fetch`: HTTP 200 `archived_snapshots: {}` no longer silently fails — falls back to direct `web.archive.org/web/2024/{url}` with `follow_redirects=True`
- `github_search_issues`: label name prefixes like `06 - Regression` resolved automatically
- Expert-leak `Attempt web search.` / `Attempt tool call.` now triggers runner retry
- Merger fallback excludes expert-leak answers from fallback pool

## [2.1.0] - 2026-03-29

### Added
- **Two-Tier Expert System**
  - `_infer_tier(model_name)`: extrahiert Modellgröße aus Name (`:14b`, `:32b`) → T1 ≤20B, T2 >20B
  - T1-Experten laufen zuerst (schnell); T2 nur wenn kein T1-Ergebnis `KONFIDENZ: hoch` liefert
  - Spart VRAM bei einfachen Anfragen, eskaliert zu Premiumberechnung bei Bedarf
- **Chat-History-Injection**
  - `_truncate_history()`: letzte 4 Gesprächsrunden, max. 3000 Zeichen
  - Alle Expert-LLMs erhalten den Gesprächskontext vor ihrer Aufgabe
  - `chat_history`-Feld im `AgentState` und API-Layer (extrahiert aus `request.messages`)
- **Citation Tracking**
  - `_web_search_with_citations()`: nutzt `search.results()` statt `search.run()`
  - Strukturierte Quellenangaben (Titel + URL) werden nummeriert an Web-Recherche-Ergebnis angehängt
  - Gilt für `research_node` und `research_fallback_node`
- **`thinking_node`** — neue Node zwischen `research_fallback` und `merger`
  - Aktiviert bei komplexen Plänen (>1 Task) oder wenn Experten `KONFIDENZ: niedrig` melden
  - Magistral:24b führt expliziten 4-Schritt Chain-of-Thought durch:
    Problemzerlegung → Quellenauswertung → Wissenslücken → Schlussfolgerung
  - Ausgabe als `reasoning_trace` fließt priorisiert in den Merger-Prompt
  - Erscheint im `<think>`-Panel von Open WebUI
- **Expert-Deduplication**
  - `_dedup_by_category()`: behält pro Kategorie nur das Ergebnis mit höchster Konfidenz
  - Verhindert Echo-Chamber wenn mehrere Experten derselben Kategorie widersprüchliche Antworten liefern
- **`critic_node`** — neue Node nach `merger`, vor `END`
  - Aktiv ausschließlich für `medical_consult` und `legal_advisor` (safety-critical)
  - Judge-LLM prüft Merger-Antwort auf faktische Fehler und gefährliche Aussagen
  - Bei `BESTÄTIGT`: Antwort unverändert; bei Fehler: korrigierte Version wird zurückgegeben
- **Neue Helper-Funktionen:** `_infer_tier`, `_dedup_by_category`, `_truncate_history`, `_web_search_with_citations`
- **`AgentState`** um `chat_history: List[Dict]` und `reasoning_trace: str` erweitert

### Changed
- **Graph-Topologie:** `research_fallback → thinking → merger → critic → END`
  (vorher: `research_fallback → merger → END`)
- **`expert_worker`:** Two-Tier-Logik ersetzt einfaches paralleles Fan-out; Chat-History in Experten-Messages
- **`merger_node`:** ruft `_dedup_by_category()` vor Prompt-Aufbau; `reasoning_trace` als priorisierter Abschnitt
- **`research_node` / `research_fallback_node`:** Citation-Tracking via `search.results()`
- **Judge-LLM:** `magistral:24b` (vorher: `gpt-oss:20b`) — Reasoning-fokussiert, 24B

### Expert-Modell-Upgrades

| Kategorie | Alt | Neu | Begründung |
|---|---|---|---|
| `general` [2] | `qwen3.5:27b` | `qwen3.5:35b` | +8B, tieferes Reasoning |
| `math` [2] | `mathstral:7b` | `qwq:32b` | T2-Eskalation mit echtem Math-Reasoning |
| `code_reviewer` [2] | `codestral:22b` | `qwen3-coder:30b` | Neuer, größer, stärkere Security-Analyse |
| `medical_consult` [2] | `gemma3:12b` | `gemma3:27b` | T2-Eskalation ergänzt (safety-critical) |
| `legal_advisor` [2] | `mistral-small:24b` | `command-r:35b` | Citation-aware RAG, §§-Zitierung |
| `translation` | solo | + `qwen3.5:35b` | Zweite Meinung, multilinguales Training |
| `reasoning` [1] | `magistral:24b` | `deepseek-r1:32b` | Kein Echo-Chamber mit Judge; echtes CoT |

- `deepseek-r1:32b` (19.9 GB) und `qwq:32b` (19.9 GB) via Ollama API gepullt

---

## [2.0.0] - 2026-03-29

### Added
- **Kafka-Integration** (`confluentinc/cp-kafka:7.7.0`, KRaft-Mode, kein Zookeeper)
  - Topic `moe.ingest`: GraphRAG-Ingest aus Fire-and-Forget zu persistentem Kafka-Event
  - Topic `moe.requests`: Vollständiger Audit-Log aller Anfragen und Cache-Hits
  - Topic `moe.feedback`: Feedback-Events für externe Consumer
  - `AIOKafkaProducer` mit 12-facher Retry-Logik (Backoff 5–60s)
  - `AIOKafkaConsumer` als permanenter asyncio-Background-Task (Group `moe-worker`)
  - Graceful Degradation: System funktioniert vollständig ohne Kafka
- **GraphRAG / Neo4j Knowledge Graph**
  - `GraphRAGManager`: async Neo4j-Client, 2-Hop-Traversal, Kontext-Abfrage
  - Basis-Ontologie: 104 Entitäten, 100 Relationen über 4 Domänen (Medical, Legal, Technical, Math)
  - Hintergrund-Ingest: Tripel-Extraktion aus Merger-Antworten via Judge-LLM
  - Konflikt-Detektion via `_CONTRADICTORY_PAIRS` (TREATS/CAUSES/CONTRAINDICATES)
  - `graph_rag_node`: paralleler LangGraph-Node, 2-Hop-Traversal je Anfrage
  - Domain-Filter: `AND e.type IN $allowed_types` in Cypher (verhindert Cross-Domain-Kontamination)
  - API-Endpunkte: `GET /graph/stats`, `GET /graph/search`
- **MCP Precision Tools Server** (`mcp_server/server.py`, Port 8003)
  - 16 deterministische Tools: calculate, solve_equation, date_diff, date_add, day_of_week,
    unit_convert, statistics_calc, hash_text, base64_codec, regex_extract, subnet_calc,
    text_analyze, prime_factorize, gcd_lcm, json_query, roman_numeral
  - Safe-AST-Evaluator für `calculate` (kein `eval()`)
  - REST-Shim (`POST /invoke`) für internen LangGraph-Zugriff
  - FastMCP SSE-Endpoint (`/mcp/sse`) für externe MCP-Clients
- **Selbstlern-Infrastruktur**
  - `POST /v1/feedback`: Rating 1–5, aktualisiert Expert-Scores, flaggt Cache-Einträge, verifiziert Neo4j-Tripel
  - Expert-Performance-Tracking in Redis (`moe:perf:{model}:{category}`, Laplace-Glättung)
  - Response-Metadaten in Redis (`moe:response:{id}`, TTL 7 Tage)
  - Cache-Lookup: 3 Kandidaten, überspringt `flagged=True`-Einträge
  - Expert-Sortierung nach Performance-Score (beste zuerst); Score < 0.3 → übersprungen
- **LangGraph Pipeline erweitert**
  - Neu: `mcp_node`, `graph_rag_node` (beide parallel im Fan-out)
  - Cache-Short-Circuit: Cosine-Distance < 0.15 → direkte Merger-Rückgabe
  - Expert-Output-Cap: `MAX_EXPERT_OUTPUT_CHARS=2400` (≈ 600 Tokens)
  - Stagger-Delay entfernt; VRAM-Semaphore reicht
  - `math_node`: aktiv nur wenn kein `precision_tools`-Task im Plan
  - `AgentState` um `response_id` und `expert_models_used` erweitert
- **Konfidenz-Scoring** (`KERNAUSSAGE / KONFIDENZ / DETAILS`-Format)
  - Experten strukturieren Output mit `KONFIDENZ: hoch | mittel | niedrig`
  - Merger berücksichtigt Konfidenz bei Quellpriorisierung
- **Research Fallback Node** — automatische Web-Recherche bei `KONFIDENZ: niedrig`
  - Für `medical_consult` und `legal_advisor` bei niedriger Konfidenz erzwungen
- **Output-Modi** — drei separate Modell-IDs für Open WebUI:
  - `moe-orchestrator`: vollständige Antworten (Standard)
  - `moe-orchestrator-code`: nur Quellcode, kein Fließtext
  - `moe-orchestrator-concise`: max. 120 Wörter
- **Token-Tracking** — akkumulierte `prompt_tokens` + `completion_tokens` über alle LLM-Calls
  - `_extract_usage()` aus `usage_metadata` (Ollama) oder `response_metadata`
  - Vollständige `usage`-Felder in Streaming- und Non-Streaming-Responses
- **`<think>`-Panel** für Open WebUI
  - `_progress_queue` via `contextvars.ContextVar` in alle Nodes vererbt
  - Fortschrittsberichte aus jedem Node erscheinen im „Denke nach"-Panel
  - SSE Keep-alive (`": keep-alive"`) verhindert Proxy-Timeouts
- **Open WebUI Internal-Request-Detection**
  - `_is_openwebui_internal()`: erkennt Follow-up/Title/Autocomplete-Prompts
  - `_handle_internal_direct()`: Fast-Path direkt zum Judge ohne MoE-Pipeline
  - Verhindert dass Open WebUI-interne Requests den Spinner nicht stoppen
- **Expert System-Prompts**: Rollenidentität pro Kategorie
- **`AsyncRedisSaver`**: LangGraph-Checkpoints persistent in Redis (`terra_cache`)
- **Streaming-Fix**: OpenAI-kompatibler Abschluss-Chunk mit `finish_reason: "stop"`
- **Experten-Modelle aktualisiert**: meditron/medllama2 (Llama-2-Basis) durch phi4:14b + gemma3:12b ersetzt; magistral:24b und devstral:24b für legal/code hinzugefügt; Kategorien `translation` und `reasoning` neu
- **GraphRAG Domain-Filter**: verhindert Cross-Domain-Kontamination (z.B. medizinische Entitäten bei technischen Anfragen)

### Changed
- `planner_node`: Prompt verschärft (strikteres Format), `_sanitize_plan()` validiert alle Task-Einträge
- `stream_response`: vollständig OpenAI-kompatibles Chunk-Format
- `chat_completions` (non-stream): vollständige Antwort mit allen OpenAI-Pflichtfeldern
- GraphRAG-Ingest: Von `asyncio.create_task` zu Kafka-Publish — persistenter, entkoppelt

### Fixed
- **Planner lieferte Strings statt Task-Dicts** → `_sanitize_plan()` fängt alle ungültigen Einträge ab
- **Neo4j `DEPRECATED_BY` + `UNRELATED_TO` Warnings** → aus `_CONTRADICTORY_PAIRS` entfernt
- **docker-compose doppelter `environment`-Block** → zusammengeführt

---

## [1.2.0] - 2026-02-12

### Added
- SymPy-Mathematik-Modul (`math_node.py`): Gleichungslösung, Vereinfachung, Ableitung, Integration

### Changed
- Expert Worker: verbesserter Stagger-Algorithmus `(t_idx * 3) + (e_idx * 1.5)` Sekunden
- GPU-Count dynamisch via Umgebungsvariable konfigurierbar
- Verbessertes Error-Handling mit spezifischer CUDA-OOM-Erkennung

### Fixed
- Checkpointer-Initialisierungsfehler (`_GeneratorContextManager`)
- Memory-Leak im Semaphore-Management des Expert Workers
- Mathematische Ausdrucks-Erkennung verbessert

---

## [1.1.0] - 2026-02-11

### Added
- LangGraph Multi-Model LLM-Orchestrierung (initiale Pipeline)
- Multi-Node GPU-Cluster-Support via Ollama
- Redis-Checkpoint-Persistenz für Graph-State
- ChromaDB Vektorspeicher für Knowledge-Caching
- SearXNG Web-Recherche-Integration
- Docker Compose Deployment
- OpenAI-kompatibler API-Endpunkt (`/v1/chat/completions`)
- Streaming-Response (SSE)

---

## [1.0.0] - 2026-02-10

### Added
- Projekt-Initialisierung mit LangGraph-Grundstruktur
- Kern-Nodes: cache_lookup, planner, expert_workers, merger
- Docker-Konfiguration
- Basis-Fehlerbehandlung und Logging
