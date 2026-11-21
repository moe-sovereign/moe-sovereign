#!/bin/bash
#SBATCH --job-name=itv_traces
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=01:30:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-stack/logs/itv_traces_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-stack/logs/itv_traces_%j.err
# Teacher traces for the planner interview (C24). One node, TP=8. Env: MODEL (default Qwen3-Next-80B), TASKS, TAG.
set -euo pipefail
# vLLM offline loses its shared-memory segments when the node wipes /dev/shm a few minutes after the start (jobs 23914522, 23900914): retry in the same allocation
S=/scratch/project_465003058/hornphil
MODEL="${MODEL:-Qwen/Qwen3-Next-80B-A3B-Instruct}"; TASKS="${TASKS:-$S/moe-stack/interview/train.jsonl}"; TAG="${TAG:-qn80}"
OUT="$S/moe-stack/interview/traces_${TAG}.jsonl"
mkdir -p "$S/triton_cache" "$S/tmp"
export ROCR_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 PYTORCH_HIP_ALLOC_CONF=expandable_segments:True,max_split_size_mb:512
for attempt in 1 2 3; do
  ( singularity exec --bind /scratch/project_465003058:/scratch/project_465003058 \
      --env HF_HOME=$S/hf_cache --env HF_HUB_OFFLINE=1 --env TRITON_CACHE_DIR=$S/triton_cache --env TMPDIR=$S/tmp \
      --env ROCR_VISIBLE_DEVICES=$ROCR_VISIBLE_DEVICES --env HIP_VISIBLE_DEVICES=$HIP_VISIBLE_DEVICES --env PYTORCH_HIP_ALLOC_CONF=$PYTORCH_HIP_ALLOC_CONF \
      $S/lumi-multitorch-latest.sif python3 $S/moe-stack/app_pilot/benchmarks/orchestration_suite/interview/generate_traces.py \
      --tasks "$TASKS" --model "$MODEL" --out "$OUT" | tee "$S/moe-stack/interview/traces_${TAG}.summary.json" ) && break
  echo "attempt $attempt failed, retrying in 60 s"; sleep 60
done
