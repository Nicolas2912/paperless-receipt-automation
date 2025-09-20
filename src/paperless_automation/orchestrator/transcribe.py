"""Orchestration helper to transcribe receipt images via Ollama."""

from __future__ import annotations

import base64
import json
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

# Toggle default token echoing during streaming. Set to False to suppress
# live token output without modifying call sites.
STREAM_ECHO_DEFAULT: bool = False


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
    timeout: int = 300,
    instruction: str = DEFAULT_INSTRUCTION,
    echo: bool = STREAM_ECHO_DEFAULT,
) -> Optional[str]:
    """Return transcript text for the image using Ollama chat (vision).

    Uses streaming from Ollama's /api/chat endpoint, optionally echoing tokens
    to stdout as they arrive (controlled by `echo`). Concatenates streamed
    content and strips any trailing '<eot>' marker before returning.
    """
    url = ollama_url if ollama_url.endswith("/api/chat") else ollama_url.rstrip("/") + "/api/chat"
    LOG.info("Transcribing image via Ollama")
    LOG.debug(f"Image path: {image_path}")
    LOG.debug(f"Ollama URL: {url}; model: {model}; timeout: {timeout}s")

    img_b64 = _encode_image_b64(image_path)
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": instruction, "images": [img_b64]}],
        "stream": True,
        "options": {
            "num_predict": 2048,
            "temperature": 0,
            "stop": ["<eot>"],
        },
    }

    try:
        # Stream the response and echo tokens to console as they arrive
        if echo:
            LOG.info("Streaming transcription from Ollama (tokens will appear below)...")
        response = requests.post(url, json=payload, timeout=timeout, stream=True)
        response.raise_for_status()

        chunks: list[str] = []
        for raw_line in response.iter_lines(decode_unicode=False):
            if not raw_line:
                continue
            # Ensure text string regardless of server encoding
            if isinstance(raw_line, bytes):
                try:
                    line = raw_line.decode(response.encoding or "utf-8", errors="ignore")
                except Exception:
                    line = raw_line.decode("utf-8", errors="ignore")
            else:
                line = str(raw_line)
            line = line.strip()
            if line.startswith("data:"):
                line = line[5:].strip()
            try:
                obj = json.loads(line)
            except Exception:
                # If it's not JSON, optionally echo the raw line
                if echo:
                    print(line, end="", flush=True)
                chunks.append(line)
                continue

            if obj.get("error"):
                LOG.error(f"Ollama error: {obj['error']}")
                return None

            if obj.get("done") is True:
                break

            delta = ""
            msg = obj.get("message") or {}
            if isinstance(msg, dict):
                delta = msg.get("content") or ""
            if not delta:
                # Fallback for /api/generate-style events
                delta = obj.get("response") or ""
            if delta:
                if echo:
                    print(delta, end="", flush=True)
                chunks.append(delta)

        if echo:
            print()  # newline after streaming
        text = ("".join(chunks)).strip()
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
