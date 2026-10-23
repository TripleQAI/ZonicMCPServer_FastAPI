# Zonic MCP Server — Redesign & Architecture Plan (Rev 2)

> **Carried over from the Django project.** The design is unchanged in the FastAPI port; Django paths map as follows: `core/settings.py` -> `app/config.py` (typed `Settings`), `core/asgi.py` and `mcp_server/asgi.py` -> `app/main.py`, and `engine/`, `infra/`, `models/`, `mcp_server/` -> the same folders under `app/`. Settings are read with `get_settings()` instead of `django.conf.settings`.

**Project:** `C:\InsitePlatform\Try\ZonicMCPServer_DjangoApp`
**Type:** Standalone async **MCP-protocol-only** server (no frontend, no REST) — a
transport-thin front over a call-execution engine, consumed exclusively over the
Model Context Protocol.
**Date:** 2026-07-16
**Status:** Design only — no code (implementation reverted for this redesign).

---

## Rev 2 — what changed from Rev 1

This revision narrows the surface to **MCP only** and streamlines the wire
contract. The transport-agnostic engine (executors, strategies, decorators,
cache) is unchanged; the transport and schema are redesigned.

| # | New / changed requirement | Design answer |
|---|---|---|
| N1 | Use the **Anthropic `mcp` package** | Server built on the official `mcp` Python SDK (`mcp.server.lowlevel.Server` + `StreamableHTTPSessionManager`). Anthropic-authored; **no Anthropic API / model dependency**. |
| N2 | **MCP protocol only — remove normal HTTP** | Delete the REST `/api/v1/*` API **and** the `/mcp/register/` HTTP side channel. The single wire surface is the MCP streamable-HTTP endpoint. Non-MCP HTTP is rejected (406). |
| N3 | **Optimize connection build + tool dispatch** | One reusable MCP session (no per-call register/unregister round-trips), stateless transport, O(1) executor dispatch, no per-session tool-spec building. See §12. |
| N4 | **No `tools/list`; receive tool configurations in-band** | The server does not advertise/enumerate business tools. A single `execute` entry receives the full **tool configuration set** (the tools + the execution mode: sequential / concurrent / pipeline) as its call arguments. |
| N5 | **One clean common schema for all tool types** | A single uniform request/result schema (`§3`): every tool shares the same envelope; type-specific settings live under one `config` key; every result shares the same `{id, status, output, error, meta}` shape. |

### Retained engine requirements (unchanged from Rev 1)
Three call types (**database / api / function**), each runnable **sequential /
concurrent / pipeline**; DB **script + stored procedure**; API
**GET/POST/PUT/PATCH/DELETE**; functions from **installed packages**; **Redis
cache**; **async non-blocking** throughout; **no hardcoded logic**.

---

## 1. Design goals & principles

- **MCP is the only door.** The server speaks the Model Context Protocol and
  nothing else. There is no REST API and no HTTP registration step. A plain HTTP
  request to the MCP endpoint is rejected by the SDK (HTTP 406).
- **Config is in-band.** The client sends the complete tool-configuration set
  (which tools, of which types, in which execution mode) inside a single MCP tool
  call. The server holds no pre-registered session state — it executes what it is
  handed and returns results.
- **No discovery surface.** The server does not enumerate business tools via
  `tools/list`; it exposes one well-known execution entry. (Removing discovery is
  also what removes a whole class of round-trips and state — see §12.)
- **Mechanism, not policy.** Executors implement *how* to run a call; the payload
  supplies *what*. No table names, URLs, or function names live in code.
- **One schema to rule them all.** Every tool type flows through the same request
  envelope and the same result shape (§3) — clients write one integration.
- **Async, isolated, no-hardcode.** ASGI end-to-end; blocking drivers in
  `asyncio.to_thread`; per-call failure isolation; executor types resolved from a
  registry, connections/secrets from env.

---

## 2. Layered architecture (MCP-only)

```
┌──────────────────────────────────────────────────────────────────────┐
│ MCP TRANSPORT  (the only surface)                                    │
│   Anthropic `mcp` SDK · StreamableHTTPSessionManager                 │
│   ALL /mcp/  ← MCP streamable-HTTP (JSON-RPC 2.0). Non-MCP HTTP → 406 │
│   one tool: `execute`  — receives the full tool-configuration set     │
│        (no tools/list of business tools, no HTTP register step)       │
└───────────────────────────┬──────────────────────────────────────────┘
                            │  ExecuteRequest (domain object)
┌───────────────────────────▼──────────────────────────────────────────┐
│ ORCHESTRATION  (Facade + Strategy)                                   │
│   ExecutionEngine.run(request)  ← single entrypoint (Facade)         │
│   ExecutionPlanner: payload → ExecutionPlan (stages / DAG)           │
│   ExecutionStrategy:  Concurrent | Sequential | Pipeline            │
│   ExecutionContext:   flow/system state + accumulating results       │
└───────────────────────────┬──────────────────────────────────────────┘
                            │  CallCommand[]  (Command)
┌───────────────────────────▼──────────────────────────────────────────┐
│ EXECUTION  (Strategy + Factory + Template Method + Decorator)        │
│   ExecutorRegistry:  type → CallExecutor   (O(1) dispatch)           │
│   BaseExecutor.execute():  resolve → run → normalize   (Template)    │
│     • DatabaseExecutor → QueryMode(Script | StoredProcedure)        │
│     • ApiExecutor      → method dispatch (GET/POST/PUT/PATCH/DELETE) │
│     • FunctionExecutor → dotted-path resolver                       │
│   Decorators: Cache · Retry · Timeout · Trace                        │
└───────────────────────────┬──────────────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────────────┐
│ INFRASTRUCTURE  (Repository + DI)                                    │
│   CacheService (redis.asyncio)      ConnectionRegistry (DB conns)     │
│   AsyncHttpClient (pooled httpx)    VariableResolver ({{tokens}})     │
│   Settings/env (no hardcode)                                         │
└──────────────────────────────────────────────────────────────────────┘
```

The dashed boundary at the top is the only thing that changed shape from Rev 1:
one MCP surface replaces the REST API + MCP-with-registration.

---

## 3. Clean common schema (requirement N5)

One envelope for the request, one shape for every result — identical across
database, api, and function tools. Validated with **Pydantic v2** at the envelope
level; the per-type `config` is a documented open object.

### 3.1 Request — the `execute` tool arguments

The client calls the single MCP tool `execute` with these arguments (this *is*
the tool-configuration set of requirement N4):

```jsonc
{
  "requestId": "optional-uuid",          // echoed back
  "context": {                           // shared state for {{token}} resolution
    "flow":   { "clientCode": "FSH" },
    "system": { "userId": 42 }
  },
  "execution": {
    "mode": "pipeline",                  // sequential | concurrent | pipeline
    "maxConcurrency": 8,                 // fan-out bound
    "failFast": false                    // stop on first error (seq/pipeline)
  },
  "tools": [                             // the tool configurations
    {
      "id": "getBills",                  // unique within the batch
      "type": "database",                // database | api | function
      "config": { /* type-specific, see §3.4 */ },
      "input":  { /* dynamic caller args */ },
      "stage": 0,                        // optional pipeline ordering
      "dependsOn": [],                   // optional DAG edges
      "cache": { "enabled": true, "ttlSeconds": 1800 }   // optional
    }
  ]
}
```

Every tool — regardless of type — has the **same seven fields**
(`id, type, config, input, stage, dependsOn, cache`). Only `config`'s inner keys
differ by type. That uniformity is requirement N5.

### 3.2 Result — one shape for every tool type

```jsonc
{
  "id": "getBills",
  "status": "success",                   // success | error
  "output": [ /* rows | HTTP body | return value — the primary payload */ ],
  "error": null,                         // or { "type": "...", "message": "..." }
  "meta": {                              // uniform envelope + type-specific extras
    "type": "database",
    "durationMs": 42,
    "cached": false
    // extras: rowCount / httpStatus / functionPath / outputParams / cacheKey …
  }
}
```

`output` is the single, uniform result payload for all types. Anything
type-specific is **metadata**, never a new top-level shape.

### 3.3 Response envelope (returned as MCP tool result content)

```jsonc
{
  "requestId": "…",
  "status": "success",                   // success | partial | error
  "results": {
    "getBills": { /* §3.2 result */ }
  },
  "meta": { "totalDurationMs": 55, "toolCount": 1, "completed": 1,
            "flowUpdates": { "billCacheKey": "…" } }
}
```

The engine serialises this envelope to JSON and returns it as MCP
`TextContent`. Errors are carried *inside* results (`status: "error"`), not as
MCP protocol faults, so a partial batch still returns cleanly.

### 3.4 Per-type `config` contracts (the only type-specific part)

**database**
```jsonc
{ "connection": "default",
  "queryType": "script" | "stored_procedure",
  "sql": "SELECT ... WHERE code = {code}",          // script
  "procedureName": "dbo.GetBills",                  // stored_procedure
  "parameters": [ { "name": "code", "source": "input", "direction": "in" } ],
  "result": { "maxRows": 1000, "timeoutSeconds": 30 } }
```
**api**
```jsonc
{ "url": "https://api/things/{{id}}", "method": "GET|POST|PUT|PATCH|DELETE",
  "headers": {}, "queryParams": {},
  "auth": { "type": "none|api_key|bearer|basic", "...": "..." },
  "bodyType": "none|json|form", "bodyTemplate": {}, "result": { "format": "json|raw" } }
```
**function**
```jsonc
{ "functionPath": "polars.read_csv" | "builtins.sorted" | "pkg.Cls.method",
  "callStyle": "kwargs|single|args" }
```

Adding a new type means a new executor + a documented `config` block — the
envelope (§3.1/§3.2) never changes.

---

## 4. Execution modes (sequential / concurrent / pipeline)

Execution mode is a **Strategy**, decoupled from tool type, so any mix of types
runs under any mode. Set via `execution.mode`.

| Mode | Behavior | Async mechanism |
|---|---|---|
| `concurrent` | All tools fan out at once | `asyncio.gather` bounded by `Semaphore(maxConcurrency)` |
| `sequential` | Tools run in listed order; each sees prior results | `await` in a loop, `record` between |
| `pipeline` | Grouped by `stage` / topologically sorted by `dependsOn`; stages sequential, within-stage concurrent | per-stage `gather`, stages in order |

`concurrent` = one stage of N; `sequential` = N stages of one. **Pipeline** is the
general engine; the other two are presets. Cross-tool references use
`{{results.<id>.<path>}}`, resolvable once a producing tool has completed
(sequential/pipeline only). `failFast` short-circuits ordered runs.

---

## 5. Execution layer — executors (three call types, no hardcode)

### 5.1 Contracts
```
CallExecutor (interface / Strategy)
    async def execute(config, input, context) -> Result

BaseExecutor (Template Method):
    1. resolve {{tokens}} in config/input via VariableResolver
    2. _run(resolved_config, resolved_input, context)   # subclass hook
    3. normalize into the common result shape (§3.2)
```

### 5.2 ExecutorRegistry (Factory / Registry)
- Maps `type` → singleton executor, populated at startup from
  `settings.EXECUTOR_TYPES` (type → dotted class path).
- Dispatch is `registry.get(type)` — **O(1), no `if type ==`**. Unknown type → a
  clean error result.

### 5.3 DatabaseExecutor (script + stored procedure)
- `QueryMode` chosen by `config.queryType`:
  - `ScriptMode` — parameterized SQL (bound `?`/`{name}` params).
  - `StoredProcedureMode` — `{CALL proc(?, …)}` with typed IN/OUT/INOUT
    params; captures **multiple result sets + OUTPUT params** (in `meta`).
- Params are always **bound**, never concatenated (single SQL-injection surface).
- **Non-blocking:** pyodbc runs in `asyncio.to_thread`; connections from
  `ConnectionRegistry`, short-lived.

### 5.4 ApiExecutor (all verbs, config-driven)
- `url`, `method`, `headers`, `queryParams`, `auth`, `bodyType`, `bodyTemplate`
  all from `config`. `method` dispatch covers GET/POST/PUT/PATCH/DELETE
  (validated against an allow-set, not branched per verb).
- `{{token}}` interpolation from `input` + `context`.
- **Non-blocking:** shared pooled `httpx.AsyncClient`, per-call timeout.

### 5.5 FunctionExecutor (installed packages)
- Resolves a dotted `functionPath` to any importable callable
  (longest-prefix import, `lru_cache`); `callStyle` selects the call convention.
- Sync callables run in `asyncio.to_thread`; async awaited.
- `FUNCTION_ALLOWLIST` gates permitted module prefixes.

### 5.6 Cross-cutting decorators (Decorator pattern)
`Trace( Retry( Timeout( Cache( executor ) ) ) )` — Cache (§7), Timeout
(`asyncio.wait_for`), Retry (idempotent only), Trace (Observer seam for
Langfuse/OTel). Written once, applied to every executor uniformly.

---

## 6. Async / non-blocking model

- **Framework:** Django **ASGI** (app registry + settings + management), served by
  **Uvicorn**; the MCP streamable app is mounted by the ASGI router.
- **Blocking I/O quarantine:** pyodbc + sync callables → `asyncio.to_thread`.
- **Async clients:** pooled `httpx.AsyncClient`, `redis.asyncio`.
- **Bounded concurrency:** `Semaphore(MAX_CONCURRENCY)` guards fan-out.
- **Per-request state only** in `ExecutionContext`; pooled clients + read-mostly
  registries are the only shared globals.

---

## 7. Redis caching

- **`CacheService`** (Repository over `redis.asyncio`): `get`/`set(ttl)`/
  `get_or_set`; degrades to a miss/no-op when Redis is unreachable or disabled.
- **Key** = stable hash of `(type, resolved config, resolved input)`.
- **Policy per tool:** `cache.enabled`, `cache.ttlSeconds` (defaults from
  settings). **Writes never cached** (POST/PUT/DELETE, DML, side-effecting sprocs).
- **Read-through for large results:** oversized DB rows stored in Redis; the
  result returns `{ status, rowCount, cacheKey }` so responses stay small.
- Cache is a **decorator** → applies to all tool types without per-executor code.

---

## 8. MCP transport (the only surface — requirements N1, N2, N4)

### 8.1 Built on the Anthropic `mcp` SDK (N1)
- `mcp.server.lowlevel.Server` for handlers; `StreamableHTTPSessionManager`
  for the streamable-HTTP transport. Anthropic-authored SDK; **no Anthropic API,
  no Claude model, no `anthropic` dependency** — purely the protocol.

### 8.2 One endpoint, one tool, config in-band (N2, N4)
- **Endpoint:** `ALL /mcp/` — MCP streamable-HTTP (JSON-RPC 2.0). The SDK enforces
  MCP content negotiation; a non-MCP HTTP request → **HTTP 406**. There is **no
  REST API and no `/register/` HTTP endpoint**.
- **Single tool `execute`** (name from `MCP_EXECUTE_TOOL_NAME`, default
  `"execute"`). Its `arguments` are the §3.1 request — the full tool-configuration
  set including the execution mode. The `call_tool` handler builds an
  `ExecuteRequest` and calls `ExecutionEngine.run`, returning the §3.3 envelope as
  `TextContent`.
- **No business-tool discovery (N4):** the server does not enumerate the
  database/api/function tools via `tools/list`. `tools/list` returns just the
  single `execute` entry (so a generic MCP client can find the door), or is
  omitted when clients call it by well-known name. The *tools themselves* arrive
  in-band in each call.
- **No session registration / no `X-Config-Token`:** because config is in-band,
  the in-memory registration store and its HTTP endpoints are removed entirely.
  The transport is **stateless** — any instance serves any request.

### 8.3 Call workflow
```
client                          server (/mcp/)
  │ initialize ───────────────▶ │  (MCP handshake, once per session)
  │ tools/call "execute" ─────▶ │  arguments = { context, execution, tools[] }
  │      {batch config}         │    → build ExecuteRequest
  │                             │    → ExecutionEngine.run (plan → strategy → executors)
  │ ◀──────────── result ───────│    → §3.3 envelope as TextContent JSON
  │ tools/call "execute" ─────▶ │  (reuse the same session for more batches)
```
One `execute` call carries **many tools of mixed types** and runs them
sequential / concurrent / pipeline — in a single round trip.

---

## 9. Project structure

```
ZonicMCPServer_DjangoApp/
  manage.py  requirements.txt  .env.example
  core/                         # Django ASGI project config
    settings.py  urls.py  asgi.py  wsgi.py     # asgi routes /mcp/ → MCP app
  mcp_server/                   # the MCP transport (only surface)
    server.py                   # mcp.Server: single `execute` tool → engine
    asgi.py                     # StreamableHTTPSessionManager (stateless)
    schemas.py                  # Pydantic request/result/response (§3)
  engine/                       # transport-agnostic core (unchanged)
    models.py  engine.py  planner.py  container.py
    strategies/  base.py concurrent.py sequential.py pipeline.py
    executors/
      base.py  registry.py
      database/  executor.py  query_mode.py  runner.py
      api/       executor.py
      function/  executor.py
    decorators/  cache.py  retry.py  timeout.py  trace.py
  infra/
    cache.py  connections.py  http_client.py  resolver.py
  tests/
    engine/  executors/  strategies/  mcp_server/  infra/
```
Removed vs Rev 1: the entire `api/` REST app and the `mcp_adapter/registry.py`
HTTP-registration store. `mcp_server/` replaces `mcp_adapter/`.

---

## 10. Settings & configuration (no hardcode)

Env-driven (`.env` + `settings.py`):
- `DB_CONNECTIONS`, `REDIS_URL`, `CACHE_DEFAULT_TTL_SECONDS`.
- `MAX_CONCURRENCY`, `DEFAULT_CALL_TIMEOUT_SECONDS`, `HTTP_TIMEOUT_SECONDS`.
- `ALLOWED_HTTP_METHODS`, `FUNCTION_ALLOWLIST`.
- `EXECUTOR_TYPES` (type → class path — add a type by config).
- `MCP_EXECUTE_TOOL_NAME` (default `execute`), `MCP_JSON_RESPONSE`,
  `MCP_STATELESS` (default `True`).
- `ALLOWED_HOSTS`, `DEBUG`, `SECRET_KEY`.

---

## 11. Error handling & observability

- **Per-tool isolation:** every tool yields a result; a failure is captured
  (`status:error`, structured message) without aborting siblings (unless
  `failFast` / a dependency failed).
- **Batch status:** `success` (all ok), `partial` (some failed / short-circuited),
  `error` (invalid payload / total failure).
- **Safe errors:** DB/HTTP driver errors mapped to non-leaking messages (never
  echo connection strings/secrets).
- **Tracing:** `requestId` propagated; per-tool `durationMs`, `cached`, `type` in
  `meta`; optional Langfuse/OTel via the Trace decorator (Observer).

---

## 12. Optimization — connection build & tool dispatch (requirement N3)

**Connection build (fewer round-trips, no state):**
- **No register/unregister.** Config is in-band, so the two HTTP round-trips and
  the TTL session store from Rev 1 are gone. A session is just: `initialize` once,
  then reuse for many `execute` calls.
- **Stateless streamable-HTTP.** No `Mcp-Session-Id` affinity → any server
  instance serves any request; horizontal scaling needs no sticky load balancer.
- **Reused session, reused engine.** Clients keep one MCP session open across
  batches; the server holds a process-wide `ExecutionEngine` singleton and pooled
  `httpx` / `redis` clients — no per-call setup.

**Tool dispatch / "tools list find" (no scanning):**
- **No tool-spec building.** Because there is no business-tool `tools/list`, the
  server never assembles per-session tool schemas.
- **O(1) executor lookup.** Each tool's `type` resolves via a dict
  (`registry.get`), not a linear scan of a registered-tools list.
- **Batch indexed once.** Tools are keyed by `id` into a dict at plan time; the
  planner's dependency layering is a single Kahn pass.
- **One round trip per batch.** Many mixed-type tools execute (concurrently /
  pipeline) inside one `execute` call, instead of one MCP round trip per tool.

**Net effect vs Rev 1 for an N-tool workload:** round-trips drop from
`register + initialize + N×call + unregister` to `initialize + 1×call`, and the
server does zero session-state bookkeeping.

---

## 13. Design patterns catalogue

| Pattern | Where | Why |
|---|---|---|
| **Strategy** | executors, execution modes, DB query types, HTTP method | swap behavior without branching |
| **Factory / Registry** | `ExecutorRegistry`, `ConnectionRegistry` | resolve by key; O(1); open/closed |
| **Command** | `CallCommand` | uniform `run()` for batch/concurrency |
| **Template Method** | `BaseExecutor.execute()` | shared resolve→run→normalize |
| **Decorator** | cache / retry / timeout / trace | composable cross-cutting concerns |
| **Facade** | `ExecutionEngine` | one entrypoint over the subsystem |
| **Adapter** | MCP transport; pyodbc driver | isolate protocol/driver |
| **Repository** | `CacheService`, `ConnectionRegistry` | abstract storage/config |
| **Builder** | query builder, response builder | step-wise assembly |
| **Observer** | Trace / Langfuse hooks | side-channel observability |
| **Dependency Injection** | infra into executors | testability, no hidden globals |

---

## 14. Phased implementation roadmap

Each phase ends with `python manage.py check` + green tests.

1. **Scaffold** — Django ASGI project, settings/env, requirements (incl. `mcp`).
2. **Domain + infra** — `models.py`, `ExecutionContext`, `CacheService`,
   `ConnectionRegistry`, `AsyncHttpClient`, `VariableResolver`.
3. **Executors + registry** — `BaseExecutor`, `ExecutorRegistry`; Function → API →
   Database (script, then stored procedure).
4. **Strategies + engine** — Pipeline/Concurrent/Sequential + `ExecutionEngine`
   facade + planner (stages/DAG).
5. **Decorators** — Cache → Timeout → Retry → Trace.
6. **MCP transport** — `mcp_server/`: single `execute` tool, common schema (§3),
   streamable-HTTP ASGI, stateless, no register, no business `tools/list`.
7. **Tests & docs** — unit (executors/strategies/planner/schema), MCP round-trip
   (initialize → `execute` → result), integration (fake Redis + DB); consumer doc.

---

## 15. Acceptance criteria

- [ ] The **only** wire surface is the MCP streamable endpoint; a non-MCP HTTP
      request returns **406**. No REST endpoint and no HTTP register step exist.
- [ ] Built on the Anthropic **`mcp`** SDK; no `anthropic`/Claude dependency.
- [ ] A single `execute` call accepts the tool-configuration set (tools + mode)
      **in-band**; the server advertises **no** business `tools/list`.
- [ ] One `execute` call runs **multiple mixed-type tools** under
      **sequential / concurrent / pipeline**; `dependsOn` cross-references work.
- [ ] Database supports **script** and **stored procedure** (OUTPUT params);
      API supports GET/POST/PUT/PATCH/DELETE; functions resolve installed-package
      callables — all via the common schema.
- [ ] Every tool result has the **same shape** `{id, status, output, error, meta}`.
- [ ] Redis caching via decorator; writes bypass; large results use cache keys.
- [ ] All I/O async/non-blocking (pyodbc via `to_thread`; async httpx/redis).
- [ ] Adding a call type = new executor class + one `EXECUTOR_TYPES` entry.
- [ ] Connection build is register-free and dispatch is O(1) (§12).
- [ ] No secrets/connections/URLs/function names hardcoded; all via env/payload.

---

## 16. Open decisions for review

1. **`execute` tool name** — default `"execute"`; confirm or rename
   (`run` / `invoke`). Configurable via `MCP_EXECUTE_TOOL_NAME`.
2. **`tools/list` behavior** — return the single `execute` entry (discoverable) vs
   return empty (pure by-name). Recommended: return the single entry for generic
   MCP-client compatibility.
3. **Response transport** — `MCP_JSON_RESPONSE=True` (JSON replies, simple) vs SSE
   streaming. Recommended: JSON + stateless for horizontal scaling.
4. **Large-result read-through** — keep the Redis cache-key indirection for big DB
   result sets? Recommended: yes, threshold-based.
5. **Framework** — Django ASGI (fleet consistency) confirmed; FastAPI is the
   leaner alternative if preferred.
