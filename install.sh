#!/usr/bin/env bash
# =============================================================================
#  MoE Sovereign — One-Line Installer
#  Usage: curl -sSL https://raw.githubusercontent.com/h3rb3rn/moe-sovereign/main/install.sh | bash
#         or: bash install.sh
#
#  Supported OS: Debian 11 (bullseye), 12 (bookworm), 13 (trixie)
#                Ubuntu 22.04 (jammy), 24.04 (noble), 25.04 (plucky), 26.04+
#  Requires: sudo access (system packages, Docker install, group membership)
#  Run as the user that will own the installation, NOT as root directly.
# =============================================================================
set -euo pipefail
IFS=$'\n\t'

# The entire installer body lives inside this one function, called only on
# the very last line of the file. `curl | bash` streams the download straight
# into bash's parser as it arrives; without this wrapper, a connection that
# drops partway through would let bash execute every top-level command it had
# already parsed before hitting the cutoff — a truncated, partially-run
# script. A function body has to be fully present (matching braces) before
# bash will parse and run it at all, so a truncated download instead fails
# to define the function (or the trailing call is simply never reached) and
# nothing in it executes.
moe_sovereign_install() {

# --- Interactive-terminal detection ------------------------------------------
# Every prompt below reads from /dev/tty explicitly (not stdin), because under
# `curl | bash` stdin is the script itself, not the user's keyboard. That
# requires an actual controlling terminal to exist. When one doesn't — no pty
# at all (some CI runners, `ssh host cmd` without -t, certain sandboxed/
# automation shells) — opening /dev/tty for the redirect fails outright, and
# under `set -e` that aborted the entire install at the very first prompt
# with a bare "/dev/tty: No such device or address". Detect that once, up
# front, and fall back to accepting every default non-interactively instead
# of crashing. All prompts below already have a sensible `${var:-default}`
# fallback for exactly this case (a real user just pressing ENTER).
# IMPORTANT: the probe must run in a subshell. `exec 3<>/dev/tty` in the
# current shell leaves bash's terminal handling in a state where every
# later `read -p ... < /dev/tty` still reads input correctly but silently
# stops printing its own prompt text — the exact "stdout looks broken,
# have to guess what's being asked" bug. A subshell probes the same fd
# without leaking that state back into the running installer.
if (exec 3<>/dev/tty) 2>/dev/null; then
  HAS_TTY="1"
else
  HAS_TTY="0"
  echo "  [!] No controlling terminal detected — installing non-interactively" >&2
  echo "      and accepting every default shown below. To customize the" >&2
  echo "      installation, either run this script from a real terminal" >&2
  echo "      (download it first: 'curl -O .../install.sh && bash install.sh')" >&2
  echo "      or pre-set the relevant environment variables before running it." >&2
  echo "" >&2
fi

# --- Configurable defaults (override via environment) -----------------------
MOE_REPO_URL="${MOE_REPO_URL:-https://github.com/h3rb3rn/moe-sovereign.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/moe-sovereign}"
MOE_ENV_FILE="${INSTALL_DIR}/.env"

# --- Deploy user detection ---------------------------------------------------
# The deploy user is whoever runs this script. System commands use _sudo().
# If someone mistakenly runs as root via sudo, honour SUDO_USER so ownership
# ends up on the real account.
if [[ $EUID -eq 0 && -n "${SUDO_USER:-}" ]]; then
  DEPLOY_USER="$SUDO_USER"
else
  DEPLOY_USER="${USER:-$(id -un)}"
fi

# Elevate system commands without requiring the whole script to run as root.
_sudo() { if [[ $EUID -eq 0 ]]; then "$@"; else sudo "$@"; fi; }

# Container runtime group (set by Section 4/5; empty means no group needed).
_RT_GROUP=""

# Group-aware compose execution: if the runtime group isn't active yet in this
# session (user was just added), use 'sg' to activate it for the command.
# Falls back to sudo when sg is absent (minimal cloud images omit the login pkg).
_compose() {
  if [[ -n "$_RT_GROUP" ]] && ! id -Gn 2>/dev/null | tr ' ' '\n' | grep -qx "$_RT_GROUP"; then
    if command -v sg &>/dev/null; then
      sg "$_RT_GROUP" -c "${COMPOSE_CMD[*]} $*"
    else
      _sudo "${COMPOSE_CMD[@]}" "$@"
    fi
  else
    "${COMPOSE_CMD[@]}" "$@"
  fi
}

# Idempotent post-up bootstrap for the codex stack:
#   1. Ensure the MinIO bucket lakeFS will use exists.
#   2. Wait for lakeFS to come up, then run its one-shot setup_lakefs API call
#      so the LAKEFS_INSTALLATION_* credentials become valid login keys.
# Both steps are no-ops if the stack is not configured or already bootstrapped.
_bootstrap_codex_stack() {
  local _env="${MOE_ENV_FILE:-${INSTALL_DIR:-.}/.env}"
  [[ -r "$_env" ]] || return 0

  local _eds; _eds="$(grep -E '^INSTALL_CODEX=' "$_env" | cut -d= -f2- | tr -d '"' | tr -d "'")"
  [[ "${_eds:-false}" == "true" ]] || return 0

  local _bucket="lakefs-data"
  local _lake_port; _lake_port="$(grep -E '^LAKEFS_HOST_PORT=' "$_env" | cut -d= -f2- | tr -d '"' | tr -d "'")"
  _lake_port="${_lake_port:-8010}"
  local _akey; _akey="$(grep -E '^LAKEFS_ACCESS_KEY_ID=' "$_env" | cut -d= -f2- | tr -d '"' | tr -d "'")"
  local _skey; _skey="$(grep -E '^LAKEFS_SECRET_ACCESS_KEY=' "$_env" | cut -d= -f2- | tr -d '"' | tr -d "'")"
  local _user; _user="$(grep -E '^ADMIN_USER=' "$_env" | cut -d= -f2- | tr -d '"' | tr -d "'")"
  _user="${_user:-admin}"

  echo "  Bootstrapping codex stack..."

  # Step 1: ensure Garage bucket for lakeFS exists (create + allow key — idempotent).
  local _gkey; _gkey="$(grep -E '^GARAGE_ACCESS_KEY_ID=' "$_env" | cut -d= -f2- | tr -d '"' | tr -d "'")"
  if _compose ps moe-storage-garage 2>/dev/null | grep -q "Up" && [[ -n "$_gkey" ]]; then
    _compose exec -T moe-storage-garage garage bucket create "${_bucket}" 2>/dev/null || true
    _compose exec -T moe-storage-garage garage bucket allow \
      --read --write --owner "${_bucket}" --key "${_gkey}" 2>/dev/null || true
    echo "    Garage bucket '${_bucket}' ensured ✓"
  fi

  # Step 2: wait for lakeFS, then run setup_lakefs (idempotent: returns "already initialized" on second run).
  if [[ -n "$_akey" && -n "$_skey" ]]; then
    local _lake_url="http://localhost:${_lake_port}"
    local _i=0
    while [[ $_i -lt 30 ]]; do
      if curl -sf "${_lake_url}/_health" -o /dev/null 2>/dev/null; then break; fi
      sleep 2; _i=$((_i + 1))
    done
    if curl -sf "${_lake_url}/_health" -o /dev/null 2>/dev/null; then
      local _state; _state="$(curl -s "${_lake_url}/api/v1/setup_lakefs" 2>/dev/null | grep -oE '"state":"[^"]*"' | cut -d'"' -f4)"
      if [[ "$_state" == "not_initialized" ]]; then
        curl -s -X POST "${_lake_url}/api/v1/setup_lakefs" \
          -H "Content-Type: application/json" \
          -d "{\"username\":\"${_user}\",\"key\":{\"access_key_id\":\"${_akey}\",\"secret_access_key\":\"${_skey}\"}}" \
          >/dev/null 2>&1 || true
        echo "    lakeFS bootstrapped with admin '${_user}' ✓"
      else
        echo "    lakeFS already initialised ✓"
      fi
    else
      echo "    [!] lakeFS not reachable on ${_lake_url} — bootstrap skipped, run manually later"
    fi
  fi
  # Update services manifest so the admin dashboard reflects the new stack.
  _write_services_manifest
}


# Write (or overwrite) .moe-services.json next to .env.
# Reads INSTALL_* variables and COMPOSE_PROFILES from the .env file.
# Called after install and after _bootstrap_codex_stack so the admin
# dashboard always shows exactly what was deployed.
_write_services_manifest() {
  local _env="${MOE_ENV_FILE:-${INSTALL_DIR:-.}/.env}"
  local _manifest="${INSTALL_DIR}/.moe-services.json"
  [[ -r "$_env" ]] || return 0

  _renv_local() { grep -E "^${1}=" "$_env" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'"; }

  local _profiles; _profiles="$(_renv_local COMPOSE_PROFILES)"
  local _neo4j=false;      echo "$_profiles" | grep -q "neo4j"      && _neo4j=true
  local _caddy=false;      echo "$_profiles" | grep -q "caddy"      && _caddy=true
  local _auth=false;       echo "$_profiles" | grep -q "authentik"  && _auth=true
  local _monitoring=false; echo "$_profiles" | grep -q "monitoring" && _monitoring=true
  if [[ -z "$_profiles" ]] && [[ "$(_renv_local INSTALL_MONITORING)" != "false" ]]; then
    _monitoring=true
  fi

  local _codex=false
  [[ "$(_renv_local INSTALL_CODEX)" == "true" ]] && _codex=true

  local _ts; _ts="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u)"

  cat > "${_manifest}" <<MANIFEST_EOF
{
  "version": 1,
  "updated_at": "${_ts}",
  "services": {
    "neo4j":                 ${_neo4j},
    "caddy":                 ${_caddy},
    "authentik":             ${_auth},
    "monitoring":            ${_monitoring},
    "akhq":                  ${_monitoring},
    "dozzle":                ${_monitoring},
    "docs":                  true,
    "prometheus_stack":      ${_monitoring},
    "codex": ${_codex}
  }
}
MANIFEST_EOF
  echo "  Services manifest written ✓  (${_manifest})"
}

# =============================================================================
#  SECTION 1: Banner
# =============================================================================
print_banner() {
  cat <<'EOF'

  ███╗   ███╗ ██████╗ ███████╗    ███████╗ ██████╗ ██╗   ██╗███████╗██████╗
  ████╗ ████║██╔═══██╗██╔════╝    ██╔════╝██╔═══██╗██║   ██║██╔════╝██╔══██╗
  ██╔████╔██║██║   ██║█████╗      ███████╗██║   ██║██║   ██║█████╗  ██████╔╝
  ██║╚██╔╝██║██║   ██║██╔══╝      ╚════██║██║   ██║╚██╗ ██╔╝██╔══╝  ██╔══██╗
  ██║ ╚═╝ ██║╚██████╔╝███████╗    ███████║╚██████╔╝ ╚████╔╝ ███████╗██║  ██║
  ╚═╝     ╚═╝ ╚═════╝ ╚══════╝    ╚══════╝ ╚═════╝   ╚═══╝  ╚══════╝╚═╝  ╚═╝

           Sovereign Mixture-of-Experts Orchestrator — Installer
  ==========================================================================

EOF
}

print_banner

echo "  System requirements (realistic usage, not container limits):"
echo "    Core stack, no Neo4j  :  4 GB RAM minimum,  8 GB recommended"
echo "    Core stack + Neo4j    :  6 GB RAM minimum, 10 GB recommended"
echo "    + MoE Codex           : +8.5 GB RAM on top of the above"
echo "    Disk                  : 20 GB minimum (images + data volumes)"
echo ""

# =============================================================================
#  SECTION 1a: Installation paths
# =============================================================================
# Prompted early so Section 1b (update detection) uses the correct MOE_ENV_FILE.
echo "=========================================================================="
echo "  Installation Paths"
echo "=========================================================================="
echo ""
echo "  Press ENTER to accept the default shown in [brackets]."
echo ""

_default_install="${INSTALL_DIR:-/opt/moe-sovereign}"
[[ "$HAS_TTY" == "1" ]] && read -rp "  App installation directory [${_default_install}]: " _tmp_install < /dev/tty
INSTALL_DIR="${_tmp_install:-${_default_install}}"
MOE_ENV_FILE="${INSTALL_DIR}/.env"

echo ""
echo "  Install dir: ${INSTALL_DIR}"
echo ""

# =============================================================================
#  SECTION 1b: Re-run detection — UPDATE MODE
# =============================================================================
# Trigger: existing .env + at least one data volume present.
# We NEVER overwrite credentials: Postgres, Neo4j, Redis and Grafana write
# their passwords into their data volumes on first init — regenerating .env
# would lock every service out of its own data.
#
# Update mode runs 5 steps — git pull is FIRST so subsequent steps always
# work with the newest code and the newest .env.example:
#   1. git pull                    (with dirty-tree guard and diverge recovery)
#   2. .env migration              (add new keys from .env.example; skip secrets)
#   3. .env plausibility check     (self-heal blank non-secret keys; flag the rest)
#   4. Re-apply volume ownership   (fixes container UID mismatches)
#   5. docker/podman compose build + up
# Use an array so "docker compose" is two words regardless of IFS=$'\n\t'.
_upd_rt=()
if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
  _upd_rt=(docker compose)
elif command -v podman &>/dev/null; then
  if podman compose version &>/dev/null 2>&1; then
    _upd_rt=(podman compose)
  elif command -v podman-compose &>/dev/null; then
    _upd_rt=(podman-compose)
  else
    _upd_rt=(podman compose)
  fi
fi

# Helper: read a key from existing .env — defined early so it's available
# during volume detection (line ~128) as well as the full update block.
_renv() { grep -E "^${1}=" "${MOE_ENV_FILE}" 2>/dev/null | head -1 | cut -d= -f2-; }

# Container UID/GID ownership helpers — defined early so the update path
# (which runs as top-level code) can call them before the fresh-install block.
#
# Three strategies for assigning correct ownership to volume directories:
# 1. `podman unshare chown` — runs inside the user namespace (native Linux).
# 2. Direct `sudo chown <host-uid>` — WSL2 fallback; maps container UIDs via
#    /etc/subuid offsets.
# 3. chmod a+rwX — last resort when both chown strategies fail.
_container_uid_to_host_uid() {
  local cuid="$1"
  if [[ "$cuid" -eq 0 ]]; then
    id -u "${DEPLOY_USER}" 2>/dev/null || echo "${UID:-1000}"
  else
    local _sub
    _sub=$(grep -m1 "^${DEPLOY_USER}:" /etc/subuid 2>/dev/null | cut -d: -f2)
    [[ -n "$_sub" ]] && echo $(( _sub + cuid - 1 )) || echo "$cuid"
  fi
}

_chown_for_container() {
  local uid="$1" gid="$2"; shift 2
  local _ok=0
  if [[ "${CONTAINER_RUNTIME}" == "podman" ]]; then
    # Strategy 1: podman unshare (native Linux)
    _podman_as_user unshare chown -R "${uid}:${gid}" "$@" 2>/dev/null && _ok=1 || true
    if [[ $_ok -eq 0 ]]; then
      # Strategy 2: direct sudo chown with computed host UIDs (WSL2 fallback)
      local h_uid h_gid
      h_uid=$(_container_uid_to_host_uid "$uid")
      h_gid=$(_container_uid_to_host_uid "$gid")
      _sudo chown -R "${h_uid}:${h_gid}" "$@" 2>/dev/null && _ok=1 || true
    fi
  else
    _sudo chown -R "${uid}:${gid}" "$@" 2>/dev/null && _ok=1 || true
  fi
  if [[ $_ok -eq 1 ]]; then
    _sudo chmod -R u+rwX "$@" 2>/dev/null || true
  else
    # Strategy 3: world-writable fallback
    echo "  [!] chown ${uid}:${gid} failed for $* — applying fallback chmod a+rwX"
    _sudo chmod -R a+rwX "$@" 2>/dev/null || true
  fi
}

if [[ -f "${MOE_ENV_FILE}" ]] && [[ ${#_upd_rt[@]} -gt 0 ]]; then
  # Volume detection: check both named volumes AND the data-root directory so
  # the update path is also triggered on installs that pre-date named volumes.
  if [[ "${_upd_rt[0]}" == podman* ]]; then
    vol_count=$(podman volume ls -q 2>/dev/null \
      | grep -cE '(terra_checkpoints|neo4j|redis|cache|grafana)' || true)
  else
    vol_count=$(docker volume ls -q 2>/dev/null \
      | grep -cE '(terra_checkpoints|neo4j|redis|cache|grafana)' || true)
  fi
  # Fallback: if no named volumes found, check whether the data directory exists
  # and contains container data (covers bind-mount installs and podman rootless).
  if [[ "${vol_count:-0}" -eq 0 ]]; then
    _upd_data_root="$(_renv MOE_DATA_ROOT)"
    if [[ -n "$_upd_data_root" && -d "${_upd_data_root}/redis-data" ]]; then
      vol_count=1
    fi
  fi
  if [[ "${vol_count:-0}" -gt 0 ]]; then
    # Run every remaining step from INSTALL_DIR — most operations below already
    # use absolute paths, but compose itself needs its cwd to be the project
    # directory. Doing this once, up front, means no step has to remember it.
    cd "${INSTALL_DIR}"

    # ── Read current version before any changes ───────────────────────────────
    _upd_ver_before=""
    if [[ -d "${INSTALL_DIR}/.git" ]]; then
      _upd_ver_before=$(git -C "${INSTALL_DIR}" describe --tags --always 2>/dev/null \
        || git -C "${INSTALL_DIR}" log -1 --format='%h (%s)' 2>/dev/null \
        || echo "unknown")
    fi

    echo ""
    echo "  =================================================================="
    echo "  UPDATE MODE — existing installation detected"
    echo "  =================================================================="
    echo "  Found .env at ${MOE_ENV_FILE}"
    echo "  Found ${vol_count} data volume(s) from a previous install."
    echo "  Current version : ${_upd_ver_before:-unknown}"
    echo ""
    echo "  Credentials will NOT be changed. Running:"
    echo "    1. git pull (with dirty-tree and diverge handling)"
    echo "    2. Migrate .env — add new keys from .env.example, preserve all existing values"
    echo "    3. Re-apply volume permissions"
    echo "    4. ${_upd_rt[*]} build + up -d"
    echo "  =================================================================="
    echo ""

    # chown needs sudo — verify access early so we fail fast.
    if ! _sudo true 2>/dev/null; then
      echo "[ERROR] Update mode requires sudo access for volume ownership."
      exit 1
    fi

    _upd_data=$(_renv MOE_DATA_ROOT);     _upd_data="${_upd_data:-/opt/moe-sovereign}"
    _upd_graf=$(_renv GRAFANA_DATA_ROOT); _upd_graf="${_upd_graf:-/opt/grafana}"

    # ── STEP 1: git pull (with dirty-tree and diverge handling) ──────────────
    # Pull first so subsequent steps use the latest code and latest .env.example.
    _skip_pull=0
    if [[ -d "${INSTALL_DIR}/.git" ]]; then
      _dirty=$(git -C "${INSTALL_DIR}" status --porcelain 2>/dev/null || true)
      if [[ -n "$_dirty" ]]; then
        echo ""
        echo "  [!] Local modifications detected in ${INSTALL_DIR}:"
        echo "$_dirty" | head -10 | sed 's/^/      /'
        [[ $(echo "$_dirty" | wc -l) -gt 10 ]] && echo "      ... (truncated)"
        echo ""
        echo "  Choose how to handle local changes:"
        echo "    s) Stash changes, then pull  (recommended — changes are saved)"
        echo "    k) Keep changes — skip git pull"
        echo "    a) Abort update"
        [[ "$HAS_TTY" == "1" ]] && read -rp "  Choice [s/k/a] (default: s): " _pull_choice < /dev/tty
        _pull_choice="${_pull_choice:-s}"
        case "${_pull_choice,,}" in
          s|stash)
            if git -C "${INSTALL_DIR}" stash push \
                -m "install.sh update stash $(date +%Y%m%d-%H%M%S)" 2>/dev/null; then
              echo "  Changes stashed ✓  (restore later: git -C ${INSTALL_DIR} stash pop)"
            else
              echo "  [!] Stash failed — skipping pull to preserve local changes."
              _skip_pull=1
            fi
            ;;
          k|keep)
            echo "  Keeping local changes — skipping git pull."
            _skip_pull=1
            ;;
          *)
            echo "  Update aborted."
            exit 1
            ;;
        esac
      fi

      if [[ ${_skip_pull} -eq 0 ]]; then
        echo "  [1/5] Pulling latest code..."
        if ! git -C "${INSTALL_DIR}" pull --ff-only 2>&1; then
          # Fast-forward failed — branches may have diverged (e.g. force-push upstream)
          echo ""
          echo "  [!] Fast-forward pull failed — local and remote branches have diverged."
          echo "      (This can happen after a force-push to the upstream repository.)"
          echo ""
          echo "  Options:"
          echo "    r) Reset to origin  (discards local commits — data volumes are safe)"
          echo "    k) Keep current code — skip pull"
          echo "    a) Abort"
          [[ "$HAS_TTY" == "1" ]] && read -rp "  Choice [r/k/a] (default: k): " _ff_choice < /dev/tty
          _ff_choice="${_ff_choice:-k}"
          case "${_ff_choice,,}" in
            r|reset)
              _upd_branch=$(git -C "${INSTALL_DIR}" rev-parse --abbrev-ref HEAD \
                2>/dev/null || echo "main")
              git -C "${INSTALL_DIR}" fetch origin
              git -C "${INSTALL_DIR}" reset --hard "origin/${_upd_branch}"
              echo "  Reset to origin/${_upd_branch} ✓"
              ;;
            k|keep)
              echo "  Keeping current code."
              _skip_pull=1
              ;;
            *)
              echo "  Update aborted."
              exit 1
              ;;
          esac
        fi
      fi

      _upd_ver_after=$(git -C "${INSTALL_DIR}" describe --tags --always 2>/dev/null \
        || git -C "${INSTALL_DIR}" log -1 --format='%h (%s)' 2>/dev/null \
        || echo "unknown")
      if [[ "${_upd_ver_before}" != "${_upd_ver_after}" ]]; then
        echo "  [1/5] Code updated: ${_upd_ver_before} → ${_upd_ver_after} ✓"
      else
        echo "  [1/5] Already at latest (${_upd_ver_after}) ✓"
      fi
    else
      echo "  [1/5] ${INSTALL_DIR} is not a git repo — skipping pull."
      _upd_ver_after="${_upd_ver_before:-unknown}"
    fi

    # ── STEP 2: .env migration ────────────────────────────────────────────────
    # Runs after git pull so .env.example is the freshly-fetched version.
    # Phase A: legacy hardcoded keys (kept for installs predating .env.example)
    if [[ -z "$(_renv COMPOSE_PROFILES)" ]]; then
      if docker ps -a --filter "name=moe-caddy" --format '{{.Names}}' 2>/dev/null \
          | grep -q "moe-caddy"; then
        echo "COMPOSE_PROFILES=caddy" >> "${MOE_ENV_FILE}"
        echo "  [migrate] COMPOSE_PROFILES=caddy (existing Caddy container detected) ✓"
      else
        echo "COMPOSE_PROFILES=" >> "${MOE_ENV_FILE}"
        echo "  [migrate] COMPOSE_PROFILES= (no Caddy container) ✓"
      fi
    fi
    for _cpu_var in NEO4J_CPU_LIMIT LANGGRAPH_CPU_LIMIT KAFKA_CPU_LIMIT CHROMA_CPU_LIMIT; do
      if [[ -z "$(_renv ${_cpu_var})" ]]; then
        echo "${_cpu_var}=2" >> "${MOE_ENV_FILE}"
      fi
    done

    # Phase B: generic sync from .env.example — add any key not yet in .env.
    # Credential/secret keys get an empty placeholder so the admin is alerted;
    # non-secret keys get the example default value directly.
    _env_example="${INSTALL_DIR}/.env.example"
    _migrated=0
    if [[ -f "${_env_example}" ]]; then
      while IFS= read -r _line; do
        [[ "$_line" =~ ^[[:space:]]*# ]] && continue   # skip comments
        [[ -z "${_line// }"            ]] && continue   # skip blank lines
        _ekey="${_line%%=*}"
        [[ -z "$_ekey"                 ]] && continue
        if ! grep -qE "^${_ekey}=" "${MOE_ENV_FILE}" 2>/dev/null; then
          if echo "$_ekey" | grep -qiE '(PASSWORD|SECRET|_PASS$|_KEY$|PRIVATE)'; then
            # Credential — leave empty; admin must set manually if needed
            printf '%s=\n' "${_ekey}" >> "${MOE_ENV_FILE}"
            echo "  [migrate] ${_ekey}= (credential — set manually if needed)"
          else
            _eval="${_line#*=}"
            printf '%s\n' "${_line}" >> "${MOE_ENV_FILE}"
            echo "  [migrate] ${_ekey}=${_eval}"
          fi
          (( _migrated++ )) || true
        fi
      done < "${_env_example}"
    fi
    if [[ ${_migrated} -gt 0 ]]; then
      echo "  [2/5] .env migrated — ${_migrated} new key(s) added ✓"
    else
      echo "  [2/5] .env up to date ✓"
    fi

    # ── STEP 3: .env plausibility check ───────────────────────────────────────
    # A blank value for a key is either (a) a credential the admin hasn't set
    # yet — expected, nothing to do — or (b) a non-secret config key that
    # ended up empty by accident (stale migration bug, manual edit gone
    # wrong, ...). For (b) we self-heal from .env.example's own default,
    # since that's exactly what Phase B above would have written had the key
    # not already existed. We also catch the case where a key holds a value
    # that doesn't parse where .env.example's default is a plain integer —
    # that can't be auto-corrected (we don't know the intended number), so
    # it's reported instead. Duplicate "${_ekey}=" lines (an artifact of
    # earlier buggy runs appending a second, blank copy of a key that
    # already existed) are collapsed into a single correct line; compose
    # itself would otherwise silently use whichever duplicate comes last.
    _plaus_fixed=0
    _plaus_manual=()
    if [[ -f "${_env_example}" ]]; then
      while IFS= read -r _line; do
        [[ "$_line" =~ ^[[:space:]]*# ]] && continue
        [[ -z "${_line// }"            ]] && continue
        _ekey="${_line%%=*}"
        [[ -z "$_ekey"                 ]] && continue
        _exval="${_line#*=}"
        _dup_count="$(grep -cE "^${_ekey}=" "${MOE_ENV_FILE}" 2>/dev/null || echo 0)"
        # Last *non-empty* occurrence wins here, not simply the last line: a
        # stray blank duplicate appended after a real, user-customized value
        # must not make us throw that value away in favor of the generic
        # .env.example default. (Compose itself has no such preference — it
        # always takes the literal last line — which is exactly why any
        # duplicate at all needs collapsing below.)
        _curval="$(awk -F= -v k="${_ekey}" \
          'index($0, k "=") == 1 { v2 = substr($0, length(k) + 2); if (v2 != "") v = v2 } END { print v }' \
          "${MOE_ENV_FILE}" 2>/dev/null)"

        _newval=""; _reason=""
        if [[ -z "${_curval}" ]]; then
          # Credentials are meant to stay empty until the admin sets them —
          # already communicated by Phase B, nothing more to check here.
          echo "$_ekey" | grep -qiE '(PASSWORD|SECRET|_PASS$|_KEY$|PRIVATE)' && continue
          [[ -z "${_exval}" ]] && continue   # .env.example is blank too — intentionally optional
          _newval="${_exval}"; _reason="was empty — restored default"
        elif [[ "${_dup_count}" -gt 1 ]]; then
          _newval="${_curval}"; _reason="had duplicate lines — collapsed to"
        fi

        if [[ -n "${_newval}" ]]; then
          grep -vE "^${_ekey}=" "${MOE_ENV_FILE}" > "${MOE_ENV_FILE}.tmp" || true
          printf '%s=%s\n' "${_ekey}" "${_newval}" >> "${MOE_ENV_FILE}.tmp"
          mv "${MOE_ENV_FILE}.tmp" "${MOE_ENV_FILE}"
          echo "  [plausibility-fix] ${_ekey} ${_reason} '${_newval}'"
          (( _plaus_fixed++ )) || true
        elif [[ "${_exval}" =~ ^-?[0-9]+$ ]] && ! [[ "${_curval}" =~ ^-?[0-9]+$ ]]; then
          _plaus_manual+=("${_ekey}=${_curval}  (expected a whole number, e.g. ${_exval})")
        fi
      done < "${_env_example}"
    fi

    # Cross-check against config.py's own int(os.getenv(...)) call sites too.
    # .env.example's committed value is normally a reliable stand-in for the
    # type config.py expects, but it can drift (e.g. config.py parsing a key
    # with int() while .env.example — correctly — ships a fractional-seconds
    # value for it): read the requirement from the code that actually does
    # the parsing, not from the template.
    _config_py="${INSTALL_DIR}/config.py"
    if [[ -f "${_config_py}" ]]; then
      while IFS= read -r _ikey; do
        [[ -z "${_ikey}" ]] && continue
        # awk, not grep|tail|cut: under `set -o pipefail`, a grep that matches
        # nothing (any key config.py references but .env doesn't define yet)
        # makes the whole pipeline's exit status non-zero, and `set -e` kills
        # the script right here with no error message — awk's END block
        # always runs and exits 0, match or no match.
        _curval="$(awk -F= -v k="${_ikey}" \
          'index($0, k "=") == 1 { v2 = substr($0, length(k) + 2); if (v2 != "") v = v2 } END { print v }' \
          "${MOE_ENV_FILE}" 2>/dev/null)"
        [[ -z "${_curval}" ]] && continue                      # blank — already handled above
        [[ "${_curval}" =~ ^-?[0-9]+$ ]] && continue            # already a valid integer
        printf '%s\n' "${_plaus_manual[@]+"${_plaus_manual[@]}"}" | grep -qF "${_ikey}=" && continue  # already flagged above
        _plaus_manual+=("${_ikey}=${_curval}  (config.py requires a whole number)")
      done < <(grep -oE 'int\(os\.getenv\("[A-Z0-9_]+"' "${_config_py}" | sed -E 's/^int\(os\.getenv\("//; s/"$//')
    fi

    if [[ ${#_plaus_manual[@]} -gt 0 ]]; then
      echo ""
      echo "  [!] .env plausibility check found value(s) that need a human to fix:"
      for _pm in "${_plaus_manual[@]}"; do
        echo "      - ${_pm}"
      done
      echo "      Edit ${MOE_ENV_FILE}, then re-run: sudo ${_upd_rt[*]} up -d"
      echo ""
    fi
    if [[ ${_plaus_fixed} -gt 0 ]]; then
      echo "  [3/5] .env plausibility check — ${_plaus_fixed} value(s) restored ✓"
    else
      echo "  [3/5] .env plausibility check — no issues found ✓"
    fi

    # ── STEP 4: Re-apply container UID ownership ──────────────────────────────
    _sudo mkdir -p \
      "${_upd_data}/kafka-data"        "${_upd_data}/neo4j-data"     \
      "${_upd_data}/neo4j-logs"        "${_upd_data}/agent-logs"     \
      "${_upd_data}/user-audit-logs"   "${_upd_data}/chroma-onnx-cache" \
      "${_upd_data}/chroma-data"       "${_upd_data}/redis-data"     \
      "${_upd_data}/prometheus-data"   "${_upd_data}/admin-logs"     \
      "${_upd_data}/userdb"            "${_upd_data}/few-shot"       \
      "${_upd_data}/jupyterlab"        "${_upd_data}/garage/meta"    \
      "${_upd_data}/garage/data"       "${_upd_data}/garage/etc"     \
      "${_upd_graf}/data"              "${_upd_graf}/dashboards"     \
      2>/dev/null || true

    # _chown_for_container reads CONTAINER_RUNTIME, which is only assigned later
    # in the fresh-install path. Derive it now from _upd_rt so the update path
    # works correctly without waiting for the full runtime-detection block.
    [[ "${_upd_rt[0]}" == podman* ]] && CONTAINER_RUNTIME="podman" || CONTAINER_RUNTIME="docker"

    _upd_chown() {
      # Delegate to _chown_for_container so the sudo fallback uses correctly
      # remapped host UIDs under rootless Podman (subuid offset applied).
      _chown_for_container "$@"
    }
    _upd_chown 1000 1000   "${_upd_data}/kafka-data"
    # postgres:17-alpine runs as UID 70 internally. With rootless Podman, container
    # UID 0 maps to the invoking host user and the entrypoint can chmod to 70 inside,
    # so 0:0 is correct on disk. With rootful Docker, container UID 0 == host root;
    # the postgres process (UID 70) then cannot read root-owned data files, causing
    # FATAL: could not open file "global/pg_filenode.map": Permission denied on
    # every reconnect. Pick the host-side UID that matches the engine's mapping.
    if [[ "${_upd_rt[0]}" == podman* ]]; then
      _upd_chown 0  0  "${_upd_data}/langgraph-checkpoints"
    else
      _upd_chown 70 70 "${_upd_data}/langgraph-checkpoints"
    fi
    _upd_chown 0    0      "${_upd_data}/redis-data"
    _upd_chown 0    0      "${_upd_data}/neo4j-data"
    _upd_chown 0    0      "${_upd_data}/neo4j-logs"
    _upd_chown 0    0      "${_upd_data}/chroma-data"
    _upd_chown 0    0      "${_upd_data}/chroma-onnx-cache"
    _upd_chown 0    0      "${_upd_data}/garage/meta"
    _upd_chown 0    0      "${_upd_data}/garage/data"
    _upd_chown 0    0      "${_upd_data}/jupyterlab"
    _upd_chown 65534 65534 "${_upd_data}/prometheus-data"
    _upd_chown 1001 0      "${_upd_data}/agent-logs"
    _upd_chown 1001 0      "${_upd_data}/user-audit-logs"
    _upd_chown 1001 0      "${_upd_data}/admin-logs"
    _upd_chown 1001 0      "${_upd_data}/generated"
    _upd_chown 1001 0      "${_upd_data}/gap-healer-stats"
    _upd_chown 1001 0      "${_upd_data}/checkpoint-archives"
    _upd_chown 472  472    "${_upd_graf}/data" "${_upd_graf}/dashboards"

    echo "  [4/5] Volume permissions reset ✓"

    # ── STEP 5: Rebuild and restart containers ────────────────────────────────
    # (already running from INSTALL_DIR — cd'd there as soon as the update was
    # confirmed, see top of this block)
    echo "  [5/5] Rebuilding containers..."
    _upd_group=""; [[ "${_upd_rt[0]}" == "docker" ]] && _upd_group="docker"
    _upd_q="";    [[ "${_upd_rt[0]}" == "docker" ]] && _upd_q="--quiet"
    # Read active compose profiles from .env so optional services (caddy, neo4j,
    # authentik) are included in the update — compose up without --profile flags
    # silently skips any service that declares a profile.
    _upd_profiles=()
    _prof_val="$(grep -E '^COMPOSE_PROFILES=' "${MOE_ENV_FILE}" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")"
    IFS=',' read -ra _prof_arr <<< "${_prof_val}"
    for _p in "${_prof_arr[@]}"; do
      [[ -n "$_p" ]] && _upd_profiles+=(--profile "$_p")
    done
    # Podman-compose does not recreate containers with network-namespace
    # dependencies automatically. Bring the stack down first so every container
    # is removed cleanly before the new image is started.
    # Docker handles recreation in-place; the extra down is a no-op there.
    if [[ "${_upd_rt[0]}" == podman* ]]; then
      echo "  [5/5] Stopping existing containers (Podman)..."
      "${_upd_rt[@]}" "${_upd_profiles[@]}" down 2>/dev/null || true

      # After compose down, bind-mount data directories may be owned by UIDs
      # that are outside the rootless Podman subuid range (e.g. host UID 999
      # for Valkey, 70 for PostgreSQL). In rootless Podman without keep-id,
      # container root (UID 0) maps to the current user (typically UID 1000).
      # Files owned by any other host UID are inaccessible to container root,
      # causing entrypoint chown calls to fail with "Operation not permitted".
      #
      # Fix: reset ownership to the current user (= container root) so the
      # container entrypoints can re-apply their own UID/GID on startup.
      _cur_uid="$(id -u)"
      _cur_gid="$(id -g)"
      for _vol_dir in \
          "${_upd_data}/redis-data" \
          "${_upd_data}/langgraph-checkpoints" \
          "${_upd_data}/chroma-data" \
          "${_upd_data}/neo4j-data" \
          "${_upd_data}/neo4j-logs"; do
        if [[ -d "$_vol_dir" ]]; then
          _sudo chmod -R u+rwX "$_vol_dir" 2>/dev/null || true
          _sudo chown -R "${_cur_uid}:${_cur_gid}" "$_vol_dir" 2>/dev/null || true
        fi
      done
      echo "  Volume permissions reset for rootless Podman ✓"
    fi

    # Remove stale Neo4j Java file locks that survive a crashed JVM.
    # The JVM holds store_lock / database_lock as OS-level file locks; if the
    # process is killed (OOM, SIGKILL) without a clean shutdown these files are
    # left on disk and prevent Neo4j from starting with "Lock file locked by
    # another process". Safe to remove when the container is confirmed stopped.
    for _neo4j_lock in \
        "${_upd_data}/neo4j-data/databases/store_lock" \
        "${_upd_data}/neo4j-data/databases/neo4j/database_lock" \
        "${_upd_data}/neo4j-data/databases/system/database_lock" \
        "${_upd_data}/neo4j-data/databases/neo4j/neostore" ; do
      if [[ -f "$_neo4j_lock" ]]; then
        _sudo rm -f "$_neo4j_lock" 2>/dev/null || true
        echo "  [cleanup] stale neo4j lock removed: $(basename "$_neo4j_lock") ✓"
      fi
    done

    # Opt out of the bake backend on Docker >= v2.37 when COMPOSE_BAKE is not
    # already set — bake requires --allow=network.host if any build block had
    # "network: host", which we no longer ship but guard here defensively.
    if [[ "${_upd_rt[0]}" == "docker" ]] && [[ "${COMPOSE_BAKE:-}" != "0" ]] && [[ "${COMPOSE_BAKE:-}" != "1" ]]; then
      _upd_compose_minor=$(docker compose version --short 2>/dev/null | cut -d. -f2 || echo "0")
      [[ "${_upd_compose_minor:-0}" -ge 37 ]] && export COMPOSE_BAKE=0
    fi
    if [[ -n "$_upd_group" ]] && ! id -Gn 2>/dev/null | tr ' ' '\n' | grep -qx "$_upd_group"; then
      if command -v sg &>/dev/null; then
        sg "$_upd_group" -c "COMPOSE_BAKE=${COMPOSE_BAKE:-} ${_upd_rt[*]} ${_upd_profiles[*]} build ${_upd_q}"
        sg "$_upd_group" -c "${_upd_rt[*]} ${_upd_profiles[*]} up -d"
      else
        _sudo "${_upd_rt[@]}" "${_upd_profiles[@]}" build ${_upd_q}
        _sudo "${_upd_rt[@]}" "${_upd_profiles[@]}" up -d
      fi
    else
      "${_upd_rt[@]}" "${_upd_profiles[@]}" build ${_upd_q}
      "${_upd_rt[@]}" "${_upd_profiles[@]}" up -d
    fi
    # Start MoE Codex if it was enabled during initial install
    _upd_eds="$(grep -E '^INSTALL_CODEX=' "${MOE_ENV_FILE}" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")"
    if [[ "${_upd_eds:-false}" == "true" ]]; then
      if [[ -n "$_upd_group" ]] && ! id -Gn 2>/dev/null | tr ' ' '\n' | grep -qx "$_upd_group"; then
        if command -v sg &>/dev/null; then
          sg "$_upd_group" -c "${_upd_rt[*]} -f docker-compose.codex.yml --profile codex up -d"
        else
          _sudo "${_upd_rt[@]}" -f docker-compose.codex.yml --profile codex up -d
        fi
      else
        "${_upd_rt[@]}" -f docker-compose.codex.yml --profile codex up -d
      fi
      _bootstrap_codex_stack
    fi
    echo "  Containers started ✓"
    _write_services_manifest

    # ── Health check ──────────────────────────────────────────────────────────
    echo ""
    echo "  Waiting for MoE Sovereign API to become ready..."
    _health_url="http://localhost:8002/metrics"
    _max_wait=120; _interval=5; _elapsed=0
    while true; do
      if curl -sf "${_health_url}" &>/dev/null; then
        echo "  API ready ✓"
        break
      fi
      if [[ ${_elapsed} -ge ${_max_wait} ]]; then
        echo ""
        echo "  [!] API did not respond within ${_max_wait}s."
        echo "      Check logs: cd ${INSTALL_DIR} && sudo ${_upd_rt[*]} logs langgraph-app"
        break
      fi
      printf "."
      sleep "${_interval}"
      _elapsed=$(( _elapsed + _interval ))
    done

    # ── Summary ───────────────────────────────────────────────────────────────
    echo ""
    echo "  =================================================================="
    echo "  MoE Sovereign updated successfully."
    echo ""
    if [[ "${_upd_ver_before:-unknown}" != "${_upd_ver_after:-unknown}" ]]; then
      echo "  Version : ${_upd_ver_before:-unknown} → ${_upd_ver_after:-unknown}"
    else
      echo "  Version : ${_upd_ver_after:-unknown} (no code change)"
    fi
    echo ""
    echo "  Logs:    cd ${INSTALL_DIR} && sudo ${_upd_rt[*]} logs -f"
    echo "  Status:  cd ${INSTALL_DIR} && sudo ${_upd_rt[*]} ps"
    echo "  =================================================================="
    echo ""
    exit 0
  fi
fi

# =============================================================================
#  SECTION 2: Sudo capability check
# =============================================================================
if [[ $EUID -ne 0 ]]; then
  echo "  Deploy user: ${DEPLOY_USER}"
  # Use sudo -n (non-interactive) rather than sudo -v: sudo -v requires a TTY
  # to validate credentials even with NOPASSWD, while -n just tests capability.
  if ! sudo -n true 2>/dev/null; then
    echo "[ERROR] '${DEPLOY_USER}' needs sudo access for system-level operations."
    echo "        Add to sudoers: echo '${DEPLOY_USER} ALL=(ALL) NOPASSWD:ALL' | sudo tee /etc/sudoers.d/${DEPLOY_USER}"
    exit 1
  fi
  echo "  Sudo access confirmed ✓"
else
  echo "  Running as root (SUDO_USER=${SUDO_USER:-none}) → deploy user: ${DEPLOY_USER} ✓"
fi

# =============================================================================
#  SECTION 3: OS detection
# =============================================================================
echo "[1/9] Detecting operating system..."

if [[ ! -f /etc/os-release ]]; then
  echo "[ERROR] /etc/os-release not found. Cannot detect OS."
  exit 1
fi

source /etc/os-release

DISTRO_ID="${ID:-}"

case "${DISTRO_ID}" in
  debian)
    VERSION_MAJOR="${VERSION_ID%%.*}"
    case "${VERSION_MAJOR}" in
      11) VERSION_CODENAME="bullseye" ;;
      12) VERSION_CODENAME="bookworm" ;;
      13) VERSION_CODENAME="trixie"   ;;
      *)
        echo "[ERROR] Unsupported Debian version: ${VERSION_ID}."
        echo "        Supported: Debian 11 (bullseye), 12 (bookworm), 13 (trixie)."
        exit 1
        ;;
    esac
    ;;
  ubuntu)
    # Ubuntu ships VERSION_CODENAME in /etc/os-release — use it directly.
    # Only validate the version number; the codename comes from the OS.
    case "${VERSION_ID}" in
      22.04|24.04|25.04|26.04) ;;
      *)
        echo "[ERROR] Unsupported Ubuntu version: ${VERSION_ID}."
        echo "        Supported: Ubuntu 22.04 (jammy), 24.04 (noble), 25.04 (plucky), 26.04."
        exit 1
        ;;
    esac
    # VERSION_CODENAME is already set by /etc/os-release on Ubuntu.
    ;;
  *)
    echo "[ERROR] Unsupported OS: ${PRETTY_NAME:-unknown}."
    echo "        Supported: Debian 11–13, Ubuntu 22.04–26.04."
    exit 1
    ;;
esac

echo "  OS: ${PRETTY_NAME} (${VERSION_CODENAME}) ✓"

# =============================================================================
#  SECTION 4: Container runtime detection
# =============================================================================
echo "[2/9] Detecting container runtime..."

CONTAINER_RUNTIME=""
COMPOSE=""        # display / .env string
COMPOSE_CMD=()    # executable array — immune to IFS=$'\n\t'

# Checks whether docker-compose plugin is registered — uses "docker info" which
# we know works, avoiding any dependency on "docker compose version" exit codes.
_docker_has_compose() {
  docker info --format '{{range .ClientInfo.Plugins}}{{.Name}} {{end}}' 2>/dev/null \
    | grep -qw "compose"
}

_resolve_podman_compose() {
  if podman compose version &>/dev/null 2>&1; then
    COMPOSE="podman compose"; COMPOSE_CMD=(podman compose)
  elif command -v podman-compose &>/dev/null; then
    COMPOSE="podman-compose"; COMPOSE_CMD=(podman-compose)
  else
    COMPOSE="podman compose"; COMPOSE_CMD=(podman compose)
  fi
}

if command -v docker &>/dev/null && docker info &>/dev/null 2>&1; then
  DOCKER_VERSION=$(docker --version 2>/dev/null | awk '{print $3}' | tr -d ',')
  CONTAINER_RUNTIME="docker"
  COMPOSE="docker compose"; COMPOSE_CMD=(docker compose)
  if _docker_has_compose; then
    echo "  Docker ${DOCKER_VERSION} + compose plugin detected ✓"
  else
    echo "  Docker ${DOCKER_VERSION} detected (compose plugin missing — will install) ✓"
  fi
elif command -v podman &>/dev/null; then
  PODMAN_VERSION=$(podman --version 2>/dev/null | awk '{print $3}')
  CONTAINER_RUNTIME="podman"
  _resolve_podman_compose
  echo "  Podman ${PODMAN_VERSION} detected ✓"
else
  echo ""
  echo "  No container runtime found. Choose one to install:"
  echo ""
  echo "  1) Docker CE  — industry-standard daemon-based engine, broad ecosystem"
  echo "  2) Podman     — daemonless, rootless OCI runtime, drop-in compatible"
  echo ""
  while true; do
    [[ "$HAS_TTY" == "1" ]] && read -rp "  Your choice [1/2, default 1]: " _rt_choice < /dev/tty
    _rt_choice="${_rt_choice:-1}"
    case "${_rt_choice}" in
      1) CONTAINER_RUNTIME="docker"; COMPOSE="docker compose"; COMPOSE_CMD=(docker compose); break ;;
      2) CONTAINER_RUNTIME="podman"; break ;;
      *) echo "  Please enter 1 or 2." ;;
    esac
  done
fi

# =============================================================================
#  SECTION 5: Install runtime and required packages
# =============================================================================
echo "[3/9] Installing runtime and required packages..."

_sudo apt-get update -qq
_sudo apt-get install -y --no-install-recommends \
  ca-certificates curl gnupg lsb-release git apache2-utils python3

if [[ "$CONTAINER_RUNTIME" == "docker" ]]; then

  if ! command -v docker &>/dev/null; then
    # ── Fresh Docker CE install from the official repo ──────────────────────
    _sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/${DISTRO_ID}/gpg" \
      | _sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    _sudo chmod a+r /etc/apt/keyrings/docker.gpg

    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/${DISTRO_ID} ${VERSION_CODENAME} stable" \
      | _sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    _sudo apt-get update -qq
    _sudo apt-get install -y --no-install-recommends \
      docker-ce docker-ce-cli containerd.io \
      docker-buildx-plugin docker-compose-plugin

    _sudo systemctl enable --now docker
    echo "  Docker CE + compose plugin installed ✓"

  elif ! _docker_has_compose; then
    # ── Docker present but compose plugin missing ────────────────────────────
    if [[ ! -f /etc/apt/sources.list.d/docker.list ]]; then
      _sudo install -m 0755 -d /etc/apt/keyrings
      curl -fsSL "https://download.docker.com/linux/${DISTRO_ID}/gpg" \
        | _sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
      _sudo chmod a+r /etc/apt/keyrings/docker.gpg
      echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/${DISTRO_ID} ${VERSION_CODENAME} stable" \
        | _sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
      _sudo apt-get update -qq
    fi
    _sudo apt-get install -y --no-install-recommends docker-compose-plugin
    if ! _docker_has_compose; then
      echo "[ERROR] docker-compose-plugin not registering with Docker."
      echo "        If Docker was installed via Snap, reinstall via the official repo:"
      echo "        https://docs.docker.com/engine/install/${DISTRO_ID}/"
      exit 1
    fi
    echo "  docker-compose-plugin installed ✓"

  else
    echo "  Docker CE + compose plugin already present ✓"
  fi

  # Add deploy user to docker group so they can run docker compose without sudo.
  _RT_GROUP="docker"
  if ! id -nG "$DEPLOY_USER" 2>/dev/null | tr ' ' '\n' | grep -qx "docker"; then
    _sudo usermod -aG docker "$DEPLOY_USER"
    echo "  User '${DEPLOY_USER}' added to 'docker' group ✓"
    echo "  (Re-login after install to use docker without sudo in future sessions)"
  else
    echo "  User '${DEPLOY_USER}' already in 'docker' group ✓"
  fi

elif [[ "$CONTAINER_RUNTIME" == "podman" ]]; then

  if ! command -v podman &>/dev/null; then
    # Install Podman + rootless dependencies from OS-native repos.
    # Debian 11+ and Ubuntu 22.04+ ship recent-enough versions.
    echo "  Installing Podman + rootless dependencies..."
    _sudo apt-get update -qq
    _sudo apt-get install -y --no-install-recommends \
      podman \
      uidmap \
      slirp4netns \
      fuse-overlayfs \
      dbus-user-session
    # passt: available from Ubuntu 23.04+ and Debian 12+ only; skip on older releases
    # (slirp4netns acts as the fallback network backend).
    _sudo apt-get install -y --no-install-recommends passt 2>/dev/null || true
    if ! command -v podman &>/dev/null; then
      echo "[ERROR] Podman installation failed. Check 'apt policy podman'."
      exit 1
    fi
    echo "  Podman + rootless dependencies installed ✓"
  else
    echo "  Podman already present — installing missing rootless dependencies..."
    _sudo apt-get install -y --no-install-recommends \
      uidmap slirp4netns fuse-overlayfs dbus-user-session 2>/dev/null || true
    _sudo apt-get install -y --no-install-recommends passt 2>/dev/null || true
  fi

  # Ensure podman-compose is available.
  # Not in Ubuntu 22.04 / Debian 11 repos — fall back to pip3 on older systems.
  if ! command -v podman-compose &>/dev/null; then
    if ! _sudo apt-get install -y --no-install-recommends podman-compose 2>/dev/null; then
      echo "  podman-compose not in apt — trying pip3 fallback..."
      # python3 is already installed from Section 5; pip3 may need to be fetched first.
      _sudo apt-get install -y --no-install-recommends python3-pip 2>/dev/null || true
      _sudo pip3 install --break-system-packages --quiet podman-compose 2>/dev/null || \
        _sudo pip3 install --quiet podman-compose 2>/dev/null || \
        echo "  [!] podman-compose not installable — 'podman compose' built-in will be used"
    fi
  fi

  # Configure user namespace mappings required for rootless containers.
  # newuidmap/newgidmap (from uidmap) use these files to set up the UID/GID
  # ranges that Podman can map inside the container.
  if ! grep -q "^${DEPLOY_USER}:" /etc/subuid 2>/dev/null; then
    echo "${DEPLOY_USER}:100000:65536" | _sudo tee -a /etc/subuid > /dev/null
    echo "  subuid range added for '${DEPLOY_USER}' ✓"
  fi
  if ! grep -q "^${DEPLOY_USER}:" /etc/subgid 2>/dev/null; then
    echo "${DEPLOY_USER}:100000:65536" | _sudo tee -a /etc/subgid > /dev/null
    echo "  subgid range added for '${DEPLOY_USER}' ✓"
  fi

  # Helper to run podman as the deploy user even when script is root.
  _podman_as_user() {
    if [[ $EUID -eq 0 && "$DEPLOY_USER" != "root" ]]; then
      sudo -u "$DEPLOY_USER" podman "$@"
    else
      podman "$@"
    fi
  }

  # Configure Podman to use cgroupfs instead of systemd.
  # Without a full systemd user session (e.g. SSH logins), Podman would warn
  # "cgroupv2 manager set to systemd but no user session available" on every
  # invocation and may fail to start containers. cgroupfs works everywhere.
  _deploy_home=$(eval echo "~${DEPLOY_USER}")
  _containers_conf="${_deploy_home}/.config/containers/containers.conf"
  # Detect environments where nftables is unavailable (WSL, some LXC/VMs).
  # Netavark defaults to nftables; switch it to iptables when nft is missing
  # or broken. Note: CNI is not an option on Debian 13+ (not compiled in).
  _netavark_fw="nftables"
  if ! nft list ruleset &>/dev/null 2>&1; then
    _netavark_fw="iptables"
    _sudo apt-get install -y --no-install-recommends iptables 2>/dev/null || true
    echo "  nftables unavailable (WSL/LXC?) — netavark will use iptables ✓"
  fi

  # Always write containers.conf so re-runs pick up the correct settings.
  if [[ $EUID -eq 0 && "$DEPLOY_USER" != "root" ]]; then
    sudo -u "$DEPLOY_USER" mkdir -p "${_deploy_home}/.config/containers"
    printf '[engine]\ncgroup_manager = "cgroupfs"\nenv = ["NETAVARK_FW=%s"]\n' \
      "$_netavark_fw" | sudo -u "$DEPLOY_USER" tee "$_containers_conf" > /dev/null
  else
    mkdir -p "${_deploy_home}/.config/containers"
    printf '[engine]\ncgroup_manager = "cgroupfs"\nenv = ["NETAVARK_FW=%s"]\n' \
      "$_netavark_fw" | tee "$_containers_conf" > /dev/null
  fi
  echo "  Podman: cgroup_manager=cgroupfs, NETAVARK_FW=${_netavark_fw} ✓"

  # Prune networks created with the wrong firewall backend so they are
  # recreated correctly on next compose up.
  _podman_as_user network prune --force &>/dev/null || true

  # Fix "/ is not a shared mount" warning: configure the kernel mount
  # propagation for the root filesystem via a systemd override.
  # This persists across reboots and avoids bind-mount issues in rootless mode.
  _mnt_override="/etc/systemd/system/local-fs.target.d/shared-propagation.conf"
  if [[ ! -f "$_mnt_override" ]]; then
    _sudo mkdir -p "$(dirname "$_mnt_override")"
    printf '[Unit]\nConditionPathIsMountPoint=/\n[Service]\nExecStartPost=/bin/mount --make-rshared /\n' \
      | _sudo tee "$_mnt_override" > /dev/null
    _sudo mount --make-rshared / 2>/dev/null || true
    echo "  Root mount propagation set to shared ✓"
  fi

  # Configure unqualified-search-registries so short image names like
  # "postgres:17-alpine" resolve to docker.io without explicit prefix.
  _reg_conf="/etc/containers/registries.conf.d/docker-io-search.conf"
  if [[ ! -f "$_reg_conf" ]]; then
    echo 'unqualified-search-registries = ["docker.io"]' \
      | _sudo tee "$_reg_conf" > /dev/null
    echo "  docker.io added as default search registry ✓"
  fi

  # Install nftables (required by netavark network backend) and aardvark-dns
  # (container DNS resolution). Without these, container networking fails.
  _sudo apt-get install -y --no-install-recommends nftables 2>/dev/null || true
  _sudo apt-get install -y --no-install-recommends aardvark-dns 2>/dev/null || true

  # Enable Podman socket so docker-socket-proxy and moe-admin can use the
  # container API. With rootless Podman the socket lives at
  # /run/user/<uid>/podman/podman.sock.
  _deploy_uid=$(id -u "$DEPLOY_USER" 2>/dev/null || echo "1000")
  _podman_socket="/run/user/${_deploy_uid}/podman/podman.sock"
  if [[ $EUID -eq 0 && "$DEPLOY_USER" != "root" ]]; then
    XDG_RUNTIME_DIR="/run/user/${_deploy_uid}" \
      sudo -u "$DEPLOY_USER" systemctl --user enable --now podman.socket 2>/dev/null || true
  else
    systemctl --user enable --now podman.socket 2>/dev/null || true
  fi

  _podman_as_user system migrate &>/dev/null || true

  # Smoke-test: verify rootless Podman is functional before proceeding.
  echo "  Verifying rootless Podman..."
  if ! _podman_as_user info &>/dev/null; then
    echo "[ERROR] Podman smoke test failed — 'podman info' returned an error."
    echo "        Try: podman info"
    exit 1
  fi
  echo "  Podman rootless smoke test passed ✓"

  _resolve_podman_compose

  # Enable lingering so rootless containers survive logout / start on boot.
  if command -v loginctl &>/dev/null && [[ "$DEPLOY_USER" != "root" ]]; then
    _sudo loginctl enable-linger "$DEPLOY_USER" 2>/dev/null || true
    echo "  Linger enabled for '${DEPLOY_USER}' (containers survive logout) ✓"
  fi

  # Rootless Podman needs no group — each user manages their own containers.
  _RT_GROUP=""
  # Store socket path for use in .env generation (Section 10).
  _PODMAN_SOCKET="$_podman_socket"
  echo "  Podman + compose ready ✓"

  # Rootless Podman cannot bind privileged ports (< 1024) by default.
  # Lower the threshold to 80 so Caddy can serve HTTP/HTTPS without root.
  if [[ "$(uname -s)" == "Linux" ]] && [[ $EUID -ne 0 ]]; then
    _cur_port=$(sysctl -n net.ipv4.ip_unprivileged_port_start 2>/dev/null || echo 1024)
    if [[ "${_cur_port}" -gt 80 ]]; then
      _sudo sysctl -w net.ipv4.ip_unprivileged_port_start=80 &>/dev/null || true
      echo 'net.ipv4.ip_unprivileged_port_start=80' \
        | _sudo tee /etc/sysctl.d/99-podman-unprivileged-ports.conf >/dev/null 2>&1 || true
      echo "  Unprivileged port start lowered to 80 (Caddy TLS support) ✓"
    fi
  fi

fi

# ── NVIDIA Container Toolkit & GPG Keyring Setup ────────────────────────────
if command -v nvidia-smi &>/dev/null || [[ -e /dev/nvidia0 ]]; then
  echo "  NVIDIA GPU detected — configuring NVIDIA Container Toolkit repository & GPG keyring..."
  _sudo install -m 0755 -d /etc/apt/keyrings
  if curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | _sudo gpg --dearmor -o /etc/apt/keyrings/nvidia-container-toolkit-keyring.gpg 2>/dev/null; then
    _sudo chmod a+r /etc/apt/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
      | sed 's#deb https://#deb [signed-by=/etc/apt/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
      | _sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list > /dev/null
    _sudo apt-get update -qq
    _sudo apt-get install -y --no-install-recommends nvidia-container-toolkit 2>/dev/null || true
    echo "  NVIDIA Container Toolkit repository + GPG keyring configured ✓"
  else
    echo "  [!] Could not fetch NVIDIA GPG key — skipping repository configuration."
  fi
fi

# podman-compose does not support --quiet; docker compose does.
[[ "$CONTAINER_RUNTIME" == "docker" ]] && _Q="--quiet" || _Q=""

echo "  Runtime: ${CONTAINER_RUNTIME} | Compose: ${COMPOSE} | Group: ${_RT_GROUP:-none} ✓"

# =============================================================================
#  SECTION 6: Host directory creation
# =============================================================================
echo "[4/9] Creating host directories..."

# Determine system defaults (macOS uses home dir; bind mounts in /opt not shared by default)
case "$(uname -s)" in
  Darwin)
    _sys_data_default="${HOME}/moe-data"
    _sys_graf_default="${HOME}/moe-grafana"
    ;;
  *)
    _sys_data_default="/opt/moe-sovereign"
    _sys_graf_default="/opt/grafana"
    ;;
esac

# Preserve existing paths from a previous install as prompt defaults.
# Moving an existing data root would orphan all volumes.
_data_default="${MOE_DATA_ROOT:-${_sys_data_default}}"
_graf_default="${GRAFANA_DATA_ROOT:-${_sys_graf_default}}"
if [[ -f "${MOE_ENV_FILE}" ]]; then
  _prev_data=$(grep -E '^MOE_DATA_ROOT=' "${MOE_ENV_FILE}" 2>/dev/null | head -1 | cut -d= -f2-)
  _prev_graf=$(grep -E '^GRAFANA_DATA_ROOT=' "${MOE_ENV_FILE}" 2>/dev/null | head -1 | cut -d= -f2-)
  [[ -n "$_prev_data" ]] && _data_default="$_prev_data"
  [[ -n "$_prev_graf" ]] && _graf_default="$_prev_graf"
fi

[[ "$HAS_TTY" == "1" ]] && read -rp "  Persistent data directory   [${_data_default}]: " _tmp_data < /dev/tty
MOE_DATA_ROOT="${_tmp_data:-${_data_default}}"

[[ "$HAS_TTY" == "1" ]] && read -rp "  Grafana data directory      [${_graf_default}]: " _tmp_graf < /dev/tty
GRAFANA_DATA_ROOT="${_tmp_graf:-${_graf_default}}"

echo "  Data root:   ${MOE_DATA_ROOT}"
echo "  Grafana root: ${GRAFANA_DATA_ROOT}"
echo ""

_sudo mkdir -p \
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
  "${MOE_DATA_ROOT}/jupyterlab" \
  "${MOE_DATA_ROOT}/garage/meta" \
  "${MOE_DATA_ROOT}/garage/data" \
  "${MOE_DATA_ROOT}/garage/etc" \
  "${GRAFANA_DATA_ROOT}/data" \
  "${GRAFANA_DATA_ROOT}/dashboards" \
  "${INSTALL_DIR}" 2>/dev/null || true

# INSTALL_DIR is owned by the deploy user so git clone and .env writes work without sudo.
_sudo chown "$DEPLOY_USER":"$DEPLOY_USER" "${INSTALL_DIR}"

# Run every remaining step from INSTALL_DIR — most operations below already
# use absolute paths, but compose itself needs its cwd to be the project
# directory. Doing this once, up front, means no step has to remember it.
cd "${INSTALL_DIR}"

# Files that must exist as regular files (not directories) before the container
# runtime mounts them.  Create them owned by the deploy user so rootless
# container processes can write to them without a chown step.
_touch_as_user() {
  local f="$1"
  [[ -f "$f" ]] && return
  if [[ $EUID -eq 0 && "$DEPLOY_USER" != "root" ]]; then
    sudo -u "$DEPLOY_USER" touch "$f" 2>/dev/null || { _sudo touch "$f"; _sudo chown "$DEPLOY_USER":"$DEPLOY_USER" "$f"; }
  else
    touch "$f" 2>/dev/null || { _sudo touch "$f"; _sudo chown "$DEPLOY_USER":"$DEPLOY_USER" "$f"; }
  fi
}
# dozzle-users.yml — moe-dozzle-init overwrites with bcrypt hash on first start
_touch_as_user "${MOE_DATA_ROOT}/dozzle-users.yml"
# moe-admin runtime files
_touch_as_user "${MOE_DATA_ROOT}/cleanup-config.json"
_touch_as_user "${MOE_DATA_ROOT}/cleanup-history.jsonl"

echo "  Host directories created at ${MOE_DATA_ROOT} ✓"

# Fix ownership for containers that run as non-root UIDs.
# (_container_uid_to_host_uid and _chown_for_container are defined early,
#  before the update path, so they are available on both install and update.)

# kafka (confluentinc/cp-kafka): appuser uid=1000
_chown_for_container 1000 1000 "${MOE_DATA_ROOT}/kafka-data"

# Containers whose entrypoints chown the data dir to their service user at
# startup need the directory owned by container root (UID 0) first.
#
# In rootless Podman container UID 0 == the deploy user on the host.
# Directories created by `sudo mkdir` are host-root-owned, which maps to
# UID 65534 (overflow/nobody) inside the container — the entrypoint's root
# process cannot chown a file it does not own.  Pre-chowning to UID 0 via
# `podman unshare` translates to the deploy user on the host, giving
# container root ownership and allowing the entrypoint's chown to succeed.
# In Docker container root == host root, so UID 0:0 is already the owner
# after `sudo mkdir` — this is effectively a no-op.
_chown_for_container 0 0 "${MOE_DATA_ROOT}/langgraph-checkpoints"  # postgres entrypoint: chown → postgres (70)
_chown_for_container 0 0 "${MOE_DATA_ROOT}/redis-data"              # valkey entrypoint:   chown → valkey (999)
_chown_for_container 0 0 "${MOE_DATA_ROOT}/neo4j-data"              # neo4j entrypoint:    chown → neo4j (7474)
_chown_for_container 0 0 "${MOE_DATA_ROOT}/neo4j-logs"              # neo4j entrypoint:    chown → neo4j (7474)
_chown_for_container 0 0 "${MOE_DATA_ROOT}/chroma-data"             # chromadb: runs as root, needs writable /data
_chown_for_container 0 0 "${MOE_DATA_ROOT}/chroma-onnx-cache"       # chromadb: ONNX model cache
_sudo chmod -R 777 "${MOE_DATA_ROOT}/chroma-onnx-cache" 2>/dev/null || true
_sudo mkdir -p "${MOE_DATA_ROOT}/chroma-onnx-cache/onnx_models" 2>/dev/null || true
_sudo chmod -R 777 "${MOE_DATA_ROOT}/chroma-onnx-cache/onnx_models" 2>/dev/null || true
_sudo mkdir -p "${MOE_DATA_ROOT}/ollama-models" 2>/dev/null || true
_chown_for_container 0 0 "${MOE_DATA_ROOT}/ollama-models"             # ollama: model cache
_chown_for_container 0 0 "${MOE_DATA_ROOT}/garage/meta"             # garage: distroless, runs as root
_chown_for_container 0 0 "${MOE_DATA_ROOT}/garage/data"             # garage: distroless, runs as root
_chown_for_container 0 0 "${MOE_DATA_ROOT}/jupyterlab"              # jupyter: user:root + CHOWN_HOME

# prometheus: runs as nobody uid=65534
_chown_for_container 65534 65534 "${MOE_DATA_ROOT}/prometheus-data"
# langgraph-orchestrator + mcp-precision + moe-admin: moe uid=1001 gid=0
_chown_for_container 1001 0 "${MOE_DATA_ROOT}/agent-logs"
_chown_for_container 1001 0 "${MOE_DATA_ROOT}/user-audit-logs"
_chown_for_container 1001 0 \
  "${MOE_DATA_ROOT}/admin-logs" \
  "${MOE_DATA_ROOT}/generated" \
  "${MOE_DATA_ROOT}/gap-healer-stats" \
  "${MOE_DATA_ROOT}/checkpoint-archives" \
  "${MOE_DATA_ROOT}/cleanup-config.json" \
  "${MOE_DATA_ROOT}/cleanup-history.jsonl"
# grafana: uid=472
_chown_for_container 472 472 "${GRAFANA_DATA_ROOT}/data" "${GRAFANA_DATA_ROOT}/dashboards"

if [[ "$(uname -s)" == "Darwin" ]]; then
  echo ""
  echo "  [!] macOS: Docker Desktop must allow ${MOE_DATA_ROOT} for bind"
  echo "      mounts. Open Docker Desktop → Settings → Resources →"
  echo "      File Sharing and add this path if it is not already listed."
  echo ""
fi

# =============================================================================
#  SECTION 7: Repo clone or update
# =============================================================================
echo "[5/9] Setting up MoE Sovereign repository..."

# Git operations must run as DEPLOY_USER so all repo files are user-owned.
# If the script is executed as root (sudo bash install.sh), use sudo -u to
# drop privileges for git commands.
_git_as_user() {
  if [[ $EUID -eq 0 && "$DEPLOY_USER" != "root" ]]; then
    sudo -u "$DEPLOY_USER" git "$@"
  else
    git "$@"
  fi
}

if [[ -d "${INSTALL_DIR}/.git" ]]; then
  echo "  Existing repo found — pulling latest changes..."
  # Guard against dirty working tree: stash silently on fresh-install path
  # (no interactive prompts here — update mode handles the interactive case above).
  _s7_dirty=$(git -C "${INSTALL_DIR}" status --porcelain 2>/dev/null || true)
  _s7_stashed=0
  if [[ -n "$_s7_dirty" ]]; then
    echo "  [!] Local modifications found — stashing before pull..."
    if _git_as_user -C "${INSTALL_DIR}" stash push \
        -m "install.sh section-7 stash $(date +%Y%m%d-%H%M%S)" 2>/dev/null; then
      _s7_stashed=1
    else
      echo "  [!] Stash failed — attempting pull anyway."
    fi
  fi
  if ! _git_as_user -C "${INSTALL_DIR}" pull --ff-only 2>&1; then
    echo "  [!] Fast-forward pull failed — resetting to origin..."
    _s7_branch=$(git -C "${INSTALL_DIR}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main")
    _git_as_user -C "${INSTALL_DIR}" fetch origin
    _git_as_user -C "${INSTALL_DIR}" reset --hard "origin/${_s7_branch}"
    echo "  Reset to origin/${_s7_branch} ✓"
  fi
  # Restore local modifications after pull. On conflict, always prefer the
  # upstream (HEAD) version so the deployment runs the correct released code.
  if [[ $_s7_stashed -eq 1 ]]; then
    if ! _git_as_user -C "${INSTALL_DIR}" stash pop 2>/dev/null; then
      echo "  [!] Stash pop conflict — keeping upstream versions for conflicted files."
      _git_as_user -C "${INSTALL_DIR}" checkout HEAD -- \
        $(git -C "${INSTALL_DIR}" diff --name-only --diff-filter=U 2>/dev/null) \
        2>/dev/null || true
      _git_as_user -C "${INSTALL_DIR}" reset HEAD 2>/dev/null || true
    fi
    echo "  Local modifications restored ✓"
  fi
else
  echo "  Cloning repository to ${INSTALL_DIR}..."
  if ! _git_as_user clone "${MOE_REPO_URL}" "${INSTALL_DIR}" 2>/dev/null; then
    echo "  [!] Target directory '${INSTALL_DIR}' exists — initializing repository in-place..."
    _git_as_user -C "${INSTALL_DIR}" init -q
    _git_as_user -C "${INSTALL_DIR}" remote add origin "${MOE_REPO_URL}" 2>/dev/null || \
      _git_as_user -C "${INSTALL_DIR}" remote set-url origin "${MOE_REPO_URL}"
    _git_as_user -C "${INSTALL_DIR}" fetch origin -q
    _git_as_user -C "${INSTALL_DIR}" checkout -B main origin/main --force
  fi
fi

# Postgres mounts ./scripts/postgres-init as docker-entrypoint-initdb.d:ro.
# The container user (uid 70 in postgres:alpine) needs read + execute on the dir.
chmod -R a+rX "${INSTALL_DIR}/scripts" 2>/dev/null || true

echo "  Repository ready ✓"

# =============================================================================
#  SECTION 8: Interactive .env generation
# =============================================================================

# When running via pipe (curl | bash), stdin is the script itself.
# We must NOT use exec < /dev/tty here because that would prevent bash
# from reading the rest of the script. Instead, read from /dev/tty
# explicitly in prompt_input().

echo ""
echo "=========================================================================="
echo "  Configuration"
echo "=========================================================================="
echo ""
echo "  The following questions configure your MoE Sovereign instance."
echo "  Press ENTER to accept a default value [shown in brackets]."
echo "  Inference server (LLM API) configuration is done via the web wizard"
echo "  after installation."
echo ""

# Pre-initialize variables that will be set by prompt_input (prevents nounset errors)
ADMIN_USER=""
ADMIN_PASSWORD=""
DOMAIN=""
GEN_OIDC_CLIENT_ID=""
GEN_OIDC_CLIENT_SECRET=""
GEN_OIDC_REDIRECT_URI=""
GEN_OIDC_JWKS_URL=""
GEN_OIDC_ISSUER=""
GEN_OIDC_END_SESSION_URL=""

# Helper: prompt with optional default
prompt_input() {
  local varname="$1"
  local prompt_text="$2"
  local default_val="${3:-}"
  local is_secret="${4:-false}"
  local required="${5:-true}"

  # Ensure the variable exists (prevents nounset if read fails)
  declare -g "${varname}=${default_val}"

  local display_default=""
  if [[ -n "$default_val" ]]; then
    if [[ "$is_secret" == "true" ]]; then
      display_default=" [auto-generated]"
    else
      display_default=" [${default_val}]"
    fi
  fi

  if [[ "$HAS_TTY" != "1" ]]; then
    # No controlling terminal — can't prompt, let alone re-prompt on a
    # validation failure. Accept the default silently; only a required
    # field with no default has no sane non-interactive outcome.
    if [[ -z "$default_val" && "$required" == "true" ]]; then
      echo "  [!] '${prompt_text}' has no default and requires a terminal to set." >&2
      echo "      Re-run from a real terminal, or pre-set its environment variable." >&2
      exit 1
    fi
    printf -v "${varname}" '%s' "${default_val}"
    return
  fi

  while true; do
    if [[ "$is_secret" == "true" ]]; then
      read -rsp "  ${prompt_text}${display_default}: " input_val < /dev/tty
      echo ""
    else
      read -rp "  ${prompt_text}${display_default}: " input_val < /dev/tty
    fi

    if [[ -z "$input_val" ]]; then
      input_val="$default_val"
    fi

    if [[ -z "$input_val" && "$required" == "true" ]]; then
      echo "  [!] This field is required."
    else
      break
    fi
  done

  printf -v "${varname}" '%s' "${input_val}"
}

# Preserve infrastructure secrets across re-runs.
# Re-running install.sh must never desync from existing data volumes:
# Postgres, Neo4j, Redis and Grafana persist their credentials on first
# init. Regenerating the .env would lock every service out of its volume.
# Strategy: read existing secrets from .env (or backup) and only generate
# new values for fields that are still missing.
EXISTING_ADMIN_SECRET=""
EXISTING_REDIS_PASS=""
EXISTING_NEO4J_PASS=""
EXISTING_GRAFANA_PASS=""
EXISTING_PG_LANGGRAPH_PASS=""
EXISTING_PG_USERDB_PASS=""
EXISTING_MINIO_USER=""
EXISTING_MINIO_PASS=""
EXISTING_GARAGE_SECRET=""
EXISTING_NIFI_PASS=""
EXISTING_MARQUEZ_PG_PASS=""
EXISTING_LAKEFS_DB_PASS=""
EXISTING_LAKEFS_ENCRYPT=""
EXISTING_LAKEFS_AK_ID=""
EXISTING_LAKEFS_SK=""
EXISTING_AUTHENTIK_SECRET=""
EXISTING_AUTHENTIK_PG_PASS=""
EXISTING_ADMIN_USER=""
EXISTING_ADMIN_PASSWORD=""
EXISTING_DOMAIN=""

if [[ -f "${MOE_ENV_FILE}" ]]; then
  echo ""
  echo "  [!] Existing .env detected at ${MOE_ENV_FILE}"
  echo "      Infrastructure secrets will be PRESERVED to keep data"
  echo "      volumes (Postgres, Neo4j, Redis) accessible."
  echo ""
  # Source existing values without executing the file (grep + cut is safer)
  read_env() {
    local key="$1"
    grep -E "^${key}=" "${MOE_ENV_FILE}" 2>/dev/null | head -1 | cut -d= -f2-
  }
  EXISTING_ADMIN_SECRET=$(read_env ADMIN_SECRET_KEY)
  EXISTING_REDIS_PASS=$(read_env REDIS_PASSWORD)
  EXISTING_NEO4J_PASS=$(read_env NEO4J_PASS)
  EXISTING_GRAFANA_PASS=$(read_env GF_SECURITY_ADMIN_PASSWORD)
  EXISTING_PG_LANGGRAPH_PASS=$(read_env POSTGRES_CHECKPOINT_PASSWORD)
  EXISTING_PG_USERDB_PASS=$(read_env MOE_USERDB_PASSWORD)
  EXISTING_LIBRIS_DB_PASS=$(read_env LIBRIS_DB_PASSWORD)
  EXISTING_MINIO_USER=$(read_env MINIO_ROOT_USER)
  EXISTING_MINIO_PASS=$(read_env MINIO_ROOT_PASSWORD)
  EXISTING_GARAGE_SECRET=$(read_env GARAGE_RPC_SECRET)
  EXISTING_NIFI_PASS=$(read_env NIFI_ADMIN_PASSWORD)
  EXISTING_MARQUEZ_PG_PASS=$(read_env MARQUEZ_POSTGRES_PASSWORD)
  EXISTING_LAKEFS_DB_PASS=$(read_env LAKEFS_DB_PASSWORD)
  EXISTING_LAKEFS_ENCRYPT=$(read_env LAKEFS_ENCRYPT_SECRET)
  EXISTING_LAKEFS_AK_ID=$(read_env LAKEFS_ACCESS_KEY_ID)
  EXISTING_LAKEFS_SK=$(read_env LAKEFS_SECRET_ACCESS_KEY)
  EXISTING_AUTHENTIK_SECRET=$(read_env AUTHENTIK_SECRET_KEY)
  EXISTING_AUTHENTIK_PG_PASS=$(read_env AUTHENTIK_POSTGRESQL__PASSWORD)
  EXISTING_ADMIN_USER=$(read_env ADMIN_USER)
  EXISTING_ADMIN_PASSWORD=$(read_env ADMIN_PASSWORD)
  EXISTING_DOMAIN=$(read_env DOMAIN)
fi

# Generate new secrets only where none exist yet
GEN_ADMIN_SECRET="${EXISTING_ADMIN_SECRET:-$(openssl rand -hex 32)}"
GEN_REDIS_PASS="${EXISTING_REDIS_PASS:-$(openssl rand -hex 16)}"
GEN_NEO4J_PASS="${EXISTING_NEO4J_PASS:-$(openssl rand -hex 16)}"
GEN_GRAFANA_PASS="${EXISTING_GRAFANA_PASS:-$(openssl rand -hex 12)}"
GEN_PG_LANGGRAPH_PASS="${EXISTING_PG_LANGGRAPH_PASS:-$(openssl rand -hex 16)}"
GEN_PG_USERDB_PASS="${EXISTING_PG_USERDB_PASS:-$(openssl rand -hex 16)}"
GEN_LIBRIS_DB_PASS="${EXISTING_LIBRIS_DB_PASS:-$(openssl rand -hex 16)}"
GEN_MINIO_USER="${EXISTING_MINIO_USER:-moeadmin}"
GEN_MINIO_PASS="${EXISTING_MINIO_PASS:-$(openssl rand -hex 16)}"
GEN_GARAGE_SECRET="${EXISTING_GARAGE_SECRET:-$(openssl rand -hex 32)}"
GEN_NIFI_PASS="${EXISTING_NIFI_PASS:-$(openssl rand -hex 8)}"
GEN_MARQUEZ_PG_PASS="${EXISTING_MARQUEZ_PG_PASS:-$(openssl rand -hex 16)}"
GEN_LAKEFS_DB_PASS="${EXISTING_LAKEFS_DB_PASS:-$(openssl rand -hex 16)}"
GEN_LAKEFS_ENCRYPT="${EXISTING_LAKEFS_ENCRYPT:-$(openssl rand -hex 16)}"
GEN_LAKEFS_AK_ID="${EXISTING_LAKEFS_AK_ID:-lakefs-$(openssl rand -hex 8)}"
GEN_LAKEFS_SK="${EXISTING_LAKEFS_SK:-$(openssl rand -hex 20)}"
GEN_AUTHENTIK_SECRET="${EXISTING_AUTHENTIK_SECRET:-$(openssl rand -hex 32)}"
GEN_AUTHENTIK_PG_PASS="${EXISTING_AUTHENTIK_PG_PASS:-$(openssl rand -hex 16)}"

echo "  --- Admin Account ---"
prompt_input ADMIN_USER "Admin username" "${EXISTING_ADMIN_USER:-admin}"

# Prompt for admin password with confirmation and basic strength check.
# On updates: pressing ENTER keeps the existing password (no re-confirmation needed).
# On fresh installs: requires two matching entries and minimum length of 10.
prompt_password() {
  local varname="$1"
  local existing="${2:-}"

  if [[ -n "$existing" ]]; then
    # Update path — keep existing unless user types a replacement
    while true; do
      [[ "$HAS_TTY" == "1" ]] && read -rsp "  Admin password (ENTER keeps existing): " _pw1 < /dev/tty; echo ""
      if [[ -z "$_pw1" ]]; then
        printf -v "${varname}" '%s' "${existing}"
        break
      fi
      if [[ ${#_pw1} -lt 10 ]]; then
        echo "  [!] Password must be at least 10 characters. Try again."
        continue
      fi
      [[ "$HAS_TTY" == "1" ]] && read -rsp "  Confirm new password: " _pw2 < /dev/tty; echo ""
      if [[ "$_pw1" != "$_pw2" ]]; then
        echo "  [!] Passwords do not match. Try again."
        continue
      fi
      printf -v "${varname}" '%s' "${_pw1}"
      break
    done
  else
    # Fresh install — require confirmation and enforce minimum strength
    while true; do
      [[ "$HAS_TTY" == "1" ]] && read -rsp "  Admin password (min 10 chars): " _pw1 < /dev/tty; echo ""
      if [[ -z "$_pw1" ]]; then
        echo "  [!] Password is required."
        continue
      fi
      if [[ ${#_pw1} -lt 10 ]]; then
        echo "  [!] Password must be at least 10 characters (got ${#_pw1}). Try again."
        continue
      fi
      [[ "$HAS_TTY" == "1" ]] && read -rsp "  Confirm password: " _pw2 < /dev/tty; echo ""
      if [[ "$_pw1" != "$_pw2" ]]; then
        echo "  [!] Passwords do not match. Try again."
        continue
      fi
      printf -v "${varname}" '%s' "${_pw1}"
      break
    done
  fi
}
prompt_password ADMIN_PASSWORD "${EXISTING_ADMIN_PASSWORD:-}"

# Validate password was set (guard against unexpected empty result)
if [[ -z "$ADMIN_PASSWORD" ]]; then
  echo "[ERROR] Admin password is required."
  exit 1
fi

echo ""
echo "  --- Domain Configuration ---"
prompt_input DOMAIN "Your public domain (e.g. moe-sovereign.org), or leave blank for local use" "${EXISTING_DOMAIN:-}" "false" "false"

# URL defaults resolved after Caddy selection (see below)

echo ""
echo "  --- Optional Components ---"

# Neo4j GraphRAG
echo ""
echo "  Neo4j powers GraphRAG (knowledge-graph enrichment) and the ontology curator."
echo "  Adds ~1.5 GB RAM (raises minimum from 4 GB → 6 GB). Skip on small VMs."
INSTALL_NEO4J="true"
while true; do
  [[ "$HAS_TTY" == "1" ]] && read -rp "  Install Neo4j GraphRAG? [Y/n]: " _neo4j_choice < /dev/tty
  _neo4j_choice="${_neo4j_choice:-Y}"
  case "${_neo4j_choice,,}" in
    y|yes) INSTALL_NEO4J="true";  break ;;
    n|no)  INSTALL_NEO4J="false"; break ;;
    *) echo "  Please enter y or n." ;;
  esac
done

# Monitoring Tools (Prometheus, Grafana, Dozzle, AKHQ)
echo ""
echo "  Monitoring tools provide Prometheus metrics, Grafana dashboards,"
echo "  Dozzle log viewer, and AKHQ Kafka event inspection."
echo "  Adds ~500 MB RAM. Recommended for observability and topic debugging."
INSTALL_MONITORING="true"
while true; do
  [[ "$HAS_TTY" == "1" ]] && read -rp "  Install Monitoring Tools (Prometheus, Grafana, Dozzle, AKHQ)? [Y/n]: " _mon_choice < /dev/tty
  _mon_choice="${_mon_choice:-Y}"
  case "${_mon_choice,,}" in
    y|yes) INSTALL_MONITORING="true";  break ;;
    n|no)  INSTALL_MONITORING="false"; break ;;
    *) echo "  Please enter y or n." ;;
  esac
done

# Caddy reverse proxy
echo ""
echo "  Caddy is a built-in TLS reverse proxy for this stack."
echo "  Skip if you already run Nginx, Traefik, or another proxy in front."
INSTALL_CADDY="false"
while true; do
  [[ "$HAS_TTY" == "1" ]] && read -rp "  Install Caddy reverse proxy? [y/N]: " _caddy_choice < /dev/tty
  _caddy_choice="${_caddy_choice:-N}"
  case "${_caddy_choice,,}" in
    y|yes) INSTALL_CADDY="true";  break ;;
    n|no)  INSTALL_CADDY="false"; break ;;
    *) echo "  Please enter y or n." ;;
  esac
done

# Public URLs — prompted only when Caddy+domain, otherwise localhost defaults
if [[ "$INSTALL_CADDY" == "true" && -n "${DOMAIN:-}" ]]; then
  echo ""
  echo "  --- Public Subdomain URLs ---"
  echo "  Caddy will serve each service at the URL you confirm below."
  echo "  Press ENTER to accept the subdomain shown in [brackets]."
  prompt_input DEFAULT_ADMIN_URL  "Admin UI URL"    "https://admin.${DOMAIN}" "false" "false"
  prompt_input DEFAULT_BASE_URL   "User portal URL" "https://${DOMAIN}"       "false" "false"
  prompt_input DEFAULT_API_URL    "API URL"         "https://api.${DOMAIN}"   "false" "false"
else
  DEFAULT_ADMIN_URL="http://localhost:8088"
  DEFAULT_BASE_URL="http://localhost:8088"
  DEFAULT_API_URL="http://localhost:8000"
fi

# Authentik SSO — deploy built-in server
echo ""
echo "  Authentik is a self-hosted SSO/OIDC provider (server + worker + DB + Redis)."
echo "  Installs 4 containers inside this stack. Skip if you already run an external IdP."
INSTALL_AUTHENTIK="false"
AUTHENTIK_BOOTSTRAP_EMAIL_INPUT=""
AUTHENTIK_BOOTSTRAP_PASSWORD_INPUT=""
while true; do
  [[ "$HAS_TTY" == "1" ]] && read -rp "  Deploy Authentik SSO server? [y/N]: " _authentik_choice < /dev/tty
  _authentik_choice="${_authentik_choice:-N}"
  case "${_authentik_choice,,}" in
    y|yes) INSTALL_AUTHENTIK="true";  break ;;
    n|no)  INSTALL_AUTHENTIK="false"; break ;;
    *) echo "  Please enter y or n." ;;
  esac
done
if [[ "$INSTALL_AUTHENTIK" == "true" ]]; then
  prompt_input AUTHENTIK_BOOTSTRAP_EMAIL_INPUT \
    "Authentik bootstrap admin e-mail" "${ADMIN_EMAIL:-admin@example.com}" "false" "true"
  prompt_input AUTHENTIK_BOOTSTRAP_PASSWORD_INPUT \
    "Authentik bootstrap admin password (min 10 chars)" "" "true" "true"
fi

# Authentik SSO — OIDC client configuration
echo ""
INSTALL_SSO="false"
AUTHENTIK_URL_INPUT=""
if [[ "$INSTALL_AUTHENTIK" == "true" ]]; then
  # Configure OIDC to point at the local Authentik instance
  INSTALL_SSO="true"
  if [[ -n "${DOMAIN:-}" && "$INSTALL_CADDY" == "true" ]]; then
    prompt_input AUTHENTIK_URL_INPUT \
      "SSO (Authentik) URL" "https://sso.${DOMAIN}" "false" "false"
  elif [[ -n "${DOMAIN:-}" ]]; then
    AUTHENTIK_URL_INPUT="https://sso.${DOMAIN}"
  else
    AUTHENTIK_URL_INPUT="http://localhost:9000"
  fi
else
  echo "  Configure OIDC against an existing Authentik (or other IdP)?"
  while true; do
    [[ "$HAS_TTY" == "1" ]] && read -rp "  Configure Authentik/OIDC SSO? [y/N]: " _sso_choice < /dev/tty
    _sso_choice="${_sso_choice:-N}"
    case "${_sso_choice,,}" in
      y|yes) INSTALL_SSO="true";  break ;;
      n|no)  INSTALL_SSO="false"; break ;;
      *) echo "  Please enter y or n." ;;
    esac
  done
  if [[ "$INSTALL_SSO" == "true" ]]; then
    prompt_input AUTHENTIK_URL_INPUT \
      "Authentik base URL (e.g. https://auth.example.com)" "" "false" "true"
  fi
fi

echo ""
if [[ -n "$EXISTING_REDIS_PASS" ]]; then
  echo "  --- Infrastructure secrets (preserved from existing .env) ---"
  echo "  Admin secret key:  [kept]"
  echo "  Redis password:    [kept]"
  echo "  Neo4j password:    [kept]"
  echo "  Grafana password:  [kept]"
  echo "  Postgres passwords: [kept]"
  echo "  MinIO credentials: [kept]"
else
  echo "  --- Auto-generated secrets (saved to .env) ---"
  echo "  Admin secret key:  [auto]"
  echo "  Redis password:    [auto]"
  echo "  Neo4j password:    [auto]"
  echo "  Grafana password:  [auto]"
  echo "  MinIO user:        ${GEN_MINIO_USER}"
  echo "  MinIO password:    [auto]"
fi
echo ""

# =============================================================================
#  SECTION 8b: Optional MoE Codex
# =============================================================================
echo "=========================================================================="
echo "  Optional: MoE Codex Stack"
echo "=========================================================================="
echo ""
echo "  MoE Codex adds JupyterLab notebooks, Apache NiFi ETL pipelines,"
echo "  OpenLineage audit tracking, and lakeFS data versioning (Git for data)."
echo ""
echo "  Requires approx. 8.5 GB additional RAM (NiFi JVM + Marquez + lakeFS + JupyterLab)."
echo ""
[[ "$HAS_TTY" == "1" ]] && read -rp "  Install MoE Codex (JupyterLab, NiFi, Marquez, lakeFS)? [y/N]: " _eds_choice < /dev/tty
_eds_choice="${_eds_choice:-N}"
case "${_eds_choice,,}" in
  y|yes) INSTALL_CODEX=true  ;;
  *)     INSTALL_CODEX=false ;;
esac
export INSTALL_CODEX
echo ""

# =============================================================================
#  SECTION 8c: Optional Local Ollama Inference Engine & Model Pulling
# =============================================================================
echo "=========================================================================="
echo "  Optional: Local Ollama Inference Engine"
echo "=========================================================================="
echo ""
echo "  Deploy a dedicated local Ollama container for LLM inference directly on"
echo "  this host (CPU or NVIDIA GPU). Default: No (use existing cluster/endpoints)."
echo ""
INSTALL_OLLAMA="false"
OLLAMA_GPU_ENABLED="false"
PULL_PLANNER_MODEL="false"
PULL_JUDGE_MODEL="false"
PULL_EXPERT_MODEL="false"

while true; do
  [[ "$HAS_TTY" == "1" ]] && read -rp "  Deploy local Ollama instance (Docker container)? [y/N]: " _ollama_choice < /dev/tty
  _ollama_choice="${_ollama_choice:-N}"
  case "${_ollama_choice,,}" in
    y|yes) INSTALL_OLLAMA="true"; break ;;
    n|no)  INSTALL_OLLAMA="false"; break ;;
    *) echo "  Please enter y or n." ;;
  esac
done

if [[ "$INSTALL_OLLAMA" == "true" ]]; then
  echo ""
  # Detect NVIDIA GPU on host
  _has_nvidia=false
  if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
    _has_nvidia=true
  fi
  if [[ "$_has_nvidia" == "true" ]]; then
    echo "  NVIDIA GPU detected on this host."
    [[ "$HAS_TTY" == "1" ]] && read -rp "  Enable NVIDIA GPU acceleration for Ollama? [Y/n]: " _gpu_choice < /dev/tty
    _gpu_choice="${_gpu_choice:-Y}"
    case "${_gpu_choice,,}" in
      n|no) OLLAMA_GPU_ENABLED="false" ;;
      *)    OLLAMA_GPU_ENABLED="true" ;;
    esac
  else
    echo "  No NVIDIA GPU detected — Ollama will run in CPU mode."
    [[ "$HAS_TTY" == "1" ]] && read -rp "  Enable NVIDIA GPU container passthrough anyway? [y/N]: " _gpu_choice < /dev/tty
    _gpu_choice="${_gpu_choice:-N}"
    case "${_gpu_choice,,}" in
      y|yes) OLLAMA_GPU_ENABLED="true" ;;
      *)     OLLAMA_GPU_ENABLED="false" ;;
    esac
  fi

  echo ""
  echo "  --- Model Pulling Options (Individual Confirmation) ---"
  echo "  You can optionally pre-pull Sovereign models into the local Ollama instance:"
  
  [[ "$HAS_TTY" == "1" ]] && read -rp "  Pull Sovereign Planner LLM (moe-sovereign-student:4b from HuggingFace)? [y/N]: " _p_choice < /dev/tty
  _p_choice="${_p_choice:-N}"
  case "${_p_choice,,}" in
    y|yes) PULL_PLANNER_MODEL="true" ;;
    *)     PULL_PLANNER_MODEL="false" ;;
  esac

  [[ "$HAS_TTY" == "1" ]] && read -rp "  Pull Sovereign Judge & Refiner LLM (sovereign-judge:35b-q4km from HuggingFace)? [y/N]: " _j_choice < /dev/tty
  _j_choice="${_j_choice:-N}"
  case "${_j_choice,,}" in
    y|yes) PULL_JUDGE_MODEL="true" ;;
    *)     PULL_JUDGE_MODEL="false" ;;
  esac

  [[ "$HAS_TTY" == "1" ]] && read -rp "  Pull General 35B Expert LLM (qwen3.6:35b)? [y/N]: " _e_choice < /dev/tty
  _e_choice="${_e_choice:-N}"
  case "${_e_choice,,}" in
    y|yes) PULL_EXPERT_MODEL="true" ;;
    *)     PULL_EXPERT_MODEL="false" ;;
  esac
fi
export INSTALL_OLLAMA OLLAMA_GPU_ENABLED PULL_PLANNER_MODEL PULL_JUDGE_MODEL PULL_EXPERT_MODEL
echo ""

# =============================================================================
#  SECTION 8d: RAM check — warn if host memory is below stack requirements
# =============================================================================
_ram_total_mb=0
if [[ -r /proc/meminfo ]]; then
  _ram_total_mb=$(awk '/^MemTotal:/{printf "%d", $2/1024}' /proc/meminfo)
elif command -v sysctl &>/dev/null; then
  # macOS fallback
  _ram_bytes=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
  _ram_total_mb=$(( _ram_bytes / 1024 / 1024 ))
fi

if [[ "${_ram_total_mb}" -gt 0 ]]; then
  # Calculate minimum requirement based on selected components
  _ram_min_mb=4096                                               # core baseline
  [[ "${INSTALL_NEO4J:-true}"  == "true" ]] && _ram_min_mb=$(( _ram_min_mb + 1536 ))  # +1.5 GB
  [[ "${INSTALL_CODEX:-false}" == "true" ]] && _ram_min_mb=$(( _ram_min_mb + 8704 ))  # +8.5 GB

  _ram_rec_mb=$(( _ram_min_mb * 2 ))  # recommended = 2× minimum

  _ram_total_gb=$(( _ram_total_mb / 1024 ))
  _ram_min_gb=$(( _ram_min_mb / 1024 ))
  _ram_rec_gb=$(( _ram_rec_mb / 1024 ))

  echo "  Host RAM detected: ${_ram_total_gb} GB"

  if [[ "${_ram_total_mb}" -lt "${_ram_min_mb}" ]]; then
    echo ""
    echo "  [!] WARNING: Host RAM (${_ram_total_gb} GB) is below the minimum"
    echo "      requirement (${_ram_min_gb} GB) for the selected stack."
    echo "      Containers may be OOM-killed. Consider:"
    [[ "${INSTALL_CODEX:-false}" == "true" ]] && \
      echo "        - Skipping MoE Codex (-8.5 GB)"
    [[ "${INSTALL_NEO4J:-true}" == "true" ]] && \
      echo "        - Skipping Neo4j (-1.5 GB)"
    echo "        - Adding swap space as emergency buffer"
    echo ""
    [[ "$HAS_TTY" == "1" ]] && read -rp "  Continue anyway? [y/N]: " _ram_cont < /dev/tty
    [[ "${_ram_cont,,}" == "y" || "${_ram_cont,,}" == "yes" ]] || { echo "  Aborted."; exit 1; }
  elif [[ "${_ram_total_mb}" -lt "${_ram_rec_mb}" ]]; then
    echo "  [!] Note: ${_ram_rec_gb} GB recommended for this stack — performance may vary."
  else
    echo "  RAM sufficient for selected stack ✓"
  fi
  echo ""
fi

# =============================================================================
#  SECTION 9: Caddyfile — only when Caddy was selected
# =============================================================================
# Skipped when INSTALL_CADDY=false (user runs Nginx, Traefik, or no proxy).
# When enabled, docker-compose activates the moe-caddy service via the
# "caddy" profile (COMPOSE_PROFILES=caddy in .env).
if [[ "$INSTALL_CADDY" == "true" ]]; then
  CADDYFILE="${INSTALL_DIR}/Caddyfile"
  # Helper: strip scheme and path from a URL → bare hostname for Caddy
  _caddy_host() { echo "${1:-}" | sed 's|^https\?://||' | cut -d/ -f1; }

  if [[ ! -f "${CADDYFILE}" ]]; then
    if [[ -n "${DOMAIN:-}" ]]; then
      _h_admin=$(_caddy_host "${DEFAULT_ADMIN_URL:-admin.${DOMAIN}}")
      _h_portal=$(_caddy_host "${DEFAULT_BASE_URL:-${DOMAIN}}")
      _h_api=$(_caddy_host "${DEFAULT_API_URL:-api.${DOMAIN}}")

      {
        echo "# Generated by install.sh — edit as needed, then: ${COMPOSE} restart moe-caddy"
        echo ""
        echo "${_h_admin} {"
        echo "    reverse_proxy moe-admin:8088"
        echo "}"
        echo ""
        echo "${_h_api} {"
        echo "    reverse_proxy langgraph-orchestrator:8000"
        echo "}"
        echo ""
        echo "grafana.${DOMAIN} {"
        echo "    reverse_proxy moe-grafana:3000"
        echo "}"
        echo ""
        echo "logs.${DOMAIN} {"
        echo "    reverse_proxy moe-dozzle:8080"
        echo "}"
        echo ""
        echo "docs.${DOMAIN} {"
        echo "    reverse_proxy moe-docs:8000"
        echo "}"
        echo ""
        echo "files.${DOMAIN} {"
        echo "    reverse_proxy moe-storage-garage:3900"
        echo "}"
        if [[ "$INSTALL_AUTHENTIK" == "true" && -n "${AUTHENTIK_URL_INPUT:-}" ]]; then
          _h_sso=$(_caddy_host "${AUTHENTIK_URL_INPUT}")
          echo ""
          echo "${_h_sso} {"
          echo "    reverse_proxy authentik-server:9000"
          echo "}"
        fi
        echo ""
        # portal.DOMAIN — user-facing login; /login blocked to prevent admin access
        echo "${_h_portal} {"
        echo "    @admin_login path /login"
        echo "    redir @admin_login /user/login 302"
        echo "    @root path /"
        echo "    redir @root /user/login 302"
        echo "    reverse_proxy moe-admin:8088"
        echo "}"
        echo ""
        # bare domain → portal
        echo "${DOMAIN} {"
        echo "    handle /install.sh {"
        echo "        root * /srv"
        echo "        file_server"
        echo "    }"
        echo "    redir * https://${_h_portal}{uri} 302"
        echo "}"
      } > "${CADDYFILE}"
      echo "[6/9] Caddyfile generated for domain '${DOMAIN}' ✓"
    else
      cat > "${CADDYFILE}" <<CADDY
# Minimal localhost stub — replace with Caddyfile.example once a domain is set,
# then: sudo ${COMPOSE} restart moe-caddy
:80 {
    respond "MoE Sovereign — configure domain in Admin UI" 200
}
CADDY
      echo "[6/9] Caddyfile: localhost stub written (no domain configured) ✓"
    fi
  else
    # Migrate: older installs used the host-bridge IP (10.89.x.x:8002) instead
    # of the container-DNS name.  That address belongs to the podman1 bridge —
    # a different Podman network from the compose-internal one — so requests
    # routed to it silently fail or time out.  Patch in-place using Python to
    # avoid breaking the bind-mount (sed -i replaces the file inode, which
    # would unmount the volume inside the running Caddy container).
    if grep -qE '10\.[0-9]+\.[0-9]+\.[0-9]+:8002' "${CADDYFILE}" 2>/dev/null; then
      python3 - "${CADDYFILE}" <<'PYEOF'
import sys, re
path = sys.argv[1]
with open(path) as f:
    c = f.read()
c = re.sub(r'reverse_proxy\s+10\.\d+\.\d+\.\d+:8002',
           'reverse_proxy langgraph-orchestrator:8000', c)
with open(path, 'w') as f:
    f.write(c)
PYEOF
      echo "[6/9] Caddyfile migrated: host-bridge IP → container-DNS ✓"
    else
      echo "[6/9] Caddyfile already exists — skipping ✓"
    fi
  fi
else
  echo "[6/9] Caddy skipped — configure your existing proxy manually ✓"
fi

# =============================================================================
#  SECTION 9b: Dozzle users file (generated automatically on first compose start)
# =============================================================================
# The moe-dozzle-init container reads ADMIN_USER / ADMIN_PASSWORD from .env
# and generates /opt/moe-sovereign/dozzle-users.yml with a bcrypt hash on startup.
# No manual step required here.
echo "[6b/9] Dozzle credentials: generated by moe-dozzle-init on first start ✓"

# =============================================================================
#  SECTION 10: Write .env
# =============================================================================
echo "[7/9] Writing configuration to ${MOE_ENV_FILE}..."

# If an existing .env exists, back it up
if [[ -f "${MOE_ENV_FILE}" ]]; then
  cp "${MOE_ENV_FILE}" "${MOE_ENV_FILE}.bak.$(date +%Y%m%d_%H%M%S)"
  echo "  Existing .env backed up ✓"
fi

# Write .env using printf to safely handle passwords with special characters
# (single quotes, dollar signs, backticks etc. are not interpreted).
{
  echo "# MoE Sovereign — Runtime Configuration"
  echo "# Generated by install.sh on $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
  echo "# Edit this file or use the Admin UI to change settings."
  echo "# After changes: sudo ${COMPOSE} up -d"
  echo ""
  echo "# --- Compose profiles (controls optional services) ---"
  _env_profiles=()
  [[ "$INSTALL_NEO4J"     == "true" ]] && _env_profiles+=(neo4j)
  [[ "$INSTALL_CADDY"     == "true" ]] && _env_profiles+=(caddy)
  [[ "$INSTALL_AUTHENTIK" == "true" ]] && _env_profiles+=(authentik)
  [[ "${INSTALL_OLLAMA:-false}" == "true" ]] && _env_profiles+=(ollama)
  [[ "${INSTALL_MONITORING:-true}" == "true" ]] && _env_profiles+=(monitoring)
  printf 'COMPOSE_PROFILES=%s\n' "$(IFS=,; echo "${_env_profiles[*]}")"
  printf 'INSTALL_CODEX=%s\n' "${INSTALL_CODEX:-false}"
  printf 'INSTALL_OLLAMA=%s\n' "${INSTALL_OLLAMA:-false}"
  printf 'OLLAMA_GPU_ENABLED=%s\n' "${OLLAMA_GPU_ENABLED:-false}"
  printf 'OLLAMA_HOST_PORT=%s\n' "${OLLAMA_HOST_PORT:-11434}"
  echo ""
  echo "# --- Container runtime socket + storage paths ---"
  echo "# Docker: DOCKER_SOCKET=/var/run/docker.sock, CONTAINER_STORAGE_ROOT=/var/lib/docker"
  echo "# Podman: DOCKER_SOCKET=/run/user/<uid>/podman/podman.sock, CONTAINER_STORAGE_ROOT=~/.local/share/containers"
  if [[ "$CONTAINER_RUNTIME" == "podman" ]]; then
    printf 'DOCKER_SOCKET=%s\n'              "${_PODMAN_SOCKET:-/run/user/1000/podman/podman.sock}"
    printf 'CONTAINER_STORAGE_ROOT=%s\n'    "${HOME}/.local/share/containers"
  else
    echo 'DOCKER_SOCKET=/var/run/docker.sock'
    echo 'CONTAINER_STORAGE_ROOT=/var/lib/docker'
  fi
  echo ""
  echo "# --- Host data roots (bind-mount sources) ---"
  printf 'MOE_DATA_ROOT=%s\n'            "${MOE_DATA_ROOT}"
  printf 'GRAFANA_DATA_ROOT=%s\n'        "${GRAFANA_DATA_ROOT}"
  printf 'FEW_SHOT_HOST_DIR=%s\n'        "${MOE_DATA_ROOT}/few-shot"
  echo ""
  echo "# --- Authentication (Admin UI) ---"
  printf 'ADMIN_USER=%s\n'               "${ADMIN_USER}"
  printf 'ADMIN_PASSWORD=%s\n'           "${ADMIN_PASSWORD}"
  printf 'ADMIN_SECRET_KEY=%s\n'         "${GEN_ADMIN_SECRET}"
  echo ""
  echo "# --- Infrastructure Passwords ---"
  printf 'REDIS_PASSWORD=%s\n'           "${GEN_REDIS_PASS}"
  printf 'REDIS_URL=redis://:%s@terra_cache:6379/0\n' "${GEN_REDIS_PASS}"
  # NEO4J_URI empty when Neo4j is not installed — signals _init_graph_rag() to skip
  if [[ "$INSTALL_NEO4J" == "true" ]]; then
    printf 'NEO4J_PASS=%s\n' "${GEN_NEO4J_PASS}"
    echo 'NEO4J_URI=bolt://neo4j-knowledge:7687'
    echo 'NEO4J_USER=neo4j'
  else
    echo 'NEO4J_PASS='
    echo 'NEO4J_URI='
    echo 'NEO4J_USER=neo4j'
  fi
  echo ""
  echo "# --- PostgreSQL ---"
  printf 'POSTGRES_CHECKPOINT_PASSWORD=%s\n' "${GEN_PG_LANGGRAPH_PASS}"
  printf 'POSTGRES_CHECKPOINT_URL=postgresql://langgraph:%s@terra_checkpoints:5432/langgraph\n' "${GEN_PG_LANGGRAPH_PASS}"
  printf 'MOE_USERDB_URL=postgresql://moe_admin:%s@terra_checkpoints:5432/moe_userdb\n' "${GEN_PG_USERDB_PASS}"
  printf 'MOE_USERDB_PASSWORD=%s\n' "${GEN_PG_USERDB_PASS}"
  printf 'LIBRIS_DB_PASSWORD=%s\n' "${GEN_LIBRIS_DB_PASS}"
  echo ""
  echo "# --- Grafana ---"
  printf 'GF_SECURITY_ADMIN_USER=%s\n'   "${ADMIN_USER}"
  printf 'GF_SECURITY_ADMIN_PASSWORD=%s\n' "${GEN_GRAFANA_PASS}"
  echo 'GF_AUTH_ANONYMOUS_ENABLED=false'
  echo 'GF_ANALYTICS_REPORTING_ENABLED=false'
  echo 'GF_ANALYTICS_CHECK_FOR_UPDATES=false'
  echo ""
  echo "# --- Public URLs (optional) ---"
  printf 'APP_BASE_URL=%s\n'             "${DEFAULT_BASE_URL}"
  printf 'PUBLIC_ADMIN_URL=%s\n'         "${DEFAULT_ADMIN_URL}"
  printf 'PUBLIC_API_URL=%s\n'           "${DEFAULT_API_URL}"
  echo ""
  echo "# --- Inference Servers: configure via the Setup Wizard after first login ---"
  echo 'INFERENCE_SERVERS=[]'
  echo 'JUDGE_ENDPOINT='
  echo 'JUDGE_MODEL='
  echo 'PLANNER_MODEL='
  echo 'PLANNER_ENDPOINT='
  echo ""
  echo "# --- Legacy inference fields (backward compat) ---"
  echo 'OLLAMA_API_KEY='
  echo 'URL_RTX='
  echo 'URL_TESLA='
  echo 'GPU_COUNT=0'
  echo 'GPU_COUNT_RTX=0'
  echo 'GPU_COUNT_TESLA=0'
  echo 'EXPERT_MODELS={}'
  echo ""
  echo "# --- Web Search (SearXNG) ---"
  echo '# Set to your SearXNG instance URL for web research (optional)'
  echo 'SEARXNG_URL='
  echo ""
  echo "# --- Vector Store (ChromaDB) ---"
  echo 'CHROMA_HOST=chromadb-vector'
  echo 'CHROMA_PORT=8000'
  echo ""
  echo "# --- Logging ---"
  echo 'LOG_LEVEL=INFO'
  echo ""
  echo "# --- Conversation audit log ---"
  echo 'CONVERSATION_LOG_ENABLED=true'
  echo 'CONVERSATION_LOG_RETENTION_DAYS_DEFAULT=90'
  echo 'CONVERSATION_LOG_RETENTION_MAX=365'
  echo ""
  echo "# --- Prometheus ---"
  echo 'PROMETHEUS_RETENTION_DAYS=30'
  echo ""
  echo "# --- Token Pricing ---"
  echo 'TOKEN_PRICE_EUR=0.0000002'
  echo ""
  echo "# --- Claude Code Integration ---"
  echo "# Set CLAUDE_SKILLS_DIR to your Claude Code commands directory if using the integration"
  echo 'CLAUDE_SKILLS_DIR='
  echo 'CLAUDE_CODE_TOOL_MODEL='
  echo 'CLAUDE_CODE_TOOL_ENDPOINT='
  echo 'CLAUDE_CODE_MODELS='
  echo 'CLAUDE_CODE_MODE=code'
  echo 'CLAUDE_CODE_REASONING_MODEL='
  echo 'CLAUDE_CODE_REASONING_ENDPOINT='
  echo 'CLAUDE_CODE_PROFILES=[]'
  echo ""
  echo "# --- Cache & Routing ---"
  echo 'CACHE_HIT_THRESHOLD=0.15'
  echo 'SOFT_CACHE_THRESHOLD=0.50'
  echo 'SOFT_CACHE_MAX_EXAMPLES=2'
  echo 'CACHE_MIN_RESPONSE_LEN=150'
  echo 'EXPERT_TIER_BOUNDARY_B=20'
  echo 'EXPERT_MIN_SCORE=0.3'
  echo 'EXPERT_MIN_DATAPOINTS=5'
  echo 'EVAL_CACHE_FLAG_THRESHOLD=2'
  echo 'FEEDBACK_POSITIVE_THRESHOLD=4'
  echo 'FEEDBACK_NEGATIVE_THRESHOLD=2'
  echo ""
  echo "# --- Host-facing ports (change to avoid conflicts on the host) ---"
  echo 'KAFKA_HOST_PORT=9092'
  echo 'MCP_HOST_PORT=8003'
  echo 'LANGGRAPH_HOST_PORT=8002'
  echo 'CHROMA_HOST_PORT=8001'
  echo 'PROMETHEUS_HOST_PORT=9090'
  echo 'ADMIN_UI_HOST_PORT=8088'
  echo 'GRAFANA_HOST_PORT=3001'
  echo 'NODE_EXPORTER_HOST_PORT=9100'
  echo 'CADVISOR_HOST_PORT=9338'
  echo 'DOCS_HOST_PORT=8098'
  echo 'DOZZLE_HOST_PORT=9999'
  echo 'NEO4J_HTTP_PORT=7474'
  echo 'NEO4J_BOLT_PORT=7687'
  echo ""
  echo "# --- Localisation ---"
  echo 'TZ=Europe/Berlin'
  echo ""
  echo "# --- Garage S3 endpoint (internal compose network address) ---"
  echo 'GARAGE_S3_ENDPOINT=http://moe-storage-garage:3900'
  echo ""
  echo "# --- MoE Codex ports (only used when INSTALL_CODEX=true) ---"
  echo 'JUPYTERLAB_HOST_PORT=8899'
  echo 'NIFI_HOST_PORT=8181'
  echo 'MARQUEZ_HOST_PORT=5000'
  echo 'MARQUEZ_ADMIN_PORT=5001'
  echo 'MARQUEZ_WEB_PORT=3030'
  echo 'LAKEFS_HOST_PORT=8010'
  printf 'NIFI_ADMIN_USER=%s\n'            "${ADMIN_USER}"
  printf 'NIFI_ADMIN_PASSWORD=%s\n'        "${GEN_NIFI_PASS}"
  printf 'MARQUEZ_POSTGRES_PASSWORD=%s\n'  "${GEN_MARQUEZ_PG_PASS}"
  printf 'LAKEFS_DB_PASSWORD=%s\n'         "${GEN_LAKEFS_DB_PASS}"
  printf 'LAKEFS_ENCRYPT_SECRET=%s\n'      "${GEN_LAKEFS_ENCRYPT}"
  printf 'LAKEFS_ACCESS_KEY_ID=%s\n'       "${GEN_LAKEFS_AK_ID}"
  printf 'LAKEFS_SECRET_ACCESS_KEY=%s\n'   "${GEN_LAKEFS_SK}"
  echo ""
  echo "# --- Container CPU Limits (increase on systems with more than 2 cores) ---"
  echo '# Default 2 is safe for 2-core hosts; raise per-service on larger machines.'
  echo 'NEO4J_CPU_LIMIT=2'
  echo 'LANGGRAPH_CPU_LIMIT=2'
  echo 'KAFKA_CPU_LIMIT=2'
  echo 'CHROMA_CPU_LIMIT=2'
  echo ""
  echo "# --- Timeouts & Limits ---"
  echo 'HISTORY_MAX_TURNS=4'
  echo 'HISTORY_MAX_CHARS=3000'
  echo 'JUDGE_TIMEOUT=3600'
  echo 'EXPERT_TIMEOUT=3600'
  echo 'PLANNER_TIMEOUT=3600'
  echo 'PLANNER_RETRIES=3'
  echo 'PLANNER_MAX_TASKS=8'
  echo 'TOOL_MAX_TOKENS=8192'
  echo 'REASONING_MAX_TOKENS=16384'
  echo 'SSE_CHUNK_SIZE=32'
  echo 'MAX_EXPERT_OUTPUT_CHARS=8000'
  echo 'JUDGE_REFINE_MAX_ROUNDS=1'
  echo 'JUDGE_REFINE_MIN_IMPROVEMENT=0.05'
  echo ""
  echo "# --- Streaming / CORS ---"
  echo 'CORS_ALL_ORIGINS=false'
  echo 'CORS_ORIGINS='
  echo ""
  echo "# --- Expert Templates (managed via Admin UI) ---"
  echo 'EXPERT_TEMPLATES=[]'
  echo 'CUSTOM_EXPERT_PROMPTS={}'
  echo ""
  echo "# --- Graph RAG ---"
  echo 'GRAPH_INGEST_MODEL='
  echo 'GRAPH_INGEST_ENDPOINT='
  echo ""
  echo "# --- SMTP (optional) ---"
  echo 'SMTP_HOST='
  echo 'SMTP_PORT=587'
  echo 'SMTP_USER='
  echo 'SMTP_PASS='
  echo 'SMTP_FROM='
  echo 'SMTP_STARTTLS=true'
  echo 'SMTP_SSL=false'
  echo ""
  echo "# --- SSO / OIDC (optional) ---"
  printf 'AUTHENTIK_URL=%s\n'     "${AUTHENTIK_URL_INPUT:-}"
  printf 'OIDC_CLIENT_ID=%s\n'    "${GEN_OIDC_CLIENT_ID:-}"
  printf 'OIDC_CLIENT_SECRET=%s\n' "${GEN_OIDC_CLIENT_SECRET:-}"
  printf 'OIDC_REDIRECT_URI=%s\n' "${GEN_OIDC_REDIRECT_URI:-}"
  printf 'OIDC_JWKS_URL=%s\n'     "${GEN_OIDC_JWKS_URL:-}"
  printf 'OIDC_ISSUER=%s\n'       "${GEN_OIDC_ISSUER:-}"
  printf 'OIDC_END_SESSION_URL=%s\n' "${GEN_OIDC_END_SESSION_URL:-}"
  echo 'PUBLIC_SSO_URL='
  echo ""
  echo "# --- Authentik Server (only active when COMPOSE_PROFILES includes 'authentik') ---"
  echo "AUTHENTIK_TAG=2026.2.1"
  printf 'AUTHENTIK_SECRET_KEY=%s\n'           "${GEN_AUTHENTIK_SECRET}"
  echo 'AUTHENTIK_POSTGRESQL__USER=authentik'
  echo 'AUTHENTIK_POSTGRESQL__NAME=authentik'
  printf 'AUTHENTIK_POSTGRESQL__PASSWORD=%s\n' "${GEN_AUTHENTIK_PG_PASS}"
  printf 'AUTHENTIK_BOOTSTRAP_EMAIL=%s\n'      "${AUTHENTIK_BOOTSTRAP_EMAIL_INPUT:-}"
  printf 'AUTHENTIK_BOOTSTRAP_PASSWORD=%s\n'   "${AUTHENTIK_BOOTSTRAP_PASSWORD_INPUT:-}"
  echo 'AUTHENTIK_HTTP_PORT=9000'
  echo 'AUTHENTIK_HTTPS_PORT=9443'
  echo 'AUTHENTIK_ERROR_REPORTING__ENABLED=false'
  echo ""
  echo "# --- Object Storage (Garage S3 — EOL MinIO replaced 2026-04-25) ---"
  echo "# MINIO_ENDPOINT: internal Garage S3 API URL — used by MCP server for file uploads"
  echo "# MINIO_ROOT_USER/PASSWORD: set to your Garage access key ID and secret after running:"
  echo "#   docker exec moe-storage-garage garage key create moe-mcp"
  printf 'MINIO_ROOT_USER=%s\n'     "${GEN_MINIO_USER}"
  printf 'MINIO_ROOT_PASSWORD=%s\n' "${GEN_MINIO_PASS}"
  echo 'MINIO_ENDPOINT=moe-storage-garage:3900'
  echo 'MINIO_PUBLIC_URL='
  echo 'MINIO_DEFAULT_BUCKET=moe-files'
  echo ""
  echo "# --- Garage cluster secret (also embedded in garage.toml — keep in sync) ---"
  printf 'GARAGE_RPC_SECRET=%s\n'   "${GEN_GARAGE_SECRET}"
  echo ""
  echo "# --- Garage S3 access key (fill after running: docker exec moe-storage-garage garage key create moe-mcp) ---"
  echo 'GARAGE_ACCESS_KEY_ID='
  echo 'GARAGE_SECRET_ACCESS_KEY='
} > "${MOE_ENV_FILE}"

# 644: containers bind-mount this :ro and run as non-root (uid 1001 for langgraph,
# uid 1001 for moe-admin). chmod 600 would lock them out. The file is read-only
# inside all containers anyway, so world-readable on the host is acceptable.
chmod 644 "${MOE_ENV_FILE}"
echo "  Configuration written ✓"

# =============================================================================
#  SECTION 11: Build and deploy
# =============================================================================
echo "[8/9] Building and starting MoE Sovereign..."
echo "  This may take several minutes on first run (image pulls + builds)."
echo ""

# Generate garage.toml if it does not exist yet.
# Garage requires a static config file mounted at /etc/garage.toml:ro.
# Podman (unlike Docker) refuses to start a container when a file bind-mount
# source is missing — so we must create the file before compose up.
_garage_toml="${MOE_DATA_ROOT}/garage/etc/garage.toml"
if [[ ! -f "${_garage_toml}" ]]; then
  _sudo tee "${_garage_toml}" > /dev/null <<GARAGE_TOML
# Generated by install.sh — edit to customise Garage S3 storage.
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
GARAGE_TOML
  echo "  garage.toml generated at ${_garage_toml} ✓"
fi

# Create Authentik data directories (bind-mount sources must exist before compose up)
if [[ "$INSTALL_AUTHENTIK" == "true" ]]; then
  _sudo mkdir -p "${MOE_DATA_ROOT}/authentik"
  for _ak_dir in postgres redis media certs custom-templates blueprints; do
    _sudo mkdir -p "${MOE_DATA_ROOT}/authentik/${_ak_dir}"
  done
  _sudo chown -R "${DEPLOY_USER}:${DEPLOY_USER}" "${MOE_DATA_ROOT}/authentik"
  echo "  Authentik data directories created under ${MOE_DATA_ROOT}/authentik/ ✓"

  # Build OIDC redirect URIs: always include localhost fallback
  _ak_redirect_uris="http://localhost:8088/auth/callback"
  if [[ -n "${DOMAIN:-}" ]]; then
    _ak_redirect_uris="${_ak_redirect_uris}
https://admin.${DOMAIN}/auth/callback
https://portal.${DOMAIN}/auth/callback"
  fi

  # Write Authentik blueprint — applied automatically by the worker on first start.
  # Creates the OAuth2/OIDC provider + MoE Sovereign application with pre-generated
  # client credentials so no manual Authentik UI interaction is needed.
  cat > "${MOE_DATA_ROOT}/authentik/blueprints/moe-sovereign.yaml" <<BLUEPRINT
# Generated by install.sh — applied once by authentik-worker on first start.
# Re-running install.sh regenerates this file with the current credentials.
version: 1
metadata:
  name: MoE Sovereign OIDC
entries:
  - model: authentik_providers_oauth2.oauth2provider
    state: present
    identifiers:
      name: MoE Sovereign
    attrs:
      name: MoE Sovereign
      client_type: confidential
      client_id: moe-sovereign
      client_secret: "${GEN_AUTHENTIK_SECRET}"
      authorization_flow: !Find [authentik_flows.flow, [slug, default-provider-authorization-implicit-consent]]
      redirect_uris: |
$(echo "${_ak_redirect_uris}" | sed 's/^/        /')
      signing_key: !Find [authentik_crypto.certificatekeypair, [name, authentik Self-signed Certificate]]
      include_claims_in_id_token: true
      issuer_mode: per_provider

  - model: authentik_core.application
    state: present
    identifiers:
      slug: moe-sovereign
    attrs:
      name: MoE Sovereign
      slug: moe-sovereign
      provider: !KeyOf moe-sovereign
      meta_launch_url: "${AUTHENTIK_URL_INPUT}/if/user/"
BLUEPRINT
  echo "  Authentik blueprint written ✓"

  # Pre-fill OIDC vars in the installer scope so they land in .env
  GEN_OIDC_CLIENT_ID="moe-sovereign"
  GEN_OIDC_CLIENT_SECRET="${GEN_AUTHENTIK_SECRET}"
  if [[ -n "${DOMAIN:-}" ]]; then
    GEN_OIDC_REDIRECT_URI="https://admin.${DOMAIN}/auth/callback"
    GEN_OIDC_ISSUER="${AUTHENTIK_URL_INPUT}/application/o/moe-sovereign/"
    GEN_OIDC_JWKS_URL="${AUTHENTIK_URL_INPUT}/application/o/moe-sovereign/jwks/"
    GEN_OIDC_END_SESSION_URL="${AUTHENTIK_URL_INPUT}/application/o/moe-sovereign/end-session/"
  else
    GEN_OIDC_REDIRECT_URI="http://localhost:8088/auth/callback"
    GEN_OIDC_ISSUER="${AUTHENTIK_URL_INPUT}/application/o/moe-sovereign/"
    GEN_OIDC_JWKS_URL="${AUTHENTIK_URL_INPUT}/application/o/moe-sovereign/jwks/"
    GEN_OIDC_END_SESSION_URL="${AUTHENTIK_URL_INPUT}/application/o/moe-sovereign/end-session/"
  fi
fi

# Build explicit --profile flags: podman-compose does not auto-read COMPOSE_PROFILES
# from .env; docker compose does. Passing --profile explicitly works for both.
_PROFILE_ARGS=()
[[ "$INSTALL_NEO4J"     == "true" ]] && _PROFILE_ARGS+=(--profile neo4j)
[[ "$INSTALL_CADDY"     == "true" ]] && _PROFILE_ARGS+=(--profile caddy)
[[ "$INSTALL_AUTHENTIK" == "true" ]] && _PROFILE_ARGS+=(--profile authentik)

# Docker Compose >= v2.37 defaults to the BuildKit bake backend, which requires
# explicit --allow=network.host when any build block carries "network: host".
# We removed those entries from docker-compose.yml, but as a safety net we also
# opt out of the bake backend when it would otherwise block the build.
# COMPOSE_BAKE=0 falls back to the classic sequential docker-build path.
if [[ "$CONTAINER_RUNTIME" == "docker" ]] && [[ "${COMPOSE_BAKE:-}" != "0" ]]; then
  _bake_blocked=0
  if docker buildx bake --help 2>&1 | grep -q "network.host" 2>/dev/null; then
    _bake_blocked=1
  elif docker compose build --help 2>&1 | grep -q "bake" 2>/dev/null; then
    _compose_ver=$(docker compose version --short 2>/dev/null || echo "0")
    _compose_minor=$(echo "$_compose_ver" | cut -d. -f2)
    [[ "${_compose_minor:-0}" -ge 37 ]] && _bake_blocked=1
  fi
  if [[ "$_bake_blocked" -eq 1 ]] && [[ "${COMPOSE_BAKE:-}" != "1" ]]; then
    export COMPOSE_BAKE=0
    echo "  [info] COMPOSE_BAKE=0 — using classic build backend (BuildKit bake requires --allow=network.host on this Docker version)"
  fi
fi

_compose "${_PROFILE_ARGS[@]}" pull ${_Q} 2>/dev/null || true
_compose "${_PROFILE_ARGS[@]}" build ${_Q}
_compose "${_PROFILE_ARGS[@]}" up -d

if [[ "${INSTALL_CODEX:-false}" == "true" ]]; then
  echo ""
  echo "  Starting MoE Codex (JupyterLab, NiFi, Marquez, lakeFS)..."
  _compose -f docker-compose.codex.yml --profile codex pull ${_Q} 2>/dev/null || true
  _compose -f docker-compose.codex.yml --profile codex up -d
  _bootstrap_codex_stack
  echo "  MoE Codex started ✓"
fi

echo "  Containers started ✓"

_write_services_manifest

# =============================================================================
#  SECTION 12: Health check
# =============================================================================
echo "[9/9] Waiting for MoE Sovereign API to become ready..."

# The /v1/models endpoint requires auth, so we check the metrics endpoint
# which is unauthenticated and confirms the FastAPI app is running.
HEALTH_URL="http://localhost:8002/metrics"
MAX_WAIT=120
INTERVAL=5
ELAPSED=0

while true; do
  if curl -sf "${HEALTH_URL}" &>/dev/null; then
    echo "  API ready ✓"
    break
  fi
  if [[ $ELAPSED -ge $MAX_WAIT ]]; then
    echo ""
    echo "  [!] API did not respond within ${MAX_WAIT}s."
    echo "      Check logs with: cd ${INSTALL_DIR} && sudo ${COMPOSE} logs langgraph-app"
    echo "      The rest of the stack may still be starting — this is normal"
    echo "      on first run while models and databases initialize."
    break
  fi
  printf "."
  sleep $INTERVAL
  ELAPSED=$(( ELAPSED + INTERVAL ))
done

# =============================================================================
#  SECTION 12b: Seed Knowledge Graph & Vector Store from data/corpora/
# =============================================================================
if [[ -f "${INSTALL_DIR}/scripts/ingest_corpora_batch.py" ]]; then
  echo ""
  echo "[12b/13] Seeding Knowledge Graph & Vector Store from data/corpora/ ..."
  if command -v python3 &>/dev/null; then
    python3 "${INSTALL_DIR}/scripts/ingest_corpora_batch.py" --corpora-dir "${INSTALL_DIR}/data/corpora" || echo "  [!] Knowledge ingestion skipped (will resume on stack start)"
  fi
fi

# =============================================================================
#  SECTION 12c: Pull confirmed models to local Ollama container
# =============================================================================
if [[ "${INSTALL_OLLAMA:-false}" == "true" ]]; then
  echo ""
  echo "[12c/13] Checking local Ollama inference service..."
  _ollama_ready=false
  for _try in {1..30}; do
    if curl -sf "http://127.0.0.1:${OLLAMA_HOST_PORT:-11434}/api/tags" >/dev/null 2>&1; then
      _ollama_ready=true
      break
    fi
    sleep 2
  done

  if [[ "$_ollama_ready" == "true" ]]; then
    echo "  Local Ollama service is online ✓"
    if [[ "${PULL_PLANNER_MODEL:-false}" == "true" ]]; then
      echo "  Pulling Planner LLM: hf.co/h3rb3rn/moe-sovereign-student-4b:latest ..."
      _compose exec -T moe-ollama ollama pull hf.co/h3rb3rn/moe-sovereign-student-4b:latest || true
    fi
    if [[ "${PULL_JUDGE_MODEL:-false}" == "true" ]]; then
      echo "  Pulling Judge LLM: hf.co/h3rb3rn/Qwen3-MoE-35B-Sovereign-Judge-v3-GGUF:sovereign-judge-35b-q4_k_m.gguf ..."
      _compose exec -T moe-ollama ollama pull hf.co/h3rb3rn/Qwen3-MoE-35B-Sovereign-Judge-v3-GGUF:sovereign-judge-35b-q4_k_m.gguf || true
    fi
    if [[ "${PULL_EXPERT_MODEL:-false}" == "true" ]]; then
      echo "  Pulling Expert LLM: qwen3.6:35b ..."
      _compose exec -T moe-ollama ollama pull qwen3.6:35b || true
    fi
  else
    echo "  [!] Local Ollama container did not report healthy in time — skipping pre-pull."
  fi
fi

# =============================================================================
#  SECTION 13: Success banner
# =============================================================================
echo ""
echo "=========================================================================="
echo ""
echo "  MoE Sovereign is installed and running!"
echo ""

if [[ -n "${DOMAIN:-}" ]]; then
  echo "  Admin UI:    https://admin.${DOMAIN}"
  echo "  API:         https://api.${DOMAIN}  (or http://localhost:8088)"
  echo "  Docs:        https://docs.${DOMAIN}"
  if [[ "$INSTALL_CADDY" == "true" ]]; then
    echo "  Log Viewer:  https://logs.${DOMAIN}"
  else
    echo "  Log Viewer:  http://localhost:8082 (direct, no proxy)"
  fi
else
  echo "  Admin UI:    http://localhost:8088"
  echo "  API:         http://localhost:8002"
  if [[ "$INSTALL_CADDY" == "true" ]]; then
    echo "  Log Viewer:  http://localhost (via Caddy)"
  else
    echo "  Log Viewer:  http://localhost:8082 (direct)"
  fi
fi

if [[ "$INSTALL_AUTHENTIK" == "true" ]]; then
  echo ""
  if [[ -n "${DOMAIN:-}" ]]; then
    echo "  Authentik:   https://sso.${DOMAIN}"
  else
    echo "  Authentik:   http://localhost:9000"
  fi
  echo "  Admin login: ${AUTHENTIK_BOOTSTRAP_EMAIL_INPUT}"
  echo ""
  echo "  OIDC is pre-configured via blueprint — no manual Authentik UI steps needed."
  echo "  Client ID:   moe-sovereign"
  echo "  Issuer:      ${AUTHENTIK_URL_INPUT}/application/o/moe-sovereign/"
  echo ""
  echo "  [!] Authentik needs ~60s to initialise and apply the blueprint on first start."
  echo "      The Admin UI SSO login will work automatically once Authentik is ready."
elif [[ "$INSTALL_SSO" == "true" ]]; then
  echo ""
  echo "  SSO/OIDC:    ${AUTHENTIK_URL_INPUT}"
  echo "  [!] Complete OIDC client registration in Authentik, then add"
  echo "      Client ID, Secret and redirect URI via Admin UI → Settings → SSO."
fi

echo ""
echo "  Login credentials:"
echo "    Username: ${ADMIN_USER}"
echo "    Password: (as configured)"
echo ""
echo "  NEXT STEPS:"
echo "  1. Open the Admin UI and complete the Setup Wizard"
echo "  2. Add at least one inference server (Ollama, OpenAI, LiteLLM, etc.)"
echo "  3. Pull Sovereign Planner & Judge LLMs from HuggingFace:"
echo "     • Planner (4.2B Student):"
echo "       https://huggingface.co/h3rb3rn/moe-sovereign-student-4b"
echo "       ollama run hf.co/h3rb3rn/moe-sovereign-student-4b:latest"
echo "     • Judge & Refiner (35B Sovereign Judge v3 GGUF):"
echo "       https://huggingface.co/h3rb3rn/Qwen3-MoE-35B-Sovereign-Judge-v3-GGUF"
echo "       ollama run hf.co/h3rb3rn/Qwen3-MoE-35B-Sovereign-Judge-v3-GGUF:sovereign-judge-35b-q4_k_m.gguf"
echo "  4. Start chatting at your Open WebUI instance"
echo ""
echo "  Logs:    cd ${INSTALL_DIR} && sudo ${COMPOSE} logs -f"
echo "  Status:  cd ${INSTALL_DIR} && sudo ${COMPOSE} ps"
echo "  Stop:    cd ${INSTALL_DIR} && sudo ${COMPOSE} down"
echo ""
echo "  Project: https://github.com/h3rb3rn/moe-sovereign"
echo "  Docs:    https://docs.moe-sovereign.org"
echo "  Models:  https://huggingface.co/h3rb3rn"
echo ""
echo "=========================================================================="
echo ""
echo "  [!] SECURITY: configure your host firewall before exposing this box."
echo "      Ports 8002, 8003, 8088, 8098 and 3001 are published on 0.0.0.0 and"
echo "      several of them (e.g. /graph/*, /v1/admin/*) have NO authentication"
echo "      by design — they rely on network-level isolation. Only 80/443 should"
echo "      be reachable from the public internet (served by Caddy)."
echo ""
echo "      Full firewall recipe (UFW / firewalld / iptables):"
echo "      https://docs.moe-sovereign.org/deployment/firewall/"
echo ""
echo "=========================================================================="
}

moe_sovereign_install "$@"
