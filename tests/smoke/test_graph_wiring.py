"""Graph-wiring smoke test — guards the Phase-15 class of regression.

Phase 15 deleted `builder = StateGraph(...)` from main.py; the graph silently
broke and no test caught it. langgraph is stubbed in the test env (see
conftest.py), so we cannot run a real `builder.compile()`. Instead we introspect
the add_node / add_edge / add_conditional_edges calls that main.py records at
import time and assert the wiring is internally consistent:

  * the builder still exists and registers nodes (a missing builder NameErrors
    at import, which `import main` below would surface);
  * every edge source/target (plain, fan-out list, and conditional mapping) is a
    registered node or the END sentinel — catches dead edges / renamed nodes;
  * the entry point is a real node;
  * the expected core nodes are all present.
"""

import main

# END is the same stubbed sentinel object main.py imported from langgraph.graph.
_END = main.END

_EXPECTED_CORE_NODES = {
    "cache", "semantic_router", "knowledge_broker", "planner", "fuzzy_router", "workers",
    "research", "math", "mcp", "graph_rag", "research_fallback",
    "thinking", "merger", "resolve_conflicts", "critic",
    "precision_preflight", "precision_mcp", "precision_renderer",
    "precision_slots", "precision_bind", "quality_gate",
    "response_commit",
}


def _node_names() -> set[str]:
    return {c.args[0] for c in main.builder.add_node.call_args_list if c.args}


def test_builder_is_defined_and_wired():
    assert main.builder is not None
    assert main.builder.add_node.call_args_list, "no nodes registered on builder"


def test_core_nodes_present():
    missing = _EXPECTED_CORE_NODES - _node_names()
    assert not missing, f"graph nodes removed from main.py: {sorted(missing)}"


def test_entry_point_is_a_registered_node():
    nodes = _node_names()
    entries = [c.args[0] for c in main.builder.set_entry_point.call_args_list if c.args]
    assert entries, "no entry point set on the builder"
    for e in entries:
        assert e in nodes, f"entry point {e!r} is not a registered node"


def test_plain_edge_endpoints_are_nodes():
    nodes = _node_names()
    for c in main.builder.add_edge.call_args_list:
        if len(c.args) < 2:
            continue
        src, dst = c.args[0], c.args[1]
        for s in (src if isinstance(src, list) else [src]):
            assert s in nodes, f"edge source {s!r} is not a registered node"
        assert dst in nodes or dst is _END, f"edge target {dst!r} is not a node/END"


def test_conditional_edge_targets_are_nodes():
    nodes = _node_names()
    for c in main.builder.add_conditional_edges.call_args_list:
        assert c.args, "conditional edge call without arguments"
        src = c.args[0]
        assert src in nodes, f"conditional edge source {src!r} is not a registered node"
        mapping = next((a for a in c.args if isinstance(a, dict)), None)
        assert mapping, f"conditional edge from {src!r} has no routing map"
        for target in mapping.values():
            assert target in nodes or target is _END, \
                f"conditional routing target {target!r} (from {src!r}) is not a node/END"


def test_knowledge_broker_sits_between_semantic_router_and_planner():
    edges = {(src, c.args[1]) for c in main.builder.add_edge.call_args_list if len(c.args) >= 2
             for src in (c.args[0] if isinstance(c.args[0], list) else [c.args[0]])}
    assert ("semantic_router", "knowledge_broker") in edges and ("knowledge_broker", "planner") in edges
    assert ("semantic_router", "planner") not in edges  # the old direct edge is gone
