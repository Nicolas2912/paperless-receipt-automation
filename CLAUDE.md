# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Automated receipt processing for Paperless-ngx on Windows 11. Watches a scans folder, transcribes receipts via Ollama vision models, generates searchable PDFs with invisible text overlays, extracts metadata (date/merchant/amount), and uploads to Paperless-ngx with proper tags and correspondent mapping.

Key features:
- Watch mode for continuous processing + single-file mode
- Invisible text PDF overlay (PyMuPDF render_mode=3) for OCR-free indexing
- SQLite processed-index to prevent duplicate uploads
- Product database extraction (OpenAI GPT-5-mini) for detailed line-item tracking
- Transcript-based + PDF-based + LLM-vision metadata extractors

## Commands

**Environment setup:**
```powershell
conda activate paperless
python -m pip install -r requirements.txt
```

**Watch mode (continuous):**
```powershell
python -m paperless_automation watch --base-url http://localhost:8000
```

**Single file processing:**
```powershell
python -m paperless_automation single --source "C:\path\to\receipt.jpg"
```

**Utilities:**
- Transcribe: `python -m paperless_automation transcribe --source <img> --ollama-url <url> --ollama-model <model>`
- Extract metadata: `python -m paperless_automation extract --source <file>`
- Verify PDF overlay: `python -m paperless_automation verify --pdf <file.pdf>`
- Product DB init: `python -m paperless_automation productdb init`
- Product DB extract: `python -m paperless_automation productdb extract --source <img>`

**Tests:**
```powershell
pytest -q
```

**Debug mode:**
Set `LOG_LEVEL=DEBUG` and optionally `LOG_FILE=run.log` in `.env`.

## Architecture

### Pipeline Flow (src/paperless_automation/orchestrator/flow.py)

**Image processing:**
1. Hash check against processed index → skip if seen
2. Transcribe via Ollama vision (`transcribe.py`)
3. Create searchable PDF with invisible text overlay (`overlay.py`)
4. Extract metadata from transcript + heuristics (`metadata.py`)
5. Rename image + PDF to `YYYY-MM-DD_<Korrespondent>_<id>` (`rename.py`)
6. Upload to Paperless via `/api/documents/post_document/` (`upload.py`)
7. Enforce tags via PATCH `/api/documents/{id}`
8. Record in processed index (`index.py`)
9. Run product DB extraction (OpenAI GPT-5-mini) for line items (`productdb/`)

**PDF processing:**
Skip overlay; extract metadata directly via PDF rules or LLM-vision extractor.

### Key Modules

- **cli/main.py**: Unified CLI entry point; all subcommands (flow, watch, single, extract, verify, transcribe, productdb)
- **orchestrator/flow.py**: `ReceiptFlow` class orchestrates end-to-end pipeline
- **orchestrator/watch.py**: `ScanEventListener` for filesystem polling; reads watch dir from `scan-image-path.txt`
- **orchestrator/transcribe.py**: Ollama `/api/chat` (vision) integration with `<eot>` trimming
- **orchestrator/overlay.py**: PyMuPDF invisible text layer creation (`render_mode=3`)
- **orchestrator/metadata.py**: Transcript heuristics → fallback to registry extractors
- **metadata/extractors.py**: Extractor registry (PDF rules, LLM-vision)
- **orchestrator/upload.py**: Build upload fields, ensure resources (correspondents, document types, tags), upload + enforce exact tags
- **orchestrator/index.py**: SQLite processed index at `var/paperless_db/paperless.sqlite3`; initial sync from Paperless API
- **orchestrator/rename.py**: Windows-safe filename scheme `YYYY-MM-DD_<Korrespondent>_<id>`
- **paperless/client.py**: Paperless-ngx API client (GET/POST/PATCH)
- **domain/models.py**: Core domain models (ReceiptMetadata)
- **productdb/**: Product database extraction pipeline (db.py, models.py, parser.py, extraction.py, service.py)

### Data Flow

**Watch mode:**
1. `ScanEventListener` polls watch dir for new images/PDFs
2. `ReceiptFlow.process_source()` dispatches to `process_image()` or `process_pdf()`
3. Processed index prevents re-upload
4. Generated PDFs saved to `var/generated_pdfs/` (default)
5. Product DB extraction runs on original image (preserved copy) after upload

**Single mode:**
Process one file immediately; no watch loop.

### Configuration

- `.env` in repo root: `PAPERLESS_TOKEN`, `PAPERLESS_BASE_URL`, `OLLAMA_URL`, `OLLAMA_MODEL`, `LOG_LEVEL`, `LOG_FILE`
- `scan-image-path.txt`: First non-comment line is watch directory (supports `%USERPROFILE%`, quotes, Windows path repair)
- `tag_map.json`: Map normalized merchant names to tag names (optional)

### Product Database

Schema in `product-database-plan.md`. Tracks:
- Receipts (merchant, date/time, totals, payment method)
- Line items (product name, quantity, unit price, tax rate)
- Addresses, merchants, extraction runs, raw artifacts

Implementation: `orchestrator/productdb/` (service layer, extraction, parser, DB, models)

## Project Structure

```
src/paperless_automation/
├── cli/            # Unified CLI entry (main.py)
├── orchestrator/   # End-to-end flow and services (preferred for new code)
│   ├── productdb/  # Product database extraction (OpenAI GPT-5-mini)
│   ├── flow.py     # ReceiptFlow orchestrator
│   ├── watch.py    # ScanEventListener
│   ├── transcribe.py
│   ├── overlay.py
│   ├── metadata.py
│   ├── rename.py
│   ├── upload.py
│   ├── index.py
│   └── verify.py
├── metadata/       # Extractor registry
├── paperless/      # API client
├── domain/         # Domain models
├── config.py       # Config loaders (.env, tag_map.json)
├── logging.py      # Structured logging (get_logger)
└── paths.py        # Path helpers (expand_abs, find_project_root, var_dir)

var/                # Git-ignored runtime data
├── generated_pdfs/ # Searchable PDFs (default output)
├── paperless_db/   # Processed index (paperless.sqlite3)
└── productdb/      # Product DB (products.sqlite3), preserved images

tests/              # pytest tests
scripts/            # Test/debugging scripts
```

## Coding Conventions

- Python 4-space indentation
- Type hints and docstrings for new code
- Naming: `snake_case` (modules/functions), `PascalCase` (classes), `UPPER_SNAKE_CASE` (constants)
- Logging: Use `paperless_automation.logging.get_logger(__name__)`. Write descriptive messages with context (follow "good prints" spirit). Never log secrets.
- Paths: Use `paths.py` helpers (`expand_abs`, `var_dir`, `find_project_root`). Be Windows-safe.
- Prefer editing existing files over creating new ones. Avoid writing to `src/` for outputs; use `var/` at repo root.

## Commit Style

Follow Conventional Commits:
- Format: `type(scope): subject` (subject ≤ 50 chars), body wrapped ≈72 chars
- Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`
- Explain **why** the change is needed and the **effect**
- Prepare messages in a temp file to ensure clarity and wrapping

## Environment Requirements

- Windows 11
- Conda environment named `paperless`
- Paperless-ngx instance + API token
- Ollama with vision model (default: `qwen2.5vl-receipt:latest`)
- OpenAI API key for product DB extraction (in `.env`: `OPENAI_API_KEY`)

## Notes

- Generated artifacts default to `var/` at repo root (not under `src/`)
- Processed index: `var/paperless_db/paperless.sqlite3`
- Product DB: `var/productdb/products.sqlite3`
- Preserved images for product extraction: `var/productdb/sources/`
- Watch directory resolution: `scan-image-path.txt` or `--watch-dir` override
- Initial sync: On first watch mode run, syncs existing Paperless documents into processed index
- Product DB extraction: Runs **after** upload to Paperless; uses preserved original image
