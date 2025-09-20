"""Metadata extraction helpers for the orchestrator pipeline."""

from __future__ import annotations

import json
import re
from typing import Optional

from ..domain.models import ExtractedMetadata
from ..domain.normalize import detect_currency, normalize_amount, normalize_date_iso
from ..logging import get_logger

LOG = get_logger("orchestrator-metadata")

from ..domain.merchant import normalize_korrespondent


_TRANSCRIPT_BLACKLIST = {"kassenbon", "rechnung", "beleg", "bon"}
_AMOUNT_LABELS = ("summe", "gesamt", "total", "betrag", "zu zahlen", "payable")
_DATE_LABELS = ("datum", "rechnungsdatum", "ausgestellt", "kauf")


def _guess_merchant_from_transcript(text: str) -> str:
    lines = [ln.strip() for ln in re.split(r"[\r\n]+", text) if ln.strip()]
    for line in lines[:10]:
        lowered = re.sub(r"[^a-z0-9äöüß ]", "", line.lower())
        if not lowered:
            continue
        if any(token in lowered for token in _TRANSCRIPT_BLACKLIST):
            continue
        return line[:60]
    return "Unbekannt"


def _date_from_transcript(text: str) -> Optional[str]:
    for match in re.finditer(r"(\d{1,2}[./]\d{1,2}[./]\d{2,4})", text):
        iso = normalize_date_iso(match.group(1))
        if iso:
            return iso
    for match in re.finditer(r"(\d{4}-\d{2}-\d{2})", text):
        iso = normalize_date_iso(match.group(1))
        if iso:
            return iso
    return None


def _labeled_date(text: str) -> Optional[str]:
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        lowered = line.lower()
        if any(lbl in lowered for lbl in _DATE_LABELS):
            iso = _date_from_transcript(line)
            if iso:
                return iso
            if idx + 1 < len(lines):
                iso = _date_from_transcript(lines[idx + 1])
                if iso:
                    return iso
    return _date_from_transcript(text)


def _amount_from_transcript(text: str) -> Optional[str]:
    pattern = re.compile(r"(\d{1,3}(?:[.,]\d{3})*[.,]\d{2})")
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        lowered = line.lower()
        if any(label in lowered for label in _AMOUNT_LABELS):
            match = pattern.search(line)
            if match:
                norm = normalize_amount(match.group(1))
                if norm:
                    return norm
            if idx + 1 < len(lines):
                match = pattern.search(lines[idx + 1])
                if match:
                    norm = normalize_amount(match.group(1))
                    if norm:
                        return norm
    # Fallback: choose the numerically largest candidate from the entire text
    best_amount = None
    for match in pattern.finditer(text):
        value = normalize_amount(match.group(1))
        if value:
            if best_amount is None or float(value) >= float(best_amount):
                best_amount = value
    return best_amount


def _metadata_from_transcript(text: str) -> Optional[ExtractedMetadata]:
    if not text:
        return None
    merchant_guess = normalize_korrespondent(_guess_merchant_from_transcript(text))
    date_iso = _labeled_date(text) or "1970-01-01"
    amount = _amount_from_transcript(text) or "0.00"
    currency = detect_currency(text)

    if not merchant_guess:
        merchant_guess = "Unbekannt"

    md = ExtractedMetadata(
        korrespondent=merchant_guess,
        ausstellungsdatum=date_iso,
        betrag_value=amount,
        betrag_currency=currency,
    )
    LOG.info("Transcript heuristics succeeded; preview metadata follows")
    LOG.info(f"korrespondent     : {md.korrespondent}")
    LOG.info(f"ausstellungsdatum: {md.ausstellungsdatum}")
    LOG.info(f"betrag_value     : {md.betrag_value}")
    LOG.info(f"betrag_currency  : {md.betrag_currency}")
    LOG.info(f"title (preview)  : {md.title()}")
    if md.ausstellungsdatum == "1970-01-01":
        LOG.warning("Transcript heuristic fell back to 1970-01-01 for the date")
    if md.betrag_value == "0.00":
        LOG.warning("Transcript heuristic fell back to 0.00 for the amount")
    return md


def _metadata_via_registry(path: str, *, ollama_url: str, model: str) -> Optional[ExtractedMetadata]:
    try:
        from ..metadata.extractors import (
            ExtractionContext,
            extract_with_registry,
        )
    except Exception as exc:  # pragma: no cover - optional dependency missing
        LOG.debug(f"Metadata registry unavailable: {exc}")
        return None

    context = ExtractionContext(ollama_url=ollama_url, ollama_model=model)
    return extract_with_registry(path, context)



def extract_metadata(
    *,
    transcript: Optional[str],
    source_path: str,
    ollama_url: str,
    model: str,
) -> Optional[ExtractedMetadata]:
    """Best-effort metadata extraction for receipts.

    Order:
    1. Transcript heuristics (fast, no extra API calls).
    2. Registry-based PDF/image extractors (pluggable custom logic, includes LLM).
    """
    if transcript:
        LOG.info("Attempting transcript heuristic extraction")
        md = _metadata_from_transcript(transcript)
        if md:
            return md

    if source_path:
        LOG.info("Attempting registry-based extraction")
        md = _metadata_via_registry(source_path, ollama_url=ollama_url, model=model)
        if md:
            return md

    LOG.error("All metadata extraction strategies failed")
    return None
