# Third-party question sets (checked 2026-11-09)

Verified gold answers from public datasets, used as an independent check next to the self-generated tasks.
Licenses were read from the primary source (dataset README, project page, repository `LICENSE`); where sources disagree this is stated.
This is a technical reading, not legal advice. Re-check before redistributing any derived file.

| Dataset | Used | Split / size in `ext_tasks` | License (primary source) | Notes for use |
|---|---|---|---|---|
| TAT-QA | numeric questions (arithmetic, count) with table and text inline | dev, 150 sampled | **CC BY 4.0** (project README); repository code MIT | Attribution required. Answers carry a scale; the prompt states it |
| FinQA | numeric questions with table and text inline | test, 150 sampled | **MIT** (repository `LICENSE`); the Hugging Face card of `ibm-research/finqa` says CC BY 4.0, the README has no data statement — treated as MIT/CC BY, keep attribution | Source texts are public earnings reports |
| GSM8K | final number after `####` | test, 150 sampled | **MIT** (dataset card) | Likely seen in base-model pre-training: measures the calculate step, not orchestration |
| MuSiQue | answerable questions, supporting paragraphs plus 6 distractors inline | dev, 150 sampled | **CC BY 4.0** (project README) | Text scoring on answer and aliases. Paragraphs come from Wikipedia |
| HotpotQA | distractor setting, all 10 paragraphs inline, yes/no questions removed | validation, 150 sampled | **CC BY-SA 4.0** (hotpotqa.github.io); repository code Apache-2.0 | **ShareAlike**: files derived from it must stay CC BY-SA |
| CypherBench | questions on the `nba` and `flight_accident` graphs, graph loaded into the product's GraphRAG schema | test, 20 of 459 qualify | **Apache-2.0** (dataset card and repository); graph content derives from Wikidata | Only these two graphs are used because their sampled version equals the full graph, so the published gold answers hold. Only questions without aggregation, ordering, `CASE`, or relation qualifiers remain: what is answerable from retrieved facts |

## Looked at, not integrated

| Dataset | License found | Why not now |
|---|---|---|
| BIRD | CC BY-SA 4.0 (project page) | Needs databases and an SQL execution step; the product has no SQL tool, so the stack could only run it inline. ShareAlike applies |
| Spider 2.0 | MIT (repository) | Enterprise Snowflake/BigQuery workflows, not runnable offline on LUMI |
| WikiTableQuestions | repository CC BY-SA 4.0; Hugging Face tag CC BY 4.0 — conflicting | Answers are cell denotations, scoring would need table-aware matching |
| MATH, MathQA, SVAMP | MIT (Hugging Face tag / repository), Apache-2.0 (Hugging Face tag), MIT (repository) | Same role as GSM8K; no additional orchestration signal |
| 2WikiMultihopQA | Apache-2.0 (repository and dataset card) | Same role as MuSiQue/HotpotQA, can be added with the existing text scoring |
| Neo4j `text2cypher-2024v1` | Apache-2.0 (dataset card) | Partly LLM-generated without manual validation (criticised in the IBM "Mind the Query" paper) |
| SynthCypher | CC BY-NC-SA 4.0, gated | Non-commercial; excluded |
| Mind the Query (IBM) | not found on Hugging Face under that name | Dataset location unconfirmed |

## Scoring

`final.kind = "number"`: last number of the answer against `ref` with relative tolerance 1 % (at least 0.01).
`final.kind = "text"`: normalised (case, punctuation, articles) containment with word boundaries of the answer or one of its aliases;
`all: true` for list answers requires every item.

## Reproduce

    adapters.py --root <dir with tatqa/ finqa/ gsm8k/ musique/ hotpot/ cypherbench/> --out ext_tasks.jsonl --graph-out ext_graph.json --per-dataset 150
    load_graph_facts.py --facts ext_graph.json         # CypherBench graph in the GraphRAG schema (stack job: LOAD_FACTS)
