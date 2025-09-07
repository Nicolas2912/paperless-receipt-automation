import argparse
import os
import sys
import json
import mimetypes
from typing import Optional, Dict, Any, List, Tuple
import requests
import re
import time

try:
    from extract_metadata import extract_from_source, ExtractedMetadata  # type: ignore
except Exception as e:
    extract_from_source = None  # type: ignore
    ExtractedMetadata = None  # type: ignore
    def _noop_debug_import(msg: str) -> None:
        print(f"[paperless-uploader] [WARN] extract_metadata not importable: {e}")

# Try to reuse the same correspondent normalizer as extractor
try:
    from extract_metadata import _normalize_korrespondent  # type: ignore
except Exception:
    def _normalize_korrespondent(name: str) -> str:  # type: ignore
        return (name or "").strip()


def debug(msg: str) -> None:
    print(f"[paperless-uploader] {msg}", flush=True)


def build_endpoint(base_url: str) -> str:
    base = base_url.rstrip("/")
    return f"{base}/api/documents/post_document/"


def guess_mime(path: str) -> str:
    mt, _ = mimetypes.guess_type(path)
    return mt or "application/octet-stream"


def _fix_windows_path_input(p: str) -> str:
    """Best-effort repair for common Windows path paste issues.

    - If user passes "C:Users..." (missing backslash after drive), insert it.
    - Trim surrounding quotes/spaces.
    """
    try:
        s = (p or "").strip().strip('"').strip("'")
        if os.name == "nt":
            if re.match(r"^[A-Za-z]:(?![\\/])", s):
                fixed = s[:2] + "\\" + s[2:]
                if fixed != s:
                    debug(f"Repaired Windows path input: '{s}' -> '{fixed}'")
                s = fixed
        return s
    except Exception:
        return p



def _load_token_from_env_or_dotenv(dotenv_path: str) -> Optional[str]:
    """Load PAPERLESS_TOKEN from the environment or a .env file next to this script.

    - Only reads PAPERLESS_TOKEN.
    - Ignores blank lines and comment lines starting with # or ;
    - Trims surrounding single/double quotes around the value.
    - Emits debug prints without leaking the actual token.
    """
    tok = os.environ.get("PAPERLESS_TOKEN")
    if tok:
        debug("Using PAPERLESS_TOKEN from environment.")
        return tok.strip()

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

    debug("PAPERLESS_TOKEN not found in .env")
    return None


# -------------- Paperless helper API --------------

def _auth_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Token {token}", "Accept": "application/json"}

def _api_patch_document(base_url: str, token: str, doc_id: int, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    url = f"{base_url.rstrip('/')}/api/documents/{doc_id}/"
    try:
        r = requests.patch(
            url,
            headers=_auth_headers(token) | {"Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        debug(f"ERROR patching document {doc_id}: {e}")
        try:
            if 'r' in locals() and hasattr(r, 'text'):
                preview = r.text
                preview = (preview[:500] + "...") if len(preview) > 500 else preview
                debug(f"PATCH response body preview: {preview}")
        except Exception:
            pass
        return None


def _api_get_first_by_name(base_url: str, token: str, resource: str, name: str) -> Optional[Dict[str, Any]]:
    # Try iexact first, then fallback to icontains
    base = base_url.rstrip("/")
    for param in (f"name__iexact={requests.utils.quote(name)}", f"name__icontains={requests.utils.quote(name)}"):
        url = f"{base}/api/{resource}/?{param}&page_size=1"
        try:
            r = requests.get(url, headers=_auth_headers(token), timeout=30)
            r.raise_for_status()
            data = r.json()
            results = data.get("results") if isinstance(data, dict) else None
            if results:
                return results[0]
        except Exception:
            continue
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
    if not name:
        return None
    found = _api_get_first_by_name(base_url, token, "correspondents", name)
    if found:
        return found.get("id")
    created = _api_create_resource(base_url, token, "correspondents", {"name": name})
    return created.get("id") if created else None


def ensure_tag_ids(base_url: str, token: str, names: List[str]) -> List[int]:
    ids: List[int] = []
    for name in names:
        if not name:
            continue
        found = _api_get_first_by_name(base_url, token, "tags", name)
        if found:
            ids.append(found.get("id"))
            continue
        created = _api_create_resource(base_url, token, "tags", {"name": name})
        if created and "id" in created:
            ids.append(created["id"])
    return [i for i in ids if isinstance(i, int)]


def ensure_document_type_id(base_url: str, token: str, name: str) -> Optional[int]:
    found = _api_get_first_by_name(base_url, token, "document_types", name)
    if found:
        return found.get("id")
    created = _api_create_resource(base_url, token, "document_types", {"name": name})
    return created.get("id") if created else None


ASN_PATTERN = re.compile(r"AS:\s*(\d+)")


def get_next_archive_serial_number(base_url: str, token: str, *, max_pages: int = 5, page_size: int = 100) -> int:
    """Return the next Archive Serial Number (ASN).

    Prefer the Paperless `archive_serial_number` field if present, otherwise
    fall back to scanning titles for the pattern "AS: <int>". Iterates up to
    `max_pages` pages to find the current maximum and returns +1.
    """
    base = base_url.rstrip("/")
    url = f"{base}/api/documents/?ordering=-id&page_size={page_size}"
    max_found = 0
    pages = 0
    debug("Determined next ASN: scanning API for current maximum …")
    while url and pages < max_pages:
        try:
            r = requests.get(url, headers=_auth_headers(token), timeout=30)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            debug(f"WARN: Could not list documents to determine ASN: {e}")
            break
        results = data.get("results") if isinstance(data, dict) else None
        if not results:
            break
        for doc in results:
            if not isinstance(doc, dict):
                continue
            # 1) Prefer explicit API field
            as_field = doc.get("archive_serial_number")
            try:
                if isinstance(as_field, int) and as_field > 0:
                    if as_field > max_found:
                        max_found = as_field
                    continue
            except Exception:
                pass
            # 2) Fallback to title scan (backwards compatibility)
            title = doc.get("title")
            if isinstance(title, str):
                m = ASN_PATTERN.search(title)
                if m:
                    try:
                        num = int(m.group(1))
                        if num > max_found:
                            max_found = num
                    except Exception:
                        pass
        url = data.get("next") if isinstance(data, dict) else None
        pages += 1
    next_asn = max_found + 1 if max_found > 0 else 1
    debug(f"Determined next ASN candidate: {next_asn} (max found: {max_found})")
    return next_asn

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
    archive_serial_number: Optional[int] = None,
    timeout: int = 60,
    verify_tls: bool = True,
) -> Dict:
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"Input file not found: {file_path}")
    if not token:
        raise ValueError("API token missing. Provide --token or set PAPERLESS_TOKEN.")

    endpoint = build_endpoint(base_url)
    size = os.path.getsize(file_path)
    mime = guess_mime(file_path)

    debug(f"Target: {endpoint}")
    debug(f"File: {file_path} ({size} bytes, mime={mime})")
    debug(f"Title: {title!r}, Created: {created!r}")
    debug("Sending request...")

    headers = _auth_headers(token)
    # Use a list of tuples to allow repeated 'tags' fields
    data_list: List[Tuple[str, str]] = []
    if title:
        data_list.append(("title", title))
    if created:
        data_list.append(("created", created))
    if correspondent_id is not None:
        data_list.append(("correspondent", str(correspondent_id)))
    if document_type_id is not None:
        data_list.append(("document_type", str(document_type_id)))
    if tag_ids:
        for tid in tag_ids:
            data_list.append(("tags", str(tid)))
    # Avoid sending ASN in POST to prevent collisions under concurrent uploads.
    if archive_serial_number is not None:
        debug("Note: archive_serial_number provided but will NOT be included in POST. Will PATCH after upload.")

    with open(file_path, "rb") as f:
        files = {"document": (os.path.basename(file_path), f, mime)}
        try:
            resp = requests.post(
                endpoint, headers=headers, data=data_list, files=files,
                timeout=timeout, verify=verify_tls
            )
        except requests.RequestException as e:
            debug(f"HTTP error while uploading: {e}")
            raise

    debug(f"HTTP {resp.status_code}")
    ct = resp.headers.get("Content-Type", "")
    text = resp.text

    # Try JSON first if Content-Type hints at JSON, otherwise heuristically.
    is_json_ct = "json" in ct.lower()
    payload = None
    if is_json_ct:
        try:
            payload = resp.json()
        except Exception as e:
            debug(f"JSON parse failed despite JSON Content-Type: {e}; falling back to text.")

    if payload is None and text:
        t = text.lstrip()
        if t[:1] in ("{", "[", '"'):
            try:
                payload = json.loads(text)
                debug("Parsed JSON from response body by heuristic.")
            except Exception as e:
                debug(f"Heuristic JSON parse failed: {e}")

    if payload is not None:
        # Log payload shape without assuming dict
        if isinstance(payload, dict):
            debug(f"Response JSON keys: {list(payload.keys())}")
        elif isinstance(payload, list):
            debug(f"Response JSON is a list with {len(payload)} items")
        else:
            preview = str(payload)
            preview = (preview[:200] + "...") if len(preview) > 200 else preview
            debug(f"Response JSON type={type(payload).__name__}; preview: {preview}")

        if resp.ok or resp.status_code in (200, 201, 202, 204):
            debug("Upload accepted by Paperless-NGX.")
        else:
            debug(f"Upload not OK (status {resp.status_code}); returning payload for inspection.")
        return {"status_code": resp.status_code, "json": payload}

    # Non-JSON path
    if resp.ok:
        debug("Non-JSON response with HTTP OK; returning raw text for inspection.")
        return {"status_code": resp.status_code, "raw": text}
    else:
        debug("Non-JSON response and not OK; raising for status.")
        resp.raise_for_status()
        return {"status_code": resp.status_code, "raw": text}


def main():
    p = argparse.ArgumentParser(description="Upload a document to Paperless‑NGX via API.")
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
    tag_map_path = os.path.join(script_dir, "tag_map.json")
    tag_map: Dict[str, str] = {}
    try:
        if os.path.isfile(tag_map_path):
            with open(tag_map_path, "r", encoding="utf-8") as f:
                tag_map = json.load(f)
            debug(f"Loaded tag map: {len(tag_map)} entries from {tag_map_path}")
        else:
            debug("No tag_map.json found; tags will be empty unless provided manually.")
    except Exception as e:
        debug(f"WARN: Failed to read tag_map.json: {e}")

    # Normalize possibly broken Windows path inputs early (spaces, C:Users...)
    try:
        args.file = _fix_windows_path_input(args.file)
    except Exception:
        pass

    # Load or prepare metadata
    md = None
    md_json: Optional[Dict[str, Any]] = None
    final_asn_used: Optional[int] = None

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
            # Created date
            created = created or (md.ausstellungsdatum if md else None) or md_json.get("ausstellungsdatum")
            # Correspondent and document type
            kor = (md.korrespondent if md else _normalize_korrespondent(str(md_json.get("korrespondent") or md_json.get("merchant") or "")).strip())
            if kor:
                correspondent_id = ensure_correspondent_id(args.base_url, token, kor)
                if correspondent_id:
                    debug(f"Using correspondent '{kor}' (id={correspondent_id}) from metadata JSON")
                else:
                    debug(f"WARN: Could not resolve/create correspondent for '{kor}' from metadata JSON")
            doc_type = (md.dokumenttyp if md else (md_json.get("dokumenttyp") or "Kassenbon")).strip() or "Kassenbon"
            document_type_id = ensure_document_type_id(args.base_url, token, doc_type)
            if document_type_id:
                debug(f"Using document type '{doc_type}' (id={document_type_id}) from metadata JSON")
            # Tags via tag_map using korrespondent
            tag_key = kor.lower() if kor else None
            if tag_key and isinstance(tag_map, dict):
                tag_name = tag_map.get(tag_key)
                if tag_name:
                    tag_ids = ensure_tag_ids(args.base_url, token, [tag_name])
                    debug(f"Mapped tag '{tag_name}' -> ids={tag_ids} (from metadata JSON)")
            # ASN
            md_asn = md_json.get("asn") if isinstance(md_json, dict) else None
            if isinstance(md_asn, int) and md_asn > 0:
                final_asn_used = md_asn
                debug(f"Using ASN from metadata JSON: {final_asn_used}")
            if not isinstance(final_asn_used, int):
                try:
                    final_asn_used = get_next_archive_serial_number(args.base_url, token)
                except Exception as e2:
                    debug(f"WARN: Failed to determine next ASN, defaulting to 1: {e2}")
                    final_asn_used = 1
            # Title: build from md if not explicitly provided by CLI
            if md is not None and not title:
                title = md.title(final_asn_used)
                debug(f"Constructed title from metadata JSON: {title}")
        except Exception as e:
            debug(f"WARN: Failed applying metadata JSON: {e}")

    elif md is not None:
        # Ensure correspondent
        correspondent_id = ensure_correspondent_id(args.base_url, token, md.korrespondent)
        if correspondent_id:
            debug(f"Using correspondent '{md.korrespondent}' (id={correspondent_id})")
        else:
            debug(f"WARN: Could not resolve/create correspondent for '{md.korrespondent}'.")

        # Ensure document type 'Kassenbon'
        document_type_id = ensure_document_type_id(args.base_url, token, md.dokumenttyp)
        if document_type_id:
            debug(f"Using document type '{md.dokumenttyp}' (id={document_type_id})")

        # Map tags from korrespondent via tag_map (case-insensitive)
        tag_name = tag_map.get(md.korrespondent.lower()) if isinstance(tag_map, dict) else None
        if tag_name:
            tag_ids = ensure_tag_ids(args.base_url, token, [tag_name])
            debug(f"Mapped tag '{tag_name}' -> ids={tag_ids}")

        # Title + created with sequential ASN
        created = created or md.ausstellungsdatum
        try:
            final_asn_used = get_next_archive_serial_number(args.base_url, token)
        except Exception as e:
            debug(f"WARN: Failed to determine next ASN, defaulting to 1: {e}")
            final_asn_used = 1

        # Use md.title(asn) to keep consistency with extractor and required format
        title = title or md.title(final_asn_used)
        debug(f"Final title (from ExtractedMetadata): {title}")

    # If title already contains an ASN, remember it for consistency
    if title:
        _m = ASN_PATTERN.search(title)
        if _m:
            try:
                title_asn_val = int(_m.group(1))
                if final_asn_used is None:
                    final_asn_used = title_asn_val
                    debug(f"Detected ASN from provided title: {final_asn_used}")
                elif final_asn_used != title_asn_val:
                    debug(f"WARN: ASN mismatch between computed ({final_asn_used}) and title ({title_asn_val}). Will apply {final_asn_used} to Paperless.")
            except Exception:
                pass

    # Do not append provisional ASN here; we will patch the title after upload with the actually assigned ASN

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
            archive_serial_number=final_asn_used,
            timeout=args.timeout,
            verify_tls=not args.insecure,
        )
    except Exception as e:
        debug(f"FATAL: {e}")
        sys.exit(1)

    debug("Upload finished. Final response payload below:")
    try:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except Exception:
        print(result)

    # Attempt to extract created document ID and set archive_serial_number via PATCH
    doc_id: Optional[int] = None
    rj = result.get("json") if isinstance(result, dict) else None
    if isinstance(rj, dict):
        try:
            if isinstance(rj.get("id"), int):
                doc_id = rj["id"]
            elif isinstance(rj.get("document"), dict) and isinstance(rj["document"].get("id"), int):
                doc_id = rj["document"]["id"]
            elif isinstance(rj.get("results"), list) and rj["results"]:
                first = rj["results"][0]
                if isinstance(first, dict) and isinstance(first.get("id"), int):
                    doc_id = first["id"]
        except Exception:
            pass

    if doc_id:
        assigned_asn: Optional[int] = None
        max_attempts = 7
        for attempt in range(1, max_attempts + 1):
            try:
                fresh_asn = get_next_archive_serial_number(args.base_url, token)
            except Exception as e:
                debug(f"WARN: Could not compute next ASN on attempt {attempt}: {e}")
                break
            debug(f"Attempt {attempt}/{max_attempts}: Patching doc {doc_id} with ASN={fresh_asn}")
            patched = _api_patch_document(args.base_url, token, doc_id, {"archive_serial_number": fresh_asn})
            if patched and isinstance(patched, dict) and isinstance(patched.get("archive_serial_number"), int):
                assigned_asn = patched.get("archive_serial_number")
                debug(f"ASN set on document {doc_id}: {assigned_asn}")
                break
            else:
                # Exponential backoff to let previous uploads finalize their ASN
                sleep_s = min(0.25 * (2 ** (attempt - 1)), 3.0)
                debug(f"WARN: ASN patch failed; possibly due to collision. Sleeping {sleep_s:.2f}s before retry…")
                time.sleep(sleep_s)
        if not assigned_asn:
            debug("WARN: Could not set archive_serial_number after retries. Document remains without official ASN.")
        else:
            # Always align the title to reflect the assigned ASN (append if not present)
            try:
                if title and isinstance(assigned_asn, int):
                    current_title = title
                    m = ASN_PATTERN.search(current_title)
                    if m:
                        new_title = ASN_PATTERN.sub(f"AS: {assigned_asn}", current_title)
                    else:
                        new_title = f"{current_title} - AS: {assigned_asn}"
                    if new_title != current_title:
                        debug(f"Updating title to reflect assigned ASN: {new_title}")
                        _ = _api_patch_document(args.base_url, token, doc_id, {"title": new_title})
            except Exception as e:
                debug(f"WARN: Failed to synchronize title with assigned ASN: {e}")
    else:
        if not doc_id:
            debug("WARN: Could not determine document ID from upload response; cannot set archive_serial_number.")


if __name__ == "__main__":
    main()
