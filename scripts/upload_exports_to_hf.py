#!/usr/bin/env python3
"""
scripts/upload_exports_to_hf.py -- upload finished GGUF exports + their
README.md model card directly from LUMI-G to HuggingFace Hub.

Runs inside the Singularity container (huggingface_hub already available
there, confirmed 0.36.2). Reads the token the same way the extraction jobs
already do: ~/.cache/huggingface/token.

Usage:
    python3 scripts/upload_exports_to_hf.py <export_dir_name> <hf_repo_id>
    python3 scripts/upload_exports_to_hf.py --all   # uses the built-in map below
"""
import argparse
import sys
from pathlib import Path

from huggingface_hub import HfApi

SCRATCH = Path("/scratch/project_465003058/hornphil")
EXPORTS = SCRATCH / "exports"

REPO_MAP = {
    "moe-expert-coder-4b": "h3rb3rn/moe-expert-coder-4b",
    "moe-expert-datainfra-4b": "h3rb3rn/moe-expert-datainfra-4b",
    "moe-expert-governance-4b": "h3rb3rn/moe-expert-governance-4b",
    "moe-expert-graphrag-4b": "h3rb3rn/moe-expert-graphrag-4b",
    "moe-expert-omni-4b": "h3rb3rn/moe-expert-omni-4b",
    "moe-expert-precision-4b": "h3rb3rn/moe-expert-precision-4b",
    "moe-expert-research-4b": "h3rb3rn/moe-expert-research-4b",
    "moe-expert-security-4b": "h3rb3rn/moe-expert-security-4b",
    "smollm3-expert-coder-3b": "h3rb3rn/smollm3-expert-coder-3b",
    "smollm3-expert-datainfra-3b": "h3rb3rn/smollm3-expert-datainfra-3b",
    "smollm3-expert-governance-3b": "h3rb3rn/smollm3-expert-governance-3b",
    "smollm3-expert-graphrag-3b": "h3rb3rn/smollm3-expert-graphrag-3b",
    "smollm3-expert-omni-3b": "h3rb3rn/smollm3-expert-omni-3b",
    "smollm3-expert-precision-3b": "h3rb3rn/smollm3-expert-precision-3b",
    "smollm3-expert-research-3b": "h3rb3rn/smollm3-expert-research-3b",
    "smollm3-expert-security-3b": "h3rb3rn/smollm3-expert-security-3b",
    "moe-sovereign-planner-olmo3-7b": "h3rb3rn/moe-sovereign-planner-olmo3-7b",
    "sovereign-judge-olmo31-32b": "h3rb3rn/sovereign-judge-olmo31-32b",
    "moe-sovereign-student-9b": "h3rb3rn/moe-sovereign-planner-9b",
    "sovereign-judge-27b": "h3rb3rn/sovereign-judge-27b",
}


def upload_one(api: HfApi, export_dir_name: str, repo_id: str) -> None:
    local_dir = EXPORTS / export_dir_name
    if not local_dir.is_dir():
        print(f"SKIP {export_dir_name}: directory not found at {local_dir}")
        return
    readme = local_dir / "README.md"
    if not readme.exists():
        print(f"SKIP {export_dir_name}: no README.md staged, refusing to upload without a model card")
        return
    gguf_files = sorted(local_dir.glob("*.gguf"))
    if not gguf_files:
        print(f"SKIP {export_dir_name}: no .gguf files found")
        return

    print(f"=== {export_dir_name} -> {repo_id} ===")
    api.create_repo(repo_id=repo_id, repo_type="model", private=False, exist_ok=True)
    api.upload_file(path_or_fileobj=str(readme), path_in_repo="README.md", repo_id=repo_id, repo_type="model")
    for g in gguf_files:
        print(f"  uploading {g.name} ({g.stat().st_size / 1e9:.2f} GB)...")
        api.upload_file(path_or_fileobj=str(g), path_in_repo=g.name, repo_id=repo_id, repo_type="model")
    print(f"  DONE: https://huggingface.co/{repo_id}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("export_dir", nargs="?", help="export directory name under exports/")
    p.add_argument("repo_id", nargs="?", help="target HF repo id, e.g. h3rb3rn/moe-expert-coder-4b")
    p.add_argument("--all", action="store_true", help="upload every entry in the built-in REPO_MAP")
    args = p.parse_args()

    token = (Path.home() / ".cache/huggingface/token").read_text().strip()
    api = HfApi(token=token)

    if args.all:
        for export_dir_name, repo_id in REPO_MAP.items():
            upload_one(api, export_dir_name, repo_id)
        return 0

    if not args.export_dir or not args.repo_id:
        print("Usage: upload_exports_to_hf.py <export_dir> <repo_id>  OR  --all", file=sys.stderr)
        return 1
    upload_one(api, args.export_dir, args.repo_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
