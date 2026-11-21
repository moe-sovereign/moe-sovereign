#!/usr/bin/env python3
"""Tasks for the SQL-tool experiment (pre-registration E): sql_calc tasks whose table lives in the database, at several table sizes.

For a table scale S the generator (generate.py build --families sql_calc --row-scale S) gives the task with the data inline (`prompts_inline`,
what the single model sees without a tool) and without it (`prompts`, schema and question only, what a model with a SQL tool sees).
Outputs under --out-dir:
  grpo_train/grpo.jsonl     {"system","user","setup","ref","tol"}   scale 1 only (the SQL does not depend on the table size, and the
                                                                      tool setting is size-invariant by construction)
  test_s<S>.jsonl           the generator's own task rows, for the direct inline runs (direct_baseline.py) and the tool evaluation
Seeds are disjoint from every earlier task file (ids are checked against --known).

  build_sql_tasks.py --out-dir sqlrl --train 4000 --test 400 --scales 1,3,10,30 --known orch_tasks_heldout.jsonl,orch_tasks_w3.jsonl
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SYSTEM = ("You write PostgreSQL. The table described in the question exists in the database; you cannot see its rows. Answer with exactly one "
          "```sql block containing a single SELECT that returns the final result as one number, rounded to two decimals as the question asks.")


def tool_row(t: dict) -> dict:
    user = t["prompts"][t["lang"]].split("\nState the final")[0].split("\nNenne das Endergebnis")[0].strip()
    return {"id": t["id"], "system": SYSTEM, "user": user, "setup": t["setup"]["sql"], "ref": float(t["final"]["ref"]), "tol": float(t["final"]["tol"])}


def generate(scale: int, n: int, seed: int, out: Path) -> list:
    subprocess.run([sys.executable, str(HERE.parent / "generate.py"), "build", "--n", str(n), "--seed", str(seed), "--families", "sql_calc",
                    "--row-scale", str(scale), "--out", str(out)], check=True, capture_output=True)
    return [json.loads(l) for l in open(out, encoding="utf-8")]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--train", type=int, default=4000)
    ap.add_argument("--test", type=int, default=400)
    ap.add_argument("--scales", default="1,3,10,30")
    ap.add_argument("--known", default="", help="comma list of task files whose ids must not appear")
    a = ap.parse_args()
    out = Path(a.out_dir)
    (out / "grpo_train").mkdir(parents=True, exist_ok=True)
    known = {json.loads(l)["id"] for f in filter(None, a.known.split(",")) for l in open(f, encoding="utf-8")}
    train = [t for t in generate(1, int(a.train * 1.2) + 50, 5101, out / "_train_raw.jsonl") if t["id"] not in known][:a.train]
    train_ids = {t["id"] for t in train}
    with open(out / "grpo_train" / "grpo.jsonl", "w", encoding="utf-8") as f:
        for t in train:
            f.write(json.dumps(tool_row(t), ensure_ascii=False) + "\n")
    stats = {"train": len(train)}
    for s in (int(x) for x in a.scales.split(",")):
        tasks = [t for t in generate(s, int(a.test * 1.3) + 50, 5200 + s, out / f"_raw_s{s}.jsonl") if t["id"] not in known and t["id"] not in train_ids][:a.test]
        with open(out / f"test_s{s}.jsonl", "w", encoding="utf-8") as f:
            for t in tasks:
                f.write(json.dumps(t, ensure_ascii=False) + "\n")
        stats[f"test_s{s}"] = len(tasks)
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
