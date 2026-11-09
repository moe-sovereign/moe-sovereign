"""Third-party question sets: adapters keep provenance and license, scoring handles number and text gold answers."""
import importlib.util
import json
import random
from pathlib import Path

_BASE = Path(__file__).resolve().parents[1] / "benchmarks" / "orchestration_suite"


def _load(rel, name):
    spec = importlib.util.spec_from_file_location(name, _BASE / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ad = _load("external/adapters.py", "ext_adapters")
drv = _load("rollout_driver.py", "rd_ext")


def test_text_scoring_uses_aliases_word_boundaries_and_all():
    f = {"kind": "text", "answers": ["Miquette Giraudy", "M. Giraudy"], "all": False}
    assert drv.score("Her spouse is Miquette Giraudy.", f, "en")
    assert not drv.score("The answer is Giraudys", {"kind": "text", "answers": ["Giraudy"], "all": False}, "en")
    assert drv.score("a) Kelgozero, b) Dlinnoye", {"kind": "text", "answers": ["Kelgozero", "Dlinnoye"], "all": True}, "en")
    assert not drv.score("Kelgozero", {"kind": "text", "answers": ["Kelgozero", "Dlinnoye"], "all": True}, "en")


def test_number_scoring_is_unchanged_by_the_dispatcher():
    assert drv.score("The result is 18.0", {"ref": 18.0, "tol": 0.005}, "en")
    assert not drv.score("The result is 19", {"ref": 18.0, "tol": 0.005}, "en")


def test_tatqa_keeps_only_numeric_questions_and_carries_license(tmp_path):
    doc = [{"table": {"uid": "t", "table": [["", "2019", "2018"], ["Revenue", "10", "8"]]}, "paragraphs": [{"text": "Text."}],
            "questions": [{"uid": "q1", "question": "Change in revenue?", "answer": 2, "answer_type": "arithmetic", "scale": "million"},
                          {"uid": "q2", "question": "Which year?", "answer": ["2019"], "answer_type": "span", "scale": ""}]}]
    p = tmp_path / "d.json"
    p.write_text(json.dumps(doc))
    out = ad.tatqa(p, 10, random.Random(0))
    assert [t["id"] for t in out] == ["tatqa-dev-q1"]
    t = out[0]
    assert t["final"]["ref"] == 2.0 and "Revenue | 10 | 8" in t["prompts_inline"]["en"] and "Revenue" not in t["prompts"]["en"]
    assert t["source"]["license"] == "CC BY 4.0"


def test_graph_conversion_uses_the_product_schema():
    g = {"entities": [{"eid": "P#1", "label": "Player", "name": "Ann", "aliases": ["A."], "properties": {"height_cm": 190.5, "nicknames": ["Ace", "Tall"]}},
                      {"eid": "T#1", "label": "Team", "name": "Hawks", "properties": {}}],
         "relations": [{"label": "playsFor", "subj_id": "P#1", "obj_id": "T#1", "properties": {}}]}
    f = ad.graph_facts(g)
    assert {"s": "Ann", "r": "PLAYS_FOR", "o": "Hawks", "conf": 0.9} in f["relations"]
    assert {"s": "Ann", "r": "HAS_HEIGHT_CM", "o": "190.5", "conf": 0.9} in f["relations"]
    assert sum(r["r"] == "HAS_NICKNAMES" for r in f["relations"]) == 2
    assert next(e for e in f["entities"] if e["name"] == "Ann")["aliases"] == ["A."]


def test_every_dataset_has_a_documented_license():
    assert set(ad.LICENSES) >= {"tatqa", "finqa", "gsm8k", "musique", "hotpotqa", "cypherbench"}
    assert "ShareAlike" in ad.LICENSES["hotpotqa"][0]
