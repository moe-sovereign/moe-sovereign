#!/usr/bin/env python3
"""Inputs for the generative verifier's rationales (pre-registration D): a label-balanced sample of the verifier training rows.

Synthetic rows (source `fork`) are balanced per family between correct and incorrect and capped in length; every external row (source
`pool:*`, the external pool of the verifier v2 data) is kept. The label is NOT shown to the teacher; it only filters the rationales.

  build_genverifier_data.py --train verifier_data_v2/train.jsonl --out gen_inputs.jsonl [--per-family 10000 --max-chars 6000 --seed 3]
"""
import argparse
import collections
import json
import random


def build(rows: list, per_family: int, max_chars: int, seed: int) -> list:
    rnd = random.Random(seed)
    by: dict = collections.defaultdict(lambda: {0: [], 1: []})
    external = []
    for r in rows:
        if len(r["text"]) > max_chars:
            continue
        if str(r["source"]).startswith("pool:"):
            external.append(r)
        else:
            by[r["family"]][int(r["q"] >= 0.5)].append(r)
    out = []
    for fam, d in sorted(by.items()):
        half = per_family // 2
        for label in (0, 1):
            rnd.shuffle(d[label])
            out.extend(d[label][:half])
    out.extend(external)
    rnd.shuffle(out)
    return [{"id": r["id"], "family": r["family"], "source": r["source"], "label": int(r["q"] >= 0.5), "text": r["text"]} for r in out]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--per-family", type=int, default=10000)
    ap.add_argument("--max-chars", type=int, default=6000)
    ap.add_argument("--seed", type=int, default=3)
    a = ap.parse_args()
    rows = [json.loads(l) for l in open(a.train, encoding="utf-8")]
    out = build(rows, a.per_family, a.max_chars, a.seed)
    with open(a.out, "w", encoding="utf-8") as f:
        for r in out:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    c = collections.Counter((r["family"], r["label"]) for r in out)
    print(json.dumps({"rows": len(out), "by_family_label": {f"{k[0]}:{k[1]}": v for k, v in sorted(c.items())}}))


if __name__ == "__main__":
    main()
