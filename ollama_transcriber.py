import requests
import time
import base64
import json
import os
import re

# Shared logging & paths (Phase 1)
try:
    from src.paperless_automation.logging import get_logger  # type: ignore
    from src.paperless_automation.paths import fix_windows_path_input as _fix_input  # type: ignore
except Exception:
    def get_logger(name: str):  # type: ignore
        class _L:
            def info(self, m):
                print(f"[{name}] {m}")
            debug = info
            warning = info
            error = info
        return _L()
    def _fix_input(p: str) -> str:  # type: ignore
        return p

_LOG = get_logger("ollama-transcriber")


def debug_print(msg: str) -> None:
    _LOG.info(msg)


MODEL_NAME = "qwen2.5vl-receipt:latest"
OLLAMA_URL = "http://localhost:11434/api/chat"
INSTRUCTION = "Transcribe this receipt EXACTLY (spacing, order). Output plain text only. Keep german letters (like ä/ö/ü). When finished, print <eot> on a new line."


def encode_image(path: str) -> str:
    p = _fix_input(path)
    if not os.path.isabs(p):
        p = os.path.abspath(p)
    if not os.path.isfile(p):
        debug_print(f"ERROR: Image not found: {p}")
        raise FileNotFoundError(p)
    try:
        with open(p, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except OSError as e:
        debug_print(f"ERROR: Failed to read image '{p}': {e}")
        raise


def _fix_windows_path_input(p: str) -> str:
    # Backwards compatibility: delegate to centralized helper
    return _fix_input(p)


def transcribe_image_via_ollama(
    image_path: str,
    model: str = MODEL_NAME,
    ollama_url: str = OLLAMA_URL,
    instruction: str = INSTRUCTION,
    timeout: int = 120,
):
    """Stream a vision chat request to Ollama and return concatenated text.

    Mirrors the payload and streaming handling used by this script's main loop.
    Returns the text with a trailing <eot> marker removed, or None on error.
    """
    debug_print(f"Preparing Ollama request to {ollama_url} with model '{model}'")
    # Normalize URL to /api/chat
    url = ollama_url if ollama_url.endswith("/api/chat") else ollama_url.rstrip("/") + "/api/chat"
    img_path = _fix_input(image_path)
    img_b64 = encode_image(img_path)
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": instruction,
                "images": [img_b64],
            }
        ],
        "stream": True,
    }
    try:
        with requests.post(url, json=payload, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            parts = []
            for line in r.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = data.get("message", {})
                content = msg.get("content")
                if content:
                    parts.append(content)
            text = "".join(parts).strip()
            if "<eot>" in text:
                text = text.split("<eot>", 1)[0].strip()
            debug_print(f"Received {len(text)} chars from Ollama")
            return text or None
    except Exception as e:
        debug_print(f"Ollama request failed: {e}")
        return None


def main() -> None:
    # Import here so the module can be imported without this dependency
    try:
        from scan_event_listener import ScanEventListener as _ScanEventListener, debug_print as _debug_print
    except Exception:
        _ScanEventListener = None
        _debug_print = debug_print

    # Optional: local ollama package is not required for HTTP usage
    try:
        import ollama  # noqa: F401
    except Exception:
        pass

    _debug_print("Starting ollama-transcriber; initializing ScanEventListener")
    if _ScanEventListener is None:
        _debug_print("scan_event_listener not available; exiting main loop.")
        raise SystemExit(2)

    # Disable internal printing so we control output here
    listener = _ScanEventListener(print_on_detect=False)

    try:
        while True:
            new_paths = listener.scan_once()
            if new_paths:
                last_path = listener.get_last_new_image_path()
                debug_print(f"Last detected image path (from listener variable): {last_path}")
                print(f"Last detected image: {last_path}", flush=True)
                # Ollama
                img_b64 = encode_image(last_path)

                payload = {
                    "model": MODEL_NAME,
                    "messages": [
                        {
                            "role": "user",
                            "content": INSTRUCTION,
                            "images": [img_b64],
                        }
                    ],
                    # Set to True to get a single JSON reply instead of a server-sent event stream
                    "stream": True,
                }

                with requests.post(OLLAMA_URL, json=payload, stream=True) as r:
                    r.raise_for_status()
                    print("Assistant:")
                    for line in r.iter_lines(decode_unicode=True):
                        if not line:
                            continue
                        # Each line is a JSON object with partial response
                        data = json.loads(line)
                        msg = data.get("message", {})
                        content = msg.get("content")
                        if content:
                            print(content, end="", flush=True)
                    print()
            time.sleep(listener.poll_interval_sec)
    except KeyboardInterrupt:
        debug_print("Interrupted by user in ollama-transcriber. Exiting.")
        raise SystemExit(130)


if __name__ == "__main__":
    main()
