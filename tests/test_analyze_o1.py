import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks/orchestration_suite/experts"))
import analyze_o1 as A  # noqa: E402


def make_pool(n_tasks=60, seed=0, expert_edge=0.0, mix_edge=False):
    """Synthetic pool: the judge solves 4 % per sample, the expert `exp_a` solves 4 % + expert_edge on family fam1; verifier scores are informative."""
    rnd = random.Random(seed)
    pool, scores = [], {}
    for i in range(n_tasks):
        tid, fam = f"t{i}", ("fam1" if i % 2 == 0 else "fam2")
        for k in range(24):
            q = float(rnd.random() < 0.04)
            pool.append({"id": f"{tid}#judge{k}", "task_id": tid, "src": "judgeA", "family": fam, "q": q}); scores[f"{tid}#judge{k}"] = q * 0.25 + rnd.random() * 0.75
        for e in ("exp_a", "exp_b"):
            for k in range(12):
                p = 0.04 + (expert_edge if (e == "exp_a" and fam == "fam1") else 0.0)
                q = float(rnd.random() < p)
                pool.append({"id": f"{tid}#{e}{k}", "task_id": tid, "src": e, "family": fam, "q": q}); scores[f"{tid}#{e}{k}"] = q * 0.25 + rnd.random() * 0.75
    return pool, scores


def test_split_is_fixed_and_balanced():
    ids = [f"t{i}" for i in range(400)]
    assert [A.split_of(i) for i in ids] == [A.split_of(i) for i in ids] and 150 < sum(A.split_of(i) for i in ids) < 250


def test_h1_finds_a_real_specialist_and_not_a_noise_expert():
    pool, _ = make_pool(n_tasks=200, expert_edge=0.30)
    g, fam = A.group(pool), A.family_of(pool)
    r = A.h1(g, fam, boot=300)
    assert r["met"] and r["best"]["expert"] == "exp_a" and r["best"]["family"] == "fam1" and r["best"]["diff"] > 0.2
    pool0, _ = make_pool(n_tasks=200, expert_edge=0.0)
    assert not A.h1(A.group(pool0), A.family_of(pool0), boot=300)["met"]


def test_h2_picks_the_specialist_on_dev_and_wins_on_test():
    pool, _ = make_pool(n_tasks=300, expert_edge=0.30)
    g, fam = A.group(pool), A.family_of(pool)
    r = A.h2(g, fam, boot=300)
    assert r["choice_on_dev"]["fam1"] == "exp_a" and r["choice_on_dev"]["fam2"] in ("judge", "exp_a", "exp_b")
    assert r["met"] and r["diff"] > 0.05
    pool0, _ = make_pool(n_tasks=300, expert_edge=0.0)
    assert not A.h2(A.group(pool0), A.family_of(pool0), boot=300)["met"]


def test_h3_matched_n_does_not_credit_the_mere_gain_of_choosing_among_more_answers():
    pool, scores = make_pool(n_tasks=80, expert_edge=0.0)     # experts exactly as good as the judge
    r = A.h3(pool, scores, boot=300, draws=60)
    assert not r["met"] and abs(r["mix_minus_judge"]["diff"]) < 0.05 and r["tasks"] == 80
    pool2, scores2 = make_pool(n_tasks=80, expert_edge=0.45)  # the experts really are better on one family
    r2 = A.h3(pool2, scores2, boot=300, draws=60)
    assert r2["mix_minus_judge"]["diff"] > 0.0 and r2["judge_selection_curve"][24] >= r2["judge_selection_curve"][1]


def test_select_takes_the_highest_score():
    assert A.select([(0.2, 1.0), (0.9, 0.0), (0.5, 1.0)]) == 0.0
