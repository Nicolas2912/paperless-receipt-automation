from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator, Optional, Dict, Any, List

from ...logging import get_logger
from ...paths import find_project_root, var_dir


LOG = get_logger("productdb-db")


# Prefer new path var/productdb; keep backward-compat with var/product_db if present
DEFAULT_DB_FOLDER = "productdb"
DEFAULT_DB_FILENAME = "products.sqlite3"


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

-- 1) Address book
CREATE TABLE IF NOT EXISTS addresses (
  address_id   INTEGER PRIMARY KEY,
  street       TEXT,
  city         TEXT,
  postal_code  TEXT,
  country      TEXT
);

CREATE TABLE IF NOT EXISTS merchants (
  merchant_id  INTEGER PRIMARY KEY,
  name         TEXT NOT NULL,
  address_id   INTEGER REFERENCES addresses(address_id) ON UPDATE CASCADE ON DELETE SET NULL,
  created_at   TEXT DEFAULT (datetime('now')),
  UNIQUE(name, address_id)
);

-- 2) Artifacts
CREATE TABLE IF NOT EXISTS files (
  file_id    INTEGER PRIMARY KEY,
  filename   TEXT NOT NULL,
  mime_type  TEXT,
  byte_size  INTEGER,
  sha256     TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS texts (
  text_id    INTEGER PRIMARY KEY,
  content    TEXT NOT NULL           -- raw JSON (string)
);

-- 3) Receipt header
CREATE TABLE IF NOT EXISTS receipts (
  receipt_id         INTEGER PRIMARY KEY,
  merchant_id        INTEGER NOT NULL REFERENCES merchants(merchant_id) ON UPDATE CASCADE ON DELETE RESTRICT,
  purchase_date_time TEXT NOT NULL,   -- "YYYY-MM-DDTHH:MM:SS"
  currency           TEXT NOT NULL CHECK(length(currency)=3),
  payment_method     TEXT NOT NULL CHECK (payment_method IN ('CASH','CARD','OTHER')),
  -- prefer INTEGER cents; if you stay with REAL, keep them >= 0
  total_net          INTEGER,          -- cents; NULL if unknown
  total_tax          INTEGER,
  total_gross        INTEGER,
  source_file_id     INTEGER REFERENCES files(file_id) ON DELETE SET NULL,
  raw_content_id     INTEGER REFERENCES texts(text_id) ON DELETE SET NULL,
  created_at         TEXT DEFAULT (datetime('now')),
  UNIQUE(merchant_id, purchase_date_time)
);

-- 4) Line items
CREATE TABLE IF NOT EXISTS receipt_items (
  item_id           INTEGER PRIMARY KEY,
  receipt_id        INTEGER NOT NULL REFERENCES receipts(receipt_id) ON DELETE CASCADE,
  product_name      TEXT NOT NULL,
  quantity          REAL NOT NULL CHECK(quantity > 0),
  unit              TEXT,               -- "x","kg","l"
  unit_price_net    INTEGER,            -- cents (nullable)
  unit_price_gross  INTEGER,            -- cents (nullable)
  tax_rate          REAL NOT NULL CHECK(tax_rate IN (0.00, 0.07, 0.19)),
  line_net          INTEGER,            -- cents
  line_tax          INTEGER,            -- cents
  line_gross        INTEGER,            -- cents
  created_at        TEXT DEFAULT (datetime('now')),
  CHECK(line_net    IS NULL OR line_net    >= 0),
  CHECK(line_tax    IS NULL OR line_tax    >= 0),
  CHECK(line_gross  IS NULL OR line_gross  >= 0)
);

-- 5) Extraction runs
CREATE TABLE IF NOT EXISTS extraction_runs (
  run_id         INTEGER PRIMARY KEY,
  receipt_id     INTEGER REFERENCES receipts(receipt_id) ON DELETE CASCADE,
  model_name     TEXT NOT NULL,
  prompt_version TEXT,
  started_at     TEXT DEFAULT (datetime('now')),
  finished_at    TEXT,
  status         TEXT CHECK (status IN ('OK','WARN','ERROR')) DEFAULT 'OK',
  raw_content_id INTEGER REFERENCES texts(text_id) ON DELETE SET NULL,
  notes          TEXT
);

-- Helpful indexes
CREATE INDEX IF NOT EXISTS idx_receipts_merchant_dt ON receipts(merchant_id, purchase_date_time);
CREATE INDEX IF NOT EXISTS idx_items_receipt        ON receipt_items(receipt_id);
CREATE INDEX IF NOT EXISTS idx_items_taxrate        ON receipt_items(tax_rate);
CREATE INDEX IF NOT EXISTS idx_merchants_name       ON merchants(name);
"""


class ProductDatabase:
    """SQLite-backed product/receipt database.

    - Places DB under `<repo-root>/var/product_db/products.sqlite3`.
    - Ensures schema on first use.
    - Provides a context-managed connection method.
    """

    def __init__(self, root_dir: Optional[str] = None) -> None:
        root = find_project_root(root_dir)
        var_path = var_dir(root)
        # Backward compatibility: if legacy folder exists and has a DB, prefer it
        legacy_folder = os.path.join(var_path, "product_db")
        legacy_db = os.path.join(legacy_folder, DEFAULT_DB_FILENAME)
        preferred_folder = os.path.join(var_path, DEFAULT_DB_FOLDER)
        # Decide target folder
        if os.path.exists(legacy_db) and not os.path.exists(os.path.join(preferred_folder, DEFAULT_DB_FILENAME)):
            db_folder = legacy_folder
            LOG.info("Using legacy DB location at var/product_db (existing file found).")
        else:
            db_folder = preferred_folder
        os.makedirs(db_folder, exist_ok=True)
        self.db_path = os.path.join(db_folder, DEFAULT_DB_FILENAME)
        LOG.info(f"Product DB path: {self.db_path}")
        self._ensure_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self.connect() as conn:
            cur = conn.cursor()
            try:
                cur.execute("PRAGMA journal_mode=WAL;")
                cur.execute("PRAGMA synchronous=NORMAL;")
            except Exception:
                # Non-fatal; continue with schema creation
                pass
            LOG.info("Ensuring product DB schema is present…")
            cur.executescript(SCHEMA_SQL)
            conn.commit()
            LOG.info("Product DB schema ensured.")

    # --------------- Insert/Upsert helpers ---------------
    def insert_address(self, addr: Dict[str, Optional[str]]) -> int:
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO addresses (street, city, postal_code, country)
                VALUES (?, ?, ?, ?)
                RETURNING address_id;
                """,
                (addr.get("street"), addr.get("city"), addr.get("postal_code"), addr.get("country")),
            )
            row = cur.fetchone()
            conn.commit()
            return int(row[0])

    def upsert_merchant(self, name: str, address_id: Optional[int]) -> int:
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO merchants (name, address_id)
                VALUES (?, ?)
                ON CONFLICT(name, address_id) DO UPDATE SET name=excluded.name
                RETURNING merchant_id;
                """,
                (name, address_id),
            )
            row = cur.fetchone()
            conn.commit()
            return int(row[0])

    def upsert_file(self, f: Dict[str, Any]) -> int:
        with self.connect() as conn:
            cur = conn.cursor()
            # Prefer sha256 uniqueness; fall back to filename if no hash available
            if f.get("sha256"):
                cur.execute(
                    """
                    INSERT INTO files (filename, mime_type, byte_size, sha256)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(sha256) DO UPDATE SET
                        filename=excluded.filename,
                        mime_type=excluded.mime_type,
                        byte_size=excluded.byte_size
                    RETURNING file_id;
                    """,
                    (f.get("filename"), f.get("mime_type"), f.get("byte_size"), f.get("sha256")),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO files (filename, mime_type, byte_size)
                    VALUES (?, ?, ?)
                    RETURNING file_id;
                    """,
                    (f.get("filename"), f.get("mime_type"), f.get("byte_size")),
                )
            row = cur.fetchone()
            conn.commit()
            return int(row[0])

    def insert_text(self, content: str) -> int:
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute("INSERT INTO texts (content) VALUES (?) RETURNING text_id;", (content,))
            row = cur.fetchone()
            conn.commit()
            return int(row[0])

    def insert_receipt(self, r: Dict[str, Any]) -> int:
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO receipts (
                    merchant_id, purchase_date_time, currency, payment_method,
                    total_net, total_tax, total_gross, source_file_id, raw_content_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                RETURNING receipt_id;
                """,
                (
                    r["merchant_id"],
                    r["purchase_date_time"],
                    r["currency"],
                    r["payment_method"],
                    r.get("total_net"),
                    r.get("total_tax"),
                    r.get("total_gross"),
                    r.get("source_file_id"),
                    r.get("raw_content_id"),
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return int(row[0])

    def insert_items(self, receipt_id: int, items: List[Dict[str, Any]]) -> int:
        with self.connect() as conn:
            cur = conn.cursor()
            count = 0
            for it in items:
                cur.execute(
                    """
                    INSERT INTO receipt_items (
                        receipt_id, product_name, quantity, unit,
                        unit_price_net, unit_price_gross, tax_rate,
                        line_net, line_tax, line_gross
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        receipt_id,
                        it["product_name"],
                        float(it["quantity"]),
                        it.get("unit"),
                        it.get("unit_price_net"),
                        it.get("unit_price_gross"),
                        float(it["tax_rate"]),
                        it.get("line_net"),
                        it.get("line_tax"),
                        it.get("line_gross"),
                    ),
                )
                count += 1
            conn.commit()
            return count

    def insert_extraction_run(self, run: Dict[str, Any]) -> int:
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO extraction_runs (
                    receipt_id, model_name, prompt_version, finished_at, status, raw_content_id, notes
                ) VALUES (?, ?, ?, datetime('now'), ?, ?, ?)
                RETURNING run_id;
                """,
                (
                    run.get("receipt_id"),
                    run["model_name"],
                    run.get("prompt_version"),
                    run.get("status", "OK"),
                    run.get("raw_content_id"),
                    run.get("notes"),
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return int(row[0])
