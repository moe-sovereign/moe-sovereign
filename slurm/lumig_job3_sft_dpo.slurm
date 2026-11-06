#!/bin/bash
# slurm/lumig_job3_sft_dpo.slurm
# SLURM job: Rule-Based RL / DPO Alignment of the distilled 3B student model
#
# Architecture:
#   Base: Output checkpoint from Job 2 (moe_3b_distilled)
#   Framework: TRL SFTTrainer + PEFT LoRA + DeepSpeed ZeRO-2 BF16
#   Runtime: Singularity container (lumi-multitorch-latest.sif) — LUMI-G
#            system Python is 3.6.15 and cannot run the training script.
#
# Usage:
#   sbatch slurm/lumig_job3_sft_dpo.slurm

#SBATCH --job-name=moe_rl_dpo_alignment
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=48:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-sovereign/logs/rl_dpo_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-sovereign/logs/rl_dpo_%j.err

set -euo pipefail

# === Signal Trap for Automatic Checkpoint Save on Timeout / Preemption ===
checkpoint_and_exit() {
    echo "RECEIVED SIGTERM / SIGUSR1 FROM SLURM! SAVING DPO CHECKPOINT & FLUSHING BUFFER..."
    pkill -SIGINT -f python3 || true
    sync
    echo "FLUSH COMPLETED SAFELY AT $(date)"
    exit 0
}
trap 'checkpoint_and_exit' SIGTERM SIGUSR1 SIGINT

# ── Paths ────────────────────────────────────────────────────────────────────
SCRATCH="/scratch/project_465003058/hornphil"
CONTAINER="${SCRATCH}/lumi-multitorch-latest.sif"
SCRIPT_DIR="${SCRATCH}/moe-sovereign/scripts"
HF_CACHE="${SCRATCH}/hf_cache"
HF_TOKEN="$(cat ~/.cache/huggingface/token 2>/dev/null || echo '')"

# ── Model & Training Config ─────────────────────────────────────────────────
# Use the distilled student checkpoint from Job 2 as base model
MODEL_ID="${SCRATCH}/checkpoints/moe_qwen35_4b_distilled"
DATASET="${SCRATCH}/datasets/moe_rule_based_rl_dpo.jsonl"
TRAIN_RUN="moe_qwen35_4b_dpo_aligned"
OUTPUT_DIR="${SCRATCH}/checkpoints/${TRAIN_RUN}"

# ── Hyperparameters ──────────────────────────────────────────────────────────
# DPO alignment uses a lower learning rate than SFT to preserve learned knowledge
EPOCHS=3
# micro_batch=2 and --no-packing: proven stable in Job 2 (#21032430)
MICRO_BATCH=2
GRAD_ACCUM=8
MAX_SEQ_LEN=4096
LR="1e-5"
LORA_R=16
LORA_ALPHA=32
LORA_DROPOUT=0.05
SAVE_STEPS=100
GRADIENT_CHECKPOINTING=true

mkdir -p "$OUTPUT_DIR" "${SCRATCH}/moe-sovereign/logs"

echo "========================================"
echo "Job ID      : $SLURM_JOB_ID"
echo "Node        : $SLURMD_NODENAME"
echo "Train run   : $TRAIN_RUN"
echo "Base model  : $MODEL_ID (from distillation)"
echo "Dataset     : $DATASET"
echo "Output dir  : $OUTPUT_DIR  (fest — Resume-fähig)"
echo "Epochs      : $EPOCHS"
echo "Batch/GPU   : $MICRO_BATCH × grad_accum=$GRAD_ACCUM = $(( MICRO_BATCH * GRAD_ACCUM * 8 )) effective"
echo "Max seq len : $MAX_SEQ_LEN"
echo "LR          : $LR"
echo "LoRA r/α    : $LORA_R / $LORA_ALPHA (dropout=$LORA_DROPOUT)"
echo "Grad ckpt   : $GRADIENT_CHECKPOINTING"
echo "Save steps  : $SAVE_STEPS  (save_total_limit=3)"
echo "ZeRO stage  : 2"
echo "========================================"

# ── Sanity checks ────────────────────────────────────────────────────────────
if [ ! -f "$DATASET" ]; then
    echo "ERROR: DPO dataset not found: $DATASET"
    echo "Run Job 2 (distillation) first, then generate the DPO dataset."
    exit 1
fi
# The legacy compile_rule_based_rl_dataset.py output is templated placeholder text, not verified traces
if grep -q -m1 '\[VERIFIED EXECUTION PLAN #' "$DATASET"; then
    echo "ERROR: $DATASET contains placeholder pairs from compile_rule_based_rl_dataset.py; refusing to train on them."
    exit 1
fi
[ ! -f "$CONTAINER" ] && echo "ERROR: Singularity container not found: $CONTAINER" && exit 1

if [ ! -d "$MODEL_ID" ]; then
    echo "ERROR: Distilled model checkpoint not found: $MODEL_ID"
    echo "Run Job 2 (distillation) first."
    exit 1
fi

DATASET_LINES=$(wc -l < "$DATASET")
echo "Dataset lines: $DATASET_LINES"

# ── Checkpoint-Status ────────────────────────────────────────────────────────
LAST_CKPT=$(ls -d "${OUTPUT_DIR}/checkpoint-"* 2>/dev/null | sort -V | tail -1 || true)
if [ -n "$LAST_CKPT" ]; then
    CKPT_STEP=$(basename "$LAST_CKPT" | grep -oP '\d+')
    echo "Resuming from: $LAST_CKPT (step ${CKPT_STEP})"
else
    echo "No checkpoint found — DPO alignment starts from scratch."
fi

# ── ROCm / AMD Environment ──────────────────────────────────────────────────
export ROCR_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export HIP_VISIBLE_DEVICES=0,1,2,3,4,5,6,7
export NCCL_SOCKET_IFNAME=hsn0
export NCCL_NET_GDR_LEVEL=3
export MASTER_ADDR=$(hostname)
export MASTER_PORT=29500
export OMP_NUM_THREADS=7
# expandable_segments: prevents fragmentation OOM on MI250X
export PYTORCH_HIP_ALLOC_CONF=expandable_segments:True,max_split_size_mb:512
export PYTORCH_ALLOC_CONF=expandable_segments:True,max_split_size_mb:512
export TOKENIZERS_PARALLELISM=false
export CUSTOM_SITE="${SCRATCH}/.user_site"
mkdir -p "$CUSTOM_SITE"

# ── DPO Alignment starten ───────────────────────────────────────────────────
echo "[$(date +%H:%M:%S)] Starting DPO alignment (run: $TRAIN_RUN) ..."

singularity exec \
    --bind /scratch/project_465003058:/scratch/project_465003058 \
    --env PYTHONPATH="$CUSTOM_SITE" \
    --env HF_HOME="$HF_CACHE" \
    --env HUGGING_FACE_HUB_TOKEN="$HF_TOKEN" \
    --env ROCR_VISIBLE_DEVICES="$ROCR_VISIBLE_DEVICES" \
    --env HIP_VISIBLE_DEVICES="$HIP_VISIBLE_DEVICES" \
    --env NCCL_SOCKET_IFNAME="$NCCL_SOCKET_IFNAME" \
    --env NCCL_NET_GDR_LEVEL="$NCCL_NET_GDR_LEVEL" \
    --env MASTER_ADDR="$MASTER_ADDR" \
    --env MASTER_PORT="$MASTER_PORT" \
    --env OMP_NUM_THREADS="$OMP_NUM_THREADS" \
    --env PYTORCH_HIP_ALLOC_CONF="$PYTORCH_HIP_ALLOC_CONF" \
    --env PYTORCH_ALLOC_CONF="$PYTORCH_ALLOC_CONF" \
    --env TOKENIZERS_PARALLELISM="$TOKENIZERS_PARALLELISM" \
    "$CONTAINER" \
    deepspeed --num_gpus 8 \
        "$SCRIPT_DIR/train_planner_sft.py" \
        --model-id       "$MODEL_ID" \
        --dataset        "$DATASET" \
        --output-dir     "$OUTPUT_DIR" \
        --deepspeed      "$SCRIPT_DIR/deepspeed_zero2_qwen3.json" \
        --epochs         "$EPOCHS" \
        --micro-batch    "$MICRO_BATCH" \
        --grad-accum     "$GRAD_ACCUM" \
        --max-seq-len    "$MAX_SEQ_LEN" \
        --lr             "$LR" \
        --lora-r         "$LORA_R" \
        --lora-alpha     "$LORA_ALPHA" \
        --lora-dropout   "$LORA_DROPOUT" \
        --save-steps     "$SAVE_STEPS" \
        --hf-cache       "$HF_CACHE" \
        --val-split      0.005 \
        --no-packing \
        --no-4bit

DPO_EXIT=$?
echo "[$(date +%H:%M:%S)] DPO alignment exited with code $DPO_EXIT"

echo "========================================"
if [ $DPO_EXIT -eq 0 ]; then
    echo "DPO ALIGNMENT COMPLETED — run: $TRAIN_RUN"
    ls -lh "$OUTPUT_DIR/final_adapter/" 2>/dev/null
else
    echo "DPO ALIGNMENT FAILED (exit $DPO_EXIT) — run: $TRAIN_RUN"
    echo "Checkpoints preserved in: $OUTPUT_DIR"
    echo "Last checkpoint: $(ls -d ${OUTPUT_DIR}/checkpoint-* 2>/dev/null | sort -V | tail -1 || echo 'none')"
fi
echo "========================================"
exit $DPO_EXIT
