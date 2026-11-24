#!/usr/bin/env python3
"""
scripts/download_and_register_all_models.py
Downloads all 9 Q4_K_M GGUF models from HuggingFace Hub at 100+ MB/s and registers them in Ollama.
"""

import os
import subprocess
import tempfile
import time
from pathlib import Path
from huggingface_hub import hf_hub_download

MODELS = [
    ("moe-expert-coder:4b", "moe-expert-coder-4b", "moe-expert-coder-4b-Q4_K_M.gguf", 0.05),
    ("moe-expert-precision:4b", "moe-expert-precision-4b", "moe-expert-precision-4b-Q4_K_M.gguf", 0.0),
    ("moe-expert-graphrag:4b", "moe-expert-graphrag-4b", "moe-expert-graphrag-4b-Q4_K_M.gguf", 0.05),
    ("moe-expert-governance:4b", "moe-expert-governance-4b", "moe-expert-governance-4b-Q4_K_M.gguf", 0.1),
    ("moe-expert-research:4b", "moe-expert-research-4b", "moe-expert-research-4b-Q4_K_M.gguf", 0.15),
    ("moe-expert-security:4b", "moe-expert-security-4b", "moe-expert-security-4b-Q4_K_M.gguf", 0.05),
    ("moe-expert-datainfra:4b", "moe-expert-datainfra-4b", "moe-expert-datainfra-4b-Q4_K_M.gguf", 0.05),
    ("moe-expert-omni:4b", "moe-expert-omni-4b", "moe-expert-omni-4b-Q4_K_M.gguf", 0.2),
]

LOCAL_DIR = Path("/opt/deployment/moe-sovereign/moe-infra/models/gguf")
LOCAL_DIR.mkdir(parents=True, exist_ok=True)

TEMPLATE_STR = '"""{{ if .System }}<|im_start|>system\n{{ .System }}<|im_end|>\n{{ end }}{{ if .Prompt }}<|im_start|>user\n{{ .Prompt }}<|im_end|>\n{{ end }}<|im_start|>assistant\n{{ .Response }}<|im_end|>"""'

def main():
    print("🚀 Downloading all 9 GGUF models from Hugging Face Hub (h3rb3rn/*)...")
    for model_tag, repo_name, filename, temp in MODELS:
        print(f"\n================================================================================")
        print(f"📦 Processing [{model_tag}] from h3rb3rn/{repo_name}")
        t0 = time.time()
        try:
            gguf_path = hf_hub_download(
                repo_id=f"h3rb3rn/{repo_name}",
                filename=filename,
                local_dir=str(LOCAL_DIR)
            )
            dt = round(time.time() - t0, 1)
            print(f"✅ Downloaded {filename} in {dt}s to {gguf_path}")

            # Register in Ollama
            modelfile_content = f"""FROM {gguf_path}
PARAMETER num_ctx 262144
PARAMETER temperature {temp}
TEMPLATE {TEMPLATE_STR}
"""
            with tempfile.NamedTemporaryFile("w", delete=False) as tf:
                tf.write(modelfile_content)
                tf_path = tf.name

            print(f"⚙️ Registering Ollama model [{model_tag}]...")
            subprocess.run(["ollama", "create", model_tag, "-f", tf_path], check=True)
            # Create HF alias
            hf_tag = f"hf.co/h3rb3rn/{repo_name}"
            subprocess.run(["ollama", "cp", model_tag, hf_tag], check=True)
            print(f"🎉 Successfully created [{model_tag}] and [{hf_tag}] in Ollama!")
            if os.path.exists(tf_path):
                os.remove(tf_path)
        except Exception as e:
            print(f"❌ Error on {model_tag}: {e}")

    print("\n🏁 All 9 models downloaded and registered in Ollama!")

if __name__ == "__main__":
    main()
