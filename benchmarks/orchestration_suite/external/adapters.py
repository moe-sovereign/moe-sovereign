#!/usr/bin/env python3
"""Third-party question sets with publicly verified gold answers, converted to the suite's task format.

Every task carries `source` (dataset, split, original id, license, license source) and `final`:
  {"kind": "number", "ref": float, "tol": float}      numeric, scored with rollout_driver.final_number
  {"kind": "text",   "answers": [str, ...], "all": bool}  normalised containment, `all` = every answer must occur
Prompts follow the suite: the data (table / paragraphs) is inline in `prompts_inline`; CypherBench tasks keep `prompts`
free of data because their facts live in the graph (see graph file, loaded by graphrag/load_graph_facts.py --facts).

  adapters.py --root /opt/tmp/ext-datasets --out ext_tasks.jsonl --per-dataset 200 --seed 5
"""
import argparse
import json
import random
import re
from pathlib import Path

LICENSES = {
    "tatqa": ("CC BY 4.0", "NExTplusplus/tat-qa README, 'License' section"),
    "finqa": ("MIT (repository LICENSE; the README makes no separate data statement)", "czyssrs/FinQA LICENSE"),
    "gsm8k": ("MIT", "openai/gsm8k dataset card, 'Licensing Information'"),
    "musique": ("CC BY 4.0", "StonyBrookNLP/musique README"),
    "hotpotqa": ("CC BY-SA 4.0 (ShareAlike: derived question sets must stay CC BY-SA)", "hotpotqa.github.io"),
    "cypherbench": ("Apache-2.0 (dataset card and repository); graph content derives from Wikidata", "megagonlabs/cypherbench dataset card + GitHub license"),
}
SUFFIX = {"number": " Give the final number as the last line.", "text": " Answer briefly."}
_WS = re.compile(r"\s+")


def norm(s: str) -> str:
    s = re.sub(r"\b(a|an|the)\b", " ", s.lower())
    return _WS.sub(" ", re.sub(r"[^\w\s]", " ", s)).strip()


def number(s) -> float | None:
    t = re.sub(r"[^\d.\-]", "", str(s).replace(",", "").replace("−", "-"))
    try:
        return float(t)
    except ValueError:
        return None


def task(ds, split, oid, family, question, inline, final, extra=None):
    prompt = question + SUFFIX[final["kind"]]
    pi = f"{question}\n\n{inline}{SUFFIX[final['kind']]}" if inline else prompt
    return {"id": f"{ds}-{split}-{oid}", "family": family, "lang": "en", "prompts": {"en": prompt, "de": prompt},
            "prompts_inline": {"en": pi, "de": pi}, "final": final,
            "source": {"dataset": ds, "split": split, "id": str(oid), "license": LICENSES[ds][0], "license_source": LICENSES[ds][1]}, **(extra or {})}


def rel_tol(ref: float) -> float:
    return max(0.01, 0.01 * abs(ref))


def table_text(rows) -> str:
    return "\n".join(" | ".join(str(c) for c in r) for r in rows)


def tatqa(path, n, rnd):
    out = []
    for d in json.load(open(path, encoding="utf-8")):
        for q in d["questions"]:
            a = q["answer"]
            if q["answer_type"] not in ("arithmetic", "count") or isinstance(a, list) or not isinstance(a, (int, float)):
                continue
            ctx = "Table:\n" + table_text(d["table"]["table"]) + "\n\nText:\n" + "\n".join(p["text"] for p in d["paragraphs"])
            unit = {"percent": " (answer in percent)", "thousand": " (answer in thousands)", "million": " (answer in millions)", "billion": " (answer in billions)"}.get(q["scale"], "")
            out.append(task("tatqa", "dev", q["uid"], "ext_table_calc", q["question"] + unit, ctx, {"kind": "number", "ref": float(a), "tol": rel_tol(float(a))}))
    return rnd.sample(out, min(n, len(out)))


def finqa(path, n, rnd):
    out = []
    for d in json.load(open(path, encoding="utf-8")):
        qa = d["qa"]
        ref = number(qa["answer"]) if qa.get("answer") and not re.search(r"[a-z]{3,}", str(qa["answer"]).lower()) else None
        if ref is None:
            continue
        ctx = "Text:\n" + " ".join(d["pre_text"] + d["post_text"]) + "\n\nTable:\n" + table_text(d["table"])
        pct = " (answer in percent)" if "%" in str(qa["answer"]) else ""
        out.append(task("finqa", "test", d["id"], "ext_table_calc", qa["question"] + pct, ctx, {"kind": "number", "ref": ref, "tol": rel_tol(ref)}))
    return rnd.sample(out, min(n, len(out)))


def gsm8k(path, n, rnd):
    rows = [json.loads(l) for l in open(path, encoding="utf-8")]
    out = []
    for i, r in enumerate(rows):
        ref = number(r["answer"].split("####")[-1])
        out.append(task("gsm8k", "test", i, "ext_math", r["question"], "", {"kind": "number", "ref": ref, "tol": 0.005}))
    return rnd.sample(out, min(n, len(out)))


def musique(path, n, rnd, distractors=6):
    out = []
    for line in open(path, encoding="utf-8"):
        r = json.loads(line)
        if not r.get("answerable", True) or norm(r["answer"]) in ("yes", "no"):
            continue
        sup = [p for p in r["paragraphs"] if p["is_supporting"]]
        rest = [p for p in r["paragraphs"] if not p["is_supporting"]]
        ps = sup + rnd.sample(rest, min(distractors, len(rest)))
        rnd.shuffle(ps)
        ctx = "\n\n".join(f"[{p['title']}] {p['paragraph_text']}" for p in ps)
        out.append(task("musique", "dev", r["id"], "ext_text_qa", r["question"], ctx, {"kind": "text", "answers": [r["answer"]] + list(r.get("answer_aliases") or []), "all": False}))
    return rnd.sample(out, min(n, len(out)))


def hotpotqa(path, n, rnd):
    import pyarrow.parquet as pq
    out = []
    for r in pq.read_table(path).to_pylist():
        if norm(r["answer"]) in ("yes", "no"):
            continue
        ctx = "\n\n".join(f"[{t}] {''.join(s)}" for t, s in zip(r["context"]["title"], r["context"]["sentences"]))
        out.append(task("hotpotqa", "validation", r["id"], "ext_text_qa", r["question"], ctx, {"kind": "text", "answers": [r["answer"]], "all": False}))
    return rnd.sample(out, min(n, len(out)))


_NOT_RETRIEVABLE = re.compile(r"\b(count|sum|avg|min|max|collect|union|call|with|order|limit|exists|size|case)\b|r\d+\.", re.I)


def snake(s: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", s).upper()


def cypherbench(test_path, graph_paths, n, rnd, max_answers=5):
    """Questions on graphs whose sampled version is the full graph (gold answers stay valid). Only questions whose gold
    Cypher has no aggregation, ordering or relation qualifiers: what is left is answerable from retrieved facts."""
    graphs = {g: json.load(open(p, encoding="utf-8")) for g, p in graph_paths.items()}
    tasks = []
    for r in json.load(open(test_path, encoding="utf-8")):
        if r["graph"] not in graphs or _NOT_RETRIEVABLE.search(r["gold_cypher"].split("RETURN")[0] + " " + r["gold_cypher"].split("RETURN")[-1]):
            continue
        ans = json.loads(r["answer_json"])
        flat = [str(c) for row in ans for c in row]
        if not 1 <= len(ans) <= max_answers or not flat or {x.lower() for x in flat} <= {"true", "false"}:
            continue
        nums = [number(x) for x in flat]
        final = {"kind": "number", "ref": nums[0], "tol": rel_tol(nums[0])} if len(flat) == 1 and nums[0] is not None and re.fullmatch(r"-?[\d.]+", flat[0]) \
            else {"kind": "text", "answers": flat, "all": True}
        t = task("cypherbench", r["graph"], r["qid"], "ext_graph_qa", r["nl_question"], "", final, {"graph_name": r["graph"], "gold_cypher": r["gold_cypher"]})
        t["prompts_inline"] = t["prompts"]  # facts are in the graph only
        tasks.append(t)
    return rnd.sample(tasks, min(n, len(tasks))), {g: graph_facts(d) for g, d in graphs.items()}


def graph_facts(g: dict) -> dict:
    """CypherBench property graph -> the product's GraphRAG schema (Entity nodes, typed relations, value entities)."""
    ents, rels, seen = [], [], set()
    for e in g["entities"]:
        name, label = str(e.get("name") or e["eid"]), e["label"]
        if name in seen:
            continue
        seen.add(name)
        ents.append({"name": name, "type": label, "aliases": [str(x) for x in e.get("aliases") or []][:8]})
        for k, v in (e.get("properties") or {}).items():
            for x in (v if isinstance(v, list) else [v]):
                if x is None or x == "":
                    continue
                ents.append({"name": str(x), "type": "Value"})
                rels.append({"s": name, "r": "HAS_" + snake(k), "o": str(x), "conf": 0.9})
    byid = {e["eid"]: str(e.get("name") or e["eid"]) for e in g["entities"]}
    for r in g["relations"]:
        s, o = byid.get(r["subj_id"]), byid.get(r["obj_id"])
        if s and o:
            rels.append({"s": s, "r": snake(r["label"]), "o": o, "conf": 0.9})
    uniq = {e["name"]: e for e in ents}
    return {"entities": list(uniq.values()), "relations": rels}


def main(a):
    root, rnd = Path(a.root), random.Random(a.seed)
    tasks = tatqa(root / "tatqa/dev.json", a.per_dataset, rnd) + finqa(root / "finqa/test.json", a.per_dataset, rnd) \
        + gsm8k(root / "gsm8k/test.jsonl", a.per_dataset, rnd) + musique(root / "musique/dev.jsonl", a.per_dataset, rnd) \
        + hotpotqa(root / "hotpot/val.parquet", a.per_dataset, rnd)
    cb, facts = cypherbench(root / "cypherbench/test.json", {"nba": root / "cypherbench/nba_sampled.json", "flight_accident": root / "cypherbench/flight_accident_sampled.json"},
                            a.per_dataset, rnd)
    tasks += cb
    with open(a.out, "w", encoding="utf-8") as f:
        for t in tasks:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    json.dump({"entities": [e for g in facts.values() for e in g["entities"]], "relations": [r for g in facts.values() for r in g["relations"]]},
              open(a.graph_out, "w", encoding="utf-8"))
    from collections import Counter
    print(json.dumps({"tasks": len(tasks), "by_dataset": Counter(t["source"]["dataset"] for t in tasks)}))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--graph-out", required=True)
    ap.add_argument("--per-dataset", type=int, default=200)
    ap.add_argument("--seed", type=int, default=5)
    main(ap.parse_args())
