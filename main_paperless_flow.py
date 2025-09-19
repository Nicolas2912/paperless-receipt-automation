from __future__ import annotations

import argparse
import os

from src.paperless_automation.logging import get_logger
from src.paperless_automation.orchestrator import (
    ReceiptFlow,
    build_flow_config,
    log_environment_banner,
)


LOG = get_logger("main-paperless-flow")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "End-to-end pipeline: watch scans, transcribe, overlay, extract metadata, "
            "upload to Paperless"
        ),
    )
    parser.add_argument(
        "--mode",
        choices=["watch", "single"],
        default="watch",
        help="Run watcher loop or process a single file",
    )
    parser.add_argument(
        "--source",
        help="When --mode=single, path to the image or PDF to process",
    )
    parser.add_argument(
        "--watch-dir",
        help="Optional override for watch directory; otherwise scan-image-path.txt is used",
    )
    parser.add_argument(
        "--output-dir",
        default="generated_pdfs",
        help="Directory for generated searchable PDFs",
    )
    parser.add_argument(
        "--ollama-url",
        help="Override Ollama base URL (defaults to env/.env)",
    )
    parser.add_argument(
        "--ollama-model",
        help="Override Ollama model name (defaults to env/.env)",
    )
    parser.add_argument(
        "--base-url",
        help="Override Paperless base URL (defaults to env/.env)",
    )
    parser.add_argument(
        "--token",
        help="Paperless API token (overrides env/.env)",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS verification for Paperless calls",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="HTTP timeout in seconds for Paperless requests",
    )
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    log_environment_banner()
    LOG.info(f"Selected mode        : {args.mode}")
    if args.mode == "single":
        LOG.info(f"Single source target : {args.source}")
    if args.watch_dir:
        LOG.info(f"Watcher override     : {args.watch_dir}")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    config = build_flow_config(args, script_dir=script_dir)
    flow = ReceiptFlow(config)

    if args.mode == "single":
        flow.run_single(args.source)
    else:
        flow.run_watch(watch_dir=args.watch_dir)


if __name__ == "__main__":
    main()

