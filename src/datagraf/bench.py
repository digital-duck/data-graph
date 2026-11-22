"""Scoring a text-to-SQL answer by running it: `dgr bench` over a questions file (benchmarks/chinook/questions.yaml).

An answer's SQL and each reference query run on the same read-only database; the answer passes if its rows match one of
the references by that reference's `check` (rows / superset / within / mentions; see the questions file for what each means).
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import yaml


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


def _is_num(s: str) -> bool:
    try:
        float(s)
        return True
    except ValueError:
        return False


def matches(gold: dict, gold_rows: list[tuple] | None, sql: str, pred_rows: list[tuple] | None) -> bool:
    check = gold.get("check", "rows")
    if check == "mentions":
        text = (sql or "").lower()
        return all(w.lower() in text for w in gold["words"])
    if pred_rows is None or gold_rows is None:
        return False
    if check == "rows":
        if len(gold_rows) != len(pred_rows):
            return False
        if not gold_rows:
            return True
        pcols = columns(pred_rows)
        return all(any(_covers(gc, pc) for pc in pcols) for gc in columns(gold_rows))
    if check == "within":
        # one of a set of right answers (e.g. any customer tied for the most): some answer column's values, all of them,
        # are among the reference column's values (text may be longer: "Frank Harris" is within ["harris"])
        if not pred_rows:
            return False
        allowed = [set(gc) for gc in columns(gold_rows)]
        def inside(pc, ok):
            return all(v in ok or any(g and not _is_num(g) and g in v for g in ok) for v in pc if v)
        return all(any(any(pc) and inside(pc, ok) for pc in columns(pred_rows)) for ok in allowed)
    if check == "superset":
        if not gold_rows:
            return True
        pcols = columns(pred_rows)
        return all(any(set(gc) <= set(pc) for pc in pcols) for gc in columns(gold_rows))
    raise ValueError(f"unknown check {check!r}")


def run_sql(db, sql: str) -> tuple[list[tuple] | None, str | None]:
    """Rows of a query on a dd-db connection, or the error."""
    try:
        df = db.run_query(sql)
    except Exception as e:                   # the database's own message
        return None, str(e)
    return [tuple(None if (isinstance(v, float) and v != v) else (v.item() if hasattr(v, "item") else v) for v in r)
            for r in df.itertuples(index=False, name=None)], None


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
