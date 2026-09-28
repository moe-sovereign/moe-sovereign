import json
from datetime import date

import pytest

from services.debate_analysis.contracts import (
    DebateAnalysisError,
    DebateAnalysisPolicy,
    DebateProfile,
)
from services.debate_analysis.rubric import (
    aggregate_axis,
    load_fallacy_catalog,
    load_rubric,
    pick_winner,
)
from services.debate_analysis.runtime import (
    DebateAnalysisExecutionError,
    analyze_debate,
    analyze_debate_frames,
    compare_debates,
    parse_judge_output,
)
from services.debate_analysis.transcript import normalize_transcript

RUBRIC = load_rubric("v1")
CATALOG = load_fallacy_catalog("v1")
TRANSCRIPT = normalize_transcript(
    "Anna: Die Steuer senkt den Konsum, weil Preise steigen.\n"
    "Bernd: Das ist wie bei Äpfeln und Birnen, du vergleichst Falsches.\n"
    "Anna: Ich gebe zu, meine Zahlen sind von 2019.\n"
    "Bernd: Danke, das war die Frage."
)
POLICY = DebateAnalysisPolicy(activation="enabled", judge_count=2, samples_per_judge=1, max_model_calls=20)
PROFILE = DebateProfile(frame_date="2026-09-01")


def judge_json(a=7.0, b=5.0, fallacies=(), missteps=()):
    def speaker(score, fal, mis):
        return {
            "criteria": {
                c.id: {"score": score, "evidence": [f"reason {c.id}"]} for c in RUBRIC.criteria
            },
            "fallacies": [{"turn_index": t, "fallacy_id": f} for t, f in fal],
            "missteps": [{"turn_index": t, "note": n} for t, n in mis],
        }

    return json.dumps(
        {"speakers": {"Anna": speaker(a, fallacies, missteps), "Bernd": speaker(b, (), ())}}
    )


class FakeLLM:
    def __init__(self, reply=None):
        self.reply = reply or judge_json()
        self.prompts = []

    async def __call__(self, judge, prompt):
        self.prompts.append((judge, prompt))
        return self.reply(judge, prompt) if callable(self.reply) else self.reply


async def no_research(transcript, profile):
    return ["note"]


def test_rubric_and_catalog_are_valid_and_versioned():
    assert RUBRIC.version == "v1" and RUBRIC.status == "draft"
    assert {f.id for f in CATALOG.fallacies} >= {"ad_hominem", "false_analogy", "quote_phrase_confusion"}
    with pytest.raises(DebateAnalysisError):
        load_rubric("../etc")
    with pytest.raises(DebateAnalysisError):
        load_rubric("v99")


def test_aggregate_axis_renormalises_and_is_deterministic():
    scores = {"preparedness": 8.0, "context_fit_of_evidence": 6.0, "claim_accuracy": None, "quote_accuracy": None}
    a = aggregate_axis("substance", scores, RUBRIC)
    b = aggregate_axis("substance", scores, RUBRIC)
    assert a == b
    assert a.not_assessable  # only 40% of the axis weight is assessable
    scores["claim_accuracy"] = 9.0
    assert not aggregate_axis("substance", scores, RUBRIC).not_assessable


def test_pick_winner_tie_and_not_assessable():
    from services.debate_analysis.contracts import AxisScore

    hi = AxisScore(axis="substance", score=8.0, confidence=1.0)
    lo = AxisScore(axis="substance", score=6.0, confidence=1.0)
    na = AxisScore(axis="substance", not_assessable=True)
    assert pick_winner({"A": hi, "B": lo}) == "A"
    assert pick_winner({"A": hi, "B": AxisScore(axis="substance", score=7.8, confidence=1.0)}) == "tie"
    assert pick_winner({"A": hi, "B": na}) == "not_assessable"


def test_parse_judge_output_rejects_garbage_and_clips():
    assert parse_judge_output("```json\n" + judge_json() + "\n```").speakers["Anna"]
    with pytest.raises(DebateAnalysisExecutionError):
        parse_judge_output("no json here")
    with pytest.raises(DebateAnalysisExecutionError):
        parse_judge_output('{"speakers": {"A": {"criteria": {"x": {"score": 11}}}}}')
    long = json.dumps({"speakers": {"A": {"criteria": {"x": {"score": 1, "evidence": ["y" * 5000]}}}}})
    assert len(parse_judge_output(long).speakers["A"].criteria["x"].evidence[0]) == 400


@pytest.mark.asyncio
async def test_substance_needs_retrieval_and_is_never_guessed():
    llm = FakeLLM()
    result = await analyze_debate(TRANSCRIPT, PROFILE, POLICY, RUBRIC, CATALOG, llm, ["j1", "j2"])
    assert result.speakers["Anna"].axes["substance"].not_assessable
    assert result.winners["substance"] == "not_assessable"
    assert "claim_accuracy" in result.not_assessable_criteria
    # composure needs audio/video: forced empty although the judge scored it
    assert result.speakers["Anna"].criteria["composure_and_retort"].score is None
    assert "composure_and_retort" in result.not_assessable_criteria


@pytest.mark.asyncio
async def test_with_research_all_three_axes_are_scored():
    llm = FakeLLM()
    result = await analyze_debate(
        TRANSCRIPT, PROFILE, POLICY, RUBRIC, CATALOG, llm, ["j1", "j2"], research=no_research
    )
    assert not result.speakers["Anna"].axes["substance"].not_assessable
    assert result.winners["argumentation"] == "Anna"
    assert result.overall_winner == "Anna"
    assert result.runs_valid == 4 and result.runs_total == 4 and result.model_calls == 4
    assert result.rubric_version == "v1" and result.transcript_sha256 == TRANSCRIPT.sha256


@pytest.mark.asyncio
async def test_content_prompt_never_contains_the_frame():
    llm = FakeLLM()
    profile = DebateProfile(frame_date="1975-05-01", culture="US-ZZ")
    await analyze_debate(TRANSCRIPT, profile, POLICY, RUBRIC, CATALOG, llm, ["j1"], research=no_research)
    content = [p for _, p in llm.prompts if "Reception frame" not in p]
    audience = [p for _, p in llm.prompts if "Reception frame" in p]
    assert content and audience
    assert all("1975-05-01" not in p and "US-ZZ" not in p for p in content)
    assert all("1975-05-01" in p and "US-ZZ" in p for p in audience)


@pytest.mark.asyncio
async def test_reframing_reruns_only_the_audience_phase():
    llm = FakeLLM()
    profiles = [DebateProfile(frame_date="1990-01-01"), DebateProfile(frame_date="2026-09-01")]
    results = await analyze_debate_frames(
        TRANSCRIPT, profiles, POLICY, RUBRIC, CATALOG, llm, ["j1"], research=no_research
    )
    content_calls = [p for _, p in llm.prompts if "Reception frame" not in p]
    assert len(results) == 2 and len(content_calls) == 1
    a, b = results
    for speaker in ("Anna", "Bernd"):
        assert a.speakers[speaker].axes["substance"] == b.speakers[speaker].axes["substance"]
        assert a.speakers[speaker].axes["argumentation"] == b.speakers[speaker].axes["argumentation"]


@pytest.mark.asyncio
async def test_default_frame_date_is_reported():
    result = await analyze_debate(
        TRANSCRIPT, DebateProfile(), POLICY, RUBRIC, CATALOG, FakeLLM(), ["j1"],
        research=no_research, today=date(2026, 9, 26),
    )
    assert result.profile.frame_date == "2026-09-26" and result.frame_date_defaulted
    assert "frame_date defaulted to today" in result.notes


@pytest.mark.asyncio
async def test_findings_follow_majority_and_catalogue():
    good = judge_json(fallacies=[(1, "false_analogy"), (2, "made_up_fallacy")], missteps=[(2, "admitted stale data")])
    llm = FakeLLM(good)
    result = await analyze_debate(
        TRANSCRIPT, PROFILE, POLICY, RUBRIC, CATALOG, llm, ["j1", "j2"], research=no_research
    )
    anna = result.speakers["Anna"]
    assert [f.fallacy_id for f in anna.fallacies] == ["false_analogy"]
    assert anna.fallacies[0].agreement == 1.0
    assert anna.missteps[0].turn_index == 2


@pytest.mark.asyncio
async def test_minority_finding_is_dropped_and_dispersion_reported():
    replies = iter([judge_json(a=9.0, fallacies=[(1, "straw_man")]), judge_json(a=3.0), judge_json(a=3.0), judge_json(a=3.0)])
    llm = FakeLLM(lambda judge, prompt: next(replies))
    policy = POLICY.model_copy(update={"judge_count": 2})
    result = await analyze_debate(TRANSCRIPT, PROFILE, policy, RUBRIC, CATALOG, llm, ["j1", "j2"], research=no_research)
    assert result.speakers["Anna"].fallacies == []  # 1 of 2 content runs is not a majority
    assert result.speakers["Anna"].criteria["structure"].dispersion == 6.0
    assert result.mean_dispersion > 0


@pytest.mark.asyncio
async def test_invalid_and_invented_speaker_runs_are_dropped():
    replies = iter(["not json", judge_json().replace('"Bernd"', '"Carla"'), judge_json(), judge_json()])
    llm = FakeLLM(lambda judge, prompt: next(replies))
    result = await analyze_debate(TRANSCRIPT, PROFILE, POLICY, RUBRIC, CATALOG, llm, ["j1", "j2"], research=no_research)
    assert result.runs_total == 4 and result.runs_valid == 2


@pytest.mark.asyncio
async def test_all_runs_invalid_raises():
    with pytest.raises(DebateAnalysisExecutionError):
        await analyze_debate(TRANSCRIPT, PROFILE, POLICY, RUBRIC, CATALOG, FakeLLM("nope"), ["j1"])


@pytest.mark.asyncio
async def test_model_failure_does_not_abort_and_budget_is_hard():
    async def flaky(judge, prompt):
        if judge == "j1":
            raise RuntimeError("backend down")
        return judge_json()

    result = await analyze_debate(TRANSCRIPT, PROFILE, POLICY, RUBRIC, CATALOG, flaky, ["j1", "j2"], research=no_research)
    assert result.runs_valid == 2 and result.runs_total == 4

    small = DebateAnalysisPolicy(activation="enabled", judge_count=1, samples_per_judge=3, max_model_calls=6)
    llm = FakeLLM()
    result = await analyze_debate(TRANSCRIPT, PROFILE, small, RUBRIC, CATALOG, llm, ["j1"], research=no_research)
    assert len(llm.prompts) <= 6 and result.model_calls <= 6


@pytest.mark.asyncio
async def test_disabled_policy_and_limits_are_enforced():
    with pytest.raises(DebateAnalysisError):
        await analyze_debate(TRANSCRIPT, PROFILE, DebateAnalysisPolicy(), RUBRIC, CATALOG, FakeLLM(), ["j1"])
    tight = POLICY.model_copy(update={"max_turns": 2})
    with pytest.raises(DebateAnalysisError):
        await analyze_debate(TRANSCRIPT, PROFILE, tight, RUBRIC, CATALOG, FakeLLM(), ["j1"])


@pytest.mark.asyncio
async def test_failed_research_degrades_instead_of_guessing():
    async def broken(transcript, profile):
        raise RuntimeError("search down")

    result = await analyze_debate(TRANSCRIPT, PROFILE, POLICY, RUBRIC, CATALOG, FakeLLM(), ["j1"], research=broken)
    assert result.speakers["Anna"].axes["substance"].not_assessable
    assert any("research unavailable" in n for n in result.notes)


@pytest.mark.asyncio
async def test_compare_requires_same_rubric_and_frame():
    llm = FakeLLM()
    a = await analyze_debate(TRANSCRIPT, PROFILE, POLICY, RUBRIC, CATALOG, llm, ["j1"], research=no_research)
    b = await analyze_debate(TRANSCRIPT, PROFILE, POLICY, RUBRIC, CATALOG, FakeLLM(judge_json(a=9, b=9)), ["j1"], research=no_research)
    cmp = compare_debates(a, b)
    assert cmp.comparable and cmp.better_by_axis["argumentation"] == "b"
    other = await analyze_debate(
        TRANSCRIPT, DebateProfile(frame_date="1990-01-01"), POLICY, RUBRIC, CATALOG, llm, ["j1"], research=no_research
    )
    refused = compare_debates(a, other)
    assert not refused.comparable and "different frame dates" in refused.reasons
