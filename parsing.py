"""
parsing.py — Stateless parsing helpers for MoE Sovereign.

All functions in this module are pure (no side effects) or accept optional
Prometheus counter callbacks so they remain testable without the full app context.

Public API:
    _extract_usage          — token counts from LangChain AIMessage
    _extract_json           — robust JSON extraction from LLM output
    _parse_expert_confidence — confidence level from structured expert output
    _parse_expert_gaps      — gaps and referrals from expert output
    _expert_category        — category tag from expert result header
    _dedup_by_category      — keep highest-confidence result per category
    _truncate_history       — prune conversation history by turns / chars
    _improvement_ratio      — text change rate between two strings (0..1)
    _oai_content_to_str     — flatten OpenAI content to plain text
    _anthropic_content_to_text — flatten Anthropic content to plain text
    _extract_images         — base64 image blocks from Anthropic content
    _extract_oai_images     — base64 image blocks from OpenAI content
    _anthropic_to_openai_messages — convert Anthropic messages → OpenAI format
    _anthropic_tools_to_openai    — convert Anthropic tool schemas → OpenAI format
"""

from __future__ import annotations

import json
import os
import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

from context_compressor import compress_message_content


# ── Token accounting ───────────────────────────────────────────────────────────

def _extract_usage(res: Any) -> Dict[str, int]:
    """Extract prompt_tokens + completion_tokens from a LangChain AIMessage.

    Ollama provides usage_metadata; the fallback reads response_metadata
    ['token_usage'] which is the OpenAI-compatible field.

    Args:
        res: A LangChain AIMessage (or any object with usage_metadata /
             response_metadata attributes).

    Returns:
        Dict with keys 'prompt_tokens' and 'completion_tokens' (both int ≥ 0).
    """
    meta = getattr(res, "usage_metadata", None)
    if meta:
        return {
            "prompt_tokens":     int(meta.get("input_tokens",  0)),
            "completion_tokens": int(meta.get("output_tokens", 0)),
        }
    token_usage = getattr(res, "response_metadata", {}).get("token_usage", {})
    return {
        "prompt_tokens":     int(token_usage.get("prompt_tokens",     0)),
        "completion_tokens": int(token_usage.get("completion_tokens", 0)),
    }


# ── JSON extraction ────────────────────────────────────────────────────────────

def _extract_json(text: str) -> "dict | list | None":
    """Robustly extract JSON from LLM output — handles markdown fences and mixed text.

    Args:
        text: Raw LLM output string, possibly wrapped in triple-backtick fences.

    Returns:
        Parsed Python object (dict or list), or None on failure.
    """
    text = text.strip()
    if "```" in text:
        text = text.split("```")[1].lstrip("json").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r'(\{.*\}|\[.*\])', text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return None


# ── Expert output parsing ──────────────────────────────────────────────────────

def _parse_expert_confidence(result: str) -> str:
    """Extracts CONFIDENCE from structured expert output.

    Structural guard: empty, very short, or error-prefix responses are treated
    as low-confidence so research_fallback_node triggers automatically.

    Args:
        result: Raw expert response string.

    Returns:
        'high' | 'medium' | 'low'
    """
    if not result or len(result.strip()) < 40:
        return "low"
    lower = result.lower().lstrip()
    if lower.startswith(("[error", "error:", "none", "n/a", "null")):
        return "low"
    # Catch run_single() error format: "[model-name ERROR]: <message>"
    if re.search(r'^\[\S.*?\s+error\]:', lower[:120]):
        return "low"
    m = re.search(r'CONFIDENCE:\s*(high|medium|low)', result, re.I)
    return m.group(1).lower() if m else "medium"


def _parse_expert_gaps(result: str) -> tuple[str, str]:
    """Extracts GAPS and REFERRAL from structured expert output.

    Args:
        result: Raw expert response string.

    Returns:
        Tuple of (gaps, referral) — each empty string if not present.
    """
    luecken = ""
    verweis = ""
    m = re.search(r'GAPS:\s*([^\n·]+)', result, re.I)
    if m:
        val = m.group(1).strip().rstrip("·").strip()
        if val.lower() not in ("none", "—", "-", ""):
            luecken = val
    m = re.search(r'REFERRAL:\s*([^\n·]+)', result, re.I)
    if m:
        val = m.group(1).strip()
        if val not in ("—", "-", ""):
            verweis = val
    return luecken, verweis


def _expert_category(result: str) -> str:
    """Extracts category from expert result header '[MODEL / category]:'.

    Args:
        result: Raw expert response string.

    Returns:
        Lowercase category string, or '' if the header is absent.
    """
    m = re.search(r'/\s*(\w+)\]:', result)
    return m.group(1).lower() if m else ""


def _compute_routing_confidence(
    plan: List[Dict],
    complexity_level: str,
    enable_graphrag: bool,
) -> tuple[float, float]:
    """Derive fuzzy routing confidence scores from the planner's output.

    Returns independent confidence scores for vector search (web/semantic)
    and knowledge-graph retrieval, suitable as inputs to t-norm conjunction
    functions (Gödel or Łukasiewicz) for programmatic routing decisions.

    Mathematical foundation:
        Fuzzy logics as the most general framework in the algebraic hierarchy —
        de Vries (2007), arXiv:0707.2161. Confidence values are truth degrees
        in [0.0, 1.0] rather than binary on/off flags. T-norm conjunction then
        determines whether a retrieval node is activated.

    Gödel t-norm (min) and Łukasiewicz t-norm discussed as special cases in
    de Vries (2007), §4; originally: Gödel (1932), Łukasiewicz (1920).

    Args:
        plan:            Planner output — list of task dicts.
        complexity_level: 'trivial' | 'moderate' | 'complex' | 'memory_recall'
        enable_graphrag: Current graphrag toggle from template config.

    Returns:
        Tuple (vector_confidence, graph_confidence), each in [0.0, 1.0].
    """
    _GRAPH_HEAVY_CATS = {"legal_advisor", "medical_consult", "science", "reasoning"}
    # GRAPH_ROUTER_EXTRA_CATEGORIES="compounding_knowledge+graph_query" (also comma): planner categories that mean "look it up in the graph".
    # Off by default. With the defaults a plain lookup plan scores graph confidence 0.30, just below FUZZY_GRAPH_THRESHOLD
    # (0.35), so the product router never queried the graph in the ablation (retrieved share 0.00 in 480 lookup episodes).
    _GRAPH_HEAVY_CATS |= {c.strip() for c in os.getenv("GRAPH_ROUTER_EXTRA_CATEGORIES", "").replace("+", ",").split(",") if c.strip()}
    _RESEARCH_CATS    = {"general", "research", "technical_support", "translation"}

    # Base scores from complexity level
    _complexity_vector: Dict[str, float] = {
        "trivial":       0.1,
        "memory_recall": 0.0,
        "moderate":      0.5,
        "complex":       0.9,
    }
    _complexity_graph: Dict[str, float] = {
        "trivial":       0.1,
        "memory_recall": 0.0,
        "moderate":      0.4,
        "complex":       0.7,
    }

    base_vector = _complexity_vector.get(complexity_level, 0.5)
    base_graph  = _complexity_graph.get(complexity_level, 0.4)

    if not plan:
        return base_vector, base_graph

    # Signal from plan content
    categories   = [t.get("category", "") for t in plan]
    has_search   = any(t.get("search_query") for t in plan)
    n_graph_cats = sum(1 for c in categories if c in _GRAPH_HEAVY_CATS)
    n_res_cats   = sum(1 for c in categories if c in _RESEARCH_CATS)
    n_tasks      = max(len(plan), 1)

    vector_signal = (0.4 if has_search else 0.0) + (n_res_cats / n_tasks) * 0.6
    graph_signal  = (n_graph_cats / n_tasks) * 0.8 + (0.2 if enable_graphrag else 0.0)

    vector_conf = min(1.0, (base_vector + vector_signal) / 2)
    graph_conf  = min(1.0, (base_graph  + graph_signal)  / 2)

    return round(vector_conf, 3), round(graph_conf, 3)


def _collect_conflicts(raw_results: List[str], divergence_threshold: float = 0.35) -> List[Dict]:
    """Detect and record paraconsistent conflicts between expert outputs.

    When two experts in the same category produce significantly different
    answers, classical logic would overwrite one. Paraconsistent logic
    tolerates the contradiction: both propositions are recorded in the
    conflict registry without either being discarded.

    Mathematical basis:
        de Vries (2007), arXiv:0707.2161, Section 2 — paraconsistent logics
        reject the principle of explosion (ex contradictione quodlibet).
        Contradictions A ∧ ¬A do not imply every formula; instead they are
        preserved as structured entries for explicit resolution.

    The Gödel t-norm (minimum) and Łukasiewicz t-norm mentioned as routing
    primitives are classical results (Gödel 1932; Łukasiewicz 1920) that
    de Vries 2007 discusses as special cases of the algebraic hierarchy.

    Args:
        raw_results:          All expert results before deduplication.
        divergence_threshold: Minimum _improvement_ratio score to flag as a
                              conflict. 0.35 = texts diverge by >35%.

    Returns:
        List of ConflictEntry-compatible dicts (category, proposition_a,
        proposition_b, divergence_score, resolution, resolved_by).
    """
    by_category: Dict[str, List[str]] = {}
    for r in raw_results:
        cat = _expert_category(r)
        if cat:
            by_category.setdefault(cat, []).append(r)

    conflicts: List[Dict] = []
    for cat, results in by_category.items():
        if len(results) < 2:
            continue
        for i in range(len(results)):
            for j in range(i + 1, len(results)):
                score = _improvement_ratio(results[i], results[j])
                if score >= divergence_threshold:
                    conflicts.append({
                        "category":         cat,
                        "proposition_a":    results[i][:600],
                        "proposition_b":    results[j][:600],
                        "divergence_score": round(score, 3),
                        "resolution":       "pending",
                        "resolved_by":      "",
                    })
    return conflicts


def _dedup_by_category(expert_results: List[str]) -> List[str]:
    """Keeps only the result with highest confidence per category.

    Results without a parseable category are included unconditionally.

    Args:
        expert_results: List of raw expert response strings.

    Returns:
        Deduplicated list — at most one result per category (highest confidence).
    """
    _CONF_RANK = {"high": 2, "medium": 1, "low": 0}
    best: Dict[str, tuple] = {}
    no_cat: List[str] = []
    for r in expert_results:
        cat = _expert_category(r)
        if not cat:
            no_cat.append(r)
            continue
        rank = _CONF_RANK.get(_parse_expert_confidence(r), 1)
        if cat not in best or rank > best[cat][0]:
            best[cat] = (rank, r)
    return [v[1] for v in best.values()] + no_cat


# ── History truncation ─────────────────────────────────────────────────────────

def _truncate_history(
    messages: List[Dict],
    max_turns: Optional[int] = None,
    max_chars: Optional[int] = None,
    *,
    default_max_turns: int = 4,
    default_max_chars: int = 3000,
    prom_unlimited: Any = None,
    prom_compressed: Any = None,
) -> List[Dict]:
    """Truncates conversation history to the last N rounds and max. character count.

    Older assistant answers are compressed to '[…]' instead of hard-truncated,
    so that user questions are preserved as conversation context.

    Per-template overrides: 0 = use global default, -1 = unlimited (no truncation).

    Args:
        messages: Full conversation history as list of {role, content} dicts.
        max_turns: Maximum rounds to keep (None → use default_max_turns).
        max_chars: Maximum total chars to keep (None → use default_max_chars).
        default_max_turns: Fallback when max_turns is None (caller provides env-var value).
        default_max_chars: Fallback when max_chars is None (caller provides env-var value).
        prom_unlimited: Optional Prometheus Counter for unlimited-history events.
        prom_compressed: Optional Prometheus Counter for compression events.

    Returns:
        Pruned list of {role, content} dicts.
    """
    max_turns = max_turns if max_turns is not None else default_max_turns
    max_chars = max_chars if max_chars is not None else default_max_chars
    history = [m for m in messages if m.get("role") in ("user", "assistant")]
    if max_turns == -1 and max_chars == -1:
        if prom_unlimited is not None:
            prom_unlimited.inc()
        return list(history)
    if max_turns > 0:
        history = history[-(max_turns * 2):]

    if max_chars == -1:
        return list(history)

    # Compress older assistant answers when total history length > 2/3 of the limit.
    # Strategy: compress content first (content-aware), collapse to '[…]' only if
    # it still doesn't fit — preserves more information than immediate erasure.
    _total_chars = sum(len(m["content"]) for m in history)
    if _total_chars > max_chars * 2 // 3:
        if prom_compressed is not None:
            prom_compressed.inc()
        compressed = []
        _old_assistant_count = sum(
            1 for i, m in enumerate(history)
            if m["role"] == "assistant" and i < len(history) - 2
        )
        _per_msg_budget = max(120, max_chars // max(_old_assistant_count, 1))
        for i, msg in enumerate(history):
            if msg["role"] == "assistant" and i < len(history) - 2:
                raw = msg["content"]
                if isinstance(raw, str):
                    shrunk = compress_message_content(raw, _per_msg_budget)
                    compressed.append({"role": "assistant", "content": shrunk if shrunk else "[…]"})
                else:
                    # Multimodal content (list of blocks) — collapse to placeholder
                    compressed.append({"role": "assistant", "content": "[…]"})
            else:
                compressed.append(msg)
        history = compressed

    total = 0
    result: List[Dict] = []
    for msg in reversed(history):
        content = msg["content"]
        if total + len(content) > max_chars:
            remaining = max_chars - total
            if remaining > 100:
                result.insert(0, {"role": msg["role"], "content": content[:remaining] + "…"})
            break
        result.insert(0, {"role": msg["role"], "content": content})
        total += len(content)
    return result


# ── Text change rate ───────────────────────────────────────────────────────────

def _improvement_ratio(old: str, new: str) -> float:
    """Returns 0..1: 0 = identical, 1 = completely different (text change rate).

    Used by the critic node to decide whether a judge refinement was substantial
    enough to replace the original merger output.

    Args:
        old: Original text.
        new: Revised text.

    Returns:
        Float in [0.0, 1.0].
    """
    return 1.0 - SequenceMatcher(None, old, new).ratio()


# ── Content serialisation ──────────────────────────────────────────────────────

def _oai_content_to_str(content: Any) -> str:
    """Extract plain text from OpenAI-format content (str or list of content parts).

    Args:
        content: OpenAI message content — str, list of {type, text} dicts, or None.

    Returns:
        Concatenated plain text string.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
        return " ".join(parts)
    return str(content)


def _anthropic_content_to_text(content: Any) -> str:
    """Extract plain text from Anthropic content (str or content block list).

    Image blocks are annotated as '[Bild-Eingabe vorhanden]' so the planner
    recognises that vision input is present without receiving raw base64.

    Args:
        content: Anthropic message content.

    Returns:
        Concatenated plain text string.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        has_image = False
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif b.get("type") == "image":
                has_image = True
        if has_image:
            parts.append("[Bild-Eingabe vorhanden]")
        return " ".join(parts)
    return str(content) if content else ""


def _extract_images(content: Any) -> List[Dict]:
    """Extract base64 image blocks from Anthropic content for the vision expert.

    Args:
        content: Anthropic message content (list of content blocks).

    Returns:
        List of {media_type, data} dicts; empty list if no images are present.
    """
    if not isinstance(content, list):
        return []
    images = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "image":
            source = block.get("source", {})
            if source.get("type") == "base64":
                images.append({
                    "media_type": source.get("media_type", "image/jpeg"),
                    "data": source.get("data", ""),
                })
    return images


def _extract_oai_images(content: Any) -> List[Dict]:
    """Extract images from OpenAI-format content (image_url with data-URI).

    Returns list of {media_type, data} dicts — same structure as _extract_images
    so callers handle both content formats uniformly.

    Args:
        content: OpenAI message content (list of content blocks).

    Returns:
        List of {media_type, data} dicts; empty list if no images are present.
    """
    if not isinstance(content, list):
        return []
    images = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "image_url":
            continue
        url = (block.get("image_url") or {}).get("url", "")
        if url.startswith("data:"):
            # data:<media_type>;base64,<data>
            try:
                header, data = url.split(",", 1)
                media_type = header.split(":")[1].split(";")[0]
                images.append({"media_type": media_type, "data": data})
            except Exception:
                pass
    return images


# ── Format conversion ──────────────────────────────────────────────────────────

def _anthropic_to_openai_messages(messages: list, system: Optional[str]) -> list:
    """Convert Anthropic message list → OpenAI format.

    Handles text, tool_use, and tool_result blocks. Image blocks are converted
    to OpenAI image_url format with data-URIs.

    Args:
        messages: List of Anthropic message dicts ({role, content}).
        system: Optional system prompt — prepended as {"role": "system"} if set.

    Returns:
        OpenAI-compatible message list.
    """
    result: list = []
    if system:
        # Normalize Anthropic list-style system (content blocks with cache_control) to string.
        if isinstance(system, list):
            system = "\n".join(
                b.get("text", "") for b in system
                if isinstance(b, dict) and b.get("type") == "text"
            )
        if system:
            result.append({"role": "system", "content": system})

    def _merge_system(text: str) -> None:
        """Merge extra system-role content into the single leading system
        message instead of appending a second one. Claude Code's own
        `messages` array can itself carry a mid-conversation entry with
        role "system" (e.g. a system-reminder) alongside the top-level
        `system` field — forwarding it as a second role:system message
        causes providers that validate for exactly one system entry at
        position 0 (observed: Hetzner) to reject the request with HTTP 400
        ("System message must be at the beginning")."""
        if not text:
            return
        if result and result[0].get("role") == "system":
            result[0] = {**result[0], "content": (result[0].get("content") or "") + "\n\n" + text}
        else:
            result.insert(0, {"role": "system", "content": text})

    for msg in messages:
        role    = msg.get("role", "user")
        content = msg.get("content", "")
        if role == "system":
            if isinstance(content, list):
                content = "\n".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            elif not isinstance(content, str):
                content = str(content) if content else ""
            _merge_system(content)
            continue
        if isinstance(content, str):
            result.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            result.append({"role": role, "content": str(content)})
            continue
        tool_calls   = [b for b in content if b.get("type") == "tool_use"]
        tool_results = [b for b in content if b.get("type") == "tool_result"]
        text_blocks  = [b for b in content if b.get("type") == "text"]
        if tool_calls:
            text_part = " ".join(b.get("text", "") for b in text_blocks) or None
            oai_calls = [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc.get("input", {}))
                    }
                }
                for tc in tool_calls
            ]
            # OpenAI spec allows content=null with tool_calls, but some LiteLLM-backed
            # providers reject null and require an empty string instead.
            result.append({"role": "assistant", "content": text_part or "", "tool_calls": oai_calls})
        elif tool_results:
            for tr in tool_results:
                tr_content = tr.get("content", "")
                if isinstance(tr_content, list):
                    tr_content = " ".join(
                        b.get("text", "") for b in tr_content if b.get("type") == "text"
                    )
                result.append({
                    "role": "tool",
                    "tool_call_id": tr.get("tool_use_id", ""),
                    "content": tr_content or ""
                })
        else:
            image_blocks = [b for b in content if b.get("type") == "image"]
            parts: List[Dict] = []
            for b in text_blocks:
                parts.append({"type": "text", "text": b.get("text", "")})
            for b in image_blocks:
                src = b.get("source", {})
                if src.get("type") == "base64":
                    parts.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{src.get('media_type', 'image/jpeg')};base64,{src.get('data', '')}"
                        }
                    })
            if len(parts) == 1 and parts[0]["type"] == "text":
                result.append({"role": role, "content": parts[0]["text"]})
            elif parts:
                result.append({"role": role, "content": parts})
            else:
                result.append({"role": role, "content": ""})
    return result


_UNSUPPORTED_SCHEMA_KEYS = frozenset({"propertyNames", "if", "then", "else", "not", "contains", "unevaluatedProperties", "unevaluatedItems"})


def _strip_unsupported_schema_keys(schema: object) -> object:
    """Recursively remove JSON Schema keys unsupported by llama.cpp grammar parsers."""
    if isinstance(schema, list):
        return [_strip_unsupported_schema_keys(i) for i in schema]
    if not isinstance(schema, dict):
        return schema
    return {
        k: _strip_unsupported_schema_keys(v)
        for k, v in schema.items()
        if k not in _UNSUPPORTED_SCHEMA_KEYS
    }


def _anthropic_tools_to_openai(tools: list) -> list:
    """Convert Anthropic tool schemas → OpenAI function calling format.

    The only difference is the key name: Anthropic uses 'input_schema',
    OpenAI uses 'parameters'. The schema content is identical, except
    that JSON Schema keys unsupported by llama.cpp grammar parsers
    (e.g. propertyNames) are stripped to avoid HTTP 400 errors.

    Args:
        tools: List of Anthropic tool definition dicts.

    Returns:
        List of OpenAI function definition dicts.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": _strip_unsupported_schema_keys(
                    t.get("input_schema", {"type": "object", "properties": {}})
                )
            }
        }
        for t in tools
    ]
