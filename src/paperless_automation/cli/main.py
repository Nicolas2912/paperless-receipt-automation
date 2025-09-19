from __future__ import annotations

import argparse
import importlib
import sys
from typing import Iterable, Sequence

try:
    from ..logging import get_logger  # type: ignore
except Exception:
    def get_logger(name: str):  # type: ignore
        class _FallbackLogger:
            def info(self, message: str) -> None:
                print(f"[{name}] {message}", flush=True)

            debug = info
            warning = info
            error = info

        return _FallbackLogger()

LOG = get_logger("cli-main")


def _delegate(module_name: str, argv: Sequence[str], *, func_name: str = "main") -> int:
    forwarded = list(argv)
    LOG.info(
        f"Delegating command to {module_name}.{func_name} with arguments: {forwarded}"
    )

    module = importlib.import_module(module_name)
    if not hasattr(module, func_name):
        LOG.error(
            f"Module '{module_name}' does not expose callable '{func_name}'."
        )
        raise SystemExit(2)

    target = getattr(module, func_name)
    original_argv = sys.argv
    sys.argv = [f"{original_argv[0]} {module_name}"] + forwarded
    try:
        result = target()
    except SystemExit as exc:
        LOG.info(
            f"{module_name}.{func_name} exited early with SystemExit({exc.code})."
        )
        raise
    except Exception as exc:  # pragma: no cover - defensive logging
        LOG.error(f"Unhandled exception from {module_name}.{func_name}: {exc}")
        raise
    finally:
        sys.argv = original_argv

    exit_code = 0
    if result is not None:
        try:
            exit_code = int(result)
        except Exception:
            LOG.warning(
                f"Return value {result!r} from {module_name}.{func_name} is not an int; "
                "defaulting exit code to 0."
            )
            exit_code = 0
    LOG.info(
        f"{module_name}.{func_name} completed successfully with exit code {exit_code}."
    )
    return exit_code


def _register_passthrough(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    name: str,
    module_name: str,
    help_text: str,
    func_name: str = "main",
    prefix: Iterable[str] | None = None,
    description: str | None = None,
) -> None:
    parser = subparsers.add_parser(name, help=help_text, description=description)
    parser.add_argument(
        "forwarded_args",
        nargs=argparse.REMAINDER,
        help=(
            "Arguments forwarded verbatim to the legacy entry point. "
            "Use '--' if additional flags start with '-'."
        ),
    )

    pre_args = list(prefix or [])

    def _handler(namespace: argparse.Namespace, *, command_name: str = name) -> int:
        forwarded = pre_args + list(namespace.forwarded_args)
        LOG.info(f"Executing subcommand '{command_name}' with args: {forwarded}")
        return _delegate(module_name, forwarded, func_name=func_name)

    parser.set_defaults(handler=_handler)


def main(argv: Sequence[str] | None = None) -> int:
    provided = list(argv) if argv is not None else sys.argv[1:]
    LOG.info(f"Unified CLI invoked with arguments: {provided}")

    parser = argparse.ArgumentParser(
        prog="paperless-auto",
        description="Unified CLI entry point for the paperless receipt automation toolkit.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    _register_passthrough(
        subparsers,
        name="flow",
        module_name="main_paperless_flow",
        help_text="Run the legacy end-to-end flow (accepts original --mode flag).",
    )

    _register_passthrough(
        subparsers,
        name="watch",
        module_name="main_paperless_flow",
        help_text="Watch a scans directory and process receipts continuously.",
        prefix=["--mode", "watch"],
    )

    _register_passthrough(
        subparsers,
        name="single",
        module_name="main_paperless_flow",
        help_text="Process a single image or PDF once and exit.",
        prefix=["--mode", "single"],
    )

    _register_passthrough(
        subparsers,
        name="extract",
        module_name="extract_metadata",
        help_text="Extract structured metadata from an image or PDF.",
    )

    overlay_parser = subparsers.add_parser(
        "overlay",
        help="Create searchable PDFs with invisible text or run overlay watchers.",
        description=(
            "Interact with the overlay tooling. Subcommands mirror the original "
            "preconsume_overlay_pdf.py modes."
        ),
    )
    overlay_subparsers = overlay_parser.add_subparsers(
        dest="overlay_command", required=True
    )
    for mode, help_text in {
        "cli": "Convert a single image to PDF using the CLI mode.",
        "watch": "Watch a folder and generate PDFs automatically.",
        "preconsume": "Run the Paperless pre-consume hook mode.",
    }.items():
        _register_passthrough(
            overlay_subparsers,
            name=mode,
            module_name="preconsume_overlay_pdf",
            help_text=help_text,
            prefix=["--mode", mode],
        )

    _register_passthrough(
        subparsers,
        name="upload",
        module_name="upload_paperless",
        help_text="Upload a file to Paperless with metadata enforcement.",
    )

    _register_passthrough(
        subparsers,
        name="verify",
        module_name="verify_invinsible_text",
        help_text="Inspect a PDF for invisible text overlays.",
    )

    _register_passthrough(
        subparsers,
        name="transcribe",
        module_name="ollama_transcriber",
        help_text="Transcribe receipts via Ollama vision models.",
    )

    _register_passthrough(
        subparsers,
        name="scan-listener",
        module_name="scan_event_listener",
        func_name="main_scaneventlistener",
        help_text="Run the standalone scan event listener loop.",
    )

    args = parser.parse_args(provided)
    code = args.handler(args)
    LOG.info(f"Subcommand '{args.command}' finished with exit code {code}.")
    return code


if __name__ == "__main__":
    sys.exit(main())
