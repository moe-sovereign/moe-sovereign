#!/usr/bin/env python3
"""One-shot health check of a running benchmark: progress, errors, audit anomalies, foreign traffic, loaded models.

  python3 benchmarks/run_health_check.py [--minutes 20]
Anomalies worth interrupting the run for are printed with a leading '!!'.
"""
import argparse
import glob
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
MINE = {"c7750e6c49b9": "benchmark"}  # key ids belonging to the benchmark


def psql(sql):
    return subprocess.run(["docker", "exec", "terra_checkpoints", "psql", "-U", "moe_admin", "-d", "moe_userdb", "-At", "-F", " | ", "-c", sql],
                          capture_output=True, text=True).stdout.strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=20)
    m = ap.parse_args().minutes
    now = time.strftime("%H:%M:%S")
    logs = sorted(glob.glob(str(BASE / "results/lumig_spur*_*.log")), key=os.path.getmtime)
    print(f"[{now}] Health check, window {m} min")
    if logs:
        log = logs[-1]
        age = (time.time() - os.path.getmtime(log)) / 60
        lines = [l for l in Path(log).read_text(errors="replace").splitlines() if re.search(r"Task:|Condition:|judged|Score|Traceback|Error", l) and "Deprecat" not in l]
        print(f"log {os.path.basename(log)} (last write {age:.0f} min ago)")
        for l in lines[-6:]:
            print("   ", l[:170])
        if age > 25:
            print("!! log unchanged for more than 25 minutes (stall?)")
    errs = glob.glob(str(BASE / "results/errors_scientific_benchmark_*.jsonl"))
    if errs:
        newest = max(errs, key=os.path.getmtime)
        rows = [json.loads(l) for l in Path(newest).read_text().splitlines() if l.strip()]
        print(f"errors in newest run: {len(rows)}", [(r['condition'], r.get('error_type'), r.get('error_code', '')[:40]) for r in rows[-3:]])
    print("-- audit anomalies")
    slow = psql(f"select to_char(started_at,'HH24:MI:SS'), stage, split_part(model,'/',3), round(extract(epoch from completed_at-started_at)::numeric), coalesce(round((response_body->>'load_duration')::numeric/1e9)::text,'-'), coalesce(response_body->>'eval_count','-') "
                f"from ai_io_audit_log where started_at>now()-interval '{m} minutes' and (extract(epoch from completed_at-started_at)>240 or (response_body->>'load_duration')::numeric>60e9 or (response_body->>'eval_count')::int>4000 or status not in ('completed')) order by started_at desc limit 8")
    for l in slow.splitlines():
        print("!!", "stage|model|s|load_s|tokens:", l)
    if not slow:
        print("none")
    print("-- foreign traffic (orchestrator, other keys)")
    out = subprocess.run(["docker", "logs", "--since", f"{m}m", "langgraph-orchestrator"], capture_output=True, text=True)
    keys = {}
    for l in (out.stdout + out.stderr).splitlines():
        mm = re.search(r"chat-auth-debug ip=(\S+).*key_id=([a-z0-9]+).*model='([^']*)'", l)
        if mm and mm.group(2) not in MINE and not mm.group(3).startswith("refresh-trigger"):
            keys.setdefault((mm.group(2), mm.group(1)), []).append(mm.group(3))
    for (k, ip), ms in keys.items():
        print(f"!! key {k} from {ip}: {len(ms)} requests, e.g. {ms[:2]}")
    if not keys:
        print("none")
    print("-- loaded models")
    for name, url in (("N04-RTX", "192.168.155.224:11434"), ("N04-RGTX", "192.168.155.224:11435")):
        try:
            d = json.load(urllib.request.urlopen(f"http://{url}/api/ps", timeout=5))["models"]
            print(f"   {name}:", [(x["name"].split("/")[-1], x.get("context_length"), f"{x['size_vram']/1e9:.0f}/{x['size']/1e9:.0f}GB") for x in d] or "empty")
        except Exception as e:
            print(f"!! {name} not reachable: {e}")


if __name__ == "__main__":
    main()
