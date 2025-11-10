from __future__ import annotations

from typing import Tuple, Set

# Canonical line type values used across the product DB.
LINE_TYPE_SALE = "SALE"
LINE_TYPE_DEPOSIT_CHARGE = "DEPOSIT_CHARGE"
LINE_TYPE_DEPOSIT_REFUND = "DEPOSIT_REFUND"
LINE_TYPE_DISCOUNT = "DISCOUNT"
LINE_TYPE_OTHER = "OTHER"

LINE_TYPE_CHOICES: Tuple[str, ...] = (
    LINE_TYPE_SALE,
    LINE_TYPE_DEPOSIT_CHARGE,
    LINE_TYPE_DEPOSIT_REFUND,
    LINE_TYPE_DISCOUNT,
    LINE_TYPE_OTHER,
)

LINE_TYPE_DEFAULT = LINE_TYPE_SALE

# Types where negative monetary amounts are expected/allowed.
LINE_TYPES_ALLOWING_NEGATIVES: Set[str] = {
    LINE_TYPE_DEPOSIT_REFUND,
    LINE_TYPE_DISCOUNT,
    LINE_TYPE_OTHER,
}
