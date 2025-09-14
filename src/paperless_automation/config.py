import json
import os
from typing import Dict, Optional, Tuple

from .logging import get_logger

log = get_logger("config")


def _read_dotenv(dotenv_dir: str) -> Dict[str, str]:
    """Minimal .env reader (no external dependencies).

    - Reads key=value pairs, ignores comments (#/;) and blank lines.
    - Trims single/double quotes around the value.
    - Returns mapping; does not mutate environment.
    """
    env: Dict[str, str] = {}
    path = os.path.join(os.path.abspath(dotenv_dir), ".env")
    if not os.path.isfile(path):
        log.debug(f"No .env found at: {path}")
        return env
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or line.startswith(";"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                    v = v[1:-1]
                env[k] = v.strip()
        log.debug(f"Loaded {len(env)} key(s) from .env")
    except Exception as e:
        log.warning(f"Failed reading .env: {e}")
    return env


def load_token(dotenv_dir: str) -> Optional[str]:
    tok = os.environ.get("PAPERLESS_TOKEN")
    if tok:
        log.info("Using PAPERLESS_TOKEN from environment")
        return tok.strip()
    env = _read_dotenv(dotenv_dir)
    v = env.get("PAPERLESS_TOKEN")
    if v:
        log.info("Loaded PAPERLESS_TOKEN from .env file")
        return v.strip()
    log.debug("PAPERLESS_TOKEN not found in env or .env")
    return None


def load_base_url(dotenv_dir: str, fallback: str = "http://localhost:8000") -> str:
    v = os.environ.get("PAPERLESS_BASE_URL")
    if v:
        return v.strip()
    env = _read_dotenv(dotenv_dir)
    return (env.get("PAPERLESS_BASE_URL") or fallback).strip()


def load_ollama(dotenv_dir: str) -> Tuple[str, str]:
    """Return (ollama_url, ollama_model) with sensible defaults."""
    url = os.environ.get("OLLAMA_URL")
    model = os.environ.get("OLLAMA_MODEL")
    if url and model:
        return url.strip(), model.strip()
    env = _read_dotenv(dotenv_dir)
    url = (url or env.get("OLLAMA_URL") or "http://localhost:11434").strip()
    model = (model or env.get("OLLAMA_MODEL") or "qwen2.5vl-receipt:latest").strip()
    return url, model


def load_tag_map(script_dir: str) -> Dict[str, str]:
    path = os.path.join(os.path.abspath(script_dir), "tag_map.json")
    if not os.path.isfile(path):
        log.info("No tag_map.json found; proceeding without tag mapping")
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            log.info(f"Loaded tag_map.json with {len(data)} entries")
            return {str(k).lower(): str(v) for k, v in data.items()}
    except Exception as e:
        log.warning(f"Failed to read tag_map.json: {e}")
    return {}

