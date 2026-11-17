"""Verifier rows: labelled drafts of forks and base episodes, held-out answers as the ood split, skipped tasks left out."""
import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "benchmarks" / "orchestration_suite" / "prm" / "build_verifier_data.py"
_spec = importlib.util.spec_from_file_location("build_verifier_data", _PATH)
bv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bv)

TASKS = {"sql_calc-x-1": {"id": "sql_calc-x-1", "family": "sql_calc", "prompts": {"en": "q1"}},
         "sql_calc-x-2": {"id": "sql_calc-x-2", "family": "sql_calc", "prompts": {"en": "q2"}}}


def rec(tid, forks, outcome="hitl", correct=True):
    return {"task_id": tid, "lang": "en", "base": {"answer": "= 42", "outcome": outcome, "correct": correct, "draft": ""},
            "decision_points": [{"forks": [{"draft": d, "draft_correct": ok} for d, ok in forks]}]}


def test_rows_use_drafts_of_blocked_forks_and_skip_listed_tasks(tmp_path):
    held = tmp_path / "direct.jsonl"
    held.write_text('{"task_id": "sql_calc-x-1", "lang": "en", "repeat": 0, "base": {"answer": "7", "draft": "7", "correct": false, "draft_correct": false, "outcome": "answer"}}\n')
    rows = bv.build([rec("sql_calc-x-1", [("= 42", True), ("9", False)]), rec("sql_calc-x-2", [("1", True)])], TASKS, [str(held)], skip={"sql_calc-x-2"})
    assert {r["group"] for r in rows} == {"sql_calc-x-1"}
    fork_rows = [r for r in rows if r["source"] == "fork"]
    assert len(fork_rows) == 3 and sorted(r["q"] for r in fork_rows) == [0.0, 1.0, 1.0]  # two forks plus the base answer
    assert all(r["text"].startswith("[TASK]\nq1\n[ANSWER]\n") for r in rows)
    ood = [r for r in rows if r["split"] == "test_ood"]
    assert len(ood) == 1 and ood[0]["q"] == 0.0 and ood[0]["source"] == "direct"
