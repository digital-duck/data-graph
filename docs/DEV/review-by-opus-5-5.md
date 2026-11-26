# data-graph code review (Opus 5.5)

Date: 2026-11-25 · Commit reviewed: `43fb837` (main, clean) · Scope: `src/datagraf/` (1,669 lines), the two recipes, the tests.

**How I checked it.**

- I read `sqlite.py`, `ask.py`, `draft.py`, `bench.py`, `steps.py`, the `ask` recipe's timeouts, and the CLI's commands.
- **Setup first:** data-graph was not installed in the shared `graf` environment, so its tests could not be collected
  (`ModuleNotFoundError: datagraf`, and `sqlglot` was missing). I installed it with `pip install -e ".[dev]"`, as CLAUDE.md says.
- `pytest`: **78 passed** (37.6 s); `dgr validate` on the TPC-DS fixture: 0 errors.
- Every bug marked **confirmed** was reproduced against a real SQLite file, made with data-graph's own `draft()`.

## 1. Summary

data-graph is compact and principled:

- databases are opened read-only twice over (`mode=ro` and `PRAGMA query_only`);
- identifiers are always quoted, and values never interpolated;
- the model's SQL must be a single SELECT whose every table and column exists (sqlglot `qualify` against the live catalogue);
- the answer's status (`checked` or `failed`) is decided by a program, never by the model.

That is the right design for text-to-SQL. The main gap is **resource bounds**: the model's SQL is executed unbounded, in time and in
memory (B1). Second, the benchmark's `rows` check is lenient enough to pass a wrong answer (B2). That matters, because benchmark
results are saved in `benchmarks/chinook/results/`.

## 2. Strengths

| Area | What is good |
|---|---|
| Read-only by construction | `sqlite.connect` is dd-db's `SQLiteDB(path, read_only=True)` (`sqlite.py:22-27`); `ask.check_sql` additionally refuses anything but one `exp.Query` (`ask.py:159-163`). Writes are impossible two ways over. |
| SQL checked before it runs | Unknown tables are reported with the list of real ones; `qualify(..., validate_qualify_columns=True)` rejects unknown columns, with a message the model can act on (`ask.py:166-174`). |
| Joins against the model | Joins that are not declared relationships become warnings, not silent acceptance (`ask.py:196-213`). |
| Status decided by a program | `report()` sets `checked` or `failed`; the model never claims success (`ask.py:224-243`). |
| Context by join paths | `pick_datasets` narrows the model by names, then adds the datasets on the join paths between them, with the reasoning written down (vector similarity misses "invoice_items" for "best-selling albums"). |
| Draft cannot drift | `draft.check` requires datasets, sources, fields, expressions, keys and relationships to stay exactly as the catalogue says (`draft.py:84-103`); the write is policy-checked and re-checked (`write_root_ok`). |
| Fixtures | Verbatim Apache Ossie copies with a NOTICE, and a rule not to edit them. |

## 3. Bugs

### B1 (High, confirmed; FIXED 2026-11-25): the model's SQL runs with no time or memory bound

`check_sql` runs the model's statement with `db.run_query(sql)` (`ask.py:216`), which fetches **every** row into a pandas DataFrame.
Only afterwards does it take `head(row_limit)`. Nothing limits how long the query runs, or how many rows it returns, and it passes
every check: one statement, a SELECT, and the only table it reads is its own CTE.

```
WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT x FROM c
```

- **Measured:** after 20 s the process held **3.5 GB** (`/usr/bin/time -v`). It cannot be interrupted from Python, because a SIGALRM
  handler never ran while SQLite was in C; only killing the process stopped it.
- **In the recipe:** the `ask_check` step's 120 s timeout kills it, but at that rate memory reaches roughly 20 GB first, and a run that
  times out gets no repair round.
- **Elsewhere:** the same path runs in `dgr bench` (`bench.run_sql`, `bench.py:98-105`), which has no step timeout at all. And
  `draft.check` runs every metric the model wrote through `profile()` (`SELECT {m.sql} FROM …`, `sqlite.py:130`).

A model does not need to be malicious to hit this. An accidental cross join, or a missing join condition on large tables, does the
same.

**Fix, in dd-db or at the call:**
- fetch with a cursor and `fetchmany(row_limit + 1)`, to know there are more rows without materializing them; count separately, or
  report "more than N";
- set SQLite's `progress_handler` to abort after a time budget (it works where SIGALRM does not);
- for other dd-db adapters, use a statement timeout. Since dd-db is shared, a `run_query(sql, max_rows=, timeout_s=)` there fixes
  `ask`, `bench`, `draft` and `profile` together.

### B2 (Medium, confirmed; FIXED 2026-11-25): the benchmark's `rows` check passes an answer with wrong pairings

`bench.matches` with `check: rows` (`bench.py:74-80`) requires the same row count, and that every reference column is covered by
*some* answer column, **each column on its own**. The pairing between columns is never checked. In the same way, `_covers` matches
text by substring (`bench.py:52`).

```
reference [("Rock", 10), ("Jazz", 5)]
answer    [("Rock", 5), ("Jazz", 10)]      -> passes          (the values are swapped between rows)
answer    [("Rock and Roll", 10), ...]      -> passes          ("rock" is a substring)
```

The questions file documents column-by-column matching ("every column of the reference matches some column of the answer … any row
order"). But column independence is not what a reader of a score expects: a wrong answer counts as correct. The saved results in
`benchmarks/chinook/results/` (2026-11-22) were scored this way.

**Fix:** match rows, not columns. Find a mapping from reference columns to answer columns (each reference column to a distinct answer
column whose values cover it). Then require the multiset of reference rows, projected through that mapping, to equal the answer's
projected rows (or, for `superset`, be contained in them). Keep substring matching only where a question opts in. Then re-score the
saved results.

### B3 (Low; bounded 2026-11-25): the model document's SQL is interpolated into queries

`profile()` builds `SELECT {m.sql} FROM (SELECT ({f.sql}) AS … FROM …)` from the semantic model's own expressions (`sqlite.py:111-130`).
The connection is read-only and `sqlite3` refuses a second statement, so this cannot change data. But a model file from someone else
can run any read-only query, including B1's endless one. When the file comes from outside, it deserves the same bounds as `ask`.

## 4. Weaknesses and smaller notes

- **`pick_datasets` rebuilds the lineage graph for every metric that matches the question** (`ask.py:63`). It is built again in
  `context()` too: build it once and pass it in.
- **`words()` drops a trailing `s` from any word longer than 3 letters** ("class" becomes "clas", "status" becomes "statu"). That is
  harmless while both sides of a match are stemmed the same way, but it makes the stems surprising in logs.
- **Implicit joins are not checked:** only `JOIN … ON` equalities are compared with the model's relationships (`ask.py:205-213`).
  `FROM a, b WHERE a.x = b.y` passes without a warning, so the check is easy to sidestep without meaning to.
- **`table_of` falls back to the last dotted part of `source`** when no table has the whole name. Two datasets `sales.orders` and
  `archive.orders` would then map to one table, `orders`, without a warning.

## 5. Gaps

- **Not installed in `graf`.** The shared environment did not have data-graph or `sqlglot`, so `pytest` failed at collection until a
  `pip install -e`. Setup.md (or the plan) should say which environment it lives in, as the other apps' guides do.
- **No test for a runaway query** (B1), or for a swapped-pairs answer in `bench` (B2).
- **No user guide** (`docs/GUIDE/` is absent, as noted in book-graph's comparison table).

## 6. Suggested order

1. **B1**, preferably in dd-db so every adapter and caller gets it.
2. **B2**, then re-score `benchmarks/chinook/results/`.
3. **B3**, and the implicit-join warning.

## 7. Fixes (2026-11-25)

**B1, in dd-db 0.1.4 and data-graph.**

- dd-db has `run_select(sql, *, max_rows, timeout_s, min_free_bytes)` for SQLite and DuckDB. It fetches at most `max_rows`
  rows. It stops the query after `timeout_s`: SQLite through a progress handler, which works where SIGALRM did not; DuckDB
  through a watchdog that calls `interrupt()`. And it stops the query when free space where the database writes
  temporary files falls below `min_free_bytes` (the "Avail" that `df` shows). A stopped query raises `QueryLimitExceeded`.
- data-graph's `limits.py` runs every query through it: `ask`, `bench`, `profile` and `draft`, with 30 s, 1 GiB free, and
  `row_limit` rows (a 5 s budget for counting the rest).
- `check_sql` also refuses a recursive CTE with no stop condition before running it (`unbounded_recursion`), and the `ask`
  prompts now ask for queries that finish: a stop condition on recursion, an ON for every join, aggregation in SQL.
- **Measured again:** the endless CTE is refused in 0.0 s. A CTE whose WHERE is never false returns its 20 rows, or is
  stopped at 30 s with a hint the repair round can act on. The process peaked at 144 MB (it used to be 3.5 GB after 20 s
  and climbing).

**B2:** `matches` compares rows as rows. Each reference column gets its own answer column; for `rows`, the projected rows
must pair up one for one (a bipartite matching), and for `superset`/`within`, row by row. All 24 Chinook references still
pass against themselves.

The saved results in `benchmarks/chinook/results/` (2026-11-22) do not hold the answers' SQL, so they can only be
re-scored by running the benchmark again. `dgr bench -o` now saves each answer's SQL.

Along the way: dd-db's repository was behind its PyPI release, so it was first brought up to the published 0.1.3. That
release had a regression: `SQLiteDB(":memory:")` lost its tables between calls. It now keeps one shared in-memory
database per instance. Tests: dd-db 60 pass, plus 1 DuckDB 2.0 failure that predates this change
(`test_query_from_dataframe`); data-graph 102 pass.
