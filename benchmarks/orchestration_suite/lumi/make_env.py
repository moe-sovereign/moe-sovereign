#!/usr/bin/env python3
"""Derive the research-stack environment from a production .env without carrying secrets.

Only tuning values pass: a key is kept when its name matches no secret/infrastructure pattern
and its value is a number, boolean or short plain token (no URL, host, path or JSON). Endpoints,
credentials and model assignments are then set explicitly for the research stack; runtime
secrets (database/Valkey passwords, SYSTEM_API_KEY) are appended by the job script and never
written here.

  make_env.py --prod-env ../../../.env --out lumi.env [--print-kept]
"""
import argparse
import json
import re
from pathlib import Path

DENY_NAME = re.compile(r"(PASS|SECRET|TOKEN|API_KEY|ACCESS_KEY|KEY_ID|_KEY$|PRIVATE|CREDENTIAL|SMTP|OIDC|AUTHENTIK|LANGFUSE|"
                       r"MINIO|GARAGE|LAKEFS|NIFI|MARQUEZ|^GF_|GRAFANA|JUPYTER|CODEX|SUPERSET|BUDIBASE|HEDGEDOC|OPENROUTER|AIHUB|"
                       r"URL|URI|HOST|PORT|ENDPOINT|DIR$|ROOT|PATH|SOCKET|EMAIL|ADMIN_USER|COMPOSE_|INFERENCE_SERVERS|"
                       r"_MODEL$|_MODELS$|CLAUDE_CODE|HERMES|CPU_LIMIT|INSTALL_|CORS|PUBLIC_|PROMETHEUS|LOG_URL|TZ$)")
PLAIN_VALUE = re.compile(r"^(-?\d+(\.\d+)?|true|false|yes|no|on|off|[a-z][a-z0-9_.:-]{0,30})$", re.I)


def parse_env(path: Path) -> dict:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line.strip())
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def research_env(models: dict, nodes: list[str], shim_url: str) -> dict:
    servers = [{"name": n, "url": shim_url, "api_type": "ollama", "enabled": True, "gpu_count": 1, "timeout": 18000} for n in nodes]
    return {
        "INFERENCE_SERVERS": json.dumps(servers),
        "PLANNER_MODEL": models["planner"], "PLANNER_ENDPOINT": "N04-RGTX",
        "JUDGE_MODEL": models["judge"], "JUDGE_ENDPOINT": "N04-RTX",
        "GRAPH_INGEST_MODEL": models["judge"], "GRAPH_INGEST_ENDPOINT": "N04-RTX",
        "GUARD_MODEL": "",  # no guard model is served on the research stack (benign synthetic tasks)
        "EXPERT_TEMPLATES": "[]",
        "AIHUB_FALLBACK_NODE": "", "AIHUB_FALLBACK_MODEL": "", "AIHUB_FALLBACK_MODEL_SECOND": "",
        "WEB_SEARCH_FALLBACK_DDG": "false", "SEARXNG_URL": "",
        "CORRECTION_MEMORY_ENABLED": "false",   # branches must not learn from each other mid-rollout
        "CONVERSATION_LOG_ENABLED": "false",
        "RESEARCH_ROUTES_ENABLED": "true",
        "INSTALL_ENTERPRISE_DATA_STACK": "false", "INSTALL_LANGFUSE": "false",
        "HABE_SCHEDULER_ENABLED": "false", "GRAPH_DECAY_SCHEDULER_ENABLED": "false", "EURISKO_SCHEDULER_ENABLED": "false",
        "MOE_QUALITY_PROBE": "0",
        "TZ": "Europe/Berlin",
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prod-env", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--shim-url", default="http://127.0.0.1:18100/v1")  # prod URLs end in /v1; native paths strip it
    ap.add_argument("--planner", default="hf.co/h3rb3rn/moe-sovereign-planner-olmo3-7b:Q4_K_M")
    ap.add_argument("--judge", default="hf.co/h3rb3rn/sovereign-judge-olmo31-32b:Q4_K_M")
    ap.add_argument("--nodes", default="N04-RGTX,N04-RTX," + ",".join(f"N02-M60-0{i}" for i in range(2, 10)))
    ap.add_argument("--print-kept", action="store_true")
    a = ap.parse_args()
    prod = parse_env(Path(a.prod_env))
    kept = {k: v for k, v in prod.items() if not DENY_NAME.search(k) and PLAIN_VALUE.match(v or "x")}
    env = kept | research_env({"planner": a.planner, "judge": a.judge}, a.nodes.split(","), a.shim_url)
    with open(a.out, "w", encoding="utf-8") as f:
        for k in sorted(env):
            f.write(f"{k}={json.dumps(env[k]) if k == 'INFERENCE_SERVERS' else env[k]}\n")
    print(f"kept {len(kept)} tuning keys from production, dropped {len(prod) - len(kept)}, wrote {len(env)} keys")
    if a.print_kept:
        print(" ".join(sorted(kept)))


if __name__ == "__main__":
    main()
