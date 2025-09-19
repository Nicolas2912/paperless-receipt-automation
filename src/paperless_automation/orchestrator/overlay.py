"""Create searchable PDFs with invisible text for the orchestrator."""

from __future__ import annotations

import os
from typing import Optional

from ..logging import get_logger

LOG = get_logger("orchestrator-overlay")


def create_searchable_pdf(
    image_path: str,
    transcript: str,
    output_dir: str,
) -> Optional[str]:
    """Return the path to a PDF containing the image + invisible transcript text."""
    try:
        from preconsume_overlay_pdf import (
            create_pdf_with_invisible_text,
            ensure_dir,
            unique_path,
        )
    except Exception as exc:  # pragma: no cover - defensive fallback
        LOG.error(f"preconsume_overlay_pdf import failed: {exc}")
        return None

    ensure_dir(output_dir)
    stem = os.path.splitext(os.path.basename(image_path))[0]
    candidate = os.path.join(output_dir, f"{stem}.pdf")
    pdf_path = unique_path(candidate)

    LOG.info(f"Creating searchable PDF at {pdf_path}")
    try:
        create_pdf_with_invisible_text(image_path, transcript, pdf_path)
    except Exception as exc:
        LOG.error(f"PDF creation failed: {exc}")
        return None

    LOG.info("Searchable PDF created successfully")
    return pdf_path

