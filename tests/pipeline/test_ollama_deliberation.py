from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from services.pipeline.ollama import _ollama_internal_stream


def _user_context(template: dict) -> dict:
    return {
        "user_id": "user-1",
        "key_id": "key-1",
        "permissions_json": json.dumps({"expert_template": []}),
        "user_templates_json": json.dumps({"owned-template-id": template}),
        "user_connections_json": "{}",
    }


@pytest.mark.asyncio
async def test_ollama_facade_passes_frozen_policy_and_output_budget_to_graph():
    template = {
        "name": "owned-template",
        "experts": {},
        "deliberation_policy": {
            "activation": "adaptive",
            "mode": "auto",
            "max_model_calls": 9,
        },
    }
    captured: dict = {}

    async def fake_stream_response(*args, **kwargs):
        captured.update(kwargs)
        yield "data: [DONE]\n\n"

    with (
        patch("services.pipeline.ollama._read_expert_templates", return_value=[]),
        patch("services.routing._read_expert_templates", return_value=[]),
        patch(
            "services.pipeline.ollama._register_active_request",
            new=AsyncMock(),
        ),
        patch("main.stream_response", new=fake_stream_response),
    ):
        chunks = [
            chunk
            async for chunk in _ollama_internal_stream(
                _user_context(template),
                template["name"],
                [{"role": "user", "content": "test"}],
                {"options": {"num_predict": 2048}},
            )
        ]

    assert chunks == ["data: [DONE]\n\n"]
    assert captured["deliberation_policy"]["activation"] == "adaptive"
    assert captured["deliberation_policy"]["max_model_calls"] == 9
    assert captured["client_max_output_tokens"] == 2048


@pytest.mark.asyncio
async def test_ollama_facade_awaits_registration_before_streaming():
    """Regression test: _register_active_request must be awaited before
    stream_response starts.

    Previously this was a fire-and-forget asyncio.create_task(...), so a
    fast-completing request could reach _deregister_active_request before
    the scheduled registration task had actually written the
    moe:active:{chat_id} key to Redis. _deregister_active_request only
    archives to the moe:admin:completed history when it finds an existing
    active-request key, so the lost race made the request invisible to
    both the live and the completed-request views in MoE-Admin's
    Live-Monitoring, even though the response was delivered successfully.
    """
    template = {"name": "owned-template", "experts": {}}
    order: list[str] = []

    async def fake_register(**kwargs):
        order.append("register")

    async def fake_stream_response(*args, **kwargs):
        order.append("stream")
        yield "data: [DONE]\n\n"

    with (
        patch("services.pipeline.ollama._read_expert_templates", return_value=[]),
        patch("services.routing._read_expert_templates", return_value=[]),
        patch(
            "services.pipeline.ollama._register_active_request",
            side_effect=fake_register,
        ),
        patch("main.stream_response", new=fake_stream_response),
    ):
        chunks = [
            chunk
            async for chunk in _ollama_internal_stream(
                _user_context(template),
                template["name"],
                [{"role": "user", "content": "test"}],
                {},
            )
        ]

    assert chunks == ["data: [DONE]\n\n"]
    assert order == ["register", "stream"]


@pytest.mark.asyncio
async def test_ollama_facade_fails_closed_on_invalid_policy():
    template = {
        "name": "invalid-template",
        "experts": {},
        "deliberation_policy": {"mode": "unbounded"},
    }

    with (
        patch("services.pipeline.ollama._read_expert_templates", return_value=[]),
        patch("services.routing._read_expert_templates", return_value=[]),
    ):
        chunks = [
            chunk
            async for chunk in _ollama_internal_stream(
                _user_context(template),
                template["name"],
                [{"role": "user", "content": "test"}],
                {},
            )
        ]

    assert len(chunks) == 1
    assert "deliberation_policy_invalid" in chunks[0]
