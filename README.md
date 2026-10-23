# Zonic MCP Server (FastAPI)

An MCP server with one tool, `execute`, that runs a set of **database** (MSSQL script or
stored procedure), **API** (any HTTP verb) and **function** (installed Python callable)
tools in a single round-trip, sequentially, concurrently or as a dependency pipeline.

This is the FastAPI port of `ZonicMCPServer_DjangoApp`. The engine, executors, cache,
models and MCP contract are unchanged; Django is replaced by FastAPI for hosting and by
`pydantic-settings` for configuration. The conversion report is in
`../docs/mcp-server/mcp-server-fastapi-conversion-report.md`.

## Run locally

```powershell
uv venv .venv --python 3.13                      # once
uv pip install --python .venv\Scripts\python.exe -e ".[dev]"
copy .env.example .env                           # then adjust
.\run.bat                                        # http://localhost:8300 (or: .\run.bat 9000)
```

Or directly: `.venv\Scripts\python.exe -m uvicorn app.main:app --port 8300`.

| Path | What it does |
|---|---|
| `POST /mcp` or `/mcp/` | MCP streamable-HTTP transport (JSON-RPC 2.0); stateless JSON responses by default |
| `GET /health` | Liveness: `{"status": "ok", "tool": "execute", "executors": [...]}` |
| anything else | `406 {"detail": "Not Acceptable. This server speaks MCP only."}` |

**Verify it is up** (MCP `tools/list`; a plain browser GET on `/mcp/` returns 406 by design):

```powershell
curl.exe -sS http://localhost:8300/mcp/ `
  -H "Content-Type: application/json" `
  -H "Accept: application/json, text/event-stream" `
  -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\",\"params\":{}}'
```

Ready-to-use payloads and a curl smoke script are in `testing/`
(`bash testing/curl_smoke.sh`).

## Configuration

All settings come from environment variables or `.env`; see `.env.example`. The names are
the Django project's, so an existing `.env` keeps working. `app/config.py` defines them
as a typed `Settings` class; code reads them with `get_settings()`.

| Area | Variables |
|---|---|
| MCP | `MCP_EXECUTE_TOOL_NAME`, `MCP_JSON_RESPONSE`, `MCP_STATELESS` |
| Execution | `MAX_CONCURRENCY`, `DEFAULT_CALL_TIMEOUT_SECONDS`, `HTTP_TIMEOUT_SECONDS`, `ALLOWED_HTTP_METHODS` |
| Cache service | `CACHE_API_URL`, `CACHE_HTTP_TIMEOUT_SECONDS`, `CACHE_DEFAULT_TTL_SECONDS` |
| Database | `MSSQL_*`, or `DB_CONNECTION_STRING` to bypass assembly |
| Function tools | `FUNCTION_ALLOWLIST`; dynamic registration: `FUNCTION_DYNAMIC_REGISTRATION_ENABLED`, `CONFIG_API_*`, `FUNCTION_ALLOWED_MODULE_PREFIXES`, `FUNCTION_PACKAGE_INDEX_URL`, `FUNCTION_NEGATIVE_CACHE_TTL_SECONDS` |
| Executors | `EXECUTOR_TYPES` (JSON: tool type -> class path) |
| Logging | `LOG_LEVEL` |

The database executor needs an ODBC driver for SQL Server on the host
(`ODBC Driver 18 for SQL Server`).

## Layout

```
app/
  main.py            FastAPI app: MCP transport at /mcp, /health, 406 elsewhere
  config.py          Settings (pydantic-settings)
  mcp_server/        MCP server: the execute tool and its input schema
  engine/            facade, planner, strategies (sequential/concurrent/pipeline),
                     executors (database/api/function), decorators (retry/timeout/trace)
  infra/             pooled httpx client, cache service client, connections,
                     placeholder parameters, {{token}} resolver
  models/            request/response dataclasses, cached-result envelope
tests/               unit tests (ported) and app tests (new)
testing/             MCP payloads and curl smoke script
docs/                design plan and dynamic function registration (from the Django project)
```

## Test

```powershell
.venv\Scripts\python.exe -m pytest           # 169 tests, no database, network or cache needed
.venv\Scripts\python.exe -m ruff check app tests
```
