# CLAUDE.md

data-graph: lineage, checks and profiling for Apache Ossie semantic models. Plan and status: `docs/DEV/plan.md`.

## Commands

```bash
pip install -e ".[dev]"
pytest                      # 44 tests; no network, no LLM; builds its own SQLite database
datagraph validate tests/fixtures/ossie/tpcds_semantic_model.yaml
```

## Layout (`src/datagraph/`)

- `model.py`: reads 0.1.x (`semantic_model:` list, `dialects`) and 0.3.x (root model, `sql`) into dataclasses.
- `lineage.py`: `build()` makes a networkx DiGraph with ids `source:`, `column:`, `dataset:`, `field:`, `metric:`.
  Edges point from what is needed to what needs it. References come from sqlglot, by the spec's scoping rules.
  Unresolved references go into `G.graph["problems"]`; they are never guessed. Joins live in `G.graph["joins"]`, not as edges.
- `checks.py`: the bundled JSON Schema for the document's version, plus reference, join and gap checks.
- `export.py`: zinet `graph.yaml` (sources and columns are primitives, the model is the one application), or JSON.
- `sqlite.py`: `draft()` from a catalogue, `profile()` of the data, through dd-db (`SQLiteDB(path, read_only=True)`,
  i.e. `mode=ro` + `PRAGMA query_only`). Uses only `BaseDB` methods, so other dd-db adapters can follow.
- `cli.py`: the `datagraph` / `dg` click group. Exit 0 ok, 1 model errors, 2 usage.

## Rules

- Reuse the dd-* stack (dd-db, dd-llm via GraphLang, dd-config, dd-logging) rather than writing a local version; if a dd
  package lacks something, add it there.
- No LLM calls in the library. LLM work will be GraphLang recipes (see the plan), as in app-graph.
- Databases are only ever opened read-only. Quote identifiers with `sqlite.q()`.
- `from __future__ import annotations` in every module.
- Fixtures under `tests/fixtures/` are verbatim copies from apache/ossie (see NOTICE); don't edit them.
