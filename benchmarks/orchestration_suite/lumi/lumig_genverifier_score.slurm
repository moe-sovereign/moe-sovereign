#!/bin/bash
#SBATCH --job-name=gv_score
#SBATCH --account=project_465003058
#SBATCH --partition=standard-g
#SBATCH --nodes=1
#SBATCH --gpus-per-node=8
#SBATCH --exclusive
#SBATCH --ntasks-per-node=1
#SBATCH --cpus-per-task=56
#SBATCH --mem=480G
#SBATCH --time=02:00:00
#SBATCH --requeue
#SBATCH --open-mode=append
#SBATCH --output=/scratch/project_465003058/hornphil/moe-stack/logs/gv_score_%j.out
#SBATCH --error=/scratch/project_465003058/hornphil/moe-stack/logs/gv_score_%j.err
# Score the three verifier test sets with the generative verifier (pre-registration D3), one set per GCD in parallel. Env: MODEL (merged dir), TAG.
set -uo pipefail
S=/scratch/project_465003058/hornphil
: "${MODEL:?}" "${TAG:?}"
APP=$S/moe-stack/app_e2/benchmarks/orchestration_suite/prm
OUT=$S/prm/score/gv_$TAG; mkdir -p "$OUT" "$S/tmp" "$S/cache/triton"
ENVB="PYTHONPATH=$S/.user_site,HF_HOME=$S/cache/huggingface,HF_HUB_OFFLINE=1,XDG_CACHE_HOME=$S/cache,TRITON_CACHE_DIR=$S/cache/triton,TMPDIR=$S/tmp"
i=0
for t in test_ext test_iid test_ood; do
    ( SINGULARITYENV_ROCR_VISIBLE_DEVICES=$i singularity exec --bind "$S:$S" --env "$ENVB" $S/lumi-multitorch-latest.sif \
        python3 $APP/score_genverifier.py --model-dir "$MODEL" --rows $S/prm/data_verifier2/$t.jsonl --out "$OUT/$t.jsonl" > "$OUT/$t.log" 2>&1 ) &
    i=$((i + 1))
done
wait
ls -la "$OUT"
