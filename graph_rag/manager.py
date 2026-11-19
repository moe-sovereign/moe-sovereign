"""
GraphRAG Manager — manages the Neo4j knowledge graph.

Responsibilities:
  - Build schema & indexes
  - Load base ontology
  - Retrieve context for incoming requests (query_context)
  - Extract and store entities & relations from LLM responses (extract_and_ingest)
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, List, Optional, Set

import httpx
from neo4j import AsyncGraphDatabase, AsyncDriver

from config import (
    GRAPH_INGEST_MODEL, GRAPH_INGEST_URL, GRAPH_INGEST_TOKEN,
    GRAPHRAG_T2C_ENABLED, GRAPHRAG_T2C_TIMEOUT, GRAPHRAG_T2C_MAX_NODES,
    GRAPHRAG_REFORMULATE_ENABLED, GRAPHRAG_REFORMULATE_TIMEOUT,
)

logger = logging.getLogger("MOE-SOVEREIGN.GraphRAG")

# Relation pairs that are logically contradictory (subject → rel → object).
# If (A)-[TREATS]->(B) exists and a new triple (A)-[CAUSES]->(B) arrives → conflict.
_CONTRADICTORY_PAIRS: Dict[str, List[str]] = {
    "TREATS":          ["CAUSES", "CONTRAINDICATES"],
    "CAUSES":          ["TREATS"],
    "CONTRAINDICATES": ["TREATS"],
    # Procedural relations have no strict logical inverses yet.
    "NECESSITATES_PRESENCE": [],
    "DEPENDS_ON_LOCATION":   [],
    "ENABLES_ACTION":        [],
}

# Procedural relation types extracted by the causal learning loop.
_PROCEDURAL_RELS = frozenset({"NECESSITATES_PRESENCE", "DEPENDS_ON_LOCATION", "ENABLES_ACTION"})

# ─── Trust Score & Confidence Decay ─────────────────────────────────────────
# Trust formula: confidence * source_weight * temporal_decay * verification_bonus
# Relations below _TRUST_DELETE_THRESHOLD that are unverified and single-assertion
# are candidates for automatic removal during graph linting.
_SOURCE_WEIGHTS: Dict[str, float] = {
    "ontology": 1.0,
    "ontology_gap_healer": 0.9,
    "extracted": 0.6,
}
_DECAY_PERIOD_DAYS = 365       # Full decay period (1 year)
_DECAY_FLOOR = 0.3             # Minimum temporal multiplier
_TRUST_DELETE_THRESHOLD = 0.2  # Trust below this = candidate for removal
_BLAST_RADIUS_THRESHOLD = 20   # Max connected entities before quarantine

# Domain filter: which entity types each category is allowed to see.
# None = no filter (all types permitted).
_CATEGORY_ENTITY_TYPES: Dict[str, Optional[set]] = {
    "medical_consult":  {"Drug", "Disease", "Symptom", "Treatment", "Anatomy", "Medical_Concept", "Drug_Class"},
    "legal_advisor":    {"Law", "Right", "Legal_Concept", "Organization"},
    "technical_support":{"Framework", "Tool", "Protocol", "Tech_Concept", "DevOps", "Architecture",
                         "Security", "DataStructure", "Algorithm", "Pattern", "Principle",
                         "Action", "Location", "Condition"},
    "code_reviewer":    {"Framework", "Tool", "Tech_Concept", "DataStructure", "Algorithm",
                         "Pattern", "Principle", "Architecture", "Security", "Game_Concept",
                         "Data_Concept", "Action", "Condition"},
    "math":             {"Math_Concept", "Science", "Concept", "Algorithm", "DataStructure"},
    "translation":      {"Language", "Concept", "Narrative"},
    "creative_writer":  {"Narrative", "Concept"},
    "reasoning":        None,
    "general":          None,
    "research":         None,
    "precision_tools":  None,
}

# ─── Text-to-Cypher constants ────────────────────────────────────────────────

# Write-operation pattern — any match aborts query execution.
_T2C_WRITE_PATTERN = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|DETACH\s+DELETE|REMOVE|DROP|ALTER|GRANT|REVOKE|FOREACH"
    r"|CALL\s+\w+\.(write|create|delete|drop|merge|set|remove))\b",
    re.IGNORECASE,
)

# Compact schema fed to the LLM. Kept short to fit in the model's context
# without crowding out the actual query.
_T2C_SCHEMA = """Graph schema:
Nodes: (:Entity {name, type, aliases_str})
Types: Drug|Disease|Symptom|Treatment|Anatomy|Medical_Concept|Drug_Class|
  Law|Right|Legal_Concept|Organization|Framework|Tool|Protocol|Tech_Concept|
  DevOps|Architecture|Security|DataStructure|Algorithm|Pattern|Principle|
  Action|Location|Condition|Math_Concept|Science|Concept|Language|Narrative|Person|AI_Concept
Relations: IS_A|PART_OF|TREATS|CAUSES|INTERACTS_WITH|CONTRAINDICATES|DEFINES|
  REGULATES|USES|IMPLEMENTS|DEPENDS_ON|EXTENDS|RELATED_TO|EQUIVALENT_TO|
  AFFECTS|RUNS|NECESSITATES_PRESENCE|DEPENDS_ON_LOCATION|ENABLES_ACTION"""

_T2C_EXAMPLES = """Examples:
Q: What laws regulate banks?
MATCH (l:Entity)-[:REGULATES]->(o:Entity) WHERE o.type='Organization' AND toLower(o.name) CONTAINS 'bank' RETURN l.name AS entity, l.type, o.name AS target LIMIT 8

Q: Which drugs treat diabetes?
MATCH (d:Entity {type:'Drug'})-[:TREATS]->(c:Entity) WHERE toLower(c.name) CONTAINS 'diabet' RETURN d.name AS drug, c.name AS condition LIMIT 8

Q: What frameworks implement microservices?
MATCH (f:Entity)-[:IMPLEMENTS]->(p:Entity) WHERE toLower(p.name) CONTAINS 'microservice' RETURN f.name AS framework, f.type LIMIT 8"""


def _build_t2c_prompt(query: str, allowed_types: Optional[set]) -> str:
    """Build the Text-to-Cypher prompt with schema, examples, and query."""
    type_hint = ""
    if allowed_types:
        type_hint = f"\nOnly use these entity types for this query: {', '.join(sorted(allowed_types))}"

    return (
        f"{_T2C_SCHEMA}{type_hint}\n\n"
        f"{_T2C_EXAMPLES}\n\n"
        "Rules:\n"
        "- Output ONLY the Cypher MATCH query — no explanation, no markdown\n"
        "- Must start with MATCH\n"
        "- Must contain RETURN and LIMIT (≤ 8 results)\n"
        "- No write operations: CREATE MERGE SET DELETE REMOVE DROP\n\n"
        f"Q: {query}\n"
    )


def _validate_t2c_cypher(cypher: str) -> str:
    """Validate and normalise a generated Cypher query.

    Raises ValueError when the query contains write operations or is malformed.
    Injects a LIMIT clause when missing so unbounded queries never run.
    """
    stripped = cypher.strip()
    if not stripped.upper().startswith("MATCH"):
        raise ValueError(f"T2C: query does not start with MATCH: {stripped[:80]!r}")
    if _T2C_WRITE_PATTERN.search(stripped):
        raise ValueError(f"T2C: write operation detected — rejected: {stripped[:80]!r}")
    if "RETURN" not in stripped.upper():
        raise ValueError(f"T2C: missing RETURN clause: {stripped[:80]!r}")
    # Inject LIMIT when absent to bound result size unconditionally.
    if not re.search(r"\bLIMIT\b", stripped, re.IGNORECASE):
        stripped = stripped.rstrip(";").rstrip() + f" LIMIT {GRAPHRAG_T2C_MAX_NODES}"
    return stripped


def _format_t2c_result(records: list) -> str:
    """Format Neo4j records from a Text-to-Cypher query as a context block."""
    if not records:
        return ""
    lines = ["[Knowledge Graph — Text-to-Cypher]"]
    for rec in records[:GRAPHRAG_T2C_MAX_NODES]:
        parts = []
        for key, val in rec.items():
            if val is not None:
                parts.append(f"{key}: {val}")
        if parts:
            lines.append("• " + " | ".join(parts))
    lines.append("[End Text-to-Cypher]")
    return "\n".join(lines)


# Semaphore limiting concurrent LLM calls during background ingest.
# Created lazily inside the async event loop on first use.
_ingest_semaphore: Optional[asyncio.Semaphore] = None

# ── Fuzzy Entity Name Resolution ─────────────────────────────────────────────
# Minimum SequenceMatcher ratio to accept a candidate as the canonical name.
# 0.82 tolerates case, punctuation, and minor spelling variants while rejecting
# unrelated names (empirically: "Einstein, Albert" ↔ "Albert Einstein" ≈ 0.84).
_FUZZY_ENTITY_THRESHOLD = float(0.82)


def _fuzzy_resolve_entity_name(name: str, candidates: Set[str], threshold: float = _FUZZY_ENTITY_THRESHOLD) -> str:
    """Return the canonical entity name from candidates that best matches `name`.

    Used before Neo4j MERGE to avoid creating duplicate nodes for the same
    real-world entity with slightly different spellings (e.g., "Albert Einstein"
    vs "Einstein, Albert" vs "A. Einstein").

    Mathematical basis:
        Fuzzy numerical attribute matching — tolerance-based similarity with a
        threshold gate. SequenceMatcher implements the Ratcliff/Obershelp
        algorithm (Ratcliff & Metzener 1988), which measures the longest
        common substring iteratively. A match is accepted only when the ratio
        exceeds `threshold`, preventing false positives between short names.

    Args:
        name:       Incoming entity name from LLM extraction.
        candidates: Set of already-known entity names from Neo4j.
        threshold:  Minimum similarity ratio to accept a match (default: 0.82).

    Returns:
        The best matching canonical name if similarity >= threshold, else `name`.
    """
    if name in candidates:
        return name
    name_lower = name.lower()
    best_score, best_candidate = 0.0, name
    for candidate in candidates:
        score = SequenceMatcher(None, name_lower, candidate.lower()).ratio()
        if score > best_score or (score == best_score and len(candidate) < len(best_candidate)):
            best_score, best_candidate = score, candidate
    return best_candidate if best_score >= threshold else name


def _get_ingest_semaphore() -> asyncio.Semaphore:
    """Returns the module-level ingest semaphore, creating it on first call."""
    global _ingest_semaphore
    if _ingest_semaphore is None:
        _ingest_semaphore = asyncio.Semaphore(2)
    return _ingest_semaphore

# Stopwords for term-based entity recognition (no extra LLM call)
_STOPWORDS = {
    "der", "die", "das", "ein", "eine", "ist", "sind", "hat", "haben", "war",
    "wird", "werden", "wurde", "können", "kann", "soll", "muss", "darf",
    "wie", "was", "wer", "wo", "wann", "warum", "welche", "welcher", "welches",
    "und", "oder", "aber", "auch", "nicht", "noch", "nur", "schon", "bereits",
    "in", "an", "auf", "bei", "mit", "von", "zu", "aus", "nach", "über",
    "mir", "ich", "du", "er", "sie", "es", "wir", "ihr", "man", "sich",
    "bitte", "erkläre", "erklärt", "beschreibe", "zeige", "nenne", "liste",
    "the", "a", "an", "is", "are", "was", "what", "how", "why", "and", "or",
    "tell", "me", "about", "explain", "describe", "please", "give",
}


# Relation sources whose facts are authoritative on their own; "community_import" is third-party data, everything else
# (the LLM-extracted default) sits in between.
_AUTHORITATIVE_SOURCES = frozenset({"ontology", "ontology_gap_healer", "fixture", "curated"})


def fact_authority(edge: Dict[str, Any]) -> int:
    """4 = verified or curated, 2 = extracted and unverified, 1 = community import. Higher wins a conflict.

    valid_from is the time a fact was last ingested or confirmed, not when it became true, so recency only breaks ties
    inside one authority class (services/knowledge_reconcile.py)."""
    if edge.get("verified") or edge.get("source") in _AUTHORITATIVE_SOURCES:
        return 4
    if edge.get("source") == "community_import":
        return 1
    return 2


def make_fact(subject: str, edge: Dict[str, Any]) -> Dict[str, Any]:
    """One direct relation of an entity as a JSON-safe KnowledgeFact (plain types only: it ends up in checkpoints)."""
    rel, obj = str(edge.get("rel") or ""), str(edge.get("target") or "")
    conf, trust = edge.get("confidence"), edge.get("trust_score")
    return {
        "id": "g:" + hashlib.sha1(f"{subject}|{rel}|{obj}".encode()).hexdigest()[:12],
        "source": "graph",
        "subject": subject,
        "relation": rel,
        "object": obj,
        "text": f"{subject} {rel} {obj}",
        "valid_from_ms": int(edge["valid_from"]) if edge.get("valid_from") is not None else None,
        "created_ms": int(edge["created"]) if edge.get("created") is not None else None,
        "verified": bool(edge.get("verified")),
        "origin": str(edge.get("source") or "?"),
        "source_model": str(edge.get("source_model") or ""),
        "confidence": float(conf) if conf is not None else None,
        "trust_score": float(trust) if trust is not None else None,
        "version": int(edge["version"]) if edge.get("version") is not None else None,
        "authority": fact_authority(edge),
    }


class GraphRAGManager:

    def __init__(self, uri: str, user: str, password: str):
        # Filter benign server notifications: this is a schema-flexible graph where
        # optional properties (e.g. relation `flagged`, set only by verify/linting)
        # legitimately may not exist yet. Without this filter Neo4j emits an
        # "UNRECOGNIZED: property key does not exist" WARNING on every stats query
        # (every 60 s) plus "constraint already exists" INFO on each startup —
        # pure log noise. We keep all other WARNING-level notifications
        # (PERFORMANCE/DEPRECATION). Degrades gracefully if the driver predates
        # the GQL notification-filter kwargs.
        _notif = {
            "notifications_min_severity": "WARNING",
            "notifications_disabled_classifications": ["UNRECOGNIZED"],
        }
        try:
            self.driver: AsyncDriver = AsyncGraphDatabase.driver(uri, auth=(user, password), **_notif)
        except (TypeError, ValueError):
            self.driver = AsyncGraphDatabase.driver(uri, auth=(user, password))

    # ─── SETUP ───────────────────────────────────────────────────────────────

    async def setup(self) -> None:
        """Build schema, indexes and base ontology."""
        await self._create_schema()
        loaded = await self._load_ontology()
        stats = await self.get_stats()
        logger.info(
            f"✅ GraphRAG initialized — "
            f"{stats['entities']} entities, {stats['relations']} relations "
            f"({loaded} ontology triples loaded)"
        )

    async def _create_schema(self) -> None:
        async with self.driver.session() as session:
            # Constraint: Entity.name must be unique
            await session.run("""
                CREATE CONSTRAINT entity_name_unique IF NOT EXISTS
                FOR (e:Entity) REQUIRE e.name IS UNIQUE
            """)
            # Index on type for fast type filtering
            await session.run("""
                CREATE INDEX entity_type_idx IF NOT EXISTS
                FOR (e:Entity) ON (e.type)
            """)
            # Index on Synthesis.domain for domain-scoped queries
            await session.run("""
                CREATE INDEX synthesis_domain_idx IF NOT EXISTS
                FOR (s:Synthesis) ON (s.domain)
            """)
            # Index on Entity.tenant_id for multi-tenant RBAC filtering
            await session.run("""
                CREATE INDEX entity_tenant_idx IF NOT EXISTS
                FOR (e:Entity) ON (e.tenant_id)
            """)

    async def _load_ontology(self) -> int:
        """Loads the base ontology if not yet present. Returns count of new triples."""
        from .ontology import ONTOLOGY
        loaded = 0
        async with self.driver.session() as session:
            # Create entities (MERGE = idempotent)
            for ent in ONTOLOGY["entities"]:
                await session.run(
                    """
                    MERGE (e:Entity {name: $name})
                    ON CREATE SET
                        e.type        = $type,
                        e.aliases     = $aliases,
                        e.aliases_str = $aliases_str,
                        e.source      = 'ontology'
                    """,
                    {
                        "name": ent["name"],
                        "type": ent["type"],
                        "aliases": ent.get("aliases", []),
                        "aliases_str": " ".join(ent.get("aliases", [])),
                    },
                )

            # Create relations
            for rel in ONTOLOGY["relations"]:
                rel_type = re.sub(r"[^A-Z_]", "", rel["rel"].upper())
                if not rel_type:
                    continue
                result = await session.run(
                    f"""
                    MATCH (a:Entity {{name: $from_name}})
                    MATCH (b:Entity {{name: $to_name}})
                    MERGE (a)-[r:{rel_type} {{source: 'ontology'}}]->(b)
                    ON CREATE SET r.created = timestamp()
                    RETURN r
                    """,
                    {"from_name": rel["from"], "to_name": rel["to"]},
                )
                records = await result.data()
                if records:
                    loaded += 1
        return loaded

    # ─── QUERY ───────────────────────────────────────────────────────────────

    def _resolve_allowed_types(self, categories: List[str]) -> Optional[set]:
        """
        Computes the allowed entity types from a list of plan categories.
        - Categories mapped to None contribute nothing to the filter.
        - Categories with defined type sets are unioned together.
        - If the result is empty (only None-mapped categories) → no filter (all types allowed).
        - If at least one category has a type set → only those types are allowed.
        """
        if not categories:
            return None
        allowed: set = set()
        has_defined = False
        for cat in categories:
            types = _CATEGORY_ENTITY_TYPES.get(cat)
            if types is not None:
                allowed |= types
                has_defined = True
            # None-mapped categories (general, reasoning, precision_tools, …) contribute nothing
        return allowed if has_defined else None

    async def _match_terms_to_entities(
        self,
        terms: List[str],
        allowed_types: Optional[set],
        tenant_ids: Optional[List[str]],
    ) -> Dict[str, Any]:
        """Execute the Neo4j 2-hop term-matching query for a list of search terms.

        Extracted from query_context() so reformulation retries can call the
        same matching logic without duplicating the Cypher.

        Returns:
            Dict mapping entity name → {type, direct, indirect} — empty when
            no terms match any entity in the graph.
        """
        type_filter   = "AND e.type IN $allowed_types" if allowed_types else ""
        tenant_filter = (
            "AND (e.tenant_id IN $tenant_ids OR e.tenant_id IS NULL)"
            if tenant_ids else ""
        )
        found: Dict[str, Any] = {}
        async with self.driver.session() as session:
            # terms[:6] (not [:3]) and LIMIT 10 (not 1, then not 2) per term,
            # [..25]/[..10] (not [..6]/[..4]) on the collected relationships:
            # a well-curated hub entity easily accumulates more than 6
            # REQUIRES-style facts as knowledge is added over time (observed
            # live: 22 facts on one entity after 5 curation rounds) -- the
            # old caps silently dropped most of them, in Neo4j's internal
            # (not relevance-ordered) collection order, so newly curated
            # facts had no reliable chance of ever being retrieved regardless
            # of the model's actual need for them.
            #
            # LIMIT 2 (the first fix) was still not enough: a common domain
            # term like "eBPF" or "XDP" matches 7-15 distinct entities in a
            # graph that accumulates auto-extracted nodes over time (not just
            # curated ones), so a curated hub entity competing against that
            # many same-term matches had no reliable chance of being one of
            # the first 2 in Neo4j's arbitrary order either -- observed live:
            # a Round 10 eBPF fact never reached the prompt because "eBPF and
            # XDP programming" placed outside the top 2 of 7-15 matches for
            # every term that should have found it. Widened further to 10;
            # this only widens what reaches query_context(), the actual
            # prompt-facing selection is relevance-ranked there
            # (_score_relation_relevance) rather than left to arrive in
            # whatever order Neo4j happens to return.
            for term in terms[:6]:
                result = await session.run(
                    f"""
                    MATCH (e:Entity)
                    WHERE (toLower(e.name) CONTAINS toLower($term)
                        OR toLower(e.aliases_str) CONTAINS toLower($term))
                    {type_filter}
                    {tenant_filter}
                    WITH e LIMIT 10
                    OPTIONAL MATCH (e)-[r1]->(n1:Entity)
                    OPTIONAL MATCH (n1)-[r2]->(n2:Entity)
                    RETURN
                        e.name      AS root,
                        e.type      AS root_type,
                        collect(DISTINCT {{
                            rel:          type(r1),
                            target:       n1.name,
                            ttype:        n1.type,
                            source_model: r1.source_model,
                            confidence:   r1.confidence,
                            version:      r1.version,
                            valid_from:   r1.valid_from,
                            created:      r1.created,
                            verified:     r1.verified,
                            trust_score:  r1.trust_score,
                            source:       r1.source,
                            valid_to:     r1.valid_to
                        }})[..25] AS direct,
                        collect(DISTINCT {{
                            via:    n1.name,
                            rel:    type(r2),
                            target: n2.name,
                            ttype:  n2.type
                        }})[..10] AS indirect
                    """,
                    {
                        "term":          term,
                        "allowed_types": list(allowed_types) if allowed_types else [],
                        "tenant_ids":    tenant_ids or [],
                    },
                )
                async for record in result:
                    root = record["root"]
                    if not root:
                        continue
                    if root in found:
                        if term not in found[root]["terms"]:
                            found[root]["terms"].append(term)
                        continue
                    found[root] = {
                        "type":     record["root_type"],
                        "direct":   [r for r in record["direct"]   if r["target"]],
                        "indirect": [r for r in record["indirect"]  if r["target"]],
                        "terms":    [term],  # which request terms led to this entity (the broker's entity hits)
                    }
        return found

    async def _reformulate_query_for_graph(
        self,
        query_text: str,
        allowed_types: Optional[set],
    ) -> List[str]:
        """Generate alternative phrasings of a query for knowledge graph term-matching.

        When term-matching fails (found = {}), the original query may contain
        inflected forms, filler words, or language-specific terms that don't
        appear verbatim in the graph. This method asks a lightweight LLM to
        produce 2 alternative formulations — shorter, with technical synonyms
        and/or English equivalents — which are then retried against Neo4j.

        Returns up to 2 alternative query strings, or [] on any failure.
        """
        if not GRAPHRAG_REFORMULATE_ENABLED:
            return []
        if not GRAPH_INGEST_URL or not GRAPH_INGEST_MODEL:
            return []

        type_hint = (
            f"Focus on these entity types: {', '.join(sorted(allowed_types))}. "
            if allowed_types else ""
        )
        prompt = (
            "Rewrite this knowledge graph search query in exactly 2 shorter alternatives.\n"
            "Rules: extract only key nouns and technical terms; remove filler words; "
            "try English equivalents if the query uses another language; "
            "use known abbreviations (e.g. BAIT, DORA, KWG, BaFin).\n"
            f"{type_hint}"
            "Output exactly 2 lines — one alternative per line, no numbering, no explanation.\n\n"
            f"Query: {query_text.strip()}\n"
        )

        try:
            async with httpx.AsyncClient(timeout=GRAPHRAG_REFORMULATE_TIMEOUT) as client:
                resp = await client.post(
                    f"{GRAPH_INGEST_URL.rstrip('/')}/chat/completions",
                    json={
                        "model":       GRAPH_INGEST_MODEL,
                        "messages":    [{"role": "user", "content": prompt}],
                        "temperature": 0.2,
                        "max_tokens":  80,
                    },
                    headers={"Authorization": f"Bearer {GRAPH_INGEST_TOKEN}"},
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            logger.debug(f"GraphRAG reformulate: LLM call failed: {exc}")
            return []

        alternatives = [
            line.strip().lstrip("0123456789.-) ")
            for line in content.splitlines()
            if line.strip() and len(line.strip()) >= 3
        ][:2]

        logger.debug(f"GraphRAG reformulate: alternatives={alternatives}")
        return alternatives

    async def query_context(
        self,
        query_text: str,
        categories: Optional[List[str]] = None,
        max_hops: int = 2,
        tenant_ids: Optional[List[str]] = None,
    ) -> str:
        """Returns structured graph context for a request.

        Retrieval pipeline (Agentic RAG — iterative fallback):
          1. Term-matching on original query terms (direct Neo4j lookup).
          2. Query Reformulation retry: if step 1 is empty, rephrase the query
             and retry term-matching with each alternative (up to 2 attempts).
          3. Text-to-Cypher fallback: if both steps above return nothing, ask
             an LLM to generate a targeted Cypher MATCH query.

        Filters entity types based on plan categories to prevent domain
        cross-contamination. Optionally filters by tenant_ids (RBAC).
        Shows provenance metadata (source, confidence) for audit traceability.
        """
        terms = self._extract_terms(query_text)
        if not terms:
            return ""

        allowed_types = self._resolve_allowed_types(categories or [])
        if allowed_types:
            logger.debug(f"GraphRAG domain filter active: {allowed_types}")
        if tenant_ids:
            logger.debug(f"GraphRAG tenant filter active: {tenant_ids}")

        # ── Step 1: term-matching on original query ───────────────────────────
        found = await self._match_terms_to_entities(terms, allowed_types, tenant_ids)

        # ── Step 2: Query Reformulation retry ────────────────────────────────
        if not found:
            alternatives = await self._reformulate_query_for_graph(query_text, allowed_types)
            for alt in alternatives:
                alt_terms = self._extract_terms(alt)
                if not alt_terms:
                    continue
                logger.debug(f"GraphRAG reformulate: retrying with {alt!r}")
                found = await self._match_terms_to_entities(alt_terms, allowed_types, tenant_ids)
                if found:
                    logger.info(f"GraphRAG reformulate: hit on alt query {alt!r}")
                    break

        # ── Step 3: Text-to-Cypher fallback ──────────────────────────────────
        if not found:
            return await self._text_to_cypher(query_text, allowed_types)

        found = self._gate_and_cap(terms, found)
        if not found:
            return ""

        return await self._render_found(found, terms)

    def _gate_and_cap(self, terms: List[str], found: Dict[str, Any]) -> Dict[str, Any]:
        """Corrective-RAG relevance gate and the entity cap, shared by query_context() and query_facts()."""
        # ── Corrective RAG Gate (Yan et al., 2024 — arXiv:2401.15884) ─────────
        # Evaluate retrieved entities for relevance before injection.
        # Entities scoring below the threshold are discarded to prevent context
        # pollution — injecting weakly-relevant graph nodes degrades judge quality.
        # Set GRAPHRAG_CORRECTIVE_THRESHOLD=0 to disable (default: 0.15).
        _corrective_threshold = float(os.getenv("GRAPHRAG_CORRECTIVE_THRESHOLD", "0.15"))
        if _corrective_threshold > 0:
            before = len(found)
            found = {
                entity: data for entity, data in found.items()
                if self._corrective_relevance_score(terms, entity, data) >= _corrective_threshold
            }
            discarded = before - len(found)
            if discarded:
                logger.debug(
                    f"GraphRAG corrective gate: discarded {discarded}/{before} entities "
                    f"(threshold={_corrective_threshold:.2f})"
                )
            if not found:
                logger.debug("GraphRAG corrective gate: all results below threshold — returning empty")
                return {}

        # Cap the number of entities actually rendered, ranked by the same
        # relevance score the gate just used -- widening the per-term match
        # limit above (2 -> 10) casts a wider net to make sure a curated hub
        # isn't excluded by sheer number of same-term matches, but without a
        # final cap that same widening could inject dozens of entities into
        # the prompt. Keep the most relevant ones, not just however many
        # survived the threshold.
        _max_entities = int(os.getenv("GRAPHRAG_MAX_ENTITIES", "15"))
        if len(found) > _max_entities:
            found = dict(
                sorted(
                    found.items(),
                    key=lambda kv: self._corrective_relevance_score(terms, kv[0], kv[1]),
                    reverse=True,
                )[:_max_entities]
            )

        return found

    async def _render_found(self, found: Dict[str, Any], terms: List[str]) -> str:
        """The prompt text of the matched entities (output format unchanged since before query_facts())."""
        lines = ["[Knowledge Graph]"]
        for entity, data in found.items():
            etype = data["type"]
            # Rank by relevance to the actual query before truncating, instead
            # of keeping whichever [..25] happened to survive the Neo4j
            # collection order (see _match_terms_to_entities) -- a hub entity
            # can carry far more facts than fit in a prompt, and the ones
            # actually relevant to this request must not lose to older or
            # unrelated ones purely by arrival order.
            rels = sorted(
                data["direct"],
                key=lambda r: self._score_relation_relevance(terms, r),
                reverse=True,
            )[:8]
            indirect = data["indirect"]

            if rels:
                rel_parts = []
                for r in rels:
                    part = f"{r['rel']} {r['target']}"
                    conf = r.get("confidence")
                    src  = r.get("source_model")
                    ver  = r.get("version")
                    # Add provenance hint for low confidence or updated relations
                    if conf is not None and conf < 0.4:
                        part += f" [confidence: {conf:.0%}, source: {src or '?'}]"
                    elif ver is not None and ver > 1:
                        part += f" [v{ver}, source: {src or '?'}]"
                    rel_parts.append(part)
                lines.append(f"• {entity} ({etype}): {' | '.join(rel_parts)}")
            else:
                lines.append(f"• {entity} ({etype})")

            if indirect:
                ind_parts = [
                    f"{r['via']} → {r['rel']} → {r['target']}"
                    for r in indirect[:2]
                    if r["target"]
                ]
                if ind_parts:
                    lines.append(f"  ↳ {' | '.join(ind_parts)}")

        # Targeted procedural traversal: look up requirements for any Action entities found.
        action_names = [e for e, d in found.items() if d.get("type") == "Action"]
        if action_names:
            proc_lines = await self._query_procedural_requirements(action_names)
            if proc_lines:
                lines.append("")
                lines.append("[Procedural Requirements]")
                lines.extend(proc_lines)

        return "\n".join(lines)

    async def query_facts(
        self,
        text: str,
        categories: Optional[List[str]] = None,
        tenant_ids: Optional[List[str]] = None,
        max_facts: int = 40,
    ) -> Dict[str, Any]:
        """Structured counterpart of query_context(): the matched facts with their provenance instead of prose.

        Same terms, domain filter, tenant filter, corrective gate and entity cap as query_context(), but none of its LLM steps
        (no reformulation, no text-to-Cypher): the knowledge broker calls this before planning and must stay cheap.

        Returns a JSON-safe dict:
          terms, searched_terms  - the request terms and the ones that were looked up (the first 6)
          found                  - entity -> {type, direct, indirect, terms}
          entity_hits            - entity -> type
          term_hits              - term -> [entities it led to]; a searched term without a key here is a gap
          facts                  - list of KnowledgeFact dicts, most relevant first, at most max_facts
          rendered               - exactly what query_context() would have returned for these entities
        """
        terms = self._extract_terms(text)
        result: Dict[str, Any] = {"terms": terms, "searched_terms": terms[:6], "found": {}, "entity_hits": {},
                                  "term_hits": {}, "facts": [], "rendered": ""}
        if not terms:
            return result
        allowed_types = self._resolve_allowed_types(categories or [])
        found = await self._match_terms_to_entities(terms, allowed_types, tenant_ids)
        found = self._gate_and_cap(terms, found) if found else {}
        if not found:
            return result
        scored = []
        for entity, data in found.items():
            for edge in data["direct"]:
                if edge.get("valid_to"):  # superseded (nothing writes this yet; honoured once something does)
                    continue
                scored.append((self._score_relation_relevance(terms, edge), entity, edge))
        scored.sort(key=lambda t: t[0], reverse=True)
        result["found"] = found
        result["entity_hits"] = {entity: data["type"] for entity, data in found.items()}
        for entity, data in found.items():
            for term in data.get("terms", []):
                result["term_hits"].setdefault(term, []).append(entity)
        result["facts"] = [make_fact(entity, edge) for _, entity, edge in scored[:max_facts]]
        result["rendered"] = await self._render_found(found, terms)
        return result

    async def _query_procedural_requirements(self, action_names: List[str]) -> List[str]:
        """
        For a list of Action-type entities, fetches all procedural dependencies
        via NECESSITATES_PRESENCE, DEPENDS_ON_LOCATION, and ENABLES_ACTION relations.
        Returns formatted lines ready to append to the graph context block.
        """
        lines: List[str] = []
        async with self.driver.session() as session:
            result = await session.run(
                """
                MATCH (a:Entity)
                WHERE a.name IN $action_names AND a.type = 'Action'
                MATCH (a)-[r:NECESSITATES_PRESENCE|DEPENDS_ON_LOCATION]->(dep:Entity)
                RETURN a.name AS action, type(r) AS rel, dep.name AS dependency,
                       dep.type AS dep_type
                UNION
                MATCH (cond:Entity)-[r:ENABLES_ACTION]->(a:Entity)
                WHERE a.name IN $action_names AND a.type = 'Action'
                RETURN a.name AS action, 'ENABLED_BY' AS rel, cond.name AS dependency,
                       cond.type AS dep_type
                LIMIT 20
                """,
                {"action_names": action_names},
            )
            async for record in result:
                lines.append(
                    f"• {record['action']} {record['rel']} {record['dependency']} "
                    f"({record['dep_type']})"
                )
        return lines

    def _extract_terms(self, text: str) -> List[str]:
        """
        Extracts relevant search terms from the query text without an LLM call.
        Prefers capitalized words (nouns/proper nouns) and longer terms.
        """
        # Capitalized words first (nouns, proper names)
        caps = re.findall(r"\b[A-ZÄÖÜ][a-zäöüßA-ZÄÖÜ0-9+#]{2,}\b", text)
        # All words >= 4 characters
        words = re.findall(r"\b[A-Za-zÄäÖöÜüß]{4,}\b", text)
        seen: set = set()
        result = []
        for w in caps + words:
            key = w.lower()
            if key not in _STOPWORDS and key not in seen:
                seen.add(key)
                result.append(w)
        return result[:10]

    @staticmethod
    def _corrective_relevance_score(
        terms: List[str],
        entity: str,
        data: Dict[str, Any],
    ) -> float:
        """Score the relevance of a retrieved graph entity to the original query.

        Implements the lightweight retrieval evaluator from CRAG (Yan et al., 2024).
        Returns a float in [0.0, 1.0]; entities below GRAPHRAG_CORRECTIVE_THRESHOLD
        are discarded before injection into the judge prompt.

        Args:
            terms:  Query terms extracted by _extract_terms() — content words only,
                    stopwords already removed, up to 10 terms.
            entity: The entity name as stored in Neo4j (e.g. "Python", "BAIT").
            data:   Dict with keys:
                      "type"     — entity type string (e.g. "Framework")
                      "direct"   — list of dicts: {rel, target, ttype, confidence, ...}
                      "indirect" — list of dicts: {via, rel, target, ttype}

        Returns:
            Relevance score in [0.0, 1.0].

        Returns:
            Relevance score in [0.0, 1.0].
        """
        terms_lower = [t.lower() for t in terms]
        if not terms_lower:
            return 1.0  # no terms to evaluate against — pass through

        # Build the textual surface of this entity for term matching.
        # Entity name hit counts double: a query for "BAIT" matching entity "BAIT"
        # is far more signal than a match buried in a relation target.
        name_lower = entity.lower()
        relation_surface = " ".join(
            r["target"].lower() for r in data.get("direct", []) if r.get("target")
        )

        name_hits = sum(1 for t in terms_lower if t in name_lower)
        rel_hits  = sum(1 for t in terms_lower if t in relation_surface)

        # Weighted overlap: name match is worth 2x a relation-target match.
        total_weight = len(terms_lower) * 3  # max possible: all terms hit name (2x) + rels (1x)
        overlap_score = (name_hits * 2 + rel_hits) / total_weight if total_weight else 0.0

        # Average confidence of direct relations (neutral 0.5 when unset).
        confidences = [
            r["confidence"] for r in data.get("direct", [])
            if r.get("confidence") is not None
        ]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.5

        # Entities with no relations at all get a small base score so they are not
        # silently dropped when the graph is sparse on a topic — the term match alone
        # is enough evidence if the overlap is high.
        return min(1.0, overlap_score * 0.75 + avg_confidence * 0.25)

    @staticmethod
    def _score_relation_relevance(terms: List[str], rel: Dict[str, Any]) -> float:
        """Score one entity's direct relation/fact against the query terms, so
        query_context() can keep the most relevant facts when an entity has
        more than fit in the prompt, instead of an arbitrary Neo4j collection
        order silently dropping newer or more relevant ones. Mirrors
        _corrective_relevance_score's term-overlap approach at the
        individual-fact level rather than the whole-entity level.
        """
        target_lower = (rel.get("target") or "").lower()
        if not terms or not target_lower:
            return rel.get("confidence") or 0.5
        terms_lower = [t.lower() for t in terms]
        hits = sum(1 for t in terms_lower if t in target_lower)
        overlap = hits / len(terms_lower)
        confidence = rel.get("confidence")
        confidence = confidence if confidence is not None else 0.5
        return overlap * 0.8 + confidence * 0.2

    async def _text_to_cypher(
        self,
        query: str,
        allowed_types: Optional[set],
    ) -> str:
        """Generate and execute a Cypher query from natural language.

        Called when term-matching returns no entities. Uses the configured
        GRAPH_INGEST_MODEL to translate the natural-language query into a
        read-only Cypher MATCH statement, validates it, and executes it.

        Returns a formatted context block, or "" on any failure (including
        LLM unavailability, Cypher validation failure, or empty results).
        All errors are logged at DEBUG level — this is a best-effort fallback.
        """
        if not GRAPHRAG_T2C_ENABLED:
            return ""
        if not GRAPH_INGEST_URL or not GRAPH_INGEST_MODEL:
            logger.debug("T2C: GRAPH_INGEST_ENDPOINT/MODEL not configured — skipping")
            return ""

        prompt = _build_t2c_prompt(query, allowed_types)

        # ── LLM call ─────────────────────────────────────────────────────────
        cypher_raw = ""
        try:
            async with httpx.AsyncClient(timeout=GRAPHRAG_T2C_TIMEOUT) as client:
                resp = await client.post(
                    f"{GRAPH_INGEST_URL.rstrip('/')}/chat/completions",
                    json={
                        "model":       GRAPH_INGEST_MODEL,
                        "messages":    [{"role": "user", "content": prompt}],
                        "temperature": 0.0,
                        "max_tokens":  256,
                    },
                    headers={"Authorization": f"Bearer {GRAPH_INGEST_TOKEN}"},
                )
                resp.raise_for_status()
                cypher_raw = resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            logger.debug(f"T2C: LLM call failed: {exc}")
            return ""

        # ── Extract bare Cypher (strip markdown fences if present) ───────────
        # Some models wrap their output in ```cypher ... ``` blocks.
        fence_match = re.search(r"```(?:cypher)?\s*(MATCH.+?)```", cypher_raw, re.S | re.I)
        cypher_raw = fence_match.group(1).strip() if fence_match else cypher_raw
        # Take first MATCH statement when model outputs multiple lines.
        for line in cypher_raw.splitlines():
            if line.strip().upper().startswith("MATCH"):
                cypher_raw = line.strip()
                break

        # ── Validate ─────────────────────────────────────────────────────────
        try:
            cypher = _validate_t2c_cypher(cypher_raw)
        except ValueError as exc:
            logger.debug(f"T2C: validation failed — {exc}")
            return ""

        logger.info(f"T2C: executing — {cypher[:120]}")

        # ── Execute ───────────────────────────────────────────────────────────
        try:
            async with self.driver.session() as session:
                result = await session.run(cypher)
                records = [dict(rec) async for rec in result]
        except Exception as exc:
            logger.debug(f"T2C: Neo4j execution failed: {exc}")
            return ""

        ctx = _format_t2c_result(records)
        if ctx:
            logger.info(f"T2C: returned {len(records)} record(s), {len(ctx)} chars")
        else:
            logger.debug("T2C: query executed but returned no records")
        return ctx

    # ─── INGEST ──────────────────────────────────────────────────────────────

    async def _check_conflict(
        self, session: Any, s_name: str, rel_type: str, o_name: str
    ) -> Optional[str]:
        """
        Checks whether a new triple contradicts an existing one.
        Returns conflict description or None if no conflict.
        """
        contradictory = _CONTRADICTORY_PAIRS.get(rel_type, [])
        if not contradictory:
            return None
        for contra in contradictory:
            result = await session.run(
                f"MATCH (a:Entity {{name: $s}})-[:{contra}]->(b:Entity {{name: $o}}) "
                "RETURN count(*) AS cnt",
                {"s": s_name, "o": o_name},
            )
            record = await result.single()
            if record and record["cnt"] > 0:
                return f"({s_name})-[{contra}]->({o_name}) exists, conflicts with [{rel_type}]"
        return None

    async def extract_and_ingest(
        self,
        question: str,
        answer: str,
        llm: Any,
        domain: Optional[str] = None,
        source_model: str = "unknown",
        confidence: float = 0.5,
        knowledge_type: str = "factual",
        expert_domain: str = "",
        tenant_id: Optional[str] = None,
        redis_client: Any = None,
    ) -> str:
        """
        Extracts entities and relations from an LLM response via a judge LLM
        and stores them in the graph. Called as a background task (fire & forget).
        Provenance metadata (source_model, confidence, valid_from, version) is
        stored on each relation for temporal traceability.

        Returns the detected knowledge_type ('factual' or 'procedural') so callers
        can log or act on the classification.
        """
        extract_prompt = (
            "Extract entities and relationships from the following text as a compact JSON array.\n"
            "Only factually confirmed statements! Maximum 8 triples.\n\n"
            "Allowed relation types:\n"
            "  IS_A, PART_OF, TREATS, CAUSES, INTERACTS_WITH, CONTRAINDICATES,\n"
            "  DEFINES, REGULATES, USES, IMPLEMENTS, DEPENDS_ON, EXTENDS,\n"
            "  RELATED_TO, EQUIVALENT_TO, AFFECTS, RUNS,\n"
            "  NECESSITATES_PRESENCE, DEPENDS_ON_LOCATION, ENABLES_ACTION\n\n"
            "Allowed entity types:\n"
            "  Drug, Disease, Symptom, Treatment, Anatomy, Medical_Concept,\n"
            "  Law, Right, Legal_Concept, Language, Framework, Tool, Protocol,\n"
            "  AI_Concept, Tech_Concept, Math_Concept, Science, Concept, Person,\n"
            "  Organization, Action, Location, Condition\n\n"
            "IMPORTANT — also extract procedural world-rules:\n"
            "  If the text implies that performing an action requires physical presence\n"
            "  at a location, use NECESSITATES_PRESENCE (Action → Location).\n"
            "  If a prerequisite condition or resource enables an action, use\n"
            "  ENABLES_ACTION (Condition → Action). Maximum 4 procedural triples.\n\n"
            'Format: [{"s":"Name","s_type":"Type","r":"RELATION","o":"Name","o_type":"Type"}]\n\n'
            f"Text:\n{answer[:1800]}\n\n"
            "JSON array (ONLY the array, no explanatory text):"
        )

        try:
            async with _get_ingest_semaphore():
                result = await llm.ainvoke(extract_prompt)
            match = re.search(r"\[.*?\]", result.content, re.S)
            if not match:
                return

            triples = json.loads(match.group())
            if not triples or not isinstance(triples, list):
                return

            # Auto-detect knowledge_type from extracted triples
            detected_type = (
                "procedural"
                if any(t.get("r", "").upper() in _PROCEDURAL_RELS for t in triples)
                else knowledge_type
            )

            ingested = 0
            conflicts = 0
            async with self.driver.session() as session:
                # Pre-load a prefix-based entity name index for fuzzy dedup.
                # Collects names sharing the same first 3 chars as any incoming
                # entity. One batched query per ingest session avoids per-triple
                # round-trips to Neo4j.
                _incoming_names = {
                    str(t.get("s", ""))[:100].strip() for t in triples[:8]
                } | {
                    str(t.get("o", ""))[:100].strip() for t in triples[:8]
                }
                _prefixes = {n[:3].upper() for n in _incoming_names if len(n) >= 3}
                _known_entity_names: Set[str] = set()
                if _prefixes:
                    try:
                        _idx_result = await session.run(
                            "MATCH (e:Entity) WHERE substring(toUpper(e.name), 0, 3) IN $pfx "
                            "RETURN e.name AS name",
                            {"pfx": list(_prefixes)},
                        )
                        _known_entity_names = {r["name"] async for r in _idx_result}
                    except Exception:
                        pass

                for triple in triples[:8]:
                    if not all(k in triple for k in ("s", "s_type", "r", "o", "o_type")):
                        continue
                    rel_type = re.sub(r"[^A-Z_]", "", str(triple["r"]).upper())
                    if not rel_type:
                        continue
                    s_name = str(triple["s"])[:100].strip()
                    o_name = str(triple["o"])[:100].strip()
                    if not s_name or not o_name:
                        continue

                    # Fuzzy entity deduplication: resolve incoming names to
                    # canonical existing names before MERGE to prevent duplicates
                    # caused by minor spelling differences across knowledge sources.
                    s_name = _fuzzy_resolve_entity_name(s_name, _known_entity_names) or s_name
                    o_name = _fuzzy_resolve_entity_name(o_name, _known_entity_names) or o_name

                    # Conflict check before saving
                    conflict = await self._check_conflict(session, s_name, rel_type, o_name)
                    if conflict:
                        logger.warning(f"⚠️ GraphRAG conflict skipped: {conflict}")
                        conflicts += 1
                        continue

                    # Blast-radius check: quarantine high-impact triples
                    if redis_client:
                        reach = await self._estimate_blast_radius(session, s_name, o_name)
                        if reach > _BLAST_RADIUS_THRESHOLD:
                            q_entry = json.dumps({
                                "s": s_name, "s_type": str(triple["s_type"]),
                                "r": rel_type,
                                "o": o_name, "o_type": str(triple["o_type"]),
                                "reach": reach, "question": question[:200],
                                "domain": domain, "source_model": source_model,
                                "confidence": confidence,
                                "tenant_id": tenant_id,
                                "ts": datetime.now(timezone.utc).isoformat(),
                            })
                            try:
                                await redis_client.zadd("moe:quarantine", {q_entry: time.time()})
                                await redis_client.expire("moe:quarantine", 7 * 86400)
                            except Exception:
                                pass
                            logger.warning(
                                f"⚠️ GraphRAG quarantined: ({s_name})-[{rel_type}]->({o_name}) "
                                f"reach={reach} > {_BLAST_RADIUS_THRESHOLD}"
                            )
                            continue

                    _merge_result = await session.run(
                        f"""
                        MERGE (a:Entity {{name: $s_name}})
                        ON CREATE SET a.type = $s_type, a.source = 'extracted',
                                      a.domain = $domain, a.expert_domain = $expert_domain,
                                      a.tenant_id = $tenant_id
                        MERGE (b:Entity {{name: $o_name}})
                        ON CREATE SET b.type = $o_type, b.source = 'extracted',
                                      b.domain = $domain, b.expert_domain = $expert_domain,
                                      b.tenant_id = $tenant_id
                        MERGE (a)-[r:{rel_type} {{source: 'extracted'}}]->(b)
                        ON CREATE SET
                            r.created       = timestamp(),
                            r.valid_from    = timestamp(),
                            r.from_q        = $question,
                            r.verified      = false,
                            r.domain        = $domain,
                            r.expert_domain = $expert_domain,
                            r.source_model  = $source_model,
                            r.confidence    = $confidence,
                            r.version       = 1
                        ON MATCH SET
                            r.prev_source_model  = r.source_model,
                            r.prev_confidence    = r.confidence,
                            r.superseded_version = r.version,
                            r.version            = coalesce(r.version, 0) + 1,
                            r.source_model       = $source_model,
                            r.confidence         = $confidence,
                            r.valid_from         = timestamp()
                        RETURN r.version AS v, r.prev_confidence AS pc,
                               r.prev_source_model AS prev_model
                        """,
                        {
                            "s_name":        s_name,
                            "s_type":        str(triple["s_type"])[:50],
                            "o_name":        o_name,
                            "o_type":        str(triple["o_type"])[:50],
                            "question":      question[:200],
                            "domain":        domain or "unknown",
                            "expert_domain": expert_domain,
                            "source_model":  source_model,
                            "confidence":    confidence,
                            "tenant_id":     tenant_id,
                        },
                    )
                    _merge_rows = await _merge_result.data()
                    if _merge_rows:
                        _row = _merge_rows[0]
                        _ver = _row.get("v") or 1
                        _prev_conf = _row.get("pc")
                        # Paraconsistent conflict detection: when an existing
                        # relation is updated (v > 1) and its confidence
                        # flips by more than _GRAPH_CONFLICT_DELTA, the two
                        # propositions are structurally contradictory and must
                        # be preserved — not silently overwritten.
                        # de Vries (2007), §2: paraconsistent tolerance.
                        _GRAPH_CONFLICT_DELTA = 0.30
                        if (
                            _ver > 1
                            and _prev_conf is not None
                            and abs(float(_prev_conf) - confidence) >= _GRAPH_CONFLICT_DELTA
                            and redis_client is not None
                        ):
                            _conflict_entry = json.dumps({
                                "category":        domain or expert_domain or "unknown",
                                "triple":          f"({s_name})-[{rel_type}]->({o_name})",
                                "prev_confidence": round(float(_prev_conf), 3),
                                "new_confidence":  round(confidence, 3),
                                "prev_model":      _row.get("prev_model", ""),
                                "new_model":       source_model,
                                "version":         _ver,
                                "resolution":      "pending",
                                "ts":              datetime.now(timezone.utc).isoformat(),
                            })
                            try:
                                await redis_client.zadd(
                                    "moe:graph_conflict_log",
                                    {_conflict_entry: time.time()},
                                )
                                await redis_client.expire("moe:graph_conflict_log", 30 * 86400)
                            except Exception:
                                pass
                            logger.info(
                                f"⚖️ Graph conflict logged: ({s_name})-[{rel_type}]->({o_name})"
                                f" conf {_prev_conf:.2f}→{confidence:.2f} v{_ver}"
                            )
                    ingested += 1

            if ingested or conflicts:
                logger.info(
                    f"GraphRAG ingest: {ingested} saved, {conflicts} conflicts skipped "
                    f"[{detected_type}]"
                )
            return detected_type

        except json.JSONDecodeError:
            logger.debug("GraphRAG ingest: no valid JSON extracted")
        except Exception as e:
            logger.warning(f"GraphRAG ingest error: {e}")
        return knowledge_type

    # ─── STATS & SEARCH ──────────────────────────────────────────────────────

    async def get_stats(self) -> Dict[str, int]:
        """Returns entity, relation, synthesis node, and flagged relation counts."""
        async with self.driver.session() as session:
            result = await session.run("""
                MATCH (e:Entity)   WITH count(e) AS entities
                MATCH ()-[r]->()   WITH entities, count(r) AS relations
                OPTIONAL MATCH (s:Synthesis) WITH entities, relations, count(s) AS synthesis_nodes
                OPTIONAL MATCH ()-[fr]->() WHERE coalesce(fr.flagged, false) = true
                RETURN entities, relations, synthesis_nodes,
                       count(fr) AS flagged_relations
            """)
            record = await result.single()
            if record:
                return {
                    "entities":          record["entities"],
                    "relations":         record["relations"],
                    "synthesis_nodes":   record["synthesis_nodes"] or 0,
                    "flagged_relations": record["flagged_relations"] or 0,
                }
        return {"entities": 0, "relations": 0, "synthesis_nodes": 0, "flagged_relations": 0}

    async def search_entities(self, term: str, limit: int = 10) -> List[Dict]:
        """Simple entity search — useful for debugging & admin."""
        async with self.driver.session() as session:
            result = await session.run(
                """
                MATCH (e:Entity)
                WHERE toLower(e.name) CONTAINS toLower($term)
                   OR toLower(e.aliases_str) CONTAINS toLower($term)
                RETURN e.name AS name, e.type AS type, e.source AS source
                LIMIT $limit
                """,
                {"term": term, "limit": limit},
            )
            return [dict(r) async for r in result]

    async def get_provenance(self, entity_name: str) -> List[Dict]:
        """
        Returns all relations of an entity with complete version history.
        Useful for debugging, admin UI and contradiction analysis.
        """
        async with self.driver.session() as session:
            result = await session.run(
                """
                MATCH (a:Entity {name: $name})-[r]->(b:Entity)
                RETURN
                    type(r)            AS relation,
                    b.name             AS target,
                    r.source_model     AS source_model,
                    r.confidence       AS confidence,
                    r.version          AS version,
                    r.valid_from       AS valid_from,
                    r.prev_source_model AS prev_source_model,
                    r.prev_confidence   AS prev_confidence,
                    r.superseded_version AS superseded_version,
                    r.verified         AS verified,
                    r.flagged          AS flagged,
                    r.domain           AS domain
                ORDER BY r.valid_from DESC
                """,
                {"name": entity_name},
            )
            return [dict(r) async for r in result]

    async def mark_triples_unverified(self, question: str) -> int:
        """Marks all triples extracted from a question as unverified and flagged (negative feedback)."""
        async with self.driver.session() as session:
            result = await session.run(
                "MATCH ()-[r {from_q: $q}]->() "
                "SET r.verified = false, r.flagged = true "
                "RETURN count(r) AS cnt",
                {"q": question[:200]},
            )
            record = await result.single()
            return record["cnt"] if record else 0

    async def verify_triples(self, question: str) -> int:
        """Marks all triples extracted from a question as verified (positive feedback)."""
        async with self.driver.session() as session:
            result = await session.run(
                "MATCH ()-[r {from_q: $q}]->() "
                "SET r.verified = true, r.flagged = false "
                "RETURN count(r) AS cnt",
                {"q": question[:200]},
            )
            record = await result.single()
            return record["cnt"] if record else 0

    # ─── GRAPH LINTING ───────────────────────────────────────────────────────

    # ─── TRUST SCORE & SELF-HEALING ────────────────────────────────────────────

    async def compute_trust_scores(self, batch_size: int = 500) -> int:
        """
        Recomputes trust_score on all relations.
        Formula: trust = confidence * source_weight * temporal_decay * verification_bonus
        Returns count of relations updated.
        """
        updated = 0
        now_ts = time.time() * 1000  # Neo4j timestamp() is milliseconds
        try:
            async with self.driver.session() as session:
                result = await session.run("""
                    MATCH ()-[r]->()
                    RETURN id(r) AS rid,
                           r.confidence AS conf,
                           r.source AS src,
                           r.valid_from AS vf,
                           r.verified AS verified
                    LIMIT $batch
                """, {"batch": batch_size})
                rows = [dict(r) async for r in result]

            for row in rows:
                conf = row.get("conf") or 0.5
                src = row.get("src") or "extracted"
                vf = row.get("vf") or now_ts
                verified = bool(row.get("verified"))

                source_w = _SOURCE_WEIGHTS.get(src, 0.6)
                days_old = max(0, (now_ts - vf) / (86400 * 1000))
                # Static (ontology) knowledge is permanent — no temporal decay.
                if src in ("ontology", "ontology_gap_healer"):
                    decay = 1.0
                else:
                    decay = max(_DECAY_FLOOR, 1.0 - (days_old / _DECAY_PERIOD_DAYS))
                v_bonus = 1.5 if verified else 1.0
                trust = min(1.0, conf * source_w * decay * v_bonus)

                async with self.driver.session() as session:
                    await session.run(
                        "MATCH ()-[r]->() WHERE id(r) = $rid SET r.trust_score = $score",
                        {"rid": row["rid"], "score": round(trust, 4)},
                    )
                updated += 1

            logger.info(f"Trust scores recomputed for {updated} relations")
        except Exception as exc:
            logger.warning(f"Trust score computation failed: {exc}")
        return updated

    async def _estimate_blast_radius(
        self, session: Any, s_name: str, o_name: str
    ) -> int:
        """
        Counts distinct entities reachable within 2 hops from subject OR object.
        Used to decide whether a new triple should be quarantined.
        """
        try:
            result = await session.run("""
                OPTIONAL MATCH (a:Entity {name: $s})-[*1..2]-(n1:Entity)
                WITH collect(DISTINCT n1.name) AS s_neighbors
                OPTIONAL MATCH (b:Entity {name: $o})-[*1..2]-(n2:Entity)
                WITH s_neighbors + collect(DISTINCT n2.name) AS all_n
                UNWIND all_n AS n
                RETURN count(DISTINCT n) AS reach
            """, {"s": s_name, "o": o_name})
            record = await result.single()
            return record["reach"] if record else 0
        except Exception:
            return 0

    async def run_graph_linting(
        self, llm: Any, kafka_publish_fn: Optional[Callable] = None,
    ) -> Dict[str, int]:
        """
        Background janitor: removes orphaned nodes, resolves contradictions, and
        sweeps decayed low-trust relations.

        Phase 1: Delete Entity nodes that have no relationships (orphans), up to 50 per run.
        Phase 2: For each unique contradictory relationship pair in _CONTRADICTORY_PAIRS,
                 find same-subject conflicts, ask the LLM which to keep, then flag the loser.
        Phase 3: Confidence decay sweep — recompute trust scores and delete relations
                 with trust < 0.2 that are unverified and never re-confirmed (version=1).
        Throttled via _ingest_semaphore to avoid VRAM contention with active requests.

        Returns:
            dict with 'orphans_deleted', 'conflicts_resolved', and 'decay_deleted' counts.
        """
        logger.info("🧹 Graph-Linting: starting")

        # ── Phase 1: Orphan cleanup ─────────────────────────────────────────
        orphans_deleted = 0
        try:
            async with self.driver.session() as session:
                result = await session.run("""
                    MATCH (e:Entity)
                    WHERE NOT (e)--()
                    WITH e LIMIT 50
                    DELETE e
                    RETURN count(e) AS deleted
                """)
                record = await result.single()
                orphans_deleted = record["deleted"] if record else 0
            if orphans_deleted:
                logger.info(f"🧹 Graph-Linting orphans: {orphans_deleted} nodes deleted")
        except Exception as exc:
            logger.warning(f"Graph-Linting orphan phase failed: {exc}")

        # ── Phase 2: Conflict detection & LLM resolution ────────────────────
        seen_pairs: set = set()
        conflicts_resolved = 0

        for rel_a, contradicts in _CONTRADICTORY_PAIRS.items():
            for rel_b in contradicts:
                # Deduplicate symmetric pairs (TREATS/CAUSES == CAUSES/TREATS)
                pair_key = frozenset([rel_a, rel_b])
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                try:
                    async with self.driver.session() as session:
                        result = await session.run(
                            f"""
                            MATCH (a:Entity)-[r1:{rel_a}]->(x:Entity)
                            WHERE (a)-[:{rel_b}]->(x)
                              AND (r1.flagged IS NULL OR r1.flagged = false)
                            MATCH (a)-[r2:{rel_b}]->(x)
                            WHERE (r2.flagged IS NULL OR r2.flagged = false)
                            RETURN a.name      AS subject,
                                   x.name      AS target,
                                   r1.confidence AS conf_a,
                                   r2.confidence AS conf_b,
                                   r1.source_model AS model_a,
                                   r2.source_model AS model_b
                            LIMIT 10
                            """
                        )
                        rows = [dict(r) async for r in result]
                except Exception as exc:
                    logger.warning(f"Graph-Linting conflict query [{rel_a}/{rel_b}] failed: {exc}")
                    continue

                for row in rows:
                    subject = row["subject"]
                    target  = row["target"]
                    conf_a  = row.get("conf_a", 0.5)
                    conf_b  = row.get("conf_b", 0.5)
                    model_a = row.get("model_a", "unknown")
                    model_b = row.get("model_b", "unknown")

                    prompt = (
                        f"Two contradictory facts are stored about the same entity:\n"
                        f"  (1) ({subject})-[{rel_a}]->({target})  "
                        f"[confidence={conf_a}, model={model_a}]\n"
                        f"  (2) ({subject})-[{rel_b}]->({target})  "
                        f"[confidence={conf_b}, model={model_b}]\n"
                        f"Which relationship is more likely correct? "
                        f'Respond ONLY with JSON: {{"keep": "{rel_a}" or "{rel_b}", '
                        f'"reason": "one sentence explanation"}}'
                    )

                    try:
                        async with _get_ingest_semaphore():
                            response = await llm.ainvoke(prompt)
                        verdict  = json.loads(response.content.strip())
                        keep     = str(verdict.get("keep", "")).upper()
                        reason   = str(verdict.get("reason", ""))[:200]
                        flag_rel = rel_b if keep == rel_a else rel_a
                        if keep not in (rel_a, rel_b):
                            logger.debug(f"Graph-Linting: unrecognized keep='{keep}', skipping")
                            await asyncio.sleep(0.5)
                            continue
                    except Exception as exc:
                        logger.warning(
                            f"Graph-Linting LLM verdict failed for ({subject},{target}): {exc}"
                        )
                        await asyncio.sleep(0.5)
                        continue

                    # Write-back: flag the losing relationship
                    try:
                        async with self.driver.session() as session:
                            await session.run(
                                f"""
                                MATCH (a:Entity {{name: $subject}})-[r:{flag_rel}]->
                                      (x:Entity {{name: $target}})
                                SET r.flagged    = true,
                                    r.lint_note  = $reason,
                                    r.lint_ts    = timestamp(),
                                    r.lint_model = $model
                                """,
                                {
                                    "subject": subject,
                                    "target":  target,
                                    "reason":  reason,
                                    "model":   getattr(llm, "model_name", "unknown"),
                                },
                            )
                        conflicts_resolved += 1
                        logger.debug(
                            f"🧹 Linting: flagged ({subject})-[{flag_rel}]->({target}) — {reason}"
                        )
                    except Exception as exc:
                        logger.warning(f"Graph-Linting write-back failed: {exc}")

                    # Yield the event loop between LLM calls to avoid VRAM starvation
                    await asyncio.sleep(0.5)

        logger.info(f"🧹 Graph-Linting Phase 2 complete: {conflicts_resolved} conflicts resolved")

        # ── Phase 3: Confidence decay sweep ────────────────────────────────
        decay_deleted = 0
        try:
            await self.compute_trust_scores(batch_size=500)
            async with self.driver.session() as session:
                result = await session.run("""
                    MATCH (a:Entity)-[r]->(b:Entity)
                    WHERE r.trust_score < $threshold
                      AND (r.verified IS NULL OR r.verified = false)
                      AND (r.version IS NULL OR r.version = 1)
                      AND r.source NOT IN ['ontology', 'ontology_gap_healer']
                    RETURN id(r) AS rid, a.name AS subject, type(r) AS rel,
                           b.name AS target, r.trust_score AS score
                    LIMIT 100
                """, {"threshold": _TRUST_DELETE_THRESHOLD})
                stale = [dict(r) async for r in result]

            for row in stale:
                try:
                    async with self.driver.session() as session:
                        await session.run(
                            "MATCH ()-[r]->() WHERE id(r) = $rid DELETE r",
                            {"rid": row["rid"]},
                        )
                    decay_deleted += 1
                    logger.debug(
                        f"🧹 Decay: deleted ({row['subject']})-[{row['rel']}]->({row['target']}) "
                        f"trust={row['score']}"
                    )
                    # Publish to Kafka audit topic if available
                    if kafka_publish_fn:
                        try:
                            await kafka_publish_fn("moe.audit", {
                                "event": "confidence_decay_delete",
                                "subject": row["subject"],
                                "relation": row["rel"],
                                "target": row["target"],
                                "trust_score": row["score"],
                                "ts": datetime.now(timezone.utc).isoformat(),
                            })
                        except Exception:
                            pass  # Audit logging is best-effort
                except Exception as exc:
                    logger.warning(f"Decay delete failed for rid={row['rid']}: {exc}")

            if decay_deleted:
                logger.info(f"🧹 Graph-Linting Phase 3: {decay_deleted} decayed relations deleted")
        except Exception as exc:
            logger.warning(f"Graph-Linting decay phase failed: {exc}")

        logger.info(
            f"🧹 Graph-Linting complete: orphans={orphans_deleted}, "
            f"conflicts={conflicts_resolved}, decay={decay_deleted}"
        )
        return {
            "orphans_deleted": orphans_deleted,
            "conflicts_resolved": conflicts_resolved,
            "decay_deleted": decay_deleted,
        }

    # ─── SYNTHESIS PERSISTENCE ───────────────────────────────────────────────

    async def ingest_synthesis(
        self,
        synthesis: Dict[str, Any],
        domain: Optional[str] = None,
        source_model: str = "unknown",
        confidence: float = 0.5,
        expert_domain: str = "",
    ) -> None:
        """
        Persists a synthesis insight as a :Synthesis node linked to relevant :Entity nodes.

        Args:
            synthesis: Dict with keys:
                - "summary": str — the insight text (required)
                - "entities": List[str] — entity names to link via :RELATED_TO
                - "insight_type": str — "comparison" | "synthesis" | "inference"
            domain: Knowledge domain (propagated from the originating response).
            source_model: Model that produced the insight.
            confidence: Confidence score of the originating response.
        """
        summary      = str(synthesis.get("summary", "")).strip()[:500]
        insight_type = str(synthesis.get("insight_type", "synthesis"))[:50]
        entity_names = [
            str(e).strip()[:100]
            for e in synthesis.get("entities", [])
            if str(e).strip()
        ]

        if not summary:
            logger.debug("ingest_synthesis: empty summary, skipping")
            return

        # Deterministic ID: first 16 hex chars of sha256(summary)
        node_id = hashlib.sha256(summary.encode()).hexdigest()[:16]

        # Fallback: derive entity names from summary text if none were provided
        if not entity_names:
            entity_names = list(self._extract_terms(summary))[:10]

        try:
            async with self.driver.session() as session:
                # Create or update the :Synthesis node (idempotent via MERGE on id)
                await session.run(
                    """
                    MERGE (s:Synthesis {id: $id})
                    ON CREATE SET
                        s.text          = $summary,
                        s.insight_type  = $insight_type,
                        s.entities      = $entity_names,
                        s.created       = timestamp(),
                        s.domain        = $domain,
                        s.expert_domain = $expert_domain,
                        s.source_model  = $source_model,
                        s.confidence    = $confidence
                    """,
                    {
                        "id":            node_id,
                        "summary":       summary,
                        "insight_type":  insight_type,
                        "entity_names":  entity_names,
                        "domain":        domain or "unknown",
                        "expert_domain": expert_domain,
                        "source_model":  source_model,
                        "confidence":    confidence,
                    },
                )
                # Link :Synthesis to matching :Entity nodes in one batch query
                if entity_names:
                    await session.run(
                        """
                        MATCH (s:Synthesis {id: $id})
                        UNWIND $entity_names AS ename
                        MATCH (e:Entity {name: ename})
                        MERGE (s)-[:RELATED_TO]->(e)
                        """,
                        {"id": node_id, "entity_names": entity_names},
                    )
            logger.info(
                f"💡 Synthesis persisted: [{insight_type}] '{summary[:60]}...' "
                f"→ {len(entity_names)} entities linked"
            )
            return insight_type
        except Exception as exc:
            logger.warning(f"ingest_synthesis failed: {exc}")
            return None

    # ─── KNOWLEDGE EXPORT / IMPORT ──────────────────────────────────────────

    # ─── Privacy Scrubber (Semantic Leakage Protection) ────────────────────

    # Regex patterns that indicate potentially sensitive content in entity names
    _SENSITIVE_PATTERNS = [
        re.compile(r"password|passwd|secret|token|api.?key|credential", re.IGNORECASE),
        re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),  # IP addresses
        re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}"),  # Email
        re.compile(r"sk-[a-zA-Z0-9]{20,}|moe-sk-[a-f0-9]+"),  # API keys
        re.compile(r"prod_|staging_|internal_", re.IGNORECASE),  # Infra hints
        re.compile(r"kunde|client|customer.*[A-Z]", re.IGNORECASE),  # Client names
    ]

    def _is_sensitive_name(self, name: str) -> bool:
        """Check if an entity name contains potentially sensitive data."""
        return any(p.search(name) for p in self._SENSITIVE_PATTERNS)

    def _scrub_triple(self, subject: str, predicate: str, obj: str) -> bool:
        """Returns True if this triple should be EXCLUDED from export."""
        # Hard filter: known sensitive relation types
        sensitive_rels = {"HAS_PASSWORD", "HAS_CREDENTIAL", "HAS_TOKEN",
                          "AUTHENTICATES_WITH", "CONNECTS_TO_INTERNAL"}
        if predicate.upper() in sensitive_rels:
            return True
        # Check subject and object for PII/secrets
        if self._is_sensitive_name(subject) or self._is_sensitive_name(obj):
            return True
        return False

    async def export_knowledge_bundle(
        self,
        domains: Optional[List[str]] = None,
        min_trust: float = 0.3,
        include_syntheses: bool = True,
        strip_sensitive: bool = True,
    ) -> dict:
        """Export knowledge triples as a community-shareable JSON-LD bundle.

        Filters:
          - domains: Only export entities/relations in these expert domains (None = all)
          - min_trust: Minimum trust_score on relations (filters low-quality)
          - strip_sensitive: Remove tenant_id, source_model + run privacy scrubber
        Returns a dict ready for json.dumps().
        """
        bundle = {
            "@context": "https://moe-sovereign.org/knowledge/v1",
            "format_version": "1.0",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "filters": {
                "domains": domains,
                "min_trust": min_trust,
            },
            "entities": [],
            "relations": [],
            "syntheses": [],
            "stats": {},
        }

        # Shared with the relations query below: a relation whose subject/object
        # entity fails this same source filter would otherwise reference an
        # entity absent from this bundle's own entities[] — silently breaking
        # import elsewhere (MATCH (s),(o) finds nothing, no exception, no
        # relation created; see import_knowledge_bundle's relations_created fix).
        _entity_source_filter = "['ontology', 'extracted', 'ontology_gap_healer']"

        async with self.driver.session() as session:
            # Export entities
            domain_filter = "AND e.domain IN $domains" if domains else ""
            q_entities = f"""
                MATCH (e:Entity)
                WHERE e.source IN {_entity_source_filter}
                {domain_filter}
                RETURN e.name AS name, e.type AS type, e.source AS source,
                       e.aliases AS aliases, e.domain AS domain,
                       e.tenant_id AS tenant_id
                ORDER BY e.name
            """
            params = {"domains": domains} if domains else {}
            result = await session.run(q_entities, params)
            records = await result.data()
            for r in records:
                entity = {
                    "name": r["name"],
                    "type": r["type"],
                    "source": r["source"],
                    "domain": r.get("domain"),
                }
                if r.get("aliases"):
                    entity["aliases"] = r["aliases"]
                if not strip_sensitive:
                    entity["tenant_id"] = r.get("tenant_id")
                # Privacy scrubber: skip entities with sensitive names
                if strip_sensitive and self._is_sensitive_name(r["name"]):
                    bundle.setdefault("scrubbed_count", 0)
                    bundle["scrubbed_count"] = bundle.get("scrubbed_count", 0) + 1
                    continue
                bundle["entities"].append(entity)

            # Export relations with trust filtering
            trust_filter = "AND r.trust_score >= $min_trust" if min_trust > 0 else ""
            q_relations = f"""
                MATCH (s:Entity)-[r]->(o:Entity)
                WHERE type(r) <> 'RELATED_TO_SYNTHESIS'
                AND s.source IN {_entity_source_filter}
                AND o.source IN {_entity_source_filter}
                {trust_filter}
                {domain_filter.replace('e.domain', 'r.domain')}
                RETURN s.name AS subject, type(r) AS predicate, o.name AS object,
                       r.confidence AS confidence, r.trust_score AS trust_score,
                       r.source AS source, r.domain AS domain,
                       r.verified AS verified, r.source_model AS source_model,
                       r.created AS created
                ORDER BY r.trust_score DESC
            """
            params["min_trust"] = min_trust
            result = await session.run(q_relations, params)
            records = await result.data()
            for r in records:
                rel = {
                    "subject": r["subject"],
                    "predicate": r["predicate"],
                    "object": r["object"],
                    "confidence": r.get("confidence"),
                    "trust_score": r.get("trust_score"),
                    "source": r.get("source"),
                    "domain": r.get("domain"),
                    "verified": r.get("verified", False),
                }
                if not strip_sensitive:
                    rel["source_model"] = r.get("source_model")
                    rel["created"] = str(r.get("created")) if r.get("created") else None
                # Privacy scrubber: skip triples with sensitive content
                if strip_sensitive and self._scrub_triple(r["subject"], r["predicate"], r["object"]):
                    bundle["scrubbed_count"] = bundle.get("scrubbed_count", 0) + 1
                    continue
                bundle["relations"].append(rel)

            # Export syntheses
            if include_syntheses:
                q_synth = f"""
                    MATCH (s:Synthesis)
                    WHERE s.confidence >= $min_trust
                    {domain_filter.replace('e.domain', 's.domain')}
                    RETURN s.id AS id, s.text AS text, s.insight_type AS insight_type,
                           s.entities AS entities, s.domain AS domain,
                           s.confidence AS confidence, s.source_model AS source_model
                    ORDER BY s.confidence DESC
                """
                result = await session.run(q_synth, params)
                records = await result.data()
                for r in records:
                    synth = {
                        "text": r["text"],
                        "insight_type": r.get("insight_type"),
                        "entities": r.get("entities", []),
                        "domain": r.get("domain"),
                        "confidence": r.get("confidence"),
                    }
                    if not strip_sensitive:
                        synth["source_model"] = r.get("source_model")
                    bundle["syntheses"].append(synth)

        bundle["stats"] = {
            "entities": len(bundle["entities"]),
            "relations": len(bundle["relations"]),
            "syntheses": len(bundle["syntheses"]),
            "scrubbed": bundle.pop("scrubbed_count", 0),
        }
        logger.info(
            f"📦 Knowledge export: {bundle['stats']['entities']} entities, "
            f"{bundle['stats']['relations']} relations, "
            f"{bundle['stats']['syntheses']} syntheses"
        )
        return bundle

    async def import_knowledge_bundle(
        self,
        bundle: dict,
        source_tag: str = "community_import",
        trust_floor: float = 0.5,
        dry_run: bool = False,
        kafka_publish_fn: Optional[Callable] = None,
    ) -> dict:
        """Import a knowledge bundle into the graph.

        Merges entities by name (no duplicates). Relations are added only if they
        don't already exist with higher trust. Imported relations get
        source='community_import' and capped trust_score.

        Args:
            bundle: JSON-LD bundle from export_knowledge_bundle
            source_tag: Source label for imported triples
            trust_floor: Max trust_score for imported relations (prevents
                         community data from outranking locally verified facts)
            dry_run: If True, only report what would be imported
        Returns: {entities_created, entities_skipped, relations_created,
                  relations_skipped, syntheses_created, errors}
        """
        stats = {
            "entities_created": 0, "entities_skipped": 0,
            "relations_created": 0, "relations_skipped": 0,
            "syntheses_created": 0, "errors": [],
        }
        now = datetime.now(timezone.utc).isoformat()

        entities = bundle.get("entities", [])
        relations = bundle.get("relations", [])
        syntheses = bundle.get("syntheses", [])

        async with self.driver.session() as session:
            # Import entities
            for ent in entities:
                name = ent.get("name", "").strip()
                if not name:
                    continue
                try:
                    if dry_run:
                        result = await session.run(
                            "MATCH (e:Entity {name: $name}) RETURN e.name AS n",
                            {"name": name},
                        )
                        exists = await result.single()
                        if exists:
                            stats["entities_skipped"] += 1
                        else:
                            stats["entities_created"] += 1
                        continue

                    result = await session.run(
                        """MERGE (e:Entity {name: $name})
                        ON CREATE SET
                            e.type = $type,
                            e.source = $source,
                            e.domain = $domain,
                            e.aliases = $aliases,
                            e.aliases_str = $aliases_str,
                            e.created = $now
                        RETURN e.source AS existing_source""",
                        {
                            "name": name,
                            "type": ent.get("type", "Concept"),
                            "source": source_tag,
                            "domain": ent.get("domain"),
                            "aliases": ent.get("aliases", []),
                            "aliases_str": " ".join(ent.get("aliases", [])),
                            "now": now,
                        },
                    )
                    record = await result.single()
                    if record and record["existing_source"] and record["existing_source"] != source_tag:
                        stats["entities_skipped"] += 1
                    else:
                        stats["entities_created"] += 1
                except Exception as e:
                    stats["errors"].append(f"Entity '{name}': {e}")

            # Import relations — with contradiction detection
            contradictions = []
            for rel in relations:
                subj = rel.get("subject", "").strip()
                obj = rel.get("object", "").strip()
                pred = rel.get("predicate", "").strip()
                if not (subj and obj and pred):
                    continue
                # Cap trust score
                import_trust = min(
                    float(rel.get("trust_score") or rel.get("confidence") or 0.5),
                    trust_floor,
                )

                # Contradiction detection: check if importing this triple
                # would contradict an existing high-trust local relation
                contra_rels = _CONTRADICTORY_PAIRS.get(pred, [])
                if contra_rels:
                    try:
                        for contra in contra_rels:
                            chk = await session.run(
                                f"""MATCH (s:Entity {{name: $s}})-[r:{contra}]->(o:Entity {{name: $o}})
                                WHERE r.trust_score >= $threshold
                                RETURN r.trust_score AS ts, r.source AS src""",
                                {"s": subj, "o": obj, "threshold": import_trust},
                            )
                            conflict = await chk.single()
                            if conflict:
                                contradictions.append({
                                    "imported": f"({subj})-[{pred}]->({obj})",
                                    "conflicts_with": f"({subj})-[{contra}]->({obj})",
                                    "existing_trust": conflict["ts"],
                                    "existing_source": conflict["src"],
                                })
                                stats["relations_skipped"] += 1
                                break
                        else:
                            # No contradiction found — proceed with import
                            pass
                        if contradictions and contradictions[-1]["imported"] == f"({subj})-[{pred}]->({obj})":
                            continue  # Skip this relation due to contradiction
                    except Exception:
                        pass  # Contradiction check failed — proceed with import

                try:
                    if dry_run:
                        result = await session.run(
                            f"""MATCH (s:Entity {{name: $s}})-[r:{pred}]->(o:Entity {{name: $o}})
                            RETURN r.trust_score AS ts""",
                            {"s": subj, "o": obj},
                        )
                        existing = await result.single()
                        if existing and (existing["ts"] or 0) >= import_trust:
                            stats["relations_skipped"] += 1
                        else:
                            stats["relations_created"] += 1
                        continue

                    # Only create if not exists or existing trust is lower.
                    # MATCH (s),(o) silently returns zero rows (no exception) when
                    # either endpoint doesn't resolve — without checking the actual
                    # write counters, relations_created was incremented unconditionally
                    # even when MERGE never ran. Observed live: 1286 reported created,
                    # only 12 actually landed (+231 pre-existing = 243 total).
                    _rel_result = await session.run(
                        f"""MATCH (s:Entity {{name: $s}}), (o:Entity {{name: $o}})
                        MERGE (s)-[r:{pred}]->(o)
                        ON CREATE SET
                            r.source = $source,
                            r.confidence = $conf,
                            r.trust_score = $trust,
                            r.domain = $domain,
                            r.verified = false,
                            r.version = 1,
                            r.created = $now,
                            r.valid_from = $now
                        WITH r
                        WHERE r.trust_score < $trust
                        SET r.trust_score = $trust, r.source = $source""",
                        {
                            "s": subj, "o": obj,
                            "source": source_tag,
                            "conf": import_trust,
                            "trust": import_trust,
                            "domain": rel.get("domain"),
                            "now": now,
                        },
                    )
                    _rel_summary = await _rel_result.consume()
                    if _rel_summary.counters.relationships_created > 0:
                        stats["relations_created"] += 1
                    else:
                        stats["relations_skipped"] += 1
                except Exception as e:
                    stats["errors"].append(f"Relation '{subj}-[{pred}]->{obj}': {e}")

            if contradictions:
                stats["contradictions"] = contradictions
                logger.warning(
                    f"⚠️ Import detected {len(contradictions)} contradictions "
                    f"with existing knowledge — skipped conflicting triples"
                )
                # Publish contradictions to moe.linting Kafka topic for admin review
                if kafka_publish_fn and not dry_run:
                    for c in contradictions:
                        try:
                            await kafka_publish_fn("moe.linting", {
                                "type": "import_contradiction",
                                "source": source_tag,
                                "imported_triple": c["imported"],
                                "conflicts_with": c["conflicts_with"],
                                "existing_trust": c.get("existing_trust"),
                                "existing_source": c.get("existing_source"),
                                "action": "skipped",
                                "timestamp": now,
                            })
                        except Exception:
                            pass

            # Import syntheses
            for syn in syntheses:
                text = syn.get("text", "").strip()
                if not text:
                    continue
                syn_id = hashlib.sha256(text.encode()).hexdigest()[:16]
                try:
                    if dry_run:
                        stats["syntheses_created"] += 1
                        continue
                    await session.run(
                        """MERGE (s:Synthesis {id: $id})
                        ON CREATE SET
                            s.text = $text,
                            s.insight_type = $type,
                            s.entities = $entities,
                            s.domain = $domain,
                            s.confidence = $conf,
                            s.source_model = $source,
                            s.created = $now""",
                        {
                            "id": syn_id,
                            "text": text[:500],
                            "type": syn.get("insight_type", "synthesis"),
                            "entities": syn.get("entities", []),
                            "domain": syn.get("domain"),
                            "conf": min(float(syn.get("confidence") or 0.5), trust_floor),
                            "source": source_tag,
                            "now": now,
                        },
                    )
                    stats["syntheses_created"] += 1
                except Exception as e:
                    stats["errors"].append(f"Synthesis: {e}")

        action = "DRY RUN" if dry_run else "IMPORT"
        logger.info(
            f"📥 Knowledge {action}: "
            f"{stats['entities_created']} entities, "
            f"{stats['relations_created']} relations, "
            f"{stats['syntheses_created']} syntheses "
            f"({len(stats['errors'])} errors)"
        )
        return stats

    # ─── KNOWLEDGE PROMOTION ─────────────────────────────────────────────────

    async def promote_knowledge(
        self,
        from_tenant_id: str,
        to_tenant_id: Optional[str],
        entity_names: Optional[list] = None,
    ) -> int:
        """Moves entities from one knowledge namespace to another.

        Used to promote user-private knowledge (user:{id}) up the hierarchy:
        user:{id} → team:{team_id} → tenant:{tenant_id} → None (global).

        Args:
            from_tenant_id: Source namespace (e.g. "user:abc123").
            to_tenant_id: Target namespace, or None for global (public).
            entity_names: Restrict promotion to specific entity names; None = all.

        Returns:
            Number of entities updated.
        """
        names_filter = "AND e.name IN $names" if entity_names else ""
        cypher = (
            "MATCH (e:Entity) "
            f"WHERE e.tenant_id = $from_tenant {names_filter} "
            "SET e.tenant_id = $to_tenant "
            "RETURN count(e) AS promoted"
        )
        async with self.driver.session() as session:
            result = await session.run(
                cypher,
                from_tenant=from_tenant_id,
                to_tenant=to_tenant_id,
                names=entity_names or [],
            )
            record = await result.single()
        count = record["promoted"] if record else 0
        logger.info(
            f"🔼 Knowledge promoted: {count} entities "
            f"{from_tenant_id!r} → {to_tenant_id!r}"
        )
        return count

    # ─── LIFECYCLE ───────────────────────────────────────────────────────────

    async def close(self) -> None:
        await self.driver.close()
