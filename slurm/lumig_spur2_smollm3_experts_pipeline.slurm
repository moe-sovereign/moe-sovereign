#!/bin/bash
# slurm/lumig_spur2_smollm3_experts_pipeline.slurm
# Plan Phase 4: Spur-2 (echte Open-Source-Basismodelle) parallel zu Spur 1
# (Qwen3.5, open-weight) -- SmolLM3-3B (HuggingFaceTB, Gewichte + Trainingsdaten
# + Trainingscode oeffentlich) als Experten-Basis fuer dieselben 7 Rollen,
# die in lumig_expert_ensemble_pipeline.slurm auf Qwen3.5-4B laufen.
#
# SmolLM3-3B-Eignung bereits verifiziert (Plan Phase 4): Grounding-Smoke-Test
# 21/24 real brauchbar, alle 7 Standard-target_modules
# (q/k/v/o_proj, gate/up/down_proj) empirisch bestaetigt vorhanden -- Skript
# train_expert_slm_pipeline.py verwendet diese bereits hart codiert, keine
# Anpassung noetig.
#
# EIGENES Skript statt weiterer Zweig in der Qwen-Pipeline, aus demselben
# Grund wie beim Judge-OLMo-3.1-32B-Pilot: eigene, frische Output-Pfade
# (smollm3_expert_<role>_*, nicht moe_expert_<role>_*) um jede Kollision mit
# den parallel laufenden Qwen-Checkpoints auszuschliessen (Lehre aus Job
# 21915822: Pfadkollision + --resume-Default fuehrte zu einer
# Fehlausfuehrung, die unbemerkt geblieben waere ohne Log-Pruefung).
#
# --role wird unveraendert an train_expert_slm_pipeline.py durchgereicht
# (die echten Rollennamen sind gueltige choices, steuert nur den
# System-Prompt). Fuer den GGUF-Export wird "<role>_smollm3" verwendet
# (export_expert_gguf_array.sh MODEL_NAME-Zweig ergibt "smollm3-expert-<role>-3b").
#
# Usage:
#   sbatch slurm/lumig_spur2_smollm3_experts_pipeline.slurm <role>
#   role in: precision graphrag governance research security datainfra omni

#SBATCH --job-name=moe_spur2_smollm3_expert
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=48:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/spur2_smollm3_expert_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/spur2_smollm3_expert_%j.err

set -euo pipefail

checkpoint_and_exit() {
    echo "RECEIVED SIGTERM / SIGUSR1 FROM SLURM! FLUSHING BUFFERS AT $(date)"
    pkill -SIGINT -f python3 || true
    sync
    echo "CHECKPOINT FLUSH COMPLETED SAFELY AT $(date)"
    exit 0
}
trap 'checkpoint_and_exit' SIGTERM SIGUSR1 SIGINT

ROLE="${1:?Usage: sbatch lumig_spur2_smollm3_experts_pipeline.slurm <role>}"
case "$ROLE" in
    coder|precision|graphrag|governance|research|security|datainfra|omni) ;;
    *) echo "ERROR: unsupported role '$ROLE' for the Spur-2 SmolLM3 expert pipeline." >&2; exit 1 ;;
esac

SCRATCH="/scratch/project_465003058/hornphil"
CONTAINER="${SCRATCH}/lumi-multitorch-latest.sif"
SCRIPT_DIR="${SCRATCH}/moe-sovereign/scripts"
DATASET_FILE="${SCRATCH}/datasets/dataset_expert_${ROLE}_merged.jsonl"
BASE_MODEL="${SCRATCH}/hf_cache/hub/models--HuggingFaceTB--SmolLM3-3B/snapshots/a07cc9a04f16550a088caea529712d1d335b0ac1"
TRAIN_OUT_DIR="${SCRATCH}/checkpoints/smollm3_expert_${ROLE}_sft"
MERGE_OUT_DIR="${SCRATCH}/checkpoints/merged_smollm3_expert_${ROLE}"
EXPORT_DIR="${SCRATCH}/exports/smollm3-expert-${ROLE}-3b"
BATCH_SIZE=4
GRAD_ACCUM=4
LR=1.5e-5

SINGULARITY_BIN="$(command -v singularity || true)"
if [ -z "$SINGULARITY_BIN" ] || [ ! -x "$SINGULARITY_BIN" ]; then
    echo "ERROR: singularity binary not found on PATH at job start. Aborting before Stage 1." >&2
    exit 1
fi
if [ ! -f "$CONTAINER" ]; then echo "ERROR: Container not found: $CONTAINER"; exit 1; fi
if [ ! -f "$DATASET_FILE" ]; then echo "ERROR: Dataset not found: $DATASET_FILE"; exit 1; fi
if [ ! -d "$BASE_MODEL" ]; then echo "ERROR: Base model snapshot not found: $BASE_MODEL"; exit 1; fi
if [ ! -f "${SCRIPT_DIR}/train_expert_slm_pipeline.py" ]; then
    echo "ERROR: Training script not found: ${SCRIPT_DIR}/train_expert_slm_pipeline.py"
    exit 1
fi

# Landmine check (Lehre aus Job 21915822): niemals blind in ein bereits
# bestehendes, nicht-leeres Checkpoint-Verzeichnis hinein resumen.
if [ -d "$TRAIN_OUT_DIR" ] && [ -n "$(ls -A "$TRAIN_OUT_DIR" 2>/dev/null)" ]; then
    echo "ERROR: $TRAIN_OUT_DIR already exists and is non-empty. Refusing to auto-resume into it blindly."
    echo "Inspect it manually first (it may be a legitimate resume point, or stale data)."
    exit 1
fi

export HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export PYTORCH_HIP_ALLOC_CONF=garbage_collection_threshold:0.8,max_split_size_mb:512
export PYTHONPATH="/scratch/project_465003058/hornphil/.user_site:${PYTHONPATH:-}"
export HF_HOME="${SCRATCH}/cache/huggingface"
export XDG_CACHE_HOME="${SCRATCH}/cache"
export TRITON_CACHE_DIR="${SCRATCH}/cache/triton"
export TORCH_EXTENSIONS_DIR="${SCRATCH}/cache/torch_extensions"
export TMPDIR="${SCRATCH}/tmp"
mkdir -p "${SCRATCH}/cache/huggingface" "${SCRATCH}/cache/triton" "${SCRATCH}/cache/torch_extensions" "${SCRATCH}/tmp"
mkdir -p "$TRAIN_OUT_DIR" "$MERGE_OUT_DIR" "$EXPORT_DIR" "${SCRATCH}/moe-sovereign/logs"

echo "================================================================================"
echo "🚀 LUMI-G SPUR-2 PIPELINE: [smollm3-expert-${ROLE}] (open source, parallel zu Spur 1)"
echo "Job ID          : $SLURM_JOB_ID"
echo "Role            : $ROLE"
echo "Base Model      : $BASE_MODEL"
echo "Dataset         : $DATASET_FILE"
echo "Train Output    : $TRAIN_OUT_DIR"
echo "Merged Output   : $MERGE_OUT_DIR"
echo "Export Directory: $EXPORT_DIR"
echo "Batch / Accum   : Batch $BATCH_SIZE x Accum $GRAD_ACCUM (LR $LR)"
echo "================================================================================"

# ── STAGE 1: SFT Training ────────────────────────────────────────────────
echo "[$(date +%H:%M:%S)] === STAGE 1: Distributed SFT Training on 8x MI250X (Torchrun) ==="
"$SINGULARITY_BIN" exec \
    --bind /scratch/project_465003058:/scratch/project_465003058 \
    --env PYTHONPATH="/scratch/project_465003058/hornphil/.user_site:${PYTHONPATH:-}",HF_HOME="${SCRATCH}/cache/huggingface",XDG_CACHE_HOME="${SCRATCH}/cache",TRITON_CACHE_DIR="${SCRATCH}/cache/triton",TORCH_EXTENSIONS_DIR="${SCRATCH}/cache/torch_extensions",TMPDIR="${SCRATCH}/tmp" \
    "$CONTAINER" \
    torchrun --standalone --nproc_per_node=8 \
        "${SCRIPT_DIR}/train_expert_slm_pipeline.py" \
        --role "$ROLE" \
        --base-model "$BASE_MODEL" \
        --dataset "$DATASET_FILE" \
        --output-dir "$TRAIN_OUT_DIR" \
        --epochs 3 \
        --batch-size "$BATCH_SIZE" \
        --grad-accum "$GRAD_ACCUM" \
        --learning-rate "$LR" \
        --max-seq-len 4096

ADAPTER_DIR="${TRAIN_OUT_DIR}/final_adapter"
if [ ! -d "$ADAPTER_DIR" ]; then
    echo "ERROR: Training failed to produce final adapter at $ADAPTER_DIR"
    exit 1
fi

# ── STAGE 2: CPU-BF16 LoRA Merge ─────────────────────────────────────────
echo "[$(date +%H:%M:%S)] === STAGE 2: CPU-BF16 LoRA Merge ==="
"$SINGULARITY_BIN" exec \
    --bind /scratch/project_465003058:/scratch/project_465003058 \
    --env PYTHONPATH="/scratch/project_465003058/hornphil/.user_site:${PYTHONPATH:-}",HF_HOME="${SCRATCH}/cache/huggingface",XDG_CACHE_HOME="${SCRATCH}/cache",TRITON_CACHE_DIR="${SCRATCH}/cache/triton",TORCH_EXTENSIONS_DIR="${SCRATCH}/cache/torch_extensions",TMPDIR="${SCRATCH}/tmp" \
    "$CONTAINER" \
    python3 "${SCRIPT_DIR}/merge_expert_lora_cpu.py" \
        --base-model "$BASE_MODEL" \
        --adapter-dir "$ADAPTER_DIR" \
        --out-dir "$MERGE_OUT_DIR"

# ── STAGE 3: GGUF Q8_0 & Q4_K_M Quantization ─────────────────────────────
echo "[$(date +%H:%M:%S)] === STAGE 3: GGUF Quantization (Q8_0 & Q4_K_M) ==="
bash "${SCRIPT_DIR}/export_expert_gguf_array.sh" "${ROLE}_smollm3" "$MERGE_OUT_DIR" "$EXPORT_DIR" "$SINGULARITY_BIN"

echo "[$(date +%H:%M:%S)] 🎉 SPUR-2 PIPELINE COMPLETED SUCCESSFULLY FOR [smollm3-expert-${ROLE}]!"
