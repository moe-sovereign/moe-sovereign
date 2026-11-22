#!/usr/bin/env python3
"""Which strand of the end-game plan can be evaluated? One ssh call to sacct, jobs grouped by strand.

  results_status.py            # prints a table; exit code 2 when LUMI cannot be reached (certificate expired)

A strand is `ready` when every job ended COMPLETED with exit code 0, `failed` when one ended otherwise (fix, resubmit, do not evaluate),
`waiting` otherwise. The job ids are the ones in docs/system/lumi_activity_log.md; add a line to STRANDS when a job is resubmitted.
"""
import subprocess
import sys

STRANDS = {
    "A1 verifier scoring (done)": [23914482, 23914483],
    "A4 verifier gate + escalation in the loop (evaluated)": [23928260],
    "B synthesis ablation (evaluated)": [23924030, 23924031, 23926559, 23914438],
    "C fine-tuned vs base experts (evaluated)": [23924033, 23924034],
    "D generative verifier: rationales, SFT (done) + test-set scoring": [23924043, 23928243, 23944192],
    "E1 SQL tool: inline runs (evaluated)": [23924037, 23924038, 23924039],
    "E2 SQL tool: GRPO runs + evals + 32B control (evaluated)": [23924058, 23924059, 23926560, 23928221],
    "E0 SQL tool: GRPO pilot (evaluated)": [23914683],
    "F1 prospective router test set (evaluated)": [23924035, 23914503],
    "F2 memory write pre-check (evaluated)": [23924041, 23924042],
    "G1 interview traces (done)": [23900915],
    "G1 interview SFT + offline eval (evaluated)": [23921943, 23926561],
    "P0 knowledge-broker gate (evaluated)": [23919612, 23924026, 23924027, 23900911, 23900913, 23924028],
    "stack smoke (new paths) (evaluated)": [23919615],
    "A5 smoke (fallback + verifier gate, 30 tasks)": [23944495],
    "O1 smoke + judge x16": [23944743, 23944744],
    "O2 smoke (rule routing)": [23944800],
    "H unseen families (evaluated)": [23921903, 23924036, 23928226, 23928227],
    "I verifier v2 seed 2 (evaluated)": [23921905, 23928229],
    "F1 scoring of the new held-out set (evaluated)": [23926557, 23926558],
    "J external release rule (scoring)": [23921906],
}


def main() -> int:
    ids = sorted({j for v in STRANDS.values() for j in v})
    cmd = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=30", "lumi-g",
           f"sacct -X -n -P -j {','.join(map(str, ids))} -o JobID,State,ExitCode"]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=150).stdout
    except subprocess.TimeoutExpired:
        print("LUMI: timeout")
        return 2
    state = {}
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) == 3 and parts[0].isdigit():
            state[int(parts[0])] = (parts[1].split()[0], parts[2])
    if not state:
        print("LUMI: no answer (certificate expired?)")
        return 2
    for name, jobs in STRANDS.items():
        st = [state.get(j, ("PENDING", "")) for j in jobs]
        if all(s == "COMPLETED" and e == "0:0" for s, e in st):
            verdict = "READY"
        elif any(s in ("FAILED", "TIMEOUT", "CANCELLED", "OUT_OF_MEMORY", "NODE_FAIL") or (s == "COMPLETED" and e != "0:0") for s, e in st):
            verdict = "FAILED"
        else:
            verdict = "waiting"
        print(f"{verdict:8} {name}: " + ", ".join(f"{j}={s}" for j, (s, _) in zip(jobs, st)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
