"""Orchestration helper to transcribe receipt images via Ollama."""

from __future__ import annotations

from typing import Optional

from ..logging import get_logger

LOG = get_logger("orchestrator-transcribe")


def transcribe_image(
    image_path: str,
    *,
    ollama_url: str,
    model: str,
    timeout: int = 120,
) -> Optional[str]:
    """Return transcript text for the image using the legacy Ollama helper.

    Delegates to ollama_transcriber.transcribe_image_via_ollama so existing
    behaviour is retained while giving the orchestrator a single call-site.
    """
    try:
        from ollama_transcriber import transcribe_image_via_ollama  # type: ignore
    except Exception as exc:  # pragma: no cover - defensive fallback
        LOG.error(f"transcribe_image_via_ollama import failed: {exc}")
        return None

    LOG.info(
        "Transcribing image via Ollama",  # good logging context
    )
    LOG.debug(f"Image path: {image_path}")
    LOG.debug(f"Ollama URL: {ollama_url}; model: {model}; timeout: {timeout}s")

    text = transcribe_image_via_ollama(
        image_path=image_path,
        model=model,
        ollama_url=ollama_url,
        timeout=timeout,
    )
    if not text:
        LOG.error("Ollama transcription returned no text")
        return None
    LOG.info(f"Received transcript with {len(text)} characters")
    return text

