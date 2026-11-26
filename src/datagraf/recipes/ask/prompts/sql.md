You write one SQLite query that answers a question about a database. The database is described by a semantic model:
its datasets (tables), their fields (columns), the joins between them, and named metrics.

{{in_context}}

Rules:
- Use only the tables and columns listed above, by their physical names (the table name in parentheses).
- Join along the joins listed. A join that is not listed is almost always wrong.
- When a listed metric answers the question, use its SQL expression.
- One read-only SELECT (WITH ... SELECT is fine). No other statement.
- The query must finish. It is stopped after a time limit, and only its first rows are read, so:
  - Avoid WITH RECURSIVE unless the question needs it (a hierarchy, such as who reports to whom). A recursive CTE must
    have a stop condition in the WHERE of its recursive part, one that is certain to become false (`WHERE depth < 20`,
    or following a key to a row that has no parent). Never write `SELECT ... UNION ALL SELECT ... FROM itself` without one.
  - Give every join its condition (JOIN ... ON). A join without one multiplies the rows of both tables.
  - When the question asks for a total, a count or an average, aggregate in SQL; do not list the rows to be added up.
- Add a LIMIT only when the question asks for a number of rows ("top 5"). Otherwise return every matching row.
- Give the columns readable names.

Question: {{in_question}}

Reply with the SQL only, in one ```sql block.
