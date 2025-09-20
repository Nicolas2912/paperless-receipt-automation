# Repository Guidelines

This repository automates turning scanned receipts into searchable PDFs and
uploads them to Paperless‑ngx. It targets Windows 11 with a Conda env named
`paperless` and favors clear, verbose logging for fast debugging.

## Project Structure & Module Organization

- `src/paperless_automation/` — main package
  - `cli/` unified CLI entry (`python -m paperless_automation …`)
  - `orchestrator/` end‑to‑end flow and services (preferred for new code)
  - `legacy/` stable scripts kept for compatibility; avoid extending unless needed
  - `domain/`, `metadata/`, `paperless/` domain models, extractors, API client
- `var/paperless_db/` local SQLite processed-index (git-ignored)
- `requirements.txt`, `tag_map.json`, `.env` (user‑provided)
- `tests/` add new tests here (pytest), e.g. `tests/test_flow.py`
  
Note on generated artifacts:
- Generated PDFs default to `var/generated_pdfs/` at the repo root.
- Avoid writing outputs under `src/`. The code resolves paths relative to the
  repository root even when executed from `src/`.

## Build, Test, and Development Commands

- Activate env: `conda activate paperless`
- Install deps: `python -m pip install -r requirements.txt`
- Run (watch): `python -m paperless_automation watch --base-url http://localhost:8000`
- Run (single): `python -m paperless_automation single --source C:\path\file.jpg`
- Useful env: `LOG_LEVEL=DEBUG`, `LOG_FILE=run.log`
- Tests (if present): `pytest -q`

## Coding Style & Naming Conventions

- Python, 4‑space indentation, include type hints and docstrings for new code.
- Names: modules/functions `snake_case`, classes `PascalCase`, constants
  `UPPER_SNAKE_CASE`.
- Logging: use `paperless_automation.logging.get_logger(name)`; write
  descriptive messages with relevant context (keep the project’s “good prints”
  spirit). Do not log secrets.
- Paths: use helpers in `paths.py` (e.g., `expand_abs`) and be Windows‑safe.


## Commit & Pull Request Guidelines

- Commit messages follow Conventional Commits:
  - `type(scope): subject` (subject ≤ 50 chars), body wrapped ≈72 chars.
  - Explain why the change is needed and the effect.
  - Prepare the message in a temp file, then paste into the CLI to ensure
    wrapping and clarity.
- PRs: concise description, link issues, include logs/screenshots for behavior
  changes, note config/env impacts, and add usage examples for CLI changes.
- Keep PRs focused; split unrelated changes.

## Security & Configuration Tips

- Secrets via `.env`; never commit tokens. Required vars: `PAPERLESS_TOKEN`,
  `PAPERLESS_BASE_URL`. Optional: `OLLAMA_URL`, `OLLAMA_MODEL`, `LOG_LEVEL`,
  `LOG_FILE`.
- Avoid printing tokens/headers in logs. Prefer parameterized, context‑rich logs.
