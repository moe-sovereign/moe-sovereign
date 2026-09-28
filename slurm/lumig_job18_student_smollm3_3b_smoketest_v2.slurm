#!/bin/bash
# slurm/lumig_job18_student_smollm3_3b_smoketest_v2.slurm
# RETEST of job 21853583 (smollm3_3b_grounding_smoketest.jsonl), not a new
# candidate. Real-content inspection of that run found only 12/24 usable:
# `technical_support` empty (0/3), `research` was pure numbered-list filler
# ("request 1"/"request 2"/"request 3"). Root-caused to two independent
# gaps, both fixed in scripts/generate_diverse_training_seeds.py before
# this retest:
#   1. `_GROUNDING_GENERATION_TEMPLATE` had no concrete per-category example
#      and no explicit anti-placeholder instruction -- added
#      `_GROUNDING_CATEGORY_EXAMPLES` + an inline example + an explicit
#      "do NOT write filler placeholders" instruction per category.
#   2. `parse_grounding_output` only checked for a non-empty string, so
#      literal filler like "request 1" passed silently -- added
#      `_is_numbered_placeholder()` to reject it mechanically, independent
#      of whether the improved prompt alone is enough.
# Same model, same --count-per-category 3, same everything else as job
# 21853583 -- output goes to a new _v2 file so the original (already
# analyzed) output is preserved for direct before/after comparison.
#
# Usage:
#   sbatch slurm/lumig_job18_student_smollm3_3b_smoketest_v2.slurm

#SBATCH --job-name=moe_student_smollm3_3b_smoketest_v2
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=02:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/student_smollm3_3b_smoketest_v2_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/student_smollm3_3b_smoketest_v2_%j.err

set -euo pipefail

checkpoint_and_exit() {
    echo "RECEIVED SIGTERM/SIGUSR1/SIGINT -- exiting smoke test."
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
OUT_DIR="${SCRATCH}/datasets/student_smoketests"
MODEL="HuggingFaceTB/SmolLM3-3B"

TRITON_CACHE="${SCRATCH}/triton_cache"
TORCHINDUCTOR_CACHE="${SCRATCH}/torchinductor_cache"
XDG_CACHE="${SCRATCH}/xdg_cache"
SCRATCH_TMP="${SCRATCH}/tmp"
mkdir -p "$TRITON_CACHE" "$TORCHINDUCTOR_CACHE" "$XDG_CACHE" "$SCRATCH_TMP"

export ROCR_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export PYTORCH_HIP_ALLOC_CONF=expandable_segments:True,max_split_size_mb:512

mkdir -p "$OUT_DIR" "${SCRATCH}/moe-sovereign/logs"

if [ ! -f "$CONTAINER" ]; then
    echo "ERROR: Container not found: $CONTAINER"
    exit 1
fi

SING_OPTS="--bind /scratch/project_465003058:/scratch/project_465003058 \
    --env HF_HOME=$HF_CACHE --env HUGGING_FACE_HUB_TOKEN=$HF_TOKEN \
    --env ROCR_VISIBLE_DEVICES=$ROCR_VISIBLE_DEVICES --env HIP_VISIBLE_DEVICES=$HIP_VISIBLE_DEVICES \
    --env PYTORCH_HIP_ALLOC_CONF=$PYTORCH_HIP_ALLOC_CONF \
    --env TRITON_CACHE_DIR=$TRITON_CACHE --env TORCHINDUCTOR_CACHE_DIR=$TORCHINDUCTOR_CACHE \
    --env XDG_CACHE_HOME=$XDG_CACHE --env TMPDIR=$SCRATCH_TMP"
PYTHON="singularity exec $SING_OPTS $CONTAINER python3"

echo "========================================"
echo "Job ID   : $SLURM_JOB_ID"
echo "Node     : $SLURMD_NODENAME"
echo "Model    : $MODEL (SmolLM3ForCausalLM, unquantized, 6.15GB verified)"
echo "Purpose  : RETEST with improved grounding prompt + placeholder filter"
echo "========================================"

cd "$SCRIPT_DIR"

echo "[$(date +%H:%M:%S)] Smoke test v2: --mode grounding (count-per-category=3) ..."
$PYTHON scripts/generate_diverse_training_seeds.py \
    --mode grounding --model "$MODEL" --tensor-parallel-size 1 \
    --count-per-category 3 --batch-size 3 \
    --output "$OUT_DIR/smollm3_3b_grounding_smoketest_v2.jsonl"
GROUNDING_LINES=$(wc -l < "$OUT_DIR/smollm3_3b_grounding_smoketest_v2.jsonl" 2>/dev/null || echo 0)
echo "[$(date +%H:%M:%S)] grounding smoke test v2: $GROUNDING_LINES parsed request(s)"

echo "========================================"
if [ "$GROUNDING_LINES" -gt 0 ]; then
    echo "SMOKE TEST v2 EXIT 0 -- $GROUNDING_LINES lines parsed. Real-content inspection still required before drawing conclusions."
    echo "Sample output:"
    head -c 2000 "$OUT_DIR/smollm3_3b_grounding_smoketest_v2.jsonl"
    exit 0
else
    echo "SMOKE TEST v2 FAILED -- 0 parsed results. Inspect $OUT_DIR and this job's .err log."
    exit 1
fi
