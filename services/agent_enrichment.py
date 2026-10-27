"""
services/agent_enrichment.py — Augmented Tool Path for agentic clients.

Agentic tools (Claude Code CLI via /v1/messages, OpenCode via /v1/chat/completions)
always ship a `tools` array / tool_result turns. The tool-call fast path
(_anthropic_tool_handler in services/pipeline/anthropic.py, _handle_tool_calls
in services/pipeline/chat.py) forwards these straight to the configured tool
model, bypassing the MoE pipeline entirely — no L0/L1 cache, no GraphRAG
context, no knowledge ingestion.

This module holds the shared, pipeline-agnostic logic for the opt-in
enrichment layer that sits between those handlers and the tool model:
turn classification, cache read/write, and GraphRAG context injection.
Every public function is safe to call unconditionally — all I/O is wrapped
with timeouts and falls back to a no-op on error, so the tool path degrades
to today's plain passthrough whenever a flag is off or an enrichment call
fails.

Design reference: UMSETZUNGSPLAN_AGENT_ENRICHMENT_2026-07-09.md

All flags governing whether these functions are actually invoked live in
config.py (AGENT_CACHE_*, AGENT_GRAPHRAG_*, AGENT_INGEST_*) and are resolved
per CC profile / expert template — see services/pipeline/cc_session.py and
services/templates.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import httpx
import psycopg
from psycopg.rows import dict_row

from config import (
    AGENT_CACHE_L0_TTL, AGENT_CACHE_MAX_LOOKUP_MS, AGENT_CACHE_MIN_CONF,
    AGENT_CACHE_TTL_DAYS, AGENT_INGEST_JUDGE, CACHE_MIN_RESPONSE_LEN,
    JUDGE_MODEL, KAFKA_TOPIC_INGEST, KNOWLEDGE_BYPASS_THRESHOLD,
    MOE_USERDB_URL, POSTGRES_CHECKPOINT_URL,
)
from metrics import PROM_AGENT_CACHE, PROM_AGENT_WRITEBACK
from services.helpers import _entry_is_fresh
from services.kafka import _kafka_publish
from services.async_utils import run_sync_daemon
from services.tracking import _register_active_request, _deregister_active_request

logger = logging.getLogger("MOE-SOVEREIGN")

# Same markers/threshold convention as graph/synthesis.py::merger_node and
# services/helpers.py::_self_evaluate — kept in sync deliberately so agent
# write-backs and interactive-pipeline write-backs classify consistently.
_PROC_MARKERS = {
    "requires", "must", "necessary", "prerequisite", "needed",
    "location", "on-site", "on premises", "physically", "necessitates",
}
_JUDGE_PROMOTE_SCORE_MIN = 4   # 1-5 scale; unlocks cache-serving confidence
_JUDGE_FLAG_SCORE_MAX = 2      # 1-5 scale; marks the entry bad (never served)
_AGENT_CONFIDENCE_INITIAL = 0.6   # below AGENT_CACHE_MIN_CONF (0.85) — not servable yet
_AGENT_CONFIDENCE_PROMOTED = 0.9  # above AGENT_CACHE_MIN_CONF — servable after judge OK


# ── Turn classification ───────────────────────────────────────────────────────

@dataclass
class TurnInfo:
    kind: str          # "initial_task" | "mid_loop"
    query: str          # last user text (best-effort extraction)
    scope: str          # sha256(f"{user_id}|{workspace}")[:16]
    cacheable: bool     # kind == "initial_task" AND is_informational(query)


def _field(msg, name: str, default=None):
    """Read `name` off a message that may be a dict or a pydantic-style object."""
    if isinstance(msg, dict):
        return msg.get(name, default)
    return getattr(msg, name, default)


def _has_tool_result_block(content) -> bool:
    """Anthropic content is either a string or a list of typed content blocks."""
    if not isinstance(content, list):
        return False
    for block in content:
        block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
        if block_type == "tool_result":
            return True
    return False


def _last_user_text(messages, api: str) -> str:
    """Best-effort extraction of the most recent user-authored text turn."""
    for msg in reversed(list(messages or [])):
        role = _field(msg, "role")
        if role != "user":
            continue
        content = _field(msg, "content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
                if block_type == "text":
                    text = block.get("text") if isinstance(block, dict) else getattr(block, "text", "")
                    if text:
                        parts.append(text)
            if parts:
                return "\n".join(parts)
    return ""


def classify_turn(messages, tools, api: str, user_id: str = "", system_text: str = "") -> TurnInfo:
    """Classify the current tool-path turn as 'initial_task' or 'mid_loop'.

    initial_task: `tools` present AND no message carries a tool result yet
                  (Anthropic: a `tool_result` content block; OpenAI: role=="tool").
    mid_loop:     at least one tool result is already present in the transcript.

    Tool_use/tool_call decisions themselves are never classified as cacheable —
    only a genuine initial informational question is (see is_informational()).
    """
    messages = list(messages or [])
    if api == "anthropic":
        has_tool_result = any(
            _has_tool_result_block(_field(m, "content"))
            for m in messages
        )
    else:  # "openai"
        has_tool_result = any(_field(m, "role") == "tool" for m in messages)

    kind = "mid_loop" if has_tool_result else "initial_task"
    query = _last_user_text(messages, api)
    workspace = extract_workspace(system_text, messages)
    scope = make_scope(user_id, workspace)
    cacheable = kind == "initial_task" and bool(query) and is_informational(query)
    return TurnInfo(kind=kind, query=query, scope=scope, cacheable=cacheable)


# ── Cacheability heuristic ────────────────────────────────────────────────────

_QUESTION_WORDS = re.compile(
    r"^\s*(what|why|how|where|which|who|when|explain|describe"
    r"|was|warum|wie|wo|welche[rs]?|wer|wann|erkl[aä]re|beschreibe)\b",
    re.IGNORECASE,
)

_MUTATION_WORDS = re.compile(
    r"\b(fix|implement|refactor|write|create|delete|remove|edit|add|install"
    r"|deploy|update|upgrade|migrate|rename|move|build"
    r"|[aä]ndere|schreibe|erstelle|l[oö]sche|entferne|bearbeite|f[uü]ge"
    r"|installiere|deploye|aktualisiere|migriere|benenne|baue|behebe|implementiere)\b",
    re.IGNORECASE,
)


def is_informational(query: str) -> bool:
    """Conservative gate: True only for genuine informational questions.

    Requires question form (trailing '?' or a leading question word) AND the
    absence of any file-mutation imperative — "how do I fix ..." is treated
    as a mutation task, not an informational query, because the mutation verb
    signals the client expects a tool_use action, not a cacheable text answer.
    """
    if not query or not query.strip():
        return False
    stripped = query.strip()
    is_question = stripped.endswith("?") or bool(_QUESTION_WORDS.match(stripped))
    if not is_question:
        return False
    if _MUTATION_WORDS.search(stripped):
        return False
    return True


# Generalized from the kanban-worker "_planning_keywords" heuristic that
# already shipped in services/pipeline/chat.py's two-phase kanban handling —
# same idea (a model announcing what it's about to do is not the same as
# actually doing it), broadened beyond kanban-specific phrasing for the
# tool_choice=auto premature-stop retry (see _handle_tool_calls).
#
# Patterns live in Postgres (admin_premature_stop_patterns), not hardcoded
# here: a newly observed announcement phrasing is discovered in production,
# on a customer's deployment, from a model's own idiosyncratic wording —
# waiting for a code change + container rebuild every time is not viable
# there. This list is only the SEED data, inserted once on first use if the
# table is still empty (see _load_premature_stop_patterns_sync) — after that,
# the table is the source of truth and is managed via the Admin UI
# (admin_ui/templates/premature_stop_patterns.html).
_SEED_PREMATURE_STOP_PATTERNS = [
    # (pattern, pattern_type, language, category, description)
    ("i will now", "literal", "en", "announcement", ""),
    ("i'll now", "literal", "en", "announcement", ""),
    ("let me now", "literal", "en", "announcement", ""),
    ("i am going to", "literal", "en", "announcement", ""),
    ("i'm going to", "literal", "en", "announcement", ""),
    ("next, i will", "literal", "en", "announcement", ""),
    ("next i will", "literal", "en", "announcement", ""),
    ("i will call", "literal", "en", "announcement", ""),
    ("i will use", "literal", "en", "announcement", ""),
    ("i will proceed", "literal", "en", "announcement", ""),
    ("i will start by", "literal", "en", "announcement", ""),
    ("i will first", "literal", "en", "announcement", ""),
    ("first, i will", "literal", "en", "announcement", ""),
    ("step 1:", "literal", "en", "announcement", ""),
    ("step 1)", "literal", "en", "announcement", ""),
    ("i'll call", "literal", "en", "announcement", ""),
    ("i'll use", "literal", "en", "announcement", ""),
    ("i'll proceed", "literal", "en", "announcement", ""),
    ("i'll start by", "literal", "en", "announcement", ""),
    ("i'll first", "literal", "en", "announcement", ""),
    ("let's orient", "literal", "en", "announcement", ""),
    ("orient myself", "literal", "en", "announcement", ""),
    ("orient ourselves", "literal", "en", "announcement", ""),
    # German — confirmed live: qwen3.6:35b responding in German ("Lass mich
    # die JWT-Funktion komplett neu schreiben...", "Prüfen wir woher das
    # kommt...", "Lass mich alles am Stück fixen:") hit none of the English
    # patterns above and the retry never fired. Deployment templates in this
    # system are German-first (see AGENTS.md), so German phrasing is at
    # least as likely as English here, not an edge case.
    ("lass mich", "literal", "de", "announcement", ""),
    ("lass uns", "literal", "de", "announcement", ""),
    ("lasst uns", "literal", "de", "announcement", ""),
    ("ich werde jetzt", "literal", "de", "announcement", ""),
    ("ich werde nun", "literal", "de", "announcement", ""),
    ("ich werde zuerst", "literal", "de", "announcement", ""),
    ("ich werde zunächst", "literal", "de", "announcement", ""),
    ("zunächst muss ich", "literal", "de", "announcement", ""),
    ("zuerst muss ich", "literal", "de", "announcement", ""),
    ("ich muss zunächst", "literal", "de", "announcement", ""),
    ("ich muss zuerst", "literal", "de", "announcement", ""),
    ("als nächstes werde ich", "literal", "de", "announcement", ""),
    ("als erstes werde ich", "literal", "de", "announcement", ""),
    ("schauen wir uns", "literal", "de", "announcement", ""),
    ("schauen wir mal", "literal", "de", "announcement", ""),
    ("prüfen wir", "literal", "de", "announcement", ""),
    # Confirmed live: "Ich fix das jetzt mit einem saubereren Ansatz:" —
    # matched none of the literal phrases above (casual "ich fix das jetzt"
    # instead of "ich werde ... reparieren"). Enumerating every verb variant
    # of "ich <verb> das/es jetzt" isn't tractable as a literal list, so this
    # one narrow regex covers the recurring shape instead.
    (r"\bich\s+\w{2,12}\s+(das|es|dies)\s+(jetzt|nun)\b", "regex", "de", "announcement",
     "casual 'ich <verb> das/es jetzt|nun'"),
    # Confirmed live across three separate incidents: every observed
    # announcement ended with a trailing colon ("...fixen:", "...Ansatz:",
    # "...Debate Session:") — the response stops right where it was about to
    # introduce the next thing. Language-independent, cheap, and matches the
    # actual observed failure shape better than any phrase list could.
    (r":\s*$", "regex", "", "trailing_colon",
     "response text ends on a colon — usually an unfinished announcement"),
    # Confirmed live: qwen3.6:35b sometimes writes its tool call as literal
    # XML-ish markup in the plain-text content field instead of populating
    # the structured OpenAI tool_calls delta — e.g. a message ending in
    # "</parameter>\n</function>\n</tool_call>" with the actual command
    # embedded as text a few lines above. The existing text-embedded-tool-
    # call extraction in _normalise_tool_calls_response (services/pipeline/
    # chat.py) is deliberately scoped to hermes3/qwen2.5 only, to avoid
    # misreading ordinary code snippets in general-purpose replies (e.g.
    # OpenCode discussing a `bash(...)`-style function) as tool calls.
    # Rather than widen that extraction's blast radius, this is a narrow
    # *signal* — literal tool-call markup fragments essentially never appear
    # in a genuine prose answer — used only to decide whether a retry is
    # warranted.
    ("<tool_call>", "literal", "", "malformed_tool_call", ""),
    ("</tool_call>", "literal", "", "malformed_tool_call", ""),
    ("<function=", "literal", "", "malformed_tool_call", ""),
    ("</function>", "literal", "", "malformed_tool_call", ""),
    ("<parameter=", "literal", "", "malformed_tool_call", ""),
    ("</parameter>", "literal", "", "malformed_tool_call", ""),
]


def _load_premature_stop_patterns_sync() -> Optional[list]:
    """One-shot sync query against admin_premature_stop_patterns — same
    pattern as services/templates.py::_load_templates_from_db_sync (sync
    psycopg, safe to call from a background thread, never touches the
    asyncio event loop). Seeds the table from _SEED_PREMATURE_STOP_PATTERNS
    on first use if it's still empty (idempotent — checks emptiness, not a
    fixed set of ids, so admin-deleted seed rows stay deleted on restart).

    Returns a list of (pattern_type, compiled_matcher, category) tuples
    ready for looks_like_premature_stop, or None on any DB failure so the
    caller can fall back to an empty-but-safe default.
    """
    dsn = MOE_USERDB_URL or POSTGRES_CHECKPOINT_URL or ""
    if not dsn:
        return None
    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute("SELECT COUNT(*) AS n FROM admin_premature_stop_patterns")
                if (cur.fetchone() or {}).get("n", 0) == 0:
                    now = datetime.now(timezone.utc).isoformat()
                    for pattern, ptype, lang, cat, desc in _SEED_PREMATURE_STOP_PATTERNS:
                        cur.execute(
                            "INSERT INTO admin_premature_stop_patterns "
                            "(id, pattern, pattern_type, language, category, description, "
                            "created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                            (f"seed-{uuid.uuid4().hex[:12]}", pattern, ptype, lang, cat, desc, now, now),
                        )
                    conn.commit()
                    logger.info(
                        "admin_premature_stop_patterns was empty — seeded %d default patterns",
                        len(_SEED_PREMATURE_STOP_PATTERNS),
                    )
                cur.execute(
                    "SELECT pattern, pattern_type, category FROM admin_premature_stop_patterns "
                    "WHERE enabled = TRUE"
                )
                rows = cur.fetchall()
    except Exception as e:
        logger.debug("Premature-stop pattern DB load failed: %s", e)
        return None
    return _compile_pattern_rows(rows)


def _compile_pattern_rows(rows) -> list:
    """Turns raw {"pattern","pattern_type","category"} rows into matcher
    tuples for looks_like_premature_stop. Split out from
    _load_premature_stop_patterns_sync so tests can compile
    _SEED_PREMATURE_STOP_PATTERNS directly without a live DB connection."""
    compiled: list = []
    for row in rows:
        pattern, ptype, category = row["pattern"], row["pattern_type"], row["category"]
        if ptype == "regex":
            try:
                compiled.append(("regex", re.compile(pattern, re.IGNORECASE), category))
            except re.error as e:
                logger.warning("Skipping invalid regex pattern %r: %s", pattern, e)
                continue
        else:
            compiled.append(("literal", pattern.lower(), category))
    return compiled


def _read_premature_stop_patterns() -> list:
    """Return the current pattern list (60s in-process cache, stale-while-
    revalidate) — same caching mechanics as
    services/templates.py::_read_expert_templates, so a pattern added via
    the Admin UI becomes active within a minute, without a restart, and a
    slow/unreachable DB never blocks a request on the hot tool-passthrough
    path (the sync psycopg connect happens in a background thread)."""
    now = time.monotonic()
    cache = _read_premature_stop_patterns._cache
    if cache["data"] is not None:
        if now - cache["ts"] >= 60 and not cache["refreshing"]:
            cache["refreshing"] = True
            threading.Thread(target=_refresh_premature_stop_patterns_cache, daemon=True).start()
        return cache["data"]
    _refresh_premature_stop_patterns_cache()
    return cache["data"] or []


def _refresh_premature_stop_patterns_cache() -> None:
    cache = _read_premature_stop_patterns._cache
    try:
        data = _load_premature_stop_patterns_sync()
        if data is not None:
            cache["data"] = data
        elif cache["data"] is None:
            # DB unreachable on cold start — better to match nothing than to
            # crash the tool-passthrough path; the next 60s refresh retries.
            cache["data"] = []
        cache["ts"] = time.monotonic()
    finally:
        cache["refreshing"] = False


_read_premature_stop_patterns._cache: dict = {"ts": 0.0, "data": None, "refreshing": False}


def looks_like_premature_stop(text: str) -> bool:
    """True if `text` matches any enabled pattern in
    admin_premature_stop_patterns — an announcement of intent instead of an
    actual answer, a tool call written as literal text markup instead of
    populating the API's structured tool_calls field, or a response that
    trails off on a colon. All are the same underlying failure mode from the
    caller's perspective: a finish_reason=stop/no-tool_calls turn that
    should have been a real tool call.
    """
    if not text:
        return False
    lowered = text.strip().lower()
    # An odd number of ``` fence markers means a code/diagram block was
    # opened and never closed — the same "cut off mid-thought" failure as
    # the trailing-colon heuristic below, just spanning a block instead of
    # a single line. Confirmed live: qwen3.6:35b announcing a plan
    # ("Ich erstelle die komplette Lösung aufgesetzt aus:") with an opened
    # ```mermaid block, then stopping without closing it or calling a tool
    # — matched none of the phrase/regex patterns below since the text
    # itself is fluent, not a truncated word. This is a structural
    # invariant, not a wording variant, so it lives in code rather than the
    # admin-editable pattern table alongside it.
    if lowered.count("```") % 2 == 1:
        return True
    for ptype, matcher, _category in _read_premature_stop_patterns():
        if ptype == "literal":
            if matcher in lowered:
                return True
        elif matcher.search(lowered):
            return True
    return False


# ── Tool-ending classifier (LLM-judged, admin-configurable) ────────────────────
# Text-only tool-passthrough endings that match none of
# admin_premature_stop_patterns still slip through silently (confirmed live —
# see admin_unclassified_tool_endings). Rather than only logging these for a
# human to review, an admin-assignable LLM judges each one asynchronously.
# The model/endpoint is a single DB row (admin_classifier_config), not a
# config.py constant, specifically so it can be pointed at a different Ollama
# instance or a different model size without a code change or rebuild — the
# same reasoning that made the premature-stop patterns themselves DB-backed.

_DEFAULT_CLASSIFIER_MODEL = "gemma4:12b"
_DEFAULT_CLASSIFIER_URL = "http://192.168.155.224:11435/v1"  # N04-RGTX
_DEFAULT_CLASSIFIER_TOKEN = "ollama"


def _load_classifier_config_sync() -> Optional[dict]:
    """Sync psycopg read of the admin_classifier_config singleton row, safe to
    call from a background thread. Seeds the default row (gemma4:12b@N04-RGTX)
    on first use if missing — same idempotent-seed shape as
    _load_premature_stop_patterns_sync. Returns None on any DB failure."""
    dsn = MOE_USERDB_URL or POSTGRES_CHECKPOINT_URL or ""
    if not dsn:
        return None
    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT model, base_url, token, enabled "
                    "FROM admin_classifier_config WHERE id = 'default'"
                )
                row = cur.fetchone()
                if row is None:
                    now = datetime.now(timezone.utc).isoformat()
                    cur.execute(
                        "INSERT INTO admin_classifier_config "
                        "(id, model, base_url, token, enabled, updated_at) "
                        "VALUES ('default', %s, %s, %s, TRUE, %s) "
                        "ON CONFLICT (id) DO NOTHING",
                        (_DEFAULT_CLASSIFIER_MODEL, _DEFAULT_CLASSIFIER_URL,
                         _DEFAULT_CLASSIFIER_TOKEN, now),
                    )
                    conn.commit()
                    row = {
                        "model": _DEFAULT_CLASSIFIER_MODEL,
                        "base_url": _DEFAULT_CLASSIFIER_URL,
                        "token": _DEFAULT_CLASSIFIER_TOKEN,
                        "enabled": True,
                    }
    except Exception as e:
        logger.debug("Classifier config DB load failed: %s", e)
        return None
    return dict(row)


def _read_classifier_config() -> Optional[dict]:
    """60s stale-while-revalidate cache in front of _load_classifier_config_sync
    — same caching mechanics as _read_premature_stop_patterns, so a model
    swapped via the Admin UI takes effect within a minute, without a restart."""
    now = time.monotonic()
    cache = _read_classifier_config._cache
    if cache["data"] is not None:
        if now - cache["ts"] >= 60 and not cache["refreshing"]:
            cache["refreshing"] = True
            threading.Thread(target=_refresh_classifier_config_cache, daemon=True).start()
        return cache["data"]
    _refresh_classifier_config_cache()
    return cache["data"]


def _refresh_classifier_config_cache() -> None:
    cache = _read_classifier_config._cache
    try:
        data = _load_classifier_config_sync()
        if data is not None:
            cache["data"] = data
        cache["ts"] = time.monotonic()
    finally:
        cache["refreshing"] = False


_read_classifier_config._cache: dict = {"ts": 0.0, "data": None, "refreshing": False}

_CLASSIFIER_PROMPT_TEMPLATE = (
    "Du bewertest die Antwort eines LLM-Coding-Agenten (OpenCode/Claude Code) "
    "in einem Tool-Calling-Kontext. Die Antwort war reiner Text, ohne einen "
    "Tool-Call auszulösen, mitten in einer laufenden Coding-Session.\n\n"
    "Ist das ein VORZEITIGER ABBRUCH (der Agent kündigt eine Aktion oder einen "
    "Plan an, führt ihn aber nicht aus — z.B. eine Beschreibung dessen, was "
    "gleich erstellt/geändert wird) oder eine LEGITIME Text-Antwort (z.B. eine "
    "Rückfrage an den Nutzer oder eine abschließende Zusammenfassung, bei der "
    "wirklich keine weitere Aktion nötig ist)?\n\n"
    "Antwort-Text:\n---\n{text}\n---\n\n"
    'Antworte NUR als JSON: {{"premature": true|false, "reasoning": "kurze Begründung, max 1 Satz"}}'
)


async def _classify_tool_ending(text: str) -> Optional[dict]:
    """Calls the admin-configured classifier LLM (default: gemma4:12b@N04-RGTX,
    see admin_classifier_config) to judge whether `text` is a premature stop
    or a legitimate text-only answer. Returns {"premature": bool, "reasoning": str}
    or None if the classifier is disabled/unreachable/returns unparseable
    output — best-effort only, never raises for the caller
    (services/tracking.py::_record_and_classify_tool_ending)."""
    cfg = _read_classifier_config()
    if not cfg or not cfg.get("enabled") or not cfg.get("model"):
        return None
    base = (cfg.get("base_url") or "").rstrip("/").removesuffix("/v1")
    if not base:
        return None
    payload = {
        "model": cfg["model"],
        "messages": [{"role": "user", "content": _CLASSIFIER_PROMPT_TEMPLATE.format(text=text[:2000])}],
        "stream": False,
        "format": "json",
    }
    try:
        # No explicit keep_alive here on purpose — the operator has already
        # configured OLLAMA_KEEP_ALIVE=24h server-side on the target Ollama
        # instance for exactly this reason (keep a loaded model warm), and a
        # per-request override here would silently defeat that. This is now
        # the convention across every Ollama call site in this codebase, not
        # just this one. A cold first call after 24h of inactivity can still
        # spend well over 60s on the VRAM load before generating anything,
        # which is why the timeout below is generous — this call is
        # fire-and-forget background work and never blocks a user request.
        async with httpx.AsyncClient(timeout=180) as hc:
            r = await hc.post(
                f"{base}/api/chat", json=payload,
                headers={"Authorization": f"Bearer {cfg.get('token', '')}"},
            )
        r.raise_for_status()
        content = r.json().get("message", {}).get("content", "")
        parsed = json.loads(content)
        return {
            "premature": bool(parsed.get("premature")),
            "reasoning": str(parsed.get("reasoning", ""))[:500],
        }
    except Exception as e:
        logger.debug("Classifier LLM call failed (model=%s): %s", cfg.get("model"), e)
        return None


def _insert_unclassified_tool_ending_sync(row_id: str, chat_id: str, model: str,
                                           content: str, tool_msgs: int, now: str) -> None:
    dsn = MOE_USERDB_URL or POSTGRES_CHECKPOINT_URL or ""
    if not dsn:
        return
    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO admin_unclassified_tool_endings "
                    "(id, chat_id, model, content, content_chars, tool_msgs, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (row_id, chat_id or "", model or "", content, len(content), tool_msgs, now),
                )
            conn.commit()
    except Exception as e:
        logger.debug("Unclassified tool ending insert failed: %s", e)


def _update_tool_ending_classification_sync(row_id: str, verdict: dict) -> None:
    dsn = MOE_USERDB_URL or POSTGRES_CHECKPOINT_URL or ""
    if not dsn:
        return
    now = datetime.now(timezone.utc).isoformat()
    classification = "premature_stop" if verdict.get("premature") else "legitimate"
    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE admin_unclassified_tool_endings SET "
                    "classification=%s, classifier_reasoning=%s, classified_at=%s WHERE id=%s",
                    (classification, verdict.get("reasoning", ""), now, row_id),
                )
            conn.commit()
    except Exception as e:
        logger.debug("Unclassified tool ending classification update failed: %s", e)


async def record_and_classify_tool_ending(chat_id: str, model: str, content: str, tool_msgs: int) -> None:
    """Fire-and-forget entry point called from services/pipeline/chat.py at
    the existing "text-only ending, no retry triggered" diagnostic points —
    persists the ending to admin_unclassified_tool_endings (visible in the
    Admin UI review queue immediately) and asks the configured classifier LLM
    to judge it, updating the row once a verdict comes back. Two separate
    sync DB round-trips (insert, then update) so a slow/unreachable
    classifier never delays the insert. Never raises."""
    row_id = f"ute-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()
    await run_sync_daemon(
        _insert_unclassified_tool_ending_sync,
        row_id,
        chat_id,
        model,
        content,
        tool_msgs,
        now,
        timeout=5.0,
    )
    verdict = await _classify_tool_ending(content)
    if verdict is not None:
        await run_sync_daemon(
            _update_tool_ending_classification_sync,
            row_id,
            verdict,
            timeout=5.0,
        )


# ── Workspace / scope ─────────────────────────────────────────────────────────

_WORKSPACE_RE = re.compile(r"Working directory:\s*(\S+)", re.IGNORECASE)


def extract_workspace(system_text: str, messages=None) -> str:
    """Parse the CC/OpenCode workspace path out of the system prompt env block.

    Falls back to scanning the first user message (some clients embed the cwd
    there instead of in `system`), then to the literal "global" scope.
    """
    if system_text:
        m = _WORKSPACE_RE.search(system_text)
        if m:
            return m.group(1)
    for msg in list(messages or []):
        if _field(msg, "role") != "user":
            continue
        content = _field(msg, "content")
        text = content if isinstance(content, str) else ""
        if not text and isinstance(content, list):
            first_text_block = next(
                (b for b in content
                 if (b.get("type") if isinstance(b, dict) else getattr(b, "type", None)) == "text"),
                None,
            )
            if first_text_block is not None:
                text = first_text_block.get("text", "") if isinstance(first_text_block, dict) \
                    else getattr(first_text_block, "text", "")
        m = _WORKSPACE_RE.search(text or "")
        if m:
            return m.group(1)
    return "global"


def make_scope(user_id: str, workspace: str) -> str:
    """Cache/graph scope key: same user + same workspace share knowledge,
    different workspaces (projects) never leak into each other."""
    basis = f"{user_id or 'anon'}|{workspace or 'global'}"
    return hashlib.sha256(basis.encode()).hexdigest()[:16]


def _l0_key(scope: str, query: str) -> str:
    """L0 exact-match cache key — same normalization as graph/router_nodes.py's
    interactive cache_lookup_node, but in the agent-cache namespace."""
    normalized = re.sub(r"\s+", " ", query.lower().strip().rstrip("?!.,;"))
    q_hash = hashlib.sha256(normalized.encode()).hexdigest()[:24]
    return f"moe:agent:qcache:{scope}:{q_hash}"


# ── File-touch extraction (live-pipeline-visualization "which files did this
# session touch" view) ──────────────────────────────────────────────────────
# Tool schemas are client-defined (OpenCode, Claude Code CLI, ...) and not
# under our control, so classification is a conservative heuristic on the
# tool NAME only — good enough for a visualization, not meant to be exact.
_WRITE_TOOL_RE  = re.compile(r"write|edit|create|delete|remove|str_replace|patch|notebookedit", re.I)
_READ_TOOL_RE   = re.compile(r"read|view|cat|show", re.I)
_SEARCH_TOOL_RE = re.compile(r"grep|glob|search|find|^ls$|list", re.I)

# Common path-argument key names across Claude Code / OpenCode style tool
# schemas (Read/Write/Edit use file_path; NotebookEdit uses notebook_path;
# some clients use camelCase or a bare "path").
_PATH_ARG_KEYS = ("file_path", "path", "notebook_path", "filePath", "target_file", "filepath")


def classify_file_action(tool_name: str) -> str:
    """'write' | 'read' | 'search' | 'exec' | 'other' from the tool name alone."""
    name = tool_name or ""
    if _WRITE_TOOL_RE.search(name):
        return "write"
    if _READ_TOOL_RE.search(name):
        return "read"
    if _SEARCH_TOOL_RE.search(name):
        return "search"
    if re.search(r"bash|shell|exec|run|command", name, re.I):
        return "exec"
    return "other"


def extract_file_touches(tool_name: str, arguments) -> list[dict]:
    """Scan known path-argument keys in a tool call's arguments for file
    paths. Never raises — malformed/empty/non-dict arguments yield [].

    Deliberately conservative: only looks at a fixed set of known argument
    key names (see _PATH_ARG_KEYS), skips URLs (http/https — e.g. WebFetch's
    "url" arg is never one of the known keys anyway, but this guards any
    client that reuses "path" for a URL). Multiple matching keys on the same
    call (rare) all get recorded.
    """
    if not isinstance(arguments, dict) or not tool_name:
        return []
    action = classify_file_action(tool_name)
    touches = []
    for key in _PATH_ARG_KEYS:
        val = arguments.get(key)
        if isinstance(val, str) and val.strip() and not val.startswith(("http://", "https://")):
            touches.append({"path": val.strip(), "action": action, "tool": tool_name})
    return touches


def accumulate_stream_tool_call_delta(acc: dict, index: int, delta_fn: dict) -> None:
    """Merge one OpenAI-style streamed tool_calls delta fragment into acc
    (keyed by the delta's `index`) — id/name arrive once, arguments arrive
    as JSON-string fragments across many chunks and must be concatenated.

    Used by the fast/streaming passthrough path (services/pipeline/chat.py
    _stream_tool_synthesis), which forwards raw upstream SSE chunks to the
    client unchanged and only needs the fully-assembled tool_calls at the
    very end (after `data: [DONE]`) to extract file touches — never affects
    what's forwarded. Mutates acc in place; never raises.
    """
    try:
        entry = acc.setdefault(index, {"id": "", "name": "", "arguments": ""})
        if delta_fn.get("id"):
            entry["id"] = delta_fn["id"]
        fn = delta_fn.get("function") or {}
        if fn.get("name"):
            entry["name"] = fn["name"]
        if fn.get("arguments"):
            entry["arguments"] += fn["arguments"]
    except Exception:
        pass


def finalize_stream_tool_calls(acc: dict) -> list[dict]:
    """Convert the accumulator built by accumulate_stream_tool_call_delta into
    a list of {"name": ..., "arguments": <dict>} ready for
    extract_file_touches. Skips entries whose arguments never parsed as JSON
    (still-partial/malformed) rather than raising."""
    result = []
    for entry in acc.values():
        name = entry.get("name", "")
        if not name:
            continue
        try:
            args = json.loads(entry.get("arguments") or "{}")
        except Exception:
            continue
        result.append({"name": name, "arguments": args})
    return result


# ── Cache / GraphRAG / write-back ─────────────────────────────────────────────
# agent_graph_context (AP4) and agent_writeback (AP5) are fully implemented
# above. agent_cache_lookup remains a stub until AP6 (Cache-Lesepfad) — kept
# here so callers already wired up have a stable import surface; it is a safe
# no-op (never raises, never blocks) until then.

async def agent_cache_lookup(
    query: str, scope: str, redis_client, collection, path: str = "anthropic",
) -> str | None:
    """Look up a cached final answer for an informational initial-task turn.

    Callers must only call this when TurnInfo.cacheable is True (initial_task
    + is_informational) and the CC profile / expert template does not force
    tool_choice='required' — this function does not re-check either.

    L0 (Valkey exact-match) is checked first, then L1 (ChromaDB semantic,
    scoped to `scope` via a metadata filter — never crosses workspaces/users).
    The L1 gate mirrors the interactive knowledge-bypass in
    graph/router_nodes.py::cache_lookup_node: nearest-neighbour distance <
    KNOWLEDGE_BYPASS_THRESHOLD, stored confidence >= AGENT_CACHE_MIN_CONF,
    entry fresh within AGENT_CACHE_TTL_DAYS, and not flagged. Only the single
    nearest non-flagged neighbour is evaluated (same behaviour as the
    interactive path) — a near-miss on the closest entry is a miss, not a
    fall-through to the second-nearest.

    The whole lookup (both layers) is time-boxed to AGENT_CACHE_MAX_LOOKUP_MS;
    on timeout or any error this returns None (miss) — never raises, never
    blocks the tool-call fast path.
    """
    path_label = path if path in ("anthropic", "openai") else "anthropic"
    if not query or collection is None:
        PROM_AGENT_CACHE.labels(path=path_label, result="miss").inc()
        return None
    try:
        result = await asyncio.wait_for(
            _agent_cache_lookup_impl(query, scope, redis_client, collection),
            timeout=AGENT_CACHE_MAX_LOOKUP_MS / 1000,
        )
    except asyncio.TimeoutError:
        logger.debug("agent_cache_lookup: timed out after %d ms", AGENT_CACHE_MAX_LOOKUP_MS)
        PROM_AGENT_CACHE.labels(path=path_label, result="timeout").inc()
        return None
    except Exception as e:
        logger.debug("agent_cache_lookup: lookup failed: %s", e)
        PROM_AGENT_CACHE.labels(path=path_label, result="miss").inc()
        return None
    PROM_AGENT_CACHE.labels(path=path_label, result="hit" if result else "miss").inc()
    return result


async def _agent_cache_lookup_impl(query: str, scope: str, redis_client, collection) -> str | None:
    if redis_client:
        try:
            l0_hit = await redis_client.get(_l0_key(scope, query))
            if l0_hit:
                text = l0_hit if isinstance(l0_hit, str) else l0_hit.decode()
                if len(text) > 50:
                    return text
        except Exception as e:
            logger.debug("agent_cache_lookup: L0 check failed: %s", e)

    res = await run_sync_daemon(
        collection.query,
        query_texts=[query],
        n_results=3,
        where={"scope": scope},
        timeout=max(0.05, AGENT_CACHE_MAX_LOOKUP_MS / 1000),
    )
    docs = (res.get("documents") or [[]])[0]
    if not docs:
        return None
    dists = (res.get("distances") or [[1.0] * len(docs)])[0]
    metas = (res.get("metadatas") or [[{}] * len(docs)])[0]
    for doc, dist, meta in zip(docs, dists, metas):
        if meta.get("flagged"):
            continue
        if (
            dist < KNOWLEDGE_BYPASS_THRESHOLD
            and float(meta.get("confidence", 0.0) or 0.0) >= AGENT_CACHE_MIN_CONF
            and _entry_is_fresh(meta.get("ts", ""), AGENT_CACHE_TTL_DAYS)
        ):
            return doc
        break  # only the nearest non-flagged neighbour is considered — matches
               # the interactive cache_lookup_node behaviour.
    return None


async def agent_graph_context(
    query: str, tenant_ids, session_id: str, redis_client, graph_manager,
    max_chars: int, timeout_s: float, user_id: str = "",
) -> str:
    """Fetch (or reuse cached) GraphRAG context for injection into the tool
    model's system prompt.

    Replaces the inline Tier-4 block previously duplicated in
    services/pipeline/anthropic.py, fixing three gaps in the original:
    tenant_ids is honoured (was always None), the L2 Valkey cache key is
    scoped by tenant (was query-only — cross-tenant leak), and the result is
    capped to max_chars (the caller is expected to pass
    min(AGENT_GRAPHRAG_MAX_CHARS, graphrag_budget_chars(tool_model, ...))).

    Only called on initial_task turns by the caller — mid_loop turns should
    instead re-read the per-session cc:graphctx:{session_id} Redis key that
    the caller writes after a successful call here, at zero Neo4j/L2 cost.
    Never raises; times out to "" so the tool path degrades to plain
    passthrough.
    """
    if not query or graph_manager is None:
        return ""

    tenant_key = ",".join(sorted(tenant_ids or []))
    cache_key = (
        "moe:graph:cc:"
        # user_id is part of the key (not just tenant_ids) because the cached
        # value now also carries the caller's episode hint (see _fetch below),
        # which is scoped per-user — without this, one user's routing-history
        # hint could be served back to a different user sharing the same
        # tenant_ids scope on an L2 cache hit.
        + hashlib.sha256(f"{tenant_key}|{user_id}|{query[:200]}".encode()).hexdigest()[:16]
    )

    if redis_client:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                text = cached if isinstance(cached, str) else cached.decode()
                return text[:max_chars] if max_chars > 0 else text
        except Exception as e:
            logger.debug("agent_graph_context: L2 cache read failed: %s", e)

    async def _fetch() -> str:
        ctx = await graph_manager.query_context(query[:500], ["general"], tenant_ids=tenant_ids) or ""
        try:
            from episodic_memory import get_episode_hint as _get_ep_hint
            hint = await _get_ep_hint(graph_manager.driver, query[:500], "general", user_id)
            if hint:
                ctx = (ctx + "\n\n" + hint) if ctx else hint
        except Exception as e:
            logger.debug("agent_graph_context: episode hint skipped: %s", e)
        return ctx

    try:
        ctx = await asyncio.wait_for(_fetch(), timeout=timeout_s)
    except Exception as e:
        logger.warning("agent_graph_context: query_context failed/timed out: %s", e)
        return ""

    if ctx and redis_client:
        try:
            await asyncio.wait_for(
                redis_client.setex(cache_key, 3600, ctx), timeout=1.0
            )
        except Exception:
            pass

    if max_chars > 0 and len(ctx) > max_chars:
        ctx = ctx[:max_chars]
    return ctx


async def agent_writeback(
    query: str, answer: str, scope: str, tenant_id: str, user_id: str,
    source_model: str, session_id: str, redis_client, collection,
    path: str = "anthropic", api_key_id: str = "",
) -> None:
    """Fire-and-forget write of a clean final agent answer into the agent
    cache (moe_agent_cache) and the Kafka ingestion topic.

    Callers must only invoke this for a genuinely clean session end (no
    stream error, no is_error tool_result, no tool_use/tool_calls in the
    final answer — see classify_turn) — this function itself does not
    re-derive that; it only re-checks the cheap length gate.

    Writes at initial confidence 0.6 — deliberately below AGENT_CACHE_MIN_CONF
    (0.85, see config.py), so a fresh answer is never immediately servable
    from the cache. If AGENT_INGEST_JUDGE is on, an async judge call scores
    the answer and either promotes the confidence to 0.9 (and writes the L0
    exact-match key, unlocking cache-serving) or flags the entry as bad.

    Never raises — every step is independently wrapped so a Kafka/Chroma/judge
    failure cannot affect the response already sent to the client. Always
    returns None; call via asyncio.create_task() for zero added latency.
    """
    path_label = path if path in ("anthropic", "openai") else "anthropic"
    if not query or not answer or len(answer) <= CACHE_MIN_RESPONSE_LEN or collection is None:
        PROM_AGENT_WRITEBACK.labels(path=path_label, result="skipped").inc()
        return None

    doc_id = hashlib.sha256(answer.encode()).hexdigest()[:32]
    metadata = {
        "scope": scope,
        "ts": datetime.now().isoformat(),
        "input": query[:500],
        "confidence": _AGENT_CONFIDENCE_INITIAL,
        "flagged": False,
        "session_id": session_id or "",
    }
    try:
        await run_sync_daemon(
            collection.upsert,
            ids=[doc_id],
            documents=[answer],
            metadatas=[metadata],
            timeout=5.0,
        )
    except Exception as e:
        logger.warning("agent_writeback: L1 upsert failed: %s", e)
        PROM_AGENT_WRITEBACK.labels(path=path_label, result="error").inc()
        return None

    knowledge_type = "procedural" if any(kw in answer for kw in _PROC_MARKERS) else "factual"
    try:
        await _kafka_publish(KAFKA_TOPIC_INGEST, {
            "response_id":       session_id or "",
            "input":             query,
            "answer":            answer,
            "domain":            "technical_support",
            "source_expert":     "agent_session",
            "source_model":      source_model,
            "template_name":     "",
            "confidence":        _AGENT_CONFIDENCE_INITIAL,
            "knowledge_type":    knowledge_type,
            "synthesis_insight": None,
            "tenant_id":         tenant_id,
        })
    except Exception as e:
        logger.warning("agent_writeback: Kafka publish failed: %s", e)

    PROM_AGENT_WRITEBACK.labels(path=path_label, result="written").inc()

    if AGENT_INGEST_JUDGE:
        asyncio.create_task(
            _agent_judge_promote(query, answer, doc_id, metadata, scope, redis_client, collection,
                                  user_id=user_id, api_key_id=api_key_id)
        )
    return None


async def _agent_judge_promote(
    query: str, answer: str, doc_id: str, metadata: dict, scope: str,
    redis_client, collection,
    user_id: str = "anon", api_key_id: str = "",
) -> None:
    """Async, fire-and-forget: judge-scores a freshly written agent answer and
    promotes its confidence (unlocking cache-serving) or flags it as bad.

    No session/response_id in the interactive pipeline's tracking tables is
    touched here — this is a self-contained scoring pass, deliberately not
    reusing services/helpers.py::_self_evaluate (which writes to
    moe:response:{response_id} state that belongs to the merger/judge
    pipeline, not agent tool-path sessions).

    Registered in live monitoring under its own synthetic chat_id: this call
    POSTs directly to the judge model's node and, on VRAM-constrained nodes
    (e.g. N04-RTX, which also hosts the tool/expert models), can evict the
    model the client just used — previously invisible in the admin "Laufende
    API-Anfragen" table because this fire-and-forget task never registered,
    so a model vanishing right after a request looked like it had no cause.
    """
    _judge_chat_id = f"agent-judge-{uuid.uuid4().hex[:12]}"
    await _register_active_request(
        chat_id=_judge_chat_id, user_id=user_id, model=JUDGE_MODEL,
        moe_mode="agent_judge_promote", req_type="background",
        api_key_id=api_key_id,
    )
    try:
        from services.inference import ainvoke_judge_llm
        eval_prompt = (
            "Rate the following answer on a scale of 1-5.\n"
            "1=incomplete/wrong, 3=adequate, 5=complete/correct\n\n"
            f"QUESTION: {query[:200]}\n\n"
            f"ANSWER: {answer[:600]}\n\n"
            "Reply ONLY with: SELF_RATING: N"
        )
        eval_res = await ainvoke_judge_llm(eval_prompt)
        m = re.search(r"SELF_RATING:\s*([1-5])", eval_res.content)
        score = int(m.group(1)) if m else 3
    except Exception as e:
        logger.debug("agent_writeback: judge scoring failed: %s", e)
        return None
    finally:
        await _deregister_active_request(_judge_chat_id)

    try:
        if score >= _JUDGE_PROMOTE_SCORE_MIN:
            promoted = {**metadata, "confidence": _AGENT_CONFIDENCE_PROMOTED}
            await run_sync_daemon(
                collection.upsert,
                ids=[doc_id],
                documents=[answer],
                metadatas=[promoted],
                timeout=5.0,
            )
            if redis_client:
                await redis_client.setex(_l0_key(scope, query), AGENT_CACHE_L0_TTL, answer)
        elif score <= _JUDGE_FLAG_SCORE_MAX:
            flagged = {**metadata, "flagged": True}
            await run_sync_daemon(
                collection.upsert,
                ids=[doc_id],
                documents=[answer],
                metadatas=[flagged],
                timeout=5.0,
            )
    except Exception as e:
        logger.warning("agent_writeback: judge promotion write failed: %s", e)
    return None
