#!/bin/bash
# scripts/export_expert_gguf_array.sh
# Automated GGUF Quantization for MoE Sovereign (Planner, 8 Experts, Judge)
# Exports:
# 1. Q8_0 (Reference Quality)
# 2. Q4_K_M (Production Target)

set -euo pipefail

SCRATCH="${SCRATCH:-/scratch/project_465003058/hornphil}"
ROLE="${1:-coder}"
MERGED_DIR="${2:-${SCRATCH}/checkpoints/merged_expert_${ROLE}}"
EXPORT_DIR="${3:-${SCRATCH}/exports/moe-expert-${ROLE}-4b}"
# $4 lets the caller (lumig_expert_ensemble_pipeline.slurm) pass down the
# already-resolved singularity path, so Stage 3 does not depend on its own
# PATH lookup succeeding independently of Stage 1/2's -- root cause of the
# "singularity: command not found" failure that broke 9/10 export runs was
# never fully isolated, so this removes the dependency instead of assuming
# the PATH lookup will work.
SINGULARITY_BIN="${4:-$(command -v singularity || true)}"
if [ -z "$SINGULARITY_BIN" ] || [ ! -x "$SINGULARITY_BIN" ]; then
    echo "ERROR: singularity binary not found (checked \$4 and \`command -v singularity\`)." >&2
    echo "Stage 3 cannot run the HF->GGUF conversion without it. Aborting before any work is lost." >&2
    exit 1
fi
echo "Using singularity: $SINGULARITY_BIN"

echo "================================================================================"
echo "📦 GGUF EXPORT & QUANTIZATION PIPELINE: [moe-${ROLE}]"
echo "Input Directory : $MERGED_DIR"
echo "Export Directory: $EXPORT_DIR"
echo "================================================================================"

mkdir -p "$EXPORT_DIR"

LLAMA_CPP_DIR="/scratch/project_465003058/hornphil/llama.cpp"
if [ ! -d "$LLAMA_CPP_DIR" ]; then
    echo "Cloning llama.cpp for GGUF conversion..."
    git clone --depth 1 https://github.com/ggerganov/llama.cpp "$LLAMA_CPP_DIR"
    make -C "$LLAMA_CPP_DIR" llama-quantize -j16
fi

if [ "$ROLE" == "judge" ]; then
    MODEL_NAME="sovereign-judge-27b"
elif [ "$ROLE" == "planner" ]; then
    MODEL_NAME="moe-sovereign-student-9b"
elif [ "$ROLE" == "judge_olmo31" ]; then
    MODEL_NAME="sovereign-judge-olmo31-32b"
elif [[ "$ROLE" == *_smollm3 ]]; then
    MODEL_NAME="smollm3-expert-${ROLE%_smollm3}-3b"
elif [ "$ROLE" == "planner_olmo3" ]; then
    MODEL_NAME="moe-sovereign-planner-olmo3-7b"
else
    MODEL_NAME="moe-expert-${ROLE}-4b"
fi

F16_GGUF="${EXPORT_DIR}/${MODEL_NAME}-F16.gguf"
Q8_GGUF="${EXPORT_DIR}/${MODEL_NAME}-Q8_0.gguf"
Q4_GGUF="${EXPORT_DIR}/${MODEL_NAME}-Q4_K_M.gguf"

# Find quantize binary
if [ -x "${LLAMA_CPP_DIR}/build/bin/llama-quantize" ]; then
    QUANTIZE_BIN="${LLAMA_CPP_DIR}/build/bin/llama-quantize"
elif [ -x "${LLAMA_CPP_DIR}/llama-quantize" ]; then
    QUANTIZE_BIN="${LLAMA_CPP_DIR}/llama-quantize"
elif [ -x "/scratch/project_465003058/hornphil/llama_cpp_tools/llama-quantize" ]; then
    QUANTIZE_BIN="/scratch/project_465003058/hornphil/llama_cpp_tools/llama-quantize"
else
    QUANTIZE_BIN="llama-quantize"
fi

CONTAINER="${SCRATCH}/lumi-multitorch-latest.sif"

# 1. Convert HuggingFace model to F16 GGUF (inside Singularity container for PyTorch/Transformers)
echo "⏳ Converting HuggingFace model to F16 GGUF..."
"$SINGULARITY_BIN" exec \
    --bind /scratch/project_465003058:/scratch/project_465003058 \
    --env PYTHONPATH="/scratch/project_465003058/hornphil/.user_site:${PYTHONPATH:-}",HF_HOME="${SCRATCH}/cache/huggingface",XDG_CACHE_HOME="${SCRATCH}/cache",TMPDIR="${SCRATCH}/tmp" \
    "$CONTAINER" \
    python3 "${LLAMA_CPP_DIR}/convert_hf_to_gguf.py" "$MERGED_DIR" \
        --outfile "$F16_GGUF" \
        --outtype f16

# 2. Quantize to Q8_0 (High Precision Reference, native on host)
echo "⏳ Quantizing to Q8_0 (High Precision Reference)..."
"$QUANTIZE_BIN" "$F16_GGUF" "$Q8_GGUF" Q8_0

# 3. Quantize to Q4_K_M (Production Target, native on host)
echo "⏳ Quantizing to Q4_K_M (Production Target)..."
"$QUANTIZE_BIN" "$F16_GGUF" "$Q4_GGUF" Q4_K_M

# 4. Cleanup raw F16 GGUF to save scratch disk space
rm -f "$F16_GGUF"

echo "================================================================================"
echo "✅ GGUF EXPORT COMPLETED FOR [${MODEL_NAME}]"
ls -lh "$EXPORT_DIR"
echo "================================================================================"
