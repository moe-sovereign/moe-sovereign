import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("bcm", Path(__file__).resolve().parents[1] / "benchmarks/orchestration_suite/experts/build_competence_matrix.py")
bcm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bcm)


def _dev_ids(n):
    out, i = [], 0
    while len(out) < n:
        tid = f"t{i}"
        if bcm.is_dev(tid):
            out.append(tid)
        i += 1
    return out


def test_matrix_uses_dev_tasks_only_and_breaks_ties_alphabetically():
    dev = _dev_ids(2)
    test = next(f"t{i}" for i in range(100) if not bcm.is_dev(f"t{i}"))
    rows = [{"src": "exp_coder", "task_id": dev[0], "family": "f", "q": 1}, {"src": "exp_coder", "task_id": dev[1], "family": "f", "q": 0},
            {"src": "exp_governance", "task_id": dev[0], "family": "f", "q": 1}, {"src": "exp_governance", "task_id": dev[1], "family": "f", "q": 0},
            {"src": "exp_omni", "task_id": test, "family": "f", "q": 1}, {"src": "judgeA", "task_id": dev[0], "family": "f", "q": 1}]
    matrix, table = bcm.build(rows)
    assert matrix == {"f": "code_reviewer"} and "omni" not in table["f"] and "judgeA" not in table["f"]
