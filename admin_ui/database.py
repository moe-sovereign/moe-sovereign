"""
User-management database for MoE Admin UI.
Postgres (psycopg3) via a module-global AsyncConnectionPool + bcrypt for passwords.
Valkey sync for fast auth lookups in the orchestrator.
"""

import asyncio
import hashlib
import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

import base64

import bcrypt as _bcrypt
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from cryptography.fernet import Fernet

_SECRET_KEY_RAW = os.getenv("ADMIN_SECRET_KEY", "")


def _get_fernet() -> Fernet:
    """Derive a stable Fernet key from ADMIN_SECRET_KEY via SHA-256."""
    raw = hashlib.sha256(_SECRET_KEY_RAW.encode()).digest() if _SECRET_KEY_RAW else b"\x00" * 32
    return Fernet(base64.urlsafe_b64encode(raw))


def encrypt_api_key(plaintext: str) -> str:
    """Fernet-encrypt an API key string. Returns empty string for empty input."""
    return "" if not plaintext else _get_fernet().encrypt(plaintext.encode()).decode()


def decrypt_api_key(ciphertext: str) -> str:
    """Decrypt a Fernet-encrypted API key. Returns empty string on failure or empty input."""
    if not ciphertext:
        return ""
    try:
        return _get_fernet().decrypt(ciphertext.encode()).decode()
    except Exception:
        logger.warning("Failed to decrypt user API key — possible key rotation or data corruption")
        return ""

DATABASE_URL = os.getenv(
    "MOE_USERDB_URL",
    "postgresql://moe_admin@terra_checkpoints:5432/moe_userdb",
)
REDIS_URL    = os.getenv("REDIS_URL", "redis://terra_cache:6379")

# Backwards-compat shim: older code paths referenced DB_PATH for log messages.
# The variable is kept as a human-readable identifier only.
DB_PATH = DATABASE_URL


# ─── Schema ──────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                    TEXT PRIMARY KEY,
    username              TEXT UNIQUE NOT NULL,
    email                 TEXT UNIQUE NOT NULL,
    display_name          TEXT NOT NULL DEFAULT '',
    hashed_password       TEXT NOT NULL,
    is_active             BOOLEAN NOT NULL DEFAULT TRUE,
    is_admin              BOOLEAN NOT NULL DEFAULT FALSE,
    role                  TEXT NOT NULL DEFAULT 'user',
    language              TEXT NOT NULL DEFAULT 'de_DE',
    timezone_offset_hours DOUBLE PRECISION NOT NULL DEFAULT 0,
    alert_enabled         BOOLEAN NOT NULL DEFAULT FALSE,
    alert_threshold_pct   INTEGER NOT NULL DEFAULT 80,
    alert_email           TEXT,
    last_alert_sent_at    TEXT,
    default_cc_profile_id TEXT,
    -- Stammdaten
    first_name            TEXT NOT NULL DEFAULT '',
    last_name             TEXT NOT NULL DEFAULT '',
    street_address        TEXT NOT NULL DEFAULT '',
    postal_code           TEXT NOT NULL DEFAULT '',
    city                  TEXT NOT NULL DEFAULT '',
    country               TEXT NOT NULL DEFAULT '',
    date_of_birth         TEXT,
    gender                TEXT,
    -- Conversation audit log: NULL means use the global default from config
    conversation_log_retention_days  INTEGER,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_keys (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_hash      TEXT UNIQUE NOT NULL,
    key_prefix    TEXT NOT NULL,
    label         TEXT NOT NULL DEFAULT '',
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    cc_profile_id      TEXT,
    dynamic_routing    BOOLEAN NOT NULL DEFAULT FALSE,
    local_only_routing BOOLEAN NOT NULL DEFAULT FALSE,
    created_at         TEXT NOT NULL,
    last_used_at       TEXT,
    expires_at         TEXT
);

CREATE TABLE IF NOT EXISTS token_budgets (
    user_id       TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    daily_limit   BIGINT,
    monthly_limit BIGINT,
    total_limit   BIGINT,
    budget_type   TEXT NOT NULL DEFAULT 'subscription',
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS permissions (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    resource_type TEXT NOT NULL,
    resource_id   TEXT NOT NULL,
    granted_at    TEXT NOT NULL,
    UNIQUE(user_id, resource_type, resource_id)
);

CREATE TABLE IF NOT EXISTS usage_log (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL REFERENCES users(id),
    api_key_id        TEXT REFERENCES api_keys(id),
    request_id        TEXT NOT NULL,
    session_id        TEXT,
    model             TEXT NOT NULL,
    moe_mode          TEXT NOT NULL,
    prompt_tokens     BIGINT NOT NULL DEFAULT 0,
    completion_tokens BIGINT NOT NULL DEFAULT 0,
    total_tokens      BIGINT NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'ok',
    notes             TEXT,
    requested_at      TEXT NOT NULL,
    -- Pipeline transparency fields (added for routing observability)
    latency_ms        INTEGER,
    complexity_level  TEXT,
    expert_domains    TEXT,
    cache_hit         BOOLEAN NOT NULL DEFAULT FALSE,
    agentic_rounds    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
    token      TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used       BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS user_expert_templates (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    cost_factor DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    config_json TEXT NOT NULL,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_cc_profiles (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    config_json TEXT NOT NULL,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_api_connections (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    url          TEXT NOT NULL,
    api_type     TEXT NOT NULL DEFAULT 'openai',
    api_key_enc  TEXT NOT NULL DEFAULT '',
    models_cache TEXT NOT NULL DEFAULT '[]',
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    UNIQUE(user_id, name)
);

CREATE INDEX IF NOT EXISTS idx_api_keys_hash             ON api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_api_keys_user             ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_permissions_user          ON permissions(user_id);
CREATE INDEX IF NOT EXISTS idx_usage_user_date           ON usage_log(user_id, requested_at);
CREATE INDEX IF NOT EXISTS idx_usage_request             ON usage_log(request_id);
CREATE INDEX IF NOT EXISTS idx_user_templates_user       ON user_expert_templates(user_id);
CREATE INDEX IF NOT EXISTS idx_user_cc_profiles_user     ON user_cc_profiles(user_id);
CREATE INDEX IF NOT EXISTS idx_user_api_connections_user ON user_api_connections(user_id);

CREATE TABLE IF NOT EXISTS admin_expert_templates (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    config_json TEXT NOT NULL,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS model_metadata (
    model_id         TEXT PRIMARY KEY,
    base_model       TEXT NOT NULL DEFAULT '',
    parameter_size_b DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    context_window   INTEGER NOT NULL DEFAULT 4096,
    family           TEXT NOT NULL DEFAULT '',
    strengths        TEXT[] NOT NULL DEFAULT '{}',
    benchmark_scores JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at       TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS dynamic_template_feedback_log (
    template_id TEXT PRIMARY KEY,
    prompt      TEXT NOT NULL,
    config_json TEXT NOT NULL,
    user_rating INTEGER,
    latency_ms  BIGINT,
    tokens_used INTEGER,
    status      TEXT NOT NULL DEFAULT 'success',
    created_at  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS federation_config (
    id                    TEXT PRIMARY KEY DEFAULT 'default',
    enabled               BOOLEAN NOT NULL DEFAULT FALSE,
    hub_url               TEXT NOT NULL DEFAULT '',
    hub_api_key           TEXT NOT NULL DEFAULT '',
    node_id               TEXT NOT NULL DEFAULT '',
    node_name             TEXT NOT NULL DEFAULT '',
    sync_interval_seconds INTEGER NOT NULL DEFAULT 3600,
    auto_push_enabled     BOOLEAN NOT NULL DEFAULT FALSE,
    last_push_at          TEXT,
    last_pull_at          TEXT,
    updated_at            TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS federation_domain_policy (
    id              TEXT PRIMARY KEY,
    domain          TEXT NOT NULL UNIQUE,
    mode            TEXT NOT NULL DEFAULT 'blocked',
    min_confidence  DOUBLE PRECISION NOT NULL DEFAULT 0.7,
    only_verified   BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS federation_outbox (
    id            TEXT PRIMARY KEY,
    bundle_json   TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    domain        TEXT NOT NULL,
    triple_count  INTEGER NOT NULL DEFAULT 0,
    entity_count  INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    sent_at       TEXT,
    error         TEXT
);

CREATE INDEX IF NOT EXISTS idx_fed_outbox_status ON federation_outbox(status);
CREATE INDEX IF NOT EXISTS idx_fed_outbox_domain ON federation_outbox(domain);

-- ── Teams & Tenants (knowledge hierarchy) ─────────────────────────────────────

CREATE TABLE IF NOT EXISTS tenants (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    slug        TEXT UNIQUE NOT NULL,
    created_by  TEXT REFERENCES users(id),
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS teams (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    slug        TEXT UNIQUE NOT NULL,
    tenant_id   TEXT REFERENCES tenants(id) ON DELETE SET NULL,
    created_by  TEXT REFERENCES users(id),
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_memberships (
    id        TEXT PRIMARY KEY,
    team_id   TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id   TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role      TEXT NOT NULL DEFAULT 'member',
    joined_at TEXT NOT NULL,
    UNIQUE(team_id, user_id)
);

CREATE TABLE IF NOT EXISTS tenant_memberships (
    id        TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    team_id   TEXT REFERENCES teams(id) ON DELETE CASCADE,
    user_id   TEXT REFERENCES users(id) ON DELETE CASCADE,
    role      TEXT NOT NULL DEFAULT 'member',
    joined_at TEXT NOT NULL,
    CHECK (team_id IS NOT NULL OR user_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS team_budgets (
    team_id       TEXT PRIMARY KEY REFERENCES teams(id) ON DELETE CASCADE,
    monthly_limit BIGINT,
    daily_limit   BIGINT,
    monthly_used  BIGINT NOT NULL DEFAULT 0,
    daily_used    BIGINT NOT NULL DEFAULT 0,
    budget_type   TEXT NOT NULL DEFAULT 'shared',
    updated_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_team_memberships_team ON team_memberships(team_id);
CREATE INDEX IF NOT EXISTS idx_team_memberships_user ON team_memberships(user_id);
CREATE INDEX IF NOT EXISTS idx_tenant_memberships_tenant ON tenant_memberships(tenant_id);

-- Memory preferences: idempotent column additions for existing deployments
ALTER TABLE users ADD COLUMN IF NOT EXISTS memory_prefer_fresh       BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS memory_share_with_team    BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS force_password_change     BOOLEAN NOT NULL DEFAULT FALSE;

-- Pipeline transparency log: idempotent additions for existing deployments
ALTER TABLE usage_log ADD COLUMN IF NOT EXISTS latency_ms        INTEGER;
ALTER TABLE usage_log ADD COLUMN IF NOT EXISTS complexity_level  TEXT;
ALTER TABLE usage_log ADD COLUMN IF NOT EXISTS expert_domains    TEXT;
ALTER TABLE usage_log ADD COLUMN IF NOT EXISTS cache_hit         BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE usage_log ADD COLUMN IF NOT EXISTS agentic_rounds    INTEGER NOT NULL DEFAULT 0;

-- Conversation audit log retention: per-user override (NULL = global default)
ALTER TABLE users ADD COLUMN IF NOT EXISTS conversation_log_retention_days INTEGER;

-- Dynamic routing flag on API keys
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS dynamic_routing    BOOLEAN NOT NULL DEFAULT FALSE;
-- Local-only compliance flag on API keys (restrict dynamic router to local endpoints)
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS local_only_routing BOOLEAN NOT NULL DEFAULT FALSE;
-- Per-key Ollama num_ctx override for native model calls (0 = use model default)
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS native_num_ctx INTEGER NOT NULL DEFAULT 0;
-- Soft-archive: key is hidden from UI but kept for audit trail (only valid when is_active=FALSE)
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE;

-- Connection-level rate limit config for tagged models (JSON: {max_requests, period_amount, period_unit, tagged})
ALTER TABLE user_api_connections ADD COLUMN IF NOT EXISTS rate_limit_config TEXT NOT NULL DEFAULT '{}';

-- Global auto-detected tags on model metadata (free, vision, agentic, reasoning, code)
ALTER TABLE model_metadata ADD COLUMN IF NOT EXISTS tags TEXT[] NOT NULL DEFAULT '{}';

-- Dynamic template ID resolved for moe-auto requests (references admin_expert_templates.id)
ALTER TABLE usage_log ADD COLUMN IF NOT EXISTS dynamic_tmpl_id TEXT;

-- Quality signals from TASK-10 to TASK-22 (Trust Score, Cynefin, Self-Critique)
ALTER TABLE usage_log ADD COLUMN IF NOT EXISTS trust_score              DOUBLE PRECISION;
ALTER TABLE usage_log ADD COLUMN IF NOT EXISTS trust_verdict            TEXT;
ALTER TABLE usage_log ADD COLUMN IF NOT EXISTS cynefin_domain           TEXT;
ALTER TABLE usage_log ADD COLUMN IF NOT EXISTS self_critique_round      INTEGER NOT NULL DEFAULT 0;
ALTER TABLE usage_log ADD COLUMN IF NOT EXISTS cascade_type             TEXT;
ALTER TABLE usage_log ADD COLUMN IF NOT EXISTS structured_failure_round INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS ai_io_audit_log (
    audit_id          TEXT PRIMARY KEY,
    session_id        TEXT,
    request_id        TEXT,
    model             TEXT,
    endpoint          TEXT,
    stage             TEXT,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    started_at        TIMESTAMPTZ,
    completed_at      TIMESTAMPTZ,
    status            TEXT,
    request_body      JSONB,
    response_body     JSONB
);
CREATE INDEX IF NOT EXISTS idx_ai_io_audit_request ON ai_io_audit_log(request_id);
CREATE INDEX IF NOT EXISTS idx_ai_io_audit_started ON ai_io_audit_log(started_at DESC);

-- Agent Tool Path "premature stop" detection patterns — admin-editable so a
-- newly observed announcement phrasing (a model writing "Let me now..." /
-- "Ich fix das jetzt..." and then stopping instead of calling a tool) can be
-- added in production without a code change or container rebuild. Seeded
-- once from the patterns that were previously hardcoded in
-- services/agent_enrichment.py — see scripts/seed_premature_stop_patterns.py.
CREATE TABLE IF NOT EXISTS admin_premature_stop_patterns (
    id           TEXT PRIMARY KEY,
    pattern      TEXT NOT NULL,
    pattern_type TEXT NOT NULL DEFAULT 'literal',  -- 'literal' | 'regex'
    language     TEXT NOT NULL DEFAULT '',          -- free tag e.g. 'de','en'; '' = any
    category     TEXT NOT NULL DEFAULT 'announcement',
    description  TEXT NOT NULL DEFAULT '',
    enabled      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TEXT NOT NULL DEFAULT '',
    updated_at   TEXT NOT NULL DEFAULT ''
);

-- Singleton config row (same shape as federation_config) for the LLM that
-- judges tool-passthrough text-only endings which none of the
-- admin_premature_stop_patterns rules matched. Admin-editable so the model
-- can be swapped (different Ollama instance, different size) without a code
-- change or container rebuild — see services/agent_enrichment.py::_classify_tool_ending.
CREATE TABLE IF NOT EXISTS admin_classifier_config (
    id         TEXT PRIMARY KEY DEFAULT 'default',
    model      TEXT NOT NULL DEFAULT 'gemma4:12b',
    base_url   TEXT NOT NULL DEFAULT 'http://192.168.155.224:11435/v1',
    token      TEXT NOT NULL DEFAULT 'ollama',
    enabled    BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TEXT NOT NULL DEFAULT ''
);

-- One row per tool-passthrough response that ended as plain text (no
-- tool_calls, finish_reason=stop) mid-session without triggering the
-- existing premature-stop retry — i.e. exactly the case that previously
-- required a human watching the logs to notice. classification is filled
-- in asynchronously by the configured classifier LLM (NULL = still
-- pending/classifier disabled/call failed); promoted_to_pattern tracks
-- whether an admin has already turned this into a permanent
-- admin_premature_stop_patterns rule via the review UI.
CREATE TABLE IF NOT EXISTS admin_unclassified_tool_endings (
    id                   TEXT PRIMARY KEY,
    chat_id              TEXT NOT NULL DEFAULT '',
    model                TEXT NOT NULL DEFAULT '',
    content              TEXT NOT NULL DEFAULT '',
    content_chars        INTEGER NOT NULL DEFAULT 0,
    tool_msgs            INTEGER NOT NULL DEFAULT 0,
    classification       TEXT,                       -- 'premature_stop' | 'legitimate' | NULL=pending
    classifier_reasoning TEXT NOT NULL DEFAULT '',
    promoted_to_pattern  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at           TEXT NOT NULL DEFAULT '',
    classified_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_unclassified_tool_endings_created
    ON admin_unclassified_tool_endings(created_at DESC);
"""


# ─── Pool lifecycle ───────────────────────────────────────────────────────────

_pool: Optional[AsyncConnectionPool] = None


async def _bootstrap_role_and_db() -> None:
    """Legacy-install rescue: create moe_admin role + moe_userdb database on an
    existing Postgres volume that pre-dates scripts/postgres-init/01-moe_admin.sh.

    /docker-entrypoint-initdb.d/* only runs on first init. Operators who
    initialised their volume before that script existed get stuck with a
    'database "moe_userdb" does not exist' error forever. We fix it from
    the application side: connect as the superuser (POSTGRES_USER, which
    owns the bootstrap 'langgraph' database), CREATE ROLE/DATABASE if
    missing, then let init_db() retry the normal pool open.
    """
    import urllib.parse as _url

    parsed = _url.urlparse(DATABASE_URL)
    if not parsed.hostname:
        raise RuntimeError("MOE_USERDB_URL has no hostname — cannot bootstrap")
    su_user = os.getenv("POSTGRES_USER", "langgraph")
    su_pass = os.getenv("POSTGRES_CHECKPOINT_PASSWORD", "")
    su_db   = os.getenv("POSTGRES_DB", su_user)
    if not su_pass:
        raise RuntimeError(
            "POSTGRES_CHECKPOINT_PASSWORD not set — cannot bootstrap moe_userdb"
        )

    target_user = parsed.username or "moe_admin"
    target_pass = _url.unquote(parsed.password or "")
    target_db   = (parsed.path or "/moe_userdb").lstrip("/") or "moe_userdb"

    # Validate identifiers before injecting into DDL — psycopg cannot parameterise
    # CREATE ROLE / CREATE DATABASE, so we enforce a strict safe-identifier pattern.
    import re as _re_db
    _safe_ident = _re_db.compile(r'^[a-zA-Z_][a-zA-Z0-9_]{0,62}$')
    if not _safe_ident.match(target_user):
        raise ValueError(f"Unsafe DB username '{target_user}' — must match [a-zA-Z_][a-zA-Z0-9_]{{0,62}}")
    if not _safe_ident.match(target_db):
        raise ValueError(f"Unsafe DB name '{target_db}' — must match [a-zA-Z_][a-zA-Z0-9_]{{0,62}}")

    dsn = (
        f"host={parsed.hostname} port={parsed.port or 5432} "
        f"user={su_user} password={su_pass} dbname={su_db}"
    )
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = %s",
                (target_user,),
            )
            if not await cur.fetchone():
                # Role name and password are not user-controlled here but we
                # still route the password through a parameterised string:
                # psycopg does not parameterise DDL, so use literal escaping
                # (double single-quotes) on the password only.
                await cur.execute(
                    f"CREATE ROLE {target_user} LOGIN PASSWORD "
                    f"'{target_pass.replace(chr(39), chr(39)*2)}'"
                )
            else:
                await cur.execute(
                    f"ALTER ROLE {target_user} WITH LOGIN PASSWORD "
                    f"'{target_pass.replace(chr(39), chr(39)*2)}'"
                )
            await cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (target_db,)
            )
            if not await cur.fetchone():
                await cur.execute(f"CREATE DATABASE {target_db} OWNER {target_user}")
            await cur.execute(
                f"GRANT ALL PRIVILEGES ON DATABASE {target_db} TO {target_user}"
            )
    logger.info(
        "🔧 bootstrapped role %s and database %s via %s superuser",
        target_user, target_db, su_user,
    )


async def init_db() -> None:
    """Open the connection pool and create the schema if it doesn't exist yet.

    On legacy Postgres volumes that were initialised before the
    /docker-entrypoint-initdb.d/ init script existed, the moe_admin role
    and moe_userdb database are missing. We detect that once and bootstrap
    them as the Postgres superuser so the user never has to wipe the
    volume manually."""
    global _pool
    if _pool is None:
        for attempt in range(2):
            try:
                _pool = AsyncConnectionPool(
                    DATABASE_URL,
                    min_size=1,
                    max_size=10,
                    open=False,
                    kwargs={"row_factory": dict_row, "autocommit": False},
                )
                await _pool.open()
                await _pool.wait()
                break
            except Exception as e:
                msg = str(e).lower()
                legacy_db = 'database "moe_userdb"' in msg and "does not exist" in msg
                legacy_role = 'role "moe_admin"' in msg and "does not exist" in msg
                bad_pw = 'password authentication failed for user "moe_admin"' in msg
                if attempt == 0 and (legacy_db or legacy_role or bad_pw):
                    logger.warning(
                        "moe_userdb connection failed (%s) — running one-time bootstrap",
                        e.__class__.__name__,
                    )
                    if _pool is not None:
                        try:
                            await _pool.close()
                        except Exception:
                            pass
                        _pool = None
                    await _bootstrap_role_and_db()
                    continue
                raise
        logger.info("moe_userdb pool opened: %s", DATABASE_URL.split("@")[-1])
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SCHEMA)


async def close_db() -> None:
    """Close the pool on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def _get_pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("moe_userdb pool not initialized — call init_db() first")
    return _pool


async def seed_initial_admin() -> None:
    """Legt den ersten Admin-User aus Umgebungsvariablen an, falls noch kein Admin existiert."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id FROM users WHERE role='admin' OR is_admin=TRUE LIMIT 1"
            )
            if await cur.fetchone():
                return
    admin_user  = os.getenv("ADMIN_USER",  "admin")
    admin_pass  = os.getenv("ADMIN_PASSWORD", "")
    admin_email = os.getenv("ADMIN_EMAIL", f"{admin_user}@localhost")
    await create_user(admin_user, admin_email, admin_pass,
                      display_name="Administrator", is_admin=True, role="admin")


# ─── Helpers ─────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    """Returns (raw_key, key_hash, key_prefix)."""
    raw = "moe-sk-" + secrets.token_hex(24)
    return raw, hash_api_key(raw), raw[:16]


async def hash_password(password: str) -> str:
    pw = password.encode("utf-8")
    hashed = await asyncio.to_thread(_bcrypt.hashpw, pw, _bcrypt.gensalt(rounds=12))
    return hashed.decode()


async def verify_password(password: str, hashed: str) -> bool:
    hashed_bytes = hashed.encode()
    if await asyncio.to_thread(_bcrypt.checkpw, password.encode("utf-8"), hashed_bytes):
        return True
    legacy_pw = hashlib.sha256(password.encode("utf-8")).digest()
    return await asyncio.to_thread(_bcrypt.checkpw, legacy_pw, hashed_bytes)


async def is_legacy_hash(password: str, hashed: str) -> bool:
    """True if the hash was created with the old SHA-256 scheme — re-hash needed."""
    hashed_bytes = hashed.encode()
    direct_ok = await asyncio.to_thread(_bcrypt.checkpw, password.encode("utf-8"), hashed_bytes)
    if direct_ok:
        return False
    legacy_pw = hashlib.sha256(password.encode("utf-8")).digest()
    return await asyncio.to_thread(_bcrypt.checkpw, legacy_pw, hashed_bytes)


async def update_user_language(user_id: str, lang: str) -> None:
    """Saves the language preference for a user."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET language=%s, updated_at=%s WHERE id=%s",
                (lang, now_iso(), user_id),
            )


# ─── User CRUD ───────────────────────────────────────────────────────────────

async def create_user(
    username: str, email: str, password: str,
    display_name: str = "", is_admin: bool = False, role: str = "user",
    first_name: str = "", last_name: str = "",
    street_address: str = "", postal_code: str = "", city: str = "",
    country: str = "", date_of_birth: str = "", gender: str = "",
) -> dict:
    """Creates a user, returns user dict. Raises ValueError on duplicate."""
    if role == "admin":
        is_admin = True
    hashed = await hash_password(password)
    uid = new_id()
    now = now_iso()
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            try:
                await cur.execute(
                    "INSERT INTO users "
                    "(id,username,email,display_name,hashed_password,is_active,is_admin,role,"
                    "first_name,last_name,street_address,postal_code,city,country,date_of_birth,gender,"
                    "force_password_change,created_at,updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,TRUE,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,%s,%s)",
                    (uid, username, email, display_name, hashed, bool(is_admin), role,
                     first_name, last_name, street_address, postal_code, city,
                     country or "", date_of_birth or None, gender or None,
                     now, now),
                )
                await cur.execute(
                    "INSERT INTO token_budgets (user_id,daily_limit,monthly_limit,total_limit,budget_type,updated_at) "
                    "VALUES (%s,NULL,NULL,NULL,'subscription',%s)",
                    (uid, now),
                )
            except psycopg.errors.UniqueViolation as e:
                raise ValueError(f"Username or email already taken: {e}") from e
    return await get_user(uid)


async def get_user(user_id: str) -> Optional[dict]:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT u.*, b.daily_limit, b.monthly_limit, b.total_limit "
                "FROM users u LEFT JOIN token_budgets b ON u.id=b.user_id WHERE u.id=%s",
                (user_id,),
            )
            return await cur.fetchone()


async def get_user_by_username(username: str) -> Optional[dict]:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT u.*, b.daily_limit, b.monthly_limit, b.total_limit "
                "FROM users u LEFT JOIN token_budgets b ON u.id=b.user_id "
                "WHERE u.username=%s OR u.email=%s",
                (username, username),
            )
            return await cur.fetchone()


async def list_users() -> list[dict]:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT u.id, u.username, u.email, u.display_name, u.first_name, u.last_name, "
                "u.is_active, u.is_admin, u.role, u.created_at, u.updated_at, "
                "b.daily_limit, b.monthly_limit, b.total_limit "
                "FROM users u LEFT JOIN token_budgets b ON u.id=b.user_id ORDER BY u.created_at DESC"
            )
            return await cur.fetchall()


async def get_user_memory_prefs(user_id: str) -> dict:
    """Return memory preference flags for a user. Defaults to False for both."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT memory_prefer_fresh, memory_share_with_team FROM users WHERE id=%s",
                (user_id,),
            )
            row = await cur.fetchone()
    if not row:
        return {"prefer_fresh": False, "share_with_team": False}
    return {
        "prefer_fresh":      bool(row.get("memory_prefer_fresh",    False)),
        "share_with_team":   bool(row.get("memory_share_with_team", False)),
    }


async def set_user_memory_prefs(
    user_id: str,
    prefer_fresh: bool,
    share_with_team: bool,
) -> None:
    """Persist memory preference flags for a user."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET memory_prefer_fresh=%s, memory_share_with_team=%s, "
                "updated_at=%s WHERE id=%s",
                (prefer_fresh, share_with_team, now_iso(), user_id),
            )


async def update_user(user_id: str, **kwargs) -> Optional[dict]:
    allowed = {
        "email", "display_name", "is_active", "is_admin", "role",
        "alert_enabled", "alert_threshold_pct", "alert_email", "last_alert_sent_at",
        "first_name", "last_name", "street_address", "postal_code", "city",
        "country", "date_of_birth", "gender",
        "memory_prefer_fresh", "memory_share_with_team",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return await get_user(user_id)
    # Normalize booleans for Postgres
    for bool_key in ("is_active", "is_admin", "alert_enabled"):
        if bool_key in updates:
            updates[bool_key] = bool(updates[bool_key])
    updates["updated_at"] = now_iso()
    sets = ", ".join(f"{k}=%s" for k in updates)
    vals = list(updates.values()) + [user_id]
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(f"UPDATE users SET {sets} WHERE id=%s", vals)
    return await get_user(user_id)


async def update_password(user_id: str, new_password: str) -> None:
    hashed = await hash_password(new_password)
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET hashed_password=%s, force_password_change=FALSE, updated_at=%s WHERE id=%s",
                (hashed, now_iso(), user_id),
            )


async def update_user_timezone(user_id: str, offset_hours: float) -> None:
    """Setzt den Zeitzonen-Offset eines Users (−12.0 bis +14.0 Stunden)."""
    offset_hours = max(-12.0, min(14.0, float(offset_hours)))
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET timezone_offset_hours=%s, updated_at=%s WHERE id=%s",
                (offset_hours, now_iso(), user_id),
            )


async def delete_user(user_id: str) -> None:
    """Soft-delete: setzt is_active=FALSE."""
    await update_user(user_id, is_active=False)


async def get_user_log_retention(user_id: str) -> int:
    """Return the effective conversation log retention in days for a user.

    Falls back to CONVERSATION_LOG_RETENTION_DAYS_DEFAULT when the user has no
    personal setting.  The return value is always clamped to
    [0, CONVERSATION_LOG_RETENTION_MAX].
    """
    _default = int(os.getenv("CONVERSATION_LOG_RETENTION_DAYS_DEFAULT", "90"))
    _max     = int(os.getenv("CONVERSATION_LOG_RETENTION_MAX", "365"))
    try:
        async with _get_pool().connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT conversation_log_retention_days FROM users WHERE id=%s",
                    (user_id,),
                )
                row = await cur.fetchone()
        if row and row[0] is not None:
            days = int(row[0])
        else:
            days = _default
    except Exception:
        days = _default
    return max(0, min(days, _max))


async def set_user_log_retention(user_id: str, days: int) -> None:
    """Persist a per-user conversation log retention value (capped at the global max)."""
    _max = int(os.getenv("CONVERSATION_LOG_RETENTION_MAX", "365"))
    capped = max(0, min(int(days), _max))
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET conversation_log_retention_days=%s, updated_at=%s WHERE id=%s",
                (capped, __import__("datetime").datetime.utcnow().isoformat(), user_id),
            )


async def get_all_user_retentions() -> list[dict]:
    """Return [(user_id, effective_retention_days)] for the cleanup job."""
    _default = int(os.getenv("CONVERSATION_LOG_RETENTION_DAYS_DEFAULT", "90"))
    _max     = int(os.getenv("CONVERSATION_LOG_RETENTION_MAX", "365"))
    try:
        async with _get_pool().connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT id, conversation_log_retention_days FROM users WHERE is_active=TRUE")
                rows = await cur.fetchall()
        return [
            {
                "user_id": r[0],
                "retention_days": max(0, min(
                    r[1] if r[1] is not None else _default,
                    _max,
                )),
            }
            for r in rows
        ]
    except Exception:
        return []


async def get_user_by_email(email: str) -> Optional[dict]:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT u.*, b.daily_limit, b.monthly_limit, b.total_limit "
                "FROM users u LEFT JOIN token_budgets b ON u.id=b.user_id "
                "WHERE u.email=%s AND u.is_active=TRUE",
                (email,),
            )
            return await cur.fetchone()


# ─── Password Reset Tokens ────────────────────────────────────────────────────

async def create_reset_token(user_id: str) -> str:
    """Creates a one-time reset token (TTL 1h) and returns it."""
    token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO password_reset_tokens (token, user_id, expires_at, used) VALUES (%s,%s,%s,FALSE)",
                (token, user_id, expires_at),
            )
    return token


async def get_reset_token(token: str) -> Optional[dict]:
    """Returns the token dict if valid (unused, not expired), otherwise None."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM password_reset_tokens WHERE token=%s AND used=FALSE",
                (token,),
            )
            row = await cur.fetchone()
    if not row:
        return None
    try:
        expires_at = datetime.fromisoformat(row["expires_at"])
        if datetime.now(timezone.utc) > expires_at:
            return None
    except (ValueError, KeyError):
        return None
    return row


async def consume_reset_token(token: str) -> None:
    """Markiert Token als verbraucht."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE password_reset_tokens SET used=TRUE WHERE token=%s",
                (token,),
            )


# ─── Budget CRUD ─────────────────────────────────────────────────────────────

async def set_budget(user_id: str, daily: Optional[int], monthly: Optional[int],
                     total: Optional[int], budget_type: str = "subscription") -> None:
    now = now_iso()
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO token_budgets (user_id,daily_limit,monthly_limit,total_limit,budget_type,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT(user_id) DO UPDATE SET "
                "daily_limit=excluded.daily_limit, monthly_limit=excluded.monthly_limit, "
                "total_limit=excluded.total_limit, budget_type=excluded.budget_type, "
                "updated_at=excluded.updated_at",
                (user_id, daily, monthly, total, budget_type, now),
            )


async def get_budget(user_id: str) -> dict:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM token_budgets WHERE user_id=%s",
                (user_id,),
            )
            row = await cur.fetchone()
    return row or {
        "user_id": user_id, "daily_limit": None, "monthly_limit": None,
        "total_limit": None, "budget_type": "subscription",
    }


# ─── Permissions CRUD ────────────────────────────────────────────────────────

async def grant_permission(user_id: str, resource_type: str, resource_id: str) -> dict:
    pid = new_id()
    now = now_iso()
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            try:
                await cur.execute(
                    "INSERT INTO permissions (id,user_id,resource_type,resource_id,granted_at) "
                    "VALUES (%s,%s,%s,%s,%s)",
                    (pid, user_id, resource_type, resource_id, now),
                )
            except psycopg.errors.UniqueViolation:
                pass  # already granted
    return {"id": pid, "user_id": user_id, "resource_type": resource_type,
            "resource_id": resource_id, "granted_at": now}


async def revoke_permission(perm_id: str) -> None:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM permissions WHERE id=%s", (perm_id,))


async def revoke_permission_by_resource(user_id: str, resource_type: str, resource_id: str) -> None:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM permissions WHERE user_id=%s AND resource_type=%s AND resource_id=%s",
                (user_id, resource_type, resource_id),
            )


async def get_permissions(user_id: str) -> list[dict]:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM permissions WHERE user_id=%s ORDER BY resource_type, resource_id",
                (user_id,),
            )
            return await cur.fetchall()


async def get_all_granted_model_endpoints() -> list[str]:
    """Returns all system-wide granted model_endpoint resource IDs (deduplicated)."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT DISTINCT resource_id FROM permissions WHERE resource_type='model_endpoint'"
            )
            rows = await cur.fetchall()
    return [r["resource_id"] for r in rows]


async def get_permissions_map(user_id: str) -> dict:
    """Returns compact {resource_type: [resource_id, ...]} for Valkey cache."""
    perms = await get_permissions(user_id)
    result: dict = {}
    for p in perms:
        rt = p["resource_type"]
        result.setdefault(rt, [])
        result[rt].append(p["resource_id"])
    return result


# ─── API Key CRUD ─────────────────────────────────────────────────────────────

async def create_api_key(user_id: str, label: str = "") -> tuple[str, dict]:
    """Creates a new API key. Returns (raw_key, key_dict).
    raw_key is only returned once — never visible again."""
    raw, key_hash, key_prefix = generate_api_key()
    kid = new_id()
    now = now_iso()
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO api_keys (id,user_id,key_hash,key_prefix,label,is_active,created_at) "
                "VALUES (%s,%s,%s,%s,%s,TRUE,%s)",
                (kid, user_id, key_hash, key_prefix, label, now),
            )
    key_dict = {"id": kid, "user_id": user_id, "key_prefix": key_prefix,
                "label": label, "is_active": True, "created_at": now,
                "last_used_at": None, "expires_at": None, "cc_profile_id": None,
                "dynamic_routing": False, "local_only_routing": False, "native_num_ctx": 0}
    return raw, key_dict


async def list_api_keys(user_id: str) -> list[dict]:
    """Returns all non-archived keys for a user (active and deactivated)."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id,user_id,key_prefix,label,is_active,is_archived,created_at,last_used_at,expires_at,"
                "cc_profile_id,dynamic_routing,local_only_routing,native_num_ctx "
                "FROM api_keys WHERE user_id=%s AND is_archived=FALSE ORDER BY created_at DESC",
                (user_id,),
            )
            return await cur.fetchall()


async def revoke_api_key(key_id: str) -> Optional[str]:
    """Locks a key; returns key_hash for Valkey DEL."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT key_hash FROM api_keys WHERE id=%s", (key_id,))
            row = await cur.fetchone()
            if not row:
                return None
            key_hash = row["key_hash"]
            await cur.execute("UPDATE api_keys SET is_active=FALSE WHERE id=%s", (key_id,))
    return key_hash


async def reactivate_api_key(key_id: str, user_id: str) -> Optional[str]:
    """Re-enables a previously locked key; returns key_hash for Valkey re-sync."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT key_hash FROM api_keys WHERE id=%s AND user_id=%s AND is_archived=FALSE",
                (key_id, user_id),
            )
            row = await cur.fetchone()
            if not row:
                return None
            key_hash = row["key_hash"]
            await cur.execute(
                "UPDATE api_keys SET is_active=TRUE WHERE id=%s AND user_id=%s",
                (key_id, user_id),
            )
    return key_hash


async def archive_api_key(key_id: str, user_id: str) -> bool:
    """Soft-deletes a deactivated key; hidden from UI, kept for audit trail."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE api_keys SET is_archived=TRUE "
                "WHERE id=%s AND user_id=%s AND is_active=FALSE",
                (key_id, user_id),
            )
            return cur.rowcount > 0


async def get_active_key_hashes(user_id: str) -> list[dict]:
    """All active API keys for a user (id + key_hash + cc_profile_id) for Valkey sync."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, key_hash, cc_profile_id, dynamic_routing, local_only_routing, native_num_ctx "
                "FROM api_keys WHERE user_id=%s AND is_active=TRUE",
                (user_id,),
            )
            return await cur.fetchall()


async def get_user_id_by_key_hash(key_hash: str) -> str | None:
    """Return user_id for an active API key by its SHA256 hash, or None."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT user_id FROM api_keys WHERE key_hash=%s AND is_active=TRUE",
                (key_hash,),
            )
            row = await cur.fetchone()
    return row["user_id"] if row else None


async def update_api_key_label(key_id: str, label: str, user_id: Optional[str] = None) -> bool:
    """Updates the label of an API key. user_id restricts to own keys."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            if user_id:
                await cur.execute(
                    "UPDATE api_keys SET label=%s WHERE id=%s AND user_id=%s",
                    (label.strip(), key_id, user_id),
                )
            else:
                await cur.execute(
                    "UPDATE api_keys SET label=%s WHERE id=%s",
                    (label.strip(), key_id),
                )
            return cur.rowcount > 0


async def set_user_default_cc_profile(user_id: str, profile_id: Optional[str]) -> bool:
    """Sets or clears the default CC profile for a user."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE users SET default_cc_profile_id=%s, updated_at=%s WHERE id=%s",
                (profile_id, now_iso(), user_id),
            )
            ok = cur.rowcount > 0
    if ok:
        await sync_user_to_redis(user_id)
    return ok


async def set_api_key_cc_profile(key_id: str, user_id: str, profile_id: Optional[str]) -> bool:
    """Weist einem API-Key ein spezifisches CC-Profil zu (oder entfernt die Zuweisung)."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE api_keys SET cc_profile_id=%s WHERE id=%s AND user_id=%s",
                (profile_id, key_id, user_id),
            )
            ok = cur.rowcount > 0
    if ok:
        await sync_user_to_redis(user_id)
    return ok


async def set_api_key_dynamic_routing(key_id: str, user_id: str, enabled: bool) -> bool:
    """Weist einem API-Key den Status für dynamisches Routing zu."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE api_keys SET dynamic_routing=%s WHERE id=%s AND user_id=%s",
                (enabled, key_id, user_id),
            )
            ok = cur.rowcount > 0
    if ok:
        await sync_user_to_redis(user_id)
    return ok


async def set_api_key_local_only_routing(key_id: str, user_id: str, enabled: bool) -> bool:
    """Aktiviert/deaktiviert die Beschränkung auf lokale Endpoints für einen API-Key."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE api_keys SET local_only_routing=%s WHERE id=%s AND user_id=%s",
                (enabled, key_id, user_id),
            )
            ok = cur.rowcount > 0
    if ok:
        await sync_user_to_redis(user_id)
    return ok


async def set_api_key_native_num_ctx(key_id: str, user_id: str, num_ctx: int) -> bool:
    """Sets the Ollama num_ctx override for native model calls on an API key (0 = model default)."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE api_keys SET native_num_ctx=%s WHERE id=%s AND user_id=%s",
                (max(0, int(num_ctx)), key_id, user_id),
            )
            ok = cur.rowcount > 0
    if ok:
        await sync_user_to_redis(user_id)
    return ok


async def update_usage_note(usage_id: str, user_id: str, note: str) -> bool:
    """Sets or clears the note for a usage entry (only if it belongs to the user)."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE usage_log SET notes=%s WHERE id=%s AND user_id=%s",
                (note.strip() or None, usage_id, user_id),
            )
            return cur.rowcount > 0


async def get_usage(user_id: str, days: int = 30, limit: int = 200) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT MIN(ul.id) AS id, "
                "COALESCE(ul.session_id, ul.request_id) AS session_id, "
                "ul.request_id, ul.user_id, ul.api_key_id, "
                "ul.model, ul.moe_mode, "
                "SUM(ul.prompt_tokens) AS prompt_tokens, "
                "SUM(ul.completion_tokens) AS completion_tokens, "
                "SUM(ul.total_tokens) AS total_tokens, "
                "CASE WHEN COUNT(CASE WHEN ul.status != 'ok' THEN 1 END) > 0 THEN 'error' ELSE 'ok' END AS status, "
                "MIN(ul.requested_at) AS requested_at, "
                "MAX(ul.requested_at) AS last_requested_at, "
                "COUNT(*) AS turn_count, "
                "(SELECT u2.notes FROM usage_log u2 WHERE u2.request_id = ul.request_id "
                " ORDER BY u2.requested_at ASC LIMIT 1) AS notes, "
                "MAX(ak.label) AS key_label, MAX(ak.key_prefix) AS key_prefix, "
                "MAX(ul.request_id) AS _req_id "
                "FROM usage_log ul "
                "LEFT JOIN api_keys ak ON ul.api_key_id = ak.id "
                "WHERE ul.user_id=%s AND ul.requested_at >= %s "
                "GROUP BY COALESCE(ul.session_id, ul.request_id), ul.request_id, ul.user_id, "
                "ul.api_key_id, ul.model, ul.moe_mode "
                "ORDER BY MIN(ul.requested_at) DESC LIMIT %s",
                (user_id, cutoff, limit),
            )
            return await cur.fetchall()


async def get_usage_summary(user_id: str) -> dict:
    """Aggregated statistics for user dashboard (incl. input/output breakdown).

    Uses ISO-timestamp string comparisons. `requested_at` is stored as
    `datetime.isoformat()` so LEFT(x,10) gives YYYY-MM-DD and LEFT(x,7) gives YYYY-MM.
    """
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    month_str = now.strftime("%Y-%m")
    cutoff_30 = (now - timedelta(days=30)).isoformat()
    cutoff_14 = (now - timedelta(days=14)).isoformat()
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            # Heute
            # Note: SUM() over numeric columns returns PG numeric → Python Decimal,
            # which Jinja's |tojson filter cannot serialize. Cast to bigint so
            # psycopg returns plain ints.
            await cur.execute(
                "SELECT COALESCE(SUM(total_tokens),0)::bigint AS tokens, "
                "COALESCE(SUM(prompt_tokens),0)::bigint AS prompt_tokens, "
                "COALESCE(SUM(completion_tokens),0)::bigint AS completion_tokens, "
                "COUNT(*) AS requests "
                "FROM usage_log WHERE user_id=%s AND LEFT(requested_at, 10) = %s",
                (user_id, today_str),
            )
            today = await cur.fetchone()
            # Dieser Monat
            await cur.execute(
                "SELECT COALESCE(SUM(total_tokens),0)::bigint AS tokens, "
                "COALESCE(SUM(prompt_tokens),0)::bigint AS prompt_tokens, "
                "COALESCE(SUM(completion_tokens),0)::bigint AS completion_tokens, "
                "COUNT(*) AS requests "
                "FROM usage_log WHERE user_id=%s AND LEFT(requested_at, 7) = %s",
                (user_id, month_str),
            )
            month = await cur.fetchone()
            # Gesamt
            await cur.execute(
                "SELECT COALESCE(SUM(total_tokens),0)::bigint AS tokens, "
                "COALESCE(SUM(prompt_tokens),0)::bigint AS prompt_tokens, "
                "COALESCE(SUM(completion_tokens),0)::bigint AS completion_tokens, "
                "COUNT(*) AS requests "
                "FROM usage_log WHERE user_id=%s",
                (user_id,),
            )
            total = await cur.fetchone()
            # Broken down by model (30 days)
            await cur.execute(
                "SELECT model, moe_mode, "
                "SUM(total_tokens)::bigint AS tokens, "
                "SUM(prompt_tokens)::bigint AS prompt_tokens, "
                "SUM(completion_tokens)::bigint AS completion_tokens, "
                "COUNT(*) AS requests "
                "FROM usage_log WHERE user_id=%s AND requested_at >= %s "
                "GROUP BY model, moe_mode ORDER BY tokens DESC LIMIT 20",
                (user_id, cutoff_30),
            )
            by_model = await cur.fetchall()
            # Tagesweise (letzte 14 Tage)
            await cur.execute(
                "SELECT LEFT(requested_at, 10) AS day, "
                "SUM(total_tokens)::bigint AS tokens, "
                "SUM(prompt_tokens)::bigint AS prompt_tokens, "
                "SUM(completion_tokens)::bigint AS completion_tokens, "
                "COUNT(*) AS requests "
                "FROM usage_log WHERE user_id=%s AND requested_at >= %s "
                "GROUP BY LEFT(requested_at, 10) ORDER BY day",
                (user_id, cutoff_14),
            )
            daily = await cur.fetchall()
    return {
        "today": today, "month": month, "total": total,
        "by_model": by_model, "daily": daily,
    }


# ─── User Expert Templates ───────────────────────────────────────────────────

async def create_user_template(user_id: str, name: str, description: str,
                                cost_factor: float, config: dict) -> dict:
    """Creates a user-owned expert template. ID prefix: 'user:'."""
    tmpl_id = "user:" + new_id()
    now = now_iso()
    config_str = json.dumps(config, ensure_ascii=False)
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO user_expert_templates "
                "(id,user_id,name,description,cost_factor,config_json,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (tmpl_id, user_id, name, description, cost_factor, config_str, now, now),
            )
    return await get_user_template(tmpl_id, user_id)


async def get_user_template(tmpl_id: str, user_id: str) -> Optional[dict]:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM user_expert_templates WHERE id=%s AND user_id=%s",
                (tmpl_id, user_id),
            )
            return await cur.fetchone()


async def list_user_templates(user_id: str) -> list:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM user_expert_templates WHERE user_id=%s ORDER BY created_at DESC",
                (user_id,),
            )
            return await cur.fetchall()


async def update_user_template(tmpl_id: str, user_id: str, name: str, description: str,
                                cost_factor: float, config: dict) -> Optional[dict]:
    now = now_iso()
    config_str = json.dumps(config, ensure_ascii=False)
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE user_expert_templates "
                "SET name=%s,description=%s,cost_factor=%s,config_json=%s,updated_at=%s "
                "WHERE id=%s AND user_id=%s",
                (name, description, cost_factor, config_str, now, tmpl_id, user_id),
            )
    return await get_user_template(tmpl_id, user_id)


async def delete_user_template(tmpl_id: str, user_id: str) -> bool:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM user_expert_templates WHERE id=%s AND user_id=%s",
                (tmpl_id, user_id),
            )
            return cur.rowcount > 0


# ─── User CC Profiles ─────────────────────────────────────────────────────────

async def create_user_cc_profile(user_id: str, name: str, config: dict) -> dict:
    """Creates a user-owned CC profile. ID prefix: 'ucp-'."""
    profile_id = "ucp-" + new_id()
    now = now_iso()
    config_str = json.dumps(config, ensure_ascii=False)
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO user_cc_profiles (id,user_id,name,config_json,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (profile_id, user_id, name, config_str, now, now),
            )
    return await get_user_cc_profile(profile_id, user_id)


async def get_user_cc_profile(profile_id: str, user_id: str) -> Optional[dict]:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM user_cc_profiles WHERE id=%s AND user_id=%s",
                (profile_id, user_id),
            )
            return await cur.fetchone()


async def list_user_cc_profiles(user_id: str) -> list:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM user_cc_profiles WHERE user_id=%s ORDER BY created_at DESC",
                (user_id,),
            )
            return await cur.fetchall()


async def update_user_cc_profile(profile_id: str, user_id: str, name: str, config: dict) -> Optional[dict]:
    now = now_iso()
    config_str = json.dumps(config, ensure_ascii=False)
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE user_cc_profiles SET name=%s,config_json=%s,updated_at=%s "
                "WHERE id=%s AND user_id=%s",
                (name, config_str, now, profile_id, user_id),
            )
    return await get_user_cc_profile(profile_id, user_id)


async def delete_user_cc_profile(profile_id: str, user_id: str) -> bool:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM user_cc_profiles WHERE id=%s AND user_id=%s",
                (profile_id, user_id),
            )
            return cur.rowcount > 0


# ─── Admin Functions (cross-user) ────────────────────────────────────────────

async def list_all_user_templates() -> list:
    """Admin: all user_expert_templates for all users, JOIN with users for username."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT uet.*, u.username, u.display_name FROM user_expert_templates uet "
                "JOIN users u ON uet.user_id = u.id ORDER BY uet.created_at DESC"
            )
            return await cur.fetchall()


async def list_all_user_cc_profiles() -> list:
    """Admin: all user_cc_profiles for all users, JOIN with users for username."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT ucp.*, u.username, u.display_name FROM user_cc_profiles ucp "
                "JOIN users u ON ucp.user_id = u.id ORDER BY ucp.created_at DESC"
            )
            return await cur.fetchall()


async def admin_update_user_template(tmpl_id: str, patch: dict) -> Optional[dict]:
    """Admin: aktualisiert name/description/cost_factor/config_json eines User-Templates
    ohne user_id-Filter.  Nur übergebene Felder werden überschrieben.
    Gibt das aktualisierte Row-Dict zurück, oder None wenn nicht gefunden."""
    allowed = {"name", "description", "cost_factor", "config_json"}
    updates = {k: v for k, v in patch.items() if k in allowed}
    if not updates:
        async with _get_pool().connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT * FROM user_expert_templates WHERE id=%s", (tmpl_id,))
                return await cur.fetchone()
    if "config_json" in updates and isinstance(updates["config_json"], dict):
        updates["config_json"] = json.dumps(updates["config_json"], ensure_ascii=False)
    updates["updated_at"] = now_iso()
    set_clause = ", ".join(f"{k}=%s" for k in updates)
    values = list(updates.values()) + [tmpl_id]
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"UPDATE user_expert_templates SET {set_clause} WHERE id=%s",
                values,
            )
            if cur.rowcount == 0:
                return None
            await cur.execute("SELECT * FROM user_expert_templates WHERE id=%s", (tmpl_id,))
            return await cur.fetchone()


async def set_user_template_active(tmpl_id: str, is_active: bool) -> Optional[dict]:
    """Admin: aktiviert/deaktiviert ein User-Template (ohne user_id-Filter)."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE user_expert_templates SET is_active=%s WHERE id=%s",
                (bool(is_active), tmpl_id),
            )
            await cur.execute("SELECT * FROM user_expert_templates WHERE id=%s", (tmpl_id,))
            return await cur.fetchone()


async def set_user_cc_profile_active(profile_id: str, is_active: bool) -> Optional[dict]:
    """Admin: aktiviert/deaktiviert ein User-CC-Profil (ohne user_id-Filter)."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE user_cc_profiles SET is_active=%s WHERE id=%s",
                (bool(is_active), profile_id),
            )
            await cur.execute("SELECT * FROM user_cc_profiles WHERE id=%s", (profile_id,))
            return await cur.fetchone()


async def admin_delete_user_template(tmpl_id: str) -> Optional[str]:
    """Admin: deletes a user template without user_id check. Returns user_id."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT user_id FROM user_expert_templates WHERE id=%s", (tmpl_id,))
            row = await cur.fetchone()
            if not row:
                return None
            user_id = row["user_id"]
            await cur.execute("DELETE FROM user_expert_templates WHERE id=%s", (tmpl_id,))
            return user_id if cur.rowcount > 0 else None


async def admin_delete_user_cc_profile(profile_id: str) -> Optional[str]:
    """Admin: deletes a user CC profile without user_id check. Returns user_id."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT user_id FROM user_cc_profiles WHERE id=%s", (profile_id,))
            row = await cur.fetchone()
            if not row:
                return None
            user_id = row["user_id"]
            await cur.execute("DELETE FROM user_cc_profiles WHERE id=%s", (profile_id,))
            return user_id if cur.rowcount > 0 else None


# ─── User API Connections ─────────────────────────────────────────────────────


async def create_user_connection(
    user_id: str,
    name: str,
    display_name: str,
    url: str,
    api_type: str,
    api_key_plain: str,
) -> dict:
    """Create a private API connection for an expert user. API key is encrypted at rest."""
    conn_id = "uconn-" + uuid.uuid4().hex[:12]
    now = datetime.now(timezone.utc).isoformat()
    api_key_enc = encrypt_api_key(api_key_plain)
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            try:
                await cur.execute(
                    "INSERT INTO user_api_connections "
                    "(id, user_id, name, display_name, url, api_type, api_key_enc, "
                    "models_cache, is_active, created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,'[]',TRUE,%s,%s)",
                    (conn_id, user_id, name, display_name, url,
                     api_type, api_key_enc, now, now),
                )
            except psycopg.errors.UniqueViolation:
                raise ValueError(f"Connection name '{name}' already exists for this user")
    return await get_user_connection(conn_id, user_id)


async def get_user_connection(conn_id: str, user_id: str) -> Optional[dict]:
    """Fetch a single connection by ID, scoped to the owner. Returns None if not found."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM user_api_connections WHERE id=%s AND user_id=%s",
                (conn_id, user_id),
            )
            return await cur.fetchone()


async def list_user_connections(user_id: str) -> list:
    """Return all connections for a user. api_key_enc is NOT decrypted here."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM user_api_connections WHERE user_id=%s ORDER BY created_at DESC",
                (user_id,),
            )
            return await cur.fetchall()


async def update_user_connection(
    conn_id: str,
    user_id: str,
    display_name: str,
    url: str,
    api_type: str,
    api_key_plain: Optional[str] = None,
) -> Optional[dict]:
    """Update a connection. Pass api_key_plain=None to keep the existing key unchanged."""
    now = datetime.now(timezone.utc).isoformat()
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            if api_key_plain is not None:
                await cur.execute(
                    "UPDATE user_api_connections "
                    "SET display_name=%s, url=%s, api_type=%s, api_key_enc=%s, updated_at=%s "
                    "WHERE id=%s AND user_id=%s",
                    (display_name, url, api_type, encrypt_api_key(api_key_plain), now,
                     conn_id, user_id),
                )
            else:
                await cur.execute(
                    "UPDATE user_api_connections "
                    "SET display_name=%s, url=%s, api_type=%s, updated_at=%s "
                    "WHERE id=%s AND user_id=%s",
                    (display_name, url, api_type, now, conn_id, user_id),
                )
    return await get_user_connection(conn_id, user_id)


async def delete_user_connection(conn_id: str, user_id: str) -> bool:
    """Delete a connection. Returns True if a row was deleted."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM user_api_connections WHERE id=%s AND user_id=%s",
                (conn_id, user_id),
            )
            return cur.rowcount > 0


async def revoke_model_endpoints_by_node(user_id: str, node_name: str) -> int:
    """Revoke all model_endpoint permissions for a user that reference a specific node name.

    Matches entries like 'model@node_name' and '*@node_name'. Returns the number of revoked rows.
    Uses fuzzy matching (normalized: lowercase, no hyphens/underscores) to handle renamed/legacy nodes.
    """
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, resource_id FROM permissions "
                "WHERE user_id=%s AND resource_type='model_endpoint'",
                (user_id,),
            )
            rows = await cur.fetchall()
    # Normalize both names: lowercase, strip hyphens/underscores for fuzzy matching
    # Normalize to handle renamed nodes (e.g. "my-node" matches "MY_NODE")
    target = node_name.lower().replace("-", "").replace("_", "")
    ids_to_delete = []
    for r in rows:
        rid = r["resource_id"]
        if not rid or "@" not in rid:
            continue
        # Extract the suffix after the last @
        suffix = rid.rsplit("@", 1)[1]
        if suffix.lower().replace("-", "").replace("_", "") == target:
            ids_to_delete.append(r["id"])
    if not ids_to_delete:
        return 0
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM permissions WHERE id = ANY(%s)",
                (ids_to_delete,),
            )
            return cur.rowcount


async def revoke_all_model_endpoints_by_node(node_name: str) -> int:
    """Revoke model_endpoint permissions for ALL users that reference a specific node name.

    Called after a server is removed or renamed so stale entries don't accumulate
    in the Permissions UI. Returns the total number of deleted rows.
    """
    users = await list_users()
    total = 0
    for user in users:
        total += await revoke_model_endpoints_by_node(user["id"], node_name)
    return total


async def scan_stale_model_endpoint_permissions(active_server_names: list[str]) -> dict:
    """Return all model_endpoint permissions that reference a server not in active_server_names.

    Uses fuzzy normalization (lowercase, strip hyphens/underscores) so renamed
    servers are matched correctly.  Returns {'count': int, 'stale_resource_ids': list[str]}.
    """
    active_norm = {n.lower().replace("-", "").replace("_", "") for n in active_server_names}
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT DISTINCT resource_id FROM permissions WHERE resource_type='model_endpoint'"
            )
            rows = await cur.fetchall()
    stale: list[str] = []
    for row in rows:
        rid = row["resource_id"]
        if not rid or "@" not in rid:
            continue
        suffix = rid.rsplit("@", 1)[1]
        if suffix.lower().replace("-", "").replace("_", "") not in active_norm:
            stale.append(rid)
    return {"count": len(stale), "stale_resource_ids": sorted(stale)}


async def cleanup_stale_model_endpoint_permissions(
    active_server_names: list[str], dry_run: bool = False
) -> dict:
    """Delete every model_endpoint permission that references an inactive server.

    With dry_run=True the function only scans and returns what would be deleted.
    Returns {'deleted': int, 'dry_run': bool, 'count': int, 'stale_resource_ids': list[str]}.
    """
    scan = await scan_stale_model_endpoint_permissions(active_server_names)
    if dry_run or not scan["stale_resource_ids"]:
        return {"deleted": 0, "dry_run": dry_run, **scan}
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM permissions WHERE resource_type='model_endpoint' AND resource_id = ANY(%s)",
                (scan["stale_resource_ids"],),
            )
            deleted = cur.rowcount
    return {"deleted": deleted, "dry_run": False, **scan}


async def admin_get_user_connection(conn_id: str) -> Optional[dict]:
    """Fetch any user connection by ID without user scope. Admin use only."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM user_api_connections WHERE id=%s",
                (conn_id,),
            )
            return await cur.fetchone()


async def update_connection_models_cache(conn_id: str, user_id: str, models: list) -> None:
    """Persist a refreshed model list for a connection."""
    now = datetime.now(timezone.utc).isoformat()
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE user_api_connections SET models_cache=%s, updated_at=%s "
                "WHERE id=%s AND user_id=%s",
                (json.dumps(models), now, conn_id, user_id),
            )


async def update_model_tags_in_cache(conn_id: str, user_id: str, model_id: str, tags: list[str]) -> bool:
    """Update tags for a single model entry within a connection's models_cache.

    Returns True when the model was found and updated, False when it was not present
    in the cache (caller should refresh models first).
    """
    conn_row = await get_user_connection(conn_id, user_id)
    if not conn_row:
        return False
    try:
        models: list = json.loads(conn_row.get("models_cache") or "[]")
    except Exception:
        models = []
    found = False
    for m in models:
        if isinstance(m, dict) and m.get("id") == model_id:
            m["tags"] = list(dict.fromkeys(tags))  # deduplicate, preserve order
            found = True
            break
    if not found:
        return False
    now = datetime.now(timezone.utc).isoformat()
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE user_api_connections SET models_cache=%s, updated_at=%s "
                "WHERE id=%s AND user_id=%s",
                (json.dumps(models), now, conn_id, user_id),
            )
    return True


async def update_connection_rate_limit(conn_id: str, user_id: str, rate_limit_config: dict) -> Optional[dict]:
    """Persist the rate-limit config for a connection. Returns the updated row or None."""
    now = datetime.now(timezone.utc).isoformat()
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE user_api_connections SET rate_limit_config=%s, updated_at=%s "
                "WHERE id=%s AND user_id=%s",
                (json.dumps(rate_limit_config), now, conn_id, user_id),
            )
            if cur.rowcount == 0:
                return None
    return await get_user_connection(conn_id, user_id)


# ─── Valkey Sync ──────────────────────────────────────────────────────────────

async def _get_redis():
    """Lazy Valkey connection (only when Valkey is available)."""
    try:
        import redis.asyncio as aioredis
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        await r.ping()
        return r
    except Exception:
        return None


def _get_user_cost_factor(perms: dict) -> float:
    """Cost factor from unlocked inference servers (maximum over all allowed servers)."""
    import os as _os, json as _js
    endpoint_perms = perms.get("model_endpoint", [])
    if not endpoint_perms:
        return 1.0
    try:
        all_servers = _js.loads(_os.getenv("INFERENCE_SERVERS", "[]"))
        server_map = {s["name"]: float(s.get("cost_factor", 1.0)) for s in all_servers}
        max_factor = 1.0
        for ep in endpoint_perms:
            _, _, srv = ep.partition("@")
            if not srv:
                srv = ep
            if srv == "*":
                max_factor = max(max_factor, max(server_map.values(), default=1.0))
                break
            if srv in server_map:
                max_factor = max(max_factor, server_map[srv])
        return max_factor
    except Exception:
        logger.exception("_get_user_cost_factor failed — defaulting to 1.0")
        return 1.0


async def sync_user_to_redis(user_id: str) -> None:
    """Synct alle aktiven Keys eines Users in den Valkey-Cache."""
    user = await get_user(user_id)
    if not user:
        return
    budget = await get_budget(user_id)
    perms = await get_permissions_map(user_id)
    perms_json = json.dumps(perms)
    cost_factor = _get_user_cost_factor(perms)

    # Cache user-owned templates inline (for orchestrator access without DB hit).
    # The template name and description from the DB row are merged into the config
    # so the orchestrator can match templates by name without a DB hit.
    user_tmpls = await list_user_templates(user_id)
    user_templates_map = {}
    for t in user_tmpls:
        if not t.get("is_active", True):
            continue
        cfg = json.loads(t["config_json"])
        cfg["name"]        = t.get("name", "")         # needed for name-based matching
        cfg["description"] = t.get("description", "")
        user_templates_map[t["id"]] = cfg
    user_templates_json = json.dumps(user_templates_map)

    # User-eigene CC-Profile inline cachen
    user_cc = await list_user_cc_profiles(user_id)
    user_cc_map = {}
    for p in user_cc:
        if not p.get("is_active", True):
            continue
        try:
            cfg = json.loads(p["config_json"])
            cfg["id"]   = p["id"]    # ensure id is always present in the dict
            cfg["name"] = p["name"]  # merge table-level name so warning logs are readable
            user_cc_map[p["id"]] = cfg
        except (json.JSONDecodeError, TypeError) as _e:
            logger.warning("sync_user_to_redis: skipping CC profile %s with invalid config_json: %s",
                           p.get("id", "?"), _e)
    user_cc_profiles_json = json.dumps(user_cc_map)

    # User-eigene API-Verbindungen cachen (mit entschlüsseltem Key für Orchestrator)
    user_conns_raw = await list_user_connections(user_id)
    user_conns_map = {}
    for c in user_conns_raw:
        if c.get("is_active", True):
            try:
                rate_limit_config = json.loads(c.get("rate_limit_config") or "{}")
            except (json.JSONDecodeError, TypeError):
                rate_limit_config = {}
            user_conns_map[c["name"]] = {
                "id":                c["id"],
                "url":               c["url"],
                "api_type":          c.get("api_type", "openai"),
                "api_key":           decrypt_api_key(c.get("api_key_enc", "")),
                "models_cache":      json.loads(c.get("models_cache", "[]")),
                "rate_limit_config": rate_limit_config,
            }
    user_connections_json = json.dumps(user_conns_map)

    r = await _get_redis()
    if not r:
        return
    try:
        key_records = await get_active_key_hashes(user_id)
        for rec in key_records:
            redis_key = f"user:apikey:{rec['key_hash']}"
            if user["is_active"]:
                await r.hset(redis_key, mapping={
                    "user_id":                user_id,
                    "username":               user["username"],
                    "is_active":              "1",
                    "permissions_json":       perms_json,
                    "user_templates_json":    user_templates_json,
                    "user_cc_profiles_json":   user_cc_profiles_json,
                    "user_connections_json":   user_connections_json,
                    "budget_daily":           str(budget.get("daily_limit") or ""),
                    "budget_monthly":         str(budget.get("monthly_limit") or ""),
                    "budget_total":           str(budget.get("total_limit") or ""),
                    "budget_type":            budget.get("budget_type", "subscription"),
                    "budget_cost_factor":     str(cost_factor),
                    "key_id":                 rec["id"],
                    "default_cc_profile_id":  user.get("default_cc_profile_id") or "",
                    "key_cc_profile_id":      rec.get("cc_profile_id") or "",
                    "dynamic_routing":        "1" if rec.get("dynamic_routing") else "0",
                    "local_only_routing":     "1" if rec.get("local_only_routing") else "0",
                    "native_num_ctx":         str(rec.get("native_num_ctx") or 0),
                })
                await r.expire(redis_key, 86400)
            else:
                await r.delete(redis_key)
        await r.set(f"user:{user_id}:cost_factor", str(cost_factor), ex=86400)
    finally:
        await r.aclose()


async def invalidate_user_redis(user_id: str) -> None:
    """Deletes all Valkey keys for a user (on deactivation/deletion)."""
    r = await _get_redis()
    if not r:
        return
    try:
        key_records = await get_active_key_hashes(user_id)
        for rec in key_records:
            await r.delete(f"user:apikey:{rec['key_hash']}")
    finally:
        await r.aclose()


async def invalidate_api_key_redis(key_hash: str) -> None:
    """Deletes a single key from Valkey (on revoke)."""
    r = await _get_redis()
    if not r:
        return
    try:
        await r.delete(f"user:apikey:{key_hash}")
    finally:
        await r.aclose()


async def get_redis_budget_usage(user_id: str) -> dict:
    """Liest aktuellen Token-Verbrauch aus Valkey-Countern (inkl. Input/Output-Trennung)."""
    from datetime import date
    today = date.today().strftime("%Y-%m-%d")
    month = date.today().strftime("%Y-%m")
    r = await _get_redis()
    if not r:
        return {
            "daily_used": 0, "monthly_used": 0, "total_used": 0,
            "daily_input": 0, "daily_output": 0,
            "monthly_input": 0, "monthly_output": 0,
            "total_input": 0, "total_output": 0,
        }
    try:
        daily          = await r.get(f"user:{user_id}:tokens:daily:{today}")
        monthly        = await r.get(f"user:{user_id}:tokens:monthly:{month}")
        total          = await r.get(f"user:{user_id}:tokens:total")
        daily_input    = await r.get(f"user:{user_id}:tokens:daily:{today}:input")
        daily_output   = await r.get(f"user:{user_id}:tokens:daily:{today}:output")
        monthly_input  = await r.get(f"user:{user_id}:tokens:monthly:{month}:input")
        monthly_output = await r.get(f"user:{user_id}:tokens:monthly:{month}:output")
        total_input    = await r.get(f"user:{user_id}:tokens:total:input")
        total_output   = await r.get(f"user:{user_id}:tokens:total:output")
        return {
            "daily_used":      int(daily          or 0),
            "monthly_used":    int(monthly        or 0),
            "total_used":      int(total          or 0),
            "daily_input":     int(daily_input    or 0),
            "daily_output":    int(daily_output   or 0),
            "monthly_input":   int(monthly_input  or 0),
            "monthly_output":  int(monthly_output or 0),
            "total_input":     int(total_input    or 0),
            "total_output":    int(total_output   or 0),
        }
    finally:
        await r.aclose()


# ─── Admin Expert Templates (database-backed) ────────────────────────────────

async def list_admin_templates() -> list[dict]:
    """List manually created admin expert templates (excludes moe-auto dynamic templates)."""
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT id, name, description, config_json, is_active, created_at, updated_at "
                "FROM admin_expert_templates "
                "WHERE id NOT LIKE 'moe-dyn-%' "
                "ORDER BY created_at ASC"
            )
            rows = await cur.fetchall()
    result = []
    for row in rows:
        tmpl = json.loads(row["config_json"])
        tmpl["id"] = row["id"]
        tmpl["name"] = row["name"]
        tmpl["description"] = row["description"]
        tmpl["is_active"] = row["is_active"]
        result.append(tmpl)
    return result


async def list_dynamic_templates() -> list[dict]:
    """List moe-auto generated dynamic templates joined with feedback log data."""
    async with _pool.connection() as conn:
        conn.row_factory = dict_row
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT t.id, t.name, t.description, t.config_json,
                       t.is_active, t.created_at, t.updated_at,
                       f.prompt, f.user_rating, f.latency_ms, f.tokens_used, f.status
                FROM admin_expert_templates t
                LEFT JOIN dynamic_template_feedback_log f ON f.template_id = t.id
                WHERE t.id LIKE 'moe-dyn-%'
                ORDER BY t.created_at DESC
            """)
            rows = await cur.fetchall()
    result = []
    for row in rows:
        r = dict(row)
        try:
            r["config"] = json.loads(r.pop("config_json"))
        except Exception:
            r["config"] = {}
            
        # Fallback: extract reasoning trace from description if not in config
        if "reasoning_trace" not in r["config"] and r.get("description") and " | Reasoning: " in r["description"]:
            parts = r["description"].split(" | Reasoning: ", 1)
            r["config"]["reasoning_trace"] = parts[1]
            
        result.append(r)
    return result


async def delete_dynamic_template(tmpl_id: str) -> bool:
    """Delete a moe-auto dynamic template and its feedback log entry."""
    if not tmpl_id.startswith("moe-dyn-"):
        return False
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM dynamic_template_feedback_log WHERE template_id=%s", (tmpl_id,))
            await cur.execute("DELETE FROM admin_expert_templates WHERE id=%s", (tmpl_id,))
            return cur.rowcount > 0


async def upsert_admin_template(tmpl: dict) -> dict:
    """Insert or update an admin expert template in the database."""
    tmpl_id = tmpl.get("id", f"tmpl-{secrets.token_hex(4)}")
    name = tmpl.get("name", "")
    description = tmpl.get("description", "")
    now = datetime.now(timezone.utc).isoformat()

    # Store everything except id/name/description/is_active in config_json
    config = {k: v for k, v in tmpl.items()
              if k not in ("id", "name", "description", "is_active")}
    config_json = json.dumps(config, ensure_ascii=False)

    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT INTO admin_expert_templates (id, name, description, config_json, is_active, created_at, updated_at)
                VALUES (%s, %s, %s, %s, TRUE, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    config_json = EXCLUDED.config_json,
                    updated_at = EXCLUDED.updated_at
            """, (tmpl_id, name, description, config_json, now, now))
        await conn.commit()

    tmpl["id"] = tmpl_id
    return tmpl


async def save_all_admin_templates(templates: list[dict]) -> None:
    """Replace all admin expert templates in the database (bulk save)."""
    now = datetime.now(timezone.utc).isoformat()
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM admin_expert_templates")
            for tmpl in templates:
                tmpl_id = tmpl.get("id", f"tmpl-{secrets.token_hex(4)}")
                config = {k: v for k, v in tmpl.items()
                          if k not in ("id", "name", "description", "is_active")}
                await cur.execute("""
                    INSERT INTO admin_expert_templates (id, name, description, config_json, is_active, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, (
                    tmpl_id,
                    tmpl.get("name", ""),
                    tmpl.get("description", ""),
                    json.dumps(config, ensure_ascii=False),
                    tmpl.get("is_active", True),
                    now, now,
                ))
        await conn.commit()


async def migrate_env_templates_to_db(env_templates: list[dict]) -> int:
    """One-time migration: move templates from .env to database.

    Only imports templates whose IDs don't already exist in the DB.
    Returns the number of templates migrated.
    """
    existing = await list_admin_templates()
    existing_ids = {t["id"] for t in existing}
    existing_names = {t["name"] for t in existing}
    migrated = 0

    for tmpl in env_templates:
        if tmpl.get("id") in existing_ids or tmpl.get("name") in existing_names:
            continue
        await upsert_admin_template(tmpl)
        migrated += 1

    return migrated


# ─── Federation Config ────────────────────────────────────────────────────────

async def get_federation_config() -> dict:
    """Get the federation configuration (singleton row)."""
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM federation_config WHERE id = 'default'")
            row = await cur.fetchone()
    if not row:
        return {
            "id": "default", "enabled": False, "hub_url": "", "hub_api_key": "",
            "node_id": "", "node_name": "", "sync_interval_seconds": 3600,
            "auto_push_enabled": False, "last_push_at": None, "last_pull_at": None,
        }
    return dict(row)


async def save_federation_config(config: dict) -> None:
    """Insert or update the federation configuration."""
    now = datetime.now(timezone.utc).isoformat()
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT INTO federation_config (id, enabled, hub_url, hub_api_key,
                    node_id, node_name, sync_interval_seconds, auto_push_enabled, updated_at)
                VALUES ('default', %(enabled)s, %(hub_url)s, %(hub_api_key)s,
                    %(node_id)s, %(node_name)s, %(sync_interval_seconds)s,
                    %(auto_push_enabled)s, %(updated_at)s)
                ON CONFLICT (id) DO UPDATE SET
                    enabled = EXCLUDED.enabled,
                    hub_url = EXCLUDED.hub_url,
                    hub_api_key = CASE WHEN EXCLUDED.hub_api_key = '' THEN federation_config.hub_api_key
                                      ELSE EXCLUDED.hub_api_key END,
                    node_id = EXCLUDED.node_id,
                    node_name = EXCLUDED.node_name,
                    sync_interval_seconds = EXCLUDED.sync_interval_seconds,
                    auto_push_enabled = EXCLUDED.auto_push_enabled,
                    updated_at = EXCLUDED.updated_at
            """, {**config, "updated_at": now})
        await conn.commit()


async def update_federation_sync_timestamp(direction: str) -> None:
    """Update last_push_at or last_pull_at."""
    now = datetime.now(timezone.utc).isoformat()
    col = "last_push_at" if direction == "push" else "last_pull_at"
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"UPDATE federation_config SET {col} = %s WHERE id = 'default'", (now,)
            )
        await conn.commit()


# ─── Federation Domain Policies ───────────────────────────────────────────────

async def list_federation_policies() -> list[dict]:
    """List all domain policies."""
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM federation_domain_policy ORDER BY domain"
            )
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_federation_policy(domain: str) -> Optional[dict]:
    """Get policy for a specific domain."""
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM federation_domain_policy WHERE domain = %s", (domain,)
            )
            row = await cur.fetchone()
    return dict(row) if row else None


async def upsert_federation_policy(domain: str, mode: str,
                                    min_confidence: float = 0.7,
                                    only_verified: bool = True) -> dict:
    """Create or update a domain policy."""
    now = datetime.now(timezone.utc).isoformat()
    policy_id = f"fedpol-{domain}"
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT INTO federation_domain_policy (id, domain, mode, min_confidence, only_verified, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (domain) DO UPDATE SET
                    mode = EXCLUDED.mode,
                    min_confidence = EXCLUDED.min_confidence,
                    only_verified = EXCLUDED.only_verified,
                    updated_at = EXCLUDED.updated_at
            """, (policy_id, domain, mode, min_confidence, only_verified, now))
        await conn.commit()
    return {"id": policy_id, "domain": domain, "mode": mode,
            "min_confidence": min_confidence, "only_verified": only_verified}


async def delete_federation_policy(domain: str) -> bool:
    """Delete a domain policy."""
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM federation_domain_policy WHERE domain = %s", (domain,)
            )
            deleted = cur.rowcount > 0
        await conn.commit()
    return deleted


# ─── Federation Outbox ────────────────────────────────────────────────────────

async def create_outbox_entry(bundle_json: str, domain: str,
                               triple_count: int, entity_count: int) -> dict:
    """Queue a knowledge bundle for manual review before push."""
    entry_id = uuid.uuid4().hex[:16]
    now = datetime.now(timezone.utc).isoformat()
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                INSERT INTO federation_outbox (id, bundle_json, status, domain,
                    triple_count, entity_count, created_at)
                VALUES (%s, %s, 'pending', %s, %s, %s, %s)
            """, (entry_id, bundle_json, domain, triple_count, entity_count, now))
        await conn.commit()
    return {"id": entry_id, "status": "pending", "domain": domain,
            "triple_count": triple_count, "entity_count": entity_count}


async def list_outbox(status: Optional[str] = None, limit: int = 50) -> list[dict]:
    """List outbox entries, optionally filtered by status."""
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            if status:
                await cur.execute(
                    "SELECT id, status, domain, triple_count, entity_count, created_at, sent_at, error "
                    "FROM federation_outbox WHERE status = %s ORDER BY created_at DESC LIMIT %s",
                    (status, limit)
                )
            else:
                await cur.execute(
                    "SELECT id, status, domain, triple_count, entity_count, created_at, sent_at, error "
                    "FROM federation_outbox ORDER BY created_at DESC LIMIT %s",
                    (limit,)
                )
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_outbox_entry(entry_id: str) -> Optional[dict]:
    """Get a single outbox entry with full bundle data."""
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT * FROM federation_outbox WHERE id = %s", (entry_id,)
            )
            row = await cur.fetchone()
    return dict(row) if row else None


async def update_outbox_status(entry_id: str, status: str,
                                error: Optional[str] = None) -> None:
    """Update outbox entry status (pending → sent/failed/approved)."""
    now = datetime.now(timezone.utc).isoformat()
    async with _pool.connection() as conn:
        async with conn.cursor() as cur:
            if status == "sent":
                await cur.execute(
                    "UPDATE federation_outbox SET status = %s, sent_at = %s WHERE id = %s",
                    (status, now, entry_id)
                )
            elif error:
                await cur.execute(
                    "UPDATE federation_outbox SET status = %s, error = %s WHERE id = %s",
                    (status, error, entry_id)
                )
            else:
                await cur.execute(
                    "UPDATE federation_outbox SET status = %s WHERE id = %s",
                    (status, entry_id)
                )
        await conn.commit()


# ─── Skill Registry ───────────────────────────────────────────────────────────

async def list_skill_registry() -> list[dict]:
    """Returns all entries in the skill_registry table ordered by skill name."""
    if _pool is None:
        return []
    async with _pool.connection() as conn:
        conn.row_factory = dict_row
        rows = await conn.execute(
            "SELECT skill_name, admin_approved, approved_by, approved_at, "
            "audit_verdict, is_builtin, created_at "
            "FROM skill_registry ORDER BY skill_name"
        )
        return [dict(r) for r in await rows.fetchall()]


async def approve_skill(skill_name: str, approved_by: str) -> bool:
    """Sets admin_approved=TRUE for skill_name and records who approved it.

    Returns True if the skill was found and updated, False otherwise.
    """
    if _pool is None:
        return False
    now = datetime.now(timezone.utc).isoformat()
    async with _pool.connection() as conn:
        result = await conn.execute(
            "UPDATE skill_registry SET admin_approved=TRUE, approved_by=%s, approved_at=%s "
            "WHERE skill_name=%s",
            (approved_by, now, skill_name),
        )
        await conn.commit()
        return result.rowcount > 0


async def revoke_skill(skill_name: str) -> bool:
    """Revokes approval for skill_name (admin_approved=FALSE, clears approver)."""
    if _pool is None:
        return False
    async with _pool.connection() as conn:
        result = await conn.execute(
            "UPDATE skill_registry SET admin_approved=FALSE, approved_by=NULL, approved_at=NULL "
            "WHERE skill_name=%s",
            (skill_name,),
        )
        await conn.commit()
        return result.rowcount > 0


async def get_skill_audit_log(skill_name: Optional[str] = None, limit: int = 100) -> list[dict]:
    """Returns recent skill execution audit log entries, optionally filtered by skill name."""
    if _pool is None:
        return []
    async with _pool.connection() as conn:
        conn.row_factory = dict_row
        if skill_name:
            rows = await conn.execute(
                "SELECT id, skill_name, user_id, session_id, args_hash, executed_at, outcome "
                "FROM skill_audit_log WHERE skill_name=%s "
                "ORDER BY executed_at DESC LIMIT %s",
                (skill_name, limit),
            )
        else:
            rows = await conn.execute(
                "SELECT id, skill_name, user_id, session_id, args_hash, executed_at, outcome "
                "FROM skill_audit_log ORDER BY executed_at DESC LIMIT %s",
                (limit,),
            )
        return [dict(r) for r in await rows.fetchall()]


# ─── Teams CRUD ───────────────────────────────────────────────────────────────

async def create_team(name: str, slug: str, created_by: str, tenant_id: Optional[str] = None) -> dict:
    """Creates a new team and returns its record."""
    tid = new_id()
    now = now_iso()
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO teams (id, name, slug, tenant_id, created_by, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (tid, name, slug, tenant_id, created_by, now),
            )
    return {"id": tid, "name": name, "slug": slug, "tenant_id": tenant_id,
            "created_by": created_by, "created_at": now}


async def get_team(team_id: str) -> Optional[dict]:
    async with _get_pool().connection() as conn:
        conn.row_factory = dict_row
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM teams WHERE id=%s", (team_id,))
            row = await cur.fetchone()
    return dict(row) if row else None


async def list_teams() -> list[dict]:
    async with _get_pool().connection() as conn:
        conn.row_factory = dict_row
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM teams ORDER BY name")
            return [dict(r) for r in await cur.fetchall()]


async def delete_team(team_id: str) -> None:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM teams WHERE id=%s", (team_id,))


async def add_team_member(team_id: str, user_id: str, role: str = "member") -> None:
    """Adds user to team and auto-grants graph_tenant permission for the team namespace."""
    mid = new_id()
    now = now_iso()
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            try:
                await cur.execute(
                    "INSERT INTO team_memberships (id, team_id, user_id, role, joined_at) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (mid, team_id, user_id, role, now),
                )
            except Exception:
                await cur.execute(
                    "UPDATE team_memberships SET role=%s WHERE team_id=%s AND user_id=%s",
                    (role, team_id, user_id),
                )
    await grant_permission(user_id, "graph_tenant", f"team:{team_id}")
    team = await get_team(team_id)
    if team and team.get("tenant_id"):
        await grant_permission(user_id, "graph_tenant", f"tenant:{team['tenant_id']}")


async def remove_team_member(team_id: str, user_id: str) -> None:
    """Removes user from team and revokes the team graph_tenant permission."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "DELETE FROM team_memberships WHERE team_id=%s AND user_id=%s",
                (team_id, user_id),
            )
    await revoke_permission_by_resource(user_id, "graph_tenant", f"team:{team_id}")


async def list_team_members(team_id: str) -> list[dict]:
    async with _get_pool().connection() as conn:
        conn.row_factory = dict_row
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT tm.*, u.username, u.display_name, u.email "
                "FROM team_memberships tm JOIN users u ON u.id=tm.user_id "
                "WHERE tm.team_id=%s ORDER BY tm.role DESC, u.username",
                (team_id,),
            )
            return [dict(r) for r in await cur.fetchall()]


async def get_user_teams(user_id: str) -> list[str]:
    """Returns list of team_ids the user belongs to."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT team_id FROM team_memberships WHERE user_id=%s",
                (user_id,),
            )
            rows = await cur.fetchall()
    return [r[0] for r in rows]


async def get_team_member_role(team_id: str, user_id: str) -> Optional[str]:
    """Returns the user's role in a team, or None if not a member."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT role FROM team_memberships WHERE team_id=%s AND user_id=%s",
                (team_id, user_id),
            )
            row = await cur.fetchone()
    return row[0] if row else None


async def get_team_members_ids(team_id: str) -> list[str]:
    """Returns list of user_ids in a team."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT user_id FROM team_memberships WHERE team_id=%s",
                (team_id,),
            )
            rows = await cur.fetchall()
    return [r[0] for r in rows]


# ─── Tenants CRUD ─────────────────────────────────────────────────────────────

async def create_tenant(name: str, slug: str, created_by: str) -> dict:
    """Creates a new tenant (Mandant) and returns its record."""
    tid = new_id()
    now = now_iso()
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO tenants (id, name, slug, created_by, created_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (tid, name, slug, created_by, now),
            )
    return {"id": tid, "name": name, "slug": slug, "created_by": created_by, "created_at": now}


async def get_tenant(tenant_id: str) -> Optional[dict]:
    async with _get_pool().connection() as conn:
        conn.row_factory = dict_row
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM tenants WHERE id=%s", (tenant_id,))
            row = await cur.fetchone()
    return dict(row) if row else None


async def list_tenants() -> list[dict]:
    async with _get_pool().connection() as conn:
        conn.row_factory = dict_row
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM tenants ORDER BY name")
            return [dict(r) for r in await cur.fetchall()]


async def delete_tenant(tenant_id: str) -> None:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM tenants WHERE id=%s", (tenant_id,))


async def add_tenant_member(
    tenant_id: str,
    role: str = "member",
    user_id: Optional[str] = None,
    team_id: Optional[str] = None,
) -> None:
    """Adds a user or entire team to a tenant and grants the tenant namespace."""
    mid = new_id()
    now = now_iso()
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO tenant_memberships (id, tenant_id, team_id, user_id, role, joined_at) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (mid, tenant_id, team_id, user_id, role, now),
            )
    if team_id:
        for uid in await get_team_members_ids(team_id):
            await grant_permission(uid, "graph_tenant", f"tenant:{tenant_id}")
    elif user_id:
        await grant_permission(user_id, "graph_tenant", f"tenant:{tenant_id}")


async def list_tenant_members(tenant_id: str) -> list[dict]:
    async with _get_pool().connection() as conn:
        conn.row_factory = dict_row
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT tm.*, u.username AS user_name, u.display_name, t.name AS team_name "
                "FROM tenant_memberships tm "
                "LEFT JOIN users u ON u.id=tm.user_id "
                "LEFT JOIN teams t ON t.id=tm.team_id "
                "WHERE tm.tenant_id=%s ORDER BY tm.role DESC",
                (tenant_id,),
            )
            return [dict(r) for r in await cur.fetchall()]


async def get_tenant_member_role(tenant_id: str, user_id: str) -> Optional[str]:
    """Returns user's role in a tenant via direct or team membership, or None."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT role FROM tenant_memberships WHERE tenant_id=%s AND user_id=%s",
                (tenant_id, user_id),
            )
            row = await cur.fetchone()
            if row:
                return row[0]
            await cur.execute(
                "SELECT tm.role FROM tenant_memberships tm "
                "JOIN team_memberships tmem ON tmem.team_id=tm.team_id "
                "WHERE tm.tenant_id=%s AND tmem.user_id=%s LIMIT 1",
                (tenant_id, user_id),
            )
            row = await cur.fetchone()
    return row[0] if row else None


# ─── Team Budgets ─────────────────────────────────────────────────────────────

async def set_team_budget(team_id: str, monthly_limit: Optional[int],
                          daily_limit: Optional[int]) -> None:
    now = now_iso()
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO team_budgets (team_id, monthly_limit, daily_limit, updated_at) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT(team_id) DO UPDATE SET "
                "monthly_limit=excluded.monthly_limit, daily_limit=excluded.daily_limit, "
                "updated_at=excluded.updated_at",
                (team_id, monthly_limit, daily_limit, now),
            )


async def get_team_budget(team_id: str) -> dict:
    async with _get_pool().connection() as conn:
        conn.row_factory = dict_row
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM team_budgets WHERE team_id=%s", (team_id,))
            row = await cur.fetchone()
    return dict(row) if row else {"team_id": team_id, "monthly_limit": None,
                                   "daily_limit": None, "monthly_used": 0, "daily_used": 0}


async def deduct_team_budget(team_id: str, tokens: int) -> None:
    """Atomically increments token usage counters for a team."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE team_budgets SET monthly_used=monthly_used+%s, daily_used=daily_used+%s "
                "WHERE team_id=%s",
                (tokens, tokens, team_id),
            )


async def check_team_budget(team_id: str, tokens: int) -> tuple[bool, str]:
    """Returns (allowed, reason). Checks if team has budget remaining for `tokens`."""
    budget = await get_team_budget(team_id)
    if budget.get("monthly_limit") and (budget["monthly_used"] + tokens) > budget["monthly_limit"]:
        return False, f"Team monthly budget exhausted ({budget['monthly_used']}/{budget['monthly_limit']} tokens)"
    if budget.get("daily_limit") and (budget["daily_used"] + tokens) > budget["daily_limit"]:
        return False, f"Team daily budget exhausted ({budget['daily_used']}/{budget['daily_limit']} tokens)"
    return True, ""


# ─── Dynamic Router & Model Metadata Helpers ─────────────────────────────────

async def upsert_model_metadata(
    model_id: str,
    base_model: str,
    parameter_size_b: float,
    context_window: int,
    family: str,
    strengths: list[str],
    benchmark_scores: dict,
    tags: Optional[list[str]] = None,
) -> None:
    now = now_iso()
    effective_tags = list(dict.fromkeys(tags or []))  # deduplicate, preserve order
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO model_metadata (model_id, base_model, parameter_size_b, context_window, family, strengths, benchmark_scores, tags, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT(model_id) DO UPDATE SET "
                "base_model=excluded.base_model, parameter_size_b=excluded.parameter_size_b, "
                "context_window=excluded.context_window, family=excluded.family, "
                "strengths=excluded.strengths, benchmark_scores=excluded.benchmark_scores, "
                "tags=excluded.tags, updated_at=excluded.updated_at",
                (model_id, base_model, parameter_size_b, context_window, family, strengths, json.dumps(benchmark_scores), effective_tags, now),
            )


async def get_model_metadata(model_id: str) -> Optional[dict]:
    async with _get_pool().connection() as conn:
        conn.row_factory = dict_row
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM model_metadata WHERE model_id=%s", (model_id,))
            row = await cur.fetchone()
            if row:
                res = dict(row)
                if isinstance(res.get("benchmark_scores"), str):
                    try:
                        res["benchmark_scores"] = json.loads(res["benchmark_scores"])
                    except Exception:
                        pass
                return res
            return None


async def list_all_model_metadata() -> list[dict]:
    async with _get_pool().connection() as conn:
        conn.row_factory = dict_row
        async with conn.cursor() as cur:
            await cur.execute("SELECT * FROM model_metadata ORDER BY model_id")
            rows = await cur.fetchall()
            result = []
            for r in rows:
                res = dict(r)
                if isinstance(res.get("benchmark_scores"), str):
                    try:
                        res["benchmark_scores"] = json.loads(res["benchmark_scores"])
                    except Exception:
                        pass
                result.append(res)
            return result


async def find_model_metadata_by_base(base_model_id: str) -> Optional[dict]:
    """Find a model_metadata entry by base model ID, ignoring any @suffix.

    Lookup priority:
    1. Exact match on base_model_id (no suffix)
    2. base_model_id@anything (any server/connection suffix)
    Returns the best matching row, preferring the entry with the most metadata
    (non-zero parameter_size_b), or None if nothing found.
    """
    async with _get_pool().connection() as conn:
        conn.row_factory = dict_row
        async with conn.cursor() as cur:
            # Exact match first
            await cur.execute("SELECT * FROM model_metadata WHERE model_id = %s", (base_model_id,))
            row = await cur.fetchone()
            if row:
                return dict(row)
            # Any @suffix variant — prefer entries with real metadata (size > 0)
            await cur.execute(
                "SELECT * FROM model_metadata WHERE model_id LIKE %s "
                "ORDER BY (parameter_size_b > 0) DESC, model_id LIMIT 1",
                (base_model_id + "@%",),
            )
            row = await cur.fetchone()
            if row:
                return dict(row)
            return None


async def delete_model_metadata(model_id: str) -> bool:
    """Delete a model_metadata entry by its primary key. Returns True if deleted."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM model_metadata WHERE model_id=%s", (model_id,))
            return cur.rowcount > 0


async def aggregate_connection_model_tags() -> dict[str, list[str]]:
    """Collect tags assigned to models in any user's connection cache.

    Returns {model_id@conn_name: sorted_list_of_tags} — the @conn_name suffix makes
    each entry uniquely identifiable by connection, consistent with the model_metadata
    format used for locally-indexed models (e.g. model@N04-RTX).
    """
    buckets: dict[str, set] = {}
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT name, models_cache FROM user_api_connections WHERE is_active=TRUE")
            rows = await cur.fetchall()
    for row in rows:
        conn_name = row.get("name", "")
        try:
            models = json.loads(row.get("models_cache") or "[]")
        except Exception:
            continue
        for m in models:
            if not isinstance(m, dict):
                continue
            mid  = m.get("id", "")
            tags = m.get("tags") or []
            if mid and tags and conn_name:
                key = f"{mid}@{conn_name}"
                if key not in buckets:
                    buckets[key] = set()
                buckets[key].update(tags)
    return {key: sorted(tags) for key, tags in buckets.items()}


async def log_dynamic_template_feedback(
    template_id: str,
    prompt: str,
    config_json: str,
    latency_ms: Optional[int],
    tokens_used: Optional[int],
    status: str = "success",
) -> None:
    now = now_iso()
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "INSERT INTO dynamic_template_feedback_log (template_id, prompt, config_json, latency_ms, tokens_used, status, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (template_id, prompt, config_json, latency_ms, tokens_used, status, now),
            )


async def update_dynamic_template_feedback_metrics(
    template_id: str, latency_ms: int, tokens_used: int
) -> None:
    """Fills in latency_ms and tokens_used on the seed row created by log_dynamic_template_feedback.
    Only updates if the row still has NULL metrics (i.e. first real inference after template creation)."""
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE dynamic_template_feedback_log "
                "SET latency_ms=%s, tokens_used=%s "
                "WHERE template_id=%s AND latency_ms IS NULL",
                (latency_ms, tokens_used, template_id),
            )


async def update_dynamic_template_feedback_rating(template_id: str, rating: int) -> bool:
    async with _get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE dynamic_template_feedback_log SET user_rating=%s WHERE template_id=%s",
                (rating, template_id),
            )
            return cur.rowcount > 0
