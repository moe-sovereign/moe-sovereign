"""Guards for the placeholder rule-based DPO compiler."""

import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "compile_rule_based_rl_dataset",
    Path(__file__).resolve().parent.parent / "scripts" / "compile_rule_based_rl_dataset.py",
)
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


def test_missing_flags_count_as_failed():
    assert mod.evaluate_rule_based_reward({}) == -4.0
    assert mod.evaluate_rule_based_reward({"dag_valid": True}) == -2.0


def test_only_true_counts_as_passed():
    truthy = {flag: "yes" for flag in mod.REWARD_FLAGS}
    assert mod.evaluate_rule_based_reward(truthy) == -4.0
    assert mod.evaluate_rule_based_reward({flag: True for flag in mod.REWARD_FLAGS}) == 4.0


def test_cli_refuses_placeholder_by_default(tmp_path):
    out = tmp_path / "dpo.jsonl"
    assert mod.main(["--out", str(out), "--samples", "3"]) == 2
    assert not out.exists()


def test_cli_marks_placeholder_output(tmp_path):
    out = tmp_path / "dpo.jsonl"
    assert mod.main(["--out", str(out), "--samples", "3", "--allow-placeholder"]) == 0
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(rows) == 3 and all(r["placeholder"] for r in rows)
    assert mod.PLACEHOLDER_MARKER in rows[0]["chosen"][1]["content"]
