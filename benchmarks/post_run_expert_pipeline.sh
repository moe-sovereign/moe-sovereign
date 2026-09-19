#!/usr/bin/env bash
# post_run_expert_pipeline.sh — waits for run_spur1_and_spur2.sh to finish, then evaluates the experts:
#   1. score_expert_answers.py : the expert answers given inside the templates during the run (both track judges)
#   2. replay_expert_prompts.py: identical recorded prompts to the Open Source and Open Weight experts (both track judges)
# Usage: bash benchmarks/post_run_expert_pipeline.sh <runner-log>   (the runner log is only used to find this run's sidecars)
set -u
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."
RUNNER_LOG="${1:?path of the runner log (benchmarks/results/runner_<ts>.log)}"
OUT="benchmarks/results/post_run_$(date -u +%Y%m%dT%H%M%SZ).log"
exec >>"$OUT" 2>&1
echo "$(date -u +%FT%TZ) waiting for run_spur1_and_spur2.sh to finish"
while pgrep -f "run_spur1_and_spur2.sh" >/dev/null; do sleep 120; done
echo "$(date -u +%FT%TZ) benchmark finished"; grep -E "finished with exit code" "$RUNNER_LOG"
mapfile -t SIDECARS < <(find benchmarks/results -name 'sidecar_scientific_benchmark_*.jsonl' -newer "$RUNNER_LOG" | sort)
if [ "${#SIDECARS[@]}" -ne 2 ]; then
  echo "ABORT: expected 2 sidecars newer than the runner log (Spur 1, Spur 2), found ${#SIDECARS[@]}: ${SIDECARS[*]:-}"; exit 1
fi
echo "Spur 1 sidecar: ${SIDECARS[0]}"; echo "Spur 2 sidecar: ${SIDECARS[1]}"
python3 benchmarks/score_expert_answers.py --sidecar "spur1=${SIDECARS[0]}" --sidecar "spur2=${SIDECARS[1]}" --concurrency 2
echo "$(date -u +%FT%TZ) expert answers scored (rc=$?)"
python3 benchmarks/replay_expert_prompts.py --archive spur1=benchmarks/results/expert_outputs_spur1.jsonl \
        --archive spur2=benchmarks/results/expert_outputs_spur2.jsonl --per-category 8 --concurrency 2
echo "$(date -u +%FT%TZ) replay finished (rc=$?)"
