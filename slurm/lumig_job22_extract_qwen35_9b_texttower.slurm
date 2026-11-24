#!/bin/bash
# slurm/lumig_job22_extract_qwen35_9b_texttower.slurm
# Plan Phase 1: extract the text-only CausalLM backbone from the
# multimodal Qwen/Qwen3.5-9B checkpoint, for the Planner role (Nutzer-
# entscheidung 2026-09-10: nur Planner auf 9B, Experten bleiben auf 4B).
#
# Uses scripts/extract_qwen35_text_tower.py, whose transformation was
# verified field-by-field (config) against the already-working
# moe_qwen35_4b_distilled/merged reference checkpoint (same procedure,
# confirmed loadable + correct target_modules + real training step in
# job 21915492) before this job was written -- not a fresh guess.
#
# CPU/RAM-only (download + safetensors filtering), no GPU compute needed,
# but the 9B download (~18GB bf16) benefits from LUMI-G's storage/bandwidth
# and needs to land in /scratch anyway.
#
# Usage:
#   sbatch slurm/lumig_job22_extract_qwen35_9b_texttower.slurm

#SBATCH --job-name=moe_extract_qwen35_9b_texttower
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/extract_qwen35_9b_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/extract_qwen35_9b_%j.err

set -euo pipefail

SCRATCH="/scratch/project_465003058/hornphil"
CONTAINER="${SCRATCH}/lumi-multitorch-latest.sif"
SCRIPT_DIR="${SCRATCH}/moe-sovereign"
OUTPUT_DIR="${SCRATCH}/checkpoints/moe_qwen35_9b_text/merged"
HF_TOKEN="$(cat ~/.cache/huggingface/token 2>/dev/null || echo '')"

mkdir -p "$OUTPUT_DIR" "${SCRATCH}/moe-sovereign/logs"

if [ ! -f "$CONTAINER" ]; then echo "ERROR: Container not found: $CONTAINER"; exit 1; fi
if [ ! -f "${SCRIPT_DIR}/scripts/extract_qwen35_text_tower.py" ]; then
    echo "ERROR: Extraction script not found: ${SCRIPT_DIR}/scripts/extract_qwen35_text_tower.py"
    exit 1
fi

SING_ENV="PYTHONPATH=/scratch/project_465003058/hornphil/.user_site:${PYTHONPATH:-},HF_HOME=${SCRATCH}/cache/huggingface,HUGGING_FACE_HUB_TOKEN=${HF_TOKEN},XDG_CACHE_HOME=${SCRATCH}/cache,TMPDIR=${SCRATCH}/tmp"

echo "========================================"
echo "Job ID   : $SLURM_JOB_ID"
echo "Source   : Qwen/Qwen3.5-9B (multimodal)"
echo "Output   : $OUTPUT_DIR"
echo "========================================"

cd "$SCRIPT_DIR"
singularity exec --bind /scratch/project_465003058:/scratch/project_465003058 \
    --env "$SING_ENV" "$CONTAINER" python3 scripts/extract_qwen35_text_tower.py \
        --source Qwen/Qwen3.5-9B \
        --output "$OUTPUT_DIR"

echo "========================================"
if [ -f "$OUTPUT_DIR/config.json" ] && [ -f "$OUTPUT_DIR/model.safetensors" ]; then
    echo "EXTRACTION PASSED -- $OUTPUT_DIR"
    python3 -c "
import json
c = json.load(open('$OUTPUT_DIR/config.json'))
print('architectures:', c.get('architectures'))
print('model_type:', c.get('model_type'))
print('max_position_embeddings:', c.get('max_position_embeddings'))
"
    exit 0
else
    echo "EXTRACTION FAILED -- config.json or model.safetensors missing"
    exit 1
fi
