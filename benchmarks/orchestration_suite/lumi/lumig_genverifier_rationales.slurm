#!/bin/bash
#SBATCH --job-name=gv_rationales
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=04:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-stack/logs/gv_rationales_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-stack/logs/gv_rationales_%j.err
# Teacher rationales for the generative verifier (pre-registration D1). One node, TP=8. Env: MODEL, INPUTS, OUT (default under $S/prm/gen).
# The first rows are a smoke phase: a yield below 0.3 stops the job (pre-registered abort).
set -euo pipefail
S=/scratch/project_465003058/hornphil
MODEL="${MODEL:-Qwen/Qwen3-Next-80B-A3B-Instruct}"; INPUTS="${INPUTS:-$S/prm/gen/gen_inputs.jsonl}"; OUT="${OUT:-$S/prm/gen/rationales.jsonl}"
mkdir -p "$S/triton_cache" "$S/tmp" "$(dirname "$OUT")"
export ROCR_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7 PYTORCH_HIP_ALLOC_CONF=expandable_segments:True,max_split_size_mb:512
singularity exec --bind /scratch/project_465003058:/scratch/project_465003058 \
  --env HF_HOME=$S/hf_cache --env HF_HUB_OFFLINE=1 --env TRITON_CACHE_DIR=$S/triton_cache --env TMPDIR=$S/tmp \
  --env ROCR_VISIBLE_DEVICES=$ROCR_VISIBLE_DEVICES --env HIP_VISIBLE_DEVICES=$HIP_VISIBLE_DEVICES --env PYTORCH_HIP_ALLOC_CONF=$PYTORCH_HIP_ALLOC_CONF \
  $S/lumi-multitorch-latest.sif python3 $S/moe-stack/app_e2/benchmarks/orchestration_suite/prm/gen_rationales.py \
  --inputs "$INPUTS" --model "$MODEL" --out "$OUT" | tee "${OUT%.jsonl}.summary.json"
