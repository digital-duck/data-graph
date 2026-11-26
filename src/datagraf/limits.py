"""The limits every query data-graph runs is held to: a model's SQL, a benchmark's, and a semantic model's own expressions.

Every query goes through dd-db's `run_select`, which fetches at most `max_rows` rows (so a huge result never fills memory),
stops the query after `QUERY_TIMEOUT_S`, and stops it when free space where the database writes temporary files (SQLite:
$SQLITE_TMPDIR, $TMPDIR, /var/tmp or /tmp, the folder `df` would show) falls below `MIN_FREE_BYTES`. A stopped query raises
`dd_db.QueryLimitExceeded`, and its message is what a model is shown for a repair.
"""
from __future__ import annotations

from dd_db import BaseDB, QueryLimitExceeded, SelectResult

QUERY_TIMEOUT_S = 30.0          # one query; the ask check step (timeout 120 s) runs the query and, if it has more rows, a count (COUNT_TIMEOUT_S)
COUNT_TIMEOUT_S = 5.0           # counting the rows of a result longer than row_limit: a nicety, so a short budget
MIN_FREE_BYTES = 1 * 2**30      # 1 GiB left free for the rest of the machine
BENCH_MAX_ROWS = 10_000         # a benchmark answer (or reference) with more rows than this is an error, not a comparison

__all__ = ["QUERY_TIMEOUT_S", "COUNT_TIMEOUT_S", "MIN_FREE_BYTES", "BENCH_MAX_ROWS", "QueryLimitExceeded", "select", "limit_hint"]


def select(db: BaseDB, sql: str, max_rows: int, timeout_s: float | None = None) -> SelectResult:
    """`db.run_select` with data-graph's time and disk limits (read when called, so a test can lower them)."""
    return db.run_select(sql, max_rows=max_rows, timeout_s=QUERY_TIMEOUT_S if timeout_s is None else timeout_s,
                         min_free_bytes=MIN_FREE_BYTES)


def limit_hint(e: QueryLimitExceeded) -> str:
    """A stopped query, in words a model can act on."""
    if e.kind == "timeout":
        return (f"{e}. It may never finish: give every recursive CTE a stop condition (a WHERE on its recursive part, "
                "e.g. WHERE n < 100), give every join an ON condition, and aggregate instead of listing millions of rows")
    return f"{e}. The query writes too much temporary data: aggregate or filter earlier"
