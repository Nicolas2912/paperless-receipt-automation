"""
Extract structured metadata from a receipt using Ollama (qwen2.5vl-receipt).

Goals (from initial comments):
- Use qwen2.5vl-receipt to extract:
  - Korrespondent (store name on the receipt)
  - Ausstellungsdatum (date of purchase)
  - Titel (title - we will build a consistent title in Python
  - Tags - chosen via a mapping file (not by the model)
  - Dokumenttyp - always "Kassenbon"
  - The Archive Serial Number (ASN) is not used in this project.

Strictness:
- The LLM must return ONLY compact JSON with a fixed schema so we can parse reliably.
- We normalize the date to ISO 8601 (YYYY-MM-DD) and the amount to a decimal with a dot separator.

CLI usage examples:
  conda activate paperless
  python extract_metadata.py --source "generated_pdfs/03.09.25, 21_32 Microsoft Lens.pdf"
  python extract_metadata.py --source "C:/scans/receipt.jpg" --ollama-url http://localhost:11434

Programmatic usage:
  from extract_metadata import extract_from_source
  md = extract_from_source(source_path)
"""

import argparse
import base64
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any
import requests
import re

# Shared logging & paths (Phase 1)
try:
    from src.paperless_automation.logging import get_logger  # type: ignore
    from src.paperless_automation.paths import fix_windows_path_input as _fix_input  # type: ignore
except Exception:
    def get_logger(name: str):  # type: ignore
        class _L:
            def info(self, m):
                print(f"[{name}] {m}")
            debug = info
            warning = info
            error = info
        return _L()
    def _fix_input(p: str) -> str:  # type: ignore
        return p

_LOG = get_logger("metadata")

# Shared normalization (Phase 2)
try:
    from src.paperless_automation.domain.normalize import (
        normalize_date_iso as _normalize_date_iso_shared,
        normalize_amount as _normalize_amount_shared,
    )
except Exception:
    _normalize_date_iso_shared = None  # type: ignore
    _normalize_amount_shared = None  # type: ignore


def debug(msg: str) -> None:
    _LOG.info(msg)


DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5vl-receipt:latest")


# =======================
# PDF TEXT EXTRACTION PATH
# =======================
# The code below adds a non-LLM, PyMuPDF-based extractor specifically for PDFs.
# JPEG images continue to use the existing LLM vision path. The PDF path aims
# to be deterministic and fast for structured invoices like REWE.

class PDFMetadataExtractor:
    """Rule-based extractor for searchable PDFs (no LLM involved).

    Extraction rules requested for REWE invoices:
    - korrespondent: if the document contains "REWE Markt GmbH" set to "REWE"
      (with normalization later). If not found, attempt a light fallback by
      checking for the standalone token "REWE".
    - ausstellungsdatum: find the label "Rechnungsdatum" and parse the nearest
      date (same line or next line). Output as YYYY-MM-DD.
    - betrag_value: find the label "Summe" or "ZU ZAHLEN" which is usually on the bottom or middle of the receipt and never on the top and parse the nearest amount. 
      The correct amount is usually the biggest value with the highest value so find it. Normalize to dot decimal with two places.
    - betrag_currency: always "EUR".
    - dokumenttyp: always "Rechnung".

    Logging is verbose so you can see exactly which rule fired.
    """

    DATE_PAT = re.compile(r"(\d{1,2})[./](\d{1,2})[./](\d{2,4})|\b(\d{4})-(\d{2})-(\d{2})\b")
    AMOUNT_PAT = re.compile(r"(?<!\d)(\d{1,3}(?:[.,]\d{3})*[.,]\d{2})(?!\d)")

    @staticmethod
    def _read_pdf_lines(path: str) -> list[str]:
        try:
            import fitz  # PyMuPDF
        except Exception as e:
            debug(f"ERROR: PyMuPDF is required for PDF extraction: {e}")
            raise
        doc = fitz.open(path)
        lines: list[str] = []
        debug(f"[pdf] Opened PDF with {doc.page_count} page(s)")
        for i in range(doc.page_count):
            page = doc.load_page(i)
            text = page.get_text("text") or ""
            page_lines = [ln.rstrip("\r") for ln in text.splitlines()]
            debug(f"[pdf] Page {i+1}: lines={len(page_lines)} chars={len(text)}")
            lines.extend(page_lines)
        doc.close()
        return lines

    @staticmethod
    def _find_korrespondent(lines: list[str]) -> str | None:
        joined = "\n".join(lines)
        if re.search(r"rewe\s+markt\s+gmbh", joined, re.IGNORECASE):
            debug("[pdf] Found vendor phrase 'REWE Markt GmbH' → korrespondent=REWE")
            return "REWE"
        # Light fallback: presence of 'REWE' anywhere
        if re.search(r"\bREWE\b", joined, re.IGNORECASE):
            debug("[pdf] Fallback vendor hit for 'REWE' token → korrespondent=REWE")
            return "REWE"
        debug("[pdf] Vendor not found via rules")
        return None

    @staticmethod
    def _find_date_near_label(lines: list[str], label: str) -> str | None:
        lab_pat = re.compile(re.escape(label), re.IGNORECASE)
        for idx, line in enumerate(lines):
            if not lab_pat.search(line):
                continue
            debug(f"[pdf] Label '{label}' found on line {idx+1}: {line!r}")
            # Try same line
            m = PDFMetadataExtractor.DATE_PAT.search(line)
            if m:
                cand = m.group(0)
                iso = _normalize_date_iso(cand)
                debug(f"[pdf] Date on same line: {cand!r} → {iso}")
                if iso:
                    return iso
            # Try next line if exists
            if idx + 1 < len(lines):
                nxt = lines[idx + 1]
                m2 = PDFMetadataExtractor.DATE_PAT.search(nxt)
                if m2:
                    cand = m2.group(0)
                    iso = _normalize_date_iso(cand)
                    debug(f"[pdf] Date on next line: {cand!r} → {iso}")
                    if iso:
                        return iso
        debug(f"[pdf] No date found near label '{label}'")
        return None

    @staticmethod
    def _find_amount_near_label(lines: list[str], label: str) -> str | None:
        lab_pat = re.compile(re.escape(label), re.IGNORECASE)
        for idx, line in enumerate(lines):
            if not lab_pat.search(line):
                continue
            debug(f"[pdf] Label '{label}' found on line {idx+1}: {line!r}")
            # Same line first
            m = PDFMetadataExtractor.AMOUNT_PAT.search(line)
            if m:
                raw = m.group(1)
                norm = _normalize_amount(raw)
                debug(f"[pdf] Amount on same line: {raw!r} → {norm}")
                if norm:
                    return norm
            # Next line fallback
            if idx + 1 < len(lines):
                nxt = lines[idx + 1]
                m2 = PDFMetadataExtractor.AMOUNT_PAT.search(nxt)
                if m2:
                    raw = m2.group(1)
                    norm = _normalize_amount(raw)
                    debug(f"[pdf] Amount on next line: {raw!r} → {norm}")
                    if norm:
                        return norm
        debug(f"[pdf] No amount found near label '{label}'")
        return None

    @staticmethod
    def extract(path: str) -> Optional["ExtractedMetadata"]:
        lines = PDFMetadataExtractor._read_pdf_lines(path)
        if not lines:
            debug("[pdf] No text lines extracted from PDF")
        # Vendor
        kor = PDFMetadataExtractor._find_korrespondent(lines) or "Unbekannt"
        # Date
        date_iso = PDFMetadataExtractor._find_date_near_label(lines, "Rechnungsdatum") or "1970-01-01"
        # Amount
        amt = PDFMetadataExtractor._find_amount_near_label(lines, "Summe") or "0.00"
        # Fixed fields
        cur = "EUR"
        dtype = "Rechnung"

        # Normalize merchant as the rest of the pipeline expects
        try:
            from merchant_normalization import normalize_korrespondent as _norm_k  # type: ignore
            kor_norm = _norm_k(kor)
        except Exception:
            kor_norm = kor

        md = ExtractedMetadata(
            korrespondent=kor_norm or "Unbekannt",
            ausstellungsdatum=date_iso,
            betrag_value=amt,
            betrag_currency=cur,
            dokumenttyp=dtype,
        )
        # Structured summary for PDF route (no LLM involved)
        _LOG.info("[pdf] === Extracted metadata summary (PDF route) ===")
        _LOG.info(f"[pdf] korrespondent     : {md.korrespondent}")
        _LOG.info(f"[pdf] ausstellungsdatum: {md.ausstellungsdatum}")
        _LOG.info(f"[pdf] betrag_value     : {md.betrag_value}")
        _LOG.info(f"[pdf] betrag_currency  : {md.betrag_currency}")
        _LOG.info(f"[pdf] dokumenttyp      : {md.dokumenttyp}")
        _LOG.info(f"[pdf] title (preview)  : {md.title()}")
        if md.ausstellungsdatum == "1970-01-01":
            _LOG.warning("[pdf] ausstellungsdatum fallback in use (1970-01-01)")
        if md.betrag_value == "0.00":
            _LOG.warning("[pdf] betrag_value fallback in use (0.00)")
        try:
            pretty = json.dumps(asdict(md) | {"title": md.title()}, ensure_ascii=False, indent=2)
            for line in pretty.splitlines():
                _LOG.debug(f"[pdf] json {line}")
        except Exception:
            pass
        _LOG.info("[pdf] ==============================================")
        return md


def _encode_image_to_b64(path: str) -> str:
    """Return base64 image data. If source is a PDF, render first page to PNG.

    Requires PyMuPDF (pymupdf) when handling PDF input.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        try:
            import fitz  # PyMuPDF
        except Exception as e:
            debug(f"ERROR: PyMuPDF is required to render PDF pages: {e}")
            raise
        with fitz.open(path) as doc:
            if doc.page_count == 0:
                raise RuntimeError("PDF has no pages")
            page = doc.load_page(0)
            mat = fitz.Matrix(150/72, 150/72)
            pix = page.get_pixmap(matrix=mat)
            img_bytes = pix.tobytes("png")
            return base64.b64encode(img_bytes).decode("utf-8")
    else:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")


def _fix_windows_path_input(p: str) -> str:
    """Best-effort repair for common Windows path paste issues.

    - If user passes "C:Users..." (missing backslash after drive), insert it.
    - Heuristically insert separators between common tokens when missing
      (Users, <username>, Downloads, Desktop, Documents/Dokumente).
    - Trim surrounding quotes/spaces.
    """
    try:
        s = (p or "").strip().strip('"').strip("'")
        if os.name == "nt":
            # Ensure backslash after drive
            if re.match(r"^[A-Za-z]:(?![\\/])", s):
                fixed = s[:2] + "\\" + s[2:]
                if fixed != s:
                    debug(f"Repaired Windows path input: '{s}' -> '{fixed}'")
                s = fixed
            # If string still lacks separators (common paste), try inserting
            # them before well-known path tokens.
            if re.match(r"^[A-Za-z]:\\[^\\/]+$", s) or ("\\" not in s and "/" not in s):
                tokens = [
                    "Users",
                    os.environ.get("USERNAME", "Anwender"),
                    "Anwender",  # fallback explicit user seen in repo
                    "Downloads",
                    "Desktop",
                    "Documents",
                    "Dokumente",
                ]
                for tok in tokens:
                    if not tok:
                        continue
                    # Insert a backslash before and after token when missing
                    s = re.sub(rf"(?i)(?<![\\/]){re.escape(tok)}(?![\\/])", rf"\\{tok}\\", s)
                # Collapse duplicated separators
                s = re.sub(r"[\\/]{2,}", r"\\", s)
                debug(f"Applied token-based path repair → {s}")
        return s
    except Exception:
        return p


def _build_prompt() -> str:
    """Strict, minimal JSON instruction to reduce small-model load and hallucinations."""
    return (
        "You read a single retail receipt (Kassenbon). Return ONLY one compact JSON object, "
        "no code fences, no extra text, no paths, no explanations. Use EXACTLY these keys:\n"
        "{\n"
        "  \"korrespondent\": string,\n"
        "  \"ausstellungsdatum\": \"YYYY-MM-DD\",\n"
        "  \"betrag_value\": \"0.00\",\n"
        "  \"betrag_currency\": \"EUR\",\n"
        "  \"dokumenttyp\": \"Kassenbon\"\n"
        "}\n"
        "Rules:\n"
        "- korrespondent: the store/brand as printed (short, no URLs, no legal text).\n"
        "- ausstellungsdatum: parse date (e.g., DD.MM.YYYY) and output ISO YYYY-MM-DD. If none, leave as \"1970-01-01\".\n"
        "- betrag_value: the grand total; dot decimal, exactly two decimals, no thousands separators.\n"
        "- betrag_currency: 3-letter like EUR; if symbol â‚¬ is shown, use EUR.\n"
        "- dokumenttyp: exactly Kassenbon.\n"
        "- Do NOT invent data. Do NOT include any file paths. Output ONLY the JSON object."
    )


def _ollama_chat_vision(image_path: str, model: str, ollama_url: str, timeout: int = 120) -> Optional[Dict[str, Any]]:
    url = ollama_url.rstrip("/") + "/api/chat"
    img_b64 = _encode_image_to_b64(image_path)
    prompt = _build_prompt()
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt, "images": [img_b64]},
        ],
        "stream": False,
    }
    debug(f"Posting vision request to {url} with model={model}")
    try:
        r = requests.post(url, json=payload, timeout=timeout)
        r.raise_for_status()
    except Exception as e:
        debug(f"ERROR calling Ollama: {e}")
        return None

    try:
        data = r.json()
    except Exception as e:
        debug(f"ERROR: Non-JSON response from Ollama: {e}")
        return None

    # Ollama returns a dict with 'message': {'content': '...'}
    content = (data or {}).get("message", {}).get("content", "").strip()
    if content:
        # Helpful preview for debugging when JSON parsing fails downstream
        preview = content.replace("\n", " ")[:300]
        _LOG.debug(f"[llm] Raw content preview (first 300 chars): {preview}")
    if not content:
        debug("ERROR: Empty content from Ollama")
        return None

    # Some models may wrap JSON in code fences; strip them robustly
    if content.startswith("```"):
        content = re.sub(r"^```[a-zA-Z0-9]*\n|\n```$", "", content, flags=re.MULTILINE).strip()

    try:
        obj = json.loads(content)
        if not isinstance(obj, dict):
            debug("ERROR: LLM did not return a JSON object.")
            return None
        _LOG.debug(f"[llm] Parsed JSON keys: {sorted(list(obj.keys()))}")
        return obj
    except Exception as e:
        debug(f"ERROR parsing LLM JSON: {e}; content preview: {content[:200]}")
        return None


def _normalize_date_iso(value: str) -> Optional[str]:
    if _normalize_date_iso_shared is not None:
        return _normalize_date_iso_shared(value)  # delegate to shared
    if not value:
        return None
    v = value.strip()
    # Accept already ISO
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return v
    # Common German formats
    m = re.fullmatch(r"(\d{1,2})[\./](\d{1,2})[\./](\d{2,4})", v)
    if m:
        d, mth, y = m.groups()
        if len(y) == 2:
            y = ("20" + y) if int(y) < 70 else ("19" + y)
        return f"{int(y):04d}-{int(mth):02d}-{int(d):02d}"
    return None


def _normalize_amount(val: Any) -> Optional[str]:
    if _normalize_amount_shared is not None:
        return _normalize_amount_shared(val)  # delegate to shared
    """Normalize various decimal/thousands notations to dot-decimal string with two decimals.

    Handles inputs like '14,70', '14.70', '1.470,00', '1,470.00', and raw numbers.
    """
    if val is None:
        return None
    s = str(val).strip().replace(" ", "")
    if not s:
        return None
    # Determine decimal separator heuristically
    has_dot = "." in s
    has_comma = "," in s
    s2 = s
    if has_dot and has_comma:
        # If comma looks like decimal separator at the end, treat comma as decimal
        if re.search(r",\d{1,2}$", s):
            s2 = s.replace(".", "").replace(",", ".")
        # Else if dot looks like decimal separator at the end, remove commas (thousands)
        elif re.search(r"\.\d{1,2}$", s):
            s2 = s.replace(",", "")
        else:
            # Ambiguous: default to comma as decimal
            s2 = s.replace(".", "").replace(",", ".")
    elif has_comma:
        if re.search(r",\d{1,2}$", s):
            s2 = s.replace(".", "").replace(",", ".")
        else:
            # No decimal part; remove commas as thousands
            s2 = s.replace(",", "")
    elif has_dot:
        if re.search(r"\.\d{1,2}$", s):
            s2 = s  # already dot-decimal
        else:
            s2 = s.replace(".", "")  # dots used as thousands
    else:
        s2 = s

    m = re.search(r"-?\d+(?:\.\d{1,2})?", s2)
    if not m:
        return None
    from decimal import Decimal
    try:
        num = Decimal(m.group(0))
    except Exception:
        try:
            num = Decimal(str(float(m.group(0))))
        except Exception:
            return None
    return f"{num:.2f}"


@dataclass
class ExtractedMetadata:
    korrespondent: str
    ausstellungsdatum: str
    betrag_value: str
    betrag_currency: str
    dokumenttyp: str = "Kassenbon"

    def title(self) -> str:
        """Return title in format:
        "<dateinisoformat> - <korrespondent> - <betrag_value_de>"

        - betrag_value_de is German formatted (dot thousands, comma decimals)
        """
        k = self.korrespondent
        date = self.ausstellungsdatum
        # Format amount for title in German notation (comma decimal, dot thousands)
        def _fmt_de(x: str) -> str:
            from decimal import Decimal
            try:
                val = Decimal(str(x).replace(",", "."))
                s = f"{val:,.2f}"
                return s.replace(",", "_").replace(".", ",").replace("_", ".")
            except Exception:
                return str(x)
        amt_de = _fmt_de(self.betrag_value)
        base = f"{date} - {k} - {amt_de}"
        return base



def _log_llm_metadata_summary(md: "ExtractedMetadata", obj: Dict[str, Any], *, source: str | None = None, model: str | None = None) -> None:
    """Log a clear, structured summary for LLM-extracted metadata.

    - Shows normalized values and how they differ from the raw LLM output.
    - Warns when fallbacks/defaults are used (e.g., date 1970-01-01, amount 0.00).
    - Uses the shared logger for consistent console output.
    """
    raw_k = str(obj.get("korrespondent", "")).strip()
    raw_d = str(obj.get("ausstellungsdatum", "")).strip()
    raw_v = obj.get("betrag_value")
    raw_c = str(obj.get("betrag_currency", "")).strip()

    # Normalization diffs (string compare; case-insensitive for korrespondent)
    diffs: list[str] = []
    if raw_k and (raw_k.strip().lower() != (md.korrespondent or "").strip().lower()):
        diffs.append(f"korrespondent: '{raw_k}' -> '{md.korrespondent}'")
    if raw_d and raw_d != md.ausstellungsdatum:
        diffs.append(f"ausstellungsdatum: '{raw_d}' -> '{md.ausstellungsdatum}'")
    raw_v_str = None if raw_v is None else str(raw_v)
    if raw_v_str and raw_v_str != md.betrag_value:
        diffs.append(f"betrag_value: '{raw_v_str}' -> '{md.betrag_value}'")
    if raw_c and raw_c.upper() != (md.betrag_currency or "").upper():
        diffs.append(f"betrag_currency: '{raw_c}' -> '{md.betrag_currency}'")

    # Fallback warnings
    if md.ausstellungsdatum == "1970-01-01":
        _LOG.warning("[llm] ausstellungsdatum fallback in use (1970-01-01)")
    if md.betrag_value == "0.00":
        _LOG.warning("[llm] betrag_value fallback in use (0.00)")
    if not md.korrespondent:
        _LOG.warning("[llm] korrespondent empty after normalization")

    header_bits = []
    if source:
        header_bits.append(f"source={os.path.basename(source)}")
    if model:
        header_bits.append(f"model={model}")
    header = ", ".join(header_bits) if header_bits else "LLM metadata"

    # Main structured summary
    _LOG.info("[llm] === Extracted metadata summary ({}) ===".format(header))
    _LOG.info(f"[llm] korrespondent     : {md.korrespondent}")
    _LOG.info(f"[llm] ausstellungsdatum: {md.ausstellungsdatum}")
    _LOG.info(f"[llm] betrag_value     : {md.betrag_value}")
    _LOG.info(f"[llm] betrag_currency  : {md.betrag_currency}")
    _LOG.info(f"[llm] dokumenttyp      : {md.dokumenttyp}")
    _LOG.info(f"[llm] title (preview)  : {md.title()}")

    if diffs:
        _LOG.info("[llm] Normalization changes:")
        for d in diffs:
            _LOG.info(f"[llm]  - {d}")
    else:
        _LOG.debug("[llm] No normalization changes detected")

    # Pretty JSON for quick inspection at DEBUG level
    try:
        pretty = json.dumps(asdict(md) | {"title": md.title()}, ensure_ascii=False, indent=2)
        for line in pretty.splitlines():
            _LOG.debug(f"[llm] json {line}")
    except Exception:
        pass

    _LOG.info("[llm] ===============================================")


def extract_from_source(
    source_path: str,
    *,
    ollama_url: str = DEFAULT_OLLAMA_URL,
    model: str = DEFAULT_MODEL,
) -> Optional[ExtractedMetadata]:
    source_path = _fix_windows_path_input(source_path)
    source_path = os.path.abspath(source_path)
    if not os.path.isfile(source_path):
        debug(f"ERROR: Source not found: {source_path}")
        return None

    _, ext = os.path.splitext(source_path)
    if ext.lower() == ".pdf":
        debug("[route] PDF detected → using PDFMetadataExtractor (no LLM)")
        return PDFMetadataExtractor.extract(source_path)

    # JPEG and other raster images → keep existing LLM-based path
    debug("[route] Non-PDF detected → using LLM vision extractor")
    obj = _ollama_chat_vision(source_path, model=model, ollama_url=ollama_url)
    if obj is None:
        return None

    from merchant_normalization import normalize_korrespondent as _norm_k
    kor = _norm_k(obj.get("korrespondent", ""))
    date_iso = _normalize_date_iso(str(obj.get("ausstellungsdatum", "").strip())) or "1970-01-01"
    amt = _normalize_amount(obj.get("betrag_value")) or "0.00"
    cur = (obj.get("betrag_currency") or "EUR").strip().upper()
    md = ExtractedMetadata(
        korrespondent=kor or "Unbekannt",
        ausstellungsdatum=date_iso,
        betrag_value=amt,
        betrag_currency=cur,
    )
    _log_llm_metadata_summary(md, obj, source=source_path, model=model)
    return md


def main():
    ap = argparse.ArgumentParser(description="Extract receipt metadata (PDF path uses PyMuPDF; images use LLM vision)")
    ap.add_argument("--source", help="Path to receipt image or PDF. If omitted with --test-rewe, uses the REWE example path.")
    ap.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL, help="Ollama base URL (used for non-PDF images)")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model name (used for non-PDF images)")
    ap.add_argument("--test-rewe", action="store_true", help="Use hardcoded path for quick testing")
    # Note: ASN is not used; no Paperless API lookup is needed here.
    args = ap.parse_args()

    debug("Starting extract_metadata.py")
    src = args.source
    if not src and args.test_rewe:
        src = r"C:\Users\Anwender\Downloads\Rechnung_PN25064804420414.pdf"
        debug("Using --test-rewe sample path")

    if not src:
        debug("FATAL: Provide --source or use --test-rewe for the sample file.")
        sys.exit(2)

    md = extract_from_source(src, ollama_url=args.ollama_url, model=args.model)
    if md is None:
        debug("FATAL: Extraction failed")
        sys.exit(1)

    # Build output without ASN
    out = asdict(md) | {"title": md.title()}
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    from src.paperless_automation.cli.main import main as cli_main

    cli_main(["extract", *sys.argv[1:]])
