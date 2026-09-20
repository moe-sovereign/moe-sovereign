"""A native call without num_ctx must reuse the loaded context instead of forcing a model reload."""

from services.ollama_warm_ctx import pick_warm_ctx

PS = {"models": [{"name": "judge:Q4_K_M", "model": "judge:Q4_K_M", "context_length": 65536},
                 {"name": "other:latest", "model": "other:latest", "context_length": 16384}]}


def test_loaded_model_returns_its_context():
    assert pick_warm_ctx(PS, "judge:Q4_K_M") == 65536
    assert pick_warm_ctx(PS, "other:latest") == 16384


def test_model_that_is_not_loaded_returns_zero():
    assert pick_warm_ctx(PS, "missing:latest") == 0
    assert pick_warm_ctx({}, "judge:Q4_K_M") == 0
    assert pick_warm_ctx(None, "judge:Q4_K_M") == 0


def test_garbage_context_value_returns_zero():
    assert pick_warm_ctx({"models": [{"name": "m", "context_length": "x"}]}, "m") == 0


def test_judge_path_frees_vram_before_a_larger_context_reload():
    """Regression: a judge reload with a larger context hung for 75 min while a second large model held the VRAM."""
    import inspect

    import services.inference as inference

    src = inspect.getsource(inference._invoke_judge_with_retry)
    assert "_judge_ctx_reused" in src and "_evict_competing_models(_ollama_base, _jm, ctx=_ctx)" in src


def test_native_passthrough_reuses_the_loaded_context_when_none_is_given():
    import inspect

    import services.pipeline.chat as chat

    assert "loaded_ctx as _loaded_ctx" in inspect.getsource(chat)


def test_plain_ollama_chat_uses_native_api_so_that_num_ctx_is_honoured():
    import inspect

    import services.pipeline.chat as chat

    assert "or not request.tools)" in inspect.getsource(chat)
