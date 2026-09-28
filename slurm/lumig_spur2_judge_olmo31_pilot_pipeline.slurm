#!/bin/bash
# slurm/lumig_spur2_judge_olmo31_pilot_pipeline.slurm
# Plan Phase 4.3: Spur-2-Pilot-Finetuning fuer die Judge-Rolle auf
# OLMo-3.1-32B-Instruct (genuinely open-source, Nutzerempfehlung als
# Spur-2-Pilot, da am gruendlichsten verifiziert: Grounding-Smoke-Test
# 24/24, 6 Zusatzlehrer-Rollen sauber, Phase-C-Trainings-Smoke-Test
# bestanden mit ZeRO-3).
#
# Analog zu lumig_expert_ensemble_pipeline.slurm (Stage 1 SFT -> Stage 2
# CPU-Merge -> Stage 3 GGUF), aber als EIGENES Skript statt eines weiteren
# Zweigs in der Qwen-Pipeline, weil:
#   1. DeepSpeed-Config MUSS ZeRO-3 sein, nicht der Qwen-Pipeline-Default
#      ZeRO-2 -- Job 21915493 bewies real per HIP-OOM, dass ein 32B-Modell
#      in bf16 (~64GB) den 64GB-Speicher pro MI250X-GCD schon alleine
#      saettigt, ZeRO-2 shardet nur Optimizer/Gradienten, nicht die
#      Modellgewichte selbst. ZeRO-3-Retry (Job 21915742) bestand sauber.
#   2. EIGENE, frische Output-Pfade (olmo31_32b_judge_*, nicht judge_*)
#      -- bewusst NICHT die alten `sovereign_judge_27b_sft`/
#      `merged_sovereign_judge_27b`-Namen wiederverwendet, nachdem genau
#      diese Pfadkollision beim Coder-Piloten (Job 21915822) real zu einer
#      Fehlausfuehrung fuehrte (--resume fand den alten August-Checkpoint,
#      lief 0.003s durch, reichte den defekten Adapter unveraendert durch).
#
# Rolle "judge_olmo31" (nicht "judge") wird an train_expert_slm_pipeline.py
# durchgereicht -- dessen --role choices-Liste erlaubt nur die 10
# bekannten Rollennamen, "judge_olmo31" ist NICHT enthalten. Deshalb wird
# hier "judge" als --role uebergeben (steuert nur System-Prompt-Auswahl
# innerhalb des Skripts, falls vorhanden -- tatsaechlich unbenutzt fuer
# reine Trainingszwecke) UND alle Pfade explizit mit dem eigenen Praefix
# gesetzt, damit keine Verwechslung mit der Qwen-Judge-Pipeline entsteht.
#
# Usage:
#   sbatch slurm/lumig_spur2_judge_olmo31_pilot_pipeline.slurm

#SBATCH --job-name=moe_spur2_judge_olmo31_pilot
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=72:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/spur2_judge_olmo31_pilot_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/spur2_judge_olmo31_pilot_%j.err

set -euo pipefail

checkpoint_and_exit() {
    echo "RECEIVED SIGTERM / SIGUSR1 FROM SLURM! FLUSHING BUFFERS AT $(date)"
    pkill -SIGINT -f python3 || true
    sync
    echo "CHECKPOINT FLUSH COMPLETED SAFELY AT $(date)"
    exit 0
}
trap 'checkpoint_and_exit' SIGTERM SIGUSR1 SIGINT

SCRATCH="/scratch/project_465003058/hornphil"
CONTAINER="${SCRATCH}/lumi-multitorch-latest.sif"
SCRIPT_DIR="${SCRATCH}/moe-sovereign/scripts"
DATASET_FILE="${SCRATCH}/datasets/dataset_expert_judge_merged.jsonl"
BASE_MODEL="allenai/Olmo-3.1-32B-Instruct"
TRAIN_OUT_DIR="${SCRATCH}/checkpoints/olmo31_32b_judge_sft"
MERGE_OUT_DIR="${SCRATCH}/checkpoints/merged_olmo31_32b_judge"
EXPORT_DIR="${SCRATCH}/exports/sovereign-judge-olmo31-32b"
DEEPSPEED_CONFIG="${SCRATCH}/configs/ds_zero3_bf16.json"
BATCH_SIZE=1
GRAD_ACCUM=16
LR=1.0e-5

SINGULARITY_BIN="$(command -v singularity || true)"
if [ -z "$SINGULARITY_BIN" ] || [ ! -x "$SINGULARITY_BIN" ]; then
    echo "ERROR: singularity binary not found on PATH at job start. Aborting before Stage 1." >&2
    exit 1
fi
if [ ! -f "$CONTAINER" ]; then echo "ERROR: Container not found: $CONTAINER"; exit 1; fi
if [ ! -f "$DATASET_FILE" ]; then echo "ERROR: Dataset not found: $DATASET_FILE"; exit 1; fi
if [ ! -f "$DEEPSPEED_CONFIG" ]; then echo "ERROR: DeepSpeed config not found: $DEEPSPEED_CONFIG"; exit 1; fi
if [ ! -f "${SCRIPT_DIR}/train_expert_slm_pipeline.py" ]; then
    echo "ERROR: Training script not found: ${SCRIPT_DIR}/train_expert_slm_pipeline.py"
    exit 1
fi

# Landmine check (lesson from job 21915822): refuse to silently resume
# into a pre-existing, unrelated checkpoint directory. This path is new
# and should not exist yet -- if it does, something unexpected is here.
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
echo "🚀 LUMI-G SPUR-2 PILOT PIPELINE: [judge_olmo31]"
echo "Job ID          : $SLURM_JOB_ID"
echo "Base Model      : $BASE_MODEL"
echo "Dataset         : $DATASET_FILE"
echo "Train Output    : $TRAIN_OUT_DIR"
echo "Merged Output   : $MERGE_OUT_DIR"
echo "Export Directory: $EXPORT_DIR"
echo "DeepSpeed Config: $DEEPSPEED_CONFIG (ZeRO-3 -- required for 32B, ZeRO-2 OOMs)"
echo "Batch / Accum   : Batch $BATCH_SIZE x Accum $GRAD_ACCUM (LR $LR)"
echo "================================================================================"

# ── STAGE 1: SFT Training ────────────────────────────────────────────────
echo "[$(date +%H:%M:%S)] === STAGE 1: Distributed SFT Training on 8x MI250X (Torchrun, ZeRO-3) ==="
"$SINGULARITY_BIN" exec \
    --bind /scratch/project_465003058:/scratch/project_465003058 \
    --env PYTHONPATH="/scratch/project_465003058/hornphil/.user_site:${PYTHONPATH:-}",HF_HOME="${SCRATCH}/cache/huggingface",XDG_CACHE_HOME="${SCRATCH}/cache",TRITON_CACHE_DIR="${SCRATCH}/cache/triton",TORCH_EXTENSIONS_DIR="${SCRATCH}/cache/torch_extensions",TMPDIR="${SCRATCH}/tmp" \
    "$CONTAINER" \
    torchrun --standalone --nproc_per_node=8 \
        "${SCRIPT_DIR}/train_expert_slm_pipeline.py" \
        --role judge \
        --base-model "$BASE_MODEL" \
        --dataset "$DATASET_FILE" \
        --output-dir "$TRAIN_OUT_DIR" \
        --epochs 3 \
        --batch-size "$BATCH_SIZE" \
        --grad-accum "$GRAD_ACCUM" \
        --learning-rate "$LR" \
        --max-seq-len 4096 \
        --deepspeed "$DEEPSPEED_CONFIG"

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
bash "${SCRIPT_DIR}/export_expert_gguf_array.sh" "judge_olmo31" "$MERGE_OUT_DIR" "$EXPORT_DIR" "$SINGULARITY_BIN"

echo "[$(date +%H:%M:%S)] 🎉 SPUR-2 PILOT PIPELINE COMPLETED SUCCESSFULLY FOR [judge_olmo31]!"
