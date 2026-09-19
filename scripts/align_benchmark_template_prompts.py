#!/usr/bin/env python3
"""Align the system prompts of the scientific benchmark expert templates with the training prompts.

Why: the fine-tuned experts, planners and judges were trained with fixed role prompts
(``CHATML_SYSTEM_PROMPTS`` in generate_expert_ensemble_datasets.py, ``_PLANNER_PROMPT_TEMPLATE`` in
generate_planner_dataset.py) while the templates sent unrelated one-liners. The pre-finetune reference templates
must receive the SAME prompts, otherwise the comparison mixes a weights effect with a prompt effect.

What it sets, identically in every target template:
  * experts[cat].system_prompt  = training role prompt of the domain role assigned to the category
  * judge_prompt                = training judge prompt
  * planner_prompt              = training preamble + category block (training format) for the template's own
                                  categories + the "never return an empty array" guard

Usage (host only, uses ``docker exec terra_checkpoints psql``):
    python3 scripts/align_benchmark_template_prompts.py            # dry run, prints what would change
    python3 scripts/align_benchmark_template_prompts.py --apply    # backs up the rows, then updates them
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Templates whose prompts are aligned (name -> family). Hybrid and frontier templates are out of scope.
TARGETS = [
    "LUMI-G Base (Pre-Finetune)",
    "LUMI-G Base (Pre-Finetune) - Deliberation",
    "LUMI-G Base (Pre-Finetune) - No-GraphRAG",
    "LUMI-G OLMo + SmolLM3 Sovereign Ensemble",
    "LUMI-G OLMo + SmolLM3 Sovereign Ensemble - Deliberation",
    "LUMI-G OLMo + SmolLM3 Sovereign Ensemble - No-GraphRAG",
    "LUMI-G OLMo + SmolLM3 Sovereign Ensemble - Review",
    "LUMI-G OLMo + SmolLM3 Sovereign Ensemble - Review NoSC",
    "Open-Weight Base (Pre-Finetune)",
    "Open-Weight Base (Pre-Finetune) - Deliberation",
    "Open-Weight Base (Pre-Finetune) - No-GraphRAG",
    "Open-Weight Finetuned Ensemble",
    "Open-Weight Finetuned Ensemble - Deliberation",
    "Open-Weight Finetuned Ensemble - No-GraphRAG",
]

# category -> training role of the fine-tuned domain expert that serves it (same rule in both tracks)
CATEGORY_ROLE = {
    "general": "omni", "reasoning": "omni",
    "research": "research", "web_researcher": "research",
    "security": "security",
    "governance": "governance",
    "code_reviewer": "coder", "systems_programming": "coder",
    "precision_tools": "precision", "math": "precision", "tool_expert": "precision",
    "data_analyst": "datainfra", "technical_support": "datainfra", "dynamic": "datainfra",
    "compounding_knowledge": "graphrag", "graphrag": "graphrag", "science": "graphrag",
}

# Descriptions for categories that are not part of the training taxonomy (wording taken from the existing template
# prompts); categories of the training pool are rendered with the training descriptions.
EXTRA_DESCRIPTIONS = {
    "research": ["Literature review, fact-finding, synthesizing information from sources"],
    "security": ["Vulnerability analysis, secure coding review, threat assessment"],
    "governance": ["Policy, regulation, compliance, organizational process questions"],
    "compounding_knowledge": ["Storing, updating or querying structured facts and relationships in the",
                              "Knowledge Graph (GraphRAG); use it whenever the task persists or retrieves them"],
    "precision_tools": ["Deterministic calculations via a registered MCP tool ONLY;", "never invent an mcp_tool name"],
    "systems_programming": ["Systems programming: concurrency, memory ordering, kernel and eBPF code"],
    "web_researcher": ["Web research and literature extraction"],
    "tool_expert": ["MCP tool execution"],
    "graphrag": ["Knowledge-graph extraction and relational reasoning (GraphRAG)"],
}
# Fixed category order so that templates with the same categories get byte-identical planner prompts.
CATEGORY_ORDER = [
    "general", "reasoning", "research", "web_researcher", "science", "security", "governance", "code_reviewer",
    "systems_programming", "precision_tools", "math", "tool_expert", "data_analyst", "technical_support",
    "compounding_knowledge", "graphrag", "dynamic",
]
# The fine-tuned expert that serves data_analyst is the data-infrastructure expert (Spur 1 assignment), so the
# planner must be told the same scope instead of the training pool's "statistics / ML" wording.
DESCRIPTION_OVERRIDES = {
    "data_analyst": ["Data pipelines, databases, SQL and analytics"],
}
# Model assignments that differ between the tracks for the same category name (Spur 1 is the reference).
MODEL_FIXES = {
    "Open-Weight Finetuned Ensemble": {"data_analyst": "hf.co/h3rb3rn/moe-expert-datainfra-4b:Q4_K_M"},
    "Open-Weight Finetuned Ensemble - Deliberation": {"data_analyst": "hf.co/h3rb3rn/moe-expert-datainfra-4b:Q4_K_M"},
    "Open-Weight Finetuned Ensemble - No-GraphRAG": {"data_analyst": "hf.co/h3rb3rn/moe-expert-datainfra-4b:Q4_K_M"},
}
EMPTY_PLAN_GUARD = (
    'Use "dynamic" only when a task\'s domain is genuinely absent from all categories above.\n'
    "You MUST always produce at least one task. NEVER return an empty JSON array. "
    'Minimum valid response: [{"task": "<concrete description of what to do>", "category": "general"}]'
)


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
    spec.loader.exec_module(module)
    return module


def psql(sql: str, stdin: str | None = None) -> str:
    cmd = ["docker", "exec", "-i", "terra_checkpoints", "psql", "-U", "moe_admin", "-d", "moe_userdb",
           "-At", "-v", "ON_ERROR_STOP=1"] + ([] if stdin else ["-c", sql])
    done = subprocess.run(cmd, input=stdin, capture_output=True, text=True)
    if done.returncode:
        raise SystemExit(f"psql failed: {done.stderr.strip()[:300]}")
    return done.stdout.strip()


def canonical_prompts():
    experts_mod = _load("scripts/generate_expert_ensemble_datasets.py", "gen_experts")
    planner_mod = _load("scripts/generate_planner_dataset.py", "gen_planner")
    strip = lambda s: re.sub(r"<\|im_(?:start|end)\|>(?:system)?", "", s).strip()
    roles = {k: strip(v) for k, v in experts_mod.CHATML_SYSTEM_PROMPTS.items()}
    train = planner_mod.PLANNER_SYSTEM_PROMPT
    preamble = train[: train.index("MANDATORY:")].strip()
    return roles, preamble, planner_mod


def planner_prompt(categories: list[str], preamble: str, planner_mod) -> str:
    pool = planner_mod._CANONICAL_LLM_CATEGORIES
    described = {}
    for cat in sorted(categories, key=CATEGORY_ORDER.index):
        if cat == "dynamic":  # handled by the runtime "DYNAMIC EXPERT" section
            continue
        described[cat] = DESCRIPTION_OVERRIDES.get(cat) or pool.get(cat) or EXTRA_DESCRIPTIONS.get(cat)
        if described[cat] is None:
            raise SystemExit(f"no description for category {cat!r}; extend EXTRA_DESCRIPTIONS")
    # training column width (21) unless a category name is longer
    column = max(planner_mod._CATEGORY_COLUMN, max(len(name) + 4 for name in described))
    lines = []
    for name, description in described.items():
        head, *rest = description
        lines.append(f'"{name}"'.ljust(column) + head)
        lines.extend(" " * column + line for line in rest)
    block = "\n".join(lines)
    rule = "─" * 66
    return f"{preamble}\n\n{rule}\nLLM EXPERT CATEGORIES\n{rule}\n{block}\n\n{EMPTY_PLAN_GUARD}"


def h(text: str) -> str:
    return hashlib.sha1((text or "").strip().encode()).hexdigest()[:8]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--backup", default=str(ROOT / "benchmarks/results/runbook/template_prompts_backup.json"))
    args = ap.parse_args()
    roles, preamble, planner_mod = canonical_prompts()
    judge = roles["judge"]
    names = ", ".join("'" + n.replace("'", "''") + "'" for n in TARGETS)
    rows = json.loads(psql(f"select json_agg(t) from (select id, name, config_json from admin_expert_templates "
                           f"where name in ({names}) order by name) t") or "[]")
    missing = set(TARGETS) - {r["name"] for r in rows}
    if missing:
        raise SystemExit(f"templates not found: {sorted(missing)}")
    updates, backup = [], []
    for row in rows:
        cfg = json.loads(row["config_json"])
        backup.append({"id": row["id"], "name": row["name"], "config_json": row["config_json"]})
        new = json.loads(row["config_json"])
        cats = list(new["experts"].keys())
        unknown = [c for c in cats if c not in CATEGORY_ROLE]
        if unknown:
            raise SystemExit(f"{row['name']}: category without role mapping: {unknown}")
        for cat, ec in new["experts"].items():
            ec["system_prompt"] = roles[CATEGORY_ROLE[cat]]
        for cat, model in MODEL_FIXES.get(row["name"], {}).items():
            for slot in new["experts"][cat]["models"]:
                if slot.get("role") != "always":
                    slot["model"] = model
        new["judge_prompt"] = judge
        new["planner_prompt"] = planner_prompt(cats, preamble, planner_mod)
        changed = [k for k in ("planner_prompt", "judge_prompt") if new[k] != cfg.get(k)]
        changed += [f"experts.{c}" for c in cats if new["experts"][c]["system_prompt"] != cfg["experts"][c].get("system_prompt")]
        changed += [f"model.{c}" for c in MODEL_FIXES.get(row["name"], {}) if new["experts"][c]["models"] != cfg["experts"][c]["models"]]
        print(f"{row['name'][:58]:58s} categories={len(cats):2d} changes={len(changed):2d} planner={h(new['planner_prompt'])} judge={h(judge)}")
        if changed:
            updates.append((row["id"], json.dumps(new, ensure_ascii=False)))
    if not args.apply:
        print(f"\ndry run: {len(updates)} of {len(rows)} templates would change (use --apply)")
        return
    Path(args.backup).write_text(json.dumps(backup, indent=1))
    sql = ["BEGIN;"] + [
        f"UPDATE admin_expert_templates SET config_json=$j${cfg}$j$, updated_at=now()::text WHERE id='{tid}';"
        for tid, cfg in updates
    ] + ["COMMIT;"]
    psql("", stdin="\n".join(sql))
    print(f"\napplied to {len(updates)} templates; backup: {args.backup}")


if __name__ == "__main__":
    main()
