#!/bin/bash
# slurm/lumig_job18_student_olmo3_7b_smoketest.slurm
# STUDENT base-model candidate smoke test: genuinely open-source candidate
# for the ~8B Planner role, replacing Qwen3.5:8b.
#
# REPLACES the OLMo-2-1124-7B-Instruct candidate (job 21853584, cancelled
# before it started): verified live, 2026-09-09, that OLMo 2's ENTIRE
# generation (7B-1124, 13B-1124, and even the 0325 32B refresh) ships with
# max_position_embeddings=4096, rope_scaling=null in config.json -- a
# genuine, maintainer-confirmed 4K-token native context ceiling (OLMo 2's
# own "2 OLMo 2 Furious" paper, arXiv:2501.00656, documents this as the
# intended pretraining context), nowhere near this project's ~32768-token
# production requirement. OLMo-3-7B-Instruct instead ships with the same
# YaRN context extension already verified on OLMo-3.1-32B: 65536 native
# (rope_type=yarn, factor=8.0, original_max_position_embeddings=8192).
#
# Verified prior to this job (config-only HF Hub checks, no weights
# downloaded):
#   1. Architecture registered in this cluster's vLLM build: Olmo3ForCausalLM
#      confirmed present in ModelRegistry.get_supported_archs() (same
#      architecture already proven on OLMo-3.1-32B, job 21849323).
#   2. No quantization_config in config.json -- loads natively.
#   3. Checkpoint size: 14.6GB (verified via model.safetensors.index.json)
#      -- trivially small vs. the ~512GB/node ceiling.
#
# --enforce-eager included from the start (not tested without it first):
# OLMo-3.1-32B (same Olmo3ForCausalLM architecture) needed it to work
# around a ROCm/HIP CUDA-graph-capture crash (hipErrorCapturedEvent) on
# this cluster's build -- likely to affect this architecture generally,
# so testing without it first would just reproduce the already-diagnosed
# failure for no new information.
#
# Usage:
#   sbatch slurm/lumig_job18_student_olmo3_7b_smoketest.slurm

#SBATCH --job-name=moe_student_olmo3_7b_smoketest
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=02:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/student_olmo3_7b_smoketest_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/student_olmo3_7b_smoketest_%j.err

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
MODEL="allenai/Olmo-3-7B-Instruct"

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
echo "Model    : $MODEL (Olmo3ForCausalLM, unquantized, 14.6GB verified, 65536 native context via YaRN)"
echo "Purpose  : genuinely open-source STUDENT base-model candidate"
echo "========================================"

cd "$SCRIPT_DIR"

echo "[$(date +%H:%M:%S)] Smoke test: --mode grounding (count-per-category=3) ..."
$PYTHON scripts/generate_diverse_training_seeds.py \
    --mode grounding --model "$MODEL" --tensor-parallel-size 1 --enforce-eager \
    --count-per-category 3 --batch-size 3 \
    --output "$OUT_DIR/olmo3_7b_grounding_smoketest.jsonl"
GROUNDING_LINES=$(wc -l < "$OUT_DIR/olmo3_7b_grounding_smoketest.jsonl" 2>/dev/null || echo 0)
echo "[$(date +%H:%M:%S)] grounding smoke test: $GROUNDING_LINES parsed request(s)"

echo "========================================"
if [ "$GROUNDING_LINES" -gt 0 ]; then
    echo "SMOKE TEST PASSED -- allenai/Olmo-3-7B-Instruct confirmed loadable + generating on LUMI-G."
    echo "Sample output:"
    head -c 2000 "$OUT_DIR/olmo3_7b_grounding_smoketest.jsonl"
    exit 0
else
    echo "SMOKE TEST FAILED -- 0 parsed results. Do NOT use this model as a student base model yet. Inspect $OUT_DIR and this job's .err log."
    exit 1
fi
