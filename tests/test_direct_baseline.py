"""Direct single-model baseline: same scoring as the driver, no gate, replicas used round-robin."""
import argparse
import asyncio
import importlib.util
import json
from pathlib import Path

import httpx

_PATH = Path(__file__).resolve().parents[1] / "benchmarks" / "orchestration_suite" / "direct_baseline.py"
_spec = importlib.util.spec_from_file_location("direct_baseline", _PATH)
db = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(db)

TASKS = [{"id": f"sql_calc-x-{i:012d}", "family": "sql_calc", "lang": "en", "prompts": {"en": f"q{i}"}, "final": {"ref": 42.0, "tol": 0.005}}
         for i in range(3)]


def test_scores_each_task_repeat_and_balances_replicas(tmp_path, monkeypatch):
    seen = []

    def handler(req: httpx.Request):
        seen.append(str(req.url.host_header if hasattr(req.url, "host_header") else req.url))
        prompt = json.loads(req.content)["messages"][0]["content"]
        text = "= 42" if prompt != "q1" else "I do not know"
        return httpx.Response(200, json={"choices": [{"message": {"content": text}}], "usage": {"total_tokens": 5}})

    real = httpx.AsyncClient
    monkeypatch.setattr(db.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    tasks = tmp_path / "t.jsonl"
    tasks.write_text("\n".join(json.dumps(t) for t in TASKS))
    out = tmp_path / "o.jsonl"
    a = argparse.Namespace(tasks=str(tasks), urls="http://a:1,http://b:1", model="m", label="j", offset=0, n=0, repeats=2,
                           temperature=0.7, max_tokens=50, concurrency=2, timeout=5, out=str(out))
    asyncio.run(db.main(a))
    recs = [json.loads(l) for l in open(out)]
    assert len(recs) == 6 and all(r["arm"] == "direct:j" and not r["errors"] for r in recs)
    assert sum(r["base"]["correct"] for r in recs) == 4 and all(r["base"]["draft_correct"] == r["base"]["correct"] for r in recs)
    assert len(set(seen)) == 2  # both replicas got requests


def test_a_system_prompt_is_sent_when_requested(tmp_path, monkeypatch):
    import asyncio
    import json as _json
    from types import SimpleNamespace
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("direct_baseline", Path(__file__).resolve().parents[1] / "benchmarks/orchestration_suite/direct_baseline.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    prompts = tmp_path / "p.json"
    prompts.write_text(_json.dumps({"coder": "You are the coder."}))
    sent = []

    class FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": {"content": "42"}}], "usage": {}}

    class FakeHttp:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, json=None): sent.append(json["messages"]); return FakeResp()

    tasks = tmp_path / "t.jsonl"
    tasks.write_text(_json.dumps({"id": "t1", "family": "f", "lang": "en", "prompts": {"en": "What is 6*7?"}, "final": {"ref": 42, "tol": 0.5}}) + "\n")
    base = dict(tasks=str(tasks), urls="http://x", model="m", label="l", offset=0, n=0, repeats=1, temperature=0.7, max_tokens=50, concurrency=1, timeout=5, out=str(tmp_path / "o.jsonl"))
    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda **k: FakeHttp())  # restored after the test: httpx is the shared module
    asyncio.run(mod.main(SimpleNamespace(**base, system_file=str(prompts), system_role="coder")))
    asyncio.run(mod.main(SimpleNamespace(**base)))
    assert sent[0] == [{"role": "system", "content": "You are the coder."}, {"role": "user", "content": "What is 6*7?"}]
    assert sent[1] == [{"role": "user", "content": "What is 6*7?"}]
