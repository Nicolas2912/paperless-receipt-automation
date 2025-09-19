"""Pluggable metadata extractors for receipts and invoices.

Phase 4 of the refactor plan introduces a registry so new merchant- or
media-specific extractors can be added without editing the legacy scripts.
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Type

import requests

from ..domain.models import ExtractedMetadata
from ..domain.normalize import normalize_amount, normalize_date_iso
from ..logging import get_logger

LOG = get_logger("metadata-registry")
PDF_LOG = get_logger("metadata-pdf-rewe")
LLM_LOG = get_logger("metadata-llm")


@dataclass
class ExtractionContext:
    """Context shared across extractors.

    The cache allows extractors to share expensive intermediate results such as
    PDF text lines so they are only computed once per invocation.
    """

    ollama_url: str
    ollama_model: str
    timeout: int = 120
    vendor_hint: Optional[str] = None
    cache: Dict[str, Any] = field(default_factory=dict)


class BaseExtractor:
    """Interface for metadata extractors.

    Subclasses should implement :meth: and return None when the
    document does not match their rules so the registry can fall back to the
    next extractor.
    """

    media_types: Tuple[str, ...] = ("image", "pdf")

    def try_extract(self, path: str, context: ExtractionContext) -> Optional[ExtractedMetadata]:
        raise NotImplementedError


_REGISTRY: List[Tuple[str, Type[BaseExtractor]]] = []


def register(key: str, extractor_cls: Type[BaseExtractor]) -> None:
    """Register an extractor class under a descriptive key."""

    _REGISTRY.append((key, extractor_cls))
    LOG.debug(f"Registered extractor {key} -> {extractor_cls.__name__}")


def list_extractors() -> List[Tuple[str, Type[BaseExtractor]]]:
    return list(_REGISTRY)


def detect_media_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return "pdf"
    if ext in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
        return "image"
    # Default to image for unknown raster types; LLM extractor can still handle
    return "image"


def extract_with_registry(path: str, context: ExtractionContext) -> Optional[ExtractedMetadata]:
    media_type = detect_media_type(path)
    context.cache.setdefault("media_type", media_type)
    LOG.info(f"Starting metadata extraction via registry; media_type={media_type}")

    for key, extractor_cls in _REGISTRY:
        extractor = extractor_cls()
        if media_type not in extractor.media_types:
            LOG.debug(f"Skipping extractor {key}; unsupported media_type={media_type}")
            continue
        LOG.info(f"Trying extractor {key} for {os.path.basename(path)}")
        try:
            result = extractor.try_extract(path, context)
        except Exception as exc:
            LOG.error(f"Extractor {key} raised {exc.__class__.__name__}: {exc}")
            continue
        if result is None:
            LOG.debug(f"Extractor {key} yielded no metadata; continuing")
            continue
        LOG.info(f"Extractor {key} succeeded for {os.path.basename(path)}")
        return result

    LOG.warning("No extractor produced metadata")
    return None


# ---------------------------------------------------------------------------
# PDF utilities
# ---------------------------------------------------------------------------


def _get_pdf_lines(path: str, context: ExtractionContext) -> List[str]:
    cache_key = f"pdf_lines::{path}"
    if cache_key in context.cache:
        return context.cache[cache_key]
    try:
        import fitz  # PyMuPDF
    except Exception as exc:  # pragma: no cover - dependency missing at runtime
        PDF_LOG.error(f"PyMuPDF is required for PDF extraction: {exc}")
        raise

    lines: List[str] = []
    with fitz.open(path) as doc:
        PDF_LOG.info(f"Opened PDF with {doc.page_count} page(s)")
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            text = page.get_text("text") or ""
            page_lines = [ln.rstrip("\r") for ln in text.splitlines()]
            PDF_LOG.info(
                "Page %s extracted (lines=%s chars=%s)",
                page_index + 1,
                len(page_lines),
                len(text),
            )
            lines.extend(page_lines)
    context.cache[cache_key] = lines
    return lines
    lines: List[str] = []
    with fitz.open(path) as doc:
        PDF_LOG.info(f"Opened PDF with {doc.page_count} page(s)")
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            text = page.get_text("text") or ""
            page_lines = [ln.rstrip("\r") for ln in text.splitlines()]
            PDF_LOG.info(
                "Page %s extracted (lines=%s chars=%s)",
                page_index + 1,
                len(page_lines),
                len(text),
            )
            lines.extend(page_lines)
    context.cache[cache_key] = lines
    return lines


def _find_first_date(lines: List[str], labels: Tuple[str, ...]) -> Optional[str]:
    date_pattern = re.compile(r"(\d{1,2})[./](\d{1,2})[./](\d{2,4})|\b(\d{4})-(\d{2})-(\d{2})\b")
    for label in labels:
        lab_pat = re.compile(re.escape(label), re.IGNORECASE)
        for idx, line in enumerate(lines):
            if not lab_pat.search(line):
                continue
            PDF_LOG.debug(f"Label '{label}' found on line {idx+1}: {line!r}")
            match = date_pattern.search(line)
            if match:
                iso = normalize_date_iso(match.group(0))
                PDF_LOG.debug(f"Date on same line: {match.group(0)!r} -> {iso}")
                if iso:
                    return iso
            if idx + 1 < len(lines):
                next_line = lines[idx + 1]
                match_next = date_pattern.search(next_line)
                if match_next:
                    iso = normalize_date_iso(match_next.group(0))
                    PDF_LOG.debug(f"Date on next line: {match_next.group(0)!r} -> {iso}")
                    if iso:
                        return iso
    return None


def _find_first_amount(lines: List[str], labels: Tuple[str, ...]) -> Optional[str]:
    amount_pattern = re.compile(r"(?<!\d)(\d{1,3}(?:[.,]\d{3})*[.,]\d{2})(?!\d)")
    for label in labels:
        lab_pat = re.compile(re.escape(label), re.IGNORECASE)
        for idx, line in enumerate(lines):
            if not lab_pat.search(line):
                continue
            PDF_LOG.debug(f"Label '{label}' found on line {idx+1}: {line!r}")
            match = amount_pattern.search(line)
            if match:
                norm = normalize_amount(match.group(1))
                PDF_LOG.debug(f"Amount on same line: {match.group(1)!r} -> {norm}")
                if norm:
                    return norm
            if idx + 1 < len(lines):
                next_line = lines[idx + 1]
                match_next = amount_pattern.search(next_line)
                if match_next:
                    norm = normalize_amount(match_next.group(1))
                    PDF_LOG.debug(f"Amount on next line: {match_next.group(1)!r} -> {norm}")
                    if norm:
                        return norm
    return None


class RewePdfExtractor(BaseExtractor):
    media_types = ("pdf",)

    def try_extract(self, path: str, context: ExtractionContext) -> Optional[ExtractedMetadata]:
        lines = _get_pdf_lines(path, context)
        if not lines:
            PDF_LOG.warning("PDF contains no text lines")
            return None

        content = "\n".join(lines)
        if not re.search(r"rewe", content, re.IGNORECASE):
            PDF_LOG.debug("REWE keyword not detected; skipping extractor")
            return None

        korrespondent = "REWE"
        try:
            from merchant_normalization import normalize_korrespondent  # type: ignore

            korrespondent = normalize_korrespondent(korrespondent)
        except Exception as exc:
            PDF_LOG.debug(f"Could not normalize korrespondent: {exc}")

        ausstellungsdatum = _find_first_date(lines, ("Rechnungsdatum",)) or "1970-01-01"
        betrag_value = _find_first_amount(lines, ("Summe", "ZU ZAHLEN")) or "0.00"

        metadata = ExtractedMetadata(
            korrespondent=korrespondent or "Unbekannt",
            ausstellungsdatum=ausstellungsdatum,
            betrag_value=betrag_value,
            betrag_currency="EUR",
            dokumenttyp="Rechnung",
        )

        PDF_LOG.info("[PDF] korrespondent     : %s", metadata.korrespondent)
        PDF_LOG.info("[PDF] ausstellungsdatum: %s", metadata.ausstellungsdatum)
        PDF_LOG.info("[PDF] betrag_value     : %s", metadata.betrag_value)
        PDF_LOG.info("[PDF] betrag_currency  : %s", metadata.betrag_currency)
        PDF_LOG.info("[PDF] dokumenttyp      : %s", metadata.dokumenttyp)
        PDF_LOG.info("[PDF] title (preview)  : %s", metadata.title())
        if metadata.ausstellungsdatum == "1970-01-01":
            PDF_LOG.warning("[PDF] ausstellungsdatum fallback in use (1970-01-01)")
        if metadata.betrag_value == "0.00":
            PDF_LOG.warning("[PDF] betrag_value fallback in use (0.00)")
        return metadata
