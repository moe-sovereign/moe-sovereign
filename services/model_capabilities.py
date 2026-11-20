"""services/model_capabilities.py — Per-model capability matrix (TASK-31).

Loads configs/model_capabilities.yaml once (or per-request in dev mode) and
exposes typed getters for inference.py to make capability-aware call decisions.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
logger = logging.getLogger("MOE-SOVEREIGN")

_DEFAULT_CAPS: dict = {
    "json_schema":         False,
    "json_object":         True,
    "stream":              True,
    "native_tool_calling": True,
}

_CAPS_PATH = Path(__file__).parent.parent / "configs" / "model_capabilities.yaml"
_RELOAD_ON_REQUEST = os.getenv("CAPABILITIES_RELOAD_ON_REQUEST", "false").lower() == "true"

_cache: dict | None = None


def load_capabilities(path: str | Path | None = None) -> dict:
    """Load (or reload) the capability YAML. Cached after first load unless
    CAPABILITIES_RELOAD_ON_REQUEST=true."""
    global _cache
    if _cache is not None and not _RELOAD_ON_REQUEST:
        return _cache
    _p = Path(path) if path else _CAPS_PATH
    try:
        import yaml  # PyYAML, available in requirements.txt
        with _p.open() as f:
            raw = yaml.safe_load(f)
        _cache = raw or {}
    except Exception as exc:
        logger.warning("model_capabilities: failed to load %s: %s — using defaults", _p, exc)
        _cache = {}
    return _cache


def get_model_caps(model: str) -> dict:
    """Return capability dict for *model*, falling back to 'default' block."""
    caps = load_capabilities()
    models_section = caps.get("models") or {}
    default = {**_DEFAULT_CAPS, **(caps.get("default") or {})}
    entry = models_section.get(model)
    if entry is None:
        return default
    return {**default, **entry}


def model_supports_json_schema(model: str) -> bool:
    """True if the model honours response_format={"type":"json_schema",...}."""
    return bool(get_model_caps(model).get("json_schema", False))


def model_supports_json_object(model: str) -> bool:
    """True if the model honours response_format={"type":"json_object"}."""
    return bool(get_model_caps(model).get("json_object", True))


def get_knowledge_cutoff(model: str):
    """The model's training-data cutoff as a date (first day of the month), or None when it is not documented.

    YAML field ``knowledge_cutoff: "YYYY-MM"`` per model. Only enter values a model card or the vendor states; a guess here makes
    the reconciliation prefer infrastructure facts for the wrong reasons."""
    import datetime as _dt
    raw = str(get_model_caps(model).get("knowledge_cutoff") or "").strip()
    try:
        return _dt.datetime.strptime(raw, "%Y-%m").date()
    except ValueError:
        return None


def model_supports_streaming(model: str) -> bool:
    """True if the model can stream tokens."""
    return bool(get_model_caps(model).get("stream", True))


def model_supports_native_tool_calling(model: str) -> bool:
    """True if the model's Ollama `tools` param can be trusted not to crash
    grammar/sampler initialization (some models fail with 400 "failed to
    parse grammar" on the larger/nested Claude Code tool schemas)."""
    return bool(get_model_caps(model).get("native_tool_calling", True))


def enforce_streaming_capability(model: str, requested: bool) -> bool:
    """Return the wire-level stream flag allowed by the selected model."""
    return bool(requested and model_supports_streaming(model))


def apply_ollama_structured_capability(
    payload: dict,
    model: str,
    schema: dict | None = None,
) -> dict:
    """Apply the strongest supported Ollama ``format`` contract.

    A copy is returned so callers can safely reuse their base payload.
    """
    result = dict(payload)
    result["stream"] = enforce_streaming_capability(
        model, bool(result.get("stream", False))
    )
    if schema and model_supports_json_schema(model):
        result["format"] = schema
    elif schema and model_supports_json_object(model):
        result["format"] = "json"
    else:
        result.pop("format", None)
    return result


def openai_response_format(model: str, schema: dict | None = None, *, strict: bool = True) -> dict | None:
    """Return a provider-neutral OpenAI response_format or ``None``.

    ``strict=False`` for schemas with open objects (the planner's ``mcp_args``): OpenAI's strict mode rejects them, vLLM and
    Ollama do not care."""
    if schema and model_supports_json_schema(model):
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "moe_structured_output",
                "strict": strict,
                "schema": schema,
            },
        }
    if model_supports_json_object(model):
        return {"type": "json_object"}
    return None
