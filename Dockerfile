# Zonic MCP server as a Lambda container image.
# The Lambda Web Adapter extension forwards Lambda events to uvicorn, so the app runs
# unchanged. Outside Lambda the extension stays idle and the image is a plain web server:
#   docker run -p 8080:8080 --env-file .env zonic-mcp-server
# --provenance=false matters: Lambda rejects image indexes that carry attestations.
#   docker buildx build --platform linux/arm64 --provenance=false --sbom=false -t zonic-mcp-server .
FROM public.ecr.aws/docker/library/python:3.13-slim-bookworm

COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 /lambda-adapter /opt/extensions/lambda-adapter

# ODBC Driver 18 for SQL Server (database executor, MSSQL connections)
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl gnupg ca-certificates \
 && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
 && curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list -o /etc/apt/sources.list.d/mssql-release.list \
 && apt-get update \
 && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 unixodbc \
 && apt-get purge -y curl gnupg && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /var/task
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir . && python -m compileall -q app

# Lambda's filesystem is read-only except /tmp. Response streaming (with the Function
# URL's RESPONSE_STREAM mode) lifts the 6 MB limit of a buffered Lambda response.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    AWS_LWA_READINESS_CHECK_PATH=/health \
    AWS_LWA_INVOKE_MODE=response_stream

RUN useradd --system --uid 10001 mcp
USER mcp

EXPOSE 8080
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
