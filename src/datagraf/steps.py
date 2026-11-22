"""Recipe steps: each public function is one SOLVE node of a recipe (`python3 -I -m datagraf.tool STEP`), taking the node's
inputs as keyword arguments (port `in_x` arrives as `x`) and returning a JSON-ready dict. No model calls here."""
from __future__ import annotations

from pathlib import Path

from . import ask as A
from . import draft as D
from .model import load


def _int(v, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ── ask ──────────────────────────────────────────────────────────────────────────────────────────────────────────────

def ask_context(question: str, model: str, db: str = "", scope: str = "graph") -> dict:
    """The semantic model narrowed to the question (scope `graph`), or all of it (`all`)."""
    tables = None
    if db:
        from .sqlite import connect
        tables = set(connect(db).list_tables())
    return A.context(load(model), question, scope or "graph", tables)


def ask_check(reply: str, model: str, db: str, row_limit=20) -> dict:
    return A.check_sql(reply, load(model), db, _int(row_limit, 20))


def ask_report(question: str, check: dict, max_repairs=2) -> dict:
    """A failed check has used every attempt: the first SQL and each repair."""
    return A.report(question, check or {}, 1 + _int(max_repairs, 2))


def pick(first: dict, check=None) -> dict:
    """Inside a repair loop: the latest check, or the first one on the first round (a loop state starts empty)."""
    return check if isinstance(check, dict) and check else first


# ── ossie_draft ──────────────────────────────────────────────────────────────────────────────────────────────────────

def draft_plan(db: str, output_path: str, overwrite: str = "no") -> dict:
    """Whether the policy may allow the write (`write_ok`), checked before any model call."""
    exists = Path(output_path).exists()
    ok = D.write_root_ok(output_path) and Path(db).is_file() and (not exists or str(overwrite).lower() in ("yes", "true", "1"))
    return {"write_ok": ok, "exists": exists}


def draft_skeleton(db: str, name: str = "", sample_rows=3) -> dict:
    return D.skeleton(db, name, _int(sample_rows, 3))


def draft_check(candidate: str, skeleton: str, db: str) -> dict:
    return D.check(candidate, skeleton, db)


def write_model(candidate: str, output_path: str) -> dict:
    return D.write(candidate, output_path)
