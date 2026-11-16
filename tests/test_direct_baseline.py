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
