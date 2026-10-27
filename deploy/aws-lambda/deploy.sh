#!/usr/bin/env bash
# Deploy the Zonic MCP server to AWS Lambda behind a public Function URL.
#
# Cheapest AWS shape for this service, as for pgfoundation: Lambda (arm64 container
# image) runs the unchanged uvicorn app through the AWS Lambda Web Adapter, and a
# Function URL exposes it over HTTPS with response streaming. No load balancer, no NAT
# gateway, no always-on compute: at low traffic, usage stays inside the always-free
# tier and the standing cost is ECR storage for the image (cents per month). The
# function runs outside a VPC, so its databases and APIs must be reachable over the
# internet (the Neon branches are).
#
# Idempotent: the first run creates everything; later runs build, push and roll the
# function forward to the new image and settings.
#
# Usage (from the project root, in Git Bash or Linux):
#   bash deploy/aws-lambda/deploy.sh
#
# Settings: the function's environment variables (names as in .env.example), merged as
#   built-in defaults  <  SSM parameter /zonic-mcp-server/runtime  <  .env.lambda
# The SSM parameter (SecureString, standard tier: free) is the shared copy, updated with
# the merged result on every run, so a machine without .env.lambda deploys the same
# settings. MCP_AUTH_TOKEN is generated (and kept in .env.lambda) when neither holds one.
# Read it with:
#   MSYS_NO_PATHCONV=1 aws ssm get-parameter --name /zonic-mcp-server/runtime --with-decryption
#
# Overridable environment variables:
#   AWS_PROFILE    (default tripleq-development)
#   AWS_REGION     (default us-east-1, the region of the Neon branches)
#   PARAMETER_NAME (default /zonic-mcp-server/runtime)
#   FUNCTION_NAME, ECR_REPO (default zonic-mcp-server)
#   ROLE_NAME      (default zonic-mcp-server-lambda)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export AWS_PROFILE="${AWS_PROFILE:-tripleq-development}"
REGION="${AWS_REGION:-us-east-1}"
FUNCTION="${FUNCTION_NAME:-zonic-mcp-server}"
REPO="${ECR_REPO:-zonic-mcp-server}"
ROLE="${ROLE_NAME:-zonic-mcp-server-lambda}"
PARAMETER="${PARAMETER_NAME:-/zonic-mcp-server/runtime}"
ARCH=arm64
MEMORY_MB=512
TIMEOUT_S=120
LOG_RETENTION_DAYS=14
PY="$(command -v python3 || command -v python)"

log() { printf '\n==> %s\n' "$*"; }

# Windows AWS CLI needs a Windows path inside file:// URIs.
native_path() { cygpath -w "$1" 2>/dev/null || printf '%s' "$1"; }

aws_() { aws --region "$REGION" "$@"; }
# Git Bash rewrites arguments that start with "/" (parameter and log group names) into
# Windows paths before a Windows program sees them. Docker needs that rewriting for
# local paths, so it is switched off only for these calls. No effect on Linux.
aws_raw() { MSYS_NO_PATHCONV=1 aws --region "$REGION" "$@"; }

VALUES_FILE="$(mktemp)"
ENV_FILE="$(mktemp)"
HEADER_FILE="$(mktemp)"
ERR_FILE="$(mktemp)"
trap 'rm -f "$VALUES_FILE" "$ENV_FILE" "$HEADER_FILE" "$ERR_FILE"' EXIT

# --- settings ----------------------------------------------------------------
# Values stay in files and environment variables; they are never printed or put on a
# command line.
PARAMETER_JSON="$(aws_raw ssm get-parameter --name "$PARAMETER" --with-decryption \
  --query Parameter.Value --output text 2>/dev/null || true)"
if [ -n "$PARAMETER_JSON" ]; then
  log "settings from SSM ($PARAMETER) and .env.lambda"
else
  log "parameter $PARAMETER not found; settings from .env.lambda"
fi

PARAMETER_STATE="$(PARAMETER_JSON="$PARAMETER_JSON" "$PY" - "$ROOT/.env.lambda" "$VALUES_FILE" <<'EOF'
import json, os, re, secrets, sys

env_path, out_path = sys.argv[1], sys.argv[2]
DEFAULTS = {
    'CACHE_API_URL': '',              # no cache service is deployed: results come back inline
    'FUNCTION_ALLOWLIST': 'statistics',
    'LOG_LEVEL': 'INFO',
}
# Prefixes that would let a caller run arbitrary code or reach the host.
UNSAFE = {'builtins', 'os', 'sys', 'subprocess', 'importlib', 'shutil', 'pathlib', 'io', 'ctypes'}


def fail(message):
    print(message, file=sys.stderr)
    sys.exit(1)


def read_dotenv(path):
    values = {}
    if os.path.exists(path):
        for line in open(path, encoding='utf-8-sig'):
            match = re.match(r'\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$', line.rstrip('\r\n'))
            if match:
                value = match.group(2).strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in '\'"':
                    value = value[1:-1]
                values[match.group(1)] = value
    return values


current = json.loads(os.environ['PARAMETER_JSON'] or '{}')
merged = {**DEFAULTS, **current, **read_dotenv(env_path)}

if not merged.get('MCP_AUTH_TOKEN'):
    merged['MCP_AUTH_TOKEN'] = secrets.token_hex(32)
    with open(env_path, 'a', encoding='utf-8') as f:
        f.write(f"MCP_AUTH_TOKEN={merged['MCP_AUTH_TOKEN']}\n")
    print('generated a new MCP_AUTH_TOKEN in .env.lambda', file=sys.stderr)

allowlist = [p.strip() for p in merged.get('FUNCTION_ALLOWLIST', '').split(',') if p.strip()]
if not allowlist:
    fail('FUNCTION_ALLOWLIST is empty, which allows every Python callable (os.system included).')
unsafe = [p for p in allowlist if p.split('.')[0] in UNSAFE]
if unsafe:
    fail(f'FUNCTION_ALLOWLIST names modules that can run arbitrary code: {", ".join(unsafe)}.')
if merged.get('FUNCTION_DYNAMIC_REGISTRATION_ENABLED', '').lower() in ('1', 'true', 'yes'):
    fail('FUNCTION_DYNAMIC_REGISTRATION_ENABLED cannot work on Lambda: pip cannot install into a read-only image.')

connections = merged.get('DATABASE_CONNECTIONS', '')
try:
    names = sorted(json.loads(connections)) if connections else []
except ValueError:
    fail('DATABASE_CONNECTIONS is not valid JSON.')
if 'sample_open_data' not in names:
    print('warning: DATABASE_CONNECTIONS has no "sample_open_data"; the open-data database tools will fail',
          file=sys.stderr)

body = json.dumps(merged, separators=(',', ':'))
# Both a standard SSM parameter and a function's environment hold at most 4 KB.
if len(body.encode()) > 4096:
    fail(f'The settings are {len(body.encode())} bytes; SSM and Lambda allow 4096.')
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(body)
print(f"settings: {', '.join(sorted(merged))}", file=sys.stderr)
print('new' if not current else ('same' if merged == current else 'changed'))
EOF
)"
PARAMETER_STATE="$(printf '%s' "$PARAMETER_STATE" | tr -d '\r')"

VALUES_URI="file://$(native_path "$VALUES_FILE")"
if [ "$PARAMETER_STATE" != same ]; then
  aws_raw ssm put-parameter --name "$PARAMETER" --type SecureString --tier Standard \
    --value "$VALUES_URI" --overwrite >/dev/null
  log "$PARAMETER_STATE parameter $PARAMETER"
fi

"$PY" -c 'import json, sys; print(json.dumps({"Variables": json.load(open(sys.argv[1], encoding="utf-8"))}))' \
  "$VALUES_FILE" > "$ENV_FILE"
ENV_URI="file://$(native_path "$ENV_FILE")"
"$PY" -c 'import json, sys; print("Authorization: Bearer " + json.load(open(sys.argv[1], encoding="utf-8"))["MCP_AUTH_TOKEN"])' \
  "$VALUES_FILE" > "$HEADER_FILE"

ACCOUNT="$(aws_ sts get-caller-identity --query Account --output text)"
REGISTRY="$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
TAG="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo manual)-$(date +%Y%m%d%H%M%S)"
IMAGE="$REGISTRY/$REPO:$TAG"
log "account $ACCOUNT, region $REGION, profile $AWS_PROFILE"

# --- ECR ---------------------------------------------------------------------
log "ECR repository $REPO"
if ! aws_ ecr describe-repositories --repository-names "$REPO" >/dev/null 2>&1; then
  aws_ ecr create-repository --repository-name "$REPO" \
    --image-scanning-configuration scanOnPush=true >/dev/null
fi
# Keep the five newest images so storage stays at cents per month.
aws_ ecr put-lifecycle-policy --repository-name "$REPO" --lifecycle-policy-text \
  '{"rules":[{"rulePriority":1,"description":"keep the 5 newest images","selection":{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":5},"action":{"type":"expire"}}]}' \
  >/dev/null

log "build and push $IMAGE"
aws_ ecr get-login-password | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null
# --provenance/--sbom off: Lambda rejects image indexes that carry attestations.
docker buildx build --platform "linux/$ARCH" --provenance=false --sbom=false \
  -t "$IMAGE" --push "$ROOT"

# --- IAM role ----------------------------------------------------------------
log "IAM role $ROLE"
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document \
    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
    >/dev/null
  # Logs only: the function needs no other AWS access.
  aws iam attach-role-policy --role-name "$ROLE" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
fi
ROLE_ARN="$(aws iam get-role --role-name "$ROLE" --query Role.Arn --output text)"

# --- log group with a retention limit (logs otherwise grow forever) ----------
aws_raw logs create-log-group --log-group-name "/aws/lambda/$FUNCTION" >/dev/null 2>&1 || true
aws_raw logs put-retention-policy --log-group-name "/aws/lambda/$FUNCTION" \
  --retention-in-days "$LOG_RETENTION_DAYS"

# --- Lambda function ---------------------------------------------------------
log "Lambda function $FUNCTION"
if ! aws_ lambda get-function --function-name "$FUNCTION" >/dev/null 2>&1; then
  # A new role takes a few seconds before Lambda can assume it.
  for attempt in 1 2 3 4 5 6 7 8; do
    if aws_ lambda create-function --function-name "$FUNCTION" --package-type Image \
        --code ImageUri="$IMAGE" --role "$ROLE_ARN" --architectures "$ARCH" \
        --memory-size "$MEMORY_MB" --timeout "$TIMEOUT_S" --environment "$ENV_URI" \
        --description "Zonic MCP server (execute tool)" >/dev/null 2>"$ERR_FILE"; then
      break
    fi
    if grep -q "cannot be assumed" "$ERR_FILE" && [ "$attempt" -lt 8 ]; then
      sleep 5
      continue
    fi
    cat "$ERR_FILE" >&2
    exit 1
  done
  aws_ lambda wait function-active-v2 --function-name "$FUNCTION"
else
  aws_ lambda update-function-code --function-name "$FUNCTION" --image-uri "$IMAGE" \
    --architectures "$ARCH" >/dev/null
  aws_ lambda wait function-updated-v2 --function-name "$FUNCTION"
  aws_ lambda update-function-configuration --function-name "$FUNCTION" \
    --memory-size "$MEMORY_MB" --timeout "$TIMEOUT_S" --environment "$ENV_URI" >/dev/null
  aws_ lambda wait function-updated-v2 --function-name "$FUNCTION"
fi

# --- Function URL ------------------------------------------------------------
log "Function URL"
if URL="$(aws_ lambda get-function-url-config --function-name "$FUNCTION" \
      --query FunctionUrl --output text 2>/dev/null)"; then
  aws_ lambda update-function-url-config --function-name "$FUNCTION" \
    --auth-type NONE --invoke-mode RESPONSE_STREAM >/dev/null
else
  URL="$(aws_ lambda create-function-url-config --function-name "$FUNCTION" \
    --auth-type NONE --invoke-mode RESPONSE_STREAM --query FunctionUrl --output text)"
fi
# Public at the URL layer; MCP_AUTH_TOKEN decides who may call /mcp.
aws_ lambda add-permission --function-name "$FUNCTION" --statement-id FunctionUrlPublicAccess \
  --action lambda:InvokeFunctionUrl --principal '*' --function-url-auth-type NONE \
  >/dev/null 2>&1 || true
aws_ lambda add-permission --function-name "$FUNCTION" --statement-id FunctionUrlInvokeFunction \
  --action lambda:InvokeFunction --principal '*' --invoked-via-function-url \
  >/dev/null 2>&1 || true

# --- smoke test --------------------------------------------------------------
BASE="${URL%/}"
MCP_HEADERS=(-H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream')
log "smoke test $BASE"
curl -sS --retry 5 --retry-all-errors --retry-delay 2 -m 30 -w '  [HTTP %{http_code}]\n' "$BASE/health"
printf 'without token: '
curl -sS -o /dev/null -m 30 -w '[HTTP %{http_code}]\n' "${MCP_HEADERS[@]}" "$BASE/mcp/" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
printf 'execute statistics.mean: '
curl -sS -m 30 -w '  [HTTP %{http_code}]\n' "${MCP_HEADERS[@]}" -H @"$HEADER_FILE" "$BASE/mcp/" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"execute","arguments":{"tools":[{"id":"mean","type":"function","config":{"functionPath":"statistics.mean","callStyle":"args"},"input":{"data":[2,4,9]}}]}}}'

cat <<EOF

Endpoint:  $BASE/mcp/
Token:     MCP_AUTH_TOKEN in $ROOT/.env.lambda and SSM $PARAMETER
Call with: Authorization: Bearer <token>
EOF
