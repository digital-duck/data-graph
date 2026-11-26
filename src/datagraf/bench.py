"""Scoring a text-to-SQL answer by running it: `dgr bench` over a questions file (benchmarks/chinook/questions.yaml).

An answer's SQL and each reference query run on the same read-only database, bounded (limits.py); the answer passes if its
rows match one of the references by that reference's `check` (rows / superset / within / mentions; see `matches`).
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import networkx as nx
import yaml

from .limits import BENCH_MAX_ROWS, QueryLimitExceeded, select


def load_questions(path: str | Path) -> dict:
    p = Path(path)
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    doc["_dir"] = p.parent
    for q in doc["questions"]:
        golds = q["gold"] if isinstance(q["gold"], list) else [q["gold"]]
        q["gold"] = [{"check": "rows", **g} for g in golds]
    return doc


def norm(v: Any) -> str:
    """A cell as comparable text: numbers to 2 places (1 == 1.0 == 1.00), None as empty, text lower-cased and trimmed."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(int(v))
    if isinstance(v, (int, float)):
        r = round(float(v), 2)
        return str(int(r)) if r == int(r) else f"{r:.2f}"
    s = str(v).strip()
    try:
        return norm(float(s)) if s and s.lower() not in ("nan", "inf", "-inf") else s.lower()
    except ValueError:
        return s.lower()


def columns(rows: list[tuple]) -> list[list[str]]:
    return [[norm(r[i]) for r in rows] for i in range(len(rows[0]))] if rows else []


def _is_num(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def cell_matches(gold: str, pred: str) -> bool:
    """A reference cell matches an answer cell: equal, or (text) contained in it ("adams" in "andrew adams")."""
    return gold == pred or bool(gold) and not _is_num(gold) and gold in pred


def _row_matches(g: tuple, p: tuple) -> bool:
    return all(cell_matches(a, b) for a, b in zip(g, p))


def _pairs_up(gold: list[tuple], pred: list[tuple]) -> bool:
    """Every reference row is paired with a distinct answer row that matches it (both lists the same length).

    Equal rows are paired first; the rest (text contained in longer text) by a maximum bipartite matching."""
    left = Counter(pred)
    rest_g = []
    for g in gold:
        if left[g] > 0:
            left[g] -= 1
        else:
            rest_g.append(g)
    if not rest_g:
        return True
    rest_p = list(left.elements())
    G = nx.Graph()
    G.add_nodes_from(("g", i) for i in range(len(rest_g)))
    G.add_nodes_from(("p", j) for j in range(len(rest_p)))
    G.add_edges_from((("g", i), ("p", j)) for i, g in enumerate(rest_g) for j, p in enumerate(rest_p) if _row_matches(g, p))
    matching = nx.bipartite.maximum_matching(G, top_nodes=[("g", i) for i in range(len(rest_g))])
    return all(("g", i) in matching for i in range(len(rest_g)))


def _assignments(gcols: list[list[str]], pcols: list[list[str]], fits) -> Iterator[tuple[int, ...]]:
    """Ways to give each reference column its own answer column, keeping only answer columns that `fits` it."""
    cand = [[j for j, pc in enumerate(pcols) if fits(gc, pc)] for gc in gcols]

    def walk(i: int, used: tuple[int, ...]):
        if i == len(cand):
            yield used
            return
        for j in cand[i]:
            if j not in used:
                yield from walk(i + 1, used + (j,))
    yield from walk(0, ())


def matches(gold: dict, gold_rows: list[tuple] | None, sql: str, pred_rows: list[tuple] | None) -> bool:
    """Whether an answer's rows match a reference's, by the reference's `check`. Rows are compared as rows: each reference
    column is given its own answer column, and the rows, projected onto those columns, must match one for one (`rows`),
    include every reference row exactly (`superset`), or lie among the reference rows (`within`). Extra answer columns and
    any row order are fine; numbers are rounded to 2 places; for `rows` and `within` a text cell also matches a longer one
    containing it ("adams" in "andrew adams")."""
    check = gold.get("check", "rows")
    if check == "mentions":
        text = (sql or "").lower()
        return all(w.lower() in text for w in gold["words"])
    if check not in ("rows", "superset", "within"):
        raise ValueError(f"unknown check {check!r}")
    if pred_rows is None or gold_rows is None:
        return False
    if check == "rows" and len(gold_rows) != len(pred_rows):
        return False
    if not gold_rows:
        return check != "within" or not pred_rows
    if not pred_rows:
        return False
    gn = [tuple(norm(v) for v in r) for r in gold_rows]
    pn = [tuple(norm(v) for v in r) for r in pred_rows]
    gcols, pcols = columns(gold_rows), columns(pred_rows)
    if len(pcols) < len(gcols):
        return False
    if check == "rows":                                  # a quick column-wise test prunes the assignments
        fits = lambda gc, pc: _covers(gc, pc)                                                     # noqa: E731
    elif check == "superset":
        fits = lambda gc, pc: set(gc) <= set(pc)                                                  # noqa: E731
    else:
        fits = lambda gc, pc: all(any(cell_matches(g, p) for g in set(gc)) for p in set(pc) if p)  # noqa: E731
    for cols in _assignments(gcols, pcols, fits):
        proj = [tuple(r[j] for j in cols) for r in pn]
        if check == "rows" and _pairs_up(gn, proj):
            return True
        if check == "superset" and set(gn) <= set(proj):
            return True
        if check == "within" and all(any(_row_matches(g, p) for g in set(gn)) for p in set(proj) if any(p)):
            if any(any(p) for p in proj):
                return True
    return False


def _covers(gold: list[str], pred: list[str]) -> bool:
    """Every gold value is matched by a distinct pred value: equal, or (text) contained in it."""
    left = Counter(pred)
    for g in gold:
        if left[g] > 0:
            left[g] -= 1
            continue
        hit = next((p for p in left if left[p] > 0 and g and not _is_num(g) and g in p), None)
        if hit is None:
            return False
        left[hit] -= 1
    return True


def run_sql(db, sql: str, max_rows: int = BENCH_MAX_ROWS) -> tuple[list[tuple] | None, str | None]:
    """Rows of a query on a dd-db connection, or the error. Bounded: more than `max_rows` rows, or a query stopped by the
    time or disk limit, is an error, so a runaway answer cannot fill memory or hang the benchmark."""
    try:
        res = select(db, sql, max_rows=max_rows)
    except QueryLimitExceeded as e:
        return None, str(e)
    except Exception as e:                   # the database's own message
        return None, str(e)
    if res.truncated:
        return None, f"more than {max_rows} rows (the benchmark compares at most {max_rows})"
    return [tuple(None if (isinstance(v, float) and v != v) else (v.item() if hasattr(v, "item") else v) for v in r)
            for r in res.rows.itertuples(index=False, name=None)], None


def score(question: dict, sql: str | None, db) -> dict:
    """{passed, error, rows} for one answer against the question's references."""
    pred, err = run_sql(db, sql) if sql else (None, "no SQL")
    for g in question["gold"]:
        gold_rows = None
        if g.get("sql"):
            gold_rows, gerr = run_sql(db, g["sql"])
            if gerr:
                raise RuntimeError(f"question {question['id']}: the reference query fails: {gerr}")
        if matches(g, gold_rows, sql or "", pred):
            return {"passed": True, "error": None, "rows": None if pred is None else len(pred)}
    return {"passed": False, "error": err, "rows": None if pred is None else len(pred)}
