from __future__ import annotations

import copy

import pytest

import datagraf as dg
from datagraf.model import expression_sql

from conftest import OSSIE_EXAMPLES, SHOP_MODEL, TPCDS, TPCDS_011, write_yaml


def kinds(G):
    out: dict[str, int] = {}
    for _, k in G.nodes(data="kind"):
        out[k] = out.get(k, 0) + 1
    return out


class TestLoad:
    @pytest.mark.parametrize("path", OSSIE_EXAMPLES + [TPCDS_011], ids=lambda p: f"{p.parent.name}/{p.name}")
    def test_examples_build_without_problems(self, path):
        G = dg.build(dg.load(path))
        assert G.graph["problems"] == []
        assert kinds(G)["dataset"] >= 3

    def test_both_versions_read_alike(self):
        new, old = dg.load(TPCDS), dg.load(TPCDS_011)
        assert (new.version, old.version) == ("0.3.0.dev0", "0.1.1")
        assert new.name == old.name == "tpcds_retail_model"
        assert {d.name for d in new.datasets} == {d.name for d in old.datasets}
        assert old.metric("total_sales").sql == new.metric("total_sales").sql == "SUM(store_sales.ss_ext_sales_price)"

    def test_expression_forms(self):
        assert expression_sql({"sql": "a", "dialects": [{"dialect": "ANSI_SQL", "expression": "b"}]}) == "a"
        assert expression_sql({"dialects": [{"dialect": "TABLEAU", "expression": "x"},
                                            {"dialect": "ansi_sql", "expression": "y"}]}) == "y"
        assert expression_sql({"dialects": [{"dialect": "TABLEAU", "expression": "x"}]}) is None

    def test_several_models_in_one_011_file(self, tmp_path):
        m = {"name": "a", "datasets": [{"name": "t", "source": "t", "fields": []}]}
        p = write_yaml(tmp_path / "two.yaml", {"version": "0.1.1", "semantic_model": [m, {**m, "name": "b"}]})
        with pytest.raises(dg.ModelError, match="holds 2 models"):
            dg.load(p)
        assert dg.load(p, "b").name == "b"
        assert [x.name for x in dg.load_models(p)] == ["a", "b"]

    def test_not_a_model(self, tmp_path):
        p = write_yaml(tmp_path / "x.yaml", {"primitives": []})
        with pytest.raises(dg.ModelError, match="neither"):
            dg.load(p)


class TestLineage:
    def test_tpcds_counts(self):
        G = dg.build(dg.load(TPCDS))
        assert kinds(G) == {"dataset": 5, "source": 5, "field": 32, "column": 31, "metric": 9}
        assert len(G.graph["joins"]) == 4

    def test_upstream_reaches_columns_across_datasets(self):
        G = dg.build(dg.load(TPCDS))
        up = dg.upstream(G, dg.resolve(G, "customer_lifetime_value"))
        cols = sorted(n for n in up if G.nodes[n]["kind"] == "column")
        assert cols == ["column:tpcds.public.customer.c_customer_sk", "column:tpcds.public.store_sales.ss_ext_sales_price"]
        assert G.nodes["metric:customer_lifetime_value"]["datasets"] == ["store_sales", "customer"]

    def test_impact_of_a_field(self):
        G = dg.build(dg.load(TPCDS))
        down = dg.downstream(G, dg.resolve(G, "store_sales.ss_ext_sales_price"))
        assert sum(G.nodes[n]["kind"] == "metric" for n in down) == 7

    def test_model_metric_over_dataset_metric(self, shop_model):
        G = dg.build(dg.load(shop_model))
        assert G.has_edge("metric:orders.units", "metric:units_per_customer")
        assert G.edges["metric:orders.units", "metric:units_per_customer"]["rel"] == "computes"
        assert G.has_edge("field:orders.qty", "metric:orders.units")
        assert G.has_edge("column:shopdb.main.orders.qty", "field:orders.big_order")

    def test_resolve(self, shop_model):
        G = dg.build(dg.load(shop_model))
        assert dg.resolve(G, "revenue") == "metric:revenue"
        assert dg.resolve(G, "metric:revenue") == "metric:revenue"
        assert dg.resolve(G, "ORDERS.QTY") == "field:orders.qty"
        with pytest.raises(KeyError, match="ambiguous"):
            dg.resolve(G, "customers")                   # dataset:customers and source:customers
        with pytest.raises(KeyError, match="no node"):
            dg.resolve(G, "nope")

    def test_sql_refs(self):
        refs, err = dg.sql_refs("SUM(a.x) / COUNT(*) + y")
        assert err is None
        assert {(r.qualifier, r.name) for r in refs} == {("a", "x"), (None, "y")}
        refs, err = dg.sql_refs("SUM(")
        assert refs == [] and err


class TestProblems:
    def broken(self, tmp_path, mutate):
        doc = copy.deepcopy(SHOP_MODEL)
        mutate(doc)
        return dg.build(dg.load(write_yaml(tmp_path / "b.yaml", doc)))

    def codes(self, G):
        return {p["code"] for p in G.graph["problems"]}

    def test_unknown_field_in_dataset_metric(self, tmp_path):
        G = self.broken(tmp_path, lambda d: d["datasets"][0]["metrics"].append({"name": "x", "expression": {"sql": "SUM(qtty)"}}))
        assert self.codes(G) == {"UNKNOWN_REFERENCE"}

    def test_unknown_dataset_in_model_metric(self, tmp_path):
        G = self.broken(tmp_path, lambda d: d["metrics"].append({"name": "x", "expression": {"sql": "SUM(order.qty)"}}))
        assert self.codes(G) == {"UNKNOWN_REFERENCE"}

    def test_bare_name_in_model_metric(self, tmp_path):
        G = self.broken(tmp_path, lambda d: d["metrics"].append({"name": "x", "expression": {"sql": "SUM(qty)"}}))
        assert self.codes(G) == {"UNKNOWN_REFERENCE"}

    def test_parse_error_and_opaque(self, tmp_path):
        def m(d):
            d["metrics"].append({"name": "x", "expression": {"sql": "SUM(orders.qty"}})
            d["metrics"].append({"name": "y", "expression": {"dialects": [{"dialect": "DAX", "expression": "SUM(x)"}]}})
        assert self.codes(self.broken(tmp_path, m)) == {"SQL_PARSE", "OPAQUE_EXPRESSION"}
