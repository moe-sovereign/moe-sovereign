import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks/orchestration_suite/interview"))
import build_tasks as bt  # noqa: E402
import generate_traces as gt  # noqa: E402

PUB = {"request": "What is the capital of Australia?", "needs_knowledge": True}
NON = {"request": "Sum the column", "needs_knowledge": False}


def test_judge_rules_by_kind():
    ok = json.dumps({"questions": ["What is the capital city of Australia?"]})
    assert gt.judge(PUB, [ok, ok], 3) == (["What is the capital city of Australia?"], "accepted")
    assert gt.judge(PUB, [ok, '{"questions": []}'], 3)[1] == "asked_nothing"
    assert gt.judge(PUB, [ok, "nonsense"], 3)[1] == "unparseable"
    assert gt.judge(PUB, [json.dumps({"questions": ["Tell me about cats please"]})] * 2, 3)[1] == "off_topic"
    assert gt.judge(NON, ['{"questions": []}'] * 2, 3) == ([], "accepted")
    assert gt.judge(NON, [ok, '{"questions": []}'], 3)[1] == "asked_when_not_needed"


def test_splits_are_disjoint_by_fact_across_languages():
    rows = bt.public_tasks()
    by_key = {}
    for r in rows:
        by_key.setdefault(r["id"].rsplit("-", 1)[0], set()).add(r["split"])
    assert all(len(s) == 1 for s in by_key.values()) and {r["split"] for r in rows} == {"train", "test"}


def test_infra_tasks_carry_their_answer_in_the_known_facts():
    for t in bt.infra_tasks(5, 3, 1):
        assert t["gold"][0] in t["known_facts"] and not t["needs_knowledge"] and t["gaps"] == []
