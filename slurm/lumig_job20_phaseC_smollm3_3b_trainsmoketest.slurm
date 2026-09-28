#!/bin/bash
# slurm/lumig_job20_phaseC_smollm3_3b_trainsmoketest.slurm
# Plan Phase C (Spur 2): empirically verify that
# scripts/train_expert_slm_pipeline.py's hardcoded target_modules =
# ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj",
# "down_proj"] actually match SmolLM3-3B's real module names, THEN prove
# a real training step runs end-to-end -- not assumed by architecture
# family analogy (this project's own established principle after the
# OLMo-2/OLMo-3 4K-context mixup and the Instella-MoE MLA surprise).
#
# Two checks in one job:
#   1. Dump model.named_modules() right after loading, grep for the exact
#      target_modules strings -- proves/disproves the LoRA config BEFORE
#      spending time on step 2.
#   2. Invoke train_expert_slm_pipeline.py for real (--max-steps 3, 1
#      epoch cap) against the just-verified, real `coder` merged dataset
#      (2296 examples, datasets/merged/dataset_expert_coder_merged.jsonl)
#      -- proves LoRA init + a real training step + loss computation
#      succeed end-to-end, not just that get_peft_model() doesn't throw.
#
# NOT a production training run -- --max-steps 3 only, no checkpoint of
# training value expected. Uses the same 8x MI250X / torchrun --standalone
# invocation as the real pipeline (lumig_expert_ensemble_pipeline.slurm)
# so a pass here is representative of the real Stage 1 invocation shape.
#
# Usage:
#   sbatch slurm/lumig_job20_phaseC_smollm3_3b_trainsmoketest.slurm

#SBATCH --job-name=moe_phaseC_smollm3_3b_trainsmoke
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=02:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/phaseC_smollm3_3b_trainsmoke_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/phaseC_smollm3_3b_trainsmoke_%j.err

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
TRAIN_OUT_DIR="${SCRATCH}/checkpoints/_smoketest_smollm3_3b_phaseC"
MODEL="HuggingFaceTB/SmolLM3-3B"

mkdir -p "$TRAIN_OUT_DIR" "${SCRATCH}/moe-sovereign/logs"

if [ ! -f "$CONTAINER" ]; then
    echo "ERROR: Container not found: $CONTAINER"
    exit 1
fi
if [ ! -f "$DATASET_FILE" ]; then
    echo "ERROR: Dataset not found: $DATASET_FILE (sync it from local datasets/merged/ first)"
    exit 1
fi

SING_ENV="PYTHONPATH=/scratch/project_465003058/hornphil/.user_site:${PYTHONPATH:-},HF_HOME=${SCRATCH}/cache/huggingface,XDG_CACHE_HOME=${SCRATCH}/cache,TRITON_CACHE_DIR=${SCRATCH}/cache/triton,TORCH_EXTENSIONS_DIR=${SCRATCH}/cache/torch_extensions,TMPDIR=${SCRATCH}/tmp"

echo "========================================"
echo "Job ID   : $SLURM_JOB_ID"
echo "Node     : $SLURMD_NODENAME"
echo "Model    : $MODEL"
echo "Dataset  : $DATASET_FILE"
echo "Purpose  : Phase C -- target_modules dump + real training-step smoke test"
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
    echo "TRAIN-SMOKE-TEST PASSED -- SmolLM3-3B durchlief LoRA-Init + 3 Trainingsschritte."
    exit 0
else
    echo "TRAIN-SMOKE-TEST FAILED -- kein Checkpoint/Adapter erzeugt. .err-Log pruefen."
    exit 1
fi
