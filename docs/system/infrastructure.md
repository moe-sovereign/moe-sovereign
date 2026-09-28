# Infrastructure & Hardware

## Overview

The reference infrastructure consists of 7 GPU nodes running on repurposed enterprise and consumer hardware. It demonstrates that MoE Sovereign functions across a wide spectrum of hardware — from CPU-only inference with 7B models up to high-end enterprise GPUs. The Tesla M10, M60 and K80 nodes are **Proof-of-Concept hardware**: they show what is technically feasible, not what is recommended for production deployments. A systematic latency comparison across all hardware tiers is planned.

!!! note "Reference Implementation"
    The nodes shown are Ollama instances. In an enterprise environment,
    these can be replaced by cloud API endpoints, dedicated GPU clusters, or
    cloud inference services.

## Hardware Table

| Node | CPU | RAM | GPUs | Total VRAM | Notes |
|------|-----|-----|------|------------|-------|
| **N1** | AMD Ryzen 5 5600G | 64 GB DDR4 | 3× RTX 2060 12 GB + 2× RTX 3060 12 GB | 60 GB | Consumer GPUs |
| **N2** | Intel Core i5-4590 | 32 GB DDR3 | 1× Tesla M10 (4× 8 GB) | 32 GB | Legacy Enterprise |
| **N3** | AMD Athlon II X2 270 | 16 GB DDR3 | 1× Tesla M10 (4× 8 GB) | 32 GB | Ultra-Legacy CPU |
| **N4** | AMD EPYC Embedded 3151 | 128 GB DDR4 ECC | 3× Tesla M10 (96 GB) | 96 GB | HPC Server |
| **N5** | AMD EPYC Embedded 3151 | 128 GB DDR4 ECC | 3× Tesla M10 (96 GB) | 96 GB | HPC Server (identical to N4) |
| **N6** | AMD EPYC Embedded 3151 | 128 GB DDR4 ECC | 7× Tesla K80 (2× 12 GB) | 168 GB | Ollama37 fork (Kepler CC3.7) |
| **EXP** | Dell Wyse Thin Client | minimal | Tesla M10 (32 GB) via eGPU | 32 GB | Experiment: MiniPCI → PCIe x16 |

**Total VRAM: ~516 GB** (all nodes)

## Network Topology

### External Access — Host-Level Nginx

The external access layer uses a Nginx instance running **natively on the host OS** (not in Docker).
It terminates TLS via Let's Encrypt (certbot) and proxies requests to the Docker service ports.
See [Webserver & Reverse Proxy](webserver.md) for full details.

```mermaid
graph TD
    INET["🌐 Internet"]

    subgraph HOST_OS["Host OS (native)"]
        NGINX["Nginx\nTLS via Let's Encrypt\nPort 443"]
    end

    subgraph DOCKER_STACK["Docker Stack"]
        ORCH["langgraph-orchestrator\n:8002"]
        ADMIN["moe-admin\n:8088"]
        DOCS_C["moe-caddy :80/:443\n→ moe-docs :8098\n→ moe-dozzle :9999"]
    end

    subgraph GPU_CLUSTER["GPU Cluster (separate nodes)"]
        N1["N1: Consumer GPUs\n60 GB VRAM · Ollama :11434"]
        N2["N2: Tesla M10\n32 GB VRAM · Ollama :11434"]
        N3["N3: Tesla M10\n32 GB VRAM · Ollama :11434"]
        N4["N4: 3× Tesla M10\n96 GB VRAM · Ollama :11434"]
        N5["N5: 3× Tesla M10\n96 GB VRAM · Ollama :11434"]
        N6["N6: 7× Tesla K80\n168 GB VRAM · Ollama37 :11434"]
    end

    INET -->|HTTPS| NGINX
    NGINX -->|proxy_pass :8002| ORCH
    NGINX -->|proxy_pass :8088| ADMIN
    NGINX -->|proxy_pass :80/:443| DOCS_C
    ORCH <-->|"HTTP/REST Ollama API"| N1
    ORCH <-->|"HTTP/REST Ollama API"| N2
    ORCH <-->|"HTTP/REST Ollama API"| N3
    ORCH <-->|"HTTP/REST Ollama API"| N4
    ORCH <-->|"HTTP/REST Ollama API"| N5
    ORCH <-->|"HTTP/REST Ollama API"| N6
```

### Internal Container Dependency Graph

```mermaid
---
config:
  look: classic
---
graph TD
    KAFKA["moe-kafka\n:9092 KRaft"]
    NEO4J["neo4j-knowledge\n:7687 Bolt · :7474 HTTP"]
    MCP["mcp-precision\n:8003"]
    CHROMA["chromadb-vector\n:8001"]
    CACHE["terra_cache\nValkey :6379"]
    PG["terra_checkpoints\nPostgres :5432"]
    PROXY["docker-socket-proxy\n:2375 (read-only)"]

    ORCH["langgraph-orchestrator\n:8002"]
    ADMIN["moe-admin\n:8088"]
    PROM["moe-prometheus\n:9090"]
    GRAF["moe-grafana\n:3001"]
    NODE["node-exporter\n:9100"]
    CADV["cadvisor\n:9338"]
    DOCS["moe-docs\n:8098"]
    DOZZLE["moe-dozzle\n:9999"]
    CADDY["moe-caddy\n:80/:443"]
    SYNC["moe-docs-sync"]

    %% orchestrator dependencies
    ORCH -->|"redis://"| CACHE
    ORCH -->|"postgresql://"| PG
    ORCH -->|"http://"| CHROMA
    ORCH -->|"bolt://"| NEO4J
    ORCH -->|"kafka://"| KAFKA
    ORCH -->|"http://"| MCP
    KAFKA -.->|"moe.ingest consumer"| ORCH

    %% admin dependencies
    ADMIN -->|"http://"| ORCH
    ADMIN -->|"http://"| PROM
    ADMIN -->|"redis://"| CACHE
    ADMIN -->|"postgresql://"| PG
    ADMIN -->|"http:// Docker API"| PROXY

    %% observability
    PROM -->|"scrape :8002/metrics"| ORCH
    PROM -->|"scrape :9100/metrics"| NODE
    PROM -->|"scrape :9338/metrics"| CADV
    GRAF -->|"datasource"| PROM

    %% docs stack
    CADDY -->|"reverse_proxy"| DOCS
    CADDY -->|"reverse_proxy"| DOZZLE
    SYNC -->|"HTTP ingest"| ORCH

    style ORCH fill:#1e3a5f,color:#fff
    style ADMIN fill:#2a1e5f,color:#fff
    style KAFKA fill:#4a2a00,color:#fff
    style NEO4J fill:#1e5f3a,color:#fff
    style PROM fill:#5f2a00,color:#fff
```

## MoE Codex — Optional Data Intelligence Layer

MoE Codex is an independently deployable extension that adds an enterprise data
management stack on top of MoE Sovereign. It runs in its own Docker Compose project
at `/opt/moe-sovereign/moe-codex/` and communicates with the core stack
over the shared host network.

### Architecture Position

```
┌─────────────────────────────────────────────────────────────┐
│                    External Access (Nginx / Caddy)          │
└───────────────┬─────────────────────────────────────────────┘
                │
┌───────────────▼─────────────────────────────────────────────┐
│              MoE Sovereign Core  (moe-infra/)               │
│  langgraph-orchestrator :8002 · mcp-precision :8003        │
│  neo4j :7687 · chromadb :8001 · kafka :9092                │
│  moe-admin :8088 · grafana :3001 · prometheus :9090         │
└───────────────┬─────────────────────────────────────────────┘
                │  OpenLineage events · REST API calls
┌───────────────▼─────────────────────────────────────────────┐
│              MoE Codex Extension  (moe-codex/)  [optional]  │
│  codex-api :8090 · marquez :5000 · lakefs :8010            │
│  nifi :8181 · mlflow :5002 · opa :8282 · trino :8080       │
│  superset :8889 · docling :7080 · opensearch :9200         │
└─────────────────────────────────────────────────────────────┘
                │
┌───────────────▼─────────────────────────────────────────────┐
│              MoE Libris Federation Hub  [optional]          │
│  Federation API · PostgreSQL · Neo4j (global graph)         │
└─────────────────────────────────────────────────────────────┘
```

### Codex Port Reference

| Service | Host Port | Role |
|---|---|---|
| `moe-codex-api` | `8090` | Codex FastAPI backend (25 modules) |
| `moe-marquez` | `5000` | OpenLineage / Marquez lineage server |
| `moe-lakefs` | `8010` | lakeFS data versioning UI + API |
| `moe-nifi` | `8181` | Apache NiFi ETL canvas |
| `moe-mlflow` | `5002` | MLflow experiment tracking |
| `moe-opa` | `8282` | Open Policy Agent policy evaluation |
| `moe-trino` | `8080` | Trino federated SQL |
| `moe-docling` | `7080` | DocLing document intelligence API |
| `moe-superset` | `8889` | Apache Superset BI |
| `moe-opensearch` | `9200` | OpenSearch federated search (optional) |

### Data Flow Between Core and Codex

1. The `langgraph-orchestrator` emits OpenLineage events to `moe-marquez:5000` for
   every knowledge bundle import.
2. Dataset snapshots are written to `moe-lakefs` on a `pending/<tag>` branch; the
   Codex approval UI presents them for human review before merging to `main`.
3. Apache NiFi fans out raw bundle submissions as OpenLineage run events and routes
   them to the appropriate sinks (Marquez, lakeFS, Trino catalogs).
4. OPA evaluates every write request against Rego policies before the Codex API
   commits any change to the catalog or knowledge graph.
5. Trino federates SQL queries across Postgres, lakeFS S3, and Marquez without
   data movement.

---

## Ollama vs. Ollama37

### Standard Ollama
- Supports NVIDIA GPUs from **Compute Capability 5.0** (Maxwell+)
- Tesla K80 (Kepler, CC 3.7): **not supported**

### Ollama37 Fork
- Reactivates CUDA support for **Compute Capability 3.7** (Kepler architecture)
- Enables full inference on Tesla K80 GPUs
- Same API as standard Ollama (drop-in)
- Node N6: 7× Tesla K80 = 168 GB VRAM

!!! info "Machbarkeitsstudie"
    Tesla K80 GPUs are officially no longer supported by Ollama.
    The Ollama37 fork reactivates these cards and enables LLM inference
    on hardware that others treat as electronic waste — demonstrated as a PoC.
    How this compares in latency and throughput to consumer or enterprise GPUs
    remains to be quantified in the planned hardware comparison study.

## VRAM Management

The orchestrator manages model placement dynamically:

- **VRAM inventory**: Each node reports available VRAM via Ollama API
- **Model routing**: Large T2 models are preferentially placed on nodes with more VRAM
- **Multi-GPU**: Tesla M10 and K80 are treated as a single logical pool
- **Failover**: If a node fails, requests are automatically redirected to others

## Runtime Stack per Node

```mermaid
flowchart TD
    A["Debian 13 (Bookworm)"] --> B[Docker CE]
    B --> C["Ollama (or Ollama37 for N6)"]
    C --> D[NVIDIA Container Toolkit]
    D --> E[CUDA 12.x]
```
