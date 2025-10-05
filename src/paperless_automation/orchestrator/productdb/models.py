from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Address:
    address_id: Optional[int]
    street: Optional[str]
    city: Optional[str]
    postal_code: Optional[str]
    country: Optional[str]


@dataclass
class Merchant:
    merchant_id: Optional[int]
    name: str
    address_id: Optional[int]
    created_at: Optional[str] = None


@dataclass
class FileArtifact:
    file_id: Optional[int]
    filename: str
    mime_type: Optional[str]
    byte_size: Optional[int]
    sha256: Optional[str]


@dataclass
class TextBlob:
    text_id: Optional[int]
    content: str  # raw JSON string


@dataclass
class Receipt:
    receipt_id: Optional[int]
    merchant_id: int
    purchase_date_time: str  # YYYY-MM-DDTHH:MM:SS
    currency: str            # 3-letter
    payment_method: str      # CASH | CARD | OTHER
    total_net: Optional[int]
    total_tax: Optional[int]
    total_gross: Optional[int]
    source_file_id: Optional[int]
    raw_content_id: Optional[int]
    created_at: Optional[str] = None


@dataclass
class ReceiptItem:
    item_id: Optional[int]
    receipt_id: int
    product_name: str
    quantity: float
    unit: Optional[str]
    unit_price_net: Optional[int]
    unit_price_gross: Optional[int]
    tax_rate: float
    line_net: Optional[int]
    line_tax: Optional[int]
    line_gross: Optional[int]
    created_at: Optional[str] = None


@dataclass
class ExtractionRun:
    run_id: Optional[int]
    receipt_id: Optional[int]
    model_name: str
    started_at: Optional[str]
    finished_at: Optional[str]
    status: Optional[str]
    raw_content_id: Optional[int]
    notes: Optional[str]


@dataclass
class TaxRate:
    tax_code: str
    country: str
    rate: float
    description: Optional[str]
