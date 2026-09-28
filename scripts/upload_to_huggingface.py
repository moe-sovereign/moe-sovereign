#!/usr/bin/env python3
"""
scripts/upload_to_huggingface.py
Uploads quantized GGUF models and model cards to Hugging Face Hub (h3rb3rn/*).
"""

import os
import sys
import glob
from pathlib import Path
from huggingface_hub import HfApi, create_repo, upload_file

SCRATCH = os.environ.get("SCRATCH", "/scratch/project_465003058/hornphil")
EXPORTS_BASE = Path(SCRATCH) / "exports"
MODEL_CARDS_DIR = Path(SCRATCH) / "moe-sovereign" / "model_cards"
if not MODEL_CARDS_DIR.exists():
    MODEL_CARDS_DIR = Path("/opt/deployment/moe-sovereign/moe-infra/model_cards")

HF_USERNAME = "h3rb3rn"

MODELS = [
    {
        "role": "governance",
        "repo_name": "moe-expert-governance-4b",
        "export_dir": EXPORTS_BASE / "moe-expert-governance-4b",
        "card": MODEL_CARDS_DIR / "moe-expert-governance-4b.md"
    },
    {
        "role": "datainfra",
        "repo_name": "moe-expert-datainfra-4b",
        "export_dir": EXPORTS_BASE / "moe-expert-datainfra-4b",
        "card": MODEL_CARDS_DIR / "moe-expert-datainfra-4b.md"
    },
    {
        "role": "security",
        "repo_name": "moe-expert-security-4b",
        "export_dir": EXPORTS_BASE / "moe-expert-security-4b",
        "card": MODEL_CARDS_DIR / "moe-expert-security-4b.md"
    },
    {
        "role": "research",
        "repo_name": "moe-expert-research-4b",
        "export_dir": EXPORTS_BASE / "moe-expert-research-4b",
        "card": MODEL_CARDS_DIR / "moe-expert-research-4b.md"
    },
    {
        "role": "omni",
        "repo_name": "moe-expert-omni-4b",
        "export_dir": EXPORTS_BASE / "moe-expert-omni-4b",
        "card": MODEL_CARDS_DIR / "moe-expert-omni-4b.md"
    },
    {
        "role": "graphrag",
        "repo_name": "moe-expert-graphrag-4b",
        "export_dir": EXPORTS_BASE / "moe-expert-graphrag-4b",
        "card": MODEL_CARDS_DIR / "moe-expert-graphrag-4b.md"
    },
    {
        "role": "precision",
        "repo_name": "moe-expert-precision-4b",
        "export_dir": EXPORTS_BASE / "moe-expert-precision-4b",
        "card": MODEL_CARDS_DIR / "moe-expert-precision-4b.md"
    },
    {
        "role": "coder",
        "repo_name": "moe-expert-coder-4b",
        "export_dir": EXPORTS_BASE / "moe-expert-coder-4b",
        "card": MODEL_CARDS_DIR / "moe-expert-coder-4b.md"
    },
    {
        "role": "judge",
        "repo_name": "sovereign-judge-27b",
        "export_dir": EXPORTS_BASE / "sovereign-judge-27b",
        "card": MODEL_CARDS_DIR / "sovereign-judge-27b.md"
    }
]

def main():
    api = HfApi()
    user_info = api.whoami()
    print(f"🔑 Authenticated with HuggingFace as: {user_info['name']}")

    target_role = sys.argv[1] if len(sys.argv) > 1 else None

    for model_cfg in MODELS:
        role = model_cfg["role"]
        if target_role and target_role != "all" and target_role != role:
            continue

        repo_id = f"{HF_USERNAME}/{model_cfg['repo_name']}"
        export_dir = model_cfg["export_dir"]
        card_path = model_cfg["card"]

        print(f"\n================================================================================")
        print(f"🚀 Processing [{role}] -> {repo_id}")
        print(f"Export Dir: {export_dir}")
        print(f"================================================================================")

        # Check if export directory has GGUFs
        gguf_files = list(export_dir.glob("*.gguf")) if export_dir.exists() else []
        if not gguf_files:
            print(f"⚠️ No GGUF files found in {export_dir}. Skipping for now...")
            continue

        # Filter out intermediate F16 if present
        ready_ggufs = [f for f in gguf_files if "-F16.gguf" not in f.name]
        if not ready_ggufs:
            print(f"⚠️ Only intermediate F16 found in {export_dir}. Quantization still in progress. Skipping...")
            continue

        # 1. Ensure Repo exists
        try:
            create_repo(repo_id=repo_id, repo_type="model", exist_ok=True, private=False)
            print(f"✅ Repository {repo_id} verified/created.")
        except Exception as e:
            print(f"⚠️ create_repo note: {e}")

        # 2. Upload README.md (Model Card)
        if card_path.exists():
            print(f"📄 Uploading Model Card from {card_path} -> README.md...")
            upload_file(
                path_or_fileobj=str(card_path),
                path_in_repo="README.md",
                repo_id=repo_id,
                repo_type="model",
                commit_message=f"docs: upload comprehensive model card for {model_cfg['repo_name']}"
            )
            print(f"✅ README.md uploaded.")
        else:
            print(f"⚠️ Model card not found at {card_path}.")

        # 3. Upload GGUF binaries
        for gguf_path in ready_ggufs:
            file_name = gguf_path.name
            file_size_gb = gguf_path.stat().st_size / (1024 ** 3)
            print(f"📦 Uploading {file_name} ({file_size_gb:.2f} GB) to {repo_id}...")
            upload_file(
                path_or_fileobj=str(gguf_path),
                path_in_repo=file_name,
                repo_id=repo_id,
                repo_type="model",
                commit_message=f"feat: add quantized {file_name} ({file_size_gb:.2f} GB)"
            )
            print(f"✅ Uploaded {file_name}")

    print(f"\n🎉 All available models processed successfully!")

if __name__ == "__main__":
    main()
