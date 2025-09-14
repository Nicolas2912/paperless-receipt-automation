import os
import re
import sys
import argparse
import fitz  # PyMuPDF
from typing import List, Set, Tuple, Optional

# Shared logging (Phase 1)
try:
    from src.paperless_automation.logging import get_logger  # type: ignore
except Exception:
    def get_logger(name: str):  # type: ignore
        class _L:
            def info(self, m):
                print(f"[{name}] {m}")
            debug = info
            warning = info
            error = info
        return _L()

_LOG = get_logger("verify-v2")


def log(msg: str) -> None:
    _LOG.info(msg)

def extract_text_sample(page: fitz.Page, max_chars=200) -> Tuple[str, str]:
    txt = page.get_text("text") or ""
    return txt, txt[:max_chars].replace("\n", "\\n")

def content_xrefs_from_page(doc: fitz.Document, page: fitz.Page) -> List[int]:
    raw = doc.xref_object(page.xref, compressed=False) or ""
    # /Contents N 0 R  OR  /Contents [ N 0 R M 0 R ... ]
    single = re.findall(r"/Contents\s+(\d+)\s+0\s+R", raw)
    if single:
        return [int(n) for n in single]
    arr = re.search(r"/Contents\s*\[(.*?)\]", raw, re.S)
    if arr:
        nums = re.findall(r"(\d+)\s+0\s+R", arr.group(1))
        return [int(n) for n in nums]
    return []

def form_xobject_xrefs_from_obj(doc: fitz.Document, obj_xref: int) -> List[int]:
    """
    Given any object (page or form xobject), read its object dictionary and
    return xrefs of /XObject entries whose /Subtype /Form.
    """
    out: List[int] = []
    raw = doc.xref_object(obj_xref, compressed=False) or ""
    # Find XObject dict block if present
    xo_match = re.search(r"/XObject\s*<<(.+?)>>", raw, re.S)
    if not xo_match:
        return out
    refs = re.findall(r"(\d+)\s+0\s+R", xo_match.group(1))
    for n in refs:
        xr = int(n)
        sub = doc.xref_object(xr, compressed=False) or ""
        if "/Subtype" in sub and "/Form" in sub:
            out.append(xr)
    return out

def stream_text_ops(doc: fitz.Document, xref: int) -> Optional[str]:
    """Return decoded content stream text if available."""
    try:
        data = doc.xref_stream(xref)
        return data.decode("latin-1", errors="ignore") if data is not None else None
    except Exception:
        return None

def stream_has_invisible_text_ops(stream_text: str) -> bool:
    has_tr3 = re.search(r"\b3\s+Tr\b", stream_text) is not None
    has_text_op = (" Tj" in stream_text) or (" TJ" in stream_text)
    return has_tr3 and has_text_op

def recurse_for_tr3(doc: fitz.Document, roots: List[int]) -> Tuple[bool, Set[int]]:
    """
    DFS through content streams and nested Form XObjects.
    Returns (found_tr3, visited_xrefs).
    """
    visited: Set[int] = set()
    stack: List[int] = list(roots)
    found = False

    while stack:
        xr = stack.pop()
        if xr in visited:
            continue
        visited.add(xr)

        st = stream_text_ops(doc, xr)
        if st:
            # Heuristic check for invisible text ops
            if stream_has_invisible_text_ops(st):
                log(f"Found '3 Tr' with text operators in stream xref {xr}")
                found = True
                # We still continue to visit others for completeness
            # Descend into any nested form XObjects reachable via this object
            nested = form_xobject_xrefs_from_obj(doc, xr)
            if nested:
                log(f"Descending into form XObjects from xref {xr}: {nested}")
                stack.extend(nested)
        else:
            # Even if no stream text (e.g., compressed object without stream),
            # still try to discover nested forms via its object dictionary.
            nested = form_xobject_xrefs_from_obj(doc, xr)
            if nested:
                log(f"Object xref {xr} has form XObjects (no direct stream): {nested}")
                stack.extend(nested)

    return found, visited

def verify_pdf(pdf_path: str, page_index: Optional[int] = None) -> int:
    pdf_path = os.path.abspath(pdf_path)
    if not os.path.isfile(pdf_path):
        log(f"ERROR: file not found: {pdf_path}")
        return 2

    log(f"Opening PDF: {pdf_path}")
    doc = fitz.open(pdf_path)
    total = doc.page_count
    log(f"Pages: {total}")

    pages = range(total)
    if page_index is not None:
        if page_index < 0 or page_index >= total:
            log(f"ERROR: page index out of range 0..{total-1}")
            return 2
        pages = [page_index]

    overall_code = 0

    for i in pages:
        page = doc.load_page(i)
        log(f"--- Page {i+1} ---")
        text, sample = extract_text_sample(page)
        has_text = len(text.strip()) > 0
        log(f"Extractable text length: {len(text)}")
        log(f"Text sample (first 200 chars): {sample!r}")

        roots = content_xrefs_from_page(doc, page)
        log(f"Top-level content stream xrefs: {roots if roots else 'none'}")

        found_tr3, visited = recurse_for_tr3(doc, roots)
        log(f"Visited xrefs (streams / forms): {sorted(visited) if visited else 'none'}")
        log(f"Invisible render mode (3 Tr) detected: {found_tr3}")

        if has_text and found_tr3:
            log("RESULT: Embedded text present and marked invisible (render mode 3).")
        elif has_text and not found_tr3:
            log("RESULT: Embedded text present, but could not confirm 3 Tr.")
            log("Explanation: Text may be inside deeper objects, or invisibility uses alpha/clipping/layers.")
            overall_code = max(overall_code, 1)
        else:
            log("RESULT: No extractable text on this page.")
            overall_code = max(overall_code, 1)

    doc.close()
    return overall_code

def main():
    ap = argparse.ArgumentParser(description="Verify invisible embedded text in a PDF (recursively checks Form XObjects).")
    ap.add_argument("--pdf", required=True, help="Path to the PDF to verify")
    ap.add_argument("--page", type=int, help="1-based page number (default: all)")
    args = ap.parse_args()

    page_idx = args.page - 1 if args.page is not None else None
    sys.exit(verify_pdf(args.pdf, page_idx))

if __name__ == "__main__":
    main()
