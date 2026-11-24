"""The template editor must not overwrite policy fields it does not display."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EDITOR = ROOT / "admin_ui/templates/expert_templates.html"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _editor_functions() -> str:
    source = EDITOR.read_text(encoding="utf-8")
    start = source.index("// Debate-analysis policy as loaded")
    end = source.index("async function createTemplate()")
    block = source[start:end]
    # Keep only the helper definitions, not surrounding comments' section markers.
    return re.sub(r"// ───.*", "", block)


def _run(script: str) -> dict:
    js = f"""
const elements = {{}};
const document = {{ addEventListener: () => {{}}, getElementById: (id) => elements[id] || (elements[id] = {{ value: '' }}) }};
{_editor_functions()}
{script}
"""
    result = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


STORED = {
    "schema_version": "1.0", "activation": "adaptive", "mode": "moderated",
    "min_agents": 3, "initial_agent_cap": 6, "reserve_agents": 2, "absolute_max_agents": 8,
    "min_rounds": 2, "initial_round_cap": 3, "reserve_rounds": 2, "absolute_max_rounds": 5,
    "max_model_calls": 18, "max_turn_tokens": 1024, "moderator_interval": 2,
    "estimated_turn_seconds": 45.5, "synthesis_reserve_seconds": 90.0,
    "convergence_threshold": 0.9, "repetition_threshold": 0.6, "fallback": "fail",
}


def test_hidden_deliberation_fields_survive_load_and_save():
    out = _run(f"""
setDeliberationControls('ct', {json.dumps(STORED)}, false);
console.log(JSON.stringify(collectDeliberationPolicy('ct')));
""")
    assert out == STORED


def test_visible_field_edit_is_applied_and_hidden_fields_kept():
    out = _run(f"""
setDeliberationControls('ct', {json.dumps(STORED)}, false);
elements['ct_deliberation_max_calls'].value = '30';
console.log(JSON.stringify(collectDeliberationPolicy('ct')));
""")
    assert out["max_model_calls"] == 30
    assert out["max_turn_tokens"] == 1024 and out["convergence_threshold"] == 0.9


def test_new_template_still_gets_defaults():
    out = _run("""
setDeliberationControls('ct', null, false);
console.log(JSON.stringify(collectDeliberationPolicy('ct')));
""")
    assert out["min_agents"] == 2 and out["convergence_threshold"] == 0.82
    assert out["activation"] == "disabled"


def test_prefixes_do_not_leak_into_each_other():
    out = _run(f"""
setDeliberationControls('ct', {json.dumps(STORED)}, false);
setDeliberationControls('et', null, false);
console.log(JSON.stringify(collectDeliberationPolicy('et')));
""")
    assert out["min_agents"] == 2 and out["min_rounds"] == 1 and "max_turn_tokens" not in out


def test_debate_analysis_hidden_fields_survive():
    stored = {"schema_version": "1.0", "activation": "disabled", "judge_count": 3,
              "samples_per_judge": 2, "max_model_calls": 90, "research_enabled": False}
    out = _run(f"""
setDebateAnalysisControls('ct', {json.dumps(stored)});
elements['ct_debate_analysis_activation'].value = 'enabled';
console.log(JSON.stringify(collectDebateAnalysis('ct')));
""")
    assert out["activation"] == "enabled"
    assert out["judge_count"] == 3 and out["research_enabled"] is False
