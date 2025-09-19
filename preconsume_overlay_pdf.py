import argparse
import base64
import json
import os
import sys
import tempfile
from typing import Optional

# Shared logging (Phase 1) must be available early
try:
    from src.paperless_automation.logging import get_logger  # type: ignore
except Exception:
    def get_logger(name: str):  # type: ignore
        class _L:
            def info(self, m):
                print(f"[{name}] {m}")
            debug = info
            warning = info
            error = info
        return _L()

_LOG = get_logger("preconsume-overlay-pdf")

try:
    import fitz  # PyMuPDF
except Exception as e:
    _LOG.error(f"PyMuPDF (package 'pymupdf') is required: {e}")
    raise

try:
    import requests  # optional, for Ollama
except Exception:
    requests = None

# (logger already initialized above)

# Try to import the project's transcriber utilities so we use the exact logic
try:
    from ollama_transcriber import (
        transcribe_image_via_ollama,
        MODEL_NAME as TRANSCRIBER_MODEL_NAME,
        OLLAMA_URL as TRANSCRIBER_OLLAMA_URL,
        INSTRUCTION as TRANSCRIBER_INSTRUCTION,
    )
except Exception as e:
    transcribe_image_via_ollama = None  # type: ignore
    TRANSCRIBER_MODEL_NAME = "qwen2.5vl-receipt:latest"
    TRANSCRIBER_OLLAMA_URL = "http://localhost:11434/api/chat"
    TRANSCRIBER_INSTRUCTION = (
        "Transcribe this receipt EXACTLY (spacing, order). Output plain text only. "
        "When finished, print <eot> on a new line."
    )
    _LOG.warning(f"Could not import ollama_transcriber: {e}. Falling back to internal defaults.")

# Reuse the project's scan directory watcher
try:
    from scan_event_listener import ScanEventListener, debug_print as scan_debug
except Exception as e:
    ScanEventListener = None  # type: ignore
    def scan_debug(msg: str) -> None:
        _LOG.info(f"[scan-listener] {msg}")
    _LOG.warning(f"Could not import ScanEventListener: {e}")


def log_env_overview():
    _LOG.info("Environment overview for debugging:")
    for key in [
        "DOCUMENT_WORKING_PATH",
        "PAPERLESS_PRE_CONSUME_SCRIPT",
        "PAPERLESS_CONSUMER_NAME",
        "OLLAMA_URL",
        "OLLAMA_MODEL",
    ]:
        _LOG.info(f"  - {key} = {os.environ.get(key)}")


def pixmap_from_any(path: str) -> fitz.Pixmap:
    """Load an image or the first page of a PDF into a Pixmap.

    - If `path` is an image: load directly via Pixmap(path).
    - If `path` is a PDF: render first page to pixmap at 150 dpi.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}:
        _LOG.debug(f"Loading raster image as Pixmap: {path}")
        return fitz.Pixmap(path)

    if ext == ".pdf":
        _LOG.debug(f"Rendering first page of PDF to Pixmap: {path}")
        with fitz.open(path) as doc:
            if doc.page_count == 0:
                raise RuntimeError("Input PDF has no pages")
            page = doc.load_page(0)
            # 150 dpi is a decent compromise for LLM OCR prompts
            mat = fitz.Matrix(150 / 72, 150 / 72)
            return page.get_pixmap(matrix=mat)

    raise ValueError(f"Unsupported input type: {ext}")


def create_pdf_with_invisible_text(image_path: str, text: str, output_pdf: str) -> None:
    """Create a single-page PDF with the image as the page background and
    embed `text` as invisible text (render_mode=3) occupying the page.
    """
    _LOG.info("Creating PDF with invisible text overlay")
    _LOG.debug(f"Image path: {image_path}")
    _LOG.debug(f"Output PDF: {output_pdf}")

    # Determine image dimensions
    pix = pixmap_from_any(image_path)
    width, height = pix.width, pix.height
    _LOG.debug(f"Image dimensions (px): {width} x {height}")

    # Build PDF
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    rect = fitz.Rect(0, 0, width, height)
    # Place image to fill page
    _LOG.debug("Inserting image onto the page")
    ext = os.path.splitext(image_path)[1].lower()
    if ext == ".pdf":
        # Insert rendered pixmap as image stream
        _LOG.debug("Input is PDF; inserting rendered first page as image stream")
        img_bytes = pix.tobytes("png")
        page.insert_image(rect, stream=img_bytes, keep_proportion=False)
    else:
        page.insert_image(rect, filename=image_path, keep_proportion=False)

    # Insert invisible text across the page. render_mode=3 => invisible
    # Choose a reasonable fontsize so selection order is sane even if invisible.
    fontsize = 10
    margin = 20
    textbox = fitz.Rect(margin, margin, width - margin, height - margin)
    _LOG.debug("Inserting invisible text layer (render_mode=3)")
    page.insert_textbox(
        textbox,
        text,
        fontname="helv",
        fontsize=fontsize,
        color=(0, 0, 0),
        render_mode=3,
        align=fitz.TEXT_ALIGN_LEFT,
    )

    # Save atomically
    _LOG.debug("Saving PDF")
    doc.save(output_pdf)
    doc.close()
    _LOG.info(f"PDF created: {output_pdf}")


def ensure_dir(path: str) -> str:
    """Create a directory if missing; return its absolute path."""
    absdir = os.path.abspath(os.path.expanduser(os.path.expandvars(path)))
    if not os.path.isdir(absdir):
        _LOG.info(f"Output directory does not exist. Creating: {absdir}")
        try:
            os.makedirs(absdir, exist_ok=True)
        except Exception as e:
            _LOG.error(f"Failed to create output directory '{absdir}': {e}")
            raise
    else:
        _LOG.debug(f"Output directory exists: {absdir}")
    return absdir


def unique_path(base_path: str) -> str:
    """Return a non-clobbering path by adding numeric suffixes if needed."""
    if not os.path.exists(base_path):
        return base_path
    stem, ext = os.path.splitext(base_path)
    counter = 1
    while True:
        cand = f"{stem} ({counter}){ext}"
        if not os.path.exists(cand):
            return cand
        counter += 1


def encode_pixmap_base64(pix: fitz.Pixmap, image_format: str = "png") -> str:
    """Return base64-encoded raw image data (not data URL), matching ollama_transcriber."""
    if image_format not in {"png", "jpeg"}:
        image_format = "png"
    _LOG.debug(f"Encoding pixmap to base64 as {image_format}")
    img_bytes = pix.tobytes(output=image_format)
    return base64.b64encode(img_bytes).decode("ascii")


def _raw_file_b64(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as f:
            data = f.read()
        return base64.b64encode(data).decode("ascii")
    except Exception as e:
        _LOG.error(f"Failed to read file for base64: {e}")
        return None


def call_ollama_vision(
    image_path: str,
    prompt: str,
    ollama_url: str,
    model: str,
    timeout: int = 120,
) -> Optional[str]:
    """Delegate to the project's transcriber function if available, else None."""
    if transcribe_image_via_ollama is None:
        _LOG.error("transcribe_image_via_ollama not available; ensure ollama_transcriber.py is importable.")
        return None
    # The transcriber function expects instruction text in 'instruction'.
    # It will use the provided url/model and stream results.
    return transcribe_image_via_ollama(
        image_path=image_path,
        model=model,
        ollama_url=ollama_url,
        instruction=prompt,
        timeout=timeout,
    )


def guess_text_source(
    args: argparse.Namespace, image_path: str
) -> Optional[str]:
    # 1) --text has highest priority
    if args.text:
        _LOG.info("Using text provided via --text")
        return args.text

    # 2) --text-file
    if args.text_file:
        if not os.path.isfile(args.text_file):
            _LOG.error(f"--text-file not found: {args.text_file}")
            return None
        _LOG.info(f"Reading text from file: {args.text_file}")
        with open(args.text_file, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()

    # 3) Ollama (if configured)
    ollama_url = args.ollama_url or os.environ.get("OLLAMA_URL") or TRANSCRIBER_OLLAMA_URL
    ollama_model = args.ollama_model or os.environ.get("OLLAMA_MODEL") or TRANSCRIBER_MODEL_NAME

    if ollama_url:
        prompt = args.prompt or TRANSCRIBER_INSTRUCTION
        text = call_ollama_vision(image_path, prompt, ollama_url, ollama_model)
        if text:
            _LOG.info("Using text returned by Ollama")
            return text
        else:
            _LOG.warning("Ollama did not return text. Provide --text or --text-file.")

    return None


def replace_inplace(original_path: str, new_pdf_path: str) -> None:
    """Replace the file at original_path with new_pdf_path content atomically.
    Note: Paperless pre-consume allows replacing the working file path; the
    file extension may not match its content after replacement, which is fine for the consumer.
    """
    _LOG.info("Replacing working file in place")
    _LOG.debug(f"Original path: {original_path}")
    _LOG.debug(f"New PDF path:  {new_pdf_path}")
    # On Windows, os.replace does an atomic move when possible.
    os.replace(new_pdf_path, original_path)
    _LOG.info("Replacement completed")


def run_preconsume_mode():
    log_env_overview()
    working_path = os.environ.get("DOCUMENT_WORKING_PATH")
    if not working_path:
        _LOG.error("DOCUMENT_WORKING_PATH not set. Are you running under Paperless pre-consume?")
        sys.exit(2)

    # Build a minimal args namespace for guess_text_source
    args = argparse.Namespace(
        text=None,
        text_file=None,
        ollama_url=os.environ.get("OLLAMA_URL") or TRANSCRIBER_OLLAMA_URL,
        ollama_model=os.environ.get("OLLAMA_MODEL") or TRANSCRIBER_MODEL_NAME,
        prompt=None,
    )

    if not os.path.isfile(working_path):
        _LOG.error(f"Working file not found: {working_path}")
        sys.exit(2)

    # Choose text source (text, text-file, or Ollama via env)
    text = guess_text_source(args, working_path)
    if not text:
        _LOG.error("No text available for overlay. Provide --text/--text-file or set OLLAMA_URL.")
        sys.exit(3)

    # Create PDF to a temp file then replace
    with tempfile.TemporaryDirectory() as td:
        tmp_pdf = os.path.join(td, "overlay.pdf")
        create_pdf_with_invisible_text(working_path, text, tmp_pdf)
        replace_inplace(working_path, tmp_pdf)

    _LOG.info("Pre-consume finished successfully")


def run_watch_mode(args: argparse.Namespace) -> None:
    """Watch a directory for new JPEG scans, transcribe via Ollama, and
    write a PDF with an invisible text layer to the specified output folder.
    """
    if ScanEventListener is None:
        _LOG.error("scan_event_listener.py not importable; cannot run watch mode.")
        sys.exit(2)

    outdir = ensure_dir(args.output_dir)
    # Prepare listener; if --watch-dir is omitted, listener reads scan-image-path.txt
    listener = ScanEventListener(
        watch_dir=args.watch_dir,
        print_on_detect=False,
        poll_interval_sec=float(args.poll_interval),
    )
    scan_debug(f"Watch mode active. Watching: {listener.watch_dir}")
    _LOG.info(f"Saving generated PDFs to: {outdir}")
    _LOG.info("Press Ctrl+C to stop.")

    try:
        while True:
            new_paths = listener.scan_once()
            if not new_paths:
                import time as _t
                _t.sleep(listener.poll_interval_sec)
                continue

            for image_path in new_paths:
                _LOG.info(f"Detected new scan: {image_path}")
                # Build output path
                base = os.path.splitext(os.path.basename(image_path))[0]
                target_pdf = os.path.join(outdir, f"{base}.pdf")
                target_pdf = unique_path(target_pdf)
                _LOG.debug(f"Target PDF path: {target_pdf}")

                # Get text via args or Ollama
                text = guess_text_source(args, image_path)
                if not text:
                    _LOG.warning("No text available from Ollama/args; skipping PDF creation for this file.")
                    continue

                try:
                    create_pdf_with_invisible_text(image_path, text, target_pdf)
                except Exception as e:
                    _LOG.error(f"Failed creating PDF for '{image_path}': {e}")
                    continue
    except KeyboardInterrupt:
        _LOG.info("Watch mode interrupted by user. Exiting.")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Create a PDF with the original image as background and an invisible text layer. "
            "Can be used as a Paperless pre-consume hook or as a standalone CLI."
        )
    )
    sub = p.add_subparsers(dest="mode")

    # CLI mode
    cli = sub.add_parser("cli", help="Run as CLI tool")
    cli.add_argument("--image", required=True, help="Path to input image or PDF (first page)")
    cli.add_argument("--output", required=True, help="Path to output PDF")
    cli.add_argument("--text", help="Text to embed as invisible layer")
    cli.add_argument("--text-file", help="Path to a .txt file with text to embed")
    cli.add_argument("--ollama-url", dest="ollama_url", default="http://localhost:11434", help="Ollama base URL, e.g. http://localhost:11434")
    cli.add_argument("--ollama-model", dest="ollama_model", default="qwen2.5vl-receipt:latest", help="Ollama model name")
    cli.add_argument("--prompt", help="Prompt text for the LLM")

    # Pre-consume mode (no positional args; uses env DOCUMENT_WORKING_PATH)
    sub.add_parser("preconsume", help="Run under Paperless pre-consume (uses DOCUMENT_WORKING_PATH)")

    # Watch mode: integrate ScanEventListener + Ollama + PDF output
    watch = sub.add_parser("watch", help="Watch a scans folder and write PDFs with invisible text")
    watch.add_argument("--output-dir", required=True, help="Folder to write generated PDFs")
    watch.add_argument("--watch-dir", help="Folder to watch; if omitted, read from scan-image-path.txt")
    watch.add_argument("--poll-interval", type=float, default=1.0, help="Polling interval in seconds")
    watch.add_argument("--text", help="Force this text for all files (skip Ollama)")
    watch.add_argument("--text-file", help="Read text from this file for all files")
    watch.add_argument("--ollama-url", dest="ollama_url", help="Ollama base URL, e.g. http://localhost:11434 or http://localhost:11434/api/chat")
    watch.add_argument("--ollama-model", dest="ollama_model", default="qwen2.5vl-receipt:latest", help="Ollama model name")
    watch.add_argument("--prompt", help="Prompt text for the LLM (falls back to ollama_transcriber.INSTRUCTION)")

    return p


def main():
    parser = build_arg_parser()
    args = parser.parse_args()

    if args.mode == "preconsume":
        run_preconsume_mode()
        return

    if args.mode == "cli":
        _LOG.info("Running in CLI mode")
        image = args.image
        output = args.output

        if not os.path.isfile(image):
            _LOG.error(f"Input not found: {image}")
            sys.exit(2)

        text = guess_text_source(args, image)
        if not text:
            _LOG.error("No text available. Provide --text/--text-file or --ollama-url.")
            sys.exit(3)

        create_pdf_with_invisible_text(image, text, output)
        _LOG.info("CLI finished successfully")
        return

    if args.mode == "watch":
        _LOG.info("Running in WATCH mode")
        run_watch_mode(args)
        return

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    from src.paperless_automation.cli.main import main as cli_main

    forwarded = list(sys.argv[1:])
    if (
        len(forwarded) >= 2
        and forwarded[0] == "--mode"
        and forwarded[1] in {"cli", "watch", "preconsume"}
    ):
        forwarded = [forwarded[1]] + forwarded[2:]
    cli_main(["overlay", *forwarded])
