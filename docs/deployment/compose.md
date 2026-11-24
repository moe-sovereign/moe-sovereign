# Docker Compose

Docker Compose is the **team profile default** and the fastest way to get a
complete MoE Sovereign stack running on a single host. The same
`docker-compose.yaml` that the development team uses is production-ready.

!!! danger "Firewall is mandatory in production"
    The compose stack publishes ports `8002` (orchestrator), `8003` (MCP),
    `8088` (admin UI), `8098` (ChromaDB) and `3001` (Grafana) on `0.0.0.0` —
    they are reachable from any host that can route to this machine.
    Several of these endpoints have **no authentication** (e.g.
    `POST /graph/knowledge/import`) and assume network-level isolation.
    Configure your host firewall to expose only `80/443` to the public
    internet. See [Firewall & Network Exposure](firewall.md) for
    ready-to-run UFW / firewalld / iptables rules.

## What the stack contains

```mermaid
---
config:
  look: classic
---
flowchart TB
    subgraph CORE[Core services]
        O[langgraph-app<br/>:8002 → :8000]
        M[mcp-precision<br/>:8003]
        A[moe-admin<br/>:8088]
    end
    subgraph DATA[Data tier]
        PG[(terra_checkpoints<br/>postgres 17)]
        VK[(terra_cache<br/>valkey)]
        CH[(chromadb-vector<br/>:8001)]
        NJ[(neo4j-knowledge<br/>:7474/:7687)]
        KF[(moe-kafka<br/>KRaft :9092)]
    end
    subgraph OBS[Observability]
        PR[(moe-prometheus<br/>:9090)]
        GR[(moe-grafana<br/>:3001)]
        NE[(node-exporter)]
        CA[(cadvisor)]
    end
    subgraph EDGE[Edge]
        CD[moe-caddy<br/>:80/:443]
        DZ[moe-dozzle<br/>:9999]
    end

    CD --> O & A
    O --> PG & VK & CH & NJ & KF & M
    A --> PG & VK
    PR -- scrape --> O & NE & CA
    GR --> PR

    classDef core fill:#eef2ff,stroke:#6366f1;
    classDef data fill:#fef3c7,stroke:#d97706;
    classDef obs  fill:#ecfdf5,stroke:#059669;
    classDef edge fill:#fce7f3,stroke:#db2777;
    class O,M,A core;
    class PG,VK,CH,NJ,KF data;
    class PR,GR,NE,CA obs;
    class CD,DZ edge;
```

## Optional components via Compose profiles

Three services are behind explicit profiles and only start when the profile
is active. `install.sh` sets `COMPOSE_PROFILES` in `.env` and passes
`--profile <name>` automatically based on your answers during setup.

| Profile | Services | When to enable |
|---------|----------|---------------|
| `neo4j` | `neo4j-knowledge` (~1.5 GB RAM) | GraphRAG, ontology curator, knowledge graph |
| `caddy` | `moe-caddy` | Built-in TLS reverse proxy |
| `authentik` | `authentik-server`, `authentik-worker`, `authentik-postgresql`, `authentik-redis` | Self-hosted SSO/OIDC |

```bash
# Core stack only (no Neo4j, no Caddy, no Authentik)
sudo docker compose up -d

# With Neo4j GraphRAG
sudo docker compose --profile neo4j up -d

# Full stack (Neo4j + Caddy + Authentik)
sudo docker compose --profile neo4j --profile caddy --profile authentik up -d
```

!!! note "podman-compose"
    `podman-compose` does not auto-read `COMPOSE_PROFILES` from `.env`.
    Always pass `--profile <name>` explicitly on the command line.

## Launch

## Rebuilding after code changes

```bash
sudo docker compose build langgraph-app && sudo docker compose up -d langgraph-app
sudo docker compose build moe-admin    && sudo docker compose up -d moe-admin
sudo docker compose build mcp-precision && sudo docker compose up -d mcp-precision
```

The Dockerfiles are multi-stage, so rebuilds only redo the layers that
actually changed — a typical `main.py` edit rebuilds in under 10 seconds.

## Configuration via `.env` (no compose-file edits needed)

The shipped `docker-compose.yml` is fully parameterised. Every host path
and every published port resolves through `${VAR:-default}` so you can
override them from `.env` without touching the compose file. This keeps
`git pull` conflict-free.

### Required passwords

Compose enforces these with `${VAR:?…}` — `docker compose up` aborts
with an explicit error if any are missing. `install.sh` /
`bootstrap-macos.sh` write all of them automatically.

| `.env` key | Used by | Required |
|---|---|---|
| `POSTGRES_CHECKPOINT_PASSWORD` | terra_checkpoints, langgraph-app, moe-admin | Always |
| `MOE_USERDB_PASSWORD` | langgraph-app, moe-admin | Always |
| `REDIS_PASSWORD` | terra_cache, langgraph-app, moe-admin | Always |
| `NEO4J_PASS` | neo4j-knowledge | Only with `neo4j` profile — leave empty to disable GraphRAG |
| `GF_SECURITY_ADMIN_PASSWORD` | moe-grafana | Always |
| `ADMIN_PASSWORD`, `ADMIN_SECRET_KEY` | moe-admin | Always |
| `AUTHENTIK_SECRET_KEY`, `AUTHENTIK_POSTGRESQL__PASSWORD` | authentik-* | Only with `authentik` profile |

### Host data roots (bind-mount sources)

| `.env` key | Linux default | macOS default | Holds |
|---|---|---|---|
| `MOE_DATA_ROOT` | `/opt/moe-infra` | `$HOME/moe-data` | Postgres, Neo4j, Redis, ChromaDB, Kafka, Prometheus, logs |
| `GRAFANA_DATA_ROOT` | `/opt/grafana` | `$HOME/moe-grafana` | Grafana SQLite + dashboards |
| `FEW_SHOT_HOST_DIR` | `${MOE_DATA_ROOT}/few-shot` | same | Few-shot example store |
| `CLAUDE_SKILLS_DIR` | `./skills` | `./skills` | Claude Code skill bundle |

### Published host ports (override to avoid collisions)

All ports are remappable. Container-internal ports stay the same; only
the host side changes when you set `<SERVICE>_HOST_PORT` in `.env`.

| Default | `.env` key | Service | Bind |
|---:|---|---|---|
| 80 | `CADDY_HTTP_PORT` | Caddy reverse proxy | `0.0.0.0` |
| 443 | `CADDY_HTTPS_PORT` | Caddy HTTPS / HTTP3 | `0.0.0.0` |
| 8002 | `LANGGRAPH_HOST_PORT` | Orchestrator API | `0.0.0.0` |
| 8003 | `MCP_HOST_PORT` | MCP precision tools | `0.0.0.0` |
| 8088 | `ADMIN_UI_HOST_PORT` | Admin UI | `0.0.0.0` |
| 8098 | `DOCS_HOST_PORT` | Docs site | `0.0.0.0` |
| 3001 | `GRAFANA_HOST_PORT` | Grafana | `0.0.0.0` |
| 8001 | `CHROMA_HOST_PORT` | ChromaDB | `127.0.0.1` |
| 9090 | `PROMETHEUS_HOST_PORT` | Prometheus | `127.0.0.1` |
| 7474 | `NEO4J_HTTP_PORT` | Neo4j Browser | `127.0.0.1` |
| 7687 | `NEO4J_BOLT_PORT` | Neo4j Bolt | `127.0.0.1` |
| 9092 | `KAFKA_HOST_PORT` | Kafka broker | `127.0.0.1` |
| 9999 | `DOZZLE_HOST_PORT` | Dozzle log viewer | `127.0.0.1` |
| 9100 | `NODE_EXPORTER_HOST_PORT` | node-exporter | `127.0.0.1` |
| 9338 | `CADVISOR_HOST_PORT` | cAdvisor | `127.0.0.1` |

Example `.env` snippet to move the Admin UI off the default 8088 because
your host already runs something there:

```bash
ADMIN_UI_HOST_PORT=8089
```

After editing `.env`, `docker compose up -d` re-creates only the
affected container.

See `.env.example` for the full annotated reference and
[Firewall & Network Exposure](firewall.md) for which services to
firewall on each binding interface.
