#!/usr/bin/env python3
"""Create the research user, API key and permissions on a fresh research stack and sync to Valkey.

Runs inside the orchestrator container (uses admin_ui.database, like moe-admin does), after the
moe_userdb schema/config snapshot was loaded. The raw API key is written to --key-file (0600)
and never printed.

  python3 bootstrap_research_user.py --key-file /path/research.key --templates tmpl-11f532fc,... \
      --nodes N04-RGTX,N04-RTX,N02-M60-02,...
"""
import argparse
import asyncio
import os
import secrets
import sys

sys.path.insert(0, os.environ.get("MOE_APP_DIR", "/app"))


async def main(a):
    from admin_ui import database as db

    await db.init_db()
    user = await db.get_user_by_username(a.username)
    if user is None:
        user = await db.create_user(a.username, f"{a.username}@research.invalid", secrets.token_urlsafe(24),
                                    display_name="Research rollouts")
    uid = user["id"]
    grants = [("expert_template", t) for t in a.templates.split(",") if t]
    grants += [("model_endpoint", f"*@{n}") for n in a.nodes.split(",") if n]
    grants += [("mcp_tool", "*"), ("moe_mode", "moe-auto"), ("moe_mode", "moe-auto:deliberation-required"),
               ("feature", "templates")]
    for rtype, rid in grants:
        await db.grant_permission(uid, rtype, rid)
    raw, _ = await db.create_api_key(uid, label="research-rollouts")
    fd = os.open(a.key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(raw)
    await db.sync_user_to_redis(uid)
    print(f"research user {a.username} ready: {len(grants)} grants, key written to {a.key_file}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--username", default="research")
    ap.add_argument("--key-file", required=True)
    ap.add_argument("--templates", required=True)
    ap.add_argument("--nodes", required=True)
    asyncio.run(main(ap.parse_args()))
