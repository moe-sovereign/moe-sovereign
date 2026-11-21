#!/bin/bash
# Upload the inputs of strands H and J (unseen families, external release analysis). Run locally; needs a valid LUMI certificate.
set -euo pipefail
S=/scratch/project_465003058/hornphil
R=/opt/tmp/lumi-rollouts
scp -q $R/orch_tasks_heldout4.jsonl $R/orch_tasks_heldout4x2.jsonl lumi-g:$S/moe-stack/tasks/
scp -q $R/wave6/pool_ext_new.jsonl lumi-g:$S/prm/score/
echo uploaded
