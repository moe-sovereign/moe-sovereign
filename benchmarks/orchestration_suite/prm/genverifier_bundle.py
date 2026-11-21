#!/usr/bin/env python3
"""Turn teacher rationales (gen_rationales.py) into an SFT bundle for scripts/lumi/train_expert_pilot.py: sft.jsonl, heldout.jsonl, SHA256SUMS.

A row is {"messages": [{"role": "user", "content": <verifier prompt>}, {"role": "assistant", "content": <rationale ending in VERDICT: ...>}]}.
The held-out part is a group split by task (the id before `#`), so no task appears on both sides.

  genverifier_bundle.py --rationales rationales.jsonl --out-dir bundle [--heldout-groups 300 --seed 3]
"""
import argparse
import hashlib
import json
import random
from pathlib import Path


def split(rows: list, heldout_groups: int, seed: int) -> tuple:
    groups = sorted({r["id"].split("#")[0] for r in rows})
    random.Random(seed).shuffle(groups)
    held = set(groups[:heldout_groups])
    return [r for r in rows if r["id"].split("#")[0] not in held], [r for r in rows if r["id"].split("#")[0] in held]


def message_row(r: dict) -> dict:
    return {"messages": [{"role": "user", "content": r["prompt"]}, {"role": "assistant", "content": r["completion"]}]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rationales", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--heldout-groups", type=int, default=300)
    ap.add_argument("--seed", type=int, default=3)
    a = ap.parse_args()
    rows = [json.loads(l) for l in open(a.rationales, encoding="utf-8")]
    train, held = split(rows, a.heldout_groups, a.seed)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sums = []
    for name, part in (("sft.jsonl", train), ("heldout.jsonl", held)):
        with open(out / name, "w", encoding="utf-8") as f:
            for r in part:
                f.write(json.dumps(message_row(r), ensure_ascii=False) + "\n")
        sums.append(f"{hashlib.sha256((out / name).read_bytes()).hexdigest()}  {name}")
    (out / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    print(json.dumps({"train": len(train), "heldout": len(held)}))


if __name__ == "__main__":
    main()
