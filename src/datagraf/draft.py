"""The deterministic half of the `ossie_draft` recipe: a skeleton from the catalogue, the check a model's enrichment must
pass, and the write.

The model may add descriptions, `ai_context`, `dimension` flags and metrics. It may not change what the catalogue says:
datasets, sources, fields, field expressions, keys and relationships stay exactly as drafted. Every dataset-scoped metric
must run against the data (read-only), and the whole document must pass the schema and the model checks.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from . import checks as C
from . import lineage as L
from .limits import select
from .model import ModelError, _model, raw_models
from .sqlite import connect, draft as catalogue_draft, profile, q

HEADER = ("# Drafted by data-graf (dgr) from a database catalogue; descriptions, ai_context and metrics written by a model\n"
          "# and checked by a program (schema, references, structure unchanged, every dataset metric runs). Review before use.\n")


def write_root_ok(path: str) -> bool:
    """The recipe may write here: an absolute path under the user's home or the temporary folder (the policy's `write_ok`)."""
    p = Path(path)
    if not p.is_absolute():
        return False
    p = p.resolve()
    roots = [Path.home().resolve(), Path(tempfile.gettempdir()).resolve()]
    return any(r in p.parents for r in roots)


def skeleton(db_path: str, name: str = "", sample_rows: int = 3) -> dict:
    """{skeleton: YAML text, samples: text}: the catalogue draft, and a few rows plus distinct counts per table."""
    doc = catalogue_draft(db_path, name or None)
    db = connect(db_path)
    out = []
    for d in doc["datasets"]:
        t = d["source"]
        df = select(db, f"SELECT * FROM {q(t)}", max_rows=int(sample_rows)).rows
        n = select(db, f"SELECT COUNT(*) AS n FROM {q(t)}", max_rows=1).rows.iat[0, 0]
        out.append(f"## {t} ({int(n)} rows)")
        out.append(df.to_string(index=False, max_colwidth=40) if len(df) else "(empty)")
    return {"skeleton": yaml.safe_dump(doc, sort_keys=False, allow_unicode=True), "samples": "\n".join(out)}


def _strip(reply: str) -> str:
    text = (reply or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip() + "\n"


def _structure(doc: dict) -> dict:
    ds = {}
    for d in doc.get("datasets") or []:
        ds[d.get("name")] = {"source": d.get("source"), "primary_key": list(d.get("primary_key") or []),
                             "fields": {f.get("name"): (f.get("expression") or {}).get("sql") for f in d.get("fields") or []}}
    rels = sorted((r.get("from"), r.get("to"), tuple(r.get("from_columns") or []), tuple(r.get("to_columns") or []))
                  for r in doc.get("relationships") or [])
    return {"datasets": ds, "relationships": rels}


def check(candidate: str, skeleton_yaml: str, db_path: str) -> dict:
    """{ok, validation, candidate, metrics}: `validation` lists what is wrong, in words a model can act on."""
    text = _strip(candidate)
    fail = lambda msg: {"ok": False, "validation": msg, "candidate": text, "metrics": 0}  # noqa: E731
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return fail(f"the YAML does not parse: {str(e).splitlines()[0]}")
    if not isinstance(doc, dict):
        return fail("the reply is not one YAML mapping (an Ossie semantic model)")
    problems = [p for p in C.schema_problems(doc) if p["severity"] == "error"]
    try:
        model = _model(raw_models(doc)[0], str(doc.get("version", "")), None)
    except (ModelError, AttributeError, TypeError) as e:
        return fail(f"not a semantic model: {e}")
    problems += [p for p in C.model_problems(model, L.build(model)) if p["severity"] == "error"]
    msgs = [f"{p['code']} {p['node']}: {p['message']}".strip() for p in problems]

    want, got = _structure(yaml.safe_load(skeleton_yaml)), _structure(doc)
    for name, d in want["datasets"].items():
        g = got["datasets"].get(name)
        if g is None:
            msgs.append(f"dataset {name!r} was removed; keep every dataset of the skeleton")
            continue
        if g["source"] != d["source"]:
            msgs.append(f"dataset {name!r}: `source` must stay {d['source']!r}")
        if g["primary_key"] != d["primary_key"]:
            msgs.append(f"dataset {name!r}: `primary_key` must stay {d['primary_key']}")
        if set(g["fields"]) != set(d["fields"]):
            msgs.append(f"dataset {name!r}: the fields must stay exactly {sorted(d['fields'])} "
                        f"(missing {sorted(set(d['fields']) - set(g['fields']))}, extra {sorted(set(g['fields']) - set(d['fields']))})")
        for f, sql in d["fields"].items():
            if f in g["fields"] and g["fields"][f] != sql:
                msgs.append(f"field {name}.{f}: `expression.sql` must stay {sql!r}")
    for extra in sorted(set(got["datasets"]) - set(want["datasets"])):
        msgs.append(f"dataset {extra!r} is not in the database; remove it")
    if got["relationships"] != want["relationships"]:
        msgs.append("the relationships must stay exactly as in the skeleton (same from, to, from_columns, to_columns)")

    n_metrics = len(model.all_metrics())
    if n_metrics == 0:
        msgs.append("add metrics: at least one dataset-scoped metric for the main facts (sums, counts, averages)")
    if not msgs:
        prof = profile(model, db_path)
        for d in prof["datasets"]:
            for m in d["metrics"]:
                if m.get("error"):
                    msgs.append(f"metric {d['name']}.{m['name']} does not run on the data: {m['error']}")
    if msgs:
        return fail("\n".join(f"- {m}" for m in msgs))
    return {"ok": True, "validation": f"valid: {len(model.datasets)} datasets, {n_metrics} metrics, every dataset metric runs",
            "candidate": text, "metrics": n_metrics}


def write(candidate: str, output_path: str) -> dict:
    p = Path(output_path)
    if not write_root_ok(output_path):                    # the GUARD has already said yes; this is the same rule, kept here too
        raise ValueError(f"refusing to write {output_path}: not absolute and under home or the temporary folder")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(HEADER + _strip(candidate), encoding="utf-8")
    return {"path": str(p)}
