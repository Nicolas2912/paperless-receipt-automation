"""Filename management for the orchestrated receipt pipeline."""

from __future__ import annotations

import os
from typing import Any, Tuple

from ..domain.models import ExtractedMetadata
from ..logging import get_logger

LOG = get_logger("orchestrator-rename")


def _update_listener_baseline(listener: Any, new_path: str) -> None:
    if listener is None:
        return
    try:
        basename = os.path.basename(new_path)
        if hasattr(listener, "baseline") and isinstance(listener.baseline, set):
            listener.baseline.add(basename)
            LOG.debug(f"Watcher baseline updated with {basename}")
        if hasattr(listener, "last_new_image_path"):
            listener.last_new_image_path = new_path
    except Exception as exc:  # pragma: no cover - defensive
        LOG.warning(f"Failed to update watcher baseline after rename: {exc}")


def rename_receipt_files(
    image_path: str,
    pdf_path: str,
    metadata: ExtractedMetadata,
    *,
    listener: Any = None,
) -> Tuple[str, str]:
    """Rename image & PDF following the YYYY-MM-DD_Korrespondent_id pattern and update watcher."""
    try:
        from rename_documents import rename_with_metadata  # type: ignore
    except Exception as exc:  # pragma: no cover - defensive fallback
        LOG.error(f"rename_with_metadata import failed: {exc}")
        raise

    LOG.info("Renaming image and PDF based on extracted metadata")
    LOG.debug(f"Original image path: {image_path}")
    LOG.debug(f"Original pdf path:   {pdf_path}")

    new_image, new_pdf = rename_with_metadata(
        image_path,
        pdf_path,
        date_iso=metadata.ausstellungsdatum or "1970-01-01",
        korrespondent=metadata.korrespondent or "Unbekannt",
    )
    LOG.info(f"Renamed image path: {new_image}")
    LOG.info(f"Renamed pdf path:   {new_pdf}")

    _update_listener_baseline(listener, new_image)
    return new_image, new_pdf


def rename_pdf(
    pdf_path: str,
    metadata: ExtractedMetadata,
    *,
    listener: Any = None,
) -> str:
    """Rename a standalone PDF and optionally update the watcher baseline."""
    try:
        from rename_documents import rename_pdf_only  # type: ignore
    except Exception as exc:  # pragma: no cover - defensive fallback
        LOG.error(f"rename_pdf_only import failed: {exc}")
        raise

    LOG.info("Renaming PDF based on extracted metadata")
    LOG.debug(f"Original pdf path: {pdf_path}")

    new_pdf = rename_pdf_only(
        pdf_path,
        date_iso=metadata.ausstellungsdatum or "1970-01-01",
        korrespondent=metadata.korrespondent or "Unbekannt",
    )
    LOG.info(f"Renamed pdf path: {new_pdf}")

    _update_listener_baseline(listener, new_pdf)
    return new_pdf

