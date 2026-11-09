#!/usr/bin/env python3
"""Load the lookup tasks' facts into Neo4j in the product's GraphRAG schema (graph_rag/manager.py):
(:Entity {name, type, source, tenant_id})-[:REL {confidence, source_model, version, verified}]->(:Entity).

Arms `graph` and `noisy` get their facts; every run also gets a haystack of filler entities. Neo4j runs without
auth inside the stack job.

  load_graph_facts.py --tasks lookup_tasks.jsonl [--facts ext_graph.json] --uri bolt://127.0.0.1:7687 [--seed 11]
"""
import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_lookup import FILLER, filler_entities  # noqa: E402

BATCH = 500
_REL = re.compile(r"^[A-Z][A-Z0-9_]*$")


async def run(a):
    from neo4j import AsyncGraphDatabase
    tasks = [json.loads(l) for l in open(a.tasks, encoding="utf-8")] if a.tasks else []
    ents, rels, aliases = {}, [], {}
    if a.facts:
        extra = json.load(open(a.facts, encoding="utf-8"))
        for e in extra["entities"]:
            ents[e["name"]] = e["type"]
            if e.get("aliases"):
                aliases[e["name"]] = " | ".join(e["aliases"])
        rels += extra["relations"]
    for t in tasks:
        if not t.get("graph"):
            continue
        for e in t["graph"]["entities"]:
            ents[e["name"]] = e["type"]
        rels += t["graph"]["relations"]
    for e in filler_entities(a.seed, a.filler):
        ents[e["name"]] = e["type"]
    bad = {r["r"] for r in rels if not _REL.match(r["r"])}
    assert not bad, f"relation types not safe for Cypher: {bad}"
    driver = AsyncGraphDatabase.driver(a.uri, auth=None if not a.password else (a.user, a.password))
    async with driver.session() as s:
        await s.run("CREATE CONSTRAINT entity_name_unique IF NOT EXISTS FOR (e:Entity) REQUIRE e.name IS UNIQUE")
        await s.run("CREATE INDEX entity_type_idx IF NOT EXISTS FOR (e:Entity) ON (e.type)")
        rows = [{"name": n, "type": ty} for n, ty in ents.items()]
        for i in range(0, len(rows), BATCH):
            await s.run("UNWIND $rows AS r MERGE (e:Entity {name: r.name}) ON CREATE SET e.type = r.type, e.source = 'fixture', e.domain = 'lookup'",
                        {"rows": rows[i:i + BATCH]})
        al = [{"name": n, "al": v} for n, v in aliases.items()]
        for i in range(0, len(al), BATCH):  # the product matches terms against name and aliases_str
            await s.run("UNWIND $rows AS r MATCH (e:Entity {name: r.name}) SET e.aliases_str = r.al", {"rows": al[i:i + BATCH]})
        for rel in sorted({r["r"] for r in rels}):
            part = [r for r in rels if r["r"] == rel]
            for i in range(0, len(part), BATCH):
                await s.run(f"""UNWIND $rows AS r MATCH (a:Entity {{name: r.s}}), (b:Entity {{name: r.o}})
                                MERGE (a)-[x:{rel} {{source: 'fixture'}}]->(b)
                                ON CREATE SET x.confidence = r.conf, x.source_model = 'fixture', x.version = 1, x.verified = true,
                                              x.created = timestamp(), x.valid_from = timestamp()""", {"rows": part[i:i + BATCH]})
        count = await (await s.run("MATCH (e:Entity) RETURN count(e) AS n")).single()
    await driver.close()
    print(json.dumps({"entities": count["n"], "relations": len(rels), "filler": a.filler}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tasks", default="", help="lookup tasks whose `graph` facts are loaded (graph and noisy arms)")
    ap.add_argument("--facts", default="", help="extra JSON {entities, relations}, e.g. the CypherBench graph from external/adapters.py")
    ap.add_argument("--uri", default="bolt://127.0.0.1:7687")
    ap.add_argument("--user", default="neo4j")
    ap.add_argument("--password", default="")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--filler", type=int, default=FILLER)
    asyncio.run(run(ap.parse_args()))
