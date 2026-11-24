import json
from types import SimpleNamespace

import pytest

from services.debate_analysis.adapters import make_judge_llm_call, make_research_call
from services.debate_analysis.contracts import DebateAnalysisPolicy, DebateProfile
from services.debate_analysis.rubric import load_fallacy_catalog, load_rubric
from services.debate_analysis.runtime import JudgeAbort, analyze_debate
from services.debate_analysis.store import build_log_row
from services.debate_analysis.transcript import normalize_transcript
from datetime import date

TRANSCRIPT = normalize_transcript(
    'Anna: Goethe schrieb: „Die Botschaft hör ich wohl, allein mir fehlt der Glaube“. Das Gesetz wurde im Jahr 1808 vom Landtag verabschiedet.\n'
    "Bernd: Der nächste Gegner ist immer der schwerste Gegner."
)


class EgressDenied(Exception):
    pass


@pytest.mark.asyncio
async def test_judge_call_sets_model_and_deterministic_options():
    seen = {}

    async def invoke(state, prompt, **kwargs):
        seen.update(state=state, kwargs=kwargs)
        return SimpleNamespace(content="ok")

    call = make_judge_llm_call(
        {"local_only_routing": True, "user_id": "u1"},
        invoke=invoke,
        endpoint_overrides={"m1": {"url": "http://x", "token": "t"}},
    )
    assert await call("m1", "p") == "ok"
    assert seen["state"]["judge_model_override"] == "m1"
    assert seen["state"]["judge_url_override"] == "http://x"
    assert seen["state"]["local_only_routing"] is True
    assert seen["kwargs"] == {"max_retries": 1, "temperature": 0.0}
    assert await call("m2", "p") == "ok"
    assert seen["state"]["judge_url_override"] == ""


@pytest.mark.asyncio
async def test_judge_unavailable_and_empty_content_raise():
    async def unavailable(state, prompt, **kw):
        return SimpleNamespace(content="[Judge unavailable after 1 retries: x]")

    async def empty(state, prompt, **kw):
        return SimpleNamespace(content="  ")

    for invoke in (unavailable, empty):
        with pytest.raises(RuntimeError):
            await make_judge_llm_call({}, invoke=invoke)("m", "p")


@pytest.mark.asyncio
async def test_egress_denied_aborts_the_whole_analysis():
    async def invoke(state, prompt, **kw):
        raise EgressDenied("blocked")

    call = make_judge_llm_call({"local_only_routing": True}, invoke=invoke)
    with pytest.raises(JudgeAbort):
        await analyze_debate(
            TRANSCRIPT, DebateProfile(frame_date="2026-09-01"),
            DebateAnalysisPolicy(activation="enabled", judge_count=1, samples_per_judge=3, max_model_calls=6),
            load_rubric("v1"), load_fallacy_catalog("v1"), call, ["m"],
        )


@pytest.mark.asyncio
async def test_research_queries_quotes_and_numbers_without_frame():
    queries = []

    async def search(q):
        queries.append(q)
        return "snippet for " + q

    notes = await make_research_call(search)(TRANSCRIPT, None)
    assert any(q.startswith('"Die Botschaft') for q in queries)
    assert any("1808" in q for q in queries)
    assert notes and all(n.startswith("[query:") for n in notes)


@pytest.mark.asyncio
async def test_research_historical_frame_states_its_limit():
    async def search(q):  # must not be called for a historical frame
        raise AssertionError("searched")

    notes = await make_research_call(search, today=date(2026, 9, 26))(
        TRANSCRIPT, DebateProfile(frame_date="1975-05-01")
    )
    assert len(notes) == 1 and "No contemporaneous sources" in notes[0]


@pytest.mark.asyncio
async def test_research_raises_only_when_every_query_fails():
    async def broken(q):
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        await make_research_call(broken)(TRANSCRIPT, None)

    calls = iter([RuntimeError("down"), "ok"])

    async def partial(q):
        r = next(calls)
        if isinstance(r, Exception):
            raise r
        return r

    assert await make_research_call(partial)(TRANSCRIPT, None)


@pytest.mark.asyncio
async def test_log_row_contains_scores_but_no_transcript_text():
    rubric = load_rubric("v1")
    payload = json.dumps({"speakers": {
        s: {"criteria": {c.id: {"score": 6.0, "evidence": ["Goethe schrieb geheim"]} for c in rubric.criteria},
            "fallacies": [], "missteps": [{"turn_index": 0, "note": "Die Botschaft"}]}
        for s in ("Anna", "Bernd")
    }})

    async def llm(judge, prompt):
        return payload

    async def research(t, p):
        return ["note"]

    analysis = await analyze_debate(
        TRANSCRIPT, DebateProfile(frame_date="2026-09-01"),
        DebateAnalysisPolicy(activation="enabled", judge_count=1, samples_per_judge=1, max_model_calls=4),
        rubric, load_fallacy_catalog("v1"), llm, ["m"], research=research,
    )
    row = build_log_row(analysis, request_id="r", user_id="u", template_id="t")
    dumped = json.dumps(row)
    assert row["transcript_sha256"] == TRANSCRIPT.sha256 and row["rubric_version"] == "v1"
    assert "Goethe" not in dumped and "Botschaft" not in dumped and "geheim" not in dumped
    assert json.loads(row["speakers"])["Anna"]["misstep_turns"] == [0]
