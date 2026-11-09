#!/usr/bin/env python3
"""Graph-only lookup tasks for the GraphRAG ablation (mini wave).

Every base task asks for a number that only exists as facts in the knowledge graph. The same task is
emitted once per arm, with arm-specific entity names so the arms never share graph nodes:
  graph  facts in the graph (product schema), nothing in the prompt
  noisy  as graph, plus stale duplicates of the entities (lower confidence) and a haystack of filler entities
  empty  nothing in the graph, nothing in the prompt: the answer cannot be derived (hallucination / gate control)
  inline facts in the prompt, graph untouched: the reference of the earlier waves

  generate_lookup.py --n 250 --seed 11 --out lookup_tasks.jsonl
"""
import argparse
import hashlib
import json
import random

ARMS = ("graph", "noisy", "empty", "inline")
# (node type, name stem, value relation, unit-free value label, de/en noun)
VOCABS = [
    {"type": "Plant", "stems": ["Field", "Roof", "Hall", "Tower", "Shore", "Ridge", "Dune"], "attr": "HAS_YIELD", "unit": "kWh",
     "en": ("yield", "plants"), "de": ("Ertrag", "Anlagen")},
    {"type": "Depot", "stems": ["North", "South", "East", "West", "Harbor", "Central", "Mill"], "attr": "HAS_CAPACITY", "unit": "pallets",
     "en": ("capacity", "depots"), "de": ("Kapazität", "Lager")},
    {"type": "Ward", "stems": ["Alder", "Birch", "Cedar", "Elm", "Fir", "Maple", "Oak"], "attr": "HAS_BEDS", "unit": "beds",
     "en": ("bed count", "wards"), "de": ("Bettenzahl", "Stationen")},
]
SUFFIX = {"en": " Answer with the final number only if you can verify it.", "de": " Antworte nur mit der Endzahl, wenn du sie belegen kannst."}
FILLER = 3000


def tag(base_id: str, arm: str, i: int) -> str:
    return hashlib.sha256(f"{base_id}-{arm}-{i}".encode()).hexdigest()[:4]


def q2(x: float) -> float:
    return round(x + 1e-9, 2)


def fmt(x: float, lang: str) -> str:
    s = f"{x:.2f}"
    return s.replace(".", ",") if lang == "de" else s


def base_task(rnd: random.Random, k: int, seed: int) -> dict:
    v = rnd.choice(VOCABS)
    kind = rnd.choice(["sum", "hop"])
    # query_context() renders at most two 2-hop facts per entity, so a hub is only solvable with two neighbours
    n = 3 if kind == "hop" else rnd.randint(3, 5)
    stems = rnd.sample(v["stems"], n)
    vals = [rnd.randint(40, 990) for _ in stems]
    bid = f"g{seed}-{k:04d}"
    if kind == "sum":
        pct = rnd.choice([5, 7, 12, 19])
        ref = q2(sum(vals) * (1 + pct / 100))
        chosen = list(range(n))
        return {"base": bid, "vocab": v["type"], "kind": kind, "stems": stems, "vals": vals, "pct": pct, "chosen": chosen, "ref": ref, "attr": v["attr"], "unit": v["unit"],
                "noun": {"en": v["en"], "de": v["de"]}}
    # hop: a hub is connected to the others; ask for the total over everything it is connected to, with a surcharge
    hub = 0
    pct = rnd.choice([5, 7, 12, 19])
    others = list(range(1, n))
    ref = q2(sum(vals[i] for i in others) * (1 + pct / 100))
    return {"base": bid, "vocab": v["type"], "kind": kind, "stems": stems, "vals": vals, "pct": pct, "hub": hub, "chosen": others, "ref": ref,
            "attr": v["attr"], "unit": v["unit"], "noun": {"en": v["en"], "de": v["de"]}}


def names_for(b: dict, arm: str) -> list[str]:
    return [f"{s}{tag(b['base'], arm, i)}{rn}" for i, (s, rn) in enumerate(zip(b["stems"], [str(10 + i * 7) for i in range(len(b["stems"]))]))]


def prompt(b: dict, names: list[str], lang: str, with_data: bool) -> str:
    attr, plural = b["noun"][lang]
    if b["kind"] == "sum":
        listed = ", ".join(names[:-1]) + (" und " if lang == "de" else " and ") + names[-1]
        q = {"en": f"What is the total {attr} of the {plural} {listed}, increased by {b['pct']} %? Result to two decimal places?",
             "de": f"Wie hoch ist die Summe von {attr} der {plural} {listed}, erhöht um {b['pct']} %? Ergebnis auf zwei Nachkommastellen?"}[lang]
    else:
        q = {"en": f"Add up the {attr} of every one of the {plural} that {names[b['hub']]} is connected to, then increase the total by {b['pct']} %. Result to two decimal places?",
             "de": f"Addiere {attr} aller {plural}, mit denen {names[b['hub']]} verbunden ist, und erhöhe die Summe um {b['pct']} %. Ergebnis auf zwei Nachkommastellen?"}[lang]
    if not with_data:
        return q + SUFFIX[lang]
    rows = [f"{n}: {b['vals'][i]} {b['unit']}" for i, n in enumerate(names)]
    links = [f"{names[b['hub']]} -- {names[i]}" for i in b["chosen"]] if b["kind"] == "hop" else []
    head = {"en": "Data for this request:", "de": "Daten zu dieser Anfrage:"}[lang]
    body = "\n".join(rows + links)
    return f"{q}\n\n{head}\n```text\n{body}\n```" + SUFFIX[lang]


def facts(b: dict, names: list[str], noisy: bool, rnd: random.Random) -> dict:
    """Entities and relations in the product schema (Entity nodes, typed relations, value entities)."""
    ents, rels = [], []
    for i, n in enumerate(names):
        ents.append({"name": n, "type": b["vocab"]})
        val = f"{b['vals'][i]} {b['unit']}"
        ents.append({"name": val, "type": "Value"})
        rels.append({"s": n, "r": b["attr"], "o": val, "conf": 0.9})
    if b["kind"] == "hop":
        rels += [{"s": names[b["hub"]], "r": "CONNECTED_TO", "o": names[i], "conf": 0.9} for i in b["chosen"]]
    if noisy:
        for i, n in enumerate(names[: 2 if b["kind"] == "sum" else 1]):  # stale duplicates that still contain the entity name
            old = f"{n} (legacy record)"
            wrong = f"{max(1, b['vals'][i] + rnd.choice([-1, 1]) * rnd.randint(20, 200))} {b['unit']}"
            ents += [{"name": old, "type": b["vocab"]}, {"name": wrong, "type": "Value"}]
            rels.append({"s": old, "r": b["attr"], "o": wrong, "conf": 0.3})
    return {"entities": ents, "relations": rels}


def generate(n: int, seed: int, de_share: float = 0.3) -> list[dict]:
    rnd = random.Random(f"lookup-{seed}")
    lang_rnd = random.Random(f"lookup-lang-{seed}")
    out = []
    for k in range(n):
        b = base_task(rnd, k, seed)
        lang = "de" if lang_rnd.random() < de_share else "en"
        for arm in ARMS:
            names = names_for(b, arm)
            graph = facts(b, names, arm == "noisy", rnd) if arm in ("graph", "noisy") else None
            prompts = {la: prompt(b, names, la, arm == "inline") for la in ("en", "de")}
            out.append({"id": f"lookup_{b['kind']}-{b['base']}-{arm}", "family": f"lookup_{b['kind']}", "arm": arm, "base": b["base"],
                        "vocab": b["vocab"], "lang": lang, "prompts": prompts, "graph": graph, "final": {"ref": b["ref"], "tol": 0.005},
                        "entities": names})
    return out


def filler_entities(seed: int, n: int = FILLER) -> list[dict]:
    """Haystack: unrelated entities in the same schema, shared by the whole run."""
    rnd = random.Random(f"filler-{seed}")
    words = ["Alpha", "Brook", "Crest", "Delta", "Ember", "Forge", "Grove", "Haven", "Iris", "Jade", "Kiln", "Lumen"]
    out = []
    for i in range(n):
        name = f"{rnd.choice(words)}{rnd.randint(1000, 9999)}x{i}"
        out.append({"name": name, "type": rnd.choice(["Plant", "Depot", "Ward", "Person", "Project"])})
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=250)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    tasks = generate(a.n, a.seed)
    with open(a.out, "w", encoding="utf-8") as f:
        for t in tasks:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    print(json.dumps({"written": len(tasks), "base_tasks": a.n, "arms": ARMS}))
