"""Client for the answer verifier: a learned P(answer is correct) in place of the heuristic trust score at the quality gate.

Evidence (LUMI-G, November 2026, synthetic task families plus external sets, see docs/system/lumi_findings_2026-11.md F1/F10): the trust
score separates correct from incorrect answers with AUROC 0.60, a 7B verifier trained on (task, draft, correct) rows with 0.95 on unseen tasks
of the same families; it transfers to numeric external tasks and not to free text. Limits apply: use `shadow` first and read the log.

Modes (VERIFIER_MODE, default off):
  off     nothing is called
  shadow  the answer is scored and the score is recorded (state, decision log); the gate is unchanged
  gate    the score decides: < VERIFIER_BLOCK -> BLOCK, < VERIFIER_PASS -> PROCEED_WITH_ASSUMPTION (the existing human/Cynefin path), else PROCEED

Configuration: VERIFIER_URL (scoring server, scripts/verifier_server.py), VERIFIER_PASS (0.7), VERIFIER_BLOCK (0.2), VERIFIER_TIMEOUT_S (5).
No server, a timeout or an error never changes the gate: the status becomes `unavailable`/`timeout` and the trust verdict stays in force
(fail-closed would turn an outage of an optional component into an outage of the product). Precision/contract checks of the gate run
before the verdict is used and are unaffected. The prompt and answer are sent to VERIFIER_URL only: a `local_only` request never reaches a
non-local host.
"""
from __future__ import annotations

import logging
import os
from typing import Mapping, Optional

import httpx

from services.deadline import remaining_timeout
from services.sovereignty import EgressDenied, assert_egress_allowed

logger = logging.getLogger("MOE-SOVEREIGN")


def verifier_mode() -> str:
    mode = os.getenv("VERIFIER_MODE", "off").strip().lower()
    return mode if mode in ("shadow", "gate") else "off"


def verifier_config() -> Optional[dict]:
    url = os.getenv("VERIFIER_URL", "").strip()
    if not url:
        return None

    def num(env: str, default: float) -> float:
        try:
            return float(os.getenv(env, default))
        except (TypeError, ValueError):
            return default

    pass_t, block_t = num("VERIFIER_PASS", 0.7), num("VERIFIER_BLOCK", 0.2)
    return {"url": url.rstrip("/"), "pass": max(pass_t, block_t), "block": min(pass_t, block_t), "timeout_s": max(0.5, num("VERIFIER_TIMEOUT_S", 5.0))}


def text_of(prompt: str, answer: str) -> str:
    """The training/scoring format (identical to prm/score_answers.py and scripts/verifier_server.py)."""
    return f"[TASK]\n{prompt}\n[ANSWER]\n{answer}"


def verdict_for(score: float, cfg: Mapping) -> str:
    if score < cfg["block"]:
        return "BLOCK"
    if score < cfg["pass"]:
        return "PROCEED_WITH_ASSUMPTION"
    return "PROCEED"


async def score_answer(state_: Mapping, prompt: str, answer: str, cfg: Mapping, *, client: Optional[httpx.AsyncClient] = None) -> tuple:
    """(score or None, status). Never raises for a service failure; a spent request deadline does propagate."""
    try:
        assert_egress_allowed(cfg["url"], bool(state_.get("local_only_routing")))
    except EgressDenied:
        return None, "unavailable"
    timeout = remaining_timeout(state_, cfg["timeout_s"], stage="answer_verifier")  # raises RequestDeadlineExceeded when spent
    own = client is None
    http = client or httpx.AsyncClient(timeout=timeout)
    try:
        r = await http.post(f"{cfg['url']}/score", json={"items": [{"prompt": prompt, "answer": answer}]}, timeout=timeout)
        r.raise_for_status()
        return float(r.json()["scores"][0]), "ok"
    except httpx.TimeoutException:
        return None, "timeout"
    except Exception as exc:  # noqa: BLE001 - an optional component must not fail the request
        logger.warning("answer verifier: call failed: %s: %s", type(exc).__name__, exc)
        return None, "unavailable"
    finally:
        if own:
            await http.aclose()


async def apply_verifier(state_: dict, *, client: Optional[httpx.AsyncClient] = None) -> dict:
    """State keys to add before the quality gate runs; {} when the verifier is off or there is nothing to score."""
    mode = verifier_mode()
    answer = str(state_.get("final_response") or "").strip()
    if mode == "off" or not answer:
        return {}
    cfg = verifier_config()
    if cfg is None:
        return {"verifier_status": "disabled"}
    score, status = await score_answer(state_, str(state_.get("input") or ""), answer, cfg, client=client)
    out: dict = {"verifier_status": status}
    if score is None:
        return out
    out["verifier_score"] = score
    verdict = verdict_for(score, cfg)
    if mode == "gate":
        out["verifier_verdict"] = verdict
    try:
        from services.decision_log import DecisionType, log_decision
        log_decision(DecisionType.VERIFIER_SCORE, str(state_.get("response_id") or ""),
                     f"verifier score {score:.3f} -> {verdict} (mode {mode}); trust verdict {state_.get('trust_verdict') or '-'}",
                     {"score": score, "verdict": verdict, "mode": mode, "trust_score": state_.get("trust_score"),
                      "trust_verdict": state_.get("trust_verdict") or ""})
    except Exception as exc:  # noqa: BLE001 - logging must not fail the request
        logger.debug("answer verifier: decision log failed: %s", exc)
    return out
