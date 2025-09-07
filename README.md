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

The orchestrator is `main_paperless_flow.py`, with two modes.

- Watch mode (continuous):

  ```powershell
  python .\main_paperless_flow.py --mode watch --base-url http://<host>:<port>
  ```

- Single-file mode (one image):

  ```powershell
  python .\main_paperless_flow.py --mode single --source "C:\path\to\image.jpg" --base-url http://<host>:<port>
  ```

Useful flags / env vars:
- `--base-url` or `PAPERLESS_BASE_URL` (default `http://localhost:8000`)
- `--token` or `PAPERLESS_TOKEN` (read from `.env` if not passed)
- `--output-dir` (default `generated_pdfs`)
- `--ollama-url` or `OLLAMA_URL` (default `http://localhost:11434`)
- `--ollama-model` or `OLLAMA_MODEL` (default `qwen2.5vl-receipt:latest`)
- `--insecure` to skip TLS verification when using HTTPS test setups

Processing steps (watch mode):
1) Detect a new JPEG via `scan_event_listener.py`.
2) Transcribe via Ollama (`ollama_transcriber.py`).
3) Create a searchable PDF with invisible text (`preconsume_overlay_pdf.py`).
4) Extract metadata (heuristics on the transcript; fallback to LLM on PDF).
5) Rename image + PDF to `YYYY-MM-DD_<Merchant>_<id>.*` (`rename_documents.py`).
6) Upload to Paperless‑ngx (`upload_paperless.py`) and enforce mapped tags.

## Script Tooling

- `scan_event_listener.py`
  - Reads `scan-image-path.txt`, normalizes Windows paths, and detects new JPEGs.
  - Exported class: `ScanEventListener` with `scan_once()` and history helpers.

- `preconsume_overlay_pdf.py`
  - Create PDF with invisible text layer. Three subcommands:
    - `cli`: `--image`, `--output`, `--text|--text-file|--ollama-*`
    - `preconsume`: run under Paperless pre-consume using `DOCUMENT_WORKING_PATH`
    - `watch`: watch a folder and write PDFs to `--output-dir`

- `extract_metadata.py`
  - Calls Ollama to return strict JSON with keys
    `{korrespondent, ausstellungsdatum, betrag_value, betrag_currency, dokumenttyp}`.
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

## License

MIT License. See LICENSE.
