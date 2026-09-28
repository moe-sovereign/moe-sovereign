#!/usr/bin/env python3
"""
scripts/update_lumig_shadow_experts.py — Configure shadow security experts and multi-disciplinary planner rules.

Updates admin_expert_templates in PostgreSQL (moe_userdb):
1. Adds smollm3-expert-security-3b as forced: true shadow expert to code_reviewer.
2. Configures review_lenses: ["security"].
3. Injects the MULTI-DISCIPLINARY CO-EVALUATION rule into planner_prompt.
"""

import json
import logging
import psycopg
from psycopg.rows import dict_row

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("update_templates")

DB_URL = "postgresql://moe_admin:faf105fe5246e948a7d189b29d65296136c4f87630171a74@172.20.0.15:5432/moe_userdb"

TARGET_TEMPLATES = [
    "tmpl-11f532fc",            # LUMI-G OLMo + SmolLM3 Sovereign Ensemble
    "tmpl-smollm3-delib",       # LUMI-G OLMo + SmolLM3 Sovereign Ensemble - Deliberation
    "tmpl-smollm3-nograph",     # LUMI-G OLMo + SmolLM3 Sovereign Ensemble - No-GraphRAG
    "tmpl-0ec81b11",            # LUMI-G Ensemble
    "tmpl-f1fe952c",            # LUMI-G Ensemble - Deliberation
    "tmpl-565c9fb4",            # LUMI-G Ensemble - No-GraphRAG
    "tmpl-smollm3-review",      # LUMI-G OLMo + SmolLM3 Sovereign Ensemble - Review
    "tmpl-smollm3-review-nosc", # LUMI-G OLMo + SmolLM3 Sovereign Ensemble - Review NoSC
]

SHADOW_SECURITY_MODEL = {
    "role": "always",
    "model": "hf.co/h3rb3rn/smollm3-expert-security-3b:Q4_K_M",
    "endpoint": "N02-M60-03",
    "context_window": 48128,
    "forced": True,
    "system_prompt": (
        "You are a specialized security, concurrency, and thread-safety expert. "
        "Audit code, memory orderings, and invariants strictly for security flaws, "
        "race conditions, ABA hazards, false sharing, and edge-case failure modes."
    ),
}

MULTI_DISCIPLINARY_RULE = (
    "\n\nMULTI-DISCIPLINARY CO-EVALUATION RULE:\n"
    "For complex software, systems programming, or architecture requests:\n"
    "- NEVER assign multiple subtasks to the same category alone.\n"
    "- ALWAYS assign distinct, complementary categories that run on separate hardware specialists simultaneously:\n"
    "  1. Core implementation logic: 'code_reviewer'\n"
    "  2. Concurrency, memory-safety, and threat audit: 'security'\n"
    "  3. Edge-case test suite, failure modes, and verification: 'data_analyst'\n"
    "  4. Architectural patterns, background, and reference comparison: 'research'\n"
    "- Keep tasks independent (no 'depends_on') whenever possible so they execute strictly in parallel on level 0.\n"
    "- NOTE ON PRECISION TOOLS: 'precision_tools' is STRICTLY for deterministic tools (requires explicit 'mcp_tool' and 'mcp_args'). Never use 'precision_tools' for general math reasoning without an MCP tool."
)

def update_templates():
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT id, name, config_json FROM admin_expert_templates WHERE id = ANY(%s)",
                (TARGET_TEMPLATES,)
            )
            rows = cur.fetchall()
            logger.info("Found %d target templates to update.", len(rows))

            for row in rows:
                tmpl_id = row["id"]
                name = row["name"]
                cfg_raw = row["config_json"]
                cfg = json.loads(cfg_raw) if isinstance(cfg_raw, str) else dict(cfg_raw)

                experts = cfg.setdefault("experts", {})
                code_reviewer = experts.setdefault("code_reviewer", {})
                models = code_reviewer.setdefault("models", [])

                # 1. Update models in code_reviewer: ensure shadow security expert exists
                has_shadow = any(
                    m.get("endpoint") == "N02-M60-03" or "security" in m.get("model", "")
                    for m in models
                )
                if not has_shadow:
                    models.append(SHADOW_SECURITY_MODEL)
                    logger.info("[%s] Added shadow security expert to code_reviewer", tmpl_id)
                else:
                    # Update existing shadow entry to have forced: True and role: always
                    for m in models:
                        if m.get("endpoint") == "N02-M60-03" or "security" in m.get("model", ""):
                            m["forced"] = True
                            m["role"] = "always"
                            m["system_prompt"] = SHADOW_SECURITY_MODEL["system_prompt"]
                    logger.info("[%s] Updated existing security entry to forced: true", tmpl_id)

                # 2. Configure review_lenses
                review_lenses = code_reviewer.setdefault("review_lenses", [])
                if "security" not in review_lenses:
                    review_lenses.append("security")

                # 3. Update planner_prompt with multi-disciplinary co-evaluation rule
                planner_prompt = cfg.get("planner_prompt", "")
                if "MULTI-DISCIPLINARY CO-EVALUATION" in planner_prompt:
                    # Strip prior block before appending updated rule
                    prefix = planner_prompt.split("MULTI-DISCIPLINARY CO-EVALUATION")[0].strip()
                    cfg["planner_prompt"] = prefix + MULTI_DISCIPLINARY_RULE
                else:
                    cfg["planner_prompt"] = planner_prompt.strip() + MULTI_DISCIPLINARY_RULE
                logger.info("[%s] Updated multi-disciplinary rule in planner_prompt", tmpl_id)

                # Save back to database
                updated_json = json.dumps(cfg)
                cur.execute(
                    "UPDATE admin_expert_templates SET config_json = %s WHERE id = %s",
                    (updated_json, tmpl_id)
                )
                logger.info("Successfully updated template '%s' (%s)", name, tmpl_id)

        conn.commit()
    logger.info("All templates committed successfully.")

if __name__ == "__main__":
    update_templates()
