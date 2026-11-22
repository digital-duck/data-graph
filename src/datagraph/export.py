"""The lineage graph as zinet `graph.yaml` (for zinet's validate / render / slice) or as JSON.

zinet layers: sources and columns are primitives (they depend on nothing); datasets, fields and metrics are concepts;
the model itself is the one application, depending on every metric (or every dataset, when it has no metrics).
zinet ids are plain names, so `kind:name` becomes `kind__name` with dots kept.
"""
from __future__ import annotations

import json
import re

import networkx as nx
from zinet import EdgeType, Graph, Node

_EDGE_TYPE = {"reads": "data_flow", "derives": "data_flow", "contains": "depends_on", "computes": "depends_on"}


def zinet_id(node: str) -> str:
    return re.sub(r"[^\w.\-]", "_", node.replace(":", "__", 1))


def to_zinet(G: nx.DiGraph) -> Graph:
    prims, concepts = [], []
    for n, data in G.nodes(data=True):
        deps = sorted(G.predecessors(n))
        meta = {k: v for k, v in data.items() if v not in (None, "", [], False) and k != "label"}
        node = Node(id=zinet_id(n), label=f"{data['kind']} {n.split(':', 1)[1]}", deps=[zinet_id(d) for d in deps],
                    edge_types={zinet_id(d): EdgeType(_EDGE_TYPE[G.edges[d, n]["rel"]]) for d in deps}, meta=meta)
        (prims if data["kind"] in ("source", "column") and not deps else concepts).append(node)
    tops = [n for n, k in G.nodes(data="kind") if k == "metric" and G.out_degree(n) == 0] or \
           [n for n, k in G.nodes(data="kind") if k == "dataset"]
    app = Node(id=zinet_id(f"model:{G.graph['name']}"), label=f"model {G.graph['name']}", deps=[zinet_id(t) for t in sorted(tops)],
               meta={"kind": "model", "version": G.graph.get("version"), "joins": G.graph["joins"]})
    return Graph(primitives=prims, concepts=concepts, applications=[app])


def to_json(G: nx.DiGraph) -> str:
    return json.dumps({"name": G.graph["name"], "version": G.graph.get("version"),
                       "nodes": [{"id": n, **d} for n, d in G.nodes(data=True)],
                       "edges": [{"from": a, "to": b, **d} for a, b, d in G.edges(data=True)],
                       "joins": G.graph["joins"]}, indent=2)
