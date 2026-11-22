"""The deterministic half of the `ask` recipe: what the model is shown, and the check its SQL must pass.

context()   the semantic model, narrowed by the graph: the datasets a question names (by name, synonym, description, field or
            metric), plus every dataset on the join paths between them. Vector similarity is a poor guide to which tables a
            query needs (anadata's finding: "invoice_items" shares no word with "best-selling albums"); join paths are exact.
check_sql() one read-only statement; every table and column real (sqlglot, against the database's own catalogue); joins
            compared with the model's relationships (a join that is not declared is a warning); then run, read-only.
"""
from __future__ import annotations

import re
from itertools import combinations
from typing import Any

import networkx as nx
import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError, TokenError
from sqlglot.optimizer.qualify import qualify

from . import lineage as L
from .model import SemanticModel
from .sqlite import connect

SYSTEM_TABLES = {"sqlite_master": ["type", "name", "tbl_name", "rootpage", "sql"],
                 "sqlite_schema": ["type", "name", "tbl_name", "rootpage", "sql"]}
_STOP = set("a an and are as at be by can do does each for from get give how i in is it list me most of on or per show "
            "that the their there these this to top was were what when where which who with all any find have has".split())


def words(text: str) -> set[str]:
    """Lower-case word stems: camelCase and snake_case split, a plural -s dropped."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "")
    out = set()
    for w in re.findall(r"[A-Za-z][A-Za-z0-9]+", text):
        w = w.lower()
        if w in _STOP or len(w) < 3:
            continue
        out.add(w[:-1] if w.endswith("s") and len(w) > 3 else w)
    return out


def table_of(source: str, tables: set[str] | None = None) -> str:
    """The physical table a dataset's `source` names (its last dotted part, unless the whole name is a table)."""
    if tables and source in tables:
        return source
    return source.split(".")[-1]


def pick_datasets(model: SemanticModel, question: str) -> tuple[list[str], dict[str, int]]:
    """Datasets the question names, then the datasets on the join paths that connect them. All of them when none match."""
    q = words(question)
    scores: dict[str, int] = {}
    for d in model.datasets:
        s = 3 * len(q & (words(d.name) | words(" ".join(d.synonyms))))
        s += len(q & words(d.description))
        s += sum(1 for f in d.fields if q & words(f.name + " " + " ".join(f.synonyms)))
        s += sum(2 for m in d.metrics if q & words(m.name + " " + " ".join(m.synonyms)))
        if s:
            scores[d.name] = s
    for m in model.metrics:
        if q & words(m.name + " " + " ".join(m.synonyms)):
            G = L.build(model)
            for ds in G.nodes.get(L.metric_id(m.name), {}).get("datasets", []):
                scores[ds] = scores.get(ds, 0) + 2
    if not scores:
        return [d.name for d in model.datasets], {}
    picked = set(scores)
    J = L.join_graph(L.build(model))
    for a, b in combinations(sorted(picked), 2):
        if a in J and b in J and nx.has_path(J, a, b):
            picked.update(nx.shortest_path(J, a, b))
    return [d.name for d in model.datasets if d.name in picked], scores


def context(model: SemanticModel, question: str, scope: str = "graph", tables: set[str] | None = None) -> dict:
    """The text the model is shown, and the ids in it."""
    names = [d.name for d in model.datasets] if scope == "all" else pick_datasets(model, question)[0]
    keep = set(names)
    lines = [f"Semantic model: {model.name}" + (f" - {model.description}" if model.description else "")]
    if model.instructions:
        lines.append(f"Notes: {model.instructions}")
    ids: list[str] = []
    for d in model.datasets:
        if d.name not in keep:
            continue
        ids.append(L.dataset_id(d.name))
        syn = f" Also called: {', '.join(d.synonyms)}." if d.synonyms else ""
        lines.append(f"\nDataset {d.name} (table {table_of(d.source, tables)}): {d.description or '-'}{syn}")
        for f in d.fields:
            ids.append(L.field_id(d.name, f.name))
            bits = [f.datatype or ""]
            if f.name in d.primary_key:
                bits.append("key")
            if f.is_time:
                bits.append("time")
            expr = f" = {f.sql}" if f.sql and f.sql != f.name else ""
            more = f" - {f.description}" if f.description else ""
            syn = f" (also: {', '.join(f.synonyms)})" if f.synonyms else ""
            lines.append(f"  {f.name}{expr} [{', '.join(b for b in bits if b)}]{more}{syn}")
        for m in d.metrics:
            ids.append(L.metric_id(m.name, d.name))
            syn = f" (also: {', '.join(m.synonyms)})" if m.synonyms else ""
            lines.append(f"  metric {m.name} = {m.sql}  - {m.description or '-'}{syn}")
    joins = [r for r in model.relationships if r.from_dataset in keep and r.to_dataset in keep]
    if joins:
        lines.append("\nJoins (many -> one):")
        for r in joins:
            a, b = model.dataset(r.from_dataset), model.dataset(r.to_dataset)
            ta, tb = table_of(a.source, tables), table_of(b.source, tables)
            on = " AND ".join(f"{ta}.{x} = {tb}.{y}" for x, y in zip(r.from_columns, r.to_columns))
            lines.append(f"  {r.name}: {on}")
    G = L.build(model)
    mm = [m for m in model.metrics if set(G.nodes[L.metric_id(m.name)].get("datasets", [])) <= keep]
    if mm:
        lines.append("\nModel metrics (they name dataset.field):")
        for m in mm:
            ids.append(L.metric_id(m.name))
            lines.append(f"  {m.name} = {m.sql}  - {m.description or '-'}")
    left_out = [d.name for d in model.datasets if d.name not in keep]
    if left_out:
        lines.append(f"\n(Other datasets, not shown: {', '.join(left_out)}.)")
    return {"text": "\n".join(lines), "ids": ids, "datasets": names}


def extract_sql(reply: str) -> str:
    """The SQL in a model's reply: the ```sql block if there is one, else the whole reply; no trailing semicolon."""
    m = re.search(r"```(?:sql|sqlite)?\s*\n(.*?)```", reply or "", re.S | re.I)
    sql = (m.group(1) if m else (reply or "")).strip()
    return sql.rstrip().rstrip(";").strip()


def catalogue(db) -> dict[str, dict[str, str]]:
    """{table: {column: type}} from the database, plus SQLite's catalogue table."""
    out = {t: {c.name: c.data_type or "TEXT" for c in db.get_schema(t).columns} for t in db.list_tables()}
    for t, cols in SYSTEM_TABLES.items():
        out.setdefault(t, {c: "TEXT" for c in cols})
    return out


def _clean(v: Any) -> Any:
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float) and v != v:
        return None
    return v if isinstance(v, (int, float, str)) or v is None else str(v)


def check_sql(reply: str, model: SemanticModel, db_path: str, row_limit: int = 20) -> dict:
    """{ok, error, sql, columns, rows, row_count, uses, warnings}. `error` is written for the model to act on."""
    sql = extract_sql(reply)
    base = {"ok": False, "sql": sql, "columns": [], "rows": [], "row_count": 0, "uses": [], "warnings": []}
    if not sql:
        return {**base, "error": "the reply holds no SQL"}
    try:
        trees = [t for t in sqlglot.parse(sql, read="sqlite") if t is not None]
    except (ParseError, TokenError) as e:
        return {**base, "error": f"the SQL does not parse: {str(e).splitlines()[0][:300]}"}
    if len(trees) != 1:
        return {**base, "error": f"write exactly one statement (found {len(trees)})"}
    tree = trees[0]
    if not isinstance(tree, exp.Query):
        return {**base, "error": f"only a read-only SELECT (or WITH ... SELECT) is allowed, not {tree.key.upper()}"}
    db = connect(db_path)
    cat = catalogue(db)
    lower = {t.lower(): t for t in cat}
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    unknown = sorted({t.name for t in tree.find_all(exp.Table) if t.name and t.name.lower() not in lower and t.name.lower() not in ctes})
    if unknown:
        return {**base, "error": f"no such table: {', '.join(unknown)}. The tables are: {', '.join(sorted(t for t in cat if t not in SYSTEM_TABLES))}"}
    try:
        q = qualify(tree.copy(), schema=cat, dialect="sqlite", validate_qualify_columns=True, quote_identifiers=False)
    except OptimizeError as e:
        return {**base, "error": f"{str(e).split('. Line')[0]} (check the column names against the tables given)"}

    # which tables the aliases stand for, the columns used, and the joins
    alias = {t.alias_or_name.lower(): lower.get(t.name.lower(), t.name) for t in q.find_all(exp.Table)}
    used = sorted({(alias.get(c.table.lower(), c.table), c.name) for c in q.find_all(exp.Column) if c.table})
    tables = set(cat)
    by_table: dict[str, Any] = {}
    for d in model.datasets:
        by_table.setdefault(table_of(d.source, tables).lower(), d)
    uses: list[str] = []
    for t in dict.fromkeys(alias.values()):            # every table read, even with no column named (COUNT(*))
        d = by_table.get(str(t).lower())
        if d is not None:
            uses.append(L.dataset_id(d.name))
    for t, col in used:
        d = by_table.get(str(t).lower())
        if d is None:
            continue
        uses.append(L.dataset_id(d.name))
        f = next((f for f in d.fields if (f.sql or f.name).lower() == col.lower() or f.name.lower() == col.lower()), None)
        if f:
            uses.append(L.field_id(d.name, f.name))
    declared = set()
    for r in model.relationships:
        a, b = model.dataset(r.from_dataset), model.dataset(r.to_dataset)
        if a and b:
            for x, y in zip(r.from_columns, r.to_columns):
                pa = (table_of(a.source, tables).lower(), x.lower())
                pb = (table_of(b.source, tables).lower(), y.lower())
                declared.add(frozenset((pa, pb)))
    warnings = []
    for j in q.find_all(exp.Join):
        on = j.args.get("on")
        for eq in (on.find_all(exp.EQ) if on is not None else []):
            l, r = eq.left, eq.right
            if isinstance(l, exp.Column) and isinstance(r, exp.Column) and l.table and r.table:
                pa = (str(alias.get(l.table.lower(), l.table)).lower(), l.name.lower())
                pb = (str(alias.get(r.table.lower(), r.table)).lower(), r.name.lower())
                if pa[0] in by_table and pb[0] in by_table and frozenset((pa, pb)) not in declared:
                    warnings.append(f"join {pa[0]}.{pa[1]} = {pb[0]}.{pb[1]} is not a relationship of the model")

    try:
        df = db.run_query(sql)
    except Exception as e:                       # the database's own message, e.g. a misuse of an aggregate
        return {**base, "uses": list(dict.fromkeys(uses)), "warnings": warnings, "error": f"the database refused it: {e}"}
    rows = [[_clean(v) for v in r] for r in df.head(row_limit).itertuples(index=False, name=None)]
    return {**base, "ok": True, "error": "", "columns": [str(c) for c in df.columns], "rows": rows, "row_count": int(len(df)),
            "uses": list(dict.fromkeys(uses)), "warnings": warnings}


def report(question: str, check: dict, attempts: int) -> dict:
    """The answer as text, and a status a program decided: checked (it ran) or failed."""
    lines = [f"Question: {question}", "", "SQL:", check.get("sql") or "(none)", ""]
    if check.get("ok"):
        cols, rows, n = check["columns"], check["rows"], check["row_count"]
        lines.append(f"CHECKED: every table and column exists, it is read-only, and it ran ({n} row{'s' if n != 1 else ''}).")
        if cols:
            lines += ["", " | ".join(cols), " | ".join("---" for _ in cols)]
            lines += [" | ".join("" if v is None else str(v) for v in r) for r in rows]
            if n > len(rows):
                lines.append(f"... {n - len(rows)} more")
        if check.get("uses"):
            lines += ["", "Uses: " + ", ".join(f"[{u}]" for u in check["uses"])]
        for w in check.get("warnings") or []:
            lines.append(f"Warning: {w}")
        status = "checked"
    else:
        lines.append(f"FAILED after {attempts} attempt(s): {check.get('error')}. Do not rely on this SQL.")
        status = "failed"
    return {"text": "\n".join(lines), "status": status}
