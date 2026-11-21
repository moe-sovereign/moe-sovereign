#!/bin/bash
# After the allocation ends (2026-12-05) or before the last jobs finish: copy what must survive to a durable place and record checksums.
# Run locally, from the repository root. Needs a valid LUMI certificate until the project's scratch is cleaned.
set -euo pipefail
DEST=${1:?usage: export_artifacts.sh <local dir>}
S=/scratch/project_465003058/hornphil
mkdir -p "$DEST"/{runs,prm,logs,rlvr}
# accounting: every job of the project, for the final report
ssh lumi-g "sacct -S 2026-07-01 -X -P -o JobID,JobName,Partition,State,Elapsed,AllocTRES,ExitCode" > "$DEST/sacct_all.psv"
# small result files of the stack runs (rollouts and summaries; the vLLM logs stay behind)
ssh lumi-g "cd $S/moe-stack/runs && tar czf - */rollouts.jsonl */driver_summary.json */shim_usage.jsonl */graph_stats_*.txt 2>/dev/null" | tar xzf - -C "$DEST/runs" || true
ssh lumi-g "cd $S/prm && tar czf - runs/*/eval.json score/*.jsonl 2>/dev/null" | tar xzf - -C "$DEST/prm" || true
ssh lumi-g "cd $S/moe-stack && tar czf - rlvr/*/eval.json interview/*.json* 2>/dev/null" | tar xzf - -C "$DEST/rlvr" || true
( cd "$DEST" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS )
echo "exported $(wc -l < "$DEST/SHA256SUMS") files to $DEST; checkpoints (verifier, GRPO adapters) are copied separately: they are large."
