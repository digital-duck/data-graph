You are finishing an Apache Ossie semantic model (YAML, spec version 0.3.0.dev0) drafted from a database catalogue.
A semantic model tells people and programs what the data means: what each dataset is, what its fields mean, and the
business metrics computed from it.

The skeleton (datasets, fields, keys and relationships, read from the catalogue):
```yaml
{{in_skeleton}}
```

Sample rows from each table:
{{in_samples}}

Return the complete model, with these additions only:
- `description` on the model, on every dataset, and on every field whose meaning is not obvious from its name.
- `ai_context: {synonyms: [...]}` on datasets, fields and metrics that people call by other names (e.g. invoices: order, sale).
- `ai_context: {instructions: ...}` on the model: one or two sentences on how to read it (what revenue is, which table is the fact).
- `dimension: {is_time: false}` on fields people group or filter by (names, categories, places); `dimension: {is_time: true}` on dates.
- `metrics:` on the datasets that hold facts: 2 to 6 dataset-scoped metrics in total, each with `name`, `expression: {sql: ...}`,
  `datatype` (Integer, Decimal or Float) and `description`. A dataset-scoped metric's SQL is an aggregate over that dataset's
  own fields, unqualified: `SUM(Total)`, `COUNT(InvoiceId)`, `SUM(UnitPrice * Quantity)`.
- Optionally a root `metrics:` list of model-scoped metrics that combine datasets, naming `dataset.field`
  (e.g. `SUM(invoices.Total) / COUNT(DISTINCT customers.CustomerId)`).

Do not change anything the skeleton already says: keep every dataset, `source`, `primary_key`, field `name` and
`expression`, and every relationship exactly as they are. Do not add or remove datasets, fields or relationships.

Output ONLY the YAML document, starting with `version: "0.3.0.dev0"`. No preamble, no markdown fences.
