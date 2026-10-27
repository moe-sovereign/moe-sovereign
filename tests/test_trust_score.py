"""Tests for services/trust_score.py (TASK-10)."""
import pytest
from services.trust_score import (
    TrustVerdict, compute_trust_score,
    _extract_checkable_claims, _unsupported_claim_ratio,
)


def _state(**kwargs):
    base = {
        "expert_results": [],
        "plan": [],
        "graph_context": "",
        "web_research": "",
        "conflict_registry": [],
        "judge_before_after": {},
    }
    base.update(kwargs)
    return base


def test_hard_block_when_no_content():
    ts = compute_trust_score(_state())
    assert ts.hard_blocked is True
    assert ts.verdict == TrustVerdict.BLOCK
    assert ts.score == 0.0


def test_proceed_with_good_experts_and_context():
    # 5 expert results (max), 5 tasks covered, 10 web sources, rich graph context
    ts = compute_trust_score(_state(
        expert_results=["[RESEARCH / llama3]: GraphQL is a query language with many features and advantages."] * 5,
        plan=[{"task": f"t{i}", "category": "research"} for i in range(5)],
        web_research="\n".join(f"https://site{i}.com — reference" for i in range(10)),
        graph_context=" ".join(f"[NEO4J:entity_{i}]" for i in range(10)),
    ))
    assert ts.hard_blocked is False
    assert ts.verdict == TrustVerdict.PROCEED
    assert ts.score >= 0.65


def test_proceed_with_assumption_borderline():
    ts = compute_trust_score(_state(
        expert_results=["[RESEARCH / llama3]: Some answer with minimal detail."],
        plan=[{"task": "t1"}, {"task": "t2"}],  # 2 tasks, 1 expert → 50% coverage
        web_research="",
        graph_context="",
    ))
    assert ts.hard_blocked is False
    assert ts.verdict in (TrustVerdict.PROCEED_WITH_ASSUMPTION, TrustVerdict.BLOCK)


def test_conflict_reduces_score():
    no_conflict = compute_trust_score(_state(
        expert_results=["[RESEARCH / m]: Good long expert answer with details."] * 2,
        plan=[{"task": "t1"}],
        web_research="https://a.com",
        graph_context="entity: X",
    ))
    with_conflict = compute_trust_score(_state(
        expert_results=["[RESEARCH / m]: Good long expert answer with details."] * 2,
        plan=[{"task": "t1"}],
        web_research="https://a.com",
        graph_context="entity: X",
        conflict_registry=[{"category": "research", "a": "x", "b": "y"}] * 3,
    ))
    assert with_conflict.score < no_conflict.score


def test_factors_keys_present():
    ts = compute_trust_score(_state(
        expert_results=["[CODE / m]: def foo(): pass"],
        plan=[{"task": "t"}],
        web_research="https://docs.python.org",
    ))
    assert "source_count" in ts.factors
    assert "expert_count" in ts.factors
    assert "conflict_penalty" in ts.factors
    assert "cross_reference_coverage" in ts.factors


def test_score_clipped_to_zero_one():
    ts = compute_trust_score(_state(
        expert_results=["[RESEARCH / m]: " + "x" * 100] * 10,
        plan=[{"task": f"t{i}"} for i in range(10)],
        web_research=" ".join(f"https://site{i}.com" for i in range(20)),
        graph_context=" ".join(f"[NEO4J:entity_{i}]" for i in range(20)),
    ))
    assert 0.0 <= ts.score <= 1.0


# ── Hallucination proxy: unsupported_claims_penalty ──────────────────────────

def test_extract_checkable_claims_finds_numbers_and_proper_nouns():
    claims = _extract_checkable_claims(
        "Construction of the Eiffel Tower finished in 1889, reaching 330 meters."
    )
    assert "330" in claims
    assert "1889" in claims
    assert any("Eiffel Tower" in c for c in claims)


def test_extract_checkable_claims_empty_for_no_text():
    assert _extract_checkable_claims("") == set()
    assert _extract_checkable_claims("the quick brown fox") == set()  # no caps/numbers


def test_unsupported_claim_ratio_zero_when_no_source():
    # No source material to check against → can't penalise, ratio is 0.0
    assert _unsupported_claim_ratio("The Eiffel Tower is 330 meters tall.", "") == 0.0


def test_unsupported_claim_ratio_zero_when_claims_covered():
    ratio = _unsupported_claim_ratio(
        "The Eiffel Tower is 330 meters tall.",
        "Source: The Eiffel Tower stands 330 meters tall in Paris.",
    )
    assert ratio == 0.0


def test_unsupported_claim_ratio_high_when_claims_absent():
    ratio = _unsupported_claim_ratio(
        "The Eiffel Tower is 330 meters tall.",
        "Source: unrelated content about database indexing strategies.",
    )
    assert ratio == 1.0


def test_unsupported_claims_penalty_factor_present():
    ts = compute_trust_score(_state(
        expert_results=["[CODE / m]: def foo(): pass"],
        plan=[{"task": "t"}],
        web_research="https://docs.python.org",
    ))
    assert "unsupported_claims_penalty" in ts.factors


def test_unsupported_claims_lower_score_than_grounded_claims():
    # Same expert-count/source-count/coverage shape, only the claim-vs-source
    # overlap differs — isolates the new factor's effect on the final score.
    grounded = compute_trust_score(_state(
        expert_results=["[RESEARCH / m]: Berlin has 3800000 residents."],
        plan=[{"task": "t1"}],
        web_research="https://stats.example.com",
        graph_context="Berlin population figures: 3800000 residents recorded.",
        retrieved_graph_chunks=[{"entity": "Berlin"}],
    ))
    ungrounded = compute_trust_score(_state(
        expert_results=["[RESEARCH / m]: Berlin has 3800000 residents."],
        plan=[{"task": "t1"}],
        web_research="https://stats.example.com",
        graph_context="unrelated content about database indexing strategies.",
        retrieved_graph_chunks=[{"entity": "database"}],
    ))
    assert ungrounded.factors["unsupported_claims_penalty"] > grounded.factors["unsupported_claims_penalty"]
    assert ungrounded.score < grounded.score


def test_verified_trivial_direct_response_does_not_require_retrieval_sources():
    ts = compute_trust_score(_state(
        expert_results=["[qwen3.6:35b / general]: CONFIDENCE: low\nDETAILS:\nOK"],
        plan=[{"task": "Antworte ausschließlich mit: OK", "category": "general"}],
        trivial_fast_path=True,
    ))
    assert ts.hard_blocked is False
    assert ts.verdict == TrustVerdict.PROCEED
    assert ts.factors["trivial_fast_path"] == 1.0


def test_trivial_direct_response_with_conflict_is_not_auto_trusted():
    ts = compute_trust_score(_state(
        expert_results=["[qwen3.6:35b / general]: A sufficiently long answer."],
        plan=[{"task": "Say hello", "category": "general"}],
        trivial_fast_path=True,
        conflict_registry=[{"category": "general", "resolution": "pending"}],
    ))
    assert ts.verdict != TrustVerdict.PROCEED


def test_completed_precision_evidence_counts_as_grounded_execution():
    ts = compute_trust_score(
        _state(
            expert_results=[
                "[CODE_REVIEWER / qwen]: Parameterized query is safe and complete."
            ],
            plan=[
                {
                    "id": "task-1",
                    "task": "gcd",
                    "category": "precision_tools",
                },
                {
                    "id": "task-2",
                    "task": "convert",
                    "category": "precision_tools",
                },
                {
                    "id": "task-3",
                    "task": "weekday",
                    "category": "precision_tools",
                },
                {
                    "id": "task-4",
                    "task": "review SQL",
                    "category": "code_reviewer",
                },
            ],
            task_events=[
                {
                    "task_id": f"task-{index}",
                    "status": "completed",
                    "iteration": 0,
                }
                for index in range(1, 5)
            ],
            mcp_evidence=[
                {
                    "task_id": f"task-{index}",
                    "status": "completed",
                    "result": result,
                }
                for index, result in enumerate(
                    ["gcd=23", "20 m/s", "Mittwoch"],
                    start=1,
                )
            ],
        )
    )

    assert ts.verdict == TrustVerdict.PROCEED
    assert ts.factors["deterministic_coverage"] == 1.0
    assert ts.factors["cross_reference_coverage"] == 1.0


def test_irrelevant_graph_text_without_attribution_does_not_count_as_source():
    ts = compute_trust_score(
        _state(
            expert_results=["[GENERAL / model]: A sufficiently detailed answer."],
            plan=[{"id": "task-1", "task": "answer", "category": "general"}],
            task_events=[
                {
                    "task_id": "task-1",
                    "status": "completed",
                    "iteration": 0,
                }
            ],
            graph_context="666 characters of unrelated graph material",
            retrieved_graph_chunks=[],
        )
    )

    assert ts.factors["source_count"] == 0.0


def test_creative_task_does_not_require_fact_sources():
    ts = compute_trust_score(
        _state(
            expert_results=[
                "[CREATIVE_WRITER / model]: A long original poem about blue rain."
            ],
            plan=[
                {
                    "id": "task-1",
                    "task": "write a poem",
                    "category": "creative_writer",
                }
            ],
            task_events=[
                {
                    "task_id": "task-1",
                    "status": "completed",
                    "iteration": 0,
                }
            ],
        )
    )

    assert ts.verdict == TrustVerdict.PROCEED
    assert ts.factors["creative_task_fit"] == 1.0


def test_self_critique_and_review_entries_do_not_count_as_experts():
    from services.trust_score import compute_trust_score
    base = {
        "expert_results": ["[m / code_reviewer]: " + "x" * 50],
        "plan": [{"task": "t", "category": "code_reviewer"}],
    }
    inflated = dict(base)
    inflated["expert_results"] = base["expert_results"] + [
        "[SELF_CRITIQUE_R1 / judge]: " + "y" * 50,
        "[REVIEW:security→code_reviewer / security]: " + "z" * 50,
    ]
    assert compute_trust_score(inflated).factors["expert_count"] == compute_trust_score(base).factors["expert_count"]
