"""Reconciliation of conflicting values for one attribute: authority first, recency inside a class, ties stay visible."""
import copy

from services.knowledge_reconcile import functional_relations, reconcile_facts


def fact(obj, *, authority=2, ms=1_700_000_000_000, rel="HAS_CAPACITY", subject="Elm12", fid=None):
    return {"id": fid or f"{subject}|{rel}|{obj}", "subject": subject, "relation": rel, "object": obj,
            "authority": authority, "valid_from_ms": ms, "created_ms": ms}


def test_newer_extracted_fact_replaces_the_older_one():
    kept, conflicts = reconcile_facts([fact("40 pallets", ms=1_000), fact("55 pallets", ms=2_000)])
    assert [f["object"] for f in kept] == ["55 pallets"]
    assert conflicts == [{"key": "Elm12|HAS_CAPACITY", "candidates": ["Elm12|HAS_CAPACITY|40 pallets", "Elm12|HAS_CAPACITY|55 pallets"],
                          "kept": ["Elm12|HAS_CAPACITY|55 pallets"], "rule": "recency"}]


def test_an_old_verified_fact_beats_a_newer_unverified_one():
    kept, conflicts = reconcile_facts([fact("40 pallets", authority=4, ms=1_000), fact("55 pallets", authority=2, ms=9_000)])
    assert [f["object"] for f in kept] == ["40 pallets"] and conflicts[0]["rule"] == "authority"


def test_a_tie_keeps_both_values_and_reports_it_unresolved():
    kept, conflicts = reconcile_facts([fact("40 pallets"), fact("55 pallets")])
    assert [f["object"] for f in kept] == ["40 pallets", "55 pallets"] and conflicts[0]["rule"] == "unresolved"


def test_lower_authority_is_dropped_even_when_the_top_tier_ties():
    kept, conflicts = reconcile_facts([fact("a", authority=4), fact("b", authority=4), fact("c", authority=2, ms=9_999_999_999_999)])
    assert sorted(f["object"] for f in kept) == ["a", "b"] and conflicts[0]["rule"] == "unresolved"


def test_non_functional_relations_pass_through_with_no_conflict():
    facts = [fact("Postgres", rel="USES"), fact("Valkey", rel="USES")]
    kept, conflicts = reconcile_facts(facts)
    assert kept == facts and conflicts == []


def test_the_same_value_asserted_twice_is_not_a_conflict():
    kept, conflicts = reconcile_facts([fact("40 pallets", fid="a"), fact("40 pallets", fid="b", authority=4)])
    assert len(kept) == 2 and conflicts == []


def test_subjects_are_reconciled_independently_and_input_order_is_kept():
    facts = [fact("1", subject="A", ms=1), fact("9", subject="B", ms=1), fact("2", subject="A", ms=2), fact("8", subject="B", ms=2)]
    kept, conflicts = reconcile_facts(facts)
    assert [f["object"] for f in kept] == ["2", "8"]  # the newer value of each subject, in input order
    assert [c["key"] for c in conflicts] == ["A|HAS_CAPACITY", "B|HAS_CAPACITY"]


def test_inputs_are_not_mutated():
    facts = [fact("40 pallets", ms=1), fact("55 pallets", ms=2)]
    before = copy.deepcopy(facts)
    reconcile_facts(facts)
    assert facts == before


def test_functional_relations_come_from_the_environment(monkeypatch):
    assert "HAS_CAPACITY" in functional_relations()
    monkeypatch.setenv("KNOWLEDGE_FUNCTIONAL_RELATIONS", "")
    assert functional_relations() == frozenset()
    kept, conflicts = reconcile_facts([fact("40 pallets", ms=1), fact("55 pallets", ms=2)])
    assert len(kept) == 2 and conflicts == []  # switched off: nothing is reconciled
    monkeypatch.setenv("KNOWLEDGE_FUNCTIONAL_RELATIONS", "USES, OWNS")
    assert functional_relations() == frozenset({"USES", "OWNS"})
