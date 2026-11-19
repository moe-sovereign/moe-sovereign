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

import os
from collections import OrderedDict
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
