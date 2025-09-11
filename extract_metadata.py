"""
Extract structured metadata from a receipt using Ollama (qwen2.5vl-receipt).

Goals (from initial comments):
- Use qwen2.5vl-receipt to extract:
  - Korrespondent (store name on the receipt)
  - Ausstellungsdatum (date of purchase)
  - Titel (title) â€“ we will build a consistent title in Python
  - Tags â€” chosen via a mapping file (not by the model)
  - Dokumenttyp â€” always "Kassenbon"
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


def debug(msg: str) -> None:
    print(f"[metadata] {msg}", flush=True)


DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5vl-receipt:latest")


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
        return obj
    except Exception as e:
        debug(f"ERROR parsing LLM JSON: {e}; content preview: {content[:200]}")
        return None


def _normalize_korrespondent(name: str) -> str:
    # Basic cleanup: strip spaces, remove URLs, keep reasonable characters
    n = (name or "").strip()
    # If value looks like a JSON key-value (e.g., "merchant": "dm ..."), extract only the value
    m = re.match(r'^\s*"?(merchant|korrespondent)"?\s*:\s*"?(.+?)"?\s*,?\s*$', n, flags=re.IGNORECASE)
    if m:
        n = m.group(2).strip()
    n = re.sub(r"https?://\S+", "", n)
    n = re.sub(r"\s+", " ", n)

    # Remove common legal/entity suffixes and generic words that frequently appear on receipts
    n = re.sub(r"\b(GmbH|GmbH & Co\. KG|Co\. KG|KG|AG|UG|SE|e\.K\.|e\.K|oHG|S\.p\.A\.|S\.p\.A|& Co\.|& Co)\b", "", n, flags=re.IGNORECASE)
    n = re.sub(r"\b(Warenhaus|Markt|Zentrale|Filiale)\b", "", n, flags=re.IGNORECASE)
    n = re.sub(r"\s+", " ", n).strip(" ,-")

    # Strip diacritics to stabilize brand keys (e.g., famÃ­lia -> familia)
    try:
        import unicodedata
        n_ascii = unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode("ascii")
        if n_ascii:
            n = n_ascii
    except Exception:
        pass

    # Heuristic normalization for known brand variants
    low = n.lower()
    # Normalize various forms like "dm drogerie markt", "dm-drogerie markt", "dmi-drogerie markt" to "DM"
    # We look for the presence of "drogerie" and a dm/dmi token to be safe.
    if ("drogerie" in low and (re.search(r"\bdm\b", low) or re.search(r"\bdmi\b", low))) or re.search(r"\bdm[-\s]?drogerie", low):
        debug(f"Normalizing korrespondent '{name}' to 'DM'")
        return "DM"

    # Famila/familia variants -> 'Familia' (title case) per user preference
    if re.search(r"\bfamila\b|\bfamilia\b", low):
        debug(f"Normalizing korrespondent '{name}' to 'Familia'")
        return "Familia"

    # Netto Marken-Discount -> Netto
    if "netto" in low:
        return "Netto"

    return n


def _normalize_date_iso(value: str) -> Optional[str]:
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
    debug(
        "Extracted: korrespondent='{}', date={}, amount={} {}".format(
            md.korrespondent, md.ausstellungsdatum, md.betrag_value, md.betrag_currency
        )
    )
    debug(f"Generated base title: {md.title()}")
    return md


def main():
    ap = argparse.ArgumentParser(description="Extract receipt metadata via Ollama (qwen2.5vl-receipt)")
    ap.add_argument("--source", required=True, help="Path to receipt image or PDF (first page will be used by the model)")
    ap.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL, help="Ollama base URL (default from OLLAMA_URL or http://localhost:11434)")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model name (default qwen2.5vl-receipt:latest)")
    # Note: ASN is not used; no Paperless API lookup is needed here.
    args = ap.parse_args()

    debug("Starting extract_metadata.py")
    md = extract_from_source(args.source, ollama_url=args.ollama_url, model=args.model)
    if md is None:
        debug("FATAL: Extraction failed")
        sys.exit(1)

    # Build output without ASN
    out = asdict(md) | {"title": md.title()}
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()


