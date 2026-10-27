"""tests/test_scientific_benchmark_harness.py — Unit tests for the harness
improvements in benchmarks/run_scientific_benchmark.py: structured error
classification, best-effort JSONL sidecar/error logging, and the
pair-coverage backfill loop that guarantees every active condition lands a
valid checkpoint entry for a given task/round before moving on.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from benchmarks.run_scientific_benchmark import (
    _append_jsonl_record,
    _classify_error_response,
    _ensure_pair_coverage,
    _result_is_valid,
    MAX_BACKFILL_ATTEMPTS,
)


class TestResultIsValid:
    def test_zero_tokens_is_invalid(self):
        assert _result_is_valid({"total_tokens": 0, "judge_verdict": "PASS", "turns": []}) is False

    def test_unknown_verdict_is_invalid(self):
        assert _result_is_valid({"total_tokens": 100, "judge_verdict": "UNSCORED_FALLBACK", "turns": []}) is False

    def test_failed_turn_is_invalid(self):
        res = {"total_tokens": 100, "judge_verdict": "PASS", "turns": [{"ok": False}]}
        assert _result_is_valid(res) is False

    def test_valid_result(self):
        res = {"total_tokens": 100, "judge_verdict": "PASS", "turns": [{"ok": True}]}
        assert _result_is_valid(res) is True

    def test_missing_turns_defaults_to_valid(self):
        res = {"total_tokens": 100, "judge_verdict": "EXCELLENT"}
        assert _result_is_valid(res) is True


class TestClassifyErrorResponse:
    def test_parses_structured_moe_api_error_body(self):
        body = json.dumps({"error": {
            "message": "The response was withheld by the quality gate.",
            "type": "quality_blocked",
            "code": "plausibility_failed:empty_or_too_short",
            "request_id": "chatcmpl-abc123",
        }})
        result = _classify_error_response(422, body)
        assert result["parsed"] is True
        assert result["error_type"] == "quality_blocked"
        assert result["error_code"] == "plausibility_failed:empty_or_too_short"
        assert result["request_id"] == "chatcmpl-abc123"

    def test_malformed_json_falls_back_without_raising(self):
        result = _classify_error_response(502, "<html>Bad Gateway</html>")
        assert result["parsed"] is False
        assert result["error_type"] is None
        assert result["error_message"] == "<html>Bad Gateway</html>"

    def test_json_without_error_key_falls_back_gracefully(self):
        result = _classify_error_response(500, json.dumps({"detail": "oops"}))
        assert result["parsed"] is True
        assert result["error_type"] is None
        assert result["error_message"] is None

    def test_truncates_long_unparsable_text(self):
        long_text = "x" * 1000
        result = _classify_error_response(500, long_text)
        assert len(result["error_message"]) == 300


class TestAppendJsonlRecord(object):
    def test_writes_valid_jsonl_line(self, tmp_path):
        path = tmp_path / "sidecar.jsonl"
        _append_jsonl_record(path, {"a": 1, "b": "x"})
        _append_jsonl_record(path, {"a": 2, "b": "y"})
        lines = path.read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"a": 1, "b": "x"}
        assert json.loads(lines[1]) == {"a": 2, "b": "y"}

    def test_write_failure_is_swallowed_not_raised(self, tmp_path):
        # Directory as "file" path guarantees an OSError on open(..., "a").
        bad_path = tmp_path  # a directory, not a file
        _append_jsonl_record(bad_path, {"a": 1})  # must not raise


@pytest.mark.asyncio
class TestEnsurePairCoverage:
    async def test_backfills_missing_run_until_valid(self, monkeypatch):
        conditions = [("compound_ai", "tmpl-a"), ("native_baseline", "qwen3.8:27b")]
        completed_runs: dict = {}
        all_results: list = []
        valid_res = {"total_tokens": 100, "judge_verdict": "PASS", "turns": [{"ok": True}], "score": 7.0}

        call_count = {"n": 0}

        async def fake_run(*args, **kwargs):
            call_count["n"] += 1
            return valid_res

        monkeypatch.setattr(
            "benchmarks.run_scientific_benchmark.run_single_test_condition",
            fake_run,
        )
        monkeypatch.setattr(
            "benchmarks.run_scientific_benchmark._write_interim_reports",
            lambda *a, **k: None,
        )

        await _ensure_pair_coverage(
            client=AsyncMock(),
            r=1,
            tc={"id": "task-1"},
            conditions=conditions,
            completed_runs=completed_runs,
            all_results=all_results,
            checkpoint_file=None,
            checkpoint_data={},
            run_id="run-x",
            timestamp="ts-x",
            permanently_failed={},
        )
        assert call_count["n"] == 2  # both conditions were missing, both backfilled once

    async def test_stops_after_max_backfill_attempts(self, monkeypatch):
        conditions = [("compound_ai", "tmpl-a")]
        completed_runs: dict = {}
        all_results: list = []
        invalid_res = {"total_tokens": 0, "judge_verdict": "N/A", "turns": []}

        attempts = {"n": 0}

        async def always_invalid(*args, **kwargs):
            attempts["n"] += 1
            return invalid_res

        monkeypatch.setattr(
            "benchmarks.run_scientific_benchmark.run_single_test_condition",
            always_invalid,
        )
        monkeypatch.setattr(
            "benchmarks.run_scientific_benchmark._write_interim_reports",
            lambda *a, **k: None,
        )

        permanently_failed: dict = {}
        await _ensure_pair_coverage(
            client=AsyncMock(),
            r=1,
            tc={"id": "task-1"},
            conditions=conditions,
            completed_runs=completed_runs,
            all_results=all_results,
            checkpoint_file=None,
            checkpoint_data={},
            run_id="run-x",
            timestamp="ts-x",
            permanently_failed=permanently_failed,
        )
        assert attempts["n"] == MAX_BACKFILL_ATTEMPTS
        assert "r1_task-1_compound_ai" not in completed_runs
        assert "r1_task-1_compound_ai" in permanently_failed

    async def test_skips_condition_already_valid_in_checkpoint(self, monkeypatch):
        conditions = [("compound_ai", "tmpl-a")]
        valid_res = {"total_tokens": 100, "judge_verdict": "PASS", "turns": [{"ok": True}]}
        completed_runs = {"r1_task-1_compound_ai": valid_res}
        all_results: list = []

        run_mock = AsyncMock()
        monkeypatch.setattr(
            "benchmarks.run_scientific_benchmark.run_single_test_condition",
            run_mock,
        )

        await _ensure_pair_coverage(
            client=AsyncMock(),
            r=1,
            tc={"id": "task-1"},
            conditions=conditions,
            completed_runs=completed_runs,
            all_results=all_results,
            checkpoint_file=None,
            checkpoint_data={},
            run_id="run-x",
            timestamp="ts-x",
            permanently_failed={},
        )
        run_mock.assert_not_called()

    async def test_skips_condition_already_permanently_failed(self, monkeypatch):
        conditions = [("compound_ai", "tmpl-a")]
        completed_runs: dict = {}
        all_results: list = []
        permanently_failed = {"r1_task-1_compound_ai": {"attempts": 2, "last_attempt_utc": "2026-09-01T00:00:00Z"}}

        run_mock = AsyncMock()
        monkeypatch.setattr(
            "benchmarks.run_scientific_benchmark.run_single_test_condition",
            run_mock,
        )

        await _ensure_pair_coverage(
            client=AsyncMock(),
            r=1,
            tc={"id": "task-1"},
            conditions=conditions,
            completed_runs=completed_runs,
            all_results=all_results,
            checkpoint_file=None,
            checkpoint_data={},
            run_id="run-x",
            timestamp="ts-x",
            permanently_failed=permanently_failed,
        )
        run_mock.assert_not_called()  # never re-attempted, not even once


class TestJudgeReference:
    def test_single_turn_reference_from_expected_answer(self):
        from benchmarks.run_scientific_benchmark import _derive_ground_truth
        ref = _derive_ground_truth({"expected_answer": {"annual_mwh": 7358.4}})
        assert "7358.4" in ref

    def test_multi_turn_reference_from_last_turn(self):
        from benchmarks.run_scientific_benchmark import _derive_ground_truth
        ref = _derive_ground_truth({"turns": [{"prompt": "a"}, {"prompt": "b", "expected_behavior": "quorum loss"}]})
        assert ref == "quorum loss"

    def test_missing_reference_is_empty(self):
        from benchmarks.run_scientific_benchmark import _derive_ground_truth
        assert _derive_ground_truth({"id": "x"}) == ""

    @pytest.mark.asyncio
    async def test_judge_prompt_contains_reference_and_rubric(self, monkeypatch):
        import benchmarks.run_scientific_benchmark as rsb
        captured = {}

        async def _fake_query(client, model, messages, **kwargs):
            captured["prompt"] = messages[0]["content"]
            return {"ok": True, "content": '{"score": 7.0, "reasoning": "ok", "verdict": "PASS"}'}

        monkeypatch.setattr(rsb, "query_moe_orchestrator", _fake_query)
        tc = {
            "id": "t1", "discipline": "d", "task_name": "n", "complexity": "expert",
            "expected_answer": {"required_concepts": ["acquire"]},
            "scoring": {"rubric": "RUBRIC-MARKER"},
        }
        res = await rsb.judge_evaluation(client=None, test_case=tc, prompt="p", response_text="r")
        assert "acquire" in captured["prompt"]
        assert "RUBRIC-MARKER" in captured["prompt"]
        assert float(res.get("score")) == 7.0


class TestNumericTolerance:
    def test_formatted_numbers_match(self):
        from benchmarks.run_scientific_benchmark import numeric_tolerance_score
        assert numeric_tolerance_score("Cost: 1,361,304.00 EUR", {"c": 1361304.0}, 0.5) == 10.0

    def test_out_of_tolerance_fails(self):
        from benchmarks.run_scientific_benchmark import numeric_tolerance_score
        assert numeric_tolerance_score("Cost: 1300000", {"c": 1361304.0}, 0.5) == 0.0

    def test_keyword_type_unchanged(self):
        from benchmarks.run_scientific_benchmark import deterministic_score
        assert deterministic_score("Acquire Release", {"required_keywords": ["Acquire", "Release"]}) == 10.0


class TestRedisPasswordLookup:
    def test_env_var_wins(self, monkeypatch):
        from benchmarks import run_scientific_benchmark as rsb
        monkeypatch.setenv("REDIS_PASSWORD", "from-env")
        assert rsb._redis_password() == "from-env"

    def test_reads_repo_env_file_when_env_unset(self, monkeypatch, tmp_path):
        from benchmarks import run_scientific_benchmark as rsb
        monkeypatch.delenv("REDIS_PASSWORD", raising=False)
        (tmp_path / ".env").write_text("OTHER=1\nREDIS_PASSWORD='from-file'\n")
        monkeypatch.setattr(rsb, "BASE_DIR", tmp_path / "benchmarks")
        assert rsb._redis_password() == "from-file"

    def test_none_when_nothing_configured(self, monkeypatch, tmp_path):
        from benchmarks import run_scientific_benchmark as rsb
        monkeypatch.delenv("REDIS_PASSWORD", raising=False)
        monkeypatch.setattr(rsb, "BASE_DIR", tmp_path / "benchmarks")
        assert rsb._redis_password() is None

    def test_no_hardcoded_password_literal_in_source(self):
        import pathlib
        import re
        src = pathlib.Path("benchmarks/run_scientific_benchmark.py").read_text()
        assert not re.search(r"password\s*=\s*[\"'][A-Za-z0-9+/_\-]{12,}[\"']", src)
