#!/usr/bin/env bash
# run_spur1_and_spur2.sh — Sequential benchmark execution for Spur 1 (Open Source) & Spur 2 (Open Weight)
#
# Spur 1: Open Source  (planner OLMo3-7B, experts SmolLM3-3B, judge OLMo3.1-32B, all fine-tuned from hf.co/h3rb3rn)
# Spur 2: Open Weight  (planner Qwen3.5-9B, experts Qwen3.5-4B, judge Qwen3.8-27B, all fine-tuned from hf.co/h3rb3rn)
# Conditions per track (7): native_baseline (large dense base model, no orchestration), the three pre-finetune
# templates (GraphRAG / GraphRAG + debate / no GraphRAG) and the three fine-tuned templates (same three variants).
#
# Each track evaluates 4 conditions:
#   1. native_baseline (direct model call)
#   2. ablation_no_graphrag (Compound AI without GraphRAG)
#   3. compound_ai (Compound AI with GraphRAG)
#   4. compound_ai_debate (Compound AI with GraphRAG + Deliberation/Debate)

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Source environment
ENV_FILE="$SCRIPT_DIR/.env"
if [ -f "$ENV_FILE" ]; then
    set -a; source "$ENV_FILE"; set +a
fi

API_BASE="${MOE_API_BASE:-http://localhost:8002}"
API_KEY="${MOE_API_KEY:-}"
# Each track is judged by its own fine-tuned judge. A single MOE_JUDGE_MODEL (e.g. from benchmarks/.env) is
# deliberately NOT used here: Spur 1 (open source) -> OLMo-3.1-32B judge, Spur 2 (open weight) -> the
# Qwen3.8-27B based hf.co/h3rb3rn/sovereign-judge-27b. Override per track with MOE_JUDGE_MODEL_SPUR1 / MOE_JUDGE_MODEL_SPUR2.
JUDGE_MODEL_SPUR1="${MOE_JUDGE_MODEL_SPUR1:-hf.co/h3rb3rn/sovereign-judge-olmo31-32b:Q4_K_M}"
# Native single-LLM baselines (the only models allowed to run without orchestration): large dense base models.
NATIVE_SPUR1="${MOE_NATIVE_SPUR1:-olmo31-32b-instruct-base-fixed:latest}"
NATIVE_SPUR2="${MOE_NATIVE_SPUR2:-qwen3.8:27b}"
JUDGE_MODEL_SPUR2="${MOE_JUDGE_MODEL_SPUR2:-hf.co/h3rb3rn/sovereign-judge-27b:Q4_K_M}"
JUDGE_NODE="${MOE_JUDGE_NODE:-N04-RTX}"
NUM_ROUNDS="${MOE_BENCHMARK_NUM_ROUNDS:-5}"
# Track selection: MOE_RUN_SPUR1=0 skips the open source phase, MOE_RUN_SPUR2=0 the open weight phase.
RUN_SPUR1="${MOE_RUN_SPUR1:-1}"
RUN_SPUR2="${MOE_RUN_SPUR2:-1}"
SPUR1_RC="skipped"; SPUR2_RC="skipped"; SPUR1_LOG="-"; SPUR2_LOG="-"
RESULTS_DIR="$SCRIPT_DIR/results"
mkdir -p "$RESULTS_DIR"

echo "================================================================================"
echo "🚀 STARTING SEQUENTIAL SCIENTIFIC BENCHMARK: SPUR 1 -> SPUR 2"
echo "  Timestamp:    $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "  API Base:     $API_BASE"
echo "  Judge Spur 1: $JUDGE_MODEL_SPUR1 @ $JUDGE_NODE"
echo "  Judge Spur 2: $JUDGE_MODEL_SPUR2 @ $JUDGE_NODE"
echo "  Native Spur 1: $NATIVE_SPUR1 | Native Spur 2: $NATIVE_SPUR2"
echo "  Num Rounds:   $NUM_ROUNDS"
echo "================================================================================"

# ==============================================================================
# SPUR 1: OPEN SOURCE (OLMo + SmolLM3)
# ==============================================================================
if [ "$RUN_SPUR1" = "1" ]; then
SPUR1_TS=$(date +%Y%m%d-%H%M%S)
SPUR1_LOG="$RESULTS_DIR/lumig_spur1_opensource_${SPUR1_TS}.log"

echo ""
echo "================================================================================"
echo "📍 PHASE 1: SPUR 1 (OPEN SOURCE — OLMo + SmolLM3)"
echo "  Log: $SPUR1_LOG"
echo "================================================================================"

MOE_API_BASE="$API_BASE" \
MOE_API_KEY="$API_KEY" \
MOE_JUDGE_MODEL="$JUDGE_MODEL_SPUR1" \
MOE_JUDGE_NODE="$JUDGE_NODE" \
MOE_BENCHMARK_NATIVE_MODEL="$NATIVE_SPUR1" \
MOE_BENCHMARK_TEMPLATE_PREFINETUNE="LUMI-G Base (Pre-Finetune)" \
MOE_BENCHMARK_TEMPLATE_PREFINETUNE_DEBATE="LUMI-G Base (Pre-Finetune) - Deliberation" \
MOE_BENCHMARK_TEMPLATE_PREFINETUNE_ABLATION_NO_GRAPHRAG="LUMI-G Base (Pre-Finetune) - No-GraphRAG" \
MOE_BENCHMARK_TEMPLATE_COMPOUND_AI="LUMI-G OLMo + SmolLM3 Sovereign Ensemble" \
MOE_BENCHMARK_TEMPLATE_COMPOUND_AI_DEBATE="LUMI-G OLMo + SmolLM3 Sovereign Ensemble - Deliberation" \
MOE_BENCHMARK_TEMPLATE_ABLATION_NO_GRAPHRAG="LUMI-G OLMo + SmolLM3 Sovereign Ensemble - No-GraphRAG" \
MOE_BENCHMARK_NUM_ROUNDS="$NUM_ROUNDS" \
MOE_BENCHMARK_SKIP_PREFLIGHT="1" \
BENCHMARK_SUITE="spur1_opensource" \
python3 "$SCRIPT_DIR/run_scientific_benchmark.py" --fresh > "$SPUR1_LOG" 2>&1

SPUR1_RC=$?
echo "📍 Phase 1 (Spur 1) finished with exit code: $SPUR1_RC at $(date -u +%Y-%m-%dT%H:%M:%SZ)"

fi

if [ "$RUN_SPUR1" = "1" ] && [ "$RUN_SPUR2" = "1" ]; then
# Brief pause between tracks to allow inference nodes and caches to settle
echo "Sleeping 30s before initiating Spur 2..."
sleep 30

# ==============================================================================
# SPUR 2: OPEN WEIGHT (Qwen3.5:4b)
# ==============================================================================
fi
if [ "$RUN_SPUR2" = "1" ]; then
SPUR2_TS=$(date +%Y%m%d-%H%M%S)
SPUR2_LOG="$RESULTS_DIR/lumig_spur2_openweight_${SPUR2_TS}.log"

echo ""
echo "================================================================================"
echo "📍 PHASE 2: SPUR 2 (OPEN WEIGHT — Qwen3.5:4B)"
echo "  Log: $SPUR2_LOG"
echo "================================================================================"

MOE_API_BASE="$API_BASE" \
MOE_API_KEY="$API_KEY" \
MOE_JUDGE_MODEL="$JUDGE_MODEL_SPUR2" \
MOE_JUDGE_NODE="$JUDGE_NODE" \
MOE_BENCHMARK_NATIVE_MODEL="$NATIVE_SPUR2" \
MOE_BENCHMARK_TEMPLATE_PREFINETUNE="Open-Weight Base (Pre-Finetune)" \
MOE_BENCHMARK_TEMPLATE_PREFINETUNE_DEBATE="Open-Weight Base (Pre-Finetune) - Deliberation" \
MOE_BENCHMARK_TEMPLATE_PREFINETUNE_ABLATION_NO_GRAPHRAG="Open-Weight Base (Pre-Finetune) - No-GraphRAG" \
MOE_BENCHMARK_TEMPLATE_COMPOUND_AI="Open-Weight Finetuned Ensemble" \
MOE_BENCHMARK_TEMPLATE_COMPOUND_AI_DEBATE="Open-Weight Finetuned Ensemble - Deliberation" \
MOE_BENCHMARK_TEMPLATE_ABLATION_NO_GRAPHRAG="Open-Weight Finetuned Ensemble - No-GraphRAG" \
MOE_BENCHMARK_NUM_ROUNDS="$NUM_ROUNDS" \
MOE_BENCHMARK_SKIP_PREFLIGHT="1" \
BENCHMARK_SUITE="spur2_openweight" \
python3 "$SCRIPT_DIR/run_scientific_benchmark.py" --fresh > "$SPUR2_LOG" 2>&1

SPUR2_RC=$?
echo "📍 Phase 2 (Spur 2) finished with exit code: $SPUR2_RC at $(date -u +%Y-%m-%dT%H:%M:%SZ)"

fi

echo ""
echo "================================================================================"
echo "🏁 ALL BENCHMARKS COMPLETED"
echo "  Spur 1 exit code: $SPUR1_RC (Log: $SPUR1_LOG)"
echo "  Spur 2 exit code: $SPUR2_RC (Log: $SPUR2_LOG)"
echo "================================================================================"
