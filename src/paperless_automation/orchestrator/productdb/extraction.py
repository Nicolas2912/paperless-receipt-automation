from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
import base64, hashlib, json, logging, mimetypes, os, time, sys, re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import httpx  # NEW
from openai import OpenAI, APIConnectionError, APITimeoutError, APIStatusError
from dotenv import load_dotenv


try:
    from ...logging import get_logger
    from ...config import load_openai, load_ollama, load_openrouter
    from ..watch import read_watch_dir_from_file
    from .constants import (
        LINE_TYPE_CHOICES,
        LINE_TYPE_DEFAULT,
        LINE_TYPE_DEPOSIT_CHARGE,
        LINE_TYPE_DEPOSIT_REFUND,
        LINE_TYPE_DISCOUNT,
        LINE_TYPES_ALLOWING_NEGATIVES,
    )
except Exception:
    # Allow running this file directly (no package parent)
    _HERE = os.path.dirname(__file__)
    _SRC = os.path.abspath(os.path.join(_HERE, "../../.."))
    if _SRC not in sys.path:
        sys.path.insert(0, _SRC)
    from paperless_automation.logging import get_logger
    from paperless_automation.config import load_openai, load_ollama, load_openrouter
    from paperless_automation.orchestrator.watch import read_watch_dir_from_file
    from paperless_automation.orchestrator.productdb.constants import (
        LINE_TYPE_CHOICES,
        LINE_TYPE_DEFAULT,
        LINE_TYPE_DEPOSIT_CHARGE,
        LINE_TYPE_DEPOSIT_REFUND,
        LINE_TYPE_DISCOUNT,
        LINE_TYPES_ALLOWING_NEGATIVES,
    )
import requests

LOG = get_logger("productdb-extraction")

ENV_PATH_WIN = r"C:\Users\Anwender\Desktop\Nicolas\Dokumente\MeineProgramme\paperless-receipt-automation\.env"

load_dotenv(dotenv_path=ENV_PATH_WIN)

# Backend and model toggles
# - BACKEND: "openai", "ollama", or "openrouter" (env: PRODUCTDB_BACKEND)
BACKEND: str = (os.getenv("PRODUCTDB_BACKEND") or "openrouter").strip().lower()
# - MODEL: default Ollama model tag (env: OLLAMA_MODEL)
MODEL: str = (os.getenv("OLLAMA_MODEL") or "gemma3:4b").strip()
# - OPENROUTER_MODEL: default model id for OpenRouter backend
OPENROUTER_MODEL: str = (os.getenv("OPENROUTER_MODEL"))
if OPENROUTER_MODEL:
    LOG.debug("Configured OpenRouter model: %s", OPENROUTER_MODEL)

_OPENROUTER_PDF_ENGINE_ENV = os.getenv("OPENROUTER_PDF_ENGINE")
if _OPENROUTER_PDF_ENGINE_ENV is not None:
    OPENROUTER_PDF_ENGINE = _OPENROUTER_PDF_ENGINE_ENV.strip()
    if OPENROUTER_PDF_ENGINE:
        LOG.debug("Configured OpenRouter PDF engine: %s", OPENROUTER_PDF_ENGINE)
else:
    OPENROUTER_PDF_ENGINE = "pdf-text"

PFAND_KEYWORDS: Tuple[str, ...] = ("pfand", "leergut", "einweg", "mehrweg")
DISCOUNT_KEYWORDS: Tuple[str, ...] = ("rabatt", "discount", "gutschein", "coupon", "nachlass")

# ---------- file helpers (unchanged) ----------
def _b64_data_url(path: str) -> Optional[str]:
    mime, _ = mimetypes.guess_type(path)
    if not mime:
        ext = os.path.splitext(path)[1].lower()
        if ext == ".pdf":
            mime = "application/pdf"
        elif ext in {".jpg", ".jpeg", ".jpe", ".jfif"}:
            mime = "image/jpeg"
        else:
            mime = "image/png"
    if not mime or (not mime.startswith("image/") and mime != "application/pdf"):
        LOG.error("Unsupported MIME type for extraction: %s", mime)
        return None
    try:
        with open(path, "rb") as f:
            data = f.read()
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception as e:
        LOG.error("Failed to read source file for data URL: %s", e)
        return None

def _file_facts(path: str) -> Dict[str, Any]:
    try:
        size = os.path.getsize(path)
    except Exception:
        size = None
    sha256 = None
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        sha256 = h.hexdigest()
    except Exception:
        pass
    mime, _ = mimetypes.guess_type(path)
    return {"filename": os.path.basename(path), "mime_type": mime, "byte_size": size, "sha256": sha256}

# ---------- scan folder helpers ----------
DEFAULT_SCAN_IMAGE_EXTENSIONS: Set[str] = {".jpg", ".jpeg", ".png", ".pdf"}

def _normalize_extensions(exts: Iterable[str]) -> Set[str]:
    normalized: Set[str] = set()
    for value in exts:
        if not value:
            continue
        clean = value.strip().lower()
        if not clean:
            continue
        if not clean.startswith("."):
            clean = "." + clean
        normalized.add(clean)
    return normalized

def list_scan_image_paths(
    config_path: Optional[str] = None,
    *,
    recursive: bool = False,
    exts: Optional[Iterable[str]] = None,
) -> List[str]:
    """Return sorted absolute paths for all scanned receipts in the configured folder."""

    directory = read_watch_dir_from_file(config_path)
    allowed_exts = _normalize_extensions(exts or DEFAULT_SCAN_IMAGE_EXTENSIONS)

    matches: List[str] = []
    if recursive:
        walker = os.walk(directory)
    else:
        try:
            entries = os.listdir(directory)
        except Exception as exc:
            LOG.error("Failed to list scan directory %s: %s", directory, exc)
            return []
        walker = [(directory, [], entries)]

    for root, _, filenames in walker:
        for name in filenames:
            full_path = os.path.join(root, name)
            if not os.path.isfile(full_path):
                continue
            _, ext = os.path.splitext(name)
            if ext.lower() not in allowed_exts:
                continue
            matches.append(os.path.abspath(full_path))

    matches.sort()
    return matches


# ---------- dataclasses & containers ----------


@dataclass(frozen=True)
class FileFacts:
    """Normalized view of the source file metadata used for provenance logging."""

    filename: Optional[str]
    mime_type: Optional[str]
    byte_size: Optional[int]
    sha256: Optional[str]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FileFacts":
        return cls(
            filename=data.get("filename"),
            mime_type=data.get("mime_type"),
            byte_size=data.get("byte_size"),
            sha256=data.get("sha256"),
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "filename": self.filename,
            "mime_type": self.mime_type,
            "byte_size": self.byte_size,
            "sha256": self.sha256,
        }


def _openrouter_content_node(data_url: str, facts: FileFacts) -> Dict[str, Any]:
    mime = (facts.mime_type or "").lower()
    if mime.startswith("image/"):
        return {"type": "image_url", "image_url": {"url": data_url}}
    filename = facts.filename or "document.pdf"
    return {
        "type": "file",
        "file": {
            "filename": filename,
            "file_data": data_url,
        },
    }


def _openrouter_plugins_for_mime(mime: Optional[str]) -> Optional[List[Dict[str, Any]]]:
    if not mime:
        return None
    normalized = mime.lower()
    if normalized != "application/pdf":
        return None
    engine = OPENROUTER_PDF_ENGINE.strip()
    if not engine or engine.lower() in {"default", "none", "off"}:
        return None
    return [
        {
            "id": "file-parser",
            "pdf": {
                "engine": engine,
            },
        }
    ]


@dataclass(frozen=True)
class OpenRouterConfig:
    """Configuration set required to talk to the OpenRouter API."""

    api_key: str
    model_name: str
    temperature: float = 0.0
    max_tokens: int = 8000
    timeout_seconds: int = 180


# ---------- prompt & schema (your originals) ----------
def _prompt() -> str:
    return """
## Task
Extract data from a retail image or PDF of a receipt. Read very carefully and capture every product (food and non-food) and Pfand line. When uncertain, still include the line as an item with your best transcription; set numeric fields you cannot read confidently to null instead of skipping the item.
Read line by line to get every product (food and non-food) and Pfand lines.

## Output (strict)
Return ONLY strict JSON (no code fences, no commentary).
The top-level object MUST have exactly these keys (no more, no less):
- merchant
- date
- currency
- payment_method
- items

### Schema: items
Each line item must include:
- description: string (raw text from the receipt line)
- quantity: integer (>= 1), always positive
- unit: string|null
- unit_price_net: integer|null (cents, per unit; NOT the row total)
- unit_price_gross: integer|null (cents, per unit; NOT the row total)
- line_net: integer (cents); sign-preserving (see rules)
- line_gross: integer (cents); sign-preserving (see rules)
- vat_rate: number|null
- line_type: enum { NORMAL, DEPOSIT_CHARGE, DEPOSIT_REFUND }

### Unit price vs row total (CRITICAL)
If a line shows both a unit price and a row total, then:
- unit_price_gross = unit price per piece (cents)
- line_gross = quantity × unit_price_gross (row total in cents)

Example:
- "Pfandtasche 4 A 0,50 € 2,00 € *"
  → quantity = 4
  → unit_price_gross = 50   (0,50 €)
  → line_gross = 200        (2,00 €)

Example Netto layout:
- Line 1: "TOMATEN"
- Line 2: "2x 1,39"
- Line 3: "SKYR 1,39 €"

You must output ONE item for SKYR:
- description = "SKYR 1,39 €"
- quantity = 2
- unit_price_gross = 139  (1,39 €)
- line_gross = 278        (2 × 1,39 €)

Tomaten stays quantity = 1 unless its own multiplier is printed.
Netto layout rule: a standalone multiplier line like "2x 1,39" belongs to the next product line below it, not the one above.

### Token cues for Pfand classification
Use case-insensitive matching; treat hyphens/whitespace/umlauts equivalently.
- Deposit CHARGE cues → DEPOSIT_CHARGE:
  - "pfand" (incl. "ew-pfand", "mehrwegpfand", "mw-pfand", "pfand bier")
  - "einweg" when attached to pfand context (e.g., "einweg-pfand", "ew-pfand")
  - "mehrweg" when attached to pfand context (e.g., "mehrweg-pfand", "mw-pfand")
- Deposit REFUND cues → DEPOSIT_REFUND:
  - "rückgabe", "rueckgabe", "rück-", "rueck-"
  - "leergut", "einweg-leergut", "mehrweg-leergut"
  - "pfandbon", "pfand-rück", "pfand rueck", "pfand rück", "einlösung pfand"
  - phrases like "einwegleergut", "mehrwegleergut"

Normalize spelling and separators only for interpretation—do not alter the extracted description.

### Amount sign rules (CRITICAL)
- Preserve the sign exactly as printed on the receipt. Do not flip the sign during fixes or validation.
- DEPOSIT_CHARGE lines are expected to be positive (no leading minus).
- DEPOSIT_REFUND lines are expected to be negative (leading minus).
- If sign and cues disagree:
  - Sign is the source of truth for line_net/line_gross. Keep the printed sign.
  - Use the cues to set line_type consistently with the observed sign:
    - Negative amount + any pfand/return cue → DEPOSIT_REFUND.
    - Positive amount (or no sign) + any pfand cue → DEPOSIT_CHARGE.
- Explicit clarity for “Leergut”:
  - Only classify as DEPOSIT_REFUND if the printed amount is negative (has a leading minus).
  - If “Leergut” (or any refund cue) shows a positive amount or no sign, treat it as DEPOSIT_CHARGE (do not infer a refund).
- quantity stays positive (usually 1) for both charges and refunds. Do not encode refunds via negative quantity.

### Non-item attribute lines (do not emit as separate items)
Never create a separate item from lines that only provide details for an adjacent product and do not represent an independent charge. Attach them to their owning product for arithmetic validation only. Examples:
- Weight/unit-price details: patterns like “1,106 kg x 1,29 EUR/kg”, “0,756 kg x 2,49 €/kg”, “750 g x 1,99 €/kg”
- Per-unit annotations: “je 1,39”, “à 1,39”, “0,25 Pfand je”
- Quantity-only/multiplier lines without a row total
Handling:
- If a product line with a gross total exists (e.g., “Bananen 1,99 €”) and the next line is a weight/unit-price detail (“1,106 kg x 1,29 EUR/kg”), emit only ONE item using the product line text as description and the printed gross total (1,99 €) for line_gross. Do not emit the detail line as an item.
- Use the detail line solely to validate that line_gross ≈ weight × unit_price within 1 cent. If it disagrees, prefer the printed row total on the product line. If still uncertain, include the item with the best description you have and leave unknown numeric fields as null rather than skipping it.
 - **VAT / tax summary rows are NEVER items.**
      Examples (like on Famila receipts):
      - Header line: "x-Satz   MWST   Netto   Brutto"
      - Group rows: "A 19 %   0,91   4,82   5,73" or "B 7 %   0,52   7,47   7,99"
      These rows summarize groups of items and must NOT appear in the `items` array at all.
      They must never be used as `line_gross` for any product.

      For every item in `items`, `line_gross` MUST be the price printed on the same line as that product.


### Field rules for Pfand lines
- line_gross and line_net are integers in cents.
- For DEPOSIT_REFUND: line_gross < 0 and line_net < 0; quantity > 0.
- For DEPOSIT_CHARGE: line_gross > 0 and line_net > 0; quantity > 0.
- Prefer classifying a line as deposit (charge/refund) over NORMAL if pfand cues are present, but never override the printed sign.
- When a merchandise line and an adjacent pfand line share a brand (e.g., beer), keep them as two separate items: the product (NORMAL) and the deposit (DEPOSIT_*).
- Pfand quantity: If a unit pfand value is visible (e.g., “0,25”) and the pfand line gross equals a multiple of it, set quantity = line_gross_cents / unit_value_cents; otherwise default to 1.

### Validation (Pfand & amounts)
- line_net and line_gross: absolute value must be positive; sign encodes charge (+) vs refund (−). Never coerce negatives to positive or vice versa.
- quantity: positive integer.
- Totals sanity: Summed line_gross across items may include both positive (charges) and negative (refunds). Do not “fix” by flipping signs.
- Leergut example clarity:
  - “Leergut −0,50 €” → DEPOSIT_REFUND, line_gross = −50
  - “Leergut 0,50 €” or “Leergut 0,50” (no minus) → DEPOSIT_CHARGE, line_gross = 50

### Extraction checklist (apply in this order)
1) Parse numeric amount exactly as printed (capture the leading minus if present) and convert to cents.
2) Detect pfand cues (see lists above).
3) Set line_type using cues and the observed sign (sign wins for amounts).
4) Enforce sign conventions: charge → positive; refund → negative. If a mismatch is found, do not change the amount; instead, align line_type to the observed sign.
5) Non-item attribute lines: If a line matches weight/unit-price/multiplier patterns and lacks its own row total context, attach it to the nearest product per layout rules below; never emit as a separate item.
6) Quantity detection:
   - Explicit multiplier near the item: detect patterns like “(\d+)[x×*](\s*)?(\d+[.,]\d{2})”, “Menge \d+”, “Anz\.?\s*\d+”, “Anzahl \d+”, “je \d+[.,]\d{2}”, “à \d+[.,]\d{2}”.
   - Netto layout rule: On Netto Marken-Discount receipts, a quantity line like “2x 1,39” appears before the product and belongs to the next non-empty product line. Confirm that line_gross ≈ 2 × 1,39 €.
   - Famila layout rule: On Famila receipts, the quantity is printed directly beneath the item it belongs to (not before it). Link the quantity line to the item above.
7) Weighed items safeguard:
   - If weight and price-per-kg/l are shown (e.g., “1,106 kg x 1,29 EUR/kg”) and a row total is printed on the product line, set quantity = 1 for the product, use the product line’s gross total, and do not emit the detail line.
   - If only a single line shows both weight × unit price and a computed total as part of that same line, emit one item using that line’s description and total; quantity = 1.
8) Arithmetic cross-check:
   - If a unit price appears and line_gross is known, compute candidate = round(line_gross_cents / unit_price_cents).
   - If remainder ≤ 1 cent and candidate ≥ 1, set quantity = candidate (even if step 6 didn’t trigger).
   - If an explicit quantity disagrees with arithmetic, choose the value where line_gross ≈ quantity × unit_price within 1 cent.
9) Fallback: If no signals exist, set quantity = 1.
10) Include all items (food and non-food)! Please make sure to really include everything listed on the receipt that is an actual item and end with the last item right above "Summe".
Double check and verfiy deeply on every line of the receipt.

### Field specifications
- merchant: { "name": string, "street": string, "city": string, "postal_code": string }
- date: purchase date in "DD.MM.YYYY" format
- currency: 3-letter code (e.g., EUR)
- payment_method: CASH | CARD | OTHER
- items: array of objects, each:
  { "description": string, "quantity": int, "unit": string|null, "unit_price_net": int|null, "unit_price_gross": int|null, "line_net": int, "line_gross": int, "vat_rate": number|null, "line_type": enum }

### Rules
- line_gross = the row total in cents (German: Summe, Gesamt, Zwischensumme (Pos.)). Sign-preserving per Pfand rules above.
- vat_rate = decimal (e.g., 19% → 0.19, 7% → 0.07). In Germany, default to 0.19 or 0.07 based on the item line; do not invent other rates unless explicitly printed.
- Locale parsing: “1,39” → €1.39 → 139 cents. Dots may be thousand separators. Ignore unit-price per kg/l unless needed for arithmetic cross-checks.
- Inclusion: Include all items you see and end with the last item right above "Summe".

### Payment method detection
Map receipt tokens to:
- CARD: EC, girocard, Maestro, Visa, Mastercard, Kreditkarte, Karte, kontaktlos, Terminal-Auth, PAN-masked numbers.
- CASH: Bar, Barzahlung, Wechselgeld.
- OTHER: Gutschein, Wallet, PayPal, Klarna, Apple Pay, Google Pay only if printed and not card-routed.

### Date selection
Use the purchase date (not print time) closest to payment/terminal blocks. Prefer numeric DD.MM.YYYY. If multiple, choose the one near totals or payment section.

### Merchant parsing
Extract exact merchant name and address as printed at the header (or imprint). Normalize street and postal code/city if split across lines. Do not fabricate.

### Final validation
- items[].quantity must be > 0. Monetary fields can be negative only for line_type = DEPOSIT_REFUND; otherwise non-negative.
- items[].vat_rate must be 0.19, 0.07, or null for German receipts unless another rate is explicitly shown.
- If a unit price is present, ensure abs(line_gross_cents − quantity × unit_price_cents) ≤ 1.
- If the receipt prints a per-item subtotal or overall total, ensure the sum of included items does not contradict those figures by more than a rounding cent; if conflicted, re-check linkage (quantity → product) and include the questionable line with partial data instead of dropping it.
- Absolutely do not emit standalone attribute lines (weights, “x EUR/kg”, “je/à ...”) as separate items. And do not emit lines like "Summe", "Gesamt", "Total", "Endbetrag", "Betrag", etc. as items.

### Output reminder
Return only the final JSON object matching the schema. Do not include explanations, notes, or extra keys.

Conversion sanity note: Only convert the printed price (e.g., 1,23 € or 12,99 €) into integer cents (123, 1299). Never append or remove zeros—preserve numeric precision exactly as printed.
    
"""

def _receipt_schema() -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["merchant","purchase_date_time","currency","payment_method","totals","items"],
        "properties": {
            "merchant": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name","address"],
                "properties": {
                    "name": {"type": "string"},
                    "address": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["street","city","postal_code","country"],
                        "properties": {
                            "street": {"type": ["string","null"]},
                            "city": {"type": ["string","null"]},
                            "postal_code": {"type": ["string","null"]},
                            "country": {"type": ["string","null"]},
                        },
                    },
                },
            },
            "purchase_date_time": {"type": "string"},
            "currency": {"type": "string"},
            "payment_method": {"type": "string", "enum": ["CASH","CARD","OTHER"]},
            "totals": {
                "type": "object",
                "additionalProperties": False,
                "required": ["total_net","total_tax","total_gross"],
                "properties": {
                    "total_net": {"type": ["integer","null"]},
                    "total_tax": {"type": ["integer","null"]},
                    "total_gross": {"type": ["integer","null"]},
                },
            },
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["product_name","quantity","unit","unit_price_net","unit_price_gross","tax_rate","line_net","line_tax","line_gross","line_type"],
                    "properties": {
                        "product_name": {"type": "string"},
                        "quantity": {"type": "number"},
                        "unit": {"type": ["string","null"]},
                        "unit_price_net": {"type": ["integer","null"]},
                        "unit_price_gross": {"type": ["integer","null"]},
                        "tax_rate": {"type": "number", "enum": [0.0,0.07,0.19]},
                        "line_net": {"type": ["integer","null"]},
                        "line_tax": {"type": ["integer","null"]},
                        "line_gross": {"type": ["integer","null"]},
                        "line_type": {"type": "string", "enum": list(LINE_TYPE_CHOICES)},
                    },
                },
            },
        },
    }


def _scavenge_json_block(s: str) -> Optional[Dict[str, Any]]:
    if not s:
        return None
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    for idx in range(end, start, -1):
        try:
            return json.loads(s[start:idx + 1])
        except Exception:
            continue
    return None


class PayloadNormalizer:
    """Normalize OpenRouter JSON into the shape expected by the parser/service."""

    DATE_FALLBACK_FORMATS: Tuple[str, ...] = (
        "%d.%m.%Y",
        "%d.%m.%y",
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%Y/%m/%d",
    )
    TOTAL_HEADER_TOKENS: Tuple[str, ...] = (
        "summe",
        "gesamt",
        "total",
        "zwischensumme",
        "endsumme",
        "endbetrag",
    )

    def __init__(self, facts: FileFacts) -> None:
        self.facts = facts

    def normalize(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        merchant = self._normalize_merchant(raw.get("merchant"))
        purchase_date_time = self._normalize_purchase_date(
            raw.get("purchase_date_time") or raw.get("date") or raw.get("purchase_date")
        )
        currency = (str(raw.get("currency")) if raw.get("currency") else "EUR").upper()
        payment_method = (str(raw.get("payment_method")) if raw.get("payment_method") else "OTHER").upper()

        payload_items = raw.get("items")
        if not isinstance(payload_items, list):
            payload_items = []
        if LOG.isEnabledFor(logging.DEBUG):
            LOG.debug("Raw items from model: %d", len(payload_items))
            for idx, candidate in enumerate(payload_items, 1):
                if isinstance(candidate, dict):
                    raw_name = candidate.get("product_name") or candidate.get("name") or candidate.get("description")
                    LOG.debug("RAW ITEM %02d: %r", idx, raw_name if raw_name else candidate)
                else:
                    LOG.debug("RAW ITEM %02d (non-dict): %r", idx, candidate)

        items = [item for item in self._normalize_items(payload_items) if item]
        if LOG.isEnabledFor(logging.DEBUG):
            LOG.debug("Normalized items after filters: %d", len(items))
            for idx, item in enumerate(items, 1):
                LOG.debug("NORM ITEM %02d: %r", idx, item.get("product_name"))
            dropped = len(payload_items) - len(items)
            if dropped > 0:
                LOG.debug("Items dropped during normalization: %d", dropped)
        totals = self._normalize_totals(raw.get("totals"), items)

        normalized: Dict[str, Any] = {
            "merchant": merchant,
            "purchase_date_time": purchase_date_time,
            "currency": currency,
            "payment_method": payment_method,
            "totals": totals,
            "items": items,
            "source_file": self.facts.as_dict(),
        }

        raw_content = raw.get("raw_content")
        if isinstance(raw_content, str) and raw_content.strip():
            normalized["raw_content"] = raw_content.strip()

        return normalized

    # ---- merchant/address helpers -------------------------------------------------
    def _normalize_merchant(self, payload: Any) -> Dict[str, Any]:
        merchant_raw = payload if isinstance(payload, dict) else {}
        address_raw = merchant_raw.get("address") if isinstance(merchant_raw.get("address"), dict) else {
            "street": merchant_raw.get("street"),
            "city": merchant_raw.get("city"),
            "postal_code": merchant_raw.get("postal_code"),
            "country": merchant_raw.get("country"),
        }

        def _clean(value: Any) -> Optional[str]:
            if isinstance(value, str) and value.strip():
                return value.strip()
            return None

        merchant = {
            "name": _clean(merchant_raw.get("name")) or "",
            "address": {
                "street": _clean(address_raw.get("street")),
                "city": _clean(address_raw.get("city")),
                "postal_code": _clean(address_raw.get("postal_code")),
                "country": _clean(address_raw.get("country")),
            },
        }
        return merchant

    # ---- item helpers -------------------------------------------------------------
    def _normalize_items(self, payload_items: List[Any]) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for candidate in payload_items:
            if not isinstance(candidate, dict):
                continue
            item = self._normalize_item(candidate)
            if item:
                items.append(item)
        return items

    def _normalize_line_type(
        self,
        raw_value: Any,
        *,
        product_name: str,
        line_amounts: Tuple[Optional[int], Optional[int], Optional[int]],
    ) -> str:
        if isinstance(raw_value, str):
            candidate = raw_value.strip().upper()
            if candidate in LINE_TYPE_CHOICES:
                return candidate

        net, tax, gross = line_amounts
        amounts = [value for value in (gross, net, tax) if value is not None]
        negative_present = any(value is not None and value < 0 for value in amounts)
        lower_name = (product_name or "").lower()

        if any(keyword in lower_name for keyword in DISCOUNT_KEYWORDS):
            return LINE_TYPE_DISCOUNT

        if any(keyword in lower_name for keyword in PFAND_KEYWORDS):
            return LINE_TYPE_DEPOSIT_REFUND if negative_present else LINE_TYPE_DEPOSIT_CHARGE

        if negative_present:
            return LINE_TYPE_DEPOSIT_REFUND

        return LINE_TYPE_DEFAULT

    def _normalize_item(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        name = self._text(
            item.get("product_name") or item.get("name") or item.get("description")
        )
        if not name:
            return None
        if self._looks_like_total_header(name):
            LOG.warning("Dropping item because it looks like a header/total line: %r", name)
            return None

        quantity = self._coerce_quantity(item.get("quantity") or item.get("amount"))
        tax_rate = self._normalize_tax_rate(
            item.get("tax_rate") or item.get("tax") or item.get("vat_rate")
        )

        line_gross = self._coerce_int_cents(item.get("line_gross") or item.get("gross") or item.get("total"))
        line_net = self._coerce_int_cents(item.get("line_net"))
        line_tax = self._coerce_int_cents(item.get("line_tax"))
        line_type = self._normalize_line_type(
            item.get("line_type"),
            product_name=name,
            line_amounts=(line_net, line_tax, line_gross),
        )
        raw_unit_price_gross = self._coerce_int_cents(item.get("unit_price_gross"))

        if (
            raw_unit_price_gross is not None
            and line_gross is not None
            and quantity > 1.0
            and abs(line_gross) == abs(raw_unit_price_gross)
        ):
            corrected_gross = int(abs(raw_unit_price_gross) * quantity)
            if line_gross < 0:
                corrected_gross *= -1
            LOG.debug(
                "Correcting line_gross for '%s' from %s to %s using quantity %.2f and unit_price_gross %s",
                name,
                line_gross,
                corrected_gross,
                quantity,
                raw_unit_price_gross,
            )
            line_gross = corrected_gross
            line_net = None
            line_tax = None

        if line_net is None or line_tax is None:
            computed_net, computed_tax = self._compute_net_and_tax(line_gross, tax_rate)
            line_net = line_net if line_net is not None else computed_net
            line_tax = line_tax if line_tax is not None else computed_tax

        if line_net is not None and line_gross is not None and line_tax is None:
            line_tax = line_gross - line_net
        if line_tax is not None and line_gross is not None and line_net is None:
            line_net = line_gross - line_tax

        allow_negative = line_type in LINE_TYPES_ALLOWING_NEGATIVES
        line_net = self._ensure_non_negative(
            line_net,
            field_name="line_net",
            item_name=name,
            allow_negative=allow_negative,
        )
        line_tax = self._ensure_non_negative(
            line_tax,
            field_name="line_tax",
            item_name=name,
            allow_negative=allow_negative,
        )
        line_gross = self._ensure_non_negative(
            line_gross,
            field_name="line_gross",
            item_name=name,
            allow_negative=allow_negative,
        )
        unit_price_gross = raw_unit_price_gross
        if unit_price_gross is None:
            unit_price_gross = self._compute_unit_value(line_gross, quantity)

        unit_price_net = self._coerce_int_cents(item.get("unit_price_net"))
        if unit_price_net is None:
            unit_price_net = self._compute_unit_value(line_net, quantity)

        quantity = self._adjust_quantity_from_unit_price(
            quantity=quantity,
            raw_unit_price_gross=raw_unit_price_gross,
            line_gross=line_gross,
            item_name=name,
        )

        normalized = {
            "product_name": name,
            "quantity": float(quantity),
            "unit": self._text(item.get("unit") or item.get("measure")),
            "unit_price_net": unit_price_net,
            "unit_price_gross": unit_price_gross,
            "tax_rate": float(tax_rate),
            "line_net": line_net,
            "line_tax": line_tax,
            "line_gross": line_gross,
            "line_type": line_type,
        }
        return normalized

    @staticmethod
    @staticmethod
    def _levenshtein(left: str, right: str) -> int:
        """Compute Levenshtein distance for short strings."""
        m, n = len(left), len(right)
        if m == 0:
            return n
        if n == 0:
            return m
        prev = list(range(n + 1))
        for i, ca in enumerate(left, 1):
            curr = [i] + [0] * n
            for j, cb in enumerate(right, 1):
                cost = 0 if ca == cb else 1
                curr[j] = min(
                    prev[j] + 1,      # deletion
                    curr[j - 1] + 1,  # insertion
                    prev[j - 1] + cost,  # substitution
                )
            prev = curr
        return prev[n]

    @classmethod
    def _looks_like_total_header(cls, name: str) -> bool:
        """Return True if text resembles a receipt total/header line."""
        if not name:
            return False
        lowered = name.lower().strip()
        if not lowered:
            return False
        header_tokens_pattern = r"^(?:%s)\b" % "|".join(re.escape(token) for token in cls.TOTAL_HEADER_TOKENS)
        if re.match(header_tokens_pattern, lowered):
            return True
        stripped = re.sub(r"\[[^\]]*\]", "", lowered)
        tokens = re.findall(r"[a-zäöüß]+", stripped)
        if not tokens:
            return False
        first_token = tokens[0]
        # aggressively catch common OCR slips of "SUMME" while keeping other
        # headers strict to reduce accidental matches from product names.
        if re.fullmatch(r"su[mn][mn]e", first_token):
            return True

        targets = ("summe", "gesamt", "total")
        for target in targets:
            max_dist = 1
            if target == "summe" and len(first_token) == 5:
                max_dist = 2
            if cls._levenshtein(first_token, target) <= max_dist:
                return True
        return False

    # ---- totals helpers -----------------------------------------------------------
    def _normalize_totals(self, totals_payload: Any, items: List[Dict[str, Any]]) -> Dict[str, Optional[int]]:
        totals_raw = totals_payload if isinstance(totals_payload, dict) else {}
        totals = {
            "total_net": self._coerce_int_cents(totals_raw.get("total_net")),
            "total_tax": self._coerce_int_cents(totals_raw.get("total_tax")),
            "total_gross": self._coerce_int_cents(totals_raw.get("total_gross")),
        }

        computed_totals = self._summarize_totals(items)
        for key, value in computed_totals.items():
            if totals.get(key) is None and value is not None:
                totals[key] = value
        return totals

    # ---- primitive helpers -------------------------------------------------------
    @staticmethod
    def _text(value: Any) -> Optional[str]:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @staticmethod
    def _round_half_up(value: Decimal) -> int:
        try:
            return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        except InvalidOperation:
            return int(value)

    @staticmethod
    def _coerce_decimal(value: Any) -> Optional[Decimal]:
        if value is None:
            return None
        if isinstance(value, int):
            return Decimal(value)
        if isinstance(value, float):
            return Decimal(str(value))
        if isinstance(value, str) and value.strip():
            try:
                return Decimal(value.replace(",", "."))
            except Exception:
                return None
        return None

    @classmethod
    def _coerce_int_cents(cls, value: Any) -> Optional[int]:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(round(value))
        if isinstance(value, str) and value.strip():
            try:
                cleaned = value.strip().replace("€", "").replace(",", ".")
                if "." in cleaned:
                    return int(round(float(cleaned)))
                return int(cleaned)
            except Exception:
                return None
        decimal_value = cls._coerce_decimal(value)
        return int(decimal_value) if decimal_value is not None else None

    @staticmethod
    def _coerce_quantity(value: Any) -> float:
        if value is None:
            return 1.0
        try:
            qty = float(value)
            return qty if qty > 0 else 1.0
        except Exception:
            return 1.0

    @staticmethod
    def _normalize_tax_rate(value: Any) -> float:
        candidate: Optional[float] = None
        if isinstance(value, (int, float)):
            candidate = float(value)
        elif isinstance(value, str) and value.strip():
            try:
                candidate = float(value.strip().replace("%", ""))
                if candidate > 1:
                    candidate /= 100.0
            except Exception:
                candidate = None

        if candidate is None:
            return 0.19

        for target in (0.0, 0.07, 0.19):
            if abs(candidate - target) < 0.02:
                return target
        return 0.19 if candidate > 0.1 else 0.07

    @classmethod
    def _compute_net_and_tax(cls, line_gross: Optional[int], tax_rate: float) -> Tuple[Optional[int], Optional[int]]:
        if line_gross is None:
            return None, None
        if tax_rate in (None, 0.0):
            return line_gross, 0

        gross_dec = Decimal(line_gross)
        divisor = Decimal("1") + Decimal(str(tax_rate))
        try:
            net_dec = gross_dec / divisor
        except InvalidOperation:
            return None, None

        net = cls._round_half_up(net_dec)
        tax = int(gross_dec - Decimal(net))
        return net, tax

    @classmethod
    def _compute_unit_value(cls, total_cents: Optional[int], quantity: float) -> Optional[int]:
        if total_cents is None or quantity <= 0:
            return total_cents
        try:
            per_unit = Decimal(total_cents) / Decimal(str(quantity))
        except InvalidOperation:
            return total_cents
        return cls._round_half_up(per_unit)

    def _adjust_quantity_from_unit_price(
        self,
        *,
        quantity: float,
        raw_unit_price_gross: Optional[int],
        line_gross: Optional[int],
        item_name: str,
    ) -> float:
        if (
            raw_unit_price_gross is None
            or raw_unit_price_gross == 0
            or line_gross is None
            or quantity <= 0
        ):
            return quantity
        try:
            candidate = Decimal(abs(line_gross)) / Decimal(abs(raw_unit_price_gross))
        except InvalidOperation:
            return quantity
        candidate_int = candidate.to_integral_value(rounding=ROUND_HALF_UP)
        if candidate_int < 1:
            return quantity
        if abs(candidate - candidate_int) > Decimal("0.01"):
            return quantity
        candidate_float = float(candidate_int)
        if abs(candidate_float - quantity) < 0.5:
            return quantity
        LOG.debug(
            "Adjusting quantity for '%s' from %.2f to %.2f using line_gross=%s and unit_price_gross=%s",
            item_name,
            quantity,
            candidate_float,
            line_gross,
            raw_unit_price_gross,
        )
        return candidate_float

    @staticmethod
    def _ensure_non_negative(
        value: Optional[int],
        *,
        field_name: str,
        item_name: str,
        allow_negative: bool = False,
    ) -> Optional[int]:
        if value is None:
            return None
        if value >= 0 or allow_negative:
            return value
        LOG.warning(
            "Normalized %s for item %s is negative (%s); clearing due to unsupported line type.",
            field_name,
            item_name or "<unknown>",
            value,
        )
        return None

    @classmethod
    def _summarize_totals(cls, items: List[Dict[str, Any]]) -> Dict[str, Optional[int]]:
        gross_values = [it.get("line_gross") for it in items if it.get("line_gross") is not None]
        net_values = [it.get("line_net") for it in items if it.get("line_net") is not None]
        tax_values = [it.get("line_tax") for it in items if it.get("line_tax") is not None]

        total_gross = sum(gross_values) if gross_values else None
        total_net = sum(net_values) if len(net_values) == len(items) else (sum(net_values) if net_values else None)
        if total_net is None and total_gross is not None and tax_values:
            total_net = total_gross - sum(tax_values)
        total_tax = sum(tax_values) if tax_values else (
            total_gross - total_net if (total_gross is not None and total_net is not None) else None
        )

        return {
            "total_net": total_net,
            "total_tax": total_tax,
            "total_gross": total_gross,
        }

    def _normalize_purchase_date(self, raw_value: Any) -> Optional[str]:
        if not raw_value:
            return None
        text = str(raw_value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return dt.replace(tzinfo=None).isoformat(timespec="seconds")
        except Exception:
            pass

        for fmt in self.DATE_FALLBACK_FORMATS:
            try:
                dt = datetime.strptime(text, fmt)
                return dt.replace(hour=12, minute=0, second=0).isoformat(timespec="seconds")
            except Exception:
                continue
        return None


def _normalize_openrouter_payload(raw: Dict[str, Any], *, facts: FileFacts) -> Dict[str, Any]:
    """Facilitate backwards compatibility for existing call sites."""

    normalizer = PayloadNormalizer(facts)
    return normalizer.normalize(raw)


class OpenRouterClient:
    """Thin wrapper around OpenRouter API requests with helpful logging."""

    ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, config: OpenRouterConfig) -> None:
        self.config = config

    # ---- core request helpers ----------------------------------------------------
    def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[int] = None,
        plugins: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[str]:
        payload = {
            "model": self.config.model_name,
            "messages": messages,
            "temperature": self.config.temperature if temperature is None else temperature,
            "max_tokens": self.config.max_tokens if max_tokens is None else max_tokens,
        }
        if plugins:
            payload["plugins"] = plugins
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(
                self.ENDPOINT,
                headers=headers,
                json=payload,
                timeout=timeout or self.config.timeout_seconds,
            )
        except Exception as exc:
            LOG.error("OpenRouter request failed: %s", exc)
            return None

        if resp.status_code >= 400:
            LOG.error("OpenRouter HTTP %s: %s", resp.status_code, resp.text[:500])
            return None

        body = resp.json()
        choices = body.get("choices") or []
        if not choices:
            LOG.error("OpenRouter returned no choices: %s", body)
            return None
        message = choices[0].get("message") or {}
        return message.get("content")

    def json_request(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[int] = None,
        plugins: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, Any]]:
        text = self.chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            plugins=plugins,
        )
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            return _scavenge_json_block(text)

    # ---- convenience helpers -----------------------------------------------------
    def guess_country(self, data_url: str, context: Dict[str, Any], *, facts: FileFacts) -> Optional[str]:
        context_text = json.dumps(context, ensure_ascii=False)
        prompt = (
            "Estimate the likely ISO 3166-1 alpha-2 country code for the merchant on this retail receipt. "
            "Use signals such as language, city names, postal codes, addresses, currency symbols, and other hints. "
            "Return strict JSON with a single key 'country'."
        )
        content_node = _openrouter_content_node(data_url, facts)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"{prompt}\n\nStructured context:\n{context_text}"},
                    content_node,
                ],
            }
        ]
        result = self.json_request(
            messages,
            max_tokens=200,
            timeout=150,
            plugins=_openrouter_plugins_for_mime(facts.mime_type),
        )
        if not result:
            return None
        country = result.get("country")
        if isinstance(country, str) and country.strip():
            return country.strip().upper()
        return None

    def fetch_raw_content(self, data_url: str, *, facts: FileFacts) -> Optional[str]:
        prompt = (
            "Transcribe the receipt exactly as text, preserving line order. "
            "Return strict JSON with key 'raw_content' containing the transcription."
        )
        content_node = _openrouter_content_node(data_url, facts)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    content_node,
                ],
            }
        ]
        result = self.json_request(
            messages,
            max_tokens=6000,
            timeout=240,
            plugins=_openrouter_plugins_for_mime(facts.mime_type),
        )
        if not result:
            return None
        text = result.get("raw_content")
        if isinstance(text, str) and text.strip():
            return text.strip()
        return None

def extract_receipt_payload_from_image(
    source_path: str,
    *,
    model_name: str = "gpt-5-mini",
    script_dir: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    # Dispatch based on backend (env can override module default)
    backend = (os.getenv("PRODUCTDB_BACKEND") or BACKEND).strip().lower()
    if backend not in {"openai", "ollama", "openrouter"}:
        LOG.warning("Unknown PRODUCTDB_BACKEND=%r; defaulting to 'openai'", backend)
        backend = "openai"

    mime, _ = mimetypes.guess_type(source_path)
    is_pdf = (mime == "application/pdf") or source_path.lower().endswith(".pdf")

    if backend == "ollama":
        if is_pdf:
            LOG.error("Ollama backend does not support PDF extraction yet; skipping %s", source_path)
            return None
        LOG.info("Backend selected: Ollama")
        LOG.debug("Effective Ollama model: %s", MODEL)
        return _extract_with_ollama(source_path, script_dir=script_dir, model_tag=MODEL)
    if backend == "openrouter":
        LOG.info("Backend selected: OpenRouter")
        effective_model = (os.getenv("OPENROUTER_MODEL") or OPENROUTER_MODEL).strip()
        LOG.debug("Effective OpenRouter model: %s", effective_model)
        return _extract_with_openrouter(source_path, script_dir=script_dir, model_name=effective_model)

    # OpenAI path below
    if is_pdf:
        LOG.error("OpenAI backend does not support PDF extraction in this workflow; skipping %s", source_path)
        return None
    api_key = load_openai(script_dir or os.getcwd())
    if not api_key:
        LOG.error("OPENAI_API_KEY missing in env/.env; cannot run extraction")
        return None

    url = _b64_data_url(source_path)
    if not url:
        return None

    facts = _file_facts(source_path)
    approx_payload_mb = round((len(url) / (1024 * 1024)), 2)

    base_url = os.environ.get("OPENAI_BASE_URL")
    try:
        import openai as _openai_mod
        sdk_ver = getattr(_openai_mod, "__version__", "?")
    except Exception:
        sdk_ver = "?"

    LOG.debug(
        "Preparing request: file=%s bytes=%s sha256=%s (~data URL %.2f MiB) base_url=%s openai=%s httpx=%s py=%s",
        facts.get("filename"), facts.get("byte_size"), facts.get("sha256"), approx_payload_mb,
        base_url or "default", sdk_ver, httpx.__version__, sys.version.split()[0],
    )
    if approx_payload_mb > 15:
        LOG.warning("Large payload (~%.2f MiB). Consider downscaling before base64 to improve latency.", approx_payload_mb)

    # ---- helpers ------------------------------------------------------------
    def _scavenge_json(s: str) -> Optional[Dict[str, Any]]:
        if not s:
            return None
        start = s.find("{")
        end = s.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        for j in range(end, start, -1):
            try:
                return json.loads(s[start:j+1])
            except Exception:
                continue
        return None

    def _attach_meta(d: Dict[str, Any], resp_obj: Any = None) -> Dict[str, Any]:
        d.setdefault("source_file", facts)
        d.setdefault("_extraction_meta", {
            "model": model_name,
            "at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "response_id": getattr(resp_obj, "id", None),
        })
        return d

    http_client = httpx.Client(
        http2=True,
        timeout=httpx.Timeout(connect=10.0, read=90.0, write=30.0, pool=10.0),
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=30),
    )
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        http_client=http_client,
        max_retries=0,
    )

    if (os.environ.get("OPENAI_LOG") or "").lower() == "debug":
        logging.getLogger("httpx").setLevel(logging.DEBUG)
        logging.getLogger("httpcore").setLevel(logging.DEBUG)

    prompt = _prompt()
    schema = _receipt_schema()

    # ---- Attempt A: Responses.create (no response_format, because your SDK rejects it)
    data = None
    t0 = time.perf_counter()
    try:
        LOG.info("Calling OpenAI Responses API (NON-STREAM, no response_format) model='%s'…", model_name)
        resp = client.responses.create(
            model=model_name,
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt + "\n\nReturn ONLY a single JSON object that matches the schema I described."},
                    {"type": "input_image", "image_url": url},
                ],
            }],
            stream=False,
            timeout=120.0,
        )
        print(f"RESPONSE: {json.dumps(resp, indent=4)}")

        # 1) structured block
        try:
            out_list = getattr(resp, "output", None) or []
            for out in out_list:
                for block in getattr(out, "content", []) or []:
                    if getattr(block, "type", None) == "output_json":
                        d = getattr(block, "json", None)
                        if isinstance(d, dict) and d:
                            data = d
                            break
                if data:
                    break
        except Exception as e:
            LOG.debug("Reading output_json failed: %s", e)

        # 2) output_parsed (some SDKs expose it)
        if data is None:
            parsed = getattr(resp, "output_parsed", None)
            if isinstance(parsed, dict) and parsed:
                data = parsed

        # 3) output_text → parse/scavenge
        if data is None:
            text = getattr(resp, "output_text", None)
            if text:
                try:
                    data = json.loads(text)
                except Exception:
                    data = _scavenge_json(text)
                    if data is None:
                        LOG.error("Responses.output_text not valid JSON; first 500 chars: %r", text[:500])

        dt = time.perf_counter() - t0
        rid = getattr(resp, "id", None)
        usage = getattr(resp, "usage", None)
        usage_dict = {k: getattr(usage, k, None) if usage else None for k in ("input_tokens","output_tokens","total_tokens")}
        LOG.info("Responses finished in %.2fs id=%s usage=%s (data=%s)", dt, rid, usage_dict, "ok" if data else "none")

        if isinstance(data, dict) and data:
            return _attach_meta(data, resp)

    except (APIConnectionError, APITimeoutError) as e:
        LOG.error("Network/timeout while calling OpenAI (Responses): %s", e)
        LOG.info("Hints: proxy/SSL interception/VPN; OPENAI_LOG=debug; allow HTTP/2 on 443.")
    except APIStatusError as e:
        body = getattr(getattr(e, "response", None), "text", None)
        LOG.error("OpenAI API (Responses) returned %s. Body preview: %r", getattr(e, "status_code", "?"), (body[:300] if body else None))
    except TypeError as e:
        # Keep for completeness, though we removed response_format already.
        LOG.error("SDK TypeError in Responses.create: %s", e)
    except Exception as e:
        LOG.error("OpenAI extraction failed in Responses path: %s", e)

    # ---- Attempt B: Chat Completions (vision)
    # Prefer JSON object mode if supported; otherwise ask for JSON plainly and scavenge.
    try:
        LOG.info("Falling back to Chat Completions (VISION) model='%s'…", model_name)

        # Build messages in the modern “vision” shape
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a strict JSON generator. Output ONLY a single JSON object that matches the provided schema. "
                    "No prose, no markdown fences, no trailing text."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": url}},
                ],
            },
        ]

        # Try JSON mode first (some SDKs support this on chat.completions)
        json_mode_supported = True
        completion = None
        try:
            completion = client.chat.completions.create(
                model=model_name,
                messages=messages,
                response_format={"type": "json_object"},
                timeout=120.0,
            )
        except TypeError:
            json_mode_supported = False

        if completion is None and not json_mode_supported:
            # Retry without response_format; rely on instruction + scavenger
            completion = client.chat.completions.create(
                model=model_name,
                messages=messages,
                timeout=120.0,
            )

        # Parse result
        choice = (completion.choices[0] if getattr(completion, "choices", None) else None)
        text = (choice.message.content if choice and getattr(choice, "message", None) else None)

        parsed = None
        if text:
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = _scavenge_json(text)
                if parsed is None:
                    LOG.error("Chat output not valid JSON; first 500 chars: %r", (text[:500] if isinstance(text, str) else text))

        rid = getattr(completion, "id", None)
        usage = getattr(completion, "usage", None)
        usage_dict = {k: getattr(usage, k, None) if usage else None for k in ("prompt_tokens","completion_tokens","total_tokens")}
        LOG.info("Chat completion finished id=%s usage=%s (data=%s)", rid, usage_dict, "ok" if isinstance(parsed, dict) else "none")

        if isinstance(parsed, dict) and parsed:
            return _attach_meta(parsed, completion)

    except (APIConnectionError, APITimeoutError) as e:
        LOG.error("Network/timeout while calling OpenAI (Chat): %s", e)
    except APIStatusError as e:
        body = getattr(getattr(e, "response", None), "text", None)
        LOG.error("OpenAI API (Chat) returned %s. Body preview: %r", getattr(e, "status_code", "?"), (body[:300] if body else None))
    except Exception as e:
        LOG.error("OpenAI extraction failed in Chat path: %s", e)
    finally:
        http_client.close()

    LOG.error("No extraction payload produced after non-stream attempts (Responses and Chat).")
    return None


# --------------------------- OLLAMA BACKEND ---------------------------
def _extract_with_ollama(
    source_path: str,
    *,
    script_dir: Optional[str],
    model_tag: str,
) -> Optional[Dict[str, Any]]:
    """Extract structured receipt JSON using a local Ollama model.

    Preference: use provided OLLAMA_URL/OLLAMA_MODEL from .env or env.
    If a model name is supplied via `model_name` and PRODUCTDB_BACKEND=ollama,
    it is used if it looks like an Ollama tag; otherwise the env/.env value is
    preferred. Defaults to 'gemma3:4b' as requested.
    """
    # Resolve Ollama connection and model
    url_from_env, model_from_env = load_ollama(script_dir or os.getcwd())
    chosen_model = (model_from_env or model_tag or "gemma3:4b").strip()

    base = (url_from_env or "http://localhost:11434").strip()
    chat_endpoint = base if base.endswith("/api/chat") else base.rstrip("/") + "/api/chat"

    # Prepare image as base64 (Ollama expects raw base64 strings in 'images')
    try:
        with open(source_path, "rb") as f:
            raw = f.read()
            img_b64 = base64.b64encode(raw).decode("utf-8")
    except Exception as e:
        LOG.error("Failed reading image for Ollama extraction: %s", e)
        return None
    LOG.debug("Loaded image bytes=%s (base64 chars=%s)", len(raw), len(img_b64))

    def _is_probably_vision_model(tag: str) -> bool:
        s = (tag or "").lower()
        vision_markers = [
            "vl", "vision", "llava", "minicpm", "bakllava", "moondream",
            "qwen2-vl", "qwen2.5vl", "qwen2.5-vl", "llama3.2-vision", "phi-3-vision",
            "internvl", "cambrian-pro", "kosmos"
        ]
        return any(m in s for m in vision_markers)

    if not _is_probably_vision_model(chosen_model):
        LOG.info("Model '%s' is likely text-only; using OCR→JSON fallback.", chosen_model)
        return _ollama_two_step_fallback(base, chosen_model, source_path)

    prompt = _prompt()
    schema = _receipt_schema()  # unused directly but kept for parity

    # Instruction: enforce strict JSON
    system = (
        "You are a strict JSON generator. Output ONLY a single JSON object "
        "matching the provided schema description. No prose, no markdown."
    )
    user_text = (
        prompt
        + "\n\nReturn ONLY a single JSON object that exactly matches the structure and constraints above."
    )

    payload = {
        "model": chosen_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text, "images": [img_b64]},
        ],
        "stream": False,
        "options": {
            "temperature": 0,
            "num_ctx": 16384,
            "num_predict": 4096,
        },
    }

    LOG.info("Calling Ollama for receipt extraction")
    LOG.debug("Backend=ollama model=%s endpoint=%s", chosen_model, chat_endpoint)

    def _scavenge_json(s: str) -> Optional[Dict[str, Any]]:
        if not s:
            return None
        start = s.find("{")
        end = s.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        for j in range(end, start, -1):
            try:
                return json.loads(s[start:j + 1])
            except Exception:
                continue
        return None

    facts = _file_facts(source_path)

    try:
        resp = requests.post(chat_endpoint, json=payload, timeout=180)
        if resp.status_code >= 400:
            LOG.error("Ollama HTTP %s: %s", resp.status_code, resp.text[:500])
            # If this looks like a non-vision model error, attempt a 2-step fallback
            return _ollama_two_step_fallback(base, chosen_model, source_path)
        data = resp.json()
    except Exception as e:
        LOG.error("Ollama request failed: %s", e)
        return _ollama_two_step_fallback(base, chosen_model, source_path)

    if data.get("error"):
        LOG.error("Ollama error: %s", data.get("error"))
        return _ollama_two_step_fallback(base, chosen_model, source_path)

    message = data.get("message") or {}
    content = message.get("content") or data.get("response") or ""

    print(content)

    parsed: Optional[Dict[str, Any]] = None
    if content:
        try:
            parsed = json.loads(content)
        except Exception:
            parsed = _scavenge_json(content)
            if parsed is None:
                LOG.error("Ollama content not valid JSON; first 500 chars: %r", content[:500])

    def _looks_like_sample(d: Dict[str, Any]) -> bool:
        try:
            m = (d or {}).get("merchant", {})
            items = (d or {}).get("items", [])
            return (
                isinstance(m, dict) and m.get("name") == "REWE Supermarkt Musterstadt"
                or any(isinstance(i, dict) and i.get("product_name") == "Vollkornbrot 750g" for i in items)
            )
        except Exception:
            return False

    if isinstance(parsed, dict) and parsed:
        if _looks_like_sample(parsed):
            LOG.warning("Ollama output resembles the sample; falling back to OCR→JSON.")
            return _ollama_two_step_fallback(base, chosen_model, source_path)
        parsed.setdefault("source_file", facts)
        parsed.setdefault(
            "_extraction_meta",
            {
                "model": chosen_model,
                "at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "backend": "ollama",
            },
        )
        LOG.info("Ollama extraction parsed successfully.")
        return parsed

    LOG.warning("Primary Ollama attempt returned no JSON; trying two-step fallback (OCR → JSON)…")
    return _ollama_two_step_fallback(base, chosen_model, source_path)


def _ollama_two_step_fallback(ollama_url: str, model_tag: str, source_path: str) -> Optional[Dict[str, Any]]:
    """Fallback for non-vision models: OCR via vision model then JSON via `model_tag`.

    - Transcribes the image using an Ollama vision model (from env OLLAMA_OCR_MODEL
      or defaults to qwen2.5vl-receipt:latest), then asks `model_tag` to produce
      strict JSON from that transcript.
    """
    # Choose a vision model for OCR step
    vision_model = os.environ.get("OLLAMA_OCR_MODEL", "qwen2.5vl-receipt:latest").strip()

    # Import locally to avoid circulars at module import time
    try:
        from ..transcribe import transcribe_image as _transcribe
    except Exception as e:
        LOG.error("Cannot import OCR helper for Ollama fallback: %s", e)
        return None

    transcript = _transcribe(source_path, ollama_url=ollama_url, model=vision_model, echo=False)
    if not transcript:
        LOG.error("Ollama OCR transcript is empty; giving up.")
        return None

    system = (
        "You are a strict JSON generator. Output ONLY a single JSON object "
        "matching the schema described. No prose, no markdown."
    )
    prompt = _prompt()
    user_text = (
        f"Here is the raw OCR transcript of a retail receipt between <transcript> tags.\n"
        f"Use it to extract the structured JSON according to the schema rules.\n\n"
        f"<transcript>\n{transcript}\n</transcript>\n\n"
        f"Return ONLY a single JSON object with the exact structure and constraints."
    )

    payload = {
        "model": model_tag,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt + "\n\n" + user_text},
        ],
        "stream": False,
        "options": {
            "temperature": 0,
            "num_ctx": 16384,
            "num_predict": 4096,
        },
    }

    chat_endpoint = ollama_url if ollama_url.endswith("/api/chat") else ollama_url.rstrip("/") + "/api/chat"
    try:
        resp = requests.post(chat_endpoint, json=payload, timeout=180)
        if resp.status_code >= 400:
            LOG.error("Ollama HTTP %s in fallback: %s", resp.status_code, resp.text[:500])
            return None
        data = resp.json()
    except Exception as e:
        LOG.error("Ollama fallback request failed: %s", e)
        return None

    if data.get("error"):
        LOG.error("Ollama fallback error: %s", data.get("error"))
        return None

    message = data.get("message") or {}
    content = message.get("content") or data.get("response") or ""

    print(content)

    def _scavenge_json(s: str) -> Optional[Dict[str, Any]]:
        if not s:
            return None
        start = s.find("{")
        end = s.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        for j in range(end, start, -1):
            try:
                return json.loads(s[start:j + 1])
            except Exception:
                continue
        return None

    parsed: Optional[Dict[str, Any]] = None
    if content:
        try:
            parsed = json.loads(content)
        except Exception:
            parsed = _scavenge_json(content)
            if parsed is None:
                LOG.error("Ollama fallback content not valid JSON; first 500 chars: %r", content[:500])

    if isinstance(parsed, dict) and parsed:
        facts = _file_facts(source_path)
        parsed.setdefault("source_file", facts)
        parsed.setdefault(
            "_extraction_meta",
            {
                "model": model_tag,
                "at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "backend": "ollama",
                "note": "two-step ocr→json",
            },
        )
        LOG.info("Ollama fallback extraction parsed successfully.")
        return parsed

    LOG.error("Ollama fallback produced no valid JSON.")
    return None


# ------------------------ OPENROUTER BACKEND -------------------------
def _extract_with_openrouter(
    source_path: str,
    *,
    script_dir: Optional[str],
    model_name: str,
) -> Optional[Dict[str, Any]]:
    """Use OpenRouter chat.completions with base64 data URL and our prompt.

    Follows the example provided (requests + base64 image) and adds
    max_tokens for larger outputs.
    """
    api_key = load_openrouter(script_dir or os.getcwd())
    if not api_key:
        LOG.error("OPEN_ROUTER_API_KEY missing in env/.env; cannot run extraction")
        return None

    effective_model = (model_name or "").strip()
    if not effective_model:
        LOG.error("No OpenRouter model configured via OPENROUTER_MODEL or parameter.")
        return None

    data_url = _b64_data_url(source_path)
    if not data_url:
        return None

    facts = FileFacts.from_dict(_file_facts(source_path))
    content_node = _openrouter_content_node(data_url, facts)
    plugins = _openrouter_plugins_for_mime(facts.mime_type)
    prompt = _prompt()
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                content_node,
            ],
        }
    ]

    config = OpenRouterConfig(api_key=api_key, model_name=effective_model)
    client = OpenRouterClient(config)

    LOG.info("Calling OpenRouter model=%s for structured extraction", effective_model)
    parsed = client.json_request(
        messages,
        max_tokens=8000,
        timeout=180,
        plugins=plugins,
    )
    if not isinstance(parsed, dict) or not parsed:
        LOG.error("OpenRouter returned no valid JSON for structured extraction.")
        return None

    normalized = _normalize_openrouter_payload(parsed, facts=facts)

    address = (normalized.get("merchant") or {}).get("address") or {}
    context_for_country = {
        "merchant": normalized.get("merchant"),
        "currency": normalized.get("currency"),
        "city": address.get("city"),
        "postal_code": address.get("postal_code"),
    }

    enrichment = normalized.setdefault("_enrichment", {})

    if not address.get("country"):
        LOG.info("Country missing; invoking OpenRouter guess helper.")
        guessed_country = client.guess_country(
            data_url,
            context_for_country,
            facts=facts,
        )
        if guessed_country:
            address["country"] = guessed_country
        enrichment["guessed_country"] = guessed_country

    if not normalized.get("raw_content"):
        LOG.info("raw_content missing; requesting transcription via OpenRouter.")
        raw_text = client.fetch_raw_content(data_url, facts=facts)
        if raw_text:
            normalized["raw_content"] = raw_text
        enrichment["raw_content_fetched"] = bool(raw_text)

    normalized.setdefault(
        "_extraction_meta",
        {
            "model": effective_model,
            "at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "backend": "openrouter",
        },
    )

    LOG.info("OpenRouter extraction parsed and enriched successfully.")
    return normalized


# ------------------------------ SELF-RUNNER ------------------------------
if __name__ == "__main__":
    """Very simple direct runner for OpenRouter.

    - Hardcoded image path is used if no CLI arg is provided.
    - Reads API key and optional model from .env via config loaders.
    - Prints the parsed JSON result; minimal logging.
    """
    # Hardcoded default; replace with your path if desired
    HARD_CODED_IMAGE = r"C:\\Users\\Anwender\\iCloudDrive\\Documents\\Scans\\1970-01-01_familia_betreff_1.jpeg"

    img = sys.argv[1] if len(sys.argv) > 1 else HARD_CODED_IMAGE
    if not os.path.isfile(img):
        print(f"Image not found: {img}")
        sys.exit(2)

    # Keep logs quiet
    try:
        import logging as _logging
        _logging.getLogger("productdb-extraction").setLevel(_logging.ERROR)
    except Exception:
        pass

    # Force OpenRouter backend unless already set
    os.environ.setdefault("PRODUCTDB_BACKEND", "openrouter")
    model = (os.getenv("OPENROUTER_MODEL") or OPENROUTER_MODEL).strip()

    # Call OpenRouter backend directly to avoid extra layers
    result = _extract_with_openrouter(img, script_dir=os.getcwd(), model_name=model)
    if not result:
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0)
