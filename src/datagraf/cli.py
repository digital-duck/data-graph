"""`dgr`: validate, explore, export and profile an Ossie semantic model; draft one and ask it questions (GraphLang recipes).

Exit codes: 0 ok, 1 the model has errors (validate / check / profile), 2 a usage or load error.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from zinet.click_helpers import HELP_OPTION_NAMES, FullHelpGroup
import yaml

from . import __version__
from . import checks as C
from . import lineage as L
from .model import ModelError, load, read_document

MODEL = click.argument("model", type=click.Path(exists=True, dir_okay=False, path_type=Path))
PICK = click.option("--name", "model_name", default=None, help="Which model, when a 0.1.x file holds several.")


def _load(path: Path, name: str | None):
    try:
        m = load(path, name)
    except ModelError as e:
        raise click.ClickException(str(e)) from None
    return m, L.build(m)


def _print_problems(problems: list[dict]) -> None:
    for p in problems:
        where = f" {p['node']}" if p["node"] else ""
        click.echo(f"  {p['severity']:7} {p['code']}{where}: {p['message']}")


@click.group(cls=FullHelpGroup, context_settings={"help_option_names": HELP_OPTION_NAMES})
@click.version_option(__version__, prog_name="dgr")
def main() -> None:
    """Lineage, checks and profiling for Apache Ossie semantic models."""


@main.command()
@MODEL
@PICK
@click.option("--strict", is_flag=True, help="Fail on warnings too.")
def validate(model: Path, model_name: str | None, strict: bool) -> None:
    """The JSON Schema for the file's Ossie version, then references, joins and gaps."""
    try:
        doc = read_document(model)
    except ModelError as e:
        raise click.ClickException(str(e)) from None
    problems = C.schema_problems(doc)
    m, G = _load(model, model_name)
    problems += C.model_problems(m, G)
    errors, warnings = C.summarize(problems)
    click.echo(f"{model}: {m.name} (Ossie {m.version or '?'}): {errors} error(s), {warnings} warning(s)")
    _print_problems(problems)
    sys.exit(1 if errors or (strict and warnings) else 0)


@main.command()
@MODEL
@PICK
def check(model: Path, model_name: str | None) -> None:
    """References, joins and gaps only (no schema)."""
    m, G = _load(model, model_name)
    problems = C.model_problems(m, G)
    errors, warnings = C.summarize(problems)
    click.echo(f"{m.name}: {errors} error(s), {warnings} warning(s)")
    _print_problems(problems)
    sys.exit(1 if errors else 0)


@main.command()
@MODEL
@PICK
def show(model: Path, model_name: str | None) -> None:
    """Datasets, fields, metrics and joins."""
    m, G = _load(model, model_name)
    click.echo(f"{m.name}  (Ossie {m.version or '?'})")
    if m.description:
        click.echo(f"  {m.description}")
    n_fields = sum(len(d.fields) for d in m.datasets)
    click.echo(f"  {len(m.datasets)} datasets, {n_fields} fields, {len(m.all_metrics())} metrics, {len(m.relationships)} joins")
    for d in m.datasets:
        dims = sum(f.is_dimension for f in d.fields)
        click.echo(f"\n  dataset {d.name}  <- {d.source}   ({len(d.fields)} fields, {dims} dimensions"
                   + (f", key {', '.join(d.primary_key)}" if d.primary_key else "") + ")")
        for mm in d.metrics:
            click.echo(f"    metric {mm.name} = {mm.sql}")
    if m.metrics:
        click.echo("\n  model metrics")
        for mm in m.metrics:
            click.echo(f"    {mm.name} = {mm.sql}")
    if m.relationships:
        click.echo("\n  joins (many -> one)")
        for r in m.relationships:
            click.echo(f"    {r.from_dataset}({', '.join(r.from_columns)}) -> {r.to_dataset}({', '.join(r.to_columns)})")


def _node(G, name: str) -> str:
    try:
        return L.resolve(G, name)
    except KeyError as e:
        raise click.ClickException(e.args[0]) from None


@main.command()
@MODEL
@click.argument("name")
@PICK
def lineage(model: Path, name: str, model_name: str | None) -> None:
    """What a metric or field is made of, down to source columns. NAME is `total_sales`, `store_sales.ss_quantity`, or an id."""
    _, G = _load(model, model_name)
    click.echo("\n".join(L.outline(G, _node(G, name), up=True)))
    cols = sorted(n for n in L.upstream(G, _node(G, name)) if G.nodes[n]["kind"] == "column")
    click.echo(f"\n{len(cols)} source column(s): {', '.join(c[len('column:'):] for c in cols) or '-'}")


@main.command()
@MODEL
@click.argument("name")
@PICK
def impact(model: Path, name: str, model_name: str | None) -> None:
    """What depends on a column, field, dataset or metric: what a change to it can break."""
    _, G = _load(model, model_name)
    node = _node(G, name)
    click.echo("\n".join(L.outline(G, node, up=False)))
    hit = sorted(n for n in L.downstream(G, node) if G.nodes[n]["kind"] == "metric" and n != node)
    click.echo(f"\n{len(hit)} metric(s) affected: {', '.join(h[len('metric:'):] for h in hit) or '-'}")


@main.command()
@MODEL
@PICK
@click.option("--format", "fmt", type=click.Choice(["zinet", "json"]), default="zinet", show_default=True)
@click.option("-o", "--out", type=click.Path(dir_okay=False, path_type=Path), help="Write here instead of stdout.")
def export(model: Path, model_name: str | None, fmt: str, out: Path | None) -> None:
    """The lineage graph as zinet graph.yaml or JSON."""
    _, G = _load(model, model_name)
    if fmt == "json":
        from .export import to_json
        text = to_json(G)
    else:
        from zinet import to_yaml
        from .export import to_zinet
        text = to_yaml(to_zinet(G))
    if out:
        out.write_text(text, encoding="utf-8")
        click.echo(f"wrote {out}")
    else:
        click.echo(text)


@main.command()
@MODEL
@PICK
@click.option("--format", "fmt", type=click.Choice(["html", "mermaid", "ascii", "svg", "png"]), default="html", show_default=True)
@click.option("-o", "--out", type=click.Path(dir_okay=False, path_type=Path), help="Required for html, svg and png.")
def render(model: Path, model_name: str | None, fmt: str, out: Path | None) -> None:
    """Draw the lineage graph through zinet (svg and png need Graphviz)."""
    from zinet import render as zrender
    from .export import to_zinet
    _, G = _load(model, model_name)
    if fmt in ("html", "svg", "png") and out is None:
        raise click.UsageError(f"--format {fmt} needs -o FILE")
    try:
        data = zrender(to_zinet(G), fmt)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from None
    if out:
        out.write_bytes(data)
        click.echo(f"wrote {out}")
    else:
        click.echo(data.decode("utf-8"))


LLM = click.option("--llm", default=None, metavar="ADAPTER[:MODEL]",
                   help="dd-llm adapter and model for the recipe, e.g. claude_cli:claude-sonnet-5-5 (default: claude_cli).")


def _recipe(name: str, inputs: dict, llm: str | None, max_llm_calls: int):
    from .run import DEFAULT_LLM, RecipeError, run_recipe
    try:
        return run_recipe(name, inputs, llm=llm or DEFAULT_LLM, max_llm_calls=max_llm_calls)
    except RecipeError as e:
        raise click.ClickException(f"recipe {name} stopped: {e}") from None


@main.command()
@click.option("--sqlite", "db", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--name", default=None, help="Model name (default: the database file's stem).")
@click.option("-o", "--out", type=click.Path(dir_okay=False, path_type=Path), help="Write here instead of stdout.")
@LLM
@click.option("--enrich", is_flag=True, help="Also have a model write descriptions, synonyms and metrics (the ossie_draft recipe; needs -o).")
@click.option("--overwrite", is_flag=True, help="With --enrich: replace an existing file.")
@click.option("--max-repairs", default=2, show_default=True)
def draft(db: Path, name: str | None, out: Path | None, llm: str | None, enrich: bool, overwrite: bool, max_repairs: int) -> None:
    """An Ossie model from a SQLite catalogue: datasets, fields, keys, joins. With --enrich (or --llm), a model adds
    descriptions, synonyms and metrics, and a program checks them (structure unchanged, schema valid, every metric runs)."""
    if enrich or llm:
        if out is None:
            raise click.UsageError("--enrich needs -o FILE")
        run = _recipe("ossie_draft", {"db": str(db.resolve()), "output_path": str(out.resolve()), "name": name or "",
                                      "overwrite": "yes" if overwrite else "no", "max_repairs": max_repairs}, llm, 1 + max_repairs)
        click.echo(run.outputs.get("validation", ""))
        if run.exit:
            click.echo(f"not written ({run.exit}, {run.error_code}); {run.llm_calls} model call(s)", err=True)
            sys.exit(1)
        click.echo(f"wrote {run.outputs['path']} ({run.llm_calls} model call(s))")
        return
    from .sqlite import draft as do_draft
    text = "# Drafted by data-graf (dgr) from a SQLite catalogue: add descriptions, ai_context and metrics.\n" + \
        yaml.safe_dump(do_draft(db, name), sort_keys=False, allow_unicode=True)
    if out:
        out.write_text(text, encoding="utf-8")
        click.echo(f"wrote {out}")
    else:
        click.echo(text)


@main.command()
@MODEL
@click.argument("question")
@click.option("--sqlite", "db", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@LLM
@click.option("--scope", type=click.Choice(["graph", "all"]), default="graph", show_default=True,
              help="graph: show the model only the datasets the question needs (by join paths); all: the whole model.")
@click.option("--max-repairs", default=2, show_default=True)
@click.option("--rows", "row_limit", default=20, show_default=True, help="Rows shown.")
def ask(model: Path, question: str, db: Path, llm: str | None, scope: str, max_repairs: int, row_limit: int) -> None:
    """Answer QUESTION with SQL that a program checked against the model and ran read-only (the ask recipe)."""
    run = _recipe("ask", {"question": question, "model": str(model.resolve()), "db": str(db.resolve()), "scope": scope,
                          "row_limit": row_limit, "max_repairs": max_repairs}, llm, 1 + max_repairs)
    click.echo(run.outputs["report"])
    click.echo(f"\n({run.llm_calls} model call(s))", err=True)
    sys.exit(0 if run.outputs.get("status") == "checked" else 1)


@main.command()
@click.argument("questions", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--sqlite", "db", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Default: the file's `database`, beside it.")
@click.option("--model", "model_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), help="Default: the file's `model`.")
@LLM
@click.option("--scope", type=click.Choice(["graph", "all"]), default="graph", show_default=True)
@click.option("--only", default="", help="Question ids, e.g. 1,4,17.")
@click.option("--max-repairs", default=2, show_default=True)
@click.option("-o", "--out", type=click.Path(dir_okay=False, path_type=Path), help="Write the results (JSON) here.")
def bench(questions: Path, db: Path | None, model_path: Path | None, llm: str | None, scope: str, only: str, max_repairs: int,
          out: Path | None) -> None:
    """Run `ask` on every question of a benchmark file and score each answer by running it against reference queries."""
    import json as _json
    import time
    from . import bench as B
    from .sqlite import connect
    doc = B.load_questions(questions)
    db = db or (doc["_dir"] / doc["database"])
    model_path = Path(model_path or (doc["_dir"] / doc["model"])).resolve()   # the recipe runs in its own folder
    if not Path(db).is_file():
        raise click.UsageError(f"no database at {db}; pass --sqlite")
    _load(model_path, None)                                                    # a bad model stops here, not 24 times
    conn = connect(db)
    wanted = {int(x) for x in only.split(",") if x.strip()}
    results = []
    for q in doc["questions"]:
        if wanted and q["id"] not in wanted:
            continue
        t0 = time.time()
        try:
            run = _recipe("ask", {"question": q["q"], "model": str(model_path), "db": str(Path(db).resolve()), "scope": scope,
                                  "row_limit": 5, "max_repairs": max_repairs}, llm, 1 + max_repairs)
            sql, status, calls = run.outputs.get("sql", ""), run.outputs.get("status", ""), run.llm_calls
        except click.ClickException as e:
            sql, status, calls = "", f"stopped: {e.message}", 0
        s = B.score(q, sql, conn)
        results.append({"id": q["id"], "question": q["q"], "passed": s["passed"], "status": status, "calls": calls,
                        "seconds": round(time.time() - t0, 1), "rows": s["rows"], "error": s["error"], "sql": sql})
        click.echo(f"{q['id']:>3} {'PASS' if s['passed'] else 'FAIL'}  {status:8} {calls} call(s) {results[-1]['seconds']:>5}s  {q['q'][:70]}")
    n, ok = len(results), sum(r["passed"] for r in results)
    calls = sum(r["calls"] for r in results)
    click.echo(f"\n{ok}/{n} passed ({100 * ok / max(n, 1):.1f}%), {calls} model calls, scope {scope}, llm {llm or 'claude_cli'}")
    if doc.get("baseline_2024"):
        click.echo("2024 baseline (data-copilot, graded by hand): " + ", ".join(f"{k} {v}" for k, v in doc["baseline_2024"].items()))
    if out:
        out.write_text(_json.dumps({"questions": str(questions), "llm": llm or "claude_cli", "scope": scope, "passed": ok, "total": n,
                                    "model_calls": calls, "results": results}, indent=2), encoding="utf-8")
        click.echo(f"wrote {out}")


@main.command()
@MODEL
@click.option("--sqlite", "db", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@PICK
@click.option("--json", "as_json", is_flag=True, help="Print the profile as JSON.")
def profile(model: Path, db: Path, model_name: str | None, as_json: bool) -> None:
    """Profile the data behind a model, read-only: rows, nulls, distinct values, ranges, dataset metric values."""
    from .sqlite import profile as do_profile
    m, _ = _load(model, model_name)
    try:
        prof = do_profile(m, db)
    except FileNotFoundError as e:
        raise click.ClickException(str(e)) from None
    failed = 0
    if as_json:
        click.echo(json.dumps(prof, indent=2, default=str))
    for d in prof["datasets"]:
        failed += bool(d.get("error")) + sum(bool(x.get("error")) for x in d["fields"] + d["metrics"])
        if as_json:
            continue
        if d.get("error"):
            click.echo(f"\n{d['name']}: ERROR {d['error']}")
            continue
        click.echo(f"\n{d['name']}  ({d['table']}, {d['rows']} rows)")
        for f in d["fields"]:
            if f.get("error"):
                click.echo(f"  {f['name']:28} ERROR {f['error']}")
            else:
                nulls = "" if f["nulls"] is None else f"{f['nulls']:>8} null"
                click.echo(f"  {f['name']:28} {f['distinct']:>8} distinct {nulls}   {f['min']!s:.20} .. {f['max']!s:.20}")
        for me in d["metrics"]:
            click.echo(f"  metric {me['name']:21} " + (f"ERROR {me['error']}" if me.get("error") else f"= {me['value']}"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
