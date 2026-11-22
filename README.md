# data-graph

Lineage, checks and profiling for semantic layers written in [Apache Ossie](https://ossie.apache.org/) (incubating;
formerly Open Semantic Interchange). An Ossie model names the datasets, fields, metrics and joins of a business's data;
data-graph turns one into a graph and answers questions about it:

- **lineage:** what a metric is made of, down to source columns;
- **impact:** which metrics a change to a column, field or dataset touches;
- **checks:** unknown references, joins to undeclared datasets or fields, metrics over datasets with no join path, gaps;
- **profiling:** read-only, against the data behind the model;
- **drawing and export:** through [zinet](https://github.com/digital-duck/zinet).

It is part of the digital-duck family: zinet for the graph format and drawing, [GraphLang](https://github.com/digital-duck/graflang)
for the LLM steps (planned), beside app-graph, work-graph and concept-book. Prototype: version 0.1.0.dev0. See
[docs/DEV/plan.md](docs/DEV/plan.md) for the design and what comes next.

## Install

```bash
pip install -e ".[dev]"     # Python 3.11+; registers `datagraph` (alias `dg`)
pytest
```

## Use

```bash
datagraph validate model.yaml              # JSON Schema for its Ossie version, then references and gaps (exit 1 on an error)
datagraph show model.yaml                  # datasets, fields, metrics, joins
datagraph lineage model.yaml total_sales   # upstream, to source columns
datagraph impact model.yaml store_sales.ss_ext_sales_price   # downstream: the metrics it feeds
datagraph check model.yaml                 # references and gaps, without the schema
datagraph export model.yaml --format zinet -o lineage.graph.yaml   # or --format json
datagraph render model.yaml -o lineage.html                       # or --format mermaid / ascii / svg / png
datagraph draft --sqlite shop.db -o shop.semantic_model.yaml      # a skeleton from a SQLite catalogue
datagraph profile model.yaml --sqlite shop.db                     # rows, nulls, distinct, ranges, metric values
```

A name is a metric (`total_sales`), a dataset-scoped metric or field (`store_sales.total_quantity`), a dataset, or a node
id (`column:tpcds.public.store_sales.ss_quantity`).

Both Ossie shapes are read: the released 0.1.1 (`semantic_model:` list, `dialects` expressions) and the 0.3.0 draft
(one model at the root, `sql` expressions). Each is checked against its own bundled schema.

`examples/chinook.semantic_model.yaml` is the Chinook sample database (the one
[data-copilot](https://github.com/digital-duck/data-copilot) used) as a finished model: drafted with `datagraph draft`,
then given descriptions, synonyms and metrics by hand.

## From Python

```python
import datagraph as dg

model = dg.load("tpcds_semantic_model.yaml")
G = dg.build(model)                                   # networkx DiGraph: source -> column -> field -> metric
up = dg.upstream(G, dg.resolve(G, "customer_lifetime_value"))
problems = dg.model_problems(model, G)
```

## License

Apache-2.0. The Ossie schemas in `src/datagraph/schemas/` and the example models in `tests/fixtures/` come from
[apache/ossie](https://github.com/apache/ossie), also Apache-2.0; see [NOTICE](NOTICE).
