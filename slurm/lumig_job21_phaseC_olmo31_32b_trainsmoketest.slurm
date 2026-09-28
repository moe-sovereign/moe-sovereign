#!/bin/bash
# slurm/lumig_job21_phaseC_olmo31_32b_trainsmoketest.slurm
# Plan Phase 4 Vorbedingung: OLMo-3.1-32B-Instruct ist derselben
# Architekturklasse (Olmo3ForCausalLM) wie das bereits verifizierte
# OLMo-3-7B-Instruct (Job 21907344, target_modules + echter Trainingsschritt
# bestanden). Gleiche Modulnamen sind hier eine deutlich sicherere Analogie
# als fruehere Cross-Generation-Annahmen (OLMo-2 vs. OLMo-3, Instella-MoE) --
# dieselbe Modell-Klasse, nur skaliert -- aber diese Kampagne verifiziert
# grundsaetzlich statt anzunehmen, deshalb dieser Job vor dem eigentlichen
# Judge-Piloten (Plan Phase 4.3, ~24-30h geschaetzte Laufzeit).
#
# Batch/Accum wie in lumig_expert_ensemble_pipeline.slurm fuer die
# Judge-Rolle (batch_size=1, grad_accum=16 -- kleineres Batch wegen
# Modellgroesse, vermeidet OOM bei 32B in BF16 + LoRA ueber 8x MI250X).
#
# RETRY (2026-09-11) nach echtem OOM in Job 21915493: "HIP out of memory.
# Tried to allocate 568.00 MiB. GPU 4 has a total capacity of 63.98 GiB
# of which 464.00 MiB is free. Of the allocated memory 61.76 GiB is
# allocated by PyTorch" -- das Modell allein (32B in bf16 = ~64GB) saettigt
# bereits fast den kompletten 64GB-Speicher pro MI250X-GCD, ZeRO-2 shardet
# nur Optimizer-Zustaende/Gradienten, NICHT die Modellgewichte selbst
# (jeder Rank haelt eine volle bf16-Kopie). Fix: --deepspeed auf
# configs/ds_zero3_bf16.json umgestellt (ZeRO-3 shardet auch die
# Modellparameter ueber alle 8 Ranks, ~8GB/Rank statt 64GB/Rank fuer die
# Gewichte). target_modules-Check (Schritt 1) war bereits vor dem OOM
# erfolgreich, hier nicht wiederholungsbeduerftig.
#
# Usage:
#   sbatch slurm/lumig_job21_phaseC_olmo31_32b_trainsmoketest.slurm

#SBATCH --job-name=moe_phaseC_olmo31_32b_trainsmoke
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=02:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/phaseC_olmo31_32b_trainsmoke_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/phaseC_olmo31_32b_trainsmoke_%j.err

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
DATASET_FILE="${SCRATCH}/datasets/dataset_expert_judge_merged.jsonl"
TRAIN_OUT_DIR="${SCRATCH}/checkpoints/_smoketest_olmo31_32b_phaseC"
MODEL="allenai/Olmo-3.1-32B-Instruct"

mkdir -p "$TRAIN_OUT_DIR" "${SCRATCH}/moe-sovereign/logs"

if [ ! -f "$CONTAINER" ]; then echo "ERROR: Container not found: $CONTAINER"; exit 1; fi
if [ ! -f "$DATASET_FILE" ]; then echo "ERROR: Dataset not found: $DATASET_FILE"; exit 1; fi
if [ ! -f "${SCRIPT_DIR}/scripts/train_expert_slm_pipeline.py" ]; then
    echo "ERROR: Training script not found: ${SCRIPT_DIR}/scripts/train_expert_slm_pipeline.py"
    exit 1
fi

SING_ENV="PYTHONPATH=/scratch/project_465003058/hornphil/.user_site:${PYTHONPATH:-},HF_HOME=${SCRATCH}/cache/huggingface,XDG_CACHE_HOME=${SCRATCH}/cache,TRITON_CACHE_DIR=${SCRATCH}/cache/triton,TORCH_EXTENSIONS_DIR=${SCRATCH}/cache/torch_extensions,TMPDIR=${SCRATCH}/tmp"

echo "========================================"
echo "Job ID   : $SLURM_JOB_ID"
echo "Node     : $SLURMD_NODENAME"
echo "Model    : $MODEL (Olmo3ForCausalLM, gleiche Klasse wie verifiziertes OLMo-3-7B)"
echo "Dataset  : $DATASET_FILE"
echo "Purpose  : Phase C -- letzte Vorbedingung fuer Spur-2-Judge-Piloten"
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
        --role judge \
        --base-model "$MODEL" \
        --dataset "$DATASET_FILE" \
        --output-dir "$TRAIN_OUT_DIR" \
        --max-steps 3 \
        --batch-size 1 \
        --grad-accum 16 \
        --max-seq-len 4096 \
        --deepspeed "${SCRATCH}/configs/ds_zero3_bf16.json"

echo "========================================"
if ls "$TRAIN_OUT_DIR"/checkpoint-* >/dev/null 2>&1 || [ -f "$TRAIN_OUT_DIR/final_adapter/adapter_config.json" ]; then
    echo "TRAIN-SMOKE-TEST PASSED -- OLMo-3.1-32B-Instruct durchlief LoRA-Init + 3 Trainingsschritte."
    exit 0
else
    echo "TRAIN-SMOKE-TEST FAILED -- kein Checkpoint/Adapter erzeugt. .err-Log pruefen."
    exit 1
fi
