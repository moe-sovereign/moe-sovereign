#!/bin/bash
# slurm/lumig_job23_extract_qwen38_27b_texttower.slurm
# Plan Phase 1 item 2: extract the text-only CausalLM backbone from the
# multimodal Qwen/Qwen3.8-27B checkpoint, so it can be used as the
# Spur-1 (open-weight) Judge base -- analogous to
# lumig_job22_extract_qwen35_9b_texttower.slurm for the Planner.
#
# Reuses scripts/extract_qwen35_text_tower.py unmodified: the script takes
# --source/--output as arguments and contains no 9B/27B-specific logic, and
# the plan already established that all Qwen3.5/3.8 HF repos share the same
# multimodal structure (Qwen3_5ForConditionalGeneration, vision_config +
# text_config).
#
# Bigger model than the 9B extraction (27B vs 9B, ~3x bf16 size) --
# extract_state_dict() accumulates all kept tensors in RAM before writing,
# so memory and time budgets are scaled up accordingly versus job22.
#
# Usage:
#   sbatch slurm/lumig_job23_extract_qwen38_27b_texttower.slurm

#SBATCH --job-name=moe_extract_qwen38_27b_texttower
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=220G
#SBATCH --time=04:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/extract_qwen38_27b_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/extract_qwen38_27b_%j.err

set -euo pipefail

SCRATCH="/scratch/project_465003058/hornphil"
CONTAINER="${SCRATCH}/lumi-multitorch-latest.sif"
SCRIPT_DIR="${SCRATCH}/moe-sovereign"
OUTPUT_DIR="${SCRATCH}/checkpoints/moe_qwen38_27b_text/merged"
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
echo "Source   : Qwen/Qwen3.8-27B (multimodal)"
echo "Output   : $OUTPUT_DIR"
echo "========================================"

cd "$SCRIPT_DIR"
singularity exec --bind /scratch/project_465003058:/scratch/project_465003058 \
    --env "$SING_ENV" "$CONTAINER" python3 scripts/extract_qwen35_text_tower.py \
        --source Qwen/Qwen3.8-27B \
        --output "$OUTPUT_DIR"

echo "========================================"
if [ -f "$OUTPUT_DIR/config.json" ] && ls "$OUTPUT_DIR"/model*.safetensors >/dev/null 2>&1; then
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
    echo "EXTRACTION FAILED -- config.json or safetensors weights missing"
    exit 1
fi
