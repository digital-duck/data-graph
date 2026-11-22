# data-graph

Lineage, checks and profiling for semantic layers written in [Apache Ossie](https://ossie.apache.org/) (incubating;
formerly Open Semantic Interchange). An Ossie model names the datasets, fields, metrics and joins of a business's data;
data-graph turns one into a graph and answers questions about it:

- **lineage:** what a metric is made of, down to source columns;
- **impact:** which metrics a change to a column, field or dataset touches;
- **checks:** unknown references, joins to undeclared datasets or fields, metrics over datasets with no join path, gaps;
- **profiling:** read-only, against the data behind the model;
- **drawing and export:** through [zinet](https://github.com/digital-duck/zinet).

It is part of the digital-duck family: zinet for the graph format and drawing, dd-db for database access, [GraphLang](https://github.com/digital-duck/graflang)
for the LLM steps (the `ask` and `ossie_draft` recipes), beside app-graph, work-graph and concept-book. Prototype: version 0.1.0.dev1. See
[docs/DEV/plan.md](docs/DEV/plan.md) for the design and what comes next.

## Install

```bash
pip install data-graf       # once published; Python 3.11+
pip install -e ".[dev]"     # from source; registers `dgr`
pytest
```

Names: the repository is `data-graph`; the PyPI package is `data-graf`, the import is `datagraf` and the command is `dgr`.
`datagraph` is already the import and command name of an unrelated package on PyPI (`datagraph-core`), and "graf"
matches GraphLang's `graf`.

## Use

```bash
dgr validate model.yaml              # JSON Schema for its Ossie version, then references and gaps (exit 1 on an error)
dgr show model.yaml                  # datasets, fields, metrics, joins
dgr lineage model.yaml total_sales   # upstream, to source columns
dgr impact model.yaml store_sales.ss_ext_sales_price   # downstream: the metrics it feeds
dgr check model.yaml                 # references and gaps, without the schema
dgr export model.yaml --format zinet -o lineage.graph.yaml   # or --format json
dgr render model.yaml -o lineage.html                       # or --format mermaid / ascii / svg / png
dgr draft --sqlite shop.db -o shop.semantic_model.yaml      # a skeleton from a SQLite catalogue
dgr profile model.yaml --sqlite shop.db                     # rows, nulls, distinct, ranges, metric values
```

With a model (GraphLang recipes; `--llm ADAPTER[:MODEL]`, default `claude_cli`; `ollama:llama3.2` runs locally):

```bash
dgr draft --sqlite shop.db --enrich -o shop.semantic_model.yaml     # ossie_draft: the skeleton, then descriptions,
                                                                    # synonyms and metrics, checked before it is written
dgr ask model.yaml "top 5 customers by money spent" --sqlite shop.db   # ask: SQL checked against the model, run read-only
dgr bench benchmarks/chinook/questions.yaml --sqlite chinook.sqlite3   # the 24 Chinook questions, scored by running them
```

`ask` shows the model only the datasets the question needs: the ones it names, plus the join paths between them (`--scope
all` shows everything). Its SQL must be one read-only statement over real tables and columns, and it must run; a failure
goes back to the model up to `--max-repairs` times, and a report says `CHECKED` or `FAILED` (a program decides which).
`ossie_draft` may add descriptions, synonyms, dimensions and metrics, but not change what the catalogue says; every
metric must run on the data, and the write sits behind a Cedar policy (an absolute path under your home or the temporary
folder, never over an existing file unless asked). Results on Chinook are in [benchmarks/chinook](benchmarks/chinook/README.md).

A name is a metric (`total_sales`), a dataset-scoped metric or field (`store_sales.total_quantity`), a dataset, or a node
id (`column:tpcds.public.store_sales.ss_quantity`).

Both Ossie shapes are read: the released 0.1.1 (`semantic_model:` list, `dialects` expressions) and the 0.3.0 draft
(one model at the root, `sql` expressions). Each is checked against its own bundled schema.

`examples/chinook.semantic_model.yaml` is the Chinook sample database (the one
[data-copilot](https://github.com/digital-duck/data-copilot) used) as a finished model: drafted with `dgr draft`,
then given descriptions, synonyms and metrics by hand.

## From Python

```python
import datagraf as dg

model = dg.load("tpcds_semantic_model.yaml")
G = dg.build(model)                                   # networkx DiGraph: source -> column -> field -> metric
up = dg.upstream(G, dg.resolve(G, "customer_lifetime_value"))
problems = dg.model_problems(model, G)
```

## License

Apache-2.0. The Ossie schemas in `src/datagraf/schemas/` and the example models in `tests/fixtures/` come from
[apache/ossie](https://github.com/apache/ossie), also Apache-2.0; see [NOTICE](NOTICE).
