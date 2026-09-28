"""Strict, versioned contracts for debate analysis.

The analysis separates three axes so that a participant can be factually right
and still lose on audience impact (or the reverse). The audience frame
(date, culture, audience) is an explicit input: content is stable, its
reception is a function of the frame. The frame only ever influences the
``audience_impact`` axis, never ``substance``.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


Axis = Literal["substance", "argumentation", "audience_impact"]
AUDIENCE_AXES: tuple[Axis, ...] = ("substance", "argumentation", "audience_impact")

ActivationMode = Literal["disabled", "enabled"]
AudienceType = Literal["general_public", "expert", "mixed", "adversarial_panel"]
AudienceLevel = Literal["lay", "mixed", "expert"]
DebateGoal = Literal["substance", "opinion", "mixed"]
FallbackMode = Literal["standard", "fail"]


class DebateAnalysisError(ValueError):
    """Raised when an explicit debate-analysis input violates its contract."""


class DebateProfile(BaseModel):
    """Reception frame of a debate: who speaks to whom, when and for what.

    ``frame_date`` is an ISO date (YYYY-MM-DD). ``None`` means "today"; the
    runtime resolves it and reports the resolved date with the result.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    frame_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    culture: str = Field(default="DE", min_length=2, max_length=16)
    audience: AudienceType = "general_public"
    audience_level: AudienceLevel = "mixed"
    goal: DebateGoal = "mixed"
    language: str = Field(default="de", min_length=2, max_length=8)


class Turn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    index: int = Field(ge=0)
    speaker: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1)


class Transcript(BaseModel):
    """Normalised debate transcript. Only the hash is meant to be persisted."""

    model_config = ConfigDict(extra="forbid", strict=True)

    turns: list[Turn] = Field(min_length=2)
    source_format: str = Field(min_length=1, max_length=64)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @property
    def speakers(self) -> list[str]:
        seen: list[str] = []
        for turn in self.turns:
            if turn.speaker not in seen:
                seen.append(turn.speaker)
        return seen


class AxisScore(BaseModel):
    """Score of one axis. Unassessable axes carry no number instead of a guess."""

    model_config = ConfigDict(extra="forbid", strict=True)

    axis: Axis
    score: float | None = Field(default=None, ge=0.0, le=10.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    not_assessable: bool = False
    evidence: list[str] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def _validate_state(self) -> "AxisScore":
        if self.not_assessable and self.score is not None:
            raise ValueError("a not_assessable axis must not carry a score")
        if not self.not_assessable and self.score is None:
            raise ValueError("an assessable axis requires a score")
        return self


class DebateAnalysisPolicy(BaseModel):
    """Per-template policy for the debate-analysis mode."""

    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0"] = "1.0"
    activation: ActivationMode = "disabled"
    rubric_version: str = Field(default="v1", pattern=r"^v\d{1,3}$")
    judge_count: int = Field(default=2, ge=1, le=3)
    samples_per_judge: int = Field(default=3, ge=1, le=5)
    max_model_calls: int = Field(default=60, ge=1, le=400)
    max_turns: int = Field(default=60, ge=2, le=200)
    research_enabled: bool = True
    fallback: FallbackMode = "standard"

    @model_validator(mode="after")
    def _validate_bounds(self) -> "DebateAnalysisPolicy":
        # Every judge sample runs two phases: content and audience impact.
        if self.max_model_calls < self.judge_count * self.samples_per_judge * 2:
            raise ValueError(
                "max_model_calls must cover judge_count * samples_per_judge * 2"
            )
        return self


def _format_errors(exc: ValidationError) -> str:
    # Do not echo the payload: only field locations and validation messages.
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
        for item in exc.errors(include_input=False)
    )


def parse_debate_analysis_policy(
    raw: Mapping[str, Any] | None,
) -> DebateAnalysisPolicy:
    """Validate an explicit policy without coercing invalid template values."""

    if raw is None:
        return DebateAnalysisPolicy()
    if not isinstance(raw, Mapping):
        raise DebateAnalysisError("debate_analysis must be an object")
    try:
        return DebateAnalysisPolicy.model_validate(dict(raw))
    except ValidationError as exc:
        raise DebateAnalysisError(
            f"invalid debate_analysis: {_format_errors(exc)}"
        ) from exc


def parse_debate_profile(raw: Mapping[str, Any] | None) -> DebateProfile:
    if raw is None:
        return DebateProfile()
    if not isinstance(raw, Mapping):
        raise DebateAnalysisError("debate profile must be an object")
    try:
        return DebateProfile.model_validate(dict(raw))
    except ValidationError as exc:
        raise DebateAnalysisError(
            f"invalid debate profile: {_format_errors(exc)}"
        ) from exc
