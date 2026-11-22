from __future__ import annotations

import copy
import hashlib
import sqlite3

import pytest
import yaml
from click.testing import CliRunner
from zinet import to_yaml, validate as zvalidate

import datagraf as dg
from datagraf import checks as C
from datagraf.cli import main
from datagraf.export import to_json, to_zinet
from datagraf.model import read_document
from datagraf.sqlite import connect, draft, ossie_type, profile

from conftest import CHINOOK, OSSIE_EXAMPLES, SHOP_MODEL, TPCDS, TPCDS_011, write_yaml


def problems(path):
    m = dg.load(path)
    return C.schema_problems(read_document(path)) + C.model_problems(m, dg.build(m))


def codes(ps, severity=None):
    return {p["code"] for p in ps if severity is None or p["severity"] == severity}


class TestChecks:
    @pytest.mark.parametrize("path", OSSIE_EXAMPLES + [TPCDS_011, CHINOOK], ids=lambda p: p.name)
    def test_examples_have_no_errors(self, path):
        assert codes(problems(path), "error") == set()

    def test_011_key_that_is_not_a_field_only_warns(self):
        ps = problems(TPCDS_011)
        assert [(p["severity"], p["code"]) for p in ps] == [("warning", "UNKNOWN_KEY")]

    def test_shop_is_clean(self, shop_model):
        assert problems(shop_model) == []

    def test_schema_error(self, tmp_path):
        doc = copy.deepcopy(SHOP_MODEL)
        doc["datasets"][0]["colour"] = "red"
        assert codes(C.schema_problems(doc)) == {"SCHEMA"}
        assert codes(C.schema_problems({"version": "9.9", "name": "x"})) == {"UNKNOWN_VERSION"}

    @pytest.mark.parametrize("mutate,expected", [
        (lambda d: d["relationships"].append({"name": "r", "from": "orders", "to": "shops", "from_columns": ["id"],
                                              "to_columns": ["id"]}), {"UNKNOWN_DATASET"}),
        (lambda d: d["relationships"][0].update(from_columns=["cust"]), {"UNKNOWN_FIELD"}),
        (lambda d: d["relationships"][0].update(to_columns=["id", "country"]), {"JOIN_ARITY", "JOIN_NOT_ON_KEY"}),
        (lambda d: d["datasets"][0].update(primary_key=["pk"]), {"UNKNOWN_KEY"}),
        (lambda d: d["datasets"][0]["fields"].append({"name": "qty", "expression": {"sql": "qty"}}), {"DUPLICATE_NAME"}),
    ])
    def test_model_errors(self, tmp_path, mutate, expected):
        doc = copy.deepcopy(SHOP_MODEL)
        mutate(doc)
        m = dg.load(write_yaml(tmp_path / "b.yaml", doc))
        assert codes(C.model_problems(m, dg.build(m))) == expected

    def test_no_join_path_and_gaps(self, tmp_path):
        doc = copy.deepcopy(SHOP_MODEL)
        doc["relationships"] = doc["relationships"][:1]                  # products is no longer joined
        doc["datasets"][0]["metrics"][0].pop("description")
        m = dg.load(write_yaml(tmp_path / "b.yaml", doc))
        ps = C.model_problems(m, dg.build(m))
        assert codes(ps, "error") == {"NO_JOIN_PATH"}
        assert codes(ps, "warning") == {"UNDESCRIBED", "UNJOINED_DATASET"}


class TestExport:
    @pytest.mark.parametrize("path", [TPCDS, TPCDS_011, CHINOOK], ids=lambda p: p.name)
    def test_zinet_graph_is_valid(self, path):
        g = to_zinet(dg.build(dg.load(path)))
        assert [i for i in zvalidate(g) if i.severity == "error"] == []
        assert len(g.applications) == 1
        assert all(n.id.startswith(("source__", "column__")) for n in g.primitives)
        yaml.safe_load(to_yaml(g))

    def test_json(self, shop_model):
        data = yaml.safe_load(to_json(dg.build(dg.load(shop_model))))
        assert data["name"] == "shop" and len(data["joins"]) == 2
        assert {"from": "metric:orders.units", "to": "metric:units_per_customer", "rel": "computes"} in data["edges"]


class TestSqlite:
    def test_types(self):
        assert [ossie_type(t) for t in ("INTEGER", "NVARCHAR(40)", "NUMERIC(10,2)", "DATETIME", "DATE", "", "BLOB")] == \
               ["Integer", "String", "Decimal", "DateTime", "Date", None, None]

    def test_draft(self, shop_db, tmp_path):
        doc = draft(shop_db, "shop")
        by = {d["name"]: d for d in doc["datasets"]}
        assert sorted(by) == ["customers", "orders", "products"]
        assert by["orders"]["primary_key"] == ["id"]
        assert {f["name"]: f.get("datatype") for f in by["orders"]["fields"]}["ordered_at"] == "DateTime"
        assert sorted((r["from"], r["to"], r["from_columns"][0], r["to_columns"][0]) for r in doc["relationships"]) == \
               [("orders", "customers", "customer_id", "id"), ("orders", "products", "product_id", "id")]
        p = write_yaml(tmp_path / "d.yaml", doc)
        assert codes(problems(p), "error") == set()          # a draft is a valid model, short of metrics

    def test_profile(self, shop_model, shop_db):
        prof = profile(dg.load(shop_model), shop_db)
        by = {d["name"]: d for d in prof["datasets"]}
        assert by["orders"]["table"] == "orders"               # `shopdb.main.orders` found by its last part
        assert by["orders"]["rows"] == 3
        country = {f["name"]: f for f in by["customers"]["fields"]}["country"]
        assert (country["non_null"], country["nulls"], country["distinct"]) == (2, 1, 2)
        assert {m["name"]: m["value"] for m in by["orders"]["metrics"]} == {"units": 7, "big_orders": 1}

    def test_profile_reports_failing_sql(self, tmp_path, shop_db):
        doc = copy.deepcopy(SHOP_MODEL)
        doc["datasets"][0]["metrics"].append({"name": "bad", "expression": {"sql": "SUM(no_such_field)"}})
        doc["datasets"].append({"name": "ghost", "source": "ghost", "fields": []})
        by = {d["name"]: d for d in profile(dg.load(write_yaml(tmp_path / "b.yaml", doc)), shop_db)["datasets"]}
        assert "no such column" in {m["name"]: m for m in by["orders"]["metrics"]}["bad"]["error"]
        assert "no table" in by["ghost"]["error"]

    def test_read_only(self, shop_db, shop_model):
        before = hashlib.sha256(shop_db.read_bytes()).hexdigest()
        with pytest.raises(sqlite3.OperationalError):
            connect(shop_db).run_query("DELETE FROM orders")
        doc = copy.deepcopy(SHOP_MODEL)                       # SQL in a model cannot write either
        doc["datasets"][0]["metrics"].append({"name": "evil", "expression": {"sql": "1); DELETE FROM orders; SELECT (1"}})
        profile(dg.load(write_yaml(shop_model.parent / "e.yaml", doc)), shop_db)
        assert hashlib.sha256(shop_db.read_bytes()).hexdigest() == before


class TestCli:
    def run(self, *args):
        return CliRunner().invoke(main, [str(a) for a in args])

    def test_validate_exit_codes(self, shop_model, tmp_path):
        assert self.run("validate", shop_model).exit_code == 0
        assert self.run("validate", "--strict", TPCDS_011).exit_code == 1
        doc = copy.deepcopy(SHOP_MODEL)
        doc["metrics"].append({"name": "x", "expression": {"sql": "SUM(nope.qty)"}})
        r = self.run("validate", write_yaml(tmp_path / "b.yaml", doc))
        assert r.exit_code == 1 and "UNKNOWN_REFERENCE" in r.output

    def test_lineage_and_impact(self, shop_model):
        r = self.run("lineage", shop_model, "revenue")
        assert r.exit_code == 0 and "2 source column(s)" in r.output and "products.price" in r.output
        r = self.run("impact", shop_model, "orders.qty")
        assert r.exit_code == 0 and "3 metric(s) affected: orders.units, revenue, units_per_customer" in r.output
        assert self.run("lineage", shop_model, "nope").exit_code == 1

    def test_draft_profile_export_render(self, shop_db, tmp_path):
        out = tmp_path / "shop.semantic_model.yaml"
        assert self.run("draft", "--sqlite", shop_db, "-o", out).exit_code == 0
        assert self.run("profile", out, "--sqlite", shop_db).exit_code == 0
        assert "flowchart" in self.run("render", out, "--format", "mermaid").output
        assert self.run("render", out).exit_code == 2                   # html needs -o
        assert self.run("export", out, "-o", tmp_path / "g.yaml").exit_code == 0
        assert self.run("show", out).exit_code == 0
