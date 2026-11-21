"""Binary execution reward for the SQL-tool experiment (pre-registration E): 1 if the model's SQL, run on the task's table, returns the
reference number within the task tolerance, else 0. No partial credit (simple execution rewards are the documented preference; partial credit
invites degenerate behaviour).

The model answers with one ```sql block (or bare SQL). Only SELECT/WITH statements run, inside a read-only session with a statement timeout; the
last statement that returns rows decides, its first cell is compared. The database layer is injected (`run`): PostgreSQL from psycopg in the LUMI
jobs (PGSOCK socket folder, one database per rank, see connect_pg), sqlite3 in the tests.
"""
import json
import os
import re
from typing import Callable, Optional

FENCE = re.compile(r"```(?:sql|postgresql|postgres|pgsql)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
READ_ONLY = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
FORBIDDEN = re.compile(r"\b(pg_sleep|pg_read_file|lo_import|copy|dblink|pg_terminate_backend|set\s+session|alter|drop|create|insert|update|delete|truncate)\b", re.IGNORECASE)


def extract_sql(answer: str) -> str:
    """The SQL of an answer: the last fenced sql block, else the whole text without comment lines."""
    blocks = FENCE.findall(answer or "")
    code = blocks[-1] if blocks else (answer or "")
    return re.sub(r"(?m)^\s*--.*$", "", code).strip()


def statements(sql: str) -> list:
    """Split on semicolons that are not inside single quotes."""
    out, cur, quoted = [], [], False
    for ch in sql:
        if ch == "'":
            quoted = not quoted
        if ch == ";" and not quoted:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur).strip())
    return [s for s in out if s]


def runnable(stmts: list) -> list:
    """Only read-only statements; anything else makes the whole answer unrunnable (reward 0), not silently skipped."""
    if not stmts or any(not READ_ONLY.match(s) or FORBIDDEN.search(s) for s in stmts):
        return []
    return stmts


def scalar(rows) -> Optional[float]:
    try:
        v = rows[0][0]
        return float(v)
    except (TypeError, ValueError, IndexError):
        return None


def reward(answer: str, setup_sql: str, ref: float, tol: float, run: Callable) -> float:
    """`run(setup_sql, [statements]) -> rows of the last row-returning statement or None`; raises are rewards of 0."""
    stmts = runnable(statements(extract_sql(answer)))
    if not stmts:
        return 0.0
    try:
        got = scalar(run(setup_sql, stmts))
    except Exception:  # noqa: BLE001 - a failing query is a wrong answer
        return 0.0
    return 1.0 if got is not None and abs(got - float(ref)) <= float(tol) + 1e-9 else 0.0


# ---- PostgreSQL layer (LUMI jobs) -------------------------------------------------------------------------------------------
_state: dict = {}


def connect_pg():
    import psycopg
    sock = os.environ.get("PGSOCK")
    if not sock:
        return psycopg.connect("host=127.0.0.1 port=15433 user=postgres password=ht dbname=postgres", autocommit=True)
    db = "r" + os.environ.get("RANK", os.environ.get("LOCAL_RANK", "0"))
    with psycopg.connect(f"host={sock} user=postgres dbname=postgres", autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_database WHERE datname=%s", (db,)).fetchone():
            c.execute(f"CREATE DATABASE {db}")
    return psycopg.connect(f"host={sock} user=postgres dbname={db}", autocommit=True)


def run_pg(setup_sql: str, stmts: list):
    """Load the task's table once per connection and setup (cached by the setup text), then run the statements read-only."""
    c = _state.get("c")
    if c is None or c.closed:
        c = _state["c"] = connect_pg()
        _state["setup"] = None
    if _state.get("setup") != setup_sql:
        c.execute("SET default_transaction_read_only = off")
        c.execute("DROP SCHEMA IF EXISTS public CASCADE")
        c.execute("CREATE SCHEMA public")
        for st in statements(setup_sql):
            c.execute(st)
        _state["setup"] = setup_sql
    c.execute("SET default_transaction_read_only = on")
    c.execute("SET statement_timeout = '3s'")
    last = None
    for st in stmts:
        try:
            cur = c.execute(st)
            if cur.description:
                last = cur.fetchall()
        except Exception:
            try:
                c.execute("ROLLBACK")
            except Exception:  # noqa: BLE001
                pass
            return None
    return last


def grpo_reward(prompts, completions, setup, ref, tol, **kw):
    """TRL reward function: columns `setup`, `ref`, `tol` of the dataset."""
    out = []
    for c, s, r, t in zip(completions, setup, ref, tol):
        text = c[0]["content"] if isinstance(c, list) else c
        out.append(reward(text, s, r, t, run_pg))
    return out


grpo_reward.__name__ = "sql_exec_exact"
