"""Uploading logic for the orchestrated receipt pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..domain.models import ExtractedMetadata
from ..logging import get_logger
from ..paperless.client import PaperlessClient

LOG = get_logger("orchestrator-upload")

try:
    from merchant_normalization import resolve_tag_and_key  # type: ignore
except Exception:  # pragma: no cover - fallback
    def resolve_tag_and_key(tag_map: Dict[str, str], extracted_name: str):  # type: ignore
        return "NO TAG FOUND", None

try:
    from upload_paperless import (
        upload_document,
        ensure_correspondent_id,
        ensure_document_type_id,
        ensure_tag_ids,
    )  # type: ignore
except Exception as exc:  # pragma: no cover - defensive fallback
    raise RuntimeError(f"upload_paperless helpers unavailable: {exc}")


@dataclass
class UploadResult:
    doc_id: Optional[int]
    response: Dict[str, Any]
    title: str
    original_filename: str


def prepare_upload_fields(
    metadata: ExtractedMetadata,
    *,
    base_url: str,
    token: str,
    tag_map: Dict[str, str] | None,
) -> Dict[str, Any]:
    """Return fields required for uploading and mutate metadata in-place if needed."""
    LOG.info("Preparing upload fields from extracted metadata")
    tag_ids: List[int] = []
    if tag_map:
        tag_name, matched_key = resolve_tag_and_key(tag_map, metadata.korrespondent)
        LOG.debug(f"Tag mapping resolve -> tag='{tag_name}', matched_key='{matched_key}'")
        if matched_key:
            metadata.korrespondent = matched_key
            LOG.info(f"Updated korrespondent to canonical tag key: {matched_key}")
        if tag_name and tag_name != "NO TAG FOUND":
            tag_ids = ensure_tag_ids(base_url, token, [tag_name])
            LOG.info(f"Ensured tag ids {tag_ids} for tag '{tag_name}'")
        else:
            LOG.info("No tag mapping found; proceeding without enforced tags")

    correspondent_id = ensure_correspondent_id(base_url, token, metadata.korrespondent)
    if correspondent_id:
        LOG.info(f"Ensured correspondent '{metadata.korrespondent}' -> id={correspondent_id}")
    else:
        LOG.warning(f"Could not ensure correspondent '{metadata.korrespondent}'")

    document_type_id = ensure_document_type_id(base_url, token, metadata.dokumenttyp)
    if document_type_id:
        LOG.info(f"Ensured document type '{metadata.dokumenttyp}' -> id={document_type_id}")
    else:
        LOG.warning(f"Could not ensure document type '{metadata.dokumenttyp}'")

    fields = {
        "title": metadata.title(),
        "created": metadata.ausstellungsdatum,
        "correspondent_id": correspondent_id,
        "document_type_id": document_type_id,
        "tag_ids": tag_ids,
    }
    LOG.info(f"Upload title will be '{fields['title']}'")
    return fields


def _extract_doc_id(response: Dict[str, Any]) -> Optional[int]:
    body = response.get("json") if isinstance(response, dict) else None
    if isinstance(body, dict):
        if isinstance(body.get("id"), int):
            return body["id"]
        if isinstance(body.get("document"), dict) and isinstance(body["document"].get("id"), int):
            return body["document"]["id"]
        if isinstance(body.get("results"), list) and body["results"]:
            first = body["results"][0]
            if isinstance(first, dict) and isinstance(first.get("id"), int):
                return first["id"]
    return None


def upload_pdf_document(
    pdf_path: str,
    *,
    base_url: str,
    token: str,
    fields: Dict[str, Any],
    insecure: bool = False,
    timeout: int = 60,
) -> UploadResult:
    """Upload the PDF and return enriched metadata about the operation."""
    verify_tls = not insecure
    LOG.info("Uploading PDF to Paperless")
    LOG.debug(f"PDF path: {pdf_path}")

    response = upload_document(
        file_path=pdf_path,
        base_url=base_url,
        token=token,
        title=fields.get("title"),
        created=fields.get("created"),
        correspondent_id=fields.get("correspondent_id"),
        tag_ids=fields.get("tag_ids"),
        document_type_id=fields.get("document_type_id"),
        timeout=timeout,
        verify_tls=verify_tls,
    )

    doc_id = _extract_doc_id(response)
    client = PaperlessClient(base_url, token, timeout=timeout, verify_tls=verify_tls)

    if doc_id is None and fields.get("title"):
        LOG.info("Document id not in response; searching by title")
        doc_id = client.find_document_by_title(fields["title"])
        if doc_id:
            LOG.info(f"Resolved document id by title search: {doc_id}")
        else:
            LOG.warning("Still no document id after title search")

    tag_ids = fields.get("tag_ids") or []
    if doc_id and isinstance(tag_ids, list) and tag_ids:
        dedup: List[int] = []
        seen = set()
        for tag_id in tag_ids:
            if isinstance(tag_id, int) and tag_id not in seen:
                seen.add(tag_id)
                dedup.append(tag_id)
        if dedup:
            LOG.info(f"Enforcing exact tag set {dedup} on document {doc_id}")
            client.patch_document(doc_id, {"tags": dedup})

    original_filename = os.path.basename(pdf_path)
    return UploadResult(
        doc_id=doc_id,
        response=response,
        title=fields.get("title") or "",
        original_filename=original_filename,
    )

