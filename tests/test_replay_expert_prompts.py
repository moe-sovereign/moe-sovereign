"""Pure-function checks of benchmarks/replay_expert_prompts.py (no database, no model calls)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benchmarks"))
import replay_expert_prompts as rp  # noqa: E402


def _row(cat, task, text, turn=1):
    return {"category": cat, "task_id": task, "turn": turn, "subtask": text,
            "request_messages": [{"role": "system", "content": f"role {cat}"}, {"role": "user", "content": text}],
            "request_options": {"temperature": 0.1}}


def test_pool_deduplicates_identical_prompts_across_tracks_and_conditions():
    a = [_row("security", "t1", "audit X"), _row("security", "t1", "audit X")]
    b = [_row("security", "t1", "audit X"), _row("security", "t1", "audit Y")]
    pool = rp.build_pool([a, b], per_category=10)
    assert sorted(i["subtask"] for i in pool) == ["audit X", "audit Y"]


def test_pool_is_capped_per_category_and_spread_over_questions():
    rows = [_row("security", "t1", f"a{i}") for i in range(6)] + [_row("security", "t2", "b0"), _row("security", "t3", "c0")]
    pool = rp.build_pool([rows], per_category=3)
    assert len(pool) == 3 and {i["task_id"] for i in pool} == {"t1", "t2", "t3"}


def test_pool_skips_rows_without_recorded_messages_or_category():
    rows = [{"category": "security", "task_id": "t", "subtask": "x"}, _row("unknown", "t", "y")]
    assert rp.build_pool([rows], per_category=5) == []


def test_model_sets_use_the_first_primary_slot_per_category():
    tpl = {name: {"experts": {"general": {"models": [{"model": f"m-{label}", "endpoint": "E1", "role": "primary"}]}}}
           for label, (_, _, name) in rp.EXPERT_SETS.items()}
    sets = rp.model_sets(tpl)
    assert set(sets) == set(rp.EXPERT_SETS) and sets["spur2/fine-tuned"]["general"] == ("m-spur2/fine-tuned", "E1")


def test_sign_test_and_paired_statistics():
    assert rp.sign_test_p(0, 0) == 1.0
    assert abs(rp.sign_test_p(5, 0) - 0.0625) < 1e-9
    scores = {f"p{i}": {"a": 8.0, "b": 6.0} for i in range(6)}
    r = rp.paired(scores, "a", "b", list(scores))
    assert r["n"] == 6 and r["wins"] == 6 and r["mean"] == 2.0 and r["losses"] == 0


def test_report_pairs_the_sets_per_prompt(tmp_path):
    pool, rows, cache = [], [], {}
    for i in range(6):
        item = _row("security", f"t{i}", f"s{i}")
        item["prompt_id"] = rp.prompt_id(item)
        pool.append(item)
        for label, score in (("spur1/fine-tuned", 6.0), ("spur2/fine-tuned", 8.0), ("spur1/pre-finetune", 4.0), ("spur2/pre-finetune", 5.0)):
            aid = f"replay-{item['prompt_id']}-{label}"
            rows.append({"prompt_id": item["prompt_id"], "expert_set": label, "audit_id": aid})
            cache[f"{aid}|j"] = {"score": score, "verdict": "PASS"}
    out = tmp_path / "r.md"
    rp.report(pool, rows, cache, ["j"], out)
    text = out.read_text()
    assert "+2.00" in text  # spur2 - spur1, fine-tuned
    assert "W/T/L 6/0/0" in text and "p=0.03" in text
    assert "Fine-tuning effect, Open Source" in text
