from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator, Optional, Dict, Any, List, Sequence, Tuple

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
        conn.row_factory = sqlite3.Row
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
            self._migrate_extraction_runs_drop_prompt_version(conn)
            cur.executescript(SCHEMA_SQL)
            conn.commit()
            LOG.info("Product DB schema ensured.")

    def _migrate_extraction_runs_drop_prompt_version(self, conn: sqlite3.Connection) -> None:
        """Remove legacy prompt_version column if older schema is detected."""
        cur = conn.cursor()
        try:
            cur.execute("PRAGMA table_info(extraction_runs);")
        except sqlite3.OperationalError:
            # Table does not exist yet; nothing to migrate.
            return

        columns = [row[1] for row in cur.fetchall()]
        if "prompt_version" not in columns:
            return

        LOG.info("Migrating extraction_runs table to drop obsolete prompt_version column")
        try:
            conn.execute("PRAGMA foreign_keys=OFF;")
            conn.execute("BEGIN IMMEDIATE;")
            cur.execute("DROP TABLE IF EXISTS extraction_runs_new;")
            cur.execute(
                """
                CREATE TABLE extraction_runs_new (
                  run_id         INTEGER PRIMARY KEY,
                  receipt_id     INTEGER REFERENCES receipts(receipt_id) ON DELETE CASCADE,
                  model_name     TEXT NOT NULL,
                  started_at     TEXT DEFAULT (datetime('now')),
                  finished_at    TEXT,
                  status         TEXT CHECK (status IN ('OK','WARN','ERROR')) DEFAULT 'OK',
                  raw_content_id INTEGER REFERENCES texts(text_id) ON DELETE SET NULL,
                  notes          TEXT
                );
                """
            )
            cur.execute(
                """
                INSERT INTO extraction_runs_new (
                  run_id,
                  receipt_id,
                  model_name,
                  started_at,
                  finished_at,
                  status,
                  raw_content_id,
                  notes
                )
                SELECT
                  run_id,
                  receipt_id,
                  model_name,
                  started_at,
                  finished_at,
                  status,
                  raw_content_id,
                  notes
                FROM extraction_runs;
                """
            )
            cur.execute("DROP TABLE extraction_runs;")
            cur.execute("ALTER TABLE extraction_runs_new RENAME TO extraction_runs;")
            conn.commit()
        except Exception:
            LOG.exception("Failed to migrate extraction_runs table; rolling back changes")
            try:
                conn.rollback()
            except sqlite3.OperationalError:
                pass
            raise
        finally:
            conn.execute("PRAGMA foreign_keys=ON;")

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
                    receipt_id, model_name, finished_at, status, raw_content_id, notes
                ) VALUES (?, ?, datetime('now'), ?, ?, ?)
                RETURNING run_id;
                """,
                (
                    run.get("receipt_id"),
                    run["model_name"],
                    run.get("status", "OK"),
                    run.get("raw_content_id"),
                    run.get("notes"),
                ),
            )
            row = cur.fetchone()
            conn.commit()
            return int(row[0])

    # --------------- Query helpers ---------------
    @staticmethod
    def _rows_to_dicts(rows: Sequence[sqlite3.Row]) -> List[Dict[str, Any]]:
        return [dict(row) for row in rows]

    @staticmethod
    def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
        return dict(row) if row is not None else None

    def fetch_summary(self) -> Dict[str, Any]:
        """Return high-level counts and total amounts for dashboard views."""
        with self.connect() as conn:
            cur = conn.cursor()
            table_counts: Dict[str, int] = {}
            for table in (
                "addresses",
                "merchants",
                "files",
                "texts",
                "receipts",
                "receipt_items",
                "extraction_runs",
            ):
                cur.execute(f"SELECT COUNT(*) AS count FROM {table};")
                table_counts[table] = int(cur.fetchone()["count"])

            cur.execute(
                """
                SELECT
                    COALESCE(SUM(total_net), 0) AS total_net,
                    COALESCE(SUM(total_tax), 0) AS total_tax,
                    COALESCE(SUM(total_gross), 0) AS total_gross
                FROM receipts;
                """
            )
            totals_row = cur.fetchone()
            totals = {
                "total_net_cents": int(totals_row["total_net"] or 0),
                "total_tax_cents": int(totals_row["total_tax"] or 0),
                "total_gross_cents": int(totals_row["total_gross"] or 0),
            }

            cur.execute(
                """
                SELECT
                    MIN(purchase_date_time) AS first_purchase,
                    MAX(purchase_date_time) AS last_purchase
                FROM receipts;
                """
            )
            span_row = cur.fetchone()

            return {
                "counts": table_counts,
                "totals": totals,
                "timespan": {
                    "first_purchase": span_row["first_purchase"],
                    "last_purchase": span_row["last_purchase"],
                },
            }

    def fetch_receipts_overview(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        search: Optional[str] = None,
        merchant_id: Optional[int] = None,
        sort: str = "purchase_date_time",
        direction: str = "desc",
    ) -> Dict[str, Any]:
        """Return paginated receipt rows joined with merchant/address details."""
        sort_map = {
            "purchase_date_time": "r.purchase_date_time",
            "total_gross": "r.total_gross",
            "merchant": "m.name",
            "item_count": "item_count",
        }
        sort_key = sort_map.get(sort, "r.purchase_date_time")
        sort_dir = "DESC" if str(direction).lower() != "asc" else "ASC"

        where_clauses: List[str] = []
        params: List[Any] = []
        if merchant_id is not None:
            where_clauses.append("r.merchant_id = ?")
            params.append(int(merchant_id))
        if search:
            like = f"%{search.lower()}%"
            where_clauses.append(
                "(LOWER(m.name) LIKE ? OR LOWER(r.purchase_date_time) LIKE ? OR LOWER(r.currency) LIKE ? OR LOWER(r.payment_method) LIKE ?)"
            )
            params.extend([like, like, like, like])

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        base_query = f"""
            SELECT
                r.receipt_id,
                r.purchase_date_time,
                r.currency,
                r.payment_method,
                r.total_net,
                r.total_tax,
                r.total_gross,
                r.merchant_id,
                m.name AS merchant_name,
                a.city AS merchant_city,
                a.country AS merchant_country,
                COALESCE(items.item_count, 0) AS item_count
            FROM receipts r
            JOIN merchants m ON m.merchant_id = r.merchant_id
            LEFT JOIN addresses a ON a.address_id = m.address_id
            LEFT JOIN (
                SELECT receipt_id, COUNT(*) AS item_count
                FROM receipt_items
                GROUP BY receipt_id
            ) AS items ON items.receipt_id = r.receipt_id
            {where_sql}
            ORDER BY {sort_key} {sort_dir}, r.receipt_id DESC
            LIMIT ? OFFSET ?;
        """

        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT COUNT(*) AS total FROM receipts r JOIN merchants m ON m.merchant_id = r.merchant_id {where_sql};",
                params,
            )
            total = int(cur.fetchone()["total"])

            cur.execute(base_query, (*params, int(limit), int(offset)))
            rows = self._rows_to_dicts(cur.fetchall())

        return {"total": total, "items": rows, "limit": limit, "offset": offset}

    def fetch_receipt_detail(self, receipt_id: int) -> Optional[Dict[str, Any]]:
        """Return detailed receipt information with related entities."""
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                    r.receipt_id,
                    r.purchase_date_time,
                    r.currency,
                    r.payment_method,
                    r.total_net,
                    r.total_tax,
                    r.total_gross,
                    r.source_file_id,
                    r.raw_content_id,
                    r.created_at,
                    m.merchant_id,
                    m.name AS merchant_name,
                    a.address_id,
                    a.street,
                    a.city,
                    a.postal_code,
                    a.country,
                    f.file_id,
                    f.filename,
                    f.mime_type,
                    f.byte_size,
                    f.sha256
                FROM receipts r
                JOIN merchants m ON m.merchant_id = r.merchant_id
                LEFT JOIN addresses a ON a.address_id = m.address_id
                LEFT JOIN files f ON f.file_id = r.source_file_id
                WHERE r.receipt_id = ?;
                """,
                (int(receipt_id),),
            )
            receipt = self._row_to_dict(cur.fetchone())
            if receipt is None:
                return None

            cur.execute(
                """
                SELECT
                    item_id,
                    product_name,
                    quantity,
                    unit,
                    unit_price_net,
                    unit_price_gross,
                    tax_rate,
                    line_net,
                    line_tax,
                    line_gross,
                    created_at
                FROM receipt_items
                WHERE receipt_id = ?
                ORDER BY item_id ASC;
                """,
                (receipt_id,),
            )
            items = self._rows_to_dicts(cur.fetchall())

            cur.execute(
                """
                SELECT
                    run_id,
                    model_name,
                    started_at,
                    finished_at,
                    status,
                    raw_content_id,
                    notes
                FROM extraction_runs
                WHERE receipt_id = ?
                ORDER BY run_id DESC;
                """,
                (receipt_id,),
            )
            runs = self._rows_to_dicts(cur.fetchall())

            raw_content_id = receipt.get("raw_content_id")
            raw_text: Optional[str] = None
            if raw_content_id:
                cur.execute("SELECT content FROM texts WHERE text_id = ?;", (raw_content_id,))
                row = cur.fetchone()
                if row:
                    raw_text = row["content"]

            receipt.update({"items": items, "extraction_runs": runs, "raw_content": raw_text})
            return receipt

    def fetch_merchants_overview(self) -> List[Dict[str, Any]]:
        """Return merchants with aggregated spend and receipt count."""
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                    m.merchant_id,
                    m.name AS merchant_name,
                    m.created_at,
                    m.address_id,
                    a.city,
                    a.country,
                    COUNT(r.receipt_id) AS receipt_count,
                    COALESCE(SUM(r.total_gross), 0) AS total_gross_cents
                FROM merchants m
                LEFT JOIN addresses a ON a.address_id = m.address_id
                LEFT JOIN receipts r ON r.merchant_id = m.merchant_id
                GROUP BY m.merchant_id
                ORDER BY receipt_count DESC, m.name ASC;
                """
            )
            return self._rows_to_dicts(cur.fetchall())

    def fetch_table_rows(
        self,
        table: str,
        *,
        limit: int = 200,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Fetch raw table rows for the supported schema tables."""
        allowed: Dict[str, Tuple[str, str]] = {
            "addresses": ("addresses", "address_id"),
            "merchants": ("merchants", "merchant_id"),
            "files": ("files", "file_id"),
            "texts": ("texts", "text_id"),
            "receipts": ("receipts", "receipt_id"),
            "receipt_items": ("receipt_items", "item_id"),
            "extraction_runs": ("extraction_runs", "run_id"),
        }
        if table not in allowed:
            raise ValueError(f"Unsupported table: {table}")

        table_name, pk = allowed[table]
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) AS total FROM {table_name};")
            total = int(cur.fetchone()["total"])

            cur.execute(
                f"SELECT * FROM {table_name} ORDER BY {pk} DESC LIMIT ? OFFSET ?;",
                (int(limit), int(offset)),
            )
            rows = self._rows_to_dicts(cur.fetchall())

        return {"total": total, "items": rows, "limit": limit, "offset": offset}
