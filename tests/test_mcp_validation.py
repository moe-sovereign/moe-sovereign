"""
test_mcp_validation.py — Unit tests for MCP tool input validation and output correctness.

All tools are pure Python functions decorated with @mcp.tool() (which conftest.py
makes a transparent pass-through). No live network or database connections needed.

Covers:
  - calculate(): safe AST evaluation, injection prevention, percentage notation
  - hash_text(): valid algorithm selection, rejection of unknown algorithms
  - base64_codec(): encode/decode round-trip, invalid mode handling
  - date_diff(): valid date arithmetic, invalid date format handling
  - calendar_facts(): localized structured calendar facts and boundary cases
  - gcd_lcm(): gcd/lcm/both operations and integer correctness
  - statistics_calc(): standard statistical measures, unknown-op silencing
  - subnet_calc(): valid CIDR parsing, invalid CIDR error handling
"""

import json

import pytest

# conftest.py has already stubbed mcp/fastapi/uvicorn so the import is safe.
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp_server"))

from mcp_server.server import (
    _TOOL_ACCESS_KIND,
    _TOOL_DESCRIPTIONS,
    _TOOL_REGISTRY,
    _input_schema,
    build_tools_catalog,
    execute_tool,
    InvokeRequest,
    base64_codec,
    calculate,
    calendar_facts,
    date_diff,
    day_of_week,
    gcd_lcm,
    hash_text,
    statistics_calc,
    subnet_calc,
    vlsm_subnet_calc,
)


def test_tools_catalog_exposes_versioned_structured_contracts():
    tools = {item["name"]: item for item in build_tools_catalog()["tools"]}

    assert len(tools) == len(_TOOL_DESCRIPTIONS)
    assert set(tools).issubset(_TOOL_REGISTRY)
    for name in ("calendar_facts", "gcd_lcm", "unit_convert"):
        contract = tools[name]
        assert contract["contract_id"].startswith("moe.precision.")
        assert contract["contract_version"] == "1.0.0"
        assert len(contract["contract_hash"]) == 64
        assert contract["structured_result"] is True
        assert contract["inputSchema"]["additionalProperties"] is False
        assert isinstance(contract["outputSchema"], dict)
        assert contract["normalization_policy"] == {
            "apply_schema_defaults": True,
            "reject_unknown_properties": True,
        }
        assert contract["retry_policy"] == {
            "max_attempts": 1,
            "argument_mutation": False,
        }
        assert contract["cache_policy"] == {"mode": "bypass"}

    assert tools["calculate"]["structured_result"] is False
    assert tools["calculate"]["contract_version"] == "0.0.0"
    assert tools["calculate"]["outputSchema"] == {"type": "string"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,args,expected",
    [
        (
            "calendar_facts",
            {"date_str": "2026-07-29", "locale": "de"},
            ("weekday_name", "Mittwoch"),
        ),
        (
            "gcd_lcm",
            {"a": 391, "b": 299, "operation": "gcd"},
            ("gcd", 23),
        ),
    ],
)
async def test_invoke_keeps_legacy_result_and_adds_valid_structured_result(
    tool,
    args,
    expected,
):
    response = await execute_tool(InvokeRequest(tool=tool, args=args))

    assert isinstance(response["result"], str)
    structured = response["structured_result"]
    assert structured["status"] == "completed"
    assert structured["tool"] == tool
    assert structured["input_normalized"] == args
    assert structured["facts"][expected[0]] == expected[1]
    assert len(structured["contract_hash"]) == 64
    assert len(structured["result_hash"]) == 64
    assert structured["source"]["version"]
    assert structured["warnings"] == []


@pytest.mark.asyncio
async def test_invoke_rejects_wrong_type_and_unknown_fields_before_tool_call():
    wrong_type = await execute_tool(
        InvokeRequest(
            tool="gcd_lcm",
            args={"a": "391", "b": 299, "operation": "gcd"},
        )
    )
    unknown = await execute_tool(
        InvokeRequest(
            tool="gcd_lcm",
            args={"a": 391, "b": 299, "operation": "gcd", "extra": True},
        )
    )

    assert wrong_type["error_code"] == "input_schema_invalid"
    assert unknown["error_code"] == "input_schema_invalid"


@pytest.mark.asyncio
async def test_invoke_rejects_invalid_structured_output(monkeypatch):
    monkeypatch.setattr(
        "mcp_server.server._structured_facts",
        lambda *_args, **_kwargs: {"gcd": "not-an-integer"},
    )

    response = await execute_tool(
        InvokeRequest(
            tool="gcd_lcm",
            args={"a": 391, "b": 299, "operation": "gcd"},
        )
    )

    assert response["error_code"] == "output_schema_invalid"
    assert "structured_result" not in response

# ── calculate() ───────────────────────────────────────────────────────────────


def test_calculate_basic_addition():
    result = calculate("2+2")
    assert "4" in result


def test_calculate_division():
    result = calculate("10/2")
    assert "5" in result


def test_calculate_power_expression():
    result = calculate("2**10")
    assert "1024" in result


def test_calculate_rejects_import_injection(capsys):
    """__import__ must not execute; the function must return an error string.

    Previously, a security vulnerability caused the SymPy fallback to evaluate
    arbitrary Python (including shell commands) when the safe-AST evaluator
    raised an exception.  The fix restricts the SymPy fallback to SyntaxError
    only, so unsafe-but-valid Python expressions are rejected at the AST stage.
    """
    result = calculate("__import__('os').system('id')")
    assert result.startswith("Error") or result.startswith("Fehler"), (
        f"Expected an error string, got: {result!r}"
    )
    # The string 'uid=' would appear in captured stdout if os.system ran.
    captured = capsys.readouterr()
    assert "uid=" not in captured.out, (
        "os.system('id') was executed — the SymPy injection vulnerability is still present!"
    )


def test_calculate_rejects_open_injection():
    """open() is not in the safe whitelist and must be refused."""
    result = calculate("open('/etc/passwd').read()")
    assert result.startswith("Error") or result.startswith("Fehler") or "Error" in result or "error" in result.lower()


def test_calculate_percentage_notation():
    """'15% of 100' should evaluate to 15."""
    result = calculate("15% of 100")
    assert "15" in result


def test_calculate_math_functions():
    """sqrt() is whitelisted and must produce a valid floating-point result."""
    result = calculate("sqrt(4)")
    assert "2" in result


# ── hash_text() ───────────────────────────────────────────────────────────────

_HASH_LENGTHS = {
    "md5":    32,
    "sha1":   40,
    "sha224": 56,
    "sha256": 64,
    "sha384": 96,
    "sha512": 128,
}


@pytest.mark.parametrize("algorithm,expected_hex_len", _HASH_LENGTHS.items())
def test_hash_text_valid_algorithm(algorithm, expected_hex_len):
    """Each supported algorithm returns a hex digest of the correct length."""
    result = hash_text("hello", algorithm)
    # The digest appears after the '=' sign.
    assert "=" in result
    digest = result.split("=")[-1].strip()
    assert len(digest) == expected_hex_len, (
        f"Expected digest length {expected_hex_len} for {algorithm}, got {len(digest)}"
    )


def test_hash_text_invalid_algorithm():
    """An unsupported algorithm name must return a human-readable error, not raise."""
    result = hash_text("hello", "rot13")
    assert "Unknown algorithm" in result or "Fehler" in result


def test_hash_text_sha256_known_value():
    """SHA-256 of the empty string has a known hex value."""
    result = hash_text("", "sha256")
    known = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert known in result


# ── base64_codec() ────────────────────────────────────────────────────────────


def test_base64_encode():
    result = base64_codec("hello", "encode")
    assert "aGVsbG8=" in result


def test_base64_decode():
    result = base64_codec("aGVsbG8=", "decode")
    assert "hello" in result


def test_base64_roundtrip():
    """Encode then decode must recover the original string."""
    original = "MoE Sovereign test payload"
    encoded_result = base64_codec(original, "encode")
    # Extract the encoded string (everything after ': ')
    encoded = encoded_result.split(": ", 1)[-1].strip()
    decoded_result = base64_codec(encoded, "decode")
    assert original in decoded_result


def test_base64_invalid_mode():
    """An unrecognised mode must return an error string, not raise."""
    result = base64_codec("hello", "compress")
    assert "Fehler" in result or "mode" in result


def test_base64_malformed_input_for_decode():
    """Non-base64 input for decode must return an error string, not raise."""
    result = base64_codec("!!!not-base64!!!", "decode")
    assert result.startswith("Error") or result.startswith("Fehler") or "Error" in result


# ── date_diff() ───────────────────────────────────────────────────────────────


def test_date_diff_valid():
    result = date_diff("2024-01-01", "2024-01-31")
    assert "30" in result   # 30 calendar days apart


def test_date_diff_same_date():
    result = date_diff("2024-06-15", "2024-06-15")
    assert "0" in result


def test_date_diff_uses_calendar_delta_not_365_30_approximation():
    result = date_diff("2024-01-31", "2024-03-01")
    assert result == (
        "From 2024-01-31 to 2024-03-01: 30 days total "
        "(0 years, 1 months, 1 days calendar delta)"
    )
    assert "≈" not in result


def test_date_diff_leap_year_delta_is_exact_and_order_independent():
    expected = (
        "From 2024-02-29 to 2025-03-01: 366 days total "
        "(1 years, 0 months, 1 days calendar delta)"
    )
    assert date_diff("2024-02-29", "2025-03-01") == expected
    assert date_diff("2025-03-01", "2024-02-29") == expected


def test_date_diff_invalid_format_returns_error():
    """DD-MM-YYYY is not the expected format; must return an error string."""
    result = date_diff("31-01-2024", "2024-01-01")
    assert result.startswith("Error") or result.startswith("Fehler")


def test_date_diff_non_date_string_returns_error():
    result = date_diff("not-a-date", "2024-01-01")
    assert result.startswith("Error") or result.startswith("Fehler")


# ── calendar_facts() ─────────────────────────────────────────────────────────


def _calendar_json(date_str: str, locale: str = "de") -> dict:
    return json.loads(calendar_facts(date_str, locale))


def test_calendar_facts_german_leap_day_is_structured_and_exact():
    result = _calendar_json("2024-02-29", "de")

    assert result == {
        "calendar_system": "proleptic_gregorian",
        "date": "2024-02-29",
        "day": 29,
        "day_of_year": 60,
        "days_in_month": 29,
        "days_in_year": 366,
        "is_leap_year": True,
        "is_weekend": False,
        "iso_week": 9,
        "iso_week_year": 2024,
        "locale": "de",
        "month": 2,
        "month_name": "Februar",
        "quarter": 1,
        "weekday_iso": 4,
        "weekday_name": "Donnerstag",
        "year": 2024,
    }


def test_calendar_facts_reports_iso_week_year_boundary():
    result = _calendar_json("2021-01-01", "en")

    assert result["weekday_name"] == "Friday"
    assert result["weekday_iso"] == 5
    assert result["iso_week"] == 53
    assert result["iso_week_year"] == 2020
    assert result["day_of_year"] == 1


def test_calendar_facts_reports_month_quarter_and_weekend_boundary():
    result = _calendar_json("2024-06-30", "en")

    assert result["month_name"] == "June"
    assert result["days_in_month"] == 30
    assert result["quarter"] == 2
    assert result["weekday_name"] == "Sunday"
    assert result["is_weekend"] is True


@pytest.mark.parametrize(
    "date_str",
    ["29.02.2024", "2023-02-29", "2024-2-09", "today", ""],
)
def test_calendar_facts_rejects_non_iso_or_invalid_dates(date_str):
    with pytest.raises(ValueError, match="valid ISO date"):
        calendar_facts(date_str, "de")


def test_calendar_facts_rejects_unknown_locale():
    with pytest.raises(ValueError, match="locale must be one of"):
        calendar_facts("2026-07-29", "de-DE")


def test_calendar_facts_registry_and_schema_are_complete():
    assert _TOOL_REGISTRY["calendar_facts"] is calendar_facts
    assert "calendar_facts" in _TOOL_DESCRIPTIONS
    assert _TOOL_ACCESS_KIND["calendar_facts"] == "read"
    assert _input_schema(calendar_facts) == {
        "type": "object",
        "properties": {
            "date_str": {"type": "string"},
            "locale": {"type": "string", "default": "de"},
        },
        "required": ["date_str"],
        "additionalProperties": False,
    }


def test_day_of_week_remains_backward_compatible():
    result = day_of_week("2026-07-29")
    assert result == "2026-07-29 is a Wednesday (CW 31, day 210 of year 2026)"


# ── gcd_lcm() ─────────────────────────────────────────────────────────────────

# gcd(12, 8) = 4, lcm(12, 8) = 24
_A, _B = 12, 8


def test_gcd_lcm_gcd_operation():
    result = gcd_lcm(_A, _B, "gcd")
    assert "4" in result
    # The LCM value should NOT appear in a gcd-only result.
    assert "24" not in result


def test_gcd_lcm_lcm_operation():
    result = gcd_lcm(_A, _B, "lcm")
    assert "24" in result
    # The GCD value should NOT appear in an lcm-only result.
    assert result.count("4") == result.count("24") * 1  # 24 contains '4'; allow that


def test_gcd_lcm_both_operation():
    result = gcd_lcm(_A, _B, "both")
    assert "4" in result
    assert "24" in result


def test_gcd_lcm_unknown_operation_falls_through():
    """An unrecognised operation silently falls through to 'both' behaviour."""
    result = gcd_lcm(_A, _B, "modulo")
    # Must still return a valid result, not raise.
    assert isinstance(result, str)
    assert "Fehler" not in result


def test_gcd_lcm_coprime_numbers():
    """gcd(7, 11) = 1, lcm(7, 11) = 77."""
    result = gcd_lcm(7, 11, "both")
    assert "1" in result
    assert "77" in result


# ── statistics_calc() ─────────────────────────────────────────────────────────


def test_statistics_calc_mean_and_median():
    result = statistics_calc("1,2,3,4,5", "mean,median")
    assert "mean" in result
    assert "median" in result
    assert "3.0" in result  # mean = 3, median = 3


def test_statistics_calc_default_operations_present():
    result = statistics_calc("10,20,30")
    for op in ("mean", "median", "min", "max", "sum", "count"):
        assert op in result, f"Expected '{op}' in statistics output"


def test_statistics_calc_unknown_op_silently_skipped():
    """Unknown operations are silently ignored; the function must not raise."""
    result = statistics_calc("1,2,3", "unknown_op")
    assert isinstance(result, str)
    assert "Fehler" not in result


def test_statistics_calc_empty_data_returns_error():
    result = statistics_calc("", "mean")
    assert "Error" in result or "Fehler" in result or "keine" in result.lower()


# ── subnet_calc() ─────────────────────────────────────────────────────────────


def test_subnet_calc_class_c():
    result = subnet_calc("192.168.1.0/24")
    assert "192.168.1.0" in result      # network address
    assert "192.168.1.255" in result    # broadcast


def test_subnet_calc_host_count_class_c():
    result = subnet_calc("192.168.1.0/24")
    assert "254" in result              # 254 usable hosts in a /24


def test_subnet_calc_slash_30():
    """/30 has exactly 2 usable host addresses."""
    result = subnet_calc("10.0.0.0/30")
    assert "2" in result


def test_subnet_calc_invalid_cidr_returns_error():
    result = subnet_calc("999.999.0.0/24")
    assert result.startswith("Error") or result.startswith("Fehler")


def test_subnet_calc_non_cidr_string_returns_error():
    result = subnet_calc("not-an-ip")
    assert result.startswith("Error") or result.startswith("Fehler")


# ── vlsm_subnet_calc() ────────────────────────────────────────────────────────
# Added after a live scientific-benchmark run showed the planner routing VLSM
# ("split one CIDR into several named subnets by host count") to subnet_calc,
# whose schema only accepts a single "cidr" — every such request failed with
# `MCP contract drift for subnet_calc: pre_call_schema_invalid:args:additionalProperties`
# (0 usable tokens, all three compound-AI conditions of that task discarded).


def test_vlsm_subnet_calc_allocates_largest_first_textbook_case():
    """10.180.0.0/19 split for A=2000, B=1000, C=250, D=250 hosts — the exact
    request that failed live. Allocation order follows VLSM convention
    (largest requirement first) regardless of input order."""
    result = vlsm_subnet_calc(
        "10.180.0.0/19",
        [
            {"id": "A", "hosts": 2000},
            {"id": "B", "hosts": 1000},
            {"id": "C", "hosts": 250},
            {"id": "D", "hosts": 250},
        ],
    )
    assert "A: 10.180.0.0/21" in result
    assert "B: 10.180.8.0/22" in result
    assert "C: 10.180.12.0/24" in result
    assert "D: 10.180.13.0/24" in result
    assert "2046 usable hosts (needed 2000)" in result
    assert "254 usable hosts (needed 250)" in result


def test_vlsm_subnet_calc_preserves_input_order_in_output():
    """Allocation internally sorts largest-first, but the report must list
    subnets in the order the caller supplied them."""
    result = vlsm_subnet_calc(
        "10.0.0.0/24",
        [{"id": "small", "hosts": 10}, {"id": "big", "hosts": 100}],
    )
    assert result.index("small:") < result.index("big:")


def test_vlsm_subnet_calc_parent_too_small_returns_error():
    result = vlsm_subnet_calc(
        "10.0.0.0/28",
        [{"id": "A", "hosts": 100}],
    )
    assert result.startswith("Error")


def test_vlsm_subnet_calc_second_subnet_does_not_fit_returns_error():
    """First subnet fits; the parent has no room left for the second."""
    result = vlsm_subnet_calc(
        "10.0.0.0/24",
        [{"id": "A", "hosts": 200}, {"id": "B", "hosts": 200}],
    )
    assert result.startswith("Error")


def test_vlsm_subnet_calc_invalid_cidr_returns_error():
    result = vlsm_subnet_calc("not-an-ip", [{"id": "A", "hosts": 10}])
    assert result.startswith("Error")


def test_calculate_closes_missing_trailing_parentheses_and_shows_the_evaluated_expression():
    assert calculate("((2+3)*4") == "((2+3)*4) = 20"
    assert calculate("(((1+1)*3") == "(((1+1)*3)) = 6"


def test_calculate_reads_caret_as_power_but_still_rejects_other_operators():
    assert calculate("2^3") == "2**3 = 8"
    assert calculate("2 & 3").startswith("Error")


def test_calculate_leaves_valid_expressions_untouched():
    assert calculate("(2+3)*4") == "(2+3)*4 = 20"
    assert calculate("10/4") == "10/4 = 2.5"
