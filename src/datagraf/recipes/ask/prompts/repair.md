Your SQLite query for a question was checked by a program and rejected.

{{in_context}}

Question: {{in_question}}

Your query:
```sql
{{in_sql}}
```

The check says: {{in_error}}

Write the query again so that it passes: only the tables and columns listed, joined along the joins listed, one
read-only SELECT that finishes (every recursive CTE stopped by a WHERE on its recursive part, every join with its ON
condition, totals aggregated in SQL). If the check says the query was stopped, do not resubmit it with small changes:
find what makes it run without end, or over too many rows, and remove that. Reply with the SQL only, in one ```sql block.
