"""API value encoders — how a parameter value becomes text inside an API tool's request.

An API tool's ``url``, ``queryParams``, ``headers`` and body carry ``<placeholder>``
markers like a database script does. Each location gets its own encoder, so a value
stays inert where it lands: percent-encoded in the URL, JSON-escaped inside a JSON
body, refused in a header when it holds a line break. Numbers, dates and booleans are
validated first, with the SQL encoders' rules, so a typed value cannot carry text.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from app.engine.executors.database.sql_encoders import (
    DateEncoder,
    NumericEncoder,
    PostgresBooleanEncoder,
)
from app.infra.parameters import EncoderFactory, ValueEncoder

#: dataType → validating encoder; other types are text.
_TYPED: dict[str, ValueEncoder] = {
    'int': NumericEncoder(), 'integer': NumericEncoder(), 'number': NumericEncoder(),
    'decimal': NumericEncoder(), 'float': NumericEncoder(),
    'date': DateEncoder(), 'datetime': DateEncoder(),
    'bool': PostgresBooleanEncoder(), 'boolean': PostgresBooleanEncoder(),
}


def _text(value: Any) -> str:
    return '' if value is None else str(value)


def _header(value: str) -> str:
    if '\r' in value or '\n' in value:
        raise ValueError('A header value cannot contain a line break.')
    return value


#: Location → how text is made safe there. Query values are encoded by the HTTP client.
_LOCATIONS = {
    'url':    lambda text: quote(text, safe=''),
    'query':  lambda text: text,
    'header': _header,
    'json':   lambda text: json.dumps(text)[1:-1],
    'text':   lambda text: text,
}


class _LocationEncoder(ValueEncoder):
    def __init__(self, typed: ValueEncoder | None, escape) -> None:
        self._typed = typed
        self._escape = escape

    def encode(self, value: Any) -> str:
        text = self._typed.encode(value) if self._typed else _text(value)
        return self._escape(text)


def api_encoder(location: str) -> EncoderFactory:
    """The encoder factory of a request location (``url``, ``query``, ``header``, ``json``, ``text``)."""
    escape = _LOCATIONS[location]

    def factory(data_type: str) -> ValueEncoder:
        return _LocationEncoder(_TYPED.get((data_type or '').strip().lower()), escape)

    return factory
