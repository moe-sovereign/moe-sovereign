"""Reuse the context window an Ollama model is already loaded with.

A request whose ``num_ctx`` differs from the loaded runner's makes Ollama unload and reload the model. For the 32B
judge that is ~110 s per call (measured 2026-09-20: 111 s of 145 s), and it happened on every alternation between the
merger call (template ``judge_num_ctx``) and a native ``model@node`` call without ``num_ctx`` (the benchmark's own judge
evaluation). Callers without an explicit context ask the running instance and reuse what is loaded.
"""
from __future__ import annotations

from typing import Optional

import httpx


def pick_warm_ctx(ps_payload: dict, model: str) -> int:
    """Loaded context length of ``model`` from an ``/api/ps`` payload, 0 when it is not loaded."""
    for entry in (ps_payload or {}).get("models", []):
        if model in (entry.get("name"), entry.get("model")):
            try:
                return int(entry.get("context_length") or 0)
            except (TypeError, ValueError):
                return 0
    return 0


async def loaded_ctx(base_url: str, token: Optional[str], model: str, timeout: float = 2.0) -> int:
    """Ask the instance which context the model is loaded with; 0 on any failure (callers then keep their default)."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(base_url.rstrip("/") + "/api/ps", headers={"Authorization": f"Bearer {token or 'ollama'}"})
            resp.raise_for_status()
            return pick_warm_ctx(resp.json(), model)
    except Exception:
        return 0
