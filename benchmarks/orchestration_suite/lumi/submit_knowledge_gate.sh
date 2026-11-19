#!/bin/bash
# Gate P0 of the knowledge-broker plan: the same tasks with the broker off and on, from a second source tree (app_kb) so that
# queued jobs of other experiments keep running on the old code. Run on a LUMI login node after the tree and task files are
# uploaded (see upload_knowledge_gate.sh):  bash submit_knowledge_gate.sh
#
#   conflicts  60 base tasks x 4 arms (stale_fresh, verified_old, conflict_tie, llm_only), product router      ~6 card-h each
#   lookup     120 base tasks x 4 arms (graph, noisy, empty, inline), product router (retrieved share 0 before)  ~6 card-h each
#   heldout    491 held-out tasks x 2 repeats on a populated graph (harm check)                                  ~8 card-h each
# Compare with: graphrag/compare_runs.py compare --a runs/<off>/rollouts.jsonl --b runs/<on>/rollouts.jsonl --tasks <tasks>
set -euo pipefail
cd /scratch/project_465003058/hornphil/moe-stack
L=app_kb/benchmarks/orchestration_suite/lumi/lumig_moe_stack.slurm
X="--parsable --exclude=nid007890,nid007880,nid007858,nid007881,nid005097,nid005105"
unset DRIVER_ARGS EXTRA_STACK_ENV FORCE_GRAPHRAG
CAP="--capture graph_context,knowledge_bundle,knowledge_block"
for mode in 0 1; do
    tag=$([ "$mode" = 1 ] && echo on || echo off)
    export EXTRA_STACK_ENV="KNOWLEDGE_BROKER_ENABLED=$mode"
    export DRIVER_ARGS="$CAP"
    sbatch $X --job-name=kb_conf_$tag --time=02:30:00 --export=ALL,APP_SUBDIR=app_kb,TASKS=tasks/lookup_conflicts.jsonl,LOAD_GRAPH=tasks/lookup_conflicts.jsonl,N=240,FORKS=0,CONC=16 $L
    sbatch $X --job-name=kb_look_$tag --time=02:30:00 --export=ALL,APP_SUBDIR=app_kb,TASKS=tasks/lookup_simple.jsonl,LOAD_GRAPH=tasks/lookup_simple.jsonl,N=480,OFFSET=120,FORKS=0,CONC=16 $L
    sbatch $X --job-name=kb_held_$tag --time=06:00:00 --export=ALL,APP_SUBDIR=app_kb,TASKS=tasks/orch_tasks_heldout2.jsonl,LOAD_GRAPH=tasks/lookup_simple.jsonl,N=982,FORKS=0,CONC=32 $L
done
squeue -u "$USER" -h -o "%i %j %T %R" | sort -k2
