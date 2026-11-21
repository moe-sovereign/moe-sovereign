#!/bin/bash
#SBATCH --job-name=itv_eval
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=7
#SBATCH --mem=60G
#SBATCH --time=00:45:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-stack/logs/itv_eval_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-stack/logs/itv_eval_%j.err
# Offline evaluation of an interview planner (pre-registration G1). Env: MODEL (merged dir), TAG, TASKS (default: the 369 held-out interview tasks).
set -euo pipefail
S=/scratch/project_465003058/hornphil
: "${MODEL:?}" "${TAG:?}"
TASKS="${TASKS:-$S/moe-stack/interview/test.jsonl}"
mkdir -p "$S/tmp" "$S/cache/triton"
singularity exec --bind "$S:$S" --env "PYTHONPATH=$S/.user_site,HF_HOME=$S/cache/huggingface,HF_HUB_OFFLINE=1,TRITON_CACHE_DIR=$S/cache/triton,TMPDIR=$S/tmp" $S/lumi-multitorch-latest.sif \
  python3 $S/moe-stack/app_e2/benchmarks/orchestration_suite/interview/eval_interview.py --tasks "$TASKS" --model "$MODEL" --out "$S/moe-stack/interview/eval_$TAG.json"
