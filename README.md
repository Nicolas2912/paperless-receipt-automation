# Paperless Receipt Automation (Windows)

End-to-end automation to turn raw scan images (receipts) into searchable
PDFs and upload them to Paperless‑ngx with sensible metadata. Optimized for
Windows 11 + Conda, with detailed debug prints throughout.

Highlights:
- Watches a scans folder for new JPEGs and processes them automatically.
- Transcribes the receipt with a local Ollama vision model (configurable).
- Creates a PDF with an invisible text layer (render_mode=3) via PyMuPDF.
- Extracts structured metadata (date, merchant, amount) to build the title.
- Ensures correspondent, document type, and mapped tags in Paperless‑ngx.

See diagrams.md for architecture flowcharts and sequence diagrams.

## Requirements

- Windows 11
- Conda environment named `paperless` (already created on your machine)
- Python packages installed into that env:
  - `pymupdf` (imported as `fitz`)
  - `requests`
- A reachable Paperless‑ngx instance and API token
- Optional: Local Ollama with a vision model (default: `qwen2.5vl-receipt:latest`)

## Installation

1) Activate Conda environment

```powershell
conda activate paperless
```

2) Install Python dependencies into the env

```powershell
python -m pip install --upgrade pip
pip install pymupdf requests
```

## Configuration

- `.env` (next to the scripts):

  ```env
  PAPERLESS_TOKEN=your_paperless_api_token_here
  PAPERLESS_BASE_URL=http://localhost:8000
  OLLAMA_URL=http://localhost:11434
  OLLAMA_MODEL=qwen2.5vl-receipt:latest
  ```

- `scan-image-path.txt` (next to the scripts): first non-empty, non-comment line
  must point to your scans folder. Examples:

  ```text
  C:\\Users\\<you>\\Scans\\Images
  PATH=C:\\Users\\<you>\\Scans\\Images
  "C:\\Users\\<you>\\Scans\\Images"
  %USERPROFILE%\\Scans\\Images
  ```

  Notes:
  - Quotes and environment variables are supported and expanded.
  - On Windows, common paste mistakes like `C:Users...` are auto-repaired.

- `tag_map.json` (optional): map a lowercase merchant name to a single tag
  name to enforce on upload, e.g.:

  ```json
  {
    "familia": "Verbrauchermarkt",
    "dm": "Drogerie",
    "netto": "Discounter"
  }
  ```

## Main Usage

All entry points now live under `src/paperless_automation`. Use the unified CLI:

- Watch mode (continuous):

  ```powershell
  python -m paperless_automation watch --base-url http://<host>:<port>
  ```

- Single-file mode (one image):

  ```powershell
  python -m paperless_automation single --source "C:\path\to\image.jpg" --base-url http://<host>:<port>
  ```

- Equivalent, explicit form with the flow command:

  ```powershell
  python -m paperless_automation flow --mode watch
  python -m paperless_automation flow --mode single --source "C:\path\to\file.pdf"
  ```

Useful flags / env vars:
- `--base-url` or `PAPERLESS_BASE_URL` (default `http://localhost:8000`)
- `--token` or `PAPERLESS_TOKEN` (read from `.env` if not passed)
- `--output-dir` (default `var/generated_pdfs` under the repo root)
- `--ollama-url` or `OLLAMA_URL` (default `http://localhost:11434`)
- `--ollama-model` or `OLLAMA_MODEL` (default `qwen2.5vl-receipt:latest`)
- `--insecure` to skip TLS verification when using HTTPS test setups

Processing steps (watch mode):
1) Detect new files via `paperless_automation.orchestrator.watch.ScanEventListener`.
2) Transcribe via Ollama (`paperless_automation.orchestrator.transcribe`).
3) Create a searchable PDF with invisible text (`paperless_automation.orchestrator.overlay`).
4) Extract metadata (heuristics + registry; includes LLM for images).
5) Rename image + PDF to `YYYY-MM-DD_<Merchant>_<id>.*` (`paperless_automation.orchestrator.rename`).
6) Upload to Paperless‑ngx (`paperless_automation.orchestrator.upload`) and enforce mapped tags.

## Script Tooling

- Watcher: `paperless_automation.orchestrator.watch`
  - Reads `scan-image-path.txt`, normalizes Windows paths, and detects new JPEGs/PDFs.
  - Class: `ScanEventListener` with `scan_once()` and history helpers.

- Overlay: `paperless_automation.orchestrator.overlay`
  - Create PDF with invisible text layer. CLI mirrors previous modes via `python -m paperless_automation overlay ...`.
  - Subcommands: `cli`, `preconsume`, `watch`.

- Metadata: `paperless_automation.orchestrator.metadata` / `paperless_automation.metadata.extractors`
  - Heuristics, registry (PDF rules), and LLM-vision extractor for images.
  - Builds titles like `YYYY-MM-DD - <Merchant> - 12,34` (German number format).

- `upload_paperless.py`
  - Posts to `/api/documents/post_document/` with title/created/correspondent/type/tags.
  - Resolves or creates correspondents, document types, and tag IDs.
  - After upload, best-effort resolves the document ID and PATCHes tags to the
    exact mapped set from `tag_map.json` (avoids duplicates from server rules).

- `ollama_transcriber.py`
  - Encodes the image and sends a vision prompt to Ollama `/api/chat`, streaming
    the response and returning the final text (stripped of `<eot>` if present).

- `rename_documents.py`
  - Sanitizes merchant names for filenames and keeps a shared numeric id across
    the image and PDF to avoid collisions.

- `verify_invinsible_text.py`
  - Utility to check a PDF for embedded text and confirm `3 Tr` (invisible text).

## Troubleshooting

- Token missing: ensure `PAPERLESS_TOKEN` is set or present in `.env`.
- Folder path: `scan-image-path.txt` must point to an existing folder.
- Ollama not reachable: confirm `OLLAMA_URL` and model availability.
- PDF not searchable: use `verify_invinsible_text.py --pdf <file.pdf>` to inspect.
- Windows paths: scripts auto-repair common `C:Users...` paste mistakes.
- Verbose logs: all modules print detailed context for faster debugging.

## Data Locations

- Generated PDFs default to `var/generated_pdfs/` at the repository root.
  Pass `--output-dir` to override.
- The processed index database lives at `var/paperless_db/paperless.sqlite3`.
  It is created automatically and ignored by git.

## License

MIT License. See LICENSE.
