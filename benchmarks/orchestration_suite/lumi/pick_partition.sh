#!/bin/bash
# Print the partition (small-g or standard-g) on which a job with the given resources is expected to start earlier. Run on LUMI.
#   P=$(bash pick_partition.sh 8)      # GPUs per node; jobs with fewer than 8 GPUs stay on small-g (standard-g allocates whole nodes)
# FORCE_PARTITION=<name> overrides. Rationale: on 2026-11-22 the scheduler estimated the start of a whole-node job 29 hours earlier on standard-g than
# on small-g (small-g: 1,584 pending, ~200 nodes; standard-g: 2,397 pending, ~2,700 nodes), and 25 jobs moved there started within seconds.
# The cost is the same for a whole node. The estimate is only an estimate: look again when the queue has changed.
GPUS=${1:-8}
[ -n "${FORCE_PARTITION:-}" ] && { echo "$FORCE_PARTITION"; exit 0; }
[ "$GPUS" -lt 8 ] && { echo small-g; exit 0; }
T=$(mktemp); printf '#!/bin/bash\n#SBATCH --account=project_465003058\n#SBATCH --nodes=1\n#SBATCH --gpus-per-node=8\n#SBATCH --exclusive\nsleep 1\n' > "$T"
best=small-g; best_t=""
for p in small-g standard-g; do
    t=$(sbatch --test-only --partition=$p --time=${TIME:-03:00:00} "$T" 2>&1 | sed -n 's/.*to start at \([0-9T:-]*\).*/\1/p' | head -n 1)
    [ -z "$t" ] && continue
    if [ -z "$best_t" ] || [[ "$t" < "$best_t" ]]; then best=$p; best_t=$t; fi
done
rm -f "$T"
echo "$best"
