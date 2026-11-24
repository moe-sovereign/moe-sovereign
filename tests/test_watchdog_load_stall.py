"""
test_watchdog_load_stall.py — LOAD_STALLED detection in watchdog.py.

Incident: on N04-RTX the Ollama scheduler stopped loading any further model after a
failed GPU discovery. The node kept answering /api/tags and /api/ps, so node_up stayed 1,
the stuck-request counter never froze (clients kept retrying) and nothing alerted while
every request for the not-yet-loaded model waited in Ollama's queue without a log line.
"""

from datetime import datetime, timedelta, timezone

import watchdog

NOW = datetime(2026, 10, 31, 1, 0, 0, tzinfo=timezone.utc)


def _req(model, node, age_s):
    started = (NOW - timedelta(seconds=age_s)).isoformat().replace("+00:00", "Z")
    return {"backend_host": node, "backend_model": model, "started_at": started}


def test_stalled_request_for_unloaded_model_alerts():
    alerts = watchdog._evaluate_load_stalls(
        [_req("qwen3.6:35b", "N04-RTX", 420)], {"N04-RTX": {"judge:27b"}}, 300, NOW
    )
    assert len(alerts) == 1
    a = alerts[0]
    assert a["type"] == "load_stalled" and a["node"] == "N04-RTX"
    assert a["model"] == "qwen3.6:35b" and a["waiting"] == 1 and a["oldest_seconds"] == 420


def test_young_request_is_a_normal_cold_load():
    assert watchdog._evaluate_load_stalls(
        [_req("qwen3.6:35b", "N04-RTX", 170)], {"N04-RTX": {"judge:27b"}}, 300, NOW
    ) == []


def test_loaded_model_is_generating_not_stalled():
    assert watchdog._evaluate_load_stalls(
        [_req("qwen3.6:35b", "N04-RTX", 900)], {"N04-RTX": {"qwen3.6:35b"}}, 300, NOW
    ) == []
    assert watchdog._evaluate_load_stalls(
        [_req("qwen3", "N04-RTX", 900)], {"N04-RTX": {"qwen3:latest"}}, 300, NOW
    ) == []


def test_node_without_successful_probe_never_alerts():
    assert watchdog._evaluate_load_stalls([_req("m", "N04-RTX", 900)], {}, 300, NOW) == []


def test_requests_without_backend_are_ignored():
    pipeline_req = {"backend_host": "", "backend_model": "", "started_at": "2026-10-31T00:00:00Z"}
    assert watchdog._evaluate_load_stalls([pipeline_req], {"N04-RTX": set()}, 300, NOW) == []


def test_waiting_requests_are_grouped_per_node_and_model():
    alerts = watchdog._evaluate_load_stalls(
        [_req("m1", "N04-RTX", 400), _req("m1", "N04-RTX", 800), _req("m2", "N04-RTX", 500)],
        {"N04-RTX": set()}, 300, NOW,
    )
    by_model = {a["model"]: a for a in alerts}
    assert by_model["m1"]["waiting"] == 2 and by_model["m1"]["oldest_seconds"] == 800
    assert by_model["m2"]["waiting"] == 1
