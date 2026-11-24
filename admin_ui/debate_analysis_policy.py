"""Admin-side validation mirror for the versioned debate-analysis policy.

The admin image has an isolated Docker build context and cannot import the
orchestrator's ``services`` package. Contract-parity tests in the repository
therefore verify this boundary model against the runtime model.
"""

from __future__ import annotations

from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class AdminDebateAnalysisPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: Literal["1.0"] = "1.0"
    activation: Literal["disabled", "enabled"] = "disabled"
    rubric_version: str = Field(default="v1", pattern=r"^v\d{1,3}$")
    judge_count: int = Field(default=2, ge=1, le=3)
    samples_per_judge: int = Field(default=3, ge=1, le=5)
    max_model_calls: int = Field(default=60, ge=1, le=400)
    max_turns: int = Field(default=60, ge=2, le=200)
    research_enabled: bool = True
    fallback: Literal["standard", "fail"] = "standard"

    @model_validator(mode="after")
    def _validate_bounds(self) -> "AdminDebateAnalysisPolicy":
        if self.max_model_calls < self.judge_count * self.samples_per_judge * 2:
            raise ValueError(
                "max_model_calls must cover judge_count * samples_per_judge * 2"
            )
        return self


def validate_debate_analysis_policy(raw: Mapping[str, Any] | None) -> dict[str, Any]:
    candidate: Mapping[str, Any] = raw if raw is not None else {}
    if not isinstance(candidate, Mapping):
        raise ValueError("debate_analysis must be an object")
    try:
        return AdminDebateAnalysisPolicy.model_validate(dict(candidate)).model_dump(mode="json")
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in exc.errors(include_input=False)
        )
        raise ValueError(f"invalid debate_analysis: {details}") from exc
