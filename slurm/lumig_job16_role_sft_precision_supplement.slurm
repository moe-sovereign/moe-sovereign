#!/bin/bash
# slurm/lumig_job16_role_sft_precision_supplement.slurm
# Supplementary LUMI-G role_sft generation for role=precision, AFTER
# _GENERIC_ROLE_TOPIC_HINTS was expanded with 3 new, boundary-checked
# topics per role (2026-09-09, on user request: broaden real per-role
# topic coverage rather than just increasing volume on the same topics).
# The initial 1800-request run (job in the 2183298x range) already covered
# the ORIGINAL hint set well -- this smaller supplementary run appends
# 600 more requests cycling through the now-larger hint list (old +
# new topics) into the SAME output file, so the new topics get real
# representation without wastefully re-generating the well-covered old
# ones from scratch.

#SBATCH --job-name=moe_role_sft_precision_supp
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=04:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/role_sft_precision_supp_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/role_sft_precision_supp_%j.err

set -euo pipefail

checkpoint_and_exit() {
    echo "RECEIVED SIGTERM/SIGUSR1/SIGINT -- exiting, whatever is already flushed to disk is kept."
    pkill -SIGINT -f python3 || true
    sync
    exit 0
}
trap 'checkpoint_and_exit' SIGTERM SIGUSR1 SIGINT

SCRATCH="/scratch/project_465003058/hornphil"
CONTAINER="${SCRATCH}/lumi-multitorch-latest.sif"
SCRIPT_DIR="${SCRATCH}/moe-sovereign"
HF_CACHE="${SCRATCH}/hf_cache"
HF_TOKEN="$(cat ~/.cache/huggingface/token 2>/dev/null || echo '')"
OUT_DIR="/scratch/project_465003058/hornphil/datasets/role_sft_precision"
MODEL="Qwen/Qwen3-Next-80B-A3B-Instruct"

TRITON_CACHE="${SCRATCH}/triton_cache"
TORCHINDUCTOR_CACHE="${SCRATCH}/torchinductor_cache"
XDG_CACHE="${SCRATCH}/xdg_cache"
SCRATCH_TMP="${SCRATCH}/tmp"
mkdir -p "$TRITON_CACHE" "$TORCHINDUCTOR_CACHE" "$XDG_CACHE" "$SCRATCH_TMP"

export ROCR_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export PYTORCH_HIP_ALLOC_CONF=expandable_segments:True,max_split_size_mb:512

mkdir -p "$OUT_DIR" "${SCRATCH}/moe-sovereign/logs"

SING_OPTS="--bind /scratch/project_465003058:/scratch/project_465003058 \
    --env HF_HOME=$HF_CACHE --env HUGGING_FACE_HUB_TOKEN=$HF_TOKEN \
    --env ROCR_VISIBLE_DEVICES=$ROCR_VISIBLE_DEVICES --env HIP_VISIBLE_DEVICES=$HIP_VISIBLE_DEVICES \
    --env PYTORCH_HIP_ALLOC_CONF=$PYTORCH_HIP_ALLOC_CONF \
    --env TRITON_CACHE_DIR=$TRITON_CACHE --env TORCHINDUCTOR_CACHE_DIR=$TORCHINDUCTOR_CACHE \
    --env XDG_CACHE_HOME=$XDG_CACHE --env TMPDIR=$SCRATCH_TMP"
PYTHON="singularity exec $SING_OPTS $CONTAINER python3"

echo "========================================"
echo "Job ID   : $SLURM_JOB_ID"
echo "Model    : $MODEL"
echo "Purpose  : Supplementary role_sft generation for role=precision (expanded topics, +600)"
echo "========================================"

cd "$SCRIPT_DIR"

$PYTHON scripts/generate_diverse_training_seeds.py \
    --mode role_sft --role precision --model "$MODEL" --tensor-parallel-size 8 \
    --count 600 --batch-size 16 --max-tokens 4096 \
    --output "$OUT_DIR/role_sft_precision_lumig.jsonl"

LINES=$(wc -l < "$OUT_DIR/role_sft_precision_lumig.jsonl" 2>/dev/null || echo 0)
echo "Done: total $LINES examples now in -> $OUT_DIR/role_sft_precision_lumig.jsonl"
