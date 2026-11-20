#!/usr/bin/env python3
"""Tasks for the planner-interview pilot: when should the planner ask the knowledge model, and what?

Four kinds, each a request plus what the system already knows (the knowledge block the broker would render) and the open gaps:
  public   a public fact (capital, symbol, author, year ...); the system knows nothing -> questions are needed          needs_knowledge
  hop      a two-hop question of the external text-QA set (HotpotQA/MuSiQue)                                              needs_knowledge
  infra    a lookup whose answer is already in the known facts                                                            no questions
  inline   an orchestration task with its data in the request (private to the system), gaps are only names in the data   no questions

Splits are by task (public: by fact key, both languages together): train for trace generation and SFT, test for evaluation only.
The inline test tasks are the held-out orchestration tasks.

  build_tasks.py --ext ext_tasks.jsonl --orch orch_tasks_w3.jsonl --heldout orch_tasks_heldout.jsonl --out-dir interview_data
"""
import argparse
import hashlib
import json
import random
import re
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from facts_public import public_facts  # noqa: E402

STOP = {"what", "which", "who", "whom", "when", "where", "how", "the", "and", "for", "with", "that", "this", "from", "give", "table", "data", "result",
        "answer", "number", "only", "csv", "text", "wie", "was", "wer", "wo", "wann", "nenne", "daten", "tabelle", "ergebnis", "antworte", "nur", "zahl",
        "request", "calculate", "compute", "round", "decimal", "places", "total", "sum", "average", "count", "find", "tell", "name", "are", "is"}
VOCAB = [("capacity", "HAS_CAPACITY", "pallets", ["North", "South", "East", "West", "Harbor", "Central", "Mill"]),
         ("bed count", "HAS_BEDS", "beds", ["Alder", "Birch", "Cedar", "Elm", "Fir", "Maple", "Oak"]),
         ("yield", "HAS_YIELD", "kWh", ["Field", "Roof", "Hall", "Tower", "Shore", "Ridge", "Dune"])]


def bucket(key: str) -> int:
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % 20


def gaps_of(text: str, limit: int = 3) -> list:
    """Capitalised terms of the request: what the broker would report as gaps when the graph has no entity for them."""
    seen, out = set(), []
    for w in re.findall(r"\b[A-ZÄÖÜ][a-zäöüßA-ZÄÖÜ0-9+#'-]{2,}\b", text or ""):
        k = w.lower()
        if k not in STOP and k not in seen:
            seen.add(k)
            out.append(w)
    return out[:limit]


def task(kind, key, request, lang, known, gaps, needs, gold, split):
    return {"id": f"{kind}-{hashlib.sha1(key.encode()).hexdigest()[:10]}-{lang}", "kind": kind, "request": request, "lang": lang,
            "known_facts": known, "gaps": gaps, "needs_knowledge": needs, "gold": gold, "split": split}


def public_tasks() -> list:
    out = []
    for key, qe, qd, ans in public_facts():
        split = "test" if bucket(key) < 5 else "train"
        for lang, q in (("en", qe), ("de", qd)):
            out.append(task("public", key, q, lang, "", gaps_of(q), True, ans.split("|"), split))
    return out


def hop_tasks(ext_path: str) -> list:
    out = []
    for line in open(ext_path, encoding="utf-8"):
        t = json.loads(line)
        if t.get("family") != "ext_text_qa":
            continue
        q = t["prompts"][t["lang"]]
        out.append(task("hop", t["id"], q, t["lang"], "", gaps_of(q), True, [str(a) for a in t["final"]["answers"]],
                        "test" if bucket(t["id"]) < 6 else "train"))
    return out


def infra_tasks(n_train: int, n_test: int, seed: int) -> list:
    rnd, out = random.Random(f"infra-{seed}"), []
    for i in range(n_train + n_test):
        label, rel, unit, stems = rnd.choice(VOCAB)
        name, val = f"{rnd.choice(stems)}{rnd.randint(100, 999)}", rnd.randint(40, 990)
        known = ("[KNOWN FACTS (infrastructure, authoritative first)]\n"
                 f"- {name} {rel} {val} {unit} (verified, fixture, 2026-05-28)")
        out.append(task("infra", f"{seed}-{i}", f"What is the {label} of {name}? Give the number only.", "en", known, [], False, [str(val)],
                        "test" if i >= n_train else "train"))
    return out


def inline_tasks(orch_path: str, heldout_path: str, n_train: int, n_test: int, seed: int) -> list:
    def load(path):
        return [json.loads(l) for l in open(path, encoding="utf-8")]
    rnd, out = random.Random(f"inline-{seed}"), []
    train, test = load(orch_path)[:7200], load(heldout_path)
    rnd.shuffle(train)
    for split, items in (("train", train[:n_train]), ("test", test[:n_test])):
        for t in items:
            prompt = (t.get("prompts_inline") or t["prompts"])[t["lang"]]
            out.append(task("inline", t["id"], prompt[:1500], t["lang"], "", gaps_of(prompt[:1500]), False, [], split))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ext", required=True)
    ap.add_argument("--orch", required=True)
    ap.add_argument("--heldout", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--seed", type=int, default=5)
    a = ap.parse_args()
    tasks = public_tasks() + hop_tasks(a.ext) + infra_tasks(250, 60, a.seed) + inline_tasks(a.orch, a.heldout, 300, 100, a.seed)
    Path(a.out_dir).mkdir(parents=True, exist_ok=True)
    stats = {}
    for split in ("train", "test"):
        rows = [t for t in tasks if t["split"] == split]
        with open(Path(a.out_dir) / f"{split}.jsonl", "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        stats[split] = {k: sum(r["kind"] == k for r in rows) for k in ("public", "hop", "infra", "inline")}
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
