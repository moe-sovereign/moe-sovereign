#!/usr/bin/env python3
"""scripts/generate_diverse_training_seeds.py — offline batch generation via
DeepSeek-V4-Flash (vLLM, LUMI-G only) to diversify the two LUMI-G
post-training datasets prepared for the benchmark-derived candidates in
docs/experiments/lumig_posttraining_candidates.md:

  --mode loom       Candidate 1 (memory-ordering): novel Rust producer/
                     consumer `loom::model` scenario pairs (broken + fixed),
                     beyond the 4 hand-written archetypes in
                     scripts/generate_loom_seed_examples.py.
  --mode grounding  Candidate 2 (planner task fabrication) / Candidate 4
                     (nested/escaped JSON on multi-entity persistence):
                     diverse, category-tagged example user requests, beyond
                     the hand-written examples in
                     scripts/extract_planner_grounding_pairs.py.
  --mode role_sft   Full-finetuning data generation (all 10 MoE Sovereign
                     roles: Planner, 8 Experts, Judge) for the LUMI-G
                     post-training plan in ~/.claude/plans/zazzy-beaming-
                     koala.md Phase 2 -- a teacher model, prompted with the
                     target role's own system prompt, invents a realistic
                     (user_request, ideal_assistant_response) pair in one
                     shot. Output is "text" (ChatML), the exact format
                     scripts/train_expert_slm_pipeline.py's
                     dataset_text_field="text" expects for ALL 10 roles
                     including judge -- confirmed by reading that script
                     directly: it has no per-role format branching, and
                     scripts/train_judge_lora.py (Alpaca instruction/input/
                     output format) is a separate, older script the current
                     production pipeline (slurm/lumig_expert_ensemble_
                     pipeline.slurm) does not actually call for judge.

Architecture note (why the teacher LLM only generates *raw material*, never
labels): DeepSeek-V4-Flash's job here is diversity of natural-language
input, not correctness grading. Ground truth for Candidate 1 still comes
exclusively from the real rust-loom-sandbox (see
scripts/generate_loom_seed_examples.py --llm-scenarios-file, which re-runs
every generated pair through the sandbox and keeps only sandbox-verified
determinate outcomes); ground truth for Candidate 2/4 still comes from the
deterministic, rule-based plan construction in
scripts/extract_planner_grounding_pairs.py --llm-requests-file (the category
is fixed by which prompt we asked for, never inferred from the model's own
output). This script's output is never used as a training target directly.

Model note: deepseek-ai/DeepSeek-V4-Flash was the first choice here but is a
confirmed dead end on this cluster's vLLM build -- its native blockwise-FP8
checkpoint (quant_method=fp8, fmt=e4m3) raises
"deepseek_v4_fp8 quantization is currently not supported in rocm" at engine
init (verified live, job 21676155). Default is now Qwen/Qwen3.5-35B-A3B: a
current (not the older Qwen2.5 line), unquantized MoE checkpoint (no
quantization_config in its config.json, so it loads as plain BF16, sidestepping
the whole class of exotic-quant-on-ROCm failures) that this project's own
slurm/lumig_job2_distillation.slurm already designates as the distillation
teacher -- reusing it here needs no new model-compatibility risk. Run
slurm/lumig_job5_enrichment_smoketest.slurm (tiny --count, single node)
BEFORE the full shard jobs to confirm the model loads and generates at all.

Usage (inside the LUMI-G Singularity container, one process per node):
    python3 scripts/generate_diverse_training_seeds.py --mode loom \\
        --count 100 --output "$OUT_DIR/loom_shard0.jsonl"
    python3 scripts/generate_diverse_training_seeds.py --mode grounding \\
        --count-per-category 125 --output "$OUT_DIR/grounding_shard0.jsonl"
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

# Delimiter-based (not JSON) output format for anything that embeds
# multi-line source code in a field. Root-caused live (job 21798250,
# role_sft/coder smoke test, GLM-4.5-Air, full raw completions in
# role_sft_coder_smoketest.jsonl.debug.log): models reliably produce
# real, on-topic content but do NOT reliably JSON-escape multi-line code
# (raw newlines/quotes inside a JSON string value break json.loads() even
# though the content itself is fine) -- this is the actual root cause of
# the long-standing "--mode loom always parses to 0" bug, previously
# mis-attributed to --max-tokens being too small. JSON stays fine for
# --mode grounding, whose values are short one-line strings with no
# embedded code -- already empirically proven robust (24/24 twice).
_LOOM_GENERATION_PROMPT = """You are an expert in Rust concurrent systems programming and the `loom` concurrency-model-checking crate (loom = "0.7.2").

Design ONE new, original Rust producer/consumer or lock-free synchronization scenario, distinct from all of: a flag-guarded payload publish, a generation-counter handoff, a lazy-init multi-field config, and a single-slot SPSC ring buffer. Draw inspiration from ideas such as: a seqlock, double-checked initialization, a hazard-pointer retire, a work-stealing deque steal, an RCU-style snapshot publish, a ticket-lock handoff, or a once-cell double publish -- or invent your own.

Output EXACTLY in this plain-text format, with no other text before the first marker or after the last line of fixed source, and no markdown code fences around the markers themselves:
===SCENARIO_NAME===
<snake_case_name, one line>
===BROKEN_SOURCE===
<full Rust source, using loom::sync::atomic (AtomicUsize/AtomicBool)/Arc/thread, with exactly one #[test] fn that calls loom::model(...) and a deliberately too-weak Ordering::Relaxed on the publishing store and/or the consuming load, guarded by an assert_eq! that Loom's interleaving exploration will find a counterexample for>
===FIXED_SOURCE===
<the identical test with ONLY the ordering corrected -- Ordering::Release on the publish, Ordering::Acquire on the consuming load -- so the same assert_eq! now holds under every interleaving>
===END===

Hard constraints: edition 2021; do not include a [package]/Cargo.toml, only the lib.rs body; use ONLY loom::sync::atomic::{AtomicUsize, AtomicBool, Ordering}, loom::sync::Arc, loom::thread; never use std::process, std::fs, std::net, extern "C", #[link], or include!; exactly one #[test] fn per source; both broken_source and fixed_source must be complete, independently compilable Rust files (each with its own `use` statements). Write the raw Rust code directly after each marker -- do not wrap it in ```rust fences and do not JSON-escape it."""

_GROUNDING_CATEGORY_DESCRIPTIONS: Dict[str, str] = {
    "general": "small talk, greetings, trivial factual questions, or simple everyday questions with no need for a specialized tool or expert",
    "precision_tools": "exact calculations, unit conversions, date/time arithmetic, percentages, or other tasks requiring precise deterministic computation",
    "code_reviewer": "reviewing, writing, debugging, or explaining source code in any programming language",
    "compounding_knowledge": "storing, persisting, or registering a single structured fact or relationship for later retrieval",
    "governance": "compliance, policy, regulatory, or organizational-governance questions",
    "research": "requests needing current external information such as news, prices, or recent events",
    "security": "security review, vulnerability assessment, or defensive security guidance",
    "technical_support": "troubleshooting technical/IT problems or explaining how a system/tool works",
}

_PERSISTENCE_CATEGORY_DESCRIPTIONS: Dict[str, str] = {
    "compounding_knowledge": "storing multiple related entities or facts in ONE request (e.g. a team roster with several people and their roles, a system topology with several interdependent services, a multi-item inventory list)",
    "governance": "persisting a multi-clause policy, directive, or regulation update in ONE request, where a later clause amends or references an earlier one",
}

# One concrete, on-domain example per category, injected into the generation
# prompt below. Added 2026-09-10 after both open-source student candidates
# (SmolLM3-3B job 21853583, OLMo-3-7B-Instruct job 21856449) scored badly on
# real-content inspection despite Exit 0: roughly half their output was
# lazy numbered-list filler ("string 1", "response 2") and a third of the
# rest was real text assigned to the WRONG category (precision_tools got
# "Hello there" / "Tell me a joke" -- general small talk, not a precision
# request). The abstract category description alone was apparently not
# enough grounding for a smaller model to both (a) generate real content
# instead of the shape of a JSON array, and (b) stay on-topic. Every
# already-verified larger teacher (GLM-4.5-Air, Qwen3-Next-80B,
# OLMo-3.1-32B) scored 24/24 on the OLD template, so this is additive
# scaffolding for weaker instruction-following, not a fix for a broken
# category description.
_GROUNDING_CATEGORY_EXAMPLES: Dict[str, str] = {
    "general": "Hi! What's the capital of Australia?",
    "precision_tools": "Convert 37.5 degrees Celsius to Fahrenheit and Kelvin, rounded to two decimal places.",
    "code_reviewer": "Can you review this Python function for an off-by-one bug? def get_last(lst): return lst[len(lst)]",
    "compounding_knowledge": "Remember that our staging database is hosted on db-staging-02, with read replica db-staging-02-ro.",
    "governance": "Does storing EU customer IP addresses in server logs for 90 days violate GDPR Article 5 data minimization?",
    "research": "What's the current spot price of Brent crude oil, and has it moved significantly in the last week?",
    "security": "A login endpoint returns a different error for 'user not found' than for 'wrong password' -- is that a security issue?",
    "technical_support": "My Docker container keeps restarting with exit code 137 -- what does that mean and how do I fix it?",
}
_PERSISTENCE_CATEGORY_EXAMPLES: Dict[str, str] = {
    "compounding_knowledge": "Save our on-call rotation: Alice covers Mon-Wed, Bob covers Thu-Fri, and Alice also backs up Bob's shifts.",
    "governance": "Update our data-retention policy: clause 4.2 now overrides clause 3.1 for financial records, extending retention from 3 to 7 years.",
}

_GROUNDING_GENERATION_TEMPLATE = """Generate {n} diverse, realistic example user requests that a person might send to an AI assistant, all clearly and unambiguously belonging to this domain: {description}.

Example of the right kind of request for this domain (write DIFFERENT requests, not this one): "{example}"

Vary the phrasing, length (from one short sentence to a few sentences), and specificity. Do NOT write requests that belong to a different domain. Do NOT write filler placeholders like "string 1" or "request 2", and do not default to generic small talk unless the domain itself is small talk -- every request must be a complete, specific, realistic message a real person would actually type.

Output STRICT JSON only: a JSON array of exactly {n} strings, no prose, no markdown code fences. Do not explain your approach, restate these instructions, or comment on the format -- your entire reply must be the JSON array itself, starting with [ and ending with ]."""

# Copied deliberately, not imported, from the production sources of truth
# below -- this script runs standalone inside the LUMI-G Singularity
# container (no guarantee the repo root is on sys.path the way the app
# expects), matching the existing pattern in
# scripts/curate_coder_expert_dataset.py's own inlined _SYSTEM_PROMPT.
# Keep in sync by hand if the originals change:
#   prompts.py: DEFAULT_EXPERT_PROMPTS[<key>], DEFAULT_PLANNER_ROLE
#   services/inference.py: JUDGE_SYSTEM_PROMPT
_ROLE_SYSTEM_PROMPTS: Dict[str, str] = {
    "coder": (
        "You are a high-assurance systems-programming and code-synthesis expert (moe-expert-coder-4b) "
        "specialized in Rust, C++, Python, and Go. Produce precise, compiler-checked code and minimal "
        "atomic diffs. Uphold memory-safety invariants strictly — correct ownership, correct lock-free "
        "memory ordering (acquire/release pairing), no data races. Flag any construct you cannot verify "
        "as sound rather than guessing."
    ),
    "precision": (
        "You are a formal-reasoning, SMT constraint formulation, and numerical-precision expert "
        "(moe-expert-precision-4b). Decompose quantitative problems into explicit, tool-verifiable "
        "calculation steps and always ground exact numbers in a deterministic calculation tool rather "
        "than estimating in prose. Never guess a numeric result you can compute exactly; show your work "
        "and validate the output before presenting it."
    ),
    "graphrag": (
        "You are a knowledge-graph navigation and GraphRAG retrieval specialist (moe-expert-graphrag-4b). "
        "Formulate syntactically correct multi-hop Cypher queries, resolve ambiguous entity mentions to "
        "canonical nodes, and extract structured knowledge triplets from unstructured text. Require the "
        "graph schema to be given in context — never invent node or relationship types that weren't "
        "provided, and bound multi-hop traversals against runaway cartesian products."
    ),
    "governance": (
        "You are a regulatory policy reasoning and privacy-by-design expert (moe-expert-governance-4b) for "
        "GDPR, the EU AI Act, BSI IT-Grundschutz, ISO 27001, and HIPAA. Ground every compliance judgment in "
        "the specific article or control it derives from, classify risk levels precisely, and identify "
        "privacy-by-design and control-mapping gaps. Never invent a legal citation — state explicitly when "
        "a source is uncertain."
    ),
    "research": (
        "You are an evidence-grounded literature review, technical trade-off synthesis, and citation "
        "verification expert (moe-expert-research-4b). Ground every claim in a verifiable source, "
        "synthesize across multiple documents, quantify engineering trade-offs, and explicitly flag "
        "uncertainty rather than inventing a citation."
    ),
    "security": (
        "You are a cybersecurity, static vulnerability analysis, and hardening expert "
        "(moe-expert-security-4b). Identify memory-safety flaws, injection vectors, and SSRF/CWE-classified "
        "vulnerabilities with the exact CWE ID; scan for exposed secrets and credentials; build STRIDE-based "
        "threat models across trust boundaries; and produce concrete hardening manifests "
        "(seccomp, AppArmor, Kubernetes policies)."
    ),
    "datainfra": (
        "You are a database engineering, query optimization, and data-infrastructure expert "
        "(moe-expert-datainfra-4b) specialized in PostgreSQL, DuckDB, and ClickHouse. "
        "Give concrete, execution-ready SQL, EXPLAIN ANALYZE-based query-plan diagnosis, safe "
        "zero-downtime schema migrations, and precise index recommendations. Verify syntax before answering."
    ),
    "omni": (
        "You are the cross-domain synthesis and interface-harmonization expert (moe-expert-omni-4b). "
        "Reconcile and integrate outputs from other specialists (coder, security, data infrastructure, "
        "governance, precision, graph retrieval) into one coherent, consistent result. "
        "Do not attempt deep mathematical proofs, raw kernel/driver code, or standalone regulatory audits "
        "yourself — state explicitly when a dedicated specialist is actually required instead."
    ),
    "planner": (
        "You are the orchestrator of a Mixture-of-Experts system.\n"
        "Decompose the following request into 1–4 subtasks.\n\n"
        "Mandatorily extract all numerical constraints and technical parameters from the request "
        "(e.g. model sizes, MTU values, protocol overheads, chemical doses, bitrates). "
        "Integrate these as IMMUTABLE_CONSTANTS directly into each subtask description for the experts, "
        "so experts cannot hallucinate default values."
    ),
    "judge": (
        "You are Sovereign Judge 27B (Qwen3.8-27B fine-tuned), the primary "
        "evaluation and synthesis authority in the MoE Sovereign compound AI "
        "platform. Evaluate input quality, factual consistency, code invariants, "
        "and safety with maximum precision across up to 258,000 context tokens."
    ),
}

# Self-critique finding, 2026-09-08 (coder pilot generation): the generic
# _ROLE_SFT_GENERATION_TEMPLATE below, with no topic scaffolding, let every
# tested teacher model regress to the single most salient concrete example
# named in its own role's system prompt -- confirmed live, both LUMI-G-side
# and OpenRouter-side, same bug in both: Mistral Large 3 put 99/400 `coder`
# examples into one near-identical "lock-free MPSC queue" template, Kimi K3
# put 86/258 into near-identical SPSC-ring-buffer variants, both anchored on
# the one phrase "lock-free memory ordering (acquire/release pairing)" in
# _ROLE_SYSTEM_PROMPTS["coder"]. This is the same failure class as Candidate
# 2's Planner task fabrication (a model collapses onto the one example it
# was given instead of the intended breadth) -- fixed the same way the loom
# prompt already avoids it: cycle through an explicit list of concrete,
# mutually distinct topic anchors per role rather than relying on sampling
# temperature alone for diversity. One list per generic (non-planner,
# non-judge) role; each entry should be a plausible request under that
# role's real system prompt above, deliberately spanning DIFFERENT concerns
# so no single theme dominates a batch.
_GENERIC_ROLE_TOPIC_HINTS: Dict[str, List[str]] = {
    "coder": [
        "parsing or serializing a binary/text wire format (e.g. a length-prefixed frame, a config file format) in Rust or Go",
        "a Python data pipeline, CLI tool, or async I/O script with correct error handling and type hints",
        "a C++ RAII/smart-pointer resource-management design or move-semantics question",
        "a Go concurrent worker pool, context-cancellation, or channel-based pipeline (not raw atomics)",
        "debugging a buggy code snippet the user pastes in, in any of Rust/C++/Python/Go",
        "a code-review request for a diff/pull request, flagging correctness or safety issues",
        "a build-system, dependency-resolution, or toolchain configuration problem (Cargo, CMake, pip/poetry, go.mod)",
        "an algorithm or data-structure implementation unrelated to concurrency (e.g. a trie, graph traversal, custom allocator, sorting variant)",
        "a lock-free/atomic memory-ordering question (acquire/release pairing, SPSC/MPSC queues, ring buffers)",
        "a testing, property-based-testing, or fuzzing question for one of the four languages",
        "a cross-language FFI/binding question (e.g. exposing a Rust library to Python via PyO3, or C ABI safety at an `unsafe extern` boundary)",
        "a CPU-bound performance-profiling or optimization question unrelated to concurrency (cache locality, allocation pressure, hot-loop vectorization)",
        "a resource-constrained/embedded-target question (no_std, fixed memory budget, no heap) distinct from the build-system topic above",
    ],
    "precision": [
        "a multi-step financial calculation (compound interest, amortization, currency conversion) that must be decomposed into exact tool calls",
        "a unit-conversion or dimensional-analysis problem with several intermediate steps",
        "an SMT/constraint-satisfaction formulation for a scheduling, allocation, or combinatorial problem",
        "a date/time arithmetic problem across timezones or calendar edge cases (leap years, DST)",
        "a probability or statistics computation requiring exact intermediate values, not estimation",
        "a network/subnet or bitwise numerical calculation (e.g. IP addressing, checksum size, encoding overhead)",
        "a physics/engineering unit calculation involving tolerances, error propagation, or significant figures",
        "catching and correcting an incorrect numeric claim the user or another model made, redoing it exactly",
        "an actuarial/insurance calculation (premium, mortality-table lookup, reserve requirement) needing an exact intermediate value at each step",
        "a dosage or concentration calculation (medical, chemical, or industrial-mixing) where an off-by-one-decimal error would be dangerous",
        "verifying a cryptographic or hashing computation bit-for-bit (e.g. a checksum, HMAC, or key-derivation parameter) rather than estimating it",
    ],
    "graphrag": [
        "formulating a multi-hop Cypher query given an explicit schema with several node/relationship types",
        "resolving an ambiguous entity mention in text to a canonical graph node given candidate matches",
        "extracting structured knowledge triplets (subject-predicate-object) from a paragraph of unstructured text",
        "declining a query that would require a node/relationship type not present in the given schema",
        "bounding a multi-hop traversal that could otherwise produce a runaway cartesian product",
        "merging or deduplicating two graph query results that refer to the same real-world entity",
        "explaining a retrieved subgraph's relevance back to the user's original natural-language question",
        "a temporal/point-in-time graph query (e.g. 'what did this org structure look like as of a given date') given a schema with versioned relationships",
        "a graph-schema-evolution question: how to add a new node or relationship type without breaking existing queries, given the current schema",
        "a centrality or community-detection style question over a described graph (most-connected node, tightly-clustered subgroup) using only graph-native reasoning, not raw statistics",
    ],
    "governance": [
        "a GDPR data-subject-rights or lawful-basis question grounded in a specific article",
        "an EU AI Act risk-classification question for a specific described system or use case",
        "a BSI IT-Grundschutz or ISO 27001 control-mapping gap analysis",
        "a HIPAA question about a specific covered-entity or business-associate scenario",
        "a privacy-by-design review of a proposed feature or data flow",
        "persisting or amending a multi-clause internal policy/directive where a later clause references an earlier one",
        "explicitly flagging that a compliance question cannot be answered with confidence because the exact citation is uncertain",
        "a SOC 2 or PCI-DSS control-mapping question (a different framework family than GDPR/AI-Act/BSI/ISO/HIPAA above) grounded in a specific control ID",
        "a cross-border data-transfer question (Standard Contractual Clauses, adequacy decision, transfer impact assessment) for a described data flow",
        "an EU AI Act Article 12-style documentation/audit-trail/logging requirement question for a described AI system, distinct from the risk-classification topic above",
    ],
    "research": [
        "synthesizing a technical trade-off comparison across multiple named approaches or tools, each grounded in a cited source",
        "a literature-review-style question about recent developments in a specific technical subfield",
        "verifying whether a specific claim in a document is well-supported, flagging uncertainty where it is not",
        "a request needing current external information (news, prices, recent releases) explicitly flagged as needing live retrieval",
        "reconciling two sources that give conflicting numbers or claims about the same topic",
        "assessing whether a described experiment or methodology (not a code vulnerability, not a compliance question) is reproducible given the reported setup",
        "a technology-maturity or adoption-curve assessment for a named tool/approach, grounded in cited evidence rather than opinion",
        "synthesizing findings across a SPECIFIC set of named papers or sources the user provides, rather than an open-ended live-information request",
    ],
    "security": [
        "identifying a memory-safety flaw (buffer overflow, use-after-free, double-free) in a pasted code snippet, with the exact CWE ID",
        "an injection-vulnerability review (SQL, command, template, SSRF) of a pasted code snippet or API design",
        "building a STRIDE-based threat model for a described system architecture with named trust boundaries",
        "scanning a pasted config/log/code snippet for an exposed secret or credential",
        "producing a concrete hardening manifest (seccomp profile, AppArmor policy, Kubernetes NetworkPolicy) for a described service",
        "a dependency/supply-chain vulnerability triage question about a CVE affecting a named library version",
        "an authentication/authorization design review (OAuth/OIDC flow flaw, JWT validation gap, session-fixation) for a described login/API system",
        "identifying cryptographic misuse (weak algorithm choice, IV/nonce reuse, hardcoded key, insufficient key length) in a pasted snippet or design description",
        "an incident-response or log-forensics triage question given a described suspicious log excerpt, distinct from the secret-scanning topic above",
    ],
    "datainfra": [
        "diagnosing a slow query via an EXPLAIN ANALYZE output the user pastes in (PostgreSQL, DuckDB, or ClickHouse)",
        "designing a zero-downtime schema migration (adding a NOT NULL column, changing a type) for a live table",
        "recommending a precise index or set of indexes for a described query workload",
        "writing execution-ready SQL for a multi-table join/aggregation reporting request",
        "a data-modeling question about normalization vs. denormalization trade-offs for a specific access pattern",
        "diagnosing a replication-lag, lock-contention, or connection-pool-exhaustion symptom",
        "designing a backup, point-in-time-recovery, or disaster-recovery strategy for a described database workload",
        "a table-partitioning or sharding-key design question for a described large-table or high-write-volume scenario",
        "designing a materialized-view or caching-layer strategy for a described reporting/analytics workload",
    ],
    "omni": [
        "reconciling conflicting outputs from two named specialists (e.g. coder vs. security) on the same request",
        "synthesizing a coherent final answer from several partial expert outputs into one consistent response",
        "explicitly declining a deep mathematical-proof or raw kernel/driver-code request and naming the specialist actually required",
        "explicitly declining a standalone regulatory-audit request and naming the governance specialist instead",
        "harmonizing terminology or formatting differences between two expert outputs before presenting them to the user",
        "identifying that a user request spans 3+ specialist domains at once (e.g. build a feature, secure it, and document its compliance posture) and sequencing which specialist output is needed first",
        "recognizing that a request is too under-specified for any specialist to act on yet, and asking the one clarifying question that unblocks routing -- without attempting the deep work of any specialist itself",
        "prioritizing between two specialists' conflicting recommendations when only one can be acted on first, explaining the trade-off rather than picking arbitrarily",
    ],
}

# Mechanical post-filter for the residual bias the {other_hints} negative
# constraint above reduces but does not eliminate (found live, 2026-09-08,
# job 21814113/21827844): GLM-4.5-Air dropped from 72% to 54% lock-free/
# concurrency-themed `coder` examples after the negative-constraint fix,
# still far above the ~10% a uniform 10-hint rotation would produce. Rather
# than a third round of prompt engineering (diminishing returns, confirmed
# by the OpenRouter control test showing the same partial-only effect),
# this discards -- never relabels or fabricates -- any example assigned a
# DIFFERENT hint that still contains one of that role's attractor keywords.
# Only populate a role here after the same empirical bias is actually
# observed for it, the way coder's was -- never assume another role has the
# same failure mode without measuring it first (the whole point of this
# project's "kein Gemini-Vorfall" discipline).
_GENERIC_ROLE_ATTRACTOR_KEYWORDS: Dict[str, List[str]] = {
    "coder": [
        "lock-free", "lock free", "wait-free", "wait free", "spsc", "mpsc", "mpmc",
        "acquire/release", "acquire-release", "compare_exchange", "compare-and-swap",
        "memory ordering", "memory-ordering", "ring buffer", "atomicusize", "atomicptr",
        "atomicbool", "ordering::acquire", "ordering::release",
    ],
}


def _role_sft_output_violates_topic(role: str, hint: str, user_request: str) -> bool:
    """True if user_request uses one of role's attractor keywords (see
    _GENERIC_ROLE_ATTRACTOR_KEYWORDS above) despite hint not being about
    that theme itself. A hint whose own text already contains the keyword
    (e.g. coder's memory-ordering hint) is exempt -- it is allowed to use
    it. Returns False for any role with no confirmed attractor keywords.
    """
    keywords = _GENERIC_ROLE_ATTRACTOR_KEYWORDS.get(role)
    if not keywords:
        return False
    hint_lower = hint.lower()
    if any(kw in hint_lower for kw in keywords):
        return False
    text_lower = user_request.lower()
    return any(kw in text_lower for kw in keywords)

# ---------------------------------------------------------------------------
# Planner-specific generation (self-critique finding, 2026-09-08): the
# generic template above produces prose Q&A, but the real Planner output is
# a structured JSON task-array with an exact MCP tool schema, $task_result
# chaining, and specific formatting rules -- none of which the generic
# template ever exercises. This directly targets 3 of 5 documented
# benchmark failures (docs/experiments/lumig_posttraining_candidates.md
# Candidates 2/4/5 + the multi-step under-decomposition finding), extracted
# verbatim from the real, static rules in graph/planner.py (not the dynamic
# per-request bits like _build_filtered_tool_desc/_build_skill_catalog,
# which depend on live MCP tool registration and can't be reproduced
# standalone here).
# ---------------------------------------------------------------------------

_PLANNER_TRAINING_SYSTEM_PROMPT = (
    "You are the orchestrator (Planner) of a Mixture-of-Experts system. Given a "
    "user request, output a JSON array of task objects that decomposes it. Each "
    "task object has at minimum a \"task\" (string, concrete description) and a "
    "\"category\" (string) field.\n\n"
    "VALID CATEGORIES: code_reviewer, precision_tools, compounding_knowledge, "
    "governance, research, security, technical_support, general, legal_advisor, "
    "vision, dynamic.\n\n"
    "PRECISION TOOLS -- mandatory for exact calculations (LLMs calculate wrong): "
    "{\"task\": \"...\", \"category\": \"precision_tools\", \"mcp_tool\": \"<name>\", "
    "\"mcp_args\": {<args>}}. Example tools: calculate (args: expression), "
    "decimal_finance (args: operation [add|subtract|multiply|divide|percentage|"
    "simple_interest|compound_interest], operands [list of decimal strings], "
    "currency [ISO 4217], scale [int 0-12], rounding), subnet_calc (args: cidr -- "
    "ONE network only), vlsm_subnet_calc (args: cidr, subnets [list of {id, hosts}] "
    "-- splitting one block into MULTIPLE named subnets; never pass a subnets list "
    "to subnet_calc, it only accepts cidr).\n\n"
    "CHAINED CALCULATIONS -- when one calculation needs a PREVIOUS calculation's "
    "result: give each precision_tools task a stable \"id\", reference an earlier "
    "task's result as {\"$task_result\": \"<id>\"} instead of computing it yourself. "
    "A reference must point to an earlier task in the same array, never to itself "
    "or a later task. Multi-step numeric problems (e.g. multi-year cost/tariff "
    "escalation) need ONE precision_tools task PER step, fully chained -- never "
    "collapse several dependent steps into one task or leave later steps for "
    "free-form prose estimation.\n\n"
    "KNOWLEDGE STORAGE -- when the user asks to store/persist/remember information "
    "(including multiple entities or a multi-clause rule/directive hierarchy in one "
    "request): do NOT hand-encode the data as a JSON string inside \"task\", and do "
    "NOT produce nested or escaped JSON. A single plain-language task that restates "
    "and acknowledges the information is sufficient: {\"task\": \"Acknowledge the "
    "following information and confirm it is noted: <restate the facts in plain "
    "prose>\", \"category\": \"compounding_knowledge\"}.\n\n"
    "LEGAL RESEARCH -- combine a precision_tools legal-lookup task with a "
    "legal_advisor interpretation task; never let legal_advisor alone answer a "
    "specific-paragraph question (it hallucinates legal text).\n\n"
    "GROUNDING -- every task MUST have a concrete lexical/semantic connection to "
    "the actual user request. Never invent tasks about topics (networking, "
    "security audits, compliance frameworks, unrelated APIs) that are not present "
    "in the request, even loosely -- this is the single most damaging failure mode "
    "for this role. Simple requests get exactly one task, no overengineering.\n\n"
    "Output ONLY the JSON array, no prose, no markdown fences."
)

_PLANNER_PATTERN_FOCUS: Dict[str, str] = {
    "simple_precision": "a single-step exact calculation, unit conversion, or date/time arithmetic (one precision_tools task)",
    "chained_calculation": "a multi-step numeric problem where later steps depend on earlier results (e.g. multi-year cost escalation, running totals, compound growth) -- MUST use $task_result chaining across at least 3 dependent precision_tools tasks, not one collapsed task",
    "vlsm_vs_subnet": "either a single-network subnet/CIDR question (use subnet_calc) or a multi-subnet VLSM allocation from one parent block (use vlsm_subnet_calc) -- pick one, get the tool choice right",
    "knowledge_storage_multi_entity": "a request to store/register multiple related entities or a multi-clause rule hierarchy in one turn (e.g. a team roster, a system topology, a multi-clause policy update) -- MUST use the plain-prose acknowledgment pattern, not nested JSON",
    "legal_research": "a question about a specific German law paragraph (BGB, StGB, etc.) -- MUST combine a precision_tools legal lookup with a legal_advisor interpretation task",
    "dynamic_expert": "a request needing deep domain expertise outside the standard categories (e.g. real-estate valuation, chemical process optimization, emergency medicine, maritime law) -- use category \"dynamic\" with a \"domain\" field",
    "research_then_code": "an implementation request with domain-specific rules that must be correct (a game, a protocol, an algorithm with precise semantics) -- MUST include a research task before the code task, with all known rules embedded in the code task's description",
    "simple_single_task": "a simple, everyday request needing exactly one non-precision task (e.g. a code review, a governance question, a research question) -- no overengineering, no fabricated additional tasks",
}

_PLANNER_SFT_GENERATION_TEMPLATE = """{system_prompt}

Invent ONE new, realistic training example for the pattern: {pattern_description}

Write a specific, concrete user request, then write the CORRECT JSON task array that a well-trained Planner would produce for it -- following every rule above exactly.

Output EXACTLY in this plain-text format, with no other text before the first marker or after ===END===:
===USER_REQUEST===
<a specific, realistic user message>
===ASSISTANT_RESPONSE===
<the correct JSON task array as a single line or pretty-printed, starting with [ and ending with ]>
===END==="""


def _validate_planner_task_array(text: str) -> bool:
    """Real structural validation, not just a length guard: the
    assistant_response for the planner role claims to be a JSON task array,
    so actually parse it and check it has the shape a real Planner output
    must have (list of dicts, each with "task" and "category", any
    mcp_args is itself a dict) -- catches a fluent-looking but structurally
    wrong response that a bare length check would miss entirely.
    """
    try:
        arr = json.loads(text)
    except json.JSONDecodeError:
        return False
    if not isinstance(arr, list) or not arr:
        return False
    for item in arr:
        if not isinstance(item, dict):
            return False
        if not isinstance(item.get("task"), str) or not item["task"].strip():
            return False
        if not isinstance(item.get("category"), str) or not item["category"].strip():
            return False
        if "mcp_args" in item and not isinstance(item["mcp_args"], dict):
            return False
    return True


def parse_planner_sft_output(text: str) -> Optional[Dict[str, str]]:
    """Planner-specific counterpart to parse_role_sft_output: same delimited
    extraction, but the assistant_response is additionally validated as a
    real, structurally-correct JSON task array (see
    _validate_planner_task_array) rather than accepted as any non-empty
    text. Returns None on any parse/validation failure.
    """
    fields = _parse_delimited_fields(text, ["USER_REQUEST", "ASSISTANT_RESPONSE"])
    if fields is None:
        return None
    response = fields["ASSISTANT_RESPONSE"].strip()
    # Teachers sometimes wrap the array in a fence despite instructions not to.
    if response.startswith("```"):
        response = response.split("\n", 1)[-1]
        if response.rstrip().endswith("```"):
            response = response.rstrip()[:-3]
        response = response.strip()
    if not _validate_planner_task_array(response):
        return None
    return {
        "user_request": fields["USER_REQUEST"],
        "assistant_response": response,
    }


# ---------------------------------------------------------------------------
# Judge-specific generation (self-critique finding, 2026-09-08): Candidate 3
# (docs/experiments/lumig_posttraining_candidates.md) needs a very specific
# format contract for critic-style prompts -- bare "CONFIRMED" or a direct
# corrected answer, zero preamble/meta-commentary -- that the generic
# template never exercises. The compliance check below mirrors
# graph/synthesis.py's real, production _critic_is_noncompliant_confirmation
# (same two regexes), inverted: a generated training target must PASS the
# same check the real system uses to reject bad Judge output, or it would
# train the model on exactly the behavior this is meant to eliminate.
# ---------------------------------------------------------------------------

_JUDGE_CRITIC_TRAINING_SYSTEM_PROMPT = (
    "You are Sovereign Judge 27B, acting as a critic that checks a candidate "
    "answer against a question. You will be shown a QUESTION and an ANSWER TO "
    "CHECK. Respond with EXACTLY the single word CONFIRMED if the answer is "
    "correct and complete. Otherwise, respond with ONLY the corrected answer -- "
    "no preamble, no meta-commentary, no phrases like 'The answer contains "
    "mistakes' or 'Factual errors were found', no restating the question. Start "
    "directly with the corrected content itself (code, prose, or data, whatever "
    "the answer's own format is)."
)

_JUDGE_CRITIC_PATTERN_FOCUS: Dict[str, str] = {
    "confirmed_code": "a QUESTION asking for a small, correct piece of code, and an ANSWER TO CHECK that is genuinely correct -- the right response is the bare word CONFIRMED",
    "confirmed_prose": "a QUESTION asking a factual/explanatory question, and an ANSWER TO CHECK that is genuinely correct and complete -- the right response is the bare word CONFIRMED",
    "corrected_code": "a QUESTION asking for code (e.g. involving concurrency, memory ordering, or a common off-by-one/edge-case bug), and an ANSWER TO CHECK containing a real, specific bug -- the right response is ONLY the corrected code, no preamble",
    "corrected_fact": "a QUESTION asking a factual question, and an ANSWER TO CHECK containing a real factual error or unsupported claim -- the right response is ONLY the corrected answer, no preamble, no phrase like 'the answer contains an error'",
}

_JUDGE_CRITIC_SFT_GENERATION_TEMPLATE = """{system_prompt}

Invent ONE new, realistic example for this pattern: {pattern_description}

Output EXACTLY in this plain-text format, with no other text before the first marker or after ===END===:
===QUESTION===
<the original question/task, one or more lines>
===ANSWER_TO_CHECK===
<the candidate answer being checked, may be multiple paragraphs or code>
===CRITIC_RESPONSE===
<either the bare word CONFIRMED, or ONLY the corrected answer with zero preamble>
===END==="""

_CRITIC_TRAILING_CONFIRMED_RE = re.compile(r'\bCONFIRMED\b\s*$', re.IGNORECASE)
_CRITIC_PREAMBLE_RE = re.compile(
    r'^\s*the\s+(provided\s+|given\s+)?["“]?'
    r'(answer(\s+to\s+check)?|response|implementation|code)["”]?\b'
    r'|^\s*(unsupported|incorrect|critical)\s+(claim|flaw|error)\b',
    re.IGNORECASE,
)


def _critic_response_is_noncompliant(critic_out: str) -> bool:
    """Mirrors graph/synthesis.py's real _critic_is_noncompliant_confirmation
    (same two regexes) -- a generated training target must pass this check,
    or it would train the model to reproduce exactly the non-compliant
    format the real system already has to guard against in production.
    """
    stripped = critic_out.strip()
    if not stripped:
        return True
    if stripped.upper() != "CONFIRMED" and _CRITIC_TRAILING_CONFIRMED_RE.search(stripped):
        return True
    if _CRITIC_PREAMBLE_RE.match(stripped):
        return True
    return False


def parse_judge_critic_sft_output(text: str) -> Optional[Dict[str, str]]:
    """Judge-critic-specific counterpart to parse_role_sft_output: extracts
    QUESTION/ANSWER_TO_CHECK/CRITIC_RESPONSE, validates the critic response
    against the real production compliance check, and renders the final
    training pair as (user_request, assistant_response) for render_chatml --
    the "user" turn is the critic-style prompt (question + answer to check),
    the "assistant" turn is the compliant critic response.
    """
    fields = _parse_delimited_fields(text, ["QUESTION", "ANSWER_TO_CHECK", "CRITIC_RESPONSE"])
    if fields is None:
        return None
    critic_response = fields["CRITIC_RESPONSE"].strip()
    if _critic_response_is_noncompliant(critic_response):
        return None
    if critic_response.upper() != "CONFIRMED" and len(critic_response) < _MIN_RESPONSE_LEN:
        return None
    user_turn = (
        f"QUESTION:\n{fields['QUESTION']}\n\n"
        f"ANSWER TO CHECK:\n{fields['ANSWER_TO_CHECK']}\n\n"
        "Respond with CONFIRMED if the answer is correct and complete. Otherwise, "
        "respond with ONLY the corrected answer -- no preamble, no meta-commentary."
    )
    return {"user_request": user_turn, "assistant_response": critic_response}

# Delimiter-based, not JSON -- see the comment above _LOOM_GENERATION_PROMPT:
# an assistant_response for a code-heavy role (coder/datainfra/security) is
# exactly the kind of multi-line, quote-and-backslash-heavy content models
# do not reliably JSON-escape (root-caused live, job 21798250).
_ROLE_SFT_GENERATION_TEMPLATE = """{system_prompt}

Invent ONE new, realistic training example for fine-tuning a smaller model to perform exactly this role. The example MUST be grounded in this specific theme -- do NOT default to the most generic or most obvious example implied by the role description above: {topic_hint}. This is a hard requirement: your answer is WRONG if it is not clearly and specifically about this theme, even if it stays unique in wording. Do NOT write about any of these other themes instead, even partially or as a side detail: {other_hints}. Write a specific, concrete user request that clearly falls within this domain (not a generic placeholder), then write the complete, ideal expert response you would give to it -- fully in character with the role above, showing real reasoning/work, not a stub.

Output EXACTLY in this plain-text format, with no other text before the first marker or after ===END===, and no markdown code fences around the markers themselves (code fences INSIDE the response text, e.g. for a code snippet, are fine):
===USER_REQUEST===
<a specific, realistic user message, one or more lines>
===ASSISTANT_RESPONSE===
<the complete, ideal expert response to it -- may be multiple paragraphs and include code blocks>
===END==="""

def _find_balanced_spans(text: str, open_ch: str, close_ch: str) -> List[str]:
    """Find every top-level, bracket-balanced `open_ch...close_ch` span in
    text, tracking string-literal state (with escape handling) so
    brackets/braces inside quoted strings never confuse depth counting.
    Unlike a single greedy regex (e.g. `\\[.*\\]` or `\\{.*\\}`), this does
    not collapse multiple JSON values -- or a real value plus unrelated
    preceding/following bracket/brace characters from model reasoning text
    -- into one unparseable blob. Returns candidate substrings in the order
    they appear in text. Used for both `[...]` arrays and `{...}` objects.
    """
    candidates: List[str] = []
    n = len(text)
    i = 0
    while i < n:
        if text[i] != open_ch:
            i += 1
            continue
        depth = 0
        in_string = False
        escape = False
        j = i
        while j < n:
            c = text[j]
            if in_string:
                if escape:
                    escape = False
                elif c == "\\":
                    escape = True
                elif c == '"':
                    in_string = False
            else:
                if c == '"':
                    in_string = True
                elif c == open_ch:
                    depth += 1
                elif c == close_ch:
                    depth -= 1
                    if depth == 0:
                        candidates.append(text[i:j + 1])
                        break
            j += 1
        i = j + 1
    return candidates


def _find_bracket_balanced_arrays(text: str) -> List[str]:
    return _find_balanced_spans(text, "[", "]")


def _parse_delimited_fields(text: str, field_names: List[str]) -> Optional[Dict[str, str]]:
    """Extract fields from a `===FIELD_NAME===\\n<content>` delimited
    completion. Used instead of JSON for anything that embeds multi-line
    source code: models reliably produce real, on-topic content but do NOT
    reliably JSON-escape raw newlines/quotes inside a string value --
    root-caused live in job 21798250 (role_sft/coder smoke test, GLM-4.5-Air:
    real Rust code generated, but malformed as JSON on every one of 5
    attempts). A plain marker scan needs no escaping at all.

    Returns None if any marker is missing or any extracted field is empty.
    Tolerates reasoning/preamble text before the first marker and markdown
    fences around the whole completion.

    Searches for each marker's LAST occurrence, working backward from the
    final field to the first (each earlier marker's search is bounded to
    end before the next field's found position, which also guarantees
    correct ordering without a separate check). This matters when a model
    botches its first attempt and restarts: root-caused live
    (deepseek/deepseek-v4-pro-0813, comparing --role research against
    z-ai/glm-5.3) -- one completion wrote a placeholder "..." attempt,
    visibly reconsidered in plain reasoning text, then produced a second,
    real attempt. Matching the FIRST occurrence of each marker (the
    original implementation) captured the placeholder block plus all the
    leftover reasoning text as one field's content; matching the LAST
    occurrence picks the model's final, presumably-corrected attempt.
    """
    positions: list = [None] * len(field_names)
    last_marker = f"==={field_names[-1]}==="
    idx = text.rfind(last_marker)
    if idx == -1:
        return None
    positions[-1] = (idx, field_names[-1], last_marker)
    search_end = idx
    for i in range(len(field_names) - 2, -1, -1):
        marker = f"==={field_names[i]}==="
        idx = text.rfind(marker, 0, search_end)
        if idx == -1:
            return None
        positions[i] = (idx, field_names[i], marker)
        search_end = idx

    end_idx = text.find("===END===", positions[-1][0])
    result: Dict[str, str] = {}
    for i, (idx, name, marker) in enumerate(positions):
        content_start = idx + len(marker)
        if i + 1 < len(positions):
            content_end = positions[i + 1][0]
        elif end_idx > content_start:
            content_end = end_idx
        else:
            content_end = len(text)
        content = text[content_start:content_end].strip()
        if not content or _is_unfilled_placeholder(content):
            return None
        result[name] = content
    return result


def _is_unfilled_placeholder(text: str) -> bool:
    """True if text is still an unfilled template placeholder echoed back
    verbatim (e.g. "<a specific, realistic user message, one or more
    lines>") instead of real generated content -- found live, 2026-09-08,
    role_sft/coder job 21829009 (GLM-4.5-Air): 10/577 "successfully parsed"
    examples were exactly this literal echo of
    _ROLE_SFT_GENERATION_TEMPLATE's own placeholder text, long enough to
    pass _MIN_RESPONSE_LEN/_MIN_SOURCE_LEN by length alone. Every
    delimited template in this file (loom/role_sft/planner/judge) uses the
    same `<description of what goes here>` placeholder convention, so this
    is checked centrally in _parse_delimited_fields rather than per-parser.
    A single outer angle-bracket pair spanning the whole field is not
    something real generated content (a user request, Rust source, a JSON
    task array) would ever produce, so this has no plausible false positive.
    """
    stripped = text.strip()
    if stripped.startswith("<") and stripped.endswith(">") and stripped.count("<") == 1 and stripped.count(">") == 1:
        return True
    # A short bracketed placeholder like "[User query]" (found live,
    # 2026-09-09, job 21832982/governance) -- bounded to short strings so a
    # real answer that happens to end in a bracketed citation/footnote is
    # never mistaken for this.
    if (stripped.startswith("[") and stripped.endswith("]") and stripped.count("[") == 1
            and stripped.count("]") == 1 and len(stripped) < 40):
        return True
    return False


_NUMBERED_PLACEHOLDER_RE = re.compile(
    r"^(string|item|example|request|response|task|prompt|entry|question|message)s?\s*#?\s*\d+\.?$",
    re.IGNORECASE,
)


def _is_numbered_placeholder(text: str) -> bool:
    """True for lazy numbered-list filler like "string 1", "response 2",
    "item #3" -- found live, 2026-09-09/10, `--mode grounding` smoke tests
    for both open-source student candidates (SmolLM3-3B job 21853583,
    OLMo-3-7B-Instruct job 21856449): a weaker model asked to emit a JSON
    array of N diverse requests sometimes produces the correct *shape*
    (valid JSON, N non-empty strings) without generating real content,
    defaulting to a generic category-agnostic word plus its own array
    index. parse_grounding_output only checked for a non-empty string, so
    this passed silently -- unlike role_sft's `_is_unfilled_placeholder`,
    which only fires on a literal echoed `<...>`/`[...]` template marker
    and would never catch this. Bounded to short strings that are ONLY the
    placeholder word plus a number, so a real request that happens to
    mention a number (e.g. "item 42 in my inventory shows the wrong SKU")
    is never caught -- those carry real content around the number.
    """
    return bool(_NUMBERED_PLACEHOLDER_RE.match(text.strip()))


# Minimum plausible length for a field that claims to be a full, compilable
# Rust source file or a real expert response -- guards against exactly the
# failure mode caught live in job 21798693's loom retest: one of 2
# completions parsed "successfully" with all three fields equal to the
# 4-character garbage string "`, `" (non-empty, so it passed the earlier
# bare-truthiness check, but obviously not real content). Loom's own
# downstream rust-loom-sandbox re-verification would eventually have caught
# this specific case (it won't compile), but role_sft has no such
# independent downstream check -- catching obviously-too-short garbage here,
# at the only point that has the raw completion, is cheaper and more
# reliable than hoping every consumer re-validates length itself.
_MIN_SOURCE_LEN = 50
_MIN_RESPONSE_LEN = 20
# Found live, 2026-09-09, job 21832982 (governance, GLM-4.5-Air): a handful
# of "successfully parsed" examples had a USER_REQUEST field of "and",
# "` and `", or similarly degenerate 3-8 character fragments -- clearly a
# template/self-reference leak (same failure class as the already-
# documented Kimi K3 "template-leak" residual risk), but unlike that case
# this one IS mechanically safe to filter: no plausible real user request
# is this short even in the shortest legitimate case seen so far
# ("What is 2+2?" is 12 chars). ASSISTANT_RESPONSE already had a length
# floor; USER_REQUEST never did.
_MIN_REQUEST_LEN = 10


def parse_loom_output(text: str) -> Optional[Dict[str, str]]:
    """Best-effort extraction of the scenario_name/broken_source/fixed_source
    fields from a raw model completion. Returns None on any parse/shape
    failure -- an unparseable generation is simply discarded, never
    fabricated into a plausible-looking fallback.
    """
    fields = _parse_delimited_fields(text, ["SCENARIO_NAME", "BROKEN_SOURCE", "FIXED_SOURCE"])
    if fields is None:
        return None
    if len(fields["BROKEN_SOURCE"]) < _MIN_SOURCE_LEN or len(fields["FIXED_SOURCE"]) < _MIN_SOURCE_LEN:
        return None
    return {
        "scenario_name": fields["SCENARIO_NAME"],
        "broken_source": fields["BROKEN_SOURCE"],
        "fixed_source": fields["FIXED_SOURCE"],
    }


def parse_grounding_output(text: str, expected_count: int) -> List[str]:
    """Best-effort extraction of a JSON array of request strings. Returns
    whatever valid non-empty strings were found -- a partial or padded
    array from the model still yields usable examples, it just yields
    fewer than expected_count.

    Tries every bracket-balanced candidate span in the completion (a model
    that emits reasoning/preamble containing its own bracket characters
    before the real answer array previously broke the single-greedy-regex
    version: `\\[.*\\]` spans from the FIRST `[` to the LAST `]` in the
    whole text, swallowing everything in between into one string that fails
    json.loads() -- observed live with zai-org/GLM-4.5-Air, where 7/8
    grounding categories parsed to 0 despite real, on-topic generations).
    Keeps whichever candidate yields the most usable string items; ties
    favor the later candidate, since a genuine final answer more often
    follows reasoning than precedes it.
    """
    best: List[str] = []
    for candidate in _find_bracket_balanced_arrays(text):
        try:
            arr = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(arr, list):
            continue
        items = [item.strip() for item in arr if isinstance(item, str) and item.strip()
                 and not _is_numbered_placeholder(item)][:expected_count]
        if len(items) >= len(best):
            best = items
    return best


def _load_llm(model: str, tensor_parallel_size: int, max_model_len: int, gpu_memory_utilization: float,
              enforce_eager: bool = False):
    # Imported lazily: vllm is a LUMI-G-only training/inference dependency,
    # not installed in the local dev environment this script is authored in.
    from vllm import LLM  # noqa: PLC0415

    # enforce_eager (default False, opt-in via --enforce-eager): found live,
    # 2026-09-09, Olmo-3.1-32B-Instruct smoke test (job 21848558) -- weight
    # loading succeeded, but vLLM's CUDA-graph-capture step ("Capturing CUDA
    # graphs (mixed prefill-decode, PIECEWISE)") crashed with a ROCm/HIP-
    # specific error (hipErrorCapturedEvent, "operation not permitted on an
    # event last recorded in a capturing stream") -- a runtime graph-capture
    # incompatibility for this architecture on this cluster's ROCm build,
    # not a weight-loading or size problem. --enforce-eager skips graph
    # capture entirely (slower per-token, but otherwise identical outputs)
    # and is the standard vLLM diagnostic for exactly this failure class.
    # Left off by default: every other teacher model already verified on
    # this cluster (Qwen3-Next-80B, GLM-4.5-Air, Qwen3.5-35B-A3B, etc.) uses
    # graph capture successfully, so forcing eager mode everywhere would
    # slow down already-proven models for no reason.
    return LLM(
        model=model,
        dtype="auto",  # let vLLM read quantization_config (native fp8) from the checkpoint itself
        tensor_parallel_size=tensor_parallel_size,
        trust_remote_code=True,
        max_model_len=max_model_len,
        gpu_memory_utilization=gpu_memory_utilization,
        enforce_eager=enforce_eager,
    )


def _log_parse_failure(debug_path: Path, raw_text: str) -> None:
    """Append a bounded excerpt of an unparseable raw completion to a debug
    sidecar file. Added after job 21794616 (role_sft/coder smoke test, GLM-
    4.5-Air) returned 0/5 parsed with no way to inspect why -- silent parse
    failures with no diagnostic trail are exactly the "looks done, wasn't
    verified" pattern this plan's own Phase 4 self-critique already flagged.
    Truncated to keep the sidecar file bounded even across many failures.
    """
    with open(debug_path, "a", encoding="utf-8") as f:
        f.write("=" * 80 + "\n")
        f.write(raw_text[:4000] + ("... [truncated]" if len(raw_text) > 4000 else "") + "\n")


def run_loom_mode(args: argparse.Namespace) -> None:
    from vllm import SamplingParams  # noqa: PLC0415

    llm = _load_llm(args.model, args.tensor_parallel_size, args.max_model_len, args.gpu_memory_utilization, args.enforce_eager)
    sampling = SamplingParams(temperature=0.9, top_p=0.95, max_tokens=args.max_tokens)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    debug_path = output_path.with_suffix(output_path.suffix + ".debug.log")
    written = 0
    total = 0
    # Chunked generation (not one single args.count-wide call): flushes
    # progress to disk continuously so a SLURM timeout mid-run still leaves
    # every already-completed batch on disk, not just an all-or-nothing result.
    with open(output_path, "a", encoding="utf-8") as f:
        for start in range(0, args.count, args.batch_size):
            batch_n = min(args.batch_size, args.count - start)
            outputs = llm.generate([_LOOM_GENERATION_PROMPT] * batch_n, sampling)
            for out in outputs:
                total += 1
                raw_text = out.outputs[0].text
                parsed = parse_loom_output(raw_text)
                if parsed is not None:
                    f.write(json.dumps(parsed, ensure_ascii=False) + "\n")
                    written += 1
                else:
                    _log_parse_failure(debug_path, raw_text)
            f.flush()
            print(f"[loom] batch {start // args.batch_size + 1}: "
                  f"{written}/{total} parsed so far", flush=True)
    print(f"[loom] done: {written}/{total} scenario pairs parsed -> {output_path}")


def run_grounding_mode(args: argparse.Namespace) -> None:
    from vllm import SamplingParams  # noqa: PLC0415

    descriptions = _PERSISTENCE_CATEGORY_DESCRIPTIONS if args.persistence else _GROUNDING_CATEGORY_DESCRIPTIONS
    examples = _PERSISTENCE_CATEGORY_EXAMPLES if args.persistence else _GROUNDING_CATEGORY_EXAMPLES
    style = "persistence" if args.persistence else "grounding"

    llm = _load_llm(args.model, args.tensor_parallel_size, args.max_model_len, args.gpu_memory_utilization, args.enforce_eager)
    sampling = SamplingParams(temperature=1.0, top_p=0.95, max_tokens=args.max_tokens)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    debug_path = output_path.with_suffix(output_path.suffix + ".debug.log")
    written = 0
    with open(output_path, "a", encoding="utf-8") as f:
        for category, description in descriptions.items():
            remaining = args.count_per_category
            while remaining > 0:
                batch_n = min(args.batch_size, remaining)
                prompt = _GROUNDING_GENERATION_TEMPLATE.format(n=batch_n, description=description, example=examples[category])
                outputs = llm.generate([prompt], sampling)
                raw_text = outputs[0].outputs[0].text
                requests = parse_grounding_output(raw_text, batch_n)
                for request_text in requests:
                    f.write(json.dumps({"category": category, "style": style, "prompt": request_text},
                                        ensure_ascii=False) + "\n")
                    written += 1
                f.flush()
                print(f"[grounding:{style}] {category}: got {len(requests)}/{batch_n} this batch "
                      f"({written} total so far)", flush=True)
                # A batch that yields nothing usable means the model isn't
                # cooperating with this prompt shape -- stop retrying rather
                # than spin forever on zero-progress batches.
                if not requests:
                    _log_parse_failure(debug_path, raw_text)
                    print(f"[grounding:{style}] {category}: batch produced 0 usable requests, "
                          f"moving to next category", flush=True)
                    break
                remaining -= len(requests)
    print(f"[grounding:{style}] done: {written} tagged requests -> {output_path}")


def parse_role_sft_output(text: str) -> Optional[Dict[str, str]]:
    """Best-effort extraction of the user_request/assistant_response fields
    from a raw model completion. Returns None on any parse/shape failure --
    an unparseable generation is simply discarded, never fabricated into a
    plausible-looking fallback.
    """
    fields = _parse_delimited_fields(text, ["USER_REQUEST", "ASSISTANT_RESPONSE"])
    if fields is None:
        return None
    if len(fields["ASSISTANT_RESPONSE"]) < _MIN_RESPONSE_LEN:
        return None
    if len(fields["USER_REQUEST"]) < _MIN_REQUEST_LEN:
        return None
    return {
        "user_request": fields["USER_REQUEST"],
        "assistant_response": fields["ASSISTANT_RESPONSE"],
    }


def render_chatml(system: str, user: str, assistant: str) -> str:
    """Render a (system, user, assistant) triple as ChatML text -- the exact
    format scripts/train_expert_slm_pipeline.py's dataset_text_field="text"
    expects (mirrors scripts/curate_coder_expert_dataset.py's _render_chatml,
    duplicated rather than imported for the same standalone-container reason
    documented at _ROLE_SYSTEM_PROMPTS above).
    """
    return (
        f"<|im_start|>system\n{system}<|im_end|>\n"
        f"<|im_start|>user\n{user}<|im_end|>\n"
        f"<|im_start|>assistant\n{assistant}<|im_end|>\n"
    )


def run_role_sft_mode(args: argparse.Namespace) -> None:
    from vllm import SamplingParams  # noqa: PLC0415

    is_planner = args.role == "planner"
    is_judge = args.role == "judge"
    system_prompt = _ROLE_SYSTEM_PROMPTS[args.role]
    llm = _load_llm(args.model, args.tensor_parallel_size, args.max_model_len, args.gpu_memory_utilization, args.enforce_eager)
    sampling = SamplingParams(temperature=1.0, top_p=0.95, max_tokens=args.max_tokens)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    debug_path = output_path.with_suffix(output_path.suffix + ".debug.log")
    written = 0
    total = 0

    if is_planner:
        pattern_names = list(_PLANNER_PATTERN_FOCUS.keys())
        prompts_by_pattern = {
            name: _PLANNER_SFT_GENERATION_TEMPLATE.format(
                system_prompt=_PLANNER_TRAINING_SYSTEM_PROMPT, pattern_description=desc)
            for name, desc in _PLANNER_PATTERN_FOCUS.items()
        }
    elif is_judge:
        pattern_names = list(_JUDGE_CRITIC_PATTERN_FOCUS.keys())
        prompts_by_pattern = {
            name: _JUDGE_CRITIC_SFT_GENERATION_TEMPLATE.format(
                system_prompt=_JUDGE_CRITIC_TRAINING_SYSTEM_PROMPT, pattern_description=desc)
            for name, desc in _JUDGE_CRITIC_PATTERN_FOCUS.items()
        }
    else:
        hints = _GENERIC_ROLE_TOPIC_HINTS[args.role]
        pattern_names = list(range(len(hints)))
        prompts_by_pattern = {
            i: _ROLE_SFT_GENERATION_TEMPLATE.format(
                system_prompt=system_prompt, topic_hint=hint,
                other_hints="; ".join(h for j, h in enumerate(hints) if j != i))
            for i, hint in enumerate(hints)
        }

    with open(output_path, "a", encoding="utf-8") as f:
        for start in range(0, args.count, args.batch_size):
            batch_n = min(args.batch_size, args.count - start)
            # Cycle through pattern/topic focuses so a batch covers a spread
            # instead of N copies of one dominant theme (see the
            # self-critique note above _GENERIC_ROLE_TOPIC_HINTS).
            batch_patterns = [pattern_names[(start + i) % len(pattern_names)] for i in range(batch_n)]
            batch_prompts = [prompts_by_pattern[p] for p in batch_patterns]
            outputs = llm.generate(batch_prompts, sampling)
            for i, out in enumerate(outputs):
                total += 1
                raw_text = out.outputs[0].text
                if is_planner:
                    parsed = parse_planner_sft_output(raw_text)
                elif is_judge:
                    parsed = parse_judge_critic_sft_output(raw_text)
                else:
                    parsed = parse_role_sft_output(raw_text)
                if parsed is not None and not (is_planner or is_judge) and _role_sft_output_violates_topic(
                        args.role, hints[batch_patterns[i]], parsed["user_request"]):
                    parsed = None
                if parsed is not None:
                    text = render_chatml(system_prompt, parsed["user_request"], parsed["assistant_response"])
                    f.write(json.dumps({"text": text}, ensure_ascii=False) + "\n")
                    written += 1
                else:
                    _log_parse_failure(debug_path, raw_text)
            f.flush()
            print(f"[role_sft:{args.role}] batch {start // args.batch_size + 1}: "
                  f"{written}/{total} parsed so far", flush=True)
    print(f"[role_sft:{args.role}] done: {written}/{total} training examples -> {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["loom", "grounding", "role_sft"], required=True)
    parser.add_argument("--model", default="Qwen/Qwen3.5-35B-A3B")
    parser.add_argument("--tensor-parallel-size", type=int, default=8)
    parser.add_argument("--max-model-len", type=int, default=8192)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.9, help="raise (e.g. 0.95-0.97) for tight-fit teacher models whose weights alone exceed vLLM's 90%% default budget per GPU")
    parser.add_argument("--enforce-eager", action="store_true", help="skip vLLM's CUDA-graph capture (slower per-token, otherwise identical output) -- diagnostic for a ROCm/HIP graph-capture crash seen live on Olmo-3.1-32B-Instruct (job 21848558); leave off for already-verified teacher models")
    parser.add_argument("--max-tokens", type=int, default=2048)
    parser.add_argument("--batch-size", type=int, default=16, help="generations per vLLM.generate() call, for incremental flushing")
    parser.add_argument("--output", required=True)
    # loom mode
    parser.add_argument("--count", type=int, default=100, help="loom mode: total scenario-pair generations to attempt")
    # grounding mode
    parser.add_argument("--count-per-category", type=int, default=100, help="grounding mode: target requests per category")
    parser.add_argument("--persistence", action="store_true", help="grounding mode: use the multi-entity persistence category set (Candidate 4) instead of the single-topic set (Candidate 2)")
    # role_sft mode
    parser.add_argument("--role", choices=sorted(_ROLE_SYSTEM_PROMPTS.keys()),
                         help="role_sft mode: which of the 10 MoE Sovereign roles to generate training data for")
    args = parser.parse_args()

    if args.mode == "role_sft" and not args.role:
        parser.error("--mode role_sft requires --role")

    if args.mode == "loom":
        run_loom_mode(args)
    elif args.mode == "grounding":
        run_grounding_mode(args)
    else:
        run_role_sft_mode(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
