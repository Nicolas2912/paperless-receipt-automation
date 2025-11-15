import sys
import types

sys.modules.setdefault("fitz", types.SimpleNamespace())


class _HttpxClientStub:
    def __init__(self, *_, **__):
        pass

    def close(self):
        pass


class _HttpxTimeoutStub:
    def __init__(self, *_, **__):
        pass


class _HttpxLimitsStub:
    def __init__(self, *_, **__):
        pass


sys.modules.setdefault(
    "httpx",
    types.SimpleNamespace(
        Client=_HttpxClientStub,
        Timeout=_HttpxTimeoutStub,
        Limits=_HttpxLimitsStub,
        __version__="stub",
    ),
)


class _OpenAIStub:
    class OpenAI:  # pragma: no cover - simple import shim
        def __init__(self, *_, **__):
            pass

    class APIConnectionError(Exception):
        pass

    class APITimeoutError(Exception):
        pass

    class APIStatusError(Exception):
        pass


sys.modules.setdefault(
    "openai",
    types.SimpleNamespace(
        OpenAI=_OpenAIStub.OpenAI,
        APIConnectionError=_OpenAIStub.APIConnectionError,
        APITimeoutError=_OpenAIStub.APITimeoutError,
        APIStatusError=_OpenAIStub.APIStatusError,
    ),
)


def _load_dotenv_stub(*_, **__):
    return None


sys.modules.setdefault(
    "dotenv",
    types.SimpleNamespace(load_dotenv=_load_dotenv_stub),
)


class _StarletteStub:
    def __init__(self, *_, **__):
        pass


class _JSONResponseStub:
    def __init__(self, *_, **__):
        pass


class _RouteStub:
    def __init__(self, *_, **__):
        pass


starlette_module = types.ModuleType("starlette")
starlette_module.__path__ = []  # mark as package for import machinery
starlette_module.applications = types.SimpleNamespace(Starlette=_StarletteStub)
starlette_module.responses = types.SimpleNamespace(JSONResponse=_JSONResponseStub)
starlette_module.routing = types.SimpleNamespace(Route=_RouteStub, Mount=_RouteStub)
starlette_module.exceptions = types.SimpleNamespace(HTTPException=Exception)
starlette_module.middleware = types.SimpleNamespace(cors=types.SimpleNamespace(CORSMiddleware=_StarletteStub))
starlette_module.requests = types.SimpleNamespace(Request=_StarletteStub)
starlette_module.staticfiles = types.SimpleNamespace(StaticFiles=_StarletteStub)
sys.modules.setdefault("starlette", starlette_module)
sys.modules.setdefault("starlette.applications", starlette_module.applications)
sys.modules.setdefault("starlette.responses", starlette_module.responses)
sys.modules.setdefault("starlette.routing", starlette_module.routing)
sys.modules.setdefault("starlette.exceptions", starlette_module.exceptions)
sys.modules.setdefault("starlette.middleware", starlette_module.middleware)
sys.modules.setdefault("starlette.middleware.cors", starlette_module.middleware.cors)
sys.modules.setdefault("starlette.requests", starlette_module.requests)
sys.modules.setdefault("starlette.staticfiles", starlette_module.staticfiles)

from paperless_automation.orchestrator.productdb.extraction import (
    FileFacts,
    PayloadNormalizer,
    apply_focused_quantity_tax_overrides,
)


def _item(name: str, quantity: float = 1.0, tax_rate: float = 0.19):
    return {
        "product_name": name,
        "quantity": quantity,
        "unit_price_net": None,
        "unit_price_gross": None,
        "tax_rate": tax_rate,
        "line_net": None,
        "line_tax": None,
        "line_gross": None,
        "line_type": "NORMAL",
    }


def test_apply_focused_overrides_updates_quantity_and_tax():
    items = [_item("Apfel", quantity=1.0, tax_rate=0.19)]
    overrides = [
        {
            "product_name": "Apfel",
            "quantity": 2,
            "tax_rate": 0.07,
        }
    ]

    summary = apply_focused_quantity_tax_overrides(items, overrides)

    assert items[0]["quantity"] == 2.0
    assert items[0]["tax_rate"] == 0.07
    assert summary["updated_items"] == 1
    assert summary["unmatched_entries"] == 0


def test_apply_focused_overrides_handles_similar_names_and_umlauts():
    items = [_item("HÄHN. BRU.-F.TEILS-QS", quantity=1.0, tax_rate=0.19)]
    overrides = [
        {
            "product_name": "hahn bru f teils qs",
            "quantity": 3,
            "tax_rate": 0.07,
        }
    ]

    summary = apply_focused_quantity_tax_overrides(items, overrides)

    assert items[0]["quantity"] == 3.0
    assert items[0]["tax_rate"] == 0.07
    assert summary["updated_items"] == 1
    assert summary["details"][0]["matched_override_name"] == overrides[0]["product_name"]


def test_apply_focused_overrides_counts_unmatched_rows():
    items = [_item("Apfel")] 
    overrides = [
        {"product_name": "Unknown", "quantity": 4, "tax_rate": 0.19},
    ]

    summary = apply_focused_quantity_tax_overrides(items, overrides)

    assert items[0]["quantity"] == 1.0  # unchanged
    assert summary["updated_items"] == 0
    assert summary["unmatched_entries"] == 1


def test_apply_focused_overrides_respects_allowed_fields():
    items = [_item("Apfel", quantity=1.0, tax_rate=0.19)]
    overrides = [
        {"product_name": "Apfel", "quantity": 4, "tax_rate": 0.07},
    ]

    summary = apply_focused_quantity_tax_overrides(items, overrides, fields=("quantity",))

    assert items[0]["quantity"] == 4.0
    assert items[0]["tax_rate"] == 0.19  # unchanged because tax_rate not allowed
    assert "quantity" in summary["details"][0]["fields"]
    assert "tax_rate" not in summary["details"][0]["fields"]


def test_reconcile_after_overrides_recomputes_amounts():
    facts = FileFacts(filename="receipt.jpg", mime_type="image/jpeg", byte_size=None, sha256=None)
    payload = {
        "items": [
            {
                "product_name": "Apfel",
                "quantity": 2.0,
                "unit_price_net": 100,
                "unit_price_gross": 139,
                "tax_rate": 0.07,
                "line_net": 100,
                "line_tax": 7,
                "line_gross": 139,
                "line_type": "NORMAL",
            }
        ],
        "totals": {"total_gross": None, "total_net": None, "total_tax": None},
    }

    normalizer = PayloadNormalizer(facts)
    normalizer.reconcile_after_overrides(payload)

    item = payload["items"][0]
    assert item["line_gross"] == 278
    assert item["unit_price_gross"] == 139
