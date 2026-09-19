#!/usr/bin/env python3
"""Generate the benchmark template matrix (markdown + csv) from the database.

Lists the scientifically relevant expert templates per track (pre-finetune reference and fine-tuned variants) with
every planner / judge / expert assignment and checks the comparability criteria on the live configuration:

  C1  pre-finetune and fine-tuned counterpart differ ONLY in model names (structure, flags, context windows)
  C2  planner, judge and expert system prompts are identical between the counterparts
  C3  every referenced model exists on its endpoint
  C4  privacy local_only, cache off, web research off

Usage (host only):  python3 scripts/benchmark_template_matrix.py [--out docs/system/benchmark-template-matrix.md]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TRACKS = {
    "Spur 1 (open source)": {
        "native_default": "olmo31-32b-instruct-base-fixed:latest",
        "rows": [
            ("Pre-Finetune, GraphRAG", "LUMI-G Base (Pre-Finetune)", None),
            ("Pre-Finetune, no GraphRAG", "LUMI-G Base (Pre-Finetune) - No-GraphRAG", None),
            ("Pre-Finetune, GraphRAG + debate", "LUMI-G Base (Pre-Finetune) - Deliberation", None),
            ("Fine-tuned, GraphRAG", "LUMI-G OLMo + SmolLM3 Sovereign Ensemble", "LUMI-G Base (Pre-Finetune)"),
            ("Fine-tuned, no GraphRAG", "LUMI-G OLMo + SmolLM3 Sovereign Ensemble - No-GraphRAG", "LUMI-G Base (Pre-Finetune) - No-GraphRAG"),
            ("Fine-tuned, GraphRAG + debate", "LUMI-G OLMo + SmolLM3 Sovereign Ensemble - Deliberation", "LUMI-G Base (Pre-Finetune) - Deliberation"),
        ],
    },
    "Spur 2 (open weight)": {
        "native_default": "qwen3.6:27b",
        "rows": [
            ("Pre-Finetune, GraphRAG", "Open-Weight Base (Pre-Finetune)", None),
            ("Pre-Finetune, no GraphRAG", "Open-Weight Base (Pre-Finetune) - No-GraphRAG", None),
            ("Pre-Finetune, GraphRAG + debate", "Open-Weight Base (Pre-Finetune) - Deliberation", None),
            ("Fine-tuned, GraphRAG", "Open-Weight Finetuned Ensemble", "Open-Weight Base (Pre-Finetune)"),
            ("Fine-tuned, no GraphRAG", "Open-Weight Finetuned Ensemble - No-GraphRAG", "Open-Weight Base (Pre-Finetune) - No-GraphRAG"),
            ("Fine-tuned, GraphRAG + debate", "Open-Weight Finetuned Ensemble - Deliberation", "Open-Weight Base (Pre-Finetune) - Deliberation"),
        ],
    },
}


NOTES = """## Findings and open points

1. **Spur 1 fine-tuned variants were not comparable and have been corrected (2026-09-19).** In all three variants
   (`tmpl-11f532fc`, `tmpl-smollm3-nograph`, `tmpl-smollm3-delib`) the category `code_reviewer` had a second, forced
   security model (`role: always`) and `review_lenses: ["security"]`; the reference has one model. With
   `MOE_REVIEW_WAVE_ENABLED` (default on in the deployed orchestrator) only the fine-tuned arm would have run extra
   review calls. Both additions were removed (backup `benchmarks/results/runbook/spur1_finetuned_before_cleanup_20260919.json`);
   all six pairs now pass C1 to C4. They are not part of the benchmark design and can re-appear if another agent edits
   the templates again: re-run this script before every benchmark start. `review_lenses` still exist in the Review and
   Review NoSC arms (intended) and in the three `LUMI-G Ensemble` hybrids (outside the matrix).
2. **Spur 2 is comparable.** All six templates pass C1 to C4.
3. **System prompts are aligned (C2)** by `scripts/align_benchmark_template_prompts.py`. Experts: each category gets the
   training role prompt of its assigned domain expert; judge: the training judge prompt. **Planner: not the training
   prompt.** An A/B test (9 planner calls per variant, 2026-09-19) showed that the training preamble with a
   training-format category block never routed a GDPR question to `governance` (0/6) and once returned an empty plan,
   while the original list format routed it 3/3 and never returned an empty plan. The canonical planner prompt is
   therefore the original descriptive list ("- category: description" plus the empty-plan guard) for each template's own
   categories in a fixed order; for Spur 1 it is byte-identical to the validated original. The same category has the
   same expert prompt hash in both tracks. Expert-prompt alignment itself has no A/B evidence yet.
4. **Model assignment aligned across tracks:** `data_analyst` was served by the precision expert in Spur 2 and by the
   data-infrastructure expert in Spur 1; Spur 2 now follows Spur 1. Questionable but unchanged: Spur 2 `science` and
   `dynamic` are served by the GraphRAG and data-infrastructure experts.
5. **Differences between the tracks that are inherent, not defects:** category sets (8 vs 15), planner context
   (65536 vs 32768), judge context (65536 vs 262144) and different judges. Absolute scores must not be compared across
   tracks; compare each track with its own baseline.
6. **Planner taxonomy (unverified effect):** the planners were trained on a fixed taxonomy; `security`, `governance`,
   `compounding_knowledge` (Spur 1) and `systems_programming`, `web_researcher`, `tool_expert`, `graphrag` (Spur 2) are
   not part of it. The planner must generalise to them; this is not measured.
7. **Native baseline:** called through the orchestrator route `model@N04-RTX` with the user prompt only (no system
   prompt) at temperature 0.2. The model defaults above follow the operator's earlier statement (Qwen3.6-27B for
   Open Weight, OLMo 3.1 for Open Source) and are not yet confirmed.
8. **Design per track: 7 conditions** = 3 pre-finetune templates + 3 fine-tuned templates (GraphRAG, no GraphRAG,
   GraphRAG + debate) + 1 native baseline. Each pre-finetune template is compared with its fine-tuned counterpart.
9. **Not part of the matrix:** the Review and Review NoSC variants (review-wave arms), the `LUMI-G Ensemble` hybrids and
   the `moe-frontier-*` templates.
""".splitlines()


def psql_json(sql: str):
    done = subprocess.run(["docker", "exec", "terra_checkpoints", "psql", "-U", "moe_admin", "-d", "moe_userdb", "-At", "-c", sql],
                          capture_output=True, text=True, check=True)
    return json.loads(done.stdout.strip() or "null")


def servers():
    env = {}
    for line in (ROOT / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v
    raw = env["INFERENCE_SERVERS"].strip()
    if raw[0] in "'\"":
        raw = raw[1:-1]
    return {s["name"]: s["url"].replace("/v1", "") for s in json.loads(raw.replace('\\"', '"'))}


def h(text: str) -> str:
    return hashlib.sha1((text or "").strip().encode()).hexdigest()[:8]


def split_model(spec: str):
    return tuple(spec.rsplit("@", 1)) if "@" in spec else (spec, "")


def structure(cfg: dict) -> dict:
    """Configuration without model names and prompts (what must be identical between counterparts)."""
    c = json.loads(json.dumps(cfg))
    for k in ("planner_model", "judge_model", "planner_prompt", "judge_prompt"):
        c.pop(k, None)
    for ec in c["experts"].values():
        ec.pop("system_prompt", None)
        for m in ec.get("models", []):
            m.pop("model", None)
    return c


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs/system/benchmark-template-matrix.md"))
    args = ap.parse_args()
    out = Path(args.out)
    srv = servers()
    tags: dict[str, set] = {}

    def has(endpoint: str, model: str) -> bool:
        if endpoint not in tags:
            try:
                tags[endpoint] = {m["name"] for m in json.load(urllib.request.urlopen(srv[endpoint] + "/api/tags", timeout=8))["models"]}
            except Exception:
                tags[endpoint] = set()
        return model in tags[endpoint] or f"{model}:latest" in tags[endpoint]

    names = sorted({n for t in TRACKS.values() for _, name, cp in t["rows"] for n in (name, cp) if n})
    quoted = ", ".join("'" + n.replace("'", "''") + "'" for n in names)
    rows = psql_json(f"select json_agg(t) from (select id, name, config_json from admin_expert_templates where name in ({quoted})) t")
    tpl = {r["name"]: {"id": r["id"], "cfg": json.loads(r["config_json"])} for r in rows}

    md = [f"# Benchmark template matrix ({date.today().isoformat()})", "",
          "**Status:** validated against the live database and the Ollama endpoints at generation time "
          "(`python3 scripts/benchmark_template_matrix.py`). Regenerate after every template change.", "",
          "Design per track: 3 pre-finetune templates, 3 fine-tuned templates and 1 native baseline (7 conditions). "
          "Comparability criteria checked per fine-tuned template against its pre-finetune counterpart:", "",
          "- **C1** identical structure (categories, flags, context windows, endpoints, number of models per category); only model names differ",
          "- **C2** identical planner, judge and expert system prompts",
          "- **C3** every referenced model is present on its endpoint",
          "- **C4** `local_only`, cache off, web research off", ""]
    csv_rows = []
    for track, spec in TRACKS.items():
        md += [f"## {track}", "", f"Native baseline (single dense LLM without orchestration, default of `run_spur1_and_spur2.sh`): `{spec['native_default']}` on N04-RTX (**to be confirmed**).", "",
               "| Role | Template (id) | Planner | Judge | GraphRAG | Debate | Prompt hashes planner / judge | C1 | C2 | C3 | C4 | Verdict |",
               "|---|---|---|---|:-:|:-:|---|:-:|:-:|:-:|:-:|---|"]
        for role, name, counterpart in spec["rows"]:
            t = tpl[name]
            cfg = t["cfg"]
            pm, pe = split_model(cfg["planner_model"])
            jm, je = split_model(cfg["judge_model"])
            refs = [(pm, pe), (jm, je)] + [(m["model"], m["endpoint"]) for ec in cfg["experts"].values() for m in ec["models"]]
            c3 = all(has(e, m) for m, e in refs)
            c4 = cfg.get("_privacy_level") == "local_only" and not cfg.get("enable_cache") and not cfg.get("enable_web_research")
            c1 = c2 = None
            notes = []
            if counterpart:
                ref = tpl[counterpart]["cfg"]
                c1 = structure(cfg) == structure(ref)
                if not c1:
                    for cat, ec in cfg["experts"].items():
                        r_ec = ref["experts"].get(cat, {})
                        if len(ec.get("models", [])) != len(r_ec.get("models", [])):
                            notes.append(f"`{cat}` has {len(ec['models'])} models (reference {len(r_ec.get('models', []))})")
                        for extra in ("review_lenses", "review_replaces_self_critique"):
                            if extra in ec and extra not in r_ec:
                                notes.append(f"`{cat}` sets `{extra}`")
                c2 = (h(cfg["planner_prompt"]) == h(ref["planner_prompt"]) and h(cfg["judge_prompt"]) == h(ref["judge_prompt"])
                      and all(h(ec["system_prompt"]) == h(ref["experts"][c]["system_prompt"]) for c, ec in cfg["experts"].items()))
            checks = [x for x in (c1, c2) if x is not None] + [c3, c4]
            verdict = "pre-finetune" if counterpart is None and c3 and c4 else ("sound" if all(checks) else "**not sound**: " + ("; ".join(notes) or "see checks"))
            mark = lambda v: "-" if v is None else ("yes" if v else "**NO**")
            md.append(f"| {role} | `{name}` (`{t['id']}`) | `{pm.split('/')[-1]}` @{pe} ctx {cfg.get('planner_num_ctx')} | `{jm.split('/')[-1]}` @{je} ctx {cfg.get('judge_num_ctx')} | "
                      f"{'on' if cfg.get('enable_graphrag') else 'off'} | {'on' if 'deliberation_policy' in cfg else 'off'} | "
                      f"{h(cfg['planner_prompt'])} / {h(cfg['judge_prompt'])} | {mark(c1)} | {mark(c2)} | {mark(c3)} | {mark(c4)} | {verdict} |")
        md += ["", f"### {track}: expert assignment (category -> model @ endpoint)", "",
               "| Category | Training role prompt | Pre-Finetune reference | Fine-tuned |", "|---|---|---|---|"]
        pre_cfg = tpl[spec["rows"][0][1]]["cfg"]
        ft_cfg = tpl[spec["rows"][1][1]]["cfg"]
        for cat in pre_cfg["experts"]:
            pre_m = "; ".join(f"`{m['model'].split('/')[-1]}` @{m['endpoint']}" for m in pre_cfg["experts"][cat]["models"])
            ft_m = "; ".join(f"`{m['model'].split('/')[-1]}` @{m['endpoint']}" + (" (forced)" if m.get("forced") else "") for m in ft_cfg["experts"][cat]["models"])
            md.append(f"| `{cat}` | {h(pre_cfg['experts'][cat]['system_prompt'])} | {pre_m} | {ft_m} |")
            csv_rows.append(dict(track=track, category=cat, prompt_hash=h(pre_cfg["experts"][cat]["system_prompt"]), pre_finetune=pre_m, finetuned=ft_m))
        md.append("")
    md += NOTES
    out.write_text("\n".join(md) + "\n")
    with open(out.with_suffix(".csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(csv_rows[0]))
        w.writeheader()
        w.writerows(csv_rows)
    print(f"wrote {out} and {out.with_suffix('.csv')}")


if __name__ == "__main__":
    main()
