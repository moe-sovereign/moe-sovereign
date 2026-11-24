# MoE Sovereign — implementation, deployment and documentation GAP report

Date: 2026-09-11. Evidence window: approximately 06:39–06:52 UTC.
Prepared by: Codex. Intended reviewer and implementation owner: Claude Code,
once assigned by the operator. Status: **review handoff; no remediation performed**.

This report answers whether the documented system matches the code and the
deployment on this host. The architectural direction is coherent, but the match
is incomplete: there are confirmed runtime/security defects, incomplete recovery
coverage, stale status records and overstated descriptions of some algorithms.

This is an internal review artifact, deliberately outside the public MkDocs
tree. It is evidence for review, not a replacement for AGENTS.md, product policy,
task ownership, or the operator's authorization. Revalidate each finding against
the then-current deployment before implementation; other sessions are active.

## 1. Scope and evidence baseline

Inspected: core API/graph/services, MCP boundaries, memory and learning paths,
backup/import implementation, deployment mounts, selected tests, repository
history, project instructions, AI memory, technical docs, roadmap, selected
whitepaper sections, and both public homepages. Codex, Libris and the inference
dashboard were inspected for architecture/context in the preceding session;
their complete deployments were not audited here.

| Item | Observed evidence |
| --- | --- |
| Development checkout | `/opt/deployment/moe-sovereign/moe-infra` |
| Local branch / HEAD | `main` / `266283d2`, merge of the WeasyPrint dependency update |
| Core container | `langgraph-orchestrator`, started `2026-09-11T06:39:40.701944427Z` |
| Core image ID | `sha256:941bbef67f24178ffc250835eabc6a4b260a5b6c30f3a9c4c4078207a5ca8516` |
| MCP image ID | `sha256:fa6e87c249df63b2e34605eca1570a472315dc8a508ade599ed63cab5024d55a` |
| Core published port | `0.0.0.0:8002` and `[::]:8002`, mapped to container port 8000 |
| MCP published port | `127.0.0.1:8003` |
| Readiness | HTTP success; graph, boundary contracts, Valkey, user DB, Neo4j, MCP and Chroma all `ok: true` |
| Targeted regression | **131 passed in 1.35s**, host Python 3.13.5 |
| Refinement reproduction | First test passed; fixture teardown hung; bounded run exited 124 |
| Git worktree | Dirty, including training changes and a staged `docker-compose.langfuse.yml` |
| Source/image identity | Seven selected core files had identical SHA-256 hashes; this is a sample, not complete image provenance |
| Image revision label | `org.opencontainers.image.revision` was empty |

Matched files: `main.py`, `routes/admin_backup.py`,
`routes/admin_knowledge_ingest.py`, `services/boundary_check.py`,
`services/sovereignty.py`, `services/graphrag/graph_clustering.py`, and
`services/quality_gate.py`.

The previous handoff's claim that backup/import work was uncommitted is now
superseded: commits `9f909419`, `356bf6f5` and merge `41b867ed` are in the local
history. Remote GitHub/GitLab heads were not refreshed during this review; do not
infer remote equality from the local log.

SessionMesh handoff and current-session membership were read. Its compact
handoff had no useful source HEAD and contained historical build notifications;
membership also included unrelated project sessions. Relevant local session
transcripts were cross-checked. SessionMesh was not treated as runtime authority.

## 2. Prioritized review queue

P0 = immediate runtime/security investigation; P1 = correctness, recovery or
release-proof issue; P2 = documentation/maintainability work. These are proposed
priorities, not a claim that every P1/P2 issue has an active production exploit.

| ID | Priority | Finding | Evidence level | Reviewer disposition |
| --- | --- | --- | --- | --- |
| GAP-01 | P0 | Streaming imports a missing Langfuse module | Reproduced from deployed code | Pending |
| GAP-02 | P0 | Administrative operations lack API authentication | Backup route confirmed live; related routes inspected | Pending |
| GAP-03 | P1 | Autobackup is a partial export, not proven system recovery | Confirmed code coverage gaps; restore not exercised | Pending |
| GAP-04 | P1 | Episodic retrieval lacks owner/tenant filtering | Confirmed query shape; cross-user impact needs isolated test | Pending |
| GAP-05 | P1 | Full `local_only` data-flow guarantee remains unproven | Contract/control mismatch requiring review | Pending |
| GAP-06 | P1 | Test lifecycle hangs and readiness misses a broken path | Reproduced locally / in deployed function | Pending |
| GAP-07 | P1 | Research algorithm names overstate implementation | Confirmed source semantics and call-site search | Pending |
| GAP-08 | P1 | Legacy placeholder training paths remain selectable artifacts | Confirmed source; active training use not rechecked | Pending |
| GAP-09 | P2 | Architecture, roadmap and memory describe conflicting states | Confirmed document/code/runtime differences | Pending |
| GAP-10 | P1 | Source-to-image and mutable-mount provenance is incomplete | Confirmed deployment metadata | Pending |
| GAP-11 | P2 | Request auth diagnostics log API-key prefixes | Confirmed source; log retention/exposure not audited | Pending |

## 3. Findings and acceptance criteria

### GAP-01 — Missing Langfuse dependency breaks orchestrated streaming

Evidence:

- [main.py](main.py), `stream_response`, around line 1897: unconditional
  `from services.langfuse_client import with_langfuse_callbacks`, before the
  first response chunk.
- In the running core container, `importlib.util.find_spec` returned `None` for
  both `services.langfuse_client` and `langfuse`.
- Isolating the deployed `stream_response` function from its AST and advancing
  its async generator reproduced:
  `ModuleNotFoundError: No module named 'services.langfuse_client'`.
  Only the chat-context helper was stubbed; no model or persistence call ran.
- A Langfuse service container is running, but its presence does not install
  the missing orchestrator integration. The optional integration branch
  `feature/langfuse-observability-v2` contains additional files absent from HEAD.

Impact: a request that reaches this streaming function cannot emit its first
chunk. This is not a claim that native passthrough or non-streaming requests
all fail. `/ready` did not detect it.

Proposed correction: reconcile the partial integration as a complete, optional
feature. Determine the intended enabled/disabled configuration before choosing
whether to complete the integration or remove the dangling call. A running
Langfuse web service alone is not evidence of trace ingestion.

Acceptance:

- [ ] Fresh core image imports every enabled API path without missing modules.
- [ ] A real `moe-auto` streamed request returns content and terminates cleanly.
- [ ] Disabled/unavailable optional tracing does not break responses.
- [ ] When tracing is enabled, a request can be correlated to an actual trace.
- [ ] Non-streaming and native modes remain functional.

### GAP-02 — Administrative API and tool proxy authorization gaps

Evidence:

- [routes/admin_backup.py](routes/admin_backup.py), `run_backup`, line 32:
  no identity or authorization dependency.
- [routes/admin_knowledge_ingest.py](routes/admin_knowledge_ingest.py),
  `trigger_document_ingestion`, line 22: no identity or authorization dependency.
- [main.py](main.py) includes both routers without shared auth dependencies;
  installed middleware provides headers, body limits, caching and CORS, not
  authentication.
- A live request to the backup endpoint without credentials, with
  `{"filename":"../invalid"}`, returned HTTP 400 `Invalid filename` from inside
  the handler. That validation precedes all file/database work, so this probe
  did not create a backup. It confirms handler reachability without identity.
- [routes/health.py](routes/health.py) also exposes `/invoke` and
  `/tools/{name}/toggle` without authentication in the proxy handlers. Their
  downstream authorization/effects need separate review; no tool was toggled or
  executed during this audit.

Impact: an actor who can reach the core port can reach administrative handlers.
The core binds all host interfaces. Public internet reachability and external
firewall/proxy restrictions were not established; neither should be assumed.
Protecting the Admin UI does not protect a separate orchestrator endpoint.

Proposed correction: define explicit admin/service permissions at the API
boundary and authenticate internal Admin-to-core calls. Inventory all routes,
including tool proxies and read endpoints that expose sensitive records. Keep
intentional public health/discovery surfaces explicitly documented.

Acceptance:

- [ ] Missing/invalid identity returns 401 before handler side effects.
- [ ] Valid identity without the required grant returns 403 or scoped 404.
- [ ] Authorized scheduled backup/import and permitted tool calls still work.
- [ ] Negative tests cover direct core access, not only Admin UI login.
- [ ] Rate limits and concurrency limits bound authorized expensive jobs.
- [ ] Deployment documentation states the actual port exposure and trust model.

### GAP-03 — Backup scope, success semantics and restore proof

Evidence:

- [routes/admin_backup.py](routes/admin_backup.py) exports only the Chroma
  collection `moe_template_cache`; the actual answer caches include
  `moe_fact_cache` and `moe_agent_cache`, and conversation memory uses a separate
  collection (`main.py`, `memory_retrieval.py`). It omits embeddings from that
  export as well; a rebuild procedure would need to account for this.
- Its Valkey export reads string keys `moe:routing:feedback:*`, while expert
  performance/Thompson counters are hashes under `moe:perf:*`
  (`services/inference.py`, `services/dynamic_router.py`).
- `GraphRAGManager.export_knowledge_bundle`, around line 1595 in
  [graph_rag/manager.py](graph_rag/manager.py), is a community-bundle exporter.
  It filters entity sources to `ontology`, `extracted`, `ontology_gap_healer`
  and exports selected properties. It is not a complete Neo4j snapshot; Episode
  nodes and other node types/properties are not covered by that selection.
- [admin_ui/app.py](admin_ui/app.py), `_run_system_backup`, dumps
  `MOE_USERDB_URL`. A separate checkpoint database is not included by this path.
- The core endpoint can return `ok: true` with component failures in `warnings`.
  No restoration was attempted during this audit.

Impact: archive creation is not proof that the persistent learning state,
conversation memory and checkpoint state can be recovered after host loss.
Some caches can intentionally be excluded, but only with an explicit durable
source and a demonstrated rebuild procedure.

Proposed correction: define a per-store recovery contract first: authoritative
data, rebuildable projections, intentional exclusions, restore order, expected
loss window and recovery time. Use complete exports/snapshots where needed,
version the manifest and distinguish complete, partial and failed backups.

Acceptance:

- [ ] Inventory every required PostgreSQL database, Neo4j label/relationship,
  Chroma collection, Valkey namespace and persistent filesystem artifact.
- [ ] Each exclusion has a tested rebuild path or an accepted loss policy.
- [ ] Restore into isolated fresh stores and compare counts, ownership,
  provenance and representative retrievals, including corrections/episodes.
- [ ] Restore proof covers failure, partial archive and corrupted archive cases.
- [ ] Failed components cannot produce an undifferentiated successful backup.
- [ ] Retention retains a usable recovery set; backup concurrency is bounded.

### GAP-04 — Episodic memory is not consistently scoped by identity

Evidence: [episodic_memory.py](episodic_memory.py), `_STORE_EPISODE`,
`_QUERY_EPISODES`, `_QUERY_EPISODES_FALLBACK`, `_episode_hash` and
`get_episode_hint` (around lines 57, 77, 98, 120 and 244).
Episodes store `user_id`, but both retrieval queries filter only task type,
confidence, expiry and possibly similarity. The deduplication key omits owner
and tenant. `get_episode_hint(driver, query, task_type)` accepts no identity.

Impact: user attribution on write does not establish retrieval isolation.
Returned hints contain routing/tools/model metadata rather than the complete
past answer; cross-scope influence still requires review. No private records
were read and no cross-user production request was sent to demonstrate leakage.

Proposed correction: make intended private/shared episode semantics explicit;
carry validated principal/scope through keys, writes, reads and fallback queries.
Align this with E-2.5 rather than treating one field addition as full tenancy.

Acceptance:

- [ ] Two synthetic tenants with identical query/task text cannot collide or
  retrieve each other's private episode metadata.
- [ ] Missing scope fails closed; non-APOC retrieval preserves the same scope.
- [ ] Shared knowledge requires explicit policy, ownership and provenance.
- [ ] Existing unscoped data has a reviewed migration/quarantine strategy.

### GAP-05 — Prove the full `local_only` contract, including indirect egress

Evidence: [PROJECT_COMPLIANCE.md](PROJECT_COMPLIANCE.md) forbids non-local
egress of prompts, context, embeddings, tool arguments, memory and derived
content. [services/sovereignty.py](services/sovereignty.py) primarily classifies
an endpoint hostname by private IPs or exact allowlist. Multiple LLM dispatch
paths call it, and focused tests pass. Those facts do not establish every
research, embedding, tool, memory-writeback or redirect path.

A local proxy can forward to a remote provider while passing an address-locality
test. Entropy testing is not an authorization/provenance mechanism: a low-entropy
payload may contain private data, and the optional payload argument is not proof
that every dispatch supplies it.

This is a **verification gap**, not a reproduced production data leak. The audit
has not established the configured egress policy of every local proxy.

Proposed correction: inventory every outbound boundary, propagate the frozen
request policy, and define locality using provider/data-flow configuration as
well as destination checks. Test configured redirects and local relay providers.

Acceptance:

- [ ] Negative egress tests cover planner, experts, judge, native/agent paths,
  research, embeddings, precision/generative tools and asynchronous writeback.
- [ ] A local relay to a non-local provider cannot silently satisfy `local_only`.
- [ ] Unknown locality and forbidden redirects fail closed before payload egress.
- [ ] Offline/local-only documentation states which features are unavailable.

### GAP-06 — Test teardown and deployment checks leave important blind spots

Evidence:

- The previous full-suite run stopped progressing around expert refinement and
  was interrupted; its historical green count was not reproduced.
- Today's isolated bounded run of `tests/test_expert_refinement.py` passed its
  first assertion test, then hung during pytest-asyncio fixture teardown.
  Faulthandler showed `asyncio.runners.Runner.close` waiting in the event loop.
  Result: `1 passed in 14.19s`, shell timeout exit 124. Root cause is not yet
  established; this may depend on the host Python/plugin combination.
- The test stubs audited model inference, but `_refine_expert_response` also
  makes an HTTP `/api/ps` request. Its lifecycle and mocking need investigation.
- `/ready` is green while GAP-01 is reproducible. The current checks establish
  infrastructure readiness, not correctness of every protocol path.
- `tests/conftest.py` heavily stubs framework/storage dependencies. Mock-based
  graph wiring tests cannot alone establish installed-image API behavior.

Proposed correction: diagnose leaked tasks/resources and isolate all network
activity in unit tests. Add installed-image smoke tests for enabled API modes;
keep readiness inexpensive and separate from scheduled inference canaries.

Acceptance:

- [ ] Refinement tests and the relevant full suite terminate normally on a
  documented supported Python/dependency set, with no leaked tasks/threads.
- [ ] The missing-module case fails a build/deployment check before rollout.
- [ ] Streaming/non-streaming, native/orchestrated and degraded optional
  integration cases have representative API smoke coverage.
- [ ] Reports distinguish unit, integration, readiness and model-E2E evidence.

### GAP-07 — Algorithm labels and public claims exceed the demonstrated code

Confirmed examples:

| Claimed/implied mechanism | Actual inspected implementation |
| --- | --- |
| `compute_leiden_communities` / Leiden or Louvain | Breadth-first traversal of connected components; no modularity optimization or Leiden refinement |
| Hierarchical community summaries | Fixed level 1 and a formatted string of member names; inspected references are tests and the module's own demo |
| `run_dspy_teleprompter_gate` | Three dictionary checks; no DSPy optimizer invocation in this function |
| `evaluate_program_sketch` / `smt_bounds`, `unsat_core` | Local min/max, enum and default-value checks; no SMT solver or cross-variable constraint solving in this function |

Sources: [services/graphrag/graph_clustering.py](services/graphrag/graph_clustering.py),
[services/quality_gate.py](services/quality_gate.py), associated tests and the
DE/EN public homepages (`../moe-web/index.html`, `../moe-web-int/index.html`).
The German homepage's research cards mention formal SMT guarantees and
teleprompter optimization/mathematical protection against egress. Some content is
status-labelled research, but the concrete capabilities still need narrowing.

Impact: trivial passing tests can validate the substitute implementation while
not validating the named algorithm. This report does not claim that every
solver-related component elsewhere in the project is absent.

Proposed correction: rename and document the actual mechanism now; separately
scope real research implementation when justified. Do not introduce dependencies
solely to make an existing label true.

Acceptance:

- [ ] Names, diagrams and claims match observable behavior in both locales.
- [ ] Tests distinguish the promised algorithm from a trivial substitute (for
  example, multiple communities inside a connected graph).
- [ ] Production call sites and benchmark limits are recorded for each claim.
- [ ] Research hypotheses remain visibly distinct from implemented guarantees.

### GAP-08 — Prevent legacy placeholder generators from re-entering training

Evidence: [scripts/generate_expert_ensemble_datasets.py](scripts/generate_expert_ensemble_datasets.py)
contains repeated hardcoded prompt/answer patterns.
[slurm/lumig_job1_dataset_gen.slurm](slurm/lumig_job1_dataset_gen.slurm), around
line 95, still writes `Synthetic CoT reasoning prompt {i}` records with literal
`gbnf_valid: True` and `smt_proof: SAT` without performing those validations.
This corroborates the failure pattern described in
[the September postmortem](docs/experiments/antigravity_frontier_pipeline_postmortem.md).

New teacher generation, diversity filters, dataset merge, delimiter cleanup and
Loom verification work exists. This review did not query LUMI's scheduler or
inspect current remote training artifacts; it does not assert that current jobs
are consuming the old data. Uncommitted training work is owned by other sessions.

Proposed correction: explicitly quarantine obsolete generators from runnable
production paths and require a dataset manifest/provenance gate at training
submission. Dataset size, a filename or an exit code is insufficient evidence.

Acceptance:

- [ ] Every role's active input file is resolved unambiguously and hash-pinned.
- [ ] Gates reject placeholders, delimiter leakage, fabricated proof labels,
  duplicate collapse and structurally invalid role targets.
- [ ] Teacher provenance, transformations, exclusions and validation results
  survive merge and export.
- [ ] Held-out evaluation checks grounding, plan coverage and tool arguments;
  evaluation prompts are separated from training inputs.
- [ ] Legacy fixtures cannot be selected by broad filename globs or defaults.

### GAP-09 — Documentation and memory need reconciliation, not blanket rewriting

| Source | Mismatch with inspected state | Required resolution |
| --- | --- | --- |
| `docs/backlog/current/roadmap.md`; `PROJECT_COMPLIANCE.md` COMP-01 variance | Required boundary validation still described as fail-open/open work; code now raises `BoundaryConfigurationError`, readiness checks it, focused tests pass | Revalidate all required callers, then close the stale variance with evidence |
| `docs/system/architecture.md` (June) | Old graph shape and 16-tool inventory omit precision preflight, final binding, response commit and later nodes | Generate topology/tool inventory from runtime contracts and explain the current sequence |
| Claude `MEMORY.md` IMoE index | Index calls bugs C/D open; linked detail says fixed; current prompt-cache text and feedback metrics call sites support the fixes | Correct the compact index after reviewer verification; avoid reopening completed work |
| Claude volume-mount memory | Says scripts need rebuild and services are live-mounted; core now mounts scripts and bakes main/services/graph into the image | Document mount policy separately for core and Admin UI |
| Claude Langfuse memory | Says no Langfuse service is running; service exists, but core integration is incomplete (GAP-01) | Distinguish service deployed, SDK installed, hooks wired, and traces verified |
| `docs/system/memory.md` | References old `main.py` node ownership and background writes; current graph has post-quality `response_commit` | Update call sites and clarify persistence guarantees/partial failures |
| Public deployment copy | Mentions multi-tenant high load while E-2.5 remains unproven and GAP-04 exists | Label target architecture separately from validated tenant isolation |
| Auto-generated `docs/system/status.md` | Container/metric snapshot is not an API, security, recovery or source-parity validation | Display timestamp/version and separate health from capability proof |

References: [roadmap](docs/backlog/current/roadmap.md),
[architecture](docs/system/architecture.md), [memory architecture](docs/system/memory.md),
[AI restore status](docs/ai-memory/07-current-status-and-next-work.md).
Claude memory lives under
`/home/philipp/.claude/projects/-opt-deployment-moe-sovereign/memory/` and was not
modified in this review. Access/write permissions for that directory must be
handled by the reviewing agent's environment.

Acceptance:

- [ ] A single dated capability matrix distinguishes implemented, E2E-validated,
  degraded, planned and research status, with source/test evidence.
- [ ] Operator memory points to current evidence without overwriting history.
- [ ] DE/EN sites and whitepaper claims are reconciled where semantics changed.
- [ ] Governance checks and documentation/link checks pass for the changes.

### GAP-10 — Deployment is not fully traceable to an immutable source snapshot

Evidence: the core image has no OCI revision label. Seven source hashes matched,
but the worktree is dirty and mutable mounts remain. Core scripts, models and
data are mounted; Admin UI additionally mounts `app.py`, `database.py`, services,
templates and languages. Mounted Python source can differ from code already
imported into a running process. The presence of matching files on disk alone
does not establish the Admin process's loaded code version.

Impact: commit, working tree, build inputs and active runtime can diverge. This
complicates rollback, model-output attribution and confirmation that all fixes
are both merged and deployed. No complete reproducible-release proof is claimed.

Proposed correction: produce a release manifest with commit, dirty-source hash
or clean-worktree requirement, image digest, configuration schema/version,
model/template IDs and persistent mounts. Separate intentional development mounts
from immutable release deployment. Never include secrets in manifests.

Acceptance:

- [ ] Running API exposes a non-secret build identity correlated to image digest.
- [ ] Build inputs include both tracked and relevant untracked/mounted artifacts.
- [ ] Fresh deployment and rollback preserve a documented compatible data state.
- [ ] Remote equality, if claimed, is checked against fresh remote refs.

### GAP-11 — Remove credential-derived request diagnostics

Evidence: [services/pipeline/chat.py](services/pipeline/chat.py), around lines
1657–1663, slices the first ten characters of an API key and logs them at warning
level with other auth diagnostics. This is a prefix, not a full-key disclosure;
no secret values were collected for this report.

Proposed correction: retain useful auth reason/source/request identifiers, but
replace raw credential fragments with a safe key record ID or a deliberately
designed non-reversible identifier. Review historical log handling before any
cleanup; do not delete logs or rotate keys based on this report alone.

Acceptance:

- [ ] Synthetic-key tests show neither key nor raw prefix in success/failure logs.
- [ ] Authentication troubleshooting remains possible through safe identifiers.
- [ ] Retention/access policy for existing diagnostics is explicitly reviewed.

## 4. Verification record and reproducible checks

The following commands are diagnostic. Run from the repository root, with the
environment's applicable permissions. They are not deployment commands.

### Readiness and installed dependency check

```sh
curl -fsS --max-time 15 http://127.0.0.1:8002/ready
docker exec langgraph-orchestrator python3 -c 'import importlib.util; print(importlib.util.find_spec("services.langfuse_client")); print(importlib.util.find_spec("langfuse"))'
```

Observed: all seven readiness checks successful; both module specs `None`.
The deployed-function reproduction extracted only `stream_response` using AST,
stubbed the request-context helper, and advanced the generator once. It failed
at the missing import before inference. Repeat through a real API smoke test
after remediation; the isolated check is not a full API E2E test.

### Authentication check without backup side effects

First verify that invalid filename validation still precedes all writes in the
current handler; only then reuse this diagnostic request:

```sh
curl -sS --max-time 15 -w '\nHTTP %{http_code}\n' \
  -H 'Content-Type: application/json' \
  -d '{"filename":"../invalid"}' \
  http://127.0.0.1:8002/v1/admin/backup/run
```

Observed: HTTP 400, `{"detail":"Invalid filename"}`, without credentials.
Expected after auth remediation: authentication rejection before this handler.
No valid backup request, ingestion trigger, tool toggle or restore was sent.

### Targeted regression

```sh
PYTHONDONTWRITEBYTECODE=1 timeout 45s python3 -m pytest -q -p no:cacheprovider \
  tests/test_boundary_check.py tests/test_sovereignty.py tests/test_deadline.py \
  tests/test_response_commit.py tests/test_precision_preflight.py \
  tests/test_precision_response.py tests/test_precision_rollout.py \
  tests/smoke/test_graph_wiring.py tests/test_graph_rag_import_relations_created.py \
  tests/test_chat_endpoint_v1_prefix.py tests/test_generate_diverse_training_seeds.py
```

Observed: `131 passed in 1.35s`. This supports those test contracts, not full
multi-tenant isolation, model quality or production readiness.

### Bounded refinement teardown reproduction

```sh
PYTHONDONTWRITEBYTECODE=1 timeout --signal=INT --kill-after=5s 15s \
  python3 -m pytest tests/test_expert_refinement.py -vv -p no:cacheprovider \
  -o faulthandler_timeout=5
```

Observed environment: Python 3.13.5, pytest 9.0.3, pytest-asyncio 1.3.0,
asyncio strict mode. First test passed; teardown stalled in
`asyncio.runners.Runner.close` / pytest-asyncio `_scoped_runner`. Exit 124.
The failing lifecycle must be diagnosed; the trace does not identify a specific
leaked task or prove the production service has the same problem.

## 5. Suggested Claude Code workflow

1. Refresh SessionMesh, local Git status, nearest instructions and active owner
   logs. Reinspect container start times/digests; this snapshot was taken while
   another session was deploying. Do not overwrite its training changes.
2. Reclassify each row as confirmed, superseded, not reproduced or still open.
   Record concise evidence and a date. An old finding is not an instruction to
   reintroduce an already fixed issue.
3. Prioritize GAP-01 and GAP-02. Resolve GAP-06 early enough that subsequent
   acceptance tests terminate reliably. Investigate GAP-03/04/05 next.
4. Convert accepted implementation work into the project's appropriate backlog
   sheets and owner/status entries; link GAP IDs. This report is a review queue,
   not an automatically claimed Lastenheft task or a release authorization.
5. Use separate cohesive changes for runtime imports/auth, recovery/isolation,
   dataset controls and documentation. Preserve existing precision binding and
   post-quality persistence contracts.
6. For each implemented fix, prove negative paths, then relevant integration and
   API behavior. Record source/image identity and rollback when deploying within
   operator-authorized scope. Follow the existing feature-branch/review policy.
7. Update this report's dispositions and durable SessionMesh tasks/decisions with
   confirmed outcomes. Do not copy secrets, raw private prompts or reasoning.

No application code, configuration, database contents, training job, deployed
service or external repository was changed by the report author. Diagnostic
requests/tests may produce ordinary operational logs. This file is the handoff
artifact; historical full-suite and training success claims remain historical
until independently reproduced.
