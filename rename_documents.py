import os
import re
from typing import Tuple, Iterable


def _log(msg: str) -> None:
    print(f"[rename-docs] {msg}", flush=True)


INVALID_WIN_CHARS = r'<>:"/\\|?*'
INVALID_RE = re.compile(f"[{re.escape(INVALID_WIN_CHARS)}]")


def sanitize_component(name: str) -> str:
    """Return a filesystem-safe component for Windows and POSIX.

    - Removes characters not allowed on Windows.
    - Collapses whitespace to single spaces, then replace spaces with underscores.
    - Strips trailing dots and spaces.
    - Keeps Unicode letters (e.g., ä/ö/ü) intact.
    """
    n = (name or "").strip()
    # Remove invalid characters
    n = INVALID_RE.sub("", n)
    # Collapse whitespace
    n = re.sub(r"\s+", " ", n).strip()
    # Replace spaces with underscores for stability
    n = n.replace(" ", "_")
    # Strip trailing dots/spaces
    n = n.rstrip(". ")
    # Guard against empty
    return n if n else "Unbekannt"


JPEG_EXTS = {".jpg", ".jpeg"}


def _next_shared_id(
    image_dir: str,
    pdf_dir: str,
    date_iso: str,
    kor: str,
    image_ext: str,
) -> int:
    """Return the next available integer id s.t. neither
    `<image_dir>/<date>_<kor>_<id>.<image_ext>` nor `<pdf_dir>/<date>_<kor>_<id>.pdf` exists.
    Additionally, guard against collisions with other common JPEG variants
    (e.g., .jpg, .jpeg) to keep the sequence stable across variants.
    Starts at 1.
    """
    sid = 1
    # Build the set of image extensions to check for collisions
    exts_to_check: Iterable[str] = set([image_ext.lower()]) | JPEG_EXTS
    while True:
        base = f"{date_iso}_{kor}_{sid}"
        # If any variant exists, treat id as taken
        variant_exists = any(
            os.path.exists(os.path.join(image_dir, base + ext)) for ext in exts_to_check
        )
        pdf_path = os.path.join(pdf_dir, base + ".pdf")
        if not variant_exists and not os.path.exists(pdf_path):
            return sid
        sid += 1


def rename_with_metadata(
    image_path: str,
    pdf_path: str,
    *,
    date_iso: str,
    korrespondent: str,
) -> Tuple[str, str]:
    """Rename the image (keeping its original extension) and PDF to
    `<date>_<kor>_<id>.<ext>` using a shared id.

    - The `id` is chosen so that neither the target JPEG (considering common JPEG variants)
      nor the target PDF exists yet.
    - Returns `(new_image_path, new_pdf_path)`.
    """
    image_dir = os.path.abspath(os.path.dirname(image_path))
    pdf_dir = os.path.abspath(os.path.dirname(pdf_path))
    kor_safe = sanitize_component(korrespondent)
    # Keep the original image extension as-is
    _, img_ext = os.path.splitext(image_path)
    img_ext = img_ext or ".jpg"  # fall back to .jpg if missing

    sid = _next_shared_id(image_dir, pdf_dir, date_iso, kor_safe, img_ext.lower())
    base = f"{date_iso}_{kor_safe}_{sid}"

    new_image = os.path.join(image_dir, base + img_ext)
    new_pdf = os.path.join(pdf_dir, base + ".pdf")

    _log(f"Computed shared id={sid} for date={date_iso}, korrespondent={korrespondent!r}")
    _log(f"Renaming image -> {new_image}")
    _log(f"Renaming pdf   -> {new_pdf}")

    # Perform renames atomically (best-effort on OS)
    try:
        if os.path.abspath(image_path) != os.path.abspath(new_image):
            os.replace(image_path, new_image)
    except Exception as e:
        _log(f"ERROR renaming image: {e}")
        raise

    try:
        if os.path.abspath(pdf_path) != os.path.abspath(new_pdf):
            os.replace(pdf_path, new_pdf)
    except Exception as e:
        _log(f"ERROR renaming PDF: {e}")
        # Try to roll back image rename to reduce confusion
        try:
            os.replace(new_image, image_path)
        except Exception:
            pass
        raise

    return new_image, new_pdf
