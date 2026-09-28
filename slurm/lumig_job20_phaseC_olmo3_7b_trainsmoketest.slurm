#!/bin/bash
# slurm/lumig_job20_phaseC_olmo3_7b_trainsmoketest.slurm
# Plan Phase 4 (Spur 2), Nutzerentscheidung 2026-09-10: OLMo-3-7B-Instruct
# wurde dreimal am `--mode grounding` Smoke-Test bewertet (0/24, 3/12, 0/24
# ueber drei Prompt-Iterationen) und dabei als Planner-Kandidat verworfen.
# Kritische Selbstpruefung ergab: dieser Test misst Zero-Shot-Befolgung
# einer ungewoehnlichen Meta-Instruktion (vLLM-Generierung eines JSON-Arrays
# aus Beispielanfragen), NICHT die tatsaechlich relevante Faehigkeit --
# echtes Lernen via LoRA-SFT auf kuratierten Rollendaten. Die beiden
# Faehigkeiten sind unabhaengig; ein schwacher Grounding-Smoke-Test
# widerlegt Fine-Tuning-Eignung nicht.
#
# Dieser Job ersetzt die Grounding-Bewertung durch denselben
# Trainings-Smoke-Test, der bei SmolLM3-3B bereits die richtige Frage
# beantwortet hat (siehe lumig_job20_phaseC_smollm3_3b_trainsmoketest.slurm):
#   1. Dump model.named_modules() gegen die hardcodierten target_modules
#      aus train_expert_slm_pipeline.py -- proves/disproves die LoRA-Config
#      VOR dem eigentlichen Trainingsschritt.
#   2. Echter Trainingsschritt (--max-steps 3) gegen den bereits bereinigten
#      `coder`-Datensatz (2295 Zeilen nach Plan-Phase 0,
#      dataset_expert_coder_merged.jsonl) -- beweist LoRA-Init + Vorwaerts-/
#      Rueckwaertspass + Loss-Berechnung Ende-zu-Ende.
#
# WICHTIG: kein --enforce-eager hier -- das war ein vLLM-CUDA-Graph-Capture-
# Workaround (Olmo-3.1-32B, Job 21848558/21849323), irrelevant fuer
# HF-transformers-Training via train_expert_slm_pipeline.py (kein vLLM im
# Trainingspfad). Die fruehere Analogie-Uebernahme dieses Flags betraf nur
# die Grounding-Smoke-Tests, nicht diesen Job.
#
# NICHT ein Produktionstraining -- --max-steps 3, kein trainingswertiger
# Checkpoint erwartet. Gleiche torchrun --standalone Aufruf-Form wie
# lumig_expert_ensemble_pipeline.slurm.
#
# Usage:
#   sbatch slurm/lumig_job20_phaseC_olmo3_7b_trainsmoketest.slurm

#SBATCH --job-name=moe_phaseC_olmo3_7b_trainsmoke
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=02:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/phaseC_olmo3_7b_trainsmoke_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/phaseC_olmo3_7b_trainsmoke_%j.err

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
TRAIN_OUT_DIR="${SCRATCH}/checkpoints/_smoketest_olmo3_7b_phaseC"
MODEL="allenai/Olmo-3-7B-Instruct"

mkdir -p "$TRAIN_OUT_DIR" "${SCRATCH}/moe-sovereign/logs"

if [ ! -f "$CONTAINER" ]; then
    echo "ERROR: Container not found: $CONTAINER"
    exit 1
fi
if [ ! -f "$DATASET_FILE" ]; then
    echo "ERROR: Dataset not found: $DATASET_FILE"
    exit 1
fi
if [ ! -f "${SCRIPT_DIR}/scripts/train_expert_slm_pipeline.py" ]; then
    echo "ERROR: Training script not found: ${SCRIPT_DIR}/scripts/train_expert_slm_pipeline.py"
    exit 1
fi

SING_ENV="PYTHONPATH=/scratch/project_465003058/hornphil/.user_site:${PYTHONPATH:-},HF_HOME=${SCRATCH}/cache/huggingface,XDG_CACHE_HOME=${SCRATCH}/cache,TRITON_CACHE_DIR=${SCRATCH}/cache/triton,TORCH_EXTENSIONS_DIR=${SCRATCH}/cache/torch_extensions,TMPDIR=${SCRATCH}/tmp"

echo "========================================"
echo "Job ID   : $SLURM_JOB_ID"
echo "Node     : $SLURMD_NODENAME"
echo "Model    : $MODEL"
echo "Dataset  : $DATASET_FILE"
echo "Purpose  : Phase C -- target_modules dump + real training-step smoke test"
echo "          (ersetzt die verworfene Grounding-Smoke-Test-Bewertung)"
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
    echo "TRAIN-SMOKE-TEST PASSED -- OLMo-3-7B-Instruct durchlief LoRA-Init + 3 Trainingsschritte."
    exit 0
else
    echo "TRAIN-SMOKE-TEST FAILED -- kein Checkpoint/Adapter erzeugt. .err-Log pruefen."
    exit 1
fi
