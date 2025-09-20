"""Processed index and initial Paperless sync for the orchestrator pipeline."""

from __future__ import annotations

import os
import re
import sqlite3
import hashlib
from dataclasses import dataclass
from typing import Optional

import requests

from ..logging import get_logger

LOG = get_logger("orchestrator-index")


@dataclass
class IndexRecord:
    file_hash: str
    doc_id: Optional[int]
    title: Optional[str]


DB_FILENAME = "paperless.sqlite3"
DB_FOLDERNAME = "paperless_db"
VAR_FOLDERNAME = "var"
TABLE_NAME = "processed_files"


class ProcessedIndex:
    def __init__(self, root_dir: str) -> None:
        root_abs = os.path.abspath(root_dir)
        # Place the processed-index into a dedicated var directory at repo root
        folder = os.path.join(root_abs, VAR_FOLDERNAME, DB_FOLDERNAME)
        os.makedirs(folder, exist_ok=True)
        self.db_path = os.path.join(folder, DB_FILENAME)
        self._ensure_schema()
        LOG.info(f"Processed index ready at {self.db_path}")
        self._initial_sync_needed = self._is_empty()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _ensure_schema(self) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            try:
                cur.execute("PRAGMA journal_mode=WAL;")
                cur.execute("PRAGMA synchronous=NORMAL;")
            except Exception:
                pass
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_hash TEXT NOT NULL UNIQUE,
                    file_path TEXT,
                    original_filename TEXT,
                    paperless_doc_id INTEGER,
                    title TEXT,
                    status TEXT DEFAULT 'processed',
                    created_at TEXT DEFAULT (datetime('now')),
                    last_seen_at TEXT DEFAULT (datetime('now'))
                );
                """
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_doc_id ON {TABLE_NAME}(paperless_doc_id);"
            )
            conn.commit()
        finally:
            conn.close()

    def _count_records(self) -> int:
        try:
            conn = self._connect()
            try:
                cur = conn.cursor()
                cur.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
                row = cur.fetchone()
                return int(row[0]) if row else 0
            finally:
                conn.close()
        except Exception as exc:
            LOG.warning(f"Failed to count records in processed index: {exc}")
            return -1

    def _is_empty(self) -> bool:
        if not os.path.exists(self.db_path):
            return True
        count = self._count_records()
        if count == 0:
            LOG.info("Processed index empty; scheduling initial sync with Paperless")
            return True
        if count > 0:
            LOG.info(f"Processed index already contains {count} record(s)")
            return False
        LOG.warning("Could not determine processed index size; will attempt initial sync defensively")
        return True

    # ---------------- hash + CRUD ----------------
    def compute_hash(self, path: str, chunk_size: int = 1024 * 1024) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while True:
                b = f.read(chunk_size)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()

    def is_processed(self, file_hash: str) -> bool:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT 1 FROM {TABLE_NAME} WHERE file_hash=?", (file_hash,))
            row = cur.fetchone()
            return row is not None
        finally:
            conn.close()

    def mark_seen(self, file_hash: str) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE {TABLE_NAME} SET last_seen_at=datetime('now') WHERE file_hash=?",
                (file_hash,),
            )
            conn.commit()
        finally:
            conn.close()

    def mark_processed(
        self,
        *,
        file_hash: str,
        file_path: str,
        original_filename: str,
        doc_id: Optional[int],
        title: Optional[str],
    ) -> IndexRecord:
        LOG.info(f"Recording processed file hash={file_hash[:8]} -> doc_id={doc_id}")
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                f"""
                INSERT INTO {TABLE_NAME} (file_hash, file_path, original_filename, paperless_doc_id, title, status)
                VALUES (?, ?, ?, ?, ?, 'processed')
                ON CONFLICT(file_hash) DO UPDATE SET
                    file_path=excluded.file_path,
                    original_filename=excluded.original_filename,
                    paperless_doc_id=excluded.paperless_doc_id,
                    title=excluded.title,
                    last_seen_at=datetime('now');
                """,
                (file_hash, file_path, original_filename, doc_id, title),
            )
            conn.commit()
        finally:
            conn.close()
        return IndexRecord(file_hash=file_hash, doc_id=doc_id, title=title)

    def _update_doc_id_for_hash(self, file_hash: str, doc_id: int) -> None:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                f"UPDATE {TABLE_NAME} SET paperless_doc_id=?, last_seen_at=datetime('now') WHERE file_hash=?",
                (doc_id, file_hash),
            )
            conn.commit()
        finally:
            conn.close()

    # ---------------- initial sync ----------------
    def initial_sync_if_needed(self, *, watch_dir: str, base_url: str, token: str) -> None:
        if not self._initial_sync_needed:
            LOG.debug("Initial sync not required; processed index already hydrated")
            return
        if not watch_dir or not os.path.isdir(watch_dir):
            LOG.warning("Watch directory missing; skipping initial sync")
            return
        LOG.info("Running initial sync against Paperless to avoid duplicate uploads")

        def _auth_headers(tok: str) -> dict[str, str]:
            return {"Authorization": f"Token {tok}", "Accept": "application/json"}

        def _get_json(url: str) -> Optional[dict]:
            try:
                r = requests.get(url, headers=_auth_headers(token), timeout=30)
                r.raise_for_status()
                return r.json()
            except Exception:
                return None

        def _find_by_original_filename(filename: str) -> Optional[int]:
            base = base_url.rstrip("/")
            url = f"{base}/api/documents/?original_filename__iexact={requests.utils.quote(filename)}&ordering=-id&page_size=1"
            data = _get_json(url) or {}
            results = data.get("results") if isinstance(data, dict) else None
            if results and isinstance(results, list) and isinstance(results[0], dict):
                did = results[0].get("id")
                return int(did) if isinstance(did, int) else None
            return None

        def _find_by_title_prefix(prefix: str) -> Optional[int]:
            base = base_url.rstrip("/")
            url = f"{base}/api/documents/?title__icontains={requests.utils.quote(prefix)}&ordering=-id&page_size=1"
            data = _get_json(url) or {}
            results = data.get("results") if isinstance(data, dict) else None
            if results and isinstance(results, list) and isinstance(results[0], dict):
                did = results[0].get("id")
                return int(did) if isinstance(did, int) else None
            return None

        names = []
        try:
            names = sorted([n for n in os.listdir(watch_dir) if os.path.isfile(os.path.join(watch_dir, n)) and os.path.splitext(n)[1].lower() in {".jpg", ".jpeg", ".jpe", ".jfif", ".pdf"}])
        except Exception as exc:
            LOG.warning(f"Failed to list watch_dir for sync: {exc}")

        synced = 0
        already = 0
        missing = 0
        for name in names:
            img_path = os.path.abspath(os.path.join(watch_dir, name))
            try:
                h = self.compute_hash(img_path)
            except Exception as e:
                LOG.warning(f"Failed to hash {img_path}: {e}")
                continue
            if self.is_processed(h):
                already += 1
                if self._get_doc_id_for_hash(h) is None:
                    base_no_ext = os.path.splitext(name)[0]
                    did0 = _find_by_original_filename(base_no_ext + ".pdf")
                    if isinstance(did0, int):
                        self._update_doc_id_for_hash(h, did0)
                    else:
                        m = re.match(r"^(\d{4}-\d{2}-\d{2})_(.+?)_\d+$", base_no_ext)
                        if m:
                            prefix = f"{m.group(1)} - {m.group(2).replace('_', ' ')}"
                            did1 = _find_by_title_prefix(prefix)
                            if isinstance(did1, int):
                                self._update_doc_id_for_hash(h, did1)
                self.mark_seen(h)
                continue

            base_no_ext = os.path.splitext(name)[0]
            did = _find_by_original_filename(base_no_ext + ".pdf")
            if not isinstance(did, int):
                m = re.match(r"^(\d{4}-\d{2}-\d{2})_(.+?)_\d+$", base_no_ext)
                if m:
                    prefix = f"{m.group(1)} - {m.group(2).replace('_', ' ')}"
                    did = _find_by_title_prefix(prefix) or did
                if not isinstance(did, int):
                    # final fallback – exact title match
                    base = base_url.rstrip("/")
                    url = f"{base}/api/documents/?title__iexact={requests.utils.quote(base_no_ext)}&ordering=-id&page_size=1"
                    data = _get_json(url) or {}
                    results = data.get("results") if isinstance(data, dict) else None
                    if results and isinstance(results, list) and isinstance(results[0], dict):
                        did = results[0].get("id")

            if isinstance(did, int):
                # fetch details to store original_filename and title
                url = f"{base_url.rstrip('/')}/api/documents/{did}/"
                info = _get_json(url) or {}
                original_filename = str(info.get("original_filename") or f"{base_no_ext}.pdf")
                title = str(info.get("title") or base_no_ext)
                self.mark_processed(
                    file_hash=h,
                    file_path=img_path,
                    original_filename=original_filename,
                    doc_id=did,
                    title=title,
                )
                synced += 1
            else:
                missing += 1
                LOG.info(f"No matching Paperless doc for: {name}; will process later")

        LOG.info(f"Sync completed. synced={synced}, already_in_db={already}, without_match={missing}")
        self._initial_sync_needed = False

    def _get_doc_id_for_hash(self, file_hash: str) -> Optional[int]:
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                f"SELECT paperless_doc_id FROM {TABLE_NAME} WHERE file_hash=?",
                (file_hash,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            did = row[0]
            return int(did) if isinstance(did, int) else None
        except Exception:
            return None
        finally:
            conn.close()
