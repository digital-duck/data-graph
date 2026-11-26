"""Queries run bounded (rows, time, free disk), recursive CTEs need a stop condition, and the benchmark compares rows as rows."""
from __future__ import annotations

import time

import pytest
import sqlglot

import datagraf as dg
from datagraf import ask as A
from datagraf import bench as B
from datagraf import limits
from datagraf.run import recipe_path
from datagraf.sqlite import connect, profile

from test_recipes import Scripted, ask

ENDLESS = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT x FROM c"
# has a WHERE, so it passes the static check, but the condition never becomes false: only the time limit stops it
NEVER_FALSE = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x > 0) SELECT COUNT(*) AS n FROM c"


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(limits, "QUERY_TIMEOUT_S", 0.5)


# ── the static check ─────────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("sql,endless", [
    (ENDLESS, ["c"]),
    ("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 10) SELECT x FROM c", []),
    ("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c LIMIT 10) SELECT x FROM c", []),
    ("WITH c(x) AS (SELECT 1) SELECT x FROM c", []),
    ("WITH RECURSIVE a(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM a WHERE x < 3), "
     "b(y) AS (SELECT 1 UNION ALL SELECT y + 1 FROM b) SELECT * FROM a, b", ["b"]),
    ("WITH RECURSIVE c(x) AS (SELECT 1 UNION SELECT 2 UNION ALL SELECT x + 1 FROM c) SELECT x FROM c", ["c"]),
])
def test_unbounded_recursion(sql, endless):
    assert A.unbounded_recursion(sqlglot.parse_one(sql, read="sqlite")) == endless


def test_an_endless_cte_is_refused_before_it_runs(shop_model, shop_db):
    t0 = time.monotonic()
    r = A.check_sql(ENDLESS, dg.load(shop_model), str(shop_db))
    assert not r["ok"] and "has no stop condition" in r["error"] and "WHERE" in r["error"]
    assert time.monotonic() - t0 < 2


# ── the limits at run time ───────────────────────────────────────────────────────────────────────────────────────────

def test_a_query_that_never_ends_is_stopped_with_a_hint(shop_model, shop_db, quick):
    t0 = time.monotonic()
    r = A.check_sql(NEVER_FALSE, dg.load(shop_model), str(shop_db))
    assert not r["ok"] and "the query was stopped" in r["error"] and "stop condition" in r["error"]
    assert time.monotonic() - t0 < 5


def test_only_row_limit_rows_are_fetched_and_the_rest_counted(shop_model, shop_db):
    sql = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c WHERE x < 5000) SELECT x FROM c"
    r = A.check_sql(sql, dg.load(shop_model), str(shop_db), row_limit=3)
    assert r["ok"] and r["rows"] == [[1], [2], [3]] and r["row_count"] == 5000 and r["more"]
    assert "5000 rows" in A.report("q", r, 1)["text"]


def test_a_count_that_is_too_slow_is_reported_as_more(shop_model, shop_db, monkeypatch):
    real = limits.select

    def slow_count(db, sql, max_rows, timeout_s=None):
        if sql.startswith("SELECT COUNT(*) FROM ("):
            raise limits.QueryLimitExceeded("timeout", "query stopped after 30 s (its time limit)")
        return real(db, sql, max_rows, timeout_s)

    monkeypatch.setattr(A, "select", slow_count)
    r = A.check_sql("SELECT id FROM customers", dg.load(shop_model), str(shop_db), row_limit=1)
    assert r["ok"] and r["row_count"] is None
    text = A.report("q", r, 1)["text"]
    assert "more than 1 rows" in text and "too many to count" in text


def test_too_little_free_disk_stops_the_query(shop_model, shop_db, monkeypatch):
    monkeypatch.setattr(limits, "MIN_FREE_BYTES", 2**62)
    r = A.check_sql("SELECT COUNT(*) FROM customers", dg.load(shop_model), str(shop_db))
    assert not r["ok"] and "GiB free" in r["error"]


def test_profile_reports_an_endless_metric_instead_of_hanging(shop_model, shop_db, quick):
    m = dg.load(shop_model)
    m.datasets[0].metrics[0].sql = f"(SELECT COUNT(*) FROM ({NEVER_FALSE}))"
    t0 = time.monotonic()
    prof = profile(m, shop_db)
    err = prof["datasets"][0]["metrics"][0].get("error", "")
    assert "stopped" in err and time.monotonic() - t0 < 10


# ── the prompts ──────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_prompts_ask_for_a_query_that_finishes():
    folder = recipe_path("ask").parent / "prompts"
    sql, repair = (folder / "sql.md").read_text(), (folder / "repair.md").read_text()
    assert "WITH RECURSIVE" in sql and "stop condition" in sql and "JOIN ... ON" in sql
    assert "stopped" in repair and "recursive CTE" in repair


def test_an_endless_first_answer_is_repaired(shop_model, shop_db):
    llm = Scripted(ENDLESS, "SELECT COUNT(*) AS n FROM customers")
    run = ask(shop_model, shop_db, "how many customers", llm)
    assert (run.outputs["status"], run.llm_calls) == ("checked", 2)
    assert "has no stop condition" in llm.prompts[1]


# ── the benchmark ────────────────────────────────────────────────────────────────────────────────────────────────────

GOLD = [("Rock", 10), ("Jazz", 5)]


@pytest.mark.parametrize("pred,ok", [
    ([("Rock", 10), ("Jazz", 5)], True),
    ([("Jazz", 5), ("Rock", 10)], True),                          # any row order
    ([(10, "Rock", "x"), (5, "Jazz", "y")], True),                # extra columns, any column order
    ([("Rock", 5), ("Jazz", 10)], False),                         # the values swapped between rows
    ([("Rock", 10), ("Rock", 5)], False),
    ([("Rock", 10)], False),
])
def test_rows_are_compared_as_rows(pred, ok):
    assert B.matches({"check": "rows"}, GOLD, "", pred) is ok


def test_text_may_be_longer_within_the_same_row():
    gold = [("adams", 7), ("harris", 7), ("smith", 3)]
    assert B.matches({"check": "rows"}, gold, "", [("Andrew Adams", 7), ("Frank Harris", 7), ("Jo Smith", 3)])
    assert not B.matches({"check": "rows"}, gold, "", [("Andrew Adams", 3), ("Frank Harris", 7), ("Jo Smith", 7)])


def test_duplicate_rows_count():
    assert B.matches({"check": "rows"}, [(1,), (1,), (2,)], "", [(1,), (2,), (1,)])
    assert not B.matches({"check": "rows"}, [(1,), (1,), (2,)], "", [(1,), (2,), (2,)])


def test_superset_and_within_are_row_wise():
    gold = [("Rock", 10), ("Jazz", 5)]
    assert B.matches({"check": "superset"}, gold, "", [("Rock", 10), ("Jazz", 5), ("Pop", 1)])
    assert not B.matches({"check": "superset"}, gold, "", [("Rock", 5), ("Jazz", 10), ("Pop", 1)])
    assert B.matches({"check": "within"}, gold, "", [("Rock", 10)])
    assert not B.matches({"check": "within"}, gold, "", [("Rock", 5)])
    assert not B.matches({"check": "within"}, gold, "", [])


def test_bench_run_sql_is_bounded(shop_db, quick):
    rows, err = B.run_sql(connect(shop_db), ENDLESS, max_rows=100)
    assert rows is None and "more than 100 rows" in err
    rows, err = B.run_sql(connect(shop_db), NEVER_FALSE)
    assert rows is None and "stopped" in err
