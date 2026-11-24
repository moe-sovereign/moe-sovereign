"""Pure-function checks of benchmarks/score_expert_answers.py (no database, no judge calls)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benchmarks"))
import score_expert_answers as sea  # noqa: E402

JUDGES = ["judge-a", "judge-b"]


def _answer(track, stage, cat, audit_id):
    return {"track": track, "stage_of_template": stage, "category": cat, "audit_id": audit_id, "answer": "x"}


def test_answer_of_handles_ollama_and_openai_shapes_and_think_blocks():
    assert sea.answer_of({"message": {"content": "<think>hm</think> ok "}}) == "ok"
    assert sea.answer_of({"choices": [{"message": {"content": "hi"}}]}) == "hi"
    assert sea.answer_of(None) == ""


def test_subtask_of_strips_no_think_and_unwraps_message_repr():
    body = {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "/no_think\nDo X"}]}
    assert sea.subtask_of(body) == "Do X"
    wrapped = {"messages": [{"role": "user", "content": "{'role': 'user', 'content': 'Do Y'}"}]}
    assert sea.subtask_of(wrapped) == "Do Y"


def test_report_averages_over_judges_and_ignores_fallback_scores(tmp_path):
    answers, cache = [], {}
    for i in range(5):
        for track, score in (("spur1", 6.0), ("spur2", 8.0)):
            aid = f"{track}-{i}"
            answers.append(_answer(track, "fine-tuned", "security", aid))
            cache[f"{aid}|judge-a"] = {"score": score, "verdict": "PASS"}
            cache[f"{aid}|judge-b"] = {"score": score + 1, "verdict": "PASS"}
    bad = _answer("spur1", "fine-tuned", "security", "spur1-fallback")
    answers.append(bad)
    cache["spur1-fallback|judge-a"] = {"score": 5.0, "verdict": "UNSCORED_FALLBACK"}
    out = tmp_path / "report.md"
    sea.report(answers, cache, JUDGES, out)
    text = out.read_text()
    assert "6.50 ± 0.00 (n=5)" in text and "8.50 ± 0.00 (n=5)" in text
    assert "+2.00" in text  # delta spur2 - spur1, both cells have n >= MIN_N
    assert (tmp_path / "report.json").exists()


def test_small_cells_are_flagged_and_get_no_delta(tmp_path):
    answers = [_answer("spur1", "pre-finetune", "general", "a"), _answer("spur2", "pre-finetune", "general", "b")]
    cache = {"a|judge-a": {"score": 4.0, "verdict": "FAIL"}, "b|judge-a": {"score": 9.0, "verdict": "EXCELLENT"}}
    out = tmp_path / "r.md"
    sea.report(answers, cache, ["judge-a"], out)
    text = out.read_text()
    assert "(n<5)" in text and "n/a" in text
