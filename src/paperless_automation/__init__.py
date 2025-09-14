"""
Paperless Receipt Automation – foundations package.

This package provides shared utilities (config, logging, paths, domain models)
to reduce duplication across scripts while keeping current behavior.

Phase 1 introduces these modules without breaking existing entrypoints.
"""

__all__ = [
    "config",
    "logging",
    "paths",
]

