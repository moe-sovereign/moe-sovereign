# MoE Codex — EU Data Intelligence Extension

> **Document type:** Technical companion document  
> **Status:** Current as of May 2026  
> **Scope:** Architecture, modules, use cases, and deployment of MoE Codex as an
> optional extension to MoE Sovereign

---

## Executive Summary

MoE Sovereign is a sovereign, self-hosted LLM orchestration platform that covers
95 % of enterprise AI use cases out of the box: intelligent expert routing, GraphRAG
knowledge accumulation, 51 deterministic MCP tools, and a 1-million-token semantic
memory layer — all without mandatory cloud calls.

The remaining 5 % of use cases occur in regulated sectors (government, healthcare,
banking, pharma, critical infrastructure) where proof of data provenance, approval
workflows, reproducibility, and machine-readable audit trails are required by law.
These operators need more than an LLM gateway; they need a data intelligence platform.

**MoE Codex is that platform.** It is a fully open-source (Apache 2.0), air-gap-capable
data management stack that deploys alongside an existing MoE Sovereign installation
without touching the core configuration. It is architecturally inspired by Palantir
Foundry but built entirely on open standards: OpenLineage, lakeFS, Apache NiFi,
Trino, Apache Superset, Open Policy Agent, and OpenSearch.

Key facts:

- **25 route modules** covering catalog, lineage, versioning, BI, investigation,
  pipelines, compliance, and document intelligence.
- **92 % coverage** of the Palantir Foundry / Gotham / AIP feature surface area,
  with 2 structural gaps (mobile tactical edge, commercial enterprise certifications).
- **EU-first compliance posture:** built against EU AI Act (Reg. 2024/1689),
  NIS2/NIS2UmsuCG, DSGVO Art. 35, and BSI Grundschutz/C5.
- **Zero vendor lock-in:** Apache 2.0, no telemetry, no SaaS dependency, fully
  auditable codebase.

---

## Architecture

### Overall Stack Position

MoE Sovereign operates as the LLM core. MoE Codex is an optional data intelligence
layer that sits beneath it, receiving OpenLineage events and exposing data assets to
the orchestrator via REST. MoE Libris is a separate optional federation hub for
multi-cluster knowledge exchange.

```
┌─────────────────────────────────────────────────────────────────┐
│                    Clients (Open WebUI · Claude Code · SDK)     │
└───────────────────────────┬─────────────────────────────────────┘
                            │  OpenAI / Anthropic API
┌───────────────────────────▼─────────────────────────────────────┐
│                 MoE Sovereign Core  (:8002)                     │
│  LangGraph orchestrator · 15 experts · 51 MCP tools            │
│  Neo4j GraphRAG · ChromaDB · Kafka · Admin UI :8088            │
└────────────┬──────────────────────────────────────┬────────────┘
             │  OpenLineage events                  │  Bundle import API
             │  REST API calls                      │
┌────────────▼──────────────────────────────────────▼────────────┐
│                 MoE Codex Extension  (:8090)  [optional]       │
│                                                                 │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │  Platform   │  │ BI &         │  │ Investigation│         │
│  │  Catalog    │  │ Analytics    │  │ Tools        │         │
│  │  Lineage    │  │ Superset     │  │ Link analysis│         │
│  │  Versioning │  │ Trino SQL    │  │ Timeline     │         │
│  │  lakeFS     │  │ MLflow       │  │ Dossier      │         │
│  └─────────────┘  └──────────────┘  └──────────────┘         │
│                                                                 │
│  ┌─────────────┐  ┌──────────────┐                            │
│  │  Pipelines  │  │ Security     │                            │
│  │  NiFi ETL   │  │ OPA policies │                            │
│  │  Kestra     │  │ Guardrails   │                            │
│  │  DocLing    │  │ OpenSearch   │                            │
│  └─────────────┘  └──────────────┘                            │
└─────────────────────────────────────────────────────────────────┘
             │  Knowledge bundles · Federation events
┌────────────▼────────────────────────────────────────────────────┐
│                 MoE Libris Federation Hub  [optional]           │
│  JSON-LD knowledge bundles · bilateral consent · trust scores   │
└─────────────────────────────────────────────────────────────────┘
```

### Design Principles

**Opt-in, non-invasive.** MoE Codex runs in a separate Docker Compose project. It
connects to the MoE Sovereign core via published API endpoints and does not require
changes to the core stack, its `.env`, or its Compose file.

**Event-driven lineage.** Rather than pulling data from the core, Codex receives
push events. The orchestrator emits OpenLineage events to Marquez on every knowledge
bundle import; Codex builds the lineage graph from these events passively.

**Approval-gated writes.** No external data enters the live knowledge graph without
explicit human approval. Incoming bundles land on a `pending/<tag>-<ts>` lakeFS branch
and are presented in the approval UI. An admin approves (triggering Neo4j import and
branch merge to `main`) or rejects (triggering branch deletion) — one click, full audit.

**Policy-as-code.** Every write request is evaluated by the Open Policy Agent before
the Codex API commits it. Compliance rules are Rego files checked into version control;
adding a new data classification rule requires no application deployment.

---

## Module Reference

### Platform — Core Data Infrastructure

| Module | Route | Backing Service | Description |
|---|---|---|---|
| **Data Catalog** | `/catalog` | Marquez + lakeFS + Neo4j | Cross-source asset browser: Marquez datasets, lakeFS repositories, and Neo4j knowledge domains in one searchable, filterable table |
| **Lineage** | `/lineage` | Marquez (OpenLineage) | End-to-end lineage graph from raw source through every NiFi ETL run to the final Neo4j entity; drilldown to run inputs and outputs |
| **Data Versioning** | `/versioning` | lakeFS | Git-style branches and commits for datasets; create snapshots before every training run or compliance audit |
| **Approval Workflows** | `/approval` | lakeFS + Neo4j | Human-in-the-loop gate: review pending lakeFS branches, inspect entity diffs, approve to merge to `main` or reject to delete the branch |
| **Data Health** | `/health` | Internal drift engine | Drift detection with 6 flag types (entity dedup suppressed, zero entities added, entity count shrank, etc.) and severity ladder: ok / info / warn / crit |
| **ML Experiment Tracking** | `/mlflow` | MLflow | Log training runs, hyperparameters, and metrics; associate each run with its lakeFS dataset branch for full reproducibility |

### BI & Analytics

| Module | Route | Backing Service | Description |
|---|---|---|---|
| **BI Dashboards** | `/superset` | Apache Superset | SQL-driven dashboards, drag-and-drop chart builder, pivot analysis; connects to all Trino-federated catalogs |
| **Federated SQL** | `/trino` | Trino | Query Postgres, lakeFS S3, and memory catalogs with a single SQL statement; no data movement |
| **Charts & Analytics** | `/charts` | Embedded chart engine | Embedded pivot and time-series visualisation of catalog and lineage data without leaving the Codex UI |
| **Time Series** | `/timeseries` | TimescaleDB | Hypertable catalog with metric query and chart UI; window filters: 1d / 7d / 30d / 90d |
| **Workshop** | `/workshop` | Budibase | Visual low-code app builder for internal data tools; connects to Trino and Postgres directly |

### Investigation

| Module | Route | Description |
|---|---|---|
| **Graph Explorer** | `/explorer` | Read-only Cypher editor with two write-protection layers: regex blacklist + READ_ACCESS driver mode; preset queries and Neo4j Browser deep-link for ad-hoc investigation without live-graph risk |
| **Link Analysis** | `/graph_viz` | Cytoscape.js interactive graph: visualise entity relationships, filter by type, expand neighbourhood, export for reports |
| **Timeline** | `/timeline` | vis-timeline.js unified event stream — Marquez lineage runs, lakeFS commits, drift events, NiFi executions — with 1d / 7d / 30d / 90d window |
| **Geospatial** | `/geo` | PostGIS + KeplerGL: GeoJSON layer browser, point-in-polygon filter, bounding-box query; overlay ontology objects on a live map |
| **Dossier** | `/dossier` | Investigation container: pin graph entities, timeline events, geo features, notes, and alerts into a structured case file; Redis-backed for persistence |

### Pipelines

| Module | Route | Backing Service | Description |
|---|---|---|---|
| **ETL Automation** | `/etl` | Apache NiFi | Visual drag-and-drop ETL canvas; 300+ processors; `ListenHTTP` fan-out for bundle submissions; every run emits an OpenLineage event to Marquez |
| **Pipeline Builder** | `/kestra` | Kestra | YAML-based workflow orchestration; namespace/trigger support; lighter-weight NiFi alternative for scheduled pipelines |
| **Document Intelligence** | `/documents` | DocLing | OCR, layout analysis, and entity extraction from PDFs, scans, and office documents; extracted entities are submitted to the catalog |
| **Notebook** | `/notebook` | JupyterLab | Full server-side Python kernel (scipy / pandas / numpy); 5 pre-built API snippet notebooks for catalog, lineage, graph query, Trino SQL, and approval writeback |
| **Forms** | `/forms` | JSONForms | Schema-driven data entry with validation; used for compliance risk assessments, DPIA forms, and bundle submission templates |
| **Notes** | `/notes` | HedgeDoc | Real-time Markdown collaboration for investigation reports, compliance memos, and team knowledge bases |

### Security & Compliance

| Module | Route | Backing Service | Description |
|---|---|---|---|
| **Policy Enforcement** | `/opa` | Open Policy Agent | Rego policy evaluation for every write request; policies version-controlled in `moe-codex/policies/`; reload without redeployment |
| **Guardrails** | `/guardrails` | NeMo Guardrails | Pattern-based content guardrails with quarantine; self-correction journal for audit; configuration at `guardrails/config.yml` |
| **Federated Search** | `/search` | OpenSearch | BM25 + vector search across catalog, lakeFS commit messages, Marquez events, and Kestra runs in a single query; multi-tenant capable |
| **Compliance Checks** | `/compliance` | Internal engine | EU AI Act, NIS2, DSGVO Art. 35, and BSI Grundschutz checklist evaluation against the live catalog and lineage data |
| **Eval Framework** | `/eval` | Internal benchmark runner | Structured evaluation of LLM pipeline outputs against ground truth; produces per-expert accuracy metrics stored in MLflow |

---

## Industry Use Cases

### Government & Public Authorities

**Applicable regulations:** EU AI Act Annex III (high-risk systems), NIS2/NIS2UmsuCG,
DSGVO Art. 35 DPIA, BSI Grundschutz.

**Deployment pattern:**

1. MoE Sovereign handles citizen-query routing through the `legal_advisor` and `general`
   expert specialists. All inference runs air-gap on-premise; no citizen data leaves the
   network.
2. MoE Codex adds the compliance infrastructure required under EU AI Act Article 9 (risk
   management) and Article 12 (record-keeping):
   - Every inference run that feeds a decision process emits an OpenLineage event to
     Marquez — traceable from raw citizen query to final administrative output.
   - lakeFS snapshots the input dataset version before every batch processing run,
     enabling exact replay for post-hoc audit.
   - OPA policies enforce data classification markings (e.g., `SCHUTZBEDUERFTIG`,
     `VERTRAULICH`) at the API layer without requiring application code changes.
   - The Compliance module generates a machine-readable EU AI Act conformity checklist
     from the live catalog and lineage graph.
3. MoE Libris (optional): federate legal knowledge bundles between municipal deployments
   — each authority remains autonomous; approved shared bundles accumulate in the local
   knowledge graph.

**Key modules used:** Lineage, Approval Workflows, Policy Enforcement (OPA), Compliance
Checks, Data Catalog, Data Versioning (lakeFS), Federated Search.

---

### Healthcare & Pharma

**Applicable regulations:** DSGVO, EU AI Act Annex III, MDR (for software as medical device),
GxP data integrity requirements, IDMP for pharmaceutical data.

**Deployment pattern:**

1. MoE Sovereign routes medical queries to the `medical_consult` and `science` experts.
   DocLing in the Codex pipeline pre-processes clinical documents (discharge summaries,
   lab reports, trial protocols) for entity extraction before they reach the knowledge
   graph.
2. Clinical trial reproducibility:
   - Each trial's input dataset is committed to a dedicated lakeFS repository branch
     before any ML model training run begins.
   - MLflow records the branch commit hash alongside every training run and its metrics —
     any run can be exactly reproduced by checking out the corresponding branch.
   - Marquez records the full lineage from raw clinical data source through every
     transformation to the final model artifact.
3. Compliance dashboard for data stewards: Superset dashboards connected to Trino
   aggregate lineage event counts, approval queue depth, drift alert counts, and OPA
   policy violations into a single compliance control panel — refreshed on a schedule
   without manual reporting.
4. Geospatial module (optional): epidemiological data overlaid on regional maps via
   PostGIS + KeplerGL for public health analysis.

**Key modules used:** Document Intelligence (DocLing), Data Versioning (lakeFS),
ML Experiment Tracking (MLflow), Lineage (Marquez), BI Dashboards (Superset),
Compliance Checks, Data Health (drift detection).

---

### Banking & Compliance (Financial Services)

**Applicable regulations:** DSGVO Art. 35, BSI C5, DORA (Digital Operational Resilience Act),
EU AI Act (credit scoring = high-risk Annex III), EBA ML/AI guidelines.

**Deployment pattern:**

1. MoE Sovereign routes model-risk queries, regulatory interpretation requests, and
   compliance analysis to the appropriate expert ensemble. The `legal_advisor` expert
   retrieves relevant BGB/KWG/MaRisk paragraphs via MCP legal tools.
2. Audit trail infrastructure:
   - Every model run that informs a credit, fraud, or risk decision emits a full
     OpenLineage event chain: raw feature dataset → transformation → model output.
   - lakeFS stores the exact feature dataset version used for each model run with a
     signed commit reference — satisfying EBA requirements for model auditability.
   - OPA enforces the four-eyes principle on high-risk decisions: approval workflows
     require two distinct reviewer approvals before a dataset can be promoted to `main`
     and used in production inference.
3. Cross-system investigation:
   - OpenSearch indexes all catalog assets, lineage events, and Kestra run logs.
   - The Dossier module enables compliance officers to build structured investigation
     files that pin relevant entities, timeline events, and evidence documents into a
     single case record.
   - The Link Analysis module visualises entity relationships (account — transaction —
     counterparty — risk flag) for anti-money-laundering and fraud investigation.
4. DORA operational resilience: Prometheus + Grafana (from MoE Sovereign core) plus
   the Codex `/health` drift detection and Starfleet alert loop collectively satisfy
   DORA Article 11 ICT risk monitoring requirements.

**Key modules used:** Lineage (Marquez), Approval Workflows, Policy Enforcement (OPA),
Federated Search (OpenSearch), Dossier, Link Analysis, Timeline, BI Dashboards
(Superset + Trino), Compliance Checks.

---

## Deployment Guide

### Prerequisites

- A running MoE Sovereign installation (moe-infra `docker compose up -d`).
- Docker and Docker Compose on the same host (or a dedicated host with network
  access to the Sovereign API on port 8002).
- At least 16 GB RAM for the Codex stack (plus Sovereign's ~6 GB footprint).
- 100 GB free disk space for database volumes.

### Quick Start

```bash
# 1. Clone the Codex repository
git clone https://github.com/moe-sovereign/moe-codex.git /opt/moe-sovereign/moe-codex

# 2. Copy and configure environment
cp /opt/moe-sovereign/moe-codex/.env.example \
   /opt/moe-sovereign/moe-codex/.env
# → set SOVEREIGN_API_URL, SOVEREIGN_API_KEY, and storage paths

# 3. Create persistent storage directories
sudo mkdir -p /opt/moe-codex/{marquez-db,lakefs-db,mlflow-db,superset-db,kestra-db,opensearch-data}

# 4. Start the Codex stack
cd /opt/moe-sovereign/moe-codex
sudo docker compose up -d

# 5. Verify all services are healthy
sudo docker compose ps
curl http://localhost:8090/health
```

### Environment Variables

| Variable | Description | Example |
|---|---|---|
| `SOVEREIGN_API_URL` | Base URL of the MoE Sovereign orchestrator | `http://localhost:8002` |
| `SOVEREIGN_API_KEY` | API key for Sovereign → Codex service calls | (generate via Admin UI) |
| `MARQUEZ_URL` | Marquez lineage server endpoint | `http://moe-marquez:5000` |
| `LAKEFS_URL` | lakeFS API endpoint | `http://moe-lakefs:8010` |
| `TRINO_URL` | Trino coordinator URL | `http://moe-trino:8080` |
| `OPENSEARCH_URL` | OpenSearch endpoint | `http://moe-opensearch:9200` |
| `OPA_URL` | Open Policy Agent endpoint | `http://moe-opa:8282` |

### Connecting Codex to Sovereign

The orchestrator emits OpenLineage events automatically when the following variable
is set in the Sovereign `.env`:

```bash
OPENLINEAGE_URL=http://localhost:5000/api/v1/lineage
```

After adding this variable, restart the orchestrator:

```bash
cd /opt/moe-sovereign
sudo docker compose restart langgraph-orchestrator
```

Lineage events will appear in the Marquez UI and the Codex `/lineage` route within
seconds of the next knowledge bundle import.

### Selective Deployment

Not all Codex services need to be running. Comment out or remove services from
`docker-compose.yml` to reduce resource footprint. The Codex API gracefully degrades:
modules whose backing service is unavailable return a `503` with a clear error message
rather than crashing the entire API.

Minimal compliance-focused deployment (catalog + lineage + versioning only):

```bash
# Start only the core compliance services
sudo docker compose up -d moe-codex-api moe-marquez moe-marquez-db \
    moe-lakefs moe-lakefs-db moe-codex-db
```

---

## Palantir Comparison Summary

MoE Codex achieves **92 % coverage** of the combined Palantir Foundry / Gotham / AIP
feature surface area as of May 2026.

### Coverage by Area

| Palantir Product | Modules | Coverage | Notes |
|---|---|---|---|
| Foundry (Data Platform) | 24 | ~90 % | 2 out-of-scope (Slate custom JS, Code Repositories) |
| Gotham (Investigation) | 9 | ~89 % | 1 structural gap (mobile tactical edge) |
| AIP (AI Platform) | 9 | ~100 % | Exceeds in multi-model routing and deterministic tooling |
| Apollo (Deployment Mgmt) | — | ⚪ out of scope | Apollo manages Palantir's own SaaS; not applicable |

### Two Structural Gaps

| Gap | Reason | Mitigation |
|---|---|---|
| **Mobile tactical edge** (Gotham) | Requires hardened native mobile apps — not practical without a dedicated mobile team | Progressive Web App via the Codex web UI is functional in most enterprise mobile scenarios |
| **Commercial enterprise certifications** | BSI C5 attestation, SOC 2, ISO 27001 for the software itself require formal third-party audit | Deploy on BSI-C5-certified EU infrastructure (Hetzner, IONOS, STACKIT, OVHcloud) to satisfy hosting-level certification requirements |

### What MoE Sovereign + Codex Offers That Palantir Does Not

| Aspect | MoE Sovereign + Codex |
|---|---|
| Licensing | Apache 2.0 — fully auditable, forkable, zero licence fee |
| Data sovereignty | 100 % air-gap capable; no telemetry of any kind |
| CLOUD Act exposure | None when deployed on EU infrastructure |
| Per-token inference cost | €0 on own hardware |
| Codebase transparency | Open weights, open source, public benchmarks, public CI |
| Vendor lock-in | None — every component is a replaceable open-source standard |

### Important Caveat

MoE Codex is not a drop-in replacement for Palantir Foundry in terms of commercial
product maturity, enterprise support, certification depth, or integration breadth.
Palantir employs thousands of engineers and has decades of product development.
MoE Codex is a technically capable open-source platform that is the right choice when
**transparency, sovereignty, and auditability** outweigh the need for commercial
enterprise support — which is precisely the scenario defined by EU AI Act, NIS2, and
BSI Grundschutz requirements for public-sector and critical-infrastructure operators.
