"""vLLM arguments for checkpoints saved with transformers 5 (OLMo-3 family) when vLLM is used offline (LLM(...)), as lumig_moe_stack.slurm does for the servers.

transformers 5 nests `rope_parameters` per layer type; vLLM's olmo2.py expects the flat full-attention dict and derives the sliding layers itself.
Base checkpoints keep the legacy format (`rope_scaling` + `rope_theta`): the same flat dict is built from it. OLMo models also need eager mode on ROCm
(CUDA-graph capture fails with hipErrorCapturedEvent). Without these arguments the engine fails with KeyError: 'rope_theta' (interview evaluation 23921944).
"""
import json
from pathlib import Path


def rope_override(model_dir: str):
    cfg = json.loads((Path(model_dir) / "config.json").read_text())
    rp = cfg.get("rope_parameters") or {}
    fa = rp.get("full_attention")
    if not fa and cfg.get("rope_scaling") and cfg.get("rope_theta"):
        fa, rp = {**cfg["rope_scaling"], "rope_theta": cfg["rope_theta"]}, {}
    if not fa:
        return None
    sa = rp.get("sliding_attention", {})
    if float(sa.get("rope_theta", fa["rope_theta"])) != float(fa["rope_theta"]):
        raise ValueError("sliding and full attention differ in rope_theta: not representable as one flat dict")
    return {"rope_parameters": fa}


def llm_kwargs(model_dir: str) -> dict:
    """Extra keyword arguments for vllm.LLM for this checkpoint."""
    cfg = json.loads((Path(model_dir) / "config.json").read_text())
    out: dict = {}
    ov = rope_override(model_dir)
    if ov:
        out["hf_overrides"] = ov
    if "olmo" in str(cfg.get("model_type", "")).lower():
        out["enforce_eager"] = True
    return out
