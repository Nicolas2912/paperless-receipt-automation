"""Processed index facade for the orchestrator pipeline."""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import Optional

from ..logging import get_logger

LOG = get_logger("orchestrator-index")


def _import_processed_index():
    try:
        import processed_index  # type: ignore
    except Exception as exc:  # pragma: no cover - defensive fallback
        raise RuntimeError(f"processed_index module unavailable: {exc}")
    return processed_index


@dataclass
class IndexRecord:
    file_hash: str
    doc_id: Optional[int]
    title: Optional[str]


class ProcessedIndex:
    """Thin wrapper around the legacy processed_index module."""

    def __init__(self, root_dir: str) -> None:
        mod = _import_processed_index()
        self._mod = mod
        root_abs = os.path.abspath(root_dir)
        folder_name = getattr(mod, "DB_FOLDERNAME", "paperless_db")
        file_name = getattr(mod, "DB_FILENAME", "paperless.sqlite3")
        self._table_name = getattr(mod, "TABLE_NAME", "processed_files")
        planned_path = os.path.join(root_abs, folder_name, file_name)
        self.db_path = mod.ensure_db(root_dir)
        LOG.info(f"Processed index ready at {self.db_path}")
        if not os.path.exists(planned_path):
            planned_path = self.db_path
        self._initial_sync_needed = self._is_empty(planned_path)

    def _connect(self):
        if hasattr(self._mod, "_connect"):
            return self._mod._connect(self.db_path)
        return sqlite3.connect(self.db_path)

    def _count_records(self) -> int:
        try:
            conn = self._connect()
            try:
                cur = conn.cursor()
                cur.execute(f"SELECT COUNT(*) FROM {self._table_name}")
                row = cur.fetchone()
                return int(row[0]) if row else 0
            finally:
                conn.close()
        except Exception as exc:
            LOG.warning(f"Failed to count records in processed index: {exc}")
            return -1

    def _is_empty(self, planned_path: str) -> bool:
        if not os.path.exists(planned_path):
            LOG.info("Processed index database newly created; will attempt initial sync")
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

    def initial_sync_if_needed(self, *, watch_dir: str, base_url: str, token: str) -> None:
        if not self._initial_sync_needed:
            LOG.debug("Initial sync not required; processed index already hydrated")
            return
        if not hasattr(self._mod, "initial_sync_with_paperless"):
            LOG.warning("processed_index module lacks initial_sync_with_paperless; cannot hydrate index")
            self._initial_sync_needed = False
            return
        if not watch_dir:
            LOG.warning("Watch directory missing; skipping initial sync")
            return
        if not os.path.isdir(watch_dir):
            LOG.warning(f"Watch directory not found for initial sync: {watch_dir}")
            return
        LOG.info("Running initial sync against Paperless to avoid duplicate uploads")
        try:
            self._mod.initial_sync_with_paperless(
                db_path=self.db_path,
                watch_dir=watch_dir,
                base_url=base_url,
                token=token,
            )
        except Exception as exc:
            LOG.error(f"Initial sync with Paperless failed: {exc}")
            return
        count = self._count_records()
        if count >= 0:
            LOG.info(f"Initial sync stored {count} record(s) in processed index")
        self._initial_sync_needed = False

    def compute_hash(self, path: str) -> str:
        return self._mod.compute_file_hash(path)

    def is_processed(self, file_hash: str) -> bool:
        return self._mod.is_processed(self.db_path, file_hash)

    def mark_seen(self, file_hash: str) -> None:
        if hasattr(self._mod, "mark_seen"):
            self._mod.mark_seen(self.db_path, file_hash)

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
        self._mod.mark_processed(
            self.db_path,
            file_hash=file_hash,
            file_path=file_path,
            original_filename=original_filename,
            paperless_doc_id=doc_id,
            title=title,
        )
        return IndexRecord(file_hash=file_hash, doc_id=doc_id, title=title)

    def update_doc_id(self, file_hash: str, doc_id: int) -> None:
        if hasattr(self._mod, "_update_doc_id_for_hash"):
            LOG.info(f"Updating stored doc id for hash={file_hash[:8]} -> {doc_id}")
            self._mod._update_doc_id_for_hash(self.db_path, file_hash, doc_id)
