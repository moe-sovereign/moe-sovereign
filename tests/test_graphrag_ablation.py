"""GraphRAG ablation: task arms are paired and leak-free, graph facts fit the product schema, analysis counts retrieval."""
import importlib.util
import sys
from pathlib import Path

_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "orchestration_suite" / "graphrag"
sys.path.insert(0, str(_DIR))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gen, ana = _load("generate_lookup"), _load("analyze")
TASKS = gen.generate(30, seed=3, kinds=("sum", "hop"))
SIMPLE = gen.generate(30, seed=3)


def test_each_base_task_has_all_arms_with_the_same_answer():
    by_base = {}
    for t in TASKS:
        by_base.setdefault(t["base"], {})[t["arm"]] = t
    assert len(by_base) == 30
    for arms in by_base.values():
        assert set(arms) == set(gen.ARMS)
        assert len({a["final"]["ref"] for a in arms.values()}) == 1


def test_only_the_inline_arm_carries_data_and_arm_entities_are_disjoint():
    for t in TASKS:
        prompt = t["prompts"]["en"]
        assert ("Data for this request" in prompt) == (t["arm"] == "inline")
    names = [e for t in TASKS if t["arm"] != "empty" for e in t["entities"]]
    assert len(names) == len(set(names))


def test_graph_facts_use_the_product_schema_and_reproduce_the_answer():
    for t in TASKS:
        if t["arm"] == "graph":
            facts = t["graph"]
            vals = {r["s"]: float(r["o"].split()[0]) for r in facts["relations"] if r["r"].startswith("HAS_")}
            assert all(r["r"].isupper() for r in facts["relations"])
            ref = t["final"]["ref"]
            pct = int(t["prompts"]["en"].split("by ")[1].split(" %")[0]) if "increased by" in t["prompts"]["en"] else None
            if t["family"] == "lookup_sum":
                assert abs(sum(vals.values()) * (1 + pct / 100) - ref) < 0.01
        if t["arm"] in ("empty", "inline"):
            assert t["graph"] is None
        if t["arm"] == "noisy":
            assert any("(legacy record)" in e["name"] for e in t["graph"]["entities"])


def test_entity_names_survive_the_product_term_extraction():
    import re
    caps = re.compile(r"\b[A-ZÄÖÜ][a-zäöüßA-ZÄÖÜ0-9+#]{2,}\b")
    for t in TASKS[:40]:
        found = set(caps.findall(t["prompts"]["en"]))
        named = t["entities"] if t["family"] == "lookup_sum" else t["entities"][:1]  # a hop task names only the hub
        assert set(named) <= found, (named, found)


def test_analysis_reports_retrieval_and_paired_difference():
    tasks = {t["id"]: t for t in TASKS}
    recs = []
    for t in TASKS:
        ok = t["arm"] in ("graph", "inline")
        ctx = " ".join(t["entities"]) if t["arm"] in ("graph", "noisy") else ""
        recs.append({"task_id": t["id"], "base": {"correct": ok, "outcome": "answer" if ok else "blocked:x"}, "captured": {"graph_context": ctx}})
    res = ana.analyze(recs, tasks, boot=200)
    assert res["arms"]["graph"]["correct"] == 1.0 and res["arms"]["empty"]["correct"] == 0.0
    assert res["arms"]["graph"]["retrieved_share"] == 1.0 and res["arms"]["empty"]["retrieved_share"] == 0.0
    assert res["paired"]["graph-empty"]["mean_diff"] == 1.0


def test_arithmetic_free_kinds_ask_for_one_stored_number():
    kinds = {t["family"] for t in SIMPLE}
    assert kinds <= {"lookup_val", "lookup_hop1"} and len(kinds) == 2
    for t in SIMPLE:
        prompt = t["prompts"]["en"]
        assert "%" not in prompt and "increased" not in prompt
        if t["arm"] == "graph":
            vals = {r["s"]: float(r["o"].split()[0]) for r in t["graph"]["relations"] if r["r"].startswith("HAS_")}
            target = t["entities"][1] if t["family"] == "lookup_hop1" else t["entities"][0]
            assert vals[target] == t["final"]["ref"]
        if t["arm"] == "noisy":  # the stale duplicate belongs to the entity that carries the answer
            assert any("(legacy record)" in e["name"] for e in t["graph"]["entities"])


def test_answer_in_context_checks_the_channel():
    t = next(x for x in SIMPLE if x["arm"] == "graph")
    ref = int(t["final"]["ref"])
    assert ana.holds_answer({"captured": {"graph_context": f"• X (Plant): HAS_YIELD {ref} kWh"}}, t)
    assert not ana.holds_answer({"captured": {"graph_context": ""}}, t)


# ---- knowledge-conflict arms, loader provenance, gate-free analysis, run comparison ---------------------------------------
cmp_runs, loader = _load("compare_runs"), _load("load_graph_facts")
CONFLICTS = gen.generate_conflicts(40, seed=31)


def _by_arm(tasks):
    out = {}
    for t in tasks:
        out.setdefault(t["base"], {})[t["arm"]] = t
    return out


def test_conflict_arms_are_complete_deterministic_and_leak_free():
    assert CONFLICTS == gen.generate_conflicts(40, seed=31)
    for arms in _by_arm(CONFLICTS).values():
        assert set(arms) == set(gen.CONFLICT_ARMS)
        assert {a["expected_rule"] for a in arms.values()} == {"recency", "authority", "unresolved", "llm"}
    names = [e for t in CONFLICTS for e in t["entities"]]
    assert len(names) == len(set(names))  # arms never share graph nodes


def test_stale_fresh_expects_the_newer_value_and_verified_old_the_older_one():
    for arms in _by_arm(CONFLICTS).values():
        sf, vo = arms["stale_fresh"], arms["verified_old"]
        rels = sorted(sf["graph"]["relations"], key=lambda r: r["valid_from"])
        assert rels[0]["valid_from"] < rels[1]["valid_from"] and sf["final"]["ref"] == float(rels[1]["o"].split()[0])
        old_edge, new_edge = sorted(vo["graph"]["relations"], key=lambda r: r["valid_from"])
        assert old_edge["verified"] is True and old_edge["source"] == "fixture" and new_edge["verified"] is False
        assert vo["final"]["ref"] == float(old_edge["o"].split()[0])


def test_conflict_tie_has_equal_provenance_and_asks_for_both_values():
    for arms in _by_arm(CONFLICTS).values():
        tie = arms["conflict_tie"]
        e1, e2 = tie["graph"]["relations"]
        assert (e1["valid_from"], e1["source"], e1["verified"]) == (e2["valid_from"], e2["source"], e2["verified"])
        assert tie["final"] == {"kind": "text", "answers": [e1["o"].split()[0], e2["o"].split()[0]], "all": True}


def test_llm_only_has_no_graph_and_a_public_answer():
    for arms in _by_arm(CONFLICTS).values():
        t = arms["llm_only"]
        assert t["graph"] is None and t["entities"] == [] and t["final"]["kind"] == "text" and t["final"]["answers"]


def test_loader_keeps_per_relation_provenance_and_defaults_to_fixture():
    rows = loader.relation_rows([{"s": "a", "o": "b", "conf": 0.9},
                                 {"s": "a", "o": "c", "conf": 0.9, "source": "extracted", "verified": False, "valid_from": 5, "created": 4}])
    assert rows[0] == {"s": "a", "o": "b", "conf": 0.9, "src": "fixture", "ver": True, "vf": None, "cr": None}
    assert rows[1]["src"] == "extracted" and rows[1]["ver"] is False and (rows[1]["vf"], rows[1]["cr"]) == (5, 4)


def _rec(task, outcome, correct, draft_correct, seconds=1.0, **captured):
    return {"task_id": task["id"], "base": {"outcome": outcome, "correct": correct, "draft_correct": draft_correct, "seconds": seconds},
            "captured": captured}


def test_effective_correctness_sees_through_the_gate():
    assert ana.effective_correct({"outcome": "blocked:trust_score_block", "correct": False, "draft_correct": True}) is True
    assert ana.effective_correct({"outcome": "hitl", "correct": True, "draft_correct": False}) is True
    assert ana.effective_correct({"outcome": "answer", "correct": False, "draft_correct": True}) is False


def test_analysis_handles_text_tasks_and_reports_conflicts_and_latency():
    tasks = {t["id"]: t for t in CONFLICTS}
    recs = [_rec(t, "blocked:trust_score_block", False, True, seconds=2.0,
                 knowledge_bundle={"conflicts": [{"rule": "recency"}]} if t["arm"] == "stale_fresh" else {})
            for t in CONFLICTS]
    arms = ana.analyze(recs, tasks)["arms"]
    assert arms["stale_fresh"]["correct"] == 0.0 and arms["stale_fresh"]["effective_correct"] == 1.0  # the gate hid it
    assert arms["stale_fresh"]["conflicts_per_episode"] == 1.0 and arms["stale_fresh"]["conflict_rules"] == {"recency": 40}
    assert arms["llm_only"]["broker_bundle_share"] == 0.0 and arms["llm_only"]["seconds_p95"] == 2.0


def test_compare_is_paired_gate_free_and_reports_the_latency_shift():
    tasks = {t["id"]: t for t in CONFLICTS if t["arm"] == "stale_fresh"}
    off = [_rec(t, "blocked:x", False, False, seconds=1.0) for t in tasks.values()]
    on = [_rec(t, "answer", True, True, seconds=2.5) for t in tasks.values()]
    res = cmp_runs.compare(off, on, tasks)
    assert res["groups"]["ALL"]["diff"] == 1.0 and res["groups"]["ALL"]["a"] == 0.0
    assert res["seconds"]["a_p95"] == 1.0 and res["seconds"]["b_p95"] == 2.5


def test_p0_gate_needs_all_three_criteria():
    def mk(diff, a95, b95):
        return {"groups": {"ALL": {"diff": diff}}, "seconds": {"a_p95": a95, "b_p95": b95}}
    diluted = {"groups": {"ALL": {"diff": 0.3}, "graph": {"diff": 0.6}, "noisy": {"diff": 0.5}, "inline": {"diff": 0.0}, "empty": {"diff": 0.0}},
               "seconds": {"a_p95": 1, "b_p95": 1}}
    assert cmp_runs.p0_gate(diluted, mk(0.0, 10.0, 12.0))["checks"]["lookup_gain"]["value"] == 0.55  # graph + noisy arms only
    assert cmp_runs.p0_gate(mk(0.5, 1, 1), mk(0.0, 10.0, 12.0))["pass"] is True
    assert cmp_runs.p0_gate(mk(0.1, 1, 1), mk(0.0, 10.0, 12.0))["pass"] is False   # too little gain on lookups
    assert cmp_runs.p0_gate(mk(0.5, 1, 1), mk(-0.05, 10.0, 12.0))["pass"] is False  # held-out regression
    assert cmp_runs.p0_gate(mk(0.5, 1, 1), mk(0.0, 10.0, 14.0))["pass"] is False    # +4 s at p95
