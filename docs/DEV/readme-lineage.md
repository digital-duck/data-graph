# Lineage: data-copilot → anadata → data-graph

*Written 2026-11-22. A codebase review of the two earlier data apps, and why the third one is built differently.*

Three attempts at the same goal: **let an analyst ask a database questions in plain language, and trust the answer.**

| | data-copilot | anadata | data-graph |
|---|---|---|---|
| Built | 2023 (this repository's history starts 2024-12-21; last commit 2025-06-23) | 2026-02-24 to 02-25 (6 commits) | 2026-11-22 (prototype) |
| Engine | vanna.ai, through the fork `ssadata` | its own `AnaData` class on the dd-* stack | Ossie semantic model + a lineage graph (zinet); GraphLang recipes planned |
| What the LLM sees | DDL, documentation and example question/SQL pairs, retrieved from ChromaDB | the same, retrieved through dd-embed / dd-vectordb | a semantic model: datasets, fields, metrics, joins, synonyms (planned) |
| Size | about 3,700 lines of Python, `utils.py` 1,474 | about 2,900 lines | about 1,000 lines of library + 44 tests |
| Tests | none | none | 44, no network, no LLM |

## 1. data-copilot (2023)

**What it is.** A Streamlit app "for data professionals, powered by vanna.ai". Pages:
- Configure: data source, vector store, LLM.
- Database: browse tables and data.
- KnowledgeBase: add DDL, documentation, question/SQL pairs and business terms.
- Ask-RAG: question → SQL → dataframe → Plotly chart.
- QA-Results: saved question/answer pairs.
- Evaluations: an LLM benchmark.
- Import from CSV, XLSX and SQLite.

**How it works.**
- `vanna_calls.py` (342 lines) builds one vanna class per vendor by mixing `ChromaDB_VectorStore` with a chat class: `MyVannaOpenAI`, `MyVannaGoogle`, `MyVannaAnthropic`, `MyVannaBedrockChat`, `MyVannaOllama`.
- It wraps every vanna call twice, `*_cached` (Streamlit cache) and `*_not_cached`: `generate_sql`, `run_sql`, `is_sql_valid`, `should_generate_chart`, `generate_plotly_code`, `get_plotly_figure`.
- Training is vanna's `vn.train(ddl=...)`, `vn.train(documentation=...)`, `vn.train(question=..., sql=...)`. "Add All DDL scripts" reads `sqlite_master` and trains each `CREATE TABLE`.
- Business terms come from a `bus_term` table (term, description, related tables, related columns), imported from CSV.
- `utils.py` (62 functions) holds the app's own SQLite metadata store: configuration, notes, Q&A results, generic upsert, select, update and delete helpers.
- Sample databases ship in `src/store/sql/sqlite/`: `chinook`, `imdb` (with `bus_term.csv`), `stocks`.

**What it got right.**
- The **knowledge-base idea**: schema, plus documentation, plus worked examples, plus business vocabulary. That is the context a model needs, and it is what a semantic layer standardizes.
- **Measurement.** 24 Chinook questions across ten models (gpt-4o, gpt-4, claude-3.5-sonnet, gemini-1.5-pro, qwen2.5, deepseek, llama3, gemma2, codegemma, mistral). Top models scored above 90%.
- **Local privacy** through Ollama.
- Q&A pairs kept: every answer can become a training example.

**What held it back.**
- **The engine was someone else's.** It used a vanna fork (`ssadata`) installed from source, with one mixin class per vendor. Each new provider or vector store meant a new class and a new pair of wrappers.
- **Code in the UI layer.** The logic sat in Streamlit pages and a 1,474-line `utils.py`, with no library a script or another tool could import, and no tests.
- **The SQL was trusted.**
  - `is_sql_valid` only parses.
  - `allow_llm_to_see_data=True` sends query results to the model.
  - `run_sql` executes whatever the model wrote, on a read-write connection.
  - `get_plotly_figure` `exec`s model-written Python.
- **The knowledge base was private.** Vectors sat in a ChromaDB folder. Nothing else could read them, diff them, review them in git, or check them against the database.

## 2. anadata (February 2026)

**What it is.** A rewrite of data-copilot onto the dd-* stack, without vanna:
- `dd-llm`: `UnifiedLLMProvider`, one class for every vendor;
- `dd-embed`, `dd-vectordb` (Chroma, FAISS, in-memory);
- `dd-db` (Postgres, DuckDB), `dd-cache`, `dd-config`, `dd-logging`.

It has three front ends over one engine: a Streamlit UI (five pages), a click CLI and a FastAPI service.

**How it works** (`src/anadata/core.py`, 527 lines; `prompts.py`, 92):
- `AnaData(config: dict)` builds the logger, the database, the LLM, the embedder, the vector store and the cache from `config.yaml`.
- `train(ddl= | documentation= | question=+sql=)` embeds the text and stores it in one collection, with `metadata["type"]` in {ddl, documentation, sql}. It mirrors the text to `.cache/training_data.jsonl`, so the knowledge base can be listed without the vector store's help.
- `generate_sql(question)`:
  1. similar question/SQL pairs, by vector search;
  2. **all** DDL, from the manifest;
  3. related documentation, by vector search;
  4. one prompt (`build_sql_system_prompt`: DDL → docs → examples → instructions);
  5. `extract_sql` strips the code fences.
- `run_sql`, `generate_plotly_code` with `get_plotly_figure` (`exec`), `generate_summary`, `ask_llm`.
- CLI: `ask`, `sql`, `run`, `train ddl|doc|example|add-db`, `kb list|remove|clear`. API: `/sql`, `/ask`, `/run`, `/training`, `/history`, `/tables`, `/schema`, `/config`.
- Configuration and history are plain files (`config.yaml`, `.cache/qa_history.jsonl`) instead of data-copilot's four SQLite tables.

**What it got right.**
- **Its own engine, and a small one.** One `AnaData` class replaces five vendor mixins and the doubled wrappers. Changing the provider, model, embedder or vector store is a config edit.
- **A library first.** The UI, CLI and API are thin over `AnaData`, and the plan already names using it as a node in a workflow ("Future: SPL-Flow Integration").
- **One retrieval lesson, learned and written down.** `get_related_ddl` returns *all* tables, not the most similar ones: "`invoice_items` has no overlap with 'best-selling albums' but is required for the JOIN". Vector similarity is a poor guide to which tables a query needs. **The schema is a graph, not a bag of documents.** This is the point data-graph starts from.
- Files over databases for configuration, history and the training manifest: easy to read, grep and diff.

**What it kept from data-copilot that still needs work.**
- **SQL still runs as written, and can write.**
  - `SQLiteDB.run_query` commits anything that is not `SELECT`/`WITH`/`PRAGMA`.
  - The `/run` endpoint runs any SQL it is sent.
  - `anadata-api` binds to `0.0.0.0` by default, so anyone on the network who can reach port 8000 could change the database.
  - **Fixed 2026-11-22:** anadata now uses dd-db's `SQLiteDB(read_only=True)` (new in dd-db 0.1.3), with `database.read_only` defaulting to `true`, and its own copy of the SQLite class is gone. Its DDL now includes `FOREIGN KEY` lines, which helps the model with joins. The API (`anadata-api`, `anadata serve`) now binds to `127.0.0.1` by default; `--host 0.0.0.0` serves the network on purpose. Still open: read-only for Postgres in dd-db.
- **`exec` of model-written Plotly code**, in the app's own process.
- **Validity is still "it parses"** (`sqlparse`). Nothing checks that the tables and columns exist, that the joins follow the foreign keys, or that "revenue" means what the business means by revenue.
- **The knowledge is still opaque text in a vector store.** DDL strings, documentation strings and example pairs carry no structure. There are no metrics, no join paths and no lineage, and nothing can say which example breaks when a column changes.
- **No tests.** It has a manual checklist (`readme-plan.md`, "Verification Checklist").
- **`get_schema` built SQL with an f-string** (`PRAGMA table_info({table_name})`), unquoted. Fixed by the same move to dd-db 0.1.3, which quotes identifiers.

## 3. Why data-graph now

Both apps put their effort into **retrieval**: finding the right text to paste into a prompt. Both treat the model's SQL as the answer. Three things have changed since then.

**1. There is now a standard for the knowledge base.** [Apache Ossie](https://ossie.apache.org/) (incubating; formerly Open Semantic Interchange) is a vendor-neutral YAML format. Salesforce, Snowflake, dbt Labs, Google and 50+ others back it. It covers exactly what data-copilot's knowledge base held, with structure:

| data-copilot / anadata knowledge base | Ossie semantic model |
|---|---|
| DDL strings | `datasets[]`: `source`, `fields[]` with `datatype`, `primary_key` |
| foreign keys, implied by the DDL | `relationships[]`: many-to-one, `from_columns` → `to_columns` |
| documentation strings | `description` on the model, dataset, field and metric |
| `bus_term` (term, description, tables, columns) | `metrics[]` with SQL expressions, and `ai_context.synonyms` |
| question/SQL pairs | `ai_context.examples` |

A model is a file. It lives in git, is reviewed like code, and is read by other tools: Snowflake, dbt MetricFlow, Salesforce, GoodData and Polaris already have converters. It is also checkable: data-graph validates it against the official schema and against itself.

**2. The schema is a graph, and the graph does the work retrieval could not.** anadata found that similarity search picks the wrong tables. data-graph doesn't search for tables at all; it follows edges:
- **lineage:** a metric down to its source columns;
- **impact:** a column up to every metric it feeds;
- **join paths:** whether a metric over two datasets can be computed at all.

These are exact answers, from a program, before any model is asked. The graph is zinet's format, so it can be drawn, sliced and diffed with the tools the rest of the family uses.

**3. The model's SQL can now be checked, not trusted.** The stack has grown the pieces both apps lacked:
- **GraphLang** runs LLM steps as recipes, with a call meter, checkpoints, and "draft, validate, repair" loops (app-graph's `compile_blueprint`). It also puts Cedar guards before risky steps, and runs commands in containers.
- **The semantic model is the validator.** Generated SQL must name real metrics and fields, and join along declared relationships.
- **Databases are opened read-only**, by construction. `datagraph profile` already runs each metric's SQL against the data, so a metric that doesn't run is found before anyone relies on it. On Chinook, the two ways of computing revenue agree (2328.6 each).

**What carries forward.**
- The **Chinook database and the 24-question benchmark**, so a data-graph text-to-SQL recipe can be scored against data-copilot's and anadata's numbers on the same questions.
- **anadata's dd-llm configuration** (one provider string), through GraphLang, which already uses dd-llm.
- **The question/SQL history:** saved pairs become `ai_context.examples` in the model, reviewed in git instead of hidden in a vector store.
- The **Streamlit front end**, later, as a thin face over the library, as anadata already does it.

**What changes.**

| data-copilot / anadata | data-graph |
|---|---|
| retrieve text, prompt, run what comes back | read the model, check the question's terms against it, generate SQL that must cite metric and field ids, check it, run it read-only, show it |
| knowledge base in a vector store | a semantic model file in git, in a standard format |
| "valid" = it parses | valid = schema, references, join paths, and it runs against the data |
| read-write connection | read-only, always |
| `exec` of model-written chart code | planned: a chart *spec* (data, not code), or code run in GraphLang's container |
| manual checklist | tests on Apache's own example models and a database the tests build |

**Where each stands.**
- **data-copilot** is the record of the idea and its benchmark.
- **anadata** is the clean dd-* engine. Its configuration, prompts and front ends are worth reusing, and its retrieval code is the part data-graph replaces with the graph.
- **data-graph** is the version where the knowledge is a checked, shareable model, and the LLM drafts and asks inside guard rails. Next steps are in [plan.md](plan.md): the recipes, multi-dataset profiling, connectors and importers.
