"""Unit tests for the orchestration task generator (no database required)."""
import importlib.util
from collections import Counter
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "benchmarks" / "orchestration_suite" / "generate.py"
_spec = importlib.util.spec_from_file_location("orch_generate", _PATH)
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


def test_generation_is_deterministic_and_unique():
    a, b = gen.generate(300, seed=7), gen.generate(300, seed=7)
    assert a == b
    assert len({t["id"] for t in a}) == 300


def test_language_share_does_not_change_tasks():
    en_only, mixed = gen.generate(200, seed=3, de_share=0.0), gen.generate(200, seed=3, de_share=0.5)
    assert [t["id"] for t in en_only] == [t["id"] for t in mixed]
    assert all(t["lang"] == "en" for t in en_only)
    assert 0.35 < Counter(t["lang"] for t in mixed)["de"] / 200 < 0.65
    for t in mixed:
        assert t["user"] == t["prompts"][t["lang"]]


def test_all_families_present_with_valid_step_dags():
    tasks = gen.generate(500, seed=1)
    assert set(Counter(t["family"] for t in tasks)) == set(gen.FAMILIES)
    for t in tasks:
        ids = [s["id"] for s in t["steps"]]
        for i, s in enumerate(t["steps"]):
            assert set(s["depends_on"]) <= set(ids[:i])  # dependencies point backwards only
        assert t["steps"][-1]["ref"] == t["final"]["ref"]
        kinds = {s["kind"] for s in t["steps"]}
        assert ("sql" in kinds) == ("sql" in t["setup"])
        assert ("cypher" in kinds) == ("cypher" in t["setup"])


def test_shortest_path_and_hops_reference():
    names = ["A", "B", "C", "D"]
    edges = {("A", "B"): 5, ("B", "C"): 5, ("A", "C"): 20, ("C", "D"): 1}
    assert gen.shortest(names, edges, "A", "D") == 11
    assert gen.within_hops(names, edges, "A", 1) == ["B", "C"]
    assert gen.within_hops(names, edges, "A", 2) == ["B", "C", "D"]
