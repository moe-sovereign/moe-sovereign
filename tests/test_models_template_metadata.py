from routes.models import _template_context_length, _template_model_entry


def test_template_model_metadata_exposes_context_window():
    template = {
        "name": "moe-n04-rtx-qwen3.6:35b-256k",
        "planner_num_ctx": 131072,
        "experts": {
            "coding": {"context_window": 262144, "models": []},
            "fast": {"context_window": 32768, "models": []},
        },
    }

    assert _template_context_length(template) == 262144
    entry = _template_model_entry("owned-id", template, 123)
    assert entry["id"] == template["name"]
    assert entry["context_length"] == 262144
    assert entry["context_window"] == 262144


import asyncio

from routes import models as models_route


def test_endpoint_allowed_matches_exact_and_wildcard():
    grants = ["qwen3.6:35b@N04-RTX", "*@N09-M10"]
    assert models_route._endpoint_allowed(grants, "qwen3.6:35b", "N04-RTX")
    assert models_route._endpoint_allowed(grants, "anything", "N09-M10")
    assert not models_route._endpoint_allowed(grants, "qwen3.6:27b", "N04-RTX")
    assert not models_route._endpoint_allowed([], "qwen3.6:35b", "N04-RTX")


def test_native_entry_prefers_loaded_context(monkeypatch):
    async def loaded(node):
        return {"qwen3.6:35b": 262144}

    monkeypatch.setattr(models_route, "_node_loaded_ctx", loaded)
    entry = asyncio.run(models_route._native_model_entry("qwen3.6:35b", "N04-RTX", 1))
    assert entry["id"] == "qwen3.6:35b@N04-RTX"
    assert entry["context_length"] == 262144
    assert entry["context_window"] == 262144


def test_native_entry_falls_back_to_metadata_then_omits(monkeypatch):
    async def none_loaded(node):
        return {}

    async def meta(model_id):
        return 32768

    monkeypatch.setitem(models_route.URL_MAP, "N04-RTX", "http://node:11434/v1")
    monkeypatch.setattr(models_route, "_node_loaded_ctx", none_loaded)
    monkeypatch.setattr(models_route, "_context_window_for_model", meta)
    entry = asyncio.run(models_route._native_model_entry("m", "N04-RTX", 1))
    assert entry["context_length"] == 32768

    async def no_meta(model_id):
        return None

    async def no_show(*a, **k):
        return 0

    monkeypatch.setattr(models_route, "_context_window_for_model", no_meta)
    monkeypatch.setattr("context_budget.fetch_ollama_native_ctx_max", no_show)
    entry = asyncio.run(models_route._native_model_entry("m", "N04-RTX", 1))
    assert "context_length" not in entry


def test_loaded_ctx_unknown_node_is_empty():
    assert asyncio.run(models_route._node_loaded_ctx("no-such-node")) == {}
