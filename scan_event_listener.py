import os
import sys
import time
from typing import Set, Optional, List

# Centralized path to 'scan-image-path.txt' defined once
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCAN_IMAGE_CONFIG_PATH = os.path.join(SCRIPT_DIR, "scan-image-path.txt")


def debug_print(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] [scan-event-listener] {msg}", flush=True)


def read_watch_dir_from_file(config_path: Optional[str] = None) -> str:
    """
    Reads the directory path to watch from a text file.
    Trims whitespace and surrounding quotes to be forgiving.
    """
    cfg = config_path or SCAN_IMAGE_CONFIG_PATH
    debug_print(f"Reading watch directory from: {cfg}")
    try:
        with open(cfg, "r", encoding="utf-8-sig") as f:  # utf-8 with BOM tolerant
            lines = [ln.strip() for ln in f.readlines()]
    except FileNotFoundError:
        debug_print("ERROR: scan-image-path.txt not found. Ensure it exists next to this script.")
        sys.exit(1)
    except Exception as e:
        debug_print(f"ERROR: Failed reading scan-image-path.txt: {e}")
        sys.exit(1)

    # Find the first non-empty, non-comment line
    raw_line = ""
    for ln in lines:
        if not ln or ln.startswith("#") or ln.startswith(";"):
            continue
        raw_line = ln
        break
    if not raw_line:
        debug_print("ERROR: scan-image-path.txt is empty or only comments.")
        sys.exit(1)

    debug_print(f"Raw config line: {raw_line!r}")

    # If line is in KEY=VALUE format (e.g., PATH=...), extract the VALUE part
    if "=" in raw_line:
        key, value = raw_line.split("=", 1)
        debug_print(f"Detected key/value: key={key.strip()!r}, value={value.strip()!r}")
        raw_path = value.strip()
    else:
        raw_path = raw_line.strip()

    # Remove optional wrapping quotes
    if (raw_path.startswith('"') and raw_path.endswith('"')) or (raw_path.startswith("'") and raw_path.endswith("'")):
        raw_path = raw_path[1:-1]
        debug_print(f"Unquoted path: {raw_path!r}")

    # Expand environment variables and ~
    expanded = os.path.expandvars(raw_path)
    expanded = os.path.expanduser(expanded)
    debug_print(f"Expanded path: {expanded!r}")

    # Normalize separators to current OS
    normalized = os.path.normpath(expanded)
    debug_print(f"Normalized path: {normalized!r}")

    # On Windows, repair malformed drive paths like C:Users...
    if os.name == "nt" and len(normalized) >= 2 and normalized[1] == ":" and "\\" not in normalized and "/" not in normalized:
        drive, rest = normalized[:2], normalized[2:]
        repaired = drive + "\\" + "\\".join(rest.split("\\"))
        debug_print(f"Heuristic repaired path from {normalized!r} to {repaired!r}")
        normalized = repaired

    abs_path = os.path.abspath(normalized)
    debug_print(f"Absolute path: {abs_path!r}")

    return abs_path


def list_jpeg_basenames_in_dir(directory: str) -> Set[str]:
    """
    Returns a set of JPEG filenames (basenames only) present in the given directory.
    Only the top-level directory is scanned (non-recursive).
    """
    exts = {".jpg", ".jpeg", ".jpe", ".jfif"}
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

    files = set()
    for name in entries:
        full = os.path.join(directory, name)
        if os.path.isfile(full):
            _, ext = os.path.splitext(name)
            if ext.lower() in exts:
                files.add(name)
    return files


class ScanEventListener:
    """
    Watches a directory for newly created JPEG files and keeps running.
    Stores the absolute path of the most recently detected image in
    `last_new_image_path` and keeps a history in `new_image_paths`.
    """

    def __init__(
        self,
        *,
        watch_dir: Optional[str] = None,
        config_path: Optional[str] = None,
        poll_interval_sec: float = 1.0,
        print_on_detect: bool = True,
    ) -> None:
        # Resolve watch directory from argument or config file
        if watch_dir:
            self.watch_dir = os.path.abspath(os.path.expanduser(os.path.expandvars(watch_dir)))
            debug_print(f"Using provided watch directory: {self.watch_dir}")
        else:
            cfg = config_path or SCAN_IMAGE_CONFIG_PATH
            self.watch_dir = read_watch_dir_from_file(cfg)

        # Validate directory
        if not os.path.isdir(self.watch_dir):
            debug_print(
                f"ERROR: Watch directory does not exist or is not a directory: {self.watch_dir!r}"
            )
            exists = os.path.exists(self.watch_dir)
            debug_print(
                f"Exists: {exists}; isfile: {os.path.isfile(self.watch_dir)}; "
                f"isdir: {os.path.isdir(self.watch_dir)}"
            )
            parent = os.path.dirname(self.watch_dir) or "."
            debug_print(f"Parent directory: {parent!r}")
            try:
                sample = []
                for name in os.listdir(parent):
                    sample.append(name)
                    if len(sample) >= 10:
                        break
                debug_print(f"Parent contents (up to 10): {sample}")
            except Exception as e:
                debug_print(f"Failed to list parent directory: {e}")
            raise SystemExit(1)

        # State
        self.poll_interval_sec = float(poll_interval_sec)
        self.print_on_detect = bool(print_on_detect)
        self.baseline: Set[str] = list_jpeg_basenames_in_dir(self.watch_dir)
        self.last_seen_count: int = len(self.baseline)
        self.last_new_image_path: Optional[str] = None
        self.new_image_paths: List[str] = []

        debug_print(f"Initial JPEG count in {self.watch_dir!r}: {len(self.baseline)}")
        debug_print("Watching for newly created JPEG files (.jpg, .jpeg, .jpe, .jfif)...")
        debug_print("Press Ctrl+C to exit manually.")

    def get_last_new_image_path(self) -> Optional[str]:
        """Return the absolute path of the last detected image (or None)."""
        return self.last_new_image_path

    def get_all_detected_paths(self) -> List[str]:
        """Return a copy of all detected absolute image paths."""
        return list(self.new_image_paths)

    def scan_once(self) -> List[str]:
        """Scan the directory once and return a list of newly detected absolute paths."""
        current = list_jpeg_basenames_in_dir(self.watch_dir)
        if len(current) != self.last_seen_count:
            debug_print(f"Detected change in JPEG count: {self.last_seen_count} -> {len(current)}")
            self.last_seen_count = len(current)

        new_files = current - self.baseline
        abs_new_paths: List[str] = []
        if new_files:
            sorted_new = sorted(new_files)
            debug_print(
                f"Detected {len(sorted_new)} new JPEG(s): {sorted_new}. "
                f"Printing absolute paths and continuing to watch."
            )
            for detected in sorted_new:
                image_path = os.path.join(self.watch_dir, detected)
                image_path = os.path.abspath(image_path)
                debug_print(f"New JPEG: {detected!r} at {image_path!r}")
                if self.print_on_detect:
                    print(image_path, flush=True)
                self.last_new_image_path = image_path
                self.new_image_paths.append(image_path)
                abs_new_paths.append(image_path)
            # Update baseline so these files are not reported again
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


def main_scaneventlistener() -> None:
    listener = ScanEventListener()
    listener.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        debug_print(f"FATAL: Unhandled exception: {e}")
        sys.exit(1)

