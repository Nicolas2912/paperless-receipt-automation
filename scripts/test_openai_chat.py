from __future__ import annotations

from typing import Any, Dict, Optional, List
import base64, hashlib, json, logging, mimetypes, os, time
from datetime import datetime
from openai import OpenAI, APIConnectionError, APITimeoutError, APIStatusError

from ...logging import get_logger
from ...config import load_openai

LOG = get_logger("productdb-extraction")


# ---------- file helpers ----------
def _b64_data_url(path: str) -> Optional[str]:
    mime, _ = mimetypes.guess_type(path)
    if not mime:
        ext = os.path.splitext(path)[1].lower()
        mime = "image/jpeg" if ext in {".jpg", ".jpeg", ".jpe", ".jfif"} else "image/png"
    if not mime or not mime.startswith("image/"):
        LOG.error("Unsupported MIME type for vision extraction: %s", mime)
        return None
    try:
        with open(path, "rb") as f:
            data = f.read()
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception as e:
        LOG.error("Failed to read source file for data URL: %s", e)
        return None


def _file_facts(path: str) -> Dict[str, Any]:
    try:
        size = os.path.getsize(path)
    except Exception:
        size = None
    sha256 = None
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        sha256 = h.hexdigest()
    except Exception:
        pass
    mime, _ = mimetypes.guess_type(path)
    return {"filename": os.path.basename(path), "mime_type": mime, "byte_size": size, "sha256": sha256}


# ---------- prompt & schema ----------
def _prompt() -> str:
    # (your original prompt, unchanged for brevity)
    return """
You extract structured data from a retail receipt image.
[... keep your full prompt here exactly as before ...]
"""


def _receipt_schema() -> Dict[str, Any]:
    # same schema as I sent earlier (strict top-level keys, items array, enums, etc.)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["merchant","purchase_date_time","currency","payment_method","totals","items"],
        "properties": {
            "merchant": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name","address"],
                "properties": {
                    "name": {"type": "string"},
                    "address": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["street","city","postal_code","country"],
                        "properties": {
                            "street": {"type": ["string","null"]},
                            "city": {"type": ["string","null"]},
                            "postal_code": {"type": ["string","null"]},
                            "country": {"type": ["string","null"]},
                        },
                    },
                },
            },
            "purchase_date_time": {"type": "string"},
            "currency": {"type": "string"},
            "payment_method": {"type": "string", "enum": ["CASH","CARD","OTHER"]},
            "totals": {
                "type": "object",
                "additionalProperties": False,
                "required": ["total_net","total_tax","total_gross"],
                "properties": {
                    "total_net": {"type": ["integer","null"]},
                    "total_tax": {"type": ["integer","null"]},
                    "total_gross": {"type": ["integer","null"]},
                },
            },
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["product_name","quantity","unit","unit_price_net","unit_price_gross","tax_rate","line_net","line_tax","line_gross"],
                    "properties": {
                        "product_name": {"type": "string"},
                        "quantity": {"type": "number"},
                        "unit": {"type": ["string","null"]},
                        "unit_price_net": {"type": ["integer","null"]},
                        "unit_price_gross": {"type": ["integer","null"]},
                        "tax_rate": {"type": "number", "enum": [0.0,0.07,0.19]},
                        "line_net": {"type": ["integer","null"]},
                        "line_tax": {"type": ["integer","null"]},
                        "line_gross": {"type": ["integer","null"]},
                    },
                },
            },
        },
    }


# ---------- main (streaming + strict JSON + 60s timeout) ----------
def extract_receipt_payload_from_image(
    source_path: str,
    *,
    model_name: str = "gpt-5-mini",
    script_dir: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Vision extraction via **Responses API streaming** with **Structured Outputs**.
    - 60s client timeout
    - logs every streaming event category for visibility
    - never relies on `output_text` (which can be empty on some SDK/model combos)
    """
    api_key = load_openai(script_dir or os.getcwd())
    if not api_key:
        LOG.error("OPENAI_API_KEY missing in env/.env; cannot run extraction")
        return None

    url = _b64_data_url(source_path)
    if not url:
        return None

    # diagnostics about the payload
    facts = _file_facts(source_path)
    approx_payload_mb = round((len(url) * 1.0) / (1024 * 1024), 2)
    LOG.debug(
        "Preparing request: file=%s bytes=%s sha256=%s (~data URL %.2f MiB)",
        facts.get("filename"), facts.get("byte_size"), facts.get("sha256"), approx_payload_mb
    )

    # client with 60s timeout
    base_url = os.environ.get("OPENAI_BASE_URL")
    client = OpenAI(api_key=api_key, timeout=60.0, max_retries=2, base_url=base_url)

    # optional deep logs
    if (os.environ.get("OPENAI_LOG") or "").lower() == "debug":
        logging.getLogger("httpx").setLevel(logging.DEBUG)
        logging.getLogger("httpcore").setLevel(logging.DEBUG)


    prompt = _prompt()
    schema = _receipt_schema()

    LOG.info("Calling OpenAI Responses API (streaming) model='%s' (timeout=60s)…", model_name)

    t0 = time.perf_counter()
    # stream=True → generator of SSE events (documented in SDK README)
    # We accumulate JSON deltas into `json_buf`.
    json_buf: List[str] = []
    usage_summary: Dict[str, Any] = {}
    response_id: Optional[str] = None

    try:
        stream = client.responses.create(
            model=model_name,
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": url},
                ],
            }],
            response_format={"type": "json_schema", "json_schema": {"name": "ReceiptExtraction", "schema": schema, "strict": True}}
        )
        print(f"STREAM: {stream}")
        
        dt = time.perf_counter() - t0

        # Reassemble structured JSON; parse strictly
        if not json_buf:
            LOG.error("Model produced no structured JSON (buffer empty).")
            return None
        raw = "".join(json_buf).strip()
        try:
            data = json.loads(raw)
        except Exception as je:
            LOG.error("Failed to parse streamed JSON: %s", je)
            LOG.debug("First 500 chars of raw JSON: %s", raw[:500])
            return None

    except (APIConnectionError, APITimeoutError) as e:
        LOG.error("Network/timeout while calling OpenAI: %s", e)
        LOG.info("Hints: check proxy/VPN; try OPENAI_LOG=debug; ensure port 443, HTTP/2 not blocked.")
        return None
    except APIStatusError as e:
        LOG.error("OpenAI API returned %s: %s", getattr(e, "status_code", "?"), getattr(e, "response", None))
        return None
    except Exception as e:
        LOG.error("OpenAI extraction failed: %s", e)
        return None

    # enrich & return
    if not isinstance(data, dict):
        data = {"_raw": data}
    data.setdefault("source_file", facts)
    data.setdefault("_extraction_meta", {"model": model_name, "at": datetime.utcnow().isoformat(timespec="seconds") + "Z"})
    LOG.debug("Final JSON keys: %s", list(data.keys()))
    LOG.info("OpenAI extraction succeeded and JSON parsed.")
    return data
