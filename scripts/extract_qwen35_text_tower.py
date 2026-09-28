#!/usr/bin/env python3
"""
scripts/extract_qwen35_text_tower.py — Plan Phase 1: extract the text-only
CausalLM backbone from a multimodal Qwen3.5 checkpoint (architectures=
Qwen3_5ForConditionalGeneration, with vision_config + text_config), so it
can be loaded by train_expert_slm_pipeline.py's AutoModelForCausalLM path.

Procedure verified empirically against the ALREADY-extracted, working
moe_qwen35_4b_distilled/merged checkpoint (confirmed loadable, confirmed
correct target_modules, confirmed real training step -- Phase C smoke
test), not guessed from the public HF config alone:

  1. Config: promote `text_config` to the top level, set
     `architectures: ["Qwen3_5ForCausalLM"]`, keep `tie_word_embeddings`
     from the top-level multimodal config, force `mtp_num_hidden_layers: 0`
     (the reference checkpoint drops the MTP head entirely).
  2. Weights: keep only keys starting with `model.language_model.` or
     exactly `lm_head.weight`. Drop everything under `model.visual.` and
     `mtp.`. Keys are kept VERBATIM, no prefix renaming -- the reference
     checkpoint's weights still carry the `model.language_model.` prefix,
     confirming Qwen3_5ForCausalLM's own module tree nests the transformer
     body under a `.language_model` attribute (same as the ConditionalGeneration
     wrapper), so no rename is needed for the keys to line up.
  3. Tokenizer/chat template/generation_config are copied as-is from the
     source repo (same tokenizer for text and multimodal variants).

Usage:
    python3 scripts/extract_qwen35_text_tower.py \
        --source Qwen/Qwen3.5-9B \
        --output /scratch/project_465003058/hornphil/checkpoints/moe_qwen35_9b_text/merged
"""
import argparse
import json
import shutil
from pathlib import Path

import torch
from safetensors.torch import save_file
from huggingface_hub import snapshot_download


def build_causal_lm_config(raw_config: dict) -> dict:
    text_config = dict(raw_config["text_config"])
    text_config["architectures"] = ["Qwen3_5ForCausalLM"]
    text_config["tie_word_embeddings"] = raw_config.get("tie_word_embeddings", False)
    text_config["mtp_num_hidden_layers"] = 0
    # Parity fields present in the already-verified moe_qwen35_4b_distilled
    # reference config but missing from the multimodal repo's text_config
    # alone (checked field-by-field against that reference, 2026-09-11).
    text_config.setdefault("bos_token_id", None)
    text_config.setdefault("pad_token_id", None)
    text_config.setdefault("transformers_version", raw_config.get("transformers_version"))
    rope = text_config.get("rope_parameters") or {}
    if "partial_rotary_factor" in rope:
        text_config.setdefault("partial_rotary_factor", rope["partial_rotary_factor"])
    return text_config


def extract_state_dict(source_dir: Path):
    """Load every safetensors shard in source_dir, keep only
    model.language_model.* and lm_head.weight, return a flat state dict."""
    index_path = source_dir / "model.safetensors.index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text())
        shard_files = sorted(set(index["weight_map"].values()))
    else:
        shard_files = ["model.safetensors"]

    from safetensors import safe_open

    kept = {}
    dropped_vision = 0
    dropped_mtp = 0
    for shard in shard_files:
        shard_path = source_dir / shard
        with safe_open(str(shard_path), framework="pt") as f:
            for key in f.keys():
                if key.startswith("model.language_model.") or key == "lm_head.weight":
                    kept[key] = f.get_tensor(key)
                elif key.startswith("model.visual."):
                    dropped_vision += 1
                elif key.startswith("mtp."):
                    dropped_mtp += 1
                else:
                    print(f"WARNING: unrecognized key not kept: {key}")
    print(f"kept={len(kept)} dropped_vision={dropped_vision} dropped_mtp={dropped_mtp}")
    return kept


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", required=True, help="HF repo id of the multimodal Qwen3.5 checkpoint")
    p.add_argument("--output", required=True, help="output directory for the extracted text-only CausalLM")
    args = p.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Downloading source snapshot: {args.source} ...")
    source_dir = Path(snapshot_download(args.source))

    raw_config = json.loads((source_dir / "config.json").read_text())
    if "text_config" not in raw_config:
        raise SystemExit(f"ERROR: {args.source} has no text_config -- is this actually a multimodal checkpoint?")

    new_config = build_causal_lm_config(raw_config)
    (output_dir / "config.json").write_text(json.dumps(new_config, indent=2))
    print(f"Wrote config.json: architectures={new_config['architectures']}, "
          f"model_type={new_config['model_type']}, "
          f"num_hidden_layers={new_config['num_hidden_layers']}")

    state_dict = extract_state_dict(source_dir)
    save_file(state_dict, str(output_dir / "model.safetensors"), metadata={"format": "pt"})
    print(f"Wrote model.safetensors ({sum(t.numel() * t.element_size() for t in state_dict.values()) / 1e9:.2f} GB)")

    for fname in ("tokenizer.json", "tokenizer_config.json", "chat_template.jinja",
                  "generation_config.json", "vocab.json", "merges.txt", "special_tokens_map.json"):
        src = source_dir / fname
        if src.exists():
            shutil.copy(src, output_dir / fname)

    print(f"Done -> {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
