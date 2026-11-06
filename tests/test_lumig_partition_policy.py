"""dev-g/debug are interactive-only on LUMI (CSC rule 2026-11-05): no checked-in batch script may target them."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("lumig_preflight_check", ROOT / "scripts" / "lumig_preflight_check.py")
pf = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pf)


def test_partition_parser():
    text = "#SBATCH --partition=dev-g\n#SBATCH -p small-g,debug\n# #SBATCH --partition=standard-g\n"
    assert pf.slurm_partitions(text) == ["dev-g", "small-g", "debug"]


def test_check_partition_rejects_interactive(tmp_path, monkeypatch):
    monkeypatch.delenv("SBATCH_PARTITION", raising=False)
    job = tmp_path / "job.slurm"
    job.write_text("#!/bin/bash\n#SBATCH --partition=small-g\n")
    assert pf.check_partition(str(job))
    monkeypatch.setenv("SBATCH_PARTITION", "dev-g")
    assert not pf.check_partition(str(job))


def test_no_checked_in_batch_script_uses_interactive_partitions():
    offenders = []
    for path in ROOT.rglob("*"):
        if path.suffix not in {".slurm", ".sh", ".sbatch"} or not path.is_file() or ".git" in path.parts:
            continue
        bad = set(pf.slurm_partitions(path.read_text(errors="ignore"))) & pf.INTERACTIVE_ONLY_PARTITIONS
        if bad:
            offenders.append(f"{path.relative_to(ROOT)}: {sorted(bad)}")
    assert not offenders, offenders
