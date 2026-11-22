#!/bin/bash
# All SQL-tool evaluations (pre-registration E) in one whole-node job, in parallel on separate GCDs: two small base models, the three GRPO models
# (pilot, SmolLM3-3B, OLMo-3-7B) and the fine-tuned 32B judge with the same tool (TP 2). One embedded PostgreSQL, one database per process (RANK).
# Replaces six small jobs that waited in the small-g queue for days. Results: $R/rlvr/<TAG>/eval.json.
#SBATCH --job-name=rl_eval_all
#SBATCH --account=project_465003058
#SBATCH --partition=standard-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --exclusive
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=03:00:00
#SBATCH --requeue
#SBATCH --open-mode=append
#SBATCH --output=/scratch/project_465003058/hornphil/moe-stack/logs/rlvr_eval_all_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-stack/logs/rlvr_eval_all_%j.err
set -uo pipefail
S=/scratch/project_465003058/hornphil; R=$S/moe-stack
APP=$R/${APP_SUBDIR:-app_e2}/benchmarks/orchestration_suite/rlvr
SIF=$S/lumi-multitorch-latest.sif
SM=$(ls -d $S/hf_cache/hub/models--HuggingFaceTB--SmolLM3-3B/snapshots/* | head -n 1)
OL=$(ls -d $S/hf_cache/hub/models--allenai--Olmo-3-7B-Instruct/snapshots/* | head -n 1)
JUDGE=$S/checkpoints/merged_olmo31_32b_judge
mkdir -p "$S/tmp" "$S/cache/triton" "$R/logs"
export PYTORCH_HIP_ALLOC_CONF=expandable_segments:True
ENVB="PYTHONPATH=$S/.user_site:$S/pgtest/lib,HF_HOME=$S/cache/huggingface,HF_HUB_OFFLINE=1,XDG_CACHE_HOME=$S/cache,TRITON_CACHE_DIR=$S/cache/triton,TMPDIR=$S/tmp"
PGD=/dev/shm/pg_$SLURM_JOB_ID; PGS=$S/pgtest/sock_$SLURM_JOB_ID; PGBIN=$S/pgtest/lib/pgserver/pginstall/bin; mkdir -p "$PGS"
pgrun() { singularity exec --bind "$S:$S" --env "$ENVB" "$SIF" "$@"; }
pgrun env LC_ALL=C "$PGBIN/initdb" -D "$PGD" --auth=trust -U postgres --encoding=utf8 --locale=C > /dev/null
pgrun env LC_ALL=C "$PGBIN/postgres" -D "$PGD" -k "$PGS" -c listen_addresses= -c fsync=off -c synchronous_commit=off -c full_page_writes=off -c max_connections=200 > "$R/logs/pg_$SLURM_JOB_ID.log" 2>&1 &
PGPID=$!
GUARD=""
trap '[ -n "$GUARD" ] && kill $GUARD 2>/dev/null; kill $PGPID 2>/dev/null; rm -rf "$PGD" "$PGS"' EXIT
sleep 10
# /dev/shm can be wiped by the node a few minutes after the start (see lumig_moe_stack.slurm): requeue instead of failing six evaluations
( while sleep 10; do [ -d "$PGD" ] && continue; echo "GUARD: $PGD vanished"; [ "${SLURM_RESTART_COUNT:-0}" -lt 3 ] && scontrol requeue "$SLURM_JOB_ID"; sleep 60; done ) &
GUARD=$!
ev() {  # tag model gcds rank tp n
    local tag=$1 model=$2 gcds=$3 rank=$4 tp=$5 n=$6
    mkdir -p "$R/rlvr/$tag"
    # a comma in a --env value splits it (GCD lists like 5,6): SINGULARITYENV_ carries the device list instead (job 23926560: the 32B control died on it)
    ( SINGULARITYENV_ROCR_VISIBLE_DEVICES=$gcds singularity exec --bind "$S:$S" --env "$ENVB,PGSOCK=$PGS,RANK=$rank" "$SIF" \
        python3 "$APP/eval_sql_models.py" --model "$model" --tests "$R/sqlrl/test_s1.jsonl" "$R/sqlrl/test_s3.jsonl" "$R/sqlrl/test_s10.jsonl" "$R/sqlrl/test_s30.jsonl" \
        --n "$n" --tp "$tp" --out "$R/rlvr/$tag/eval.json" > "$R/rlvr/$tag/eval.log" 2>&1 ) &
}
ev smollm3_base "$SM" 0 0 1 64
ev olmo7b_base "$OL" 1 1 1 64
ev smollm3_pilot "$R/rlvr/smollm3_pilot/merged" 2 2 1 64
ev smollm3_grpo "$R/rlvr/smollm3_grpo/merged" 3 3 1 64
ev olmo7b_grpo "$R/rlvr/olmo7b_grpo/merged" 4 4 1 64
ev judge32b_tool "$JUDGE" 5,6 5 2 16
wait $(jobs -p | grep -v "^$GUARD$" | grep -v "^$PGPID$") 2>/dev/null
for t in smollm3_base olmo7b_base smollm3_pilot smollm3_grpo olmo7b_grpo judge32b_tool; do echo "$t: $(ls $R/rlvr/$t/eval.json 2>/dev/null || echo MISSING)"; done
