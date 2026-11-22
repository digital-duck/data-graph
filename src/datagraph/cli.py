"""`datagraph` (alias `dg`): validate, explore, export and profile an Ossie semantic model.

Exit codes: 0 ok, 1 the model has errors (validate / check / profile), 2 a usage or load error.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import click
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


@click.group()
@click.version_option(__version__, prog_name="datagraph")
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


@main.command()
@click.option("--sqlite", "db", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--name", default=None, help="Model name (default: the database file's stem).")
@click.option("-o", "--out", type=click.Path(dir_okay=False, path_type=Path), help="Write here instead of stdout.")
def draft(db: Path, name: str | None, out: Path | None) -> None:
    """An Ossie skeleton from a SQLite catalogue: datasets, fields, keys, joins. No descriptions or metrics."""
    from .sqlite import draft as do_draft
    text = "# Drafted by datagraph from a SQLite catalogue: add descriptions, ai_context and metrics.\n" + \
        yaml.safe_dump(do_draft(db, name), sort_keys=False, allow_unicode=True)
    if out:
        out.write_text(text, encoding="utf-8")
        click.echo(f"wrote {out}")
    else:
        click.echo(text)


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
