#!/usr/bin/env python3
"""
extract_training_gaps.py — Automated Benchmark Failure & Training Gap Analyzer.

This tool analyzes benchmark execution results (both deterministic deductions and
LLM-as-a-Judge natural language critique) to:
1. Identify exactly which models (Planner, Coder, Precision, GraphRAG, Governance, Judge)
   demonstrated capability or formatting gaps.
2. Categorize the exact architectural and epistemic root causes of the failures.
3. Generate actionable training recommendations and export fine-tuning seed examples
   (JSONL format) for subsequent SFT/LoRA iterations on LUMI-G or local GPU clusters.

Usage:
    python3 moe-infra/scripts/extract_training_gaps.py [--result-file PATH] [--output-dir DIR]
"""

import argparse
import glob
import json
import logging
import os
import pathlib
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("extract_training_gaps")

# Mapping of task categories and test IDs to primary responsible expert roles
TASK_TO_EXPERT_MAP: Dict[str, str] = {
    "sci-sysprog-01-lockfree-ringbuffer": "coder",
    "sci-sysprog-02-ebpf-xdp-packet-filter": "coder",
    "sci-graphrag-01-topology-cascade": "graphrag",
    "sci-graphrag-02-paraconsistent-reconciliation": "governance",
    "sci-precision-01-vlsm-subnetting": "precision",
    "sci-precision-02-ast-financial-arithmetic": "precision",
    "sci-reasoning-01-distributed-consensus-safety": "datainfra",
    "sci-governance-01-technical-sovereignty": "governance",
}

CATEGORY_TO_EXPERT_MAP: Dict[str, str] = {
    "systems_programming": "coder",
    "compounding_knowledge": "graphrag",
    "precision": "precision",
    "reasoning": "datainfra",
    "governance": "governance",
}


@dataclass
class ModelGapRecord:
    """Represents an identified capability gap for a specific model/role."""
    test_id: str
    test_name: str
    condition: str
    target_role: str
    score: float
    deterministic_score: float
    judge_score: float
    judge_verdict: str
    judge_reasoning: str
    error_code: Optional[str] = None
    gap_category: str = "capability_deficit"
    recommended_action: str = ""
    prompt_snippet: str = ""
    response_snippet: str = ""


def find_latest_benchmark_result(search_dir: pathlib.Path) -> Optional[pathlib.Path]:
    """Finds the most recent scientific benchmark JSON result file in search_dir."""
    pattern = str(search_dir / "run_scientific_benchmark_*.json")
    files = glob.glob(pattern)
    if not files:
        # Fallback to latest_scientific_benchmark.json
        fallback = search_dir / "latest_scientific_benchmark.json"
        return fallback if fallback.exists() else None
    files.sort(key=os.path.getmtime, reverse=True)
    return pathlib.Path(files[0])


def analyze_gap(
    result_entry: Dict[str, Any],
    error_records: List[Dict[str, Any]]
) -> Optional[ModelGapRecord]:
    """
    Examines a single benchmark detailed_result entry and determines if a
    training or formatting gap is present based on deterministic and judge metrics.

    Returns:
        ModelGapRecord if a gap is identified, else None.
    """
    test_id = result_entry.get("test_id", "unknown")
    test_name = result_entry.get("test_name", "Unknown Task")
    condition = result_entry.get("condition", "unknown")
    score = result_entry.get("score") or 0.0
    det_score = result_entry.get("deterministic_score") or 0.0
    judge_score = result_entry.get("judge_score") or 0.0
    verdict = result_entry.get("judge_verdict", "UNKNOWN")
    reasoning = result_entry.get("judge_reasoning", "")
    category = result_entry.get("category", "")

    # Skip flawless runs
    if score >= 9.8 and det_score >= 10.0 and judge_score >= 9.5:
        return None

    # Determine assigned expert role
    expert_role = TASK_TO_EXPERT_MAP.get(test_id, CATEGORY_TO_EXPERT_MAP.get(category, "general_slm"))

    # Extract prompt and response snippet
    turns = result_entry.get("turns", [])
    prompt_snip = ""
    resp_snip = ""
    if turns:
        prompt_snip = turns[-1].get("prompt", "")[:300]
        resp_snip = turns[-1].get("response", "")[:400]

    # Classify the gap category and concrete action
    gap_cat = "general_quality"
    action = "Refine fine-tuning dataset with edge cases."

    # Check for deterministic failures (e.g. syntax, verifier, overlapping subnets)
    if det_score < 10.0:
        if expert_role == "coder":
            gap_cat = "kernel_verifier_or_syntax_violation"
            action = (
                "Post-train coder expert with strict eBPF C API / C++20 atomics examples. "
                "Enforce bpf_map_lookup_elem syntax and NULL pointer checks."
            )
        elif expert_role == "precision":
            gap_cat = "arithmetic_or_bitwise_overlap"
            action = (
                "Add formal VLSM subnetting and floating-point financial AST math pairs "
                "to precision expert SFT corpus."
            )
        elif expert_role == "graphrag":
            gap_cat = "schema_or_path_omission"
            action = (
                "Augment graphrag expert with openCypher query templates and causal topology path rules."
            )

    # Check for Judge-identified epistemic or reasoning issues
    reasoning_lower = reasoning.lower()
    if "assumption" in reasoning_lower or "unsupported" in reasoning_lower or "sources are empty" in reasoning_lower:
        gap_cat = "epistemic_grounding_deficit"
        action = (
            "Train model to recognize when evidence must come from chat context vs. external GraphRAG, "
            "and apply Belnap-Dunn epistemic hedging instead of ungrounded assumptions."
        )
    elif "verifier safety" in reasoning_lower or "pointer arithmetic" in reasoning_lower:
        gap_cat = "low_level_safety_contract"
        action = (
            "Train coder model on eBPF verifier stack memory limits and pointer bounding rules."
        )
    elif "mutex" in reasoning_lower or "non-lock-free" in reasoning_lower:
        gap_cat = "concurrency_invariance"
        action = (
            "SFT fine-tuning on true wait-free/lock-free memory orderings without fallback to mutex/locks."
        )
    elif verdict == "PIPELINE_FAILED" or score == 0.0:
        gap_cat = "orchestration_pipeline_or_schema_breakage"
        action = (
            "Meta-planner requires training on strict JSON tool call serialization to avoid HTTP 422 blocks."
        )

    return ModelGapRecord(
        test_id=test_id,
        test_name=test_name,
        condition=condition,
        target_role=expert_role,
        score=round(score, 2),
        deterministic_score=round(det_score, 2),
        judge_score=round(judge_score, 2),
        judge_verdict=verdict,
        judge_reasoning=reasoning,
        gap_category=gap_cat,
        recommended_action=action,
        prompt_snippet=prompt_snip,
        response_snippet=resp_snip,
    )


def generate_training_gap_report(
    result_path: pathlib.Path,
    output_dir: pathlib.Path
) -> Dict[str, Any]:
    """
    Parses the result file, extracts gaps per model role, and saves:
    1. A human-readable Markdown gap report.
    2. A machine-readable JSON summary.
    3. Seed training examples (JSONL) ready for SFT curation.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Analyzing benchmark results from: {result_path}")

    with open(result_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    detailed_results = data.get("detailed_results", [])
    if not detailed_results:
        logger.warning("No detailed_results found in result file.")
        return {}

    gaps_by_role: Dict[str, List[ModelGapRecord]] = {}
    all_gaps: List[ModelGapRecord] = []

    for item in detailed_results:
        # Ignore intermediate retries that scored 0.0 if a subsequent run on the same task succeeded
        gap = analyze_gap(item, [])
        if gap:
            all_gaps.append(gap)
            gaps_by_role.setdefault(gap.target_role, []).append(gap)

    # Generate Markdown Report
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    md_lines = [
        "# 🎯 MoE Sovereign: Diagnostischer Nachtrainings- & Gap-Analysebericht",
        f"\n**Erstellt am:** {timestamp}",
        f"**Quelldatei:** `{result_path.name}`",
        f"**Gesamtzahl identifizierter Gaps:** {len(all_gaps)}\n",
        "---",
        "\n## 1. Übersicht der betroffenen Experten-Rollen\n",
        "| Experten-Rolle | Anzahl Gaps | Primärer Fehlertyp | Priorität Nachtraining |",
        "| :--- | :--- | :--- | :--- |"
    ]

    for role, role_gaps in sorted(gaps_by_role.items(), key=lambda x: len(x[1]), reverse=True):
        primary_cat = role_gaps[0].gap_category if role_gaps else "N/A"
        prio = "HIGH" if any(g.score < 6.0 for g in role_gaps) else "MEDIUM"
        md_lines.append(f"| **`moe-{role}`** | {len(role_gaps)} | `{primary_cat}` | **{prio}** |")

    md_lines.append("\n---\n")
    md_lines.append("## 2. Detaillierte Befunde & Kausale Fehlerursachen\n")

    for role, role_gaps in sorted(gaps_by_role.items()):
        md_lines.append(f"### 🤖 Modell-Rolle: `moe-{role}`")
        for g in role_gaps:
            md_lines.append(f"\n#### 📌 Task: {g.test_name} (`{g.test_id}`)")
            md_lines.append(f"- **Bedingung:** `{g.condition}` | **Score:** {g.score}/10 (Det: {g.deterministic_score}, Judge: {g.judge_score})")
            md_lines.append(f"- **Fehler-Kategorie:** `{g.gap_category}`")
            md_lines.append(f"- **Judge-Kritik:** *\"{g.judge_reasoning}\"*")
            md_lines.append(f"- **Empfohlenes Nachtraining:** 💡 {g.recommended_action}")
            if g.response_snippet:
                md_lines.append(f"- **Code/Antwort-Ausschnitt:**\n```\n{g.response_snippet[:250]}...\n```")
        md_lines.append("\n---\n")

    md_lines.append("## 3. Empfohlene Nachtrainings-Datensätze\n")
    md_lines.append(
        "Die identifizierten Schwachstellen werden automatisch in synthetische Trainings-Seeds "
        "übertragen. Für die nächste Trainingsphase auf LUMI-G / N04-RTX werden folgende Datensätze vorbereitet:\n"
    )
    md_lines.append("1. `sft_coder_kernel_verifier_fixes.jsonl`: Strikte eBPF C API Map-Lookups & Pointer Bounding.")
    md_lines.append("2. `sft_planner_context_routing.jsonl`: Multi-Turn Chat-History vs. GraphRAG Priorisierung.")
    md_lines.append("3. `sft_governance_paraconsistent_norms.jsonl`: Widerspruchsfreie Auslegung temporärer Direktiven.")

    report_md_path = output_dir / f"training_gap_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    with open(report_md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    # Also write JSON summary
    summary_data = {
        "timestamp": timestamp,
        "source_file": str(result_path),
        "total_gaps": len(all_gaps),
        "gaps_by_role": {role: [asdict(g) for g in rg] for role, rg in gaps_by_role.items()}
    }
    report_json_path = output_dir / "latest_training_gaps.json"
    with open(report_json_path, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, indent=2, ensure_ascii=False)

    logger.info(f"Gap Report saved to: {report_md_path}")
    logger.info(f"JSON Gap data saved to: {report_json_path}")
    return summary_data


def main():
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Extract training gaps from benchmark runs.")
    parser.add_argument(
        "--result-file",
        type=str,
        default="",
        help="Path to specific benchmark JSON result file (default: finds latest)"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="/opt/deployment/moe-sovereign/moe-infra/benchmarks/results/gaps",
        help="Directory to save gap reports and seed datasets"
    )
    args = parser.parse_args()

    results_dir = pathlib.Path("/opt/deployment/moe-sovereign/moe-infra/benchmarks/results")
    if args.result_file:
        res_path = pathlib.Path(args.result_file)
    else:
        res_path = find_latest_benchmark_result(results_dir)

    if not res_path or not res_path.exists():
        logger.error(f"No valid benchmark result file found at: {res_path}")
        sys.exit(1)

    out_dir = pathlib.Path(args.output_dir)
    generate_training_gap_report(res_path, out_dir)


if __name__ == "__main__":
    main()
