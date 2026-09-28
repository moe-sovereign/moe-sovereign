#!/bin/bash
# slurm/lumig_job21_phaseC_qwen4b_distilled_trainsmoketest.slurm
# Plan Phase 2 Vorbedingung: der vorhandene moe_qwen35_4b_distilled/merged
# Checkpoint (architectures=Qwen3_5ForCausalLM, model_type=qwen3_5_text --
# der bereits extrahierte Text-Tower des multimodalen Qwen3.5-4B) wurde
# bislang NIE mit der gleichen Sorgfalt wie SmolLM3-3B/OLMo-3-7B geprueft
# (target_modules-Dump + echter Trainingsschritt). Diese Architektur ist
# neu genug (Qwen3.5-Generation), dass die Standard-target_modules-Namen
# (q_proj/k_proj/v_proj/o_proj/gate_proj/up_proj/down_proj) NICHT per
# Analogie zu aelteren Qwen-Generationen angenommen werden -- exakt das
# Prinzip, das diese Kampagne nach dem Instella-MoE-MLA-Ueberraschungsfund
# und der OLMo-2/OLMo-3-Verwechslung etabliert hat.
#
# Zwei Checks, identisches Muster zu lumig_job20_phaseC_*:
#   1. named_modules() Dump vs. hardcodierte target_modules.
#   2. Echter Trainingsschritt (--max-steps 3) gegen den bereinigten
#      `coder`-Datensatz (2295 Zeilen, Plan-Phase 0).
#
# Erfolg hier ist die letzte offene Voraussetzung fuer den echten
# Spur-1-Piloten (Plan Phase 2, volle Produktion via
# lumig_expert_ensemble_pipeline.slurm).
#
# Usage:
#   sbatch slurm/lumig_job21_phaseC_qwen4b_distilled_trainsmoketest.slurm

#SBATCH --job-name=moe_phaseC_qwen4b_trainsmoke
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=02:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/phaseC_qwen4b_trainsmoke_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/phaseC_qwen4b_trainsmoke_%j.err

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
DATASET_FILE="${SCRATCH}/datasets/dataset_expert_coder_merged.jsonl"
TRAIN_OUT_DIR="${SCRATCH}/checkpoints/_smoketest_qwen4b_phaseC"
MODEL="${SCRATCH}/checkpoints/moe_qwen35_4b_distilled/merged"

mkdir -p "$TRAIN_OUT_DIR" "${SCRATCH}/moe-sovereign/logs"

if [ ! -f "$CONTAINER" ]; then echo "ERROR: Container not found: $CONTAINER"; exit 1; fi
if [ ! -f "$DATASET_FILE" ]; then echo "ERROR: Dataset not found: $DATASET_FILE"; exit 1; fi
if [ ! -d "$MODEL" ]; then echo "ERROR: Model dir not found: $MODEL"; exit 1; fi
if [ ! -f "${SCRIPT_DIR}/scripts/train_expert_slm_pipeline.py" ]; then
    echo "ERROR: Training script not found: ${SCRIPT_DIR}/scripts/train_expert_slm_pipeline.py"
    exit 1
fi

SING_ENV="PYTHONPATH=/scratch/project_465003058/hornphil/.user_site:${PYTHONPATH:-},HF_HOME=${SCRATCH}/cache/huggingface,XDG_CACHE_HOME=${SCRATCH}/cache,TRITON_CACHE_DIR=${SCRATCH}/cache/triton,TORCH_EXTENSIONS_DIR=${SCRATCH}/cache/torch_extensions,TMPDIR=${SCRATCH}/tmp"

echo "========================================"
echo "Job ID   : $SLURM_JOB_ID"
echo "Node     : $SLURMD_NODENAME"
echo "Model    : $MODEL (Qwen3_5ForCausalLM, bereits extrahierter Text-Tower)"
echo "Dataset  : $DATASET_FILE"
echo "Purpose  : Phase C -- letzte Vorbedingung fuer Spur-1-Coder-Pilot"
echo "========================================"

cd "$SCRIPT_DIR"

echo "[$(date +%H:%M:%S)] Check 1/2: named_modules() dump vs. hardcoded target_modules ..."
singularity exec --bind /scratch/project_465003058:/scratch/project_465003058 \
    --env "$SING_ENV" "$CONTAINER" python3 -c "
from transformers import AutoModelForCausalLM
import torch
m = AutoModelForCausalLM.from_pretrained('$MODEL', torch_dtype=torch.bfloat16, trust_remote_code=True)
names = [n.split('.')[-1] for n, _ in m.named_modules()]
targets = ['q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj']
print('Eindeutige Modul-Suffixe (Auszug):', sorted(set(names))[:40])
for t in targets:
    print(f'{t}: {\"GEFUNDEN\" if t in names else \"FEHLT\"}')
"

echo "[$(date +%H:%M:%S)] Check 2/2: echter Trainingsschritt (--max-steps 3) ..."
singularity exec --bind /scratch/project_465003058:/scratch/project_465003058 \
    --env "$SING_ENV" "$CONTAINER" \
    torchrun --standalone --nproc_per_node=8 \
        "${SCRIPT_DIR}/scripts/train_expert_slm_pipeline.py" \
        --role coder \
        --base-model "$MODEL" \
        --dataset "$DATASET_FILE" \
        --output-dir "$TRAIN_OUT_DIR" \
        --max-steps 3 \
        --batch-size 2 \
        --grad-accum 2 \
        --max-seq-len 4096

echo "========================================"
if ls "$TRAIN_OUT_DIR"/checkpoint-* >/dev/null 2>&1 || [ -f "$TRAIN_OUT_DIR/final_adapter/adapter_config.json" ]; then
    echo "TRAIN-SMOKE-TEST PASSED -- moe_qwen35_4b_distilled durchlief LoRA-Init + 3 Trainingsschritte."
    exit 0
else
    echo "TRAIN-SMOKE-TEST FAILED -- kein Checkpoint/Adapter erzeugt. .err-Log pruefen."
    exit 1
fi
