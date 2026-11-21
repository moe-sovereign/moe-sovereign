#!/bin/bash
# SQL-tool experiment (pre-registration E): GRPO with a binary execution reward, and evaluation of models that write SQL for a table they cannot see.
#   MODE=train MODEL=<dir> TAG=<name> [STEPS=300 LR=2e-5]        8 GCDs, LoRA GRPO, merged model in $OUTROOT/<TAG>/merged
#   MODE=eval  MODEL=<dir> TAG=<name> [N=64 TP=1 TESTS="test_s1 test_s3 test_s10 test_s30" LIMIT=0]   result in $OUTROOT/<TAG>/eval.json
# Submit train with --gpus-per-node=8 --time=..., eval with --gpus-per-node=2 (TP=2 for the 32B control). Data: $R/sqlrl (build_sql_tasks.py).
#SBATCH --job-name=rlvr_sql
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=02:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/moe-stack/logs/rlvr_%x_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-stack/logs/rlvr_%x_%j.err
set -euo pipefail
: "${MODE:?}" "${MODEL:?}" "${TAG:?}"
S=/scratch/project_465003058/hornphil
R=$S/moe-stack
APP=$R/${APP_SUBDIR:-app_e2}/benchmarks/orchestration_suite/rlvr
OUTROOT=$S/moe-stack/rlvr; OUT=$OUTROOT/$TAG
mkdir -p "$OUT" "$R/logs" "$S/tmp" "$S/cache/triton"
SIF=$S/lumi-multitorch-latest.sif
export PYTORCH_HIP_ALLOC_CONF=expandable_segments:True NCCL_SOCKET_IFNAME=hsn0 NCCL_NET_GDR_LEVEL=3
SING_ENV="PYTHONPATH=$S/.user_site:$S/pgtest/lib,HF_HOME=$S/cache/huggingface,HF_HUB_OFFLINE=1,XDG_CACHE_HOME=$S/cache,TRITON_CACHE_DIR=$S/cache/triton,TMPDIR=$S/tmp"
run() { singularity exec --bind "$S:$S" --env "$SING_ENV" "$SIF" "$@"; }
# embedded PostgreSQL 16 (pgserver) as the checker of the reward, data in memory
PGD=/dev/shm/pg_$SLURM_JOB_ID; PGS=$S/pgtest/sock_$SLURM_JOB_ID; PGBIN=$S/pgtest/lib/pgserver/pginstall/bin; mkdir -p "$PGS"
run env LC_ALL=C "$PGBIN/initdb" -D "$PGD" --auth=trust -U postgres --encoding=utf8 --locale=C > /dev/null
run env LC_ALL=C "$PGBIN/postgres" -D "$PGD" -k "$PGS" -c listen_addresses= -c fsync=off -c synchronous_commit=off -c full_page_writes=off -c max_connections=200 \
    > "$R/logs/pg_$SLURM_JOB_ID.log" 2>&1 &
PGPID=$!
trap 'kill $PGPID 2>/dev/null || true; rm -rf "$PGD" "$PGS"' EXIT
sleep 10
SING_ENV="$SING_ENV,PGSOCK=$PGS"
echo "job $SLURM_JOB_ID mode=$MODE model=$MODEL tag=$TAG"
case "$MODE" in
  train)
    run torchrun --standalone --nproc_per_node=8 "$APP/train_grpo_sql.py" --base-model "$MODEL" --data "$R/sqlrl/grpo_train" --output-dir "$OUT" \
        --steps "${STEPS:-300}" --lr "${LR:-2e-5}" --deepspeed "$S/pilot-scripts/ds_zero2_pilot.json" 2>&1 | tee "$OUT/train.log" | grep -E "REWARD_TREND|Error|Traceback" || true
    [ -f "$OUT/adapter/adapter_config.json" ] || { echo "no adapter written"; exit 1; }
    run python3 "$APP/train_grpo_sql.py" --merge-only --base-model "$MODEL" --output-dir "$OUT"
    (cd "$OUT/merged" && sha256sum *.safetensors > SHA256SUMS)
    echo "done: $OUT/merged"
    ;;
  eval)
    TESTS_ARGS=""; for t in ${TESTS:-test_s1 test_s3 test_s10 test_s30}; do TESTS_ARGS="$TESTS_ARGS $R/sqlrl/$t.jsonl"; done
    run python3 "$APP/eval_sql_models.py" --model "$MODEL" --tests $TESTS_ARGS --n "${N:-64}" --tp "${TP:-1}" --limit "${LIMIT:-0}" --out "$OUT/eval.json"
    ;;
  *) echo "MODE must be train or eval"; exit 1 ;;
esac
