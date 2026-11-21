import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks/orchestration_suite/interview"))
import eval_interview as ev  # noqa: E402
import make_bundle as mb  # noqa: E402


def task(kind, need, gaps=(), request="What is the capital of Australia?"):
    return {"kind": kind, "needs_knowledge": need, "gaps": list(gaps), "request": request}


def test_metrics_per_kind():
    tasks = [task("public", True, ["Australia"]), task("public", True), task("infra", False), task("infra", False), task("inline", False, ["Graphen"])]
    answers = [["What is the capital city of Australia?"], [], [], ["Why?"], None]
    m = ev.metrics(tasks, answers)
    assert m["public"] == {"n": 2, "parse_rate": 1.0, "ask_rate": 0.5, "needless_rate": None, "missed_rate": 0.5, "on_topic_rate": 1.0}
    assert m["infra"]["needless_rate"] == 0.5 and m["infra"]["missed_rate"] is None and m["infra"]["on_topic_rate"] == 0.0
    assert m["inline"]["parse_rate"] == 0.0 and m["inline"]["ask_rate"] == 0.0


def test_the_rule_baseline_asks_for_every_gap_term_up_to_the_limit():
    t = task("inline", False, ["A", "B", "C", "D"])
    assert ev.baseline_questions(t) == [f"What is {x}? Answer in one or two sentences." for x in "ABC"]
    assert ev.baseline_questions(task("infra", False)) == []
    m = ev.metrics([t], [ev.baseline_questions(t)])
    assert m["inline"]["needless_rate"] == 1.0  # the rule asks about private inline data: the failure the interview planner must avoid


def test_bundle_split_keeps_both_languages_of_a_fact_together(tmp_path):
    rows = [{"id": f"public-{i // 2:03d}-{'en' if i % 2 else 'de'}", "prompt": "p", "completion": "c"} for i in range(40)]
    train, held = mb.split(rows, 5, 1)
    assert {mb.group_of(r["id"]) for r in train}.isdisjoint({mb.group_of(r["id"]) for r in held}) and len(held) == 10
