#!/bin/bash
# Upload the working tree of this branch as moe-stack/app_e2 (the end-game experiments; the trees app, app_kb and app_pilot stay untouched
# because queued jobs read them). Extracts over the existing tree (no rm -rf: a running job may be reading it).. Run locally from the repository root. Extra args: files to copy into moe-stack/tasks/.
set -euo pipefail
R=/scratch/project_465003058/hornphil/moe-stack
tar czf - --exclude=.git --exclude=tests --exclude=docs --exclude=__pycache__ --exclude='*.pdf' --exclude='*.png' --exclude=benchmarks/results \
    --exclude=.claude --exclude=node_modules . | ssh lumi-g "mkdir -p $R/app_e2 && tar xzf - -C $R/app_e2"
for f in "$@"; do scp -q "$f" lumi-g:$R/tasks/; done
ssh lumi-g "cd $R/app_e2 && ls scripts/verifier_server.py && du -sh . | tail -1"
