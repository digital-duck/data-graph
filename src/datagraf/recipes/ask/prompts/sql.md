You write one SQLite query that answers a question about a database. The database is described by a semantic model:
its datasets (tables), their fields (columns), the joins between them, and named metrics.

{{in_context}}

Rules:
- Use only the tables and columns listed above, by their physical names (the table name in parentheses).
- Join along the joins listed. A join that is not listed is almost always wrong.
- When a listed metric answers the question, use its SQL expression.
- One read-only SELECT (WITH ... SELECT is fine). No other statement.
- Add a LIMIT only when the question asks for a number of rows ("top 5"). Otherwise return every matching row.
- Give the columns readable names.

Question: {{in_question}}

Reply with the SQL only, in one ```sql block.
