# Debate Analysis

Status: **in development; implemented in the repository, not yet validated.**
The rubric is a draft derived from practitioner input and has not been
calibrated against human ratings. Do not present its scores as measured
accuracy.

Debate analysis evaluates a finished debate transcript. It is separate from
[Adaptive Deliberation](deliberation.md): deliberation uses a debate to reach
an answer, debate analysis judges how a debate was conducted and who argued
better. It does not update model weights and does not produce legal
assessments.

## Principles

- **Three separate axes.** Every speaker gets a score per axis, never only one
  number: `substance` (is it correct and informed), `argumentation` (how well is
  the case built and defended) and `audience_impact` (how does it land). A
  speaker can be factually right and still lose on impact.
- **Content and reception frame are separate.** The same statement is received
  differently depending on time, culture and audience. The frame is an explicit
  input (`DebateProfile`). It enters only the `audience_impact` phase: the
  content phase prompt contains no frame, so the frame cannot influence
  `substance` or `argumentation`. Re-analysing under another frame ("how would
  this land in 1990, today, in the US") re-runs only the audience phase.
- **Reception is an estimate.** Zeitgeist is a dated, evidence-backed estimate
  with a confidence, never a fact and never a criterion for correctness. It is
  judged as valuation and acceptance, not as legal consequence.
- **No guessing.** A criterion that cannot be judged from the available
  material is reported as *not assessable*. Criteria have required
  capabilities; a missing capability forces "not assessable" regardless of what
  a judge returned.
- **Reproducibility.** The rubric is versioned data, judges run at temperature
  0, several judges and samples are combined by the median, and everything after
  the judge call (aggregation, winners, overall value) is deterministic.
  Judge disagreement is reported as dispersion and lowers confidence.

## Inputs

| Input | Meaning |
|---|---|
| Transcript | Plain text (`Speaker: text`, continuation lines allowed) or one of the known JSON layouts. Limited to 400,000 characters and the policy's `max_turns`. |
| `DebateProfile` | `frame_date` (ISO date, default: today, reported as defaulted), `culture`, `audience`, `audience_level`, `goal` (`substance`, `opinion`, `mixed`), `language`. |
| Template policy | `debate_analysis` field of an Expert Template (below). |

## Rubric v1 (draft)

Defined in `configs/debate_rubric_v1.yaml`; fallacy catalogue in
`configs/debate_fallacies_v1.yaml`. Criteria per axis:

| Axis | Criteria | Needs |
|---|---|---|
| `substance` | `claim_accuracy`, `quote_accuracy`, `preparedness`, `context_fit_of_evidence` | Claim and quotation checks need retrieval, not model memory |
| `argumentation` | `structure`, `fallacy_freedom`, `responsiveness`, `tactical_awareness`, `concession_management` | none |
| `audience_impact` | `audience_fit`, `reception_in_frame`, `comprehensibility`, `composure_and_retort` | profile; `reception_in_frame` also retrieval; `composure_and_retort` needs timing or audio/video and is not assessable from text |

An axis is scored only if at least half of its criterion weight is assessable;
weights are renormalised over the assessable criteria. Without research,
`substance` is therefore usually *not assessable* by design. The overall value
weights the axes by the debate goal and uses only axes assessable for every
speaker. It is a convenience aggregate; the axis scores are the result.

Besides scores, the analysis reports fallacies (only clear cases, kept when a
majority of runs agree and the identifier exists in the catalogue) and
*missteps*: statements that likely cost the speaker ("better left unsaid").

## Template policy

```json
{
  "schema_version": "1.0",
  "activation": "enabled",
  "rubric_version": "v1",
  "judge_count": 2,
  "samples_per_judge": 3,
  "max_model_calls": 60,
  "max_turns": 60,
  "research_enabled": true,
  "fallback": "standard"
}
```

The contract is strict (unknown fields and wrong types are rejected). Every
judge sample runs two phases, so `max_model_calls` must be at least
`judge_count * samples_per_judge * 2`; the budget is a hard cap. New templates
default to `disabled`. An invalid stored policy leaves the analysis disabled
for that template and does not affect chat requests. The template editors expose
only the activation switch; all other fields are preserved on save.

## API

`POST /v1/debate-analysis` (authenticated like the other `/v1` routes).

```json
{
  "template": "<template name or id>",
  "transcript": "Anna: ...\nBernd: ...",
  "profile": {"frame_date": "2026-09-01", "culture": "DE", "audience": "general_public"}
}
```

Send `profiles` (at most four) instead of `profile` to analyse one transcript
under several frames; the content phase runs once. The response carries
`status: "in_development"` and one analysis per frame, including rubric
version, judge identifiers, valid/total runs, dispersion, winners per axis,
the list of not-assessable criteria and notes such as a defaulted date.

Rejections: 401 (no valid key), 403 (template not authorised, or egress denied
by `local_only`), 409 (analysis not enabled, or no judge configured), 422
(invalid input or policy), 503 (no valid judge output), 504 (deadline).

Judge models come from `DEBATE_ANALYSIS_JUDGE_MODELS` (comma-separated); if
unset, the template's judge model is used. Judge calls go through the platform
judge invocation, so `local_only` egress rules apply. With `local_only`, the
public DuckDuckGo fallback of the web search is disabled.

## Research and its limits

With research enabled, the content phase receives frame-free search snippets
for quotations and numeric claims. The audience phase receives snippets on
current reception. For a frame date more than a year in the past, no
contemporaneous sources are retrieved and the note says so; reception for
historical frames is then largely *not assessable* rather than guessed.
Search snippets are untrusted data; the judge prompt says to verify only
against them. Query construction is deliberately simple (quoted passages and
sentences with numbers) and will miss claims.

## Persistence

`debate_analysis_log` (PostgreSQL, created on first insert like
`pipeline_quality_log`) stores rubric and catalogue versions, the profile, judge
identifiers, per-speaker axis scores, criterion results, fallacy findings and
misstep turn indices, winners, run counts, dispersion, not-assessable
criteria and the transcript SHA-256. It stores no transcript text and no model
output. Speaker labels are stored because scores are unreadable without them;
pass pseudonyms if labels are personal data. A failed insert is logged and does
not fail the request.

## Not implemented

- Audio/video analysis (composure, retort speed) — text only.
- Knowledge-graph storage of the fallacy catalogue and of analyses. The
  catalogue is configuration; no runtime path reads it from the graph.
- Recency filters and news categories in the web search; frame-dependent
  research beyond a plain query.
- Calibration against human ratings and any accuracy claim. Until a documented
  calibration exists, treat scores as structured, reproducible judgements of a
  draft rubric, not as validated measurements.
- Debate roles inside the live deliberation.
