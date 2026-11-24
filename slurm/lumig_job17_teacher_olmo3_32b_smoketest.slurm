#!/bin/bash
# slurm/lumig_job17_teacher_olmo3_32b_smoketest.slurm
# New teacher-model candidate smoke test, triggered by a provenance/ToS
# concern raised 2026-09-09 (not part of the original zazzy-beaming-koala.md
# plan's finalized Tier A/B teacher table): Qwen/GLM/DeepSeek are all
# "open weight" only -- their own training-data provenance is not
# independently auditable, and academic work (arXiv 2501.12619,
# "Quantification of Large Language Model Distillation") found real
# self-identification/behavioral evidence that Qwen-Max-0919 shows
# GPT-4o-aligned distillation signals. allenai/Olmo-3.1-32B-Instruct is a
# genuinely OPEN SOURCE model (not just open weight): full Dolma 3
# pretraining corpus + Dolci post-training data + training code are all
# published by AI2, so its data provenance is independently checkable
# rather than trusted on faith.
#
# 3-filter LUMI-G compatibility check already passed prior to this job
# (config-only HF Hub checks, no weights downloaded):
#   1. Architecture registered: Olmo3ForCausalLM confirmed present in
#      ModelRegistry.get_supported_archs() on this cluster's own vLLM build.
#   2. Quant-kernel risk: no quantization_config in config.json -- loads
#      natively, same "unquantized sidesteps ROCm exotic-quant failures"
#      pattern already proven for Qwen3-Next-80B/GLM-4.5-Air/Qwen3.5-35B-A3B.
#   3. Size vs. ~512GB/node HBM2e ceiling: 128.93GB, confirmed directly via
#      model.safetensors.index.json's metadata.total_size (not estimated)
#      -- comfortably smaller than GLM-4.5-Air's 221GB, no RAM/Lustre-
#      prefetch walltime risk expected (that only hit models >90% of node
#      RAM, i.e. >400GB, per the Qwen3-235B/DeepSeek-Coder-V2 finding).
#
# This job proves real load + real generation, mirroring the exact same
# --mode grounding smoke-test pattern already used for every other Phase 1
# teacher candidate (jobs 7/8/9/10/11) -- no new methodology, same bar.
#
# RETRY (2026-09-09, job 21848558 FAILED): weight loading succeeded fine
# (all 3 static filters above held), but vLLM's CUDA-graph-capture step
# crashed with a ROCm/HIP-specific error (hipErrorCapturedEvent,
# "operation not permitted on an event last recorded in a capturing
# stream") -- a runtime graph-capture incompatibility for Olmo3ForCausalLM
# on this cluster's ROCm build, not a loading/size problem. Added
# --enforce-eager (skips graph capture, slower per-token but otherwise
# identical output) as the standard vLLM diagnostic for this failure class.
#
# Usage:
#   sbatch slurm/lumig_job17_teacher_olmo3_32b_smoketest.slurm

#SBATCH --job-name=moe_teacher_olmo3_32b_smoketest
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=02:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/teacher_olmo3_32b_smoketest_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/teacher_olmo3_32b_smoketest_%j.err

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
OUT_DIR="${SCRATCH}/datasets/teacher_smoketests"
MODEL="allenai/Olmo-3.1-32B-Instruct"

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
echo "Model    : $MODEL (Olmo3ForCausalLM, unquantized, 128.93GB verified)"
echo "Purpose  : genuinely open-source (not just open-weight) teacher candidate --"
echo "           auditable Dolma 3 / Dolci training-data provenance"
echo "========================================"

cd "$SCRIPT_DIR"

echo "[$(date +%H:%M:%S)] Smoke test: --mode grounding (count-per-category=3) ..."
$PYTHON scripts/generate_diverse_training_seeds.py \
    --mode grounding --model "$MODEL" --tensor-parallel-size 8 \
    --count-per-category 3 --batch-size 3 --enforce-eager \
    --output "$OUT_DIR/olmo3_32b_grounding_smoketest.jsonl"
GROUNDING_LINES=$(wc -l < "$OUT_DIR/olmo3_32b_grounding_smoketest.jsonl" 2>/dev/null || echo 0)
echo "[$(date +%H:%M:%S)] grounding smoke test: $GROUNDING_LINES parsed request(s)"

echo "========================================"
if [ "$GROUNDING_LINES" -gt 0 ]; then
    echo "SMOKE TEST PASSED -- Olmo-3.1-32B-Instruct confirmed as a viable teacher candidate."
    echo "Sample output:"
    head -c 2000 "$OUT_DIR/olmo3_32b_grounding_smoketest.jsonl"
    exit 0
else
    echo "SMOKE TEST FAILED -- 0 parsed results. Do NOT use this model as a teacher yet. Inspect $OUT_DIR and this job's .err log."
    exit 1
fi
