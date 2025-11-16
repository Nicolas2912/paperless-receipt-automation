from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
import base64, hashlib, json, logging, mimetypes, os, sys, re, unicodedata
from difflib import SequenceMatcher
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from dotenv import load_dotenv


try:
    from ...logging import get_logger
    from ...config import load_openrouter
    from ...paths import find_project_root, var_dir
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
    from paperless_automation.config import load_openrouter
    from paperless_automation.paths import find_project_root, var_dir
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

# Default model id for OpenRouter backend
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


def _slugify_for_filename(value: Optional[str], *, default: str) -> str:
    if not isinstance(value, str):
        return default
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    cleaned = cleaned.strip("-_.")
    return cleaned.lower() or default


class ModelResponseStore:
    """Persist model JSON responses per receipt run for later inspection."""

    def __init__(self, *, script_dir: Optional[str], facts: FileFacts) -> None:
        self.run_dir: Optional[str] = None
        try:
            root = find_project_root(script_dir or os.getcwd())
            base_dir = os.path.join(var_dir(root), "model_responses")
            os.makedirs(base_dir, exist_ok=True)
            timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
            slug = _slugify_for_filename(facts.filename or "receipt", default="receipt")
            sha_chunk = (facts.sha256 or "")[:8]
            run_folder = "_".join(part for part in (timestamp, slug, sha_chunk) if part)
            self.run_dir = os.path.join(base_dir, run_folder)
            os.makedirs(self.run_dir, exist_ok=True)
        except Exception as exc:
            LOG.warning("Response storage disabled: %s", exc)

    def write(self, scope: str, payload: Any) -> Optional[str]:
        if not self.run_dir or payload is None:
            return None
        filename = f"{_slugify_for_filename(scope or 'response', default='response')}.json"
        path = os.path.join(self.run_dir, filename)
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
        except Exception as exc:
            LOG.warning("Failed to persist %s response to %s: %s", scope, path, exc)
            return None
        LOG.debug("Stored %s response at %s", scope, path)
        return path


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
    reasoning_effort: Optional[str] = None


# ---------- prompt & schema ----------
def _prompt() -> str:
    return """
## Task
Extract data from a retail receipt image/PDF. Your job is to segment items, name them, and copy the printed row totals exactly. Do NOT do arithmetic: do not multiply, divide, or estimate any prices. Provide the raw transcript so post-processing can do the math.

## Output (strict)
Return ONLY strict JSON (no code fences, no commentary).
Top-level keys:
- merchant (object)
- purchase_date_time (string ISO or null)
- currency (string, e.g., "EUR")
- payment_method (string)
- totals (object with total_gross optional)
- raw_content (string, REQUIRED; full transcription, line order preserved)
- items (array)

### Schema: items (MINIMAL)
Each item MUST have:
- product_name: string (best transcription of the product line)
- line_index: integer (0-based index of the product line inside `raw_content` lines; required)
- line_gross: integer (cents), sign-preserving, copied exactly from the printed ROW TOTAL on that line (not multiplied)
- tax_rate: number OR tax_group: string ("A"|"B" etc). At least one of tax_rate/tax_group must be present.
- line_type: enum { SALE|DEPOSIT_CHARGE|DEPOSIT_REFUND } (optional if unclear; set null if unknown)

May include (optional):
- unit_price_gross: integer (cents) ONLY if it is explicitly printed on the same product line.

Do NOT include:
- quantity
- unit_price_net
- line_net
- line_tax

### Row total vs multipliers (CRITICAL)
- If you see a multiplier like "2 x 1,09 €" ABOVE the product (Aldi/Netto layout), still set line_gross to the printed ROW TOTAL on the product line (e.g., "2,18").
- If you see "2 x 1,09" INSIDE the product block (IKEA/Famila style), set line_gross to the printed ROW TOTAL. Do NOT multiply yourself.
- Never infer or multiply quantities; just copy the printed row total price.

### raw_content (REQUIRED)
Provide the full transcription in order, as a single string with newlines. Keep every line (including multipliers, weights, totals headers).

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
  { "description": string, "quantity": int, "unit_price_net": int|null, "unit_price_gross": int|null, "line_net": int, "line_gross": int, "vat_rate": number|null, "line_type": enum }

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


_FOCUSED_COMBINED_DEFAULT_PROMPT = (
    """
You are an expert at extracting precise structured data from retail receipts (grocery stores, discounters, etc.).

Your ONLY task:

Extract for each product line:
product_name
quantity
tax_rate
You MUST be extremely precise. Everything depends on your correctness.

You will be given a receipt (as an image or text). Use ALL visible information on the receipt. Think carefully, reason step by step INTERNALLY, but DO NOT print
your reasoning. Output ONLY the final JSON.

======================================================================
GENERAL TASK
Go through the receipt from top to bottom.
Identify every REAL product line (food or non-food).
For each product line, determine:
product_name: the product description on that line (or clearly associated with it).
quantity: how many units were bought.
tax_rate: VAT as a decimal, ONLY 0.07 or 0.19.
Stop before totals:

Do NOT include lines like "SUMME", "ZU ZAHLEN", "Endbetrag", "GESAMT", "TOTAL" etc.
Do NOT include payment block, card info, dates, transaction ids, coupons, or MWST summary as products.
======================================================================
JSON OUTPUT FORMAT (STRICT)
Output ONLY a JSON array.

Each element is an object with EXACTLY these keys:

[
{
"product_name": string,
"quantity": number,
"tax_rate": number
},
...
]

Do NOT include any other fields.

Do NOT wrap the JSON in Markdown code fences, text, or comments.

No trailing text. Only valid JSON.

======================================================================
TAX RATE RULES (GERMAN VAT)
Tax rate must ALWAYS be one of:

0.07 (for 7%)
0.19 (for 19%)
Use these cues:

Letter codes at end of line:

A → 19% → tax_rate = 0.19
B → 7% → tax_rate = 0.07
If a product line has "A" or "B" printed, you MUST use this mapping.
MWST (VAT) summary table at the bottom:

If the table shows ONLY one rate (e.g. "1 7,00% NETTO ... BRUTTO ..."),
then ALL products on this receipt have that rate.
Example: if only 7,00% appears, set tax_rate = 0.07 for every item.
If the table shows two groups (e.g. 7% and 19%), use the letters (A/B/etc.)
or line hints to assign the correct rate per item.
If there is NO explicit letter and more than one rate in the summary:

Use your best judgement based on product type:
Typical 7% (0.07): most basic food, dairy, bread, cereals, fruits, vegetables.
Typical 19% (0.19): household goods, detergents, cosmetics, non-food, many drinks, etc.
But if you see any clear printed indication (letters, tax columns), FOLLOW the printed indication.
Never invent other tax rates. Only 0.07 or 0.19 are allowed.

======================================================================
QUANTITY RULES – MULTIPLIER LINES (CRITICAL)
Many German supermarket receipts (e.g. Netto, Aldi) print quantity on a separate line ABOVE the product:

Examples of multiplier lines:

"2 x 9,99 €"
"2 x 1,09 €"
"2 x 1,49 €"
"2 x 1,29 €"
"3x 0,79 €"
These multiplier lines:

Contain ONLY a number (N), a multiplication sign (x or ×), and a unit price (P).
Do NOT contain the product name.
Are NOT products themselves.
ALWAYS refer to the next real product line below them.
You MUST follow this algorithm:

Detect multiplier lines with pattern "N x P" (or "NxP", with x or ×).
For each multiplier line:
a) Read N (integer quantity).
b) Read P (unit price).
c) Look at the next product line below that has a row total price G (e.g. "19,98 €").
d) Compute N * P and compare to G.
If N * P equals G (within 1 cent), then:
Set quantity = N for that product.
If N * P does NOT equal G, then this multiplier does NOT belong to that product.
Do NOT change quantity for that product because of this line.
Never apply one multiplier line to more than one product.
Multiplier lines MUST NEVER appear as separate JSON items.

======================================================================
NETTO MARKEN-DISCOUNT LAYOUT (SPECIAL CASE)
On Netto receipts:

Multiplier lines like "2x 1,11" usually appear directly ABOVE the product line they belong to.

Example:

2 x 1,11
GL. Speisequark mager 500g VLOG 1,11 B

Correct JSON:
{"product_name": "GL. Speisequark mager 500g VLOG", "quantity": 2, "tax_rate": 0.07}

You MUST:

Attach the multiplier ("2 x 1,11") to the NEXT product line ("GL. Speisequark ...").
Check that 2 * 1,11 = 2,22 and that this matches the printed row total for that line.
Set quantity = 2.
Do NOT output any JSON item for the "2 x 1,11" line itself.
======================================================================
ALDI-STYLE LAYOUT WITH MULTIPLIERS AND WEIGHT DETAILS
Example pattern:

2 x 9,99 €
Zimtschnecken 19,98 € 1

2 x 1,09 €
Schokolade Lindt 2,18 € 1


You MUST interpret this as:

"2 x 9,99 €" → belongs to "Zimtschnecken" because 2 * 9,99 = 19,98.
→ quantity = 2.

"2 x 1,09 €" → belongs to "Schokolade Lindt" because 2 * 1,09 = 2,18.
→ quantity = 2.


Correct JSON items (fields shown for clarity):

[
{"product_name": "Zimtschnecken", "quantity": 2, "tax_rate": 0.07},
{"product_name": "Schokolade Lindt", "quantity": 2, "tax_rate": 0.07},
]

======================================================================
WEIGHT DETAIL LINES (DO NOT CHANGE QUANTITY)
Weight lines look like:

"0,812 kg x 1,99 €/kg"
"1,060 kg x 1,29 EUR/kg"
"750 g x 1,99 €/kg"
Rules:

These lines are NOT products and must NOT appear in JSON.
They provide extra detail for the product above or below (often fruit/veg).
They DO NOT change the quantity:
Quantities for such items are usually 1.
The line gross price on the main product line is based on weight × price/kg.
For weighted items, set:
quantity = 1
tax_rate from the same rules as any other food item.
======================================================================
DEFAULT QUANTITY WHEN NO MULTIPLIER
If you do NOT find any clear multiplier line ("N x price") for a product, and the product is not clearly a bundle with explicit count, then set:

quantity = 1
Do NOT invent non-integer quantities for normal products.

======================================================================
FINAL SELF-CHECK BEFORE OUTPUT
Before you output the JSON:

Make sure you have included every REAL product on the receipt from the start of the item list down to just before the totals ("SUMME", "ZU ZAHLEN", "Endbetrag",
etc.).
Verify for every product:
If there is a multiplier line "N x P" immediately above it where N * P equals its row total, then quantity MUST equal N.
If NOT, quantity MUST be 1 (or a clearly indicated other integer).
Verify that every tax_rate is either 0.07 or 0.19 and is consistent with letters (A/B) and MWST summary.
Finally, output ONLY the JSON array with objects:

product_name
quantity
tax_rate
No explanations, no comments, no Markdown. Only valid JSON.
""".strip()
)

_FOCUSED_QUANTITY_DEFAULT_PROMPT = (
    """
Your job is to extract the product name and quantity of all real products from a receipt. You strive for absolute 100% accuracy. Every product name
and every quantity is absolutely correct. You use all your capabilites because this accuracy will save lifes.

Your job:
- Find every REAL product line (food or non-food).
- For each product, output the correct quantity as a positive integer.
- You MUST cover EVERY real product line from the first product down to the last product just before the totals block.
- It is an ERROR to skip a real product line. If you are unsure, include your BEST GUESS instead of omitting the line.

You MUST follow the rules and algorithm below EXACTLY.

============================================================
OUTPUT FORMAT (STRICT)
============================================================

- Return ONLY a JSON array.
- Each element MUST be an object with exactly:

  {
    "product_name": string,
    "quantity": number
  }

- product_name: the product description as printed (minor whitespace cleanup allowed).
- quantity: positive integer (1, 2, 3, …).
- There MUST be a 1:1 mapping between real product lines and JSON objects:
  - One JSON object for each real product line.
  - No missing products, no extra helper lines.
- Do NOT output tax_rate or any other fields.
- No commentary, no Markdown fences, no extra text. ONLY the JSON array.

============================================================
STEP 1 – IDENTIFY PRODUCT LINES
============================================================

A REAL product line usually:
- Contains a product name (words, abbreviations).
- Has a price on the right (row total), sometimes with a tax group letter.
- Is located between the header and the totals ("SUMME", "ZU ZAHLEN", "Endbetrag", etc.).

Do NOT treat as products:
- Lines such as "SUMME", "ZU ZAHLEN", "GESAMT", "TOTAL".
- Payment info, card info, transaction IDs, dates, MWST table.
- Pure helper lines: weight details ("0,812 kg x 1,99 €/kg"), multipliers without names ("2 x 1,09 €"), etc.

Start at the first product and stop right before the totals.
EVERY real product line in this range MUST appear EXACTLY ONCE in your JSON output.
If you are uncertain whether a line is a product or a helper, TREAT IT AS A PRODUCT with quantity = 1 rather than skipping it.

============================================================
STEP 2 – DEFAULT QUANTITY
============================================================

For every product line you find, start with:

- quantity = 1

You will then adjust quantity using multiplier rules.

============================================================
STEP 3 – MULTIPLIER LINES "N x PRICE" (GENERAL RULE)
============================================================

Some receipts print a separate line with a multiplier ABOVE the product line:

Examples:
- "2 x 9,99 €"
- "2 x 1,09 €"
- "3x 0,79 €"

These are MULTIPLIER LINES with pattern:

- N: integer quantity (2, 3, 4, …)
- x or ×
- PRICE P (e.g. 1,09 €)

General rules for any store:

1. A multiplier line is NOT a product. Never output it as JSON.
2. A multiplier line may belong to the next product line below, but ONLY if all of this is true:
   - The next line is a real product line with a row total G.
   - N × P equals G (within 1 cent).
3. If N × P equals G (within 1 cent), you may safely set:
   - quantity = N for that product.
4. If N × P does NOT equal G, or the association is unclear:
   - Do NOT use this multiplier for that product.
   - Leave quantity as 1.

Never apply one multiplier line to more than one product.

============================================================
STEP 4 – SPECIAL RULE: ALDI & NETTO RECEIPTS
============================================================

This section applies ONLY if the store name in the header clearly indicates:

- "ALDI"  OR
- "Netto Marken-Discount" / "Netto"

On ALDI and Netto receipts, the layout has a strong, fixed pattern:

- Multiplier lines like "2 x 1,09 €" or "3x 0,79 €" appear DIRECTLY ABOVE the product they belong to.
- The product line below shows a row total G that equals N × P.

For ALDI and Netto receipts you MUST:

1. Treat every standalone line that matches the pattern "N x PRICE" (no product name) as a multiplier candidate for the NEXT product line below.
2. Read N and P from that line.
3. Look at the very next real product line below:
   - If its row total G satisfies N × P = G (within 1 cent), then:
     - quantity for that product MUST be set to N.
   - If N × P does not match G, ignore that multiplier for this product and keep quantity = 1.

Important:
- This “belongs to the item below” rule applies ONLY for ALDI and Netto receipts identified by their header.
- Do NOT blindly apply this pattern to other supermarkets just because you see a "N x PRICE" line.
  For other stores, use the GENERAL RULE: only change quantity if the N × P → G relationship is clear and unambiguous.

============================================================
STEP 5 – WEIGHT DETAIL LINES (DO NOT CHANGE QUANTITY)
============================================================

Weight/detail lines look like:

- "0,812 kg x 1,99 €/kg"
- "1,060 kg x 1,29 EUR/kg"
- "750 g x 1,99 €/kg"

Rules:

- These lines explain weight and price-per-kg for the product above (often fruit/veg).
- They are NOT products → never output them as JSON.
- They DO NOT change quantity:
  - Quantity for such items remains 1.
  - The total price is already on the product line (e.g. "Bananas loose 1,62 €").

============================================================
STEP 6 – FINAL SELF-CHECK
============================================================

Before you output JSON:

1. Ensure there is one JSON object per product line and NO missing products:
   - Count all real product lines between header and totals.
   - Count all JSON objects.
   - These counts MUST be equal.
2. For every product, check:

   - If this is an ALDI or Netto receipt:
     - Look for a multiplier "N x PRICE" directly above the product.
     - If N × PRICE equals the row total G, quantity MUST equal N.
   - For other stores:
     - Only change quantity if there is a clear "N x PRICE" multiplier and N × PRICE clearly matches the row total G.
     - Otherwise, quantity MUST remain 1.

3. Make sure all quantities are positive integers and absolutely correct.

Finally, output ONLY the JSON array with:
- product_name
- quantity
""".strip()
)


_FOCUSED_TAX_DEFAULT_PROMPT = (
    """
Your job is to extract the product name and quantity of all real products from a receipt. You strive for absolute 100% accuracy. Every product name
and every quantity is absolutely correct. You use all your capabilites because this accuracy will save lifes.

Your job:
- For every REAL product line, assign the correct German VAT rate as a decimal:
  - 0.07  (7%)
  - 0.19  (19%)
- You MUST cover EVERY real product line from the first product down to the last product just before the totals block.
- It is an ERROR to skip a real product line. If you are unsure, include your BEST GUESS (0.07 or 0.19) instead of omitting the line.

============================================================
OUTPUT FORMAT (STRICT)
============================================================

- Return ONLY a JSON array.
- Each element MUST be an object with exactly:

  {
    "product_name": string,
    "tax_rate": number
  }

- product_name: the product description as printed (minor whitespace cleanup allowed).
- tax_rate: MUST be exactly 0.07 or 0.19.
- There MUST be a 1:1 mapping between real product lines and JSON objects:
  - One JSON object for each real product line.
  - No missing products, no extra helper lines.
- No other fields. No commentary, no Markdown, no extra text.

============================================================
STEP 1 – IDENTIFY PRODUCT LINES
============================================================

Use the same notion of product line as in the main extraction:
- Lines with a product name and a row total price on the right.
- Located between header and totals ("SUMME", "ZU ZAHLEN", "Endbetrag", etc.).

Do NOT create entries for:
- Totals, payment info, card info, transaction IDs, dates.
- MWST summary table itself.
- Pure helper lines like "0,812 kg x 1,99 €/kg" or "2 x 1,29 €".

There must be one JSON object for each real product.
If you are uncertain whether a line is a product or a helper, TREAT IT AS A PRODUCT and assign your best tax_rate guess (0.07 or 0.19) instead of skipping it.

============================================================
STEP 2 – PRIMARY TAX SOURCES
============================================================

You MUST use printed tax information as the primary source, in this order:

1. **Letter codes on product lines**  
   - If a line ends with a tax group letter, use the mapping:
     - A → 19% → tax_rate = 0.19
     - B → 7%  → tax_rate = 0.07
   - If other letters are printed and mapped in the MWST table, use those mappings.

2. **MWST summary table at the bottom**  
   - This table often lists tax groups like:

     "1   7,00%   NETTO  30,67   MWST-BETRAG  2,15   BRUTTO  32,82"

   - If the table shows ONLY one rate (e.g. ONLY 7,00% and no 19,00%), then:
     → ALL products on this receipt MUST use that rate (here tax_rate = 0.07).
   - If it shows multiple groups (e.g. one entry for 7% and one for 19%), then:
     - Use the letter/group mappings (A/B, etc.) to assign the correct rate per product.

3. **Explicit percentages on or near product lines**  
   - Rare, but if a product line explicitly mentions "7%" or "19%", use that.

============================================================
STEP 3 – DOMAIN KNOWLEDGE FALLBACK (ONLY WHEN NEEDED)
============================================================

If a product has no clear letter, no visible mapping and multiple rates exist, THEN use domain knowledge:

- Likely 0.07 (food VAT):
  - Basic foods: bread, milk, cheese, yoghurt, quark, cereals, pasta, rice, fruits, vegetables, flour, sugar, salt, nuts, etc.

- Likely 0.19:
  - Household/non-food: detergents, cleaning products, paper towels, foil, trash bags, cosmetics, shampoo, toothpaste, deodorant, razors, etc.
  - Many drinks: soft drinks, alcohol, energy drinks, special beverages.

If a deposit (Pfand) line is printed:
- It normally shares the tax rate of the associated drink (usually 0.19).
- If the receipt clearly marks the tax group for the deposit line, follow the letter instead.

When in doubt, prefer the rate that keeps the MWST summary totals plausible and matches similar items on the same receipt.
But NEVER omit a product: always choose 0.07 or 0.19 for every real product line.

============================================================
STEP 4 – SPECIAL CASE: SINGLE-RATE RECEIPTS (IMPORTANT)
============================================================

If the MWST table shows only ONE tax rate (e.g. "7,00%" and no other rate):

- ALL products MUST have that same tax_rate.
- Do NOT guess 0.19 for any product on such a receipt.
- Example: if the table shows only 7,00%, then:
  - All items (bread, fruit, dairy, tinned vegetables, etc.) → tax_rate = 0.07.

============================================================
STEP 5 – FINAL SELF-CHECK
============================================================

Before you output JSON:

1. Ensure there is one tax_rate for every product line, and no extra entries:
   - Count all real product lines between header and totals.
   - Count all JSON objects.
   - These counts MUST be equal.
2. Confirm that every tax_rate is EXACTLY 0.07 or 0.19 (no other values).
3. Check consistency:
   - If a product line has letter A or B, its tax_rate must follow the mapping.
   - If the MWST table shows only a single rate, all items must use that rate.
   - Deposit/Pfand lines must not contradict the tax rate of their associated drink if that is clearly identifiable.
4. If you are unsure about any line, include it with your best tax_rate guess instead of skipping it.

Finally, output ONLY the JSON array of objects:
- product_name
- tax_rate
""".strip()
)


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
                "required": ["product_name","quantity","unit_price_net","unit_price_gross","tax_rate","line_net","line_tax","line_gross","line_type"],
                "properties": {
                    "product_name": {"type": "string"},
                    "quantity": {"type": "number"},
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


def _scavenge_json_block(s: str) -> Optional[Any]:
    if not s:
        return None

    candidates: List[str] = []

    # 1) Try fenced code blocks first (e.g., ```json ... ```)
    fenced = re.search(r"```(?:json)?\s*(.*?)```", s, re.DOTALL)
    if fenced and fenced.group(1):
        candidates.append(fenced.group(1).strip())

    # 2) Try the full object slice
    start_obj = s.find("{")
    end_obj = s.rfind("}")
    if start_obj != -1 and end_obj != -1 and end_obj > start_obj:
        candidates.append(s[start_obj : end_obj + 1])

    # 3) Try the full array slice
    start_arr = s.find("[")
    end_arr = s.rfind("]")
    if start_arr != -1 and end_arr != -1 and end_arr > start_arr:
        candidates.append(s[start_arr : end_arr + 1])

    for candidate in candidates:
        try:
            return json.loads(candidate)
        except Exception:
            continue

    return None


def _extract_fenced_json(text: str) -> Optional[str]:
    """If the model wrapped JSON in ``` or ```json fences, return the inner content."""

    if not isinstance(text, str):
        return None
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if match and match.group(1):
        return match.group(1).strip()
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

    def reconcile_after_overrides(self, payload: Dict[str, Any]) -> None:
        """Re-run item normalization after downstream mutations change key fields."""

        items = payload.get("items")
        if not isinstance(items, list) or not items:
            return
        for idx, candidate in enumerate(items):
            if not isinstance(candidate, dict):
                continue
            normalized = self._normalize_item(
                candidate,
                allow_quantity_price_coupling=False,
            )
            if not normalized:
                LOG.warning(
                    "Focused reconciliation skipped item #%s (%r); original values kept.",
                    idx,
                    candidate.get("product_name") if isinstance(candidate, dict) else candidate,
                )
                continue
            normalized = self._reconcile_quantity_price_consistency(normalized)
            items[idx].clear()
            items[idx].update(normalized)
        payload["items"] = items
        payload["totals"] = self._normalize_totals(payload.get("totals"), items)

    def _reconcile_quantity_price_consistency(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """
        Treat quantity as authoritative and adjust either line_gross or
        unit_price_gross (whichever needs the smaller relative change) to keep
        them consistent with that quantity.
        """
        qty_raw = item.get("quantity")
        line_gross = item.get("line_gross")
        unit_price_gross = item.get("unit_price_gross")
        tax_rate = item.get("tax_rate")

        try:
            quantity = float(qty_raw) if qty_raw is not None else None
        except (TypeError, ValueError):
            quantity = None

        if quantity is None or quantity <= 0:
            return item
        if not isinstance(line_gross, int) or not isinstance(unit_price_gross, int):
            return item

        quantity_int = max(1, int(round(quantity)))
        abs_line = abs(line_gross)
        abs_unit = abs(unit_price_gross)
        if abs_line == 0 or abs_unit == 0:
            return item

        candidate_line = quantity_int * abs_unit
        try:
            candidate_unit_dec = Decimal(abs_line) / Decimal(quantity_int)
        except InvalidOperation:
            return item
        candidate_unit = int(self._round_half_up(candidate_unit_dec))

        delta_line = abs(candidate_line - abs_line) / max(abs_line, 1)
        delta_unit = abs(candidate_unit - abs_unit) / max(abs_unit, 1)
        if min(delta_line, delta_unit) > 0.51:
            return item

        reconciled_line = line_gross
        reconciled_unit = unit_price_gross
        changed = False
        if delta_line <= delta_unit:
            reconciled_line = candidate_line if line_gross >= 0 else -candidate_line
            if reconciled_line != line_gross:
                LOG.debug(
                    "Reconciling line_gross for '%s' from %s to %s using qty=%s and unit_price_gross=%s",
                    item.get("product_name"),
                    line_gross,
                    reconciled_line,
                    quantity_int,
                    unit_price_gross,
                )
                changed = True
        else:
            if candidate_unit != unit_price_gross:
                LOG.debug(
                    "Reconciling unit_price_gross for '%s' from %s to %s using qty=%s and line_gross=%s",
                    item.get("product_name"),
                    unit_price_gross,
                    candidate_unit,
                    quantity_int,
                    line_gross,
                )
                reconciled_unit = candidate_unit
                changed = True

        item["line_gross"] = reconciled_line
        item["unit_price_gross"] = reconciled_unit
        if changed:
            net, tax = self._compute_net_and_tax(reconciled_line, float(tax_rate) if tax_rate is not None else 0.19)
            item["line_net"] = net
            item["line_tax"] = tax
        return item

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

    def _normalize_item(
        self,
        item: Dict[str, Any],
        *,
        allow_quantity_price_coupling: bool = True,
    ) -> Optional[Dict[str, Any]]:
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

        if allow_quantity_price_coupling:
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

        if allow_quantity_price_coupling:
            quantity = self._adjust_quantity_from_unit_price(
                quantity=quantity,
                raw_unit_price_gross=raw_unit_price_gross,
                line_gross=line_gross,
                item_name=name,
            )

        normalized = {
            "product_name": name,
            "quantity": float(quantity),
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
        if self.config.reasoning_effort:
            payload["reasoning"] = {"effort": self.config.reasoning_effort}
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
    ) -> Optional[Any]:
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
            LOG.debug(
                "JSON parse failed for model=%s; attempting fallback (first 500 chars: %r)",
                self.config.model_name,
                text[:500],
            )
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
            "Transcribe the receipt exactly as text, preserving line order, spacing, tabs etc.. "
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


# ---- raw-content aware helpers ---------------------------------------------------
_RAW_TOTAL_TOKENS: Tuple[str, ...] = (
    "summe",
    "gesamt",
    "total",
    "zwischensumme",
    "endbetrag",
    "zu zahlen",
    "sumne",
)
_RAW_PFAND_KEYWORDS: Tuple[str, ...] = ("pfand", "ew-pfand", "einwegpfand", "mehrwegpfand", "mw-pfand", "leergut", "einweg", "mehrweg")
_RAW_STORE_HEADER_LINES = 8


def _normalize_name_for_match(name: str) -> str:
    if not isinstance(name, str):
        return ""
    normalized = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^a-z0-9]+", "", ascii_only.lower())
    return cleaned


def _match_item_index(items: List[str], candidate_name: str, *, threshold: float) -> Tuple[Optional[int], float]:
    target_key = _normalize_name_for_match(candidate_name)
    if not target_key:
        return None, 0.0
    best_idx: Optional[int] = None
    best_score: float = 0.0
    for idx, base_key in enumerate(items):
        if not base_key:
            continue
        if base_key == target_key:
            return idx, 1.0
        if target_key in base_key or base_key in target_key:
            score = 0.95
        else:
            score = SequenceMatcher(None, target_key, base_key).ratio()
        if score > best_score:
            best_idx = idx
            best_score = score
    if best_idx is not None and best_score >= threshold:
        return best_idx, best_score
    return None, best_score


def _raw_content_is_total(text: str) -> bool:
    if PayloadNormalizer._looks_like_total_header(text):
        return True
    lowered = (text or "").lower()
    return any(token in lowered for token in _RAW_TOTAL_TOKENS)


def _raw_is_pfand_line(name: str) -> bool:
    lowered = (name or "").lower()
    return any(keyword in lowered for keyword in _RAW_PFAND_KEYWORDS)


def _detect_store(raw_content: str) -> str:
    header = " ".join(
        line.strip() for line in raw_content.splitlines()[:_RAW_STORE_HEADER_LINES] if line.strip()
    ).lower()
    if "aldi" in header:
        return "aldi"
    if "netto" in header:
        return "netto"
    return "other"


def _cents_from_str(amount_str: str) -> Optional[int]:
    if not isinstance(amount_str, str):
        return None
    cleaned = amount_str.strip().replace("€", "").strip()
    if not cleaned:
        return None
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif cleaned.count(".") > 1:
        parts = cleaned.split(".")
        cleaned = "".join(parts[:-1]) + "." + parts[-1]
    try:
        cents_dec = (Decimal(cleaned) * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    except Exception:
        return None
    try:
        return int(cents_dec)
    except Exception:
        return None


def _rightmost_price(text: str) -> Optional[int]:
    matches = re.findall(r"\d+[.,]\d{2}", text or "")
    if not matches:
        return None
    return _cents_from_str(matches[-1])


def _tax_from_group(group: Any) -> Optional[float]:
    if not isinstance(group, str):
        return None
    upper = group.strip().upper()
    if upper == "A":
        return 0.19
    if upper == "B":
        return 0.07
    return None


def parse_items_from_raw_content(
    raw_content: str,
    base_items: Optional[List[Dict[str, Any]]] = None,
    *,
    merchant_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Deterministically reconstruct quantities/unit prices using raw_content and seed items.
    Seed items supply product_name/line_gross/tax_rate/line_type/line_index from the model.
    """
    if not isinstance(raw_content, str) or not raw_content.strip():
        return []

    lines = [ln.strip() for ln in raw_content.splitlines() if ln is not None]
    lines = [ln for ln in lines if ln.strip()]
    if not lines:
        return []

    store = _detect_store(raw_content)
    normalized_lines = [_normalize_name_for_match(ln) for ln in lines]
    items_to_process = base_items or []

    def _anchor_index(item: Dict[str, Any]) -> Optional[int]:
        idx = item.get("line_index")
        if isinstance(idx, int) and 0 <= idx < len(lines):
            return idx
        name = item.get("product_name") or item.get("description") or ""
        match_idx, score = _match_item_index(normalized_lines, name, threshold=0.6)
        return match_idx

    def _find_multiplier(anchor: int) -> Tuple[Optional[int], Optional[int]]:
        """Return (qty, unit_price_gross) if a supporting multiplier is nearby."""
        anchor_line = lines[anchor]
        match_same = re.search(r"(\d+)\s*[x×]\s*(\d+[.,]\d{2})", anchor_line)
        if match_same:
            return int(match_same.group(1)), _cents_from_str(match_same.group(2))

        offsets = [1] if store in {"aldi", "netto"} else [1, 2]
        for offset in offsets:
            if anchor - offset < 0:
                continue
            candidate = lines[anchor - offset]
            mult = re.search(r"(\d+)\s*[x×]\s*(\d+[.,]\d{2})", candidate)
            if mult:
                return int(mult.group(1)), _cents_from_str(mult.group(2))

        # Netto/Aldi split pattern: line-2 "2 x" and line-1 "1,49"
        if store in {"aldi", "netto"} and anchor >= 2:
            qty_line = lines[anchor - 2]
            price_line = lines[anchor - 1]
            m_qty = re.match(r"^\s*(\d+)\s*[x×]\s*$", qty_line)
            m_price = re.match(r"^\s*(\d+[.,]\d{2})\s*(?:€)?\s*$", price_line)
            if m_qty and m_price:
                qty_val = int(m_qty.group(1))
                price_val = _cents_from_str(m_price.group(1))
                if price_val is not None:
                    return qty_val, price_val
        return None, None

    results: List[Dict[str, Any]] = []
    for seed in items_to_process:
        if not isinstance(seed, dict):
            continue
        anchor = _anchor_index(seed)
        if anchor is None:
            continue

        anchor_text = lines[anchor]
        name = seed.get("product_name") or anchor_text
        base_line_gross = seed.get("line_gross") if isinstance(seed.get("line_gross"), int) else None
        row_total = base_line_gross if base_line_gross is not None else _rightmost_price(anchor_text)
        if row_total is None:
            continue

        qty, unit_price_candidate = _find_multiplier(anchor)
        quantity = float(qty) if qty and unit_price_candidate else 1.0
        unit_price_gross = (
            unit_price_candidate
            if qty and unit_price_candidate and abs(qty * unit_price_candidate - abs(row_total)) <= 1
            else int(abs(row_total))
        )

        tax_rate = PayloadNormalizer._normalize_tax_rate(seed.get("tax_rate")) if seed.get("tax_rate") is not None else None
        if tax_rate is None:
            tax_rate = _tax_from_group(seed.get("tax_group")) or 0.19

        line_type = seed.get("line_type")
        if not line_type:
            line_type = LINE_TYPE_DEPOSIT_CHARGE if _raw_is_pfand_line(name or anchor_text) else LINE_TYPE_DEFAULT

        line_net, line_tax = PayloadNormalizer._compute_net_and_tax(row_total, tax_rate)
        unit_price_net = (
            int(round(line_net / quantity)) if line_net is not None and quantity > 0 else line_net
        )

        results.append(
            {
                "product_name": name,
                "quantity": quantity,
                "unit_price_net": unit_price_net,
                "unit_price_gross": unit_price_gross,
                "tax_rate": float(tax_rate),
                "line_net": line_net,
                "line_tax": line_tax,
                "line_gross": row_total,
                "line_type": line_type,
                "line_index": anchor,
            }
        )

    if results:
        return results

    # Fallback: try to parse raw_content directly if no seed items worked
    return []


def strengthen_with_raw_text(normalized: Dict[str, Any], *, facts: Optional[FileFacts] = None) -> None:
    """Deterministically rebuild items from raw_content and model-provided row totals."""
    raw_content = normalized.get("raw_content")
    if isinstance(raw_content, str) and raw_content.strip():
        LOG.info("raw_content (verbatim):\n%s", raw_content)
    base_items = normalized.get("items") if isinstance(normalized.get("items"), list) else []
    merchant = (normalized.get("merchant") or {}).get("name")
    parsed_items = parse_items_from_raw_content(raw_content, base_items, merchant_name=merchant)
    # Only adopt deterministic parsing if we actually changed any quantities (qty > 1)
    if parsed_items and any(isinstance(it, dict) and it.get("quantity", 1) and it.get("quantity", 1) > 1 for it in parsed_items):
        LOG.info("raw_content parser produced %s items using store-aware heuristics.", len(parsed_items))
        normalized["items"] = parsed_items

        source_file = normalized.get("source_file") if isinstance(normalized.get("source_file"), dict) else {}
        fallback_facts = facts or (FileFacts.from_dict(source_file) if isinstance(source_file, dict) else None)
        normalizer = PayloadNormalizer(fallback_facts or FileFacts(None, None, None, None))
        normalized["totals"] = normalizer._normalize_totals(normalized.get("totals"), parsed_items)
        return

    if not parsed_items:
        LOG.debug("raw_content parser returned no items; keeping existing normalized items.")
        return

    LOG.debug("raw_content parser found only qty=1 items; keeping existing normalized items.")


def check_totals_consistency(normalized: Dict[str, Any]) -> None:
    items = normalized.get("items")
    totals = normalized.get("totals")
    if not isinstance(items, list) or not isinstance(totals, dict):
        return
    sum_items = sum(it.get("line_gross") or 0 for it in items if isinstance(it, dict))
    total_gross = totals.get("total_gross")
    if isinstance(total_gross, int) and abs(sum_items - total_gross) > 3:
        normalized.setdefault("_enrichment", {})["totals_mismatch"] = {
            "sum_items": sum_items,
            "total_gross": total_gross,
        }
        LOG.warning(
            "Totals mismatch detected: sum of line_gross=%s vs total_gross=%s",
            sum_items,
            total_gross,
        )


def extract_receipt_payload_from_image(
    source_path: str,
    *,
    model_name: str = "gpt-5-mini",
    script_dir: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    LOG.info("Backend selected: OpenRouter")
    effective_model = (os.getenv("OPENROUTER_MODEL") or OPENROUTER_MODEL or model_name).strip()
    LOG.debug("Effective OpenRouter model: %s", effective_model)
    return _extract_with_openrouter(source_path, script_dir=script_dir, model_name=effective_model)

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
        LOG.error("OPENROUTER_API_KEY missing in env/.env; cannot run extraction")
        return None

    effective_model = (model_name or "").strip()
    if not effective_model:
        LOG.error("No OpenRouter model configured via OPENROUTER_MODEL or parameter.")
        return None

    data_url = _b64_data_url(source_path)
    if not data_url:
        return None

    facts = FileFacts.from_dict(_file_facts(source_path))
    response_store = ModelResponseStore(script_dir=script_dir, facts=facts)
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

    config = OpenRouterConfig(
        api_key=api_key,
        model_name=effective_model,
        reasoning_effort=None,
    )
    client = OpenRouterClient(config)

    LOG.info("Calling OpenRouter model=%s for structured extraction", effective_model)
    raw = client.json_request(
        messages,
        max_tokens=8000,
        timeout=180,
        plugins=plugins,
    )
    if response_store and raw is not None:
        response_store.write("raw_general", raw)
    if not isinstance(raw, dict) or not raw:
        LOG.error("OpenRouter returned no valid JSON object for structured extraction.")
        return None

    normalized = _normalize_openrouter_payload(raw, facts=facts)
    enrichment = normalized.setdefault("_enrichment", {})
    if not normalized.get("raw_content"):
        LOG.info("raw_content missing; requesting transcription via OpenRouter.")
        raw_text = client.fetch_raw_content(data_url, facts=facts)
        if raw_text:
            normalized["raw_content"] = raw_text
        enrichment["raw_content_fetched"] = bool(raw_text)
    strengthen_with_raw_text(normalized, facts=facts)
    # Deterministic reconstruction based on raw_content + provided row totals
    PayloadNormalizer(facts).reconcile_after_overrides(normalized)
    check_totals_consistency(normalized)

    address = (normalized.get("merchant") or {}).get("address") or {}
    context_for_country = {
        "merchant": normalized.get("merchant"),
        "currency": normalized.get("currency"),
        "city": address.get("city"),
        "postal_code": address.get("postal_code"),
    }

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

    normalized.setdefault(
        "_extraction_meta",
        {
            "model": effective_model,
            "at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "backend": "openrouter",
        },
    )

    if response_store:
        response_store.write("general", normalized)
        response_store.write("final_normalized", normalized)

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

    model = (os.getenv("OPENROUTER_MODEL") or OPENROUTER_MODEL or "gpt-5-mini").strip()

    # Call OpenRouter backend directly to avoid extra layers
    result = _extract_with_openrouter(img, script_dir=os.getcwd(), model_name=model)
    if not result:
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0)
