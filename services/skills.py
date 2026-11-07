"""
services/skills.py — Server-side skill resolution and registry.

Skills are markdown files loaded from /app/skills (built-in) and
/app/skills/community (user-installed). The registry table in Postgres
acts as an admin-controlled hard-lock: a skill only executes when
admin_approved=TRUE in skill_registry.
"""

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

import state

logger = logging.getLogger("MOE-SOVEREIGN")

# ---------------------------------------------------------------------------
# Filesystem paths
# ---------------------------------------------------------------------------

_SKILLS_DIR          = Path("/app/skills")
_COMMUNITY_SKILLS_DIR = _SKILLS_DIR / "community"
_SKILL_FM_RE         = re.compile(r"^---\s*\n.*?\n---\s*\n?(.*)", re.DOTALL)

# ---------------------------------------------------------------------------
# File → skill mapping (extension / MIME type)
# ---------------------------------------------------------------------------

_FILE_SKILL_MAP: Dict[str, str] = {
    ".pdf":  "pdf",
    ".docx": "docx",
    ".doc":  "docx",
    ".xlsx": "xlsx",
    ".xls":  "xlsx",
    ".csv":  "xlsx",
    ".tsv":  "xlsx",
    ".pptx": "pptx",
    ".ppt":  "pptx",
}

_MIME_SKILL_MAP: Dict[str, str] = {
    "application/pdf":                                                                    "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document":           "docx",
    "application/msword":                                                                 "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":                 "xlsx",
    "application/vnd.ms-excel":                                                           "xlsx",
    "text/csv":                                                                           "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation":         "pptx",
    "application/vnd.ms-powerpoint":                                                      "pptx",
}


# ---------------------------------------------------------------------------
# Skill loading from filesystem
# ---------------------------------------------------------------------------

def _load_skill_body(name: str) -> Optional[str]:
    """Load a skill by name from built-in or community directory.
    Built-in skills take priority over community skills."""
    for d in (_SKILLS_DIR, _COMMUNITY_SKILLS_DIR):
        for candidate in (d / f"{name}.md", d / f"{name}.md.disabled"):
            if candidate.exists() and candidate.suffix == ".md":
                try:
                    raw = candidate.read_text(encoding="utf-8")
                    m = _SKILL_FM_RE.match(raw)
                    return m.group(1).strip() if m else raw.strip()
                except OSError:
                    pass
    return None


def _build_skill_catalog() -> str:
    """Build a compact skill catalog string for the planner prompt."""
    _DESC_RE = re.compile(r"description:\s*(.+?)(?:\n|$)")
    catalog = []
    for skill_dir in (_SKILLS_DIR, _COMMUNITY_SKILLS_DIR):
        if not skill_dir.exists():
            continue
        for f in sorted(skill_dir.iterdir()):
            if f.suffix == ".md" and ".disabled" not in f.name and f.stem not in ("_SPEC", "README"):
                try:
                    header = f.read_text(encoding="utf-8")[:500]
                    m = _DESC_RE.search(header)
                    if m:
                        desc = m.group(1).strip()[:100]
                        name = f.stem
                        if not any(name in entry for entry in catalog):
                            catalog.append(f"  /{name}: {desc}")
                except OSError:
                    pass
    if not catalog:
        return ""
    return (
        "\n\nAVAILABLE OUTPUT SKILLS (for non-plaintext deliverables):\n"
        "If the user's request would benefit from a specific output format (PDF, DOCX, HTML, slides, etc.),\n"
        "add an optional field \"output_skill\" to one of your tasks.\n"
        "Only suggest a skill when the request clearly implies a document or visual output.\n"
        + "\n".join(catalog)
        + '\nExample: {"task": "Create visual report", "category": "research", "output_skill": "web-artifacts-builder"}\n'
    )


def _resolve_skill_invocation(text: str, allowed_skills: Optional[list] = None) -> str:
    """Resolve /skill-name [args] server-side (client-independent).

    Also accepts the escape form $$/skill-name which passes through API-layer
    slash-command interceptors (e.g. Claude Code CLI) unchanged.

    allowed_skills: None = all allowed; list = only listed skills or '*' for all.
    Returns unchanged text if no matching skill found or not permitted.

    Do not call directly from async request handlers — use _resolve_skill_secure()
    which enforces the ADMIN_APPROVED hard-lock.
    """
    if not text:
        return text
    # Normalise the API-safe escape prefix $$/skill → /skill
    if text.startswith("$$/"):
        text = text[2:]
    if not text.startswith("/"):
        return text
    m = re.match(r"^/([a-zA-Z0-9][a-zA-Z0-9\-]*)(?:[ \t]+(.*))?$", text, re.DOTALL)
    if not m:
        return text
    skill_name, args = m.group(1), (m.group(2) or "").strip()
    if allowed_skills is not None and "*" not in allowed_skills and skill_name not in allowed_skills:
        logger.info("⛔ Skill /%s not allowed for this user", skill_name)
        return text
    body = _load_skill_body(skill_name)
    if body is None:
        return text
    resolved = body.replace("$ARGUMENTS", args)
    logger.info("🎯 Skill resolved: /%s → %d chars", skill_name, len(resolved))
    return resolved


# ---------------------------------------------------------------------------
# Skill registry (Postgres hard-lock)
# ---------------------------------------------------------------------------

async def _ensure_skill_registry_schema() -> None:
    """Create skill_registry and skill_audit_log tables if they do not exist."""
    if state._userdb_pool is None:
        return
    ddl = """
        CREATE TABLE IF NOT EXISTS skill_registry (
            skill_name      TEXT PRIMARY KEY,
            admin_approved  BOOLEAN NOT NULL DEFAULT FALSE,
            approved_by     TEXT,
            approved_at     TIMESTAMPTZ,
            audit_verdict   TEXT CHECK (audit_verdict IN ('safe', 'warning', 'blocked')),
            audit_file      TEXT,
            is_builtin      BOOLEAN NOT NULL DEFAULT FALSE,
            created_at      TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS skill_audit_log (
            id              BIGSERIAL PRIMARY KEY,
            skill_name      TEXT NOT NULL,
            user_id         TEXT NOT NULL,
            session_id      TEXT,
            args_hash       TEXT,
            executed_at     TIMESTAMPTZ DEFAULT NOW(),
            outcome         TEXT CHECK (outcome IN ('executed', 'blocked', 'error'))
        );
        CREATE INDEX IF NOT EXISTS idx_skill_audit_log_skill  ON skill_audit_log (skill_name);
        CREATE INDEX IF NOT EXISTS idx_skill_audit_log_user   ON skill_audit_log (user_id);
        CREATE INDEX IF NOT EXISTS idx_skill_audit_log_ts     ON skill_audit_log (executed_at);
    """
    try:
        async with state._userdb_pool.connection() as conn:
            await conn.execute(ddl)
        logger.info("✅ Skill registry schema ensured")
    except Exception as e:
        logger.warning("⚠️ Skill registry schema setup failed: %s", e)


async def _bootstrap_skill_registry() -> None:
    """Populate skill_registry from filesystem on startup (idempotent).

    Built-in skills get admin_approved=TRUE automatically.
    Community skills are approved when their audit.json verdict is 'safe'.
    """
    if state._userdb_pool is None:
        return
    rows: list[tuple] = []
    if _SKILLS_DIR.exists():
        for f in _SKILLS_DIR.iterdir():
            if f.suffix == ".md" and ".disabled" not in f.name and f.stem not in ("_SPEC", "README"):
                rows.append((f.stem, True, None, True))
    if _COMMUNITY_SKILLS_DIR.exists():
        for f in _COMMUNITY_SKILLS_DIR.iterdir():
            if f.suffix == ".md" and ".disabled" not in f.name:
                audit_path = _COMMUNITY_SKILLS_DIR / f"{f.stem}.audit.json"
                approved, verdict = False, None
                if audit_path.exists():
                    try:
                        audit = json.loads(audit_path.read_text())
                        verdict = audit.get("verdict")
                        approved = verdict == "safe"
                    except Exception:
                        pass
                rows.append((f.stem, approved, verdict, False))
    if not rows:
        return
    upsert_sql = """
        INSERT INTO skill_registry (skill_name, admin_approved, audit_verdict, is_builtin)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (skill_name) DO NOTHING
    """
    try:
        async with state._userdb_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.executemany(upsert_sql, rows)
            await conn.commit()
        approved_count = sum(1 for r in rows if r[1])
        logger.info("✅ Skill registry bootstrapped: %d skills, %d approved", len(rows), approved_count)
    except Exception as e:
        logger.warning("⚠️ Skill registry bootstrap failed: %s", e)


async def _check_skill_approved(skill_name: str) -> bool:
    """Return True if skill_name has admin_approved=TRUE in the registry.

    Fail-secure: returns False when DB is unavailable.
    """
    if state._userdb_pool is None:
        logger.warning("⛔ Skill /%s blocked — DB unavailable (fail-secure)", skill_name)
        return False
    try:
        async with state._userdb_pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT admin_approved FROM skill_registry WHERE skill_name = %s",
                    (skill_name,),
                )
                row = await cur.fetchone()
        if row is None:
            logger.warning("⛔ Skill /%s not in registry — blocked", skill_name)
            return False
        return bool(row[0])
    except Exception as e:
        logger.warning("⛔ Skill /%s approval check failed (%s) — fail-secure deny", skill_name, e)
        return False


async def _log_skill_execution(
    skill_name: str, user_id: str, session_id: Optional[str], args: str, outcome: str
) -> None:
    """Append a structured audit record for each skill invocation attempt."""
    if state._userdb_pool is None:
        return
    args_hash = hashlib.sha256(args.encode()).hexdigest()[:16] if args else None
    try:
        async with state._userdb_pool.connection() as conn:
            await conn.execute(
                """INSERT INTO skill_audit_log (skill_name, user_id, session_id, args_hash, outcome)
                   VALUES (%s, %s, %s, %s, %s)""",
                (skill_name, user_id, session_id, args_hash, outcome),
            )
    except Exception as e:
        logger.debug("Skill audit log write failed: %s", e)


async def _resolve_skill_secure(
    text: str,
    allowed_skills: Optional[list],
    user_id: str = "anon",
    session_id: Optional[str] = None,
) -> str:
    """Secure async wrapper around _resolve_skill_invocation.

    Enforces the ADMIN_APPROVED hard-lock: a skill only resolves when it has
    admin_approved=TRUE in skill_registry. All attempts are written to
    skill_audit_log for compliance auditing.
    """
    if not text:
        return text
    # Normalise API-safe escape prefix $$/skill → /skill before the guard check
    if text.startswith("$$/"):
        text = text[2:]
    if not text.startswith("/"):
        return text
    m = re.match(r"^/([a-zA-Z0-9][a-zA-Z0-9\-]*)(?:[ \t]+(.*))?$", text, re.DOTALL)
    if not m:
        return text
    skill_name = m.group(1)
    args = (m.group(2) or "").strip()

    approved = await _check_skill_approved(skill_name)
    if not approved:
        await _log_skill_execution(skill_name, user_id, session_id, args, "blocked")
        logger.warning("⛔ Skill /%s hard-locked — ADMIN_APPROVED missing (user=%s)", skill_name, user_id)
        return text

    resolved = _resolve_skill_invocation(text, allowed_skills=allowed_skills)
    outcome = "executed" if resolved != text else "error"
    await _log_skill_execution(skill_name, user_id, session_id, args, outcome)
    return resolved


# ---------------------------------------------------------------------------
# File attachment → skill detection
# ---------------------------------------------------------------------------

def _skill_for_file(name: str = "", mime: str = "") -> Optional[str]:
    """Return the skill name for a filename or MIME type, or None."""
    if name:
        ext = Path(name).suffix.lower()
        if ext in _FILE_SKILL_MAP:
            return _FILE_SKILL_MAP[ext]
    if mime:
        clean = mime.split(";")[0].strip().lower()
        if clean in _MIME_SKILL_MAP:
            return _MIME_SKILL_MAP[clean]
    return None


def _detect_file_skill(
    files: Optional[List],
    allowed_skills: Optional[list] = None,
) -> Optional[str]:
    """Detect file type attachments and return the matching skill name.

    Only real attachments (the OpenWebUI files parameter) count. A file name that is
    merely mentioned in the prompt ("Table shipments.csv: ...") is not an attachment
    and must not turn the request into document work. Returns None if no matching
    skill is found or it is not permitted.
    """
    def _allowed(skill: str) -> bool:
        if allowed_skills is None:
            return True
        return "*" in allowed_skills or skill in allowed_skills

    for f in (files or []):
        if not isinstance(f, dict):
            continue
        name = f.get("name") or f.get("filename") or ""
        mime = f.get("type") or f.get("mime_type") or f.get("content_type") or ""
        if not name and not mime and isinstance(f.get("file"), dict):
            inner = f["file"]
            name = inner.get("name") or inner.get("filename") or ""
            mime = inner.get("type") or inner.get("mime_type") or ""
        skill = _skill_for_file(name=name, mime=mime)
        if skill and _allowed(skill):
            logger.debug("📎 File skill via files param: /%s (name=%r, mime=%r)", skill, name, mime)
            return skill

    return None
