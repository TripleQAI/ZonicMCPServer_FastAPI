# MCP test payloads

Ready-to-send payloads for the Zonic MCP server (`/mcp/`, single `execute` tool).

## How to send

The server speaks **MCP streamable-HTTP** (JSON-RPC 2.0). It is `stateless=True` +
`json_response=True`, so you can POST directly — no `initialize` handshake, plain
JSON responses.

**Endpoint**
```
POST http://localhost:8300/mcp/
```

**Headers (both required — omitting the second → HTTP 406)**
```
Content-Type: application/json
Accept: application/json, text/event-stream
```

**Body:** a JSON-RPC request. For a business call, `method: "tools/call"`,
`params.name: "execute"`, and `params.arguments` is the execute request
(`context` / `execution` / `tools[]`). See payloads below.

**Reading the response:** the JSON-RPC `result.content[0].text` is a JSON string
— the execute envelope `{ requestId, status, results, meta }`. Each entry in
`results` is `{ id, status, output, error, meta }`.

### Start the server first
```bash
.un.bat          # from the project root; port 8300
# or: .venv/Scripts/python -m uvicorn app.main:app --port 8300
# The cache service (CACHE_API_URL) is optional: an unreachable cache degrades to a miss.
```

## Prerequisites per tool type

- **function** — works out of the box **only if `FUNCTION_ALLOWLIST` is empty**
  (the default when no `.env` sets it). ⚠️ `.env.example` ships
  `FUNCTION_ALLOWLIST=polars,pandas,numpy`, which would **block** the
  `builtins.*` / `json.*` examples below. For these tests, leave
  `FUNCTION_ALLOWLIST` unset, or set `FUNCTION_ALLOWLIST=builtins,json,statistics,math`.
- **api** — needs outbound internet (examples use jsonplaceholder / httpbin).
- **database** — needs a configured `DB_CONNECTION_STRING` (or the `MSSQL_*` vars) and a
  reachable MSSQL. Without it the call returns `status: "error"` (a valid test of
  error isolation). SQL uses `?` positional placeholders bound from
  `config.parameters` + `input` (insertion order).

---

## 1. Connectivity — `tools/list`
Confirms the server responds and exposes the single `execute` tool.
```json
{ "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {} }
```

## 2. Function · concurrent (two tools)
`biggest` → 17, `power` → 1024. Both run at once.
```json
{
  "jsonrpc": "2.0", "id": 2, "method": "tools/call",
  "params": {
    "name": "execute",
    "arguments": {
      "requestId": "fn-concurrent",
      "execution": { "mode": "concurrent", "maxConcurrency": 4 },
      "tools": [
        { "id": "biggest", "type": "function",
          "config": { "functionPath": "builtins.max", "callStyle": "args" },
          "input": { "a": 3, "b": 17, "c": 9 } },
        { "id": "power", "type": "function",
          "config": { "functionPath": "builtins.pow", "callStyle": "kwargs" },
          "input": { "base": 2, "exp": 10 } }
      ]
    }
  }
}
```

## 3. Function · `single` callStyle (whole input dict as one arg)
`json.dumps({...})` → a JSON string.
```json
{
  "jsonrpc": "2.0", "id": 3, "method": "tools/call",
  "params": {
    "name": "execute",
    "arguments": {
      "execution": { "mode": "concurrent" },
      "tools": [
        { "id": "serialize", "type": "function",
          "config": { "functionPath": "json.dumps", "callStyle": "single" },
          "input": { "name": "zonic", "n": 42 } }
      ]
    }
  }
}
```

## 4. Sequential · cross-tool reference
`step1` → 40, `step2` = `pow(base=40, exp=2)` → 1600. Every tool output is the
envelope `{ is_cached, data: { variable, value } }`, so `{{results.step1.data.value}}`
resolves to step1's value before step2 runs (`{{results.step1}}` would be the envelope).
```json
{
  "jsonrpc": "2.0", "id": 4, "method": "tools/call",
  "params": {
    "name": "execute",
    "arguments": {
      "execution": { "mode": "sequential", "failFast": true },
      "tools": [
        { "id": "step1", "type": "function",
          "config": { "functionPath": "builtins.max", "callStyle": "args" },
          "input": { "a": 5, "b": 40, "c": 7 } },
        { "id": "step2", "type": "function",
          "config": { "functionPath": "builtins.pow", "callStyle": "kwargs" },
          "input": { "base": "{{results.step1.data.value}}", "exp": 2 } }
      ]
    }
  }
}
```

## 5. Pipeline · DAG (stages from dependsOn)
`a`→10; then `b`,`c` run concurrently (both read `{{results.a.data.value}}`); then `d`.
Expected: a=10, b=100, c=1000, d=max(100,1000)=1000.
```json
{
  "jsonrpc": "2.0", "id": 5, "method": "tools/call",
  "params": {
    "name": "execute",
    "arguments": {
      "execution": { "mode": "pipeline", "maxConcurrency": 8 },
      "tools": [
        { "id": "a", "type": "function",
          "config": { "functionPath": "builtins.max", "callStyle": "args" },
          "input": { "x": 10, "y": 3 } },
        { "id": "b", "type": "function", "dependsOn": ["a"],
          "config": { "functionPath": "builtins.pow", "callStyle": "kwargs" },
          "input": { "base": "{{results.a.data.value}}", "exp": 2 } },
        { "id": "c", "type": "function", "dependsOn": ["a"],
          "config": { "functionPath": "builtins.pow", "callStyle": "kwargs" },
          "input": { "base": "{{results.a.data.value}}", "exp": 3 } },
        { "id": "d", "type": "function", "dependsOn": ["b", "c"],
          "config": { "functionPath": "builtins.max", "callStyle": "args" },
          "input": { "b": "{{results.b.data.value}}", "c": "{{results.c.data.value}}" } }
      ]
    }
  }
}
```

## 6. Error isolation · mixed success/failure (→ batch status `partial`)
`good` succeeds; `bad` fails (unknown functionPath). Concurrent mode isolates the
failure — `good` still returns.
```json
{
  "jsonrpc": "2.0", "id": 6, "method": "tools/call",
  "params": {
    "name": "execute",
    "arguments": {
      "execution": { "mode": "concurrent" },
      "tools": [
        { "id": "good", "type": "function",
          "config": { "functionPath": "builtins.max", "callStyle": "args" },
          "input": { "a": 1, "b": 2 } },
        { "id": "bad", "type": "function",
          "config": { "functionPath": "nonexistent.module.func", "callStyle": "args" },
          "input": { "a": 1 } }
      ]
    }
  }
}
```

## 7. API · GET (needs internet)
```json
{
  "jsonrpc": "2.0", "id": 7, "method": "tools/call",
  "params": {
    "name": "execute",
    "arguments": {
      "execution": { "mode": "concurrent" },
      "tools": [
        { "id": "todo", "type": "api",
          "config": { "url": "https://jsonplaceholder.typicode.com/todos/1",
                      "method": "GET", "result": { "format": "json" } } }
      ]
    }
  }
}
```

## 8. API · POST with JSON body (needs internet)
```json
{
  "jsonrpc": "2.0", "id": 8, "method": "tools/call",
  "params": {
    "name": "execute",
    "arguments": {
      "execution": { "mode": "concurrent" },
      "tools": [
        { "id": "create", "type": "api",
          "config": { "url": "https://httpbin.org/post", "method": "POST",
                      "bodyType": "json",
                      "bodyTemplate": { "name": "zonic", "value": 1 },
                      "result": { "format": "json" } } }
      ]
    }
  }
}
```

## 9. Mixed types · pipeline (api → function on its result)
`fetch` calls an API, then `titleLen` measures the fetched title. Requires internet.
```json
{
  "jsonrpc": "2.0", "id": 9, "method": "tools/call",
  "params": {
    "name": "execute",
    "arguments": {
      "execution": { "mode": "pipeline" },
      "tools": [
        { "id": "fetch", "type": "api",
          "config": { "url": "https://jsonplaceholder.typicode.com/todos/1",
                      "method": "GET", "result": { "format": "json" } } },
        { "id": "titleLen", "type": "function", "dependsOn": ["fetch"],
          "config": { "functionPath": "builtins.len", "callStyle": "args" },
          "input": { "value": "{{results.fetch.data.value.title}}" } }
      ]
    }
  }
}
```

## 10. Database (template — needs DB_CONNECTION_STRING)
Set the `MSSQL_*` vars in `.env` (host, port, database, username, password, driver),
or override the assembled string directly with `DB_CONNECTION_STRING=...`.
SQL uses `?` placeholders bound from `config.parameters` + `input`.
```json
{
  "jsonrpc": "2.0", "id": 10, "method": "tools/call",
  "params": {
    "name": "execute",
    "arguments": {
      "execution": { "mode": "sequential" },
      "tools": [
        { "id": "bills", "type": "database",
          "config": { "queryType": "script",
                      "sql": "SELECT TOP 5 * FROM dbo.Bill WHERE ClientCode = ?",
                      "parameters": {} },
          "input": { "code": "FSH" },
          "cache": { "enabled": true, "ttlSeconds": 300 } }
      ]
    }
  }
}
```

Stored-procedure variant:
```json
{ "id": "proc", "type": "database",
  "config": { "queryType": "stored_procedure",
              "procedureName": "dbo.GetBills",
              "parameters": [ { "name": "code", "direction": "IN" } ] },
  "input": { "code": "FSH" } }
```

---

## curl one-liner (scenario 2)
```bash
curl -sS http://localhost:8300/mcp/ \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d @testing/payload_function_concurrent.json
```
See `testing/curl_smoke.sh` to run the core scenarios in sequence.
