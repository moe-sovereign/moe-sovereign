import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks/orchestration_suite/prm"))
import build_genverifier_data as bg  # noqa: E402
import gen_rationales as gr  # noqa: E402


def rows(n, fam, q, source="fork", size=50):
    return [{"id": f"{fam}-{q}-{i}", "family": fam, "q": q, "source": source, "text": "x" * size} for i in range(n)]


def test_synthetic_rows_are_balanced_per_family_and_external_rows_are_all_kept():
    data = rows(300, "a", 0.0) + rows(40, "a", 1.0) + rows(100, "b", 0.0) + rows(100, "b", 1.0) + rows(7, "ext_math", 1.0, "pool:direct") \
        + rows(5, "a", 0.0, size=9000)
    out = bg.build(data, per_family=40, max_chars=6000, seed=1)
    count = lambda fam, lab: sum(r["family"] == fam and r["label"] == lab and not r["source"].startswith("pool") for r in out)  # noqa: E731
    assert (count("a", 0), count("a", 1), count("b", 0), count("b", 1)) == (20, 20, 20, 20)
    assert sum(r["source"].startswith("pool:") for r in out) == 7 and all(len(r["text"]) <= 6000 for r in out)
    assert bg.build(data, 40, 6000, 1) == out and bg.build(data, 40, 6000, 2) != out  # deterministic per seed


def test_verdict_parsing_and_rejection_sampling():
    assert gr.verdict_of("checked.\nVERDICT: CORRECT") == 1 and gr.verdict_of("x\nverdict: incorrect\n") == 0
    assert gr.verdict_of("VERDICT: CORRECT but unsure") is None and gr.verdict_of("no verdict") is None
    assert gr.accept(["a\nVERDICT: INCORRECT", "b\nVERDICT: CORRECT"], 1) == "b\nVERDICT: CORRECT"
    assert gr.accept(["a\nVERDICT: CORRECT", "junk"], 0) is None
    assert gr.prompt_of("[TASK]\nt\n[ANSWER]\na").endswith("[TASK]\nt\n[ANSWER]\na") and "Keep the check short" in gr.prompt_of("")


def test_bundle_split_is_by_task_and_rows_are_chat_messages():
    import genverifier_bundle as gb
    rows = [{"id": f"t{i // 3}#{i % 3}", "prompt": "p", "completion": "c\nVERDICT: CORRECT"} for i in range(60)]
    train, held = gb.split(rows, 5, 1)
    assert {r["id"].split("#")[0] for r in train}.isdisjoint({r["id"].split("#")[0] for r in held}) and len(held) == 15 and len(train) == 45
    assert gb.message_row(rows[0]) == {"messages": [{"role": "user", "content": "p"}, {"role": "assistant", "content": "c\nVERDICT: CORRECT"}]}


def test_score_from_the_verdict_token_distribution():
    import math
    import score_genverifier as sg
    assert sg.rationale_prefix("step 1\nstep 2\nVERDICT: INCORRECT") == "step 1\nstep 2\n"
    s = sg.score_from_top({" CORRECT": math.log(0.6), " INCORRECT": math.log(0.2), "x": math.log(0.1)})
    assert abs(s - 0.75) < 1e-9
    assert sg.score_from_top({"foo": -1.0}) is None
    assert sg.score_from_top({"IN": math.log(0.5), " CORRECT": math.log(0.5)}) == 0.5  # split tokenisation of INCORRECT
