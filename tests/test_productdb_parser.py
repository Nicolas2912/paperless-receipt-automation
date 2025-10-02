import os
import sys

import pytest
import types
from typing import Dict

# Ensure src/ is importable when tests run from repo root
sys.path.insert(0, os.path.abspath("src"))


def _install_stub(name: str, attrs: Dict[str, object]) -> None:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules.setdefault(name, module)


class _DummyOpenAI:  # pragma: no cover - simple stub for import-time dependencies
    pass


# Stub external dependencies not needed for parser tests
_install_stub("fitz", {})
_install_stub("httpx", {"Client": object, "Timeout": object, "Limits": object})
_install_stub(
    "openai",
    {
        "OpenAI": _DummyOpenAI,
        "APIConnectionError": Exception,
        "APITimeoutError": Exception,
        "APIStatusError": Exception,
    },
)
_install_stub("dotenv", {"load_dotenv": lambda *args, **kwargs: None})
class _RequestsResponse:  # pragma: no cover - lightweight placeholder
    pass


def _requests_post_stub(*args, **kwargs):  # pragma: no cover
    raise RuntimeError("requests.post should not be called during tests")


_install_stub(
    "requests",
    {
        "post": _requests_post_stub,
        "Response": _RequestsResponse,
    },
)
from paperless_automation.orchestrator.productdb.parser import parse_and_validate_payload, JsonValidationError
from paperless_automation.orchestrator.productdb.extraction import PayloadNormalizer, FileFacts


def test_parse_backfills_totals_and_preserves_raw_content():
    payload = {
        "merchant": {
            "name": "famila Bielefeld",
            "address": {
                "street": "Hansestr. 1",
                "city": "Bielefeld",
                "postal_code": "33689",
                "country": "DE",
            },
        },
        "purchase_date_time": "2025-09-20T12:00:00",
        "currency": "eur",
        "payment_method": "card",
        "totals": {"total_net": None, "total_tax": None, "total_gross": None},
        "items": [
            {
                "product_name": "JT SEELACHSF 950G",
                "quantity": 1,
                "unit": None,
                "unit_price_net": 532,
                "unit_price_gross": 569,
                "tax_rate": 0.07,
                "line_net": 532,
                "line_tax": 37,
                "line_gross": 569,
            },
            {
                "product_name": "GOENRGY MANGO 0.5L",
                "quantity": 1,
                "unit": None,
                "unit_price_net": 125,
                "unit_price_gross": 149,
                "tax_rate": 0.19,
                "line_net": 125,
                "line_tax": 24,
                "line_gross": 149,
            },
        ],
        "source_file": {
            "filename": "1970-01-01_familia_betreff_1.jpeg",
            "mime_type": "image/jpeg",
            "byte_size": 755939,
            "sha256": "4eac8ed61c0d9a421749887ecf6319a11a1d8f65a8628f1a40dc0bb397697c03",
        },
        "raw_content": "Sample raw receipt text",
    }

    normalized = parse_and_validate_payload(payload)

    assert normalized["totals"]["total_gross"] == 718
    assert normalized["totals"]["total_net"] == 657
    assert normalized["totals"]["total_tax"] == 61
    assert normalized["raw_content"] == "Sample raw receipt text"


def test_parse_requires_items():
    payload = {
        "merchant": {"name": "famila", "address": {}},
        "purchase_date_time": "2025-09-20T12:00:00",
        "currency": "EUR",
        "payment_method": "CARD",
        "totals": {"total_net": 0, "total_tax": 0, "total_gross": 0},
        "items": [],
        "source_file": {},
    }

    with pytest.raises(JsonValidationError):
        parse_and_validate_payload(payload)


def test_payload_normalizer_enriches_with_file_metadata():
    facts = FileFacts(filename="receipt.jpg", mime_type="image/jpeg", byte_size=123, sha256="deadbeef")
    normalizer = PayloadNormalizer(facts)
    raw = {
        "merchant": {
            "name": "famila Bielefeld",
            "address": {"street": "Hansestr. 1", "city": "Bielefeld", "postal_code": "33689"},
        },
        "items": [
            {"product_name": "Item A", "line_gross": 100, "tax": 0.19},
            {"product_name": "Item B", "line_gross": 200, "tax": 0.07},
        ],
        "currency": "eur",
        "payment_method": "card",
        "date": "20.09.2025",
    }

    normalized = normalizer.normalize(raw)

    assert normalized["source_file"]["filename"] == "receipt.jpg"
    assert normalized["totals"]["total_gross"] == 300
    assert normalized["totals"]["total_tax"] is not None
