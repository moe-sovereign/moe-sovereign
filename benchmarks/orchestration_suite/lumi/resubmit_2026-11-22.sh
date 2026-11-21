#!/bin/bash
# Resubmission of the stack jobs that died on 2026-11-22 when /dev/shm of their node was wiped (and of two that hit the planner start race with an older script copy).
# Run on LUMI. Whole-node jobs: partition by pick_partition.sh; starts staggered by STEP minutes so that 20 jobs do not read 90 GB each at the same moment.
set -euo pipefail
S=/scratch/project_465003058/hornphil
cd $S/moe-stack
P=$(bash pick_partition.sh 8); STEP=${STEP:-3}; k=0
X="--parsable --exclusive --partition=$P --exclude=nid007890,nid007880,nid007858,nid007881,nid005097,nid005105,nid005100"
L2=app_e2/benchmarks/orchestration_suite/lumi/lumig_moe_stack.slurm
LK=app_kb/benchmarks/orchestration_suite/lumi/lumig_moe_stack.slurm
BASE=$(ls -d $S/hf_cache/hub/models--HuggingFaceTB--SmolLM3-3B/snapshots/* | head -n 1)
V=$S/prm/runs/verifier7b_v2_s1_23874919/final
sub() {  # name time script exports   (env DRIVER_ARGS / EXTRA_STACK_ENV are taken from the caller)
    local id; id=$(sbatch $X --begin=now+$((k * STEP))minutes --job-name=$1 --time=$2 --export=ALL,$4 $3); k=$((k + 1)); echo "$1 $id"
}
unset DRIVER_ARGS EXTRA_STACK_ENV FORCE_GRAPHRAG
echo "partition: $P"
# P0 knowledge-broker gate first (needed for the decision), then the rest by value
CAP="--capture graph_context,knowledge_bundle,knowledge_block"
export DRIVER_ARGS="$CAP"
export EXTRA_STACK_ENV="KNOWLEDGE_BROKER_ENABLED=0"; sub kb_look_off 02:30:00 $LK "APP_SUBDIR=app_kb,TASKS=tasks/lookup_simple.jsonl,LOAD_GRAPH=tasks/lookup_simple.jsonl,N=480,OFFSET=120,FORKS=0,CONC=16"
export EXTRA_STACK_ENV="KNOWLEDGE_BROKER_ENABLED=1"; sub kb_conf_on 02:30:00 $LK "APP_SUBDIR=app_kb,TASKS=tasks/lookup_conflicts.jsonl,LOAD_GRAPH=tasks/lookup_conflicts.jsonl,N=240,FORKS=0,CONC=16"
sub kb_held_on 06:00:00 $LK "APP_SUBDIR=app_kb,TASKS=tasks/orch_tasks_heldout2.jsonl,LOAD_GRAPH=tasks/lookup_simple.jsonl,N=982,FORKS=0,CONC=32"
unset DRIVER_ARGS EXTRA_STACK_ENV
# A4 verifier gate with escalation
export EXTRA_STACK_ENV="VERIFIER_MODE=gate;VERIFIER_ESCALATE=1;VERIFIER_PASS=0.8"
sub moe_ver_gate 06:00:00 $L2 "APP_SUBDIR=app_e2,VERIFIER_CKPT=$V,TASKS=tasks/orch_tasks_heldout2.jsonl,N=982,FORKS=0,CONC=32"
unset EXTRA_STACK_ENV
# B synthesis ablation (best_member is still running)
for m in default judge_solo evidence; do
    export EXTRA_STACK_ENV="MERGER_MODE=$m"; sub moe_mm_$m 06:00:00 $L2 "APP_SUBDIR=app_e2,TASKS=tasks/orch_tasks_heldout2.jsonl,N=982,FORKS=0,CONC=32"
done
unset EXTRA_STACK_ENV
# C experts
sub moe_ex_ho 06:00:00 $L2 "APP_SUBDIR=app_e2,EXPERT_CKPT=$BASE,TASKS=tasks/orch_tasks_heldout2.jsonl,N=982,FORKS=0,CONC=32"
sub moe_ex_ext 04:00:00 $L2 "APP_SUBDIR=app_e2,EXPERT_CKPT=$BASE,TASKS=tasks/ext_tasks.jsonl,N=770,FORKS=0,CONC=32"
# F1 direct (the workflow part runs), H workflow
sub moe_h3_direct 03:00:00 $L2 "APP_SUBDIR=app_e2,DIRECT_ONLY=1,TASKS=tasks/orch_tasks_heldout3.jsonl,N=491,REPEATS=3,CONC=32"
sub moe_h4_wf 05:00:00 $L2 "APP_SUBDIR=app_e2,TASKS=tasks/orch_tasks_heldout4x2.jsonl,N=1000,FORKS=0,CONC=32"
# E inline direct runs at four table scales
for s in 1 3 10 30; do sub moe_sq_s$s 03:00:00 $L2 "APP_SUBDIR=app_e2,DIRECT_ONLY=1,TASKS=tasks/test_s$s.jsonl,N=400,REPEATS=3,CONC=32"; done
# F2 memory write pre-check
sub moe_mem_def 01:30:00 $L2 "APP_SUBDIR=app_e2,POST_STATS=1,TASKS=tasks/orch_tasks_heldout.jsonl,N=60,FORKS=0,CONC=8"
export EXTRA_STACK_ENV="CORRECTION_MEMORY_ENABLED=true"; sub moe_mem_on 01:30:00 $L2 "APP_SUBDIR=app_e2,POST_STATS=1,TASKS=tasks/orch_tasks_heldout.jsonl,N=60,FORKS=0,CONC=8"
unset EXTRA_STACK_ENV
# D1 rationales (vLLM offline, retries in the allocation)
echo "gv_rationales $(sbatch $X --begin=now+$((k * STEP))minutes app_e2/benchmarks/orchestration_suite/lumi/lumig_genverifier_rationales.slurm)"
