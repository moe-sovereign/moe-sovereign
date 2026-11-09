"""Orchestration PRM: dataset rows, task-level splits, metrics (pure Python, no torch)."""
import importlib.util
import json
import math
import sys
from pathlib import Path

PRM = Path(__file__).resolve().parents[1] / "benchmarks" / "orchestration_suite" / "prm"
sys.path.insert(0, str(PRM))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, PRM / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


metrics = _load("prm_metrics")
ds = _load("build_dataset")
train = _load("train_prm")


def test_auroc_spearman_known_values():
    assert metrics.auroc([0.1, 0.4, 0.35, 0.8], [0, 0, 1, 1]) == 0.75
    assert metrics.auroc([0.5, 0.5], [0, 1]) == 0.5
    assert metrics.auroc([0.1, 0.2], [1, 1]) is None
    assert metrics.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert metrics.spearman([1, 2, 3], [3, 2, 1]) == -1.0


def test_within_task_pairwise_only_counts_differing_states_of_one_task():
    rows = [{"q": 1.0, "group": "a", "step": 0}, {"q": 0.0, "group": "a", "step": 1},
            {"q": 0.5, "group": "b", "step": 0}, {"q": 0.5, "group": "b", "step": 1}]
    good = metrics.evaluate(rows, [0.9, 0.1, 0.3, 0.7])
    bad = metrics.evaluate(rows, [0.1, 0.9, 0.3, 0.7])
    assert good["within_task_pairs"] == 1 and good["within_task_pairwise"] == 1.0 and bad["within_task_pairwise"] == 0.0


def _rollout(task_id, family, oks_per_point):
    return {"task_id": task_id, "family": family, "lang": "en", "base": {"correct": False},
            "decision_points": [{"checkpoint_id": f"c{i}", "step": i, "next": ["planner"], "state": {"plan": "x" * 3000},
                                 "forks": [{"correct": c} for c in oks]} for i, oks in enumerate(oks_per_point)]}


def _tasks(ids):
    return {i: {"id": i, "prompts": {"en": "Compute it."}} for i in ids}


def test_rows_labels_and_split_by_task():
    ids = [f"sql_calc-clinic-{n:012d}" for n in range(60)] + ["sql_calc-energy-000000000001"]
    recs = [_rollout(i, "sql_calc", [[True, False, False, False], [True] * 4]) for i in ids]
    rows = ds.build(recs, _tasks(ids), ood_domain="energy", uniform_keep=1.0)
    by_group = {}
    for r in rows:
        by_group.setdefault(r["group"], set()).add(r["split"])
    assert all(len(s) == 1 for s in by_group.values())          # no task straddles two splits
    assert by_group["sql_calc-energy-000000000001"] == {"test_ood"}
    first = next(r for r in rows if r["index"] == 0)
    assert first["q"] == 0.25 and first["k"] == 1 and first["dq"] is None
    second = next(r for r in rows if r["group"] == first["group"] and r["index"] == 1)
    assert second["q"] == 1.0 and second["dq"] == 0.75
    assert "[TASK]\nCompute it." in first["text"] and "...[+" in first["text"]   # long strings are cut


def test_uniform_thinning_only_touches_train_and_reweights():
    ids = [f"sql_calc-clinic-{n:012d}" for n in range(400)]
    recs = [_rollout(i, "sql_calc", [[False] * 4, [True, False, True, True]]) for i in ids]
    rows = ds.build(recs, _tasks(ids), ood_domain="", uniform_keep=0.25)
    kept = [r for r in rows if r["split"] == "train" and r["q"] == 0.0]
    all_train = sum(1 for i in ids if ds.split_of(i, "clinic", "") == "train")
    assert 0.1 * all_train < len(kept) < 0.45 * all_train and all(r["weight"] == 4.0 for r in kept)
    assert all(r["weight"] == 1.0 for r in rows if r["split"] != "train")
    assert sum(r["split"] != "train" and r["q"] == 0.0 for r in rows) == sum(ds.split_of(i, "clinic", "") != "train" for i in ids)


def test_duplicate_task_keeps_the_record_with_more_forks():
    i = "sql_calc-clinic-000000000007"
    few, many = _rollout(i, "sql_calc", [[True, False]]), _rollout(i, "sql_calc", [[True, False, True, True]])
    rows = ds.build([few, many], _tasks([i]), ood_domain="", uniform_keep=1.0)
    assert [r["n"] for r in rows] == [4]


def test_bce_reference_matches_definition():
    for logit, q in [(0.0, 0.5), (2.0, 1.0), (-3.0, 0.25)]:
        p = 1 / (1 + math.exp(-logit))
        assert abs(train.bce(logit, q) - (-(q * math.log(p) + (1 - q) * math.log(1 - p)))) < 1e-9
    assert train.to_example({"text": "a b", "q": 0.75}, lambda t, **k: {"input_ids": [1, 2]}, 8) == \
        {"input_ids": [1, 2], "labels": 0.75, "weights": 1.0}
