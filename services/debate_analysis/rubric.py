"""Versioned rubric and fallacy catalogue, plus deterministic aggregation.

Scoring by the LLM judges is stochastic; everything after it is not. Given the
same per-criterion scores, the same rubric version and the same capabilities,
the aggregation below always yields the same axis scores. Criteria whose
required capability is missing are forced to "not assessable" here, whatever
a judge returned, so the system never fills a gap with a guess.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .contracts import AUDIENCE_AXES, Axis, AxisScore, DebateAnalysisError

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs"
_VERSION_RE = re.compile(r"^v\d{1,3}$")

# Minimum share of an axis's criterion weight that must be assessable.
MIN_COVERAGE = 0.5
# Score difference below which two speakers are reported as a tie.
TIE_MARGIN = 0.5

Capability = str  # "retrieval", "profile", "timing_or_media"


class Criterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    axis: Axis
    weight: float = Field(gt=0.0, le=1.0)
    description: str = Field(min_length=1)
    anchors: dict[str, str]
    requires: list[Capability] = Field(default_factory=list)


class Rubric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(pattern=r"^v\d{1,3}$")
    status: str
    axes: dict[Axis, dict[str, str]]
    axis_weights_by_goal: dict[str, dict[Axis, float]]
    criteria: list[Criterion] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_weights(self) -> "Rubric":
        ids = [c.id for c in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("criterion ids must be unique")
        for axis in AUDIENCE_AXES:
            total = sum(c.weight for c in self.criteria if c.axis == axis)
            if abs(total - 1.0) > 1e-6:
                raise ValueError(f"criterion weights of axis {axis} must sum to 1")
        for goal, weights in self.axis_weights_by_goal.items():
            if set(weights) != set(AUDIENCE_AXES) or abs(sum(weights.values()) - 1.0) > 1e-6:
                raise ValueError(f"axis weights for goal {goal} must cover all axes and sum to 1")
        return self

    def by_axis(self, axis: Axis) -> list[Criterion]:
        return [c for c in self.criteria if c.axis == axis]

    def criterion(self, criterion_id: str) -> Criterion:
        for c in self.criteria:
            if c.id == criterion_id:
                return c
        raise KeyError(criterion_id)


class Fallacy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    name: str
    definition: str
    hint: str
    not_to_be_confused_with: str


class FallacyCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(pattern=r"^v\d{1,3}$")
    status: str
    fallacies: list[Fallacy] = Field(min_length=1)

    def ids(self) -> set[str]:
        return {f.id for f in self.fallacies}


def _load_yaml(name: str) -> dict:
    path = _CONFIG_DIR / name
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise DebateAnalysisError(f"cannot read {name}") from exc
    if not isinstance(data, dict):
        raise DebateAnalysisError(f"{name} must contain a mapping")
    return data


def load_rubric(version: str = "v1") -> Rubric:
    if not _VERSION_RE.match(version):
        raise DebateAnalysisError("invalid rubric version")
    try:
        return Rubric.model_validate(_load_yaml(f"debate_rubric_{version}.yaml"))
    except ValidationError as exc:
        raise DebateAnalysisError(f"invalid rubric {version}") from exc


def load_fallacy_catalog(version: str = "v1") -> FallacyCatalog:
    if not _VERSION_RE.match(version):
        raise DebateAnalysisError("invalid catalogue version")
    try:
        return FallacyCatalog.model_validate(_load_yaml(f"debate_fallacies_{version}.yaml"))
    except ValidationError as exc:
        raise DebateAnalysisError(f"invalid fallacy catalogue {version}") from exc


def criterion_available(criterion: Criterion, capabilities: Iterable[Capability]) -> bool:
    return set(criterion.requires) <= set(capabilities)


def aggregate_axis(
    axis: Axis,
    criterion_scores: Mapping[str, float | None],
    rubric: Rubric,
    dispersion: Mapping[str, float] | None = None,
    evidence: Mapping[str, list[str]] | None = None,
) -> AxisScore:
    """Weighted mean over the assessable criteria of one axis.

    Weights are renormalised over the assessable criteria. Confidence is the
    share of assessable weight, reduced by judge disagreement. An axis with
    less than ``MIN_COVERAGE`` assessable weight carries no score.
    """

    criteria = rubric.by_axis(axis)
    total = sum(c.weight for c in criteria)
    assessable = [c for c in criteria if criterion_scores.get(c.id) is not None]
    weight = sum(c.weight for c in assessable)
    coverage = weight / total if total else 0.0
    if not assessable or coverage < MIN_COVERAGE:
        return AxisScore(axis=axis, not_assessable=True)

    score = sum(c.weight * float(criterion_scores[c.id]) for c in assessable) / weight
    spread = 0.0
    if dispersion:
        spread = sum(c.weight * dispersion.get(c.id, 0.0) for c in assessable) / weight
    confidence = max(0.0, min(1.0, coverage * (1.0 - min(1.0, spread / 10.0))))
    notes: list[str] = []
    if evidence:
        for c in assessable:
            for item in evidence.get(c.id, [])[:1]:
                notes.append(f"{c.id}: {item}")
    return AxisScore(
        axis=axis,
        score=round(score, 3),
        confidence=round(confidence, 3),
        evidence=notes[:8],
    )


def pick_winner(scores: Mapping[str, AxisScore]) -> str:
    """Return the winning speaker, ``"tie"`` or ``"not_assessable"``."""

    if not scores or any(s.not_assessable for s in scores.values()):
        return "not_assessable"
    ranked = sorted(scores.items(), key=lambda kv: (-float(kv[1].score), kv[0]))
    if len(ranked) > 1 and float(ranked[0][1].score) - float(ranked[1][1].score) < TIE_MARGIN:
        return "tie"
    return ranked[0][0]


def overall_scores(
    per_speaker_axes: Mapping[str, Mapping[Axis, AxisScore]],
    goal: str,
    rubric: Rubric,
) -> tuple[dict[str, float], list[Axis]]:
    """Convenience aggregate over the axes assessable for every speaker."""

    weights = rubric.axis_weights_by_goal.get(goal) or rubric.axis_weights_by_goal["mixed"]
    usable = [
        axis
        for axis in AUDIENCE_AXES
        if per_speaker_axes
        and all(not axes[axis].not_assessable for axes in per_speaker_axes.values())
    ]
    if not usable:
        return {}, []
    norm = sum(weights[a] for a in usable)
    result = {
        speaker: round(
            sum(weights[a] * float(axes[a].score) for a in usable) / norm, 3
        )
        for speaker, axes in per_speaker_axes.items()
    }
    return result, usable
