#!/usr/bin/env bash
# Smoke-test the MCP server's core scenarios via curl.
# Usage:  bash testing/curl_smoke.sh [BASE_URL]
#   BASE_URL defaults to http://localhost:8300
# Run from the project root with the server already started (run.bat).

set -u
BASE="${1:-http://localhost:8300}"
URL="$BASE/mcp/"
H_CT="Content-Type: application/json"
H_ACC="Accept: application/json, text/event-stream"

call() {
  local label="$1"; local body="$2"
  echo "=================================================================="
  echo "  $label"
  echo "------------------------------------------------------------------"
  curl -sS "$URL" -H "$H_CT" -H "$H_ACC" -d "$body"
  echo ""
}

# 1. connectivity
call "tools/list" '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

# 2. function · concurrent
call "function · concurrent (max=17, pow=1024)" "$(cat testing/payload_function_concurrent.json)"

# 4. sequential · cross-tool reference
call "sequential · cross-ref (step1=40, step2=1600)" '{
  "jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"execute","arguments":{
    "execution":{"mode":"sequential","failFast":true},
    "tools":[
      {"id":"step1","type":"function","config":{"functionPath":"builtins.max","callStyle":"args"},"input":{"a":5,"b":40,"c":7}},
      {"id":"step2","type":"function","config":{"functionPath":"builtins.pow","callStyle":"kwargs"},"input":{"base":"{{results.step1.data.value}}","exp":2}}
    ]}}}'

# 5. pipeline · DAG
call "pipeline · DAG (d=1000)" "$(cat testing/payload_pipeline_dag.json)"

# 6. error isolation → partial
call "error isolation (good ok, bad error → partial)" '{
  "jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"execute","arguments":{
    "execution":{"mode":"concurrent"},
    "tools":[
      {"id":"good","type":"function","config":{"functionPath":"builtins.max","callStyle":"args"},"input":{"a":1,"b":2}},
      {"id":"bad","type":"function","config":{"functionPath":"nonexistent.module.func","callStyle":"args"},"input":{"a":1}}
    ]}}}'

# 7. api · GET (needs internet)
call "api · GET jsonplaceholder (needs internet)" '{
  "jsonrpc":"2.0","id":7,"method":"tools/call","params":{"name":"execute","arguments":{
    "execution":{"mode":"concurrent"},
    "tools":[{"id":"todo","type":"api","config":{"url":"https://jsonplaceholder.typicode.com/todos/1","method":"GET","result":{"format":"json"}}}]
  }}}'

echo "=================================================================="
echo "done."
