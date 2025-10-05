from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Sequence

from ..logging import get_logger
from ..orchestrator import (
    ReceiptFlow,
    build_flow_config,
    log_environment_banner,
    create_searchable_pdf,
)
from ..orchestrator.watch import ScanEventListener, read_watch_dir_from_file
from ..orchestrator.verify import verify_pdf
from ..orchestrator.transcribe import transcribe_image
from ..orchestrator.metadata import extract_metadata
from ..paths import expand_abs
from ..orchestrator.productdb import ReceiptExtractionService

LOG = get_logger("cli-main")


def _add_overlay_cli(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    overlay_parser = subparsers.add_parser(
        "overlay",
        help="Create searchable PDFs with invisible text or run overlay watchers.",
    )
    overlay_subparsers = overlay_parser.add_subparsers(dest="overlay_command", required=True)

    cli = overlay_subparsers.add_parser("cli", help="Convert a single image/PDF to searchable PDF")
    cli.add_argument("--image", required=True)
    cli.add_argument("--output", required=True)
    cli.add_argument("--text", help="Text to embed (skip Ollama)")
    cli.add_argument("--ollama-url", help="Ollama base URL")
    cli.add_argument("--ollama-model", help="Ollama model name")

    def _overlay_cli(ns: argparse.Namespace) -> int:
        text = ns.text
        if not text:
            if ns.ollama_url and ns.ollama_model:
                text = transcribe_image(ns.image, ollama_url=ns.ollama_url, model=ns.ollama_model)
            if not text:
                LOG.error("No text available. Provide --text or set --ollama-url/--ollama-model.")
                return 2
        os.makedirs(os.path.dirname(expand_abs(ns.output)) or ".", exist_ok=True)
        from ..orchestrator.overlay import create_pdf_with_invisible_text

        create_pdf_with_invisible_text(ns.image, text, ns.output)
        LOG.info(f"Wrote: {ns.output}")
        return 0

    cli.set_defaults(handler=_overlay_cli)

    watch = overlay_subparsers.add_parser("watch", help="Watch a folder and write searchable PDFs")
    watch.add_argument("--output-dir", required=True)
    watch.add_argument("--watch-dir")
    watch.add_argument("--ollama-url")
    watch.add_argument("--ollama-model")

    def _overlay_watch(ns: argparse.Namespace) -> int:
        outdir = expand_abs(ns.output_dir)
        os.makedirs(outdir, exist_ok=True)
        wdir = expand_abs(ns.watch_dir) if ns.watch_dir else read_watch_dir_from_file()
        listener = ScanEventListener(watch_dir=wdir, print_on_detect=False)
        LOG.info(f"Saving generated PDFs to: {outdir}")
        LOG.info("Press Ctrl+C to stop.")
        try:
            while True:
                for image_path in listener.scan_once():
                    base = os.path.splitext(os.path.basename(image_path))[0]
                    pdf_out = os.path.join(outdir, f"{base}.pdf")
                    if ns.ollama_url and ns.ollama_model:
                        text = transcribe_image(image_path, ollama_url=ns.ollama_url, model=ns.ollama_model)
                    else:
                        LOG.warning("No Ollama settings; skipping %s", image_path)
                        text = None
                    if text:
                        from ..orchestrator.overlay import create_pdf_with_invisible_text

                        create_pdf_with_invisible_text(image_path, text, pdf_out)
                import time as _t
                _t.sleep(listener.poll_interval_sec)
        except KeyboardInterrupt:
            LOG.info("Watch mode interrupted by user. Exiting.")
        return 0

    watch.set_defaults(handler=_overlay_watch)

    pre = overlay_subparsers.add_parser("preconsume", help="Run under Paperless pre-consume")
    def _preconsume(_: argparse.Namespace) -> int:
        working_path = os.environ.get("DOCUMENT_WORKING_PATH")
        if not working_path or not os.path.isfile(working_path):
            LOG.error("DOCUMENT_WORKING_PATH not set or file missing")
            return 2
        tmpdir = os.path.join(os.getcwd(), ".paperless_preconsume_tmp")
        os.makedirs(tmpdir, exist_ok=True)
        tmp_pdf = os.path.join(tmpdir, "overlay.pdf")
        # Attempt to use configured Ollama
        ollama_url = os.environ.get("OLLAMA_URL")
        ollama_model = os.environ.get("OLLAMA_MODEL")
        text = None
        if ollama_url and ollama_model:
            text = transcribe_image(working_path, ollama_url=ollama_url, model=ollama_model)
        if not text:
            LOG.error("No text available for overlay. Provide OLLAMA_URL/MODEL or embed text.")
            return 3
        from ..orchestrator.overlay import create_pdf_with_invisible_text, replace_inplace

        create_pdf_with_invisible_text(working_path, text, tmp_pdf)
        replace_inplace(working_path, tmp_pdf)
        LOG.info("Pre-consume finished successfully")
        return 0

    pre.set_defaults(handler=_preconsume)


def _add_flow_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--mode", choices=["watch", "single"], default="watch", help="Run watcher loop or process a single file")
    p.add_argument("--source", help="When --mode=single, path to the image or PDF to process")
    p.add_argument("--watch-dir", help="Optional override for watch directory; otherwise scan-image-path.txt is used")
    p.add_argument(
        "--output-dir",
        default=None,
        help="Directory for generated searchable PDFs (default: var/generated_pdfs at repo root)",
    )
    p.add_argument("--ollama-url", help="Override Ollama base URL (defaults to env/.env)")
    p.add_argument("--ollama-model", help="Override Ollama model name (defaults to env/.env)")
    p.add_argument("--base-url", help="Override Paperless base URL (defaults to env/.env)")
    p.add_argument("--token", help="Paperless API token (overrides env/.env)")
    p.add_argument("--insecure", action="store_true", help="Disable TLS verification for Paperless calls")
    p.add_argument("--timeout", type=int, default=60, help="HTTP timeout in seconds for Paperless requests")


def _handle_flow(args: argparse.Namespace) -> int:
    log_environment_banner()
    LOG.info(f"Selected mode        : {args.mode}")
    if args.mode == "single":
        LOG.info(f"Single source target : {args.source}")
    if args.watch_dir:
        LOG.info(f"Watcher override     : {args.watch_dir}")

    # Read .env and tag_map.json from the current working directory
    script_dir = os.getcwd()
    config = build_flow_config(args, script_dir=script_dir)
    flow = ReceiptFlow(config)
    if args.mode == "single":
        flow.run_single(args.source)
    else:
        flow.run_watch(watch_dir=args.watch_dir)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    provided = list(argv) if argv is not None else sys.argv[1:]
    LOG.info(f"Unified CLI invoked with arguments: {provided}")

    parser = argparse.ArgumentParser(
        prog="paperless-auto",
        description="Unified CLI entry point for the paperless receipt automation toolkit.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Flow via orchestrator
    flow = subparsers.add_parser(
        "flow",
        help="Run the end-to-end flow (accepts --mode flag).",
        description="End-to-end pipeline: watch scans, transcribe, overlay, extract metadata, upload to Paperless",
    )
    _add_flow_args(flow)
    flow.set_defaults(handler=_handle_flow)

    # Convenience aliases
    watch = subparsers.add_parser("watch", help="Watch a scans directory and process receipts continuously.")
    _add_flow_args(watch)
    watch.set_defaults(handler=lambda ns: _handle_flow(argparse.Namespace(**{**vars(ns), "mode": "watch"})))

    single = subparsers.add_parser("single", help="Process a single image or PDF once and exit.")
    _add_flow_args(single)
    single.set_defaults(handler=lambda ns: _handle_flow(argparse.Namespace(**{**vars(ns), "mode": "single"})))

    # New built-in utilities (refactored from legacy)
    extract_cmd = subparsers.add_parser("extract", help="Extract structured metadata from an image or PDF.")
    extract_cmd.add_argument("--source", required=True)
    extract_cmd.add_argument("--ollama-url")
    extract_cmd.add_argument("--ollama-model")

    def _extract(ns: argparse.Namespace) -> int:
        md = extract_metadata(
            transcript=None,
            source_path=ns.source,
            ollama_url=ns.ollama_url or os.environ.get("OLLAMA_URL", "http://localhost:11434"),
            model=ns.ollama_model or os.environ.get("OLLAMA_MODEL", "qwen2.5vl-receipt:latest"),
        )
        if md is None:
            LOG.error("Extraction failed")
            return 1
        out = {
            "korrespondent": md.korrespondent,
            "ausstellungsdatum": md.ausstellungsdatum,
            "betrag_value": md.betrag_value,
            "betrag_currency": md.betrag_currency,
            "title": md.title(),
        }
        print(json.dumps(out, ensure_ascii=False))
        return 0

    extract_cmd.set_defaults(handler=_extract)

    _add_overlay_cli(subparsers)

    verify_cmd = subparsers.add_parser("verify", help="Inspect a PDF for invisible text overlays.")
    verify_cmd.add_argument("--pdf", required=True)
    verify_cmd.add_argument("--page", type=int)
    verify_cmd.set_defaults(handler=lambda ns: verify_pdf(ns.pdf, (ns.page - 1) if ns.page else None))

    trans_cmd = subparsers.add_parser("transcribe", help="Transcribe a single image/PDF via Ollama.")
    trans_cmd.add_argument("--source", required=True)
    trans_cmd.add_argument("--ollama-url", required=True)
    trans_cmd.add_argument("--ollama-model", required=True)
    def _trans(ns: argparse.Namespace) -> int:
        text = transcribe_image(ns.source, ollama_url=ns.ollama_url, model=ns.ollama_model)
        if not text:
            return 1
        print(text)
        return 0
    trans_cmd.set_defaults(handler=_trans)

    scan_cmd = subparsers.add_parser("scan-listener", help="Run the standalone scan event listener loop.")
    def _scan(_: argparse.Namespace) -> int:
        listener = ScanEventListener()
        listener.run()
        return 0
    scan_cmd.set_defaults(handler=_scan)

    # Product DB tools (scaffold)
    pdb = subparsers.add_parser(
        "productdb",
        help="Product/receipt database utilities (scaffold)",
        description="Initialize the product DB and run extraction stubs.",
    )
    pdb_sub = pdb.add_subparsers(dest="pdb_cmd", required=True)

    pdb_init = pdb_sub.add_parser("init", help="Create/ensure the product DB schema exists")
    def _pdb_init(_: argparse.Namespace) -> int:
        svc = ReceiptExtractionService()
        path = svc.init_database()
        LOG.info(f"Product DB ready at: {path}")
        print(path)
        return 0
    pdb_init.set_defaults(handler=_pdb_init)

    pdb_extract = pdb_sub.add_parser("extract", help="Run OpenAI extraction, validate, and insert into DB")
    pdb_extract.add_argument("--source", required=True, help="Path to receipt image (JPG/PNG)")
    pdb_extract.add_argument("--model", default="gpt-5-mini")
    def _pdb_extract(ns: argparse.Namespace) -> int:
        svc = ReceiptExtractionService()
        result = svc.run_and_persist(ns.source, model_name=ns.model, script_dir=os.getcwd())
        if result is None:
            LOG.error("Extraction failed.")
            return 1
        print(json.dumps(result, ensure_ascii=False))
        return 0
    pdb_extract.set_defaults(handler=_pdb_extract)

    pdb_serve = pdb_sub.add_parser(
        "serve",
        help="Run the product DB API and optional React frontend server.",
    )
    pdb_serve.add_argument("--host", default="127.0.0.1")
    pdb_serve.add_argument("--port", type=int, default=8001)
    pdb_serve.add_argument("--reload", action="store_true", help="Enable auto-reload (development only)")
    pdb_serve.add_argument("--log-level", default="info")
    pdb_serve.add_argument("--static-dir", help="Override static frontend directory relative to project root")
    pdb_serve.add_argument("--api-only", action="store_true", help="Serve JSON API without static frontend")
    pdb_serve.add_argument(
        "--allow-origin",
        action="append",
        dest="allow_origins",
        help="Allowed CORS origin (can be provided multiple times, use '*' for any).",
    )

    def _pdb_serve(ns: argparse.Namespace) -> int:
        from ..orchestrator.productdb.frontend import create_app
        import uvicorn

        allow_origins = ns.allow_origins
        if allow_origins and len(allow_origins) == 1 and allow_origins[0] == "*":
            allow_origins = ["*"]

        app = create_app(
            root_dir=os.getcwd(),
            static_dir=ns.static_dir,
            allow_origins=allow_origins,
            serve_static=not ns.api_only,
        )

        uvicorn.run(
            app,
            host=ns.host,
            port=ns.port,
            reload=ns.reload,
            log_level=ns.log_level,
        )
        return 0

    pdb_serve.set_defaults(handler=_pdb_serve)

    args = parser.parse_args(provided)
    code = args.handler(args)
    LOG.info(f"Subcommand '{args.command}' finished with exit code {code}.")
    return code


if __name__ == "__main__":
    sys.exit(main())
