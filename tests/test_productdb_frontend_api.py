from __future__ import annotations

import json
from pathlib import Path

from starlette.testclient import TestClient

from paperless_automation.orchestrator.productdb import ProductDatabase, create_app


def _seed_database(root: Path) -> int:
    (root / "README.md").write_text("test marker", encoding="utf-8")
    db = ProductDatabase(root_dir=str(root))

    raw_payload_id = db.insert_text(json.dumps({"hello": "world"}))
    address_id = db.insert_address(
        {
            "street": "Hauptstr. 1",
            "city": "Berlin",
            "postal_code": "10115",
            "country": "DE",
        }
    )
    merchant_id = db.upsert_merchant("Testmarkt", address_id)
    receipt_id = db.insert_receipt(
        {
            "merchant_id": merchant_id,
            "purchase_date_time": "2024-08-01T12:34:56",
            "currency": "EUR",
            "payment_method": "CARD",
            "total_net": 9500,
            "total_tax": 500,
            "total_gross": 10000,
            "source_file_id": None,
            "raw_content_id": raw_payload_id,
        }
    )

    db.insert_items(
        receipt_id,
        [
            {
                "product_name": "Bio Milch",
                "quantity": 2,
                "unit": "x",
                "unit_price_net": 350,
                "unit_price_gross": 400,
                "tax_rate": 0.07,
                "line_net": 700,
                "line_tax": 50,
                "line_gross": 750,
            }
        ],
    )

    db.insert_extraction_run(
        {
            "receipt_id": receipt_id,
            "model_name": "gpt-5-mini",
            "status": "OK",
            "raw_content_id": raw_payload_id,
            "notes": None,
        }
    )
    return receipt_id


def test_api_endpoints_surface_seed_data(tmp_path: Path) -> None:
    receipt_id = _seed_database(tmp_path)

    app = create_app(root_dir=str(tmp_path), serve_static=False, allow_origins=["*"])
    client = TestClient(app)

    summary = client.get("/api/summary")
    assert summary.status_code == 200
    payload = summary.json()
    assert payload["counts"]["receipts"] == 1
    assert payload["totals"]["total_gross_cents"] == 10000

    receipts = client.get("/api/receipts")
    assert receipts.status_code == 200
    receipts_payload = receipts.json()
    assert receipts_payload["total"] == 1
    assert receipts_payload["items"][0]["merchant_name"] == "Testmarkt"

    detail = client.get(f"/api/receipts/{receipt_id}")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["items"][0]["product_name"] == "Bio Milch"
    assert detail_payload["extraction_runs"]

    merchants = client.get("/api/merchants")
    assert merchants.status_code == 200
    merchants_payload = merchants.json()
    assert merchants_payload["items"][0]["merchant_name"] == "Testmarkt"

    tables = client.get("/api/tables/receipts")
    assert tables.status_code == 200
    tables_payload = tables.json()
    assert tables_payload["total"] == 1
