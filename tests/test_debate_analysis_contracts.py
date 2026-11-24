from pathlib import Path

import pytest

from services.debate_analysis.contracts import (
    AxisScore,
    DebateAnalysisError,
    DebateAnalysisPolicy,
    parse_debate_analysis_policy,
    parse_debate_profile,
)
from services.debate_analysis.transcript import (
    TranscriptError,
    normalize_transcript,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_policy_defaults_are_disabled_and_valid():
    policy = parse_debate_analysis_policy(None)
    assert policy.activation == "disabled"
    assert policy.schema_version == "1.0"


def test_policy_rejects_unknown_fields_and_coercion():
    with pytest.raises(DebateAnalysisError):
        parse_debate_analysis_policy({"unknown": 1})
    with pytest.raises(DebateAnalysisError):
        parse_debate_analysis_policy({"judge_count": "2"})


def test_policy_budget_must_cover_judges_times_samples():
    with pytest.raises(DebateAnalysisError):
        parse_debate_analysis_policy(
            {"judge_count": 3, "samples_per_judge": 5, "max_model_calls": 10}
        )


def test_policy_error_does_not_echo_payload():
    with pytest.raises(DebateAnalysisError) as exc:
        parse_debate_analysis_policy({"rubric_version": "SECRET-VALUE"})
    assert "SECRET-VALUE" not in str(exc.value)


def test_profile_defaults_and_date_format():
    assert parse_debate_profile(None).culture == "DE"
    assert parse_debate_profile({"frame_date": "1975-05-01"}).frame_date == "1975-05-01"
    with pytest.raises(DebateAnalysisError):
        parse_debate_profile({"frame_date": "01.05.1975"})


def test_axis_score_never_guesses():
    assert AxisScore(axis="substance", score=7.5, confidence=0.6).score == 7.5
    assert AxisScore(axis="audience_impact", not_assessable=True).score is None
    with pytest.raises(ValueError):
        AxisScore(axis="substance", not_assessable=True, score=5.0)
    with pytest.raises(ValueError):
        AxisScore(axis="substance")


def test_plain_text_transcript_with_continuation_lines():
    transcript = normalize_transcript(
        "Anna: Ich behaupte X.\nweil Y gilt.\nBernd: Das stimmt nicht.\n"
    )
    assert transcript.source_format == "text"
    assert transcript.speakers == ["Anna", "Bernd"]
    assert transcript.turns[0].text == "Ich behaupte X. weil Y gilt."


def test_transcript_is_rejected_when_too_short_or_unlabelled():
    with pytest.raises(TranscriptError):
        normalize_transcript("Anna: nur ein Beitrag")
    with pytest.raises(TranscriptError):
        normalize_transcript("kein Sprecher\nAnna: Hallo")


def test_hash_is_stable_and_content_sensitive():
    a = normalize_transcript("A: eins\nB: zwei")
    b = normalize_transcript("A: eins\nB: zwei")
    c = normalize_transcript("A: eins\nB: drei")
    assert a.sha256 == b.sha256
    assert a.sha256 != c.sha256


@pytest.mark.parametrize(
    "name",
    [
        "live_real_25_expert_debate.json",
        "live_real_subagent_debate.json",
        "live_niche_labs_research_debate.json",
        "live_paper_audit_expert_debate.json",
    ],
)
def test_repository_debate_fixtures_normalise(name):
    path = REPO_ROOT / name
    if not path.exists():
        pytest.skip(f"fixture not present: {name}")
    transcript = normalize_transcript(path.read_bytes())
    assert len(transcript.turns) >= 2
    assert transcript.source_format.startswith("json:")
    assert all(t.speaker and t.text for t in transcript.turns)


def test_oversized_transcript_is_rejected():
    path = REPO_ROOT / "moe_sovereign_50_round_debate.json"
    if not path.exists():
        pytest.skip("fixture not present")
    with pytest.raises(TranscriptError, match="size limit"):
        normalize_transcript(path.read_bytes())


def test_default_policy_object_matches_parser():
    assert DebateAnalysisPolicy() == parse_debate_analysis_policy({})
