from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.engine.executors.base import BaseExecutor

if TYPE_CHECKING:
    from app.engine.container import ExecutionContext


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

        url = config.get('url', '')
        headers = {**config.get('headers', {}), **resolved_input.get('headers', {})}
        query_params = {**config.get('queryParams', {}), **resolved_input.get('queryParams', {})}
        auth_cfg = config.get('auth', {})

        if auth_cfg.get('type') == 'bearer_token':
            import os
            token = os.environ.get(auth_cfg.get('envVar', ''), '')
            headers['Authorization'] = f'Bearer {token}'
        elif auth_cfg.get('type') == 'api_key':
            import os
            key = os.environ.get(auth_cfg.get('envVar', ''), '')
            headers[auth_cfg.get('header', 'X-API-Key')] = key
        elif auth_cfg.get('type') == 'basic_auth':
            import base64
            import os
            user = os.environ.get(auth_cfg.get('userEnvVar', ''), '')
            pwd  = os.environ.get(auth_cfg.get('passwordEnvVar', ''), '')
            encoded = base64.b64encode(f'{user}:{pwd}'.encode()).decode()
            headers['Authorization'] = f'Basic {encoded}'

        body_type = config.get('bodyType', 'none')
        body_template = config.get('bodyTemplate', resolved_input.get('body', ''))
        body_form = config.get('bodyFormData', resolved_input.get('formData', {}))

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
