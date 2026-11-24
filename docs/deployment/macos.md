# macOS Deployment (Docker Desktop or Podman)

MoE Sovereign runs on macOS via Docker Desktop or Podman, including Apple
Silicon (M1/M2/M3/M4). Both runtimes execute the containers in a Linux VM.
The compose stack uses bind mounts for state directories
(Postgres, Neo4j, Redis, ChromaDB, Kafka, Prometheus, Grafana) — those
need to live in a path that Docker Desktop is allowed to share.

## Common error

```text
Error response from daemon: mounts denied:
The path /opt/moe-infra is not shared from the host and is not known to Docker.
You can configure shared paths from Docker -> Preferences... -> Resources -> File Sharing.
```

This means the `MOE_DATA_ROOT` host directory is outside Docker Desktop's
File Sharing whitelist. By default Docker Desktop only permits `/Users`,
`/Volumes`, `/private`, `/tmp` and `/var/folders`. `/opt` is **not** in
that list.

## One-time setup

`install.sh` is Linux-only (it uses `apt-get` and `/etc/os-release`). On
macOS, use the dedicated installer. It never uses `sudo`, Linux package
management, UID remapping, or `/opt` bind mounts.

1. Install Docker Desktop ≥ 4.30 from
   <https://docs.docker.com/desktop/setup/install/mac-install/>, or install
   Podman / Podman Desktop from <https://podman.io/docs/installation>.
2. Clone the repository:
   ```bash
   git clone https://github.com/moe-sovereign/moe-sovereign.git
   cd moe-sovereign
   ```
3. Run the macOS installer (interactive — asks for admin password):
   ```bash
   bash install-macos.sh
   ```
   It defaults `MOE_DATA_ROOT=$HOME/moe-data` and
   `GRAFANA_DATA_ROOT=$HOME/moe-grafana`, creates the subdirectories,
   preserves existing secrets, validates Compose, and starts the stack.
   Docker Desktop is chosen automatically when ready. To select Podman, run:
   ```bash
   bash install-macos.sh --runtime podman
   ```
   To clone into `~/moe-sovereign` and install in one step, use (never use
   `sudo`):
   ```bash
   curl -fsSL https://raw.githubusercontent.com/moe-sovereign/moe-sovereign/main/install-macos.sh | bash
   ```
4. With Docker Desktop, verify that both paths are allowed in **Settings →
   Resources → File Sharing** and apply the change. Home-directory paths are
   normally shared already. With Podman, the installer creates or starts a
   rootless Podman machine; ensure a Compose provider is configured in
   **Podman Desktop → Settings → Resources → Compose** (or install
   `podman-compose`).

The installer leaves the Caddy profile disabled because this repository's
Caddy service uses Linux host networking. Use a macOS-compatible reverse
proxy if the installation must be exposed beyond localhost.

## Manual setup (without install.sh)

```bash
# 1. Choose shared paths (Grafana keeps its own root for separate backups)
export MOE_DATA_ROOT="$HOME/moe-data"
export GRAFANA_DATA_ROOT="$HOME/moe-grafana"

# 2. Pre-create the subdirectories
mkdir -p "$MOE_DATA_ROOT"/{kafka-data,neo4j-data,neo4j-logs,agent-logs,\
chroma-onnx-cache,chroma-data,redis-data,prometheus-data,admin-logs,\
userdb,few-shot}
mkdir -p "$GRAFANA_DATA_ROOT"/{data,dashboards}

# 3. Add the host roots to your .env
{
  echo "MOE_DATA_ROOT=$MOE_DATA_ROOT"
  echo "GRAFANA_DATA_ROOT=$GRAFANA_DATA_ROOT"
} >> .env

# 4. Add BOTH paths to Docker Desktop File Sharing
#    Settings → Resources → File Sharing → +  →  $MOE_DATA_ROOT
#    Settings → Resources → File Sharing → +  →  $GRAFANA_DATA_ROOT
#    → Apply & Restart

# 5. Bring up the stack
docker compose up -d
```

### Configurable host paths

All container bind mounts derive from these `.env` variables — adjust
them once and every service follows. `install.sh` writes the right
defaults for your platform.

| Variable | Linux default | macOS default | What it holds |
|---|---|---|---|
| `MOE_DATA_ROOT` | `/opt/moe-infra` | `$HOME/moe-data` | Postgres, Neo4j, Redis, ChromaDB, Kafka, Prometheus, agent/admin logs, few-shot store |
| `GRAFANA_DATA_ROOT` | `/opt/grafana` | `$HOME/moe-grafana` | Grafana SQLite + dashboards |
| `FEW_SHOT_HOST_DIR` | `${MOE_DATA_ROOT}/few-shot` | same | Override only if you mount a separate dataset disk |

## Apple Silicon notes

- All upstream images (Postgres, Neo4j, Valkey, ChromaDB, Caddy,
  Prometheus, Grafana, Kafka) ship `arm64` variants — no emulation
  required.
- The MoE orchestrator and admin UI are built locally from the included
  `Dockerfile`s. The first `docker compose build` will compile native
  arm64 wheels (mostly Pydantic / NumPy / SciPy) and takes 5–15 minutes.
- For inference, **use Ollama on the host** (`brew install ollama`),
  not inside the container. Ollama on macOS uses Metal, which is faster
  than the CPU fallback inside any container.
  ```bash
  brew install ollama
  ollama serve   # binds 127.0.0.1:11434 by default
  ollama pull llama3.1:8b
  ```
  Then point the orchestrator at `http://host.docker.internal:11434/v1`
  via the Admin UI's *Inference Servers* page.

## Performance tuning for Docker Desktop

- Docker Desktop → Settings → Resources → CPUs / Memory: allocate at
  least **6 CPUs and 12 GB RAM**. The default 2 CPU / 8 GB is enough
  to start but throttles ChromaDB embeddings and Neo4j heavily.
- Use **VirtioFS** (Settings → General → "Use Virtualization framework")
  on Ventura+. It is significantly faster for bind mounts than
  `gRPC FUSE`.
- Disable **WSL2 / Hyper-V backend leftovers** — irrelevant on macOS but
  some users carry the toggle from a Windows install.

## Verify

After `docker compose up -d`:

```bash
docker compose ps                  # all services Up + healthy
docker compose logs langgraph-app  # no "mount denied" lines
open http://localhost:8088         # Admin UI
```

Common follow-up errors and what they mean:

| Error | Cause | Fix |
|---|---|---|
| `mounts denied: … is not shared from the host` | Path not in Docker Desktop File Sharing | Add `MOE_DATA_ROOT` and `GRAFANA_DATA_ROOT` under Settings → Resources → File Sharing, then Apply & Restart |
| `POSTGRES_CHECKPOINT_PASSWORD: missing — run scripts/install.sh…` | Compose started before `.env` was generated | `bash scripts/bootstrap-macos.sh`, then retry |
| `password authentication failed for user "moe_admin"` | Postgres volume was initialised with a different password (old install.sh run) | `docker compose down && rm -rf $MOE_DATA_ROOT/langgraph-checkpoints && docker compose up -d` — the init script re-creates `moe_admin` with the current `.env` password |
| `pydantic ValidationError: searx_host` | Older image build — rebuild | `docker compose build langgraph-app && docker compose up -d langgraph-app` |
| `permission denied` on a sub-volume | Host UID ≠ container UID | `chmod -R 0775 "$MOE_DATA_ROOT"` |
| `Cannot connect to the Docker daemon` | Docker Desktop is not running | start it from /Applications |
| `no matching manifest for linux/arm64` | Pinned amd64-only image | upstream issue; report or use `--platform linux/amd64` |
| Slow `docker compose up` | gRPC FUSE bind-mount layer | switch to VirtioFS (see above) |

## Uninstall

```bash
docker compose down -v             # stop + remove containers
rm -rf "$MOE_DATA_ROOT"            # remove all bind-mounted data
# (also remove the path from Docker Desktop File Sharing if you wish)
```
