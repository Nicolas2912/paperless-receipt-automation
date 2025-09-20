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


def find_project_root(start_dir: str | None = None) -> str:
    """Find the repository root by walking upward from start_dir.

    Looks for common markers: .git/, tag_map.json, requirements.txt, README.md.
    Falls back to absolute(start_dir) if nothing found.
    """
    d = os.path.abspath(start_dir or os.getcwd() or ".")
    try:
        while True:
            # Directory marker (.git)
            if os.path.isdir(os.path.join(d, ".git")):
                return d
            # File markers
            for marker in ("tag_map.json", "requirements.txt", "README.md"):
                if os.path.isfile(os.path.join(d, marker)):
                    return d
            parent = os.path.dirname(d)
            if parent == d:
                return d
            d = parent
    except Exception:
        return d


def var_dir(root_dir: str) -> str:
    """Return the absolute var directory under the project root."""
    return os.path.join(os.path.abspath(root_dir), "var")
