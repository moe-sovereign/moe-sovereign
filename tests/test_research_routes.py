"""Research checkpoint routes: gating, thread-id validation, history and fork semantics (fake graph)."""
import asyncio
from types import SimpleNamespace

import pytest

from routes import research as R

TID = "research-abcdef123456"


class FakeGraph:
    def __init__(self):
        self.snaps = [
            SimpleNamespace(config={"configurable": {"checkpoint_id": "c2"}}, parent_config={"configurable": {"checkpoint_id": "c1"}},
                            metadata={"step": 2, "source": "loop"}, next=("synthesizer",), created_at="t2",
                            values={"plan": [{"task": "x"}], "secret_blob": object()}),
            SimpleNamespace(config={"configurable": {"checkpoint_id": "c1"}}, parent_config=None,
                            metadata={"step": 1, "source": "loop"}, next=("planner",), created_at="t1", values={"input": "q"}),
        ]
        self.invocations, self.updates = [], []

    async def aget_state_history(self, config):
        if config["configurable"]["thread_id"] != TID:
            return
        for s in self.snaps:
            yield s

    async def aget_state(self, config):
        cid = config["configurable"].get("checkpoint_id")
        if cid is None:
            return SimpleNamespace(config={"configurable": {"checkpoint_id": "c3"}}, values={"final_response": "done"})
        return next((s for s in self.snaps if s.config["configurable"]["checkpoint_id"] == cid), None)

    async def aupdate_state(self, config, values):
        self.updates.append(values)
        return {"configurable": {**config["configurable"], "checkpoint_id": "c2u"}}

    async def ainvoke(self, inp, config):
        self.invocations.append((inp, config))
        return {"final_response": "forked answer"}


def test_thread_id_header_only_honoured_when_enabled_and_well_formed():
    assert R.research_thread_id(TID, enabled=False) is None
    assert R.research_thread_id(TID, enabled=True) == TID
    assert R.research_thread_id("other-thread-123456", enabled=True) is None
    assert R.research_thread_id("research-x;DROP", enabled=True) is None
    assert R.research_thread_id(None, enabled=True) is None


@pytest.mark.parametrize("enabled,graph,tid,status", [
    (False, FakeGraph(), TID, 404),
    (True, None, TID, 503),
    (True, FakeGraph(), "not-a-research-id", 400),
])
def test_history_rejections(enabled, graph, tid, status):
    with pytest.raises(R.ResearchError) as exc:
        asyncio.run(R.history(graph, tid, [], enabled))
    assert exc.value.status == status


def test_history_lists_checkpoints_and_only_requested_values():
    out = asyncio.run(R.history(FakeGraph(), TID, ["plan", "missing"], True))
    assert [c["checkpoint_id"] for c in out["checkpoints"]] == ["c2", "c1"]
    assert out["checkpoints"][0]["parent_checkpoint_id"] == "c1"
    assert out["checkpoints"][0]["next"] == ["synthesizer"]
    assert out["checkpoints"][0]["values"] == {"plan": [{"task": "x"}]}
    assert out["checkpoints"][1]["values"] == {}


def test_history_unknown_thread_is_404():
    with pytest.raises(R.ResearchError) as exc:
        asyncio.run(R.history(FakeGraph(), "research-unknown0000", [], True))
    assert exc.value.status == 404


def test_fork_resumes_from_checkpoint_without_new_input():
    g = FakeGraph()
    out = asyncio.run(R.fork(g, TID, "c1", {}, 5.0, True))
    assert g.invocations == [(None, {"configurable": {"thread_id": TID, "checkpoint_id": "c1"}})]
    assert out == {"thread_id": TID, "forked_from": "c1", "final_checkpoint_id": "c3", "final_response": "forked answer"}


def test_fork_applies_only_allowlisted_overrides():
    g = FakeGraph()
    asyncio.run(R.fork(g, TID, "c2", {"query_temperature": 0.9}, 5.0, True))
    assert g.updates == [{"query_temperature": 0.9}]
    assert g.invocations[0][1]["configurable"]["checkpoint_id"] == "c2u"
    with pytest.raises(R.ResearchError) as exc:
        asyncio.run(R.fork(FakeGraph(), TID, "c2", {"local_only_routing": False}, 5.0, True))
    assert exc.value.status == 400


def test_fork_unknown_checkpoint_and_disabled():
    with pytest.raises(R.ResearchError) as exc:
        asyncio.run(R.fork(FakeGraph(), TID, "nope", {}, 5.0, True))
    assert exc.value.status == 404
    g = FakeGraph()
    with pytest.raises(R.ResearchError) as exc:
        asyncio.run(R.fork(g, TID, "c1", {}, 5.0, False))
    assert exc.value.status == 404 and g.invocations == []


def test_fork_timeout_maps_to_504():
    class Slow(FakeGraph):
        async def ainvoke(self, inp, config):
            await asyncio.sleep(1)

    with pytest.raises(R.ResearchError) as exc:
        asyncio.run(R.fork(Slow(), TID, "c1", {}, 0.01, True))
    assert exc.value.status == 504
