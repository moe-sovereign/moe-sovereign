#!/usr/bin/env python3
"""Competence matrix for the rule router (pre-registration O6): the best expert per task family, chosen on the DEV half of the tasks only.

Input: the O1 answer pool (build_o1_pool.py, variant `plain`). The pool rows carry `src` (exp_<role> or judge*), `task_id`, `q` (correctness) and
`family` (or the task file gives it). Output: JSON {family: category} that RULE_ROUTER_MATRIX_FILE points to. Dev = sha256(task_id) mod 2 == 0.

  build_competence_matrix.py --pool pool_o1_plain.jsonl --out competence_matrix.json
"""
import argparse
import collections
import hashlib
import json
import statistics as st

CATEGORY = {"omni": "general", "research": "research", "security": "security", "governance": "governance", "datainfra": "data_analyst",
            "coder": "code_reviewer", "precision": "precision_tools", "graphrag": "compounding_knowledge"}


def is_dev(task_id: str) -> bool:
    return int(hashlib.sha256(task_id.encode()).hexdigest()[:8], 16) % 2 == 0


def build(rows: list) -> tuple:
    """(matrix {family: category}, table {family: {role: dev accuracy}}). Ties go to the alphabetically first role (deterministic)."""
    per = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        src = r.get("src", "")
        if src.startswith("exp_") and is_dev(r["task_id"]):
            per[r["family"]][src[4:]].append(float(r["q"]))
    table = {f: {role: round(st.mean(v), 3) for role, v in roles.items()} for f, roles in per.items()}
    matrix = {f: CATEGORY[max(sorted(t), key=lambda x: t[x])] for f, t in table.items() if all(k in CATEGORY for k in t)}
    return matrix, table


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    matrix, table = build([json.loads(l) for l in open(a.pool, encoding="utf-8")])
    json.dump(matrix, open(a.out, "w", encoding="utf-8"), indent=1)
    print(json.dumps({"matrix": matrix, "dev_accuracy": table}, indent=1))


if __name__ == "__main__":
    main()
