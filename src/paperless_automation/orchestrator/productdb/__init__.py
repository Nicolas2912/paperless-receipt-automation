"""Product database orchestration package (boilerplate).

This package sets up the scaffolding to build a structured product/receipt
database backed by SQLite, following the schema described in
`product-database-plan.md`.

Modules:
- db: DB location, initialization, and connection helpers
- models: Dataclasses for typed records (addresses, merchants, receipts, ...)
- parser: JSON parsing + validation (skeleton)
- extraction: LLM vision extraction (skeleton)
- service: Orchestrator-facing service layer (skeleton)
"""

from .db import ProductDatabase
from .service import ReceiptExtractionService
from .frontend.app import create_app

__all__ = [
    "ProductDatabase",
    "ReceiptExtractionService",
    "create_app",
]
