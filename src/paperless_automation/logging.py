import logging
import os
from typing import Optional


_LEVELS = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARN": logging.WARNING,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
}


def _coerce_level(value: Optional[str]) -> int:
    if isinstance(value, str):
        return _LEVELS.get(value.upper().strip(), logging.INFO)
    if isinstance(value, int):
        return value
    return logging.INFO


def get_logger(name: str) -> logging.Logger:
    """Return a configured stdout logger with consistent formatting.

    - Honors LOG_LEVEL (default INFO) and LOG_FILE (optional path).
    - Keeps output on stdout to preserve current "good prints" behavior.
    - Minimal, no external deps.
    """
    logger = logging.getLogger(name)
    if getattr(logger, "_paperless_configured", False):
        return logger

    level = _coerce_level(os.environ.get("LOG_LEVEL", "INFO"))
    logger.setLevel(level)

    fmt = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    formatter = logging.Formatter(fmt=fmt, datefmt=datefmt)

    # Stream handler to stdout
    sh = logging.StreamHandler()
    sh.setLevel(level)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    # Optional log file (appends)
    log_file = os.environ.get("LOG_FILE")
    if log_file:
        try:
            fh = logging.FileHandler(log_file, encoding="utf-8")
            fh.setLevel(level)
            fh.setFormatter(formatter)
            logger.addHandler(fh)
        except Exception:
            # Do not fail if file handler can't be created
            logger.warning("LOG_FILE could not be opened; continuing without file logging")

    # Avoid duplicate logs if imported multiple times
    logger.propagate = False
    setattr(logger, "_paperless_configured", True)
    return logger


## Note: dynamic set_level utility removed as it was unused.
