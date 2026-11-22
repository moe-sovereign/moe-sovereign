"""Fallback to one direct judge answer when the orchestration pipeline fails (PIPELINE_FALLBACK_DIRECT=1, default off).

Evidence (LUMI-G, docs/system/lumi_findings_2026-11.md F26): with the verifier gate the system answered better than the direct judge wherever the pipeline produced an
answer (0.594 against 0.500 on the same tasks), but 23 % of the episodes failed before the gate (the pipeline raised, HTTP 500 `orchestration_failed`, or ran into its
time limit) and counted as wrong; a direct judge answer for exactly those requests is the missing piece. Which failures: PIPELINE_FALLBACK_ON (csv of `error`,
`timeout`; default `error,timeout`).

Limits, on purpose: the answer is one unorchestrated judge answer (no tools, no knowledge, no verification) and is marked as such: the response carries `moe_fallback`
("direct_judge:<error|timeout>") and a decision-log entry FALLBACK_DIRECT. It must not be used where an answer has to come from a deterministic precision contract:
the switch is for deployments and experiments that prefer an unverified answer over none. Nothing is changed when the switch is off.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Awaitable, Callable, Mapping, Optional

logger = logging.getLogger("MOE-SOVEREIGN")


def fallback_enabled(kind: str) -> bool:
    """`kind` is `error` or `timeout`."""
    if os.getenv("PIPELINE_FALLBACK_DIRECT", "0").strip() != "1":
        return False
    allowed = {k.strip() for k in os.getenv("PIPELINE_FALLBACK_ON", "error,timeout").split(",") if k.strip()}
    return kind in allowed


def judge_state(tmpl: Mapping, user_id: str = "") -> dict:
    """The few state keys the judge invocation reads, taken from the resolved template."""
    keys = ("judge_model_override", "judge_url_override", "judge_token_override", "judge_num_ctx")
    return {**{k: tmpl.get(k, "" if k != "judge_num_ctx" else 0) for k in keys}, "user_id": user_id}


async def direct_answer(state_: Mapping, prompt: str, invoke: Callable[..., Awaitable]) -> Optional[str]:
    """One judge answer to the original request, or None when it fails or is empty. Never raises."""
    try:
        res = await invoke(dict(state_), prompt, max_retries=1, temperature=0.0)
        text = (getattr(res, "content", "") or "").strip()
        return text or None
    except Exception as exc:  # noqa: BLE001 - the fallback must not turn one failure into another
        logger.warning("direct fallback failed: %s: %s", type(exc).__name__, exc)
        return None


def completion_payload(chat_id: str, model_id: str, answer: str, kind: str) -> dict:
    """OpenAI-compatible chat completion carrying the marker."""
    return {
        "id": chat_id, "object": "chat.completion", "created": int(time.time()), "model": model_id,
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": answer}}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "moe_fallback": f"direct_judge:{kind}",
    }
