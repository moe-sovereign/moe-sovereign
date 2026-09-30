"""tests/pipeline/test_anthropic_reasoning_num_ctx.py — Regression tests for
services/pipeline/anthropic.py::_anthropic_reasoning_handler and
anthropic_messages() live-monitoring registration.
"""

import inspect


def test_cc_reasoning_handler_uses_native_api_so_that_num_ctx_is_honoured():
    """Regression: _anthropic_reasoning_handler used to POST to Ollama's
    OpenAI-compat /chat/completions with no "options" at all, so a
    reasoning-mode CC profile whose model wasn't already warm loaded at
    Ollama's Modelfile-default context (observed: 32k) instead of the
    profile's configured window (e.g. 256k) — the OpenAI-compat endpoint
    silently discards "options" (see _anthropic_tool_handler for the same
    limitation). Fix: use the native /api/chat path with an explicit
    options.num_ctx, mirroring _anthropic_tool_handler and
    services.pipeline.chat's plain-Ollama-chat path."""
    import services.pipeline.anthropic as anthropic

    src = inspect.getsource(anthropic._anthropic_reasoning_handler)
    assert '_reasoning_api_type == "ollama"' in src
    assert "/api/chat" in src
    assert '"num_ctx": _reasoning_num_ctx' in src


def test_anthropic_messages_awaits_registration_before_dispatch():
    """Regression: anthropic_messages() scheduled _register_active_request via
    asyncio.create_task and immediately dispatched to a handler, so a
    fast-completing request (same race as services/pipeline/ollama.py and,
    previously, services/pipeline/chat.py) could deregister before the
    scheduled registration task had written moe:active:{chat_id} to Redis —
    making the request invisible to MoE-Admin's Live-Monitoring even though
    the response was delivered successfully."""
    import services.pipeline.anthropic as anthropic

    src = inspect.getsource(anthropic.anthropic_messages)
    assert "await _register_active_request(" in src
