import os
import sqlite3
import hashlib
import time
from typing import Optional, Dict, Any, List, Tuple

import requests as _rq

# New shared logging (Phase 1)
try:
    from src.paperless_automation.logging import get_logger  # type: ignore
except Exception:
    def get_logger(name: str):  # type: ignore
        class _L:
            def info(self, m):
                print(f"[{name}] {m}", flush=True)
            debug = info
            warning = info
            error = info
        return _L()

_LOG = get_logger("processed-index")


def debug(msg: str) -> None:
    _LOG.info(msg)


DB_FILENAME = "paperless.sqlite3"
DB_FOLDERNAME = "paperless_db"
TABLE_NAME = "processed_files"


def ensure_db(db_root_dir: str) -> str:
    """Ensure the DB folder and schema exist. Return absolute DB path.

    - DB file path: <db_root_dir>/paperless_db/paperless.sqlite3
    - Table: processed_files
    """
    root = os.path.abspath(db_root_dir)
    folder = os.path.join(root, DB_FOLDERNAME)
    os.makedirs(folder, exist_ok=True)
    db_path = os.path.join(folder, DB_FILENAME)

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        # Journal/WAL for better durability on Windows
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
    debug(f"Database ready at: {db_path}")
    return db_path


def _connect(db_path: str) -> sqlite3.Connection:
    return sqlite3.connect(db_path)


def compute_file_hash(path: str, chunk_size: int = 1024 * 1024) -> str:
    """Compute SHA-256 of a file efficiently."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk_size)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _normalize_exts(exts):
    """Normalize extensions to lowercase with a leading dot.

    Accepts any iterable of strings; returns a list preserving insertion order
    without duplicates.
    """
    out = []
    seen = set()
    for e in exts:
        if not e:
            continue
        ee = str(e).lower()
        if not ee.startswith('.'):
            ee = '.' + ee
        if ee not in seen:
            seen.add(ee)
            out.append(ee)
    return out


def is_processed(db_path: str, file_hash: str) -> bool:
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT 1 FROM {TABLE_NAME} WHERE file_hash=?", (file_hash,))
        row = cur.fetchone()
        return row is not None
    finally:
        conn.close()


def mark_seen(db_path: str, file_hash: str) -> None:
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE {TABLE_NAME} SET last_seen_at=datetime('now') WHERE file_hash=?",
            (file_hash,),
        )
        conn.commit()
    finally:
        conn.close()


def _get_doc_id_for_hash(db_path: str, file_hash: str) -> Optional[int]:
    conn = _connect(db_path)
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


def _update_doc_id_for_hash(db_path: str, file_hash: str, doc_id: int) -> None:
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE {TABLE_NAME} SET paperless_doc_id=?, last_seen_at=datetime('now') WHERE file_hash=?",
            (doc_id, file_hash),
        )
        conn.commit()
    finally:
        conn.close()


def mark_processed(
    db_path: str,
    *,
    file_hash: str,
    file_path: Optional[str] = None,
    original_filename: Optional[str] = None,
    paperless_doc_id: Optional[int] = None,
    title: Optional[str] = None,
) -> None:
    conn = _connect(db_path)
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
            (file_hash, file_path, original_filename, paperless_doc_id, title),
        )
        conn.commit()
    finally:
        conn.close()


def _auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Token {token}", "Accept": "application/json"}


def _api_find_document_by_title(base_url: str, token: str, title: str) -> Optional[int]:
    base = base_url.rstrip("/")
    url = f"{base}/api/documents/?title__iexact={_rq.utils.quote(title)}&ordering=-id&page_size=1"
    try:
        r = _rq.get(url, headers=_auth_headers(token), timeout=30)
        r.raise_for_status()
        data = r.json()
        results = data.get("results") if isinstance(data, dict) else None
        if results and isinstance(results, list) and isinstance(results[0], dict):
            did = results[0].get("id")
            if isinstance(did, int):
                return did
    except Exception as e:
        debug(f"WARN: Could not search document by title: {e}")
    return None


def _api_get_document(base_url: str, token: str, doc_id: int) -> Optional[Dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/api/documents/{doc_id}/"
    try:
        r = _rq.get(url, headers=_auth_headers(token), timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        debug(f"WARN: Failed to GET document {doc_id}: {e}")
        return None


def _api_find_document_by_original_filename(
    base_url: str, token: str, original_filename: str
) -> Optional[int]:
    """Find a document by exact original_filename (case-insensitive)."""
    base = base_url.rstrip("/")
    url = (
        f"{base}/api/documents/?original_filename__iexact="
        f"{_rq.utils.quote(original_filename)}&ordering=-id&page_size=1"
    )
    try:
        r = _rq.get(url, headers=_auth_headers(token), timeout=30)
        r.raise_for_status()
        data = r.json()
        results = data.get("results") if isinstance(data, dict) else None
        if results and isinstance(results, list) and isinstance(results[0], dict):
            did = results[0].get("id")
            if isinstance(did, int):
                return did
    except Exception as e:
        debug(f"WARN: Could not search by original_filename: {e}")
    return None


def _list_basenames_in_dir_by_ext(directory: str, exts) -> List[str]:
    """Return sorted basenames in directory matching the given extensions.

    - Non-recursive
    - Case-insensitive
    - Robust debug on errors
    """
    want = set(_normalize_exts(exts))
    try:
        names: List[str] = []
        for name in os.listdir(directory):
            full = os.path.join(directory, name)
            if os.path.isfile(full) and os.path.splitext(name)[1].lower() in want:
                names.append(name)
        return sorted(names)
    except Exception as e:
        debug(f"ERROR listing directory '{directory}': {e}")
        return []


def list_jpeg_basenames_in_dir(directory: str) -> List[str]:
    """Backward-compatible helper: only common JPEG extensions."""
    return _list_basenames_in_dir_by_ext(directory, {".jpg", ".jpeg", ".jpe", ".jfif"})


def list_pdf_basenames_in_dir(directory: str) -> List[str]:
    """New helper: list .pdf basenames in directory."""
    return _list_basenames_in_dir_by_ext(directory, {".pdf"})


def initial_sync_with_paperless(
    *,
    db_path: str,
    watch_dir: str,
    base_url: str,
    token: str,
) -> None:
    """
    Bring local DB in sync with Paperless for files currently in the watch dir.

    Strategy:
    - For each JPEG and PDF in watch_dir, compute content hash.
    - If already in DB: mark seen and continue.
    - Else: try to find a Paperless document by using the image basename (without
      extension) as the title (this matches the current flow which renames image
      and PDF consistently before upload). For PDFs, prefer exact match on
      original_filename.
    - When found, store the mapping in DB so future runs skip the file.
    """
    debug("Starting initial sync between SQLite and Paperless…")
    debug(f"Watch dir for sync: {watch_dir}")
    jpeg_names = list_jpeg_basenames_in_dir(watch_dir)
    pdf_names = list_pdf_basenames_in_dir(watch_dir)
    names = sorted(set(jpeg_names) | set(pdf_names))
    debug(f"Found {len(jpeg_names)} JPEG(s) and {len(pdf_names)} PDF(s); total considered={len(names)}")

    synced = 0
    already = 0
    missing = 0
    for name in names:
        img_path = os.path.abspath(os.path.join(watch_dir, name))
        try:
            h = compute_file_hash(img_path)
        except Exception as e:
            debug(f"WARN: Failed to hash {img_path}: {e}")
            continue
        if is_processed(db_path, h):
            already += 1
            # If we already have a record but no doc_id, try to fill it
            existing_id = _get_doc_id_for_hash(db_path, h)
            if existing_id is None:
                base_no_ext = os.path.splitext(name)[0]
                candidate_pdf_name = base_no_ext + ".pdf"
                did0 = _api_find_document_by_original_filename(base_url, token, candidate_pdf_name)
                if isinstance(did0, int):
                    _update_doc_id_for_hash(db_path, h, did0)
                    debug(f"Backfilled doc_id for existing record via original_filename: {did0}")
                else:
                    # Try title prefix fallback
                    date_part = None
                    vendor_part = None
                    try:
                        m = __import__("re").match(r"^(\d{4}-\d{2}-\d{2})_(.+?)_\d+$", base_no_ext)
                        if m:
                            date_part = m.group(1)
                            vendor_part = m.group(2).replace("_", " ")
                    except Exception:
                        date_part = None
                        vendor_part = None
                    if date_part and vendor_part:
                        base = base_url.rstrip("/")
                        url = (
                            f"{base}/api/documents/?title__icontains="
                            f"{_rq.utils.quote(date_part + ' - ' + vendor_part)}&ordering=-id&page_size=1"
                        )
                        try:
                            r = _rq.get(url, headers=_auth_headers(token), timeout=30)
                            r.raise_for_status()
                            data = r.json()
                            results = data.get("results") if isinstance(data, dict) else None
                            if results and isinstance(results[0], dict) and isinstance(results[0].get("id"), int):
                                _update_doc_id_for_hash(db_path, h, int(results[0]["id"]))
                                debug("Backfilled doc_id for existing record via title prefix")
                        except Exception:
                            pass
            mark_seen(db_path, h)
            continue

        base_no_ext = os.path.splitext(name)[0]
        # Prefer exact match on original_filename of the uploaded PDF
        candidate_pdf_name = base_no_ext + ".pdf"
        did = _api_find_document_by_original_filename(base_url, token, candidate_pdf_name)
        if not isinstance(did, int):
            # Fallback: try to find by title using reconstructed prefix
            # Pattern: YYYY-MM-DD_<Korrespondent>_<n>
            date_part = None
            vendor_part = None
            try:
                m = __import__("re").match(r"^(\d{4}-\d{2}-\d{2})_(.+?)_\d+$", base_no_ext)
                if m:
                    date_part = m.group(1)
                    vendor_part = m.group(2).replace("_", " ")
            except Exception:
                date_part = None
                vendor_part = None
            if date_part and vendor_part:
                title_prefix = f"{date_part} - {vendor_part}"
                # Use icontains on the prefix as a robust search
                base = base_url.rstrip("/")
                url = (
                    f"{base}/api/documents/?title__icontains="
                    f"{_rq.utils.quote(title_prefix)}&ordering=-id&page_size=1"
                )
                try:
                    r = _rq.get(url, headers=_auth_headers(token), timeout=30)
                    r.raise_for_status()
                    data = r.json()
                    results = data.get("results") if isinstance(data, dict) else None
                    if results and isinstance(results[0], dict) and isinstance(results[0].get("id"), int):
                        did = results[0]["id"]
                except Exception as e:
                    debug(f"WARN: Title prefix search failed: {e}")
            # Final minimal fallback: exact title equals base_no_ext (unlikely but cheap)
            if not isinstance(did, int):
                did = _api_find_document_by_title(base_url, token, base_no_ext)
        if isinstance(did, int):
            info = _api_get_document(base_url, token, did) or {}
            original_filename = str(info.get("original_filename") or f"{base_no_ext}.pdf")
            title = str(info.get("title") or base_no_ext)
            mark_processed(
                db_path,
                file_hash=h,
                file_path=img_path,
                original_filename=original_filename,
                paperless_doc_id=did,
                title=title,
            )
            synced += 1
            debug(f"Synced: {name} -> doc_id={did}, title={title!r}")
        else:
            missing += 1
            debug(f"No matching Paperless doc for: {name}; will process later")

    debug(
        f"Sync completed. synced={synced}, already_in_db={already}, without_match={missing}"
    )
