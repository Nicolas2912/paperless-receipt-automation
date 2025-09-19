"""Orchestrated end-to-end flow for the paperless receipt pipeline."""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from ..config import (
    load_base_url as _cfg_load_base_url,
    load_ollama as _cfg_load_ollama,
    load_tag_map as _cfg_load_tag_map,
    load_token as _cfg_load_token,
)
from ..logging import get_logger
from ..paths import expand_abs
from . import (
    ProcessedIndex,
    create_searchable_pdf,
    extract_metadata,
    prepare_upload_fields,
    rename_pdf,
    rename_receipt_files,
    transcribe_image,
    upload_pdf_document,
)

from scan_event_listener import (
    ScanEventListener,
    list_basenames_in_dir_by_ext,
    read_watch_dir_from_file,
)


LOG = get_logger("orchestrator-flow")


@dataclass
class FlowConfig:
    base_url: str
    token: str
    ollama_url: str
    ollama_model: str
    output_dir: str
    insecure: bool
    timeout: int
    tag_map: Dict[str, str]
    index: ProcessedIndex
    script_dir: str


def build_flow_config(args, *, script_dir: str) -> FlowConfig:
    """Create a FlowConfig from CLI args while logging helpful diagnostics."""

    token = getattr(args, "token", None) or _cfg_load_token(script_dir)
    if not token:
        LOG.error("PAPERLESS_TOKEN missing. Provide --token or set it in env/.env.")
        raise SystemExit(1)

    base_url = getattr(args, "base_url", None) or _cfg_load_base_url(script_dir)
    ollama_url_default, ollama_model_default = _cfg_load_ollama(script_dir)
    ollama_url = getattr(args, "ollama_url", None) or ollama_url_default
    ollama_model = getattr(args, "ollama_model", None) or ollama_model_default

    output_dir = expand_abs(getattr(args, "output_dir", None) or "generated_pdfs")
    os.makedirs(output_dir, exist_ok=True)

    tag_map = _cfg_load_tag_map(script_dir)
    index = ProcessedIndex(script_dir)

    LOG.info("Flow configuration prepared")
    LOG.info(f"Paperless base URL : {base_url}")
    LOG.info(f"Ollama URL         : {ollama_url}")
    LOG.info(f"Ollama model       : {ollama_model}")
    LOG.info(f"Output directory   : {output_dir}")
    LOG.info(f"TLS verification   : {not getattr(args, 'insecure', False)}")
    LOG.info(f"HTTP timeout       : {getattr(args, 'timeout', 60)}s")
    LOG.info(f"Tag map entries    : {len(tag_map)}")

    return FlowConfig(
        base_url=base_url,
        token=token,
        ollama_url=ollama_url,
        ollama_model=ollama_model,
        output_dir=output_dir,
        insecure=bool(getattr(args, "insecure", False)),
        timeout=int(getattr(args, "timeout", 60)),
        tag_map=tag_map,
        index=index,
        script_dir=script_dir,
    )


class ReceiptFlow:
    """High-level orchestrator that wires the individual pipeline services."""

    def __init__(self, config: FlowConfig) -> None:
        self.config = config
        LOG.info("ReceiptFlow orchestrator ready")
        LOG.info(f"Index database path: {self.config.index.db_path}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _pretty_print_response(response: Dict[str, object]) -> None:
        try:
            import json

            LOG.debug(json.dumps(response, ensure_ascii=False, indent=2))
        except Exception:
            LOG.debug(str(response))

    def _mark_processed(
        self,
        *,
        file_hash: Optional[str],
        file_path: str,
        upload_title: str,
        upload_doc_id: Optional[int],
        original_filename: str,
    ) -> None:
        if not file_hash:
            LOG.warning("Skipping processed-index record; file hash unavailable")
            return
        try:
            self.config.index.mark_processed(
                file_hash=file_hash,
                file_path=file_path,
                original_filename=original_filename,
                doc_id=upload_doc_id,
                title=upload_title,
            )
        except Exception as exc:
            LOG.warning(f"Failed to record processed item: {exc}")

    # ------------------------------------------------------------------
    # Core processing steps
    # ------------------------------------------------------------------
    def _preflight_hash(self, path: str) -> Tuple[Optional[str], bool]:
        file_hash: Optional[str] = None
        try:
            file_hash = self.config.index.compute_hash(path)
            if self.config.index.is_processed(file_hash):
                LOG.info("Already processed per index; marking seen and skipping upload")
                self.config.index.mark_seen(file_hash)
                return file_hash, True
        except Exception as exc:
            LOG.warning(f"Hash pre-check failed (continuing): {exc}")
        return file_hash, False

    def process_image(
        self,
        image_path: str,
        *,
        listener: Optional[ScanEventListener] = None,
    ) -> Optional[str]:
        LOG.info(f"=== Processing image: {image_path}")
        file_hash, skip = self._preflight_hash(image_path)
        if skip:
            return None

        transcript = transcribe_image(
            image_path,
            ollama_url=self.config.ollama_url,
            model=self.config.ollama_model,
        )
        if not transcript:
            LOG.error("Transcription returned no text; aborting pipeline for this file")
            return None

        pdf_path = create_searchable_pdf(image_path, transcript, self.config.output_dir)
        if not pdf_path:
            LOG.error("PDF overlay creation failed; aborting pipeline for this file")
            return None

        metadata = extract_metadata(
            transcript=transcript,
            source_path=pdf_path,
            ollama_url=self.config.ollama_url,
            model=self.config.ollama_model,
        )
        if metadata is None:
            LOG.error("Metadata extraction failed; aborting pipeline for this file")
            return None

        fields = prepare_upload_fields(
            metadata,
            base_url=self.config.base_url,
            token=self.config.token,
            tag_map=self.config.tag_map,
        )

        try:
            new_image_path, new_pdf_path = rename_receipt_files(
                image_path,
                pdf_path,
                metadata,
                listener=listener,
            )
        except Exception as exc:
            LOG.error(f"Renaming failed: {exc}")
            return None

        result = upload_pdf_document(
            new_pdf_path,
            base_url=self.config.base_url,
            token=self.config.token,
            fields=fields,
            insecure=self.config.insecure,
            timeout=self.config.timeout,
        )

        LOG.info(f"Upload HTTP status: {result.response.get('status_code')}")
        self._pretty_print_response(result.response)

        self._mark_processed(
            file_hash=file_hash,
            file_path=new_image_path,
            upload_title=result.title,
            upload_doc_id=result.doc_id,
            original_filename=result.original_filename,
        )

        return new_pdf_path

    def process_pdf(
        self,
        pdf_path: str,
        *,
        listener: Optional[ScanEventListener] = None,
    ) -> Optional[str]:
        LOG.info(f"=== Processing PDF: {pdf_path}")
        file_hash, skip = self._preflight_hash(pdf_path)
        if skip:
            return None

        metadata = extract_metadata(
            transcript=None,
            source_path=pdf_path,
            ollama_url=self.config.ollama_url,
            model=self.config.ollama_model,
        )
        if metadata is None:
            LOG.error("Metadata extraction failed for PDF; aborting")
            return None

        fields = prepare_upload_fields(
            metadata,
            base_url=self.config.base_url,
            token=self.config.token,
            tag_map=self.config.tag_map,
        )

        try:
            new_pdf_path = rename_pdf(
                pdf_path,
                metadata,
                listener=listener,
            )
        except Exception as exc:
            LOG.error(f"PDF rename failed: {exc}")
            return None

        result = upload_pdf_document(
            new_pdf_path,
            base_url=self.config.base_url,
            token=self.config.token,
            fields=fields,
            insecure=self.config.insecure,
            timeout=self.config.timeout,
        )

        LOG.info(f"Upload HTTP status: {result.response.get('status_code')}")
        self._pretty_print_response(result.response)

        self._mark_processed(
            file_hash=file_hash,
            file_path=new_pdf_path,
            upload_title=result.title,
            upload_doc_id=result.doc_id,
            original_filename=result.original_filename,
        )

        return new_pdf_path

    def process_source(
        self,
        source_path: str,
        *,
        listener: Optional[ScanEventListener] = None,
    ) -> Optional[str]:
        ext = os.path.splitext(source_path)[1].lower()
        LOG.debug(f"Dispatching source '{source_path}' with extension '{ext}'")
        if ext == ".pdf":
            return self.process_pdf(source_path, listener=listener)
        return self.process_image(source_path, listener=listener)

    # ------------------------------------------------------------------
    # Modes
    # ------------------------------------------------------------------
    def run_single(self, source: str) -> Optional[str]:
        if not source:
            LOG.error("--source is required for single mode")
            raise SystemExit(2)

        expanded = expand_abs(source)
        if not os.path.isfile(expanded):
            LOG.error(f"Source path not found: {expanded}")
            raise SystemExit(2)

        LOG.info(f"Running single-file mode for {expanded}")
        return self.process_source(expanded)

    def _iter_backlog_paths(self, listener: ScanEventListener) -> Tuple[str, ...]:
        names = list_basenames_in_dir_by_ext(listener.watch_dir, listener.exts)
        paths = tuple(os.path.join(listener.watch_dir, name) for name in sorted(names))
        LOG.info(f"Found {len(paths)} backlog item(s) to process")
        return paths

    def _resolve_watch_dir(self, override: Optional[str]) -> str:
        if override:
            resolved = expand_abs(override)
        else:
            resolved = read_watch_dir_from_file()
        LOG.info(f"Resolved watch directory: {resolved}")
        return resolved

    def run_watch(self, *, watch_dir: Optional[str] = None) -> None:
        resolved_watch_dir = self._resolve_watch_dir(watch_dir)
        listener = ScanEventListener(
            watch_dir=resolved_watch_dir,
            print_on_detect=True,
        )

        self.config.index.initial_sync_if_needed(
            watch_dir=listener.watch_dir,
            base_url=self.config.base_url,
            token=self.config.token,
        )

        LOG.info("Processing backlog before entering watch loop")
        for path in self._iter_backlog_paths(listener):
            try:
                self.process_source(path, listener=listener)
            except Exception as exc:
                LOG.exception(f"Backlog processing failed for {path}: {exc}")

        LOG.info("Starting watch loop; press Ctrl+C to exit")
        try:
            while True:
                new_paths = listener.scan_once()
                for path in new_paths:
                    try:
                        self.process_source(path, listener=listener)
                    except Exception as exc:
                        LOG.exception(f"Processing failed for {path}: {exc}")
                time.sleep(listener.poll_interval_sec)
        except KeyboardInterrupt:
            LOG.info("Interrupted by user; exiting watch mode")


def log_environment_banner() -> None:
    """Print environment information relevant for debugging runs."""

    LOG.info("Starting main_paperless_flow orchestrator")
    LOG.info(f"Working directory: {os.getcwd()}")
    LOG.info(f"Python executable: {sys.executable}")
    LOG.info(f"Conda env: {os.environ.get('CONDA_DEFAULT_ENV')}")

