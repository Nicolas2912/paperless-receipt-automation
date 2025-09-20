# Paperless Receipt Automation (Windows)

Automates turning scanned receipts into searchable PDFs and uploads them to Paperless‑ngx with sensible metadata. Optimized for Windows 11 + Conda. Includes a robust watcher, invisible-text PDF overlay, metadata extraction, and a processed index to avoid duplicates.

Highlights:
- Watches a scans folder for new JPG/JPEG/PDF and processes automatically.
- Transcribes images via a local Ollama vision model (configurable).
- Creates PDFs with an invisible text layer (PyMuPDF render_mode=3).
- Extracts metadata (date, merchant, amount) to build the title and map tags.
- Ensures correspondent, document type, and tags in Paperless‑ngx.

See diagrams.md for architecture flowcharts and sequence diagrams.

## Requirements

- Windows 11
- Conda environment named `paperless`
- Install dependencies via requirements.txt
- Paperless‑ngx instance + API token
- Optional: Local Ollama with a vision model (default: `qwen2.5vl-receipt:latest`)

## Installation

1) Activate Conda environment

```powershell
conda activate paperless
```

2) Install Python dependencies into the env

```powershell
python -m pip install -r requirements.txt
```

## Configuration

- `.env` in the repo root (or an ancestor of your CWD):

  ```env
  PAPERLESS_TOKEN=your_paperless_api_token_here
  PAPERLESS_BASE_URL=http://localhost:8000
  OLLAMA_URL=http://localhost:11434
  OLLAMA_MODEL=qwen2.5vl-receipt:latest
  LOG_LEVEL=INFO
  # LOG_FILE=run.log
  ```

- `scan-image-path.txt`: first non-empty, non-comment line must point to your scans folder. Examples:

  ```text
  C:\\Users\\<you>\\Scans\\Images
  PATH=C:\\Users\\<you>\\Scans\\Images
  "C:\\Users\\<you>\\Scans\\Images"
  %USERPROFILE%\\Scans\\Images
  ```

  Notes:
  - Quotes and environment variables are supported and expanded.
  - Windows paste mistakes like `C:Users...` are auto-repaired.

- `tag_map.json` (optional): map a normalized merchant to a single tag name to enforce on upload, e.g.:

  ```json
  {
    "familia": "Verbrauchermarkt",
    "dm": "Drogerie",
    "netto": "Discounter"
  }
  ```

## Usage (Unified CLI)

All entry points live under `src/paperless_automation`. Use:

- Watch mode (continuous):

  ```powershell
  python -m paperless_automation watch --base-url http://<host>:<port>
  ```

- Single-file mode (image or PDF):

  ```powershell
  python -m paperless_automation single --source "C:\path\to\file.jpg" --base-url http://<host>:<port>
  ```

- Explicit flow command (equivalent to the aliases above):

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
- `--insecure` to skip TLS verification for Paperless calls
- `--timeout` for Paperless HTTP requests (default 60s)

Additional utilities:
- Verify overlay: `python -m paperless_automation verify --pdf C:\path\to\file.pdf [--page 1]`
- Transcribe: `python -m paperless_automation transcribe --source C:\path\to\image.jpg --ollama-url http://localhost:11434 --ollama-model qwen2.5vl-receipt:latest`
- Extract metadata: `python -m paperless_automation extract --source C:\path\to\file.pdf`
- Overlay tools:
  - `overlay cli`: `python -m paperless_automation overlay cli --image <in> --output <out> [--text "..."] [--ollama-url ... --ollama-model ...]`
  - `overlay watch`: `python -m paperless_automation overlay watch --output-dir <dir> [--watch-dir <dir>] [--ollama-url ... --ollama-model ...]`
  - `overlay preconsume`: run inside Paperless pre-consume hook using `DOCUMENT_WORKING_PATH`
- Standalone watcher (prints new files): `python -m paperless_automation scan-listener`

## How It Works

Core modules:
- `src/paperless_automation/orchestrator/flow.py`: high-level `ReceiptFlow`, config, and run modes.
- `src/paperless_automation/orchestrator/watch.py`: `ScanEventListener`, watch-dir resolution from `scan-image-path.txt`.
- `src/paperless_automation/orchestrator/transcribe.py`: `transcribe_image` via Ollama `/api/chat` (vision) with `<eot>` trimming.
- `src/paperless_automation/orchestrator/overlay.py`: create PDF with invisible text (`render_mode=3`).
- `src/paperless_automation/orchestrator/metadata.py`: transcript heuristics; falls back to registry extractors.
- `src/paperless_automation/metadata/extractors.py`: registry with PDF rules (e.g., REWE) and LLM-vision extractor.
- `src/paperless_automation/orchestrator/rename.py`: Windows-safe filename scheme `YYYY-MM-DD_<Korrespondent>_<id>`.
- `src/paperless_automation/orchestrator/upload.py`: build upload fields, ensure resources, upload, enforce exact tags.
- `src/paperless_automation/orchestrator/index.py`: SQLite processed index under `var/paperless_db/` + initial sync.
- `src/paperless_automation/paperless/client.py`: Paperless‑ngx API client.

End-to-end (watch mode):
1) Detect new files; compute SHA-256; skip if already processed (marks seen).
2) For images: transcribe via Ollama → overlay invisible text PDF. For PDFs: skip overlay.
3) Extract metadata: transcript heuristics → registry (PDF rules, LLM‑vision for images if used directly).
4) Rename image/PDF to `YYYY-MM-DD_<Korrespondent>_<id>`.
5) Upload to Paperless (`/api/documents/post_document/`), then best‑effort resolve doc id if missing.
6) Enforce tags via `PATCH /api/documents/{id}` and record in processed index.
7) Paperless indexes; OCR is skipped due to embedded text.

## Data Locations

- Generated PDFs: `var/generated_pdfs/` (default; configurable via `--output-dir`).
- Processed index DB: `var/paperless_db/paperless.sqlite3` (auto-created, git‑ignored).

## Troubleshooting

- Token missing: ensure `PAPERLESS_TOKEN` is set or present in `.env`.
- Folder path: `scan-image-path.txt` must point to an existing folder.
- Ollama not reachable: confirm `OLLAMA_URL` and model availability.
- Overlay not visible: use `python -m paperless_automation verify --pdf <file.pdf>`.
- Windows paths: `fix_windows_path_input` repairs common `C:Users...` mistakes.
- Verbose logs: set `LOG_LEVEL=DEBUG` and optionally `LOG_FILE`.

## Security

- Never commit tokens. Use `.env` or environment variables.
- The client avoids logging auth headers; logs are parameterized and context‑rich.

## License

MIT License. See LICENSE.
