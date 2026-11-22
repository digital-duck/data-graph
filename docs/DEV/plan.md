# data-graph: plan

*Started 2026-11-22. A prototype; this file records what it is for, what is built, and what comes next.*

## What it is

data-graph reads a **semantic layer** written in [Apache Ossie](https://ossie.apache.org/) (incubating; formerly Open
Semantic Interchange, OSI) and turns it into a **lineage graph**. Ossie is an open YAML/JSON format for datasets, fields,
metrics and the joins between datasets. In the graph:
- physical tables and columns sit at the bottom;
- the datasets and fields that read them sit above;
- the metrics computed from those fields sit at the top.

With the graph you can:
- ask what a metric is made of (upstream) and what a change to a column breaks (downstream);
- check the model for gaps;
- profile the data behind it, read-only;
- draw it, and export it to zinet's `graph.yaml`;
- later, draft and query it with an LLM through GraphLang recipes.

It is a sibling of app-graph, work-graph and concept-book, built on the shared stack: **zinet** for the graph format and
drawing, **dd-db** for database access (read-only), **GraphLang** (over **dd-llm**) for the LLM steps; **dd-config** and
**dd-logging** come in with connection settings and recipe runs. It is its own package rather than a work-graph area because profiling and running SQL touch the data, which needs
credentials and connectors. work-graph stays a files-only knowledge layer and will get a thin `data` area whose processor
calls data-graph's reader (see zinets `docs/graph-vision/README-graflang-roadmap.md`, domain 6).

## Ossie, as read here

Checked against the repository (`github.com/apache/ossie`, main at `d4dca4e9f81c`, 2026-11-22) and its `osi-0.1.1-rc1` tag.

| | 0.1.1 (the released spec, 2025-12-11) | 0.3.0.dev0 (current draft, used by the repository's examples) |
|---|---|---|
| document | `version`, `semantic_model: [ {name, datasets, relationships, metrics, ...} ]`, a list | one model at the root: `version`, `name`, `datasets`, `relationships`, `metrics` |
| expression | `{dialects: [{dialect: ANSI_SQL, expression: ...}]}` | `{sql: ..., dialects: [...]}`; `sql` is authoritative (`OSSIE_SQL_2026`) |
| field type | none | `datatype` (String, Integer, Decimal, Float, Boolean, Date, Time, DateTime, DateTimeTz, Opaque) |
| schema | `core-spec/osi-schema.json` | `core-spec/ossie-schema.json` |

Both are read; both schemas ship in `datagraf/schemas/` (Apache-2.0, see `NOTICE`). An expression's text is `sql`, else
the `OSSIE_SQL_2026` or `ANSI_SQL` entry of `dialects`, else nothing: a model in another dialect is opaque, and its
lineage cannot be read from SQL.

**Reference rules** (spec 0.3, "Metric references"):
- a field's SQL names columns of its dataset's `source`;
- a dataset-scoped metric names that dataset's fields, unqualified;
- a model-scoped metric names `dataset.field`, or `dataset.metric` for a dataset-scoped metric (as in `SUM(store_sales.total_quantity)`);
- relationships are many-to-one: `from` is the many side, `to` the one side, with `from_columns` matched to `to_columns`.

## The graph

Node ids carry their kind, so names never collide:

| id | kind | from |
|---|---|---|
| `source:<table>` | source | a dataset's `source` |
| `column:<table>.<col>` | column | a column a field's SQL names |
| `dataset:<name>` | dataset | `datasets[]` |
| `field:<dataset>.<name>` | field | `datasets[].fields[]` |
| `metric:<dataset>.<name>` | metric (dataset-scoped) | `datasets[].metrics[]` |
| `metric:<name>` | metric (model-scoped) | `metrics[]` |

Edges point from what is needed to what needs it (zinet's convention). `reads` goes source → dataset, `contains` goes
dataset → field, `derives` goes column → field, and `computes` goes field or metric → metric. Relationships are not
dependencies: they are kept beside the graph as many-to-one joins, used for join-path checks and drawn as a separate edge kind.

## Built in this prototype (0.1.0.dev0)

| Command | What |
|---|---|
| `dgr validate MODEL` | the JSON Schema for the document's version, then the reference checks below; exit 1 on an error |
| `dgr show MODEL` | datasets, fields, metrics and joins, counted and listed |
| `dgr lineage MODEL NAME` | what a metric or field is made of, down to source columns |
| `dgr impact MODEL NAME` | what depends on a column, field or dataset |
| `dgr check MODEL` | gaps: unknown references, relationships to undeclared datasets or fields, a model-scoped metric over datasets with no join path, metrics without description, datasets with no relationship, SQL that does not parse |
| `dgr export MODEL --format zinet\|json` | zinet `graph.yaml` (sources and columns are primitives, the rest concepts, the model an application), or the graph as JSON |
| `dgr render MODEL --format html\|mermaid\|ascii\|svg\|png` | drawn through zinet (svg and png need Graphviz) |
| `dgr draft --sqlite DB` | an Ossie skeleton from a SQLite catalogue, with no model: tables become datasets, columns fields (with `datatype`), the primary key, foreign keys become relationships. Descriptions and metrics are left for a person or the planned `ossie_draft` recipe |
| `dgr profile MODEL --sqlite DB` | read-only (`mode=ro`, `query_only`): rows per dataset; per field the non-null and distinct counts, min and max; each dataset-scoped metric evaluated over its dataset's fields, so a metric whose SQL does not run is found before anyone relies on it |

Tested on the repository's three example models (`tpcds_semantic_model`, `flights`, `sap_business_partner`), the
0.1.1 TPC-DS model, `examples/chinook.semantic_model.yaml` (drafted from data-copilot's Chinook database, then finished by
hand), and a small `shop` model with its own SQLite database built by the tests: 44 tests.

## From data-copilot (2023)

[data-copilot](https://github.com/digital-duck/data-copilot) is a Streamlit app on vanna.ai. It answers questions about a
database with RAG text-to-SQL, Python plot code and saved Q&A results. It reached above 90% on a 24-question Chinook
benchmark across ten models. What carries over:

| data-copilot | data-graph |
|---|---|
| knowledge base: DDL, documentation, question/SQL pairs, in ChromaDB | the Ossie model: datasets and fields (the DDL), `description`, `ai_context.examples` (question/SQL pairs). A standard file instead of a private vector store |
| business terms (`bus_term`: term, description, related tables, related columns) | Ossie `metrics` and `ai_context.synonyms`: the semantic layer is the standard home for business terms (planned: `datagraf import-terms bus_term.csv`) |
| import CSV / XLSX / SQLite into a database | `draft --sqlite` (built); CSV import is next |
| the Chinook database and its 24 questions | the benchmark for the planned text-to-SQL recipe, which can then be compared with vanna's RAG on the same questions |
| LLM-generated SQL run as is (`is_sql_valid` only parses) | SQL checked against the model's metric and field ids, then run read-only, then shown |

## Next

1. **Recipes (GraphLang).**
   - `ossie_draft`: fill in the skeleton's descriptions, synonyms and metrics from the data and the docs. It uses "draft, validate, repair", with `validate` as the check, as app-graph's `compile_blueprint` does.
   - `ask`: text to SQL that must cite metric and field ids, checked by a program. The SQL is compiled from the semantic layer and run read-only before it is shown. Benchmarked on Chinook against data-copilot.
2. **Model-scoped metrics in `profile`.** Compile the joins from the relationships (many-to-one, from the fact outward), per `core-spec/relational_semantics.md`.
3. **Connectors beyond SQLite**, through dd-db's adapters (DuckDB, Postgres, Snowflake, BigQuery, ...): `draft` and `profile`
   already use only `BaseDB` methods. Each adapter needs a read-only mode first (dd-db 0.1.3 added it for SQLite; DuckDB has
   one), and `TableSchema.foreign_keys` filled in. Connection settings through dd-config.
4. **Importers.** `bus_term.csv`, a dbt project (models and the MetricFlow semantic models, which Ossie already converts), and CSV files.
5. **work-graph's `data` area.** The pack plus an `ossie` processor that calls `datagraf.load` and `datagraf.lineage`.
6. **Ossie's ontology documents** (`*.ontology.yaml`, `*.mapping.yaml`), after the spec settles.
