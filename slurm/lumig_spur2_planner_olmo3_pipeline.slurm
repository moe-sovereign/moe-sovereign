#!/bin/bash
# slurm/lumig_spur2_planner_olmo3_pipeline.slurm
# Plan Phase 4: Spur-2 (echte Open-Source-Basismodelle) Planner-Rolle auf
# OLMo-3-7B-Instruct (Ai2, Gewichte + Trainingsdaten + Trainingscode
# oeffentlich) -- parallel zum Spur-1-Planner auf Qwen3.5-9B
# (lumig_expert_ensemble_pipeline.slurm planner).
#
# OLMo-3-7B bereits als Planner-Kandidat verifiziert: Grounding-Smoke-Test
# bestanden (Job 21856449) und Trainings-Smoke-Test bestanden (Job
# 21907344, Phase-C-Kriterium der Nutzerentscheidung "OLMo-3-7B ueber
# denselben Trainings-Smoke-Test bewerten statt ueber den Grounding-Test").
# Dies ist der erste echte volle Trainingslauf danach.
#
# Nutzt denselben Datensatz wie Spur 1 (dataset_expert_planner_merged.jsonl)
# -- dieselbe Instruktions-/Antwort-Struktur ist basis-unabhaengig, exakt
# das Muster, das schon fuer die SmolLM3-Experten-Pipeline verwendet wurde
# (gleicher Datensatz, andere Basis). Der separat generierte
# role_sft_planner_olmo3.jsonl (Job 21855391) ist bereits Teil derselben
# allgemeinen Merge-Quelle, kein eigener Datensatz noetig.
#
# EIGENES Skript statt Zweig in der Qwen-Pipeline: eigene, frische
# Output-Pfade (olmo3_7b_planner_*) um jede Kollision mit den Qwen-9B- und
# 4B-Planner-Checkpoints auszuschliessen.
#
# Usage:
#   sbatch slurm/lumig_spur2_planner_olmo3_pipeline.slurm

#SBATCH --job-name=moe_spur2_planner_olmo3
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=48:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/spur2_planner_olmo3_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/spur2_planner_olmo3_%j.err

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
DATASET_FILE="${SCRATCH}/datasets/dataset_expert_planner_merged.jsonl"
BASE_MODEL="${SCRATCH}/hf_cache/hub/models--allenai--Olmo-3-7B-Instruct/snapshots/6e5971d9eba42665f5bd5a0fcf047f299ce1dccc"
TRAIN_OUT_DIR="${SCRATCH}/checkpoints/olmo3_7b_planner_sft"
MERGE_OUT_DIR="${SCRATCH}/checkpoints/merged_olmo3_7b_planner"
EXPORT_DIR="${SCRATCH}/exports/moe-sovereign-planner-olmo3-7b"
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
echo "🚀 LUMI-G SPUR-2 PIPELINE: [planner-olmo3-7b] (open source, parallel zu Spur 1)"
echo "Job ID          : $SLURM_JOB_ID"
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
        --role planner \
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
bash "${SCRIPT_DIR}/export_expert_gguf_array.sh" "planner_olmo3" "$MERGE_OUT_DIR" "$EXPORT_DIR" "$SINGULARITY_BIN"

echo "[$(date +%H:%M:%S)] 🎉 SPUR-2 PIPELINE COMPLETED SUCCESSFULLY FOR [planner-olmo3-7b]!"
