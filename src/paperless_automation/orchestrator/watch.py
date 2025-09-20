import os
import sys
import time
from typing import Iterable, List, Optional, Set

from ..logging import get_logger

LOG = get_logger("scan-event-listener")


def debug_print(msg: str) -> None:
    LOG.info(msg)


def _find_upwards(start_dir: str, filename: str) -> Optional[str]:
    """Return first matching file found when walking up from start_dir."""
    d = os.path.abspath(start_dir or ".")
    while True:
        candidate = os.path.join(d, filename)
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _default_scan_image_config_path() -> str:
    # Prefer CWD, searching upwards to repo root
    found = _find_upwards(os.getcwd(), "scan-image-path.txt")
    if found:
        return found
    # Fallback: search relative to this module location (installed package case)
    module_dir = os.path.dirname(os.path.abspath(__file__))
    found = _find_upwards(module_dir, "scan-image-path.txt")
    if found:
        return found
    # Final fallback: default to CWD path for clearer error message
    return os.path.abspath(os.path.join(os.getcwd(), "scan-image-path.txt"))


WATCH_EXTS: Set[str] = {".jpg", ".jpeg", ".pdf"}


def read_watch_dir_from_file(config_path: Optional[str] = None) -> str:
    cfg = config_path or _default_scan_image_config_path()
    debug_print(f"Reading watch directory from: {cfg}")
    try:
        with open(cfg, "r", encoding="utf-8-sig") as f:
            lines = [ln.strip() for ln in f.readlines()]
    except FileNotFoundError:
        debug_print(
            "ERROR: scan-image-path.txt not found. Place it in repo root, current working directory, or next to the module."
        )
        raise SystemExit(1)
    except Exception as e:
        debug_print(f"ERROR: Failed reading scan-image-path.txt: {e}")
        raise SystemExit(1)

    raw_line = ""
    for ln in lines:
        if not ln or ln.startswith("#") or ln.startswith(";"):
            continue
        raw_line = ln
        break
    if not raw_line:
        debug_print("ERROR: scan-image-path.txt is empty or only comments.")
        raise SystemExit(1)

    debug_print(f"Raw config line: {raw_line!r}")
    if "=" in raw_line:
        _, value = raw_line.split("=", 1)
        raw_path = value.strip()
    else:
        raw_path = raw_line.strip()

    if (raw_path.startswith('"') and raw_path.endswith('"')) or (raw_path.startswith("'") and raw_path.endswith("'")):
        raw_path = raw_path[1:-1]
        debug_print(f"Unquoted path: {raw_path!r}")

    expanded = os.path.expandvars(os.path.expanduser(raw_path))
    normalized = os.path.normpath(expanded)
    debug_print(f"Normalized path: {normalized!r}")
    abs_path = os.path.abspath(normalized)
    debug_print(f"Absolute path: {abs_path!r}")
    return abs_path


def _normalize_exts(exts: Iterable[str]) -> Set[str]:
    out: Set[str] = set()
    for e in exts:
        if not e:
            continue
        ee = e.lower()
        if not ee.startswith('.'):
            ee = '.' + ee
        out.add(ee)
    return out


def list_basenames_in_dir_by_ext(directory: str, exts: Iterable[str]) -> Set[str]:
    watch_exts = _normalize_exts(exts)
    try:
        entries = os.listdir(directory)
    except FileNotFoundError:
        debug_print(f"ERROR: Directory does not exist: {directory}")
        return set()
    except PermissionError:
        debug_print(f"ERROR: Permission denied listing directory: {directory}")
        return set()
    except Exception as e:
        debug_print(f"ERROR: Failed to list directory '{directory}': {e}")
        return set()

    files: Set[str] = set()
    for name in entries:
        full = os.path.join(directory, name)
        if os.path.isfile(full):
            _, ext = os.path.splitext(name)
            if ext.lower() in watch_exts:
                files.add(name)
    return files


class ScanEventListener:
    def __init__(
        self,
        *,
        watch_dir: Optional[str] = None,
        config_path: Optional[str] = None,
        poll_interval_sec: float = 1.0,
        print_on_detect: bool = True,
        exts: Optional[Iterable[str]] = None,
    ) -> None:
        if watch_dir:
            self.watch_dir = os.path.abspath(os.path.expanduser(os.path.expandvars(watch_dir)))
            debug_print(f"Using provided watch directory: {self.watch_dir}")
        else:
            cfg = config_path or _default_scan_image_config_path()
            self.watch_dir = read_watch_dir_from_file(cfg)

        if not os.path.isdir(self.watch_dir):
            debug_print(
                f"ERROR: Watch directory does not exist or is not a directory: {self.watch_dir!r}"
            )
            raise SystemExit(1)

        self.exts: Set[str] = _normalize_exts(exts or WATCH_EXTS)
        debug_print(f"Watching for extensions: {sorted(self.exts)}")

        self.poll_interval_sec = float(poll_interval_sec)
        self.print_on_detect = bool(print_on_detect)
        self.baseline: Set[str] = list_basenames_in_dir_by_ext(self.watch_dir, self.exts)
        self.last_seen_count: int = len(self.baseline)
        self.last_new_image_path: Optional[str] = None
        self.new_image_paths: List[str] = []

        debug_print(f"Initial watched-file count in {self.watch_dir!r}: {len(self.baseline)}")
        debug_print("Watching for newly created files with configured extensions...")
        debug_print("Press Ctrl+C to exit manually.")

    def scan_once(self) -> List[str]:
        current = list_basenames_in_dir_by_ext(self.watch_dir, self.exts)
        if len(current) != self.last_seen_count:
            debug_print(f"Detected change in watched-file count: {self.last_seen_count} -> {len(current)}")
            self.last_seen_count = len(current)

        new_files = current - self.baseline
        abs_new_paths: List[str] = []
        if new_files:
            sorted_new = sorted(new_files)
            debug_print(
                f"Detected {len(sorted_new)} new file(s): {sorted_new}. Printing absolute paths and continuing to watch."
            )
            for detected in sorted_new:
                image_path = os.path.join(self.watch_dir, detected)
                image_path = os.path.abspath(image_path)
                debug_print(f"New file: {detected!r} at {image_path!r}")
                if self.print_on_detect:
                    print(image_path, flush=True)
                self.last_new_image_path = image_path
                self.new_image_paths.append(image_path)
                abs_new_paths.append(image_path)
            self.baseline |= new_files

        return abs_new_paths

    def run(self) -> None:
        try:
            while True:
                self.scan_once()
                time.sleep(self.poll_interval_sec)
        except KeyboardInterrupt:
            debug_print("Interrupted by user. Exiting.")
            raise SystemExit(130)
