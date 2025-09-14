import os
import re
from .logging import get_logger

log = get_logger("paths")


def fix_windows_path_input(p: str) -> str:
    """Repair common Windows path paste issues like "C:Users...".

    - Inserts a backslash after drive letter if missing.
    - Trims surrounding quotes/spaces.
    - Leaves non-Windows platforms untouched.
    """
    try:
        s = (p or "").strip().strip('"').strip("'")
        if os.name == "nt" and re.match(r"^[A-Za-z]:(?![\\/])", s):
            fixed = s[:2] + "\\" + s[2:]
            if fixed != s:
                log.debug(f"Repaired Windows path input: '{s}' -> '{fixed}'")
            s = fixed
        return s
    except Exception:
        return p


def expand_abs(path: str) -> str:
    """Expand env vars and ~ then return absolute path."""
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path or "")))

