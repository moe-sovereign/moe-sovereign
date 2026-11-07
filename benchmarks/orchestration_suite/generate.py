#!/usr/bin/env python3
"""Procedurally generated multi-step orchestration tasks with execution-verified references.

Each task needs several heterogeneous steps (SQL over a sandbox database, Cypher over a
sandbox graph, deterministic calculation) with explicit dependencies. Prompts state only the
schema; data lives in the per-task sandbox, so a correct answer requires planned tool use.
Every task carries a gold step DAG with reference values so that rollouts can be labelled
per decision step, not only by the final outcome.

Every task is rendered in English and German over the identical sandbox and references
(`prompts.en`, `prompts.de`). `lang`/`user` select the variant used for training; the share of
German is set with --de-share, so the paired variants support a prompt-language ablation.

  generate.py build --n 2000 --seed 1 --de-share 0.3 --out tasks.jsonl
  generate.py selftest --pg-dsn "$ORCH_PG_DSN" --neo-url "$ORCH_NEO_URL" [--n 60]

The selftest executes every gold SQL/Cypher query against real PostgreSQL/Neo4j instances and
checks it against the Python reference. Connection targets come from flags or the
ORCH_PG_DSN / ORCH_NEO_URL environment variables; there are no built-in defaults.
"""
import argparse
import datetime as dt
import hashlib
import itertools
import json
import os
import random
import re
import sys
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

VOCABS = [
    {"name": "logistics", "table": "shipments", "group": "depot", "value": "weight_kg", "day": "ship_date",
     "node": "Depot", "link": "ROUTE", "weight": "km", "groups": ["North", "South", "East", "West", "Central", "Harbor"]},
    {"name": "clinic", "table": "treatments", "group": "ward", "value": "cost_eur", "day": "treatment_date",
     "node": "Ward", "link": "TRANSFER", "weight": "minutes", "groups": ["A1", "A2", "B1", "B2", "C1", "ICU"]},
    {"name": "energy", "table": "readings", "group": "plant", "value": "yield_kwh", "day": "reading_date",
     "node": "Plant", "link": "LINE", "weight": "meters", "groups": ["Field1", "Field2", "Roof", "Hall", "Tower", "Shore"]},
]
FAMILIES = ("sql_calc", "cypher_calc", "cypher_sql_calc", "tool_chain", "direct")
WEIGHTS = {"sql_calc": 3, "cypher_calc": 3, "cypher_sql_calc": 3, "tool_chain": 2, "direct": 1}
SUFFIX = {"en": "\nState the final result explicitly at the end.", "de": "\nNenne das Endergebnis ausdrücklich am Schluss."}


def q2(x, nd=2):
    return float(Decimal(str(x)).quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_UP))


def num(x, lang, nd=2):
    s = f"{x:,.{nd}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".") if lang == "de" else s


def date(d, lang):
    return d.strftime("%d.%m.%Y") if lang == "de" else d.isoformat()


def make_sql(v, rnd):
    start = dt.date(2026, 1, 1) + dt.timedelta(days=rnd.randrange(0, 200))
    rows = [(i, rnd.choice(v["groups"]), rnd.randint(10, 990), (start + dt.timedelta(days=rnd.randrange(0, 60))).isoformat())
            for i in range(1, rnd.randint(14, 24) + 1)]
    ddl = f"CREATE TABLE {v['table']}(id int PRIMARY KEY, {v['group']} text, {v['value']} int, {v['day']} date);"
    ins = f"INSERT INTO {v['table']} VALUES " + ",".join(f"({i},'{g}',{x},'{d}')" for i, g, x, d in rows) + ";"
    cols = f"{v['table']}(id, {v['group']}, {v['value']}, {v['day']})"
    schema = {"en": f"the table {cols}", "de": f"die Tabelle {cols}"}
    return rows, ddl + "\n" + ins, schema


def make_graph(v, rnd):
    names = list(v["groups"])
    edges = {(a, b): rnd.randint(3, 40) for a, b in zip(names, names[1:])}  # a path keeps the graph connected
    for a, b in itertools.combinations(names, 2):
        if (a, b) not in edges and rnd.random() < 0.3:
            edges[(a, b)] = rnd.randint(3, 40)
    nodes = ",".join(f"(`{n}`:{v['node']} {{name:'{n}'}})" for n in names)
    rels = ",".join(f"(`{a}`)-[:{v['link']} {{{v['weight']}:{w}}}]->(`{b}`)" for (a, b), w in edges.items())
    pattern = f"(:{v['node']} {{name}}), (:{v['node']})-[:{v['link']} {{{v['weight']}}}]->(:{v['node']})"
    schema = {"en": f"nodes and relationships {pattern}, to be read as undirected",
              "de": f"Knoten und Beziehungen {pattern}, ungerichtet zu lesen"}
    return names, edges, f"CREATE {nodes}, {rels}", schema


def shortest(names, edges, a, b):
    adj = {n: [] for n in names}
    for (x, y), w in edges.items():
        adj[x].append((y, w))
        adj[y].append((x, w))
    best, todo = {a: 0}, [a]
    while todo:  # small graphs: label-correcting relaxation is exact and sufficient
        n = todo.pop()
        for m, w in adj[n]:
            if best[n] + w < best.get(m, 10 ** 9):
                best[m] = best[n] + w
                todo.append(m)
    return best[b]


def within_hops(names, edges, a, k):
    adj = {n: set() for n in names}
    for x, y in edges:
        adj[x].add(y)
        adj[y].add(x)
    seen, frontier = {a}, {a}
    for _ in range(k):
        frontier = {m for n in frontier for m in adj[n]} - seen
        seen |= frontier
    return sorted(seen - {a})


AGG_SQL = {"SUM": {"en": "the sum of", "de": "die Summe von"}, "MAX": {"en": "the largest value of", "de": "den größten Wert von"},
           "AVG": {"en": "the average of", "de": "den Durchschnitt von"}}


def fam_sql_calc(v, rnd):
    rows, setup, schema = make_sql(v, rnd)
    g = rnd.choice(sorted({r[1] for r in rows}))
    agg = rnd.choice(["SUM", "MAX", "COUNT"])
    days = sorted({r[3] for r in rows})
    since = rnd.choice([None, days[len(days) // 3], days[len(days) // 2]])
    vals = [r[2] for r in rows if r[1] == g and (since is None or r[3] >= since)]
    total = {"SUM": sum(vals), "MAX": max(vals, default=0), "COUNT": len(vals)}[agg]
    p, t = rnd.choice([5, 10, 12, 15, 20, 25]), rnd.choice([7, 19])
    final = q2(total * (1 - p / 100) * (1 + t / 100))
    sd = dt.date.fromisoformat(since) if since else None
    what = {lang: ({"en": "the number of rows", "de": "die Anzahl der Zeilen"}[lang] if agg == "COUNT"
                   else f"{AGG_SQL[agg][lang]} {v['value']}") for lang in ("en", "de")}
    cond = {"en": f" with {v['day']} on or after {date(sd, 'en')}" if sd else "",
            "de": f" mit {v['day']} ab dem {date(sd, 'de')}" if sd else ""}
    user = {
        "en": (f"The database contains {schema['en']}. Determine {what['en']} for {v['group']} = '{g}'{cond['en']}. "
               f"Deduct {p} % from it and then add {t} % to the result. Result to two decimal places?"),
        "de": (f"In der Datenbank liegt {schema['de']}. Ermittle {what['de']} für {v['group']} = '{g}'{cond['de']}. "
               f"Ziehe davon {p} % ab und schlage auf das Ergebnis {t} % auf. Ergebnis auf zwei Nachkommastellen?"),
    }
    expr = "COUNT(*)" if agg == "COUNT" else f"COALESCE({agg}({v['value']}), 0)"
    where = f"{v['group']} = '{g}'" + (f" AND {v['day']} >= '{since}'" if since else "")
    steps = [
        {"id": "s1", "kind": "sql", "depends_on": [], "gold": f"SELECT {expr} FROM {v['table']} WHERE {where}", "ref": total},
        {"id": "s2", "kind": "calc", "depends_on": ["s1"], "gold": f"{total}*(1-{p}/100)*(1+{t}/100)", "ref": final},
    ]
    return user, {"sql": setup}, steps, final


def fam_cypher_calc(v, rnd):
    names, edges, setup, schema = make_graph(v, rnd)
    a, b = rnd.sample(names, 2)
    d = shortest(names, edges, a, b)
    rate, fee = rnd.choice([0.35, 0.42, 1.2, 2.5]), rnd.choice([0, 15, 40])
    final = q2(d * rate + fee)
    user = {
        "en": (f"The graph contains {schema['en']}. Find the minimum total of {v['weight']} along a path from '{a}' to '{b}'. "
               f"Each unit costs {num(rate, 'en')} EUR plus a flat fee of {fee} EUR. Total cost in EUR?"),
        "de": (f"Im Graphen gibt es {schema['de']}. Bestimme die minimale Summe von {v['weight']} auf einem Weg von '{a}' nach '{b}'. "
               f"Jede Einheit kostet {num(rate, 'de')} EUR, dazu kommt eine Pauschale von {fee} EUR. Gesamtkosten in EUR?"),
    }
    gold = (f"MATCH p=(x:{v['node']} {{name:'{a}'}})-[:{v['link']}*]-(y:{v['node']} {{name:'{b}'}}) "
            f"RETURN min(reduce(s=0, r IN relationships(p) | s + r.{v['weight']}))")
    steps = [
        {"id": "s1", "kind": "cypher", "depends_on": [], "gold": gold, "ref": d},
        {"id": "s2", "kind": "calc", "depends_on": ["s1"], "gold": f"{d}*{rate}+{fee}", "ref": final},
    ]
    return user, {"cypher": setup}, steps, final


def fam_cypher_sql_calc(v, rnd):
    names, edges, gsetup, gschema = make_graph(v, rnd)
    rows, ssetup, sschema = make_sql(v, rnd)
    a, k = rnd.choice(names), rnd.choice([1, 2])
    near = within_hops(names, edges, a, k)
    vals = [r[2] for r in rows if r[1] in near]
    agg = rnd.choice(["AVG", "SUM", "MAX"])
    final = {"AVG": q2(sum(vals) / len(vals)) if vals else 0.0, "SUM": float(sum(vals)), "MAX": float(max(vals, default=0))}[agg]
    user = {
        "en": (f"The graph contains {gschema['en']}. The database contains {sschema['en']}. Which {v['node']} nodes can be reached "
               f"from '{a}' in at most {k} step(s), excluding '{a}' itself? For exactly these {v['group']} values compute "
               f"{AGG_SQL[agg]['en']} {v['value']} over all table rows (0 if no row matches), to two decimal places."),
        "de": (f"Im Graphen gibt es {gschema['de']}. In der Datenbank liegt {sschema['de']}. Welche {v['node']}-Knoten sind von '{a}' aus "
               f"in höchstens {k} Schritt(en) erreichbar (ohne '{a}' selbst)? Berechne für genau diese {v['group']}-Werte "
               f"{AGG_SQL[agg]['de']} {v['value']} über alle Zeilen der Tabelle (0, falls keine Zeile passt), auf zwei Nachkommastellen."),
    }
    gold_c = f"MATCH (x:{v['node']} {{name:'{a}'}})-[:{v['link']}*1..{k}]-(y:{v['node']}) WHERE y.name <> '{a}' RETURN DISTINCT y.name ORDER BY y.name"
    in_list = ",".join(f"'{n}'" for n in near) or "''"
    steps = [
        {"id": "s1", "kind": "cypher", "depends_on": [], "gold": gold_c, "ref": near},
        {"id": "s2", "kind": "sql", "depends_on": ["s1"], "gold": f"SELECT COALESCE(ROUND({agg}({v['value']})::numeric, 2), 0) FROM {v['table']} WHERE {v['group']} IN ({in_list})", "ref": final},
        {"id": "s3", "kind": "calc", "depends_on": ["s2"], "gold": "identity", "ref": final},
    ]
    return user, {"cypher": gsetup, "sql": ssetup}, steps, final


def fam_tool_chain(v, rnd):
    d1 = dt.date(2025, 1, 1) + dt.timedelta(days=rnd.randrange(0, 500))
    d2 = d1 + dt.timedelta(days=rnd.randrange(30, 700))
    days = (d2 - d1).days
    k, r = rnd.randrange(2000, 90000, 500), rnd.choice([1.5, 2.25, 3.1, 4.0, 5.75])
    final = q2(k * r / 100 * days / 365)
    user = {
        "en": (f"A principal of {num(k, 'en', 0)} EUR earns simple interest from {date(d1, 'en')} to {date(d2, 'en')} "
               f"(rate {num(r, 'en')} % p.a., act/365, end date not counted). How much interest is earned in EUR?"),
        "de": (f"Ein Kapital von {num(k, 'de', 0)} EUR wird vom {date(d1, 'de')} bis zum {date(d2, 'de')} einfach verzinst "
               f"(Zinssatz {num(r, 'de')} % p. a., Methode act/365, Endtag nicht mitgezählt). Wie hoch sind die Zinsen in EUR?"),
    }
    steps = [
        {"id": "s1", "kind": "calc", "depends_on": [], "gold": f"date_diff({d1.isoformat()},{d2.isoformat()})", "ref": days},
        {"id": "s2", "kind": "calc", "depends_on": ["s1"], "gold": f"{k}*{r}/100*{days}/365", "ref": final},
    ]
    return user, {}, steps, final


def fam_direct(v, rnd):
    a, p = rnd.randrange(120, 9800), rnd.choice([3, 7, 12.5, 17, 33])
    final = q2(a * p / 100)
    user = {"en": f"What is {num(p, 'en', 1)} % of {a}?", "de": f"Wie viel sind {num(p, 'de', 1)} % von {a}?"}
    return user, {}, [{"id": "s1", "kind": "calc", "depends_on": [], "gold": f"{a}*{p}/100", "ref": final}], final


_ROW = re.compile(r"\((\d+),'([^']*)',(\d+),'([\d-]+)'\)")
_EDGE = re.compile(r"\(`([^`]+)`\)-\[:(\w+) \{(\w+):(\d+)\}\]->\(`([^`]+)`\)")
INLINE_HEAD = {"en": "Data for this request:", "de": "Daten zu dieser Anfrage:"}


def inline_data(setup: dict, v: dict) -> str:
    """Render the sandbox contents into the prompt for stacks without SQL/Cypher execution tools."""
    parts = []
    if "sql" in setup:
        rows = _ROW.findall(setup["sql"])
        header = f"id,{v['group']},{v['value']},{v['day']}"
        # no "<name>.csv" label: before the fix after LUMI smoke 23515108, _detect_file_skill took file names in
        # the text for attachments and prepended /xlsx; keeping the neutral label leaves the v2 suite unchanged
        parts.append(f"Table {v['table']} (CSV)\n```csv\n{header}\n" + "\n".join(",".join(r) for r in rows) + "\n```")
    if "cypher" in setup:
        edges = _EDGE.findall(setup["cypher"])
        parts.append(f"{v['link']} edges ({v['weight']})\n```text\n" + "\n".join(f"{a} -- {b}: {w}" for a, _l, _k, w, b in edges) + "\n```")
    return "\n\n".join(parts)


GEN = {"sql_calc": fam_sql_calc, "cypher_calc": fam_cypher_calc, "cypher_sql_calc": fam_cypher_sql_calc,
       "tool_chain": fam_tool_chain, "direct": fam_direct}


def generate(n, seed, de_share=0.3):
    rnd = random.Random(f"orch-{seed}")
    lang_rnd = random.Random(f"orch-lang-{seed}")  # separate stream: changing the share does not change the tasks
    fams = [f for f in FAMILIES for _ in range(WEIGHTS[f])]
    out, seen = [], set()
    while len(out) < n:
        fam, v = rnd.choice(fams), rnd.choice(VOCABS)
        user, setup, steps, final = GEN[fam](v, rnd)
        h = hashlib.sha256((user["en"] + json.dumps(setup, sort_keys=True)).encode()).hexdigest()[:12]
        if h in seen:
            continue
        seen.add(h)
        prompts = {lang: user[lang] + SUFFIX[lang] for lang in ("en", "de")}
        data = inline_data(setup, v)
        prompts_inline = {lang: user[lang] + (f"\n\n{INLINE_HEAD[lang]}\n{data}" if data else "") + SUFFIX[lang] for lang in ("en", "de")}
        lang = "de" if lang_rnd.random() < de_share else "en"
        out.append({"id": f"{fam}-{v['name']}-{h}", "family": fam, "vocab": v["name"], "lang": lang, "user": prompts[lang],
                    "prompts": prompts, "prompts_inline": prompts_inline, "setup": setup, "steps": steps,
                    "final": {"ref": final, "tol": 0.005}})
    return out


def selftest(tasks, pg_dsn, neo_url):
    import psycopg
    import requests

    def neo(stmt):
        r = requests.post(neo_url, json={"statements": [{"statement": stmt}]}, timeout=30).json()
        if r.get("errors"):
            raise RuntimeError(r["errors"][0]["message"][:200])
        res = r["results"][0] if r.get("results") else None
        return [d["row"] for d in res["data"]] if res else []

    labels = {v["node"] for v in VOCABS}
    bad = Counter()
    with psycopg.connect(pg_dsn, autocommit=True) as conn:
        for t in tasks:
            if "cypher" in t["setup"]:
                for lab in labels:  # only wipe suite labels; other data on the instance is left alone
                    neo(f"MATCH (n:{lab}) DETACH DELETE n")
                neo(t["setup"]["cypher"])
            schema = "orch_" + t["id"].rsplit("-", 1)[1]
            if "sql" in t["setup"]:
                conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE; CREATE SCHEMA {schema}; SET search_path TO {schema}")
                conn.execute(t["setup"]["sql"])
            for s in t["steps"]:
                if s["kind"] == "sql":
                    got = conn.execute(s["gold"]).fetchone()[0]
                    ok = abs(float(got or 0) - float(s["ref"])) < 0.006
                elif s["kind"] == "cypher":
                    rows = neo(s["gold"])
                    got = [r[0] for r in rows] if isinstance(s["ref"], list) else rows[0][0]
                    ok = got == s["ref"]
                else:
                    continue
                if not ok:
                    bad[t["family"]] += 1
                    print("MISMATCH", t["id"], s["id"], got, s["ref"], file=sys.stderr)
            if "sql" in t["setup"]:
                conn.execute(f"SET search_path TO public; DROP SCHEMA {schema} CASCADE")
    for lab in labels:
        neo(f"MATCH (n:{lab}) DETACH DELETE n")
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build")
    b.add_argument("--n", type=int, default=2000)
    b.add_argument("--seed", type=int, default=1)
    b.add_argument("--de-share", type=float, default=0.3)
    b.add_argument("--out", required=True)
    s = sp.add_parser("selftest")
    s.add_argument("--n", type=int, default=60)
    s.add_argument("--seed", type=int, default=1)
    s.add_argument("--pg-dsn", default=os.environ.get("ORCH_PG_DSN"))
    s.add_argument("--neo-url", default=os.environ.get("ORCH_NEO_URL"))
    a = ap.parse_args()
    if a.cmd == "build":
        tasks = generate(a.n, a.seed, a.de_share)
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            for t in tasks:
                f.write(json.dumps(t, ensure_ascii=False) + "\n")
        print(json.dumps({"written": len(tasks), "families": Counter(t["family"] for t in tasks),
                          "lang": Counter(t["lang"] for t in tasks)}, ensure_ascii=False))
        return
    if not a.pg_dsn or not a.neo_url:
        sys.exit("selftest needs --pg-dsn/--neo-url or ORCH_PG_DSN/ORCH_NEO_URL")
    bad = selftest(generate(a.n, a.seed), a.pg_dsn, a.neo_url)
    print(json.dumps({"checked": a.n, "mismatches": dict(bad)}))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
