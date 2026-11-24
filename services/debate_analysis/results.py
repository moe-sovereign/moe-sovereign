"""Result models of a debate analysis. No transcript text is stored here."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .contracts import Axis, AxisScore, DebateProfile


class CriterionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: float | None = Field(default=None, ge=0.0, le=10.0)
    dispersion: float = Field(default=0.0, ge=0.0, le=10.0)
    runs: int = Field(default=0, ge=0)


class FallacyFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fallacy_id: str
    turn_indices: list[int]
    agreement: float = Field(ge=0.0, le=1.0)


class Misstep(BaseModel):
    """A statement that likely cost the speaker ("better left unsaid")."""

    model_config = ConfigDict(extra="forbid")

    turn_index: int = Field(ge=0)
    note: str
    agreement: float = Field(ge=0.0, le=1.0)


class SpeakerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speaker: str
    axes: dict[Axis, AxisScore]
    criteria: dict[str, CriterionResult]
    fallacies: list[FallacyFinding] = Field(default_factory=list)
    missteps: list[Misstep] = Field(default_factory=list)
    overall: float | None = None


class DebateAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rubric_version: str
    rubric_status: str
    catalog_version: str
    profile: DebateProfile
    frame_date_defaulted: bool = False
    transcript_sha256: str
    speakers: dict[str, SpeakerResult]
    winners: dict[str, str]
    overall_winner: str
    overall_axes_used: list[Axis]
    judge_ids: list[str]
    runs_valid: int = Field(ge=0)
    runs_total: int = Field(ge=0)
    model_calls: int = Field(ge=0)
    mean_dispersion: float = Field(default=0.0, ge=0.0, le=10.0)
    not_assessable_criteria: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class DebateComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comparable: bool
    reasons: list[str] = Field(default_factory=list)
    axis_means_a: dict[Axis, float | None] = Field(default_factory=dict)
    axis_means_b: dict[Axis, float | None] = Field(default_factory=dict)
    axis_delta: dict[Axis, float | None] = Field(default_factory=dict)
    better_by_axis: dict[Axis, str] = Field(default_factory=dict)
