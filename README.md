# Zonic MCP Server (FastAPI)

An MCP server with one tool, `execute`, that runs a set of **database** (MSSQL script or
stored procedure), **API** (any HTTP verb) and **function** (installed Python callable)
tools in a single round-trip, sequentially, concurrently or as a dependency pipeline.

This is the FastAPI port of `ZonicMCPServer_DjangoApp`. The engine, executors, cache,
models and MCP contract are unchanged; Django is replaced by FastAPI for hosting and by
`pydantic-settings` for configuration. The conversion report
(mcp-server-fastapi-conversion-report.md) is kept in the platform's working docs, outside
this repository.

## Nebius x NVIDIA Global AI Hackathon

This repository is part of the Zonic agent platform, our entry in the Nebius x NVIDIA Global AI Hackathon (Best Apps and Agents track). The platform's agents run on NVIDIA Nemotron models served by Nebius Token Factory. How that is wired, and which model does what, is in the runtime's README: [NVIDIA Nemotron on Nebius Token Factory](https://github.com/TripleQAI/ZonicAgentRuntimeBackend_Python_LangGraph#nvidia-nemotron-on-nebius-token-factory).

**This repository's part:** Nemotron decides which tool to call and with which arguments; this server runs the call (`execute`). The demo agents' tools reach NYC Open Data, NYISO's public files, Open-Meteo and math.js through it, and a request can run several tools concurrently.

**Live demo:** the chat at https://dev.tripleq.ai/agent-runtime-lg and the agent builder at https://dev.tripleq.ai/agent-visual-builder-lg/workflows. Both sign in with Amazon Cognito; the test accounts are in the hackathon submission's testing instructions.

| Repository | Role |
|---|---|
| [ZonicAgentBuilderUI_NextJS](https://github.com/TripleQAI/ZonicAgentBuilderUI_NextJS) | The visual builder: design a workflow and pick a Nemotron model for each node |
| [ZonicAgentBuilderServer_FastAPI](https://github.com/TripleQAI/ZonicAgentBuilderServer_FastAPI) | The builder's API: validation, import and export, the node registry and model options |
| [ZonicAgentRuntimeBackend_Python_LangGraph](https://github.com/TripleQAI/ZonicAgentRuntimeBackend_Python_LangGraph) | Runs the workflows on LangGraph and calls Nemotron through Token Factory |
| [ZonicAgentRuntimeUI_NextJS_For_LangGraph](https://github.com/TripleQAI/ZonicAgentRuntimeUI_NextJS_For_LangGraph) | The chat app people use the agents in |
| **ZonicMCPServer_FastAPI** (this repository) | The agents' tools over open data |
| [ZonicPostgresqlServer_FastAPI](https://github.com/TripleQAI/ZonicPostgresqlServer_FastAPI) | The PostgreSQL gateway every service stores data through (Neon) |

## Technical design

The full technical design is in [docs/technical-design.md](docs/technical-design.md):

- [Architecture](docs/technical-design.md#architecture)
- [The execute contract](docs/technical-design.md#the-execute-contract)
- [Executors](docs/technical-design.md#executors)
- [Execution engine](docs/technical-design.md#execution-engine)
- [Security](docs/technical-design.md#security)
- [Configuration and deployment](docs/technical-design.md#configuration-and-deployment)
- [Testing](docs/technical-design.md#testing)
- [Key design decisions](docs/technical-design.md#key-design-decisions)

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
| MCP | `MCP_EXECUTE_TOOL_NAME`, `MCP_JSON_RESPONSE`, `MCP_STATELESS`, `MCP_AUTH_TOKEN` (bearer token required on `/mcp` when set) |
| Execution | `MAX_CONCURRENCY`, `DEFAULT_CALL_TIMEOUT_SECONDS`, `HTTP_TIMEOUT_SECONDS`, `ALLOWED_HTTP_METHODS` |
| Cache service | `CACHE_API_URL`, `CACHE_HTTP_TIMEOUT_SECONDS`, `CACHE_DEFAULT_TTL_SECONDS` |
| Database | `MSSQL_*`, or `DB_CONNECTION_STRING` to bypass assembly (the default connection); `DATABASE_CONNECTIONS` (JSON name → DSN) for named SQL Server or PostgreSQL connections; `POSTGRES_READ_ONLY`, `POSTGRES_STATEMENT_TIMEOUT_SECONDS` |
| Function tools | `FUNCTION_ALLOWLIST`; dynamic registration: `FUNCTION_DYNAMIC_REGISTRATION_ENABLED`, `CONFIG_API_*`, `FUNCTION_ALLOWED_MODULE_PREFIXES`, `FUNCTION_PACKAGE_INDEX_URL`, `FUNCTION_NEGATIVE_CACHE_TTL_SECONDS` |
| Executors | `EXECUTOR_TYPES` (JSON: tool type -> class path) |
| Logging | `LOG_LEVEL` |

The database executor needs an ODBC driver for SQL Server on the host
(`ODBC Driver 18 for SQL Server`) for SQL Server connections; PostgreSQL connections use
psycopg.

**Database tools** name their connection (`config.connection` or `config.connectionName`),
never a connection string; `config.databaseType`, when set, must match it. Scripts fill their
`<placeholders>` with values encoded for the connection's dialect. `config.maxRows` caps the
rows returned and `config.queryTimeout` (seconds) bounds the query. Stored procedures run on
SQL Server connections only.

**API tools** fill their `<placeholders>` in `url` (percent-encoded), `queryParams`, `headers`
(line breaks refused) and the body (JSON-escaped for `bodyType: json`). Number, date and boolean
parameters are validated as for SQL.

**Cached results.** With `config.result.saveToCache`, a tool that also sets
`config.result.preview: {"rows": N}` gets `data.preview` with the key: the first `N` rows, the
row count and columns, and the size and SHA-256 of the stored JSON, so the caller need not read
the value back.

## Deploy to AWS (Lambda)

The `Dockerfile` builds a Lambda container image (arm64): the Lambda Web Adapter runs
uvicorn unchanged, and the ODBC driver is included. `deploy/aws-lambda/deploy.sh` pushes it
to ECR and creates (or updates) the function and a public Function URL with response
streaming, as pgfoundation's deployment does; `/mcp` is guarded by `MCP_AUTH_TOKEN`.

```bash
bash deploy/aws-lambda/deploy.sh      # AWS_PROFILE defaults to tripleq-development
```

Settings are the function's environment variables: built-in defaults, then the SSM
parameter `/zonic-mcp-server/runtime` (SecureString), then the git-ignored `.env.lambda`.
Each run writes the merged result back to the parameter; the first run generates
`MCP_AUTH_TOKEN`. The defaults turn the cache off (`CACHE_API_URL` empty) and allow only
`statistics`. The script refuses an empty `FUNCTION_ALLOWLIST`, one naming a module that
can run arbitrary code (`builtins`, `os`, `subprocess`, ...), and dynamic registration,
which cannot install packages into a read-only image.

The function runs outside a VPC, so its databases and APIs must be reachable over the
internet. The `sample_open_data` connection is the Neon dev branch as
`sample_open_data_reader`: add it to `.env.lambda` as
`DATABASE_CONNECTIONS={"sample_open_data": "postgresql://..."}` and run the script again.

### Current deployment

Deployed and verified on 27 October 2026: `/health` answers, `/mcp/` refuses a call without
the token, and with it a `function` tool (`statistics.mean`) and an `api` tool (httpbin) succeed.

| Item | Value |
|---|---|
| Platform | AWS Lambda, account `001634074733` (`tripleq-development`), region `us-east-1` |
| Function | `zonic-mcp-server` (container image, arm64, 512 MB, 120-second timeout); cold start about 5 s |
| Endpoint | `https://rwlgztfgkt4kyuazqsacjv5xka0fduqz.lambda-url.us-east-1.on.aws/mcp/` (`AuthType NONE`, `RESPONSE_STREAM`) |
| Authentication | `Authorization: Bearer <MCP_AUTH_TOKEN>`; the token is in SSM `/zonic-mcp-server/runtime` and the git-ignored `.env.lambda` |
| Container image | ECR repository `zonic-mcp-server` (five newest images kept) |
| Settings | Cache off, `FUNCTION_ALLOWLIST=statistics`, no database connections yet (`sample_open_data` pending its DSN) |
| Redeploy | `bash deploy/aws-lambda/deploy.sh` |

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
.venv\Scripts\python.exe -m pytest           # 195 tests (1 skipped), no database, network or cache needed
.venv\Scripts\python.exe -m ruff check app tests
```

With `LIVE_SAMPLE_OPEN_DATA_DSN` set to the `sample_open_data_reader` connection,
`tests/engine/test_postgres.py::test_live_sample_open_data` also queries the open test data.

## License

Licensed under the Apache License 2.0; see [LICENSE](LICENSE).
