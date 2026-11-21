import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks/orchestration_suite/rlvr"))
import sql_exec_reward as R  # noqa: E402

SETUP = "CREATE TABLE t(id int, g text, x int); INSERT INTO t VALUES (1,'a',10),(2,'a',30),(3,'b',5);"


def run_sqlite(setup, stmts):
    c = sqlite3.connect(":memory:")
    c.executescript(setup)
    last = None
    for s in stmts:
        cur = c.execute(s)
        if cur.description:
            last = cur.fetchall()
    return last


def test_extract_prefers_the_last_fenced_block_and_drops_comments():
    assert R.extract_sql("text\n```sql\nSELECT 1;\n```\nmore\n```sql\n-- c\nSELECT 2;\n```") == "SELECT 2;"
    assert R.extract_sql("SELECT 3") == "SELECT 3"


def test_statements_split_outside_quotes_only():
    assert R.statements("SELECT ';' ; SELECT 2;") == ["SELECT ';'", "SELECT 2"]


def test_only_read_only_statements_run_and_one_bad_statement_voids_the_answer():
    assert R.runnable(["SELECT 1", "WITH a AS (SELECT 1) SELECT * FROM a"]) == ["SELECT 1", "WITH a AS (SELECT 1) SELECT * FROM a"]
    assert R.runnable(["SELECT 1", "DROP TABLE t"]) == [] and R.runnable(["UPDATE t SET x=1"]) == [] and R.runnable([]) == []
    assert R.runnable(["SELECT pg_sleep(10)"]) == []


def reward(answer, ref, tol=0.005):
    return R.reward(answer, SETUP, ref, tol, run_sqlite)


def test_reward_is_binary_and_uses_the_tolerance():
    ok = "```sql\nSELECT ROUND(SUM(x)*0.95*1.07, 2) FROM t WHERE g='a';\n```"
    ref = round(40 * 0.95 * 1.07, 2)
    assert reward(ok, ref) == 1.0
    assert reward("```sql\nSELECT SUM(x) FROM t WHERE g='a';\n```", ref) == 0.0           # right shape, wrong number: no partial credit
    assert reward("```sql\nSELECT SUM(x) FROM t WHERE g='a';\n```", 40.004) == 1.0         # inside tol
    assert reward("```sql\nSELECT 1 FROM nonexistent;\n```", 1) == 0.0                    # execution error
    assert reward("no sql at all, the answer is 40", 40) == 0.0
    assert reward("```sql\nDELETE FROM t;\nSELECT 40;\n```", 40) == 0.0                   # side effects are never run
    assert reward("```sql\nSELECT g FROM t;\n```", 1) == 0.0                              # non-numeric result


def test_last_row_returning_statement_decides():
    assert reward("```sql\nSELECT 1;\nSELECT 40;\n```", 40) == 1.0


def test_grpo_reward_adapter_accepts_chat_completions(monkeypatch):
    monkeypatch.setattr(R, "run_pg", run_sqlite)
    c = [[{"role": "assistant", "content": "```sql\nSELECT 40;\n```"}], "```sql\nSELECT 41;\n```"]
    assert R.grpo_reward(None, c, [SETUP, SETUP], [40.0, 40.0], [0.005, 0.005]) == [1.0, 0.0]


def test_tool_row_strips_the_answer_instruction_and_keeps_the_table_out():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks/orchestration_suite/rlvr"))
    import build_sql_tasks as B
    t = {"id": "x", "lang": "en", "prompts": {"en": "Question?\nState the final result explicitly at the end."}, "setup": {"sql": "CREATE TABLE t(a int);"},
         "final": {"ref": 874.19, "tol": 0.005}}
    row = B.tool_row(t)
    assert row["user"] == "Question?" and row["setup"] == "CREATE TABLE t(a int);" and row["ref"] == 874.19 and "INSERT" not in row["user"]


def test_pass_at_k_estimator_and_summary():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks/orchestration_suite/rlvr"))
    # eval_sql_models imports vllm only inside main(), so the module can be imported here
    import eval_sql_models as E
    assert E.pass_at_k(8, 0, 4) == 0.0 and E.pass_at_k(8, 8, 4) == 1.0 and E.pass_at_k(8, 5, 4) == 1.0
    assert abs(E.pass_at_k(4, 1, 1) - 0.25) < 1e-12 and abs(E.pass_at_k(4, 1, 2) - 0.5) < 1e-12
    s = E.summarize([(4, 1), (4, 4), (4, 0)], [1, 2, 8])
    assert s["tasks"] == 3 and abs(s["mean_sample_acc"] - (0.25 + 1 + 0) / 3) < 1e-4 and "pass@8" not in s and s["pass@2"] > s["pass@1"]
