import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks/orchestration_suite"))
import hf_overrides as H  # noqa: E402


def cfg(tmp_path, **c):
    (tmp_path / "config.json").write_text(json.dumps(c))
    return str(tmp_path)


def test_transformers5_nested_rope_parameters_become_the_flat_full_attention_dict(tmp_path):
    d = cfg(tmp_path, model_type="olmo3", rope_parameters={"full_attention": {"rope_theta": 500000.0, "rope_type": "default"}, "sliding_attention": {"rope_theta": 500000.0}})
    assert H.rope_override(d) == {"rope_parameters": {"rope_theta": 500000.0, "rope_type": "default"}}
    assert H.llm_kwargs(d) == {"hf_overrides": {"rope_parameters": {"rope_theta": 500000.0, "rope_type": "default"}}, "enforce_eager": True}


def test_legacy_base_checkpoint_builds_the_same_dict(tmp_path):
    d = cfg(tmp_path, model_type="olmo3", rope_scaling={"rope_type": "yarn", "factor": 8.0}, rope_theta=500000.0)
    assert H.rope_override(d) == {"rope_parameters": {"rope_type": "yarn", "factor": 8.0, "rope_theta": 500000.0}}


def test_other_models_get_no_overrides_and_differing_thetas_are_refused(tmp_path):
    assert H.llm_kwargs(cfg(tmp_path, model_type="smollm3")) == {}
    d = cfg(tmp_path, model_type="olmo3", rope_parameters={"full_attention": {"rope_theta": 1.0}, "sliding_attention": {"rope_theta": 2.0}})
    try:
        H.rope_override(d)
        assert False, "must raise"
    except ValueError:
        pass
