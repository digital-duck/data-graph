# Chinook benchmark

The 24 questions data-copilot was scored on in 2024 (`questions.yaml`), now scored by a program: each answer's SQL and a
reference query run on the same read-only database, and the rows are compared (`datagraf/bench.py`; the questions file
says how ties and two-reading questions are handled). Raw results are in `results/`, one JSON file per run.

```bash
dgr bench benchmarks/chinook/questions.yaml --sqlite chinook.sqlite3                      # claude_cli, graph scope
dgr bench benchmarks/chinook/questions.yaml --sqlite chinook.sqlite3 --llm ollama:llama3.2 --scope all
```

The database is data-copilot's `src/store/sql/sqlite/chinook/chinook.sqlite3` (not copied here).

## Results, 2026-11-22

Every answer went through the `ask` recipe: the semantic model as context, a SQL check (one read-only statement, real
tables and columns, run), and up to 2 repairs. *Scope* is what the model was shown:
- **graph:** the datasets the question names plus the join paths between them, on average 5.4 of the 11 datasets and
  about 2,800 characters;
- **all:** the whole model, 4,269 characters.

| Model | Semantic model | Scope | Passed | Model calls |
|---|---|---|---|---|
| claude-sonnet-5-5 (`claude_cli`) | hand-finished (`examples/`) | graph | **24/24** | 24 |
| claude-sonnet-5-5 | hand-finished | all | **24/24** | 24 |
| claude-sonnet-5-5 | drafted by `ossie_draft` (one call) | graph | **24/24** | 24 |
| llama3.2 3B (Ollama, local) | hand-finished | graph | 17/24, 17/24 (two runs) | 30, 31 |
| llama3.2 3B | hand-finished | all | 13/24, 16/24 (two runs) | 33, 33 |
| llama3.2 3B | drafted by `ossie_draft` | graph | 12/24 (one run) | 42 |

What the numbers say, and don't:
- **Chinook is easy for a current frontier model.** Sonnet 5.5 answered every question correctly on the first try, with
  no repairs, whether it saw the narrowed model, the whole model, or a model drafted by `ossie_draft`. The 2024 sheet's
  best models also reached 100% (graded by eye). This benchmark can't separate strong models.
- **For a small model, the graph scope did as well or better in both runs** (17 and 17, against 13 and 16), with prompts
  about 35% smaller. Two runs of a sampled model are too few to put a number on the gap.
- **The repair loop works live.** With llama3.2, 4 to 10 questions per run failed the check at first. Between 1 and 3 of
  them passed after a repair, and the rest ended as `failed`: the report says so, instead of showing SQL that does not run.
- **The check can't catch a wrong question.** Most of llama3.2's misses are SQL that is valid, read-only and runs, but
  answers something else. Examples: question 8 counts countries instead of invoices per country, and question 13 ranks
  tracks by revenue instead of price. Checking meaning needs the model's metrics in the SQL (next step in the plan), or
  a reviewer.
- **A hand-finished model helped the small model more than the drafted one** (17 against 12, one run). The drafted model
  passes every check, so the difference is in the wording (descriptions, synonyms, instructions).

The 2024 figures (graded by hand, data-copilot on vanna.ai, the same questions) are in `questions.yaml` under `baseline_2024`.
