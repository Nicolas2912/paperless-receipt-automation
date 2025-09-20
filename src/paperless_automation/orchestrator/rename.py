"""Filename management for the orchestrated receipt pipeline."""

from __future__ import annotations

import os
import re
from typing import Any, Iterable, Tuple

from ..domain.models import ExtractedMetadata
from ..logging import get_logger

LOG = get_logger("orchestrator-rename")


INVALID_WIN_CHARS = r'<>:"/\\|?*'
INVALID_RE = re.compile(f"[{re.escape(INVALID_WIN_CHARS)}]")
JPEG_EXTS = {".jpg", ".jpeg"}


def _sanitize_component(name: str) -> str:
    n = (name or "").strip()
    n = INVALID_RE.sub("", n)
    n = re.sub(r"\s+", " ", n).strip().replace(" ", "_")
    n = n.rstrip(". ")
    return n if n else "Unbekannt"


def _next_shared_id(
    image_dir: str,
    pdf_dir: str,
    date_iso: str,
    kor: str,
    image_ext: str,
) -> int:
    exts_to_check: Iterable[str] = set([image_ext.lower()]) | JPEG_EXTS
    ids = set()
    try:
        for name in os.listdir(image_dir):
            low = name.lower()
            for ext in exts_to_check:
                if not low.endswith(ext):
                    continue
                stem = name[: -len(ext)]
                m = re.fullmatch(rf"{re.escape(date_iso)}_{re.escape(kor)}_(\d+)", stem, flags=re.IGNORECASE)
                if m:
                    try:
                        ids.add(int(m.group(1)))
                    except Exception:
                        pass
        for name in os.listdir(pdf_dir):
            if not name.lower().endswith('.pdf'):
                continue
            stem = name[:-4]
            m = re.fullmatch(rf"{re.escape(date_iso)}_{re.escape(kor)}_(\d+)", stem, flags=re.IGNORECASE)
            if m:
                try:
                    ids.add(int(m.group(1)))
                except Exception:
                    pass
    except Exception:
        sid = 1
        while True:
            base = f"{date_iso}_{kor}_{sid}"
            variant_exists = any(
                os.path.exists(os.path.join(image_dir, base + ext)) for ext in exts_to_check
            )
            pdf_path = os.path.join(pdf_dir, base + ".pdf")
            if not variant_exists and not os.path.exists(pdf_path):
                return sid
            sid += 1

    if not ids:
        return 1
    return max(ids) + 1


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
    LOG.info("Renaming image and PDF based on extracted metadata")
    LOG.debug(f"Original image path: {image_path}")
    LOG.debug(f"Original pdf path:   {pdf_path}")

    image_dir = os.path.abspath(os.path.dirname(image_path))
    pdf_dir = os.path.abspath(os.path.dirname(pdf_path))
    kor_safe = _sanitize_component(metadata.korrespondent or "Unbekannt")
    _, img_ext = os.path.splitext(image_path)
    img_ext = img_ext or ".jpg"

    sid = _next_shared_id(image_dir, pdf_dir, metadata.ausstellungsdatum or "1970-01-01", kor_safe, img_ext.lower())
    base = f"{metadata.ausstellungsdatum or '1970-01-01'}_{kor_safe}_{sid}"
    new_image = os.path.join(image_dir, base + img_ext)
    new_pdf = os.path.join(pdf_dir, base + ".pdf")

    if os.path.abspath(image_path) != os.path.abspath(new_image):
        os.replace(image_path, new_image)
    if os.path.abspath(pdf_path) != os.path.abspath(new_pdf):
        os.replace(pdf_path, new_pdf)

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
    LOG.info("Renaming PDF based on extracted metadata")
    LOG.debug(f"Original pdf path: {pdf_path}")

    pdf_dir = os.path.abspath(os.path.dirname(pdf_path))
    kor_safe = _sanitize_component(metadata.korrespondent or "Unbekannt")
    date_iso = metadata.ausstellungsdatum or "1970-01-01"
    sid = _next_shared_id(pdf_dir, pdf_dir, date_iso, kor_safe, ".pdf")
    base = f"{date_iso}_{kor_safe}_{sid}"
    new_pdf = os.path.join(pdf_dir, base + ".pdf")

    if os.path.abspath(pdf_path) != os.path.abspath(new_pdf):
        os.replace(pdf_path, new_pdf)

    LOG.info(f"Renamed pdf path: {new_pdf}")
    _update_listener_baseline(listener, new_pdf)
    return new_pdf
