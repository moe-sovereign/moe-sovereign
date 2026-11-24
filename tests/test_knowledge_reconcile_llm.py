"""Reconciliation of a knowledge model's answers with infrastructure facts, and the cutoff lookup."""
import datetime as dt

from services import model_capabilities as mc
from services.knowledge_reconcile import reconcile_with_llm, values_agree

NOW = 1_780_000_000_000  # 2026-05-28
OLD = 1_700_000_000_000  # 2023-11-14


def fact(obj, *, authority=2, ms=NOW, source_model="", fid="f1"):
    return {"id": fid, "subject": "Elm12", "relation": "HAS_CAPACITY", "object": obj, "authority": authority,
            "valid_from_ms": ms, "created_ms": ms, "source_model": source_model}


def llm(answer, *, subject="Elm12", relation="HAS_CAPACITY", conf=0.9, unknown=False):
    return {"question": "What is the capacity of Elm12?", "answer": answer, "confidence": conf, "unknown": unknown,
            "subject": subject, "relation": relation}


def test_values_agree_compares_numbers_not_formatting():
    assert values_agree("40 pallets", "40")
    assert values_agree("1.234,5 kg", "1234.5")
    assert values_agree("1,234.5", "1234.5")
    assert not values_agree("40 pallets", "55 pallets")
    assert values_agree("Canberra.", "canberra") and values_agree("The capital is Canberra", "Canberra")
    assert not values_agree("", "x")


def test_agreement_is_no_conflict_and_adds_nothing():
    assert reconcile_with_llm([fact("40 pallets")], [llm("40")]) == ([], [])


def test_a_gap_answer_is_shown_as_unverified_model_knowledge():
    out, conflicts = reconcile_with_llm([], [{"question": "What is Birch7?", "answer": "A ward", "confidence": 0.8, "unknown": False}])
    assert conflicts == [] and out[0]["source"] == "knowledge_llm" and out[0]["authority"] == 0 and "A ward" in out[0]["text"]


def test_infrastructure_with_authority_wins_over_the_model():
    out, conflicts = reconcile_with_llm([fact("40 pallets", authority=4, ms=OLD)], [llm("55")])
    assert out == [] and conflicts[0]["rule"] == "infra_authority" and conflicts[0]["infra"] == "40 pallets" and conflicts[0]["llm"] == "55"


def test_a_fact_newer_than_the_models_cutoff_wins_for_that_reason():
    out, conflicts = reconcile_with_llm([fact("40 pallets", authority=2, ms=NOW)], [llm("55")], llm_cutoff=dt.date(2025, 6, 1))
    assert out == [] and conflicts[0]["rule"] == "infra_newer_than_cutoff"
    _, older = reconcile_with_llm([fact("40 pallets", authority=2, ms=OLD)], [llm("55")], llm_cutoff=dt.date(2025, 6, 1))
    assert older[0]["rule"] == "infra_authority"  # the fact predates the cutoff: the model could know it; authority decides


def test_a_fact_that_a_model_wrote_is_not_independent_evidence_so_both_are_shown():
    out, conflicts = reconcile_with_llm([fact("40 pallets", authority=2, source_model="qwen3.6:35b")], [llm("55")])
    assert conflicts[0]["rule"] == "unresolved" and [f["object"] for f in out] == ["55"]
    imp, c2 = reconcile_with_llm([fact("40 pallets", authority=1)], [llm("55")])
    assert c2[0]["rule"] == "unresolved" and len(imp) == 1


def test_an_unsure_model_is_ignored():
    for item in (llm("55", unknown=True), llm("55", conf=0.3), llm("  ")):
        assert reconcile_with_llm([fact("40 pallets", authority=4)], [item]) == ([], [])


def test_knowledge_cutoff_lookup(monkeypatch):
    monkeypatch.setattr(mc, "get_model_caps", lambda m: {"knowledge_cutoff": "2025-06"} if m == "m" else {})
    assert mc.get_knowledge_cutoff("m") == dt.date(2025, 6, 1)
    assert mc.get_knowledge_cutoff("other") is None
    monkeypatch.setattr(mc, "get_model_caps", lambda m: {"knowledge_cutoff": "soon"})
    assert mc.get_knowledge_cutoff("x") is None


def test_model_fact_ids_are_stable_across_processes():
    a = reconcile_with_llm([], [{"question": "q", "answer": "a", "confidence": 0.9, "unknown": False}])[0][0]["id"]
    assert a == "llm:" + __import__("hashlib").sha1(b"q|a").hexdigest()[:10]
