from __future__ import annotations

from typing import Any, Dict, Optional, List
import base64, hashlib, json, logging, mimetypes, os, time, sys
from datetime import datetime
import httpx  # NEW
from openai import OpenAI, APIConnectionError, APITimeoutError, APIStatusError

from ...logging import get_logger
from ...config import load_openai, load_ollama, load_openrouter
import requests

LOG = get_logger("productdb-extraction")

# Backend and model toggles
# - BACKEND: "openai", "ollama", or "openrouter" (env: PRODUCTDB_BACKEND)
BACKEND: str = "openrouter"
# - MODEL: default Ollama model tag (env: OLLAMA_MODEL)
MODEL: str = (os.environ.get("OLLAMA_MODEL") or "gemma3:4b").strip()
# - OPENROUTER_MODEL: default model id for OpenRouter backend
OPENROUTER_MODEL: str = (os.environ.get("OPENROUTER_MODEL") or "qwen/qwen2.5-vl-72b-instruct:free").strip()
print(OPENROUTER_MODEL)

# ---------- file helpers (unchanged) ----------
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

# ---------- prompt & schema (your originals) ----------
def _prompt() -> str:
    return """
        You extract structured data from a retail receipt image.

        Output requirements (strict):
        - Return ONLY strict JSON (no code fences, no commentary).
        - The top-level object MUST have exactly these keys (no extras):
        merchant, purchase_date_time, currency, payment_method, totals, items
        - All money values are integers in euro cents (e.g., 3.49€ → 349).
        - Use dot for decimals in any floats (e.g., tax_rate 0.19), not comma.

        Field specifications:
        - merchant: { name: string, address: { street: string|null, city: string|null, postal_code: string|null, country: string|null } }
        • Split address components if visible; otherwise set field to null. Country may be null.
        - purchase_date_time: string formatted as YYYY-MM-DDTHH:MM:SS (no timezone). If only a date is visible, set time to 12:00:00.
        - currency: 3-letter ISO code. Map the symbol '€' to 'EUR'. If unknown, use 'EUR'.
        - payment_method: one of ['CASH','CARD','OTHER'].
        • Map common forms: BAR/Bargeld → 'CASH'. KARTE/EC/EC-Karte/Girocard/Visa/Mastercard/Kontaktlos → 'CARD'.
        - totals: { total_net: int|null, total_tax: int|null, total_gross: int|null }.
        • If totals are not printed, compute from items when possible.
        - items: array of objects. Each item has:
        { product_name: string, quantity: number (>0), unit: string|null (e.g., 'x','kg','g','l','ml'),
            unit_price_net: int|null, unit_price_gross: int|null,
            tax_rate: 0.0|0.07|0.19,
            line_net: int|null, line_tax: int|null, line_gross: int|null }

        Computation and consistency rules:
        - Convert any printed decimals (comma or dot) to integer cents in output.
        - Prefer computing line values from quantity × unit_price_(net/gross) with standard rounding (half up to nearest cent) when unit prices are present.
        - Ensure line_gross = line_net + line_tax whenever both are present.
        - Compute totals so that total_gross ≈ sum(items.line_gross) within ±2 cents tolerance; if needed, distribute 1–2 cents to reconcile rounding.
        - Do NOT output negative money amounts. If discounts/coupons are present, incorporate them into the affected items (adjust unit_price or line values) so that items and totals reflect the final paid amounts without negative lines. Ignore returns/cancellations.
        - Exclude non-product/administrative rows from items (e.g., 'SUMME', 'GESAMT', 'USt', 'MwSt', 'Zwischensumme', loyalty points, payment change, cash-back, store slogans). Only include purchasable line items.

        VAT/tax marker hints (Germany):
        - Map tax markers to tax_rate as follows:
        • 'MwSt', 'USt', 'VAT', 'Tax' followed by '19' or '19%' → 0.19.
        • 'MwSt', 'USt' followed by '7' or '7%' → 0.07.
        • '0' or '0%' or explicit 'steuerfrei'/'tax free' → 0.00.
        - Many receipts label items with letters 'A','B','C' where a legend shows A=19%, B=7%, etc. Use the legend to assign each item's tax_rate accordingly.
        - If an item has no explicit tax marker but a nearby column/letter indicates grouping, inherit from that group; otherwise default to 0.19.
        - Do not infer unusual rates other than 0.00, 0.07, 0.19.

        Ambiguity resolution:
        - If time is missing, set 12:00:00; if date is completely missing, infer the most likely date from context (e.g., header/footer) and use that.
        - If currency is not explicit but the receipt is German and shows '€', use 'EUR'.
        - For 'Pfand' (deposit) lines: use the receipt's tax legend if present; if ambiguous, prefer the group/letter the receipt assigns. Only use 0.00 if the receipt’s VAT table marks that group as 0%/tax-free.
        - Use ASCII quotes and ensure valid JSON; do not include trailing commas.

        Important: An example structure may have been shown to illustrate the
        JSON shape. DO NOT copy or reuse any example values (like merchant
        names or amounts). Base the output strictly on the provided receipt.
"""

def _receipt_schema() -> Dict[str, Any]:
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

def extract_receipt_payload_from_image(
    source_path: str,
    *,
    model_name: str = "gpt-5-mini",
    script_dir: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    # Dispatch based on backend
    backend = BACKEND
    if backend not in {"openai", "ollama", "openrouter"}:
        LOG.warning("Unknown PRODUCTDB_BACKEND=%r; defaulting to 'openai'", backend)
        backend = "openai"

    if backend == "ollama":
        LOG.info("Backend selected: Ollama")
        LOG.debug("Effective Ollama model: %s", MODEL)
        return _extract_with_ollama(source_path, script_dir=script_dir, model_tag=MODEL)
    if backend == "openrouter":
        LOG.info("Backend selected: OpenRouter")
        LOG.debug("Effective OpenRouter model: %s", OPENROUTER_MODEL)
        return _extract_with_openrouter(source_path, script_dir=script_dir, model_name=OPENROUTER_MODEL)

    # OpenAI path below
    api_key = load_openai(script_dir or os.getcwd())
    if not api_key:
        LOG.error("OPENAI_API_KEY missing in env/.env; cannot run extraction")
        return None

    url = _b64_data_url(source_path)
    if not url:
        return None

    facts = _file_facts(source_path)
    approx_payload_mb = round((len(url) / (1024 * 1024)), 2)

    base_url = os.environ.get("OPENAI_BASE_URL")
    try:
        import openai as _openai_mod
        sdk_ver = getattr(_openai_mod, "__version__", "?")
    except Exception:
        sdk_ver = "?"

    LOG.debug(
        "Preparing request: file=%s bytes=%s sha256=%s (~data URL %.2f MiB) base_url=%s openai=%s httpx=%s py=%s",
        facts.get("filename"), facts.get("byte_size"), facts.get("sha256"), approx_payload_mb,
        base_url or "default", sdk_ver, httpx.__version__, sys.version.split()[0],
    )
    if approx_payload_mb > 15:
        LOG.warning("Large payload (~%.2f MiB). Consider downscaling before base64 to improve latency.", approx_payload_mb)

    # ---- helpers ------------------------------------------------------------
    def _scavenge_json(s: str) -> Optional[Dict[str, Any]]:
        if not s:
            return None
        start = s.find("{")
        end = s.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        for j in range(end, start, -1):
            try:
                return json.loads(s[start:j+1])
            except Exception:
                continue
        return None

    def _attach_meta(d: Dict[str, Any], resp_obj: Any = None) -> Dict[str, Any]:
        d.setdefault("source_file", facts)
        d.setdefault("_extraction_meta", {
            "model": model_name,
            "at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "response_id": getattr(resp_obj, "id", None),
        })
        return d

    http_client = httpx.Client(
        http2=True,
        timeout=httpx.Timeout(connect=10.0, read=90.0, write=30.0, pool=10.0),
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=30),
    )
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        http_client=http_client,
        max_retries=0,
    )

    if (os.environ.get("OPENAI_LOG") or "").lower() == "debug":
        logging.getLogger("httpx").setLevel(logging.DEBUG)
        logging.getLogger("httpcore").setLevel(logging.DEBUG)

    prompt = _prompt()
    schema = _receipt_schema()

    # ---- Attempt A: Responses.create (no response_format, because your SDK rejects it)
    data = None
    t0 = time.perf_counter()
    try:
        LOG.info("Calling OpenAI Responses API (NON-STREAM, no response_format) model='%s'…", model_name)
        resp = client.responses.create(
            model=model_name,
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt + "\n\nReturn ONLY a single JSON object that matches the schema I described."},
                    {"type": "input_image", "image_url": url},
                ],
            }],
            stream=False,
            timeout=120.0,
        )
        print(f"RESPONSE: {json.dumps(resp, indent=4)}")

        # 1) structured block
        try:
            out_list = getattr(resp, "output", None) or []
            for out in out_list:
                for block in getattr(out, "content", []) or []:
                    if getattr(block, "type", None) == "output_json":
                        d = getattr(block, "json", None)
                        if isinstance(d, dict) and d:
                            data = d
                            break
                if data:
                    break
        except Exception as e:
            LOG.debug("Reading output_json failed: %s", e)

        # 2) output_parsed (some SDKs expose it)
        if data is None:
            parsed = getattr(resp, "output_parsed", None)
            if isinstance(parsed, dict) and parsed:
                data = parsed

        # 3) output_text → parse/scavenge
        if data is None:
            text = getattr(resp, "output_text", None)
            if text:
                try:
                    data = json.loads(text)
                except Exception:
                    data = _scavenge_json(text)
                    if data is None:
                        LOG.error("Responses.output_text not valid JSON; first 500 chars: %r", text[:500])

        dt = time.perf_counter() - t0
        rid = getattr(resp, "id", None)
        usage = getattr(resp, "usage", None)
        usage_dict = {k: getattr(usage, k, None) if usage else None for k in ("input_tokens","output_tokens","total_tokens")}
        LOG.info("Responses finished in %.2fs id=%s usage=%s (data=%s)", dt, rid, usage_dict, "ok" if data else "none")

        if isinstance(data, dict) and data:
            return _attach_meta(data, resp)

    except (APIConnectionError, APITimeoutError) as e:
        LOG.error("Network/timeout while calling OpenAI (Responses): %s", e)
        LOG.info("Hints: proxy/SSL interception/VPN; OPENAI_LOG=debug; allow HTTP/2 on 443.")
    except APIStatusError as e:
        body = getattr(getattr(e, "response", None), "text", None)
        LOG.error("OpenAI API (Responses) returned %s. Body preview: %r", getattr(e, "status_code", "?"), (body[:300] if body else None))
    except TypeError as e:
        # Keep for completeness, though we removed response_format already.
        LOG.error("SDK TypeError in Responses.create: %s", e)
    except Exception as e:
        LOG.error("OpenAI extraction failed in Responses path: %s", e)

    # ---- Attempt B: Chat Completions (vision)
    # Prefer JSON object mode if supported; otherwise ask for JSON plainly and scavenge.
    try:
        LOG.info("Falling back to Chat Completions (VISION) model='%s'…", model_name)

        # Build messages in the modern “vision” shape
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a strict JSON generator. Output ONLY a single JSON object that matches the provided schema. "
                    "No prose, no markdown fences, no trailing text."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": url}},
                ],
            },
        ]

        # Try JSON mode first (some SDKs support this on chat.completions)
        json_mode_supported = True
        completion = None
        try:
            completion = client.chat.completions.create(
                model=model_name,
                messages=messages,
                response_format={"type": "json_object"},
                timeout=120.0,
            )
        except TypeError:
            json_mode_supported = False

        if completion is None and not json_mode_supported:
            # Retry without response_format; rely on instruction + scavenger
            completion = client.chat.completions.create(
                model=model_name,
                messages=messages,
                timeout=120.0,
            )

        # Parse result
        choice = (completion.choices[0] if getattr(completion, "choices", None) else None)
        text = (choice.message.content if choice and getattr(choice, "message", None) else None)

        parsed = None
        if text:
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = _scavenge_json(text)
                if parsed is None:
                    LOG.error("Chat output not valid JSON; first 500 chars: %r", (text[:500] if isinstance(text, str) else text))

        rid = getattr(completion, "id", None)
        usage = getattr(completion, "usage", None)
        usage_dict = {k: getattr(usage, k, None) if usage else None for k in ("prompt_tokens","completion_tokens","total_tokens")}
        LOG.info("Chat completion finished id=%s usage=%s (data=%s)", rid, usage_dict, "ok" if isinstance(parsed, dict) else "none")

        if isinstance(parsed, dict) and parsed:
            return _attach_meta(parsed, completion)

    except (APIConnectionError, APITimeoutError) as e:
        LOG.error("Network/timeout while calling OpenAI (Chat): %s", e)
    except APIStatusError as e:
        body = getattr(getattr(e, "response", None), "text", None)
        LOG.error("OpenAI API (Chat) returned %s. Body preview: %r", getattr(e, "status_code", "?"), (body[:300] if body else None))
    except Exception as e:
        LOG.error("OpenAI extraction failed in Chat path: %s", e)
    finally:
        http_client.close()

    LOG.error("No extraction payload produced after non-stream attempts (Responses and Chat).")
    return None


# --------------------------- OLLAMA BACKEND ---------------------------
def _extract_with_ollama(
    source_path: str,
    *,
    script_dir: Optional[str],
    model_tag: str,
) -> Optional[Dict[str, Any]]:
    """Extract structured receipt JSON using a local Ollama model.

    Preference: use provided OLLAMA_URL/OLLAMA_MODEL from .env or env.
    If a model name is supplied via `model_name` and PRODUCTDB_BACKEND=ollama,
    it is used if it looks like an Ollama tag; otherwise the env/.env value is
    preferred. Defaults to 'gemma3:4b' as requested.
    """
    # Resolve Ollama connection and model
    url_from_env, model_from_env = load_ollama(script_dir or os.getcwd())
    chosen_model = (model_tag or model_from_env or "gemma3:4b").strip()

    base = (url_from_env or "http://localhost:11434").strip()
    chat_endpoint = base if base.endswith("/api/chat") else base.rstrip("/") + "/api/chat"

    # Prepare image as base64 (Ollama expects raw base64 strings in 'images')
    try:
        with open(source_path, "rb") as f:
            raw = f.read()
            img_b64 = base64.b64encode(raw).decode("utf-8")
    except Exception as e:
        LOG.error("Failed reading image for Ollama extraction: %s", e)
        return None
    LOG.debug("Loaded image bytes=%s (base64 chars=%s)", len(raw), len(img_b64))

    def _is_probably_vision_model(tag: str) -> bool:
        s = (tag or "").lower()
        vision_markers = [
            "vl", "vision", "llava", "minicpm", "bakllava", "moondream",
            "qwen2-vl", "qwen2.5vl", "qwen2.5-vl", "llama3.2-vision", "phi-3-vision",
            "internvl", "cambrian-pro", "kosmos"
        ]
        return any(m in s for m in vision_markers)

    if not _is_probably_vision_model(chosen_model):
        LOG.info("Model '%s' is likely text-only; using OCR→JSON fallback.", chosen_model)
        return _ollama_two_step_fallback(base, chosen_model, source_path)

    prompt = _prompt()
    schema = _receipt_schema()  # unused directly but kept for parity

    # Instruction: enforce strict JSON
    system = (
        "You are a strict JSON generator. Output ONLY a single JSON object "
        "matching the provided schema description. No prose, no markdown."
    )
    user_text = (
        prompt
        + "\n\nReturn ONLY a single JSON object that exactly matches the structure and constraints above."
    )

    payload = {
        "model": chosen_model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text, "images": [img_b64]},
        ],
        "stream": False,
        "options": {
            "temperature": 0,
            "num_ctx": 16384,
            "num_predict": 4096,
        },
    }

    LOG.info("Calling Ollama for receipt extraction")
    LOG.debug("Backend=ollama model=%s endpoint=%s", chosen_model, chat_endpoint)

    def _scavenge_json(s: str) -> Optional[Dict[str, Any]]:
        if not s:
            return None
        start = s.find("{")
        end = s.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        for j in range(end, start, -1):
            try:
                return json.loads(s[start:j + 1])
            except Exception:
                continue
        return None

    facts = _file_facts(source_path)

    try:
        resp = requests.post(chat_endpoint, json=payload, timeout=180)
        if resp.status_code >= 400:
            LOG.error("Ollama HTTP %s: %s", resp.status_code, resp.text[:500])
            # If this looks like a non-vision model error, attempt a 2-step fallback
            return _ollama_two_step_fallback(base, chosen_model, source_path)
        data = resp.json()
    except Exception as e:
        LOG.error("Ollama request failed: %s", e)
        return _ollama_two_step_fallback(base, chosen_model, source_path)

    if data.get("error"):
        LOG.error("Ollama error: %s", data.get("error"))
        return _ollama_two_step_fallback(base, chosen_model, source_path)

    message = data.get("message") or {}
    content = message.get("content") or data.get("response") or ""

    print(content)

    parsed: Optional[Dict[str, Any]] = None
    if content:
        try:
            parsed = json.loads(content)
        except Exception:
            parsed = _scavenge_json(content)
            if parsed is None:
                LOG.error("Ollama content not valid JSON; first 500 chars: %r", content[:500])

    def _looks_like_sample(d: Dict[str, Any]) -> bool:
        try:
            m = (d or {}).get("merchant", {})
            items = (d or {}).get("items", [])
            return (
                isinstance(m, dict) and m.get("name") == "REWE Supermarkt Musterstadt"
                or any(isinstance(i, dict) and i.get("product_name") == "Vollkornbrot 750g" for i in items)
            )
        except Exception:
            return False

    if isinstance(parsed, dict) and parsed:
        if _looks_like_sample(parsed):
            LOG.warning("Ollama output resembles the sample; falling back to OCR→JSON.")
            return _ollama_two_step_fallback(base, chosen_model, source_path)
        parsed.setdefault("source_file", facts)
        parsed.setdefault(
            "_extraction_meta",
            {
                "model": chosen_model,
                "at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "backend": "ollama",
            },
        )
        LOG.info("Ollama extraction parsed successfully.")
        return parsed

    LOG.warning("Primary Ollama attempt returned no JSON; trying two-step fallback (OCR → JSON)…")
    return _ollama_two_step_fallback(base, chosen_model, source_path)


def _ollama_two_step_fallback(ollama_url: str, model_tag: str, source_path: str) -> Optional[Dict[str, Any]]:
    """Fallback for non-vision models: OCR via vision model then JSON via `model_tag`.

    - Transcribes the image using an Ollama vision model (from env OLLAMA_OCR_MODEL
      or defaults to qwen2.5vl-receipt:latest), then asks `model_tag` to produce
      strict JSON from that transcript.
    """
    # Choose a vision model for OCR step
    vision_model = os.environ.get("OLLAMA_OCR_MODEL", "qwen2.5vl-receipt:latest").strip()

    # Import locally to avoid circulars at module import time
    try:
        from ..transcribe import transcribe_image as _transcribe
    except Exception as e:
        LOG.error("Cannot import OCR helper for Ollama fallback: %s", e)
        return None

    transcript = _transcribe(source_path, ollama_url=ollama_url, model=vision_model, echo=False)
    if not transcript:
        LOG.error("Ollama OCR transcript is empty; giving up.")
        return None

    system = (
        "You are a strict JSON generator. Output ONLY a single JSON object "
        "matching the schema described. No prose, no markdown."
    )
    prompt = _prompt()
    user_text = (
        f"Here is the raw OCR transcript of a retail receipt between <transcript> tags.\n"
        f"Use it to extract the structured JSON according to the schema rules.\n\n"
        f"<transcript>\n{transcript}\n</transcript>\n\n"
        f"Return ONLY a single JSON object with the exact structure and constraints."
    )

    payload = {
        "model": model_tag,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt + "\n\n" + user_text},
        ],
        "stream": False,
        "options": {
            "temperature": 0,
            "num_ctx": 16384,
            "num_predict": 4096,
        },
    }

    chat_endpoint = ollama_url if ollama_url.endswith("/api/chat") else ollama_url.rstrip("/") + "/api/chat"
    try:
        resp = requests.post(chat_endpoint, json=payload, timeout=180)
        if resp.status_code >= 400:
            LOG.error("Ollama HTTP %s in fallback: %s", resp.status_code, resp.text[:500])
            return None
        data = resp.json()
    except Exception as e:
        LOG.error("Ollama fallback request failed: %s", e)
        return None

    if data.get("error"):
        LOG.error("Ollama fallback error: %s", data.get("error"))
        return None

    message = data.get("message") or {}
    content = message.get("content") or data.get("response") or ""

    print(content)

    def _scavenge_json(s: str) -> Optional[Dict[str, Any]]:
        if not s:
            return None
        start = s.find("{")
        end = s.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        for j in range(end, start, -1):
            try:
                return json.loads(s[start:j + 1])
            except Exception:
                continue
        return None

    parsed: Optional[Dict[str, Any]] = None
    if content:
        try:
            parsed = json.loads(content)
        except Exception:
            parsed = _scavenge_json(content)
            if parsed is None:
                LOG.error("Ollama fallback content not valid JSON; first 500 chars: %r", content[:500])

    if isinstance(parsed, dict) and parsed:
        facts = _file_facts(source_path)
        parsed.setdefault("source_file", facts)
        parsed.setdefault(
            "_extraction_meta",
            {
                "model": model_tag,
                "at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "backend": "ollama",
                "note": "two-step ocr→json",
            },
        )
        LOG.info("Ollama fallback extraction parsed successfully.")
        return parsed

    LOG.error("Ollama fallback produced no valid JSON.")
    return None


# ------------------------ OPENROUTER BACKEND -------------------------
def _extract_with_openrouter(
    source_path: str,
    *,
    script_dir: Optional[str],
    model_name: str,
) -> Optional[Dict[str, Any]]:
    """Use OpenRouter chat.completions with base64 data URL and our prompt.

    Follows the example provided (requests + base64 image) and adds
    max_tokens for larger outputs.
    """
    api_key = load_openrouter(script_dir or os.getcwd())
    if not api_key:
        LOG.error("OPEN_ROUTER_API_KEY missing in env/.env; cannot run extraction")
        return None

    data_url = _b64_data_url(source_path)
    if not data_url:
        return None

    facts = _file_facts(source_path)
    prompt = _prompt()

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    ]
    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": 0,
        "max_tokens": 8000,
    }

    text = None
    try:
        LOG.info("Calling OpenRouter (requests) model=%s endpoint=%s", model_name, url)
        resp = requests.post(url, headers=headers, json=payload, timeout=180)
        if resp.status_code >= 400:
            LOG.error("OpenRouter HTTP %s: %s", resp.status_code, resp.text[:500])
            return None
        body = resp.json()
        choices = body.get("choices") or []
        if choices:
            msg = (choices[0].get("message") or {})
            text = msg.get("content")
            print(json.dumps(text, indent=4))
    except Exception as e:
        LOG.error("OpenRouter request failed: %s", e)
        return None

    def _scavenge_json(s: str) -> Optional[Dict[str, Any]]:
        if not s:
            return None
        start = s.find("{")
        end = s.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        for j in range(end, start, -1):
            try:
                return json.loads(s[start:j + 1])
            except Exception:
                continue
        return None

    parsed = None
    if text:
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = _scavenge_json(text)
            if parsed is None:
                LOG.error("OpenRouter output not valid JSON; first 500 chars: %r", (text[:500] if isinstance(text, str) else text))

    if isinstance(parsed, dict) and parsed:
        parsed.setdefault("source_file", facts)
        parsed.setdefault(
            "_extraction_meta",
            {
                "model": model_name,
                "at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "backend": "openrouter",
            },
        )
        LOG.info("OpenRouter extraction parsed successfully.")
        return parsed

    LOG.error("OpenRouter returned no valid JSON.")
    return None
