"""The ask and ossie_draft recipes and the bench scorer, with a scripted model: no network, no real LLM."""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

import datagraf as dg
from datagraf import ask as A
from datagraf import bench as B
from datagraf import draft as D
from datagraf.cli import main
from datagraf.run import recipe_path, run_recipe
from datagraf.sqlite import connect

from conftest import CHINOOK, SHOP_MODEL, write_yaml

ROOT = Path(__file__).parent.parent
QUESTIONS = ROOT / "benchmarks" / "chinook" / "questions.yaml"


class Scripted:
    """Replies in order; keeps the prompts it was given."""
    name = "scripted"

    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def generate(self, prompt, model=None, **opts):
        self.prompts.append(prompt)
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def ask(model_path, db, question, llm, **extra):
    return run_recipe("ask", {"question": question, "model": str(model_path), "db": str(db), **extra}, llm=llm, log_dir=None)


# ── the deterministic half ──────────────────────────────────────────────────────────────────────────────────────────

class TestContext:
    def test_join_path_brings_in_the_tables_between(self):
        m = dg.load(CHINOOK)
        picked, _ = A.pick_datasets(m, "which genres sell best to customers")
        assert {"genres", "customers", "invoices", "invoice_items", "tracks"} <= set(picked)   # the path genres..customers

    def test_no_match_means_everything(self):
        m = dg.load(CHINOOK)
        assert A.pick_datasets(m, "zzz qqq")[0] == [d.name for d in m.datasets]

    def test_context_text(self, shop_model, shop_db):
        c = A.context(dg.load(shop_model), "units per customer", tables={"orders", "customers", "products"})
        assert "Dataset orders (table orders)" in c["text"]                # `shopdb.main.orders` -> the table that exists
        assert "metric units = SUM(qty)" in c["text"]
        assert "orders.customer_id = customers.id" in c["text"]
        assert "field:orders.qty" in c["ids"]


class TestCheckSql:
    @pytest.mark.parametrize("reply,expect", [
        ("```sql\nSELECT SUM(o.qty) AS units FROM orders o;\n```", None),
        ("SELECT qtty FROM orders", "could not be resolved"),
        ("SELECT * FROM order_lines", "no such table: order_lines"),
        ("DELETE FROM orders", "only a read-only SELECT"),
        ("SELECT 1; DROP TABLE orders", "exactly one statement"),
        ("SELECT (", "does not parse"),
        ("", "no SQL"),
    ])
    def test_check(self, shop_model, shop_db, reply, expect):
        r = A.check_sql(reply, dg.load(shop_model), str(shop_db))
        assert r["ok"] is (expect is None)
        if expect:
            assert expect in r["error"]
        else:
            assert r["rows"] == [[7]] and "field:orders.qty" in r["uses"]

    def test_undeclared_join_is_a_warning(self, shop_model, shop_db):
        r = A.check_sql("SELECT c.id FROM orders o JOIN customers c ON c.id = o.product_id", dg.load(shop_model), str(shop_db))
        assert r["ok"] and r["warnings"] == ["join customers.id = orders.product_id is not a relationship of the model"]
        r = A.check_sql("SELECT c.id FROM orders o JOIN customers c ON c.id = o.customer_id", dg.load(shop_model), str(shop_db))
        assert r["ok"] and r["warnings"] == []

    def test_database_errors_come_back(self, shop_model, shop_db):
        r = A.check_sql("SELECT SUM(SUM(qty)) FROM orders", dg.load(shop_model), str(shop_db))
        assert not r["ok"] and "the database refused it" in r["error"]


# ── the recipes ─────────────────────────────────────────────────────────────────────────────────────────────────────

class TestAskRecipe:
    def test_recipes_validate(self):
        from graphlang.grafr.runtime import load_graph
        for name in ("ask", "ossie_draft"):
            load_graph(recipe_path(name))

    def test_first_answer_checked(self, shop_model, shop_db):
        llm = Scripted("```sql\nSELECT COUNT(*) AS n FROM customers\n```")
        run = ask(shop_model, shop_db, "how many customers", llm)
        assert (run.outputs["status"], run.llm_calls, run.exit) == ("checked", 1, None)
        assert "CHECKED" in run.outputs["report"] and run.outputs["report"].rstrip().endswith("Uses: [dataset:customers]")
        assert "Dataset customers" in llm.prompts[0] and "how many customers" in llm.prompts[0]
        result = run.outputs["result"]
        assert (json.loads(result) if isinstance(result, str) else result)["rows"] == [[3]]

    def test_repair_gets_the_error(self, shop_model, shop_db):
        llm = Scripted("SELECT COUNT(*) FROM customer", "SELECT COUNT(*) AS n FROM customers")
        run = ask(shop_model, shop_db, "how many customers", llm)
        assert (run.outputs["status"], run.llm_calls) == ("checked", 2)
        assert "no such table: customer" in llm.prompts[1]
        assert run.outputs["sql"] == "SELECT COUNT(*) AS n FROM customers"

    def test_gives_up_after_max_repairs(self, shop_model, shop_db):
        llm = Scripted("DELETE FROM orders")
        before = hashlib.sha256(shop_db.read_bytes()).hexdigest()
        run = ask(shop_model, shop_db, "delete everything", llm, max_repairs=1)
        assert (run.outputs["status"], run.llm_calls) == ("failed", 2)
        assert "FAILED after 2 attempt(s)" in run.outputs["report"] and "Do not rely" in run.outputs["report"]
        assert hashlib.sha256(shop_db.read_bytes()).hexdigest() == before

    def test_scope_all_shows_every_dataset(self, shop_model, shop_db):
        llm = Scripted("SELECT COUNT(*) FROM products")
        ask(shop_model, shop_db, "price", llm, scope="all")
        assert all(f"Dataset {d}" in llm.prompts[0] for d in ("orders", "customers", "products"))


class TestOssieDraftRecipe:
    def enriched(self, db):
        doc = yaml.safe_load(D.skeleton(str(db))["skeleton"])
        doc["description"] = "A small shop"
        for d in doc["datasets"]:
            d["description"] = f"The {d['name']}"
            if d["name"] == "orders":
                d["metrics"] = [{"name": "units", "expression": {"sql": "SUM(qty)"}, "datatype": "Integer", "description": "Units"}]
        return doc

    def test_valid_draft_is_written(self, shop_db, tmp_path):
        out = tmp_path / "m" / "shop.semantic_model.yaml"
        llm = Scripted("```yaml\n" + yaml.safe_dump(self.enriched(shop_db)) + "```")
        run = run_recipe("ossie_draft", {"db": str(shop_db), "output_path": str(out)}, llm=llm, log_dir=None)
        assert run.exit is None and run.llm_calls == 1 and run.outputs["path"] == str(out)
        assert "every dataset metric runs" in run.outputs["validation"]
        assert out.read_text().startswith("# Drafted by data-graf")
        m = dg.load(out)
        assert m.dataset("orders").metric_named("units") and "Sample rows" in llm.prompts[0]

    def test_structure_change_is_repaired(self, shop_db, tmp_path):
        good = self.enriched(shop_db)
        bad = copy.deepcopy(good)
        bad["datasets"][1]["fields"].pop()
        bad["relationships"] = bad["relationships"][:1]
        llm = Scripted(yaml.safe_dump(bad), yaml.safe_dump(good))
        run = run_recipe("ossie_draft", {"db": str(shop_db), "output_path": str(tmp_path / "s.yaml")}, llm=llm, log_dir=None)
        assert run.exit is None and run.llm_calls == 2
        assert "the fields must stay exactly" in llm.prompts[1] and "relationships must stay" in llm.prompts[1]

    def test_metric_that_does_not_run_is_invalid(self, shop_db, tmp_path):
        bad = self.enriched(shop_db)
        bad["datasets"][1]["metrics"].append({"name": "avg_bad", "expression": {"sql": "AVG(qty) + MAX(SUM(qty))"}})
        out = tmp_path / "s.yaml"
        run = run_recipe("ossie_draft", {"db": str(shop_db), "output_path": str(out), "max_repairs": 1},
                         llm=Scripted(yaml.safe_dump(bad)), log_dir=None)
        assert run.exit == "invalid" and run.error_code == "E_MODEL_INVALID" and run.llm_calls == 2
        assert "does not run on the data" in run.outputs["validation"] and not out.exists()

    @pytest.mark.parametrize("where", ["/etc/shop.yaml", "relative.yaml"])
    def test_refused_before_any_model_call(self, shop_db, where):
        run = run_recipe("ossie_draft", {"db": str(shop_db), "output_path": where}, llm=Scripted("x"), log_dir=None)
        assert (run.exit, run.error_code, run.llm_calls) == ("refused", "E_WRITE_REFUSED", 0)

    def test_existing_file_needs_overwrite(self, shop_db, tmp_path):
        out = tmp_path / "s.yaml"
        out.write_text("keep me")
        llm = Scripted(yaml.safe_dump(self.enriched(shop_db)))
        assert run_recipe("ossie_draft", {"db": str(shop_db), "output_path": str(out)}, llm=llm, log_dir=None).exit == "refused"
        assert out.read_text() == "keep me"
        run = run_recipe("ossie_draft", {"db": str(shop_db), "output_path": str(out), "overwrite": "yes"}, llm=llm, log_dir=None)
        assert run.exit is None and out.read_text().startswith("# Drafted")


# ── bench ───────────────────────────────────────────────────────────────────────────────────────────────────────────

class TestBench:
    def test_questions_file(self):
        doc = B.load_questions(QUESTIONS)
        assert [q["id"] for q in doc["questions"]] == list(range(1, 25))
        assert all(g["check"] in ("rows", "superset", "within", "mentions") for q in doc["questions"] for g in q["gold"])

    @pytest.mark.parametrize("gold,pred,ok", [
        ([(7,), (5,)], [("a", 5.0), ("b", 7)], True),                 # any order, extra columns, 7 == 7.0
        ([("Adams",)], [("Andrew Adams",)], True),                    # text contained in a longer value
        ([(7,)], [(7,), (7,)], False),                                # row counts differ
        ([(1.99,)], [(1.9,)], False),
        ([], [], True),
    ])
    def test_rows(self, gold, pred, ok):
        assert B.matches({"check": "rows"}, gold, "", pred) is ok

    def test_within(self):
        tied = [("Harris",), ("Gonçalves",)]
        assert B.matches({"check": "within"}, tied, "", [("Frank", "Harris")])                 # one of the tied
        assert B.matches({"check": "within"}, tied, "", [("Frank Harris",), ("Luís Gonçalves",)])
        assert not B.matches({"check": "within"}, tied, "", [("Puja", "Srivastava")])           # not among them
        assert not B.matches({"check": "within"}, tied, "", [])

    def test_superset_and_mentions(self):
        assert B.matches({"check": "superset"}, [("albums",)], "", [("albums",), ("sqlite_sequence",)])
        assert B.matches({"check": "mentions", "words": ["invoices"]}, None, "SELECT * FROM Invoices", None)
        assert not B.matches({"check": "mentions", "words": ["invoices"]}, None, "SELECT 1", None)

    def test_score_on_shop(self, shop_db):
        q = {"id": 1, "gold": [{"check": "rows", "sql": "SELECT SUM(qty) FROM orders"}]}
        db = connect(shop_db)
        assert B.score(q, "SELECT SUM(qty) AS units FROM orders", db)["passed"]
        assert not B.score(q, "SELECT COUNT(*) FROM orders", db)["passed"]
        assert B.score(q, "SELECT nope FROM orders", db)["error"]

    def test_cli_bench_with_scripted_model(self, shop_db, shop_model, tmp_path, monkeypatch):
        qs = tmp_path / "q.yaml"
        qs.write_text(yaml.safe_dump({"database": str(shop_db), "model": str(shop_model), "questions": [
            {"id": 1, "q": "how many units", "gold": {"sql": "SELECT SUM(qty) FROM orders"}},
            {"id": 2, "q": "how many customers", "gold": {"sql": "SELECT COUNT(*) FROM customers"}}]}))
        import datagraf.run as R
        monkeypatch.setattr(R, "DEFAULT_LLM", Scripted("SELECT SUM(qty) AS units FROM orders"))
        out = tmp_path / "r.json"
        r = CliRunner().invoke(main, ["bench", str(qs), "-o", str(out)])
        assert r.exit_code == 0, r.output
        assert "1/2 passed (50.0%)" in r.output
        assert [x["passed"] for x in json.loads(out.read_text())["results"]] == [True, False]
        r = CliRunner().invoke(main, ["bench", str(qs), "--model", "missing.yaml"])
        assert r.exit_code == 2 and "missing.yaml" in r.output


# ── the reference queries, on the real Chinook database when it is here ─────────────────────────────────────────────

CHINOOK_DB = Path.home() / "projects/digital-duck/data-copilot/src/store/sql/sqlite/chinook/chinook.sqlite3"


@pytest.mark.skipif(not CHINOOK_DB.is_file(), reason="Chinook database not found")
def test_every_reference_query_passes_itself():
    doc, db = B.load_questions(QUESTIONS), connect(CHINOOK_DB)
    for q in doc["questions"]:
        g = q["gold"][0]
        assert B.score(q, g.get("sql") or "SELECT 'invoices'", db)["passed"], q["id"]
