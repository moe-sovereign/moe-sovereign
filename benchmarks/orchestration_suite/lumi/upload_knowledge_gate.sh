#!/bin/bash
# Upload the working tree of this branch as moe-stack/app_kb and the conflict tasks. Run locally from the repository root.
set -euo pipefail
R=/scratch/project_465003058/hornphil/moe-stack
tar czf - --exclude=.git --exclude=tests --exclude=docs --exclude=__pycache__ --exclude='*.pdf' --exclude='*.png' --exclude=benchmarks/results \
    --exclude=.claude --exclude=node_modules . | ssh lumi-g "mkdir -p $R/app_kb && tar xzf - -C $R/app_kb"
scp "${1:-/opt/tmp/lumi-rollouts/lookup_conflicts.jsonl}" lumi-g:$R/tasks/
ssh lumi-g "cd $R/app_kb && ls | head -5 && du -sh . | tail -1"
