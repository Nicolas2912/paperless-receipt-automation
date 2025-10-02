from __future__ import annotations

from typing import Any, Dict, Optional, List, Tuple
import base64, hashlib, json, logging, mimetypes, os, time, sys
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import httpx  # NEW
from openai import OpenAI, APIConnectionError, APITimeoutError, APIStatusError
from dotenv import load_dotenv


try:
    from ...logging import get_logger
    from ...config import load_openai, load_ollama, load_openrouter
except Exception:
    # Allow running this file directly (no package parent)
    _HERE = os.path.dirname(__file__)
    _SRC = os.path.abspath(os.path.join(_HERE, "../../.."))
    if _SRC not in sys.path:
        sys.path.insert(0, _SRC)
    from paperless_automation.logging import get_logger
    from paperless_automation.config import load_openai, load_ollama, load_openrouter
import requests

LOG = get_logger("productdb-extraction")

ENV_PATH_WIN = r"C:\Users\Anwender\Desktop\Nicolas\Dokumente\MeineProgramme\paperless-receipt-automation\.env"

load_dotenv(dotenv_path=ENV_PATH_WIN)

# Backend and model toggles
# - BACKEND: "openai", "ollama", or "openrouter" (env: PRODUCTDB_BACKEND)
BACKEND: str = (os.getenv("PRODUCTDB_BACKEND") or "openrouter").strip().lower()
# - MODEL: default Ollama model tag (env: OLLAMA_MODEL)
MODEL: str = (os.getenv("OLLAMA_MODEL") or "gemma3:4b").strip()
# - OPENROUTER_MODEL: default model id for OpenRouter backend
OPENROUTER_MODEL: str = (os.getenv("OPENROUTER_MODEL"))
if OPENROUTER_MODEL:
    LOG.debug("Configured OpenRouter model: %s", OPENROUTER_MODEL)

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


# ---------- dataclasses & containers ----------


@dataclass(frozen=True)
class FileFacts:
    """Normalized view of the source file metadata used for provenance logging."""

    filename: Optional[str]
    mime_type: Optional[str]
    byte_size: Optional[int]
    sha256: Optional[str]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FileFacts":
        return cls(
            filename=data.get("filename"),
            mime_type=data.get("mime_type"),
            byte_size=data.get("byte_size"),
            sha256=data.get("sha256"),
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "filename": self.filename,
            "mime_type": self.mime_type,
            "byte_size": self.byte_size,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class OpenRouterConfig:
    """Configuration set required to talk to the OpenRouter API."""

    api_key: str
    model_name: str
    temperature: float = 0.0
    max_tokens: int = 8000
    timeout_seconds: int = 180


# ---------- prompt & schema (your originals) ----------
def _prompt() -> str:
    return """
Extract data from a retail receipt image.

Output requirements (strict):

Return ONLY strict JSON (no code fences, no commentary).
The top-level object MUST have exactly these keys:

- merchant
- date
- currency
- payment_method
- items

Field specifications:
- merchant:

	{ "name": string, "street": string, "city": string, "postal_code": string }

- date: purchase date in "DD.MM.YYYY" format
- currency: 3-letter code
- payment_method: CASH | CARD | OTHER
- items: array of objects. Each item has:

	{ "product_name": string, "line_gross": int, "amount": int, "tax": decimal }

Rules:
- line_gross = price in euro cents (e.g., 3.49€ → 349).
- tax = tax as decimal (e.g., 19% → 0.19, 7% → 0.07).
- amount = actual quantity (should be read as the numeric amount listed next to or below the product).
- Only include purchasable line items (exclude headers, totals, discounts, etc.).
- Please search on the receipt for something that indicates the payment method (CASH, CARD, OTHER).
- Do not repeat yourself. End exactly with the last item in the list.

Example:

	{
	  "merchant": {
	    "name": "Rewe Supermarkt",
	    "street": "Musterstraße 12",
	    "city": "Berlin",
	    "postal_code": "12345"
	  },
	  "date": "27.09.2025",
      "currency": "EUR",
      "payment_method": "CARD",
	  "items": [
	    {
	      "product_name": "Gala Äpfel 1kg",
	      "line_gross": 299,
	      "amount": 2,
	      "tax": 0.07
	    },
	    {
	      "product_name": "Milch 1L",
	      "line_gross": 119,
	      "amount": 1,
	      "tax": 0.07
	    },
	    {
	      "product_name": "Nutella 450g",
	      "line_gross": 369,
	      "amount": 1,
	      "tax": 0.19
	    }
	  ]
	}
    
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


def _scavenge_json_block(s: str) -> Optional[Dict[str, Any]]:
    if not s:
        return None
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    for idx in range(end, start, -1):
        try:
            return json.loads(s[start:idx + 1])
        except Exception:
            continue
    return None


class PayloadNormalizer:
    """Normalize OpenRouter JSON into the shape expected by the parser/service."""

    DATE_FALLBACK_FORMATS: Tuple[str, ...] = (
        "%d.%m.%Y",
        "%d.%m.%y",
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%Y/%m/%d",
    )

    def __init__(self, facts: FileFacts) -> None:
        self.facts = facts

    def normalize(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        merchant = self._normalize_merchant(raw.get("merchant"))
        purchase_date_time = self._normalize_purchase_date(
            raw.get("purchase_date_time") or raw.get("date") or raw.get("purchase_date")
        )
        currency = (str(raw.get("currency")) if raw.get("currency") else "EUR").upper()
        payment_method = (str(raw.get("payment_method")) if raw.get("payment_method") else "OTHER").upper()

        items = [item for item in self._normalize_items(raw.get("items") or []) if item]
        totals = self._normalize_totals(raw.get("totals"), items)

        normalized: Dict[str, Any] = {
            "merchant": merchant,
            "purchase_date_time": purchase_date_time,
            "currency": currency,
            "payment_method": payment_method,
            "totals": totals,
            "items": items,
            "source_file": self.facts.as_dict(),
        }

        raw_content = raw.get("raw_content")
        if isinstance(raw_content, str) and raw_content.strip():
            normalized["raw_content"] = raw_content.strip()

        return normalized

    # ---- merchant/address helpers -------------------------------------------------
    def _normalize_merchant(self, payload: Any) -> Dict[str, Any]:
        merchant_raw = payload if isinstance(payload, dict) else {}
        address_raw = merchant_raw.get("address") if isinstance(merchant_raw.get("address"), dict) else {
            "street": merchant_raw.get("street"),
            "city": merchant_raw.get("city"),
            "postal_code": merchant_raw.get("postal_code"),
            "country": merchant_raw.get("country"),
        }

        def _clean(value: Any) -> Optional[str]:
            if isinstance(value, str) and value.strip():
                return value.strip()
            return None

        merchant = {
            "name": _clean(merchant_raw.get("name")) or "",
            "address": {
                "street": _clean(address_raw.get("street")),
                "city": _clean(address_raw.get("city")),
                "postal_code": _clean(address_raw.get("postal_code")),
                "country": _clean(address_raw.get("country")),
            },
        }
        return merchant

    # ---- item helpers -------------------------------------------------------------
    def _normalize_items(self, payload_items: List[Any]) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for candidate in payload_items:
            if not isinstance(candidate, dict):
                continue
            item = self._normalize_item(candidate)
            if item:
                items.append(item)
        return items

    def _normalize_item(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        name = self._text(item.get("product_name") or item.get("name"))
        if not name:
            return None

        quantity = self._coerce_quantity(item.get("quantity") or item.get("amount"))
        tax_rate = self._normalize_tax_rate(item.get("tax_rate") or item.get("tax"))

        line_gross = self._coerce_int_cents(item.get("line_gross") or item.get("gross") or item.get("total"))
        line_net = self._coerce_int_cents(item.get("line_net"))
        line_tax = self._coerce_int_cents(item.get("line_tax"))

        if line_net is None or line_tax is None:
            computed_net, computed_tax = self._compute_net_and_tax(line_gross, tax_rate)
            line_net = line_net if line_net is not None else computed_net
            line_tax = line_tax if line_tax is not None else computed_tax

        if line_net is not None and line_gross is not None and line_tax is None:
            line_tax = line_gross - line_net
        if line_tax is not None and line_gross is not None and line_net is None:
            line_net = line_gross - line_tax

        unit_price_gross = self._coerce_int_cents(item.get("unit_price_gross"))
        if unit_price_gross is None:
            unit_price_gross = self._compute_unit_value(line_gross, quantity)

        unit_price_net = self._coerce_int_cents(item.get("unit_price_net"))
        if unit_price_net is None:
            unit_price_net = self._compute_unit_value(line_net, quantity)

        normalized = {
            "product_name": name,
            "quantity": float(quantity),
            "unit": self._text(item.get("unit") or item.get("measure")),
            "unit_price_net": unit_price_net,
            "unit_price_gross": unit_price_gross,
            "tax_rate": float(tax_rate),
            "line_net": line_net,
            "line_tax": line_tax,
            "line_gross": line_gross,
        }
        return normalized

    # ---- totals helpers -----------------------------------------------------------
    def _normalize_totals(self, totals_payload: Any, items: List[Dict[str, Any]]) -> Dict[str, Optional[int]]:
        totals_raw = totals_payload if isinstance(totals_payload, dict) else {}
        totals = {
            "total_net": self._coerce_int_cents(totals_raw.get("total_net")),
            "total_tax": self._coerce_int_cents(totals_raw.get("total_tax")),
            "total_gross": self._coerce_int_cents(totals_raw.get("total_gross")),
        }

        computed_totals = self._summarize_totals(items)
        for key, value in computed_totals.items():
            if totals.get(key) is None and value is not None:
                totals[key] = value
        return totals

    # ---- primitive helpers -------------------------------------------------------
    @staticmethod
    def _text(value: Any) -> Optional[str]:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @staticmethod
    def _round_half_up(value: Decimal) -> int:
        try:
            return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        except InvalidOperation:
            return int(value)

    @staticmethod
    def _coerce_decimal(value: Any) -> Optional[Decimal]:
        if value is None:
            return None
        if isinstance(value, int):
            return Decimal(value)
        if isinstance(value, float):
            return Decimal(str(value))
        if isinstance(value, str) and value.strip():
            try:
                return Decimal(value.replace(",", "."))
            except Exception:
                return None
        return None

    @classmethod
    def _coerce_int_cents(cls, value: Any) -> Optional[int]:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(round(value))
        if isinstance(value, str) and value.strip():
            try:
                cleaned = value.strip().replace("€", "").replace(",", ".")
                if "." in cleaned:
                    return int(round(float(cleaned)))
                return int(cleaned)
            except Exception:
                return None
        decimal_value = cls._coerce_decimal(value)
        return int(decimal_value) if decimal_value is not None else None

    @staticmethod
    def _coerce_quantity(value: Any) -> float:
        if value is None:
            return 1.0
        try:
            qty = float(value)
            return qty if qty > 0 else 1.0
        except Exception:
            return 1.0

    @staticmethod
    def _normalize_tax_rate(value: Any) -> float:
        candidate: Optional[float] = None
        if isinstance(value, (int, float)):
            candidate = float(value)
        elif isinstance(value, str) and value.strip():
            try:
                candidate = float(value.strip().replace("%", ""))
                if candidate > 1:
                    candidate /= 100.0
            except Exception:
                candidate = None

        if candidate is None:
            return 0.19

        for target in (0.0, 0.07, 0.19):
            if abs(candidate - target) < 0.02:
                return target
        return 0.19 if candidate > 0.1 else 0.07

    @classmethod
    def _compute_net_and_tax(cls, line_gross: Optional[int], tax_rate: float) -> Tuple[Optional[int], Optional[int]]:
        if line_gross is None:
            return None, None
        if tax_rate in (None, 0.0):
            return line_gross, 0

        gross_dec = Decimal(line_gross)
        divisor = Decimal("1") + Decimal(str(tax_rate))
        try:
            net_dec = gross_dec / divisor
        except InvalidOperation:
            return None, None

        net = cls._round_half_up(net_dec)
        tax = int(gross_dec - Decimal(net))
        return net, tax

    @classmethod
    def _compute_unit_value(cls, total_cents: Optional[int], quantity: float) -> Optional[int]:
        if total_cents is None or quantity <= 0:
            return total_cents
        try:
            per_unit = Decimal(total_cents) / Decimal(str(quantity))
        except InvalidOperation:
            return total_cents
        return cls._round_half_up(per_unit)

    @classmethod
    def _summarize_totals(cls, items: List[Dict[str, Any]]) -> Dict[str, Optional[int]]:
        gross_values = [it.get("line_gross") for it in items if it.get("line_gross") is not None]
        net_values = [it.get("line_net") for it in items if it.get("line_net") is not None]
        tax_values = [it.get("line_tax") for it in items if it.get("line_tax") is not None]

        total_gross = sum(gross_values) if gross_values else None
        total_net = sum(net_values) if len(net_values) == len(items) else (sum(net_values) if net_values else None)
        if total_net is None and total_gross is not None and tax_values:
            total_net = total_gross - sum(tax_values)
        total_tax = sum(tax_values) if tax_values else (
            total_gross - total_net if (total_gross is not None and total_net is not None) else None
        )

        return {
            "total_net": total_net,
            "total_tax": total_tax,
            "total_gross": total_gross,
        }

    def _normalize_purchase_date(self, raw_value: Any) -> Optional[str]:
        if not raw_value:
            return None
        text = str(raw_value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return dt.replace(tzinfo=None).isoformat(timespec="seconds")
        except Exception:
            pass

        for fmt in self.DATE_FALLBACK_FORMATS:
            try:
                dt = datetime.strptime(text, fmt)
                return dt.replace(hour=12, minute=0, second=0).isoformat(timespec="seconds")
            except Exception:
                continue
        return None


def _normalize_openrouter_payload(raw: Dict[str, Any], *, facts: FileFacts) -> Dict[str, Any]:
    """Facilitate backwards compatibility for existing call sites."""

    normalizer = PayloadNormalizer(facts)
    return normalizer.normalize(raw)


class OpenRouterClient:
    """Thin wrapper around OpenRouter API requests with helpful logging."""

    ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, config: OpenRouterConfig) -> None:
        self.config = config

    # ---- core request helpers ----------------------------------------------------
    def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[int] = None,
    ) -> Optional[str]:
        payload = {
            "model": self.config.model_name,
            "messages": messages,
            "temperature": self.config.temperature if temperature is None else temperature,
            "max_tokens": self.config.max_tokens if max_tokens is None else max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(
                self.ENDPOINT,
                headers=headers,
                json=payload,
                timeout=timeout or self.config.timeout_seconds,
            )
        except Exception as exc:
            LOG.error("OpenRouter request failed: %s", exc)
            return None

        if resp.status_code >= 400:
            LOG.error("OpenRouter HTTP %s: %s", resp.status_code, resp.text[:500])
            return None

        body = resp.json()
        choices = body.get("choices") or []
        if not choices:
            LOG.error("OpenRouter returned no choices: %s", body)
            return None
        message = choices[0].get("message") or {}
        return message.get("content")

    def json_request(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        text = self.chat(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            return _scavenge_json_block(text)

    # ---- convenience helpers -----------------------------------------------------
    def guess_country(self, data_url: str, context: Dict[str, Any]) -> Optional[str]:
        context_text = json.dumps(context, ensure_ascii=False)
        prompt = (
            "Estimate the likely ISO 3166-1 alpha-2 country code for the merchant on this retail receipt. "
            "Use signals such as language, city names, postal codes, addresses, currency symbols, and other hints. "
            "Return strict JSON with a single key 'country'."
        )
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"{prompt}\n\nStructured context:\n{context_text}"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]
        result = self.json_request(messages, max_tokens=200, timeout=150)
        if not result:
            return None
        country = result.get("country")
        if isinstance(country, str) and country.strip():
            return country.strip().upper()
        return None

    def fetch_raw_content(self, data_url: str) -> Optional[str]:
        prompt = (
            "Transcribe the receipt exactly as text, preserving line order. "
            "Return strict JSON with key 'raw_content' containing the transcription."
        )
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]
        result = self.json_request(messages, max_tokens=6000, timeout=240)
        if not result:
            return None
        text = result.get("raw_content")
        if isinstance(text, str) and text.strip():
            return text.strip()
        return None

def extract_receipt_payload_from_image(
    source_path: str,
    *,
    model_name: str = "gpt-5-mini",
    script_dir: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    # Dispatch based on backend (env can override module default)
    backend = (os.getenv("PRODUCTDB_BACKEND") or BACKEND).strip().lower()
    if backend not in {"openai", "ollama", "openrouter"}:
        LOG.warning("Unknown PRODUCTDB_BACKEND=%r; defaulting to 'openai'", backend)
        backend = "openai"

    if backend == "ollama":
        LOG.info("Backend selected: Ollama")
        LOG.debug("Effective Ollama model: %s", MODEL)
        return _extract_with_ollama(source_path, script_dir=script_dir, model_tag=MODEL)
    if backend == "openrouter":
        LOG.info("Backend selected: OpenRouter")
        effective_model = (os.getenv("OPENROUTER_MODEL") or OPENROUTER_MODEL).strip()
        LOG.debug("Effective OpenRouter model: %s", effective_model)
        return _extract_with_openrouter(source_path, script_dir=script_dir, model_name=effective_model)

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
    chosen_model = (model_from_env or model_tag or "gemma3:4b").strip()

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

    effective_model = (model_name or "").strip()
    if not effective_model:
        LOG.error("No OpenRouter model configured via OPENROUTER_MODEL or parameter.")
        return None

    data_url = _b64_data_url(source_path)
    if not data_url:
        return None

    facts = FileFacts.from_dict(_file_facts(source_path))
    prompt = _prompt()
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }
    ]

    config = OpenRouterConfig(api_key=api_key, model_name=effective_model)
    client = OpenRouterClient(config)

    LOG.info("Calling OpenRouter model=%s for structured extraction", effective_model)
    parsed = client.json_request(messages, max_tokens=8000, timeout=180)
    if not isinstance(parsed, dict) or not parsed:
        LOG.error("OpenRouter returned no valid JSON for structured extraction.")
        return None

    normalized = _normalize_openrouter_payload(parsed, facts=facts)

    address = (normalized.get("merchant") or {}).get("address") or {}
    context_for_country = {
        "merchant": normalized.get("merchant"),
        "currency": normalized.get("currency"),
        "city": address.get("city"),
        "postal_code": address.get("postal_code"),
    }

    enrichment = normalized.setdefault("_enrichment", {})

    if not address.get("country"):
        LOG.info("Country missing; invoking OpenRouter guess helper.")
        guessed_country = client.guess_country(data_url, context_for_country)
        if guessed_country:
            address["country"] = guessed_country
        enrichment["guessed_country"] = guessed_country

    if not normalized.get("raw_content"):
        LOG.info("raw_content missing; requesting transcription via OpenRouter.")
        raw_text = client.fetch_raw_content(data_url)
        if raw_text:
            normalized["raw_content"] = raw_text
        enrichment["raw_content_fetched"] = bool(raw_text)

    normalized.setdefault(
        "_extraction_meta",
        {
            "model": effective_model,
            "at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "backend": "openrouter",
        },
    )

    LOG.info("OpenRouter extraction parsed and enriched successfully.")
    return normalized


# ------------------------------ SELF-RUNNER ------------------------------
if __name__ == "__main__":
    """Very simple direct runner for OpenRouter.

    - Hardcoded image path is used if no CLI arg is provided.
    - Reads API key and optional model from .env via config loaders.
    - Prints the parsed JSON result; minimal logging.
    """
    # Hardcoded default; replace with your path if desired
    HARD_CODED_IMAGE = r"C:\\Users\\Anwender\\iCloudDrive\\Documents\\Scans\\1970-01-01_familia_betreff_1.jpeg"

    img = sys.argv[1] if len(sys.argv) > 1 else HARD_CODED_IMAGE
    if not os.path.isfile(img):
        print(f"Image not found: {img}")
        sys.exit(2)

    # Keep logs quiet
    try:
        import logging as _logging
        _logging.getLogger("productdb-extraction").setLevel(_logging.ERROR)
    except Exception:
        pass

    # Force OpenRouter backend unless already set
    os.environ.setdefault("PRODUCTDB_BACKEND", "openrouter")
    model = (os.getenv("OPENROUTER_MODEL") or OPENROUTER_MODEL).strip()

    # Call OpenRouter backend directly to avoid extra layers
    result = _extract_with_openrouter(img, script_dir=os.getcwd(), model_name=model)
    if not result:
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0)
