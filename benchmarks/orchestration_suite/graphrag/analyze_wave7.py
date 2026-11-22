#!/usr/bin/env python3
"""Evaluation of strands B (synthesis ablation) and C (fine-tuned vs base experts) of docs/system/lumi_preregistration_2026-11.md.

Gate-free draft accuracy, paired by task, bootstrap over tasks (compare_runs). Run from the directory that holds wave6/, wave7/ and the task files:
  analyze_wave7.py --root /opt/tmp/lumi-rollouts --strand C|B
Strand C: base-expert workflow against the fine-tuned-expert workflow (held-out: wave-6 runs 23857468-70; external: ext/wf_on); H: |difference| < 0.03.
Strand B: each MERGER_MODE against `default` (the new default run with the token log) and against the wave-6 workflow; the direct judge for reference.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import analyze_wave6 as a  # noqa: E402


def load(root, rel):
    return a.rows(Path(root) / rel)


def strand_c(root):
    ho = {t["id"]: t for t in a.rows(Path(root) / "orch_tasks_heldout2.jsonl")}
    ext = {t["id"]: t for t in a.rows(Path(root) / "ext_tasks.jsonl")}
    wf6 = sum((load(root, f"wave6/{j}/rollouts.jsonl") for j in (23857468, 23857469, 23857470)), [])
    base_ho = load(root, "wave7/23924033/rollouts.jsonl")
    wf_on = load(root, "ext/wf_on.jsonl")
    base_ext = load(root, "wave7/23924034/rollouts.jsonl")
    out = {"heldout": {"fine_tuned_experts_wf6": a.summarize("ft", wf6, ho), "base_experts": a.summarize("base", base_ho, ho),
                       "base_minus_ft": a.cmp(wf6, base_ho, ho), "by_family": a.by_family(wf6, base_ho, ho)},
           "external": {"fine_tuned_experts_wf_on": a.summarize("ft", wf_on, ext), "base_experts": a.summarize("base", base_ext, ext),
                        "base_minus_ft": a.cmp(wf_on, base_ext, ext), "by_family": a.by_family(wf_on, base_ext, ext)}}
    return out


def strand_b(root):
    ho = {t["id"]: t for t in a.rows(Path(root) / "orch_tasks_heldout2.jsonl")}
    ho1 = {t["id"]: t for t in a.rows(Path(root) / "orch_tasks_heldout.jsonl")}
    wf6 = sum((load(root, f"wave6/{j}/rollouts.jsonl") for j in (23857468, 23857469, 23857470)), [])
    direct = load(root, "wave6/23857467/rollouts.jsonl")
    runs = {}
    for mode, job in (("default", "23924030"), ("judge_solo", "23924031"), ("evidence", "23926559"), ("best_member", "23914438")):
        p = Path(root) / "wave7" / job / "rollouts.jsonl"
        if p.exists():
            runs[mode] = a.rows(p)
    out = {"runs": {m: a.summarize(m, r, ho) for m, r in runs.items()}, "direct_ft_judge": a.summarize("direct", direct, ho1)}
    if "default" in runs:
        out["vs_default"] = {m: a.cmp(runs["default"], r, ho) for m, r in runs.items() if m != "default"}
        out["default_vs_wf6"] = a.cmp(wf6, runs["default"], ho)
    out["vs_direct"] = {m: a.cmp(direct, r, {**ho1, **ho}) for m, r in runs.items()}
    out["by_family_vs_default"] = {m: a.by_family(runs["default"], r, ho) for m, r in runs.items() if m != "default" and "default" in runs}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True)
    ap.add_argument("--strand", choices=["B", "C"], required=True)
    x = ap.parse_args()
    print(json.dumps(strand_c(x.root) if x.strand == "C" else strand_b(x.root), indent=1))


if __name__ == "__main__":
    main()
