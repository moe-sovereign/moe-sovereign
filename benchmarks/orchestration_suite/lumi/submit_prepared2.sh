#!/bin/bash
# Strands H (unseen families), I (verifier seed repeat) and J (release rule on external data) of docs/system/lumi_preregistration_2026-11.md.
# Run on LUMI after upload_prepared2.sh. All three are useful whatever the other strands show: they widen the evidence for the verifier.
set -euo pipefail
S=/scratch/project_465003058/hornphil
cd $S/moe-stack
X="--parsable --exclude=nid007890,nid007880,nid007858,nid007881,nid005097,nid005105,nid005100"
L=app_e2/benchmarks/orchestration_suite/lumi/lumig_moe_stack.slurm
unset DRIVER_ARGS EXTRA_STACK_ENV FORCE_GRAPHRAG
echo "H: unseen families (direct x3, workflow x2)"
sbatch $X --job-name=moe_h4_direct --time=02:00:00 --export=ALL,APP_SUBDIR=app_e2,DIRECT_ONLY=1,TASKS=tasks/orch_tasks_heldout4.jsonl,N=500,REPEATS=3,CONC=32 $L
sbatch $X --job-name=moe_h4_wf --time=05:00:00 --export=ALL,APP_SUBDIR=app_e2,TASKS=tasks/orch_tasks_heldout4x2.jsonl,N=1000,FORKS=0,CONC=32 $L
echo "I: verifier v2, seed 2 (same data and settings as seed 1; v2 seed 1 ran on one node)"
NODES=${NODES:-1}   # job 23874919 ran on one node (4 h 34 min)
sbatch $X --job-name=ver7b_v2_s2 --nodes=$NODES --time=08:00:00 \
  --export=ALL,MODEL=allenai/Olmo-3-7B-Instruct,DATA=$S/prm/data_verifier2,EPOCHS=1,SEED=2,TAG=verifier7b_v2_s2 $S/prm/app/lumig_prm_train.slurm
echo "J: verifier v1 on the external answers of the base-vs-fine-tuned runs (7,490 rows)"
sbatch $X --job-name=ver_sc_ext1 --time=02:00:00 --export=ALL,MODEL_DIR=$S/prm/runs/verifier7b_s1_23842900/final,ROWS=$S/prm/score/pool_ext_new.jsonl,OUT=$S/prm/score/ext_new_v1.jsonl $S/prm/lumig_prm_score.slurm
squeue -u "$USER" -h -o "%i %j %T" | sort -k2 | grep -E "h4|ver7b|ext1"
