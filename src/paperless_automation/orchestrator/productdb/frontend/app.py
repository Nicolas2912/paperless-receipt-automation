from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ....logging import get_logger
from ....paths import find_project_root
from ..db import ProductDatabase


LOG = get_logger("productdb-frontend")

DEFAULT_STATIC_SUBDIR = os.path.join("frontend", "productdb-ui", "dist")


def _parse_int(value: Optional[str], *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


def _normalise_direction(value: Optional[str]) -> str:
    if value and value.lower() == "asc":
        return "asc"
    return "desc"


def create_app(
    root_dir: Optional[str] = None,
    *,
    static_dir: Optional[str] = None,
    allow_origins: Optional[List[str]] = None,
    serve_static: bool = True,
) -> Starlette:
    """Create a Starlette app exposing the product DB API and optional frontend."""

    project_root = find_project_root(root_dir)
    db = ProductDatabase(root_dir=project_root)

    resolved_static_dir: Optional[str] = None
    if serve_static:
        if static_dir is not None:
            candidate = os.path.abspath(os.path.join(project_root, static_dir))
        else:
            candidate = os.path.abspath(os.path.join(project_root, DEFAULT_STATIC_SUBDIR))
        if os.path.isdir(candidate):
            resolved_static_dir = candidate
            LOG.info("Serving static frontend from %s", resolved_static_dir)
        else:
            LOG.warning("Frontend build not found at %s; API will run without static assets.", candidate)
    else:
        LOG.info("Static frontend serving disabled (API only mode).")

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "db_path": db.db_path})

    async def summary(request: Request) -> JSONResponse:
        qp = request.query_params
        date_from = qp.get("from") or qp.get("date_from")
        date_to = qp.get("to") or qp.get("date_to")
        try:
            payload = db.fetch_summary(date_from=date_from, date_to=date_to)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(payload)

    async def receipts(request: Request) -> JSONResponse:
        qp = request.query_params
        limit = _parse_int(qp.get("limit"), default=25, minimum=1, maximum=200)
        page = _parse_int(qp.get("page"), default=0, minimum=0, maximum=100_000)
        offset = limit * page
        merchant_raw = qp.get("merchant_id")
        merchant_id = None
        if merchant_raw is not None:
            try:
                merchant_id = int(merchant_raw)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Invalid merchant_id") from exc
        sort = qp.get("sort") or "purchase_date_time"
        direction = _normalise_direction(qp.get("direction"))
        search = qp.get("search") or None
        payload = db.fetch_receipts_overview(
            limit=limit,
            offset=offset,
            search=search,
            merchant_id=merchant_id,
            sort=sort,
            direction=direction,
        )
        payload.update({"page": page})
        return JSONResponse(payload)

    async def receipt_detail(request: Request) -> JSONResponse:
        receipt_id = int(request.path_params["receipt_id"])
        payload = db.fetch_receipt_detail(receipt_id)
        if payload is None:
            raise HTTPException(status_code=404, detail="Receipt not found")
        return JSONResponse(payload)

    async def merchants(_: Request) -> JSONResponse:
        return JSONResponse({"items": db.fetch_merchants_overview()})

    async def spend_timeseries(request: Request) -> JSONResponse:
        qp = request.query_params
        date_from = qp.get("from") or qp.get("date_from")
        date_to = qp.get("to") or qp.get("date_to")
        try:
            payload = db.fetch_spend_timeseries(date_from=date_from, date_to=date_to)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(payload)

    async def merchant_spend(request: Request) -> JSONResponse:
        qp = request.query_params
        date_from = qp.get("from") or qp.get("date_from")
        date_to = qp.get("to") or qp.get("date_to")
        limit = _parse_int(qp.get("limit"), default=10, minimum=1, maximum=100)
        try:
            payload = db.fetch_merchant_spend(date_from=date_from, date_to=date_to, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(payload)

    async def monthly_spend(request: Request) -> JSONResponse:
        qp = request.query_params
        date_from = qp.get("from") or qp.get("date_from")
        date_to = qp.get("to") or qp.get("date_to")
        try:
            payload = db.fetch_monthly_spend(date_from=date_from, date_to=date_to)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(payload)

    async def table_rows(request: Request) -> JSONResponse:
        table = request.path_params["table"]
        qp = request.query_params
        limit = _parse_int(qp.get("limit"), default=100, minimum=1, maximum=500)
        offset = _parse_int(qp.get("offset"), default=0, minimum=0, maximum=1_000_000)
        try:
            payload = db.fetch_table_rows(table, limit=limit, offset=offset)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(payload)

    routes = [
        Route("/api/health", health, methods=["GET"]),
        Route("/api/summary", summary, methods=["GET"]),
        Route("/api/receipts", receipts, methods=["GET"]),
        Route("/api/receipts/{receipt_id:int}", receipt_detail, methods=["GET"]),
        Route("/api/merchants", merchants, methods=["GET"]),
        Route("/api/timeseries/spend", spend_timeseries, methods=["GET"]),
        Route("/api/analytics/merchant_spend", merchant_spend, methods=["GET"]),
        Route("/api/analytics/monthly_spend", monthly_spend, methods=["GET"]),
        Route("/api/tables/{table:str}", table_rows, methods=["GET"]),
    ]

    app = Starlette(debug=False, routes=routes)

    origins = allow_origins or ["http://localhost:5173", "http://127.0.0.1:5173"]
    if "*" in origins:
        cors_allow_origins = ["*"]
    else:
        cors_allow_origins = origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_allow_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if resolved_static_dir:
        app.mount("/", StaticFiles(directory=resolved_static_dir, html=True), name="frontend")
    elif serve_static:
        async def missing_frontend(_: Request) -> JSONResponse:
            return JSONResponse(
                {
                    "detail": "Frontend build missing. Run 'npm install' and 'npm run build' under frontend/productdb-ui/.",
                },
                status_code=503,
            )

        app.add_route("/", missing_frontend, methods=["GET"])
        app.add_route("/{path:path}", missing_frontend, methods=["GET"])
    else:
        async def api_only(_: Request) -> JSONResponse:
            return JSONResponse(
                {
                    "detail": "Product DB API is running. Static frontend disabled (serve_static=False).",
                }
            )

        app.add_route("/", api_only, methods=["GET"])
        app.add_route("/{path:path}", api_only, methods=["GET"])

    return app


__all__ = ["create_app"]
