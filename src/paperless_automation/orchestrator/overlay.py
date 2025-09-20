"""Create searchable PDFs with invisible text for the orchestrator."""

from __future__ import annotations

import os
from typing import Optional

import fitz  # PyMuPDF

from ..logging import get_logger

LOG = get_logger("orchestrator-overlay")


def ensure_dir(path: str) -> str:
    absdir = os.path.abspath(os.path.expanduser(os.path.expandvars(path)))
    if not os.path.isdir(absdir):
        LOG.info(f"Output directory does not exist. Creating: {absdir}")
        os.makedirs(absdir, exist_ok=True)
    else:
        LOG.debug(f"Output directory exists: {absdir}")
    return absdir


def unique_path(base_path: str) -> str:
    if not os.path.exists(base_path):
        return base_path
    stem, ext = os.path.splitext(base_path)
    counter = 1
    while True:
        cand = f"{stem} ({counter}){ext}"
        if not os.path.exists(cand):
            return cand
        counter += 1


def pixmap_from_any(path: str) -> fitz.Pixmap:
    ext = os.path.splitext(path)[1].lower()
    if ext in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}:
        return fitz.Pixmap(path)
    if ext == ".pdf":
        with fitz.open(path) as doc:
            if doc.page_count == 0:
                raise RuntimeError("Input PDF has no pages")
            page = doc.load_page(0)
            mat = fitz.Matrix(150 / 72, 150 / 72)
            return page.get_pixmap(matrix=mat)
    raise ValueError(f"Unsupported input type: {ext}")


def create_pdf_with_invisible_text(image_path: str, text: str, output_pdf: str) -> None:
    LOG.info("Creating PDF with invisible text overlay")
    LOG.debug(f"Image path: {image_path}")
    LOG.debug(f"Output PDF: {output_pdf}")

    pix = pixmap_from_any(image_path)
    width, height = pix.width, pix.height

    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    rect = fitz.Rect(0, 0, width, height)
    ext = os.path.splitext(image_path)[1].lower()
    if ext == ".pdf":
        img_bytes = pix.tobytes("png")
        page.insert_image(rect, stream=img_bytes, keep_proportion=False)
    else:
        page.insert_image(rect, filename=image_path, keep_proportion=False)

    textbox = fitz.Rect(20, 20, width - 20, height - 20)
    page.insert_textbox(
        textbox,
        text,
        fontname="helv",
        fontsize=10,
        color=(0, 0, 0),
        render_mode=3,  # invisible
        align=fitz.TEXT_ALIGN_LEFT,
    )

    doc.save(output_pdf)
    doc.close()
    LOG.info(f"PDF created: {output_pdf}")


def create_searchable_pdf(
    image_path: str,
    transcript: str,
    output_dir: str,
) -> Optional[str]:
    """Return the path to a PDF containing the image + invisible transcript text."""
    ensure_dir(output_dir)
    stem = os.path.splitext(os.path.basename(image_path))[0]
    candidate = os.path.join(output_dir, f"{stem}.pdf")
    pdf_path = unique_path(candidate)

    LOG.info(f"Creating searchable PDF at {pdf_path}")
    try:
        create_pdf_with_invisible_text(image_path, transcript, pdf_path)
    except Exception as exc:
        LOG.error(f"PDF creation failed: {exc}")
        return None

    LOG.info("Searchable PDF created successfully")
    return pdf_path


def replace_inplace(original_path: str, new_pdf_path: str) -> None:
    LOG.info("Replacing working file in place")
    LOG.debug(f"Original path: {original_path}")
    LOG.debug(f"New PDF path:  {new_pdf_path}")
    os.replace(new_pdf_path, original_path)
    LOG.info("Replacement completed")
