#!/bin/bash
# slurm/lumig_job19_role_sft_graphrag_olmo3.slurm
# ADDITIONAL-TEACHER pass for role=graphrag using allenai/Olmo-3.1-32B-Instruct
# (genuinely open-source, not just open-weight -- see docs/experiments/
# lumig_openrouter_teacher_verification.md). This is IN ADDITION TO the
# existing Qwen3-Next-80B-A3B-Instruct-generated data for this role
# (slurm/lumig_job15_role_sft_graphrag.slurm), not a replacement -- both
# sources get merged together later via merge_training_datasets.py.
#
# Real LUMI-G smoke test already passed (job 21849323, 24/24 grounding
# requests parsed) using the same --enforce-eager flag required here: vLLM's
# CUDA-graph-capture step crashes on Olmo3ForCausalLM on this cluster's ROCm
# build (hipErrorCapturedEvent) without it -- loading/generation itself is
# unaffected, just slightly slower per-token.
#
# 1500 examples target (vs. 1800 for the original Qwen3-Next-80B pass) --
# this is a supplementary diversity/provenance source, not the primary
# volume driver for this role.

#SBATCH --job-name=moe_role_sft_graphrag_olmo3
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=10:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/role_sft_graphrag_olmo3_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/role_sft_graphrag_olmo3_%j.err

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
OUT_DIR="${SCRATCH}/datasets/role_sft_graphrag"
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
echo "Purpose  : Role_sft LUMI-G bulk generation for role=graphrag (target 1500, OLMo-3.1-32B additional-teacher pass)"
echo "========================================"

cd "$SCRIPT_DIR"

$PYTHON scripts/generate_diverse_training_seeds.py \
    --mode role_sft --role graphrag --model "$MODEL" --tensor-parallel-size 8 \
    --count 1500 --batch-size 16 --max-tokens 4096 --enforce-eager \
    --output "$OUT_DIR/role_sft_graphrag_olmo3.jsonl"

LINES=$(wc -l < "$OUT_DIR/role_sft_graphrag_olmo3.jsonl" 2>/dev/null || echo 0)
echo "Done: $LINES examples -> $OUT_DIR/role_sft_graphrag_olmo3.jsonl"
