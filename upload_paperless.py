import argparse
import os
import sys
import json
import mimetypes
from typing import Optional, Dict, Any, List, Tuple
import requests
import re
import time

# Shared logging & paths (Phase 1)
try:
    from src.paperless_automation.paths import fix_windows_path_input as _fix_input  # type: ignore
except Exception:
    def _fix_input(p: str) -> str:  # type: ignore
        return p

try:
    from extract_metadata import extract_from_source, ExtractedMetadata  # type: ignore
except Exception as e:
    extract_from_source = None  # type: ignore
    ExtractedMetadata = None  # type: ignore
    def _noop_debug_import(msg: str) -> None:
        print(f"[paperless-uploader] [WARN] extract_metadata not importable: {e}")

# Tag/merchant normalization utilities
try:
    from merchant_normalization import (
        normalize_korrespondent as _normalize_korrespondent,  # type: ignore
        resolve_tag_and_key as _resolve_tag_and_key,           # type: ignore
    )
except Exception:
    def _normalize_korrespondent(name: str) -> str:  # type: ignore
        return (name or "").lower()
    def _choose_tag(tag_map, name):  # type: ignore
        return "NO TAG FOUND"


# New shared foundations (Phase 1)
try:
    from src.paperless_automation.logging import get_logger  # type: ignore
    from src.paperless_automation.config import load_token as _cfg_load_token, load_tag_map as _cfg_load_tag_map  # type: ignore
except Exception:
    def get_logger(name: str):  # type: ignore
        class _L:
            def info(self, m):
                print(f"[{name}] {m}", flush=True)
            debug = info
            warning = info
            error = info
        return _L()
    def _cfg_load_token(dotenv_dir: str):  # type: ignore
        return None
    def _cfg_load_tag_map(script_dir: str):  # type: ignore
        return {}

_LOG = get_logger("paperless-uploader")


def debug(msg: str) -> None:
    _LOG.info(msg)


def build_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    return f"{base}/api/documents/post_document/"


def guess_mime(path: str) -> str:
    mt, _ = mimetypes.guess_type(path)
    return mt or "application/octet-stream"


def _fix_windows_path_input(p: str) -> str:
    # Delegate to centralized helper for consistency
    return _fix_input(p)



def _load_token_from_env_or_dotenv(dotenv_path: str) -> Optional[str]:
    # Delegate to centralized config; keep function name for compatibility.
    script_dir = os.path.dirname(os.path.abspath(dotenv_path)) or os.getcwd()
    return _cfg_load_token(script_dir)


# -------------- Paperless helper API --------------

def _auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Token {token}", "Accept": "application/json"}

def _api_patch_document(base_url: str, token: str, doc_id: int, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        from src.paperless_automation.paperless.client import PaperlessClient  # type: ignore
        client = PaperlessClient(base_url, token)
        return client.patch_document(doc_id, payload)
    except Exception as e:
        debug(f"ERROR patching document {doc_id}: {e}")
        return None


def _api_find_task_by_uuid(base_url: str, token: str, task_uuid: str) -> Optional[Dict[str, Any]]:
    try:
        from src.paperless_automation.paperless.client import PaperlessClient  # type: ignore
        client = PaperlessClient(base_url, token)
        return client.find_task_by_uuid(task_uuid)
    except Exception as e:
        debug(f"WARN: Failed to find task by UUID: {e}")
        return None


def _api_find_document_by_title(base_url: str, token: str, title: str) -> Optional[int]:
    try:
        from src.paperless_automation.paperless.client import PaperlessClient  # type: ignore
        client = PaperlessClient(base_url, token)
        return client.find_document_by_title(title)
    except Exception as e:
        debug(f"WARN: Could not search document by title: {e}")
        return None


def _api_get_document(base_url: str, token: str, doc_id: int) -> Optional[Dict[str, Any]]:
    try:
        from src.paperless_automation.paperless.client import PaperlessClient  # type: ignore
        client = PaperlessClient(base_url, token)
        return client.get_document(doc_id)
    except Exception as e:
        debug(f"WARN: Failed to GET document {doc_id}: {e}")
        return None


# ASN/AS handling removed: no title munging required.


def _api_get_first_by_name(base_url: str, token: str, resource: str, name: str) -> Optional[Dict[str, Any]]:
    # Kept for backward compatibility; now routes through client ensure calls
    try:
        from src.paperless_automation.paperless.client import PaperlessClient  # type: ignore
        client = PaperlessClient(base_url, token)
        if resource == "correspondents":
            cid = client.ensure_correspondent(name)
            return {"id": cid} if isinstance(cid, int) else None
        if resource == "document_types":
            did = client.ensure_document_type(name)
            return {"id": did} if isinstance(did, int) else None
        if resource == "tags":
            ids = client.ensure_tags([name])
            return {"id": ids[0]} if ids else None
    except Exception:
        pass
    return None


def _api_create_resource(base_url: str, token: str, resource: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    base = base_url.rstrip("/")
    url = f"{base}/api/{resource}/"
    try:
        r = requests.post(url, headers=_auth_headers(token) | {"Content-Type": "application/json"}, json=payload, timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        debug(f"ERROR creating {resource}: {e}")
        return None


def ensure_correspondent_id(base_url: str, token: str, name: str) -> Optional[int]:
    try:
        from src.paperless_automation.paperless.client import PaperlessClient  # type: ignore
        client = PaperlessClient(base_url, token)
        return client.ensure_correspondent(name)
    except Exception:
        return None


def ensure_tag_ids(base_url: str, token: str, names: List[str]) -> List[int]:
    try:
        from src.paperless_automation.paperless.client import PaperlessClient  # type: ignore
        client = PaperlessClient(base_url, token)
        return client.ensure_tags(names)
    except Exception:
        return []


def ensure_document_type_id(base_url: str, token: str, name: str) -> Optional[int]:
    try:
        from src.paperless_automation.paperless.client import PaperlessClient  # type: ignore
        client = PaperlessClient(base_url, token)
        return client.ensure_document_type(name)
    except Exception:
        return None


"""Serial number logic removed by request."""

# Removed task polling by request; rely on Paperless async processing without client-side polling.


def upload_document(
    file_path: str,
    base_url: str,
    token: Optional[str] = None,
    title: Optional[str] = None,
    created: Optional[str] = None,
    correspondent_id: Optional[int] = None,
    tag_ids: Optional[List[int]] = None,
    document_type_id: Optional[int] = None,
    timeout: int = 60,
    verify_tls: bool = True,
) -> Dict:
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Input file not found: {file_path}")
    if not token:
        raise ValueError("API token missing. Provide --token or set PAPERLESS_TOKEN.")

    size = os.path.getsize(file_path)
    mime = guess_mime(file_path)
    debug(f"Target: {build_endpoint(base_url)}")
    debug(f"File: {file_path} ({size} bytes, mime={mime})")
    debug(f"Title: {title!r}, Created: {created!r}")
    debug("Sending request via PaperlessClient...")

    try:
        from src.paperless_automation.paperless.client import PaperlessClient  # type: ignore
        client = PaperlessClient(base_url, token, timeout=timeout, verify_tls=verify_tls)
        res = client.post_document(
            file_path=file_path,
            title=title,
            created=created,
            correspondent_id=correspondent_id,
            tag_ids=tag_ids,
            document_type_id=document_type_id,
        )
        status = int(res.get("status_code", 0))
        debug(f"HTTP {status}")
        return {"status_code": status, "json": res.get("json")}
    except Exception as e:
        debug(f"HTTP error while uploading: {e}")
        raise


def main():
    p = argparse.ArgumentParser(description="Upload a document to Paperlessâ€‘NGX via API.")
    p.add_argument("--file", required=True, help="Path to the PDF (or image) to upload")
    p.add_argument("--base-url", default="http://localhost:8000",
                    help="Paperless base URL, e.g., http://localhost:8000")
    p.add_argument("--token", default=None,
                    help="API token (overrides env/.env). If omitted, loads PAPERLESS_TOKEN from environment or from a .env next to this script.")
    p.add_argument("--no-extract", action="store_true", help="Disable LLM metadata extraction step")
    p.add_argument("--metadata", default=None,
                    help="Optional: metadata JSON (inline or @path/to/file.json). When provided, overrides extraction.")
    p.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://localhost:11434"), help="Ollama base URL")
    p.add_argument("--ollama-model", default=os.environ.get("OLLAMA_MODEL", "qwen2.5vl-receipt:latest"), help="Ollama model name")
    p.add_argument("--title", help="Optional title to set on the document")
    p.add_argument("--created", help="Optional created date (YYYY-MM-DD or ISO8601)")
    p.add_argument("--insecure", action="store_true",
                    help="Disable TLS verification (use only with HTTPS test setups)")
    p.add_argument("--timeout", type=int, default=60, help="HTTP timeout seconds")
    args = p.parse_args()

    debug("Starting upload_paperless.py")
    debug(f"Working directory: {os.getcwd()}")
    debug(f"Conda env (if any): {os.environ.get('CONDA_DEFAULT_ENV')}")

    # Resolve .env path relative to this script, as requested
    script_dir = os.path.dirname(os.path.abspath(__file__))
    dotenv_path = os.path.join(script_dir, ".env")
    debug(f"Script directory: {script_dir}")
    debug(f".env path (same dir as script): {dotenv_path}")

    token = args.token or _load_token_from_env_or_dotenv(dotenv_path)
    if not token:
        debug("FATAL: API token missing. Provide --token, set PAPERLESS_TOKEN, or add PAPERLESS_TOKEN to .env.")
        sys.exit(1)

    # Do not print the token. Show a safe source indicator.
    debug_source = "--token arg" if args.token else ("env/.env" if os.environ.get("PAPERLESS_TOKEN") or os.path.isfile(dotenv_path) else "<unknown>")
    debug(f"Token source: {debug_source}")

    # Load tag mapping
    tag_map: Dict[str, str] = _cfg_load_tag_map(script_dir)

    # Normalize possibly broken Windows path inputs early (spaces, C:Users...)
    try:
        args.file = _fix_windows_path_input(args.file)
    except Exception:
        pass

    # Load or prepare metadata
    md = None
    md_json: Optional[Dict[str, Any]] = None

    # Prefer explicit JSON metadata when provided
    if args.metadata:
        meta_arg = args.metadata.strip()
        try:
            if meta_arg.startswith("@"):
                meta_path = meta_arg[1:]
                debug(f"Loading metadata JSON from file: {meta_path}")
                with open(meta_path, "r", encoding="utf-8") as f:
                    md_json = json.load(f)
            else:
                debug("Parsing inline metadata JSON from --metadata")
                md_json = json.loads(meta_arg)
        except Exception as e:
            debug(f"ERROR: Failed to load/parse --metadata JSON: {e}")
            md_json = None

    if md_json is None and not args.no_extract and extract_from_source is not None:
        debug("Running metadata extraction step via qwen2.5vl-receipt")
        try:
            md = extract_from_source(args.file, ollama_url=args.ollama_url, model=args.ollama_model)
        except Exception as e:
            debug(f"Extraction error: {e}")
            md = None
    elif md_json is None:
        debug("Skipping extraction step (disabled/unavailable and no --metadata provided)")

    # Derive fields
    title = args.title
    created = args.created
    correspondent_id = None
    tag_ids: List[int] = []
    document_type_id = None

    # Fill fields from metadata (simplified, always defer title formatting to ExtractedMetadata)
    if md_json is not None:
        debug("Using provided metadata JSON to set fields.")
        try:
            # Build ExtractedMetadata from JSON when possible
            if ExtractedMetadata is not None:
                kor_raw = str(md_json.get("korrespondent") or md_json.get("merchant") or "Unbekannt")
                kor_clean = _normalize_korrespondent(kor_raw)
                md = ExtractedMetadata(
                    korrespondent=kor_clean,
                    ausstellungsdatum=str(md_json.get("ausstellungsdatum") or "1970-01-01"),
                    betrag_value=str(md_json.get("betrag_value") or "0.00"),
                    betrag_currency=str(md_json.get("betrag_currency") or "EUR").upper(),
                )

            # Resolve canonical merchant via tag_map key first
            canonical_kor = None
            if md is not None:
                tag_name, matched_key = _resolve_tag_and_key(tag_map if isinstance(tag_map, dict) else {}, md.korrespondent)
                if matched_key:
                    debug(f"Canonical merchant from tag key: '{matched_key}' (was '{md.korrespondent}')")
                    md.korrespondent = matched_key
                    canonical_kor = matched_key
                # Ensure tags from chosen mapping
                if tag_name:
                    tag_ids = ensure_tag_ids(args.base_url, token, [tag_name])
                    debug(f"Selected tag '{tag_name}' -> ids={tag_ids}")

            # Created date
            created = created or (md.ausstellungsdatum if md else None) or md_json.get("ausstellungsdatum")

            # Document type
            doc_type = (md.dokumenttyp if md else (md_json.get("dokumenttyp") or "Kassenbon")).strip() or "Kassenbon"
            document_type_id = ensure_document_type_id(args.base_url, token, doc_type)
            if document_type_id:
                debug(f"Using document type '{doc_type}' (id={document_type_id}) from metadata JSON")

            # Correspondent: use canonical_kor (tag key) when available
            kor = canonical_kor or (md.korrespondent if md else _normalize_korrespondent(str(md_json.get("korrespondent") or md_json.get("merchant") or "")).strip())
            if kor:
                correspondent_id = ensure_correspondent_id(args.base_url, token, kor)
                if correspondent_id:
                    debug(f"Using correspondent '{kor}' (id={correspondent_id}) from metadata JSON")
                else:
                    debug(f"WARN: Could not resolve/create correspondent for '{kor}' from metadata JSON")

            # Title: build from md (which now carries canonical korrespondent) if not explicitly provided by CLI
            if md is not None and not title:
                title = md.title()
                debug(f"Constructed title from metadata JSON: {title}")
        except Exception as e:
            debug(f"WARN: Failed applying metadata JSON: {e}")

    elif md is not None:
        # Resolve canonical merchant via tag_map first and use it everywhere
        tag_name, matched_key = _resolve_tag_and_key(tag_map if isinstance(tag_map, dict) else {}, md.korrespondent)
        if matched_key:
            debug(f"Canonical merchant from tag key: '{matched_key}' (was '{md.korrespondent}')")
            md.korrespondent = matched_key
        if tag_name:
            tag_ids = ensure_tag_ids(args.base_url, token, [tag_name])
            debug(f"Selected tag '{tag_name}' -> ids={tag_ids}")

        # Ensure correspondent and document type after canonicalization
        correspondent_id = ensure_correspondent_id(args.base_url, token, md.korrespondent)
        if correspondent_id:
            debug(f"Using correspondent '{md.korrespondent}' (id={correspondent_id})")
        else:
            debug(f"WARN: Could not resolve/create correspondent for '{md.korrespondent}'.")

        document_type_id = ensure_document_type_id(args.base_url, token, md.dokumenttyp)
        if document_type_id:
            debug(f"Using document type '{md.dokumenttyp}' (id={document_type_id})")

        # Title + created (no ASN)
        created = created or md.ausstellungsdatum
        title = title or md.title()
        debug(f"Final title: {title}")

    # No ASN-related title handling required.

    try:
        result = upload_document(
            file_path=args.file,
            base_url=args.base_url,
            token=token,
            title=title,
            created=created,
            correspondent_id=correspondent_id,
            tag_ids=tag_ids,
            document_type_id=document_type_id,
            timeout=args.timeout,
            verify_tls=not args.insecure,
        )
    except Exception as e:
        debug(f"FATAL: {e}")
        sys.exit(1)

    # After upload, enforce only the mapped tag (avoid duplicate default tags)
    try:
        desired_tag_ids = tag_ids or []
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
                doc_id = None
            # If task uuid present, try to fetch it (best-effort, no long polling here)
            if not doc_id:
                try:
                    task_uuid = None
                    if isinstance(rj.get("task"), dict) and isinstance(rj["task"].get("task_id"), str):
                        task_uuid = rj["task"]["task_id"]
                    elif isinstance(rj.get("task_id"), str):
                        task_uuid = rj["task_id"]
                    if task_uuid:
                        t = _api_find_task_by_uuid(args.base_url, token, task_uuid)
                        if isinstance(t, dict):
                            # Try to extract document id from task payload
                            res = t.get("result") if isinstance(t.get("result"), dict) else None
                            if isinstance(res, dict):
                                if isinstance(res.get("document"), dict) and isinstance(res["document"].get("id"), int):
                                    doc_id = res["document"]["id"]
                                elif isinstance(res.get("document_id"), int):
                                    doc_id = res["document_id"]
                except Exception:
                    pass
        # Fallback: try by exact title if still unknown
        if not doc_id and isinstance(title, str) and title:
            did = _api_find_document_by_title(args.base_url, token, title)
            if isinstance(did, int):
                doc_id = did
                debug(f"Resolved document id by title: {doc_id}")

        # Enforce exact tags if we have both doc id and mapped tags
        if isinstance(doc_id, int) and isinstance(desired_tag_ids, list) and desired_tag_ids:
            # Deduplicate order-preserving
            seen = set()
            dedup: List[int] = []
            for t in desired_tag_ids:
                if isinstance(t, int) and t not in seen:
                    seen.add(t)
                    dedup.append(t)
            debug(f"Patching tags on document {doc_id} to {dedup}")
            try:
                _api_patch_document(args.base_url, token, doc_id, {"tags": dedup})
                debug("Tags patched successfully to exact set from tag_map.")
            except Exception as e:
                debug(f"WARN: Failed to patch tags on document {doc_id}: {e}")
        else:
            if not desired_tag_ids:
                debug("No mapped tags to enforce; skipping tag patch.")
            if not isinstance(doc_id, int):
                debug("Could not resolve document id to patch tags; skipping.")
    except Exception as e:
        debug(f"WARN: Exception during post-upload tag enforcement: {e}")

    debug("Upload finished. Final response payload below:")
    try:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except Exception:
        print(result)

    # ASN/AS post-upload logic removed.


if __name__ == "__main__":
    from src.paperless_automation.cli.main import main as cli_main

    cli_main(["upload", *sys.argv[1:]])
