from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.engine.executors.base import BaseExecutor

if TYPE_CHECKING:
    from app.engine.container import ExecutionContext

#: Authentication types; the builder's HTTP tool form offers the same names.
_AUTH_TYPES = ('bearer_token', 'api_key', 'basic_auth')


def auth_config(config: dict[str, Any]) -> dict[str, Any]:
    """The tool's ``auth`` object, or the same settings from the builder's HTTP tool form:
    ``authType``, ``credentialName`` (the server environment variable holding the secret) and
    ``apiKeyHeader``. A ``basic_auth`` credential is the pair ``<name>_USER`` / ``<name>_PASSWORD``."""
    if config.get('auth'):
        return config['auth']
    kind, name = config.get('authType'), config.get('credentialName') or ''
    if kind not in _AUTH_TYPES:
        return {}
    if kind == 'basic_auth':
        return {'type': kind, 'userEnvVar': f'{name}_USER', 'passwordEnvVar': f'{name}_PASSWORD'}
    found = {'type': kind, 'envVar': name}
    if kind == 'api_key' and config.get('apiKeyHeader'):
        found['header'] = config['apiKeyHeader']
    return found


def _secret(name: str) -> str:
    """A credential from the server's environment; a missing one fails the tool call, instead of
    sending the request without it."""
    import os

    value = os.environ.get(name or '', '')
    if not value:
        raise ValueError(f'Credential "{name}" is not configured on the MCP server.')
    return value


class ApiExecutor(BaseExecutor):
    async def _run_with_cache(self, config, produce):
        return await self._read_through(config, produce)

    async def _run(self, config: dict[str, Any], resolved_input: dict[str, Any],
                   ctx: ExecutionContext) -> Any:
        from app.config import get_settings
        from app.infra.http_client import get_http_client

        method = config.get('method', 'GET').upper()
        allowed = get_settings().allowed_http_methods
        if method not in allowed:
            raise ValueError(f'HTTP method "{method}" is not allowed.')

        from app.infra.parameters import ToolParameter, substitute

        from .encoders import api_encoder

        # The tool's <placeholders> are filled in each location with that location's encoding.
        parameters = ToolParameter.from_list(config.get('parameters', []))

        def fill(value: Any, location: str) -> Any:
            if not parameters or not isinstance(value, str):
                return value
            return substitute(value, parameters, resolved_input, api_encoder(location))

        url = fill(config.get('url', ''), 'url')
        headers = {key: fill(value, 'header') for key, value in
                   {**config.get('headers', {}), **resolved_input.get('headers', {})}.items()}
        query_params = {key: fill(value, 'query') for key, value in
                        {**config.get('queryParams', {}), **resolved_input.get('queryParams', {})}.items()}
        auth_cfg = auth_config(config)

        if auth_cfg.get('type') == 'bearer_token':
            headers['Authorization'] = f'Bearer {_secret(auth_cfg.get("envVar", ""))}'
        elif auth_cfg.get('type') == 'api_key':
            headers[auth_cfg.get('header', 'X-API-Key')] = _secret(auth_cfg.get('envVar', ''))
        elif auth_cfg.get('type') == 'basic_auth':
            import base64
            user = _secret(auth_cfg.get('userEnvVar', ''))
            pwd = _secret(auth_cfg.get('passwordEnvVar', ''))
            encoded = base64.b64encode(f'{user}:{pwd}'.encode()).decode()
            headers['Authorization'] = f'Basic {encoded}'

        body_type = config.get('bodyType', 'none')
        body_template = fill(config.get('bodyTemplate', resolved_input.get('body', '')),
                             'json' if body_type == 'json' else 'text')
        body_form = {key: fill(value, 'text') for key, value in
                     config.get('bodyFormData', resolved_input.get('formData', {})).items()}

        client = await get_http_client()

        kwargs: dict = {'headers': headers, 'params': query_params}
        if method in ('POST', 'PUT', 'PATCH') and body_type != 'none':
            if body_type == 'json':
                import json
                kwargs['json'] = json.loads(body_template) if isinstance(body_template, str) else body_template
            elif body_type == 'form':
                kwargs['data'] = body_form
            else:
                kwargs['content'] = body_template.encode() if isinstance(body_template, str) else body_template

        resp = await client.request(method, url, **kwargs)
        resp.raise_for_status()

        result_cfg = config.get('result', {})
        fmt = result_cfg.get('format', 'json')
        if fmt == 'json':
            try:
                return resp.json()
            except Exception:
                return resp.text
        return resp.text
