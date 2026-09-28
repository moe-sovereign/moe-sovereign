# Palantir Comparison

> **Detailed comparison content has moved.**
>
> The feature coverage matrix, use-case scenarios, and compliance mapping are maintained in the **[moe-codex](https://github.com/moe-sovereign/moe-codex)** repository — the compliance-grade data intelligence platform for regulated industries.
>
> - Feature coverage: `moe-codex/docs/system/palantir_comparison.md`
> - Use cases: `moe-codex/docs/use-cases/`
> - Compliance mapping: `moe-codex/docs/system/eu_ai_act_mapping.md`, `bsi_grundschutz_mapping.md`, `nis2_readiness.md`

## Positioning

MoE Sovereign is **not a drop-in replacement for Palantir AIP or Foundry today**.
It is an open-source, sovereign LLM infrastructure layer that shares several architectural
principles with Palantir-class platforms — and is designed to grow toward similar use cases
over time.

### Where the architectures converge

| Palantir concept | MoE Sovereign equivalent | Status |
|---|---|---|
| AI Orchestration layer | Deterministic expert routing (LangGraph) | Production |
| Ontology / Knowledge layer | Neo4j GraphRAG + Knowledge Bundles | Production |
| Agent Tooling | 51 MCP precision tools | Production |
| Governance & Audit | Audit trails, trust scores, skill registry hard-lock | Production |
| Human-in-the-loop | Linting pipeline, correction memory | Production |
| Continuous learning | RL Flywheel (Thompson Sampling) | Production |
| Data catalog & lineage | OpenLineage via moe-codex (optional) | Opt-in |
| Approval workflows | moe-codex (optional) | Opt-in |

### Where the gap remains significant

Palantir Technologies employs thousands of engineers, has decades of product development,
and maintains extensive enterprise certifications, global support teams, and partner
integrations. MoE Sovereign is a technically advanced open-source project — but comparing
current product maturity, enterprise support, or integration breadth would be misleading.

### What MoE Sovereign offers today that Palantir does not

| Aspect | MoE Sovereign |
|---|---|
| Licensing | Apache 2.0 — fully auditable, forkable, zero licence fee |
| Data sovereignty | 100 % air-gap capable; no telemetry |
| CLOUD Act exposure | None (when deployed on EU infrastructure) |
| Per-token cost | €0 on own hardware |
| Transparency | Open weights, open source, public benchmarks |

### Long-term vision

The MoE Sovereign family (sovereign + codex + libris) is architected toward the same
problem space as Palantir AIP — sovereign, auditable, AI-driven decision support for
regulated industries. The realistic trajectory:

- **Today (2026):** solid open-source LLM infrastructure for the 95 % of operators who need
  intelligent routing, GraphRAG, and MCP tools without full compliance-stack overhead.
- **Medium term (3–5 years):** with community growth and productisation, a credible
  open-source alternative in *selected* regulated deployment scenarios.
- **Long term:** a European reference architecture for sovereign AI, functionally comparable
  to Palantir AIP in its core use cases.

For the 5 % who need catalog, lineage, versioning, approval workflows, and investigation
tools today — deploy **moe-codex** alongside moe-sovereign.

| Stack | Role |
|-------|------|
| moe-sovereign | LLM gateway: expert routing, GraphRAG, MCP tools, caching |
| moe-codex (optional) | Data platform: catalog, approval, OpenLineage, lakeFS, NiFi ETL |
| moe-libris (optional) | Federation hub between sovereign instances |
