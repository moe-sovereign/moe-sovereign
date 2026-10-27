# Runbook: Measurement Fixes, P0 Pipeline Fixes, Review Wave and A/B Benchmark

Status: **in progress** (2026-09-18). Steps A0–E2 implemented and tested on branch `feature/parallel-review-wave`; deploy, live validation and benchmark arms B–D follow. Arm A (pre-runbook baseline) was dropped when the user requested the deploy while arm N was running; see section "Execution log" at the end.
Author of plan: Claude Code session 2026-09-18
Evidence base: `docs/experiments/2026-09-18-experten-parallelisierung-bewertung.md`
Executor: any coding agent. The steps are written so that they can be executed literally.

---

## 0. Rules for the executor (read completely before step A0)

1. Execute tasks **strictly in the order of Section 2**. Do not skip, merge or reorder tasks.
2. Every code change is given as **FIND** (exact existing text) and **REPLACE** (new text).
   - If a FIND block does not match the file exactly (whitespace included), **STOP** and report
     `FIND mismatch in <file> at task <ID>`. Do not guess a different location.
   - Line numbers are hints from commit `e3e29a87`. The FIND text is authoritative.
3. After every task, run that task's **VERIFY** commands. If any fails, **STOP** and report the full
   output. Do not "fix forward" beyond the task's scope.
4. Never push to `main`, never push to any remote, never open a PR, never commit unless a step
   explicitly says `COMMIT`. `COMMIT` steps commit locally on the feature branch only.
5. Never run `docker compose up`, `restart`, `build` or `recreate` unless the step says `DEPLOY`,
   and never while a benchmark process is running (check with A0's command).
6. Steps marked `ASK USER` require explicit user approval in chat before execution. Wait for the answer.
7. Never use `SYSTEM_API_KEY`. Benchmarks use the key in `benchmarks/.env` (`MOE_API_KEY`).
8. Never print secrets (API keys, DB passwords) into logs, chat, or files.
9. Write all code, comments and docs in English. Communicate with the user in German.
10. After each finished task, append one line to `agent_status/claude-code.md`
    (format given in A2) and continue.

Working directory for all commands: `/opt/deployment/moe-sovereign/moe-infra` unless stated otherwise.

Test command used throughout (runs on the host, no container needed):

```bash
cd /opt/deployment/moe-sovereign/moe-infra && timeout 900 python3 -m pytest -q <TESTFILES> 2>&1 | tail -15
```

---

## 1. Task list (overview)

| ID | Title | Type | Needs deploy | Depends on |
|---|---|---|---|---|
| A0 | Wait until the running Spur-2 benchmark has finished | gate | – | – |
| A1 | Create feature branch | git | – | A0 |
| A2 | Status lease + SessionMesh record | governance | – | A1 |
| A3 | Baseline test run (focused + full suite failure list) | verify | – | A2 |
| A4 | Separate pre-existing uncommitted work (ASK USER) | git | – | A3 |
| A5 | Fix pre-existing env-contract test failure (GUARD_NUM_CTX) | bugfix | no | A4 |
| B1 | Benchmark judge receives reference answer + rubric | harness fix | no (host script) | A5 |
| B2 | Numeric-tolerance deterministic scoring | harness fix | no | B1 |
| B3 | Pipeline commit + arm label in benchmark report | harness fix | no | B2 |
| B4 | Phase-timing extraction script | new tool | no | B3 |
| B5 | Arm comparison script | new tool | no | B4 |
| F1 | Run arm N (native baseline) and arm A (current pipeline) | benchmark | no | B5 |
| C1 | Trust score: exclude self-critique/review entries from expert_count | bugfix | yes | F1 |
| C2 | Self-critique router: stop when a round did not raise trust | bugfix | yes | C1 |
| C3 | Critic: strip `<think>` blocks + log non-compliance reason | bugfix | yes | C2 |
| C4 | Telemetry written for no_cache requests + real wall_clock_ms | bugfix | yes | C3 |
| C5 | Quality probe wired into OpenAI chat path | bugfix | yes | C4 |
| C6 | Log endpoint queue wait (semaphore hotspot metric) | observability | yes | C5 |
| C7 | Make complex-task planner budget configurable (default unchanged) | config knob | yes | C6 |
| C7b | Document new env vars in `.env.example` | docs | – | C7 |
| C8 | COMMIT + DEPLOY P0 | deploy | yes | C7b |
| F2 | Run arm B (P0) | benchmark | – | C8 |
| E1 | Pass `review_lenses` through template resolution | feature | yes | F2 |
| E2 | Review wave in expert node | feature | yes | E1 |
| E3 | Router: review wave may replace self-critique (P2) | feature | yes | E2 |
| E4 | Create arm templates C and D (ASK USER) | data | – | E3 |
| E5 | COMMIT + DEPLOY review wave | deploy | yes | E4 |
| F3 | Run arms C and D | benchmark | – | E5 |
| F4 | Evaluate, apply decision gates G1–G3 | analysis | – | F3 |
| G1 | (conditional) Delphi round mode for moderated deliberation | feature | yes | F4 gate G3 |
| G2 | (conditional) Diversity hint in arm template planner prompt | data | – | F4 gate G3 |
| H1 | Translate evaluation doc to English + status labels | docs | – | F4 |
| H2 | Final report to user; commit/push/PR only after ASK USER | wrap-up | – | H1 |

Estimated wall time: code work ~1 day; benchmarks ~35–45 h machine time (see F-steps).

---

## 2. Tasks

### A0 — Wait for the running benchmark

```bash
pgrep -af run_scientific_benchmark.py || echo "NO BENCHMARK RUNNING"
```

- If a process is listed: do **nothing else**. Re-check every 30 minutes. Do not proceed.
- Proceed only when the output is `NO BENCHMARK RUNNING`.
- Then save the finished orchestrator log (it is lost when the container is recreated later):

```bash
mkdir -p benchmarks/results/orchestrator_logs
docker logs -t langgraph-orchestrator > benchmarks/results/orchestrator_logs/pre_runbook_$(date -u +%Y%m%dT%H%M%SZ).log 2>&1
```

VERIFY: the saved file is larger than 1 MB (`ls -la benchmarks/results/orchestrator_logs/`).

### A1 — Feature branch

```bash
git status --short | head -50
git stash list | head -5
git switch -c feature/parallel-review-wave
```

- The working tree already contains unrelated uncommitted changes (admin UI, `.env.example`, etc.).
  **Do not stash, reset, or commit them.** They stay in the working tree untouched.
- In every `COMMIT` step, stage only the files named in that step (`git add <file> ...`),
  never `git add -A` or `git add .`.

VERIFY: `git branch --show-current` prints `feature/parallel-review-wave`.

### A2 — Status lease and SessionMesh

1. Read all files in `agent_status/*.md` and check for an `in_progress` entry that owns
   `graph/expert.py`, `graph/synthesis.py`, `services/trust_score.py`, `services/response_commit.py`,
   `services/pipeline/chat.py`, `services/routing.py` or `benchmarks/run_scientific_benchmark.py`.
   If one exists and is younger than 4 hours: **STOP** and report it to the user.
2. Append to `agent_status/claude-code.md`:

```markdown
## <UTC ISO timestamp> — RUNBOOK parallel-review-wave — starting
Plan / progress:
- Executing docs/experiments/2026-09-18-parallel-review-wave-runbook.md
- Owned files: benchmarks/run_scientific_benchmark.py, benchmarks/analyze_*.py,
  services/trust_score.py, graph/synthesis.py, graph/expert.py, services/response_commit.py,
  services/pipeline/chat.py, services/routing.py, complexity_estimator.py, pipeline/state.py, main.py
Notes:
- No container rebuild until step C8.
```

3. Record the task in SessionMesh (tool `sessionmesh_record_task`) with the text:
   `Runbook parallel-review-wave started (docs/experiments/2026-09-18-parallel-review-wave-runbook.md), branch feature/parallel-review-wave`.

For every later task use this one-line format in the same file:
`- <UTC timestamp> <TASK-ID> done: <one sentence result>`

### A3 — Baseline tests

```bash
timeout 900 python3 -m pytest -q tests/test_trust_score.py tests/test_self_critique.py tests/test_response_commit.py tests/test_scientific_benchmark_harness.py tests/test_deliberation_runtime.py 2>&1 | tail -5
```

VERIFY: last line reads `57 passed` (warnings allowed). A different count means the tree changed
since planning: report the number to the user and continue only if all tests pass.

Then record the full-suite baseline (the list of tests that already fail **before** any runbook change):

```bash
mkdir -p benchmarks/results/runbook
timeout 1500 python3 -m pytest -q -p no:cacheprovider tests 2>&1 \
  | grep -E "^(FAILED|ERROR)" | sed 's/ - .*//' | sort > benchmarks/results/runbook/baseline_failures.txt
cat benchmarks/results/runbook/baseline_failures.txt
```

At planning time (2026-09-18) this file contained exactly one line:
`FAILED tests/smoke/test_env_contract.py::test_no_new_undocumented_env_vars` (fixed in A5).

**Definition used by every later "full suite" check ("no new failures"):**

```bash
timeout 1500 python3 -m pytest -q -p no:cacheprovider tests 2>&1 \
  | grep -E "^(FAILED|ERROR)" | sed 's/ - .*//' | sort > benchmarks/results/runbook/current_failures.txt
comm -13 benchmarks/results/runbook/baseline_failures.txt benchmarks/results/runbook/current_failures.txt
```
The `comm` output must be empty. Any line printed is a new failure → STOP and report it.

### A4 — Separate pre-existing uncommitted work (ASK USER)

At planning time these runbook target files already contained **uncommitted changes from earlier
sessions** that are already deployed in the running container (embedding prior for routing bandits,
guard pre-warm, merger output budget, benchmark harness changes), plus untracked Python modules they
depend on (e.g. `services/routing_patterns.py`):

```bash
git status --short -- benchmarks/run_scientific_benchmark.py graph/expert.py graph/synthesis.py main.py \
  pipeline/state.py services/pipeline/chat.py services/response_commit.py services/routing.py \
  services/trust_score.py complexity_estimator.py .env.example
git ls-files --others --exclude-standard | grep -E "\.py$"
```

A runbook `COMMIT` with `git add <file>` would silently include those foreign changes.

`ASK USER` (German) with exactly these two options:
1. **(recommended)** "Vor dem Runbook einen separaten Commit `chore: snapshot previously deployed
   uncommitted work` auf dem Feature-Branch anlegen, mit den bereits vorhandenen Änderungen dieser
   Dateien und den unversionierten Python-Modulen aus der Liste."
2. "Keine Commits während des Runbooks; stattdessen an jedem COMMIT-Schritt nur ein Patch sichern."

If the user chooses **1**:
```bash
git add benchmarks/run_scientific_benchmark.py graph/expert.py graph/synthesis.py main.py pipeline/state.py \
        services/pipeline/chat.py services/response_commit.py
git add $(git ls-files --others --exclude-standard | grep -E "\.py$" | grep -vE "^tests/test_(admin_table_columns|ui_experience)_browser\.py$")
git status --short | head -40   # show the user what is staged before committing
git commit -m "chore: snapshot previously deployed uncommitted work

Contains changes from earlier sessions that were already running in the
langgraph-orchestrator image, committed separately so later runbook
commits contain only runbook changes.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```
Only add files from the list above whose `git status` shows a change; `git add` of an unchanged file is harmless.
Do not add `.env.example`, admin UI files or browser tests here (they belong to other work).

If the user chooses **2**: replace every later `COMMIT` block by
`git diff -- <same file list> > benchmarks/results/runbook/<TASK-ID>.patch` and do not run `git commit`.
`MOE_PIPELINE_COMMIT` then must be set to `"$(git rev-parse --short HEAD)-dirty-<TASK-ID>"`.

### A5 — Fix pre-existing env-contract failure

`tests/smoke/test_env_contract.py::test_no_new_undocumented_env_vars` fails because `GUARD_NUM_CTX`
(`config.py`, default `4096`) is not documented in `.env.example`.

File: `.env.example`

FIND:
```text
GUARD_TIMEOUT=15
```
REPLACE:
```text
GUARD_TIMEOUT=15
# Context window (num_ctx) for guard-model calls and the guard pre-warm (default 4096)
GUARD_NUM_CTX=4096
```

VERIFY: `timeout 300 python3 -m pytest -q -p no:cacheprovider tests/smoke/test_env_contract.py 2>&1 | tail -2` → all passed.
Then regenerate the baseline so it no longer contains this test:
`timeout 1500 python3 -m pytest -q -p no:cacheprovider tests 2>&1 | grep -E "^(FAILED|ERROR)" | sed 's/ - .*//' | sort > benchmarks/results/runbook/baseline_failures.txt`

COMMIT (only if A4 option 1): `.env.example` contains foreign uncommitted changes too. Do **not** commit it
here; it is committed together with C7b's additions after `ASK USER` in H2.

---

### B1 — Benchmark judge receives reference answer and rubric

Why: `judge_evaluation` reads `ground_truth_reference` and `evaluation_rules.semantic_criteria`,
which no test case in `datasets/sovereign_scientific_benchmark_v1.json` contains. The dataset has
`expected_answer` (or `turns[-1].expected_behavior`) and `scoring.rubric`. The judge therefore graded
without reference and without rubric.

File: `benchmarks/run_scientific_benchmark.py`

FIND:
```python
    criteria = test_case.get("evaluation_rules", {}).get("semantic_criteria", "")
    ground_truth = test_case.get("ground_truth_reference", "")
```
REPLACE:
```python
    criteria = (
        (test_case.get("evaluation_rules") or {}).get("semantic_criteria", "")
        or (test_case.get("scoring") or {}).get("rubric", "")
    )
    ground_truth = test_case.get("ground_truth_reference", "") or _derive_ground_truth(test_case)
    if not ground_truth:
        logger.warning("Judge evaluation for %s has no reference answer", test_case.get("id"))
```

Then add this helper function directly **above** the line `async def judge_evaluation(`:

```python
def _derive_ground_truth(test_case: Dict[str, Any]) -> str:
    """Reference answer for the judge prompt.

    The v1 dataset stores the reference as ``expected_answer`` (single-turn)
    or ``turns[-1].expected_behavior`` (multi-turn), not as
    ``ground_truth_reference``.
    """
    expected = test_case.get("expected_answer")
    if expected:
        if isinstance(expected, (dict, list)):
            return json.dumps(expected, ensure_ascii=False, indent=2)
        return str(expected)
    turns = test_case.get("turns") or []
    if turns and isinstance(turns[-1], dict) and turns[-1].get("expected_behavior"):
        return str(turns[-1]["expected_behavior"])
    return ""
```

Check that `json` is imported at the top of the file (`grep -n "^import json" benchmarks/run_scientific_benchmark.py`).
If it is not, add `import json` to the import block.

Tests — append to `tests/test_scientific_benchmark_harness.py`:

```python
class TestJudgeReference:
    def test_single_turn_reference_from_expected_answer(self):
        from benchmarks.run_scientific_benchmark import _derive_ground_truth
        ref = _derive_ground_truth({"expected_answer": {"annual_mwh": 7358.4}})
        assert "7358.4" in ref

    def test_multi_turn_reference_from_last_turn(self):
        from benchmarks.run_scientific_benchmark import _derive_ground_truth
        ref = _derive_ground_truth({"turns": [{"prompt": "a"}, {"prompt": "b", "expected_behavior": "quorum loss"}]})
        assert ref == "quorum loss"

    def test_missing_reference_is_empty(self):
        from benchmarks.run_scientific_benchmark import _derive_ground_truth
        assert _derive_ground_truth({"id": "x"}) == ""

    @pytest.mark.asyncio
    async def test_judge_prompt_contains_reference_and_rubric(self, monkeypatch):
        import benchmarks.run_scientific_benchmark as rsb
        captured = {}

        async def _fake_query(client, model, messages, **kwargs):
            captured["prompt"] = messages[0]["content"]
            return {"ok": True, "content": '{"score": 7.0, "reasoning": "ok", "verdict": "PASS"}'}

        monkeypatch.setattr(rsb, "query_moe_orchestrator", _fake_query)
        tc = {
            "id": "t1", "discipline": "d", "task_name": "n", "complexity": "expert",
            "expected_answer": {"required_concepts": ["acquire"]},
            "scoring": {"rubric": "RUBRIC-MARKER"},
        }
        res = await rsb.judge_evaluation(client=None, test_case=tc, prompt="p", response_text="r")
        assert "acquire" in captured["prompt"]
        assert "RUBRIC-MARKER" in captured["prompt"]
        assert float(res.get("score")) == 7.0
```

VERIFY:
```bash
timeout 600 python3 -m pytest -q tests/test_scientific_benchmark_harness.py 2>&1 | tail -3
python3 -c "import ast;ast.parse(open('benchmarks/run_scientific_benchmark.py').read())"
```
If the async test fails with "async def functions are not natively supported", check how the existing
async tests in the same file are marked (`grep -n "asyncio" tests/test_scientific_benchmark_harness.py`)
and use exactly that marker instead of `@pytest.mark.asyncio`.

### B2 — Numeric tolerance scoring

Why: `sci-precision-02` has `scoring.type = "numeric_tolerance"` and `tolerance_pct = 0.5`, but
`deterministic_score` only does substring matching (`"1361304"` does not match `"1,361,304"`).

File: `benchmarks/run_scientific_benchmark.py`

FIND:
```python
def deterministic_score(response: str, scoring_cfg: Dict[str, Any]) -> float:
    """Compute deterministic score based on required keywords and exact numbers."""
    if not response:
        return 0.0
```
REPLACE:
```python
_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _numbers_in(text: str) -> List[float]:
    values: List[float] = []
    for raw in _NUMBER_RE.findall(text or ""):
        try:
            values.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return values


def numeric_tolerance_score(response: str, expected: Dict[str, Any], tolerance_pct: float) -> float:
    """Share of expected numeric values found in the response within tolerance, scaled to 0..10."""
    targets = [float(v) for v in (expected or {}).values() if isinstance(v, (int, float))]
    if not targets:
        return 10.0
    found = _numbers_in(response)
    hits = 0
    for target in targets:
        allowed = abs(target) * tolerance_pct / 100.0
        if any(abs(value - target) <= allowed for value in found):
            hits += 1
    return round(hits / len(targets) * 10.0, 2)


def deterministic_score(response: str, scoring_cfg: Dict[str, Any], expected_answer: Optional[Dict[str, Any]] = None) -> float:
    """Compute deterministic score based on required keywords and exact numbers."""
    if not response:
        return 0.0
    if scoring_cfg.get("type") == "numeric_tolerance" and isinstance(expected_answer, dict):
        return numeric_tolerance_score(
            response, expected_answer, float(scoring_cfg.get("tolerance_pct") or 0.5)
        )
```

FIND:
```python
        det_score = deterministic_score(final_response, scoring_cfg)
```
REPLACE:
```python
        det_score = deterministic_score(final_response, scoring_cfg, expected_answer)
```

Check that `re`, `List` and `Optional` are imported (`grep -n "^import re\|from typing" benchmarks/run_scientific_benchmark.py`); add whatever is missing.
Check that `expected_answer` is defined in `run_single_test_condition` (it is, at the line
`expected_answer = test_case.get("expected_answer", {})`).

Tests — append to `tests/test_scientific_benchmark_harness.py`:

```python
class TestNumericTolerance:
    def test_formatted_numbers_match(self):
        from benchmarks.run_scientific_benchmark import numeric_tolerance_score
        assert numeric_tolerance_score("Cost: 1,361,304.00 EUR", {"c": 1361304.0}, 0.5) == 10.0

    def test_out_of_tolerance_fails(self):
        from benchmarks.run_scientific_benchmark import numeric_tolerance_score
        assert numeric_tolerance_score("Cost: 1300000", {"c": 1361304.0}, 0.5) == 0.0

    def test_keyword_type_unchanged(self):
        from benchmarks.run_scientific_benchmark import deterministic_score
        assert deterministic_score("Acquire Release", {"required_keywords": ["Acquire", "Release"]}) == 10.0
```

VERIFY: same as B1.

### B3 — Pipeline commit and arm label in the report

File: `benchmarks/run_scientific_benchmark.py`

FIND:
```python
    output_payload = {
        "run_id": run_id,
        "timestamp": timestamp,
        "dataset": DATASET_PATH.name,
```
REPLACE:
```python
    output_payload = {
        "run_id": run_id,
        "timestamp": timestamp,
        "dataset": DATASET_PATH.name,
        "arm": os.environ.get("MOE_BENCHMARK_ARM", ""),
        "pipeline_commit": os.environ.get("MOE_PIPELINE_COMMIT", ""),
        "judge_reference_fix": True,
```

VERIFY: `python3 -c "import ast;ast.parse(open('benchmarks/run_scientific_benchmark.py').read())"` and the B1 test command.

### B4 — Phase-timing extraction script

Create new file `benchmarks/analyze_phase_timings.py` with exactly this content:

```python
#!/usr/bin/env python3
"""Per-request pipeline phase timings from a saved orchestrator log.

Correlates each sidecar record (benchmark start/end timestamps) with the
orchestrator request that ran inside that window and derives phase durations
from the ``--- [NODE] ...`` markers.

Usage:
    python3 benchmarks/analyze_phase_timings.py \
        --sidecar benchmarks/results/sidecar_<run_id>.jsonl \
        --log benchmarks/results/orchestrator_logs/<arm>.log \
        --out benchmarks/results/phases_<run_id>.json
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone

LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+)Z .*?\[(chatcmpl-[0-9a-f-]+)\] - (.*)$")
MARKERS = [
    ("planner", "PLANNER RAW OUTPUT"),
    ("experts", "--- [NODE] EXPERTS"),
    ("review_wave", "--- [NODE] REVIEW-WAVE"),
    ("thinking", "--- [NODE] THINKING"),
    ("merger", "--- [NODE] MERGER & INGEST"),
    ("self_critique", "--- [NODE] SELF-CRITIQUE"),
    ("resolve_conflicts", "resolve_conflicts_node"),
    ("critic", "--- [NODE] CRITIC"),
]


def _ts(value: str) -> float:
    value = value.rstrip("Z")
    if "." in value:
        head, frac = value.split(".", 1)
        value = f"{head}.{frac[:6]}"
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sidecar", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    events = defaultdict(list)  # request_id -> [(ts, text)]
    with open(args.log, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            match = LINE_RE.match(line)
            if match:
                events[match.group(2)].append((_ts(match.group(1)), match.group(3)))

    results = []
    with open(args.sidecar, encoding="utf-8") as handle:
        for raw in handle:
            rec = json.loads(raw)
            if not rec.get("start_ts_utc") or not rec.get("end_ts_utc"):
                continue
            start, end = _ts(rec["start_ts_utc"]), _ts(rec["end_ts_utc"])
            best_id, best_count = "", 0
            for request_id, items in events.items():
                inside = [item for item in items if start <= item[0] <= end]
                has_experts = any("--- [NODE] EXPERTS" in text for _, text in inside)
                if has_experts and len(inside) > best_count:
                    best_id, best_count = request_id, len(inside)
            phases = defaultdict(float)
            counts = defaultdict(int)
            queue_wait_ms = 0
            if best_id:
                marks = []
                for ts, text in events[best_id]:
                    if not start <= ts <= end:
                        continue
                    for name, needle in MARKERS:
                        if needle in text:
                            marks.append((ts, name))
                            counts[name] += 1
                            break
                    wait = re.search(r"Endpoint queue wait: \S+ (\d+) ms", text)
                    if wait:
                        queue_wait_ms += int(wait.group(1))
                marks.sort()
                boundaries = [(start, "pre_planner")] + marks + [(end, "end")]
                for (t0, name), (t1, _) in zip(boundaries, boundaries[1:]):
                    phases[name] += max(0.0, t1 - t0)
            results.append({
                "condition": rec.get("condition"),
                "task_id": rec.get("task_id"),
                "round": rec.get("round"),
                "turn": rec.get("turn"),
                "request_id": best_id,
                "wall_clock_s": rec.get("wall_clock_s"),
                "phase_seconds": {k: round(v, 1) for k, v in phases.items()},
                "phase_counts": dict(counts),
                "queue_wait_ms": queue_wait_ms,
            })

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2)
    print(f"{len(results)} records, {sum(1 for r in results if r['request_id'])} matched -> {args.out}")


if __name__ == "__main__":
    main()
```

VERIFY (uses the pre-runbook log saved in A0 and the last finished Spur sidecar):
```bash
python3 benchmarks/analyze_phase_timings.py \
  --sidecar $(ls -t benchmarks/results/sidecar_scientific_benchmark_*.jsonl | head -1) \
  --log $(ls -t benchmarks/results/orchestrator_logs/pre_runbook_*.log | head -1) \
  --out /tmp/claude-phase-check.json
```
Expected: a line `N records, M matched` with M ≥ 1. If M = 0, report to the user and continue
(the script is still needed for new runs; the pre-runbook log may not cover the sidecar window).

### B5 — Arm comparison script

Create new file `benchmarks/compare_arms.py` with exactly this content:

```python
#!/usr/bin/env python3
"""Compare A/B arms of run_scientific_benchmark.py (valid results only).

Usage:
    python3 benchmarks/compare_arms.py results/run_scientific_benchmark_<ts1>.json [...]
"""
from __future__ import annotations

import json
import math
import sys

VALID = {"EXCELLENT", "PASS", "DEFICIENT", "FAIL"}


def _stats(values):
    n = len(values)
    if n == 0:
        return 0.0, 0.0, (0.0, 0.0)
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1)) if n > 1 else 0.0
    sem = sd / math.sqrt(n) if n > 1 else 0.0
    return mean, sd, (mean - 1.96 * sem, mean + 1.96 * sem)


def main() -> None:
    rows = []
    for path in sys.argv[1:]:
        data = json.load(open(path))
        arm = data.get("arm") or path
        by_cond = {}
        for r in data.get("detailed_results", []):
            by_cond.setdefault(r["condition"], []).append(r)
        for cond, items in by_cond.items():
            valid = [r for r in items if r.get("judge_verdict") in VALID]
            judge = [float(r["judge_score"]) for r in valid]
            total = [float(r["score"]) for r in valid]
            det = [float(r["deterministic_score"]) for r in valid]
            secs = [float(r["total_time_s"]) for r in valid if r.get("total_time_s")]
            jm, jsd, jci = _stats(judge)
            tm, _, tci = _stats(total)
            mean_min = (sum(secs) / len(secs) / 60.0) if secs else 0.0
            rows.append({
                "arm": arm, "condition": cond, "n_valid": len(valid), "n_all": len(items),
                "fallback_rate": round(1 - len(valid) / len(items), 3) if items else 0.0,
                "judge_mean": round(jm, 2), "judge_ci95": [round(jci[0], 2), round(jci[1], 2)],
                "score_mean": round(tm, 2), "score_ci95": [round(tci[0], 2), round(tci[1], 2)],
                "det_mean": round(sum(det) / len(det), 2) if det else 0.0,
                "latency_min": round(mean_min, 1),
                "score_per_minute": round(tm / mean_min, 3) if mean_min else 0.0,
                "self_critique_rounds_mean": round(
                    sum(int(r.get("self_critique_round") or 0) for r in valid) / len(valid), 2
                ) if valid else 0.0,
            })
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
```

VERIFY:
```bash
python3 benchmarks/compare_arms.py benchmarks/results/run_scientific_benchmark_20260918-053904.json | head -30
```
Expected: JSON rows for four conditions, no traceback.

COMMIT (local only):
```bash
git add benchmarks/run_scientific_benchmark.py benchmarks/analyze_phase_timings.py benchmarks/compare_arms.py tests/test_scientific_benchmark_harness.py
git commit -m "benchmark: judge gets reference+rubric, numeric tolerance scoring, arm metadata, analysis tools

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### F1 — Arm N (native baseline) and arm A (current pipeline)

This measures the **current** pipeline code with the **fixed** harness. The container is not touched.

Common environment for every benchmark run in this runbook:

```bash
cd /opt/deployment/moe-sovereign/moe-infra/benchmarks
set -a; source .env; set +a
export MOE_API_BASE="http://localhost:8002"
export MOE_JUDGE_MODEL="hf.co/h3rb3rn/sovereign-judge-olmo31-32b:Q4_K_M"
export MOE_JUDGE_NODE="N04-RTX"
export MOE_BENCHMARK_NATIVE_MODEL="olmo3-7b-instruct-base-fixed:latest"
export MOE_BENCHMARK_NUM_ROUNDS="3"
export MOE_BENCHMARK_SKIP_PREFLIGHT="1"
export BENCHMARK_SUITE="spur1_opensource"
export MOE_PIPELINE_COMMIT="$(git rev-parse --short HEAD)"
```

Notes added during execution (2026-09-18):
- For arm A set `MOE_PIPELINE_COMMIT=34b3a3c3` (the snapshot commit that equals the code baked into the
  running image), not `git rev-parse HEAD`.
- **Known confound:** the running container has `PLANNER_MAX_TASKS=4` (env at container creation), while `.env`
  already says `8` (edited later, never applied). The C8 deploy recreates the container and makes 8 effective.
  Arm A therefore runs with contract ceiling 4, arms B-D with 8. The soft complex budget stays 4
  (`PLANNER_BUDGET_COMPLEX`), so the effect should be small, but plans with 5-8 tasks that fail the contract
  under arm A will pass under B-D. Report contract failures per arm in F4.
- In `analyze_phase_timings.py` output the phase `pre_planner` is the planner duration plus request setup
  (the `PLANNER RAW OUTPUT` marker is emitted at the end of planning).
- Judge check with the fixed harness (real call): 11 s, valid JSON, verdict PASS.

Before starting, confirm the judge model name matches the one used by the Spur runs:
`grep -m1 "Judge Model" results/lumig_spur2_openweight_*.log`. If it differs, use the value from that log.

Run arm N (native baseline, all 8 tasks × 3 rounds, expected ~3–4 h):

```bash
MOE_BENCHMARK_ARM="N-native" MOE_BENCHMARK_CONDITIONS="native_baseline" \
  nohup python3 run_scientific_benchmark.py --fresh > results/arm_N_$(date +%Y%m%d-%H%M%S).log 2>&1 &
```

When it has finished (`pgrep -af run_scientific_benchmark.py` is empty), copy its report so later runs
cannot overwrite it:

```bash
cp results/latest_scientific_benchmark.json results/arm_N_report.json
```

Run arm A (current pipeline, expected ~10–12 h):

```bash
MOE_BENCHMARK_ARM="A-current" MOE_BENCHMARK_CONDITIONS="compound_ai" \
MOE_BENCHMARK_TEMPLATE_COMPOUND_AI="LUMI-G OLMo + SmolLM3 Sovereign Ensemble" \
  nohup python3 run_scientific_benchmark.py --fresh > results/arm_A_$(date +%Y%m%d-%H%M%S).log 2>&1 &
```

After it finished:
```bash
cp results/latest_scientific_benchmark.json results/arm_A_report.json
docker logs -t langgraph-orchestrator > results/orchestrator_logs/arm_A.log 2>&1
python3 analyze_phase_timings.py --sidecar results/sidecar_$(python3 -c "import json;print(json.load(open('results/arm_A_report.json'))['run_id'])").jsonl \
  --log results/orchestrator_logs/arm_A.log --out results/phases_arm_A.json
python3 compare_arms.py results/arm_N_report.json results/arm_A_report.json
```

VERIFY: `fallback_rate` for arm A ≤ 0.2. If it is higher, **STOP**: this is an infrastructure problem
(per project rule, infra errors are analyzed and fixed, never just excluded). Report the error lines
from `results/errors_<run_id>.jsonl` to the user.

---

### C1 — Trust score: self-critique and review entries are not experts

Why: `self_critique_node` appends `[SELF_CRITIQUE_Rn / judge]: …` to `expert_results`; it counts as an
additional expert (+0.05 trust per round). The review wave (E2) will add `[REVIEW:…]` entries that
also must not count, because the trust verdict gates self-critique, the critic and HITL.

File: `services/trust_score.py`

FIND:
```python
    non_empty_experts = [
        r
        for r in expert_results
        if r
        and len(r.strip()) > 20
        and " ERROR]:" not in r
        and not r.startswith("[Judge unavailable")
    ]
```
REPLACE:
```python
    non_empty_experts = [
        r
        for r in expert_results
        if r
        and len(r.strip()) > 20
        and " ERROR]:" not in r
        and not r.startswith("[Judge unavailable")
        # Judge-authored self-critique fragments and complementary reviews are
        # not independent expert answers; counting them inflated the score by
        # +1/_MAX_EXPERT_COUNT per round and let the loop "pass" itself.
        and not r.startswith(_NON_EXPERT_RESULT_PREFIXES)
    ]
```

Then add directly below the line `_MAX_EXPERT_COUNT = 5    # Normalise expert_count to [0, 1]: min(count/max, 1)`:

```python
# expert_results entries that are appended by the pipeline itself rather than
# produced by a planned expert task.
_NON_EXPERT_RESULT_PREFIXES = ("[SELF_CRITIQUE_", "[REVIEW:")
```

Tests — append to `tests/test_trust_score.py`:

```python
def test_self_critique_and_review_entries_do_not_count_as_experts():
    from services.trust_score import compute_trust_score
    base = {
        "expert_results": ["[m / code_reviewer]: " + "x" * 50],
        "plan": [{"task": "t", "category": "code_reviewer"}],
    }
    inflated = dict(base)
    inflated["expert_results"] = base["expert_results"] + [
        "[SELF_CRITIQUE_R1 / judge]: " + "y" * 50,
        "[REVIEW:security→code_reviewer / security]: " + "z" * 50,
    ]
    assert compute_trust_score(inflated).factors["expert_count"] == compute_trust_score(base).factors["expert_count"]
```

Before writing the test, check the attribute name of the factor dict on the returned object:
`grep -n "factors" services/trust_score.py | head`. If it is not `factors`, use the name found there.

VERIFY: `timeout 600 python3 -m pytest -q tests/test_trust_score.py tests/test_self_critique.py 2>&1 | tail -3`

### C2 — Self-critique stops when a round did not raise trust

File: `pipeline/state.py`

FIND:
```python
    self_critique_max: int              # Max self-critique rounds (from SELF_CRITIQUE_MAX_ROUNDS env)
```
REPLACE:
```python
    self_critique_max: int              # Max self-critique rounds (from SELF_CRITIQUE_MAX_ROUNDS env)
    self_critique_prev_score: float     # Trust score at the start of the last self-critique round
    review_replaces_self_critique: bool # Review wave ran and the template opted to skip self-critique
```

File: `main.py`

FIND:
```python
                 "self_critique_max": int(__import__("os").getenv("SELF_CRITIQUE_MAX_ROUNDS", "2")),
```
REPLACE:
```python
                 "self_critique_max": int(__import__("os").getenv("SELF_CRITIQUE_MAX_ROUNDS", "2")),
                 "self_critique_prev_score": 0.0,
                 "review_replaces_self_critique": False,
```

Check: `grep -n '"self_critique_max": int(__import__' main.py` must return exactly **one** line
before the edit. If it returns more, apply the same REPLACE to every occurrence.

File: `graph/synthesis.py` — router

FIND:
```python
        sc_round = state_.get("self_critique_round") or 0
        sc_max   = state_.get("self_critique_max") or int(os.getenv("SELF_CRITIQUE_MAX_ROUNDS", "2"))
        if sc_round < sc_max:
            logger.info("🔄 Self-Critique router: round %d/%d, verdict=%s", sc_round + 1, sc_max, trust_verdict)
            return "self_critique"
```
REPLACE:
```python
        sc_round = state_.get("self_critique_round") or 0
        sc_max   = state_.get("self_critique_max") or int(os.getenv("SELF_CRITIQUE_MAX_ROUNDS", "2"))
        if state_.get("review_replaces_self_critique"):
            logger.info("🔄 Self-Critique router: skipped, review wave replaces self-critique")
            return "critic"
        if sc_round >= 1:
            _prev = float(state_.get("self_critique_prev_score") or 0.0)
            _cur = float(state_.get("trust_score") or 0.0)
            _min_gain = float(os.getenv("SELF_CRITIQUE_MIN_GAIN", "0.05"))
            if _cur - _prev < _min_gain:
                logger.info(
                    "🔄 Self-Critique router: stop after round %d, trust gain %.3f < %.3f",
                    sc_round, _cur - _prev, _min_gain,
                )
                return "critic"
        if sc_round < sc_max:
            logger.info("🔄 Self-Critique router: round %d/%d, verdict=%s", sc_round + 1, sc_max, trust_verdict)
            return "self_critique"
```

File: `graph/synthesis.py` — self-critique node (two return statements)

FIND:
```python
            return {"expert_results": [new_expert], "self_critique_round": round_num, **usage}
    except Exception as _ex:
        logger.warning("⚠️ Self-Critique LLM call failed: %s", _ex)

    return {"self_critique_round": round_num}
```
REPLACE:
```python
            return {
                "expert_results": [new_expert],
                "self_critique_round": round_num,
                "self_critique_prev_score": float(trust_score or 0.0),
                **usage,
            }
    except Exception as _ex:
        logger.warning("⚠️ Self-Critique LLM call failed: %s", _ex)

    return {"self_critique_round": round_num, "self_critique_prev_score": float(trust_score or 0.0)}
```

Also in `self_critique_node`, strip reasoning blocks before use.

FIND:
```python
        improved = res.content.strip()
```
REPLACE:
```python
        improved = re.sub(r"<think>.*?</think>", "", res.content or "", flags=re.DOTALL).strip()
```
Check `grep -n "^import re" graph/synthesis.py` returns a line; if not, add `import re` to the imports.

Tests — append to `tests/test_self_critique.py`:

```python
def test_second_round_skipped_without_trust_gain():
    from graph.synthesis import _should_replan
    state = _state(trust_verdict="PROCEED_WITH_ASSUMPTION", self_critique_round=1,
                   self_critique_max=2, trust_score=0.52, self_critique_prev_score=0.52)
    assert _should_replan(state) == "critic"


def test_second_round_runs_with_trust_gain():
    from graph.synthesis import _should_replan
    state = _state(trust_verdict="PROCEED_WITH_ASSUMPTION", self_critique_round=1,
                   self_critique_max=2, trust_score=0.60, self_critique_prev_score=0.52)
    assert _should_replan(state) == "self_critique"


def test_review_wave_flag_skips_self_critique():
    from graph.synthesis import _should_replan
    state = _state(trust_verdict="PROCEED_WITH_ASSUMPTION", self_critique_round=0,
                   self_critique_max=2, review_replaces_self_critique=True)
    assert _should_replan(state) == "critic"


def test_first_round_unaffected():
    from graph.synthesis import _should_replan
    state = _state(trust_verdict="PROCEED_WITH_ASSUMPTION", self_critique_round=0, self_critique_max=2)
    assert _should_replan(state) == "self_critique"
```

VERIFY: `timeout 600 python3 -m pytest -q tests/test_self_critique.py tests/test_trust_score.py 2>&1 | tail -3`

### C3 — Critic: strip reasoning blocks and log the non-compliance reason

Why: the merger strips `<think>…</think>` (`graph/synthesis.py`, `_judge_raw = re.sub(...)`), the critic
does not. A reply `<think>…</think>CONFIRMED` fails `startswith("CONFIRMED")` and is classified
non-compliant (6 of 29 critic runs on 2026-09-18).

File: `graph/synthesis.py`

FIND:
```python
        critic_out   = res.content.strip()
```
REPLACE:
```python
        critic_out   = re.sub(r"<think>.*?</think>", "", res.content or "", flags=re.DOTALL).strip()
        if "</think>" in critic_out:
            # Unterminated/leading reasoning block: keep only the text after it.
            critic_out = critic_out.split("</think>")[-1].strip()
```

FIND:
```python
def _critic_is_noncompliant_confirmation(critic_out: str, original: str) -> bool:
```
REPLACE:
```python
def _critic_noncompliance_reason(critic_out: str, original: str) -> str:
    """Same rules as _critic_is_noncompliant_confirmation, returning which one fired ("" = compliant)."""
    stripped = critic_out.strip()
    if _CRITIC_TRAILING_CONFIRMED_RE.search(stripped):
        return "trailing_confirmed"
    if _CRITIC_PREAMBLE_RE.match(stripped):
        return "preamble"
    code_markers = ("`" * 3, "<!DOCTYPE", "<html", "def ", "function ", "class ", "import ", "setInterval")
    if any(m in original for m in code_markers) and not any(m in critic_out for m in code_markers):
        return "code_dropped"
    return ""


def _critic_is_noncompliant_confirmation(critic_out: str, original: str) -> bool:
```

FIND:
```python
        if _critic_is_noncompliant_confirmation(critic_out, final_response):
            logger.warning(
                "⚠️ Critic: non-compliant judge format (CONFIRMED reached without "
                "the required leading format, or code dropped from the reply) — "
                "preserving merger answer instead of overwriting it with the "
                "judge's deliberation trace"
            )
```
REPLACE:
```python
        if _critic_is_noncompliant_confirmation(critic_out, final_response):
            logger.warning(
                "⚠️ Critic: non-compliant judge format (CONFIRMED reached without "
                "the required leading format, or code dropped from the reply) — "
                "preserving merger answer instead of overwriting it with the "
                "judge's deliberation trace | reason=%s chars=%d head=%r",
                _critic_noncompliance_reason(critic_out, final_response),
                len(critic_out),
                critic_out[:160],
            )
```

Tests — create `tests/test_critic_format.py`:

```python
from graph.synthesis import _critic_noncompliance_reason, _critic_is_noncompliant_confirmation

FENCE = "`" * 3


def test_reason_trailing_confirmed():
    assert _critic_noncompliance_reason("long thoughts ... CONFIRMED", "plain answer") == "trailing_confirmed"


def test_reason_code_dropped():
    original = FENCE + "rust\nfn x(){}\n" + FENCE
    assert _critic_noncompliance_reason("Reorder the stores.", original) == "code_dropped"


def test_compliant_correction():
    original = FENCE + "rust\nfn x(){}\n" + FENCE
    corrected = FENCE + "rust\nfn y(){}\n" + FENCE
    assert _critic_noncompliance_reason(corrected, original) == ""


def test_bool_wrapper_consistent():
    pairs = [("a CONFIRMED", "b"), (FENCE + "x" + FENCE, FENCE + "y" + FENCE), ("Reorder.", FENCE + "z" + FENCE)]
    for out, orig in pairs:
        assert _critic_is_noncompliant_confirmation(out, orig) == bool(_critic_noncompliance_reason(out, orig))
```

VERIFY:
```bash
timeout 600 python3 -m pytest -q tests/test_critic_format.py tests/test_self_critique.py 2>&1 | tail -3
grep -rn "_critic_is_noncompliant_confirmation" tests | head
```
Run every test file listed by the grep as well; all must pass.

### C4 — Telemetry for no_cache requests and real wall_clock_ms

Why: `commit_response_payload` returns `skipped` for `no_cache` before any sink runs, so benchmark
traffic (always `no_cache: true`) never reaches `routing_telemetry` (no rows since 2026-09-14).
All 1318 existing rows have `wall_clock_ms = 0` because the sink hard-codes `wall_clock_ms=0`.
Benchmark isolation must stay: caches, knowledge ingest and learning signals remain skipped.

File: `services/response_commit.py`

FIND:
```python
    if frozen.get("no_cache") or len(response) <= CACHE_MIN_RESPONSE_LEN:
        return {"status": "skipped", "errors": []}
```
REPLACE:
```python
    if frozen.get("no_cache") or len(response) <= CACHE_MIN_RESPONSE_LEN:
        # Caches, knowledge ingest and learning sinks stay skipped (benchmark
        # isolation), but routing telemetry is observability, not learning.
        await _record_routing_telemetry_only(frozen)
        return {"status": "skipped", "errors": []}
```

FIND:
```python
        await telemetry.record_routing_decision(
            state._userdb_pool,
            str(payload.get("request_id") or ""),
            dict(payload),
            wall_clock_ms=0,
        )
```
REPLACE:
```python
        await telemetry.record_routing_decision(
            state._userdb_pool,
            str(payload.get("request_id") or ""),
            dict(payload),
            wall_clock_ms=int(payload.get("wall_clock_ms") or 0),
        )
```

Add this function directly **above** the line `async def commit_response_payload(`:

```python
async def _record_routing_telemetry_only(payload: Mapping[str, Any]) -> None:
    """Write routing telemetry for requests whose other commit sinks are skipped. Never raises."""
    try:
        import telemetry

        await telemetry.record_routing_decision(
            state._userdb_pool,
            str(payload.get("request_id") or ""),
            dict(payload),
            wall_clock_ms=int(payload.get("wall_clock_ms") or 0),
        )
    except Exception as exc:
        logger.debug("Routing telemetry (no-cache path) failed: %s", exc)
```

In `build_response_commit_payload`:

FIND:
```python
        "query_embedding": state_.get("query_embedding") or [],
    }
    return _json_safe(payload)
```
REPLACE:
```python
        "query_embedding": state_.get("query_embedding") or [],
        "wall_clock_ms": _elapsed_ms(state_),
    }
    return _json_safe(payload)
```
and add directly **above** `def build_response_commit_payload(`:

```python
def _elapsed_ms(state_: Mapping[str, Any]) -> int:
    """Milliseconds since request start, derived from the shared request deadline."""
    deadline = float(state_.get("request_deadline_monotonic") or 0.0)
    if deadline <= 0:
        return 0
    started = deadline - float(ORCHESTRATION_TIMEOUT)
    return max(0, int((time.monotonic() - started) * 1000))
```

Imports: check `grep -n "^import time\|ORCHESTRATION_TIMEOUT" services/response_commit.py`.
   - If `import time` is missing, add it to the import block.
   - Add `ORCHESTRATION_TIMEOUT,` inside the existing `from config import (` block.

Tests — append to `tests/test_response_commit.py`:

```python
@pytest.mark.asyncio
async def test_no_cache_request_still_writes_routing_telemetry(monkeypatch):
    calls = []

    async def _fake_record(pool, request_id, payload, wall_clock_ms=0):
        calls.append((request_id, wall_clock_ms))

    import telemetry
    monkeypatch.setattr(telemetry, "record_routing_decision", _fake_record)
    response = "x" * 400
    payload = {
        "request_id": "req-nc", "final_response": response,
        "response_hash": commit.canonical_json_hash(response),
        "no_cache": True, "wall_clock_ms": 1234,
    }
    result = await commit.commit_response_payload(payload)
    assert result["status"] == "skipped"
    assert calls == [("req-nc", 1234)]


def test_elapsed_ms_from_deadline():
    import time as _time
    state_ = {"request_deadline_monotonic": _time.monotonic() + commit.ORCHESTRATION_TIMEOUT - 2.0}
    assert 1500 <= commit._elapsed_ms(state_) <= 5000
    assert commit._elapsed_ms({}) == 0
```

Use the same async-test marker style the file already uses (`grep -n "mark" tests/test_response_commit.py | head -3`).
If `commit.canonical_json_hash` does not exist as a module attribute, import it the way
`services/response_commit.py` imports it and reference it from there.

VERIFY: `timeout 600 python3 -m pytest -q tests/test_response_commit.py 2>&1 | tail -3`

### C5 — Quality probe in the OpenAI chat path

Why: `run_probe` is only called from `services/pipeline/anthropic.py`. The OpenAI-compatible path
(`/v1/chat/completions`, used by Open WebUI and the benchmark) never calls it, so
`pipeline_quality_log` has 0 rows although `MOE_QUALITY_PROBE=1`. The probe must **not** run for
`no_cache` requests (benchmarks), because it adds load to N04-RTX and distorts latency.

File: `services/pipeline/chat.py`

FIND:
```python
    resp.setdefault("metadata", {}).update(_build_diagnostic_metadata(result))
    await _ol_complete(_ol_run_id, job_name="chat_completion",
                       outputs=[dataset_response(chat_id)])
    if _moe_resp_headers:
        return JSONResponse(content=resp, headers=_moe_resp_headers)
    return resp
```
REPLACE:
```python
    resp.setdefault("metadata", {}).update(_build_diagnostic_metadata(result))
    if not request.no_cache:
        # Online quality probe (sampled): pipeline vs. single best expert.
        # Skipped for no_cache traffic so benchmark latency is not distorted.
        try:
            from services.quality_probe import run_probe as _qp_run
            asyncio.create_task(_qp_run(
                query=user_input, pipeline_answer=result["final_response"],
                experts=user_experts, planner_cfg=_tmpl_prompts,
                request_id=chat_id, user_id=user_id,
                pipeline_tokens=c_tok,
                graph_context=result.get("graph_context", ""),
                web_research=result.get("web_research", ""),
                mcp_result=result.get("mcp_result", ""),
            ))
        except Exception:
            pass
    await _ol_complete(_ol_run_id, job_name="chat_completion",
                       outputs=[dataset_response(chat_id)])
    if _moe_resp_headers:
        return JSONResponse(content=resp, headers=_moe_resp_headers)
    return resp
```

Check before editing: this FIND block must occur exactly once
(`grep -c 'resp.setdefault("metadata", {}).update(_build_diagnostic_metadata(result))' services/pipeline/chat.py` → `1`).
Check that `asyncio` is imported at module level in `chat.py` (`grep -n "^import asyncio" services/pipeline/chat.py`).
Check that the enclosing function is `chat_completions` and defines `user_input`, `user_id`,
`user_experts`, `_tmpl_prompts`, `c_tok` (`awk 'NR>=1610 && NR<=3300' services/pipeline/chat.py | grep -n "user_experts =\|_tmpl_prompts =\|c_tok =\|user_input =\|user_id      ="`).
If any name is missing, STOP and report.

VERIFY:
```bash
python3 -c "import ast;ast.parse(open('services/pipeline/chat.py').read())"
timeout 900 python3 -m pytest -q tests -k "chat or quality_probe" 2>&1 | tail -3
```

### C6 — Endpoint queue wait logging

File: `graph/expert.py`

FIND:
```python
        from services.node_load import track as _track_node_load
        async with semaphore, _track_node_load(endpoint):
            task_text  = task_item.get("task", str(task_item))
```
REPLACE:
```python
        from services.node_load import track as _track_node_load
        _queue_wait_t0 = time.monotonic()
        async with semaphore, _track_node_load(endpoint):
            _queue_wait_ms = int((time.monotonic() - _queue_wait_t0) * 1000)
            if _queue_wait_ms >= 1000:
                logger.info(f"⏳ Endpoint queue wait: {endpoint} {_queue_wait_ms} ms ({model_name})")
            task_text  = task_item.get("task", str(task_item))
```

VERIFY: `python3 -c "import ast;ast.parse(open('graph/expert.py').read())"`

### C7 — Configurable complex-task planner budget (default unchanged)

File: `complexity_estimator.py`

FIND:
```python
    else:  # complex
        return {
            "max_tasks":      4,
```
REPLACE:
```python
    else:  # complex
        return {
            # Soft planner budget ("TASK BUDGET" in the planner prompt). The hard
            # contract ceiling is PLANNER_MAX_TASKS. Default kept at 4; raising it
            # is a separate, measured decision (see runbook gate G3).
            "max_tasks":      int(os.getenv("PLANNER_BUDGET_COMPLEX", "4")),
```

VERIFY: `timeout 600 python3 -m pytest -q tests -k complexity 2>&1 | tail -3`

### C7b — Document new environment variables

Append exactly this block to the end of `.env.example`:

```text

# ── Pipeline quality loop / review wave (runbook 2026-09-18) ────────────────
# Self-critique round n+1 only runs if round n raised the trust score by at least this much
SELF_CRITIQUE_MIN_GAIN=0.05
# Soft planner task budget for "complex" requests (hard ceiling: PLANNER_MAX_TASKS)
PLANNER_BUDGET_COMPLEX=4
# Complementary review wave (opt-in per template category via "review_lenses")
MOE_REVIEW_WAVE_ENABLED=1
MOE_REVIEW_WAVE_MAX_REVIEWERS=4
MOE_REVIEW_INPUT_CHARS=6000
# Moderated deliberation round mode: sequential | delphi
MOE_DELIBERATION_ROUND_MODE=sequential
```

VERIFY: `timeout 300 python3 -m pytest -q -p no:cacheprovider tests/smoke/test_env_contract.py 2>&1 | tail -2` → all passed.

### C8 — COMMIT and DEPLOY P0

1. Full suite with the "no new failures" check defined in A3. The `comm` output must be empty.

2. COMMIT:
```bash
git add services/trust_score.py graph/synthesis.py pipeline/state.py main.py services/response_commit.py \
        services/pipeline/chat.py graph/expert.py complexity_estimator.py \
        tests/test_trust_score.py tests/test_self_critique.py tests/test_critic_format.py tests/test_response_commit.py
git commit -m "fix(pipeline): trust artefact, self-critique stop rule, critic think-strip, telemetry, quality probe

- self-critique/review fragments no longer count as experts in trust score
- self-critique round n+1 only if round n raised trust by SELF_CRITIQUE_MIN_GAIN
- critic strips <think> blocks and logs the non-compliance reason
- routing telemetry written for no_cache requests, real wall_clock_ms
- quality probe wired into /v1/chat/completions (skipped for no_cache)
- endpoint queue-wait log line, PLANNER_BUDGET_COMPLEX knob (default 4)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

3. Save the current log, then DEPLOY (only if `pgrep -af run_scientific_benchmark.py` is empty):
```bash
docker logs -t langgraph-orchestrator > benchmarks/results/orchestrator_logs/pre_C8_deploy.log 2>&1
docker images --format '{{.Repository}}:{{.Tag}} {{.ID}}' | grep -i langgraph | head -3   # note the previous image ID for rollback
docker compose build langgraph-app && docker compose up -d --no-deps langgraph-app
```
4. Readiness:
```bash
for i in $(seq 1 30); do docker inspect -f '{{.State.Health.Status}}' langgraph-orchestrator | grep -q healthy && break; sleep 10; done
docker inspect -f '{{.State.Health.Status}}' langgraph-orchestrator
```
Must print `healthy`. If not: rollback with `docker tag <previous image ID> <repository:tag>` and
`docker compose up -d --no-deps langgraph-app`, then STOP and report.

5. Smoke test (one real request, uses the benchmark key, no_cache so it tests the telemetry fix):
```bash
set -a; source benchmarks/.env; set +a
curl -s -m 1800 http://localhost:8002/v1/chat/completions -H "Authorization: Bearer $MOE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"LUMI-G OLMo + SmolLM3 Sovereign Ensemble","no_cache":true,"messages":[{"role":"user","content":"Explain in 5 sentences why acquire/release ordering is needed in a lock-free SPSC ring buffer."}]}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('object'), (d.get('choices') or [{}])[0].get('message',{}).get('content','')[:200])"
docker exec terra_checkpoints psql -U moe_admin -d moe_userdb -At -c "select ts, template_name, wall_clock_ms from routing_telemetry order by ts desc limit 1"
```
Expected: a response (or `moe.hitl_gate`; then approve it the same way the benchmark does:
`curl -s -X POST http://localhost:8002/gates/<gate_id>/approve -H "Authorization: Bearer $MOE_API_KEY"`)
and a `routing_telemetry` row with today's timestamp and `wall_clock_ms > 0`.
Also check `docker logs langgraph-orchestrator 2>&1 | grep -c "Self-Critique router: stop after round"` for later reference.

6. Append the deployed image ID and commit hash to the status log.

### F2 — Arm B (P0)

Same common environment as F1 (re-run the `export` block, `MOE_PIPELINE_COMMIT` now points to the C8 commit):

```bash
MOE_BENCHMARK_ARM="B-p0" MOE_BENCHMARK_CONDITIONS="compound_ai" \
MOE_BENCHMARK_TEMPLATE_COMPOUND_AI="LUMI-G OLMo + SmolLM3 Sovereign Ensemble" \
  nohup python3 run_scientific_benchmark.py --fresh > results/arm_B_$(date +%Y%m%d-%H%M%S).log 2>&1 &
```
After finishing: `cp results/latest_scientific_benchmark.json results/arm_B_report.json`, save the log to
`results/orchestrator_logs/arm_B.log`, run `analyze_phase_timings.py` (output `results/phases_arm_B.json`) and
`compare_arms.py results/arm_N_report.json results/arm_A_report.json results/arm_B_report.json`.
Same fallback-rate check as F1.

---

### E1 — Pass review configuration through template resolution

File: `services/routing.py` — two edits, one per template format.

FIND (new-format branch, inside `models_list.append({ ... })`):
```python
                        "_mcp_tools":     _mcp_tools,
                        "_skills":        _skills,
                    })
                result[cat] = models_list
```
REPLACE:
```python
                        "_mcp_tools":     _mcp_tools,
                        "_skills":        _skills,
                        "_review_lenses": _review_lenses,
                        "_review_replaces_self_critique": _review_replaces_sc,
                    })
                result[cat] = models_list
```

FIND (legacy branch):
```python
                    "_mcp_tools":     _mcp_tools,
                    "_skills":        _skills,
                }]
        return result or None
```
REPLACE:
```python
                    "_mcp_tools":     _mcp_tools,
                    "_skills":        _skills,
                    "_review_lenses": _review_lenses,
                    "_review_replaces_self_critique": _review_replaces_sc,
                }]
        return result or None
```

FIND:
```python
            _skills    = list(cat_cfg.get("skills") or []) if isinstance(cat_cfg, dict) else []
```
REPLACE:
```python
            _skills    = list(cat_cfg.get("skills") or []) if isinstance(cat_cfg, dict) else []
            # Complementary review wave (graph/expert.py): categories whose
            # models review this category's output, opt-in per template.
            _review_lenses = [
                str(x) for x in (cat_cfg.get("review_lenses") or [])
                if isinstance(x, str) and x.strip()
            ] if isinstance(cat_cfg, dict) else []
            _review_replaces_sc = bool(cat_cfg.get("review_replaces_self_critique", False)) if isinstance(cat_cfg, dict) else False
```

Check that the FIND for `_skills    = list(...)` occurs exactly once in the file.

Test — append to the routing test file (`ls tests | grep -i routing`; if none exists create `tests/test_routing_review_lenses.py`):

```python
import json


def test_review_lenses_passed_through(monkeypatch):
    import services.routing as routing
    tmpl = {"experts": {"code_reviewer": {
        "models": [{"role": "primary", "model": "m", "endpoint": "EP"}],
        "review_lenses": ["security"], "review_replaces_self_critique": True,
    }}}
    monkeypatch.setattr(routing, "_resolve_template_selection",
                        lambda *a, **k: {"template": tmpl, "authorized": True})
    experts = routing._resolve_user_experts("{}", override_tmpl_id="x")
    cfg = experts["code_reviewer"][0]
    assert cfg["_review_lenses"] == ["security"]
    assert cfg["_review_replaces_self_critique"] is True
```

Before writing this test, open `services/routing.py` lines 120–150 and verify how
`_resolve_user_experts` obtains the template (function name and signature). If it does not call
`_resolve_template_selection`, monkeypatch the function it actually calls, keeping the assertions unchanged.

VERIFY: `timeout 600 python3 -m pytest -q tests -k "routing or review_lenses" 2>&1 | tail -3`

### E2 — Review wave in the expert node

File: `graph/expert.py`

Insert the following block directly **above** the comment line
`    # Dynamic parallel/sequential execution via dependency levels.`

```python
    async def _run_review_wave(primary_results: List[dict]) -> Tuple[List[dict], List[dict], bool]:
        """One parallel wave of complementary-lens reviews over primary expert outputs.

        Opt-in per template category via ``review_lenses``. Each lens category is
        dispatched at most once per wave, so a single-slot endpoint never gets
        more than one review call. Reviews are prefixed ``[REVIEW:`` so the trust
        score does not count them as experts (services/trust_score.py).
        Returns (results, conflicts, replaces_self_critique).
        """
        if os.getenv("MOE_REVIEW_WAVE_ENABLED", "1") != "1":
            return [], [], False
        if state_.get("force_tier1") or str(state_.get("complexity_level") or "") in ("trivial", "memory_recall"):
            return [], [], False
        catalog = state_.get("user_experts") or {}
        if not catalog:
            return [], [], False
        max_reviewers = int(os.getenv("MOE_REVIEW_WAVE_MAX_REVIEWERS", "4"))
        max_input_chars = int(os.getenv("MOE_REVIEW_INPUT_CHARS", "6000"))

        lens_targets: Dict[str, List[Tuple[str, str]]] = {}
        replaces_sc = False
        for result in primary_results:
            model_cat = str(result.get("model_cat") or "")
            text = str(result.get("res") or "")
            if "::" not in model_cat or " ERROR]" in text or not text.strip():
                continue
            primary_cat = model_cat.rsplit("::", 1)[-1]
            cfgs = catalog.get(primary_cat) or []
            if not cfgs:
                continue
            if cfgs[0].get("_review_replaces_self_critique"):
                replaces_sc = True
            for lens in cfgs[0].get("_review_lenses") or []:
                if lens == primary_cat or not catalog.get(lens):
                    continue
                lens_targets.setdefault(lens, []).append((primary_cat, text))
        if not lens_targets:
            return [], [], False

        selected = list(lens_targets.items())[:max_reviewers]
        logger.info(
            "--- [NODE] REVIEW-WAVE (%d reviewer(s): %s) ---",
            len(selected), ", ".join(lens for lens, _ in selected),
        )
        await _report(f"🔍 Review wave: {', '.join(lens for lens, _ in selected)}")
        user_query = str(state_.get("input") or "")[:4000]

        async def _one(index: int, lens: str, targets: List[Tuple[str, str]]) -> dict:
            reviewed = "\n\n".join(
                f"[Expert output ({pcat})]\n{ptext[:max_input_chars]}" for pcat, ptext in targets
            )
            review_task = {
                "id": f"review-{lens}",
                "category": lens,
                "allowed_domains": [lens] + [pcat for pcat, _ in targets],
                "_deliberation_turn": True,  # caps output at deliberation max_turn_tokens
                "task": (
                    f"[User Query]\n{user_query}\n\n{reviewed}\n\n[Task]\n"
                    f"You are a reviewer from the '{lens}' discipline. Review the expert output(s) "
                    "above strictly from the perspective of your discipline. List concrete defects, "
                    "risks, missing requirements or wrong claims, each with a one-sentence "
                    "justification and, where possible, the exact fix. Do NOT rewrite or "
                    "re-implement the full solution. If you find no defect in your discipline, "
                    "answer exactly: NO_FINDINGS"
                ),
            }
            return await run_single(catalog[lens][0], review_task, 900 + index, 1)

        raw = await asyncio.gather(
            *[_one(i, lens, targets) for i, (lens, targets) in enumerate(selected)],
            return_exceptions=True,
        )
        results: List[dict] = []
        conflicts: List[dict] = []
        from parsing import _improvement_ratio
        for (lens, targets), res in zip(selected, raw):
            if isinstance(res, BaseException) or not isinstance(res, dict):
                logger.warning("Review wave: %s failed: %s", lens, res)
                continue
            text = str(res.get("res") or "")
            if not res.get("model_cat") or " ERROR]" in text:
                continue
            content = text.split("]: ", 1)[1] if "]: " in text else text
            if not content.strip() or "NO_FINDINGS" in content[:200]:
                logger.info("Review wave: %s reported no findings", lens)
                continue
            primaries = ",".join(sorted({pcat for pcat, _ in targets}))
            results.append({**res, "res": f"[REVIEW:{lens}→{primaries} / {lens}]: {content}"})
            for pcat, ptext in targets:
                div_score = _improvement_ratio(ptext[:1200], content[:1200])
                if div_score >= 0.35:
                    conflicts.append({
                        "category": pcat,
                        "proposition_a": ptext[:600],
                        "proposition_b": content[:600],
                        "divergence_score": round(div_score, 3),
                        "resolution": "pending",
                        "resolved_by": "",
                    })
        return results, conflicts, replaces_sc

```

FIND (end of the node, after the level loop):
```python
    used = [r["model_cat"] for r in all_results if r.get("model_cat")]
    return {
        "expert_results":              [r["res"] for r in all_results if "res" in r],
```
REPLACE:
```python
    review_results, review_conflicts, review_replaces_sc = await _run_review_wave(all_results)
    all_results.extend(review_results)
    local_conflicts.extend(review_conflicts)

    used = [r["model_cat"] for r in all_results if r.get("model_cat")]
    return {
        "review_replaces_self_critique": bool(review_replaces_sc and review_results),
        "expert_results":              [r["res"] for r in all_results if "res" in r],
```

Checks before editing:
- `grep -n "^from typing import" graph/expert.py` includes `Dict`, `List`, `Tuple` (it does at planning time).
- `grep -n "    local_conflicts = \[\]" graph/expert.py` returns exactly one line (node scope list).
- The FIND in step 2 must occur exactly once (the moderated-deliberation branch returns earlier and
  uses a different structure). The review wave therefore does **not** run for moderated deliberation
  templates. This is intended.

Tests — create `tests/test_review_wave.py`. The expert node is large; test only the pure selection
contract by extracting nothing and instead asserting the trust-score and routing contracts plus a
syntax/importability check:

```python
import ast


def test_expert_module_parses_and_contains_review_wave():
    src = open("graph/expert.py", encoding="utf-8").read()
    ast.parse(src)
    assert "async def _run_review_wave(" in src
    assert "[REVIEW:" in src
    assert '"review_replaces_self_critique": bool(review_replaces_sc and review_results)' in src


def test_review_prefix_excluded_from_trust():
    from services.trust_score import _NON_EXPERT_RESULT_PREFIXES
    assert "[REVIEW:" in _NON_EXPERT_RESULT_PREFIXES
```

The behavioural proof of E2 is the live check in E5 (the unit-level node harness does not exist and
must not be invented here).

VERIFY:
```bash
python3 -c "import ast;ast.parse(open('graph/expert.py').read())"
timeout 900 python3 -m pytest -q tests/test_review_wave.py tests/test_trust_score.py tests/test_self_critique.py 2>&1 | tail -3
```

### E3 — Router flag

Already implemented in C2 (`review_replaces_self_critique` check in `_should_replan`) and E2
(the node returns the flag). Nothing to edit. VERIFY: `grep -n "review_replaces_self_critique" graph/synthesis.py graph/expert.py pipeline/state.py main.py` shows at least one hit per file.

### E4 — Arm templates C and D (ASK USER)

`ASK USER`: "Darf ich zwei neue Templates in `admin_expert_templates` anlegen (Kopien von
`tmpl-11f532fc` mit Review-Welle, IDs `tmpl-smollm3-review` und `tmpl-smollm3-review-nosc`) und
dem Benutzer `horndev` (Benchmark-Key) die Nutzungsberechtigung dafür erteilen, analog zu den
bestehenden LUMI-G-Templates? Das bestehende Template wird nicht verändert."

Only after approval, run exactly:

```bash
docker exec -i terra_checkpoints psql -U moe_admin -d moe_userdb -v ON_ERROR_STOP=1 <<'SQL'
BEGIN;
INSERT INTO admin_expert_templates (id, name, description, config_json, is_active, created_at, updated_at)
SELECT 'tmpl-smollm3-review',
       'LUMI-G OLMo + SmolLM3 Sovereign Ensemble - Review',
       'A/B arm C: base ensemble + complementary review wave (runbook 2026-09-18)',
       c::text, true, now()::text, now()::text
FROM (
  SELECT jsonb_set(jsonb_set(jsonb_set(jsonb_set(jsonb_set(jsonb_set(
           config_json::jsonb,
           '{experts,code_reviewer,review_lenses}', '["security"]'),
           '{experts,security,review_lenses}', '["code_reviewer"]'),
           '{experts,research,review_lenses}', '["compounding_knowledge"]'),
           '{experts,governance,review_lenses}', '["research"]'),
           '{experts,data_analyst,review_lenses}', '["security"]'),
           '{experts,compounding_knowledge,review_lenses}', '["research"]') AS c
  FROM admin_expert_templates WHERE id = 'tmpl-11f532fc'
) src;

INSERT INTO admin_expert_templates (id, name, description, config_json, is_active, created_at, updated_at)
SELECT 'tmpl-smollm3-review-nosc',
       'LUMI-G OLMo + SmolLM3 Sovereign Ensemble - Review NoSC',
       'A/B arm D: arm C + review wave replaces self-critique (runbook 2026-09-18)',
       jsonb_set(jsonb_set(jsonb_set(jsonb_set(jsonb_set(jsonb_set(
           config_json::jsonb,
           '{experts,code_reviewer,review_replaces_self_critique}', 'true'),
           '{experts,security,review_replaces_self_critique}', 'true'),
           '{experts,research,review_replaces_self_critique}', 'true'),
           '{experts,governance,review_replaces_self_critique}', 'true'),
           '{experts,data_analyst,review_replaces_self_critique}', 'true'),
           '{experts,compounding_knowledge,review_replaces_self_critique}', 'true')::text,
       true, now()::text, now()::text
FROM admin_expert_templates WHERE id = 'tmpl-smollm3-review';

-- Same grant the existing LUMI-G templates have (user horndev, resource_type expert_template)
INSERT INTO permissions (id, user_id, resource_type, resource_id, granted_at)
SELECT 'perm-smollm3-review', user_id, resource_type, 'tmpl-smollm3-review', now()::text
FROM permissions WHERE resource_id = 'tmpl-11f532fc';
INSERT INTO permissions (id, user_id, resource_type, resource_id, granted_at)
SELECT 'perm-smollm3-review-nosc', user_id, resource_type, 'tmpl-smollm3-review-nosc', now()::text
FROM permissions WHERE resource_id = 'tmpl-11f532fc';
COMMIT;
SQL
```

VERIFY:
```bash
docker exec terra_checkpoints psql -U moe_admin -d moe_userdb -At -c \
 "select id, name, config_json::jsonb #> '{experts,code_reviewer,review_lenses}', config_json::jsonb #> '{experts,code_reviewer,review_replaces_self_critique}' from admin_expert_templates where id like 'tmpl-smollm3-review%'"
```
Expected two rows: `…-review | … | ["security"] |` (empty last column) and `…-review-nosc | … | ["security"] | true`.

**Required after the SQL (found during execution):** the orchestrator reads permissions from a Valkey cache
(`user:apikey:<hash>`, TTL 24 h), not from the database. Without a cache refresh the API key gets
`template_not_authorized` for the new templates. Refresh it with the same function the Admin UI calls after a
permission grant:

```bash
docker exec -w /app moe-admin python3 -c "
import asyncio, json, database
async def main():
    await database.init_db()
    uid = 'b1a128df87944e5f808a9fc9a10e064a'   # user horndev
    await database.sync_user_to_redis(uid)
    s = json.dumps(await database.get_permissions_map(uid))
    print('review:', 'tmpl-smollm3-review' in s, 'review-nosc:', 'tmpl-smollm3-review-nosc' in s)
asyncio.run(main())"
```
Expected output: `review: True review-nosc: True`.

Also verify the grants: `docker exec terra_checkpoints psql -U moe_admin -d moe_userdb -At -c "select id, resource_id from permissions where resource_id like 'tmpl-smollm3-review%'"` → two rows.
If the SQL fails because the `permissions` rows for `tmpl-11f532fc` count is not exactly 1
(check: `select count(*) from permissions where resource_id='tmpl-11f532fc'` → at planning time `1`), STOP and report.
Warning for later: editing these templates in the Admin UI
may drop the `review_lenses` keys; re-run this SQL if they were edited.

### E5 — COMMIT and DEPLOY review wave

1. Full suite with the "no new failures" check defined in A3. The `comm` output must be empty.
2. COMMIT:
```bash
git add services/routing.py graph/expert.py tests/test_review_wave.py tests/test_routing_review_lenses.py
git commit -m "feat(experts): opt-in complementary review wave on idle expert endpoints

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```
(If the routing test was appended to an existing file instead, add that file instead of `tests/test_routing_review_lenses.py`.)
3. DEPLOY exactly as in C8 steps 3–4 (save log as `pre_E5_deploy.log` first).
4. Live check with arm C template:
```bash
set -a; source benchmarks/.env; set +a
curl -s -m 2400 http://localhost:8002/v1/chat/completions -H "Authorization: Bearer $MOE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"LUMI-G OLMo + SmolLM3 Sovereign Ensemble - Review","no_cache":true,"messages":[{"role":"user","content":"Implement a bounded lock-free MPSC ring buffer in Rust with power-of-two capacity and justify every memory ordering."}]}' > /dev/null
docker logs --since 45m langgraph-orchestrator 2>&1 | grep -E "REVIEW-WAVE|Review wave|Endpoint queue wait|Self-Critique router" | tail -20
```
Expected: one `--- [NODE] REVIEW-WAVE (1 reviewer(s): security) ---` line and an `expert_call ... cat=security` line.
If a gate id is returned, approve it as in C8.
If no REVIEW-WAVE line appears: check that the plan contained a `code_reviewer` task
(`grep "Plan (" ` in the same log window). If the plan had no category with lenses, repeat once with a
different code prompt. If it still does not appear, STOP and report.

### F3 — Arms C and D

Common environment as in F1, then run sequentially (never in parallel):

```bash
MOE_BENCHMARK_ARM="C-review" MOE_BENCHMARK_CONDITIONS="compound_ai" \
MOE_BENCHMARK_TEMPLATE_COMPOUND_AI="LUMI-G OLMo + SmolLM3 Sovereign Ensemble - Review" \
  nohup python3 run_scientific_benchmark.py --fresh > results/arm_C_$(date +%Y%m%d-%H%M%S).log 2>&1 &
```
after it finished: copy report to `results/arm_C_report.json`, save log `arm_C.log`, run phase analysis → `phases_arm_C.json`.

```bash
MOE_BENCHMARK_ARM="D-review-nosc" MOE_BENCHMARK_CONDITIONS="compound_ai" \
MOE_BENCHMARK_TEMPLATE_COMPOUND_AI="LUMI-G OLMo + SmolLM3 Sovereign Ensemble - Review NoSC" \
  nohup python3 run_scientific_benchmark.py --fresh > results/arm_D_$(date +%Y%m%d-%H%M%S).log 2>&1 &
```
after it finished: same with `arm_D`.

Same fallback-rate check as F1 for each arm.

### F4 — Evaluation and decision gates

```bash
cd benchmarks
python3 compare_arms.py results/arm_N_report.json results/arm_A_report.json results/arm_B_report.json \
  results/arm_C_report.json results/arm_D_report.json > results/ab_summary.json
python3 - <<'PY'
import json, statistics
for arm in "ABCD":
    rows = json.load(open(f"results/phases_arm_{arm}.json"))
    phases = {}
    for r in rows:
        for k, v in r["phase_seconds"].items():
            phases.setdefault(k, []).append(v)
    print(arm, {k: round(statistics.mean(v), 1) for k, v in sorted(phases.items())},
          "queue_wait_ms_mean", round(statistics.mean(r["queue_wait_ms"] for r in rows), 0))
PY
```

Write the numbers into a new section "Results" at the end of this runbook (table: arm, n_valid,
judge_mean ± CI, score_mean ± CI, latency_min, score_per_minute, self-critique rounds, mean phase seconds).

Decision gates (apply literally; "better" = higher mean and CI lower bound of the better arm above
the CI upper bound of the other arm; otherwise "no difference"):

- **G1 (keep P0):** P0 stays in any case (bug fixes). If arm B judge_mean is *worse* than arm A with
  non-overlapping CIs, report this to the user as a finding, do not revert.
- **G2 (review wave):**
  - If C is better than B on judge_mean → recommend enabling the review wave for the base template.
  - If D is not worse than C on judge_mean (overlapping CIs or better) **and** D latency_min < C latency_min
    → recommend D configuration (review replaces self-critique).
  - If C is worse than B → recommend not using the review wave; keep code (opt-in, inactive).
  - Recommendations are written to the user; do **not** change production templates yourself.
- **G3 (optional follow-ups):**
  - If the mean `queue_wait_ms` per request in arm C or D exceeds 30000 → report "endpoint hotspot is
    material", propose planner diversity hint (G2 task) to the user.
  - Deliberation (G1 task) is only proposed if the user wants to re-run `compound_ai_debate`.
  - `PLANNER_BUDGET_COMPLEX` stays 4 unless the user asks for a separate arm with 6 or 8.

Record the gate outcome in SessionMesh (`sessionmesh_record_decision`).

---

### G1 — (conditional, only on user request) Delphi round mode for moderated deliberation

Scope: `graph/expert.py`, inside `run_moderated_request`. Env switch `MOE_DELIBERATION_ROUND_MODE`
(`sequential` default, `delphi` = all participants of a round run in parallel and see only previous rounds).

FIND:
```python
            for participant_index, participant in enumerate(list(active_participants), start=1):
                if model_calls >= deliberation_capacity.model_call_budget:
```
REPLACE:
```python
            _delphi = os.getenv("MOE_DELIBERATION_ROUND_MODE", "sequential") == "delphi"
            _round_prefetch: dict[int, dict] = {}
            if _delphi:
                _round_transcript = compact_transcript(turns)
                _budget_left = max(0, deliberation_capacity.model_call_budget - model_calls)
                _round_members = list(enumerate(list(active_participants), start=1))[:_budget_left]

                async def _delphi_turn(p_index: int, p: dict) -> dict:
                    return await run_single(
                        p["model_cfg"],
                        {
                            "id": f"deliberation-r{round_number}-{p_index}",
                            "category": p["category"],
                            "_deliberation_turn": True,
                            "task": build_turn_task(
                                user_query=str(state_.get("input") or ""),
                                plan_summary=plan_summary,
                                role=p["role"],
                                round_number=round_number,
                                transcript=_round_transcript,
                                correction=correction,
                            ),
                        },
                        round_number,
                        p_index,
                    )

                _gathered = await asyncio.gather(
                    *[_delphi_turn(pi, p) for pi, p in _round_members], return_exceptions=True
                )
                for (pi, _), res in zip(_round_members, _gathered):
                    _round_prefetch[pi] = res if isinstance(res, dict) else {"res": f"[delphi ERROR]: {res}", "model_cat": None}
            for participant_index, participant in enumerate(list(active_participants), start=1):
                if model_calls >= deliberation_capacity.model_call_budget:
```

FIND:
```python
                result = await run_single(
                    participant["model_cfg"],
                    role_task,
                    round_number,
                    participant_index,
                )
```
REPLACE:
```python
                if _delphi and participant_index in _round_prefetch:
                    result = _round_prefetch[participant_index]
                else:
                    result = await run_single(
                        participant["model_cfg"],
                        role_task,
                        round_number,
                        participant_index,
                    )
```

Both FIND blocks must occur exactly once. VERIFY: syntax check + `timeout 900 python3 -m pytest -q tests/test_deliberation_runtime.py tests/test_deliberation_capacity.py tests/test_deliberation_admin_contract.py 2>&1 | tail -3`.
Deploy requires adding `MOE_DELIBERATION_ROUND_MODE=delphi` to `.env` → `ASK USER` first; `.env`
changes need `docker compose up -d --no-deps langgraph-app` (recreate), not `restart`.

### G2 — (conditional) Diversity hint in the arm template planner prompt

Only for template `tmpl-smollm3-review` (never the base template), only after `ASK USER`.
Append this text to the template's `planner_prompt` (Admin UI or SQL `jsonb_set` on `{planner_prompt}`):

```text
For complex software or systems tasks, prefer distinct categories for independent subtasks
(e.g. implementation -> code_reviewer, threat and concurrency audit -> security) instead of
repeating the same category. Keep subtasks independent (no depends_on) unless one subtask truly
needs another's result.
```

### H1 — Documentation

1. Translate `docs/experiments/2026-09-18-experten-parallelisierung-bewertung.md` to English into
   `docs/experiments/2026-09-18-expert-parallelization-assessment.md`. Keep all numbers and tables.
   Prefix every factual status statement with one label: **current**, **validated**, **planned** or
   **research**. Add at the top: method, date, sample sizes, comparison, limitations.
2. Delete the German file only after `ASK USER`.
3. Add the "Results" section to this runbook (from F4).
4. `python3 scripts/check_governance.py --check` must pass.

### H2 — Wrap-up

1. Append `done` entry to `agent_status/claude-code.md` with commit hashes, image IDs, arm report paths.
2. `sessionmesh_record_task` with the final state.
3. Report to the user in German: table from F4, gate outcomes, open items.
4. `ASK USER` whether `.env.example` (contains foreign earlier changes plus A5/C7b additions) should be
   committed on the feature branch.
5. `ASK USER` whether to push `feature/parallel-review-wave` and open a merge request on GitLab and/or
   GitHub. Only on explicit approval: push to the feature branch (never `main`).

---

## Execution log (2026-09-18)

- A0–A5, B1–B5, C1–C7b, E1–E2 done and committed on `feature/parallel-review-wave`
  (`34b3a3c3` snapshot, `e9173a89` benchmark harness, `cd781ea8` P0 fixes, `b4f04ab7` review wave).
- E4 done with user approval: templates `tmpl-smollm3-review`, `tmpl-smollm3-review-nosc` and their
  `permissions` rows created; base template `tmpl-11f532fc` untouched.
- F1 arm N started 20:54 UTC. **Arm A was dropped**: the user requested the deploy while arm N was still
  running, so the pre-runbook image no longer serves. Consequence: the baseline for the P0/review-wave
  effect is arm N plus the earlier partial data only; a clean A/B needs a rollback run of the old image
  (`sha256:e0047e24b14b`, kept for rollback) if wanted.
- Measurement caveat for all later arms: they use the fixed judge (reference + rubric); values from
  runs before B1 are not comparable.

### Deploy and live validation (2026-09-18, ~21:15–21:47 UTC)

- Deployed `moe-sovereign-orchestrator:local` built from a clean `git archive` export
  (revision recorded in the image label `org.opencontainers.image.revision`); previous image kept as
  `moe-sovereign-orchestrator:pre-runbook-20260918`. Container healthy, `PLANNER_MAX_TASKS=8` effective.
  `moe-admin` needed no rebuild (design files are bind-mounted, hashes identical).
- Arm N was stopped for the deploy (4 of 24 native evaluations saved in the checkpoint) and is resumed
  without `--fresh`.
- Live request with template `LUMI-G OLMo + SmolLM3 Sovereign Ensemble - Review` (`no_cache`, one Rust
  ring-buffer prompt, 27.7 min wall clock):
  - review wave ran once (`security` reviewing `code_reviewer`, 20 s) and registered conflicts;
  - endpoint queue-wait log lines appeared (34 s on `N02-M60-02`, 46 s on `N02-M60-09`);
  - self-critique stopped after round 1 (`trust gain -0.067 < 0.050`), saving the second round;
  - critic ran and returned a compliant correction; HITL gate approved; response 4.2k chars with code;
  - `routing_telemetry` row written with `wall_clock_ms = 1659682` (previously always 0) and 5 experts.
- **Not verified live:** the quality-probe wiring in `/v1/chat/completions` (sampling rate 5 %, skipped for
  `no_cache`). Static check: all names used in the block are defined in `chat_completions`.
- Defects found during validation and fixed on the branch:
  1. Permissions cache: direct DB insert of permissions is invisible until the Valkey user cache is refreshed
     (documented in E4 above).
  2. `routing_telemetry.planner_plan` was empty in all 1319 historical rows (telemetry read `planner_plan`, the
     commit payload carries `plan`); fixed with a regression test.
  3. `benchmarks/run_scientific_benchmark.py` contained a hard-coded Valkey password (present in published
     `main` since commit `f1d52b43`); now read from `REDIS_PASSWORD` (environment or repository `.env`).
     **The credential is public in git history and must be rotated by the operator.**
