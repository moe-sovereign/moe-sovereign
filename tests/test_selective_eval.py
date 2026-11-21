import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks/orchestration_suite/prm"))
import selective_eval as se  # noqa: E402


def ep(tid, q, score, released, answered=True, trust=0.5):
    return {"task_id": tid, "src": "wf", "answered": answered, "score": score if answered else None, "q": q if answered else 0.0,
            "released": released, "trust": trust, "family": "f"}


def test_split_is_deterministic_and_roughly_balanced():
    ids = [f"task-{i}" for i in range(400)]
    s = [se.split_of(i) for i in ids]
    assert s == [se.split_of(i) for i in ids] and 150 < sum(s) < 250


def test_release_rule_of_the_trust_gate():
    assert [se.released_by_trust(o) for o in ("answer", "hitl", "blocked:trust_score_block", "failed:x")] == [True, True, False, False]


def test_coverage_matched_prefers_a_better_ranking_at_equal_coverage():
    # the trust gate releases two answers (one right, one wrong); the verifier ranks the two right answers first
    eps = [ep("a", 1, 0.9, True), ep("b", 0, 0.2, True), ep("c", 1, 0.8, False), ep("d", 0, 0.1, False), ep("e", 0, None, False, answered=False)]
    r = se.coverage_matched(eps)
    assert r["n"] == 5 and r["coverage"] == 0.4 and r["trust_acc"] == 0.5 and r["verifier_acc"] == 1.0


def test_coverage_never_counts_an_episode_without_answer():
    eps = [ep("a", 1, 0.9, True), ep("b", 0, None, True, answered=False)]
    assert se.coverage_matched(eps)["coverage"] == 0.5  # released-but-empty is not released


def test_escalation_extremes_equal_workflow_and_direct():
    eps = [ep("a", 1, 0.9, True), ep("b", 0, 0.2, True), ep("c", 0, None, False, answered=False)]
    direct = {"a": 0.5, "b": 0.5, "c": 1.0}
    t_low = se.escalation(eps, direct, 0.0)      # nothing escalated except the empty episode
    assert t_low["per_task"] == {"a": 1.0, "b": 0.0, "c": 1.0} and abs(t_low["escalation_rate"] - 1 / 3) < 1e-9
    t_high = se.escalation(eps, direct, 1.01)    # everything escalated: the direct model alone
    assert t_high["per_task"] == direct and t_high["escalation_rate"] == 1.0


def test_threshold_choice_uses_dev_only_and_prefers_the_useful_cut():
    dev = [ep(f"t{i}", 1 if i % 2 else 0, 0.9 if i % 2 else 0.1, True) for i in range(20)]
    direct = {f"t{i}": 0.5 for i in range(20)}
    t = se.choose_t(dev, direct)
    assert 0.1 < t <= 0.9  # keeps the right answers, escalates the wrong ones


def test_ece_is_zero_for_perfect_calibration_and_large_for_overconfidence():
    assert se.ece([0.0, 1.0], [0.0, 1.0]) == 0.0
    assert se.ece([0.95] * 10, [0.0] * 10) == 0.95


def test_episodes_join_pool_scores_by_run_task_and_answer_counter(tmp_path):
    f = tmp_path / "wf.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in [
        {"task_id": "t1", "base": {"answer": "x", "outcome": "answer", "trust": {"score": 0.7}}},
        {"task_id": "t1", "base": {"outcome": "failed:x"}},                                         # no answer: no counter step
        {"task_id": "t1", "base": {"answer": "y", "outcome": "blocked:b", "draft": "y"}}]))
    pool = {"t1#wf0": (0.8, 1.0), "t1#wf1": (0.3, 0.0)}
    e = se.episodes({"wf": str(f)}, pool)
    assert [(x["answered"], x["released"], x["score"]) for x in e] == [(True, True, 0.8), (False, False, None), (True, False, 0.3)]
    assert e[0]["trust"] == 0.7
