#!/usr/bin/env python3
"""MoE Sovereign Rule-Based Reinforcement Learning (RLVR / Rule-Based DPO) Dataset Compiler.

Scores DPO preference pairs (prompt, chosen, rejected) with deterministic verifier flags
instead of stochastic human preference reward models:

Reward Functions:
1. R_DAG (+1.0 / -1.0): Kahn DAG Cycle-Free Topological Sorting (validate_dag_kahn)
2. R_SMT (+1.0 / -1.0): Z3 SMT Solver CoT Entailment & Program Sketch Bounds (verify_cot_step_z3)
3. R_GBNF (+1.0 / -1.0): Pre-compiled GBNF Bit-Mask JSON Grammar Compliance
4. R_Entropy (+1.0 / -1.0): Shannon Entropy Egress Bounds H(X) <= 5.6 bits/char

LIMITATION: compile_dpo_preference_pairs() does not run any verifier. It emits four fixed
prompts with constant "chosen"/"rejected" texts and hard-coded flags, i.e. placeholder data.
Training DPO on it teaches the template string, not verified behaviour. The CLI therefore
refuses to write it unless --allow-placeholder is given (pipeline/format tests only).
Real preference pairs must come from executed, verifier-scored traces.
"""

import argparse
import json
import logging
import os
import sys
import time
from typing import Dict, List

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s]: %(message)s")
logger = logging.getLogger("RuleBasedRLCompiler")

REWARD_FLAGS = ("dag_valid", "smt_sat", "gbnf_valid", "entropy_safe")
PLACEHOLDER_MARKER = "[VERIFIED EXECUTION PLAN #"


def evaluate_rule_based_reward(trace: Dict) -> float:
    """Calculates deterministic composite reward R in [-4.0, +4.0].

    Reward Components:
    - R_DAG: +1.0 if cycle-free, -1.0 if cycle detected
    - R_SMT: +1.0 if Z3 SAT, -1.0 if Z3 UNSAT
    - R_GBNF: +1.0 if valid grammar, -1.0 if syntax error
    - R_Entropy: +1.0 if H(X) <= 5.6, -1.0 if entropy leak

    A flag that is missing or not exactly True counts as failed: an unverified trace
    must never earn the positive reward.
    """
    return sum(1.0 if trace.get(flag) is True else -1.0 for flag in REWARD_FLAGS)


def compile_dpo_preference_pairs(sample_count: int = 5000) -> List[Dict]:
    """Compiles placeholder (prompt, chosen, rejected) DPO triples (see module LIMITATION)."""
    dpo_pairs = []

    prompts_catalog = [
        "Review procurement specification for contradiction and missing evidence.",
        "Synthesize an execution plan for updating database schemas without locks.",
        "Audit hybrid cloud threat model and prioritize security controls.",
        "Perform mathematical step-by-step reasoning for hardware allocation."
    ]

    for i in range(sample_count):
        prompt = prompts_catalog[i % len(prompts_catalog)]

        # Valid execution trace (Chosen)
        chosen_trace = {
            "prompt": prompt,
            "response": f"{PLACEHOLDER_MARKER}{i}]\n1. Inspect inputs\n2. Invoke Z3 solver\n3. Execute verified DAG.",
            "dag_valid": True,
            "smt_sat": True,
            "gbnf_valid": True,
            "entropy_safe": True
        }

        # Flawed execution trace (Rejected - containing cycle or hallucination)
        rejected_trace = {
            "prompt": prompt,
            "response": f"Here is how you can do it: Step 1: LLM free text generation... (Cycle detected, unverified).",
            "dag_valid": False,
            "smt_sat": False,
            "gbnf_valid": False,
            "entropy_safe": False
        }

        dpo_pairs.append({
            "id": f"moe_dpo_{i:06d}",
            "prompt": prompt,
            "chosen": [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": chosen_trace["response"]}
            ],
            "rejected": [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": rejected_trace["response"]}
            ],
            "reward_chosen": evaluate_rule_based_reward(chosen_trace),
            "reward_rejected": evaluate_rule_based_reward(rejected_trace),
            "placeholder": True,
            "timestamp": time.time()
        })

    return dpo_pairs


def generate_rl_dataset(output_file: str, sample_count: int = 10000) -> int:
    """Writes compiled DPO/RLVR preference pairs to JSONL file."""
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    pairs = compile_dpo_preference_pairs(sample_count=sample_count)

    with open(output_file, "w", encoding="utf-8") as out_f:
        for pair in pairs:
            out_f.write(json.dumps(pair, ensure_ascii=False) + "\n")

    logger.info("Compiled %d Rule-Based RL / DPO preference pairs -> %s", len(pairs), output_file)
    return len(pairs)


def main(argv: List[str] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="/opt/deployment/moe-sovereign/moe-infra/data/corpora/moe_rule_based_rl_dpo.jsonl")
    ap.add_argument("--samples", type=int, default=10000)
    ap.add_argument("--allow-placeholder", action="store_true",
                    help="write the templated placeholder pairs anyway (format tests only, never training)")
    args = ap.parse_args(argv)
    if not args.allow_placeholder:
        logger.error("Refusing to write placeholder DPO pairs: they are not verifier-scored traces. "
                     "Use --allow-placeholder for format tests only.")
        return 2
    total = generate_rl_dataset(args.out, sample_count=args.samples)
    print(f"Wrote {total} PLACEHOLDER DPO pairs (not for training).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
