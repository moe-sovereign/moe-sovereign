import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks/orchestration_suite/prm"))
import router_eval as R  # noqa: E402


def ep(tid, q, score, answered=True):
    return {"task_id": tid, "q": q if answered else 0.0, "score": score if answered else None, "answered": answered}


def test_rules_on_a_small_example():
    pool = [{"id": f"t1#direct{k}", "task_id": "t1", "src": "direct", "q": q} for k, q in enumerate((0.0, 0.0, 1.0))] + \
           [{"id": f"t2#direct{k}", "task_id": "t2", "src": "direct", "q": 1.0} for k in range(3)]
    scores = {"t1#direct0": 0.2, "t1#direct1": 0.2, "t1#direct2": 0.8, "t2#direct0": 0.9, "t2#direct1": 0.9, "t2#direct2": 0.9}
    # t1 (cypher_sql_calc): workflow right with a high score, then right with a low score; t2: workflow wrong with a low score, then no answer
    eps = [ep("t1", 1.0, 0.95), ep("t1", 1.0, 0.1), ep("t2", 0.0, 0.2), ep("t2", 0, 0, answered=False)]
    pt = R.per_task(pool, scores, eps, {"t1": "cypher_sql_calc", "t2": "sql_calc"})
    d1 = (0 + 0 + 1) / 3
    assert abs(pt["t1"]["direct"] - d1) < 1e-9 and pt["t1"]["workflow"] == 1.0
    assert pt["t1"]["R1"] == 1.0 and pt["t2"]["R1"] == 1.0                       # R1: workflow for t1, direct for t2 (direct = 1.0)
    # R2 on t1: mean direct score 0.4; the 0.95 episode is used (1.0), the 0.1 episode falls back to the direct judge (d1)
    assert abs(pt["t1"]["R2"] - (1.0 + d1) / 2) < 1e-9
    assert pt["t2"]["R2"] == 1.0                                                  # the wrong low-scored and the empty episode both use the direct judge


def test_summary_has_paired_differences():
    pt = {f"t{i}": {"direct": 0.5, "workflow": 0.4, "R1": 0.6, "R2": 0.7} for i in range(20)}
    s = R.summarize(pt, boot=200)
    assert s["direct"] == 0.5 and s["R2_minus_direct"]["diff"] == 0.2 and s["workflow_minus_direct"]["diff"] == -0.1 and s["tasks"] == 20
