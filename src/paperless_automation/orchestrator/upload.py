"""Uploading logic for the orchestrated receipt pipeline."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..domain.models import ExtractedMetadata
from ..logging import get_logger
from ..paperless.client import PaperlessClient
from ..domain.merchant import resolve_tag_and_key

LOG = get_logger("orchestrator-upload")


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
            client_tags = PaperlessClient(base_url, token)
            tag_ids = client_tags.ensure_tags([tag_name])
            LOG.info(f"Ensured tag ids {tag_ids} for tag '{tag_name}'")
        else:
            LOG.info("No tag mapping found; proceeding without enforced tags")

    client = PaperlessClient(base_url, token)
    correspondent_id = client.ensure_correspondent(metadata.korrespondent)
    if correspondent_id:
        LOG.info(f"Ensured correspondent '{metadata.korrespondent}' -> id={correspondent_id}")
    else:
        LOG.warning(f"Could not ensure correspondent '{metadata.korrespondent}'")

    document_type_id = client.ensure_document_type(metadata.dokumenttyp)
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


def _extract_task_id(response: Dict[str, Any]) -> Optional[int]:
    body = response.get("json") if isinstance(response, dict) else None
    if isinstance(body, dict):
        task_id = body.get("task_id") or body.get("task")
        if isinstance(task_id, int):
            return task_id
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

    client = PaperlessClient(base_url, token, timeout=timeout, verify_tls=verify_tls)
    response = client.post_document(
        file_path=pdf_path,
        title=fields.get("title"),
        created=fields.get("created"),
        correspondent_id=fields.get("correspondent_id"),
        tag_ids=fields.get("tag_ids"),
        document_type_id=fields.get("document_type_id"),
    )

    doc_id = _extract_doc_id(response)
    task_id = _extract_task_id(response)

    if doc_id is None and fields.get("title"):
        attempts = 0
        max_attempts = 10
        wait_seconds = 1.5
        LOG.info("Document id not in response; polling Paperless for document creation")
        while attempts < max_attempts and not doc_id:
            attempts += 1
            if task_id:
                LOG.debug(f"Polling attempt {attempts}/{max_attempts} (task_id={task_id})")
            else:
                LOG.debug(f"Polling attempt {attempts}/{max_attempts}")
            time.sleep(wait_seconds)
            doc_id = client.find_document_by_title(fields["title"])
        if doc_id:
            LOG.info(f"Resolved document id after polling: {doc_id}")
        else:
            LOG.warning("Unable to resolve document id after polling")

    if doc_id and task_id:
        max_task_attempts = 20
        LOG.info(f"Waiting for Paperless task {task_id} to finish before applying overrides")
        for attempt in range(1, max_task_attempts + 1):
            task = client.get_task(task_id)
            status = (task or {}).get("status")
            LOG.debug(
                "Task %s status=%s (attempt %s/%s)",
                task_id,
                status,
                attempt,
                max_task_attempts,
            )
            if status in {"SUCCESS", "FAILURE", "REVOKED"}:
                break
            time.sleep(1.0)
        else:
            LOG.warning(f"Task {task_id} did not reach a terminal state before timeout")

    tag_ids = fields.get("tag_ids") or []
    if doc_id:
        patch_payload: Dict[str, Any] = {}
        if isinstance(tag_ids, list) and tag_ids:
            dedup: List[int] = []
            seen = set()
            for tag_id in tag_ids:
                if isinstance(tag_id, int) and tag_id not in seen:
                    seen.add(tag_id)
                    dedup.append(tag_id)
            if dedup:
                patch_payload["tags"] = dedup
                LOG.info(f"Preparing to enforce exact tag set {dedup} on document {doc_id}")

        if isinstance(fields.get("correspondent_id"), int):
            patch_payload["correspondent"] = int(fields["correspondent_id"])
        if isinstance(fields.get("document_type_id"), int):
            patch_payload["document_type"] = int(fields["document_type_id"])
        if fields.get("created"):
            patch_payload["created"] = fields["created"]
        if fields.get("title"):
            patch_payload["title"] = fields["title"]

        if patch_payload:
            LOG.info(f"Patching document {doc_id} with extracted metadata: {list(patch_payload.keys())}")
            client.patch_document(doc_id, patch_payload)
            doc_preview = client.get_document(doc_id) or {}
            LOG.debug(
                "Document %s after patch => correspondent=%s tags=%s document_type=%s",
                doc_id,
                doc_preview.get("correspondent"),
                doc_preview.get("tags"),
                doc_preview.get("document_type"),
            )
    elif isinstance(tag_ids, list) and tag_ids:
        dedup: List[int] = []
        seen = set()
        for tag_id in tag_ids:
            if isinstance(tag_id, int) and tag_id not in seen:
                seen.add(tag_id)
                dedup.append(tag_id)
        if dedup:
            LOG.warning(f"Could not enforce tag ids {dedup}; no document id available")

    original_filename = os.path.basename(pdf_path)
    return UploadResult(
        doc_id=doc_id,
        response=response,
        title=fields.get("title") or "",
        original_filename=original_filename,
    )
