"""Deterministic reconciliation of knowledge facts: which of several values for the same attribute is kept.

Only for *functional* relations, those that have one current value per subject (a capacity, a version, a location). A
non-functional relation (`USES`, `DEPENDS_ON`) legitimately has many objects and is passed through untouched.

Rule, per (subject, relation) with more than one distinct object:
  1. the highest authority wins (verified/curated/ontology > extracted > community import, see graph_rag.manager.fact_authority);
  2. inside the same authority the more recently ingested/confirmed fact wins;
  3. a tie keeps every tied object and reports the conflict as "unresolved".

Recency only breaks ties inside an authority class, because `valid_from` on a Neo4j relation is the time it was last ingested or
confirmed, not when the fact became true: an old verified fact must beat a newer unverified extraction.

Pure functions, nothing is mutated or deleted: the graph keeps every value, the reconciliation only decides which ones are shown.
"""
from __future__ import annotations

import hashlib
import os
import re as _re
from collections import OrderedDict
from datetime import datetime as _dt, timezone as _tz
from typing import Iterable

DEFAULT_FUNCTIONAL_RELATIONS = "HAS_VALUE,HAS_VERSION,HAS_CAPACITY,HAS_YIELD,HAS_BEDS,HAS_PRICE,LOCATED_IN,CEO_OF"


def functional_relations() -> frozenset:
    """Relations with one current value per subject; KNOWLEDGE_FUNCTIONAL_RELATIONS (csv) overrides, empty switches it off."""
    raw = os.getenv("KNOWLEDGE_FUNCTIONAL_RELATIONS", DEFAULT_FUNCTIONAL_RELATIONS)
    return frozenset(r.strip() for r in raw.split(",") if r.strip())


def _stamp(fact: dict) -> int:
    return max(int(fact.get("valid_from_ms") or 0), int(fact.get("created_ms") or 0))


def reconcile_facts(facts: Iterable[dict]) -> tuple[list, list]:
    """Return (kept facts in input order, conflicts).

    A conflict is {"key": "subject|relation", "candidates": [fact ids], "kept": [fact ids], "rule": "authority"|"recency"|"unresolved"}.
    """
    facts = list(facts)
    functional = functional_relations()
    groups: "OrderedDict[tuple, list]" = OrderedDict()
    for i, fact in enumerate(facts):
        if fact.get("relation") in functional:
            groups.setdefault((fact.get("subject"), fact.get("relation")), []).append(i)

    drop: set = set()
    conflicts: list = []
    for (subject, relation), idxs in groups.items():
        by_object: "OrderedDict[str, list]" = OrderedDict()
        for i in idxs:
            by_object.setdefault(str(facts[i].get("object")), []).append(i)
        if len(by_object) < 2:
            continue
        # one representative per object: its strongest fact
        best = {obj: max((facts[i] for i in members), key=lambda f: (int(f.get("authority") or 0), _stamp(f)))
                for obj, members in by_object.items()}
        top_auth = max(int(f.get("authority") or 0) for f in best.values())
        tier = {o: f for o, f in best.items() if int(f.get("authority") or 0) == top_auth}
        newest = max(_stamp(f) for f in tier.values())
        winners = {o: f for o, f in tier.items() if _stamp(f) == newest}
        if len(winners) > 1:
            rule = "unresolved"
        elif len(tier) < len(best):
            rule = "authority"
        else:
            rule = "recency"
        for obj, members in by_object.items():
            if obj not in winners:
                drop.update(members)
        conflicts.append({
            "key": f"{subject}|{relation}",
            "candidates": [facts[i].get("id") for i in idxs],
            "kept": [facts[i].get("id") for obj, members in by_object.items() if obj in winners for i in members],
            "rule": rule,
        })
    return [f for i, f in enumerate(facts) if i not in drop], conflicts


# ---- reconciliation with a knowledge model's answers --------------------------------------------------------------------------
_NUM = _re.compile(r"-?\d[\d.,']*")


def _number(text: str):
    m = _NUM.search(text or "")
    if not m:
        return None
    t = m.group(0).replace("'", "")
    if "," in t and "." in t:
        t = t.replace(",", "") if t.rfind(".") > t.rfind(",") else t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".") if len(t.rsplit(",", 1)[1]) != 3 else t.replace(",", "")
    try:
        return float(t)
    except ValueError:
        return None


def values_agree(a: str, b: str) -> bool:
    """Two answers name the same value: equal numbers, else equal text ignoring case and punctuation, else one contains the other."""
    na, nb = _number(a), _number(b)
    if na is not None and nb is not None:
        return abs(na - nb) <= 1e-9 * max(1.0, abs(na), abs(nb))
    norm = lambda x: _re.sub(r"[^\w]+", " ", (x or "").casefold()).strip()  # noqa: E731
    ca, cb = norm(a), norm(b)
    return bool(ca and cb and (ca == cb or ca in cb or cb in ca))


def _origin_is_model(fact: dict) -> bool:
    """A fact that a model wrote into the graph is not independent evidence against a model's answer."""
    sm = str(fact.get("source_model") or "").casefold()
    return bool(sm) and sm not in {"fixture", "ontology"}


def reconcile_with_llm(facts: Iterable[dict], llm_items: Iterable[dict], *, llm_cutoff=None, min_conf: float = 0.6) -> tuple[list, list]:
    """Compare a knowledge model's answers with the infrastructure facts.

    `llm_items`: {"subject", "relation", "answer", "confidence", "unknown", "question"}; subject/relation are set for verification
    questions (the model is asked for an attribute the graph also holds) and empty for gap questions (nothing in the graph).

    Returns (llm_facts, conflicts): llm_facts are the model answers to show as *unverified model knowledge*; the infrastructure facts
    are never changed here. A model answer that disagrees with an infrastructure fact is kept out of the facts and reported:
      rule "infra_newer_than_cutoff"  the fact was ingested after the model's cutoff, so the model cannot know the current value
      rule "infra_authority"          authority >= 2 (verified/curated/extracted by this system) beats a model answer
      rule "unresolved"               the fact itself came from a model or is a third-party import: both are shown
    A model that is unsure (unknown, or confidence < min_conf) is ignored. Agreement with the infrastructure fact is no conflict.
    """
    facts = list(facts)
    cutoff_ms = int(_dt(llm_cutoff.year, llm_cutoff.month, 1, tzinfo=_tz.utc).timestamp() * 1000) if llm_cutoff else None
    out, conflicts = [], []
    for item in llm_items:
        if item.get("unknown") or float(item.get("confidence") or 0.0) < min_conf or not str(item.get("answer") or "").strip():
            continue
        mine = [f for f in facts if item.get("subject") and f.get("subject") == item["subject"] and f.get("relation") == item.get("relation")]
        if not mine:  # a gap question: the infrastructure knows nothing, the model's answer is shown as what it is
            out.append(_llm_fact(item))
            continue
        if any(values_agree(item["answer"], f.get("object", "")) for f in mine):
            continue
        best = max(mine, key=lambda f: (int(f.get("authority") or 0), _stamp(f)))
        if cutoff_ms is not None and _stamp(best) > cutoff_ms:
            rule = "infra_newer_than_cutoff"
        elif int(best.get("authority") or 0) >= 2 and not _origin_is_model(best):
            rule = "infra_authority"
        else:
            rule = "unresolved"
            out.append(_llm_fact(item))  # shown next to the infrastructure fact, flagged
        conflicts.append({"key": f"{item['subject']}|{item['relation']}", "rule": rule, "infra": best.get("object"),
                          "llm": item["answer"], "kept": [best.get("id")]})
    return out, conflicts


def _llm_fact(item: dict) -> dict:
    return {"id": "llm:" + hashlib.sha1(f"{item.get('question')}|{item.get('answer')}".encode()).hexdigest()[:10], "source": "knowledge_llm",
            "subject": item.get("subject") or "", "relation": item.get("relation") or "", "object": item["answer"],
            "text": f"{item.get('question') or ''} -> {item['answer']}".strip(" ->"), "verified": False, "authority": 0,
            "confidence": float(item.get("confidence") or 0.0), "as_of": item.get("as_of") or "", "origin": "knowledge_llm"}
