"""The lineage graph of a semantic model: source tables and columns -> datasets and fields -> metrics.

Edges point from what is needed to what needs it (zinet's convention), with `rel` one of
`reads` (source -> dataset), `contains` (dataset -> field), `derives` (column -> field), `computes` (field or metric -> metric).
Relationships are not dependencies: they are kept as many-to-one joins in `G.graph["joins"]`.

References are read from each expression's SQL (sqlglot), by the scoping rules of the spec:
a field names columns of its dataset's source; a dataset-scoped metric names that dataset's fields (or its other metrics);
a model-scoped metric names `dataset.field` or `dataset.metric`, or another model-scoped metric by its bare name.
What cannot be resolved is recorded in `G.graph["problems"]` (read by `checks`), never guessed.
"""
from __future__ import annotations

from dataclasses import dataclass

import networkx as nx
import sqlglot
from sqlglot import exp

from .model import SemanticModel


@dataclass(frozen=True)
class Ref:
    qualifier: str | None       # `store_sales` in `store_sales.ss_quantity`; None when unqualified
    name: str


def sql_refs(sql: str) -> tuple[list[Ref], str | None]:
    """The column references in an expression, and a parse error (or None). `COUNT(*)` names no column."""
    try:
        tree = sqlglot.parse_one(sql)
    except (sqlglot.errors.ParseError, sqlglot.errors.TokenError) as e:
        return [], str(e).splitlines()[0][:200]
    refs = []
    for col in tree.find_all(exp.Column):
        parts = [p.name for p in col.parts]
        if not parts or not parts[-1]:
            continue
        refs.append(Ref(".".join(parts[:-1]) or None, parts[-1]))
    return list(dict.fromkeys(refs)), None


def source_id(source: str) -> str:
    return f"source:{source}"


def column_id(source: str, column: str) -> str:
    return f"column:{source}.{column}"


def dataset_id(name: str) -> str:
    return f"dataset:{name}"


def field_id(dataset: str, name: str) -> str:
    return f"field:{dataset}.{name}"


def metric_id(name: str, dataset: str | None = None) -> str:
    return f"metric:{dataset}.{name}" if dataset else f"metric:{name}"


def build(model: SemanticModel) -> nx.DiGraph:
    G = nx.DiGraph(name=model.name, version=model.version, joins=[], problems=[])
    problems = G.graph["problems"]

    def problem(code: str, node: str, message: str, severity: str = "error") -> None:
        problems.append({"severity": severity, "code": code, "node": node, "message": message})

    for d in model.datasets:
        did = dataset_id(d.name)
        G.add_node(did, kind="dataset", label=d.name, description=d.description, source=d.source,
                   primary_key=d.primary_key, synonyms=d.synonyms)
        if d.source:
            G.add_node(source_id(d.source), kind="source", label=d.source)
            G.add_edge(source_id(d.source), did, rel="reads")
        for f in d.fields:
            fid = field_id(d.name, f.name)
            G.add_node(fid, kind="field", label=f.name, dataset=d.name, sql=f.sql, datatype=f.datatype,
                       description=f.description, dimension=f.is_dimension, time=f.is_time, synonyms=f.synonyms)
            G.add_edge(did, fid, rel="contains")
            if f.sql is None:
                problem("OPAQUE_EXPRESSION", fid, "field expression is in no SQL dialect read here; its columns are unknown", "warning")
                continue
            refs, err = sql_refs(f.sql)
            if err:
                problem("SQL_PARSE", fid, f"cannot parse {f.sql!r}: {err}", "warning")
            for r in refs:                       # a field names columns of its dataset's source
                cid = column_id(d.source, r.name)
                G.add_node(cid, kind="column", label=r.name, source=d.source)
                G.add_edge(cid, fid, rel="derives")

    def metric_node(m) -> str:
        mid = metric_id(m.name, m.dataset)
        G.add_node(mid, kind="metric", label=m.name, dataset=m.dataset, sql=m.sql, datatype=m.datatype,
                   description=m.description, instructions=m.instructions, synonyms=m.synonyms, datasets=[])
        return mid

    for d in model.datasets:
        for m in d.metrics:
            mid = metric_node(m)
            G.nodes[mid]["datasets"] = [d.name]
            if m.sql is None:
                problem("OPAQUE_EXPRESSION", mid, "metric expression is in no SQL dialect read here", "warning")
                continue
            refs, err = sql_refs(m.sql)
            if err:
                problem("SQL_PARSE", mid, f"cannot parse {m.sql!r}: {err}", "warning")
            for r in refs:
                if r.qualifier in (None, d.name):
                    if d.field_named(r.name):
                        G.add_edge(field_id(d.name, r.name), mid, rel="computes")
                    elif d.metric_named(r.name) and r.name != m.name:
                        G.add_edge(metric_id(r.name, d.name), mid, rel="computes")
                    else:
                        problem("UNKNOWN_REFERENCE", mid, f"{r.name!r} is not a field of dataset {d.name!r}")
                else:                            # `source.column` of a measure relation: a column of the dataset's source
                    cid = column_id(d.source, r.name)
                    G.add_node(cid, kind="column", label=r.name, source=d.source)
                    G.add_edge(cid, mid, rel="computes")

    for m in model.metrics:
        mid = metric_node(m)
        if m.sql is None:
            problem("OPAQUE_EXPRESSION", mid, "metric expression is in no SQL dialect read here", "warning")
            continue
        refs, err = sql_refs(m.sql)
        if err:
            problem("SQL_PARSE", mid, f"cannot parse {m.sql!r}: {err}", "warning")
        used = []
        for r in refs:
            if r.qualifier is None:
                if model.metric(r.name) and r.name != m.name:
                    G.add_edge(metric_id(r.name), mid, rel="computes")
                else:
                    problem("UNKNOWN_REFERENCE", mid, f"{r.name!r}: a model-scoped metric names `dataset.field`, "
                                                      "`dataset.metric` or another model-scoped metric")
                continue
            d = model.dataset(r.qualifier)
            if d is None:
                problem("UNKNOWN_REFERENCE", mid, f"{r.qualifier}.{r.name}: no dataset named {r.qualifier!r}")
                continue
            used.append(d.name)
            if d.field_named(r.name):
                G.add_edge(field_id(d.name, r.name), mid, rel="computes")
            elif d.metric_named(r.name):
                G.add_edge(metric_id(r.name, d.name), mid, rel="computes")
            else:
                problem("UNKNOWN_REFERENCE", mid, f"{r.qualifier}.{r.name}: dataset {d.name!r} has no field or metric {r.name!r}")
        G.nodes[mid]["datasets"] = list(dict.fromkeys(used))

    for rel in model.relationships:
        G.graph["joins"].append({"name": rel.name, "from": rel.from_dataset, "to": rel.to_dataset,
                                 "from_columns": rel.from_columns, "to_columns": rel.to_columns})
    return G


def join_graph(G: nx.DiGraph) -> nx.Graph:
    """Datasets joined by relationships, undirected (a join path may be walked either way)."""
    J = nx.Graph()
    J.add_nodes_from(n[len("dataset:"):] for n, k in G.nodes(data="kind") if k == "dataset")
    for j in G.graph["joins"]:
        if J.has_node(j["from"]) and J.has_node(j["to"]):
            J.add_edge(j["from"], j["to"], name=j["name"])
    return J


def resolve(G: nx.DiGraph, name: str) -> str:
    """A node id from what a person types: an id (`metric:total_sales`), or a name (`total_sales`, `store_sales.ss_quantity`,
    `store_sales`). Raises KeyError when nothing or more than one node matches."""
    if name in G:
        return name
    hits = [n for n in G if n.split(":", 1)[1] == name]
    if not hits:
        hits = [n for n in G if n.split(":", 1)[1].lower() == name.lower()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise KeyError(f"no node named {name!r}")
    raise KeyError(f"{name!r} is ambiguous: {', '.join(sorted(hits))}")


def upstream(G: nx.DiGraph, node: str) -> nx.DiGraph:
    """The node and everything it is made of."""
    return G.subgraph(nx.ancestors(G, node) | {node}).copy()


def downstream(G: nx.DiGraph, node: str) -> nx.DiGraph:
    """The node and everything that depends on it."""
    return G.subgraph(nx.descendants(G, node) | {node}).copy()


def outline(G: nx.DiGraph, root: str, *, up: bool) -> list[str]:
    """An indented outline from `root`, walking toward the sources (up) or toward the metrics (down)."""
    lines: list[str] = []
    seen: set[str] = set()

    def walk(n: str, depth: int) -> None:
        mark = " (see above)" if n in seen else ""
        sql = G.nodes[n].get("sql")
        extra = f"  = {sql}" if sql and G.nodes[n].get("kind") in ("metric", "field") and sql != G.nodes[n].get("label") else ""
        lines.append(f"{'  ' * depth}{n}{extra}{mark}")
        if mark:
            return
        seen.add(n)
        nxt = G.predecessors(n) if up else G.successors(n)
        for m in sorted(nxt):
            walk(m, depth + 1)

    walk(root, 0)
    return lines
