#!/bin/bash
# Score (task, answer) rows with a trained answer verifier on ONE GCD (the 7B model needs ~15 GB in bf16).
#
#   sbatch --time=01:00:00 --export=ALL,MODEL_DIR=$P/runs/verifier7b_s1_23842900/final,ROWS=$P/score/pool.jsonl,OUT=$P/score/pool_scored.jsonl lumig_prm_score.slurm
#
# Input rows: see prm/score_answers.py. Output: {"id", "score"} per row.
#SBATCH --job-name=prm_score
#SBATCH --account=project_465003058
#SBATCH --partition=small-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=1
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=7
#SBATCH --mem=60G
#SBATCH --time=01:00:00
#SBATCH --output=/scratch/project_465003058/hornphil/prm/logs/score_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/prm/logs/score_%j.err

set -euo pipefail
S=/scratch/project_465003058/hornphil
P=$S/prm
SIF=$S/lumi-multitorch-latest.sif
: "${MODEL_DIR:?set MODEL_DIR to a train_prm.py checkpoint (run/final)}" "${ROWS:?set ROWS}" "${OUT:?set OUT}"
mkdir -p "$(dirname "$OUT")" "$P/logs" "$S/cache/triton" "$S/tmp"
ENVS="PYTHONPATH=$S/.user_site,HF_HOME=$S/hf_cache,HF_HUB_OFFLINE=1,XDG_CACHE_HOME=$S/cache,TRITON_CACHE_DIR=$S/cache/triton,TMPDIR=$S/tmp,PYTORCH_HIP_ALLOC_CONF=expandable_segments:True,OMP_NUM_THREADS=7"
echo "job $SLURM_JOB_ID model=$MODEL_DIR rows=$ROWS out=$OUT"
singularity exec --bind "$S:$S" --env "$ENVS" "$SIF" \
    python3 "$P/app/score_answers.py" --model-dir "$MODEL_DIR" --rows "$ROWS" --out "$OUT" --batch "${BATCH:-4}" --max-len "${MAX_LEN:-5120}"
echo "done: $OUT"
