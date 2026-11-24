#!/usr/bin/env python3
"""
scripts/register_local_ollama_models.py
Registers the distilled 4B GGUF models directly in local and remote Ollama instances.
"""

import os
import subprocess
import tempfile
from pathlib import Path

GGUF_DIR = Path("/opt/deployment/moe-sovereign/moe-infra/models/gguf")

MODELS = [
    ("moe-expert-coder:4b", "moe-expert-coder-4b-Q4_K_M.gguf", 0.05),
    ("moe-expert-precision:4b", "moe-expert-precision-4b-Q4_K_M.gguf", 0.0),
    ("moe-expert-graphrag:4b", "moe-expert-graphrag-4b-Q4_K_M.gguf", 0.05),
    ("moe-expert-governance:4b", "moe-expert-governance-4b-Q4_K_M.gguf", 0.1),
    ("moe-expert-research:4b", "moe-expert-research-4b-Q4_K_M.gguf", 0.15),
    ("moe-expert-security:4b", "moe-expert-security-4b-Q4_K_M.gguf", 0.05),
    ("moe-expert-datainfra:4b", "moe-expert-datainfra-4b-Q4_K_M.gguf", 0.05),
    ("moe-expert-omni:4b", "moe-expert-omni-4b-Q4_K_M.gguf", 0.2),
]

TEMPLATE_STR = '"""{{ if .System }}<|im_start|>system\n{{ .System }}<|im_end|>\n{{ end }}{{ if .Prompt }}<|im_start|>user\n{{ .Prompt }}<|im_end|>\n{{ end }}<|im_start|>assistant\n{{ .Response }}<|im_end|>"""'

def main():
    print("🚀 Registering MoE Sovereign Distilled Models in Ollama...")
    for model_tag, gguf_filename, temp in MODELS:
        gguf_path = GGUF_DIR / gguf_filename
        if not gguf_path.exists():
            print(f"⚠️ GGUF not found yet: {gguf_path}. Skipping...")
            continue

        modelfile_content = f"""FROM {gguf_path}
PARAMETER num_ctx 262144
PARAMETER temperature {temp}
TEMPLATE {TEMPLATE_STR}
"""
        with tempfile.NamedTemporaryFile("w", delete=False) as tf:
            tf.write(modelfile_content)
            tf_path = tf.name

        try:
            print(f"📦 Creating Ollama model [{model_tag}] from {gguf_filename}...")
            # Create standard model tag
            subprocess.run(["ollama", "create", model_tag, "-f", tf_path], check=True)
            # Create HF alias tag
            hf_tag = f"hf.co/h3rb3rn/{model_tag.replace(':', '-')}"
            subprocess.run(["ollama", "cp", model_tag, hf_tag], check=True)
            print(f"✅ Registered {model_tag} & {hf_tag}")
        except Exception as e:
            print(f"❌ Error registering {model_tag}: {e}")
        finally:
            if os.path.exists(tf_path):
                os.remove(tf_path)

if __name__ == "__main__":
    main()
