"""Databases, through dd-db: draft an Ossie skeleton from a catalogue, and profile the data behind a model. Read-only.

The connection is dd-db's `SQLiteDB(path, read_only=True)` (`mode=ro` plus `PRAGMA query_only`), so nothing here can
write. Identifiers come from the model or the catalogue and are always quoted; values are never interpolated. A model's
field and metric expressions are SQL, so every query runs under data-graph's limits (rows, time, free disk; limits.py).
dd-db has adapters for DuckDB, Postgres, Snowflake and more; `draft` and `profile` use only `BaseDB` methods, so
another adapter is a matter of `connect()` (see docs/DEV/plan.md, "Next").
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from dd_db import BaseDB, SQLiteDB

from .limits import QueryLimitExceeded, limit_hint, select
from .model import SemanticModel

_TYPES = (("INT", "Integer"), ("CHAR", "String"), ("CLOB", "String"), ("TEXT", "String"), ("REAL", "Float"),
          ("FLOA", "Float"), ("DOUB", "Float"), ("NUMERIC", "Decimal"), ("DECIMAL", "Decimal"), ("BOOL", "Boolean"),
          ("DATETIME", "DateTime"), ("TIMESTAMP", "DateTime"), ("DATE", "Date"), ("TIME", "Time"))


def connect(db: str | Path) -> BaseDB:
    """A read-only dd-db connection to a SQLite file."""
    p = Path(db).resolve()
    if not p.is_file():
        raise FileNotFoundError(f"no database at {p}")
    return SQLiteDB(str(p), read_only=True)


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def ossie_type(decl: str) -> str | None:
    """A declared SQL type as an Ossie datatype (SQLite's affinity rules, roughly); None when it says nothing."""
    d = (decl or "").upper()
    for key, t in _TYPES:
        if key in d:
            return t
    return None


def _first(db: BaseDB, sql: str):
    """The first row of a query, under the limits; a stopped query raises with a message that says why."""
    try:
        return select(db, sql, max_rows=1).rows.iloc[0]
    except QueryLimitExceeded as e:
        raise RuntimeError(limit_hint(e)) from None


def _scalar(db: BaseDB, sql: str) -> Any:
    v = _first(db, sql).iloc[0]
    return v.item() if hasattr(v, "item") else v          # numpy scalar -> Python


def _row(db: BaseDB, sql: str) -> list:
    return [v.item() if hasattr(v, "item") else v for v in _first(db, sql).tolist()]


def draft(db: str | Path | BaseDB, name: str | None = None, version: str = "0.3.0.dev0") -> dict:
    """An Ossie document from the catalogue: one dataset per table, one field per column, the primary key, and one
    relationship per foreign key. No descriptions and no metrics: those need a person or a model."""
    conn = db if isinstance(db, BaseDB) else connect(db)
    schemas = {t: conn.get_schema(t) for t in conn.list_tables() if not t.startswith("sqlite_")}
    datasets, rels = [], []
    for t, s in sorted(schemas.items()):
        fields = []
        for c in s.columns:
            f: dict = {"name": c.name, "expression": {"sql": c.name}}
            if ossie_type(c.data_type):
                f["datatype"] = ossie_type(c.data_type)
            if f.get("datatype") in ("Date", "Time", "DateTime"):
                f["dimension"] = {"is_time": True}
            fields.append(f)
        ds: dict = {"name": t, "source": t, "fields": fields}
        pk = [c.name for c in s.columns if c.primary_key]     # dd-db lists columns in order; composite keys keep it
        if pk:
            ds["primary_key"] = pk
        datasets.append(ds)
        for fk in s.foreign_keys:
            to_cols = fk.ref_columns or [c.name for c in schemas[fk.ref_table].columns if c.primary_key] \
                if fk.ref_table in schemas else fk.ref_columns
            rels.append({"name": f"{t}_to_{fk.ref_table}" + (f"_{fk.columns[0]}" if len(s.foreign_keys) > 1 else ""),
                         "from": t, "to": fk.ref_table, "from_columns": fk.columns, "to_columns": to_cols})
    stem = Path(db).stem if not isinstance(db, BaseDB) else "model"
    doc: dict = {"version": version, "name": name or stem, "datasets": datasets}
    if rels:
        doc["relationships"] = rels
    return doc


def _table(tables: set[str], source: str) -> str | None:
    """The table a dataset's `source` names: the whole name, else its last dotted part (db.schema.table)."""
    for cand in (source, source.split(".")[-1]):
        if cand in tables:
            return cand
    return None


def profile(model: SemanticModel, db: str | Path | BaseDB, *, distinct_limit: int = 1_000_000) -> dict:
    """Row counts per dataset; per field the non-null and distinct counts, min and max; each dataset-scoped metric's value.
    A field or metric whose SQL does not run reports its error instead of a value."""
    conn = db if isinstance(db, BaseDB) else connect(db)
    where = str(Path(db).resolve()) if not isinstance(db, BaseDB) else str(conn.connection_info())
    out: dict = {"model": model.name, "database": where, "datasets": []}
    tables = set(conn.list_tables())
    for d in model.datasets:
        entry: dict = {"name": d.name, "source": d.source, "fields": [], "metrics": []}
        out["datasets"].append(entry)
        table = _table(tables, d.source)
        if table is None:
            entry["error"] = f"no table for source {d.source!r}"
            continue
        entry["table"] = table
        rows = _scalar(conn, f"SELECT COUNT(*) FROM {q(table)}")
        entry["rows"] = rows
        # each field's expression, named by the field, so dataset-scoped metrics can use field names
        exprs = [(f, f.sql or q(f.name)) for f in d.fields]
        inner = ", ".join(f"({sql}) AS {q(f.name)}" for f, sql in exprs) or "1 AS one"
        view = f"(SELECT {inner} FROM {q(table)}) AS t"
        for f, sql in exprs:
            fe: dict = {"name": f.name}
            try:
                nn, dist, lo, hi = _row(conn,
                    f"SELECT COUNT({q(f.name)}), COUNT(DISTINCT {q(f.name)}), MIN({q(f.name)}), MAX({q(f.name)}) "
                    f"FROM (SELECT ({sql}) AS {q(f.name)} FROM {q(table)} LIMIT {int(distinct_limit)})")
                fe.update(non_null=nn, nulls=rows - nn if rows <= distinct_limit else None, distinct=dist, min=lo, max=hi)
            except Exception as e:                       # the database's own error, whichever adapter raised it
                fe["error"] = str(e)
            entry["fields"].append(fe)
        for m in d.metrics:
            me: dict = {"name": m.name, "sql": m.sql}
            if m.sql is None:
                me["error"] = "no SQL expression"
            else:
                try:
                    me["value"] = _scalar(conn, f"SELECT {m.sql} FROM {view}")
                except Exception as e:
                    me["error"] = str(e)
            entry["metrics"].append(me)
    return out
