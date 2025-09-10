import os
import sys
import argparse
from typing import Optional, Dict, Any, List
import re
import requests as _rq

# Local components
try:
    from scan_event_listener import (
        ScanEventListener,
        debug_print as scan_debug,
        read_watch_dir_from_file as _read_watch_dir_from_file,
    )
except Exception as e:
    ScanEventListener = None  # type: ignore
    def scan_debug(msg: str) -> None:
        print(f"[scan-listener] {msg}", flush=True)
    print(f"[WARN] Could not import ScanEventListener: {e}", flush=True)

try:
    from ollama_transcriber import transcribe_image_via_ollama
except Exception as e:
    transcribe_image_via_ollama = None  # type: ignore
    print(f"[WARN] Could not import transcriber: {e}", flush=True)

try:
    from preconsume_overlay_pdf import create_pdf_with_invisible_text, ensure_dir, unique_path
except Exception as e:
    create_pdf_with_invisible_text = None  # type: ignore
    def ensure_dir(p: str) -> str:
        ap = os.path.abspath(p)
        os.makedirs(ap, exist_ok=True)
        return ap
    def unique_path(base_path: str) -> str:
        if not os.path.exists(base_path):
            return base_path
        stem, ext = os.path.splitext(base_path)
        i = 1
        while True:
            cand = f"{stem} ({i}){ext}"
            if not os.path.exists(cand):
                return cand
            i += 1
    print(f"[WARN] Could not import preconsume utilities: {e}", flush=True)

try:
    from extract_metadata import extract_from_source, ExtractedMetadata, _normalize_korrespondent
except Exception as e:
    extract_from_source = None  # type: ignore
    ExtractedMetadata = None  # type: ignore
    def _normalize_korrespondent(x: str) -> str:  # type: ignore
        return (x or "").strip()
    print(f"[WARN] Could not import extract_metadata: {e}", flush=True)

try:
    from rename_documents import rename_with_metadata
except Exception as e:
    rename_with_metadata = None  # type: ignore
    print(f"[WARN] Could not import rename_documents: {e}", flush=True)

try:
    from upload_paperless import (
        upload_document,
        ensure_correspondent_id,
        ensure_tag_ids,
        ensure_document_type_id,
        _api_patch_document,
    )
except Exception as e:
    upload_document = None  # type: ignore
    ensure_correspondent_id = None  # type: ignore
    ensure_tag_ids = None  # type: ignore
    ensure_document_type_id = None  # type: ignore
    _api_patch_document = None  # type: ignore
    print(f"[WARN] Could not import upload_paperless helpers: {e}", flush=True)

try:
    from processed_index import (
        ensure_db as _ensure_db,
        compute_file_hash as _compute_file_hash,
        is_processed as _is_processed,
        mark_processed as _mark_processed,
        initial_sync_with_paperless as _initial_sync_with_paperless,
        _update_doc_id_for_hash as _update_doc_id_for_hash,
        list_jpeg_basenames_in_dir as _list_jpegs,
    )
except Exception as e:
    _ensure_db = None  # type: ignore
    _compute_file_hash = None  # type: ignore
    _is_processed = None  # type: ignore
    _mark_processed = None  # type: ignore
    _initial_sync_with_paperless = None  # type: ignore
    print(f"[WARN] Could not import processed_index: {e}", flush=True)


def debug(msg: str) -> None:
    print(f"[main-paperless-flow] {msg}", flush=True)


def load_token(dotenv_dir: str) -> Optional[str]:
    # Mirror upload_paperless _load_token_from_env_or_dotenv behavior
    tok = os.environ.get("PAPERLESS_TOKEN")
    if tok:
        debug("Using PAPERLESS_TOKEN from environment.")
        return tok.strip()
    dotenv_path = os.path.join(dotenv_dir, ".env")
    if not os.path.isfile(dotenv_path):
        debug(f"No .env found at: {dotenv_path}")
        return None
    debug(f"Attempting to read PAPERLESS_TOKEN from .env: {dotenv_path}")
    try:
        with open(dotenv_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or line.startswith(";"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() != "PAPERLESS_TOKEN":
                    continue
                v = v.strip()
                if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                    v = v[1:-1]
                v = v.strip()
                if v:
                    debug("Loaded PAPERLESS_TOKEN from .env file.")
                    return v
    except Exception as e:
        debug(f"Failed reading .env file: {e}")
    return None


def load_tag_map(script_dir: str) -> Dict[str, str]:
    tag_map_path = os.path.join(script_dir, "tag_map.json")
    if not os.path.isfile(tag_map_path):
        debug("No tag_map.json found; proceeding without tag mapping.")
        return {}
    try:
        import json
        with open(tag_map_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            debug(f"Loaded tag_map.json with {len(data)} entries")
            return data
    except Exception as e:
        debug(f"WARN: Failed to read tag_map.json: {e}")
    return {}


def transcribe_to_text(image_path: str, *, ollama_url: str, ollama_model: str) -> Optional[str]:
    if transcribe_image_via_ollama is None:
        debug("FATAL: transcribe_image_via_ollama not available.")
        return None
    debug(f"Calling Ollama for transcription: url={ollama_url}, model={ollama_model}")
    return transcribe_image_via_ollama(image_path=image_path, model=ollama_model, ollama_url=ollama_url)


def create_overlay_pdf(image_path: str, text: str, out_dir: str) -> Optional[str]:
    if create_pdf_with_invisible_text is None:
        debug("FATAL: create_pdf_with_invisible_text not available.")
        return None
    ensure_dir(out_dir)
    base = os.path.splitext(os.path.basename(image_path))[0]
    out_pdf = unique_path(os.path.join(out_dir, f"{base}.pdf"))
    debug(f"Creating overlay PDF: {out_pdf}")
    try:
        create_pdf_with_invisible_text(image_path, text, out_pdf)
        return out_pdf
    except Exception as e:
        debug(f"ERROR creating overlay PDF: {e}")
        return None


def extract_md_for_upload(source_pdf: str, *, ollama_url: str, ollama_model: str) -> Optional[ExtractedMetadata]:
    if extract_from_source is None:
        debug("FATAL: extract_from_source not available.")
        return None
    debug(f"Extracting structured metadata from: {source_pdf}")
    return extract_from_source(source_pdf, ollama_url=ollama_url, model=ollama_model)


def _norm_date(text: str) -> Optional[str]:
    m = re.search(r"(\d{1,2})[./](\d{1,2})[./](\d{2,4})", text)
    if m:
        d, mth, y = m.groups()
        if len(y) == 2:
            y = ("20" + y) if int(y) < 70 else ("19" + y)
        return f"{int(y):04d}-{int(mth):02d}-{int(d):02d}"
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return m.group(0)
    return None


def _norm_amount(text: str) -> Optional[str]:
    # Prefer amounts labeled as total
    total_block = re.findall(r"(?is)(summe|gesamt|total)[^\d]*(\d+[.,]\d{2})", text)
    cand = total_block[-1][1] if total_block else None
    if not cand:
        # Fallback: pick the largest decimal-looking number
        nums = [n for n in re.findall(r"\d+[.,]\d{2}", text)]
        cand = max(nums, key=len) if nums else None
    if not cand:
        return None
    s = cand.strip().replace(" ", "")
    has_dot = "." in s
    has_comma = "," in s
    if has_dot and has_comma:
        if re.search(r",\d{1,2}$", s):
            s = s.replace(".", "").replace(",", ".")
        elif re.search(r"\.\d{1,2}$", s):
            s = s.replace(",", "")
        else:
            s = s.replace(".", "").replace(",", ".")
    elif has_comma:
        if re.search(r",\d{1,2}$", s):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif has_dot:
        if not re.search(r"\.\d{1,2}$", s):
            s = s.replace(".", "")
    try:
        from decimal import Decimal
        return f"{Decimal(s):.2f}"
    except Exception:
        try:
            return f"{float(s):.2f}"
        except Exception:
            return None


def _detect_currency(text: str) -> str:
    if "€" in text or re.search(r"\bEUR\b", text, re.I):
        return "EUR"
    if "$" in text or re.search(r"\bUSD\b", text, re.I):
        return "USD"
    return "EUR"


def _guess_merchant(text: str) -> str:
    # Use first non-empty line that doesn't look like generic words
    lines = [ln.strip() for ln in re.split(r"[\r\n]+", text) if ln.strip()]
    blacklist = {"kassenbon", "rechnung", "beleg", "bon"}
    for ln in lines[:10]:
        low = re.sub(r"[^a-z0-9äöüß ]", "", ln.lower())
        if all(w not in low for w in blacklist) and len(ln) >= 2:
            return ln[:60]
    return "Unbekannt"


def extract_metadata_from_text(text: str) -> Optional[ExtractedMetadata]:
    if ExtractedMetadata is None:
        return None
    date_iso = _norm_date(text) or "1970-01-01"
    amount = _norm_amount(text) or "0.00"
    currency = _detect_currency(text)
    merchant = _normalize_korrespondent(_guess_merchant(text))
    try:
        return ExtractedMetadata(
            korrespondent=merchant,
            ausstellungsdatum=date_iso,
            betrag_value=amount,
            betrag_currency=currency,
        )
    except Exception:
        return None


def build_upload_fields(
    md: ExtractedMetadata,
    *,
    base_url: str,
    token: str,
    script_dir: str,
) -> Dict[str, Any]:
    # Map correspondent and document type
    correspondent_id = ensure_correspondent_id(base_url, token, md.korrespondent) if ensure_correspondent_id else None
    document_type_id = ensure_document_type_id(base_url, token, md.dokumenttyp) if ensure_document_type_id else None
    if correspondent_id:
        debug(f"Resolved correspondent '{md.korrespondent}' -> id={correspondent_id}")
    else:
        debug(f"WARN: Could not resolve/create correspondent for '{md.korrespondent}'")
    if document_type_id:
        debug(f"Resolved document type '{md.dokumenttyp}' -> id={document_type_id}")

    # Tags via tag_map
    tag_map = load_tag_map(script_dir)
    tag_ids: List[int] = []
    if tag_map and isinstance(tag_map, dict):
        tag_name = tag_map.get(md.korrespondent.lower())
        if tag_name:
            tag_ids = ensure_tag_ids(base_url, token, [tag_name]) if ensure_tag_ids else []
            debug(f"Mapped tag '{tag_name}' -> ids={tag_ids}")

    # Title without ASN
    title = md.title()
    created = md.ausstellungsdatum
    debug(f"Final initial title: {title}")

    return {
        "title": title,
        "created": created,
        "correspondent_id": correspondent_id,
        "document_type_id": document_type_id,
        "tag_ids": tag_ids,
    }


def upload_with_asn(
    pdf_path: str,
    base_url: str,
    token: str,
    fields: Dict[str, Any],
    *,
    timeout: int = 60,
    insecure: bool = False,
) -> Dict[str, Any]:
    debug("Uploading PDF to Paperless …")
    base_title = fields.get("title") or ""
    result = upload_document(
        file_path=pdf_path,
        base_url=base_url,
        token=token,
        title=base_title,
        created=fields.get("created"),
        correspondent_id=fields.get("correspondent_id"),
        tag_ids=fields.get("tag_ids"),
        document_type_id=fields.get("document_type_id"),
        timeout=timeout,
        verify_tls=not insecure,
    )
    # Try to resolve doc id (handle direct, task numeric id, and task uuid)
    doc_id: Optional[int] = None
    rj = result.get("json") if isinstance(result, dict) else None
    if isinstance(rj, dict):
        try:
            if isinstance(rj.get("id"), int):
                doc_id = rj["id"]
            elif isinstance(rj.get("document"), dict) and isinstance(rj["document"].get("id"), int):
                doc_id = rj["document"]["id"]
            elif isinstance(rj.get("results"), list) and rj["results"]:
                cand = rj["results"][0]
                if isinstance(cand, dict) and isinstance(cand.get("id"), int):
                    doc_id = cand["id"]
        except Exception:
            pass
        # Task polling fallback
        if not doc_id:
            t_id_int = None
            t_id_uuid = None
            try:
                if isinstance(rj.get("task"), dict):
                    if isinstance(rj["task"].get("id"), int):
                        t_id_int = rj["task"]["id"]
                    if isinstance(rj["task"].get("task_id"), str):
                        t_id_uuid = rj["task"]["task_id"]
                if isinstance(rj.get("task_id"), int):
                    t_id_int = rj["task_id"]
                elif isinstance(rj.get("task_id"), str):
                    t_id_uuid = rj["task_id"]
                elif (isinstance(rj.get("status"), str) and isinstance(rj.get("id"), int)
                      and any(k in rj for k in ("state", "status", "result", "url"))):
                    t_id_int = rj["id"]
            except Exception:
                t_id_int = None
                t_id_uuid = None

            import time as _t
            import requests as _rq

            if t_id_int is not None:
                task_url = f"{base_url.rstrip('/')}/api/tasks/{t_id_int}/"
                debug(f"Polling numeric task {t_id_int} for document id …")
                for attempt in range(1, 41):
                    try:
                        tr = _rq.get(task_url, headers={"Authorization": f"Token {token}", "Accept": "application/json"}, timeout=15)
                        tr.raise_for_status()
                        tj = tr.json()
                    except Exception as e:
                        debug(f"WARN: Task poll failed on attempt {attempt}: {e}")
                        _t.sleep(0.5)
                        continue
                    cand_id = None
                    try:
                        if isinstance(tj, dict):
                            if isinstance(tj.get("result"), dict):
                                res = tj["result"]
                                if isinstance(res.get("document"), dict) and isinstance(res["document"].get("id"), int):
                                    cand_id = res["document"]["id"]
                                elif isinstance(res.get("document_id"), int):
                                    cand_id = res["document_id"]
                                elif isinstance(res.get("id"), int):
                                    cand_id = res["id"]
                            if cand_id is None and isinstance(tj.get("url"), str):
                                m = re.search(r"/api/documents/(\d+)/", tj["url"])  # noqa: E501
                                if m:
                                    cand_id = int(m.group(1))
                            if cand_id is None and isinstance(tj.get("document"), dict) and isinstance(tj["document"].get("id"), int):
                                cand_id = tj["document"]["id"]
                    except Exception:
                        cand_id = None
                    if isinstance(cand_id, int):
                        doc_id = cand_id
                        debug(f"Resolved document id from numeric task: {doc_id}")
                        break
                    _t.sleep(0.5)

            if doc_id is None and t_id_uuid is not None:
                list_url = f"{base_url.rstrip('/')}/api/tasks/?task_id={t_id_uuid}&page_size=1"
                debug(f"Polling uuid task {t_id_uuid} for document id …")
                for attempt in range(1, 41):
                    try:
                        tr = _rq.get(list_url, headers={"Authorization": f"Token {token}", "Accept": "application/json"}, timeout=15)
                        tr.raise_for_status()
                        data = tr.json()
                        results = data.get("results") if isinstance(data, dict) else None
                        tj = results[0] if results else None
                    except Exception as e:
                        debug(f"WARN: UUID task poll failed on attempt {attempt}: {e}")
                        _t.sleep(0.5)
                        continue
                    cand_id = None
                    try:
                        if isinstance(tj, dict):
                            if isinstance(tj.get("result"), dict):
                                res = tj["result"]
                                if isinstance(res.get("document"), dict) and isinstance(res["document"].get("id"), int):
                                    cand_id = res["document"]["id"]
                                elif isinstance(res.get("document_id"), int):
                                    cand_id = res["document_id"]
                                elif isinstance(res.get("id"), int):
                                    cand_id = res["id"]
                            if cand_id is None and isinstance(tj.get("url"), str):
                                m = re.search(r"/api/documents/(\d+)/", tj["url"])  # noqa: E501
                                if m:
                                    cand_id = int(m.group(1))
                            if cand_id is None and isinstance(tj.get("document"), dict) and isinstance(tj["document"].get("id"), int):
                                cand_id = tj["document"]["id"]
                    except Exception:
                        cand_id = None
                    if isinstance(cand_id, int):
                        doc_id = cand_id
                        debug(f"Resolved document id from uuid task: {doc_id}")
                        break
                    _t.sleep(0.5)

        # Fallback: if still none, try to match by title
        if not doc_id and isinstance(base_title, str) and base_title:
            try:
                list_url = f"{base_url.rstrip('/')}/api/documents/?title__iexact={_rq.utils.quote(base_title)}&ordering=-id&page_size=1"
                tr = _rq.get(list_url, headers={"Authorization": f"Token {token}", "Accept": "application/json"}, timeout=20)
                tr.raise_for_status()
                dj = tr.json()
                results = dj.get("results") if isinstance(dj, dict) else None
                if results and isinstance(results[0], dict) and isinstance(results[0].get("id"), int):
                    doc_id = results[0]["id"]
                    debug(f"Resolved document id by title search: {doc_id}")
            except Exception as e:
                debug(f"WARN: Fallback title search failed: {e}")
    # Helper: GET document
    def _get_doc(did: int) -> Optional[Dict[str, Any]]:
        try:
            u = f"{base_url.rstrip('/')}/api/documents/{did}/"
            rr = _rq.get(u, headers={"Authorization": f"Token {token}", "Accept": "application/json"}, timeout=20)
            rr.raise_for_status()
            return rr.json()
        except Exception as e:
            debug(f"WARN: GET document {did} failed: {e}")
            return None

    # Enforce exact tags from mapping (avoid duplicate/default tags from server rules)
    try:
        desired_tag_ids = fields.get("tag_ids") or []
        if isinstance(desired_tag_ids, list) and desired_tag_ids and isinstance(doc_id, int):
            # Deduplicate while preserving order
            seen = set()
            dedup = []
            for t in desired_tag_ids:
                if isinstance(t, int) and t not in seen:
                    seen.add(t)
                    dedup.append(t)
            if dedup:
                debug(f"Patching document {doc_id} to enforce tags={dedup}")
                try:
                    _api_patch_document(base_url, token, doc_id, {"tags": dedup})  # type: ignore[arg-type]
                    debug("Tags patched successfully to exact set from tag_map.")
                except Exception as e:
                    debug(f"WARN: Failed to patch tags on document {doc_id}: {e}")
        else:
            if not desired_tag_ids:
                debug("No mapped tags to enforce; skipping tag patch step.")
            if not isinstance(doc_id, int):
                debug("Document ID unresolved; cannot patch tags.")
    except Exception as e:
        debug(f"WARN: Exception during tag enforcement: {e}")

    # Attach resolved document id to the result for downstream consumers.
    try:
        if isinstance(result, dict):
            result["doc_id"] = doc_id
    except Exception:
        pass
    return result


def process_one_image(
    image_path: str,
    *,
    ollama_url: str,
    ollama_model: str,
    base_url: str,
    token: str,
    out_dir: str,
    insecure: bool = False,
    timeout: int = 60,
    listener=None,
    db_path: Optional[str] = None,
    precomputed_hash: Optional[str] = None,
) -> Optional[str]:
    debug(f"Processing image: {image_path}")
    # Compute file hash early for DB tracking
    file_hash: Optional[str] = None
    try:
        if precomputed_hash is not None:
            file_hash = precomputed_hash
        elif _compute_file_hash is not None:
            file_hash = _compute_file_hash(image_path)
    except Exception as e:
        debug(f"WARN: Could not compute file hash: {e}")
    text = transcribe_to_text(image_path, ollama_url=ollama_url, ollama_model=ollama_model)
    if not text:
        debug("ERROR: Transcription failed or returned empty text. Skipping.")
        return None
    pdf_path = create_overlay_pdf(image_path, text, out_dir)
    if not pdf_path:
        debug("ERROR: PDF overlay creation failed. Skipping.")
        return None
    # Prefer extracting structured fields from the already transcribed text to reduce VLM work
    md = extract_metadata_from_text(text)
    if md is None:
        md = extract_md_for_upload(pdf_path, ollama_url=ollama_url, ollama_model=ollama_model)
    if md is None:
        debug("ERROR: Metadata extraction failed. Skipping upload.")
        return None
    # Rename image and PDF right before upload using extracted metadata
    if rename_with_metadata is None:
        debug("FATAL: rename_documents module not available.")
        return None
    try:
        old_base = os.path.basename(image_path)
        new_image_path, new_pdf_path = rename_with_metadata(
            image_path,
            pdf_path,
            date_iso=getattr(md, "ausstellungsdatum", "1970-01-01"),
            korrespondent=getattr(md, "korrespondent", "Unbekannt"),
        )
        # Update locals for upload path
        image_path = new_image_path
        pdf_path = new_pdf_path
        debug(f"Renamed for upload -> image: {image_path}")
        debug(f"Renamed for upload -> pdf:   {pdf_path}")
        # Prevent the watcher from re-detecting the rename as a new file
        if listener is not None and hasattr(listener, "baseline"):
            try:
                new_base = os.path.basename(new_image_path)
                listener.baseline.add(new_base)  # type: ignore[attr-defined]
                # Keep last_new_image_path in sync for any consumer
                if hasattr(listener, "last_new_image_path"):
                    listener.last_new_image_path = new_image_path  # type: ignore[attr-defined]
                debug(f"Updated watcher baseline with renamed file: {new_base}")
            except Exception as e:
                debug(f"WARN: Could not update watcher baseline after rename: {e}")
    except Exception as e:
        debug(f"ERROR: Failed to rename files before upload: {e}")
        return None
    fields = build_upload_fields(md, base_url=base_url, token=token, script_dir=os.path.dirname(os.path.abspath(__file__)))
    result = upload_with_asn(pdf_path, base_url, token, fields, insecure=insecure, timeout=timeout)
    try:
        import json
        debug("Upload result:")
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except Exception:
        print(result)
    # Try to extract a document ID from the response for DB linking
    doc_id: Optional[int] = None
    try:
        if isinstance(result, dict) and isinstance(result.get("doc_id"), int):
            doc_id = result.get("doc_id")
        else:
            rj = result.get("json") if isinstance(result, dict) else None
            if isinstance(rj, dict):
                if isinstance(rj.get("id"), int):
                    doc_id = rj["id"]
                elif isinstance(rj.get("document"), dict) and isinstance(rj["document"].get("id"), int):
                    doc_id = rj["document"]["id"]
                elif isinstance(rj.get("results"), list) and rj["results"] and isinstance(rj["results"][0], dict):
                    cand = rj["results"][0]
                    if isinstance(cand.get("id"), int):
                        doc_id = cand["id"]
    except Exception:
        doc_id = None

    # Final best-effort: resolve by original_filename (the uploaded PDF basename)
    if doc_id is None:
        try:
            base = os.path.basename(pdf_path)
            list_url = f"{base_url.rstrip('/')}/api/documents/?original_filename__iexact={_rq.utils.quote(base)}&ordering=-id&page_size=1"
            tr = _rq.get(list_url, headers={"Authorization": f"Token {token}", "Accept": "application/json"}, timeout=20)
            tr.raise_for_status()
            dj = tr.json()
            results = dj.get("results") if isinstance(dj, dict) else None
            if results and isinstance(results[0], dict) and isinstance(results[0].get("id"), int):
                doc_id = results[0]["id"]
                debug(f"Resolved document id by original_filename: {doc_id}")
        except Exception as e:
            debug(f"WARN: original_filename lookup failed: {e}")

    # Record in processed DB
    try:
        if db_path and _mark_processed is not None:
            base_title = fields.get("title") if isinstance(fields, dict) else None
            original_filename = os.path.basename(pdf_path)
            if file_hash is None and _compute_file_hash is not None:
                try:
                    file_hash = _compute_file_hash(image_path)
                except Exception:
                    file_hash = None
            if file_hash:
                _mark_processed(
                    db_path,
                    file_hash=file_hash,
                    file_path=image_path,
                    original_filename=original_filename,
                    paperless_doc_id=doc_id,
                    title=base_title,
                )
                debug(f"Recorded to DB: hash={file_hash[:8]}..., doc_id={doc_id}")
            else:
                debug("WARN: Skipping DB record; file hash unavailable.")
    except Exception as e:
        debug(f"WARN: Failed to record processed item in DB: {e}")

    # If doc_id is still None, poll Paperless by original_filename to backfill
    if db_path and file_hash and (doc_id is None):
        try:
            import time as _t
            pdf_base = os.path.basename(pdf_path)
            list_url = f"{base_url.rstrip('/')}/api/documents/?original_filename__iexact={_rq.utils.quote(pdf_base)}&ordering=-id&page_size=1"
            debug(f"Will poll up to 45s for doc id (0.5s interval, initial 3s delay) for original_filename='{pdf_base}'…")
            start = _t.monotonic()
            _t.sleep(3.0)  # initial delay to let Paperless register the document
            deadline = start + 45.0
            attempt = 0
            while True:
                attempt += 1
                try:
                    tr = _rq.get(list_url, headers={"Authorization": f"Token {token}", "Accept": "application/json"}, timeout=15)
                    tr.raise_for_status()
                    dj = tr.json()
                    results = dj.get("results") if isinstance(dj, dict) else None
                    if results and isinstance(results[0], dict) and isinstance(results[0].get("id"), int):
                        doc_id = results[0]["id"]
                        debug(f"Backfilled paperless doc id: {doc_id} (attempt {attempt})")
                        if _update_doc_id_for_hash is not None:
                            try:
                                _update_doc_id_for_hash(db_path, file_hash, doc_id)
                                debug("Updated DB row with resolved doc id.")
                            except Exception as e:
                                debug(f"WARN: Failed to update DB with doc id: {e}")
                        break
                except Exception as e:
                    debug(f"WARN: Poll attempt {attempt} failed: {e}")
                if _t.monotonic() >= deadline:
                    break
                _t.sleep(0.5)
            if doc_id is None:
                debug("ERROR: No doc_id resolved within 45s; DB field remains NULL for now.")
        except Exception as e:
            debug(f"WARN: Error while polling for doc id: {e}")
    return pdf_path


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="End-to-end: watch scans, OCR overlay, extract metadata, upload to Paperless")
    p.add_argument("--mode", choices=["watch", "single"], default="watch", help="Run continuously watching for scans or process a single source image")
    p.add_argument("--source", help="When --mode=single, path to a single image to process")
    p.add_argument("--watch-dir", help="Optional watch directory; else read from scan-image-path.txt")
    p.add_argument("--output-dir", default="generated_pdfs", help="Directory to write generated PDFs")
    p.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://localhost:11434"), help="Ollama base URL (no /api/chat suffix needed)")
    p.add_argument("--ollama-model", default=os.environ.get("OLLAMA_MODEL", "qwen2.5vl-receipt:latest"), help="Ollama model name")
    p.add_argument("--base-url", default=os.environ.get("PAPERLESS_BASE_URL", "http://localhost:8000"), help="Paperless base URL")
    p.add_argument("--token", help="Paperless API token (overrides env/.env)")
    p.add_argument("--insecure", action="store_true", help="Disable TLS verification when talking to Paperless")
    p.add_argument("--timeout", type=int, default=60, help="HTTP timeout seconds for upload")
    return p


def main() -> None:
    args = build_arg_parser().parse_args()

    debug("Starting main_paperless_flow.py")
    debug(f"Working dir: {os.getcwd()}")
    debug(f"Conda env: {os.environ.get('CONDA_DEFAULT_ENV')}")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    debug(f"Script dir: {script_dir}")

    token = args.token or load_token(script_dir)
    if not token:
        debug("FATAL: PAPERLESS_TOKEN missing. Provide --token or set in env/.env.")
        sys.exit(1)

    out_dir = ensure_dir(args.output_dir)
    debug(f"Output dir: {out_dir}")

    # Initialize processed DB path
    db_path: Optional[str] = None
    if _ensure_db is not None:
        try:
            db_path = _ensure_db(script_dir)
        except Exception as e:
            debug(f"WARN: Failed to initialize local DB: {e}")

    if args.mode == "single":
        src = args.source
        if not src or not os.path.isfile(src):
            debug("FATAL: Provide a valid --source for single mode.")
            sys.exit(2)
        # Pre-check against DB to avoid duplicate work
        pre_h: Optional[str] = None
        try:
            if db_path and _compute_file_hash and _is_processed:
                pre_h = _compute_file_hash(src)
                if _is_processed(db_path, pre_h):
                    debug("Already processed (per DB). Skipping single file.")
                    return
        except Exception as e:
            debug(f"WARN: Pre-hash check failed; proceeding: {e}")

        process_one_image(
            src,
            ollama_url=args.ollama_url,
            ollama_model=args.ollama_model,
            base_url=args.base_url,
            token=token,
            out_dir=out_dir,
            insecure=args.insecure,
            timeout=args.timeout,
            db_path=db_path,
            precomputed_hash=pre_h,
        )
        return

    # Watch mode
    if ScanEventListener is None:
        debug("FATAL: ScanEventListener not available; cannot run watch mode.")
        sys.exit(2)

    # Resolve watch dir similarly to the listener for initial sync
    try:
        if args.watch_dir:
            resolved_watch_dir = os.path.abspath(os.path.expanduser(os.path.expandvars(args.watch_dir)))
        else:
            if callable(_read_watch_dir_from_file):
                resolved_watch_dir = _read_watch_dir_from_file()
            else:
                debug("FATAL: Cannot resolve watch dir; helper unavailable.")
                sys.exit(2)
    except Exception as e:
        debug(f"FATAL: Failed to resolve watch dir: {e}")
        sys.exit(2)

    # Bring DB in sync with Paperless for all files currently in the watch dir
    if db_path and _initial_sync_with_paperless is not None:
        try:
            _initial_sync_with_paperless(
                db_path=db_path,
                watch_dir=resolved_watch_dir,
                base_url=args.base_url,
                token=token,
            )
        except Exception as e:
            debug(f"WARN: Initial sync failed: {e}")

    # Backlog processing: process any JPEGs in watch dir that are not yet recorded in DB
    try:
        if db_path and _compute_file_hash and _is_processed and callable(_list_jpegs):
            names = _list_jpegs(resolved_watch_dir)
            debug(f"Backlog sweep: found {len(names)} JPEG(s) present at startup")
            processed_now = 0
            skipped_existing = 0
            for name in names:
                path = os.path.abspath(os.path.join(resolved_watch_dir, name))
                try:
                    h = _compute_file_hash(path)
                except Exception as e:
                    debug(f"WARN: Failed to hash backlog file '{name}': {e}")
                    continue
                if _is_processed(db_path, h):
                    skipped_existing += 1
                    continue
                debug(f"Backlog: processing '{name}' (not in DB)")
                process_one_image(
                    path,
                    ollama_url=args.ollama_url,
                    ollama_model=args.ollama_model,
                    base_url=args.base_url,
                    token=token,
                    out_dir=out_dir,
                    insecure=args.insecure,
                    timeout=args.timeout,
                    listener=None,  # will create listener after sweep
                    db_path=db_path,
                    precomputed_hash=h,
                )
                processed_now += 1
            debug(f"Backlog sweep completed: processed={processed_now}, already_in_db={skipped_existing}")
        else:
            debug("Backlog sweep skipped (DB or helpers unavailable)")
    except Exception as e:
        debug(f"WARN: Backlog sweep failed: {e}")

    listener = ScanEventListener(
        watch_dir=resolved_watch_dir,
        print_on_detect=False,
        poll_interval_sec=1.0,
    )
    scan_debug(f"Watch mode active. Watching: {listener.watch_dir}")
    debug("Press Ctrl+C to stop watching.")
    try:
        while True:
            new_paths = listener.scan_once()
            if not new_paths:
                import time as _t
                _t.sleep(listener.poll_interval_sec)
                continue
            for image_path in new_paths:
                debug(f"Detected new image: {image_path}")
                # Skip if DB already contains this by hash
                pre_h2: Optional[str] = None
                try:
                    if db_path and _compute_file_hash and _is_processed:
                        pre_h2 = _compute_file_hash(image_path)
                        if _is_processed(db_path, pre_h2):
                            debug("Already processed (per DB). Skipping this file.")
                            continue
                except Exception as e:
                    debug(f"WARN: Hash pre-check failed: {e}")

                process_one_image(
                    image_path,
                    ollama_url=args.ollama_url,
                    ollama_model=args.ollama_model,
                    base_url=args.base_url,
                    token=token,
                    out_dir=out_dir,
                    insecure=args.insecure,
                    timeout=args.timeout,
                    listener=listener,
                    db_path=db_path,
                    precomputed_hash=pre_h2,
                )
    except KeyboardInterrupt:
        debug("Interrupted by user. Exiting watch mode.")


if __name__ == "__main__":
    main()
