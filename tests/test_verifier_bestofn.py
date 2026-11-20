"""Verifier tooling: answer pool, scoring helpers and the best-of-N evaluation."""
import importlib.util
import json
from pathlib import Path

_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "orchestration_suite" / "prm"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bo, pool_mod, sc = _load("verifier_bestofn"), _load("build_answer_pool"), _load("score_answers")
NUM = lambda t: float(t.split()[-1]) if t.split() and t.split()[-1].replace(".", "").isdigit() else None  # noqa: E731


def row(rid, task, src, answer, q, family="sql_calc"):
    return {"id": rid, "task_id": task, "src": src, "family": family, "answer": answer, "q": float(q)}


def test_auroc_counts_ties_half():
    assert bo.auroc([0.9, 0.8, 0.2], [1, 1, 0]) == 1.0
    assert bo.auroc([0.5, 0.5], [1, 0]) == 0.5
    assert bo.auroc([0.5], [1]) != bo.auroc([0.5], [1])  # nan: one class only


def test_verifier_picks_a_correct_answer_that_majority_voting_misses():
    pool = [row("a", "t1", "d", "result 10", 0), row("b", "t1", "d", "result 10", 0), row("c", "t1", "d", "result 42", 1),
            row("e", "t2", "d", "result 7", 1), row("f", "t2", "d", "result 7", 1), row("g", "t2", "d", "result 9", 0)]
    scores = {"a": 0.2, "b": 0.3, "c": 0.9, "e": 0.8, "f": 0.7, "g": 0.1}
    res = bo.evaluate(pool, scores, {"all": ["d"]}, NUM)["all"]["ALL"]
    assert res["tasks"] == 2 and res["single"] == round((1 / 3 + 2 / 3) / 2, 3)
    assert res["majority"] == 0.5 and res["verifier"] == 1.0 and res["oracle"] == 1.0 and res["auroc"] == 1.0


def test_tied_vote_counts_fractionally_and_text_families_have_no_majority():
    pool = [row("a", "t1", "d", "1", 1), row("b", "t1", "d", "2", 0)]
    res = bo.evaluate(pool, {"a": 0.4, "b": 0.6}, {"s": ["d"]}, NUM)["s"]["ALL"]
    assert res["majority"] == 0.5 and res["verifier"] == 0.0  # the verifier was wrong here, and the table says so
    text = [row("a", "t1", "d", "Paris", 1, "ext_text_qa"), row("b", "t1", "d", "Rome", 0, "ext_text_qa")]
    assert bo.evaluate(text, {"a": 0.9, "b": 0.1}, {"s": ["d"]}, NUM)["s"]["ext_text_qa"]["majority"] is None


def test_selection_sets_and_minimum_answers():
    pool = [row("a", "t1", "x", "1", 1), row("b", "t1", "y", "2", 0), row("c", "t2", "x", "3", 1)]
    scores = {"a": 0.9, "b": 0.1, "c": 0.9}
    res = bo.evaluate(pool, scores, {"both": ["x", "y"], "only_x": ["x"]}, NUM)
    assert res["both"]["ALL"]["tasks"] == 1 and res["only_x"] == {}  # t2 has one answer, t1 one x answer: no competition


def test_pool_builder_uses_the_run_prompt_and_counts_repeats(tmp_path):
    tasks = {"t1": {"id": "t1", "family": "ext_math", "lang": "en", "prompts": {"en": "no data"}, "prompts_inline": {"en": "with data"}}}
    run = tmp_path / "r.jsonl"
    run.write_text("\n".join(json.dumps(x) for x in [
        {"task_id": "t1", "base": {"answer": "= 4", "outcome": "answer", "correct": True}},
        {"task_id": "t1", "base": {"answer": "", "outcome": "answer", "correct": False}},  # no answer: not a candidate
        {"task_id": "t1", "base": {"answer": "", "draft": "= 5", "outcome": "blocked:x", "correct": False, "draft_correct": True}},
        {"task_id": "zz", "base": {"answer": "x", "outcome": "answer", "correct": False}}]))
    rows = pool_mod.build(tasks, {"wf": str(run)})
    assert [r["id"] for r in rows] == ["t1#wf0", "t1#wf1"] and [r["q"] for r in rows] == [1.0, 1.0]
    assert rows[0]["prompt"] == "with data" and rows[1]["answer"] == "= 5"  # the gate-free draft is the candidate


def test_score_helpers():
    assert sc.text_of({"id": "x", "prompt": "q", "answer": "a"}) == "[TASK]\nq\n[ANSWER]\na"
    assert sc.text_of({"id": "x", "text": "t"}) == "t"
    assert round(sc.sigmoid(0), 3) == 0.5 and sc.sigmoid(-1000) == 0.0 and sc.sigmoid(1000) == 1.0
    items = [("a", "x"), ("b", "xxxx"), ("c", "xx")]
    assert [[i for i, _ in b] for b in sc.batches_by_length(items, 2)] == [["b", "c"], ["a"]]
