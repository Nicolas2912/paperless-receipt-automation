from __future__ import annotations

from typing import Any, Dict, Optional

from ...logging import get_logger
from .db import ProductDatabase
from .parser import parse_and_validate_payload
from .extraction import extract_receipt_payload_from_image


LOG = get_logger("productdb-service")


class ReceiptExtractionService:
    """High-level service coordinating extraction, validation, and persistence.

    Note: This is a scaffold. Implementations for LLM calls and DB writes
    follow in subsequent steps of the plan.
    """

    def __init__(self, db: Optional[ProductDatabase] = None) -> None:
        self.db = db or ProductDatabase()

    def init_database(self) -> str:
        """Ensure the database exists and return its path."""
        # The ProductDatabase constructor ensures schema on creation.
        LOG.info("Product database initialized.")
        return self.db.db_path

    def extract_from_image(self, source_path: str, *, model_name: str = "gpt-5-mini", script_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Run LLM extraction then validate JSON payload (no DB writes)."""
        payload = extract_receipt_payload_from_image(source_path, model_name=model_name, script_dir=script_dir)
        if payload is None:
            LOG.warning("No extraction payload produced (stub).")
            return None
        valid = parse_and_validate_payload(payload)
        LOG.info("Validated extraction payload with top-level keys: %s", list(valid.keys()))
        return valid

    def run_and_persist(self, source_path: str, *, model_name: str = "gpt-5-mini", script_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """End-to-end: extract → validate → insert all rows. Returns summary.

        Summary includes ids and counts to aid debugging/testing.
        """
        payload = self.extract_from_image(source_path, model_name=model_name, script_dir=script_dir)
        if payload is None:
            return None

        # Insert raw JSON payload for traceability
        import json as _json

        raw_json_id = self.db.insert_text(_json.dumps(payload, ensure_ascii=False))
        LOG.debug("Inserted raw payload text_id=%s", raw_json_id)

        # Insert merchant + address
        addr_dict = payload["merchant"].get("address") or {}
        address_id = self.db.insert_address(addr_dict) if any(addr_dict.values()) else None
        merchant_id = self.db.upsert_merchant(payload["merchant"]["name"], address_id)
        LOG.debug("Upserted merchant_id=%s address_id=%s", merchant_id, address_id)

        # File artifact (if provided)
        src_file = payload.get("source_file") or {}
        source_file_id = self.db.upsert_file(src_file) if src_file else None

        # Receipt header
        rcpt_dict = {
            "merchant_id": merchant_id,
            "purchase_date_time": payload["purchase_date_time"],
            "currency": payload["currency"],
            "payment_method": payload["payment_method"],
            "total_net": payload.get("totals", {}).get("total_net"),
            "total_tax": payload.get("totals", {}).get("total_tax"),
            "total_gross": payload.get("totals", {}).get("total_gross"),
            "source_file_id": source_file_id,
            "raw_content_id": raw_json_id,
        }
        receipt_id = self.db.insert_receipt(rcpt_dict)
        LOG.debug("Inserted receipt_id=%s", receipt_id)

        # Items
        items = payload.get("items") or []
        item_count = self.db.insert_items(receipt_id, items)

        # Extraction run record
        run_id = self.db.insert_extraction_run(
            {
                "receipt_id": receipt_id,
                "model_name": model_name,
                "prompt_version": "v1",
                "status": "OK",
                "raw_content_id": raw_json_id,
                "notes": None,
            }
        )

        summary = {
            "db_path": self.db.db_path,
            "merchant_id": merchant_id,
            "address_id": address_id,
            "source_file_id": source_file_id,
            "receipt_id": receipt_id,
            "item_count": item_count,
            "extraction_run_id": run_id,
        }
        LOG.info("Persisted extraction: %s", summary)
        return summary
