import os
import re
from typing import List, Optional, Set, Tuple

import fitz  # PyMuPDF

from ..logging import get_logger

LOG = get_logger("verify")


def _log(msg: str) -> None:
    LOG.info(msg)


def extract_text_sample(page: fitz.Page, max_chars=200) -> Tuple[str, str]:
    txt = page.get_text("text") or ""
    return txt, txt[:max_chars].replace("\n", "\\n")


def content_xrefs_from_page(doc: fitz.Document, page: fitz.Page) -> List[int]:
    raw = doc.xref_object(page.xref, compressed=False) or ""
    single = re.findall(r"/Contents\s+(\d+)\s+0\s+R", raw)
    if single:
        return [int(n) for n in single]
    arr = re.search(r"/Contents\s*\[(.*?)\]", raw, re.S)
    if arr:
        nums = re.findall(r"(\d+)\s+0\s+R", arr.group(1))
        return [int(n) for n in nums]
    return []


def form_xobject_xrefs_from_obj(doc: fitz.Document, obj_xref: int) -> List[int]:
    out: List[int] = []
    raw = doc.xref_object(obj_xref, compressed=False) or ""
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
            if stream_has_invisible_text_ops(st):
                _log(f"Found '3 Tr' with text operators in stream xref {xr}")
                found = True
            nested = form_xobject_xrefs_from_obj(doc, xr)
            if nested:
                _log(f"Descending into form XObjects from xref {xr}: {nested}")
                stack.extend(nested)
        else:
            nested = form_xobject_xrefs_from_obj(doc, xr)
            if nested:
                _log(f"Object xref {xr} has form XObjects (no direct stream): {nested}")
                stack.extend(nested)
    return found, visited


def verify_pdf(pdf_path: str, page_index: Optional[int] = None) -> int:
    pdf_path = os.path.abspath(pdf_path)
    if not os.path.isfile(pdf_path):
        _log(f"ERROR: file not found: {pdf_path}")
        return 2
    _log(f"Opening PDF: {pdf_path}")
    doc = fitz.open(pdf_path)
    total = doc.page_count
    _log(f"Pages: {total}")
    pages = range(total)
    if page_index is not None:
        if page_index < 0 or page_index >= total:
            _log(f"ERROR: page index out of range 0..{total-1}")
            return 2
        pages = [page_index]

    overall_code = 0
    for i in pages:
        page = doc.load_page(i)
        _log(f"--- Page {i+1} ---")
        text, sample = extract_text_sample(page)
        has_text = len(text.strip()) > 0
        _log(f"Extractable text length: {len(text)}")
        _log(f"Text sample (first 200 chars): {sample!r}")
        roots = content_xrefs_from_page(doc, page)
        _log(f"Top-level content stream xrefs: {roots if roots else 'none'}")
        found_tr3, visited = recurse_for_tr3(doc, roots)
        _log(f"Visited xrefs (streams / forms): {sorted(visited) if visited else 'none'}")
        _log(f"Invisible render mode (3 Tr) detected: {found_tr3}")
        if has_text and found_tr3:
            _log("RESULT: Embedded text present and marked invisible (render mode 3).")
        elif has_text and not found_tr3:
            _log("RESULT: Embedded text present, but could not confirm 3 Tr.")
            _log("Explanation: Text may be inside deeper objects, or invisibility uses alpha/clipping/layers.")
            overall_code = max(overall_code, 1)
        else:
            _log("RESULT: No extractable text on this page.")
            overall_code = max(overall_code, 1)

    doc.close()
    return overall_code

