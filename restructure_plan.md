Current Architecture (High Level)

  - Watch: scan_event_listener polls a folder for .jpg/.jpeg/.pdf and prints changes.
  - OCR/Transcript: ollama_transcriber streams /api/chat, strips <eot>, returns text.
  - PDF overlay: preconsume_overlay_pdf builds single-page PDF with invisible text; supports CLI, watch, and Paperless pre-consume.
  - Metadata: extract_metadata uses rule-based PDF extractor (PyMuPDF) and LLM fallback for images; normalizes date/amount/currency and builds titles.
  - Normalization/tags: merchant_normalization cleans vendor names and maps tags (exact/substr/fuzzy).
  - Rename: rename_documents applies YYYY-MM-DD_<Korrespondent>_<id>.* across image/PDF.
  - Upload: upload_paperless ensures correspondent/type/tags, posts document, and patches tags to exact set.
  - Indexing: processed_index keeps content-hash→doc_id mapping in SQLite to avoid reprocessing.
  - Verification: verify_invinsible_text checks embedded text and 3 Tr ops.

  All modules have helpful prints for debugging; many utilities are duplicated across files (path fixes, token loading).

  Pain Points & Risks

  - Cross‑cutting duplication:
      - Path repair helpers repeated in 3 modules.
      - Token/read .env logic duplicated (main_paperless_flow and upload_paperless).
      - Date/amount normalization appears in multiple places.
  - Mixed responsibilities:
      - main_paperless_flow handles config, orchestration, networking, and API polling in one large file.
      - preconsume_overlay_pdf mixes CLI/watch/LLM dispatch with rendering code.
  - API coupling:
      - Direct requests calls scattered; no central Paperless client (no session reuse, retries, or typed errors).
      - Polling/title search logic duplicated in main and processed_index.
  - CLI fragmentation:
      - Several entry points with overlapping flags; no single cohesive CLI UX.
  - Encoding:
      - README/diagrams show mojibake (likely non‑UTF‑8 saves); code has “€” and umlaut artifacts in string literals.
  - Naming/typos:
      - verify_invinsible_text.py (typo); function/constant naming styles vary.
  - Observability:
      - Rich prints but no structured log levels, timestamps everywhere, or optional log file.
  - Extensibility:
      - Metadata extraction not pluggable per merchant/type; future variants require editing core files.
  - Testing:
      - No unit tests for critical parsing/normalization/renaming; risk of regressions.

  Proposed Structure (src/ Layout)

  - src/paperless_automation/
      - __init__.py
      - config.py — env + CLI config dataclasses; load .env; tag_map loader.
      - logging.py — thin wrapper over logging that still prints to stdout with consistent prefixes and levels.
      - paths.py — Windows-safe path helpers (single source of truth).
      - domain/models.py — ExtractedMetadata, UploadFields, TagMap types.
      - domain/normalize.py — merchant normalization, date/amount currency detect.
      - io/watcher.py — ScanEventListener; optional watchdog backend later.
      - ocr/transcriber.py — Ollama client (streaming), vision prompt constants.
      - pdf/overlay.py — pixmap loader + create_pdf_with_invisible_text.
      - pdf/verify.py — invisible text verification.
      - metadata/extractors.py — PDF rules, LLM fallback, extractor registry by doc type/vendor.
      - paperless/client.py — typed API client (requests.Session, timeouts, retries, helpers).
      - paperless/uploader.py — upload + ensure resources + tag enforcement.
      - state/index.py — SQLite repository: ensure_db, hashes, CRUD, sync with Paperless.
      - cli/main.py — one CLI with subcommands:
          - watch, single, overlay, extract, upload, verify, index sync.
  - pyproject.toml with console_scripts for paperless-auto.
  - tests/ targeting utility layers first (normalization, amounts, renaming, client URL building).

  This separation keeps logging/paths/config reusable, gives you clear seams for future additions (new extractors, new upload logic, different OCR).

  Stepwise Refactor Plan (Safe, Incremental)

  - Phase 1: Foundations (no behavior change)
      - Add src/ package, config.py, logging.py, paths.py, domain/models.py.
      - Replace ad‑hoc prints with a logger wrapper that prints with levels: log.info, log.debug, etc.; keep existing verbosity by default.
      - Centralize .env and tag_map loading (single function).
  - Phase 2: Utilities de‑dup
      - Move Windows path fixes, date/amount normalization into paths.py and domain/normalize.py.
      - Update callers to use shared utilities.
  - Phase 3: Paperless client
      - Introduce paperless/client.py with methods: post_document, get_document, find_document_by_title, ensure_{correspondent,tag,document_type}.
      - Wire upload_paperless to use the client; keep function names for compatibility.
  - Phase 4: Metadata extractors
      - Move PDF rules + LLM fallback to metadata/extractors.py with a registry pattern:
          - register("pdf:rewe", RewePdfExtractor)
          - register("image:generic", LlmImageExtractor)
      - extract_from_source delegates by media type/vendor; easy to add merchants later.
  - Phase 5: CLI unification
      - Create cli/main.py with subcommands mirroring current scripts.
      - Keep thin shims: current .py files import and call into the new CLI to avoid breaking workflows.
  - Phase 6: Orchestrator split
      - Reduce main_paperless_flow to orchestration only:
          - watch → transcribe → overlay → extract → rename → upload → index
      - Use small services from the new modules; remove in‑file API polling logic.
  - Phase 7: Tests and checks
      - Add tests for normalization, amount/date parsing, renaming, tag resolution, and client URL building/timeouts.
      - Add pre-commit with Ruff + Black; optional mypy basic checks.

  Concrete Improvements (High Impact First)

  - Unified config
      - Add Config dataclass with env var names and CLI defaults, e.g.:
          - PAPERLESS_BASE_URL, PAPERLESS_TOKEN, OLLAMA_URL, OLLAMA_MODEL, OUTPUT_DIR, WATCH_DIR.
      - One function load_config() reads env + .env + CLI, prints a one‑line summary without secrets.
  - Central logging wrapper
      - Keep “good prints” while structuring levels:
          - log = setup_logger(name, level=os.getenv("LOG_LEVEL","INFO"))
          - Prefix component, timestamp, and optionally write to a rolling file in paperless_db/logs/.
  - Paperless API robustness
      - Use requests.Session with default timeouts and limited retries (e.g., 0.5s backoff up to 3 attempts) for GET/POST/PATCH.
      - Normalize URL building in one place; avoid manual rstrip('/') everywhere.
  - Extractor plugin pattern
      - Base class Extractor with supports(path)->bool and extract(path)->ExtractedMetadata.
      - Registry so adding “IKEA PDF rules” doesn’t touch generic code.
  - Single path helpers
      - Deduplicate _fix_windows_path_input into paths.py. Replace all call sites.
  - Rename and id generation
      - Keep current behavior; move to rename/ids.py and cover with tests to lock the contract.
  - Indexing
      - Wrap SQLite CRUD in a small repository class; ensure WAL pragmas and error prints remain.
      - Add a “dry-run sync” flag to see what would be written.
  - CLI UX
      - paperless-auto watch --output-dir generated_pdfs --base-url http://...
      - paperless-auto single --source <img> --upload
      - paperless-auto overlay cli --image <in> --output <out>
      - paperless-auto verify --pdf <file> [--page 1]
  - Encoding fixes
      - Save README/diagrams/code as UTF‑8; replace “�” artifacts in strings like currency checks.
      - Central currency detection: look for “€” explicitly and EUR token case-insensitively.
  - Naming tidy-ups (non‑breaking with shims)
      - Rename verify_invinsible_text.py → verify_invisible_text.py. Keep the old filename importing the new module with a deprecation print.
      - Consistent module naming: paperless_client.py not _api_* scattered helpers.

  Sample Code Sketches (Python, minimal)

  - logging.py
      - Simple wrapper that prints and supports levels.
  - config.py
      - Dataclass loader with .env parsing and safe summaries.
  - paperless/client.py
      - Session with timeouts/retries and clear debug prints.

  If you want, I can generate these three files next.

  Tooling, Testing, Ops Recommendations

  - Packaging
      - Add pyproject.toml with project.scripts entry points.
      - Provide environment.yml for Conda and pinned requirements.txt.
  - Lint/Format/Types
      - Ruff (fast lint), Black (format), isort, mypy (module-level typed stubs).
      - Pre-commit hooks to keep outputs clean.
  - Tests
      - tests/test_normalize.py, tests/test_amount_date.py, tests/test_renamer.py, tests/test_paperless_client.py (use responses to mock HTTP).
  - Observability
      - LOG_LEVEL, LOG_FILE, and --verbose flag (maps to DEBUG).
      - Time each pipeline step and print durations.
  - Reliability
      - Add bounded retries on Paperless and Ollama calls; surface a single error line with suggested next actions.
  - Docs
      - Clean UTF‑8 README; add CLI help blocks.
      - Add a short “Architecture” section pointing to diagrams.md (UTF‑8 fixed).
  - Backwards Compatibility
      - Keep current scripts as thin shims calling the new CLI for one or two releases.
      - Print deprecation notices with migration hints.

  Suggested Next Actions (Order)

  - Approve me to scaffold src/ with config/logging/paths and rewire only one script (e.g., upload_paperless.py) to the new Paperless client to validate the pattern.
  - Migrate shared helpers and remove duplicates.
  - Unify CLI, keep shims, and update README to match.
  - Add initial unit tests for normalization and renaming (fast wins).
  - Fix encoding in README/diagrams and the “invisible” typo.