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
TASKS = gen.generate(30, seed=3)


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
