from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.engine.container import ExecutionContext

_TOKEN_RE = re.compile(r'\{\{([^}]+)\}\}')


def _get_nested(obj: Any, path: str) -> Any:
    parts = path.split('.')
    cur = obj
    for p in parts:
        if isinstance(cur, dict):
            cur = cur.get(p)
        else:
            try:
                cur = getattr(cur, p)
            except AttributeError:
                return None
        if cur is None:
            return None
    return cur


def resolve_value(value: Any, ctx: ExecutionContext) -> Any:
    if isinstance(value, str):
        return _resolve_string(value, ctx)
    if isinstance(value, dict):
        return {k: resolve_value(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_value(v, ctx) for v in value]
    return value


def _resolve_string(s: str, ctx: ExecutionContext) -> Any:
    tokens = _TOKEN_RE.findall(s)
    if not tokens:
        return s
    # Full replacement: single token spanning the whole string
    if s.strip() == '{{' + tokens[0] + '}}':
        return _resolve_token(tokens[0], ctx)
    # Partial: interpolate all tokens as strings
    def replacer(m: re.Match) -> str:
        v = _resolve_token(m.group(1), ctx)
        return str(v) if v is not None else m.group(0)
    return _TOKEN_RE.sub(replacer, s)


def _resolve_token(token: str, ctx: ExecutionContext) -> Any:
    token = token.strip()
    if token.startswith('flow.'):
        return _get_nested(ctx.flow, token[5:])
    if token.startswith('system.'):
        return _get_nested(ctx.system, token[7:])
    if token.startswith('results.'):
        rest = token[8:]
        dot = rest.find('.')
        if dot == -1:
            tool_id, path = rest, None
        else:
            tool_id, path = rest[:dot], rest[dot + 1:]
        r = ctx.results.get(tool_id)
        if r is None:
            return None
        if path is None:
            return r.output
        return _get_nested(r.output, path)
    return None


def resolve_dict(d: dict, ctx: ExecutionContext) -> dict:
    return {k: resolve_value(v, ctx) for k, v in d.items()}
