from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yaml

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).parent.parent
OSSIE_EXAMPLES = sorted((FIXTURES / "ossie").glob("*.yaml"))            # apache/ossie main, 0.3.0.dev0
TPCDS = FIXTURES / "ossie" / "tpcds_semantic_model.yaml"
TPCDS_011 = FIXTURES / "ossie-0.1.1" / "tpcds_semantic_model.yaml"      # apache/ossie tag osi-0.1.1-rc1
CHINOOK = ROOT / "examples" / "chinook.semantic_model.yaml"

SHOP_SQL = """
CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, country TEXT, joined DATE);
CREATE TABLE products  (id INTEGER PRIMARY KEY, title TEXT, price NUMERIC);
CREATE TABLE orders (
    id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id), product_id INTEGER REFERENCES products(id),
    qty INTEGER, ordered_at DATETIME);
INSERT INTO customers VALUES (1,'Ann','FR','2024-01-02'), (2,'Bo','US','2024-02-03'), (3,'Cy',NULL,'2024-03-04');
INSERT INTO products  VALUES (1,'Pen',2.5), (2,'Ink',4.0);
INSERT INTO orders VALUES (1,1,1,2,'2024-05-01 10:00'), (2,1,2,1,'2024-05-02 11:00'), (3,2,1,4,'2024-05-03 12:00');
"""

SHOP_MODEL = {
    "version": "0.3.0.dev0",
    "name": "shop",
    "datasets": [
        {"name": "orders", "source": "shopdb.main.orders", "primary_key": ["id"],
         "fields": [{"name": "id", "expression": {"sql": "id"}},
                    {"name": "customer_id", "expression": {"sql": "customer_id"}},
                    {"name": "product_id", "expression": {"sql": "product_id"}},
                    {"name": "qty", "expression": {"sql": "qty"}, "datatype": "Integer"},
                    {"name": "big_order", "expression": {"sql": "CASE WHEN qty >= 4 THEN 1 ELSE 0 END"},
                     "dimension": {"is_time": False}}],
         "metrics": [{"name": "units", "expression": {"sql": "SUM(qty)"}, "description": "Units ordered"},
                     {"name": "big_orders", "expression": {"sql": "SUM(big_order)"}, "description": "Orders of 4 or more"}]},
        {"name": "customers", "source": "customers", "primary_key": ["id"],
         "fields": [{"name": "id", "expression": {"sql": "id"}},
                    {"name": "country", "expression": {"sql": "country"}, "dimension": {}}]},
        {"name": "products", "source": "products", "primary_key": ["id"],
         "fields": [{"name": "id", "expression": {"sql": "id"}}, {"name": "price", "expression": {"sql": "price"}}]},
    ],
    "relationships": [
        {"name": "orders_to_customers", "from": "orders", "to": "customers", "from_columns": ["customer_id"], "to_columns": ["id"]},
        {"name": "orders_to_products", "from": "orders", "to": "products", "from_columns": ["product_id"], "to_columns": ["id"]},
    ],
    "metrics": [
        {"name": "revenue", "expression": {"sql": "SUM(orders.qty * products.price)"}, "description": "Money from orders"},
        {"name": "units_per_customer", "expression": {"sql": "SUM(orders.units) / COUNT(DISTINCT customers.id)"},
         "description": "Units per customer"},
    ],
}


def write_yaml(path: Path, doc: dict) -> Path:
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def shop_db(tmp_path: Path) -> Path:
    db = tmp_path / "shop.sqlite3"
    con = sqlite3.connect(db)
    con.executescript(SHOP_SQL)
    con.commit()
    con.close()
    return db


@pytest.fixture
def shop_model(tmp_path: Path) -> Path:
    return write_yaml(tmp_path / "shop.semantic_model.yaml", SHOP_MODEL)
