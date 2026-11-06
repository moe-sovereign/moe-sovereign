"""Research-only checkpoint history and fork routes for counterfactual orchestration rollouts.

Disabled unless RESEARCH_ROUTES_ENABLED is set; even then every route requires SYSTEM_API_KEY
or an admin key. A rollout driver sends a normal /v1/chat/completions request with the
`X-MoE-Research-Thread` header, reads the checkpoint history of that thread, and resumes the
graph from an earlier checkpoint. LangGraph records the resumed run as a new branch of the
same thread, so every branch keeps its full provenance.
"""
import asyncio
import json
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import state
from config import ORCHESTRATION_TIMEOUT, RESEARCH_ROUTES_ENABLED
from services.auth import require_admin_or_system
from services.research_threads import THREAD_RE as _THREAD_RE, research_thread_id  # noqa: F401  (re-export)

router = APIRouter(prefix="/v1/admin/research", dependencies=[Depends(require_admin_or_system)])

# State keys a fork may override before resuming. Kept narrow on purpose: forks should differ
# by sampling, not by arbitrary state surgery.
FORK_OVERRIDES = {"query_temperature"}
OUTCOME_KEYS = ("quality_blocked", "quality_block_reason", "quality_gate_status", "hitl_gate_id", "hitl_gate_reason")
# Forks of one thread are serialized: the final checkpoint of a fork is read as the thread's
# latest checkpoint, which is only unambiguous when no other branch of that thread is running.
_thread_locks: dict[str, asyncio.Lock] = {}


class ResearchError(Exception):
    """Domain error of the research routes, mapped to an HTTP status by the route wrappers."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _check(graph, thread_id: str, enabled: bool) -> None:
    if not enabled:
        raise ResearchError(404, "Not found")
    if graph is None:
        raise ResearchError(503, "graph not ready")
    if not _THREAD_RE.match(thread_id):
        raise ResearchError(400, "thread id must match research-<8..80 chars>")


def _jsonable(value: Any, limit: int = 20000) -> Any:
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(value)[:limit]
    return value if len(text) <= limit else text[:limit]


async def history(graph, thread_id: str, keys: list[str], enabled: bool) -> dict:
    _check(graph, thread_id, enabled)
    items = []
    async for snap in graph.aget_state_history({"configurable": {"thread_id": thread_id}}):
        meta = snap.metadata or {}
        values = snap.values or {}
        items.append({
            "checkpoint_id": snap.config["configurable"].get("checkpoint_id"),
            "parent_checkpoint_id": (snap.parent_config or {}).get("configurable", {}).get("checkpoint_id"),
            "step": meta.get("step"),
            "source": meta.get("source"),
            "next": list(snap.next or ()),
            "created_at": snap.created_at,
            "values": {k: _jsonable(values[k]) for k in keys if k in values},
        })
    if not items:
        raise ResearchError(404, "unknown thread")
    return {"thread_id": thread_id, "checkpoints": items}


async def fork(graph, thread_id: str, checkpoint_id: str, overrides: dict, timeout_s: float, enabled: bool) -> dict:
    _check(graph, thread_id, enabled)
    bad = set(overrides) - FORK_OVERRIDES
    if bad:
        raise ResearchError(400, f"overrides not allowed: {sorted(bad)}")
    config = {"configurable": {"thread_id": thread_id, "checkpoint_id": checkpoint_id}}
    async with _thread_locks.setdefault(thread_id, asyncio.Lock()):
        snap = await graph.aget_state(config)
        if not snap or not snap.values:
            raise ResearchError(404, "unknown checkpoint")
        if overrides:
            config = await graph.aupdate_state(config, overrides)
        try:
            result = await asyncio.wait_for(graph.ainvoke(None, config), timeout=timeout_s)
        except asyncio.TimeoutError as exc:
            raise ResearchError(504, "fork timed out") from exc
        final = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    return {
        "thread_id": thread_id,
        "forked_from": checkpoint_id,
        "final_checkpoint_id": final.config["configurable"].get("checkpoint_id") if final else None,
        "final_response": (result or {}).get("final_response", ""),
        # quality-gate outcome: a HITL gate keeps the draft in the gate store (final_response stays empty)
        **{k: (result or {}).get(k) for k in OUTCOME_KEYS},
    }


class ForkRequest(BaseModel):
    checkpoint_id: str = Field(min_length=1, max_length=200)
    overrides: dict = Field(default_factory=dict)
    timeout_s: Optional[float] = Field(default=None, gt=0)


@router.get("/threads/{thread_id}/history")
async def thread_history(thread_id: str, keys: str = "") -> dict:
    """Checkpoints of a thread, newest first; `keys` (comma separated) selects state values to include."""
    try:
        return await history(state.app_graph, thread_id, [k for k in keys.split(",") if k], RESEARCH_ROUTES_ENABLED)
    except ResearchError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc


@router.post("/threads/{thread_id}/fork")
async def fork_thread(thread_id: str, body: ForkRequest) -> dict:
    """Resume the graph from `checkpoint_id`; the run becomes a new branch of the same thread."""
    timeout = min(body.timeout_s or ORCHESTRATION_TIMEOUT, ORCHESTRATION_TIMEOUT)
    try:
        return await fork(state.app_graph, thread_id, body.checkpoint_id, body.overrides, timeout, RESEARCH_ROUTES_ENABLED)
    except ResearchError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
