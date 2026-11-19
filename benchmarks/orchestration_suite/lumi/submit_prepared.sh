#!/bin/bash
# Prepared, not yet submitted (2026-11-19). Run on a LUMI login node: bash submit_prepared.sh [sample|router|all]
#
# sample: does voting over N samples close the gap, and is the workflow's diversity the reason?
#   Offline precursor (selfconsistency_curve.py): plurality vote lifts the workflow's planner forks from N=1 to N=8 by +18 points on
#   cypher_calc (0.73 -> 0.92) and +12 on sql_calc (0.31 -> 0.43); the direct model gains +5 from N=1 to N=3 (its samples are nearly
#   identical at temperature 0.7). These runs make both curves comparable on the same held-out tasks at N=8:
#     direct x8 (491 tasks x 8) and workflow x6 more repeats on top of the two existing ones (23795178 -> 8 samples).
# router: product router never queried the graph (retrieved share 0.00): graph confidence of a lookup plan is 0.30 against the
#   threshold 0.35 and the planner category `compounding_knowledge` does not count as graph-heavy. GRAPH_ROUTER_EXTRA_CATEGORIES
#   (default off) adds it. E1 benefit on the lookup tasks, E2 forced router on the held-out tasks with a populated graph (harm
#   check, upper bound), E3 the extra category on the held-out tasks (harm check of E1).
set -euo pipefail
cd /scratch/project_465003058/hornphil/moe-stack
L=app/benchmarks/orchestration_suite/lumi/lumig_moe_stack.slurm
X="--parsable --exclude=nid007890,nid007880,nid007858,nid007881,nid005097,nid005105"
WHAT=${1:-all}
unset DRIVER_ARGS EXTRA_STACK_ENV FORCE_GRAPHRAG
if [ "$WHAT" = sample ] || [ "$WHAT" = all ]; then
    sbatch $X --job-name=moe_direct8 --time=04:00:00 --export=ALL,DIRECT_ONLY=1,TASKS=tasks/orch_tasks_heldout.jsonl,N=491,REPEATS=8,CONC=32 $L           # ~25 card-h
    for i in 1 2 3; do   # 2 repeats each (heldout2 = every task twice): 6 samples, ~15 card-h per job
        sbatch $X --job-name=moe_ho_rep$i --time=06:00:00 --export=ALL,TASKS=tasks/orch_tasks_heldout2.jsonl,N=982,FORKS=0,CONC=32 $L
    done
fi
if [ "$WHAT" = router ] || [ "$WHAT" = all ]; then
    export DRIVER_ARGS="--capture graph_context"
    export EXTRA_STACK_ENV="GRAPH_ROUTER_EXTRA_CATEGORIES=compounding_knowledge+graph_query"
    sbatch $X --job-name=moe_rt_e1 --time=02:30:00 --export=ALL,TASKS=tasks/lookup_simple.jsonl,LOAD_GRAPH=tasks/lookup_simple.jsonl,N=480,OFFSET=120,FORKS=0,CONC=16 $L   # ~6 card-h
    unset DRIVER_ARGS EXTRA_STACK_ENV
    sbatch $X --job-name=moe_rt_e2 --time=06:00:00 --export=ALL,TASKS=tasks/orch_tasks_heldout2.jsonl,LOAD_GRAPH=tasks/lookup_simple.jsonl,N=982,FORKS=0,CONC=32,FORCE_GRAPHRAG=1 $L  # ~15 card-h
    export EXTRA_STACK_ENV="GRAPH_ROUTER_EXTRA_CATEGORIES=compounding_knowledge+graph_query"
    sbatch $X --job-name=moe_rt_e3 --time=06:00:00 --export=ALL,TASKS=tasks/orch_tasks_heldout2.jsonl,LOAD_GRAPH=tasks/lookup_simple.jsonl,N=982,FORKS=0,CONC=32 $L           # ~15 card-h
fi
squeue -u "$USER" -h -o "%i %j %T %R" | sort -k2
