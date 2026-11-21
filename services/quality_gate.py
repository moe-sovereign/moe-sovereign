"""Pure final-quality decision used by the LangGraph enforcement node."""

import re
from dataclasses import dataclass

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from services.cynefin import classify_cynefin
from services.pipeline.contracts import (
    canonical_json_hash,
    detect_required_precision_intents,
    precision_evidence_input,
    precision_args_match,
    tool_schema_contract_hash,
)


@dataclass(frozen=True)
class QualityGateDecision:
    action: str
    reason: str
    cynefin_domain: str


def incomplete_plan_tasks(state_: dict) -> list[dict]:
    """Return current-plan tasks without a successful terminal ledger event."""
    plan = state_.get("plan") or []
    if not plan:
        return []

    iteration = int(state_.get("agentic_iteration") or 0)
    latest: dict[str, dict] = {}
    for event in state_.get("task_events") or []:
        if not isinstance(event, dict):
            continue
        if int(event.get("iteration") or 0) != iteration:
            continue
        task_id = str(event.get("task_id") or "")
        if task_id:
            latest[task_id] = event

    incomplete: list[dict] = []
    for task in plan:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or "")
        # Legacy/untracked plans are not silently declared complete. New planner
        # paths always assign IDs before handoff, making this branch observable.
        event = latest.get(task_id) if task_id else None
        if event is None or event.get("status") != "completed":
            incomplete.append(
                {
                    "task_id": task_id or "<missing-id>",
                    "category": str(task.get("category") or ""),
                    "status": str((event or {}).get("status") or "missing"),
                    "reason": str((event or {}).get("reason") or ""),
                }
            )
    return incomplete


def precision_evidence_issues(state_: dict) -> list[str]:
    """Validate the mandatory intent→task→evidence chain without I/O."""
    if state_.get("guard_blocked"):
        return []
    detected = detect_required_precision_intents(str(state_.get("input") or ""))
    if not detected:
        return []

    required = state_.get("required_precision_intents")
    if not required and state_.get("precision_contract_mode") == "shadow":
        return []
    if not isinstance(required, list) or not required:
        return ["precision_preflight_missing"]
    if state_.get("precision_contract_mode") == "shadow":
        enforced_tools = {str(item.get("tool") or "") for item in required if isinstance(item, dict)}
        detected = [item for item in detected if item.tool in enforced_tools]
    detected_material = [
        {
            "tool": item.tool,
            "args": item.args,
            "source_item": item.source_item,
        }
        for item in detected
    ]
    required_material = [
        {
            "tool": item.get("tool"),
            "args": item.get("args"),
            "source_item": item.get("source_item"),
        }
        for item in required
        if isinstance(item, dict)
    ]
    if required_material != detected_material:
        return ["precision_preflight_mismatch"]

    snapshots = state_.get("precision_contract_snapshot") or {}
    contract_material = [
        {
            "tool": item.get("tool"),
            "source_item": item.get("source_item"),
            "schema_hash": item.get("schema_hash"),
        }
        for item in required
    ]
    if state_.get("precision_contract_hash") != canonical_json_hash(contract_material):
        return ["precision_contract_changed:preflight_hash"]

    plan = [task for task in (state_.get("plan") or []) if isinstance(task, dict)]
    evidence = [
        item
        for item in (state_.get("mcp_evidence") or [])
        if isinstance(item, dict)
        and int(item.get("iteration") or 0) == int(state_.get("agentic_iteration") or 0)
    ]
    used_task_ids: set[str] = set()
    issues: list[str] = []
    for intent in required:
        tool = str(intent.get("tool") or "")
        expected_args = intent.get("args") or {}
        schema = snapshots.get(tool)
        schema_hash = str(intent.get("schema_hash") or "")
        if (
            not isinstance(schema, dict)
            or not schema_hash
            or tool_schema_contract_hash(schema) != schema_hash
        ):
            issues.append(f"precision_contract_changed:{tool}")
            continue

        matching_tasks = [
            task
            for task in plan
            if str(task.get("id") or "") not in used_task_ids
            and task.get("category") == "precision_tools"
            and task.get("mcp_tool") == tool
            and precision_args_match(task.get("mcp_args"), expected_args, schema)
        ]
        if not matching_tasks:
            issues.append(f"precision_evidence_mismatch:{tool}:task")
            continue
        task = matching_tasks[0]
        task_id = str(task.get("id") or "")
        used_task_ids.add(task_id)
        matching_evidence = [
            item
            for item in evidence
            if str(item.get("task_id") or "") == task_id
            and item.get("status") == "completed"
        ]
        if not matching_evidence:
            issues.append(f"precision_evidence_missing:{task_id or tool}")
            continue
        if len(matching_evidence) != 1:
            issues.append(f"precision_evidence_mismatch:{task_id}:duplicate")
            continue
        item = matching_evidence[0]
        used_args = item.get("args")
        if (
            item.get("tool") != tool
            or item.get("contract_hash") != schema_hash
            or not precision_args_match(used_args, expected_args, schema)
            or item.get("input_hash") != canonical_json_hash(used_args)
        ):
            issues.append(f"precision_evidence_mismatch:{task_id}:content")
            continue
        if schema.get("structured_result_required"):
            facts = item.get("facts")
            expected_result_hash = canonical_json_hash(
                {
                    "contract_hash": schema_hash,
                    "input_normalized": precision_evidence_input(used_args, schema),
                    "facts": facts,
                }
            )
            output_schema = schema.get("output_schema")
            try:
                structured_valid = (
                    isinstance(output_schema, dict)
                    and not list(
                        Draft202012Validator(output_schema).iter_errors(facts)
                    )
                )
                if isinstance(output_schema, dict):
                    Draft202012Validator.check_schema(output_schema)
            except SchemaError:
                structured_valid = False
            source_metadata = item.get("source_metadata")
            if (
                not structured_valid
                or item.get("result_hash") != expected_result_hash
                or item.get("contract_id") != schema.get("contract_id")
                or item.get("contract_version") != schema.get("contract_version")
                or item.get("determinism") != schema.get("determinism")
                or not isinstance(source_metadata, dict)
                or not source_metadata.get("version")
            ):
                issues.append(f"precision_evidence_mismatch:{task_id}:structured")
    return issues


def evaluate_quality_gate(state_: dict) -> QualityGateDecision:
    """Return ``pass``, ``block`` or ``gate`` without performing I/O."""
    trust_verdict = (state_.get("trust_verdict") or "").upper()
    # VERIFIER_MODE=gate: the answer verifier's verdict replaces the trust verdict (set by graph.synthesis.quality_gate_node only then)
    verifier_verdict = (state_.get("verifier_verdict") or "").upper()
    if verifier_verdict in ("PROCEED", "PROCEED_WITH_ASSUMPTION", "BLOCK"):
        trust_verdict = verifier_verdict
    cynefin_domain = classify_cynefin(state_).value

    incomplete = incomplete_plan_tasks(state_)
    if incomplete:
        detail = ",".join(
            f"{task['task_id']}:{task['status']}" for task in incomplete[:8]
        )
        return QualityGateDecision(
            "block",
            f"incomplete_task_execution:{detail}",
            cynefin_domain,
        )

    precision_issues = precision_evidence_issues(state_)
    if precision_issues:
        return QualityGateDecision(
            "block",
            precision_issues[0],
            cynefin_domain,
        )

    if state_.get("required_precision_intents"):
        binding_status = str(state_.get("precision_binding_status") or "")
        binding_errors = state_.get("precision_binding_errors") or []
        if binding_status != "bound":
            reason = str(binding_errors[0]) if binding_errors else "precision_binding_missing"
            return QualityGateDecision("block", reason, cynefin_domain)
        from services.precision_response import precision_binding_hash

        slots = [
            item
            for item in (state_.get("precision_fact_slots") or [])
            if isinstance(item, dict)
        ]
        if (
            not slots
            or state_.get("precision_binding_hash") != precision_binding_hash(slots)
            or state_.get("precision_bound_response_hash")
            != canonical_json_hash(str(state_.get("final_response") or ""))
        ):
            return QualityGateDecision(
                "block",
                "precision_binding_mismatch",
                cynefin_domain,
            )

    if trust_verdict == "BLOCK":
        return QualityGateDecision("block", "verifier_block" if verifier_verdict == "BLOCK" else "trust_score_block", cynefin_domain)

    # Optional 3-tier assertion gate check via DSPy teleprompter trace if provided
    trace_data = state_.get("dspy_trace") or state_.get("trace")
    if isinstance(trace_data, dict):
        dspy_res = run_dspy_teleprompter_gate(trace_data)
        if not dspy_res.get("passed"):
            return QualityGateDecision("block", f"dspy_tier{dspy_res.get('tier_failed')}_failed:{dspy_res.get('reason')}", cynefin_domain)

    # Autonomous Self-Plausibility Check
    final_resp = str(state_.get("final_response") or "")
    if final_resp:
        plaus_res = verify_response_plausibility(final_resp, state_.get("mcp_evidence") or [], task_text=state_.get("input"))
        if not plaus_res.get("plausible"):
            return QualityGateDecision("block", f"plausibility_failed:{plaus_res.get('reason')}", cynefin_domain)

    constitution_violations = state_.get("constitution_violations") or []
    has_constitution_warning = any(
        str(v.get("on_violation", "")).lower() == "warn"
        for v in constitution_violations
        if isinstance(v, dict)
    )
    requires_human = (
        trust_verdict == "PROCEED_WITH_ASSUMPTION"
        and (
            has_constitution_warning
            or cynefin_domain in {"COMPLEX", "CHAOTIC"}
        )
    )
    if not requires_human:
        return QualityGateDecision("pass", "", cynefin_domain)

    reason_parts = ["trust verdict PROCEED_WITH_ASSUMPTION"]
    if has_constitution_warning:
        reason_parts.append("constitution warning")
    if cynefin_domain in {"COMPLEX", "CHAOTIC"}:
        reason_parts.append(f"Cynefin {cynefin_domain}")
    return QualityGateDecision("gate", "; ".join(reason_parts), cynefin_domain)


def check_program_sketch_bounds(sketch_data: dict) -> dict:
    """Fills a program sketch's 'holes' (placeholders) from simple per-type
    bound checks (int min<=max, non-empty enum) — plain dict/comparison
    logic, no constraint solver. 'smt_bounds'/'unsat_core' in the input/
    output shape name the SMT concepts this approximates, not an actual
    SMT solver call: there is no cross-variable constraint propagation here,
    each hole is checked independently (GAP_REPORT_2026-09-11.md, GAP-07).
    A real SMT-backed sketch solver, if needed, is separate, scoped research
    work, not a rename of this function.
    """
    holes = sketch_data.get('holes', {})
    smt_bounds = sketch_data.get('smt_bounds', {})
    
    sketch_valid = True
    filled_holes = {}
    unsat_core = []
    
    for hole_name in holes:
        if hole_name in smt_bounds:
            bounds = smt_bounds[hole_name]
            b_type = bounds.get('type')
            if b_type == 'int':
                min_val = bounds.get('min')
                max_val = bounds.get('max')
                if min_val is not None and max_val is not None and min_val > max_val:
                    sketch_valid = False
                    unsat_core.append(hole_name)
                else:
                    filled_holes[hole_name] = min_val if min_val is not None else (max_val if max_val is not None else 0)
            elif b_type == 'enum':
                enum_list = bounds.get('enum', [])
                if not enum_list:
                    sketch_valid = False
                    unsat_core.append(hole_name)
                else:
                    filled_holes[hole_name] = enum_list[0]
            elif b_type == 'str':
                filled_holes[hole_name] = "default_string"
            else:
                filled_holes[hole_name] = "default"
        else:
            filled_holes[hole_name] = None
            
    if unsat_core:
        sketch_valid = False
        
    return {
        'sketch_valid': sketch_valid,
        'filled_holes': filled_holes if sketch_valid else {},
        'unsat_core': unsat_core if unsat_core else None
    }


def check_trace_assertion_tiers(trace: dict) -> dict:
    """Checks a prompt execution trace against 3 ordered assertion tiers
    (egress_local_only set, canonical_json_hash present, trust_verdict not
    BLOCK) — three dict-key checks, no DSPy optimizer or teleprompter is
    invoked here (GAP_REPORT_2026-09-11.md, GAP-07). A real DSPy-backed gate,
    if needed, is separate, scoped work, not a rename of this function.
    """
    if not trace.get('egress_local_only', False):
        return {'passed': False, 'tier_failed': 1, 'reason': 'Egress local only flag is missing or false'}
    if not trace.get('canonical_json_hash'):
        return {'passed': False, 'tier_failed': 2, 'reason': 'Canonical JSON hash is missing or empty'}
    if trace.get('trust_verdict') == 'BLOCK':
        return {'passed': False, 'tier_failed': 3, 'reason': 'Trust verdict is BLOCK'}
        
    return {'passed': True, 'tier_failed': None, 'reason': 'Passed all tiers'}


_CODE_TASK_VERB_RE = re.compile(r'\b(implement|write|refactor|debug|fix)\b', re.IGNORECASE)
_CODE_TASK_LANG_RE = re.compile(
    r'\b(rust|c\+\+|c#|python|javascript|typescript|java|golang|go|sql|bash|shell|kotlin|swift|ruby|php)\b',
    re.IGNORECASE,
)


def _task_requires_code(task_text: str) -> bool:
    """Heuristic: does the task explicitly ask for an implementation in a
    named programming language? Catches a failure mode distinct from
    empty/too-short or an unclosed code fence: a long, fluent-looking
    response that free-associates through unrelated vocabulary and never
    once produces actual code. Observed live -- a merger synthesis response
    to "Implement ... in Rust (or modern C++20)" degenerated (under
    repeat_penalty, which suppresses verbatim repetition but not topic
    drift) into a multi-thousand-word chain of loosely associated nouns and
    verbs with zero code fences, which passed the emptiness/code-block
    checks below undetected.
    """
    if not task_text:
        return False
    return bool(_CODE_TASK_VERB_RE.search(task_text) and _CODE_TASK_LANG_RE.search(task_text))


def verify_response_plausibility(response_text: str, context_facts: list = None, task_text: str = None) -> dict:
    """
    Performs autonomous self-plausibility checks on a generated response:
    1. Empty / Whitespace check
    2. Contradiction & Negation check against context facts
    3. Structural formatting check (no unclosed code blocks)
    4. Required-code check (task_text, when given, must be answered with
       actual code if it explicitly asked for an implementation)
    """
    if not response_text or len(response_text.strip()) < 10:
        return {"plausible": False, "reason": "empty_or_too_short"}

    # Check unclosed code blocks
    if response_text.count("```") % 2 != 0:
        return {"plausible": False, "reason": "unclosed_code_block"}

    if task_text and _task_requires_code(task_text) and "```" not in response_text:
        return {"plausible": False, "reason": "missing_required_code"}

    # Fact contradiction check if context provided
    if context_facts:
        text_lower = response_text.lower()
        for fact in context_facts:
            if isinstance(fact, str) and fact.lower() in text_lower:
                # Basic check for direct negation of known fact
                negated = f"not {fact.lower()}"
                if negated in text_lower:
                    return {"plausible": False, "reason": f"fact_negation_detected:{fact}"}

    return {"plausible": True, "reason": "passed_plausibility_checks"}
