"""Reading an Ossie semantic model (0.1.x and 0.3.x) into plain dataclasses.

0.1.x wraps models in `semantic_model: [...]` and writes an expression as `{dialects: [{dialect, expression}]}`.
0.3.x puts one model at the root and writes `{sql: ..., dialects: [...]}`, with `sql` authoritative. Both are read the same way.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SQL_DIALECTS = ("OSSIE_SQL_2026", "ANSI_SQL")      # dialects whose expressions are read as SQL; any other is opaque


class ModelError(ValueError):
    """The file is not an Ossie semantic model (unreadable, or missing what every version requires)."""


def expression_sql(expr: Any) -> str | None:
    """The SQL of an Ossie expression: `sql`, else the first OSSIE_SQL_2026 or ANSI_SQL entry of `dialects`; None if the
    expression is only in another dialect (opaque here) or missing."""
    if isinstance(expr, str):
        return expr
    if not isinstance(expr, dict):
        return None
    if expr.get("sql"):
        return str(expr["sql"])
    entries = [d for d in expr.get("dialects") or [] if isinstance(d, dict)]
    for wanted in SQL_DIALECTS:
        for d in entries:
            if str(d.get("dialect", "")).upper() == wanted and d.get("expression"):
                return str(d["expression"])
    return None


def _ai_text(ai: Any) -> tuple[str, list[str]]:
    """`ai_context` as (instructions, synonyms): it is a string or an object with instructions / synonyms / examples."""
    if isinstance(ai, str):
        return ai, []
    if isinstance(ai, dict):
        return str(ai.get("instructions") or ""), [str(s) for s in ai.get("synonyms") or []]
    return "", []


@dataclass
class Field:
    name: str
    sql: str | None
    datatype: str | None = None
    description: str = ""
    is_dimension: bool = False
    is_time: bool = False
    instructions: str = ""
    synonyms: list[str] = field(default_factory=list)


@dataclass
class Metric:
    name: str
    sql: str | None
    dataset: str | None = None              # the dataset of a dataset-scoped metric; None for a model-scoped one
    datatype: str | None = None
    description: str = ""
    instructions: str = ""
    synonyms: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return f"{self.dataset}.{self.name}" if self.dataset else self.name


@dataclass
class Dataset:
    name: str
    source: str
    primary_key: list[str] = field(default_factory=list)
    description: str = ""
    instructions: str = ""
    synonyms: list[str] = field(default_factory=list)
    fields: list[Field] = field(default_factory=list)
    metrics: list[Metric] = field(default_factory=list)

    def field_named(self, name: str) -> Field | None:
        return next((f for f in self.fields if f.name == name), None)

    def metric_named(self, name: str) -> Metric | None:
        return next((m for m in self.metrics if m.name == name), None)


@dataclass
class Relationship:
    name: str
    from_dataset: str
    to_dataset: str
    from_columns: list[str]
    to_columns: list[str]


@dataclass
class SemanticModel:
    name: str
    version: str
    description: str = ""
    instructions: str = ""
    datasets: list[Dataset] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    metrics: list[Metric] = field(default_factory=list)          # model-scoped
    path: Path | None = None

    def dataset(self, name: str) -> Dataset | None:
        return next((d for d in self.datasets if d.name == name), None)

    def metric(self, name: str) -> Metric | None:
        return next((m for m in self.metrics if m.name == name), None)

    def all_metrics(self) -> list[Metric]:
        return [m for d in self.datasets for m in d.metrics] + self.metrics


def read_document(path: str | Path) -> dict:
    """The raw document (YAML or JSON) as a mapping."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
        data = json.loads(text) if p.suffix == ".json" else yaml.safe_load(text)
    except (OSError, ValueError, yaml.YAMLError) as e:
        raise ModelError(f"cannot read {p}: {e}") from None
    if not isinstance(data, dict):
        raise ModelError(f"{p}: not a mapping at the top level")
    return data


def raw_models(doc: dict) -> list[dict]:
    """The model mappings of a document: the `semantic_model` list (0.1.x) or the document itself (0.3.x)."""
    if "semantic_model" in doc:
        models = doc["semantic_model"]
        if isinstance(models, dict):
            models = [models]
        if not isinstance(models, list) or not models:
            raise ModelError("`semantic_model` must be a non-empty list of models")
        return models
    if "datasets" not in doc:
        raise ModelError("neither `datasets` (Ossie 0.2+) nor `semantic_model` (0.1.x) found")
    return [doc]


def _metric(raw: dict, dataset: str | None) -> Metric:
    instructions, synonyms = _ai_text(raw.get("ai_context"))
    return Metric(name=str(raw.get("name", "")), sql=expression_sql(raw.get("expression")), dataset=dataset,
                  datatype=raw.get("datatype"), description=str(raw.get("description") or ""),
                  instructions=instructions, synonyms=synonyms)


def _model(raw: dict, version: str, path: Path | None) -> SemanticModel:
    instructions, _ = _ai_text(raw.get("ai_context"))
    datasets = []
    for d in raw.get("datasets") or []:
        d_instr, d_syn = _ai_text(d.get("ai_context"))
        fields = []
        for f in d.get("fields") or []:
            f_instr, f_syn = _ai_text(f.get("ai_context"))
            dim = f.get("dimension")
            is_dim = isinstance(dim, dict)
            is_time = bool(dim.get("is_time")) if is_dim and "is_time" in dim else (
                is_dim and f.get("datatype") in ("Date", "Time", "DateTime", "DateTimeTz"))
            fields.append(Field(name=str(f.get("name", "")), sql=expression_sql(f.get("expression")), datatype=f.get("datatype"),
                                description=str(f.get("description") or ""), is_dimension=is_dim, is_time=is_time,
                                instructions=f_instr, synonyms=f_syn))
        name = str(d.get("name", ""))
        datasets.append(Dataset(name=name, source=str(d.get("source", "")), primary_key=list(d.get("primary_key") or []),
                                description=str(d.get("description") or ""), instructions=d_instr, synonyms=d_syn,
                                fields=fields, metrics=[_metric(m, name) for m in d.get("metrics") or []]))
    rels = [Relationship(name=str(r.get("name", "")), from_dataset=str(r.get("from", "")), to_dataset=str(r.get("to", "")),
                         from_columns=list(r.get("from_columns") or []), to_columns=list(r.get("to_columns") or []))
            for r in raw.get("relationships") or []]
    return SemanticModel(name=str(raw.get("name", "")), version=version, description=str(raw.get("description") or ""),
                         instructions=instructions, datasets=datasets, relationships=rels,
                         metrics=[_metric(m, None) for m in raw.get("metrics") or []], path=path)


def load_models(path: str | Path) -> list[SemanticModel]:
    """Every semantic model in a file (one for 0.3.x; one or more for 0.1.x)."""
    doc = read_document(path)
    version = str(doc.get("version", ""))
    return [_model(m, version, Path(path)) for m in raw_models(doc)]


def load(path: str | Path, name: str | None = None) -> SemanticModel:
    """The semantic model in a file; `name` picks one when a 0.1.x document holds several."""
    models = load_models(path)
    if name is None:
        if len(models) > 1:
            raise ModelError(f"{path} holds {len(models)} models ({', '.join(m.name for m in models)}); pick one by name")
        return models[0]
    for m in models:
        if m.name == name:
            return m
    raise ModelError(f"no model named {name!r} in {path} (has: {', '.join(m.name for m in models)})")
