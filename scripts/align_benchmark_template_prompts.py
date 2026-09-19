#!/usr/bin/env python3
"""Align the system prompts of the scientific benchmark expert templates with the training prompts.

Why: the fine-tuned experts, planners and judges were trained with fixed role prompts
(``CHATML_SYSTEM_PROMPTS`` in generate_expert_ensemble_datasets.py, ``_PLANNER_PROMPT_TEMPLATE`` in
generate_planner_dataset.py) while the templates sent unrelated one-liners. The pre-finetune reference templates
must receive the SAME prompts, otherwise the comparison mixes a weights effect with a prompt effect.

What it sets, identically in every target template:
  * experts[cat].system_prompt  = training role prompt of the domain role assigned to the category
  * judge_prompt                = training judge prompt
  * endpoints                   = expert i on N02-M60-02..09 (1:1 in both tracks), planner N04-RGTX, judge N04-RTX
  * experts                     = exactly the eight domain experts (EXPERT_SET); Spur 2 templates are reduced from 15
  * planner_prompt              = the original descriptive category list (validated by A/B test, see below) for the
                                  template's own categories, in a fixed order, plus the "never return an empty array" guard

Planner prompt decision (A/B test 2026-09-19, 9 planner calls per variant on tmpl-smollm3-nograph): the training preamble
+ training-format category block (v1, v2) never routed a GDPR question to `governance` (0/6) and v1 once returned an
empty plan; the original list format ("- category: description") routed it 3/3 and never returned an empty plan. The
planner prompt is therefore NOT aligned with the training prompt; experts and judge are.

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

# Every benchmark template has exactly these eight experts (the eight fine-tuned domain roles). Templates that carry more
# categories (Spur 2 had 15) are reduced to them; value = the existing category whose model/endpoint/tool slot is kept.
EXPERT_SET = {
    "general": "general", "research": "research", "security": "security", "governance": "governance",
    "data_analyst": "data_analyst", "code_reviewer": "code_reviewer",
    "precision_tools": "tool_expert",          # Spur 2 name of the precision expert
    "compounding_knowledge": "graphrag",       # Spur 2 name of the knowledge-graph expert
}

# Instance placement, identical in both tracks (operator requirement 2026-09-20): one Ollama instance per expert on the
# N02-M60 host in ascending port order (11435..11442), judge on N04-RTX (:11434), planner on N04-RGTX (:11435).
EXPERT_ENDPOINTS = {
    "general": "N02-M60-02", "security": "N02-M60-03", "research": "N02-M60-04", "governance": "N02-M60-05",
    "compounding_knowledge": "N02-M60-06", "precision_tools": "N02-M60-07", "data_analyst": "N02-M60-08",
    "code_reviewer": "N02-M60-09",
}
PLANNER_ENDPOINT = "N04-RGTX"
JUDGE_ENDPOINT = "N04-RTX"

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

# Planner category descriptions in the original list style. The eight Spur 1 entries are verbatim from the original
# fine-tuned Spur 1 planner prompt (each hint was added after an observed routing failure); the rest follow the same style.
PLANNER_DESCRIPTIONS = {
    "general": "everyday/cross-domain questions with no specialist fit.",
    "security": "vulnerability analysis, secure coding review, threat assessment.",
    "research": "literature review, fact-finding, synthesizing information from sources.",
    "governance": "policy, regulation, compliance, organizational process questions.",
    "compounding_knowledge": (
        "storing, updating, or querying structured knowledge in the Knowledge Graph/GraphRAG (e.g. system topologies, "
        "entity relationships, cluster architecture, \"remember/store this fact\" tasks) -- use this whenever the task is "
        "about persisting or retrieving structured facts/relationships, even if the subject matter (auth, networking, "
        "etc.) sounds like another category."
    ),
    "precision_tools": (
        "ONLY deterministic calculations dispatched to a real registered MCP tool (e.g. calculate, subnet_calc, "
        "vlsm_subnet_calc). Never invent an mcp_tool name -- if no exact tool matches, do not use this category."
    ),
    "data_analyst": "data pipelines, databases, analytics.",
    "code_reviewer": (
        "writing, reviewing, or explaining source code (any language), including systems programming, kernel/eBPF code, "
        "and code review. Never route code generation through precision_tools -- there is no code-generation MCP tool."
    ),
    "reasoning": "logic puzzles, argumentation, deductive reasoning.",
    "science": "physics, chemistry, biology, research methodology.",
    "math": "mathematical proofs, derivations, theoretical mathematics (NOT for arithmetic -- use precision_tools).",
    "technical_support": "troubleshooting, installation, configuration, DevOps, networking.",
    "systems_programming": "systems programming: concurrency, memory ordering, kernel and eBPF code.",
    "web_researcher": "web research and literature extraction.",
    "tool_expert": "MCP tool execution.",
    "graphrag": "knowledge-graph extraction and relational reasoning (GraphRAG).",
}
PLANNER_HEADER = (
    "You are a specialized planner model in a Mixture of Experts (MoE) system. Coordinate planning, tool execution, "
    "and task delegation across these expert areas:"
)
# Fixed category order (the Spur 1 subset keeps the original order) so equal category sets give byte-identical prompts.
CATEGORY_ORDER = [
    "general", "reasoning", "security", "research", "web_researcher", "science", "governance", "compounding_knowledge",
    "graphrag", "precision_tools", "tool_expert", "math", "data_analyst", "technical_support", "code_reviewer",
    "systems_programming", "dynamic",
]
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
    """Training role prompts for experts and judge (the planner prompt is not taken from training, see docstring)."""
    experts_mod = _load("scripts/generate_expert_ensemble_datasets.py", "gen_experts")
    strip = lambda s: re.sub(r"<\|im_(?:start|end)\|>(?:system)?", "", s).strip()
    return {k: strip(v) for k, v in experts_mod.CHATML_SYSTEM_PROMPTS.items()}


def planner_prompt(categories: list[str]) -> str:
    lines = []
    for cat in sorted(categories, key=CATEGORY_ORDER.index):
        if cat == "dynamic":  # covered by the "dynamic" sentence below and the runtime DYNAMIC EXPERT section
            continue
        if cat not in PLANNER_DESCRIPTIONS:
            raise SystemExit(f"no planner description for category {cat!r}; extend PLANNER_DESCRIPTIONS")
        lines.append(f"- {cat}: {PLANNER_DESCRIPTIONS[cat]}")
    return PLANNER_HEADER + "\n" + "\n".join(lines) + "\n" + EMPTY_PLAN_GUARD


def reduce_to_expert_set(experts: dict, name: str) -> dict:
    """Return the eight-expert dict for a template (unchanged if it already has exactly the set)."""
    if set(experts) == set(EXPERT_SET):
        return experts
    out = {}
    for new_cat, old_cat in EXPERT_SET.items():
        source = new_cat if new_cat in experts else old_cat
        if source not in experts:
            raise SystemExit(f"{name}: neither {new_cat!r} nor {old_cat!r} present, cannot build the eight-expert set")
        out[new_cat] = experts[source]
    return out


def h(text: str) -> str:
    return hashlib.sha1((text or "").strip().encode()).hexdigest()[:8]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    stamp = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    ap.add_argument("--backup", default=str(ROOT / f"benchmarks/results/runbook/template_prompts_backup_{stamp}.json"))
    args = ap.parse_args()
    roles = canonical_prompts()
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
        new["experts"] = reduce_to_expert_set(new["experts"], row["name"])
        cats = list(new["experts"].keys())
        if len(cats) != 8:
            raise SystemExit(f"{row['name']}: {len(cats)} experts, every benchmark template must have 8")
        unknown = [c for c in cats if c not in CATEGORY_ROLE]
        if unknown:
            raise SystemExit(f"{row['name']}: category without role mapping: {unknown}")
        for cat, ec in new["experts"].items():
            ec["system_prompt"] = roles[CATEGORY_ROLE[cat]]
        for cat, model in MODEL_FIXES.get(row["name"], {}).items():
            for slot in new["experts"][cat]["models"]:
                if slot.get("role") != "always":
                    slot["model"] = model
        for cat, ec in new["experts"].items():
            for slot in ec["models"]:
                if slot.get("role") != "always":  # the second model of the review-wave arms keeps its own instance
                    slot["endpoint"] = EXPERT_ENDPOINTS[cat]
        for key, endpoint in (("planner_model", PLANNER_ENDPOINT), ("judge_model", JUDGE_ENDPOINT)):
            new[key] = new[key].rsplit("@", 1)[0] + "@" + endpoint
        new["judge_prompt"] = judge
        new["planner_prompt"] = planner_prompt(cats)
        changed = [k for k in ("planner_prompt", "judge_prompt") if new[k] != cfg.get(k)]
        if set(cfg["experts"]) != set(cats):
            changed.append(f"expert set {len(cfg['experts'])} -> {len(cats)}")
        changed += [f"experts.{c}" for c in cats if c in cfg["experts"] and new["experts"][c]["system_prompt"] != cfg["experts"][c].get("system_prompt")]
        changed += [f"endpoint.{c}" for c in cats if c in cfg["experts"] and
                    [s["endpoint"] for s in new["experts"][c]["models"]] != [s["endpoint"] for s in cfg["experts"][c]["models"]]]
        changed += [f"model.{c}" for c in MODEL_FIXES.get(row["name"], {}) if c in cfg["experts"] and new["experts"][c]["models"] != cfg["experts"][c]["models"]]
        print(f"{row['name'][:58]:58s} categories={len(cats):2d} changes={len(changed):2d} planner={h(new['planner_prompt'])} judge={h(judge)}")
        if changed:
            updates.append((row["id"], json.dumps(new, ensure_ascii=False)))
    if not args.apply:
        print(f"\ndry run: {len(updates)} of {len(rows)} templates would change (use --apply)")
        return
    if Path(args.backup).exists():
        raise SystemExit(f"refusing to overwrite an existing backup: {args.backup}")
    Path(args.backup).write_text(json.dumps(backup, indent=1))
    sql = ["BEGIN;"] + [
        f"UPDATE admin_expert_templates SET config_json=$j${cfg}$j$, updated_at=now()::text WHERE id='{tid}';"
        for tid, cfg in updates
    ] + ["COMMIT;"]
    psql("", stdin="\n".join(sql))
    print(f"\napplied to {len(updates)} templates; backup: {args.backup}")


if __name__ == "__main__":
    main()
