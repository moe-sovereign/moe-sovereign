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


_DPATH = Path(__file__).resolve().parents[1] / "benchmarks" / "orchestration_suite" / "rollout_driver.py"
_dspec = importlib.util.spec_from_file_location("orch_driver", _DPATH)
drv = importlib.util.module_from_spec(_dspec)
_dspec.loader.exec_module(drv)


def test_number_parsing_follows_request_language():
    cases = [("370,76", "de", 370.76), ("1.234,56", "de", 1234.56), ("1.500", "de", 1500.0), ("370.76", "de", 370.76),
             ("1,234.56", "en", 1234.56), ("1,500", "en", 1500.0), ("370.76", "en", 370.76), ("3,5", "en", 3.5),
             ("12", "en", 12.0), ("-7,25", "de", -7.25)]
    for tok, lang, want in cases:
        assert drv.parse_number(tok, lang) == want, (tok, lang)


def test_answer_scoring_uses_last_number_of_closing_passage():
    assert drv.correct("Zwischenwert 205. Endergebnis: **370,76 EUR**", 370.76, 0.005, "de")
    assert not drv.correct("Endergebnis: 370,75 EUR", 370.76, 0.004, "de")
    assert not drv.correct("Ich kann das nicht berechnen.", 370.76, 0.005, "en")
    assert drv.correct("Total cost: 1,234.50 EUR", 1234.5, 0.005, "en")


def test_inline_prompts_do_not_trigger_file_skills():
    # The orchestrator auto-attaches a file skill (/xlsx) when the text names a file like "shipments.csv".
    from services.skills import _detect_file_skill
    for t in gen.generate(300, 7):
        for lang, prompt in t["prompts_inline"].items():
            assert _detect_file_skill(None, prompt, None) is None, (t["id"], lang)
