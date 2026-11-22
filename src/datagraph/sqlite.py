"""SQLite: draft an Ossie skeleton from a catalogue, and profile the data behind a model. Read-only throughout.

The database is opened `mode=ro` with `PRAGMA query_only`, so nothing here can write to it. Identifiers come from the
model or the catalogue and are always quoted; values are never interpolated.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from .model import SemanticModel

_TYPES = (("INT", "Integer"), ("CHAR", "String"), ("CLOB", "String"), ("TEXT", "String"), ("REAL", "Float"),
          ("FLOA", "Float"), ("DOUB", "Float"), ("NUMERIC", "Decimal"), ("DECIMAL", "Decimal"), ("BOOL", "Boolean"),
          ("DATETIME", "DateTime"), ("TIMESTAMP", "DateTime"), ("DATE", "Date"), ("TIME", "Time"))


def connect(db: str | Path) -> sqlite3.Connection:
    p = Path(db).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"no database at {p}")
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    con.execute("PRAGMA query_only = ON")
    return con


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def ossie_type(decl: str) -> str | None:
    """A SQLite declared type as an Ossie datatype (SQLite's affinity rules, roughly); None when it says nothing."""
    d = (decl or "").upper()
    for key, t in _TYPES:
        if key in d:
            return t
    return None


def draft(db: str | Path, name: str | None = None, version: str = "0.3.0.dev0") -> dict:
    """An Ossie document from the catalogue: one dataset per table, one field per column, the primary key, and one
    relationship per foreign key. No descriptions and no metrics: those need a person or a model."""
    con = connect(db)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        datasets, rels = [], []
        for t in tables:
            cols = con.execute(f"PRAGMA table_info({q(t)})").fetchall()       # cid, name, type, notnull, default, pk
            pk = [c[1] for c in sorted((c for c in cols if c[5]), key=lambda c: c[5])]
            fields = []
            for c in cols:
                f = {"name": c[1], "expression": {"sql": c[1]}}
                if ossie_type(c[2]):
                    f["datatype"] = ossie_type(c[2])
                if f.get("datatype") in ("Date", "Time", "DateTime"):
                    f["dimension"] = {"is_time": True}
                fields.append(f)
            ds = {"name": t, "source": t, "fields": fields}
            if pk:
                ds["primary_key"] = pk
            datasets.append(ds)
            fks: dict[int, list] = {}
            for r in con.execute(f"PRAGMA foreign_key_list({q(t)})").fetchall():   # id, seq, table, from, to, ...
                fks.setdefault(r[0], []).append(r)
            for rows in fks.values():
                rows.sort(key=lambda r: r[1])
                target = rows[0][2]
                to_cols = [r[4] for r in rows]
                if any(c is None for c in to_cols):                            # FK to the implicit primary key
                    to_cols = [c[1] for c in sorted((c for c in con.execute(f"PRAGMA table_info({q(target)})") if c[5]),
                                                    key=lambda c: c[5])]
                rels.append({"name": f"{t}_to_{target}" + (f"_{rows[0][3]}" if len(fks) > 1 else ""),
                             "from": t, "to": target, "from_columns": [r[3] for r in rows], "to_columns": to_cols})
    finally:
        con.close()
    doc: dict = {"version": version, "name": name or Path(db).stem, "datasets": datasets}
    if rels:
        doc["relationships"] = rels
    return doc


def _table(con: sqlite3.Connection, source: str) -> str | None:
    """The SQLite table a dataset's `source` names: the whole name, else its last dotted part (db.schema.table)."""
    have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    for cand in (source, source.split(".")[-1]):
        if cand in have:
            return cand
    return None


def profile(model: SemanticModel, db: str | Path, *, distinct_limit: int = 1_000_000) -> dict:
    """Row counts per dataset; per field the non-null and distinct counts, min and max; each dataset-scoped metric's value.
    A field or metric whose SQL does not run reports its error instead of a value."""
    con = connect(db)
    out: dict = {"model": model.name, "database": str(Path(db).resolve()), "datasets": []}
    try:
        for d in model.datasets:
            entry: dict = {"name": d.name, "source": d.source, "fields": [], "metrics": []}
            out["datasets"].append(entry)
            table = _table(con, d.source)
            if table is None:
                entry["error"] = f"no table for source {d.source!r}"
                continue
            entry["table"] = table
            rows = con.execute(f"SELECT COUNT(*) FROM {q(table)}").fetchone()[0]
            entry["rows"] = rows
            # each field's expression, named by the field, so dataset-scoped metrics can use field names
            exprs = [(f, f.sql or q(f.name)) for f in d.fields]
            inner = ", ".join(f"({sql}) AS {q(f.name)}" for f, sql in exprs) or "1 AS one"
            view = f"(SELECT {inner} FROM {q(table)}) AS t"
            for f, sql in exprs:
                fe: dict = {"name": f.name}
                try:
                    nn, dist, lo, hi = con.execute(
                        f"SELECT COUNT({q(f.name)}), COUNT(DISTINCT {q(f.name)}), MIN({q(f.name)}), MAX({q(f.name)}) "
                        f"FROM (SELECT ({sql}) AS {q(f.name)} FROM {q(table)} LIMIT {int(distinct_limit)})").fetchone()
                    fe.update(non_null=nn, nulls=rows - nn if rows <= distinct_limit else None, distinct=dist, min=lo, max=hi)
                except sqlite3.Error as e:
                    fe["error"] = str(e)
                entry["fields"].append(fe)
            for m in d.metrics:
                me: dict = {"name": m.name, "sql": m.sql}
                if m.sql is None:
                    me["error"] = "no SQL expression"
                else:
                    try:
                        me["value"] = con.execute(f"SELECT {m.sql} FROM {view}").fetchone()[0]
                    except sqlite3.Error as e:
                        me["error"] = str(e)
                entry["metrics"].append(me)
    finally:
        con.close()
    return out
