from __future__ import annotations

import sys
import types
from importlib import import_module
from pathlib import Path

# Avoid heavy optional dependencies when importing the orchestrator package in tests.
sys.modules.setdefault("fitz", types.SimpleNamespace())
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *_, **__: None))

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

# Provide lightweight package stubs so importing productdb.db does not execute the
# heavy orchestrator __init__ dependencies.
orchestrator_pkg = types.ModuleType("paperless_automation.orchestrator")
orchestrator_pkg.__path__ = [str(SRC_ROOT / "paperless_automation" / "orchestrator")]
sys.modules.setdefault("paperless_automation.orchestrator", orchestrator_pkg)

productdb_pkg = types.ModuleType("paperless_automation.orchestrator.productdb")
productdb_pkg.__path__ = [str(SRC_ROOT / "paperless_automation" / "orchestrator" / "productdb")]
sys.modules.setdefault("paperless_automation.orchestrator.productdb", productdb_pkg)

ProductDatabase = import_module("paperless_automation.orchestrator.productdb.db").ProductDatabase


def test_merchants_deduplicated_on_init(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("marker", encoding="utf-8")
    db = ProductDatabase(root_dir=str(tmp_path))
    # Seed legacy-style duplicates (same name, NULL address) bypassing the new unique index.
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute("DROP INDEX IF EXISTS idx_merchants_name_address_norm;")
        cur.execute(
            "INSERT INTO merchants (name, address_id) VALUES (?, ?) RETURNING merchant_id;",
            ("Netto Marken-Discount", None),
        )
        m1 = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO merchants (name, address_id) VALUES (?, ?) RETURNING merchant_id;",
            ("Netto  Marken-Discount  ", None),
        )
        m2 = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO receipts (merchant_id, purchase_date_time, currency, payment_method)
            VALUES (?, ?, ?, ?), (?, ?, ?, ?);
            """,
            (
                m1,
                "2024-01-01T12:00:00",
                "EUR",
                "CARD",
                m2,
                "2024-02-01T12:00:00",
                "EUR",
                "CARD",
            ),
        )
        conn.commit()

    # Re-initialize to trigger schema ensure + deduplication.
    db_again = ProductDatabase(root_dir=str(tmp_path))
    with db_again.connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT merchant_id, name FROM merchants ORDER BY merchant_id;")
        merchants = cur.fetchall()
        assert len(merchants) == 1
        assert merchants[0]["name"] == "Netto Marken-Discount"

        cur.execute("SELECT DISTINCT merchant_id FROM receipts;")
        merchant_ids = {row[0] for row in cur.fetchall()}
        assert merchant_ids == {merchants[0]["merchant_id"]}
