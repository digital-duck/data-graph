"""data-graph: lineage, checks and profiling for semantic layers written in Apache Ossie.

    import datagraf as dg
    model = dg.load("tpcds_semantic_model.yaml")
    G = dg.build(model)                       # networkx DiGraph: source -> column -> field -> metric
    dg.upstream(G, dg.resolve(G, "customer_lifetime_value"))
"""
from __future__ import annotations

from .checks import model_problems, schema_problems
from .lineage import build, downstream, join_graph, resolve, sql_refs, upstream
from .model import Dataset, Field, Metric, ModelError, Relationship, SemanticModel, load, load_models

__version__ = "0.1.0.dev0"

__all__ = ["Dataset", "Field", "Metric", "ModelError", "Relationship", "SemanticModel", "build", "downstream",
           "join_graph", "load", "load_models", "model_problems", "resolve", "schema_problems", "sql_refs", "upstream"]
