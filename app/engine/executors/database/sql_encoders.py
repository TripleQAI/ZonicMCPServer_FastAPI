"""SQL value encoders — how a parameter value becomes text inside a script.

Scripts pre-quote their placeholders (``DECLARE @U NVARCHAR(100) = '<userID>'``),
so encoders emit values bare and are responsible for making them inert in that
position. Anything that cannot be made safe raises rather than being inlined.
"""
from __future__ import annotations

import re
from typing import Any

from app.infra.parameters import ValueEncoder

_NUMERIC_RE = re.compile(r'^-?\d+(\.\d+)?$')
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?$')


class StringEncoder(ValueEncoder):
    """Doubles single quotes so the value cannot close its enclosing literal."""

    def encode(self, value: Any) -> str:
        if value is None:
            return ''
        return str(value).replace("'", "''")


class NumericEncoder(ValueEncoder):
    """Emits a validated number. Rejects anything else — it may land unquoted."""

    def encode(self, value: Any) -> str:
        if value is None or value == '':
            return ''
        text = str(value).strip()
        if not _NUMERIC_RE.match(text):
            raise ValueError(f'Value "{value}" is not a valid number.')
        return text


class DateEncoder(ValueEncoder):
    """Emits a validated ISO-8601 date or datetime."""

    def encode(self, value: Any) -> str:
        if value is None or value == '':
            return ''
        text = str(value).strip()
        if not _DATE_RE.match(text):
            raise ValueError(f'Value "{value}" is not a valid ISO-8601 date.')
        return text


class BooleanEncoder(ValueEncoder):
    """Emits SQL Server's bit representation."""

    _TRUE = {'true', '1', 'yes', 'y', 't'}
    _FALSE = {'false', '0', 'no', 'n', 'f', ''}

    def encode(self, value: Any) -> str:
        if value is None:
            return ''
        text = str(value).strip().lower()
        if text in self._TRUE:
            return '1'
        if text in self._FALSE:
            return '0'
        raise ValueError(f'Value "{value}" is not a valid boolean.')


_STRING = StringEncoder()

#: dataType → encoder. Unknown types fall back to the string encoder, which is the
#: conservative choice: it escapes rather than trusting the value.
_ENCODERS: dict[str, ValueEncoder] = {
    'string':   _STRING,
    'text':     _STRING,
    'int':      NumericEncoder(),
    'integer':  NumericEncoder(),
    'number':   NumericEncoder(),
    'decimal':  NumericEncoder(),
    'float':    NumericEncoder(),
    'date':     DateEncoder(),
    'datetime': DateEncoder(),
    'bool':     BooleanEncoder(),
    'boolean':  BooleanEncoder(),
}


def get_sql_encoder(data_type: str) -> ValueEncoder:
    return _ENCODERS.get((data_type or '').strip().lower(), _STRING)
