from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime

from ...logging import get_logger
from .constants import (
    LINE_TYPE_CHOICES,
    LINE_TYPE_DEFAULT,
    LINE_TYPE_DEPOSIT_REFUND,
)


LOG = get_logger("productdb-parser")


class JsonValidationError(Exception):
    pass


def parse_and_validate_payload(payload: Any) -> Dict[str, Any]:
    """Validate and normalize model output to DB-ready dict.

    Expected input shape (from extraction prompt):
    - merchant: { name: str, address: { street, city, postal_code, country } }
    - purchase_date_time: ISO string; fills missing time with 12:00:00
    - currency: 3 letters
    - payment_method: CASH|CARD|OTHER
    - totals: integers in cents or null
    - items: list of line items (see schema)
    - source_file: { filename, mime_type, byte_size, sha256 } (optional)
    """
    if not isinstance(payload, dict):
        raise JsonValidationError("Payload must be a JSON object")

    def _norm_s(s: Any) -> Optional[str]:
        return str(s).strip() if isinstance(s, str) and s.strip() else None

    def _int_or_none(v: Any) -> Optional[int]:
        if v is None:
            return None
        if isinstance(v, bool):
            raise JsonValidationError("boolean cannot be used where integer cents expected")
        if isinstance(v, (int,)):
            return int(v)
        if isinstance(v, float):
            # assume euros → cents
            return int(round(v))
        if isinstance(v, str) and v.strip():
            try:
                if any(ch in v for ch in ",[.]"):
                    return int(round(float(v.replace(",", "."))))
                return int(v)
            except Exception:
                raise JsonValidationError(f"invalid integer cents: {v}")
        return None

    def _float_or(v: Any, default: float) -> float:
        if v is None:
            return default
        try:
            return float(v)
        except Exception:
            return default

    def _normalize_line_type(value: Any) -> str:
        if isinstance(value, str):
            candidate = value.strip().upper()
            if candidate in LINE_TYPE_CHOICES:
                return candidate
        return LINE_TYPE_DEFAULT

    # merchant
    m = payload.get("merchant")
    if not isinstance(m, dict):
        raise JsonValidationError("merchant must be an object")
    merchant_name = _norm_s(m.get("name"))
    if not merchant_name:
        raise JsonValidationError("merchant.name required")
    addr = m.get("address") or {}
    if not isinstance(addr, dict):
        addr = {}
    address = {
        "street": _norm_s(addr.get("street")),
        "city": _norm_s(addr.get("city")),
        "postal_code": _norm_s(addr.get("postal_code")),
        "country": _norm_s(addr.get("country")) or "DE",
    }

    # date/time
    pdt = _norm_s(payload.get("purchase_date_time"))
    if not pdt:
        raise JsonValidationError("purchase_date_time required")
    try:
        # Accept date only and add noon time
        if len(pdt) == 10:
            pdt_iso = f"{pdt}T12:00:00"
            datetime.fromisoformat(pdt_iso)
            purchase_date_time = pdt_iso
        else:
            # May include timezone; strip it to fit DB format
            dt = datetime.fromisoformat(pdt.replace("Z", "+00:00"))
            purchase_date_time = dt.replace(tzinfo=None).isoformat(timespec="seconds")
    except Exception:
        raise JsonValidationError("purchase_date_time must be ISO date or datetime")

    # currency & payment
    currency = (_norm_s(payload.get("currency")) or "EUR").upper()
    if len(currency) != 3:
        raise JsonValidationError("currency must be a 3-letter code")
    pm = (_norm_s(payload.get("payment_method")) or "OTHER").upper()
    if pm not in {"CASH", "CARD", "OTHER"}:
        pm = "OTHER"

    # totals
    totals = payload.get("totals") or {}
    if not isinstance(totals, dict):
        totals = {}
    total_net = _int_or_none(totals.get("total_net"))
    total_tax = _int_or_none(totals.get("total_tax"))
    total_gross = _int_or_none(totals.get("total_gross"))

    # items
    items_in = payload.get("items")
    if not isinstance(items_in, list) or not items_in:
        raise JsonValidationError("items must be a non-empty list")
    norm_items: List[Dict[str, Any]] = []
    gross_values: List[int] = []
    net_values: List[int] = []
    tax_values: List[int] = []
    for idx, it in enumerate(items_in):
        if not isinstance(it, dict):
            raise JsonValidationError(f"items[{idx}] must be an object")
        name = _norm_s(it.get("product_name"))
        if not name:
            raise JsonValidationError(f"items[{idx}].product_name required")
        qty = _float_or(it.get("quantity"), 1.0)
        if qty <= 0:
            raise JsonValidationError(f"items[{idx}].quantity must be > 0")
        unit = _norm_s(it.get("unit"))
        uprn = _int_or_none(it.get("unit_price_net"))
        uprg = _int_or_none(it.get("unit_price_gross"))
        tax_rate = _float_or(it.get("tax_rate"), 0.19)
        if tax_rate not in {0.0, 0.07, 0.19}:
            raise JsonValidationError(f"items[{idx}].tax_rate must be one of 0.0, 0.07, 0.19")
        ln = _int_or_none(it.get("line_net"))
        lt = _int_or_none(it.get("line_tax"))
        lg = _int_or_none(it.get("line_gross"))
        # Optional sanity: recompute when possible
        if lg is None and ln is not None and lt is not None:
            lg = ln + lt
        if lg is not None:
            gross_values.append(int(lg))
        if ln is not None:
            net_values.append(int(ln))
        if lt is not None:
            tax_values.append(int(lt))
        line_type = _normalize_line_type(it.get("line_type"))
        if line_type == LINE_TYPE_DEFAULT and lg is not None and lg < 0:
            line_type = LINE_TYPE_DEPOSIT_REFUND
        norm_items.append(
            {
                "product_name": name,
                "quantity": float(qty),
                "unit": unit,
                "unit_price_net": uprn,
                "unit_price_gross": uprg,
                "tax_rate": float(tax_rate),
                "line_net": ln,
                "line_tax": lt,
                "line_gross": lg,
                "line_type": line_type,
            }
        )

    # source file (optional)
    src = payload.get("source_file") or {}
    if not isinstance(src, dict):
        src = {}
    source_file = {
        "filename": _norm_s(src.get("filename")),
        "mime_type": _norm_s(src.get("mime_type")),
        "byte_size": int(src.get("byte_size")) if isinstance(src.get("byte_size"), (int,)) else None,
        "sha256": _norm_s(src.get("sha256")),
    }

    # Totals fallback if missing
    if total_gross is None and gross_values:
        total_gross = sum(gross_values)
    if total_net is None and len(net_values) == len(norm_items) and norm_items:
        total_net = sum(net_values)
    if total_tax is None and len(tax_values) == len(norm_items) and norm_items:
        total_tax = sum(tax_values)
    if total_tax is None and total_gross is not None and total_net is not None:
        total_tax = total_gross - total_net
    if total_net is None and total_gross is not None and total_tax is not None:
        total_net = total_gross - total_tax

    raw_content = None
    rc_candidate = payload.get("raw_content")
    if isinstance(rc_candidate, str) and rc_candidate.strip():
        raw_content = rc_candidate.strip()

    normalized = {
        "merchant": {"name": merchant_name, "address": address},
        "purchase_date_time": purchase_date_time,
        "currency": currency,
        "payment_method": pm,
        "totals": {"total_net": total_net, "total_tax": total_tax, "total_gross": total_gross},
        "items": norm_items,
        "source_file": source_file,
    }
    if raw_content:
        normalized["raw_content"] = raw_content
    extraction_meta = payload.get("_extraction_meta")
    if isinstance(extraction_meta, dict) and extraction_meta:
        normalized["_extraction_meta"] = dict(extraction_meta)
    enrichment = payload.get("_enrichment")
    if isinstance(enrichment, dict) and enrichment:
        normalized["_enrichment"] = enrichment
    LOG.debug("Normalized payload ready with %d items", len(norm_items))
    return normalized
