#!/usr/bin/env python3
"""Generate the benchmark template matrix (markdown + csv) from the database.

Lists the scientifically relevant expert templates per track (pre-finetune reference and fine-tuned variants) with
every planner / judge / expert assignment and checks the comparability criteria on the live configuration:

  C1  pre-finetune and fine-tuned counterpart differ ONLY in model names (structure, flags, context windows)
  C2  planner, judge and expert system prompts are identical between the counterparts
  C3  every referenced model exists on its endpoint
  C4  privacy local_only, cache off, web research off
  C5  exactly eight experts

Usage (host only):  python3 scripts/benchmark_template_matrix.py [--out docs/system/benchmark-template-matrix.md]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
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
        "native_default": "qwen3.8:27b",
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
2. **Spur 2 is comparable.** All six templates pass C1 to C5.
3. **System prompts are aligned (C2)** by `scripts/align_benchmark_template_prompts.py`. Experts: each category gets the
   training role prompt of its assigned domain expert; judge: the training judge prompt. **Planner: not the training
   prompt.** An A/B test (9 planner calls per variant, 2026-09-19) showed that the training preamble with a
   training-format category block never routed a GDPR question to `governance` (0/6) and once returned an empty plan,
   while the original list format routed it 3/3 and never returned an empty plan. The canonical planner prompt is
   therefore the original descriptive list ("- category: description" plus the empty-plan guard) for each template's own
   categories in a fixed order; for Spur 1 it is byte-identical to the validated original. The same category has the
   same expert prompt hash in both tracks. Expert-prompt alignment itself has no A/B evidence yet.
4. **Every template has exactly eight experts (C5, operator requirement 2026-09-19).** Spur 2 carried 15 categories; it
   now has the eight Spur 1 categories. `precision_tools` keeps the slot of the former `tool_expert` and
   `compounding_knowledge` the slot of the former `graphrag` (models, endpoints, MCP tool lists unchanged); the categories
   `systems_programming`, `web_researcher`, `reasoning`, `math`, `technical_support`, `dynamic` and `science` were removed.
   Both planners therefore list the same eight experts with the same text (prompt hash 7c72ab83 in both tracks). Backup:
   `benchmarks/results/runbook/template_prompts_backup_20260919T130053Z.json`. Spur 2 keeps its MCP tool lists per expert;
   the Spur 1 experts have none (not a C1 criterion, but a structural difference).
5. **Empty-plan guard in the planner prompt.** The sentence "You MUST always produce at least one task. NEVER return an
   empty JSON array." is not part of the planner training prompt (`generate_planner_dataset.py` rejects empty plans as a
   training sample). It was already in the Spur 1 fine-tuned template before the alignment (byte-identical) and its
   wording matches the runtime fallback prompts added on 2026-08-08 (commit `fb7b5378`, for a planner that returned `[]`);
   the date it entered the template cannot be established from Git because templates live in the database. The runtime
   also creates a fallback task for an empty plan (`graph/planner.py`), so the guard is redundant for pipeline
   correctness. Whether removing it changes routing was not measured (A/B blocked, see the status log).
6. **Instance placement and expert prompts are identical in both tracks (operator, 2026-09-20):** one expert per instance N02-M60-02..09 in
   ascending port order, judge N04-RTX (:11434), planner N04-RGTX (:11435, same host); expert `mcp_tools` are empty in both tracks. `mcp_tools`
   only selects the "Available Tools" text block appended to the expert system prompt (`services/helpers.py`), the tools themselves are run by
   the planner path; with an empty list the block depends only on the category, so equal categories give equal effective prompts. The
   "Cross-track parity" table above checks this on the live templates.
   **Context windows follow one rule (operator, 2026-09-20): planner and judge = maximum of the model, experts = largest context that fits the VRAM of
   their GPU.** Native maxima (Ollama `/api/show`): OLMo 3 7B and OLMo 3.1 32B 65536, Qwen3.5-9B and Qwen3.8-27B 262144, SmolLM3-3B 65536, Qwen3.5-4B 262144.
   Expert VRAM measurement on idle 8 GB Maxwell GPUs (f16 KV cache, 2026-09-20): Qwen3.5-4B fine-tune 65536 = 5.0 GB / 12.1 tok/s, 98304 = 6.1 GB / 11.9 tok/s,
   114688 = 6.7 GB / 6.8 tok/s, 131072 = 7.3 GB / 0.3 tok/s, 196608 = 33 % CPU offload; SmolLM3-3B fine-tune 48128 = 5.7 GB / 8.7 tok/s, 65536 = 6.9 GB / 8.7 tok/s.
   Chosen: Qwen experts 98304 (last size without throughput loss), SmolLM3 experts 65536 (native maximum, fits). Spur 1 templates still carry 48128 for
   the experts because the templates must not change during the running benchmark: apply `align_benchmark_template_prompts.py --apply --tracks spur1`
   after the run. Not verified on the target GPU: the Qwen3.5-9B planner at its maximum of 262144 needs about 16 GB (9B weights plus 34 KB KV per token) on
   N04-RGTX (18 GB); a load test on an idle multi-GPU instance placed only 7 GB on the GPU (56 % CPU offload). The Spur 2 planner is therefore provisionally
   capped at 131072 (about 11 GB by the same formula, operator agreed 2026-09-20); after Spur 1 finishes, test 262144 on N04-RGTX (fully in VRAM, no throughput loss) and raise
   `CONTEXT["spur2"]["planner"]` if it fits. The templates only
   set the cap: the orchestrator requests a context adapted to the prompt size, so short prompts do not allocate the full window.
   **Remaining inherent differences:** the models, the judges (each track is judged by its own fine-tuned judge) and the context windows above. Absolute scores must not
   be compared across tracks; compare each track with its own baseline.
7. **Planner taxonomy (unverified effect):** the planners were trained on a fixed taxonomy (`legal_advisor`, `agentic_coder`, ...);
   `security`, `governance` and `compounding_knowledge` are not part of it. The planner must generalise to them; this is not measured.
8. **Native baseline:** called through the orchestrator route `model@N04-RTX` with the user prompt only (no system
   prompt) at temperature 0.2. Confirmed by the operator on 2026-09-19: Spur 1 `olmo31-32b-instruct-base-fixed:latest`,
   Spur 2 `qwen3.8:27b` (the base model of the Spur 2 judge).
9. **Design per track: 7 conditions** = 3 pre-finetune templates + 3 fine-tuned templates (GraphRAG, no GraphRAG,
   GraphRAG + debate) + 1 native baseline. Each pre-finetune template is compared with its fine-tuned counterpart.
10. **Not part of the matrix:** the Review and Review NoSC variants (review-wave arms), the `LUMI-G Ensemble` hybrids and
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


def effective_expert_prompt(cat: str, ec: dict) -> str:
    """System prompt as sent to the expert: template text plus the tool hint block services/helpers.py appends
    (per-category default block for an empty ``mcp_tools`` list, otherwise a block of the explicit tool names)."""
    sys.path.insert(0, str(ROOT))
    import tool_injector

    tools = ec.get("mcp_tools") or []
    hint = ("explicit:" + ",".join(tools)) if tools else tool_injector.get_tool_block(cat)
    return (ec.get("system_prompt") or "").strip() + "\n#tool-hint:" + hint


def cross_track_diffs(a: dict, b: dict) -> list:
    """Every difference between an Open Source and an Open Weight template except the model names."""
    out = []
    sa, sb = structure(a), structure(b)
    for k in sorted((set(sa) | set(sb)) - {"experts", "name", "id", "description", "planner_num_ctx", "judge_num_ctx"}):
        if sa.get(k) != sb.get(k):
            out.append(f"{k}: {json.dumps(sa.get(k))[:40]} vs {json.dumps(sb.get(k))[:40]}")
    for k in ("planner_model", "judge_model"):
        if split_model(a[k])[1] != split_model(b[k])[1]:
            out.append(f"{k} endpoint: {split_model(a[k])[1]} vs {split_model(b[k])[1]}")
    for k in ("planner_prompt", "judge_prompt"):
        if h(a[k]) != h(b[k]):
            out.append(f"{k} differs")
    if set(a["experts"]) != set(b["experts"]):
        out.append("expert categories differ")
    for cat in sorted(set(a["experts"]) & set(b["experts"])):
        ea, eb = a["experts"][cat], b["experts"][cat]
        for k in sorted((set(sa["experts"][cat]) | set(sb["experts"][cat])) - {"models", "context_window"}):
            if sa["experts"][cat].get(k) != sb["experts"][cat].get(k):
                out.append(f"expert {cat}.{k} differs")
        if [(m["endpoint"], m.get("role")) for m in ea["models"]] != [(m["endpoint"], m.get("role")) for m in eb["models"]]:
            out.append(f"expert {cat} endpoint/role differs")
        if h(effective_expert_prompt(cat, ea)) != h(effective_expert_prompt(cat, eb)):
            out.append(f"expert {cat} effective prompt differs")
    return out


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
          "- **C4** `local_only`, cache off, web research off",
          "- **C5** exactly eight experts", ""]
    csv_rows = []
    for track, spec in TRACKS.items():
        md += [f"## {track}", "", f"Native baseline (single dense LLM without orchestration, default of `run_spur1_and_spur2.sh`): `{spec['native_default']}` on N04-RTX (confirmed by the operator on 2026-09-19).", "",
               "| Role | Template (id) | Planner | Judge | GraphRAG | Debate | Prompt hashes planner / judge | C1 | C2 | C3 | C4 | C5 | Verdict |",
               "|---|---|---|---|:-:|:-:|---|:-:|:-:|:-:|:-:|:-:|---|"]
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
                      and all(h(effective_expert_prompt(c, ec)) == h(effective_expert_prompt(c, ref["experts"][c])) for c, ec in cfg["experts"].items()))
            c5 = len(cfg["experts"]) == 8
            checks = [x for x in (c1, c2) if x is not None] + [c3, c4, c5]
            verdict = "pre-finetune" if counterpart is None and c3 and c4 and c5 else ("sound" if all(checks) else "**not sound**: " + ("; ".join(notes) or "see checks"))
            mark = lambda v: "-" if v is None else ("yes" if v else "**NO**")
            md.append(f"| {role} | `{name}` (`{t['id']}`) | `{pm.split('/')[-1]}` @{pe} ctx {cfg.get('planner_num_ctx')} | `{jm.split('/')[-1]}` @{je} ctx {cfg.get('judge_num_ctx')} | "
                      f"{'on' if cfg.get('enable_graphrag') else 'off'} | {'on' if 'deliberation_policy' in cfg else 'off'} | "
                      f"{h(cfg['planner_prompt'])} / {h(cfg['judge_prompt'])} | {mark(c1)} | {mark(c2)} | {mark(c3)} | {mark(c4)} | {mark(c5)} | {verdict} |")
        md += ["", f"### {track}: expert assignment (category -> model @ endpoint)", "",
               "| Category | Training role prompt | Pre-Finetune reference | Fine-tuned |", "|---|---|---|---|"]
        pre_cfg = tpl[spec["rows"][1][1]]["cfg"]   # pre-finetune, no GraphRAG
        ft_cfg = tpl[spec["rows"][4][1]]["cfg"]    # fine-tuned, no GraphRAG
        for cat in pre_cfg["experts"]:
            pre_m = "; ".join(f"`{m['model'].split('/')[-1]}` @{m['endpoint']}" for m in pre_cfg["experts"][cat]["models"])
            ft_m = "; ".join(f"`{m['model'].split('/')[-1]}` @{m['endpoint']}" + (" (forced)" if m.get("forced") else "") for m in ft_cfg["experts"][cat]["models"])
            md.append(f"| `{cat}` | {h(pre_cfg['experts'][cat]['system_prompt'])} | {pre_m} | {ft_m} |")
            csv_rows.append(dict(track=track, category=cat, prompt_hash=h(pre_cfg["experts"][cat]["system_prompt"]), pre_finetune=pre_m, finetuned=ft_m))
        md.append("")
    md += ["## Cross-track parity (Open Source vs Open Weight)", "",
           "Everything except the model names and the per-model context windows must be identical between the tracks: categories, expert/planner/judge "
           "instances, flags, planner/judge prompts and the effective expert prompt (template text plus tool hint block). Context windows follow the rule "
           "\"planner and judge = maximum of the model, experts = largest context that fits the 8 GB GPU\" and are listed per track.", "",
           "| Condition | Differences | Context planner / judge / expert (Spur 1 vs Spur 2) |", "|---|---|---|"]
    rows1, rows2 = TRACKS["Spur 1 (open source)"]["rows"], TRACKS["Spur 2 (open weight)"]["rows"]
    for (role, n1, _), (_, n2, _) in zip(rows1, rows2):
        d = cross_track_diffs(tpl[n1]["cfg"], tpl[n2]["cfg"])
        c1, c2 = tpl[n1]["cfg"], tpl[n2]["cfg"]
        ctx = lambda c: f"{c.get('planner_num_ctx')} / {c.get('judge_num_ctx')} / {sorted({e.get('context_window') for e in c['experts'].values()})[0]}"
        md.append(f"| {role} | {'none' if not d else '**' + str(len(d)) + '**: ' + '; '.join(d[:6])} | {ctx(c1)} vs {ctx(c2)} |")
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
