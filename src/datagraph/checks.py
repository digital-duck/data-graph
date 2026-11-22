"""Checks on a semantic model: the JSON Schema for its version, then references and gaps in the lineage graph.

Each problem is a dict {severity, code, node, message}; severity is "error" (the model is wrong) or "warning" (a gap).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import networkx as nx

from jsonschema.validators import validator_for

from .lineage import dataset_id, join_graph
from .model import SemanticModel

SCHEMAS = Path(__file__).parent / "schemas"


def schema_for(version: str) -> Path | None:
    """The bundled schema for a document version: 0.1.x -> OSI 0.1.1, 0.2+ -> Ossie 0.3.0.dev0; None if unknown."""
    if version.startswith("0.1"):
        return SCHEMAS / "osi-0.1.1.schema.json"
    if version.startswith(("0.2", "0.3")):
        return SCHEMAS / "ossie-0.3.0.dev0.schema.json"
    return None


@lru_cache(maxsize=None)
def _validator(path: str):
    schema = json.loads(Path(path).read_text(encoding="utf-8"))
    cls = validator_for(schema)
    return cls(schema)


def schema_problems(doc: dict) -> list[dict]:
    version = str(doc.get("version", ""))
    path = schema_for(version)
    if path is None:
        return [{"severity": "warning", "code": "UNKNOWN_VERSION", "node": "",
                 "message": f"no bundled schema for version {version!r}; schema not checked"}]
    out = []
    for e in sorted(_validator(str(path)).iter_errors(doc), key=lambda e: list(e.absolute_path)):
        where = "/".join(str(p) for p in e.absolute_path) or "(root)"
        out.append({"severity": "error", "code": "SCHEMA", "node": where, "message": e.message[:300]})
    return out


def model_problems(model: SemanticModel, G: nx.DiGraph) -> list[dict]:
    """The lineage graph's own problems (unknown references, SQL that does not parse) plus duplicate names, joins
    and gaps."""
    out = list(G.graph["problems"])

    def add(severity: str, code: str, node: str, message: str) -> None:
        out.append({"severity": severity, "code": code, "node": node, "message": message})

    seen: set[str] = set()
    for d in model.datasets:
        if d.name in seen:
            add("error", "DUPLICATE_NAME", dataset_id(d.name), f"dataset {d.name!r} is declared twice")
        seen.add(d.name)
        names = [f.name for f in d.fields]
        for n in sorted({n for n in names if names.count(n) > 1}):
            add("error", "DUPLICATE_NAME", f"field:{d.name}.{n}", f"field {n!r} is declared twice in {d.name!r}")
        for k in d.primary_key:              # 0.2+ keys are dataset fields; 0.1.x called them columns, so only warn there
            if not d.field_named(k):
                add("warning" if model.version.startswith("0.1") else "error", "UNKNOWN_KEY", dataset_id(d.name),
                    f"primary key {k!r} is not a field of {d.name!r}")
    mnames = [m.name for m in model.metrics]
    for n in sorted({n for n in mnames if mnames.count(n) > 1}):
        add("error", "DUPLICATE_NAME", f"metric:{n}", f"model metric {n!r} is declared twice")

    joined: set[str] = set()
    for r in model.relationships:
        node = f"relationship:{r.name}"
        a, b = model.dataset(r.from_dataset), model.dataset(r.to_dataset)
        for side, ds, cols in (("from", a, r.from_columns), ("to", b, r.to_columns)):
            name = r.from_dataset if side == "from" else r.to_dataset
            if ds is None:
                add("error", "UNKNOWN_DATASET", node, f"`{side}` names no declared dataset: {name!r}")
                continue
            for c in cols:
                if not ds.field_named(c):
                    add("error", "UNKNOWN_FIELD", node, f"`{side}_columns` names {c!r}, not a field of {ds.name!r}")
        if len(r.from_columns) != len(r.to_columns):
            add("error", "JOIN_ARITY", node, f"{len(r.from_columns)} from_columns but {len(r.to_columns)} to_columns")
        if b is not None and r.to_columns and set(r.to_columns) != set(b.primary_key) and b.primary_key:
            add("warning", "JOIN_NOT_ON_KEY", node,
                f"`to_columns` {r.to_columns} are not {b.name!r}'s primary key {b.primary_key}; the join may fan out")
        joined.update({r.from_dataset, r.to_dataset})

    J = join_graph(G)
    for m in model.metrics:
        ds = G.nodes.get(f"metric:{m.name}", {}).get("datasets") or []
        for other in ds[1:]:
            if ds[0] in J and other in J and not nx.has_path(J, ds[0], other):
                add("error", "NO_JOIN_PATH", f"metric:{m.name}", f"datasets {ds[0]!r} and {other!r} have no join path")

    for mm in model.all_metrics():
        if not (mm.description or mm.instructions or mm.synonyms):
            add("warning", "UNDESCRIBED", f"metric:{mm.key}", "metric has no description or ai_context")
    if len(model.datasets) > 1:
        for d in model.datasets:
            if d.name not in joined:
                add("warning", "UNJOINED_DATASET", dataset_id(d.name), "dataset takes part in no relationship")
    if not model.all_metrics():
        add("warning", "NO_METRICS", "", "the model defines no metrics")
    return out


def summarize(problems: list[dict]) -> tuple[int, int]:
    errors = sum(p["severity"] == "error" for p in problems)
    return errors, len(problems) - errors
