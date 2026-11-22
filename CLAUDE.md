# CLAUDE.md

data-graph: lineage, checks and profiling for Apache Ossie semantic models. Plan and status: `docs/DEV/plan.md`.

## Commands

```bash
pip install -e ".[dev]"
pytest                      # 78 tests; no network, no LLM; builds its own SQLite database
dgr validate tests/fixtures/ossie/tpcds_semantic_model.yaml
```

## Layout (`src/datagraf/`)

- `model.py`: reads 0.1.x (`semantic_model:` list, `dialects`) and 0.3.x (root model, `sql`) into dataclasses.
- `lineage.py`: `build()` makes a networkx DiGraph with ids `source:`, `column:`, `dataset:`, `field:`, `metric:`.
  Edges point from what is needed to what needs it. References come from sqlglot, by the spec's scoping rules.
  Unresolved references go into `G.graph["problems"]`; they are never guessed. Joins live in `G.graph["joins"]`, not as edges.
- `checks.py`: the bundled JSON Schema for the document's version, plus reference, join and gap checks.
- `export.py`: zinet `graph.yaml` (sources and columns are primitives, the model is the one application), or JSON.
- `sqlite.py`: `draft()` from a catalogue, `profile()` of the data, through dd-db (`SQLiteDB(path, read_only=True)`,
  i.e. `mode=ro` + `PRAGMA query_only`). Uses only `BaseDB` methods, so other dd-db adapters can follow.
- `ask.py`: the program half of the `ask` recipe: `context()` (the model narrowed by join paths), `check_sql()`, `report()`.
- `draft.py`: the program half of `ossie_draft`: `skeleton()`, `check()` (structure unchanged, metrics run), `write()`.
- `bench.py`: scoring answers by running them against reference queries (rows / superset / within / mentions).
- `recipes/<name>/`: the GraphLang recipes `ask` and `ossie_draft` (graph, prompts, Cedar policy, error codes).
  `steps.py` holds their SOLVE steps, run as `python3 -I -m datagraf.tool STEP`, with JSON on stdin and stdout; `run.py`
  runs a recipe through `graphlang.recipes` (default `claude_cli`, logs in `~/data-graf/logs`).
- `cli.py`: the `dgr` click group. Exit 0 ok, 1 model errors (or an unchecked answer), 2 usage.

## Rules

- Reuse the dd-* stack (dd-db, dd-llm via GraphLang, dd-config, dd-logging) rather than writing a local version; if a dd
  package lacks something, add it there.
- No LLM calls in the library. LLM work is GraphLang recipes, as in app-graph; tests drive them with a scripted model
  (an object with `generate()`), never a real one.
- A recipe step that writes is `risky: true` behind a Cedar GUARD; keep it that way when adding one.
- GraphLang rules that apply (from app-graph): a SOLVE subprocess runs in the recipe folder with only PATH set, so pass
  absolute paths; a loop variable `$loop.X` is read as `X`; the loop's index is not visible outside it.
- Databases are only ever opened read-only. Quote identifiers with `sqlite.q()`.
- `from __future__ import annotations` in every module.
- Fixtures under `tests/fixtures/` are verbatim copies from apache/ossie (see NOTICE); don't edit them.
