import re
from decimal import Decimal
from typing import Any, Optional

from ..logging import get_logger

_LOG = get_logger("normalize")


def normalize_date_iso(value: str) -> Optional[str]:
    """Normalize common date strings to ISO YYYY-MM-DD.

    Supports:
    - DD.MM.YYYY, D.M.YYYY, DD/MM/YYYY, etc.
    - YYYY-MM-DD passthrough
    - Two-digit years map to 19xx for >=70 else 20xx
    """
    if not value:
        return None
    v = str(value).strip()
    if not v:
        return None
    # Already ISO
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return v
    m = re.fullmatch(r"(\d{1,2})[\./](\d{1,2})[\./](\d{2,4})", v)
    if m:
        d, mth, y = m.groups()
        if len(y) == 2:
            y = ("20" + y) if int(y) < 70 else ("19" + y)
        try:
            return f"{int(y):04d}-{int(mth):02d}-{int(d):02d}"
        except Exception:
            return None
    return None


def normalize_amount(val: Any) -> Optional[str]:
    """Normalize price strings to dot-decimal with two decimals.

    Handles inputs like '14,70', '14.70', '1.470,00', '1,470.00', numbers, etc.
    """
    if val is None:
        return None
    s = str(val).strip().replace(" ", "")
    if not s:
        return None
    has_dot = "." in s
    has_comma = "," in s
    s2 = s
    if has_dot and has_comma:
        if re.search(r",\d{1,2}$", s):
            s2 = s.replace(".", "").replace(",", ".")
        elif re.search(r"\.\d{1,2}$", s):
            s2 = s.replace(",", "")
        else:
            s2 = s.replace(".", "").replace(",", ".")
    elif has_comma:
        if re.search(r",\d{1,2}$", s):
            s2 = s.replace(".", "").replace(",", ".")
        else:
            s2 = s.replace(",", "")
    elif has_dot:
        if re.search(r"\.\d{1,2}$", s):
            s2 = s
        else:
            s2 = s.replace(".", "")
    else:
        s2 = s

    m = re.search(r"-?\d+(?:\.\d{1,2})?", s2)
    if not m:
        return None
    try:
        num = Decimal(m.group(0))
    except Exception:
        try:
            num = Decimal(str(float(m.group(0))))
        except Exception:
            return None
    return f"{num:.2f}"


def detect_currency(text: str) -> str:
    """Detect EUR/USD using symbol or token; default EUR."""
    t = text or ""
    if "€" in t or re.search(r"\bEUR\b", t, re.IGNORECASE):
        return "EUR"
    if "$" in t or re.search(r"\bUSD\b", t, re.IGNORECASE):
        return "USD"
    return "EUR"

