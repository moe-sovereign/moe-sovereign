#!/usr/bin/env python3
"""Teacher traces (generate_traces.py) -> SFT bundle for scripts/lumi/train_expert_pilot.py: sft.jsonl, heldout.jsonl, SHA256SUMS.

Rows are chat messages (user = interview prompt, assistant = the accepted completion). The split is by group: both languages of one public fact share a
group (the id without its language suffix), so no fact is on both sides. Test tasks (interview_data/test.jsonl) never enter the bundle.

  make_bundle.py --traces traces_qn80.jsonl --out-dir bundle [--heldout-groups 60 --seed 3]
"""
import argparse
import hashlib
import json
import random
from pathlib import Path


def group_of(task_id: str) -> str:
    return task_id.rsplit("-", 1)[0]


def split(rows: list, heldout_groups: int, seed: int) -> tuple:
    groups = sorted({group_of(r["id"]) for r in rows})
    random.Random(seed).shuffle(groups)
    held = set(groups[:heldout_groups])
    return [r for r in rows if group_of(r["id"]) not in held], [r for r in rows if group_of(r["id"]) in held]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--traces", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--heldout-groups", type=int, default=60)
    ap.add_argument("--seed", type=int, default=3)
    a = ap.parse_args()
    rows = [json.loads(l) for l in open(a.traces, encoding="utf-8")]
    train, held = split(rows, a.heldout_groups, a.seed)
    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sums = []
    for name, part in (("sft.jsonl", train), ("heldout.jsonl", held)):
        with open(out / name, "w", encoding="utf-8") as f:
            for r in part:
                f.write(json.dumps({"messages": [{"role": "user", "content": r["prompt"]}, {"role": "assistant", "content": r["completion"]}]}, ensure_ascii=False) + "\n")
        sums.append(f"{hashlib.sha256((out / name).read_bytes()).hexdigest()}  {name}")
    (out / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    print(json.dumps({"train": len(train), "heldout": len(held)}))


if __name__ == "__main__":
    main()
