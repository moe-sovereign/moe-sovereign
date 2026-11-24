#!/usr/bin/env python3
"""Detect changes to the system under test while a benchmark runs.

Records a snapshot at start (orchestrator container id/start time/image, git HEAD, hash of every benchmark template
row) and polls it. Any difference is appended to the log with a timestamp, so a run that was invalidated by a
restart, rebuild or template edit can be recognised afterwards.

Usage: python3 benchmarks/integrity_watch.py <log-file> [interval-seconds]
"""
import datetime
import hashlib
import json
import subprocess
import sys
import time

TEMPLATE_FILTER = "name like 'LUMI-G%' or name like 'Open-Weight%'"


def sh(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


def snapshot():
    rows = sh("docker", "exec", "terra_checkpoints", "psql", "-U", "moe_admin", "-d", "moe_userdb", "-At", "-F", "|", "-c",
              f"select name, md5(config_json) from admin_expert_templates where {TEMPLATE_FILTER} order by name")
    return {
        "container": sh("docker", "inspect", "langgraph-orchestrator", "--format", "{{.Id}} {{.State.StartedAt}} {{.Image}}"),
        "git_head": sh("git", "-C", "/opt/deployment/moe-sovereign/moe-infra", "rev-parse", "HEAD"),
        "templates": dict(line.split("|", 1) for line in rows.splitlines() if "|" in line),
        "env_planner": sh("docker", "exec", "langgraph-orchestrator", "printenv", "PLANNER_MODEL"),
    }


def diff(a, b):
    out = []
    for key in ("container", "git_head", "env_planner"):
        if a[key] != b[key]:
            out.append(f"{key}: {a[key]} -> {b[key]}")
    for name in sorted(set(a["templates"]) | set(b["templates"])):
        if a["templates"].get(name) != b["templates"].get(name):
            out.append(f"template changed/added/removed: {name}")
    return out


def main():
    log = open(sys.argv[1], "a", buffering=1)
    interval = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    now = lambda: datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    base = snapshot()
    log.write(f"{now()} START snapshot {json.dumps(base, sort_keys=True)}\n")
    last = base
    while True:
        time.sleep(interval)
        cur = snapshot()
        changes = diff(last, cur)
        if changes:
            for c in changes:
                log.write(f"{now()} CHANGE {c}\n")
            last = cur


if __name__ == "__main__":
    main()
