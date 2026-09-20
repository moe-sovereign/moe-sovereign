"""
services/pipeline/contracts.py — Typed contracts between pipeline stages.

parse_plan() extracts a planner response without discarding routing fields.
validate_plan_tasks() applies the deterministic, task-aware execution contract
before the plan can be handed to LangGraph.
"""

import copy
import difflib
import hashlib
import json
import logging
import os
import re
from datetime import date
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

logger = logging.getLogger("MOE-SOVEREIGN")


@dataclass
class PlanTask:
    category: str = "general"
    instruction: str = ""
    payload: dict = field(default_factory=dict)


@dataclass
class PlannerPlan:
    tasks: list = field(default_factory=list)   # list[PlanTask]
    raw: str = ""
    valid: bool = False


@dataclass(frozen=True)
class PlannerContractIssue:
    """One deterministic planner-contract violation."""

    task_index: int
    code: str
    message: str
    field: str = ""


class PlannerContractError(ValueError):
    """Raised when planner output cannot be executed as declared."""

    def __init__(self, issues: Sequence[PlannerContractIssue]):
        self.issues = list(issues)
        detail = "; ".join(issue.message for issue in self.issues)
        super().__init__(detail or "planner contract is invalid")

    def repair_instruction(self) -> str:
        """Return a bounded repair instruction without changing task intent."""
        details = "\n".join(
            f"- task[{issue.task_index}] {issue.code}: {issue.message}"
            for issue in self.issues
        )
        return (
            "\n\nCONTRACT REPAIR REQUIRED\n"
            "The previous plan was not executable. Correct only the listed "
            "schema problems and return the complete JSON task array again. "
            "Do not remove or downgrade a precision_tools task.\n"
            f"{details}\n"
            "Return JSON only:"
        )


def is_task_result_ref(value: Any) -> bool:
    """Return True for an explicit chained-operand reference object.

    A reference is ``{"$task_result": "<task_id>", "field": "<optional>"}``.
    ``field`` defaults to ``"result"`` when absent, matching the field name
    every precision tool's structured facts use for its primary output.
    """
    return (
        isinstance(value, Mapping)
        and isinstance(value.get("$task_result"), str)
        and value.get("$task_result").strip() != ""
    )


def resolve_task_result_refs(
    args: Mapping[str, Any],
    resolved_results: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    """Substitute ``$task_result`` references with already-produced facts.

    Returns ``None`` (fail-closed) the moment any reference cannot be
    resolved — a partially substituted argument set is never handed to a
    tool, since that would silently run the calculation with a missing or
    guessed operand.
    """
    resolved: dict[str, Any] = {}
    for key, value in args.items():
        if is_task_result_ref(value):
            task_id = str(value["$task_result"])
            field = str(value.get("field") or "result")
            facts = resolved_results.get(task_id)
            if not isinstance(facts, Mapping) or field not in facts:
                return None
            resolved[key] = facts[field]
        elif isinstance(value, list):
            new_list = []
            for item in value:
                if is_task_result_ref(item):
                    task_id = str(item["$task_result"])
                    field = str(item.get("field") or "result")
                    facts = resolved_results.get(task_id)
                    if not isinstance(facts, Mapping) or field not in facts:
                        return None
                    new_list.append(facts[field])
                else:
                    new_list.append(item)
            resolved[key] = new_list
        else:
            resolved[key] = value
    return resolved


def _find_task_result_ref_ids(args: Mapping[str, Any]) -> list[str]:
    """Collect every ``$task_result`` reference's task id inside ``args``."""
    ids: list[str] = []
    for value in args.values():
        if is_task_result_ref(value):
            ids.append(str(value["$task_result"]))
        elif isinstance(value, list):
            for item in value:
                if is_task_result_ref(item):
                    ids.append(str(item["$task_result"]))
    return ids


@dataclass(frozen=True)
class RequiredPrecisionIntent:
    """One explicit input operation that must retain deterministic routing."""

    tool: str
    args: dict[str, Any]
    source_item: int | None = None


def canonical_json_hash(value: Any) -> str:
    """Return a stable SHA-256 hash for JSON-compatible contract material."""
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def precision_evidence_input(
    args: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply the discovered contract's redaction policy to evidence inputs."""
    projected = copy.deepcopy(dict(args))
    policy = schema.get("evidence_policy") or {}
    fields = policy.get("redact_input_fields") if isinstance(policy, Mapping) else []
    for field_name in fields or []:
        value = projected.get(field_name)
        if isinstance(value, str):
            encoded = value.encode("utf-8")
            projected[field_name] = {
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "utf8_bytes": len(encoded),
            }
    return projected


def canonical_tool_catalog_hash(
    tool_schemas: Mapping[str, Mapping[str, Any]] | None,
) -> str:
    """Fingerprint the complete discovered tool contract, not just names."""
    return canonical_json_hash(dict(tool_schemas or {}))


def tool_schema_contract_hash(schema: Mapping[str, Any] | None) -> str:
    """Use the MCP-declared contract hash, with a legacy schema fallback."""
    if not isinstance(schema, Mapping):
        return ""
    declared = str(schema.get("contract_hash") or "")
    return declared or canonical_json_hash(dict(schema))


def _close_open_json(text: str):
    """Balance the brackets of a JSON text a small model wrote sloppily.

    Missing closers are appended and a closer that does not match the innermost open bracket is dropped (typical: one
    ``}`` too many after a long expression, or ``]`` written before the object was closed). Returns None when the text
    ends inside a string. The caller accepts the result only if it parses as JSON.
    """
    stack: list[str] = []
    out: list[str] = []
    in_str = esc = False
    for ch in text:
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
            out.append(ch)
        elif ch in "[{":
            stack.append("]" if ch == "[" else "}")
            out.append(ch)
        elif ch in "]}":
            if stack and stack[-1] == ch:
                stack.pop()
                out.append(ch)
            # else: mismatching closer, dropped
        else:
            out.append(ch)
    if in_str:
        return None
    return "".join(out) + "".join(reversed(stack))


def _first_json(text: str):
    dec = json.JSONDecoder()
    positions = sorted(
        pos for pos in (text.find("["), text.find("{")) if pos >= 0
    )
    if positions and text[positions[0]] == "[":
        # A truncated top-level array (missing closing brackets) is repaired as a whole instead of falling back to
        # its first inner object, which silently reduced a multi-task plan to its first task.
        closed = _close_open_json(text[positions[0]:].rstrip())
        if closed is not None:
            try:
                repaired = json.loads(closed)
                if isinstance(repaired, list):
                    return repaired
            except (json.JSONDecodeError, ValueError):
                pass
    for start in positions:
        pos = start
        opener = text[start]
        while pos >= 0:
            try:
                obj, _ = dec.raw_decode(text, pos)
                return obj
            except (json.JSONDecodeError, ValueError):
                pos = text.find(opener, pos + 1)
    return None


def parse_plan(raw: str) -> PlannerPlan:
    plan = PlannerPlan(raw=raw or "")
    cleaned = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S)
    obj = _first_json(cleaned)
    _array_at, _object_at = cleaned.find("["), cleaned.find("{")
    if _array_at >= 0 and (_object_at < 0 or _array_at < _object_at) and not isinstance(obj, list):
        # The answer is a JSON array that does not parse as a whole; one of its inner objects is not the plan.
        logger.warning("contracts: planner array is malformed JSON (chars=%d); rejecting instead of keeping a partial plan", len(raw or ""))
        return plan
    if isinstance(obj, dict) and "tasks" not in obj and ("task" in obj or "category" in obj or "instruction" in obj or "description" in obj or "mcp_tool" in obj):
        tasks = [obj]
    else:
        tasks = obj.get("tasks") if isinstance(obj, dict) else obj
    if isinstance(tasks, list):
        for t in tasks:
            if isinstance(t, dict):
                cat = str(t.get("category") or t.get("task_type") or t.get("type") or "general")
                instruction = str(t.get("instruction") or t.get("task") or t.get("task_description") or t.get("description") or "")
                if cat or instruction:
                    payload = dict(t)
                    if "task" not in payload and instruction:
                        payload["task"] = instruction
                    if "category" not in payload and cat:
                        payload["category"] = cat
                    plan.tasks.append(PlanTask(
                        category=cat,
                        instruction=instruction,
                        payload=payload,
                    ))
        plan.valid = bool(plan.tasks)
    if not plan.valid and os.getenv("MOE_STRICT_CONTRACTS", "0") == "1":
        logger.error("contracts: planner output failed schema parse (chars=%d)", len(raw or ""))
    return plan


def assign_stable_task_ids(tasks: list[dict]) -> list[dict]:
    """Assign deterministic IDs while preserving explicit, unique planner IDs."""
    used: set[str] = set()
    for index, task in enumerate(tasks):
        raw_id = task.get("id")
        task_id = str(raw_id).strip() if raw_id is not None else ""
        if not task_id or task_id in used:
            task_id = f"task-{index + 1}"
            suffix = 1
            while task_id in used:
                suffix += 1
                task_id = f"task-{index + 1}-{suffix}"
        task["id"] = task_id
        used.add(task_id)
    return tasks


_DEP_MATCH_THRESHOLD = 0.6  # difflib.SequenceMatcher ratio for fuzzy depends_on repair


def normalize_task_dependencies(tasks: list[dict]) -> tuple[list[dict], list[dict]]:
    """Repair or drop planner-emitted `depends_on` values, in place.

    The planner is instructed to reference a prior task's `id` in
    `depends_on`, but sometimes emits that task's natural-language `task`
    description instead (or, less often, a forward reference to a task that
    has not run yet). Neither form resolves against `working_memory` at
    dispatch time (see services/dor_check.py, rule "unresolved_dependency"),
    and a single unresolved dependency currently causes the *entire* plan to
    be reported as incomplete downstream (services/quality_gate.py,
    incomplete_plan_tasks) — one bad depends_on field blocks the whole
    response, not just the dependent task.

    Must run after assign_stable_task_ids() so every task already has its
    final `id`. Repairs a depends_on value that fuzzy-matches an *earlier*
    task's description to that task's id; drops (clears) a depends_on value
    that cannot be resolved to any earlier task, so the task is scheduled as
    independent instead of being treated as permanently not-ready.

    Returns (tasks, repairs) — repairs is a list of
    {"task_id", "from", "to"} dicts for logging (to == "" means dropped).
    """
    repairs: list[dict] = []
    known_ids = {str(t.get("id") or "") for t in tasks}
    for index, task in enumerate(tasks):
        dep = str(task.get("depends_on") or "").strip()
        if not dep:
            continue
        if dep in known_ids:
            # Guard against a self-reference or a forward reference — both
            # are unresolvable at dispatch time regardless of the id being
            # syntactically valid.
            earlier_ids = {str(t.get("id") or "") for t in tasks[:index]}
            if dep in earlier_ids:
                continue
            repairs.append({"task_id": task.get("id"), "from": dep, "to": ""})
            task["depends_on"] = ""
            continue

        best_id, best_ratio = "", 0.0
        dep_norm = dep.casefold().strip()
        for earlier in tasks[:index]:
            candidate_text = str(earlier.get("task") or "").casefold().strip()
            if not candidate_text:
                continue
            if dep_norm == candidate_text or dep_norm in candidate_text or candidate_text in dep_norm:
                best_id, best_ratio = str(earlier.get("id") or ""), 1.0
                break
            ratio = difflib.SequenceMatcher(None, dep_norm, candidate_text).ratio()
            if ratio > best_ratio:
                best_id, best_ratio = str(earlier.get("id") or ""), ratio

        resolved = best_id if best_ratio >= _DEP_MATCH_THRESHOLD else ""
        if resolved != dep:
            repairs.append({"task_id": task.get("id"), "from": dep, "to": resolved})
            task["depends_on"] = resolved
    return tasks, repairs


def _infer_precision_contracts(text: str) -> list[tuple[str, dict]]:
    """Infer deterministic MCP contracts whose arguments are fully explicit.

    This is intentionally narrow: only unambiguous arithmetic/unit/calendar
    forms with dedicated precision tools are normalized. Everything else
    remains a contract error and follows the bounded planner-repair path.
    """
    normalized = " ".join((text or "").split())
    lowered = normalized.casefold()
    inferred: list[tuple[str, dict]] = []

    decimal_token = r"(-?(?:0|[1-9]\d{0,47})(?:[.,]\d{1,24})?)"
    rounding_token = r"(half_even|half_up|half_down|down|up|floor|ceiling)"
    finance_percentage = re.fullmatch(
        rf"\s*(?:berechne|ermittle|calculate|compute)\s+{decimal_token}\s*"
        rf"(?:%|prozent|percent)\s+(?:von|of)\s+{decimal_token}\s+([A-Z]{{3}})\s+"
        rf"(?:mit|with)\s+scale\s+(\d{{1,2}})\s+(?:und|and)\s+"
        rf"(?:rundung|rounding)\s+{rounding_token}\s*[?!.]*\s*",
        text or "",
        re.I,
    )
    finance_expression = re.fullmatch(
        rf"\s*(?:berechne|ermittle|calculate|compute)\s+{decimal_token}\s*"
        rf"([+*/-])\s*{decimal_token}\s+([A-Z]{{3}})\s+"
        rf"(?:mit|with)\s+scale\s+(\d{{1,2}})\s+(?:und|and)\s+"
        rf"(?:rundung|rounding)\s+{rounding_token}\s*[?!.]*\s*",
        text or "",
        re.I,
    )
    if finance_percentage:
        rate, base, currency, scale, rounding = finance_percentage.groups()
        inferred.append((
            "decimal_finance",
            {
                "operation": "percentage",
                "operands": [base.replace(",", "."), rate.replace(",", ".")],
                "currency": currency.upper(),
                "scale": int(scale),
                "rounding": rounding.lower(),
            },
        ))
    elif finance_expression:
        left, operator, right, currency, scale, rounding = finance_expression.groups()
        inferred.append((
            "decimal_finance",
            {
                "operation": {
                    "+": "add", "-": "subtract", "*": "multiply", "/": "divide",
                }[operator],
                "operands": [left.replace(",", "."), right.replace(",", ".")],
                "currency": currency.upper(),
                "scale": int(scale),
                "rounding": rounding.lower(),
            },
        ))

    combination = re.fullmatch(
        r"\s*(?:berechne|ermittle|calculate|compute)\s+(?:die\s+|the\s+)?"
        r"(?:kombination|combination)\s+C?\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)"
        r"\s*(?:exakt|exactly)?\s*[?!.]*\s*",
        text or "",
        re.I,
    )
    permutation = re.fullmatch(
        r"\s*(?:berechne|ermittle|calculate|compute)\s+(?:die\s+|the\s+)?"
        r"(?:permutation)\s+P?\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)"
        r"\s*(?:exakt|exactly)?\s*[?!.]*\s*",
        text or "",
        re.I,
    )
    binomial = re.fullmatch(
        rf"\s*(?:berechne|ermittle|calculate|compute)\s+(?:die\s+|the\s+)?"
        rf"(?:binomialwahrscheinlichkeit|binomial probability)\s+(?:für|for)\s+"
        rf"n\s*=\s*(\d+)\s*,\s*k\s*=\s*(\d+)\s*,\s*p\s*=\s*(\d+)\s*/\s*(\d+)\s+"
        rf"(?:als\s+dezimalzahl|as\s+(?:a\s+)?decimal)\s+(?:mit|with)\s+"
        rf"scale\s+(\d{{1,2}})\s+(?:und|and)\s+(?:rundung|rounding)\s+"
        rf"{rounding_token}\s*[?!.]*\s*",
        text or "",
        re.I,
    )
    probability_match = combination or permutation or binomial
    if probability_match:
        if binomial:
            n, k, numerator, denominator, scale, rounding = binomial.groups()
            probability_args = {
                "operation": "binomial_probability", "n": int(n), "k": int(k),
                "numerator": int(numerator), "denominator": int(denominator),
                "decimal_scale": int(scale), "rounding": rounding.lower(),
            }
        else:
            n, k = probability_match.groups()
            probability_args = {
                "operation": "combination" if combination else "permutation",
                "n": int(n), "k": int(k), "numerator": None,
                "denominator": None, "decimal_scale": None, "rounding": None,
            }
        inferred.append(("exact_probability", probability_args))

    structured_match = re.fullmatch(
        r"\s*(?:validiere|validate)(?:\s+(?:dieses|this))?\s+"
        r"(json|yaml|xml)\s*:\s*([\s\S]+?)\s*",
        text or "",
        re.I,
    )
    csv_match = re.fullmatch(
        r"\s*(?:validiere|validate)(?:\s+(?:dieses|this))?\s+csv\s+"
        r"(?:mit\s+(?:trennzeichen|dialekt)|with\s+(?:delimiter|dialect))\s+"
        r"(comma|semicolon|tab|pipe)\s*:\s*([\s\S]+?)\s*",
        text or "",
        re.I,
    )
    if structured_match:
        format_name, payload = structured_match.groups()
        inferred.append((
            "structured_validate",
            {
                "format_name": format_name.lower(), "payload": payload,
                "schema_json": None, "csv_dialect": None,
            },
        ))
    elif csv_match:
        dialect, payload = csv_match.groups()
        inferred.append((
            "structured_validate",
            {
                "format_name": "csv", "payload": payload,
                "schema_json": None, "csv_dialect": dialect.lower(),
            },
        ))

    iso_instant = re.search(
        r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2}))\b",
        normalized,
    )
    local_datetime = re.search(
        r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\b",
        normalized,
    )
    zone_token = r"([A-Za-z][A-Za-z0-9._+-]*(?:/[A-Za-z0-9._+-]+)*)"
    if re.search(
        r"\b(?:zeitfakten|zeitzonenfakten|time facts|timezone facts|"
        r"utc[- ]offset|utc[- ]versatz)\b",
        lowered,
    ):
        zone_match = re.search(rf"\b(?:in|für|for)\s+{zone_token}", normalized, re.I)
        if iso_instant and zone_match:
            inferred.append((
                "time_facts",
                {
                    "instant": iso_instant.group(1),
                    "timezone_name": zone_match.group(1).rstrip(".,;!?"),
                    "locale": "de" if re.search(
                        r"\b(?:zeitfakten|zeitzonenfakten|für|bestimme|ermittle)\b",
                        lowered,
                    ) else "en",
                },
            ))

    implicit_clock = re.search(
        rf"\b(?:wie\s+spät\s+ist\s+es|aktuelle\s+uhrzeit|"
        rf"what\s+time\s+is\s+it|current\s+time)\b.*?\b(?:in|für|for)\s+{zone_token}",
        normalized,
        re.I,
    )
    if implicit_clock and not iso_instant:
        inferred.append((
            "time_facts",
            {
                "instant": "__implicit_clock__",
                "timezone_name": implicit_clock.group(1).rstrip(".,;!?"),
                "locale": "de" if re.search(
                    r"\b(?:wie|aktuelle|uhrzeit|für)\b", lowered
                ) else "en",
            },
        ))

    if local_datetime and not iso_instant and re.search(
        r"\b(?:konvertiere|konvertier|wandle|convert)\b", lowered
    ):
        zones = re.search(
            rf"\b(?:von|from)\s+{zone_token}\s+(?:nach|to)\s+{zone_token}",
            normalized,
            re.I,
        )
        if zones:
            fold_match = re.search(r"\bfold\s*[:=]?\s*([01])\b", normalized, re.I)
            inferred.append((
                "timezone_convert",
                {
                    "local_datetime": local_datetime.group(1),
                    "from_timezone": zones.group(1).rstrip(".,;!?"),
                    "to_timezone": zones.group(2).rstrip(".,;!?"),
                    "fold": int(fold_match.group(1)) if fold_match else None,
                    "locale": "de" if re.search(
                        r"\b(?:konvertiere|konvertier|wandle|von|nach)\b",
                        lowered,
                    ) else "en",
                },
            ))

    if re.search(
        r"\b(gcd|ggt|grö(?:ß|ss)ten gemeinsamen teiler|greatest common divisor)\b",
        lowered,
    ):
        # Accept normal sentence punctuation after an integer (``299.``), but
        # do not split decimal values such as ``29.5`` into two integers.
        numbers = [
            int(value)
            for value in re.findall(
                r"(?<![\d.,])\d+(?!\d|[.,]\d)",
                normalized,
            )
        ]
        if len(numbers) >= 2:
            inferred.append(
                (
                    "gcd_lcm",
                    {
                        "a": numbers[0],
                        "b": numbers[1],
                        "operation": "gcd",
                    },
                )
            )

    if re.search(r"\b(km\s*/\s*h|kmh)\b", lowered) and re.search(
        r"\bm\s*/\s*s\b", lowered
    ):
        match = re.search(r"(?<![\d.])(\d+(?:[.,]\d+)?)(?![\d.])", normalized)
        if match:
            value = float(match.group(1).replace(",", "."))
            inferred.append(
                (
                    "unit_convert",
                    {
                        "value": int(value) if value.is_integer() else value,
                        "from_unit": "km/h",
                        "to_unit": "m/s",
                    },
                )
            )

    if re.search(r"\b(wochentag|weekday|day of (?:the )?week)\b", lowered):
        iso_date = ""
        match = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", normalized)
        if match:
            day, month, year = map(int, match.groups())
            try:
                iso_date = date(year, month, day).isoformat()
            except ValueError:
                iso_date = ""
        if not iso_date:
            match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", normalized)
            if match:
                year, month, day = map(int, match.groups())
                try:
                    iso_date = date(year, month, day).isoformat()
                except ValueError:
                    iso_date = ""
        if iso_date:
            locale = "de" if re.search(r"\bwochentag\b", lowered) else "en"
            inferred.append(
                (
                    "calendar_facts",
                    {
                        "date_str": iso_date,
                        "locale": locale,
                    },
                )
            )

    return inferred


def _infer_precision_contract(text: str) -> tuple[str, dict] | None:
    """Return one contract only when the text contains exactly one intent."""
    inferred = _infer_precision_contracts(text)
    return inferred[0] if len(inferred) == 1 else None


_NUMBERED_ITEM_PATTERN = re.compile(
    r"(?ms)^\s*(\d+)[.)]\s+(.*?)(?=^\s*\d+[.)]\s+|\Z)"
)
_IMPLEMENTATION_REQUEST_PATTERN = re.compile(
    r"\b(?:implement(?:iere|ieren|ation)?|programm(?:iere|ieren)?|"
    r"schreibe\s+(?:einen?\s+)?(?:code|script|programm)|"
    r"write\s+(?:a\s+)?(?:code|script|program)|"
    r"python|javascript|typescript|java|rust|golang)\b",
    re.I,
)
_CALENDAR_REQUEST_PATTERN = re.compile(
    r"(?:\b(?:bestimm|berechne|ermittle|nenne|sag|gib|welch|what|which|"
    r"determine|calculate|tell|give)\w*\b.{0,80}\b(?:wochentag|weekday|"
    r"day of (?:the )?week)\b|\b(?:wochentag|weekday|day of (?:the )?week)\b"
    r".{0,80}\b(?:ist|war|fällt|is|was|falls)\b)",
    re.I,
)
_UNIT_REQUEST_PATTERN = re.compile(
    r"\b(?:rechne|wandle|konvert|umrechn|convert|conversion|"
    r"calculate)\w*\b|\b(?:km\s*/\s*h|kmh)\b\s+(?:in|to)\s+"
    r"\bm\s*/\s*s\b",
    re.I,
)
_TIME_FACTS_REQUEST_PATTERN = re.compile(
    r"\b(?:zeitfakten|zeitzonenfakten|time facts|timezone facts|"
    r"utc[- ]offset|utc[- ]versatz|wie\s+spät\s+ist\s+es|"
    r"aktuelle\s+uhrzeit|what\s+time\s+is\s+it|current\s+time)\b",
    re.I,
)
_TIMEZONE_REQUEST_PATTERN = re.compile(
    r"\b(?:konvertiere|konvertier|wandle|convert)\b.*"
    r"\b(?:von|from)\b.*\b(?:nach|to)\b",
    re.I,
)
_DECIMAL_FINANCE_REQUEST_PATTERN = re.compile(
    r"^\s*(?:berechne|ermittle|calculate|compute)\b.*\b(?:scale)\s+\d+\b.*"
    r"\b(?:rundung|rounding)\s+(?:half_even|half_up|half_down|down|up|floor|ceiling)\b.*$",
    re.I | re.S,
)
_EXACT_PROBABILITY_REQUEST_PATTERN = re.compile(
    r"^\s*(?:berechne|ermittle|calculate|compute)\b.*"
    r"\b(?:kombination|combination|permutation|binomialwahrscheinlichkeit|binomial probability)\b.*$",
    re.I | re.S,
)
_STRUCTURED_VALIDATE_REQUEST_PATTERN = re.compile(
    r"^\s*(?:validiere|validate)(?:\s+(?:dieses|this))?\s+"
    r"(?:json|yaml|xml|csv)\b[\s\S]+$",
    re.I,
)

_DIRECT_GCD_PATTERN = re.compile(
    r"^\s*(?:bitte\s+)?(?:"
    r"(?:(?:berechne|bestimme|ermittle|nenne|finde|was\s+ist|wie\s+lautet)"
    r"(?:\s+mir)?\s+(?:der\s+|den\s+)?)"
    r"(?:ggt|grö(?:ß|ss)te[nr]?\s+gemeinsame[nr]?\s+teiler)\s+(?:von\s+)?"
    r"-?\d+\s*(?:und|,)\s*-?\d+"
    r"|(?:(?:calculate|compute|determine|find|what\s+is)(?:\s+the)?\s+)"
    r"(?:gcd|greatest\s+common\s+divisor)\s+(?:of\s+)?"
    r"-?\d+\s*(?:and|,)\s*-?\d+"
    r")\s*[?!.]*\s*$",
    re.I,
)
_DIRECT_UNIT_PATTERN = re.compile(
    r"^\s*(?:bitte\s+)?(?:"
    r"(?:rechne|wandle|konvertiere|berechne)\s+"
    r"\d+(?:[.,]\d+)?\s*(?:km\s*/\s*h|kmh)\s+(?:in|zu)\s+"
    r"m\s*/\s*s(?:\s+um)?"
    r"|(?:convert|calculate|what\s+is)\s+"
    r"\d+(?:[.,]\d+)?\s*(?:km\s*/\s*h|kmh)\s+(?:to|in)\s+"
    r"m\s*/\s*s"
    r")\s*[?!.]*\s*$",
    re.I,
)
_DIRECT_CALENDAR_PATTERN = re.compile(
    r"^\s*(?:bitte\s+)?(?:"
    r"(?:welcher\s+wochentag\s+(?:ist|war|fällt\s+auf)|"
    r"(?:bestimme|ermittle|nenne|sag(?:e)?(?:\s+mir)?)\s+"
    r"(?:den\s+)?wochentag(?:\s+(?:für|am|des))?)\s+"
    r"(?:der\s+|am\s+|für\s+)?(?:\d{1,2}\.\d{1,2}\.\d{4}|\d{4}-\d{2}-\d{2})"
    r"|(?:what|which)\s+(?:weekday|day\s+of\s+(?:the\s+)?week)\s+"
    r"(?:is|was|falls\s+on|for)?\s*(?:\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{4})"
    r")\s*[?!.]*\s*$",
    re.I,
)
_DIRECT_TIME_FACTS_PATTERN = re.compile(
    r"^\s*(?:bitte\s+)?(?:"
    r"(?:bestimme|ermittle|nenne|gib|zeige|give|show|determine)\w*\s+"
    r"(?:die\s+|the\s+)?(?:zeitfakten|zeitzonenfakten|time facts|timezone facts|"
    r"utc[- ]offset|utc[- ]versatz)\s+(?:für|for)?\s*"
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})\s+"
    r"(?:in|für|for)\s+[A-Za-z][A-Za-z0-9._+-]*(?:/[A-Za-z0-9._+-]+)*"
    r"|(?:wie\s+spät\s+ist\s+es|aktuelle\s+uhrzeit|"
    r"what\s+time\s+is\s+it|current\s+time)(?:\s+(?:jetzt|now))?\s+"
    r"(?:in|für|for)\s+[A-Za-z][A-Za-z0-9._+-]*(?:/[A-Za-z0-9._+-]+)*"
    r")\s*[?!.]*\s*$",
    re.I,
)
_DIRECT_TIMEZONE_PATTERN = re.compile(
    r"^\s*(?:bitte\s+)?(?:konvertiere|konvertier|wandle|convert)\w*\s+"
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\s+"
    r"(?:von|from)\s+[A-Za-z][A-Za-z0-9._+-]*(?:/[A-Za-z0-9._+-]+)*\s+"
    r"(?:nach|to)\s+[A-Za-z][A-Za-z0-9._+-]*(?:/[A-Za-z0-9._+-]+)*"
    r"(?:\s+(?:mit|with)\s+fold\s*[:=]?\s*[01])?\s*[?!.]*\s*$",
    re.I,
)

_DIRECT_PRECISION_PATTERNS = {
    "gcd_lcm": _DIRECT_GCD_PATTERN,
    "unit_convert": _DIRECT_UNIT_PATTERN,
    "calendar_facts": _DIRECT_CALENDAR_PATTERN,
    "time_facts": _DIRECT_TIME_FACTS_PATTERN,
    "timezone_convert": _DIRECT_TIMEZONE_PATTERN,
    "decimal_finance": _DECIMAL_FINANCE_REQUEST_PATTERN,
    "exact_probability": _EXACT_PROBABILITY_REQUEST_PATTERN,
    "structured_validate": _STRUCTURED_VALIDATE_REQUEST_PATTERN,
}


def _numbered_query_items(text: str) -> list[str] | None:
    """Return contiguous numbered items, [] for malformed, None for prose."""
    matches = list(_NUMBERED_ITEM_PATTERN.finditer(text or ""))
    if not matches:
        return None
    numbers = [int(match.group(1)) for match in matches]
    if numbers != list(range(1, len(matches) + 1)):
        return []
    return [match.group(2).strip() for match in matches]


def _is_direct_precision_request(text: str, tool: str) -> bool:
    """Exclude examples and code-generation requests from mandatory routing."""
    if _IMPLEMENTATION_REQUEST_PATTERN.search(text or ""):
        return False
    if tool == "calendar_facts":
        return bool(_CALENDAR_REQUEST_PATTERN.search(text or ""))
    if tool == "unit_convert":
        return bool(_UNIT_REQUEST_PATTERN.search(text or ""))
    if tool == "time_facts":
        return bool(_TIME_FACTS_REQUEST_PATTERN.search(text or ""))
    if tool == "timezone_convert":
        return bool(_TIMEZONE_REQUEST_PATTERN.search(text or ""))
    if tool == "decimal_finance":
        return bool(_DECIMAL_FINANCE_REQUEST_PATTERN.fullmatch(text or ""))
    if tool == "exact_probability":
        return bool(_EXACT_PROBABILITY_REQUEST_PATTERN.fullmatch(text or ""))
    if tool == "structured_validate":
        return bool(_STRUCTURED_VALIDATE_REQUEST_PATTERN.fullmatch(text or ""))
    return tool == "gcd_lcm"


def detect_required_precision_intents(
    input_query: str,
) -> list[RequiredPrecisionIntent]:
    """Detect only explicit precision work that must not degrade to an LLM.

    Prose is accepted only when it resolves to one supported operation. A
    contiguous numbered request is inspected item by item, which preserves
    deterministic sub-tasks inside otherwise mixed plans without guessing how
    arbitrary prose should be decomposed.
    """
    numbered_items = _numbered_query_items(input_query)
    if numbered_items == []:
        return []
    if numbered_items is None:
        inferred = _infer_precision_contracts(input_query)
        if len(inferred) != 1:
            return []
        tool, args = inferred[0]
        if not _is_direct_precision_request(input_query, tool):
            return []
        return [RequiredPrecisionIntent(tool=tool, args=args)]

    required: list[RequiredPrecisionIntent] = []
    for item_index, item in enumerate(numbered_items):
        inferred = _infer_precision_contracts(item)
        if len(inferred) != 1:
            continue
        tool, args = inferred[0]
        if _is_direct_precision_request(item, tool):
            required.append(
                RequiredPrecisionIntent(
                    tool=tool,
                    args=args,
                    source_item=item_index,
                )
            )
    return required


def is_fully_covered_precision_request(input_query: str) -> bool:
    """Return true only when every user-requested item is a known contract.

    The mandatory intent detector deliberately finds precision work inside a
    mixed numbered request.  A direct response needs a stronger proof: there
    may be no prose prefix, unclassified item, explanation request or other
    outcome that bypassing the planner would silently discard.
    """
    required = detect_required_precision_intents(input_query)
    if not required:
        return False
    numbered_items = _numbered_query_items(input_query)
    if numbered_items is None:
        if len(required) != 1:
            return False
        pattern = _DIRECT_PRECISION_PATTERNS.get(required[0].tool)
        return bool(pattern and pattern.fullmatch(" ".join(input_query.split())))
    if not numbered_items or not (input_query or "").lstrip().startswith(("1.", "1)")):
        return False
    by_item = {intent.source_item: intent for intent in required}
    if set(by_item) != set(range(len(numbered_items))):
        return False
    return all(
        bool(
            _DIRECT_PRECISION_PATTERNS.get(by_item[index].tool)
            and _DIRECT_PRECISION_PATTERNS[by_item[index].tool].fullmatch(item)
        )
        for index, item in enumerate(numbered_items)
    )


def build_direct_precision_plan(input_query: str) -> list[dict[str, Any]]:
    """Build an executable plan only for a completely covered request."""
    if not is_fully_covered_precision_request(input_query):
        return []
    intents = detect_required_precision_intents(input_query)
    numbered_items = _numbered_query_items(input_query)
    normalized_query = " ".join((input_query or "").split())
    tasks = []
    for index, intent in enumerate(intents):
        instruction = (
            numbered_items[intent.source_item]
            if numbered_items is not None and intent.source_item is not None
            else normalized_query
        )
        tasks.append(
            {
                "task": instruction,
                "category": "precision_tools",
                "mcp_tool": intent.tool,
                "mcp_args": copy.deepcopy(intent.args),
                "precision_source_item": intent.source_item,
            }
        )
    return assign_stable_task_ids(tasks)


def build_precision_preflight(
    input_query: str,
    tool_schemas: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Freeze mandatory precision intent and active schemas before caching.

    The returned structure is JSON/checkpointer-safe.  A missing schema is
    intentionally preserved as ``None`` so a later catalog reload cannot turn
    an unavailable contract into an executable one inside the same request.
    """
    schemas = tool_schemas or {}
    detected = detect_required_precision_intents(input_query)
    snapshots: dict[str, Any] = {}
    required: list[dict[str, Any]] = []
    for intent in detected:
        schema = schemas.get(intent.tool)
        frozen_schema = copy.deepcopy(dict(schema)) if isinstance(schema, Mapping) else None
        schema_hash = tool_schema_contract_hash(frozen_schema)
        snapshots[intent.tool] = frozen_schema
        required.append(
            {
                "tool": intent.tool,
                "args": copy.deepcopy(intent.args),
                "source_item": intent.source_item,
                "schema_hash": schema_hash,
            }
        )
    contract_material = [
        {
            "tool": item["tool"],
            "source_item": item["source_item"],
            "schema_hash": item["schema_hash"],
        }
        for item in required
    ]
    return {
        "required_precision_intents": required,
        "precision_contract_snapshot": snapshots,
        "precision_contract_hash": (
            canonical_json_hash(contract_material) if required else ""
        ),
        "precision_catalog_hash": canonical_tool_catalog_hash(schemas),
        "precision_cache_bypassed": False,
    }


_BASELINE_ENFORCED_PRECISION_TOOLS = {
    "calendar_facts", "gcd_lcm", "unit_convert",
}


def apply_precision_contract_mode(
    preflight: Mapping[str, Any],
    mode: str,
) -> dict[str, Any]:
    """Freeze central shadow/enforce policy into one request snapshot.

    Shadow mode preserves the three contracts that predate this rollout and
    observes newer detected contracts without making them mandatory. Enforce
    mode makes every supported typed extractor mandatory.
    """
    safe_mode = mode if mode in {"shadow", "enforce"} else "shadow"
    detected = [
        copy.deepcopy(item)
        for item in (preflight.get("required_precision_intents") or [])
        if isinstance(item, Mapping)
    ]
    if safe_mode == "enforce":
        enforced = detected
        shadow = []
    else:
        enforced = [
            item for item in detected
            if str(item.get("tool") or "") in _BASELINE_ENFORCED_PRECISION_TOOLS
        ]
        shadow = [
            item for item in detected
            if str(item.get("tool") or "") not in _BASELINE_ENFORCED_PRECISION_TOOLS
        ]
    all_snapshots = preflight.get("precision_contract_snapshot") or {}
    snapshots = {
        str(item.get("tool") or ""): copy.deepcopy(
            all_snapshots.get(str(item.get("tool") or ""))
        )
        for item in enforced
    }
    contract_material = [
        {
            "tool": item.get("tool"),
            "source_item": item.get("source_item"),
            "schema_hash": item.get("schema_hash"),
        }
        for item in enforced
    ]
    return {
        **dict(preflight),
        "required_precision_intents": enforced,
        "precision_shadow_intents": shadow,
        "precision_contract_snapshot": snapshots,
        "precision_contract_hash": (
            canonical_json_hash(contract_material) if enforced else ""
        ),
        "precision_contract_mode": safe_mode,
    }


def _precision_args_match(
    actual: Any,
    expected: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> bool:
    """Compare semantic args while honoring documented optional defaults."""
    if not isinstance(actual, Mapping):
        return False
    required = schema.get("required", [])
    required_names = set(required) if isinstance(required, (list, tuple)) else set()
    properties = schema.get("args", {})
    properties = properties if isinstance(properties, Mapping) else {}
    for name, expected_value in expected.items():
        if name in actual:
            if actual.get(name) != expected_value:
                return False
            continue
        if name in required_names:
            return False
        property_schema = properties.get(name)
        if not isinstance(property_schema, Mapping):
            return False
        if property_schema.get("default") != expected_value:
            return False
    return True


def precision_args_match(
    actual: Any,
    expected: Mapping[str, Any],
    schema: Mapping[str, Any],
) -> bool:
    """Public semantic argument matcher shared by worker and final gate."""
    return _precision_args_match(actual, expected, schema)


def validate_required_precision_intents(
    tasks: Any,
    input_query: str,
    tool_schemas: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[PlannerContractIssue]:
    """Reject plans that omit or alter an explicit deterministic operation."""
    required_intents = detect_required_precision_intents(input_query)
    if not required_intents:
        return []

    schemas = tool_schemas or {}
    planned = tasks if isinstance(tasks, list) else []
    issues: list[PlannerContractIssue] = []
    for intent in required_intents:
        source = (
            f"numbered input item {intent.source_item + 1}"
            if intent.source_item is not None
            else "input request"
        )
        schema = schemas.get(intent.tool)
        if not isinstance(schema, Mapping):
            issues.append(
                PlannerContractIssue(
                    task_index=-1,
                    code="required_precision_tool_unavailable",
                    field="mcp_tool",
                    message=(
                        f"{source} requires deterministic MCP tool "
                        f"'{intent.tool}', but it is not active in the "
                        "discovered catalog"
                    ),
                )
            )
            continue

        candidates = [
            (index, task)
            for index, task in enumerate(planned)
            if isinstance(task, Mapping)
            and task.get("category") == "precision_tools"
            and task.get("mcp_tool") == intent.tool
        ]
        if any(
            _precision_args_match(task.get("mcp_args"), intent.args, schema)
            for _, task in candidates
        ):
            continue

        candidate_index = candidates[0][0] if candidates else -1
        detail = (
            "uses different semantic arguments"
            if candidates
            else "has no matching precision_tools task"
        )
        issues.append(
            PlannerContractIssue(
                task_index=candidate_index,
                code="precision_intent_downgraded",
                field="mcp_args" if candidates else "category",
                message=(
                    f"{source} requires '{intent.tool}' with "
                    f"{json.dumps(intent.args, ensure_ascii=False, sort_keys=True)}, "
                    f"but the plan {detail}"
                ),
            )
        )


    return issues


def recover_explicit_supported_plan(
    input_query: str,
    tool_schemas: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    max_tasks: int | None = None,
) -> tuple[list[dict], list[dict]]:
    """Recover an empty planner result only for a fully explicit task list.

    This is deliberately not a generic planner fallback.  Every numbered item
    must independently match one of the small deterministic precision
    contracts above, or an explicit SQL-injection review containing the code
    to inspect.  If even one item is unknown or ambiguous, no plan is returned
    and the caller must preserve the normal fail-closed contract error.
    """
    schemas = tool_schemas or {}
    numbered_items = _numbered_query_items(input_query)
    if not numbered_items:
        return [], []
    if max_tasks is not None and len(numbered_items) > max_tasks:
        return [], []

    tasks: list[dict] = []
    repairs: list[dict] = []
    for index, instruction in enumerate(numbered_items):
        inferred = _infer_precision_contract(instruction)
        if inferred is not None and inferred[0] in schemas:
            tool, args = inferred
            tasks.append(
                {
                    "task": instruction,
                    "category": "precision_tools",
                    "mcp_tool": tool,
                    "mcp_args": args,
                }
            )
            repairs.append(
                {
                    "task_index": index,
                    "tool": tool,
                    "reason": "explicit_numbered_task_recovery",
                }
            )
            continue

        lowered = instruction.casefold()
        explicit_sql_review = (
            bool(re.search(r"\bsql[\s-]*injection\b", lowered))
            and "cursor.execute" in lowered
            and bool(
                re.search(
                    r"\b(parametr(?:isiert|ized)|parameter(?:isiert|ized)|"
                    r"sicher(?:e|en|er|es)?|safe)\b",
                    lowered,
                )
            )
        )
        if explicit_sql_review:
            tasks.append(
                {
                    "task": instruction,
                    "category": "code_reviewer",
                }
            )
            repairs.append(
                {
                    "task_index": index,
                    "category": "code_reviewer",
                    "reason": "explicit_numbered_task_recovery",
                }
            )
            continue

        return [], []

    return tasks, repairs


def repair_precision_task_contracts(
    tasks: list[dict],
    input_query: str,
    tool_schemas: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Fill only uniquely inferable missing precision-tool contracts.

    The planner remains authoritative for task intent. This normalizer merely
    maps explicit GCD, km/h→m/s and weekday requests to already discovered MCP
    schemas so a model that correctly classifies the task cannot drop it by
    omitting mechanical routing fields.
    """
    schemas = tool_schemas or {}
    repaired: list[dict] = []
    repairs: list[dict] = []
    precision_count = sum(
        1
        for task in tasks
        if isinstance(task, dict) and task.get("category") == "precision_tools"
    )
    for index, original in enumerate(tasks):
        task = dict(original) if isinstance(original, dict) else original
        if not isinstance(task, dict) or task.get("category") != "precision_tools":
            repaired.append(task)
            continue

        existing_tool = task.get("mcp_tool")
        existing_args = task.get("mcp_args")
        schema = schemas.get(existing_tool) if isinstance(existing_tool, str) else None
        required = schema.get("required", []) if isinstance(schema, Mapping) else []
        existing_valid = (
            isinstance(schema, Mapping)
            and isinstance(existing_args, dict)
            and all(
                name in existing_args
                and existing_args.get(name) is not None
                and not (
                    isinstance(existing_args.get(name), str)
                    and not existing_args.get(name).strip()
                )
                for name in required
            )
        )
        if existing_valid:
            repaired.append(task)
            continue

        inferred = _infer_precision_contract(str(task.get("task") or ""))
        if inferred is None and precision_count == 1:
            inferred = _infer_precision_contract(input_query)
        if inferred is not None and inferred[0] in schemas:
            tool, args = inferred
            task["mcp_tool"] = tool
            task["mcp_args"] = args
            repairs.append(
                {
                    "task_index": index,
                    "tool": tool,
                    "reason": "deterministic_precision_contract",
                }
            )
        repaired.append(task)
    return repaired, repairs


def validate_plan_tasks(
    tasks: Any,
    tool_schemas: Mapping[str, Mapping[str, Any]] | None = None,
    max_tasks: int | None = None,
    *,
    input_query: str = "",
) -> list[PlannerContractIssue]:
    """Validate that every declared plan task has an executable handoff.

    Precision work is conditional: the task must name a discovered MCP tool and
    provide a mapping containing every argument required by that tool's schema.
    This prevents a precision task from disappearing because the executor only
    selects entries that happen to contain ``mcp_tool``.
    """
    issues: list[PlannerContractIssue] = []
    if not isinstance(tasks, list) or not tasks:
        return [
            PlannerContractIssue(
                task_index=-1,
                code="empty_plan",
                field="tasks",
                message="plan must contain at least one task",
            )
        ]
    if max_tasks is not None and len(tasks) > max_tasks:
        issues.append(
            PlannerContractIssue(
                task_index=-1,
                code="too_many_tasks",
                field="tasks",
                message=(
                    f"plan contains {len(tasks)} tasks but the executable "
                    f"maximum is {max_tasks}; combine compatible non-precision "
                    "work without omitting any requested outcome"
                ),
            )
        )

    schemas = tool_schemas or {}
    _task_id_positions = {
        task.get("id"): position
        for position, task in enumerate(tasks)
        if isinstance(task, dict) and isinstance(task.get("id"), str)
    }
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            issues.append(
                PlannerContractIssue(
                    index,
                    "invalid_task_type",
                    f"task must be a mapping, got {type(task).__name__}",
                )
            )
            continue

        instruction = task.get("task")
        if not isinstance(instruction, str) or not instruction.strip():
            issues.append(
                PlannerContractIssue(
                    index,
                    "missing_task",
                    "non-empty 'task' is required",
                    "task",
                )
            )

        category = task.get("category")
        if not isinstance(category, str) or not category.strip():
            issues.append(
                PlannerContractIssue(
                    index,
                    "missing_category",
                    "non-empty 'category' is required",
                    "category",
                )
            )
            continue

        if category != "precision_tools":
            continue

        tool = task.get("mcp_tool")
        if not isinstance(tool, str) or not tool.strip():
            issues.append(
                PlannerContractIssue(
                    index,
                    "missing_mcp_tool",
                    "precision_tools task requires a non-empty 'mcp_tool'",
                    "mcp_tool",
                )
            )
            continue
        tool = tool.strip()

        args = task.get("mcp_args")
        if not isinstance(args, dict):
            issues.append(
                PlannerContractIssue(
                    index,
                    "invalid_mcp_args",
                    "precision_tools task requires 'mcp_args' as a JSON object",
                    "mcp_args",
                )
            )
            continue

        if not schemas:
            issues.append(
                PlannerContractIssue(
                    index,
                    "tool_catalog_unavailable",
                    "MCP tool schema catalog is unavailable",
                    "mcp_tool",
                )
            )
            continue

        schema = schemas.get(tool)
        if not isinstance(schema, Mapping):
            issues.append(
                PlannerContractIssue(
                    index,
                    "unknown_mcp_tool",
                    f"MCP tool '{tool}' is not present in the discovered catalog",
                    "mcp_tool",
                )
            )
            continue

        required = schema.get("required", [])
        if not isinstance(required, (list, tuple)):
            issues.append(
                PlannerContractIssue(
                    index,
                    "invalid_tool_schema",
                    f"MCP tool '{tool}' has an invalid required-arguments schema",
                    "mcp_tool",
                )
            )
            continue

        missing = [
            str(name)
            for name in required
            if name not in args
            or args.get(name) is None
            or (isinstance(args.get(name), str) and not args.get(name).strip())
        ]
        if missing:
            issues.append(
                PlannerContractIssue(
                    index,
                    "missing_mcp_args",
                    f"MCP tool '{tool}' is missing required argument(s): "
                    + ", ".join(missing),
                    "mcp_args",
                )
            )

        for ref_task_id in _find_task_result_ref_ids(args):
            if ref_task_id == task.get("id"):
                issues.append(
                    PlannerContractIssue(
                        index,
                        "task_result_reference_cycle",
                        f"task references its own result ('{ref_task_id}')",
                        "mcp_args",
                    )
                )
                continue
            ref_index = _task_id_positions.get(ref_task_id)
            if ref_index is None or ref_index >= index:
                issues.append(
                    PlannerContractIssue(
                        index,
                        "invalid_task_result_reference",
                        f"'{ref_task_id}' must reference an earlier "
                        "precision_tools task in the same plan",
                        "mcp_args",
                    )
                )
                continue
            ref_task = tasks[ref_index]
            ref_tool = ref_task.get("mcp_tool") if isinstance(ref_task, dict) else None
            if (
                not isinstance(ref_task, dict)
                or ref_task.get("category") != "precision_tools"
                or not isinstance(ref_tool, str)
                or not ref_tool.strip()
            ):
                issues.append(
                    PlannerContractIssue(
                        index,
                        "invalid_task_result_reference",
                        f"'{ref_task_id}' does not identify a "
                        "precision_tools task with a resolved mcp_tool",
                        "mcp_args",
                    )
                )

    if input_query:
        issues.extend(
            validate_required_precision_intents(
                tasks,
                input_query,
                tool_schemas,
            )
        )
    return issues


def validate_plan_or_raise(
    tasks: Any,
    tool_schemas: Mapping[str, Mapping[str, Any]] | None = None,
    max_tasks: int | None = None,
    *,
    input_query: str = "",
) -> None:
    """Raise a structured error if the plan cannot be executed as declared."""
    issues = validate_plan_tasks(
        tasks,
        tool_schemas,
        max_tasks=max_tasks,
        input_query=input_query,
    )
    if issues:
        raise PlannerContractError(issues)
