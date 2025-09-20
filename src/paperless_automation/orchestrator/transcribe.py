"""Orchestration helper to transcribe receipt images via Ollama."""

from __future__ import annotations

import base64
import os
from typing import Optional

import requests

from ..logging import get_logger
from ..paths import fix_windows_path_input as _fix_input

LOG = get_logger("orchestrator-transcribe")


DEFAULT_INSTRUCTION = (
    "Transcribe this receipt EXACTLY (spacing, order). Output plain text only. "
    "Keep german letters (like ä/ö/ü). When finished, print <eot> on a new line."
)


def _encode_image_b64(path: str) -> str:
    p = _fix_input(path)
    if not os.path.isabs(p):
        p = os.path.abspath(p)
    with open(p, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def transcribe_image(
    image_path: str,
    *,
    ollama_url: str,
    model: str,
    timeout: int = 120,
    instruction: str = DEFAULT_INSTRUCTION,
) -> Optional[str]:
    """Return transcript text for the image using Ollama chat (vision).

    Sends a single non-streaming request to Ollama's /api/chat endpoint and
    concatenates content. Strips any trailing '<eot>' marker.
    """
    url = ollama_url if ollama_url.endswith("/api/chat") else ollama_url.rstrip("/") + "/api/chat"
    LOG.info("Transcribing image via Ollama")
    LOG.debug(f"Image path: {image_path}")
    LOG.debug(f"Ollama URL: {url}; model: {model}; timeout: {timeout}s")

    img_b64 = _encode_image_b64(image_path)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": instruction, "images": [img_b64]}],
        "stream": False,
    }
    try:
        r = requests.post(url, json=payload, timeout=timeout)
        r.raise_for_status()
        data = r.json() or {}
        content = (data.get("message") or {}).get("content") or ""
        text = str(content).strip()
        if "<eot>" in text:
            text = text.split("<eot>", 1)[0].strip()
        if not text:
            LOG.error("Ollama returned empty content")
            return None
        LOG.info(f"Received transcript with {len(text)} characters")
        return text
    except Exception as exc:
        LOG.error(f"Ollama transcription failed: {exc}")
        return None
