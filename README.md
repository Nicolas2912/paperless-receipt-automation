# Paperless Helper Tools (Windows)

This project automates a simple receipt-to-Paperless flow on Windows 11:

- Watches a directory for new scan images (JPEGs).
- Runs OCR + overlays invisible text to generate a searchable PDF.
- Extracts metadata (date, merchant, amount) and formats a title.
- Uploads the PDF to Paperless-ngx using the API token.

Everything prints detailed logs to the console to make it easy to debug.

## Requirements

- Windows 11
- An existing Conda environment named `paperless`
- Python dependencies used by the scripts (install in your `paperless` env)
- A running Paperless-ngx instance you can reach from this machine

## Quick Start

1) Activate Conda environment

```powershell
conda activate paperless
```

2) Provide configuration files (required)

- `.env` file next to the scripts with your API token:

  ```env
  PAPERLESS_TOKEN=your_paperless_api_token_here
  ```

- `scan-image-path.txt` file next to the scripts with a single line containing the absolute path to the folder where your scanner drops images, for example:

  ```text
  C:\\Users\\<you>\\Scans\\Images
  ```

  Notes:
  - You can also use a `KEY=VALUE` style like `PATH=C:\\\\Users\\\\<you>\\\\Scans\\\\Images`.
  - Quotes and environment variables (e.g., `%USERPROFILE%`) are supported.

3) Run the main flow

The main script supports two modes: `watch` (default) and `single`.

- Watch mode (continuously watches for new JPEGs):

  ```powershell
  python .\\main_paperless_flow.py --mode watch --base-url http://<paperless-host>:<port>
  ```

- Single-file mode (process one specific image):

  ```powershell
  python .\\main_paperless_flow.py --mode single --source "C:\\path\\to\\image.jpg" --base-url http://<paperless-host>:<port>
  ```

You can override defaults using environment variables or CLI flags:
- `PAPERLESS_BASE_URL` or `--base-url` (default: `http://localhost:8000`)
- `PAPERLESS_TOKEN` or `.env` file value
- `OLLAMA_URL` and `OLLAMA_MODEL` if you use local transcription

## Important Files

- `main_paperless_flow.py`: Orchestrates watching, OCR overlay, metadata extraction, and upload.
- `scan_event_listener.py`: Reads `scan-image-path.txt` and detects new JPEG images.
- `preconsume_overlay_pdf.py`: Generates searchable PDFs with invisible text overlay.
- `extract_metadata.py`: Extracts structured data used to build titles and fields.
- `upload_paperless.py`: Handles the HTTP upload to Paperless-ngx and sets metadata.

## Git and Sensitive Files

This repository intentionally does not track:
- `.env` (contains your API token)
- `scan-image-path.txt` (contains your local paths)
- Any `*.pdf` files (generated output)
- The `generated_pdfs/` directory

These are covered by the `.gitignore` created for this project.

## Troubleshooting

- Missing token: Ensure `PAPERLESS_TOKEN` is set in your environment or `.env`.
- Wrong watch folder: Confirm `scan-image-path.txt` contains a valid, existing folder path.
- Network errors: Verify `--base-url` points to your Paperless-ngx and is reachable.
- Verbose logging: All scripts emit detailed prints to help diagnose issues.

## License

Private project. No license specified.
