# MCP Server & Client Plan (GLM-4.5 via OpenRouter)

## 1. Objectives
- Serve Paperless product database insights through MCP tools so LLMs can analyze spending patterns, habits, and improvement areas.
- Provide a reusable MCP client that translates MCP tool contracts into OpenRouter-compatible tool calls targeting the `z-ai/glm-4.5` model.
- Keep sensitive receipt data on-device while allowing the LLM to reason over sanctioned summaries.

## 2. Architecture Overview
- **MCP Server (`paperless-mcp`)**: Runs inside this repo, exposes tools for querying the product DB (`ProductDatabase`) and generating analytics (totals, trends, streaks, merchant profiles). Communicates over stdio per MCP spec.
- **MCP Client Adapter**: Uses Anthropic's MCP client SDK to connect to the server, discovers tool schemas, converts them into OpenAI-compatible tool definitions, and calls OpenRouter's chat completions API (GLM-4.5).
- **LLM Backend**: OpenRouter `z-ai/glm-4.5` model invoked via `openai` SDK with `base_url="https://openrouter.ai/api/v1"`.
- **Session Store & Caching**: Optional layer to persist derived analytics (e.g., last N receipts) to reduce repeated DB hits.
- **Secrets & Config**: `.env` holds OpenRouter API key and toggles for analytics limits. Product DB location resolved via `ProductDatabase` helpers.

## 3. Prerequisites
1. Ensure Conda env `paperless` has recent Python (≥3.10).
2. Install packages (to add in `requirements.txt`):
   - `mcp[cli]` (client + server helpers)
   - `openai>=1.40.0`
   - `python-dotenv`
   - `pydantic` (if not already)
   - `fastjsonschema` (for schema validation if needed)
3. Populate `.env` with:
   ```env
   OPENROUTER_API_KEY=...
   MCP_LOG_LEVEL=INFO
   MCP_ANALYTICS_MAX_ROWS=500
   ```
4. Verify `ProductDatabase` schema initialization by running `python -m paperless_automation.orchestrator.productdb.service` (optional helper CLI).

## 4. Implementation Steps

### 4.1 Repository Layout
- Create module directory `src/paperless_automation/mcp/` with:
  - `__init__.py`
  - `server.py` – MCP server entry point & tool definitions.
  - `tools.py` – business logic wrappers around `ProductDatabase`.
  - `analytics.py` – spend aggregation helpers.
  - `client.py` – OpenRouter-backed MCP client adapter.
  - `schemas.py` – Pydantic models / JSON schemas for tool IO.
  - `config.py` – env + defaults (API key, limits, switch for anonymization).
  - `cli.py` – simple CLI: `python -m paperless_automation.mcp.cli server|client`.

### 4.2 Data Access & Analytics Layer
1. In `tools.py`, wrap `ProductDatabase` read queries:
   - `list_merchants(limit, offset)`
   - `get_receipt(receipt_id)`
   - `search_receipts(keyword, date_range)`
   - `list_recent_purchases(days)`
2. In `analytics.py`, implement derived metrics using raw rows:
   - Monthly spend totals per merchant & category.
   - Rolling averages, purchase frequency, time-of-day histograms.
   - Simple heuristics for habit insights (e.g., "late-night purchases increasing").
3. Add sanitization (drop full addresses if `ANONYMIZE=true`).
4. Unit-test each helper with fixtures that bootstrap temporary SQLite DB (see `tests/test_productdb_parser.py` for pattern).

### 4.3 MCP Server (`server.py`)
1. Use `from mcp.server import Server` to define `paperless-mcp`.
2. Register tools:
   - `list_merchants` – returns merchant summaries.
   - `merchant_profile` – includes spend trend, favorite items.
   - `receipt_details` – returns sanitized receipt lines.
   - `behavior_summary` – aggregates multi-merchant insights.
   - `health_check` – ensures DB accessible and returns counts.
3. For each tool, declare JSON schema derived from Pydantic models (`schemas.py`).
4. Integrate logging via `get_logger` to keep consistent formatting.
5. Expose CLI entry `python -m paperless_automation.mcp.cli server --stdio` that launches the server with stdio transport (using `mcp.server.stdio`).
6. Ensure graceful shutdown (handle `KeyboardInterrupt`, close DB connections).

### 4.4 MCP Client Adapter (`client.py`)
1. Reuse example structure from documentation:
   - Manage `AsyncExitStack`, `ClientSession`, `stdio_client`.
   - Implement `convert_tool_format` to map MCP tool schema to OpenAI function specs.
2. Replace hard-coded model with `MODEL = "z-ai/glm-4.5"` and set `base_url`.
3. Support multi-step tool calling:
   - After LLM returns tool call, execute `session.call_tool`.
   - Append tool response to conversation.
   - Call LLM again to obtain final answer.
4. Add context primer for LLM: describe available tools, focus on behavioural analysis.
5. Implement streaming logs for debugging (`--verbose` flag).
6. Parameterize server command via CLI args or config file.
7. Provide synchronous wrapper for simple prompts (for scripting use).

### 4.5 CLI Wrapper (`cli.py`)
- Add subcommands:
  - `server` – start MCP server (`--stdio`, `--log-level`, `--max-rows`).
  - `client` – interactive REPL, optional `--prompt` for single-shot queries, `--server-path` override.
  - `demo` – run scripted analyses (e.g., monthly summary) for smoke testing.
- Document usage in README and new markdown file under docs/.

### 4.6 Configuration & Security
1. Extend `src/paperless_automation/config.py` to load `OPENROUTER_API_KEY`, `MCP_BACKEND`, `ANONYMIZE_PII`.
2. Do not log API keys or raw receipt lines.
3. Provide toggle to restrict tool outputs to aggregated data only.
4. Optionally add rate limiting or caching to avoid repeated expensive queries.

### 4.7 Testing Strategy
1. Unit tests under `tests/mcp/`:
   - Tool schema validation (ensure `tools.py` outputs match JSON schema).
   - Analytics calculations via synthetic DB fixtures.
2. Integration test using `pytest-asyncio`:
   - Start MCP server in a subprocess with a temp DB.
   - Use MCP client to call `list_merchants` tool and assert response structure.
   - Mock OpenRouter API with `responses` or `respx` to simulate tool-calling conversation.
3. Lint / type-check: add optional mypy config for new modules.

### 4.8 Documentation
1. Update `README.md` with:
   - Overview of MCP integration.
   - Environment variables, CLI usage, security warnings.
2. Produce architecture diagrams (extend `diagrams.md`) describing MCP communication flow.
3. Add troubleshooting section (common errors: missing API key, invalid schema, server handshake failures).

### 4.9 Deployment & Automation
1. Provide PowerShell script `scripts/run_mcp_server.ps1` to launch server inside Conda env.
2. Optionally add Windows service wrapper or scheduled task for background operation.
3. For development, add VSCode tasks / devcontainer command to run server + client.
4. Consider packaging server as standalone (`python -m zipapp`) if sharing externally.

### 4.10 Roadmap Extensions
- Add caching of analytics to `var/productdb/cache.json`.
- Implement authentication layer (API tokens) for MCP tools if exposing beyond localhost.
- Explore exposing same analytics via REST API for non-LLM clients.
- Evaluate migrating to RAG-based tool to fetch embeddings instead of direct SQL queries.

## 5. Validation Checklist
- [ ] `pytest -q` passes with new tests.
- [ ] `python -m paperless_automation.mcp.cli server` starts and advertises tools.
- [ ] `python -m paperless_automation.mcp.cli client` can run sample query using GLM-4.5 and receive behavioral insight.
- [ ] No sensitive raw data leaves the workstation without explicit opt-in.
- [ ] Documentation updated and committed per Conventional Commit guidelines.

