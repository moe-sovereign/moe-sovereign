"""Ollama-to-vLLM research shim: request/response translation (no network, no FastAPI)."""
import importlib.util
import time
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "benchmarks" / "orchestration_suite" / "lumi" / "ollama_shim.py"
_spec = importlib.util.spec_from_file_location("ollama_shim", _PATH)
shim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shim)

MODELS = {"hf.co/x/planner:Q4_K_M": {"url": "http://127.0.0.1:8101", "context_length": 32768, "family": "olmo3", "parameter_size": "7B"}}


def test_chat_options_map_to_openai_sampling_and_drop_num_ctx():
    body = {"model": "m", "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
            "options": {"temperature": 0.3, "top_k": 20, "num_predict": 128, "num_ctx": 65536, "seed": 7, "repeat_penalty": 1.1},
            "think": False, "format": "json"}
    out = shim.openai_request(body, stream=False)
    assert out["messages"] == body["messages"] and out["stream"] is False
    assert (out["temperature"], out["top_k"], out["max_tokens"], out["seed"], out["repetition_penalty"]) == (0.3, 20, 128, 7, 1.1)
    assert "num_ctx" not in out and "options" not in out
    assert out["response_format"] == {"type": "json_object"}
    assert out["chat_template_kwargs"] == {"enable_thinking": False}


def test_schema_format_negative_num_predict_and_stream_usage():
    out = shim.openai_request({"model": "m", "messages": [], "format": {"type": "array"}, "options": {"num_predict": -1}}, stream=True)
    assert out["response_format"]["json_schema"]["schema"] == {"type": "array"}
    assert "max_tokens" not in out
    assert out["stream_options"] == {"include_usage": True}


def test_tools_are_rejected_not_silently_dropped():
    with pytest.raises(shim.ShimError) as exc:
        shim.openai_request({"model": "m", "messages": [], "tools": [{"type": "function"}]}, stream=False)
    assert exc.value.status == 501


def test_response_shape_matches_ollama_chat():
    oai = {"choices": [{"message": {"content": "hi", "reasoning_content": "r"}, "finish_reason": "length"}],
           "usage": {"prompt_tokens": 11, "completion_tokens": 5}}
    res = shim.ollama_chat_response("m", oai, time.monotonic())
    assert res["message"] == {"role": "assistant", "content": "hi", "thinking": "r"}
    assert res["done"] is True and res["done_reason"] == "length"
    assert (res["prompt_eval_count"], res["eval_count"]) == (11, 5)


def test_generate_maps_to_single_turn_chat_and_rejects_raw():
    chat = shim.generate_as_chat({"model": "m", "prompt": "p", "system": "s", "options": {"temperature": 0}, "context": [1]})
    assert chat["messages"] == [{"role": "system", "content": "s"}, {"role": "user", "content": "p"}]
    assert "context" not in chat and chat["options"] == {"temperature": 0}
    with pytest.raises(shim.ShimError):
        shim.generate_as_chat({"model": "m", "prompt": "p", "raw": True})


def test_tags_ps_show_report_configured_models_and_context():
    assert [m["name"] for m in shim.tags(MODELS)["models"]] == list(MODELS)
    assert shim.ps(MODELS)["models"][0]["context_length"] == 32768
    info = shim.show(MODELS, "hf.co/x/planner:Q4_K_M")
    assert info["model_info"]["olmo3.context_length"] == 32768
    with pytest.raises(shim.ShimError) as exc:
        shim.show(MODELS, "unknown")
    assert exc.value.status == 404
