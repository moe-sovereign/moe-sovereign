#!/usr/bin/env bash
# =============================================================================
#  MoE Sovereign — macOS installer
#
#  Usage: bash install-macos.sh [--runtime docker|podman]
#         curl -fsSL https://raw.githubusercontent.com/moe-sovereign/moe-sovereign/main/install-macos.sh | bash
#
#  This is the macOS counterpart to install.sh. It deliberately does not use
#  apt, sudo, systemd, Linux UID remapping, or /opt bind mounts. Docker Desktop
#  and Podman both run containers in a Linux VM on macOS, so all persistent
#  paths are kept below $HOME.
# =============================================================================
set -euo pipefail
IFS=$'\n\t'

if [[ $EUID -eq 0 ]]; then
  echo "[ERROR] Do not run the macOS installer with sudo."
  echo "        Docker Desktop and rootless Podman must run as your regular macOS user."
  exit 1
fi

RUNTIME_REQUESTED="${MOE_CONTAINER_RUNTIME:-}"
if [[ "${1:-}" == "--runtime" ]]; then
  RUNTIME_REQUESTED="${2:-}"
elif [[ "${1:-}" == --runtime=* ]]; then
  RUNTIME_REQUESTED="${1#--runtime=}"
elif [[ $# -gt 0 ]]; then
  echo "Usage: bash install-macos.sh [--runtime docker|podman]"
  exit 2
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "[ERROR] install-macos.sh is for macOS only. Use install.sh on Linux/WSL."
  exit 1
fi

# BASH_SOURCE is unset when bash reads the script from stdin (`curl | bash`).
# A checked-out script uses its containing directory; streamed execution clones
# the repository below $HOME in ensure_repo().
SCRIPT_SOURCE="${BASH_SOURCE[0]:-}"
if [[ -n "$SCRIPT_SOURCE" && -f "$SCRIPT_SOURCE" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_SOURCE")" && pwd)"
else
  SCRIPT_DIR=""
fi
INSTALL_DIR="${INSTALL_DIR:-${SCRIPT_DIR:-${HOME}/moe-sovereign}}"
ENV_FILE="${INSTALL_DIR}/.env"
MOE_DATA_ROOT_DEFAULT="${MOE_DATA_ROOT:-${HOME}/moe-data}"
GRAFANA_DATA_ROOT_DEFAULT="${GRAFANA_DATA_ROOT:-${HOME}/moe-grafana}"
MOE_REPO_URL="${MOE_REPO_URL:-https://github.com/moe-sovereign/moe-sovereign.git}"

print_banner() {
  cat <<'EOF'

  MoE Sovereign — macOS installer
  =================================
  Docker Desktop and Podman run the stack in a Linux VM. This installer keeps
  persistent data below your home directory, which is shared with that VM.

EOF
}

to_lower() {
  # macOS ships Bash 3.2, which does not support Bash 4's ${value,,} syntax.
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

prompt_yes_no() {
  local prompt="$1" default="$2" answer
  while true; do
    read -rp "  ${prompt} [${default}]: " answer < /dev/tty
    answer="${answer:-${default}}"
    answer="$(to_lower "$answer")"
    case "$answer" in
      y|yes) printf 'true'; return 0 ;;
      n|no)  printf 'false'; return 0 ;;
      *) echo "  Please enter y or n." >&2 ;;
    esac
  done
}

read_env() {
  local key="$1"
  [[ -f "$ENV_FILE" ]] || return 0
  grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- || true
}

# Replaces exactly one .env assignment (or adds it), without evaluating its
# value as shell input. The temp file is created alongside .env so the final
# rename remains atomic on the same filesystem.
set_env() {
  local key="$1" value="$2" tmp
  tmp="${ENV_FILE}.tmp.$$"
  awk -v key="$key" -v value="$value" '
    $0 ~ "^" key "=" {
      if (!seen++) print key "=" value
      next
    }
    { print }
    END { if (!seen) print key "=" value }
  ' "$ENV_FILE" > "$tmp"
  mv "$tmp" "$ENV_FILE"
}

ensure_command() {
  local command_name="$1" install_hint="$2"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "[ERROR] '${command_name}' is not installed. ${install_hint}"
    exit 1
  fi
}

docker_ready() {
  command -v docker >/dev/null 2>&1 \
    && docker info >/dev/null 2>&1 \
    && docker compose version >/dev/null 2>&1
}

podman_ready() {
  command -v podman >/dev/null 2>&1 \
    && podman info >/dev/null 2>&1 \
    && podman compose version >/dev/null 2>&1
}

install_podman_with_homebrew() {
  ensure_command brew "Install Podman Desktop or Homebrew first: https://podman.io/docs/installation"
  echo "  Installing Podman and a Compose provider with Homebrew..."
  brew install podman podman-compose
}

ensure_podman_machine() {
  local machine_count machine_state
  machine_count="$(podman machine list --format '{{.Name}}' 2>/dev/null | wc -l | tr -d ' ')"
  if [[ "$machine_count" == "0" ]]; then
    echo "  Creating a rootless Podman machine (6 CPUs, 12 GB RAM, 80 GB disk)..."
    podman machine init --now --cpus 6 --memory 12288 --disk-size 80 --update-connection=true
    return
  fi

  machine_state="$(podman machine inspect --format '{{.State}}' 2>/dev/null || true)"
  if [[ "$machine_state" != "running" ]]; then
    echo "  Starting the Podman machine..."
    podman machine start --update-connection=true
  fi
}

resolve_runtime() {
  local choice requested_runtime
  requested_runtime="$(to_lower "$RUNTIME_REQUESTED")"
  case "$requested_runtime" in
    docker|podman) CONTAINER_RUNTIME="$requested_runtime" ;;
    "")
      if docker_ready; then
        CONTAINER_RUNTIME="docker"
      elif podman_ready; then
        CONTAINER_RUNTIME="podman"
      else
        echo "  No ready container runtime was found."
        echo "  1) Docker Desktop (recommended for this Compose stack)"
        echo "  2) Podman / Podman Desktop"
        while true; do
          read -rp "  Choose runtime [1/2, default 1]: " choice < /dev/tty
          case "${choice:-1}" in
            1) CONTAINER_RUNTIME="docker"; break ;;
            2) CONTAINER_RUNTIME="podman"; break ;;
            *) echo "  Please enter 1 or 2." ;;
          esac
        done
      fi
      ;;
    *)
      echo "[ERROR] Runtime must be 'docker' or 'podman'."
      exit 2
      ;;
  esac

  if [[ "$CONTAINER_RUNTIME" == "docker" ]]; then
    if ! command -v docker >/dev/null 2>&1; then
      echo "[ERROR] Docker Desktop is not installed. Install and start it, then rerun:"
      echo "        https://docs.docker.com/desktop/setup/install/mac-install/"
      exit 1
    fi
    if ! docker info >/dev/null 2>&1; then
      echo "[ERROR] Docker Desktop is installed but not running. Open Docker Desktop and wait until it is ready."
      exit 1
    fi
    if ! docker compose version >/dev/null 2>&1; then
      echo "[ERROR] Docker Desktop Compose v2 is required (docker compose)."
      exit 1
    fi
    COMPOSE_CMD=(docker compose)
    DOCKER_SOCKET="/var/run/docker.sock"
    CONTAINER_STORAGE_ROOT="/var/lib/docker"
  else
    if ! command -v podman >/dev/null 2>&1; then
      echo "  Podman was not found."
      if [[ "$(prompt_yes_no 'Install it with Homebrew?' 'Y')" == "true" ]]; then
        install_podman_with_homebrew
      else
        echo "[ERROR] Install Podman Desktop or the Podman CLI, then rerun."
        exit 1
      fi
    fi
    ensure_podman_machine
    if ! podman info >/dev/null 2>&1; then
      echo "[ERROR] Podman cannot connect to its machine. Try: podman machine start"
      exit 1
    fi
    if ! podman compose version >/dev/null 2>&1; then
      echo "[ERROR] A Compose provider is required for Podman."
      echo "        In Podman Desktop: Settings → Resources → Compose → Setup."
      echo "        Or install one: brew install podman-compose"
      exit 1
    fi
    COMPOSE_CMD=(podman compose)

    # Compose is executed against the Linux VM. Services that use the Podman
    # API therefore need the *VM-side* rootless socket, not macOS's proxy
    # socket in /var/folders/....
    local_vm_uid="$(podman machine ssh -- 'id -u' 2>/dev/null || true)"
    [[ "$local_vm_uid" =~ ^[0-9]+$ ]] || local_vm_uid=1000
    DOCKER_SOCKET="/run/user/${local_vm_uid}/podman/podman.sock"
    CONTAINER_STORAGE_ROOT="/home/core/.local/share/containers"
    podman machine ssh -- "systemctl --user start podman.socket" >/dev/null 2>&1 || true
    if ! podman machine ssh -- "test -S '${DOCKER_SOCKET}'" >/dev/null 2>&1; then
      echo "[ERROR] Podman's VM-side API socket was not available at ${DOCKER_SOCKET}."
      echo "        Run: podman machine ssh 'systemctl --user start podman.socket'"
      exit 1
    fi
  fi
}

ensure_repo() {
  if [[ -f "${INSTALL_DIR}/docker-compose.yml" && -f "${INSTALL_DIR}/.env.example" ]]; then
    return 0
  fi

  if [[ -e "$INSTALL_DIR" && ! -d "$INSTALL_DIR" ]]; then
    echo "[ERROR] ${INSTALL_DIR} exists but is not a directory."
    exit 1
  fi
  if [[ -d "$INSTALL_DIR" && -n "$(find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
    echo "[ERROR] ${INSTALL_DIR} exists but is not a MoE Sovereign checkout."
    echo "        Choose an empty INSTALL_DIR or clone the repository there first."
    exit 1
  fi

  ensure_command git "Install Xcode Command Line Tools first: xcode-select --install"
  echo "  Cloning MoE Sovereign into ${INSTALL_DIR}..."
  git clone "$MOE_REPO_URL" "$INSTALL_DIR"
  if [[ ! -f "${INSTALL_DIR}/docker-compose.yml" || ! -f "${INSTALL_DIR}/.env.example" ]]; then
    echo "[ERROR] The cloned repository is missing its Compose configuration."
    exit 1
  fi
}

prepare_env() {
  local existing_admin_user existing_admin_password answer data_input grafana_input profiles
  existing_admin_user="$(read_env ADMIN_USER)"
  existing_admin_password="$(read_env ADMIN_PASSWORD)"

  echo ""
  echo "  Installation directory: ${INSTALL_DIR}"
  read -rp "  Persistent data directory [${MOE_DATA_ROOT_DEFAULT}]: " data_input < /dev/tty
  MOE_DATA_ROOT="${data_input:-${MOE_DATA_ROOT_DEFAULT}}"
  read -rp "  Grafana data directory [${GRAFANA_DATA_ROOT_DEFAULT}]: " grafana_input < /dev/tty
  GRAFANA_DATA_ROOT="${grafana_input:-${GRAFANA_DATA_ROOT_DEFAULT}}"

  ADMIN_USER="${existing_admin_user:-admin}"
  read -rp "  Admin username [${ADMIN_USER}]: " answer < /dev/tty
  ADMIN_USER="${answer:-${ADMIN_USER}}"

  if [[ -n "$existing_admin_password" ]]; then
    ADMIN_PASSWORD="$existing_admin_password"
    echo "  Admin password: kept from existing .env"
  else
    while true; do
      read -rsp "  Admin password (at least 10 characters): " ADMIN_PASSWORD < /dev/tty
      echo ""
      [[ ${#ADMIN_PASSWORD} -ge 10 ]] && break
      echo "  [!] A password of at least 10 characters is required."
    done
  fi

  INSTALL_NEO4J="$(prompt_yes_no 'Enable Neo4j GraphRAG (~1.5 GB additional RAM)?' 'Y')"
  # Host-level metrics containers expect Linux host paths. The core stack is
  # portable, but this profile is intentionally opt-in on macOS.
  INSTALL_MONITORING="$(prompt_yes_no 'Enable monitoring profile (Grafana/Prometheus/Dozzle)?' 'N')"

  profiles=""
  [[ "$INSTALL_NEO4J" == "true" ]] && profiles="neo4j"
  if [[ "$INSTALL_MONITORING" == "true" ]]; then
    profiles="${profiles:+${profiles},}monitoring"
  fi

  if [[ -f "$ENV_FILE" ]]; then
    cp "$ENV_FILE" "${ENV_FILE}.bak.$(date +%Y%m%d-%H%M%S)"
    echo "  Existing .env backed up; existing secrets will be preserved."
  else
    cp "${INSTALL_DIR}/.env.example" "$ENV_FILE"
  fi

  GEN_ADMIN_SECRET="$(read_env ADMIN_SECRET_KEY)"; GEN_ADMIN_SECRET="${GEN_ADMIN_SECRET:-$(openssl rand -hex 32)}"
  GEN_REDIS_PASS="$(read_env REDIS_PASSWORD)"; GEN_REDIS_PASS="${GEN_REDIS_PASS:-$(openssl rand -hex 16)}"
  GEN_NEO4J_PASS="$(read_env NEO4J_PASS)"; GEN_NEO4J_PASS="${GEN_NEO4J_PASS:-$(openssl rand -hex 16)}"
  GEN_GRAFANA_PASS="$(read_env GF_SECURITY_ADMIN_PASSWORD)"; GEN_GRAFANA_PASS="${GEN_GRAFANA_PASS:-$(openssl rand -hex 12)}"
  GEN_PG_CHECKPOINT_PASS="$(read_env POSTGRES_CHECKPOINT_PASSWORD)"; GEN_PG_CHECKPOINT_PASS="${GEN_PG_CHECKPOINT_PASS:-$(openssl rand -hex 16)}"
  GEN_PG_USERDB_PASS="$(read_env MOE_USERDB_PASSWORD)"; GEN_PG_USERDB_PASS="${GEN_PG_USERDB_PASS:-$(openssl rand -hex 16)}"
  GEN_LIBRIS_PASS="$(read_env LIBRIS_DB_PASSWORD)"; GEN_LIBRIS_PASS="${GEN_LIBRIS_PASS:-$(openssl rand -hex 16)}"
  GEN_GARAGE_SECRET="$(read_env GARAGE_RPC_SECRET)"; GEN_GARAGE_SECRET="${GEN_GARAGE_SECRET:-$(openssl rand -hex 32)}"
  GEN_MINIO_PASSWORD="$(read_env MINIO_ROOT_PASSWORD)"; GEN_MINIO_PASSWORD="${GEN_MINIO_PASSWORD:-$(openssl rand -hex 24)}"
  GEN_AUTHENTIK_SECRET="$(read_env AUTHENTIK_SECRET_KEY)"; GEN_AUTHENTIK_SECRET="${GEN_AUTHENTIK_SECRET:-$(openssl rand -hex 32)}"
  GEN_AUTHENTIK_PG_PASSWORD="$(read_env AUTHENTIK_POSTGRESQL__PASSWORD)"; GEN_AUTHENTIK_PG_PASSWORD="${GEN_AUTHENTIK_PG_PASSWORD:-$(openssl rand -hex 16)}"
  GEN_AUTHENTIK_TAG="$(read_env AUTHENTIK_TAG)"; GEN_AUTHENTIK_TAG="${GEN_AUTHENTIK_TAG:-2026.2.1}"

  set_env COMPOSE_PROFILES "$profiles"
  set_env DOCKER_SOCKET "$DOCKER_SOCKET"
  set_env CONTAINER_STORAGE_ROOT "$CONTAINER_STORAGE_ROOT"
  set_env MOE_DATA_ROOT "$MOE_DATA_ROOT"
  set_env GRAFANA_DATA_ROOT "$GRAFANA_DATA_ROOT"
  set_env FEW_SHOT_HOST_DIR "${MOE_DATA_ROOT}/few-shot"
  set_env ADMIN_USER "$ADMIN_USER"
  set_env ADMIN_PASSWORD "$ADMIN_PASSWORD"
  set_env ADMIN_SECRET_KEY "$GEN_ADMIN_SECRET"
  set_env REDIS_PASSWORD "$GEN_REDIS_PASS"
  set_env REDIS_URL "redis://:${GEN_REDIS_PASS}@terra_cache:6379/0"
  set_env NEO4J_PASS "$GEN_NEO4J_PASS"
  if [[ "$INSTALL_NEO4J" == "true" ]]; then
    set_env NEO4J_URI "bolt://neo4j-knowledge:7687"
  else
    set_env NEO4J_URI ""
  fi
  set_env NEO4J_USER neo4j
  set_env POSTGRES_CHECKPOINT_PASSWORD "$GEN_PG_CHECKPOINT_PASS"
  set_env POSTGRES_CHECKPOINT_URL "postgresql://langgraph:${GEN_PG_CHECKPOINT_PASS}@terra_checkpoints:5432/langgraph"
  set_env MOE_USERDB_PASSWORD "$GEN_PG_USERDB_PASS"
  set_env MOE_USERDB_URL "postgresql://moe_admin:${GEN_PG_USERDB_PASS}@terra_checkpoints:5432/moe_userdb"
  set_env LIBRIS_DB_PASSWORD "$GEN_LIBRIS_PASS"
  set_env GF_SECURITY_ADMIN_USER "$ADMIN_USER"
  set_env GF_SECURITY_ADMIN_PASSWORD "$GEN_GRAFANA_PASS"
  MINIO_ROOT_USER="$(read_env MINIO_ROOT_USER)"
  MINIO_ROOT_USER="${MINIO_ROOT_USER:-moe-admin}"
  set_env MINIO_ROOT_USER "$MINIO_ROOT_USER"
  set_env MINIO_ROOT_PASSWORD "$GEN_MINIO_PASSWORD"
  set_env MINIO_ENDPOINT "moe-storage-garage:3900"
  set_env GARAGE_RPC_SECRET "$GEN_GARAGE_SECRET"
  set_env INSTALL_CODEX false
  set_env INSTALL_OLLAMA false
  set_env OLLAMA_GPU_ENABLED false
  set_env KAFKA_HOST_PORT 9092
  set_env KAFKA_CPU_LIMIT 2
  set_env MCP_HOST_PORT 8003
  set_env LANGGRAPH_HOST_PORT 8002
  set_env LANGGRAPH_CPU_LIMIT 2
  set_env CHROMA_HOST_PORT 8001
  set_env CHROMA_CPU_LIMIT 2
  set_env PROMETHEUS_HOST_PORT 9090
  set_env PROMETHEUS_RETENTION_DAYS 30
  set_env ADMIN_UI_HOST_PORT 8088
  set_env GRAFANA_HOST_PORT 3001
  set_env NODE_EXPORTER_HOST_PORT 9100
  set_env CADVISOR_HOST_PORT 9338
  set_env DOCS_HOST_PORT 8098
  set_env DOZZLE_HOST_PORT 9999
  set_env NEO4J_HTTP_PORT 7474
  set_env NEO4J_BOLT_PORT 7687
  set_env NEO4J_CPU_LIMIT 2
  set_env TZ "${TZ:-Europe/Berlin}"
  # These services are profile-gated, but Compose still interpolates their
  # definitions during validation. Fill their safe local defaults so config
  # validation does not leave malformed empty port mappings behind.
  set_env AUTHENTIK_TAG "$GEN_AUTHENTIK_TAG"
  set_env AUTHENTIK_SECRET_KEY "$GEN_AUTHENTIK_SECRET"
  set_env AUTHENTIK_POSTGRESQL__USER authentik
  set_env AUTHENTIK_POSTGRESQL__NAME authentik
  set_env AUTHENTIK_POSTGRESQL__PASSWORD "$GEN_AUTHENTIK_PG_PASSWORD"
  set_env AUTHENTIK_HTTP_PORT 9000
  set_env AUTHENTIK_HTTPS_PORT 9443
  set_env AUTHENTIK_ERROR_REPORTING__ENABLED false
  set_env APP_BASE_URL "http://localhost:8088"
  set_env PUBLIC_ADMIN_URL "http://localhost:8088"
  set_env PUBLIC_API_URL "http://localhost:8002"
  chmod 600 "$ENV_FILE"
}

prepare_data_roots() {
  echo "  Creating persistent data directories..."
  mkdir -p \
    "${MOE_DATA_ROOT}/kafka-data" \
    "${MOE_DATA_ROOT}/neo4j-data" \
    "${MOE_DATA_ROOT}/neo4j-logs" \
    "${MOE_DATA_ROOT}/agent-logs" \
    "${MOE_DATA_ROOT}/user-audit-logs" \
    "${MOE_DATA_ROOT}/chroma-onnx-cache" \
    "${MOE_DATA_ROOT}/chroma-data" \
    "${MOE_DATA_ROOT}/redis-data" \
    "${MOE_DATA_ROOT}/prometheus-data" \
    "${MOE_DATA_ROOT}/admin-logs" \
    "${MOE_DATA_ROOT}/userdb" \
    "${MOE_DATA_ROOT}/few-shot" \
    "${MOE_DATA_ROOT}/generated" \
    "${MOE_DATA_ROOT}/langgraph-checkpoints" \
    "${MOE_DATA_ROOT}/gap-healer-stats" \
    "${MOE_DATA_ROOT}/checkpoint-archives" \
    "${MOE_DATA_ROOT}/embed-models" \
    "${MOE_DATA_ROOT}/garage/meta" \
    "${MOE_DATA_ROOT}/garage/data" \
    "${MOE_DATA_ROOT}/garage/etc" \
    "${GRAFANA_DATA_ROOT}/data" \
    "${GRAFANA_DATA_ROOT}/dashboards"
  : > "${MOE_DATA_ROOT}/cleanup-config.json"
  : > "${MOE_DATA_ROOT}/cleanup-history.jsonl"

  # Docker Desktop and rootless Podman map host users through a VM. chmod is
  # the portable permission mechanism here; chown to Linux container UIDs is
  # deliberately avoided because it either fails or changes macOS ownership.
  chmod -R a+rwX "$MOE_DATA_ROOT" "$GRAFANA_DATA_ROOT"

  local garage_toml="${MOE_DATA_ROOT}/garage/etc/garage.toml"
  if [[ ! -f "$garage_toml" ]]; then
    cat > "$garage_toml" <<EOF
# Generated by install-macos.sh
metadata_dir = "/var/lib/garage/meta"
data_dir     = "/var/lib/garage/data"
replication_factor = 1
rpc_bind_addr   = "[::]:3901"
rpc_public_addr = "moe-storage-garage:3901"
rpc_secret      = "${GEN_GARAGE_SECRET}"
[s3_api]
s3_region    = "us-east-1"
api_bind_addr = "0.0.0.0:3900"
[s3_web]
bind_addr   = "0.0.0.0:3902"
root_domain = ".web.garage"
index       = "index.html"
[admin]
api_bind_addr = "0.0.0.0:3903"
EOF
  fi
}

validate_and_start() {
  echo "  Validating Compose configuration..."
  (
    cd "$INSTALL_DIR"
    "${COMPOSE_CMD[@]}" config --quiet
  )

  echo "  Building and starting the stack (first run can take several minutes)..."
  (
    cd "$INSTALL_DIR"
    "${COMPOSE_CMD[@]}" pull || true
    "${COMPOSE_CMD[@]}" build
    "${COMPOSE_CMD[@]}" up -d
  )

  local elapsed=0
  echo "  Waiting for the API at http://localhost:8002/metrics..."
  while [[ "$elapsed" -lt 120 ]]; do
    if curl -fsS http://localhost:8002/metrics >/dev/null 2>&1; then
      echo "  API ready ✓"
      return 0
    fi
    printf '.'
    sleep 5
    elapsed=$((elapsed + 5))
  done
  echo ""
  echo "  [!] API is not ready yet. Inspect logs with: ${COMPOSE_CMD[*]} logs langgraph-app"
}

print_banner
ensure_repo
resolve_runtime
echo "  Runtime: ${CONTAINER_RUNTIME} (${COMPOSE_CMD[*]}) ✓"
prepare_env
prepare_data_roots
validate_and_start

echo ""
echo "  MoE Sovereign is installed."
echo "  Admin UI: http://localhost:8088"
echo "  API:      http://localhost:8002"
echo "  Logs:     cd ${INSTALL_DIR} && ${COMPOSE_CMD[*]} logs -f"
echo "  Stop:     cd ${INSTALL_DIR} && ${COMPOSE_CMD[*]} down"
echo ""
echo "  Caddy is intentionally not enabled on macOS: the existing service uses"
echo "  Linux host networking. Put a macOS-compatible reverse proxy in front if"
echo "  you later expose this installation beyond localhost."
